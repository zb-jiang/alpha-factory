"""
数据提供者工厂类
根据配置创建对应的数据提供者实例
"""

from pathlib import Path
from typing import Dict, Any, Type, List

from .base_provider import BaseDataProvider
from .tushare_sqlite_provider import TushareSQLiteProvider


class ProviderFactory:
    """数据提供者工厂类
    
    根据配置中的 data_source 字段创建对应的数据提供者。
    当前仅支持 tushare。
    
    示例:
        >>> from data_provider import ProviderFactory
        >>> config = {"data_source": "tushare", "tushare": {...}}
        >>> provider = ProviderFactory.create_provider(config)
        >>> provider.initialize()
    """
    
    # 注册的数据提供者
    _providers: Dict[str, Type[BaseDataProvider]] = {
        "tushare": TushareSQLiteProvider,
    }
    _active_provider: BaseDataProvider | None = None
    _active_signature: tuple[str, str] | None = None

    @classmethod
    def _provider_signature(cls, config: Dict[str, Any]) -> tuple[str, str]:
        source = str(config.get("data_source", "tushare")).strip().lower()
        ts_cfg = config.get("tushare", {}) or {}
        cache_dir = str(Path(ts_cfg.get("data_cache_dir", "./data/tushare_cache")).resolve())
        return source, cache_dir
    
    @classmethod
    def create_provider(cls, config: Dict[str, Any]) -> BaseDataProvider:
        """创建数据提供者
        
        Args:
            config: 配置字典，需要包含 data_source 字段
            
        Returns:
            对应的数据提供者实例
            
        Raises:
            ValueError: 如果指定的数据源不支持
        """
        source = str(config.get("data_source", "tushare")).strip().lower()
        
        if source not in cls._providers:
            available = ", ".join(cls._providers.keys())
            raise ValueError(f"当前仅支持 tushare，收到: '{source}'。可选的数据源: {available}")

        signature = cls._provider_signature(config)
        if cls._active_provider is not None and cls._active_signature == signature:
            cls._active_provider.update_config(config)
            return cls._active_provider

        if cls._active_provider is not None:
            try:
                cls._active_provider.close()
            finally:
                cls._active_provider = None
                cls._active_signature = None

        provider_class = cls._providers[source]
        provider = provider_class(config)
        cls._active_provider = provider
        cls._active_signature = signature
        return provider

    @classmethod
    def reset_active_provider(cls) -> None:
        if cls._active_provider is not None:
            try:
                cls._active_provider.close()
            finally:
                cls._active_provider = None
                cls._active_signature = None
    
    @classmethod
    def register_provider(cls, name: str, provider_class: Type[BaseDataProvider]) -> None:
        """注册新的数据提供者
        
        用于扩展支持新的数据源。
        
        Args:
            name: 数据源名称
            provider_class: 数据提供者类，必须继承 BaseDataProvider
            
        示例:
            >>> class MyProvider(BaseDataProvider):
            ...     def initialize(self): ...
            >>> ProviderFactory.register_provider("mydata", MyProvider)
        """
        if not issubclass(provider_class, BaseDataProvider):
            raise TypeError("提供者类必须继承 BaseDataProvider")
        
        cls._providers[name.lower()] = provider_class
    
    @classmethod
    def get_available_providers(cls) -> List[str]:
        """获取所有可用的数据源名称"""
        return list(cls._providers.keys())
    
    @classmethod
    def is_provider_available(cls, name: str) -> bool:
        """检查指定的数据源是否可用"""
        return name.lower() in cls._providers
