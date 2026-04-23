"""
Tushare 数据提供者实现
封装 Tushare 的数据访问接口，适配 BaseDataProvider
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd
import numpy as np

from .base_provider import BaseDataProvider


class TushareProvider(BaseDataProvider):
    """Tushare 数据提供者
    
    封装 Tushare 的数据访问功能，提供与 Qlib 兼容的统一接口。
    支持实时数据获取，数据更新到最新交易日。
    
    文档: https://tushare.pro/document/2
    """

    # Internal units are normalized to amount=RMB, volume=shares.
    VWAP_AMOUNT_SCALE = 1.0
    VWAP_VOLUME_SCALE = 1.0
    
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
        self.cache_dir = Path(self.ts_config.get("data_cache_dir", "./data/tushare_cache")).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ts = None  # tushare 模块
        self._pro = None  # Tushare Pro 接口
        self._api_key = None
        self._request_interval_seconds = max(float(self.ts_config.get("request_interval_seconds", 0.35) or 0.35), 0.0)
        self._rate_limit_retry_seconds = max(float(self.ts_config.get("rate_limit_retry_seconds", 65.0) or 65.0), 0.0)
        self._rate_limit_retries = max(int(self.ts_config.get("rate_limit_retries", 1) or 1), 0)
        self._api_call_timeout_seconds = max(float(self.ts_config.get("api_call_timeout_seconds", 30.0) or 30.0), 0.0)
        self._enable_meta_reconcile = bool(self.ts_config.get("enable_meta_reconcile", False))
        self._latest_trade_lag_days = max(int(self.ts_config.get("latest_trade_lag_days", 1) or 1), 0)
        self._last_request_started_at = 0.0
        self._cache_meta_changed = False
        self._actual_cache_range: Dict[Tuple[str, str], Optional[Tuple[str, str]]] = {}
        self._cache_dir_warned = False
        self._index_component_month_cache: Dict[Tuple[str, str], Tuple[str, List[str]]] = {}
        self._list_date_cache: dict[str, str] = {}
        self._delist_date_cache: dict[str, str] = {}
        self._list_date_cache_loaded = False
        self._latest_trade_date_cache: Optional[str] = None
        self._has_open_trade_day_cache: Dict[Tuple[str, str], bool] = {}
        self._init_cache_meta()


    def _init_cache_meta(self):
        # v2 元数据文件：旧版本在批量请求被截断时可能记录了错误的“已完整缓存”区间
        # 改名后会自动重建元数据并重新补拉区间内缺失交易日。
        self.cache_meta_file = self.cache_dir / "cache_metadata_v2.json"
        self.cache_meta = {}
        if self.cache_meta_file.exists():
            try:
                with open(self.cache_meta_file, 'r', encoding='utf-8') as f:
                    self.cache_meta = json.load(f)
            except Exception as e:
                print(f"警告: 读取缓存元数据失败: {e}")
                self.cache_meta = {}

    def _save_cache_meta(self):
        try:
            with open(self.cache_meta_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache_meta, f, ensure_ascii=False, indent=2)
            self._cache_meta_changed = False
        except Exception as e:
            print(f"警告: 保存缓存元数据失败: {e}")

    def _warn_multiple_cache_dirs_once(self) -> None:
        if self._cache_dir_warned:
            return
        self._cache_dir_warned = True
        try:
            parent = self.cache_dir.parent
            candidates = sorted(path for path in parent.glob("tushare_cache*") if path.is_dir())
            if len(candidates) <= 1:
                return
            other_dirs = [path for path in candidates if path.resolve() != self.cache_dir]
            if not other_dirs:
                return
            print("缓存目录机制提示:")
            print(f"  当前生效目录: {self.cache_dir}")
            print("  检测到同级其他缓存目录(不会自动使用):")
            for path in other_dirs:
                parquet_count = len(list(path.glob("*.parquet")))
                has_meta = (path / "cache_metadata_v2.json").exists() or (path / "cache_metadata.json").exists()
                print(f"    - {path} (parquet={parquet_count}, meta={'Y' if has_meta else 'N'})")
        except Exception:
            return

    def _load_cached_date_range_from_parquet(self, api_name: str, ts_code: str) -> Optional[Tuple[str, str]]:
        cache_key = (api_name, ts_code)
        if cache_key in self._actual_cache_range:
            return self._actual_cache_range[cache_key]
        file_path = self.cache_dir / f"{api_name}_{ts_code}.parquet"
        if not file_path.exists() and "." in ts_code:
            code, exchange = ts_code.split(".", 1)
            legacy_code = f"{exchange}{code}"
            legacy_files = sorted(
                self.cache_dir.glob(f"{api_name}_{legacy_code}_*.parquet"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if legacy_files:
                file_path = legacy_files[0]
        if not file_path.exists():
            self._actual_cache_range[cache_key] = None
            return None
        try:
            frame = pd.read_parquet(file_path, columns=["trade_date"])
            if frame.empty or "trade_date" not in frame.columns:
                self._actual_cache_range[cache_key] = None
                return None
            dates = pd.to_datetime(frame["trade_date"], errors="coerce").dropna()
            if dates.empty:
                self._actual_cache_range[cache_key] = None
                return None
            min_date = dates.min().strftime("%Y%m%d")
            max_date = dates.max().strftime("%Y%m%d")
            result = (min_date, max_date)
            self._actual_cache_range[cache_key] = result
            return result
        except Exception:
            self._actual_cache_range[cache_key] = None
            return None

    def _reconcile_meta_with_parquet(self, api_name: str, ts_code: str) -> None:
        api_meta = self.cache_meta.setdefault(api_name, {})
        fetched = list(api_meta.get(ts_code, []))
        actual_range = self._load_cached_date_range_from_parquet(api_name, ts_code)
        if actual_range is None:
            if fetched:
                api_meta.pop(ts_code, None)
                self._cache_meta_changed = True
            return
        actual_start, actual_end = actual_range
        if not fetched:
            api_meta[ts_code] = [[actual_start, actual_end]]
            self._cache_meta_changed = True
            return
        repaired: list[list[str]] = []
        for start, end in fetched:
            s = max(str(start), actual_start)
            e = min(str(end), actual_end)
            if s <= e:
                repaired.append([s, e])
        if not repaired:
            repaired = [[actual_start, actual_end]]
        repaired.sort(key=lambda item: item[0])
        merged: list[list[str]] = [repaired[0]]
        for start, end in repaired[1:]:
            prev = merged[-1]
            if start <= prev[1]:
                prev[1] = max(prev[1], end)
            else:
                merged.append([start, end])
        if merged != fetched:
            api_meta[ts_code] = merged
            self._cache_meta_changed = True

    def _get_unfetched_ranges(self, api_name: str, ts_code: str, start_date: str, end_date: str):
        from datetime import datetime, timedelta
        if self._enable_meta_reconcile:
            self._reconcile_meta_with_parquet(api_name, ts_code)
        latest_trade_date = self._latest_open_trade_date()
        if latest_trade_date and end_date > latest_trade_date:
            end_date = latest_trade_date
        # 上市前区间不需要拉取，避免永远存在“前缀缺口”导致反复补拉。
        list_date = self._get_list_date(ts_code)
        if list_date and list_date > start_date:
            start_date = list_date
        # 退市后区间不需要拉取，避免“尾部缺口”在每次运行重复出现。
        delist_date = self._get_delist_date(ts_code)
        if delist_date and delist_date < end_date:
            end_date = delist_date
        if start_date > end_date:
            return []
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
        self._cache_meta_changed = True

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
            self._actual_cache_range.pop((api_name, ts_code), None)
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
            if self._cache_meta_changed:
                self._save_cache_meta()
            return
        missing_codes = list(missing_tasks.keys())
        total = len(missing_codes)
        if api_name in {"daily", "adj_factor", "daily_basic"} and not self._has_open_trade_day_between(overall_start, overall_end):
            print(
                f"  跳过 {api_name} 补拉: 请求区间 {overall_start} 至 {overall_end} 无交易日，"
                f"标记 {total} 只股票为已覆盖"
            )
            for ts_code in missing_codes:
                self._add_fetched_range(api_name, ts_code, overall_start, overall_end)
            if self._cache_meta_changed:
                self._save_cache_meta()
            return
        print(f"  从 Tushare 获取缺失的 {api_name} 数据 ({overall_start} 至 {overall_end}), 涉及 {total} 只股票...")
        fetched_count = 0
        partial_tail_count = 0
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
                # IMPORTANT: only mark the actually returned date span as fetched.
                # Some upstream responses may stop before requested end_date.
                if "trade_date" in df.columns:
                    trade_dates = pd.to_datetime(df["trade_date"], errors="coerce").dropna()
                    if not trade_dates.empty:
                        actual_start = trade_dates.min().strftime("%Y%m%d")
                        actual_end = trade_dates.max().strftime("%Y%m%d")
                        self._add_fetched_range(api_name, ts_code, actual_start, actual_end)
                        if actual_end < end_date:
                            partial_tail_count += 1
                    else:
                        self._add_fetched_range(api_name, ts_code, overall_start, overall_end)
                else:
                    self._add_fetched_range(api_name, ts_code, overall_start, overall_end)
                fetched_count += 1

            if i % 50 == 0 or i == total:
                print(f"  {api_name} 拉取进度: {i}/{total}")

        if fetched_count < total:
            print(f"  警告: {api_name} 仅成功更新 {fetched_count}/{total} 只股票缓存，未成功部分将在后续请求继续补拉")
        if partial_tail_count > 0:
            print(
                f"  提示: {api_name} 有 {partial_tail_count} 只股票返回数据未覆盖请求末日 {end_date}，"
                "这些股票会在后续请求中继续尝试补拉"
            )
        if self._cache_meta_changed:
            self._save_cache_meta()

    def _get_list_date(self, ts_code: str) -> Optional[str]:
        if not self._list_date_cache_loaded:
            self._load_list_date_cache()
        return self._list_date_cache.get(ts_code)

    def _get_delist_date(self, ts_code: str) -> Optional[str]:
        if not self._list_date_cache_loaded:
            self._load_list_date_cache()
        return self._delist_date_cache.get(ts_code)

    def _load_list_date_cache(self) -> None:
        if self._list_date_cache_loaded:
            return
        self._list_date_cache_loaded = True
        try:
            frames: list[pd.DataFrame] = []
            for status in ("L", "D", "P"):
                stock_basic = self._call_pro_api(
                    "stock_basic",
                    exchange="",
                    list_status=status,
                    fields="ts_code,list_date,delist_date",
                )
                if stock_basic is not None and not stock_basic.empty:
                    frames.append(stock_basic)
            if not frames:
                return
            merged = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts_code"], keep="first")
            self._list_date_cache = {
                str(row["ts_code"]): str(row["list_date"])
                for _, row in merged.iterrows()
                if pd.notna(row.get("list_date")) and str(row.get("list_date"))
            }
            self._delist_date_cache = {
                str(row["ts_code"]): str(row["delist_date"])
                for _, row in merged.iterrows()
                if pd.notna(row.get("delist_date")) and str(row.get("delist_date"))
            }
        except Exception:
            # 上市日期仅用于优化缓存补拉，不应影响主流程。
            self._list_date_cache = {}
            self._delist_date_cache = {}

    def _latest_open_trade_date(self) -> str:
        if self._latest_trade_date_cache:
            return self._latest_trade_date_cache
        today = pd.Timestamp.today().normalize()
        start = (today - pd.Timedelta(days=40)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        try:
            cal = self._call_pro_api(
                "trade_cal",
                exchange="SSE",
                start_date=start,
                end_date=end,
                is_open="1",
            )
            if cal is not None and not cal.empty and "cal_date" in cal.columns:
                open_dates = sorted(str(item) for item in cal["cal_date"].dropna().tolist())
                if open_dates:
                    index = max(0, len(open_dates) - 1 - int(self._latest_trade_lag_days))
                    self._latest_trade_date_cache = open_dates[index]
                    return self._latest_trade_date_cache
                self._latest_trade_date_cache = end
                return self._latest_trade_date_cache
        except Exception:
            pass
        self._latest_trade_date_cache = end
        return self._latest_trade_date_cache

    def _has_open_trade_day_between(self, start_date: str, end_date: str) -> bool:
        if start_date > end_date:
            return False
        cache_key = (start_date, end_date)
        cached = self._has_open_trade_day_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            cal = self._call_pro_api(
                "trade_cal",
                exchange="SSE",
                start_date=start_date,
                end_date=end_date,
                is_open="1",
            )
            has_open = bool(cal is not None and not cal.empty)
        except Exception:
            # 保守策略：交易日判断失败时不阻断正常补拉流程。
            has_open = True
        self._has_open_trade_day_cache[cache_key] = has_open
        return has_open

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
            self._warn_multiple_cache_dirs_once()
            
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
        target_trade_date = str(date or "").replace("-", "")
        month_key = ""
        if target_trade_date and len(target_trade_date) >= 6:
            month_key = target_trade_date[:6]
        
        # 检查缓存是否存在且未过期（1天）
        if cache_path.exists():
            try:
                import time
                cache_age = time.time() - cache_path.stat().st_mtime
                if cache_age < 86400:  # 24小时 = 86400秒
                    cached_data = pd.read_parquet(cache_path)
                    fetch_status = "ok"
                    if "fetch_status" in cached_data.columns and not cached_data.empty:
                        fetch_status = str(cached_data["fetch_status"].iloc[0] or "ok").strip().lower()
                    instruments = (
                        cached_data["instrument"].dropna().astype(str).tolist()
                        if "instrument" in cached_data.columns
                        else []
                    )
                    if fetch_status != "ok":
                        print(f"  使用指数成分股负缓存: {index_code} ({date or 'latest'})")
                        return []
                    print(f"  从缓存加载指数成分股: {len(instruments)} 只")
                    return instruments
            except Exception:
                cache_path.unlink(missing_ok=True)

        # 同月观察日快速复用：同一月份只要命中过一次成分快照，后续观察日直接复用，
        # 避免每个观察日都请求一次 index_weight。
        if target_trade_date and month_key:
            month_cache_key = (str(index_code), month_key)
            month_hit = self._index_component_month_cache.get(month_cache_key)
            if month_hit:
                source_trade_date, instruments = month_hit
                if source_trade_date <= target_trade_date and instruments:
                    cache_df = pd.DataFrame({"instrument": instruments})
                    cache_df["fetch_status"] = "ok"
                    cache_df["source_trade_date"] = source_trade_date
                    self._save_to_cache(cache_key, cache_df)
                    print(
                        f"  使用同月成分缓存: {index_code} {target_trade_date} "
                        f"-> {source_trade_date} ({len(instruments)}只)"
                    )
                    return instruments
        
        try:
            print(f"  从 Tushare 获取指数 {index_code} 成分股...")
            ts_index_code = self._convert_index_code(index_code)

            # 先尝试目标观察日（或最新）
            instruments = self._fetch_index_components_once(ts_index_code, target_trade_date or None)
            used_trade_date = target_trade_date or "latest"

            # 观察日失败时，沿交易日向前回溯（有上限），避免大量重复失败请求。
            if (not instruments) and target_trade_date:
                max_fallback_days = max(int(self.ts_config.get("index_component_fallback_max_open_days", 20) or 20), 0)
                fallback_dates = self._list_fallback_trade_dates(target_trade_date, max_fallback_days)
                for fallback_trade_date in fallback_dates:
                    instruments = self._fetch_index_components_once(ts_index_code, fallback_trade_date)
                    if instruments:
                        used_trade_date = fallback_trade_date
                        print(
                            f"  指数成分回溯命中: {index_code} "
                            f"{target_trade_date} -> {fallback_trade_date}"
                        )
                        break

            if not instruments:
                print(f"警告: 无法获取指数 {index_code} 的成分股")
                fail_cache_df = pd.DataFrame({"instrument": [], "fetch_status": []})
                fail_cache_df.loc[0, "fetch_status"] = "failed"
                self._save_to_cache(cache_key, fail_cache_df)
                return []

            cache_df = pd.DataFrame({"instrument": instruments})
            cache_df["fetch_status"] = "ok"
            if target_trade_date:
                cache_df["source_trade_date"] = used_trade_date
                if month_key:
                    self._index_component_month_cache[(str(index_code), month_key)] = (
                        used_trade_date,
                        list(instruments),
                    )
            self._save_to_cache(cache_key, cache_df)
            print(f"  已缓存指数成分股: {len(instruments)} 只")
            return instruments

        except Exception as e:
            print(f"获取指数成分股失败: {e}")
            fail_cache_df = pd.DataFrame({"instrument": [], "fetch_status": []})
            fail_cache_df.loc[0, "fetch_status"] = "failed"
            self._save_to_cache(cache_key, fail_cache_df)
            return []

    def _fetch_index_components_once(self, ts_index_code: str, trade_date: Optional[str]) -> List[str]:
        if trade_date:
            df = self._call_pro_api("index_weight", index_code=ts_index_code, trade_date=trade_date)
        else:
            df = self._call_pro_api("index_weight", index_code=ts_index_code)
        if df is None or df.empty:
            return []
        instruments: list[str] = []
        for _, row in df.iterrows():
            ts_code = str(row.get("con_code", ""))
            if "." not in ts_code:
                continue
            code, exchange = ts_code.split(".")
            if exchange == "SH":
                instruments.append(f"SH{code}")
            elif exchange == "SZ":
                instruments.append(f"SZ{code}")
        return sorted(set(instruments))

    def _list_fallback_trade_dates(self, target_trade_date: str, max_open_days: int) -> List[str]:
        if max_open_days <= 0:
            return []
        end = pd.Timestamp(target_trade_date)
        start = (end - pd.Timedelta(days=max(120, max_open_days * 8))).strftime("%Y%m%d")
        cal = self._call_pro_api(
            "trade_cal",
            exchange="SSE",
            start_date=start,
            end_date=end.strftime("%Y%m%d"),
            is_open="1",
        )
        if cal is None or cal.empty or "cal_date" not in cal.columns:
            return []
        open_dates = sorted(str(item) for item in cal["cal_date"].dropna().tolist())
        # 回溯列表不包含目标日自己
        fallback_dates = [item for item in open_dates if item < target_trade_date]
        if not fallback_dates:
            return []
        return list(reversed(fallback_dates[-max_open_days:]))
    
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
        price_adjust = str(self.config.get("price_adjust", "none") or "none").strip().lower()
        price_adjust_reference_date = str(
            self.config.get("price_adjust_reference_date", "") or ""
        ).strip()
        base_price_fields = {"open", "high", "low", "close"}
        has_price_fields = any(
            (isinstance(field, str) and field in base_price_fields)
            or (isinstance(field, str) and field.startswith("$") and field[1:] in base_price_fields)
            for field in requested_fields
        )
        has_vwap_fields = any(
            (isinstance(field, str) and field == "vwap")
            or (isinstance(field, str) and field.startswith("$") and field[1:] == "vwap")
            for field in requested_fields
        )
        need_factor = 'factor' in requested_fields or '$factor' in requested_fields
        # Keep VWAP on the same adjustment basis as close/open/high/low when requested.
        need_adjust = price_adjust in {"pre", "post"} and (has_price_fields or has_vwap_fields)
        need_adj_factor = need_factor or need_adjust
        
        self._ensure_data_cached('daily', instruments, start_date.replace('-', ''), end_date.replace('-', ''))
        if need_adj_factor:
            self._ensure_data_cached('adj_factor', instruments, start_date.replace('-', ''), end_date.replace('-', ''))
            
        cached_data_list = []
        for qlib_code in instruments:
            ts_code = self._convert_to_ts_code(qlib_code)
            
            daily_df = self._load_from_ts_parquet('daily', ts_code, start_date.replace('-', ''), end_date.replace('-', ''))
            if daily_df.empty:
                continue
                
            if need_adj_factor:
                adj_df = self._load_from_ts_parquet('adj_factor', ts_code, start_date.replace('-', ''), end_date.replace('-', ''))
                if not adj_df.empty and 'adj_factor' in adj_df.columns:
                    daily_df = pd.merge(daily_df, adj_df[['trade_date', 'adj_factor']], on='trade_date', how='left')
                else:
                    daily_df['adj_factor'] = 1.0
            vwap_adjust_multiplier: pd.Series | None = None
            if need_adjust:
                daily_df = daily_df.sort_values("trade_date")
                trade_dates = pd.to_datetime(daily_df["trade_date"])
                adjust_series = pd.to_numeric(daily_df.get("adj_factor"), errors="coerce").ffill().bfill()
                ref_factor = float("nan")
                if price_adjust == "pre" and price_adjust_reference_date:
                    reference_date = pd.Timestamp(price_adjust_reference_date).normalize()
                    reference_mask = trade_dates <= reference_date
                    if reference_mask.any():
                        ref_factor = adjust_series.loc[reference_mask].iloc[-1]
                    elif not adjust_series.empty:
                        # 极端情况下如果参考日前没有数据，退化为首个可用交易日。
                        ref_factor = adjust_series.iloc[0]
                elif price_adjust == "pre":
                    ref_factor = adjust_series.iloc[-1]
                else:
                    ref_factor = adjust_series.iloc[0]
                if pd.notna(ref_factor) and float(ref_factor) != 0.0:
                    vwap_adjust_multiplier = adjust_series / float(ref_factor)
                    for col in base_price_fields:
                        if col in daily_df.columns:
                            daily_df[col] = pd.to_numeric(daily_df[col], errors="coerce") * vwap_adjust_multiplier
                    
            daily_df = daily_df.rename(columns={'trade_date': 'datetime'})
            daily_df['datetime'] = pd.to_datetime(daily_df['datetime'])
            daily_df['instrument'] = qlib_code
            if "vol" in daily_df.columns:
                # Tushare daily.vol is in lots (100 shares). Normalize to shares.
                daily_df["vol"] = pd.to_numeric(daily_df["vol"], errors="coerce") * 100.0
            if "amount" in daily_df.columns:
                # Tushare daily.amount is in thousand RMB. Normalize to RMB.
                daily_df["amount"] = pd.to_numeric(daily_df["amount"], errors="coerce") * 1000.0
            
            if 'vwap' in requested_fields or '$vwap' in requested_fields:
                safe_volume = pd.to_numeric(daily_df["vol"], errors="coerce").where(lambda s: s > 0)
                daily_df["vwap"] = (
                    pd.to_numeric(daily_df["amount"], errors="coerce")
                    .div(safe_volume)
                    .replace([np.inf, -np.inf], np.nan)
                )
                if vwap_adjust_multiplier is not None:
                    daily_df['vwap'] = pd.to_numeric(daily_df['vwap'], errors="coerce") * vwap_adjust_multiplier
                
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
