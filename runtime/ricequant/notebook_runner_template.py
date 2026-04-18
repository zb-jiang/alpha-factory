"""
Ricequant Notebook runner template.

Usage:
1) Copy this file content to a new notebook cell and run.
2) Ensure `rq_factor_validator.py` is in the same notebook workspace.
3) Update CONFIG and FACTOR settings.
"""

import pandas as pd
import rqdatac as rq

from rq_factor_validator import (
    BASE_DATA_FIELDS,
    _expand_formula_dependencies,
    _extract_formula_tokens,
    _load_feature_formula_map,
    run_validation,
)


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
# 2.5) Raw-field availability guard
# -----------------------------
# Probe local raw fields against current RQ account capability and
# block factors that depend on unsupported raw fields.
ENABLE_RAW_FIELD_GUARD = True
_FEATURE_FORMULA_MAP = _load_feature_formula_map()
_RAW_FIELDS = sorted(set(BASE_DATA_FIELDS))


def _pick_probe_instrument(config: dict) -> str:
    stock_pool = config.get("stock_pool", {})
    custom = stock_pool.get("custom_instruments") or []
    if custom:
        return str(custom[0])
    index_code = stock_pool.get("index_code", "SH000300")
    probe_date = pd.Timestamp(config.get("end_date", "2025-12-31")).strftime("%Y-%m-%d")
    try:
        members = rq.index_components(index_code, date=probe_date)
        if members:
            return str(members[0])
    except Exception:
        pass
    return "000001.XSHE"


def _can_get_price_field(order_book_id: str, date_str: str, field: str) -> tuple[bool, str]:
    try:
        payload = rq.get_price(
            [order_book_id],
            start_date=date_str,
            end_date=date_str,
            frequency="1d",
            fields=[field],
            expect_df=True,
        )
    except Exception as exc:
        return False, f"get_price({field}) failed: {exc}"
    if payload is None:
        return False, f"get_price({field}) returned None"
    return True, "ok"


def _can_get_factor(order_book_id: str, start_date: str, end_date: str, factor_name: str) -> tuple[bool, str]:
    try:
        payload = rq.get_factor([order_book_id], factor_name, start_date, end_date)
    except Exception as exc:
        return False, f"get_factor({factor_name}) failed: {exc}"
    if payload is None:
        return False, f"get_factor({factor_name}) returned None"
    return True, "ok"


def _probe_raw_field_support(config: dict) -> dict[str, dict[str, str]]:
    probe_id = _pick_probe_instrument(config)
    probe_end = pd.Timestamp(config.get("end_date", "2025-12-31"))
    probe_start = (probe_end - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    probe_date = probe_end.strftime("%Y-%m-%d")

    support: dict[str, dict[str, str]] = {}

    for f in ["open", "high", "low", "close", "volume"]:
        ok, reason = _can_get_price_field(probe_id, probe_date, f)
        support[f] = {"status": "supported" if ok else "unsupported", "reason": reason}

    ok_amount, reason_amount = _can_get_price_field(probe_id, probe_date, "total_turnover")
    support["amount"] = {"status": "supported" if ok_amount else "unsupported", "reason": reason_amount}

    # Local VWAP is derived from amount/volume.
    if support["amount"]["status"] == "supported" and support["volume"]["status"] == "supported":
        support["vwap"] = {"status": "supported", "reason": "derived from total_turnover and volume"}
    else:
        support["vwap"] = {"status": "unsupported", "reason": "requires total_turnover and volume"}

    turnover_candidates = ["turnover_rate", "free_turnover_rate", "turnover"]
    turnover_reasons = []
    turnover_ok = False
    for name in turnover_candidates:
        ok, reason = _can_get_factor(probe_id, probe_start, probe_date, name)
        if ok:
            turnover_ok = True
            turnover_reasons.append(f"{name}: ok")
            break
        turnover_reasons.append(f"{name}: {reason}")
    support["turnover"] = {
        "status": "supported" if turnover_ok else "unsupported",
        "reason": "; ".join(turnover_reasons),
    }

    market_cap_candidates = ["market_cap", "a_share_market_val", "total_market_cap"]
    market_cap_reasons = []
    market_cap_ok = False
    for name in market_cap_candidates:
        ok, reason = _can_get_factor(probe_id, probe_start, probe_date, name)
        if ok:
            market_cap_ok = True
            market_cap_reasons.append(f"{name}: ok")
            break
        market_cap_reasons.append(f"{name}: {reason}")
    support["market_cap"] = {
        "status": "supported" if market_cap_ok else "unsupported",
        "reason": "; ".join(market_cap_reasons),
    }

    # Industry via shenwan API.
    if hasattr(rq, "shenwan_instrument_industry"):
        try:
            payload = rq.shenwan_instrument_industry([probe_id], date=probe_date)
            if payload is None:
                support["industry"] = {"status": "unsupported", "reason": "shenwan_instrument_industry returned None"}
            else:
                support["industry"] = {"status": "supported", "reason": "ok"}
        except Exception as exc:
            support["industry"] = {"status": "unsupported", "reason": f"shenwan_instrument_industry failed: {exc}"}
    else:
        support["industry"] = {"status": "unsupported", "reason": "rq has no shenwan_instrument_industry"}

    return support


def _collect_raw_dependencies(formula: str) -> tuple[str, list[str]]:
    try:
        expanded = _expand_formula_dependencies(formula, _FEATURE_FORMULA_MAP)
    except Exception:
        expanded = formula
    tokens = sorted(_extract_formula_tokens(expanded))
    raw_tokens = sorted([t for t in tokens if t in _RAW_FIELDS])
    return expanded, raw_tokens


# -----------------------------
# 3) Run (batch)
# -----------------------------
results: list[tuple[str, object]] = []
failed: list[tuple[str, str]] = []
raw_field_support = _probe_raw_field_support(CONFIG) if ENABLE_RAW_FIELD_GUARD else {}

if ENABLE_RAW_FIELD_GUARD:
    print("\n=== Raw Field Availability (RQ) ===")
    support_rows = []
    for f in _RAW_FIELDS:
        meta = raw_field_support.get(f, {"status": "unsupported", "reason": "not probed"})
        support_rows.append({"field": f, "status": meta["status"], "reason": meta["reason"]})
    print(pd.DataFrame(support_rows))

for factor_name, factor_formula in FACTORS:
    print(f"\n[RUN] {factor_name}")
    if ENABLE_RAW_FIELD_GUARD:
        _, raw_tokens = _collect_raw_dependencies(str(factor_formula))
        unsupported = [f for f in raw_tokens if raw_field_support.get(f, {}).get("status") != "supported"]
        if unsupported:
            detail = " | ".join([f"{f}: {raw_field_support[f]['reason']}" for f in unsupported])
            msg = (
                "Factor is blocked because it depends on unsupported raw fields in current RQ environment. "
                f"Detected raw tokens: {', '.join(raw_tokens) if raw_tokens else 'None'}. "
                f"Unsupported details: {detail}"
            )
            failed.append((factor_name, msg))
            print(f"[ERR] {factor_name}: {msg}")
            continue
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
