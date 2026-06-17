"""
跨平台股票池管理器
当前使用 Tushare 数据源的统一股票池配置
"""

import random
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd


class StockPoolManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.stock_pool_config = config.get("stock_pool", {})
        self.pool_type = self.stock_pool_config.get("type", "all_market")
        self._namechange_cache: dict[str, pd.DataFrame] = {}
        
    def get_stock_pool(self, start_date: str = None, end_date: str = None) -> List[str]:
        """
        根据配置获取股票池
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            股票代码列表
        """
        if self.pool_type == "all_market":
            codes = self._get_all_market(start_date, end_date)
        elif self.pool_type == "index_components":
            codes = self._get_index_components(start_date, end_date)
        elif self.pool_type == "industry":
            codes = self._get_industry_stocks(start_date, end_date)
        elif self.pool_type == "custom_list":
            codes = self._get_custom_list()
        elif self.pool_type == "random_sample":
            codes = self._get_random_sample(start_date, end_date)
        elif self.pool_type == "stratified_sample":
            codes = self._get_stratified_sample(start_date, end_date)
        else:
            raise ValueError(f"不支持的股票池类型: {self.pool_type}")
        return self._apply_universe_filters(codes, end_date=end_date or start_date)

    @staticmethod
    def _to_ts_code(code: str) -> str:
        text = str(code).strip()
        if text.startswith("SH") and len(text) == 8:
            return f"{text[2:]}.SH"
        if text.startswith("SZ") and len(text) == 8:
            return f"{text[2:]}.SZ"
        return text

    def _namechange_history(self, provider: Any, code: str) -> pd.DataFrame:
        ts_code = self._to_ts_code(code)
        cached = self._namechange_cache.get(ts_code)
        if cached is not None:
            return cached
        try:
            frame = provider._call_pro_api(
                "namechange",
                ts_code=ts_code,
                fields="ts_code,name,start_date,end_date,change_reason",
            )
        except Exception:
            frame = pd.DataFrame()
        if frame is None or frame.empty:
            frame = pd.DataFrame(columns=["ts_code", "name", "start_date", "end_date", "change_reason"])
        else:
            frame = frame.copy()
            frame["start_date"] = frame["start_date"].astype(str)
            frame["end_date"] = frame["end_date"].fillna("").astype(str)
            frame = frame.drop_duplicates(subset=["ts_code", "name", "start_date", "end_date", "change_reason"])
        self._namechange_cache[ts_code] = frame
        return frame

    def _is_historical_st(self, provider: Any, code: str, date: pd.Timestamp, current_name: str) -> bool:
        history = self._namechange_history(provider, code)
        date_text = date.strftime("%Y%m%d")
        if not history.empty:
            active = history[
                (history["start_date"] <= date_text)
                & ((history["end_date"] == "") | (history["end_date"] >= date_text))
            ]
            if not active.empty:
                return any("ST" in str(name).upper() for name in active["name"])
        return "ST" in str(current_name).upper()

    def _apply_universe_filters(self, codes: List[str], end_date: str = None) -> List[str]:
        include_st = bool(self.stock_pool_config.get("include_st", True))
        include_new_stock = bool(self.stock_pool_config.get("include_new_stock", True))
        new_stock_days = int(self.stock_pool_config.get("new_stock_days", 60) or 60)
        if include_st and include_new_stock:
            return codes

        try:
            from data_provider import get_data_provider

            provider = get_data_provider(self.config)
            provider.initialize()
            stock_basic = provider._call_pro_api(  # type: ignore[attr-defined]
                "stock_basic",
                exchange="",
                list_status="L",
                fields="ts_code,name,list_date",
            )
            if stock_basic is None or stock_basic.empty:
                return codes
            stock_basic["instrument"] = stock_basic["ts_code"].apply(self._to_internal_code)
            meta = stock_basic.set_index("instrument")[["name", "list_date"]]
            cutoff = pd.Timestamp(end_date or self.config.get("train_end_date") or pd.Timestamp.today()).normalize()
            min_listed_date = (cutoff - pd.Timedelta(days=new_stock_days)).strftime("%Y%m%d")

            filtered: list[str] = []
            for code in codes:
                if code not in meta.index:
                    filtered.append(code)
                    continue
                name = str(meta.loc[code, "name"])
                list_date = str(meta.loc[code, "list_date"])
                if not include_st and self._is_historical_st(provider, code, cutoff, name):
                    continue
                if not include_new_stock and list_date and list_date > min_listed_date:
                    continue
                filtered.append(code)
            print(
                f"股票池过滤完成: 原始 {len(codes)} 只 -> 过滤后 {len(filtered)} 只 "
                f"(include_st={include_st}, include_new_stock={include_new_stock}, new_stock_days={new_stock_days})"
            )
            return filtered
        except Exception as exc:
            print(f"警告: 股票池 ST/新股过滤失败，回退原股票池: {exc}")
            return codes

    @staticmethod
    def _to_internal_code(ts_code: str) -> str:
        if "." not in ts_code:
            return ts_code
        code, exchange = ts_code.split(".")
        if exchange.upper() == "SH":
            return f"SH{code}"
        if exchange.upper() == "SZ":
            return f"SZ{code}"
        return ts_code
    
    def _get_all_market(self, start_date: str = None, end_date: str = None) -> List[str]:
        """
        获取全市场股票池
        """
        try:
            from data_provider import get_data_provider

            provider = get_data_provider(self.config)
            provider.initialize()
            codes = provider.get_instruments(market=str(self.config.get("market", "all")))
            max_instruments = int(self.config.get("max_instruments", 0) or 0)
            if max_instruments > 0:
                return self._apply_sampling(codes, max_instruments)
            return codes
        except Exception as e:
            print(f"警告: 获取全市场股票池失败: {e}")
            return []
    
    def _get_index_components(self, start_date: str = None, end_date: str = None) -> List[str]:
        """
        获取指数成分股
        
        支持的指数:
        - 000300.XSHG / SH000300: 沪深300
        - 000905.XSHG / SH000905: 中证500
        - 000852.XSHG / SH000852: 中证1000
        - 000016.XSHG / SH000016: 上证50
        - 399001.XSHE / SZ399001: 深证成指
        - 399006.XSHE / SZ399006: 创业板指
        """
        index_code = self.stock_pool_config.get("index_code", "000300.XSHG")
        
        return self._get_index_components_from_provider(index_code, start_date, end_date)

    def _get_index_components_from_provider(self, index_code: str, start_date: str = None, end_date: str = None) -> List[str]:
        """使用 Tushare 提供者获取指数成分股"""
        try:
            # 导入数据提供者工厂
            from data_provider import get_data_provider
            
            provider = get_data_provider(self.config)
            provider.initialize()
            
            # 优先按当前运行区间结束日取成分股，避免“最新成分”接口在部分数据源下返回空集。
            query_date = end_date or start_date
            codes = provider.get_index_components(index_code, date=query_date)
            
            print(f"从数据提供者获取 {index_code} 成分股: {len(codes)} 只 (date={query_date or 'latest'})")
            return codes
            
        except ImportError:
            print("警告: 数据提供者模块未找到，无法获取指数成分股")
            return []
        except Exception as e:
            print(f"警告: 从数据提供者获取指数 {index_code} 成分股失败: {e}")
            return []
    
    def _get_industry_stocks(self, start_date: str = None, end_date: str = None) -> List[str]:
        """
        获取行业股票池
        
        支持的行业分类:
        - sw: 申万行业
        - csrc: 中信行业
        - jq: 聚宽行业
        """
        industry_name = self.stock_pool_config.get("industry_name", "")
        industry_level = self.stock_pool_config.get("industry_level", 1)
        
        print(f"获取行业股票池: {industry_name} (级别: {industry_level})")
        if not industry_name:
            print("警告: 未配置 industry_name，返回空列表")
            return []

        try:
            from data_provider import get_data_provider

            provider = get_data_provider(self.config)
            provider.initialize()
            codes = provider.get_industry_stocks(industry_name, date=end_date or start_date)
            print(f"从数据提供者获取行业 {industry_name} 股票: {len(codes)} 只")
            return codes
        except ImportError:
            print("警告: 数据提供者模块未找到，无法获取行业股票池")
            return []
        except Exception as e:
            print(f"警告: 获取行业股票池失败: {e}")
            return []
    
    def _get_custom_list(self) -> List[str]:
        """
        获取自定义股票列表
        """
        instruments = self.stock_pool_config.get("instruments", [])
        
        if not instruments:
            print("警告: 自定义股票列表为空")
        
        return instruments
    
    def _get_random_sample(self, start_date: str = None, end_date: str = None) -> List[str]:
        """
        随机抽样
        
        配置参数:
        - base_pool_type: 基础股票池类型 (all_market, index_components等)
        - sample_size: 抽样数量
        - seed: 随机种子 (保证可复现)
        """
        base_pool_type = self.stock_pool_config.get("base_pool_type", "all_market")
        sample_size = int(self.stock_pool_config.get("sample_size", 100))
        seed = int(self.stock_pool_config.get("seed", 42))
        
        # 临时修改配置获取基础股票池
        original_pool_type = self.pool_type
        self.pool_type = base_pool_type
        
        base_pool = self.get_stock_pool(start_date, end_date)
        
        # 恢复原始配置
        self.pool_type = original_pool_type
        
        # 随机抽样
        random.seed(seed)
        if len(base_pool) > sample_size:
            sampled = random.sample(base_pool, sample_size)
        else:
            sampled = base_pool
        
        print(f"从 {len(base_pool)} 只股票中随机抽样 {len(sampled)} 只 (seed={seed})")
        return sampled
    
    def _get_stratified_sample(self, start_date: str = None, end_date: str = None) -> List[str]:
        """
        分层抽样
        
        配置参数:
        - base_pool_type: 基础股票股票池类型
        - sample_size: 抽样数量
        - stratify_by: 分层维度 (market_cap, industry)
        - seed: 随机种子
        """
        base_pool_type = self.stock_pool_config.get("base_pool_type", "all_market")
        sample_size = int(self.stock_pool_config.get("sample_size", 100))
        stratify_by = self.stock_pool_config.get("stratify_by", ["market_cap"])
        seed = int(self.stock_pool_config.get("seed", 42))
        
        # 临时修改配置获取基础股票池
        original_pool_type = self.pool_type
        self.pool_type = base_pool_type
        
        base_pool = self.get_stock_pool(start_date, end_date)
        
        # 恢复原始配置
        self.pool_type = original_pool_type
        
        # 按市值分层抽样
        if "market_cap" in stratify_by:
            sampled = self._stratify_by_market_cap(
                base_pool,
                sample_size,
                seed,
                as_of_date=end_date or start_date,
            )
        else:
            # 其他分层方式暂不支持，使用随机抽样
            print(f"警告: 不支持的分层方式 {stratify_by}，使用随机抽样")
            random.seed(seed)
            sampled = random.sample(base_pool, min(sample_size, len(base_pool)))
        
        print(f"分层抽样: {len(sampled)} 只股票 (分层维度: {stratify_by}, seed={seed})")
        return sampled
    
    def _stratify_by_market_cap(
        self,
        codes: List[str],
        sample_size: int,
        seed: int,
        as_of_date: str | None = None,
    ) -> List[str]:
        """
        按市值分层抽样

        实现说明：
        - 使用数据提供者获取市值（$market_cap）并按真实市值排序
        - 将基础池按市值分为 3 层（小/中/大）
        - 按层内随机抽样，默认目标配额尽量均分到 3 层
        """
        if not codes or sample_size <= 0:
            return []

        rng = random.Random(seed)
        all_codes = sorted(set(codes))
        target_size = min(sample_size, len(all_codes))

        as_of_ts = pd.Timestamp(
            as_of_date
            or self.config.get("train_end_date")
            or pd.Timestamp.today()
        ).normalize()
        start_date = (as_of_ts - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
        end_date = as_of_ts.strftime("%Y-%m-%d")

        market_cap_map: dict[str, float] = {}
        try:
            from data_provider import get_data_provider

            provider = get_data_provider(self.config)
            provider.initialize()
            market_cap_frame = provider.get_features(
                all_codes,
                fields=["$market_cap"],
                start_date=start_date,
                end_date=end_date,
                freq="day",
            )

            if not market_cap_frame.empty and "$market_cap" in market_cap_frame.columns:
                caps = pd.to_numeric(market_cap_frame["$market_cap"], errors="coerce")
                for code, series in caps.groupby(level="instrument", sort=False):
                    series = series.dropna()
                    if series.empty:
                        continue
                    market_cap_map[str(code)] = float(series.iloc[-1])
        except Exception as exc:
            print(f"警告: 获取市值数据失败，分层抽样回退为按代码顺序分层: {exc}")

        # 优先按真实市值排序；缺失市值的股票放到末尾（内部按代码排序保证可复现）
        valid_pairs = [(code, market_cap_map[code]) for code in all_codes if code in market_cap_map]
        valid_pairs.sort(key=lambda item: item[1])  # 小 -> 大
        missing_codes = [code for code in all_codes if code not in market_cap_map]
        ordered_codes = [code for code, _ in valid_pairs] + sorted(missing_codes)

        n = len(ordered_codes)
        base = n // 3
        rem = n % 3
        layer_sizes = [base + (1 if i < rem else 0) for i in range(3)]

        layers: list[list[str]] = []
        cursor = 0
        for size in layer_sizes:
            layers.append(ordered_codes[cursor:cursor + size])
            cursor += size

        target_base = target_size // 3
        target_rem = target_size % 3
        layer_targets = [target_base + (1 if i < target_rem else 0) for i in range(3)]

        sampled: list[str] = []
        selected = set()
        for layer, layer_target in zip(layers, layer_targets):
            take = min(layer_target, len(layer))
            if take <= 0:
                continue
            picks = rng.sample(layer, take) if len(layer) > take else list(layer)
            sampled.extend(picks)
            selected.update(picks)

        # 若某层股票不足，使用其余未选股票补齐目标样本数
        if len(sampled) < target_size:
            remaining = [code for code in ordered_codes if code not in selected]
            need = min(target_size - len(sampled), len(remaining))
            if need > 0:
                sampled.extend(rng.sample(remaining, need) if len(remaining) > need else remaining)

        return sampled[:target_size]
    
    def _apply_sampling(self, codes: List[str], max_instruments: int) -> List[str]:
        """
        应用抽样策略
        """
        sampling_method = self.stock_pool_config.get("sampling_method", "random")
        seed = int(self.stock_pool_config.get("seed", 42))
        
        if sampling_method == "random":
            random.seed(seed)
            if len(codes) > max_instruments:
                return random.sample(codes, max_instruments)
            return codes
        elif sampling_method == "first":
            return codes[:max_instruments]
        else:
            print(f"警告: 不支持的抽样方法 {sampling_method}，使用随机抽样")
            random.seed(seed)
            if len(codes) > max_instruments:
                return random.sample(codes, max_instruments)
            return codes
    
    def get_pool_info(self) -> Dict[str, Any]:
        """
        获取股票池信息
        """
        return {
            "pool_type": self.pool_type,
            "config": self.stock_pool_config,
            "description": self._get_pool_description()
        }
    
    def _get_pool_description(self) -> str:
        """
        获取股票池描述
        """
        descriptions = {
            "all_market": "全市场股票池",
            "index_components": f"指数成分股: {self.stock_pool_config.get('index_code', '未知')}",
            "industry": f"行业股票池: {self.stock_pool_config.get('industry_name', '未知')}",
            "custom_list": f"自定义股票列表 ({len(self.stock_pool_config.get('instruments', []))} 只)",
            "random_sample": f"随机抽样 ({self.stock_pool_config.get('sample_size', 100)} 只)",
            "stratified_sample": f"分层抽样 ({self.stock_pool_config.get('sample_size', 100)} 只)"
        }
        return descriptions.get(self.pool_type, "未知股票池类型")


def create_stock_pool_manager(config: Dict[str, Any]) -> StockPoolManager:
    """
    工厂函数：创建股票池管理器
    
    Args:
        config: 配置字典
        
    Returns:
        StockPoolManager 实例
    """
    return StockPoolManager(config)


def validate_stock_pool_config(config: Dict[str, Any]) -> Tuple[bool, str]:
    """
    验证股票池配置
    
    Args:
        config: 配置字典
        
    Returns:
        (是否有效, 错误信息)
    """
    stock_pool_config = config.get("stock_pool", {})
    pool_type = stock_pool_config.get("type", "all_market")
    
    valid_types = [
        "all_market", "index_components", "industry", 
        "custom_list", "random_sample", "stratified_sample"
    ]
    
    if pool_type not in valid_types:
        return False, f"不支持的股票池类型: {pool_type}，支持的类型: {valid_types}"
    
    if pool_type == "index_components":
        index_code = stock_pool_config.get("index_code")
        if not index_code:
            return False, "指数成分股类型需要指定 index_code"
    
    if pool_type == "custom_list":
        instruments = stock_pool_config.get("instruments", [])
        if not instruments:
            return False, "自定义股票列表不能为空"
    
    if pool_type in ["random_sample", "stratified_sample"]:
        sample_size = stock_pool_config.get("sample_size")
        if not sample_size or sample_size <= 0:
            return False, "抽样类型需要指定正数的 sample_size"
    
    return True, "配置有效"


if __name__ == "__main__":
    # 测试代码
    test_configs = [
        {
            "stock_pool": {
                "type": "all_market"
            },
            "max_instruments": 100
        },
        {
            "stock_pool": {
                "type": "index_components",
                "index_code": "000300.XSHG"
            }
        },
        {
            "stock_pool": {
                "type": "custom_list",
                "instruments": ["000001.XSHE", "000002.XSHE", "600000.XSHG"]
            }
        },
        {
            "stock_pool": {
                "type": "random_sample",
                "base_pool_type": "all_market",
                "sample_size": 50,
                "seed": 42
            },
            "max_instruments": 0
        },
        {
            "stock_pool": {
                "type": "stratified_sample",
                "base_pool_type": "all_market",
                "sample_size": 100,
                "stratify_by": ["market_cap"],
                "seed": 42
            },
            "max_instruments": 0
        }
    ]
    
    for i, config in enumerate(test_configs, 1):
        print(f"\n测试配置 {i}:")
        print(f"股票池类型: {config['stock_pool']['type']}")
        
        is_valid, message = validate_stock_pool_config(config)
        if not is_valid:
            print(f"配置验证失败: {message}")
            continue
        
        manager = create_stock_pool_manager(config)
        pool_info = manager.get_pool_info()
        print(f"描述: {pool_info['description']}")
        
        try:
            stock_pool = manager.get_stock_pool()
            print(f"股票池大小: {len(stock_pool)}")
            if stock_pool:
                print(f"前 5 只股票: {stock_pool[:5]}")
        except Exception as e:
            print(f"获取股票池失败: {e}")
