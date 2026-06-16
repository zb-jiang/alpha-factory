"""
数据提供者抽象基类
定义统一的数据接口，支持多种数据源实现
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import pandas as pd


class BaseDataProvider(ABC):
    """数据提供者抽象基类
    
    提供统一的数据访问接口（当前实现为 Tushare）。
    所有具体的数据提供者都需要实现这些方法。
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._initialized = False

    def update_config(self, config: Dict[str, Any]) -> None:
        """更新运行时配置。

        默认实现只覆盖 `self.config`，具体 provider 可按需同步内部派生参数。
        """
        self.config = config

    def close(self) -> None:
        """释放资源。

        默认实现为空，具体 provider 可按需关闭数据库连接、网络连接等。
        """
        return
    
    @abstractmethod
    def initialize(self) -> None:
        """初始化数据源
        
        在使用其他方法前必须先调用此方法进行初始化。
        对于 Tushare，这会初始化 API 客户端并做可用性校验。
        """
        pass
    
    @abstractmethod
    def get_instruments(self, market: str = "all") -> List[str]:
        """获取股票列表
        
        Args:
            market: 市场类型，"all" 表示全市场
            
        Returns:
            股票代码列表，格式如 ["000001.XSHE", "600000.XSHG"]
        """
        pass
    
    @abstractmethod
    def get_index_components(self, index_code: str, date: str = None) -> List[str]:
        """获取指数成分股
        
        Args:
            index_code: 指数代码，如 "000300.XSHG"（沪深300）
            date: 查询日期，None 表示最新成分
            
        Returns:
            成分股代码列表
        """
        pass
    
    @abstractmethod
    def get_price_data(
        self,
        instruments: List[str],
        fields: List[str],
        start_date: str,
        end_date: str,
        freq: str = "day"
    ) -> pd.DataFrame:
        """获取行情数据
        
        Args:
            instruments: 股票代码列表
            fields: 字段列表，如 ["$open", "$close", "$volume"]
            start_date: 开始日期，格式 "YYYY-MM-DD"
            end_date: 结束日期，格式 "YYYY-MM-DD"
            freq: 数据频率，"day" 或 "1m" 等
            
        Returns:
            DataFrame，索引为 (instrument, datetime) 的 MultiIndex
        """
        pass
    
    @abstractmethod
    def get_features(
        self,
        instruments: List[str],
        fields: List[str],
        start_date: str,
        end_date: str,
        freq: str = "day"
    ) -> pd.DataFrame:
        """获取特征数据（兼容 Qlib 接口）
        
        这是 Qlib D.features() 的通用接口，支持价格字段和衍生特征。
        
        Args:
            instruments: 股票代码列表
            fields: 特征字段列表，可以包含 $open 等价格字段
            start_date: 开始日期
            end_date: 结束日期
            freq: 数据频率
            
        Returns:
            DataFrame，格式与 Qlib D.features() 一致
        """
        pass

    def get_market_daily_indicators(
        self,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """获取市场级日度指标。

        典型用途是北向资金、融资融券等"按交易日只有一条或少量聚合记录"的
        市场状态序列。默认实现为不支持，具体数据源可按需覆盖。
        """
        raise NotImplementedError("当前数据提供者未实现市场级日度指标接口")

    def get_macro_indicators(
        self,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """获取宏观经济指标（Shibor, M2, PMI, CPI, PPI）。

        返回以 month (YYYYMM) 为索引的 DataFrame，包含：
        - shibor_1y: 1年期 Shibor 利率（日频月均值）
        - m2_yoy: M2 同比增速
        - pmi_manufacturing: 制造业 PMI
        - cpi_yoy: CPI 同比
        - ppi_yoy: PPI 同比

        默认实现为不支持，具体数据源可按需覆盖。
        """
        raise NotImplementedError("当前数据提供者未实现宏观经济指标接口")
    
    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self._initialized
