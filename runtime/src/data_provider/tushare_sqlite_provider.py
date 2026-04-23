import sqlite3
import json
import pickle
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from .tushare_provider import TushareProvider

class TushareSQLiteProvider(TushareProvider):
    """
    基于 SQLite 缓存的 Tushare 数据提供者。
    继承自 TushareProvider，重写其基于 Parquet 和 JSON 的缓存机制，
    采用全数据库管理（方案A：横纵表结合），彻底解决节假日和空数据循环拉取问题。
    """
    
    def __init__(self, config: Dict[str, Any]):
        # 初始化父类，但不依赖它的 cache_meta
        super().__init__(config)
        self.db_path = self.cache_dir / "tushare_cache.db"
        self._conn = None
        self._init_sqlite_db()
        
    def _get_conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        return self._conn

    def _load_from_cache(self, cache_key: str) -> Optional[pd.DataFrame]:
        """从 SQLite 通用缓存读取 DataFrame（替代父类 parquet 通用缓存）。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT payload FROM generic_cache WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
            if not row or row[0] is None:
                return None
            payload = bytes(row[0])
            data = pickle.loads(payload)
            if isinstance(data, pd.DataFrame):
                return data
        except Exception:
            # 读缓存失败视作未命中，并清理损坏记录，避免重复报错。
            try:
                conn.execute("DELETE FROM generic_cache WHERE cache_key=?", (cache_key,))
                conn.commit()
            except Exception:
                pass
        return None

    def _save_to_cache(self, cache_key: str, data: pd.DataFrame) -> None:
        """写入 SQLite 通用缓存（替代父类 parquet 通用缓存）。"""
        conn = self._get_conn()
        try:
            payload = sqlite3.Binary(pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL))
            conn.execute(
                """
                INSERT INTO generic_cache(cache_key, payload, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (cache_key, payload),
            )
            conn.commit()
        except Exception:
            # 通用缓存失败不应影响主流程
            pass
        
    def _init_sqlite_db(self):
        conn = self._get_conn()
        c = conn.cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS trade_cal (exchange TEXT, cal_date TEXT, is_open INTEGER, PRIMARY KEY(exchange, cal_date)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS stock_basic (ts_code TEXT PRIMARY KEY, symbol TEXT, name TEXT, area TEXT, industry TEXT, market TEXT, list_date TEXT, delist_date TEXT, list_status TEXT);
            CREATE TABLE IF NOT EXISTS index_weight (index_code TEXT, trade_date TEXT, con_code TEXT, weight REAL, PRIMARY KEY(index_code, trade_date, con_code)) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS idx_index_weight_date ON index_weight(trade_date);
            CREATE TABLE IF NOT EXISTS stock_daily_price (ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, pre_close REAL, change REAL, pct_chg REAL, vol REAL, amount REAL, adj_factor REAL, PRIMARY KEY(ts_code, trade_date)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS stock_fundamental (ts_code TEXT, trade_date TEXT, data_type TEXT, features TEXT, PRIMARY KEY(ts_code, trade_date, data_type)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS generic_cache (cache_key TEXT PRIMARY KEY, payload BLOB NOT NULL, updated_at TEXT);
        """)
        conn.commit()

    def _sync_trade_cal(self, start_date: str, end_date: str):
        """同步交易日历到数据库"""
        conn = self._get_conn()
        df = self._call_pro_api('trade_cal', exchange='SSE', start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return
        rows = []
        for _, row in df.iterrows():
            exchange = str(row.get("exchange", "SSE") or "SSE")
            cal_date = str(row.get("cal_date", "") or "")
            if not cal_date:
                continue
            is_open = int(row.get("is_open", 0) or 0)
            rows.append((exchange, cal_date, is_open))
        if not rows:
            return
        conn.executemany(
            """
            INSERT INTO trade_cal(exchange, cal_date, is_open)
            VALUES (?, ?, ?)
            ON CONFLICT(exchange, cal_date) DO UPDATE SET
                is_open=excluded.is_open
            """,
            rows,
        )
        conn.commit()

    def _sync_stock_basic(self):
        """同步股票基础信息到数据库"""
        conn = self._get_conn()
        total_count = conn.execute("SELECT COUNT(*) FROM stock_basic").fetchone()
        total_count = int(total_count[0]) if total_count and total_count[0] is not None else 0
        l_count = conn.execute(
            "SELECT COUNT(*) FROM stock_basic WHERE list_status='L'"
        ).fetchone()
        l_count = int(l_count[0]) if l_count and l_count[0] is not None else 0
        dp_count = conn.execute(
            "SELECT COUNT(*) FROM stock_basic WHERE list_status IN ('D','P')"
        ).fetchone()
        dp_count = int(dp_count[0]) if dp_count and dp_count[0] is not None else 0

        statuses: List[str] = []
        if total_count == 0:
            statuses = ["L", "D", "P"]
        else:
            # 兼容历史缓存：按缺失状态增量补齐，不再假设某一类一定存在。
            if l_count == 0:
                statuses.append("L")
            if dp_count == 0:
                statuses.extend(["D", "P"])

        if not statuses:
            return

        for status in statuses:
            df = self._call_pro_api('stock_basic', exchange='', list_status=status)
            if df is None or df.empty:
                continue
            rows = []
            for _, row in df.iterrows():
                ts_code = str(row.get("ts_code", "") or "")
                if not ts_code:
                    continue
                rows.append(
                    (
                        ts_code,
                        str(row.get("symbol", "") or ""),
                        str(row.get("name", "") or ""),
                        str(row.get("area", "") or ""),
                        str(row.get("industry", "") or ""),
                        str(row.get("market", "") or ""),
                        str(row.get("list_date", "") or ""),
                        str(row.get("delist_date", "") or ""),
                        str(row.get("list_status", status) or status),
                    )
                )
            if not rows:
                continue
            conn.executemany(
                """
                INSERT INTO stock_basic(
                    ts_code, symbol, name, area, industry, market,
                    list_date, delist_date, list_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ts_code) DO UPDATE SET
                    symbol=excluded.symbol,
                    name=excluded.name,
                    area=excluded.area,
                    industry=excluded.industry,
                    market=excluded.market,
                    list_date=excluded.list_date,
                    delist_date=excluded.delist_date,
                    list_status=excluded.list_status
                """,
                rows,
            )
        conn.commit()

    def _get_unfetched_ranges(self, api_name: str, ts_code: str, start_date: str, end_date: str):
        """SQLite 口径缺口审计：返回区间内仍未覆盖的交易日范围。"""
        conn = self._get_conn()

        valid_dates = {
            row[0]
            for row in conn.execute(
                f"""
                SELECT c.cal_date FROM trade_cal c
                LEFT JOIN stock_basic s ON s.ts_code = '{ts_code}'
                WHERE c.is_open=1 AND c.cal_date BETWEEN '{start_date}' AND '{end_date}'
                AND (s.list_date IS NULL OR c.cal_date >= s.list_date)
                """
            ).fetchall()
        }
        if not valid_dates:
            return []

        if api_name == "daily":
            existing_dates = {
                row[0]
                for row in conn.execute(
                    f"""
                    SELECT trade_date FROM stock_daily_price
                    WHERE ts_code='{ts_code}' AND open IS NOT NULL
                    AND trade_date BETWEEN '{start_date}' AND '{end_date}'
                    """
                ).fetchall()
            }
        elif api_name == "adj_factor":
            existing_dates = {
                row[0]
                for row in conn.execute(
                    f"""
                    SELECT trade_date FROM stock_daily_price
                    WHERE ts_code='{ts_code}' AND adj_factor IS NOT NULL
                    AND trade_date BETWEEN '{start_date}' AND '{end_date}'
                    """
                ).fetchall()
            }
        elif api_name == "daily_basic":
            existing_dates = {
                row[0]
                for row in conn.execute(
                    f"""
                    SELECT trade_date FROM stock_fundamental
                    WHERE ts_code='{ts_code}' AND data_type='{api_name}'
                    AND trade_date BETWEEN '{start_date}' AND '{end_date}'
                    """
                ).fetchall()
            }
        else:
            return []

        missing_set = set(valid_dates - existing_dates)
        if not missing_set:
            return []

        # 按开市日顺序压缩为连续范围，便于审计统计。
        ordered_open_dates = sorted(valid_dates)
        ranges: list[list[str]] = []
        start = None
        end = None
        for date in ordered_open_dates:
            if date not in missing_set:
                if start is not None and end is not None:
                    ranges.append([start, end])
                    start = end = None
                continue
            if start is None:
                start = end = date
            else:
                end = date
        if start is not None and end is not None:
            ranges.append([start, end])
        return ranges

    def _ensure_data_cached(self, api_name: str, instruments: list, start_date: str, end_date: str):
        """
        重写父类的 _ensure_data_cached 方法。
        使用 SQLite 查询真实缺失的交易日（排除周末、节假日、停牌、未上市）。
        """
        if not instruments:
            return
            
        self._sync_trade_cal(start_date, end_date)
        self._sync_stock_basic()
        conn = self._get_conn()
        
        missing_tasks = {}
        for qlib_code in instruments:
            ts_code = self._convert_to_ts_code(qlib_code)
            
            # 1. 查询该股票在请求区间内真正应该有的交易日（开市且在上市期间）
            cal_sql = f"""
                SELECT c.cal_date FROM trade_cal c 
                LEFT JOIN stock_basic s ON s.ts_code = '{ts_code}'
                WHERE c.is_open=1 AND c.cal_date BETWEEN '{start_date}' AND '{end_date}'
                AND (s.list_date IS NULL OR c.cal_date >= s.list_date)
            """
            valid_dates = {row[0] for row in conn.execute(cal_sql).fetchall()}
            
            # 2. 查询数据库中已经存在的日期
            if api_name == 'daily':
                check_sql = f"SELECT trade_date FROM stock_daily_price WHERE ts_code='{ts_code}' AND open IS NOT NULL AND trade_date BETWEEN '{start_date}' AND '{end_date}'"
            elif api_name == 'adj_factor':
                check_sql = f"SELECT trade_date FROM stock_daily_price WHERE ts_code='{ts_code}' AND adj_factor IS NOT NULL AND trade_date BETWEEN '{start_date}' AND '{end_date}'"
            elif api_name == 'daily_basic':
                check_sql = f"SELECT trade_date FROM stock_fundamental WHERE ts_code='{ts_code}' AND data_type='{api_name}' AND trade_date BETWEEN '{start_date}' AND '{end_date}'"
            else:
                continue
                
            existing_dates = {row[0] for row in conn.execute(check_sql).fetchall()}
            
            # 3. 计算缺失的日期
            missing_dates = sorted(list(valid_dates - existing_dates))
            if missing_dates:
                # 简化：取缺失日期的最小和最大值作为一个请求区间去 Tushare 拉取
                missing_tasks[ts_code] = (missing_dates[0], missing_dates[-1])
                
        if not missing_tasks:
            return
            
        total = len(missing_tasks)
        print(f"  [SQLite] 从 Tushare 获取缺失的 {api_name} 数据, 涉及 {total} 只股票...")
        
        fetched_count = 0
        for i, (ts_code, (req_start, req_end)) in enumerate(missing_tasks.items(), 1):
            df = self._call_pro_api(api_name, ts_code=ts_code, start_date=req_start, end_date=req_end)
            
            if df is not None and not df.empty:
                if api_name == 'daily':
                    for _, row in df.iterrows():
                        conn.execute('''
                            INSERT INTO stock_daily_price (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(ts_code, trade_date) DO UPDATE SET
                            open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, pre_close=excluded.pre_close, change=excluded.change, pct_chg=excluded.pct_chg, vol=excluded.vol, amount=excluded.amount
                        ''', (row['ts_code'], row['trade_date'], row.get('open'), row.get('high'), row.get('low'), row.get('close'), row.get('pre_close'), row.get('change'), row.get('pct_chg'), row.get('vol'), row.get('amount')))
                elif api_name == 'adj_factor':
                    for _, row in df.iterrows():
                        conn.execute('''
                            INSERT INTO stock_daily_price (ts_code, trade_date, adj_factor)
                            VALUES (?, ?, ?)
                            ON CONFLICT(ts_code, trade_date) DO UPDATE SET
                            adj_factor=excluded.adj_factor
                        ''', (row['ts_code'], row['trade_date'], row.get('adj_factor')))
                elif api_name == 'daily_basic':
                    for _, row in df.iterrows():
                        # 把基本面数据存为 JSON 格式
                        features = row.drop(['ts_code', 'trade_date']).to_dict()
                        # 处理 NaN 值，JSON 不支持 NaN
                        features = {k: (v if pd.notna(v) else None) for k, v in features.items()}
                        conn.execute('''
                            INSERT INTO stock_fundamental (ts_code, trade_date, data_type, features)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(ts_code, trade_date, data_type) DO UPDATE SET
                            features=excluded.features
                        ''', (row['ts_code'], row['trade_date'], api_name, json.dumps(features)))
                conn.commit()
            
            # 无论是否有数据返回，我们都把“有效但未返回数据的交易日”插入空记录。
            # 这样代表“已确认这天无数据（比如停牌）”，下次就不会再拉了，彻底解决死循环！
            if api_name in ['daily', 'adj_factor']:
                if api_name == 'daily':
                    existing = {r[0] for r in conn.execute(f"SELECT trade_date FROM stock_daily_price WHERE ts_code='{ts_code}' AND open IS NOT NULL AND trade_date BETWEEN '{req_start}' AND '{req_end}'").fetchall()}
                else:
                    existing = {r[0] for r in conn.execute(f"SELECT trade_date FROM stock_daily_price WHERE ts_code='{ts_code}' AND adj_factor IS NOT NULL AND trade_date BETWEEN '{req_start}' AND '{req_end}'").fetchall()}
                cal_sql = f"SELECT cal_date FROM trade_cal WHERE is_open=1 AND cal_date BETWEEN '{req_start}' AND '{req_end}'"
                valid = {r[0] for r in conn.execute(cal_sql).fetchall()}
                still_missing = valid - existing
                
                # 插入占位记录：值为一个极小的负数或 NULL
                for d in still_missing:
                    if api_name == 'daily':
                        conn.execute(
                            """
                            INSERT INTO stock_daily_price (ts_code, trade_date, open)
                            VALUES (?, ?, -1)
                            ON CONFLICT(ts_code, trade_date) DO UPDATE SET
                                open=excluded.open
                            """,
                            (ts_code, d),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO stock_daily_price (ts_code, trade_date, adj_factor)
                            VALUES (?, ?, -1)
                            ON CONFLICT(ts_code, trade_date) DO UPDATE SET
                                adj_factor=excluded.adj_factor
                            """,
                            (ts_code, d),
                        )
            elif api_name == 'daily_basic':
                existing = {r[0] for r in conn.execute(f"SELECT trade_date FROM stock_fundamental WHERE ts_code='{ts_code}' AND data_type='{api_name}' AND trade_date BETWEEN '{req_start}' AND '{req_end}'").fetchall()}
                cal_sql = f"SELECT cal_date FROM trade_cal WHERE is_open=1 AND cal_date BETWEEN '{req_start}' AND '{req_end}'"
                valid = {r[0] for r in conn.execute(cal_sql).fetchall()}
                still_missing = valid - existing
                for d in still_missing:
                    conn.execute(
                        """
                        INSERT INTO stock_fundamental (ts_code, trade_date, data_type, features)
                        VALUES (?, ?, ?, '{}')
                        ON CONFLICT(ts_code, trade_date, data_type) DO UPDATE SET
                            features=excluded.features
                        """,
                        (ts_code, d, api_name),
                    )
            conn.commit()
            
            fetched_count += 1
            if i % 50 == 0 or i == total:
                print(f"  [SQLite] {api_name} 拉取进度: {i}/{total}")

    def _load_from_ts_parquet(self, api_name: str, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """
        重写父类的方法，名称虽然叫 _load_from_ts_parquet，但实际是从 SQLite 读取数据。
        返回的 DataFrame 格式必须与原 Parquet 方案一致，这样父类的 get_price_data 就无需修改。
        """
        conn = self._get_conn()
        
        # 使用 -1 作为过滤条件，因为我们在上面用 -1 作为空数据的占位符
        if api_name == 'daily':
            # 不要 SELECT adj_factor，以免与后面的 pd.merge 冲突产生 _x, _y 后缀
            df = pd.read_sql(f"SELECT ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount FROM stock_daily_price WHERE ts_code='{ts_code}' AND open IS NOT NULL AND open != -1 AND trade_date BETWEEN '{start_date}' AND '{end_date}'", conn)
        elif api_name == 'adj_factor':
            df = pd.read_sql(f"SELECT ts_code, trade_date, adj_factor FROM stock_daily_price WHERE ts_code='{ts_code}' AND adj_factor IS NOT NULL AND adj_factor != -1 AND trade_date BETWEEN '{start_date}' AND '{end_date}'", conn)
        elif api_name == 'daily_basic':
            raw = pd.read_sql(f"SELECT trade_date, features FROM stock_fundamental WHERE ts_code='{ts_code}' AND data_type='{api_name}' AND trade_date BETWEEN '{start_date}' AND '{end_date}' AND features != '{{}}'", conn)
            if not raw.empty:
                # 解析 JSON 字段
                features_df = pd.json_normalize(raw['features'].apply(json.loads))
                df = pd.concat([raw[['trade_date']], features_df], axis=1)
                df['ts_code'] = ts_code
            else:
                df = pd.DataFrame()
        else:
            return pd.DataFrame()
            
        return df if df is not None else pd.DataFrame()

    def get_instruments(self, market: str = "all") -> List[str]:
        """从 SQLite 的 stock_basic 表读取股票列表。"""
        self.initialize()
        self._sync_stock_basic()
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT ts_code FROM stock_basic WHERE list_status='L'"
        ).fetchall()
        instruments = sorted(
            {self._convert_from_ts_code(str(row[0])) for row in rows if row and row[0]}
        )
        print(f"  从 SQLite 缓存加载股票列表: {len(instruments)} 只")
        return instruments

    def _get_static_feature_data(
        self,
        instruments: List[str],
        fields: List[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """从 SQLite 的 stock_basic 表读取静态字段并扩展到日期索引。"""
        field_mapping = self._build_source_field_mapping(fields, "stock_basic")
        if not field_mapping:
            return pd.DataFrame()

        self._sync_stock_basic()
        conn = self._get_conn()

        requested_fields = ["ts_code", *field_mapping.keys()]
        unique_fields = list(dict.fromkeys(requested_fields))
        select_sql = ", ".join(unique_fields)
        stock_basic = pd.read_sql_query(
            f"SELECT {select_sql} FROM stock_basic WHERE list_status='L'",
            conn,
        )
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
            print(f"  从 SQLite 缓存加载 stock_basic 数据: {len(result)} 行")
        return result

    def get_index_components(self, index_code: str, date: str = None) -> List[str]:
        """重写：按“指定日期 -> 逐日回溯”顺序，先查缓存，再查 Tushare。"""
        ts_index_code = self._convert_index_code(index_code)
        conn = self._get_conn()
        normalized_date = str(date or "").replace("-", "")
        configured_max_open_days = self.config.get(
            "index_component_search_max_open_days",
            self.ts_config.get("index_component_search_max_open_days", 2000),
        )
        max_search_open_days = max(int(configured_max_open_days or 2000), 1)

        def _load_snapshot(trade_date: str) -> List[str]:
            rows = conn.execute(
                "SELECT con_code FROM index_weight WHERE index_code=? AND trade_date=?",
                (ts_index_code, trade_date),
            ).fetchall()
            if not rows:
                return []
            return sorted({self._convert_from_ts_code(r[0]) for r in rows})

        def _cache_snapshot(trade_date: str, components: List[str]) -> None:
            if not trade_date or not components:
                return
            try:
                df = pd.DataFrame(
                    {
                        "index_code": ts_index_code,
                        "trade_date": trade_date,
                        "con_code": [self._convert_to_ts_code(c) for c in components],
                        "weight": 1.0,
                    }
                )
                df.to_sql("index_weight", conn, if_exists="append", index=False)
            except Exception:
                # 忽略主键冲突等写缓存异常，不影响主流程返回。
                pass

        def _iter_search_dates(target_trade_date: str) -> List[str]:
            # 按“目标日 -> 向前开市日”顺序组织搜索队列。
            fallback_dates = self._list_fallback_trade_dates(target_trade_date, max_search_open_days)
            dates = [target_trade_date]
            for item in fallback_dates:
                if item and item not in dates:
                    dates.append(item)
            return dates

        if normalized_date:
            # 严格按“每个日期先缓存、再 Tushare”的顺序逐日回溯。
            for trade_date in _iter_search_dates(normalized_date):
                cached_codes = _load_snapshot(trade_date)
                if cached_codes:
                    if trade_date != normalized_date:
                        print(
                            f"指数成分回溯命中缓存: {index_code} "
                            f"{normalized_date} -> {trade_date} ({len(cached_codes)}只)"
                        )
                    return cached_codes
                fetched_codes = self._fetch_index_components_once(ts_index_code, trade_date)
                if fetched_codes:
                    _cache_snapshot(trade_date, fetched_codes)
                    print(
                        f"指数成分回溯命中Tushare: {index_code} "
                        f"{normalized_date} -> {trade_date} ({len(fetched_codes)}只)"
                    )
                    return fetched_codes
            print(
                f"警告: 指数成分逐日回溯失败: {index_code} date={normalized_date}, "
                f"max_search_open_days={max_search_open_days}"
            )
            return []
        else:
            # 未指定日期时，优先返回库中最新快照。
            latest_row = conn.execute(
                "SELECT MAX(trade_date) FROM index_weight WHERE index_code=?",
                (ts_index_code,),
            ).fetchone()
            latest_trade_date = str(latest_row[0]) if latest_row and latest_row[0] else ""
            if latest_trade_date:
                codes = _load_snapshot(latest_trade_date)
                if codes:
                    return codes

        # 无日期场景下数据库没有：直接回源 Tushare，并写入 SQLite。
        fetched_codes = self._fetch_index_components_once(ts_index_code, None)
        if fetched_codes:
            latest_open = self._latest_open_trade_date()
            if latest_open:
                _cache_snapshot(latest_open, fetched_codes)
            return fetched_codes
        return []
