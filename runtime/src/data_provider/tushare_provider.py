"""
Tushare 数据提供者实现
封装 Tushare 的数据访问接口，适配 BaseDataProvider
"""

import hashlib
import os
import time
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
        self._request_interval_seconds = max(float(self.ts_config.get("request_interval_seconds", 0.35) or 0.35), 0.0)
        self._rate_limit_retry_seconds = max(float(self.ts_config.get("rate_limit_retry_seconds", 65.0) or 65.0), 0.0)
        self._rate_limit_retries = max(int(self.ts_config.get("rate_limit_retries", 1) or 1), 0)
        self._last_request_started_at = 0.0
        self._init_cache_meta()


    def _init_cache_meta(self):
        # v2 元数据文件：旧版本在批量请求被截断时可能记录了错误的“已完整缓存”区间
        # 改名后会自动重建元数据并重新补拉区间内缺失交易日。
        self.cache_meta_file = self.cache_dir / "cache_metadata_v2.json"
        self.cache_meta = {}
        if self.cache_meta_file.exists():
            try:
                import json
                with open(self.cache_meta_file, 'r', encoding='utf-8') as f:
                    self.cache_meta = json.load(f)
            except Exception as e:
                print(f"警告: 读取缓存元数据失败: {e}")
                self.cache_meta = {}

    def _save_cache_meta(self):
        try:
            import json
            with open(self.cache_meta_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache_meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"警告: 保存缓存元数据失败: {e}")

    def _get_unfetched_ranges(self, api_name: str, ts_code: str, start_date: str, end_date: str):
        from datetime import datetime, timedelta
        fetched = self.cache_meta.get(api_name, {}).get(ts_code, [])
        t_start = datetime.strptime(start_date, '%Y%m%d')
        t_end = datetime.strptime(end_date, '%Y%m%d')
        f = [[datetime.strptime(s, '%Y%m%d'), datetime.strptime(e, '%Y%m%d')] for s, e in fetched]
        f.sort()
        u = []
        c = t_start
        for s, e in f:
            if c < s:
                u.append([c, min(t_end, s - timedelta(days=1))])
            c = max(c, e + timedelta(days=1))
            if c > t_end:
                break
        if c <= t_end:
            u.append([c, t_end])
        return [[d[0].strftime('%Y%m%d'), d[1].strftime('%Y%m%d')] for d in u]

    def _add_fetched_range(self, api_name: str, ts_code: str, start_date: str, end_date: str):
        from datetime import datetime, timedelta
        if api_name not in self.cache_meta:
            self.cache_meta[api_name] = {}
        if ts_code not in self.cache_meta[api_name]:
            self.cache_meta[api_name][ts_code] = []
        fetched = self.cache_meta[api_name][ts_code]
        f = [[datetime.strptime(s, '%Y%m%d'), datetime.strptime(e, '%Y%m%d')] for s, e in fetched]
        f.append([datetime.strptime(start_date, '%Y%m%d'), datetime.strptime(end_date, '%Y%m%d')])
        f.sort()
        m = [f[0]]
        for s, e in f[1:]:
            p = m[-1]
            if s <= p[1] + timedelta(days=1):
                p[1] = max(p[1], e)
            else:
                m.append([s, e])
        self.cache_meta[api_name][ts_code] = [[d[0].strftime('%Y%m%d'), d[1].strftime('%Y%m%d')] for d in m]

    def _append_to_parquet(self, api_name: str, ts_code: str, df):
        if df is None or df.empty:
            return
        file_path = self.cache_dir / f"{api_name}_{ts_code}.parquet"
        if file_path.exists():
            try:
                old_df = pd.read_parquet(file_path)
                old_effective_empty = old_df.empty or old_df.dropna(how='all').empty
                new_effective_empty = df.empty or df.dropna(how='all').empty
                if old_effective_empty:
                    merged = df.copy()
                elif new_effective_empty:
                    merged = old_df.copy()
                elif 'trade_date' in old_df.columns and 'trade_date' in df.columns:
                    old_idx = old_df.drop_duplicates(subset=['trade_date'], keep='last').set_index('trade_date')
                    new_idx = df.drop_duplicates(subset=['trade_date'], keep='last').set_index('trade_date')
                    all_columns = old_idx.columns.union(new_idx.columns)
                    merged_idx = old_idx.reindex(columns=all_columns)
                    new_idx = new_idx.reindex(columns=all_columns)
                    merged_idx.loc[new_idx.index, :] = new_idx.values
                    merged = merged_idx.reset_index()
                else:
                    merged = pd.concat([old_df, df], ignore_index=True, sort=False)
                if 'trade_date' in merged.columns:
                    merged = merged.drop_duplicates(subset=['trade_date'], keep='last')
                df = merged
            except Exception:
                pass
        if 'trade_date' in df.columns:
            df = df.sort_values('trade_date')
        try:
            df.to_parquet(file_path, index=False)
        except Exception as e:
            print(f"警告: 保存缓存文件失败 {file_path}: {e}")

    def _load_from_ts_parquet(self, api_name: str, ts_code: str, start_date: str, end_date: str):
        file_path = self.cache_dir / f"{api_name}_{ts_code}.parquet"
        if not file_path.exists():
            # 兼容旧版缓存命名：{api}_{SZ000001}_{hash}.parquet / {api}_{SH600000}_{hash}.parquet
            if "." in ts_code:
                code, exchange = ts_code.split(".", 1)
                legacy_code = f"{exchange}{code}"
                legacy_files = sorted(
                    self.cache_dir.glob(f"{api_name}_{legacy_code}_*.parquet"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if legacy_files:
                    file_path = legacy_files[0]
                else:
                    return pd.DataFrame()
            else:
                return pd.DataFrame()
        try:
            df = pd.read_parquet(file_path)
            if 'trade_date' in df.columns:
                df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
            elif 'datetime' in df.columns:
                dt = pd.to_datetime(df['datetime'])
                mask = (dt >= pd.to_datetime(start_date)) & (dt <= pd.to_datetime(end_date))
                df = df.loc[mask]
            elif isinstance(df.index, pd.MultiIndex) and 'datetime' in (df.index.names or []):
                dt = pd.to_datetime(df.index.get_level_values('datetime'))
                mask = (dt >= pd.to_datetime(start_date)) & (dt <= pd.to_datetime(end_date))
                df = df.loc[mask]
            elif df.index.name == 'datetime':
                dt = pd.to_datetime(df.index)
                mask = (dt >= pd.to_datetime(start_date)) & (dt <= pd.to_datetime(end_date))
                df = df.loc[mask]
            return df
        except Exception:
            return pd.DataFrame()

    def _ensure_data_cached(self, api_name: str, instruments: list, start_date: str, end_date: str):
        missing_tasks = {}
        overall_start = end_date
        overall_end = start_date
        for qlib_code in instruments:
            ts_code = self._convert_to_ts_code(qlib_code)
            unfetched = self._get_unfetched_ranges(api_name, ts_code, start_date, end_date)
            if unfetched:
                missing_tasks[ts_code] = unfetched
                for s, e in unfetched:
                    overall_start = min(overall_start, s)
                    overall_end = max(overall_end, e)
        if not missing_tasks:
            return
        print(f"  从 Tushare 获取缺失的 {api_name} 数据 ({overall_start} 至 {overall_end}), 涉及 {len(missing_tasks)} 只股票...")
        fetched_count = 0
        missing_codes = list(missing_tasks.keys())
        total = len(missing_codes)
        for i, ts_code in enumerate(missing_codes, start=1):
            if api_name == "daily":
                df = self._call_pro_api("daily", ts_code=ts_code, start_date=overall_start, end_date=overall_end)
            elif api_name == "adj_factor":
                df = self._call_pro_api("adj_factor", ts_code=ts_code, start_date=overall_start, end_date=overall_end)
            elif api_name == "daily_basic":
                df = self._call_pro_api("daily_basic", ts_code=ts_code, start_date=overall_start, end_date=overall_end)
            else:
                continue

            if df is None:
                continue

            if not df.empty:
                self._append_to_parquet(api_name, ts_code, df)
                self._add_fetched_range(api_name, ts_code, overall_start, overall_end)
                fetched_count += 1

            if i % 50 == 0 or i == total:
                print(f"  {api_name} 拉取进度: {i}/{total}")

        if fetched_count < total:
            print(f"  警告: {api_name} 仅成功更新 {fetched_count}/{total} 只股票缓存，未成功部分将在后续请求继续补拉")
        self._save_cache_meta()

    def _wait_for_request_slot(self) -> None:
        if self._request_interval_seconds <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_request_started_at
        remaining = self._request_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _is_rate_limit_error(self, error: Exception) -> bool:
        message = str(error)
        return "每分钟最多访问该接口" in message or "频次" in message or "rate limit" in message.lower()

    def _call_pro_api(self, method_name: str, **kwargs) -> Any:
        if self._pro is None:
            raise RuntimeError("Tushare Pro 接口尚未初始化")
        method = getattr(self._pro, method_name)
        for attempt in range(self._rate_limit_retries + 1):
            self._wait_for_request_slot()
            self._last_request_started_at = time.monotonic()
            try:
                return method(**kwargs)
            except Exception as error:
                if attempt >= self._rate_limit_retries or not self._is_rate_limit_error(error):
                    raise
                wait_seconds = max(self._rate_limit_retry_seconds, self._request_interval_seconds)
                print(f"  Tushare 接口触发频率限制，{wait_seconds:.1f} 秒后重试 {method_name}")
                time.sleep(wait_seconds)
        raise RuntimeError(f"Tushare 接口调用失败: {method_name}")
    
    def initialize(self) -> None:
        """初始化 Tushare 连接
        
        从配置文件中获取 API Key 进行初始化。
        """
        if self._initialized:
            return
        
        try:
            import tushare as ts
            self._ts = ts
            
            # 获取 API Key
            self._api_key = self.ts_config.get("api_key")
            
            if not self._api_key:
                raise ValueError(
                    "Tushare API Key 未配置，请在 env.yaml 的 tushare 配置中指定 api_key"
                )
            
            # 初始化 Pro 接口
            self._pro = ts.pro_api(self._api_key)
            
            # 验证 API Key 是否有效
            try:
                # 尝试获取一条数据来验证
                test_df = self._call_pro_api("trade_cal", exchange='SSE', limit=1)
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
            stocks = self._call_pro_api(
                "stock_basic",
                exchange='',
                list_status='L',
                fields='ts_code,symbol,name,area,industry,list_date',
            )
            
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
                df = self._call_pro_api("index_weight", index_code=ts_index_code, trade_date=date.replace('-', ''))
            else:
                # 获取最新成分股
                df = self._call_pro_api("index_weight", index_code=ts_index_code)
            
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
        """获取股票价格数据"""
        self.initialize()
        
        field_mapping = {
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'vol': 'volume',
            'amount': 'amount',
            'vwap': 'vwap'
        }
        
        requested_fields = fields if fields else list(field_mapping.values())
        need_factor = 'factor' in requested_fields or '$factor' in requested_fields
        
        self._ensure_data_cached('daily', instruments, start_date.replace('-', ''), end_date.replace('-', ''))
        if need_factor:
            self._ensure_data_cached('adj_factor', instruments, start_date.replace('-', ''), end_date.replace('-', ''))
            
        cached_data_list = []
        for qlib_code in instruments:
            ts_code = self._convert_to_ts_code(qlib_code)
            
            daily_df = self._load_from_ts_parquet('daily', ts_code, start_date.replace('-', ''), end_date.replace('-', ''))
            if daily_df.empty:
                continue
                
            if need_factor:
                adj_df = self._load_from_ts_parquet('adj_factor', ts_code, start_date.replace('-', ''), end_date.replace('-', ''))
                if not adj_df.empty and 'adj_factor' in adj_df.columns:
                    daily_df = pd.merge(daily_df, adj_df[['trade_date', 'adj_factor']], on='trade_date', how='left')
                else:
                    daily_df['adj_factor'] = 1.0
                    
            daily_df = daily_df.rename(columns={'trade_date': 'datetime'})
            daily_df['datetime'] = pd.to_datetime(daily_df['datetime'])
            daily_df['instrument'] = qlib_code
            
            if 'vwap' in requested_fields or '$vwap' in requested_fields:
                daily_df['vwap'] = daily_df['amount'] * 1000 / (daily_df['vol'] * 100 + 1e-8)
                
            rename_dict = {k: v for k, v in field_mapping.items()}
            if need_factor:
                rename_dict['adj_factor'] = 'factor'
            daily_df = daily_df.rename(columns=rename_dict)
            
            for req_field in requested_fields:
                if req_field.startswith('$'):
                    base_field = req_field[1:]
                    if base_field in daily_df.columns:
                        daily_df[req_field] = daily_df[base_field]
                        
            available_fields = ['datetime', 'instrument'] + [f for f in requested_fields if f in daily_df.columns]
            daily_df = daily_df[available_fields]
            cached_data_list.append(daily_df)
            
        if not cached_data_list:
            return pd.DataFrame()
        final_df = pd.concat(cached_data_list, ignore_index=True)
        final_df = final_df.set_index(['instrument', 'datetime']).sort_index()
        # Ensure we return Qlib MultiIndex format
        final_df.index = final_df.index.set_names(["instrument", "datetime"])
        
        # apply requested fields ordering
        ordered_columns = [field for field in requested_fields if field in final_df.columns]
        final_df = final_df[ordered_columns]
        
        return final_df

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
            
        self._ensure_data_cached('daily_basic', instruments, start_date.replace('-', ''), end_date.replace('-', ''))
        
        cached_data_list = []
        for qlib_code in instruments:
            ts_code = self._convert_to_ts_code(qlib_code)
            df = self._load_from_ts_parquet('daily_basic', ts_code, start_date.replace('-', ''), end_date.replace('-', ''))
            if df.empty:
                continue

            if 'trade_date' in df.columns:
                df = df.rename(columns={'trade_date': 'datetime'})
            elif 'datetime' not in df.columns:
                if isinstance(df.index, pd.MultiIndex) and 'datetime' in (df.index.names or []):
                    df = df.reset_index()
                elif df.index.name == 'datetime':
                    df = df.reset_index()

            if 'datetime' not in df.columns:
                continue

            df['datetime'] = pd.to_datetime(df['datetime'])
            df['instrument'] = qlib_code
            
            df = df.rename(columns=field_mapping)
            available_fields = ['datetime', 'instrument'] + [f for f in field_mapping.values() if f in df.columns]
            df = df[available_fields]
            cached_data_list.append(df)
            
        if not cached_data_list:
            return pd.DataFrame()
        final_df = pd.concat(cached_data_list, ignore_index=True)
        final_df = final_df.set_index(['instrument', 'datetime']).sort_index()
        final_df.index = final_df.index.set_names(["instrument", "datetime"])
        return final_df

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
            stock_basic = self._call_pro_api("stock_basic", exchange='', list_status='L', fields=unique_fields)
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

        import pandas as pd
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
            df = self._call_pro_api(
                "trade_cal",
                exchange='SSE',
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                is_open='1',
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
            return self._call_pro_api("industry_list")
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
            stocks = self._call_pro_api(
                "stock_basic",
                exchange='',
                list_status='L',
                fields='ts_code,symbol,name,industry',
            )
            
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
