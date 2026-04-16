"""
米筐 RQData 数据提供者实现
封装米筐的数据访问接口，适配 BaseDataProvider
"""

import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

from .base_provider import BaseDataProvider


class RicequantProvider(BaseDataProvider):
    """米筐 RQData 数据提供者
    
    封装米筐的数据访问功能，提供与 Qlib 兼容的统一接口。
    支持实时数据获取，数据更新到最新交易日。
    """
    
    # 字段映射：Qlib格式 -> 米筐格式
    FIELD_MAP = {
        "$open": "open",
        "$high": "high",
        "$low": "low",
        "$close": "close",
        "$volume": "volume",
        "$money": "total_turnover",
        "$vwap": "vwap",
        "$factor": "factor",
    }
    
    # 反向映射：米筐格式 -> Qlib格式
    REVERSE_FIELD_MAP = {v: k for k, v in FIELD_MAP.items()}
    
    # 指数代码映射：统一格式 -> 米筐格式
    INDEX_CODE_MAP = {
        "000300.XSHG": "000300.XSHG",  # 沪深300
        "SH000300": "000300.XSHG",
        "000905.XSHG": "000905.XSHG",  # 中证500
        "SH000905": "000905.XSHG",
        "000852.XSHG": "000852.XSHG",  # 中证1000
        "SH000852": "000852.XSHG",
        "000016.XSHG": "000016.XSHG",  # 上证50
        "SH000016": "000016.XSHG",
        "399001.XSHE": "399001.XSHE",  # 深证成指
        "SZ399001": "399001.XSHE",
        "399006.XSHE": "399006.XSHE",  # 创业板指
        "SZ399006": "399006.XSHE",
        "399005.XSHE": "399005.XSHE",  # 中小板指
        "SZ399005": "399005.XSHE",
    }
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.rq_config = config.get("ricequant", {})
        self.cache_dir = Path(self.rq_config.get("data_cache_dir", "./data/ricequant_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._rq = None  # rqdatac 模块
        self._username = None
        self._password = None
    
    def initialize(self) -> None:
        """初始化米筐连接
        
        从配置文件中获取认证信息进行初始化。
        """
        if self._initialized:
            return
        
        try:
            import rqdatac
            self._rq = rqdatac
            
            # 获取认证信息
            self._username = self.rq_config.get("username")
            self._password = self.rq_config.get("password")
            
            if not self._username or not self._password:
                raise ValueError(
                    "米筐账号未配置，请在 env.yaml 的 ricequant 配置中指定 username 和 password"
                )
            
            # 初始化连接
            self._rq.init(username=self._username, password=self._password)
            
            # 立即验证认证是否成功（通过调用一个简单的 API）
            try:
                # 尝试获取流量配额来验证认证
                quota = self._rq.user.get_quota()
                if quota is None:
                    raise RuntimeError("认证失败，请检查用户名和密码")
            except Exception as auth_error:
                if "auth" in str(auth_error).lower() or "authentication" in str(auth_error).lower():
                    raise RuntimeError(f"米筐认证失败: {auth_error}")
                # 其他错误可能是 API 问题，继续尝试
            
            self._initialized = True
            
            # 验证连接并打印信息
            print(f"米筐数据连接成功")
            
            try:
                info = self._rq.info()
                if info:
                    print(f"  版本: {info.get('version', 'unknown')}")
                    print(f"  服务器: {info.get('server_address', 'unknown')}")
            except:
                pass
            
            # 检查流量配额
            try:
                quota = self._rq.user.get_quota()
                if quota and quota.get("bytes_limit", 0) > 0:
                    used_mb = quota.get("bytes_used", 0) / 1024 / 1024
                    limit_mb = quota.get("bytes_limit", 0) / 1024 / 1024
                    print(f"  流量配额: {used_mb:.2f} MB / {limit_mb:.2f} MB")
            except:
                pass
            
        except ImportError:
            raise ImportError(
                "rqdatac 未安装，请执行: pip install rqdatac\n"
                "米筐数据服务需要单独购买，请访问 https://www.ricequant.com/welcome/rqdata"
            )
        except Exception as e:
            raise RuntimeError(f"米筐初始化失败: {e}")
    
    def get_instruments(self, market: str = "all") -> List[str]:
        """获取股票列表
        
        获取全市场A股股票代码列表。
        """
        self.initialize()
        
        # 获取所有A股股票（CS = Common Stock）
        stocks = self._rq.all_instruments(type="CS")
        
        if stocks is None or stocks.empty:
            return []
        
        return stocks["order_book_id"].tolist()
    
    def get_index_components(self, index_code: str, date: str = None) -> List[str]:
        """获取指数成分股
        
        Args:
            index_code: 指数代码，支持多种格式
            date: 查询日期，None 表示最新成分
        """
        self.initialize()
        
        # 转换指数代码格式
        rq_index_code = self._convert_index_code(index_code)
        
        # 获取成分股
        components = self._rq.index_components(rq_index_code, date=date)
        
        if components is None:
            print(f"警告: 无法获取指数 {index_code} 的成分股")
            return []
        
        return components.tolist()
    
    def _convert_index_code(self, code: str) -> str:
        """转换指数代码为米筐格式"""
        return self.INDEX_CODE_MAP.get(code, code)
    
    def get_price_data(
        self,
        instruments: List[str],
        fields: List[str],
        start_date: str,
        end_date: str,
        freq: str = "day"
    ) -> pd.DataFrame:
        """获取行情数据
        
        从米筐获取 OHLCV 等价格数据，并转换为 Qlib 格式。
        """
        self.initialize()
        
        if not instruments:
            return pd.DataFrame()
        
        # 转换字段名
        rq_fields = []
        field_mapping = {}  # 用于后续转换回 Qlib 格式
        
        for field in fields:
            if field in self.FIELD_MAP:
                rq_field = self.FIELD_MAP[field]
                rq_fields.append(rq_field)
                field_mapping[rq_field] = field
            elif field.startswith("$"):
                # 去掉 $ 前缀
                rq_field = field[1:]
                rq_fields.append(rq_field)
                field_mapping[rq_field] = field
            else:
                rq_fields.append(field)
                field_mapping[field] = field
        
        try:
            # 获取数据（前复权）
            data = self._rq.get_price(
                order_book_ids=instruments,
                start_date=start_date,
                end_date=end_date,
                frequency=freq,
                fields=rq_fields,
                adjust_type="pre",  # 前复权
            )
        except Exception as e:
            print(f"获取价格数据失败: {e}")
            return pd.DataFrame()
        
        if data is None or data.empty:
            return pd.DataFrame()
        
        # 转换为 Qlib 格式
        return self._to_qlib_format(data, field_mapping)
    
    def _to_qlib_format(
        self,
        data: pd.DataFrame,
        field_mapping: Dict[str, str]
    ) -> pd.DataFrame:
        """将米筐数据转换为 Qlib 格式
        
        米筐格式: MultiIndex (date, order_book_id)
        Qlib 格式: MultiIndex (instrument, datetime)
        """
        # 重置索引
        data = data.reset_index()
        
        # 重命名字段
        for old_col, new_col in field_mapping.items():
            if old_col in data.columns:
                data = data.rename(columns={old_col: new_col})
        
        # 确保有必要的列
        if "date" not in data.columns and "datetime" in data.columns:
            data = data.rename(columns={"datetime": "date"})
        
        # 设置 MultiIndex (instrument, datetime)
        index_cols = []
        if "order_book_id" in data.columns:
            index_cols.append("order_book_id")
        if "date" in data.columns:
            index_cols.append("date")
        
        if len(index_cols) == 2:
            data = data.set_index(index_cols)
            data.index.names = ["instrument", "datetime"]
        
        return data
    
    def get_features(
        self,
        instruments: List[str],
        fields: List[str],
        start_date: str,
        end_date: str,
        freq: str = "day"
    ) -> pd.DataFrame:
        """获取特征数据（兼容 Qlib 接口）
        
        目前主要支持价格类字段，财务字段需要额外实现。
        """
        # 分离价格字段和其他字段
        price_fields = [f for f in fields if f.startswith("$")]
        other_fields = [f for f in fields if not f.startswith("$")]
        
        # 获取价格数据
        if price_fields:
            price_data = self.get_price_data(
                instruments, price_fields, start_date, end_date, freq
            )
        else:
            price_data = pd.DataFrame()
        
        # TODO: 实现财务指标等其他字段的获取
        # 米筐提供了 get_fundamentals() 等接口可以获取财务数据
        if other_fields:
            print(f"警告: 以下字段暂不支持: {other_fields}")
        
        return price_data
    
    def get_trading_dates(self, start_date: str, end_date: str) -> List[str]:
        """获取交易日列表
        
        获取指定日期范围内的所有交易日。
        """
        self.initialize()
        
        dates = self._rq.get_trading_dates(start_date, end_date)
        return [d.strftime("%Y-%m-%d") for d in dates] if dates is not None else []
    
    def get_all_industries(self) -> pd.DataFrame:
        """获取所有行业列表
        
        获取米筐的行业分类体系。
        """
        self.initialize()
        
        return self._rq.get_industries()
    
    def get_industry_stocks(self, industry_code: str, date: str = None) -> List[str]:
        """获取行业成分股
        
        Args:
            industry_code: 行业代码
            date: 查询日期
        """
        self.initialize()
        
        stocks = self._rq.get_industry(industry_code, date=date)
        return stocks["order_book_id"].tolist() if stocks is not None else []
