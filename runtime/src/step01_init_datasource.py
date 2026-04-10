from __future__ import annotations

from pathlib import Path

from common import (
    OUTPUT_DIR,
    env_config,
    ensure_runtime_dirs,
    get_data_provider,
    init_qlib,
    list_instruments,
    write_table,
)


def run() -> None:
    ensure_runtime_dirs()
    config = env_config()
    
    # 获取数据源类型
    data_source = config.get("data_source", "qlib")
    
    if data_source == "qlib":
        # 使用传统 Qlib 方式
        _run_with_qlib(config)
    else:
        # 使用数据提供者（Tushare、米筐等）
        _run_with_provider(config, data_source)


def _run_with_qlib(config: dict) -> None:
    """使用 Qlib 本地数据运行"""
    from qlib.data import D
    
    init_qlib(config)
    instruments = list_instruments(config)
    sample_instrument = str(config.get("sample_instrument") or instruments[0])
    preview = D.features(
        instruments=[sample_instrument],
        fields=["$open", "$close", "$high", "$low", "$volume", "$factor"],
        start_time=str(config.get("train_start_date")),
        end_time=str(config.get("test_end_date", config.get("train_end_date"))),
        freq=str(config.get("freq", "day")),
    )
    preview.columns = [column.replace("$", "") for column in preview.columns]
    preview.index = preview.index.set_names(["instrument", "datetime"])
    write_table(OUTPUT_DIR / "health" / "sample_preview.csv", preview.head(50))
    print(f"qlib init ok, instruments={len(instruments)}, sample={sample_instrument}")


def _run_with_provider(config: dict, data_source: str) -> None:
    """使用数据提供者运行（Tushare、米筐等）"""
    print(f"使用数据源: {data_source}")
    
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
            fields=["$open", "$close", "$high", "$low", "$volume", "$factor"],
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
        
        # 统一格式：去掉 $ 前缀，与 Qlib 路径保持一致
        preview.columns = [column.replace("$", "") for column in preview.columns]
        
        # 保存预览
        write_table(OUTPUT_DIR / "health" / "sample_preview.csv", preview.head(50))
        print(f"{data_source} init ok, instruments={len(instruments)}, sample={sample_instrument}")
        
    except Exception as e:
        print(f"获取预览数据失败: {e}")
        # 即使失败也创建一个空的预览文件
        import pandas as pd
        preview = pd.DataFrame(
            columns=["open", "close", "high", "low", "volume"],
            index=pd.MultiIndex.from_tuples([], names=["instrument", "datetime"])
        )
        write_table(OUTPUT_DIR / "health" / "sample_preview.csv", preview)
        print(f"{data_source} init ok (with warnings), instruments={len(instruments)}")


if __name__ == "__main__":
    run()
