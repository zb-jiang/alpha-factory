from __future__ import annotations

import pandas as pd

from common import (
    OUTPUT_DIR,
    env_config,
    feature_pool_config,
    load_raw_data,
    market_context_config,
    write_json,
)
from market_regime import build_train_context, compute_daily_market


def _cfg_int(payload: dict[str, object], key: str, default: int) -> int:
    value = payload.get(key, default)
    return int(default if value is None else value)


def run() -> None:
    config = env_config()
    mc_cfg = market_context_config()
    windows = dict(mc_cfg.get("windows", {}))
    thresholds = dict(mc_cfg.get("thresholds", {}))
    fields = dict(mc_cfg.get("fields", {}))

    train_start = pd.Timestamp(str(config.get("train_start_date"))).normalize()
    train_end = pd.Timestamp(str(config.get("train_end_date"))).normalize()
    feature_cfg = feature_pool_config()
    raw_fields = list(feature_cfg.get("raw_fields", []))
    for required in [fields.get("close", "close"), fields.get("turnover", "turnover"), fields.get("amount", "amount"), fields.get("market_cap", "market_cap")]:
        if required not in raw_fields:
            raw_fields.append(str(required))

    context_cfg = dict(config)
    context_cfg["run_mode"] = "train"
    context_cfg["train_start_date"] = str(train_start.date())
    context_cfg["train_end_date"] = str(train_end.date())

    rank_days = _cfg_int(windows, "rank_lookback_days", 250)
    warmup_days = _cfg_int(windows, "warmup_trading_days", rank_days + 10)
    raw_frame = load_raw_data(context_cfg, raw_fields=raw_fields, warmup_trading_days=warmup_days)
    daily_market = compute_daily_market(raw_frame, fields=fields, windows=windows)
    train_window = daily_market.loc[(daily_market.index >= train_start) & (daily_market.index <= train_end)]

    market_context = {
        "meta": {
            "train_start_date": str(train_start.date()),
            "train_end_date": str(train_end.date()),
            "config_source": "runtime/config/market_context.yaml",
        },
        "train_context": build_train_context(train_window, thresholds),
    }
    write_json(OUTPUT_DIR / "health" / "market_context.json", market_context)
    print("market context ready")


if __name__ == "__main__":
    run()
