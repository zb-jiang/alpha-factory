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
    
    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self._initialized
