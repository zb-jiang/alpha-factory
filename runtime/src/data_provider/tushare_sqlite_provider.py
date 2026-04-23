import sqlite3
import json
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
        """)
        conn.commit()

    def _sync_trade_cal(self, start_date: str, end_date: str):
        """同步交易日历到数据库"""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM trade_cal WHERE cal_date BETWEEN ? AND ?", (start_date, end_date))
        if c.fetchone()[0] == 0:
            df = self._call_pro_api('trade_cal', exchange='SSE', start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                df[['exchange', 'cal_date', 'is_open']].to_sql('trade_cal', conn, if_exists='append', index=False)

    def _sync_stock_basic(self):
        """同步股票基础信息到数据库"""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM stock_basic")
        if c.fetchone()[0] == 0:
            df = self._call_pro_api('stock_basic', exchange='', list_status='L')
            if df is not None and not df.empty:
                # 只保留数据库中有的字段
                cols = ['ts_code', 'symbol', 'name', 'area', 'industry', 'market', 'list_date', 'delist_date', 'list_status']
                df_cols = [col for col in cols if col in df.columns]
                df[df_cols].to_sql('stock_basic', conn, if_exists='append', index=False)

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
                        conn.execute("INSERT OR IGNORE INTO stock_daily_price (ts_code, trade_date, open) VALUES (?, ?, -1)", (ts_code, d))
                    else:
                        conn.execute("INSERT OR IGNORE INTO stock_daily_price (ts_code, trade_date, adj_factor) VALUES (?, ?, -1)", (ts_code, d))
            elif api_name == 'daily_basic':
                existing = {r[0] for r in conn.execute(f"SELECT trade_date FROM stock_fundamental WHERE ts_code='{ts_code}' AND data_type='{api_name}' AND trade_date BETWEEN '{req_start}' AND '{req_end}'").fetchall()}
                cal_sql = f"SELECT cal_date FROM trade_cal WHERE is_open=1 AND cal_date BETWEEN '{req_start}' AND '{req_end}'"
                valid = {r[0] for r in conn.execute(cal_sql).fetchall()}
                still_missing = valid - existing
                for d in still_missing:
                    conn.execute("INSERT OR IGNORE INTO stock_fundamental (ts_code, trade_date, data_type, features) VALUES (?, ?, ?, '{}')", (ts_code, d, api_name))
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

    def get_index_components(self, index_code: str, date: str = None) -> List[str]:
        """重写：优先从数据库获取指数成分股"""
        ts_index_code = self._convert_index_code(index_code)
        conn = self._get_conn()
        if date:
            # 获取距离指定日期最近的成分股变动
            c = conn.execute("SELECT con_code FROM index_weight WHERE index_code=? AND trade_date<=? ORDER BY trade_date DESC", (ts_index_code, date))
            rows = c.fetchall()
            if rows:
                return [self._convert_from_ts_code(r[0]) for r in rows]
        
        # 数据库没有，调用父类（Tushare API）获取
        components = super().get_index_components(index_code, date)
        if components and date:
            try:
                # 缓存到数据库
                df = pd.DataFrame({
                    'index_code': ts_index_code, 
                    'trade_date': date, 
                    'con_code': [self._convert_to_ts_code(c) for c in components], 
                    'weight': 1.0
                })
                df.to_sql('index_weight', conn, if_exists='append', index=False)
            except Exception as e:
                print(f"缓存指数成分股到数据库失败: {e}")
        return components
