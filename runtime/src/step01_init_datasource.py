from __future__ import annotations

from pathlib import Path

from common import (
    OUTPUT_DIR,
    env_config,
    ensure_runtime_dirs,
    get_data_provider,
    write_table,
)


def run() -> None:
    ensure_runtime_dirs()
    config = env_config()
    data_source = str(config.get("data_source", "tushare")).strip().lower()
    if data_source != "tushare":
        raise ValueError(f"当前仅支持 data_source=tushare，收到: {data_source}")
    _run_with_tushare(config)


def _run_with_tushare(config: dict) -> None:
    """使用 Tushare 数据提供者初始化并输出样本预览"""
    print("使用数据源: tushare")
    
    # 获取数据提供者
    provider = get_data_provider(config)
    provider.initialize()
    
    # 获取股票列表
    instruments = provider.get_instruments()
    print(f"获取到 {len(instruments)} 只股票")
    
    # 获取样本股票
    sample_instrument = str(config.get("sample_instrument"))
    if not sample_instrument or sample_instrument not in instruments:
        sample_instrument = instruments[0] if instruments else "SH600000"
    
    # 获取预览数据
    start_date = str(config.get("train_start_date"))
    end_date = str(config.get("test_end_date", config.get("train_end_date")))
    
    try:
        preview = provider.get_price_data(
            instruments=[sample_instrument],
            fields=["$open", "$close", "$high", "$low", "$volume"],
            start_date=start_date,
            end_date=end_date,
        )
        
        if preview.empty:
            print(f"警告: 无法获取 {sample_instrument} 的数据")
            # 创建一个空的 DataFrame 作为占位
            import pandas as pd
            preview = pd.DataFrame(
                columns=["open", "close", "high", "low", "volume"],
                index=pd.MultiIndex.from_tuples([], names=["instrument", "datetime"])
            )
        
        # 统一格式：去掉 $ 前缀，与下游步骤字段约定保持一致
        preview.columns = [column.replace("$", "") for column in preview.columns]
        
        # 保存预览
        write_table(OUTPUT_DIR / "health" / "sample_preview.csv", preview.head(50))
        print(f"tushare init ok, instruments={len(instruments)}, sample={sample_instrument}")
        
    except Exception as e:
        print(f"获取预览数据失败: {e}")
        # 即使失败也创建一个空的预览文件
        import pandas as pd
        preview = pd.DataFrame(
            columns=["open", "close", "high", "low", "volume"],
            index=pd.MultiIndex.from_tuples([], names=["instrument", "datetime"])
        )
        write_table(OUTPUT_DIR / "health" / "sample_preview.csv", preview)
        print(f"tushare init ok (with warnings), instruments={len(instruments)}")


if __name__ == "__main__":
    run()
