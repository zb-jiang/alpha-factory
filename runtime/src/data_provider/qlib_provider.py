"""
Qlib 数据提供者实现
封装 Qlib 的数据访问接口，适配 BaseDataProvider
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd

from .base_provider import BaseDataProvider


class QlibProvider(BaseDataProvider):
    """Qlib 数据提供者
    
    封装 Qlib 的数据访问功能，提供统一的接口。
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._D = None  # Qlib D 对象
    
    def initialize(self) -> None:
        """初始化 Qlib"""
        if self._initialized:
            return
        
        try:
            import qlib
            from qlib.constant import REG_CN, REG_US
            
            provider_uri = str(self.config.get("provider_uri", "~/.qlib/qlib_data/cn_data"))
            Path(provider_uri).mkdir(parents=True, exist_ok=True)
            
            region = self.config.get("region", "cn")
            reg = REG_CN if str(region).lower() == "cn" else REG_US
            
            qlib.init(
                provider_uri=provider_uri,
                region=reg,
                kernels=1,
            )
            
            from qlib.data import D
            self._D = D
            self._initialized = True
            
            print(f"Qlib 初始化成功: {provider_uri}")
            
        except ImportError:
            raise ImportError("qlib 未安装，请执行: pip install pyqlib")
        except Exception as e:
            raise RuntimeError(f"Qlib 初始化失败: {e}")
    
    def get_instruments(self, market: str = "all") -> List[str]:
        """获取股票列表"""
        self.initialize()
        
        instruments = self._D.instruments(market=market)
        
        # 根据 run_mode 确定时间范围
        run_mode = self.config.get("run_mode", "train")
        if run_mode == "test":
            start_time = str(self.config.get("test_start_date"))
            end_time = str(self.config.get("test_end_date"))
        else:
            start_time = str(self.config.get("train_start_date"))
            end_time = str(self.config.get("train_end_date"))
        
        codes = self._D.list_instruments(
            instruments=instruments,
            start_time=start_time,
            end_time=end_time,
            as_list=True,
        )
        
        return codes
    
    def get_index_components(self, index_code: str, date: str = None) -> List[str]:
        """获取指数成分股
        
        Qlib 通过 market 参数支持指数成分股查询
        """
        self.initialize()
        
        # 转换指数代码为 Qlib market 格式
        market_map = {
            "000300.XSHG": "csi300",
            "SH000300": "csi300",
            "000905.XSHG": "csi500",
            "SH000905": "csi500",
            "000852.XSHG": "csi1000",
            "SH000852": "csi1000",
            "000016.XSHG": "sse50",
            "SH000016": "sse50",
        }
        
        market = market_map.get(index_code, "all")
        
        # 获取成分股
        instruments = self._D.instruments(market=market)
        
        run_mode = self.config.get("run_mode", "train")
        if run_mode == "test":
            start_time = str(self.config.get("test_start_date"))
            end_time = str(self.config.get("test_end_date"))
        else:
            start_time = str(self.config.get("train_start_date"))
            end_time = str(self.config.get("train_end_date"))
        
        codes = self._D.list_instruments(
            instruments=instruments,
            start_time=start_time,
            end_time=end_time,
            as_list=True,
        )
        
        return codes
    
    def get_price_data(
        self,
        instruments: List[str],
        fields: List[str],
        start_date: str,
        end_date: str,
        freq: str = "day"
    ) -> pd.DataFrame:
        """获取行情数据"""
        self.initialize()
        
        # 确保字段以 $ 开头
        processed_fields = [
            field if field.startswith("$") else f"${field}"
            for field in fields
        ]
        
        data = self._D.features(
            instruments=instruments,
            fields=processed_fields,
            start_time=start_date,
            end_time=end_date,
            freq=freq,
        )
        
        return data
    
    def get_features(
        self,
        instruments: List[str],
        fields: List[str],
        start_date: str,
        end_date: str,
        freq: str = "day"
    ) -> pd.DataFrame:
        """获取特征数据（直接调用 Qlib D.features）"""
        self.initialize()
        
        # 确保字段以 $ 开头
        processed_fields = [
            field if field.startswith("$") else f"${field}"
            for field in fields
        ]
        
        return self._D.features(
            instruments=instruments,
            fields=processed_fields,
            start_time=start_date,
            end_time=end_date,
            freq=freq,
        )
