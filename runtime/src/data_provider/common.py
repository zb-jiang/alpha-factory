"""
数据提供者公共函数
"""

from typing import Dict, Any
from .provider_factory import ProviderFactory
from .base_provider import BaseDataProvider


def get_data_provider(config: Dict[str, Any]) -> BaseDataProvider:
    """获取数据提供者实例
    
    根据配置创建对应的数据提供者。
    
    Args:
        config: 配置字典，需要包含 data_source 字段
        
    Returns:
        对应的数据提供者实例
        
    示例:
        >>> config = {"data_source": "tushare", "tushare": {"api_key": "xxx"}}
        >>> provider = get_data_provider(config)
        >>> provider.initialize()
    """
    return ProviderFactory.create_provider(config)
