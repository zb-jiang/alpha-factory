"""
Tushare 数据提供者实现
封装 Tushare 的数据访问接口，适配 BaseDataProvider
"""

import hashlib
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

from .base_provider import BaseDataProvider


class TushareProvider(BaseDataProvider):
    """Tushare 数据提供者
    
    封装 Tushare 的数据访问功能，提供与 Qlib 兼容的统一接口。
    支持实时数据获取，数据更新到最新交易日。
    
    文档: https://tushare.pro/document/2
    """

    VWAP_AMOUNT_SCALE = 1000.0
    VWAP_VOLUME_SCALE = 100.0
    
    # 字段映射：Qlib格式 -> Tushare格式
    FIELD_MAP = {
        "$open": "open",
        "$high": "high",
        "$low": "low",
        "$close": "close",
        "$volume": "vol",
        "$amount": "amount",
        "$money": "amount",
        "$turnover": "turnover_rate",
        "$market_cap": "total_mv",
        "$industry": "industry",
        "$factor": "adj_factor",  # 复权因子，需要从 adj_factor 接口获取
    }

    FIELD_SOURCE_MAP = {
        "$open": "daily",
        "$high": "daily",
        "$low": "daily",
        "$close": "daily",
        "$volume": "daily",
        "$amount": "daily",
        "$money": "daily",
        "$factor": "adj_factor",
        "$vwap": "derived_price",
        "$turnover": "daily_basic",
        "$market_cap": "daily_basic",
        "$industry": "stock_basic",
    }
    
    # 反向映射：Tushare格式 -> Qlib格式
    REVERSE_FIELD_MAP = {v: k for k, v in FIELD_MAP.items()}
    
    # 指数代码映射：统一格式 -> Tushare格式
    INDEX_CODE_MAP = {
        "000300.XSHG": "000300.SH",    # 沪深300
        "SH000300": "000300.SH",
        "000905.XSHG": "000905.SH",    # 中证500
        "SH000905": "000905.SH",
        "000852.XSHG": "000852.SH",    # 中证1000
        "SH000852": "000852.SH",
        "000016.XSHG": "000016.SH",    # 上证50
        "SH000016": "000016.SH",
        "399001.XSHE": "399001.SZ",    # 深证成指
        "SZ399001": "399001.SZ",
        "399006.XSHE": "399006.SZ",    # 创业板指
        "SZ399006": "399006.SZ",
        "000001.XSHG": "000001.SH",    # 上证指数
        "SH000001": "000001.SH",
    }
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.ts_config = config.get("tushare", {})
        self.cache_dir = Path(self.ts_config.get("data_cache_dir", "./data/tushare_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ts = None  # tushare 模块
        self._pro = None  # Tushare Pro 接口
        self._api_key = None
    
    def initialize(self) -> None:
        """初始化 Tushare 连接
        
        从环境变量或配置文件中获取 API Key 进行初始化。
        """
        if self._initialized:
            return
        
        try:
            import tushare as ts
            self._ts = ts
            
            # 获取 API Key
            self._api_key = self.ts_config.get("api_key") or os.getenv("TUSHARE_API_KEY")
            
            if not self._api_key:
                raise ValueError(
                    "Tushare API Key 未配置，请设置环境变量 TUSHARE_API_KEY，"
                    "或在 env.yaml 的 tushare 配置中指定"
                )
            
            # 初始化 Pro 接口
            self._pro = ts.pro_api(self._api_key)
            
            # 验证 API Key 是否有效
            try:
                # 尝试获取一条数据来验证
                test_df = self._pro.trade_cal(exchange='SSE', limit=1)
                if test_df is None or test_df.empty:
                    raise RuntimeError("API Key 验证失败，请检查 API Key 是否有效")
            except Exception as e:
                if "PermissionError" in str(type(e)).lower() or "权限" in str(e):
                    raise RuntimeError(f"Tushare API 权限不足: {e}")
                raise RuntimeError(f"Tushare API 验证失败: {e}")
            
            self._initialized = True
            
            # 验证连接并打印信息
            print(f"Tushare 数据连接成功")
            print(f"  API Key: {self._api_key[:8]}...")
            print(f"  缓存目录: {self.cache_dir}")
            
        except ImportError:
            raise ImportError(
                "tushare 未安装，请执行: pip install tushare\n"
                "Tushare 需要注册获取 API Key，请访问 https://tushare.pro/register"
            )
        except Exception as e:
            raise RuntimeError(f"Tushare 初始化失败: {e}")
    
    def _get_cache_key(self, func_name: str, **kwargs) -> str:
        """生成缓存键
        
        根据函数名和参数生成唯一的缓存文件名。
        """
        # 将参数排序后生成字符串
        param_str = "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        # 使用 MD5 生成短哈希
        hash_obj = hashlib.md5(f"{func_name}_{param_str}".encode())
        return hash_obj.hexdigest()[:16]
    
    def _get_cache_path(self, cache_key: str) -> Path:
        """获取缓存文件路径"""
        return self.cache_dir / f"{cache_key}.parquet"
    
    def _load_from_cache(self, cache_key: str) -> Optional[pd.DataFrame]:
        """从缓存加载数据
        
        Args:
            cache_key: 缓存键
            
        Returns:
            缓存的 DataFrame，如果不存在则返回 None
        """
        cache_path = self._get_cache_path(cache_key)
        if cache_path.exists():
            try:
                return pd.read_parquet(cache_path)
            except Exception:
                # 缓存损坏，删除
                cache_path.unlink(missing_ok=True)
        return None
    
    def _save_to_cache(self, cache_key: str, data: pd.DataFrame) -> None:
        """保存数据到缓存
        
        Args:
            cache_key: 缓存键
            data: 要缓存的数据
        """
        cache_path = self._get_cache_path(cache_key)
        try:
            data.to_parquet(cache_path, index=True)
        except Exception as e:
            print(f"警告: 缓存数据失败: {e}")
    
    def get_instruments(self, market: str = "all") -> List[str]:
        """获取股票列表
        
        获取全市场A股股票代码列表。
        支持缓存机制，缓存有效期为1天。
        """
        self.initialize()
        
        # 生成缓存键
        cache_key = self._get_cache_key("get_instruments", market=market)
        cache_path = self._get_cache_path(cache_key)
        
        # 检查缓存是否存在且未过期（1天）
        if cache_path.exists():
            try:
                import time
                cache_age = time.time() - cache_path.stat().st_mtime
                if cache_age < 86400:  # 24小时 = 86400秒
                    cached_data = pd.read_parquet(cache_path)
                    instruments = cached_data['instrument'].tolist()
                    print(f"  从缓存加载股票列表: {len(instruments)} 只")
                    return instruments
            except Exception:
                cache_path.unlink(missing_ok=True)
        
        try:
            print(f"  从 Tushare 获取股票列表...")
            
            # 获取所有A股股票基本信息
            stocks = self._pro.stock_basic(exchange='', list_status='L', 
                                            fields='ts_code,symbol,name,area,industry,list_date')
            
            if stocks is None or stocks.empty:
                return []
            
            # 转换为 Qlib 格式 (SH600000, SZ000001)
            instruments = []
            for _, row in stocks.iterrows():
                ts_code = row['ts_code']
                # Tushare格式: 600000.SH -> Qlib格式: SH600000
                if '.' in ts_code:
                    code, exchange = ts_code.split('.')
                    if exchange == 'SH':
                        instruments.append(f"SH{code}")
                    elif exchange == 'SZ':
                        instruments.append(f"SZ{code}")
            
            # 保存到缓存
            cache_df = pd.DataFrame({'instrument': instruments})
            self._save_to_cache(cache_key, cache_df)
            print(f"  已缓存股票列表: {len(instruments)} 只")
            
            return instruments
            
        except Exception as e:
            print(f"获取股票列表失败: {e}")
            return []
    
    def get_index_components(self, index_code: str, date: str = None) -> List[str]:
        """获取指数成分股
        
        Args:
            index_code: 指数代码，支持多种格式
            date: 查询日期，None 表示最新成分
        """
        self.initialize()
        
        # 生成缓存键
        cache_key = self._get_cache_key("get_index_components", index_code=index_code, date=date or "latest")
        cache_path = self._get_cache_path(cache_key)
        
        # 检查缓存是否存在且未过期（1天）
        if cache_path.exists():
            try:
                import time
                cache_age = time.time() - cache_path.stat().st_mtime
                if cache_age < 86400:  # 24小时 = 86400秒
                    cached_data = pd.read_parquet(cache_path)
                    instruments = cached_data['instrument'].tolist()
                    print(f"  从缓存加载指数成分股: {len(instruments)} 只")
                    return instruments
            except Exception:
                cache_path.unlink(missing_ok=True)
        
        try:
            print(f"  从 Tushare 获取指数 {index_code} 成分股...")
            
            # 转换指数代码格式
            ts_index_code = self._convert_index_code(index_code)
            
            # 获取成分股
            if date:
                # 获取指定日期的成分股
                df = self._pro.index_weight(index_code=ts_index_code, trade_date=date.replace('-', ''))
            else:
                # 获取最新成分股
                df = self._pro.index_weight(index_code=ts_index_code)
            
            if df is None or df.empty:
                print(f"警告: 无法获取指数 {index_code} 的成分股")
                return []
            
            # 转换为 Qlib 格式
            instruments = []
            for _, row in df.iterrows():
                ts_code = row['con_code']  # 成分股代码
                if '.' in ts_code:
                    code, exchange = ts_code.split('.')
                    if exchange == 'SH':
                        instruments.append(f"SH{code}")
                    elif exchange == 'SZ':
                        instruments.append(f"SZ{code}")
            
            instruments = list(set(instruments))  # 去重
            
            # 保存到缓存
            cache_df = pd.DataFrame({'instrument': instruments})
            self._save_to_cache(cache_key, cache_df)
            print(f"  已缓存指数成分股: {len(instruments)} 只")
            
            return instruments
            
        except Exception as e:
            print(f"获取指数成分股失败: {e}")
            return []
    
    def _convert_index_code(self, code: str) -> str:
        """转换指数代码为 Tushare 格式"""
        return self.INDEX_CODE_MAP.get(code, code)
    
    def _convert_to_ts_code(self, qlib_code: str) -> str:
        """将 Qlib 代码转换为 Tushare 代码
        
        SH600000 -> 600000.SH
        SZ000001 -> 000001.SZ
        """
        if qlib_code.startswith("SH"):
            return qlib_code[2:] + ".SH"
        elif qlib_code.startswith("SZ"):
            return qlib_code[2:] + ".SZ"
        return qlib_code
    
    def get_price_data(
        self,
        instruments: List[str],
        fields: List[str],
        start_date: str,
        end_date: str,
        freq: str = "day"
    ) -> pd.DataFrame:
        """获取行情数据
        
        从 Tushare 获取 OHLCV 等价格数据，并转换为 Qlib 格式。
        支持缓存机制，避免重复调用 API。
        """
        self.initialize()
        
        if not instruments:
            return pd.DataFrame()

        requested_fields = list(fields)
        fetch_fields = list(fields)
        if "$vwap" in requested_fields:
            if "$amount" not in fetch_fields and "$money" not in fetch_fields:
                fetch_fields.append("$amount")
            if "$volume" not in fetch_fields:
                fetch_fields.append("$volume")
        
        # 生成缓存键
        cache_key = self._get_cache_key(
            "get_price_data",
            instruments=",".join(sorted(instruments)),
            fields=",".join(sorted(requested_fields)),
            start_date=start_date,
            end_date=end_date,
            freq=freq
        )
        
        # 尝试从缓存加载
        cached_data = self._load_from_cache(cache_key)
        if cached_data is not None:
            print(f"  从缓存加载价格数据: {len(cached_data)} 行")
            return cached_data
        
        # 转换字段名
        ts_fields = []
        field_mapping = {}  # 用于后续转换回 Qlib 格式
        
        for field in fetch_fields:
            if field == "$vwap":
                continue
            if field in self.FIELD_MAP:
                ts_field = self.FIELD_MAP[field]
                ts_fields.append(ts_field)
                field_mapping[ts_field] = field
            elif field.startswith("$"):
                # 去掉 $ 前缀
                ts_field = field[1:]
                ts_fields.append(ts_field)
                field_mapping[ts_field] = field
            else:
                ts_fields.append(field)
                field_mapping[field] = field
        
        # 检查是否需要获取复权因子
        need_factor = "$factor" in fields or "adj_factor" in ts_fields
        
        # 批量获取数据
        all_data = []
        
        # Tushare 有频率限制，需要分批获取
        batch_size = 100  # 每批最多100只股票
        
        print(f"  从 Tushare 获取 {len(instruments)} 只股票的价格数据...")
        
        for i in range(0, len(instruments), batch_size):
            batch = instruments[i:i+batch_size]
            
            for qlib_code in batch:
                try:
                    ts_code = self._convert_to_ts_code(qlib_code)
                    
                    # 获取日线数据
                    df = self._pro.daily(
                        ts_code=ts_code,
                        start_date=start_date.replace('-', ''),
                        end_date=end_date.replace('-', '')
                    )
                    
                    if df is not None and not df.empty:
                        # 如果需要复权因子，额外获取
                        if need_factor:
                            try:
                                factor_df = self._pro.adj_factor(
                                    ts_code=ts_code,
                                    start_date=start_date.replace('-', ''),
                                    end_date=end_date.replace('-', '')
                                )
                                if factor_df is not None and not factor_df.empty:
                                    # 合并复权因子数据
                                    df = df.merge(
                                        factor_df[['trade_date', 'adj_factor']],
                                        on='trade_date',
                                        how='left'
                                    )
                            except Exception as e:
                                print(f"获取 {qlib_code} 复权因子失败: {e}")
                        
                        df['instrument'] = qlib_code
                        all_data.append(df)
                        
                except Exception as e:
                    print(f"获取 {qlib_code} 数据失败: {e}")
                    continue
        
        if not all_data:
            return pd.DataFrame()
        
        # 合并所有数据
        data = pd.concat(all_data, ignore_index=True)
        
        # 转换为 Qlib 格式
        result = self._to_qlib_format(data, field_mapping)
        result = self._append_derived_price_fields(result, requested_fields)
        
        # 保存到缓存
        if not result.empty:
            self._save_to_cache(cache_key, result)
            print(f"  已缓存价格数据: {len(result)} 行")
        
        return result

    def _append_derived_price_fields(
        self,
        data: pd.DataFrame,
        requested_fields: List[str]
    ) -> pd.DataFrame:
        if data.empty:
            return data

        result = data.copy()
        if "$vwap" in requested_fields:
            amount_column = "$amount" if "$amount" in result.columns else "$money" if "$money" in result.columns else None
            volume_column = "$volume" if "$volume" in result.columns else None
            if amount_column is None or volume_column is None:
                raise ValueError("计算 VWAP 需要 amount/money 与 volume 字段")
            safe_volume = result[volume_column].where(result[volume_column] > 0)
            result["$vwap"] = (
                result[amount_column] * self.VWAP_AMOUNT_SCALE
            ).div(safe_volume * self.VWAP_VOLUME_SCALE).replace([np.inf, -np.inf], np.nan)

        ordered_columns = [field for field in requested_fields if field in result.columns]
        helper_columns = [column for column in result.columns if column not in ordered_columns]
        if ordered_columns:
            result = result[ordered_columns + helper_columns]
        if helper_columns:
            result = result.drop(columns=[column for column in helper_columns if column not in requested_fields])
        return result
    
    def _to_qlib_format(
        self,
        data: pd.DataFrame,
        field_mapping: Dict[str, str]
    ) -> pd.DataFrame:
        """将 Tushare 数据转换为 Qlib 格式
        
        Tushare格式: 每只股票一个 DataFrame
        Qlib 格式: MultiIndex (instrument, datetime)
        
        只保留请求的字段，统一输出格式。
        价格字段会进行后复权处理（与 Qlib 保持一致）。
        """
        # 转换日期格式
        if 'trade_date' in data.columns:
            data['trade_date'] = pd.to_datetime(data['trade_date'])
            data = data.rename(columns={'trade_date': 'datetime'})
        
        # 价格复权：转换为后复权价格（与 Qlib 一致）
        # Qlib 使用的价格是后复权价格 = 原始价格 * 复权因子 / 最新复权因子
        if 'adj_factor' in data.columns:
            # 获取最新的复权因子（用于归一化）
            latest_factor = data['adj_factor'].iloc[0]  # 数据按日期倒序，第一条是最新的
            
            # 对价格字段进行复权
            price_cols = ['open', 'high', 'low', 'close']
            for col in price_cols:
                if col in data.columns:
                    # 后复权价格 = 原始价格 * 复权因子 / 最新复权因子
                    data[col] = data[col] * data['adj_factor'] / latest_factor
        
        # 重命名字段
        for old_col, new_col in field_mapping.items():
            if old_col in data.columns:
                data = data.rename(columns={old_col: new_col})
        
        # 设置 MultiIndex (instrument, datetime)
        if 'instrument' in data.columns and 'datetime' in data.columns:
            data = data.set_index(['instrument', 'datetime'])
            data = data.sort_index()
            # 明确设置索引名称
            data.index = data.index.set_names(["instrument", "datetime"])
        
        # 只保留请求的字段（field_mapping 中的目标字段）
        requested_fields = list(field_mapping.values())
        available_fields = [f for f in requested_fields if f in data.columns]
        
        if available_fields:
            data = data[available_fields]
        
        return data

    def _build_source_field_mapping(
        self,
        fields: List[str],
        source_name: str
    ) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for field in fields:
            source = self.FIELD_SOURCE_MAP.get(field)
            if source != source_name:
                continue
            mapping[self.FIELD_MAP.get(field, field.lstrip("$"))] = field
        return mapping

    def _get_daily_basic_data(
        self,
        instruments: List[str],
        fields: List[str],
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        field_mapping = self._build_source_field_mapping(fields, "daily_basic")
        if not field_mapping:
            return pd.DataFrame()

        cache_key = self._get_cache_key(
            "get_daily_basic_data",
            instruments=",".join(sorted(instruments)),
            fields=",".join(sorted(field_mapping.values())),
            start_date=start_date,
            end_date=end_date,
        )
        cached_data = self._load_from_cache(cache_key)
        if cached_data is not None:
            print(f"  从缓存加载 daily_basic 数据: {len(cached_data)} 行")
            return cached_data

        rows = []
        requested_fields = ["ts_code", "trade_date", *field_mapping.keys()]
        unique_fields = ",".join(dict.fromkeys(requested_fields))

        for qlib_code in instruments:
            try:
                ts_code = self._convert_to_ts_code(qlib_code)
                df = self._pro.daily_basic(
                    ts_code=ts_code,
                    start_date=start_date.replace('-', ''),
                    end_date=end_date.replace('-', ''),
                    fields=unique_fields,
                )
                if df is None or df.empty:
                    continue
                df["instrument"] = qlib_code
                rows.append(df)
            except Exception as e:
                print(f"获取 {qlib_code} daily_basic 数据失败: {e}")

        if not rows:
            return pd.DataFrame()

        data = pd.concat(rows, ignore_index=True)
        result = self._to_qlib_format(data, field_mapping)
        if not result.empty:
            self._save_to_cache(cache_key, result)
            print(f"  已缓存 daily_basic 数据: {len(result)} 行")
        return result

    def _expand_static_fields(
        self,
        static_data: pd.DataFrame,
        instruments: List[str],
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        if static_data.empty:
            return pd.DataFrame()

        dates = self.get_trading_dates(start_date, end_date)
        if not dates:
            return pd.DataFrame()

        date_index = pd.to_datetime(dates)
        frames = []
        for instrument in instruments:
            if instrument not in static_data.index:
                continue
            values = static_data.loc[instrument]
            if isinstance(values, pd.Series):
                frame = pd.DataFrame([values.to_dict()] * len(date_index), index=date_index)
            else:
                frame = pd.DataFrame([values.iloc[0].to_dict()] * len(date_index), index=date_index)
            frame["instrument"] = instrument
            frame["datetime"] = date_index
            frames.append(frame)

        if not frames:
            return pd.DataFrame()

        result = pd.concat(frames, ignore_index=True).set_index(["instrument", "datetime"]).sort_index()
        result.index = result.index.set_names(["instrument", "datetime"])
        return result

    def _get_static_feature_data(
        self,
        instruments: List[str],
        fields: List[str],
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        field_mapping = self._build_source_field_mapping(fields, "stock_basic")
        if not field_mapping:
            return pd.DataFrame()

        cache_key = self._get_cache_key(
            "get_static_feature_data",
            instruments=",".join(sorted(instruments)),
            fields=",".join(sorted(field_mapping.values())),
            start_date=start_date,
            end_date=end_date,
        )
        cached_data = self._load_from_cache(cache_key)
        if cached_data is not None:
            print(f"  从缓存加载 stock_basic 数据: {len(cached_data)} 行")
            return cached_data

        requested_fields = ["ts_code", *field_mapping.keys()]
        unique_fields = ",".join(dict.fromkeys(requested_fields))

        try:
            stock_basic = self._pro.stock_basic(exchange='', list_status='L', fields=unique_fields)
        except Exception as e:
            print(f"获取 stock_basic 数据失败: {e}")
            return pd.DataFrame()

        if stock_basic is None or stock_basic.empty:
            return pd.DataFrame()

        stock_basic["instrument"] = stock_basic["ts_code"].apply(self._convert_from_ts_code)
        stock_basic = stock_basic[stock_basic["instrument"].isin(instruments)]
        if stock_basic.empty:
            return pd.DataFrame()

        rename_mapping = {source_name: target_name for source_name, target_name in field_mapping.items()}
        static_data = stock_basic.rename(columns=rename_mapping).set_index("instrument")
        selected_columns = list(rename_mapping.values())
        static_data = static_data[selected_columns]
        result = self._expand_static_fields(static_data, instruments, start_date, end_date)
        if not result.empty:
            self._save_to_cache(cache_key, result)
            print(f"  已缓存 stock_basic 数据: {len(result)} 行")
        return result

    def _convert_from_ts_code(self, ts_code: str) -> str:
        """将 Tushare 代码转换为 Qlib 代码"""
        if not isinstance(ts_code, str) or "." not in ts_code:
            return str(ts_code)
        code, exchange = ts_code.split(".")
        if exchange == "SH":
            return f"SH{code}"
        if exchange == "SZ":
            return f"SZ{code}"
        if exchange == "BJ":
            return f"BJ{code}"
        return ts_code
    
    def get_features(
        self,
        instruments: List[str],
        fields: List[str],
        start_date: str,
        end_date: str,
        freq: str = "day"
    ) -> pd.DataFrame:
        """获取特征数据（兼容 Qlib 接口）
        
        支持价格字段、每日指标字段和静态股票属性字段。
        """
        self.initialize()

        if not instruments or not fields:
            return pd.DataFrame()

        unsupported_fields = [
            field for field in fields
            if self.FIELD_SOURCE_MAP.get(field) is None
        ]
        if unsupported_fields:
            print(f"警告: 以下字段暂不支持: {unsupported_fields}")

        price_fields = [
            field for field in fields
            if self.FIELD_SOURCE_MAP.get(field) in {"daily", "adj_factor", "derived_price"}
        ]
        daily_basic_fields = [
            field for field in fields
            if self.FIELD_SOURCE_MAP.get(field) == "daily_basic"
        ]
        static_fields = [
            field for field in fields
            if self.FIELD_SOURCE_MAP.get(field) == "stock_basic"
        ]

        frames = []

        if price_fields:
            price_data = self.get_price_data(
                instruments, price_fields, start_date, end_date, freq
            )
            if not price_data.empty:
                frames.append(price_data)

        if daily_basic_fields:
            daily_basic_data = self._get_daily_basic_data(
                instruments, daily_basic_fields, start_date, end_date
            )
            if not daily_basic_data.empty:
                frames.append(daily_basic_data)

        if static_fields:
            static_data = self._get_static_feature_data(
                instruments, static_fields, start_date, end_date
            )
            if not static_data.empty:
                frames.append(static_data)

        if not frames:
            return pd.DataFrame()

        merged = frames[0]
        for frame in frames[1:]:
            merged = merged.join(frame, how="outer")

        merged = merged.sort_index()
        return merged
    
    def get_trading_dates(self, start_date: str, end_date: str) -> List[str]:
        """获取交易日列表
        
        获取指定日期范围内的所有交易日。
        """
        self.initialize()
        
        try:
            df = self._pro.trade_cal(
                exchange='SSE',
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                is_open='1'
            )
            
            if df is not None and not df.empty:
                dates = df['cal_date'].tolist()
                return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]
            
            return []
            
        except Exception as e:
            print(f"获取交易日列表失败: {e}")
            return []
    
    def get_all_industries(self) -> pd.DataFrame:
        """获取所有行业列表
        
        获取 Tushare 的行业分类体系。
        """
        self.initialize()
        
        try:
            return self._pro.industry_list()
        except Exception as e:
            print(f"获取行业列表失败: {e}")
            return pd.DataFrame()
    
    def get_industry_stocks(self, industry_code: str, date: str = None) -> List[str]:
        """获取行业成分股
        
        Args:
            industry_code: 行业代码
            date: 查询日期
        """
        self.initialize()
        
        try:
            # 通过股票基本信息中的 industry 字段筛选
            stocks = self._pro.stock_basic(exchange='', list_status='L',
                                            fields='ts_code,symbol,name,industry')
            
            if stocks is None or stocks.empty:
                return []
            
            # 筛选指定行业
            industry_stocks = stocks[stocks['industry'] == industry_code]
            
            # 转换为 Qlib 格式
            instruments = []
            for _, row in industry_stocks.iterrows():
                ts_code = row['ts_code']
                if '.' in ts_code:
                    code, exchange = ts_code.split('.')
                    if exchange == 'SH':
                        instruments.append(f"SH{code}")
                    elif exchange == 'SZ':
                        instruments.append(f"SZ{code}")
            
            return instruments
            
        except Exception as e:
            print(f"获取行业成分股失败: {e}")
            return []
