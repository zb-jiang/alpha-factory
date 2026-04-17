"""
Ricequant Notebook runner template.

Usage:
1) Copy this file content to a new notebook cell and run.
2) Ensure `rq_factor_validator.py` is in the same notebook workspace.
3) Update CONFIG and FACTOR settings.
"""

import pandas as pd
import rqdatac as rq

from rq_factor_validator import run_validation


# -----------------------------
# 0) Optional safety patch
# -----------------------------
# Some Ricequant environments do not support native `vwap` in get_price fields.
# This patch replaces any requested vwap-like field with `total_turnover`.
ENABLE_GET_PRICE_VWAP_FALLBACK = False

if ENABLE_GET_PRICE_VWAP_FALLBACK:
    # Make patch idempotent across repeated notebook runs.
    # Always keep a stable reference to the real underlying get_price.
    if not hasattr(rq, "_ff_original_get_price"):
        rq._ff_original_get_price = rq.get_price
    _ORIG_GET_PRICE = rq._ff_original_get_price

    def _safe_get_price(*args, **kwargs):
        fields = kwargs.get("fields")
        if fields is None:
            return _ORIG_GET_PRICE(*args, **kwargs)

        if isinstance(fields, str):
            fields = [fields]

        replaced = False
        mapped_fields = []
        for item in fields:
            token = str(item).strip().lower()
            if "vwap" in token:
                mapped_fields.append("total_turnover")
                replaced = True
            else:
                mapped_fields.append(item)

        kwargs["fields"] = mapped_fields
        if replaced:
            print("[fallback] replaced vwap-like fields with total_turnover")
        return _ORIG_GET_PRICE(*args, **kwargs)

    _safe_get_price._ff_vwap_fallback = True
    rq.get_price = _safe_get_price


# -----------------------------
# 1) Analysis config (aligned with local analysis_rule.yaml style)
# -----------------------------
CONFIG = {
    "start_date": "2022-10-01",
    "end_date": "2025-12-31",
    "stock_pool": {
        "type": "index_components",  # index_components | all_market | custom
        "index_code": "SH000300",
        "dynamic_membership": True,
        "include_st": False,
        "include_new_stock": False,
        "new_stock_days": 60,
        "custom_instruments": [],
    },
    "price_adjust": "pre",  # none | pre | post
    "vwap_mode": "rq",  # rq | tushare_scaled
    "rebalance": "weekly",  # daily | weekly | monthly
    "rebalance_interval": 1,
    "rebalance_anchor": "first_trading_day_of_week",
    "label": {
        "name": "rebalance_period_return",
        "return_type": "period_return",
        "price_field": "close",
    },
    "preprocess": {
        "outlier_method": "none",  # none | mad | quantile | sigma
        "outlier_options": {
            "n": 3.0,
            "lower_quantile": 0.01,
            "upper_quantile": 0.99,
        },
        "neutralization": "none",  # none | industry | market_cap | industry_market_cap
        "neutralization_options": {
            "industry_field": "industry",
            "market_cap_field": "market_cap",
        },
    },
}


# -----------------------------
# 2) Factor settings (batch mode)
# -----------------------------
# Add/remove factors here. Each item is (factor_name, factor_formula).
FACTORS = [
    ("breakout_drawdown_ratio_v1", "breakout_20d / (abs(max_drawdown_20d) + 0.001)"),
    ("volatility_inversion_v1", "close_to_high / (high_low_range + 0.001)"),
    ("volume_volatility_penalty_v1", "ret_20d / (1 + volume_vol_20d)"),
]


# -----------------------------
# 3) Run (batch)
# -----------------------------
results: list[tuple[str, object]] = []
failed: list[tuple[str, str]] = []

for factor_name, factor_formula in FACTORS:
    print(f"\n[RUN] {factor_name}")
    try:
        result = run_validation(
            factor_name=factor_name,
            formula=factor_formula,
            config_override=CONFIG,
        )
        results.append((factor_name, result))
        print(f"[OK ] {factor_name}")
    except Exception as exc:
        failed.append((factor_name, str(exc)))
        print(f"[ERR] {factor_name}: {exc}")


# -----------------------------
# 4) Output (batch summary)
# -----------------------------
print("\n=== Batch Metrics ===")
if results:
    pd.set_option("display.expand_frame_repr", False)
    pd.set_option("display.width", 2000)
    metrics_df = pd.DataFrame([r.metrics for _, r in results])
    show_cols = [
        "factor_name",
        "mean_rank_ic",
        "rank_ic_ir",
        "positive_ic_ratio",
        "coverage",
        "observation_count",
    ]
    show_cols = [c for c in show_cols if c in metrics_df.columns]
    metrics_df = metrics_df[show_cols].sort_values("factor_name").reset_index(drop=True)
    print(metrics_df)
else:
    print("No successful results.")

print("\n=== Failed Factors ===")
if failed:
    failed_df = pd.DataFrame(failed, columns=["factor_name", "error"])
    print(failed_df)
else:
    print("None")
