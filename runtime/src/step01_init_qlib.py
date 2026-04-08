from __future__ import annotations

from pathlib import Path

from qlib.data import D

from common import OUTPUT_DIR, env_config, ensure_runtime_dirs, init_qlib, list_instruments, write_table


def run() -> None:
    ensure_runtime_dirs()
    config = env_config()
    init_qlib(config)
    instruments = list_instruments(config)
    sample_instrument = str(config.get("sample_instrument") or instruments[0])
    preview = D.features(
        instruments=[sample_instrument],
        fields=["$open", "$close", "$high", "$low", "$volume"],
        start_time=str(config.get("train_start_date")),
        end_time=str(config.get("test_end_date", config.get("train_end_date"))),
        freq=str(config.get("freq", "day")),
    )
    preview.columns = [column.replace("$", "") for column in preview.columns]
    preview.index = preview.index.set_names(["instrument", "datetime"])
    write_table(OUTPUT_DIR / "health" / "sample_preview.csv", preview.head(50))
    print(f"qlib init ok, instruments={len(instruments)}, sample={sample_instrument}")


if __name__ == "__main__":
    run()
