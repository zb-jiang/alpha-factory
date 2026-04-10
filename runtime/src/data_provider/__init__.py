"""
数据提供者模块
支持 Qlib、米筐 RQData 和 Tushare 等多种数据源
"""

from .base_provider import BaseDataProvider
from .provider_factory import ProviderFactory
from .common import get_data_provider

__all__ = ["BaseDataProvider", "ProviderFactory", "get_data_provider"]
