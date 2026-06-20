from __future__ import annotations

import ast
from collections import Counter
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import yaml


_BANNER_WIDTH = 60

FUNDAMENTAL_FEATURE_NAMES = [
    # ── 估值 ──────────────────────────────────────────
    "pe_ttm",
    "pb",
    "ps_ttm",
    "dv_ttm",
    "earnings_yield",
    "sales_yield",
    "market_cap_change_20d",
    "market_cap_stability_20d",
    # ── 盈利质量 ──────────────────────────────────────
    "eps",
    "roe",
    "netprofit_yoy",
    "or_yoy",
    "quality_value",
    "profit_revenue_gap",
    "profit_quality",
    "q_roe_acceleration",
    "gross_margin",
    "net_margin",
    "real_growth",
    "op_growth",
    "equity_growth",
    # ── 投资因子 ──────────────────────────────────────
    "asset_growth_inverse",
    # ── 财务健康 ──────────────────────────────────────
    "financial_health",
    "liquidity_strength",
    "debt_service_ability",
    # ── 现金流 ──────────────────────────────────────
    "fcf_yield",
]


def _current_banner_scope() -> str:
    if not RUNTIME_CONTEXT_PATH.exists():
        return "全局"
    try:
        context = read_json(RUNTIME_CONTEXT_PATH)
    except Exception:
        return "全局"
    workflow_state = dict(context.get("workflow_state", {}))
    window_id = str(workflow_state.get("window_id", "")).strip()
    stage = str(workflow_state.get("stage", "")).strip()
    iteration = workflow_state.get("iteration")
    if not window_id:
        return "全局"
    parts = [window_id]
    if stage:
        parts.append(stage)
    if iteration is not None and str(iteration).strip() != "":
        parts.append(f"iteration_{iteration}")
    return " - ".join(parts)


def log_step_start(step_id: str, description: str = "") -> None:
    label = f"Step {step_id}"
    if description:
        label = f"Step {step_id}: {description}"
    label = f"{label} ({_current_banner_scope()})"
    print("", flush=True)
    print("=" * _BANNER_WIDTH, flush=True)
    print(f">> {label}", flush=True)
    print("=" * _BANNER_WIDTH, flush=True)


def log_step_end(step_id: str, summary: str = "", details: list[str] | None = None) -> None:
    label = f"Step {step_id}"
    if summary:
        label = f"Step {step_id} {summary}"
    label = f"{label} ({_current_banner_scope()})"
    print("-" * _BANNER_WIDTH, flush=True)
    print(f"<< {label}", flush=True)
    if details:
        for line in details:
            print(f"   {line}", flush=True)
    print("-" * _BANNER_WIDTH, flush=True)


def log_phase(phase_label: str) -> None:
    print(f"  -->> {phase_label}", flush=True)


def log_progress_bar(prefix: str, current: int, total: int, done: bool = False) -> None:
    if total <= 0:
        return
    pct = current / total
    bar_len = 20
    filled = int(bar_len * pct)
    bar = "█" * filled + "░" * (bar_len - filled)
    status = "OK" if done else f"{int(pct * 100)}%"
    line = f"  {prefix} {bar} {current}/{total} ({status})"
    if done:
        print(f"\r{line}", flush=True)
    else:
        print(f"\r{line}", end="", flush=True)


def log_data_loading(start_time: str, end_time: str, instruments: int, rows: int) -> None:
    print(f"   加载数据: {start_time} ~ {end_time}, {instruments} 只股票, {rows:,} 行", flush=True)


SRC_DIR = Path(__file__).resolve().parents[1]


def _resolve_staging_dir() -> Path:
    for i, arg in enumerate(sys.argv):
        if arg == "--staging" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1]).resolve()
        if arg.startswith("--staging="):
            return Path(arg.split("=", 1)[1]).resolve()
    env_staging = os.environ.get("STAGING_DIR", "").strip()
    if env_staging:
        return Path(env_staging).resolve()
    raise RuntimeError(
        "必须指定 staging 目录。请通过 --staging <path> 参数或 STAGING_DIR 环境变量指定。\n"
        "示例: python step10_iterate.py --staging D:/staging/tenant_A\n"
        "示例: set STAGING_DIR=D:/staging/tenant_A && python step10_iterate.py"
    )


STAGING_DIR = _resolve_staging_dir()
CONFIG_DIR = STAGING_DIR / "config"
DATA_DIR = STAGING_DIR / "data"
OUTPUT_DIR = STAGING_DIR / "outputs"
JOINQUANT_DIR = STAGING_DIR / "joinquant"
MARKET_CONTEXT_CONFIG_PATH = CONFIG_DIR / "market_context.yaml"
SELECTOR_CONFIG_PATH = CONFIG_DIR / "selector.yaml"
SCORE_CONFIG_PATH = CONFIG_DIR / "score.yaml"
RUNTIME_CONTEXT_PATH = OUTPUT_DIR / "_runtime" / "active_context.json"
OUTPUT_ARTIFACTS = [
    Path("health") / "feature_stats.csv",
    Path("health") / "feature_corr.csv",
    Path("health") / "llm_summary.json",
    Path("health") / "market_context.json",
    Path("llm") / "raw_response.json",
    Path("llm") / "factors_validated.json",
    Path("llm") / "factors_rejected.json",
    Path("backtest") / "factor_values.parquet",
    Path("backtest") / "factor_metrics.csv",
    Path("backtest") / "strategy_metrics.csv",
    Path("backtest") / "nav_curve.parquet",
    Path("backtest") / "orders.parquet",
    Path("backtest") / "positions.parquet",
    Path("backtest") / "final_score.csv",
    Path("backtest") / "top3_factors.json",
    Path("backtest") / "skipped_factors.json",
    Path("backtest") / "iteration_context.json",
]

DYNAMIC_INDEX_COMPONENT_CACHE: dict[
    tuple[str, tuple[str, ...], bool, bool, int],
    dict[pd.Timestamp, set[str]],
] = {}
NAMECHANGE_CACHE: dict[str, pd.DataFrame] = {}
WINDOW_RUNTIME_CACHE: dict[str, dict[str, Any]] = {}


def _window_cache_scope_id(config: dict[str, Any] | None = None) -> str | None:
    cfg = config or env_config()
    workflow_state = dict(cfg.get("workflow_state", {}))
    window_id = str(workflow_state.get("window_id", "")).strip()
    if not window_id:
        return None
    run_mode = str(cfg.get("run_mode", "train")).strip().lower()
    start_time, end_time = active_run_window(cfg)
    return f"{window_id}|{run_mode}|{_date_text(start_time)}|{_date_text(end_time)}"


def _window_cache_bucket(config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    scope_id = _window_cache_scope_id(config)
    if scope_id is None:
        return None
    return WINDOW_RUNTIME_CACHE.setdefault(
        scope_id,
        {
            "raw_frames": [],
            "feature_frames": {},
            "label_series": {},
            "ohlcv_maps": {},
            "daily_market": {},
        },
    )


def clear_window_runtime_cache(config: dict[str, Any] | None = None) -> None:
    scope_id = _window_cache_scope_id(config)
    if scope_id is None:
        WINDOW_RUNTIME_CACHE.clear()
        return
    WINDOW_RUNTIME_CACHE.pop(scope_id, None)


def _workflow_state_for_cache(config: dict[str, Any]) -> dict[str, Any]:
    workflow_state = dict(config.get("workflow_state", {}))
    if not workflow_state:
        return {}
    normalized: dict[str, Any] = {}
    for key in ("window_id", "stage"):
        value = workflow_state.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized[key] = text
    return normalized


def _config_cache_signature(config: dict[str, Any]) -> str:
    relevant = {
        "run_mode": config.get("run_mode"),
        "train_start_date": config.get("train_start_date"),
        "train_end_date": config.get("train_end_date"),
        "test_start_date": config.get("test_start_date"),
        "test_end_date": config.get("test_end_date"),
        "freq": config.get("freq"),
        "max_instruments": config.get("max_instruments"),
        "price_adjust_reference_date": config.get("price_adjust_reference_date"),
        "stock_pool": config.get("stock_pool"),
        "label": config.get("label"),
        "rebalance": config.get("rebalance"),
        "rebalance_interval": config.get("rebalance_interval"),
        "disable_dynamic_membership": config.get("disable_dynamic_membership"),
        "workflow_state": _workflow_state_for_cache(config),
    }
    return json.dumps(relevant, ensure_ascii=False, sort_keys=True, default=str)


def _frame_cache_signature(frame: pd.DataFrame) -> str:
    if frame.empty:
        return json.dumps(
            {"columns": list(frame.columns), "rows": 0, "index_names": list(frame.index.names)},
            ensure_ascii=False,
            sort_keys=True,
        )
    datetime_index = pd.Index(pd.to_datetime(frame.index.get_level_values("datetime")))
    payload = {
        "columns": list(frame.columns),
        "rows": int(len(frame)),
        "index_names": list(frame.index.names),
        "start": _date_text(pd.Timestamp(datetime_index.min()).normalize()),
        "end": _date_text(pd.Timestamp(datetime_index.max()).normalize()),
        "instrument_count": int(frame.index.get_level_values("instrument").nunique()),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _raw_cache_scope_signature(config: dict[str, Any]) -> str:
    relevant = {
        "run_mode": config.get("run_mode"),
        "freq": config.get("freq"),
        "max_instruments": config.get("max_instruments"),
        "stock_pool": config.get("stock_pool"),
        "disable_dynamic_membership": config.get("disable_dynamic_membership"),
        "workflow_state": _workflow_state_for_cache(config),
    }
    return json.dumps(relevant, ensure_ascii=False, sort_keys=True, default=str)


def _slice_cached_raw_frame(
    frame: pd.DataFrame,
    fields: list[str],
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
) -> pd.DataFrame:
    datetime_index = pd.Index(pd.to_datetime(frame.index.get_level_values("datetime")))
    mask = (datetime_index >= start_time) & (datetime_index <= end_time)
    return frame.loc[mask, fields].sort_index()


def _get_cached_raw_frame(
    config: dict[str, Any],
    fields: list[str],
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
) -> pd.DataFrame | None:
    bucket = _window_cache_bucket(config)
    if bucket is None:
        return None
    requested_fields = set(fields)
    scope_signature = _raw_cache_scope_signature(config)
    for entry in bucket["raw_frames"]:
        if entry["scope_signature"] != scope_signature:
            continue
        if not set(entry["fields"]).issuperset(requested_fields):
            continue
        if entry["start"] > start_time or entry["end"] < end_time:
            continue
        return _slice_cached_raw_frame(entry["frame"], fields, start_time, end_time)
    return None


def _store_cached_raw_frame(
    config: dict[str, Any],
    fields: list[str],
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
    frame: pd.DataFrame,
) -> None:
    bucket = _window_cache_bucket(config)
    if bucket is None:
        return
    bucket["raw_frames"].append(
        {
            "scope_signature": _raw_cache_scope_signature(config),
            "fields": tuple(fields),
            "start": start_time,
            "end": end_time,
            "frame": frame,
        }
    )

CONFIG_OWNERSHIP: dict[str, set[str]] = {
    "env": {
        "data_source",
        "tushare",
        "project_root",
        "region",
        "market",
        "freq",
        "sample_instrument",
        "max_instruments",
        "llm_candidate_count",
        "summary_top_k",
        "unstable_top_k",
        "high_corr_threshold",
        "max_missing_ratio",
        "fundamental_health_top_k",
        "min_rank_ic_to_backtest",
        "min_rank_ic_ir_to_backtest",
        "min_positive_ic_ratio",
        "enable_direction_filter",
        "llm_agents",
    },
    "analysis_rule": {
        "run_mode",
        "iteration_count",
        "train_start_date",
        "train_end_date",
        "test_start_date",
        "test_end_date",
        "training_workflow",
        "stock_pool",
        "price_adjust",
        "price_adjust_reference_date",
        "rebalance",
        "rebalance_interval",
        "rebalance_anchor",
        "label",
        "preprocess",
        "min_valid_ratio_per_observation",
        "index_component_search_max_open_days",
    },
    "backtest_rule": {
        "strategy_type",
        "TopKDropout",
        "EnhancedIndexing",
        "SoftTopK",
        "MarketTiming",
        "Execution",
    },
}

LABEL_DEFAULTS: dict[str, Any] = {
    "name": "rebalance_period_return",
    "return_type": "period_return",
    "price_field": "close",
}

PREPROCESS_DEFAULTS: dict[str, Any] = {
    "outlier_method": "none",
    "outlier_options": {
        "n": 3.0,
        "lower_quantile": 0.01,
        "upper_quantile": 0.99,
    },
    "neutralization": "none",
    "neutralization_options": {
        "industry_field": "industry",
        "market_cap_field": "market_cap",
    },
}


class TopKDropoutRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buy_top_n: int = Field(20, ge=1)
    sell_drop_to: int = Field(40, ge=1)
    holding_count: int = Field(20, ge=1)
    weight_mode: Literal["equal_weight", "score_weight"] = "equal_weight"
    max_drop_per_day: int = Field(5, ge=1)
    min_score_coverage: float = Field(0.90, ge=0.0, le=1.0)


class EnhancedIndexingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark: str = "000300.SH"
    active_weight_bound: float = Field(0.02, ge=0.0, le=1.0)
    holding_count: int = Field(30, ge=1)
    weight_mode: Literal["benchmark_tilt"] = "benchmark_tilt"
    min_score_coverage: float = Field(0.90, ge=0.0, le=1.0)
    min_weight_coverage: float = Field(0.90, ge=0.0, le=1.0)


class SoftTopKRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_n: int = Field(30, ge=1)
    weight_func: Literal["softmax", "rank_power"] = "softmax"
    holding_count: int = Field(30, ge=1)
    softmax_temperature: float = Field(1.0, gt=0.0)
    rank_power_alpha: float = Field(1.0, gt=0.0)
    min_score_coverage: float = Field(0.90, ge=0.0, le=1.0)


class MarketTimingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    market_indicator: str = "EMA_60"
    reduce_to: float = Field(0.5, ge=0.0, le=1.0)
    stock_open_filter: Literal["none", "ema", "rsi"] = "rsi"
    stock_ema_period: int = Field(60, ge=2)
    rsi_period: int = Field(14, ge=2)
    rsi_buy_max: float = Field(70.0, ge=0.0, le=100.0)


class ExecutionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_cash: float = Field(1_000_000.0, gt=0.0)
    trade_price: Literal["next_open", "next_close"] = "next_open"
    buy_cost: float = Field(0.0015, ge=0.0)
    sell_cost: float = Field(0.0025, ge=0.0)
    stamp_duty: float = Field(0.001, ge=0.0)
    slippage: float = Field(0.0005, ge=0.0)
    cash_buffer_ratio: float = Field(0.02, ge=0.0, le=1.0)
    enable_detailed_backtest_log: bool = False
    suspend_action: Literal["skip", "queue"] = "skip"
    limit_up_action: Literal["skip_buy", "queue_buy", "allow_buy"] = "skip_buy"
    limit_down_action: Literal["delay_sell", "skip_sell", "force_sell"] = "delay_sell"


class BacktestStrategyFactoryRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_type: Literal["TopKDropout", "EnhancedIndexing", "SoftTopK"] = "TopKDropout"
    TopKDropout: TopKDropoutRule = Field(default_factory=TopKDropoutRule)
    EnhancedIndexing: EnhancedIndexingRule = Field(default_factory=EnhancedIndexingRule)
    SoftTopK: SoftTopKRule = Field(default_factory=SoftTopKRule)
    MarketTiming: MarketTimingRule = Field(default_factory=MarketTimingRule)
    Execution: ExecutionRule = Field(default_factory=ExecutionRule)


def _is_strategy_factory_backtest_rule(backtest_cfg: dict[str, Any]) -> bool:
    return "strategy_type" in backtest_cfg


def _legacy_backtest_rule_to_factory(backtest_cfg: dict[str, Any]) -> dict[str, Any]:
    buy_top_n = int(backtest_cfg.get("buy_top_n", 20) or 20)
    sell_drop_to = int(backtest_cfg.get("sell_drop_to", 40) or 40)
    holding_count = int(backtest_cfg.get("holding_count", buy_top_n) or buy_top_n)
    suggested_drop = min(max(1, buy_top_n // 4), buy_top_n)
    return {
        "strategy_type": "TopKDropout",
        "TopKDropout": {
            "buy_top_n": buy_top_n,
            "sell_drop_to": sell_drop_to,
            "holding_count": holding_count,
            "weight_mode": str(backtest_cfg.get("weight_mode", "equal_weight") or "equal_weight"),
            "max_drop_per_day": int(backtest_cfg.get("max_drop_per_day", suggested_drop) or suggested_drop),
        },
        "EnhancedIndexing": {
            "benchmark": str(backtest_cfg.get("benchmark", "000300.SH") or "000300.SH"),
            "active_weight_bound": float(backtest_cfg.get("active_weight_bound", 0.02) or 0.02),
            "holding_count": int(backtest_cfg.get("enhanced_holding_count", 30) or 30),
            "weight_mode": "benchmark_tilt",
        },
        "SoftTopK": {
            "top_n": int(backtest_cfg.get("soft_top_n", 30) or 30),
            "weight_func": str(backtest_cfg.get("weight_func", "softmax") or "softmax"),
            "holding_count": int(backtest_cfg.get("soft_holding_count", 30) or 30),
        },
        "MarketTiming": {
            "enabled": bool(backtest_cfg.get("market_timing_enabled", False)),
            "market_indicator": str(
                backtest_cfg.get("market_timing_market_indicator",
                backtest_cfg.get("market_timing_indicator", "EMA_60")) or "EMA_60"
            ),
            "reduce_to": float(backtest_cfg.get("market_timing_reduce_to", 0.5) or 0.5),
            "stock_open_filter": str(backtest_cfg.get("market_timing_stock_open_filter", "rsi") or "rsi"),
            "stock_ema_period": int(backtest_cfg.get("market_timing_stock_ema_period", 60) or 60),
            "rsi_period": int(backtest_cfg.get("market_timing_rsi_period", 14) or 14),
            "rsi_buy_max": float(backtest_cfg.get("market_timing_rsi_buy_max", 70.0) or 70.0),
        },
        "Execution": {
            "initial_cash": float(backtest_cfg.get("initial_cash", 1_000_000.0) or 1_000_000.0),
            "trade_price": str(backtest_cfg.get("trade_price", "next_open") or "next_open"),
            "buy_cost": float(backtest_cfg.get("buy_cost", 0.0015) or 0.0015),
            "sell_cost": float(backtest_cfg.get("sell_cost", 0.0025) or 0.0025),
            "slippage": float(backtest_cfg.get("slippage", 0.0005) or 0.0005),
            "enable_detailed_backtest_log": bool(backtest_cfg.get("enable_detailed_backtest_log", False)),
            "suspend_action": str(backtest_cfg.get("suspend_action", "skip") or "skip"),
            "limit_up_action": str(backtest_cfg.get("limit_up_action", "skip_buy") or "skip_buy"),
            "limit_down_action": str(backtest_cfg.get("limit_down_action", "delay_sell") or "delay_sell"),
        },
    }


def validate_backtest_rule_factory(backtest_cfg: dict[str, Any]) -> BacktestStrategyFactoryRule:
    candidate = backtest_cfg if _is_strategy_factory_backtest_rule(backtest_cfg) else _legacy_backtest_rule_to_factory(backtest_cfg)
    return BacktestStrategyFactoryRule.model_validate(candidate)


def normalized_backtest_rule_config() -> dict[str, Any]:
    _, _, backtest_cfg = _load_layered_configs()
    validated = validate_backtest_rule_factory(backtest_cfg)
    return validated.model_dump()


def _active_strategy_fields(rule: dict[str, Any]) -> dict[str, Any]:
    strategy_type = str(rule.get("strategy_type", "TopKDropout"))
    if strategy_type == "TopKDropout":
        return dict(rule["TopKDropout"])
    if strategy_type == "EnhancedIndexing":
        return dict(rule["EnhancedIndexing"])
    if strategy_type == "SoftTopK":
        return dict(rule["SoftTopK"])
    raise ValueError(f"不支持的 strategy_type: {strategy_type}")


def _normalize_label_block(config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    raw_label = normalized.get("label", {})
    if raw_label and not isinstance(raw_label, dict):
        raise ValueError("analysis_rule.label 必须是字典结构")
    label_cfg = _deep_merge(dict(LABEL_DEFAULTS), raw_label if isinstance(raw_label, dict) else {})

    label_cfg["name"] = str(label_cfg.get("name", LABEL_DEFAULTS["name"]) or LABEL_DEFAULTS["name"])
    label_cfg["return_type"] = str(label_cfg.get("return_type", LABEL_DEFAULTS["return_type"]) or LABEL_DEFAULTS["return_type"])
    label_cfg["price_field"] = str(label_cfg.get("price_field", LABEL_DEFAULTS["price_field"]) or LABEL_DEFAULTS["price_field"])
    label_cfg.pop("start_shift", None)
    label_cfg.pop("horizon", None)
    label_cfg.pop("end_shift", None)

    normalized["label"] = label_cfg
    return normalized


def _normalize_preprocess_block(config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    raw_preprocess = normalized.get("preprocess", {})
    if raw_preprocess and not isinstance(raw_preprocess, dict):
        raise ValueError("analysis_rule.preprocess 必须是字典结构")
    preprocess_cfg = _deep_merge(dict(PREPROCESS_DEFAULTS), raw_preprocess if isinstance(raw_preprocess, dict) else {})

    outlier_method = str(preprocess_cfg.get("outlier_method", "none") or "none").strip().lower()
    allowed_outlier_methods = {"none", "mad", "quantile", "sigma"}
    if outlier_method not in allowed_outlier_methods:
        allowed_text = ", ".join(sorted(allowed_outlier_methods))
        raise ValueError(f"preprocess.outlier_method 必须是: {allowed_text}")

    outlier_options = dict(preprocess_cfg.get("outlier_options", {}))
    n_value = outlier_options.get("n", 3.0)
    lower_quantile = outlier_options.get("lower_quantile", 0.01)
    upper_quantile = outlier_options.get("upper_quantile", 0.99)
    outlier_options["n"] = float(3.0 if n_value is None else n_value)
    outlier_options["lower_quantile"] = float(0.01 if lower_quantile is None else lower_quantile)
    outlier_options["upper_quantile"] = float(0.99 if upper_quantile is None else upper_quantile)
    if outlier_options["n"] <= 0:
        raise ValueError("preprocess.outlier_options.n 必须大于 0")
    if not 0 <= outlier_options["lower_quantile"] < outlier_options["upper_quantile"] <= 1:
        raise ValueError("preprocess.outlier_options 的 lower_quantile / upper_quantile 必须满足 0 <= lower < upper <= 1")

    neutralization = str(preprocess_cfg.get("neutralization", "none") or "none").strip().lower()
    allowed_neutralizations = {"none", "industry", "market_cap", "industry_market_cap"}
    if neutralization not in allowed_neutralizations:
        allowed_text = ", ".join(sorted(allowed_neutralizations))
        raise ValueError(f"preprocess.neutralization 必须是: {allowed_text}")

    neutralization_options = dict(preprocess_cfg.get("neutralization_options", {}))
    neutralization_options["industry_field"] = str(
        neutralization_options.get("industry_field", "industry") or "industry"
    ).strip()
    neutralization_options["market_cap_field"] = str(
        neutralization_options.get("market_cap_field", "market_cap") or "market_cap"
    ).strip()

    normalized["preprocess"] = {
        "outlier_method": outlier_method,
        "outlier_options": outlier_options,
        "neutralization": neutralization,
        "neutralization_options": neutralization_options,
    }
    return normalized


def _normalize_price_adjust_block(config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    price_adjust = str(normalized.get("price_adjust", "none") or "none").strip().lower()
    allowed_modes = {"none", "pre", "post"}
    if price_adjust not in allowed_modes:
        allowed_text = ", ".join(sorted(allowed_modes))
        raise ValueError(f"price_adjust 必须是: {allowed_text}")
    normalized["price_adjust"] = price_adjust
    raw_reference_date = normalized.get("price_adjust_reference_date", "auto")
    reference_date_text = str(raw_reference_date or "auto").strip().lower()
    if reference_date_text == "auto":
        normalized["price_adjust_reference_date"] = "auto"
        return normalized
    normalized["price_adjust_reference_date"] = _date_text(_to_timestamp(raw_reference_date))
    return normalized


def ensure_runtime_dirs() -> None:
    for path in [
        DATA_DIR / "tushare_cache",
        OUTPUT_DIR / "health",
        OUTPUT_DIR / "llm",
        OUTPUT_DIR / "backtest",
        OUTPUT_DIR / "_runtime",
        OUTPUT_DIR / "train_windows",
    ]:
        path.mkdir(parents=True, exist_ok=True)


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_substitute_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _substitute_env(val) for key, val in value.items()}
    return value


def load_yaml_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    return _substitute_env(payload)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def runtime_context() -> dict[str, Any]:
    if not RUNTIME_CONTEXT_PATH.exists():
        return {}
    return read_json(RUNTIME_CONTEXT_PATH)


def set_runtime_context(payload: dict[str, Any]) -> None:
    write_json(RUNTIME_CONTEXT_PATH, payload)


def clear_runtime_context() -> None:
    if RUNTIME_CONTEXT_PATH.exists():
        RUNTIME_CONTEXT_PATH.unlink()
    WINDOW_RUNTIME_CACHE.clear()
    try:
        from data_provider import ProviderFactory

        ProviderFactory.reset_active_provider()
    except Exception:
        pass


def _flatten_config_keys(payload: dict[str, Any], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in payload.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        keys.add(full_key)
        if isinstance(value, dict):
            keys.update(_flatten_config_keys(value, full_key))
    return keys


def _validate_config_ownership(
    env_cfg: dict[str, Any],
    analysis_cfg: dict[str, Any],
    backtest_cfg: dict[str, Any],
) -> None:
    file_items = [
        ("env.yaml", env_cfg, "env"),
        ("analysis_rule.yaml", analysis_cfg, "analysis_rule"),
        ("backtest_rule.yaml", backtest_cfg, "backtest_rule"),
    ]

    # 校验同名键（完整路径）是否在多份配置中重复定义。
    key_to_files: dict[str, list[str]] = {}
    for file_name, cfg, _ in file_items:
        for key in _flatten_config_keys(cfg):
            key_to_files.setdefault(key, []).append(file_name)
    duplicates = {key: names for key, names in key_to_files.items() if len(names) > 1}
    if duplicates:
        details = "; ".join(
            f"{key} -> {', '.join(sorted(names))}" for key, names in sorted(duplicates.items())
        )
        raise ValueError(f"配置归属冲突：存在跨 YAML 重复定义参数：{details}")

    # 校验顶层参数是否落在其归属文件中。
    expected_files: dict[str, str] = {}
    for owner_name, owner_keys in CONFIG_OWNERSHIP.items():
        owner_file = f"{owner_name}.yaml" if owner_name != "analysis_rule" else "analysis_rule.yaml"
        for key in owner_keys:
            expected_files[key] = owner_file

    violations: list[str] = []
    for file_name, cfg, owner_name in file_items:
        for key in cfg.keys():
            expected = expected_files.get(str(key))
            if expected and expected != file_name:
                violations.append(f"{key} 应定义在 {expected}，当前位于 {file_name}")
        unknown = sorted(set(str(key) for key in cfg.keys()) - CONFIG_OWNERSHIP[owner_name])
        if unknown:
            violations.append(f"{file_name} 包含未登记归属参数: {', '.join(unknown)}")
    if violations:
        raise ValueError("配置归属校验失败：" + " | ".join(violations))


def _load_layered_configs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    env_cfg = load_yaml_file(CONFIG_DIR / "env.yaml")
    analysis_cfg = load_yaml_file(CONFIG_DIR / "analysis_rule.yaml")
    backtest_cfg = load_yaml_file(CONFIG_DIR / "backtest_rule.yaml")
    _validate_config_ownership(env_cfg, analysis_cfg, backtest_cfg)
    return env_cfg, analysis_cfg, backtest_cfg


def _normalize_data_cache_dir(config: dict[str, Any]) -> dict[str, Any]:
    ts_cfg = config.get("tushare")
    if not isinstance(ts_cfg, dict):
        return config
    raw_dir = ts_cfg.get("data_cache_dir")
    if not raw_dir:
        ts_cfg["data_cache_dir"] = str(DATA_DIR / "tushare_cache")
        return config
    p = Path(raw_dir)
    if not p.is_absolute():
        ts_cfg["data_cache_dir"] = str((DATA_DIR / p).resolve())
    return config


def env_config() -> dict[str, Any]:
    env_cfg, analysis_cfg, _ = _load_layered_configs()
    config = _deep_merge(env_cfg, analysis_cfg)
    context = runtime_context()
    if context:
        config = _deep_merge(config, context)
    config = _normalize_data_cache_dir(config)
    return _normalize_price_adjust_block(
        _normalize_preprocess_block(_normalize_label_block(config))
    )


def analysis_rule_config() -> dict[str, Any]:
    _, analysis_cfg, _ = _load_layered_configs()
    context = runtime_context()
    if context:
        analysis_keys = set(analysis_cfg.keys())
        analysis_context = {key: value for key, value in context.items() if key in analysis_keys}
        if analysis_context:
            analysis_cfg = _deep_merge(analysis_cfg, analysis_context)
    return _normalize_price_adjust_block(
        _normalize_preprocess_block(_normalize_label_block(analysis_cfg))
    )


def label_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or env_config()
    return dict(_normalize_label_block(cfg).get("label", {}))


def preprocess_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or env_config()
    return dict(_normalize_preprocess_block(cfg).get("preprocess", {}))


def label_name(config: dict[str, Any] | None = None) -> str:
    return str(label_config(config).get("name", "label"))


def label_signature(config: dict[str, Any] | None = None) -> str:
    cfg = config or env_config()
    label_cfg = label_config(cfg)
    rebalance = str(cfg.get("rebalance", "weekly")).strip().lower()
    interval = max(int(cfg.get("rebalance_interval", 1) or 1), 1)
    default_anchor = "first_trading_day_of_month" if rebalance == "monthly" else "first_trading_day_of_week"
    anchor = str(cfg.get("rebalance_anchor", default_anchor)).strip().lower()
    return (
        f"{label_cfg.get('return_type', 'period_return')}("
        f"{label_cfg.get('price_field', 'close')},"
        f"rebalance={rebalance},"
        f"interval={interval},"
        f"anchor={anchor})"
    )


def analysis_label_signature(config: dict[str, Any] | None = None) -> str:
    return label_signature(config)


def label_description(config: dict[str, Any] | None = None) -> str:
    cfg = config or env_config()
    label_cfg = label_config(cfg)
    rebalance = str(cfg.get("rebalance", "weekly")).strip().lower()
    interval = max(int(cfg.get("rebalance_interval", 1) or 1), 1)
    if rebalance == "daily":
        anchor = "n/a"
    else:
        default_anchor = "first_trading_day_of_month" if rebalance == "monthly" else "first_trading_day_of_week"
        anchor = str(cfg.get("rebalance_anchor", default_anchor)).strip().lower()
    return (
        f"name={label_cfg.get('name')}, return_type={label_cfg.get('return_type')}, "
        f"price_field={label_cfg.get('price_field')}, rebalance={rebalance}, "
        f"interval={interval}, anchor={anchor}"
    )


def preprocess_signature(config: dict[str, Any] | None = None) -> str:
    cfg = preprocess_config(config)
    steps: list[str] = []
    outlier_method = str(cfg.get("outlier_method", "none"))
    outlier_options = dict(cfg.get("outlier_options", {}))
    if outlier_method == "mad":
        steps.append(f"mad(n={outlier_options.get('n', 3.0):.2f})")
    elif outlier_method == "quantile":
        steps.append(
            "quantile"
            f"({outlier_options.get('lower_quantile', 0.01):.2f},{outlier_options.get('upper_quantile', 0.99):.2f})"
        )
    elif outlier_method == "sigma":
        steps.append(f"3sigma(n={outlier_options.get('n', 3.0):.2f})")

    neutralization = str(cfg.get("neutralization", "none"))
    neutralization_options = dict(cfg.get("neutralization_options", {}))
    if neutralization == "industry":
        steps.append(f"industry({neutralization_options.get('industry_field', 'industry')})")
    elif neutralization == "market_cap":
        steps.append(f"market_cap({neutralization_options.get('market_cap_field', 'market_cap')})")
    elif neutralization == "industry_market_cap":
        steps.append(
            "industry_market_cap("
            f"{neutralization_options.get('industry_field', 'industry')},"
            f"{neutralization_options.get('market_cap_field', 'market_cap')})"
        )
    return "disabled" if not steps else " -> ".join(steps)


def analysis_profile(config: dict[str, Any] | None = None) -> str:
    cfg = config or env_config()
    rebalance = str(cfg.get("rebalance", "weekly")).strip().lower()
    interval = max(int(cfg.get("rebalance_interval", 1) or 1), 1)
    default_anchor = "first_trading_day_of_month" if rebalance == "monthly" else "first_trading_day_of_week"
    anchor = str(cfg.get("rebalance_anchor", default_anchor)).strip().lower()
    return (
        f"profile=period_return_ic("
        f"rebalance={rebalance},"
        f"interval={interval},"
        f"anchor={anchor})"
    )


def feature_pool_config() -> dict[str, Any]:
    config = load_yaml_file(CONFIG_DIR / "feature_pool.yaml")
    enable_chip_features = bool(config.get("enable_chip_features", True))
    config["enable_chip_features"] = enable_chip_features
    if not enable_chip_features:
        filtered_features: list[dict[str, Any]] = []
        for item in config.get("base_features", []):
            expr = str(item.get("expr", ""))
            if expr.startswith("chip."):
                continue
            filtered_features.append(item)
        config["base_features"] = filtered_features
    return config


def market_context_config() -> dict[str, Any]:
    if not MARKET_CONTEXT_CONFIG_PATH.exists():
        return {}
    return load_yaml_file(MARKET_CONTEXT_CONFIG_PATH)


def selector_config() -> dict[str, Any]:
    if not SELECTOR_CONFIG_PATH.exists():
        return {}
    return load_yaml_file(SELECTOR_CONFIG_PATH)


def score_config() -> dict[str, Any]:
    if not SCORE_CONFIG_PATH.exists():
        return {}
    return load_yaml_file(SCORE_CONFIG_PATH)


def backtest_rule_config() -> dict[str, Any]:
    normalized = normalized_backtest_rule_config()
    active = _active_strategy_fields(normalized)
    execution = dict(normalized.get("Execution", {}))
    return {
        **active,
        **execution,
        "strategy_type": normalized.get("strategy_type"),
        "TopKDropout": normalized.get("TopKDropout", {}),
        "EnhancedIndexing": normalized.get("EnhancedIndexing", {}),
        "SoftTopK": normalized.get("SoftTopK", {}),
        "MarketTiming": normalized.get("MarketTiming", {}),
        "Execution": execution,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _to_timestamp(value: Any) -> pd.Timestamp:
    return pd.Timestamp(str(value)).normalize()


def _date_text(value: pd.Timestamp) -> str:
    return value.strftime("%Y-%m-%d")


def active_run_window(config: dict[str, Any] | None = None) -> tuple[pd.Timestamp, pd.Timestamp]:
    cfg = config or env_config()
    run_mode = str(cfg.get("run_mode", "train")).strip().lower()
    if run_mode == "test":
        start_time = _to_timestamp(cfg.get("test_start_date"))
        end_time = _to_timestamp(cfg.get("test_end_date"))
    else:
        start_time = _to_timestamp(cfg.get("train_start_date"))
        end_time = _to_timestamp(cfg.get("train_end_date"))
    return start_time, end_time


def clip_to_active_window(
    frame: pd.DataFrame | pd.Series,
    config: dict[str, Any] | None = None,
) -> pd.DataFrame | pd.Series:
    start_time, end_time = active_run_window(config)
    datetime_index = frame.index.get_level_values("datetime")
    mask = (datetime_index >= start_time) & (datetime_index <= end_time)
    return frame.loc[mask]


def _base_feature_expr_warmup(expr: str, known_feature_warmups: dict[str, int]) -> int:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return 0

    direct_windows = 0
    dependency_warmup = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            dependency_warmup = max(dependency_warmup, known_feature_warmups.get(node.id, 0))
            continue
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method_name = node.func.attr
        if method_name == "pct_change":
            if not node.args:
                direct_windows += 1
            elif _is_number_constant(node.args[0]):
                direct_windows += int(_number_constant_value(node.args[0]))
        elif method_name in {"rolling", "shift"}:
            if node.args and _is_number_constant(node.args[0]):
                direct_windows += int(_number_constant_value(node.args[0]))
    return direct_windows + dependency_warmup


def estimate_feature_pool_warmup(feature_cfg: dict[str, Any] | None = None) -> int:
    fp_cfg = feature_cfg or feature_pool_config()
    feature_warmups: dict[str, int] = {}
    for item in fp_cfg.get("base_features", []):
        name = str(item.get("name", ""))
        expr = str(item.get("expr", ""))
        if not name:
            continue
        feature_warmups[name] = _base_feature_expr_warmup(expr, feature_warmups)
    return max(feature_warmups.values(), default=0)


def estimate_formula_warmup(formula: str) -> int:
    if not formula:
        return 0
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError:
        return 0

    total_windows = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        for arg_index in WINDOW_OPERATOR_ARG_INDEXES.get(node.func.id, []):
            if len(node.args) > arg_index and _is_number_constant(node.args[arg_index]):
                total_windows += int(_number_constant_value(node.args[arg_index]))
    return total_windows


def estimate_required_warmup(
    feature_cfg: dict[str, Any] | None = None,
    formulas: list[str] | None = None,
) -> int:
    feature_warmup = estimate_feature_pool_warmup(feature_cfg)
    formula_warmup = max((estimate_formula_warmup(formula) for formula in (formulas or [])), default=0)
    return max(feature_warmup, formula_warmup + feature_warmup)


def build_training_windows(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = config or env_config()
    workflow = dict(cfg.get("training_workflow", {}))
    mode = str(workflow.get("mode", "static_split")).strip().lower()
    train_start = _to_timestamp(cfg.get("train_start_date"))
    train_end = _to_timestamp(cfg.get("train_end_date"))
    if train_end <= train_start:
        raise ValueError("train_end_date 必须晚于 train_start_date")
    if mode == "static_split":
        static_cfg = dict(workflow.get("static_split", {}))
        discovery_start = _to_timestamp(static_cfg.get("discovery_start_date", train_start))
        discovery_end = _to_timestamp(static_cfg.get("discovery_end_date", train_end))
        validation_start = _to_timestamp(static_cfg.get("validation_start_date", discovery_end + pd.Timedelta(days=1)))
        validation_end = _to_timestamp(static_cfg.get("validation_end_date", train_end))
        if discovery_start < train_start:
            raise ValueError("static_split.discovery_start_date 不能早于 train_start_date")
        if discovery_end > train_end:
            raise ValueError("static_split.discovery_end_date 不能晚于 train_end_date")
        if validation_start < train_start:
            raise ValueError("static_split.validation_start_date 不能早于 train_start_date")
            
        # 如果 validation_end 超出了总时间，自动截断到 train_end
        if validation_end > train_end:
            validation_end = train_end
            
        if not (discovery_start <= discovery_end < validation_start <= validation_end):
            raise ValueError("static_split 要求 discovery 与 validation 时间区间顺序不重叠")
        return [
            {
                "window_id": "window_01",
                "mode": mode,
                "discovery_start_date": _date_text(discovery_start),
                "discovery_end_date": _date_text(discovery_end),
                "validation_start_date": _date_text(validation_start),
                "validation_end_date": _date_text(validation_end),
            }
        ]
    if mode == "walk_forward":
        walk_cfg = dict(workflow.get("walk_forward", {}))
        discovery_months = int(walk_cfg.get("discovery_window_months", 24))
        validation_months = int(walk_cfg.get("validation_window_months", 6))
        step_months = int(walk_cfg.get("step_months", validation_months))
        max_windows = int(walk_cfg.get("max_windows", 0))
        if discovery_months <= 0 or validation_months <= 0 or step_months <= 0:
            raise ValueError("walk_forward 的月份参数必须为正整数")
        windows: list[dict[str, Any]] = []
        cursor = train_start
        index = 1
        while True:
            discovery_end = cursor + pd.DateOffset(months=discovery_months) - pd.Timedelta(days=1)
            # 1. 如果连 discovery 都超出了总时间，完全没有空间，直接结束
            if discovery_end >= train_end:
                break
                
            validation_start = discovery_end + pd.Timedelta(days=1)
            validation_end = validation_start + pd.DateOffset(months=validation_months) - pd.Timedelta(days=1)
            
            # 2. 如果 validation 阶段超出了总时间，自动截断到 train_end
            if validation_end > train_end:
                validation_end = train_end
                
            windows.append(
                {
                    "window_id": f"window_{index:02d}",
                    "mode": mode,
                    "discovery_start_date": _date_text(cursor),
                    "discovery_end_date": _date_text(discovery_end),
                    "validation_start_date": _date_text(validation_start),
                    "validation_end_date": _date_text(validation_end),
                }
            )
            if max_windows > 0 and len(windows) >= max_windows:
                break
                
            # 下一次滚动的起点
            cursor = cursor + pd.DateOffset(months=step_months)
            index += 1
            
            # 3. 提前预判下一次滚动：如果光游标就已经超过或太接近 train_end，直接结束
            if cursor >= train_end:
                break
        if not windows:
            raise ValueError("walk_forward 未生成任何窗口，请检查 train 区间与月份配置")
        return windows
    raise ValueError(f"不支持的 training_workflow.mode: {mode}")


def write_table(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".csv":
        frame.to_csv(path, index=True)
        return
    if path.suffix == ".parquet":
        frame.to_parquet(path, index=True)
        return
    raise ValueError(f"Unsupported table format: {path}")


def get_data_provider(config: dict[str, Any] | None = None) -> "BaseDataProvider":
    """获取数据提供者实例
    
    根据配置中的 data_source 字段创建对应的数据提供者。
    当前仅支持 tushare。
    
    Args:
        config: 配置字典，如果为 None 则加载默认配置
        
    Returns:
        数据提供者实例
        
    示例:
        >>> provider = get_data_provider()
        >>> provider.initialize()
        >>> instruments = provider.get_instruments()
    """
    from data_provider import ProviderFactory
    
    cfg = config or env_config()
    return ProviderFactory.create_provider(cfg)


def init_data_source(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """初始化数据源（当前仅支持 Tushare）
    
    根据配置自动选择并初始化对应的数据源。
    
    Args:
        config: 配置字典
        
    Returns:
        配置字典
    """
    cfg = config or env_config()
    data_source = str(cfg.get("data_source", "tushare")).strip().lower()
    if data_source != "tushare":
        raise ValueError(f"当前仅支持 data_source=tushare，收到: {data_source}")
    provider = get_data_provider(cfg)
    provider.initialize()
    return cfg


def _dynamic_index_pool_enabled(config: dict[str, Any]) -> bool:
    stock_pool_cfg = dict(config.get("stock_pool", {}))
    pool_type = str(stock_pool_cfg.get("type", "all_market") or "all_market").strip().lower()
    dynamic_membership = bool(stock_pool_cfg.get("dynamic_membership", False))
    return pool_type == "index_components" and dynamic_membership


def _observation_dates_from_run_window(config: dict[str, Any]) -> list[pd.Timestamp]:
    start_time, end_time = active_run_window(config)
    provider = get_data_provider(config)
    provider.initialize()
    trading_dates = provider.get_trading_dates(_date_text(start_time), _date_text(end_time))
    if not trading_dates:
        return []
    date_index = pd.Index(pd.to_datetime(trading_dates))
    return analysis_observation_dates(date_index, config)


def _dynamic_index_components_by_observation_date(
    config: dict[str, Any],
    observation_dates: list[pd.Timestamp],
) -> dict[pd.Timestamp, set[str]]:
    stock_pool_cfg = dict(config.get("stock_pool", {}))
    index_code = str(stock_pool_cfg.get("index_code", "000300.XSHG") or "000300.XSHG").strip()
    include_st = bool(stock_pool_cfg.get("include_st", True))
    include_new_stock = bool(stock_pool_cfg.get("include_new_stock", True))
    new_stock_days = int(stock_pool_cfg.get("new_stock_days", 60) or 60)
    date_keys = tuple(pd.Timestamp(item).normalize().strftime("%Y-%m-%d") for item in observation_dates)
    cache_key = (index_code, date_keys, include_st, include_new_stock, new_stock_days)
    cached = DYNAMIC_INDEX_COMPONENT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    provider = get_data_provider(config)
    provider.initialize()
    component_map: dict[pd.Timestamp, set[str]] = {}
    latest_snapshot = set(provider.get_index_components(index_code))
    last_valid_snapshot = set(latest_snapshot)
    empty_fetch_count = 0
    for date in observation_dates:
        normalized_date = pd.Timestamp(date).normalize()
        codes = provider.get_index_components(index_code, date=_date_text(normalized_date))
        snapshot = set(codes)
        if snapshot:
            last_valid_snapshot = snapshot
            component_map[normalized_date] = snapshot
            continue
        empty_fetch_count += 1
        if last_valid_snapshot:
            component_map[normalized_date] = set(last_valid_snapshot)
        else:
            component_map[normalized_date] = set(latest_snapshot)
    component_map = _apply_dynamic_universe_filters(config, component_map)
    if empty_fetch_count:
        print(
            f"动态指数成分股有 {empty_fetch_count} 个观测日返回空集，"
            "已自动回退为最近可用成分"
        )
    DYNAMIC_INDEX_COMPONENT_CACHE[cache_key] = component_map
    return component_map


def _to_ts_code_from_internal(code: str) -> str:
    text = str(code).strip()
    if text.startswith("SH") and len(text) == 8:
        return f"{text[2:]}.SH"
    if text.startswith("SZ") and len(text) == 8:
        return f"{text[2:]}.SZ"
    return text


def _namechange_history(provider: Any, code: str) -> pd.DataFrame:
    ts_code = _to_ts_code_from_internal(code)
    cached = NAMECHANGE_CACHE.get(ts_code)
    if cached is not None:
        return cached
    try:
        frame = provider._call_pro_api(  # type: ignore[attr-defined]
            "namechange",
            ts_code=ts_code,
            fields="ts_code,name,start_date,end_date,change_reason",
        )
    except Exception:
        frame = pd.DataFrame()
    if frame is None or frame.empty:
        frame = pd.DataFrame(columns=["ts_code", "name", "start_date", "end_date", "change_reason"])
    else:
        frame = frame.copy()
        frame["start_date"] = frame["start_date"].astype(str)
        frame["end_date"] = frame["end_date"].fillna("").astype(str)
        frame = frame.drop_duplicates(subset=["ts_code", "name", "start_date", "end_date", "change_reason"])
    NAMECHANGE_CACHE[ts_code] = frame
    return frame


def _is_historical_st(provider: Any, code: str, date: pd.Timestamp, current_name: str) -> bool:
    history = _namechange_history(provider, code)
    date_text = date.strftime("%Y%m%d")
    if not history.empty:
        active = history[
            (history["start_date"] <= date_text)
            & ((history["end_date"] == "") | (history["end_date"] >= date_text))
        ]
        if not active.empty:
            return any("ST" in str(name).upper() for name in active["name"])
    return "ST" in str(current_name).upper()


def _apply_dynamic_universe_filters(
    config: dict[str, Any],
    component_map: dict[pd.Timestamp, set[str]],
) -> dict[pd.Timestamp, set[str]]:
    stock_pool_cfg = dict(config.get("stock_pool", {}))
    include_st = bool(stock_pool_cfg.get("include_st", True))
    include_new_stock = bool(stock_pool_cfg.get("include_new_stock", True))
    new_stock_days = int(stock_pool_cfg.get("new_stock_days", 60) or 60)
    if include_st and include_new_stock:
        return component_map
    provider = get_data_provider(config)
    provider.initialize()
    try:
        stock_basic = provider._call_pro_api(  # type: ignore[attr-defined]
            "stock_basic",
            exchange="",
            list_status="L",
            fields="ts_code,name,list_date",
        )
    except Exception:
        return component_map
    if stock_basic is None or stock_basic.empty:
        return component_map
    stock_basic = stock_basic.copy()
    stock_basic["instrument"] = stock_basic["ts_code"].map(
        lambda code: code if "." not in str(code) else (
            f"SH{str(code).split('.')[0]}" if str(code).endswith(".SH") else (
                f"SZ{str(code).split('.')[0]}" if str(code).endswith(".SZ") else str(code)
            )
        )
    )
    meta = stock_basic.set_index("instrument")[["name", "list_date"]]
    filtered_map: dict[pd.Timestamp, set[str]] = {}
    for date, codes in component_map.items():
        cutoff = pd.Timestamp(date).normalize()
        min_listed_date = (cutoff - pd.Timedelta(days=new_stock_days)).strftime("%Y%m%d")
        filtered_codes: set[str] = set()
        for code in codes:
            if code not in meta.index:
                filtered_codes.add(code)
                continue
            name = str(meta.loc[code, "name"])
            list_date = str(meta.loc[code, "list_date"])
            if not include_st and _is_historical_st(provider, code, cutoff, name):
                continue
            if not include_new_stock and list_date and list_date > min_listed_date:
                continue
            filtered_codes.add(code)
        filtered_map[date] = filtered_codes
    return filtered_map


def _membership_mask_for_index(
    index: pd.MultiIndex,
    component_map: dict[pd.Timestamp, set[str]],
) -> pd.Series:
    datetimes = pd.to_datetime(index.get_level_values("datetime")).normalize()
    instruments = index.get_level_values("instrument")
    membership = [
        instrument in component_map.get(date, set())
        for instrument, date in zip(instruments, datetimes)
    ]
    return pd.Series(membership, index=index, dtype=bool)


def build_dynamic_universe_mask(
    index: pd.MultiIndex,
    config: dict[str, Any] | None = None,
) -> pd.Series:
    cfg = config or env_config()
    if not _dynamic_index_pool_enabled(cfg):
        return pd.Series(True, index=index, dtype=bool)
    # Callers already pass observation-level indexes here (e.g. label period
    # returns or observation-frame factors). Recomputing observation dates from
    # this sparse index can incorrectly drop the first anchor date.
    observation_dates = [
        pd.Timestamp(item).normalize()
        for item in pd.Index(pd.to_datetime(index.get_level_values("datetime"))).unique().sort_values()
    ]
    if not observation_dates:
        return pd.Series(False, index=index, dtype=bool)
    component_map = _dynamic_index_components_by_observation_date(cfg, observation_dates)
    return _membership_mask_for_index(index, component_map)


def list_instruments(config: dict[str, Any] | None = None) -> list[str]:
    cfg = config or env_config()
    
    # 检查是否使用新的股票池管理器
    stock_pool_config = cfg.get("stock_pool", {})
    if stock_pool_config:
        try:
            from stock_pool_manager import create_stock_pool_manager, validate_stock_pool_config
            
            is_valid, message = validate_stock_pool_config(cfg)
            if not is_valid:
                print(f"股票池配置配置验证失败: {message}")
                print("使用默认的全市场股票池")
            else:
                manager = create_stock_pool_manager(cfg)
                
                run_mode = cfg.get("run_mode", "train")
                if run_mode == "test":
                    start_time = str(cfg.get("test_start_date"))
                    end_time = str(cfg.get("test_end_date"))
                else:
                    start_time = str(cfg.get("train_start_date"))
                    end_time = str(cfg.get("train_end_date"))
                
                stock_pool = manager.get_stock_pool(start_time, end_time)
                pool_info = manager.get_pool_info()
                print(f"使用股票池: {pool_info['description']}")
                
                return stock_pool
                
        except ImportError:
            print("股票池管理器未找到，使用默认方法")
        except Exception as e:
            print(f"股票池管理器出错: {e}，使用默认方法")
    
    run_mode = cfg.get("run_mode", "train")
    if run_mode == "test":
        start_time = str(cfg.get("test_start_date"))
        end_time = str(cfg.get("test_end_date"))
    else:
        start_time = str(cfg.get("train_start_date"))
        end_time = str(cfg.get("train_end_date"))
    
    provider = get_data_provider(cfg)
    provider.initialize()
    codes = provider.get_instruments()
    print(f"从 tushare 获取股票列表: {len(codes)} 只")
    
    # 只有在没有配置 stock_pool 时才使用 max_instruments
    max_instruments = int(cfg.get("max_instruments", 0) or 0)
    if max_instruments > 0:
        print(f"使用 max_instruments 限制: {max_instruments} 只股票（按字母顺序）")
        return codes[:max_instruments]
    
    return codes


def raw_field_tokens(raw_fields: list) -> list[str]:
    """将 raw_fields 转为 tushare 字段 token，自动加 $ 前缀。

    raw_fields 中的元素可以是字符串或 dict（含 name/description）。
    """
    result: list[str] = []
    for field in raw_fields:
        name = field["name"] if isinstance(field, dict) else field
        result.append(name if name.startswith("$") else f"${name}")
    return result


def load_raw_data(
    config: dict[str, Any] | None = None,
    raw_fields: list[str] | None = None,
    warmup_trading_days: int = 0,
    forward_trading_days: int = 0,
) -> pd.DataFrame:
    cfg = dict(config or env_config())
    fp_cfg = feature_pool_config()
    fields_raw = raw_fields or list(fp_cfg.get("raw_fields", []))
    # raw_fields 中的元素可能是 dict（含 name/description），提取 name
    fields = list(dict.fromkeys(
        f["name"] if isinstance(f, dict) else f for f in fields_raw
    ))
    
    start_timestamp, end_timestamp = active_run_window(cfg)
    if warmup_trading_days > 0:
        # 预热期只用于计算时序特征，不改变最终统计区间。
        start_timestamp = (start_timestamp - pd.offsets.BDay(int(warmup_trading_days) + 5)).normalize()
    if forward_trading_days > 0:
        # 向后缓冲仅用于构建“下一期收益标签”，最终评估窗口仍由 clip_to_active_window 控制。
        end_timestamp = (end_timestamp + pd.offsets.BDay(int(forward_trading_days) + 5)).normalize()
    start_time = _date_text(start_timestamp)
    end_time = _date_text(end_timestamp)
    if str(cfg.get("price_adjust_reference_date", "auto") or "auto").strip().lower() == "auto":
        cfg["price_adjust_reference_date"] = end_time

    cached_frame = _get_cached_raw_frame(cfg, fields, start_timestamp, end_timestamp)
    if cached_frame is not None:
        if _dynamic_index_pool_enabled(cfg):
            observation_dates = _observation_dates_from_run_window(cfg)
            component_map = _dynamic_index_components_by_observation_date(cfg, observation_dates)
            instruments = len({code for codes in component_map.values() for code in codes})
        else:
            instruments = int(cached_frame.index.get_level_values("instrument").nunique()) if not cached_frame.empty else 0
        log_data_loading(start_time, end_time, instruments, len(cached_frame))
        return cached_frame
    
    if _dynamic_index_pool_enabled(cfg):
        observation_dates = _observation_dates_from_run_window(cfg)
        component_map = _dynamic_index_components_by_observation_date(cfg, observation_dates)
        instruments = sorted({code for codes in component_map.values() for code in codes})
    else:
        instruments = list_instruments(cfg)
    
    provider = get_data_provider(cfg)
    provider.initialize()
    frame = provider.get_features(
        instruments=instruments,
        fields=raw_field_tokens(fields),
        start_date=start_time,
        end_date=end_time,
        freq=str(cfg.get("freq", "day")),
    )
    log_data_loading(start_time, end_time, len(instruments), len(frame))
    
    frame = frame.sort_index()
    frame.columns = [column.replace("$", "") for column in frame.columns]
    
    # 统一索引名称（支持单索引和 MultiIndex）
    if isinstance(frame.index, pd.MultiIndex):
        # MultiIndex: 设置为 (instrument, datetime)
        if len(frame.index.names) == 2:
            frame.index = frame.index.set_names(["instrument", "datetime"])
    else:
        # 单索引: 尝试转换为 MultiIndex
        if frame.index.name is None or frame.index.name == "":
            frame.index.name = "datetime"
    
    _store_cached_raw_frame(cfg, fields, start_timestamp, end_timestamp, frame)
    return frame


def estimate_label_forward_days(config: dict[str, Any] | None = None) -> int:
    """估算为标签构建所需的向后交易日缓冲天数。"""
    cfg = config or env_config()
    rebalance = str(cfg.get("rebalance", "weekly")).strip().lower()
    interval = max(int(cfg.get("rebalance_interval", 1) or 1), 1)
    if rebalance == "daily":
        return interval
    if rebalance == "weekly":
        return 5 * interval
    if rebalance == "monthly":
        return 22 * interval
    return 5 * interval


def _calculate_chip_distribution(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    window: int = 210,
    bins: int = 100,
) -> dict[str, Any]:
    if len(high) < window:
        window = len(high)
    
    if window < 2:
        return {
            'concentration': 1.0,
            'profit_ratio': 0.5,
            'avg_cost': float(close.iloc[-1]),
            'peak_price': float(close.iloc[-1]),
        }
    
    recent_high = float(high.iloc[-window:].max())
    recent_low = float(low.iloc[-window:].min())
    
    if recent_high <= recent_low:
        recent_high = recent_low * 1.01
    
    price_levels = np.linspace(recent_low, recent_high, bins + 1)
    price_centers = (price_levels[:-1] + price_levels[1:]) / 2
    
    chip_distribution = np.zeros(bins)
    
    for i in range(window):
        idx = -(i + 1)
        day_low = float(low.iloc[idx])
        day_high = float(high.iloc[idx])
        day_volume = float(volume.iloc[idx])
        
        if day_high <= day_low or day_volume <= 0:
            continue
        
        bin_low = np.searchsorted(price_levels, day_low, side='left')
        bin_high = np.searchsorted(price_levels, day_high, side='right')
        
        bin_low = max(0, min(bin_low, bins - 1))
        bin_high = max(bin_low + 1, min(bin_high, bins))
        
        num_bins_in_range = bin_high - bin_low
        if num_bins_in_range > 0:
            volume_per_bin = day_volume / num_bins_in_range
            chip_distribution[bin_low:bin_high] += volume_per_bin
    
    total_volume = chip_distribution.sum()
    if total_volume <= 0:
        chip_ratio = np.ones(bins) / bins
    else:
        chip_ratio = chip_distribution / total_volume
    
    avg_cost = float(np.sum(price_centers * chip_ratio))
    
    current_price = float(close.iloc[-1])
    profit_mask = price_centers < current_price
    profit_ratio = float(chip_ratio[profit_mask].sum())
    
    p10 = np.percentile(price_centers, 10)
    p90 = np.percentile(price_centers, 90)
    if avg_cost > 0:
        concentration = float((p90 - p10) / avg_cost)
    else:
        concentration = 1.0
    
    peak_idx = np.argmax(chip_ratio)
    peak_price = float(price_centers[peak_idx])
    
    return {
        'concentration': concentration,
        'profit_ratio': profit_ratio,
        'avg_cost': avg_cost,
        'peak_price': peak_price,
    }


def _calculate_chip_features_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    window: int = 210,
    bins: int = 100,
) -> pd.DataFrame:
    n = len(close)
    
    concentration = np.full(n, np.nan)
    profit_ratio = np.full(n, np.nan)
    avg_cost = np.full(n, np.nan)
    avg_cost_distance = np.full(n, np.nan)
    peak_price = np.full(n, np.nan)
    peak_distance = np.full(n, np.nan)
    
    for i in range(window - 1, n):
        start_idx = i - window + 1
        end_idx = i + 1
        
        result = _calculate_chip_distribution(
            high.iloc[start_idx:end_idx],
            low.iloc[start_idx:end_idx],
            close.iloc[start_idx:end_idx],
            volume.iloc[start_idx:end_idx],
            window=window,
            bins=bins
        )
        
        concentration[i] = result['concentration']
        profit_ratio[i] = result['profit_ratio']
        avg_cost[i] = result['avg_cost']
        peak_price[i] = result['peak_price']
        
        current_close = float(close.iloc[i])
        if result['avg_cost'] > 0:
            avg_cost_distance[i] = (current_close - result['avg_cost']) / result['avg_cost']
        
        if result['peak_price'] > 0:
            peak_distance[i] = (current_close - result['peak_price']) / result['peak_price']
    
    return pd.DataFrame({
        'chip_concentration': concentration,
        'profit_ratio': profit_ratio,
        'avg_cost': avg_cost,
        'avg_cost_distance': avg_cost_distance,
        'peak_price': peak_price,
        'peak_distance': peak_distance,
    }, index=close.index)


def _calculate_chip_features(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    windows: list[int] | None = None,
    bins: int = 100,
) -> pd.DataFrame:
    if windows is None:
        windows = [90, 210]
    
    features = pd.DataFrame(index=close.index)
    
    for window in windows:
        result = _calculate_chip_features_series(
            high, low, close, volume, window=window, bins=bins
        )
        
        features[f'chip_concentration_{window}d'] = result['chip_concentration']
        features[f'profit_ratio_{window}d'] = result['profit_ratio']
        features[f'avg_cost_distance_{window}d'] = result['avg_cost_distance']
        features[f'peak_distance_{window}d'] = result['peak_distance']
    
    return features


def _compute_group_features(group: pd.DataFrame, base_features: list[dict[str, str]]) -> pd.DataFrame:
    local = group.droplevel("instrument").copy()
    valid_mask = local["close"].notna()
    local_valid = local.loc[valid_mask].copy()
    env = {column: local_valid[column] for column in local_valid.columns}
    values: dict[str, pd.Series] = {}
    
    chip_features_computed = False
    chip_features_df = None
    
    for item in base_features:
        name = str(item["name"])
        expr = _normalize_pct_change_expr(str(item["expr"]))
        
        if expr.startswith("chip.") and not chip_features_computed:
            required_cols = ["high", "low", "close", "volume"]
            if all(col in local_valid.columns for col in required_cols):
                chip_features_df = _calculate_chip_features(
                    local_valid["high"], local_valid["low"], local_valid["close"], local_valid["volume"]
                )
                for col in chip_features_df.columns:
                    env[col] = chip_features_df[col]
            chip_features_computed = True
        
        if expr.startswith("chip."):
            if chip_features_df is not None and name in chip_features_df.columns:
                values[name] = chip_features_df[name]
                env[name] = values[name]
            else:
                values[name] = pd.Series(np.nan, index=local_valid.index)
                env[name] = values[name]
        else:
            values[name] = eval(expr, {"__builtins__": {}}, env)
            env[name] = values[name]
    
    result_valid = pd.DataFrame(values, index=local_valid.index)
    # 将所有列统一转为 float64：把 None / object 类型的"全空列"（来自缺失的财务字段）
    # 显式转成 NaN，避免后续 result.loc[mask] = result_valid.values 触发
    # pandas 的 dtype incompatible FutureWarning。
    result_valid = result_valid.apply(pd.to_numeric, errors="coerce").astype("float64")
    result = pd.DataFrame(np.nan, index=local.index, columns=result_valid.columns)
    result.loc[valid_mask] = result_valid.values
    result.index.name = "datetime"
    result["instrument"] = group.index.get_level_values("instrument")[0]
    result = result.set_index("instrument", append=True).reorder_levels(["instrument", "datetime"])
    return result


def _feature_cache_key(
    raw_frame: pd.DataFrame,
    config: dict[str, Any],
    feature_cfg: dict[str, Any],
) -> str:
    payload = {
        "config": _config_cache_signature(config),
        "raw_frame": _frame_cache_signature(raw_frame),
        "base_features": feature_cfg.get("base_features", []),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _label_cache_key(raw_frame: pd.DataFrame, config: dict[str, Any]) -> str:
    payload = {
        "config": _config_cache_signature(config),
        "raw_frame": _frame_cache_signature(raw_frame),
        "label_name": label_name(config),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def inspect_feature_frame_cache(
    raw_frame: pd.DataFrame,
    config: dict[str, Any] | None = None,
    feature_cfg: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    cfg = config or env_config()
    fp_cfg = feature_cfg or feature_pool_config()
    bucket = _window_cache_bucket(cfg)
    if bucket is None:
        return False, "无窗口级缓存上下文"
    cache_key = _feature_cache_key(raw_frame, cfg, fp_cfg)
    if cache_key in bucket["feature_frames"]:
        return True, "命中同窗口同阶段缓存"
    if not bucket["feature_frames"]:
        return False, "当前窗口/阶段首次构建"
    return False, "当前 raw_frame 范围或配置签名与已缓存结果不同"


def build_feature_frame(
    raw_frame: pd.DataFrame,
    config: dict[str, Any] | None = None,
    feature_cfg: dict[str, Any] | None = None,
) -> pd.DataFrame:
    cfg = config or env_config()
    fp_cfg = feature_cfg or feature_pool_config()
    bucket = _window_cache_bucket(cfg)
    cache_key = _feature_cache_key(raw_frame, cfg, fp_cfg)
    if bucket is not None:
        cached = bucket["feature_frames"].get(cache_key)
        if cached is not None:
            return cached
    base_features = list(fp_cfg.get("base_features", []))
    frames = []
    for _, group in raw_frame.groupby(level="instrument", sort=False):
        frames.append(_compute_group_features(group, base_features))
    feature_frame = pd.concat(frames).sort_index()
    label = build_label_series(raw_frame, cfg)
    feature_frame[label.name] = label
    feature_frame = clip_to_active_window(feature_frame, cfg)
    if bucket is not None:
        bucket["feature_frames"][cache_key] = feature_frame
    return feature_frame


def build_label_series(
    raw_frame: pd.DataFrame,
    config: dict[str, Any] | None = None,
) -> pd.Series:
    cfg = config or env_config()
    bucket = _window_cache_bucket(cfg)
    cache_key = _label_cache_key(raw_frame, cfg)
    if bucket is not None:
        cached = bucket["label_series"].get(cache_key)
        if cached is not None:
            return cached
    label_cfg = label_config(cfg)
    return_type = str(label_cfg.get("return_type", "period_return")).strip().lower()
    price_field = str(label_cfg.get("price_field", "close")).strip()
    if return_type != "period_return":
        raise ValueError(f"暂不支持的 label.return_type: {return_type}")
    if price_field not in raw_frame.columns:
        raise KeyError(f"构建收益标签失败：原始数据中不存在价格列 {price_field}")

    prices = raw_frame[price_field].sort_index()
    observation_dates = analysis_observation_dates(
        prices.index.get_level_values("datetime"),
        cfg,
    )
    if len(observation_dates) < 2:
        return pd.Series(index=prices.index, dtype=float, name=label_name(cfg))

    observation_index = pd.Index(pd.to_datetime(observation_dates))
    observation_prices = prices.loc[prices.index.get_level_values("datetime").isin(observation_index)]
    period_return = observation_prices.groupby(level="instrument").shift(-1) / observation_prices - 1
    if _dynamic_index_pool_enabled(cfg):
        dynamic_mask = build_dynamic_universe_mask(period_return.index, cfg)
        period_return = period_return.loc[dynamic_mask]
    label = pd.Series(index=prices.index, dtype=float, name=label_name(cfg))
    label.loc[period_return.index] = period_return
    label = label.rename(label_name(cfg))
    if bucket is not None:
        bucket["label_series"][cache_key] = label
    return label


def build_analysis_label_series(
    raw_frame: pd.DataFrame,
    config: dict[str, Any] | None = None,
) -> pd.Series:
    return build_label_series(raw_frame, config)


def build_ohlcv_map(
    raw_frame: pd.DataFrame,
    config: dict[str, Any] | None = None,
) -> dict[str, pd.DataFrame]:
    cfg = config or env_config()
    bucket = _window_cache_bucket(cfg)
    cache_key = _frame_cache_signature(raw_frame)
    if bucket is not None:
        cached = bucket["ohlcv_maps"].get(cache_key)
        if cached is not None:
            return cached

    required = ["open", "high", "low", "close", "volume"]
    missing = [item for item in required if item not in raw_frame.columns]
    if missing:
        raise KeyError(f"原始行情缺少必要列: {missing}")

    result: dict[str, pd.DataFrame] = {}
    for instrument, group in raw_frame.groupby(level="instrument", sort=False):
        frame = group.droplevel("instrument")[required].copy().sort_index()
        frame = frame[~frame.index.duplicated(keep="first")]
        frame[["close", "open", "high", "low"]] = frame[["close", "open", "high", "low"]].ffill()
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
        result[str(instrument)] = frame

    if bucket is not None:
        bucket["ohlcv_maps"][cache_key] = result
    return result


def yearly_stability(series: pd.Series, label: pd.Series) -> float:
    merged = pd.concat([series, label], axis=1, keys=["feature", "label"]).dropna()
    if merged.empty:
        return 0.0
    merged["year"] = merged.index.get_level_values("datetime").year
    yearly_corr = merged.groupby("year")[["feature", "label"]].apply(
        lambda frame: frame["feature"].corr(frame["label"])
    )
    yearly_corr = yearly_corr.dropna()
    if yearly_corr.empty:
        return 0.0
    volatility = float(yearly_corr.std(ddof=0) or 0.0)
    return float(1 / (1 + volatility))


def compute_feature_stats(feature_frame: pd.DataFrame, label_name: str) -> pd.DataFrame:
    cfg = env_config()
    max_missing_ratio = float(cfg.get("max_missing_ratio", 0.2))

    rows = []
    label = feature_frame[label_name]
    for column in feature_frame.columns:
        if column == label_name:
            continue
        series = feature_frame[column]
        rows.append(
            {
                "feature_name": column,
                "missing_ratio": float(series.isna().mean()),
                "std": float(series.std(ddof=0) or 0.0),
                "label_corr": float(series.corr(label) or 0.0),
                "yearly_stability_score": yearly_stability(series, label),
            }
        )
    stats = pd.DataFrame(rows)
    stats["quality_flag"] = np.where(
        (stats["missing_ratio"] <= max_missing_ratio) & (stats["std"] > 0),
        "ok",
        "review",
    )
    return stats.sort_values("label_corr", ascending=False).reset_index(drop=True)


def compute_feature_corr(feature_frame: pd.DataFrame, label_name: str) -> pd.DataFrame:
    columns = [column for column in feature_frame.columns if column != label_name]
    corr = feature_frame[columns].corr()
    corr.index.name = "feature_name"
    return corr


def compute_fundamental_feature_health(feature_frame: pd.DataFrame, label_name: str) -> dict[str, Any]:
    cfg = env_config()
    top_k = int(cfg.get("fundamental_health_top_k", 5))
    available = [name for name in FUNDAMENTAL_FEATURE_NAMES if name in feature_frame.columns]
    label = feature_frame[label_name]
    rows: list[dict[str, Any]] = []
    for name in available:
        merged = pd.concat([feature_frame[name], label], axis=1, keys=["feature", "label"]).dropna()
        if merged.empty:
            rows.append(
                {
                    "feature_name": name,
                    "label_corr": 0.0,
                    "long_short_return": 0.0,
                    "top_quantile_return": 0.0,
                    "bottom_quantile_return": 0.0,
                    "valid_observations": 0,
                }
            )
            continue
        by_date = []
        for _, day_frame in merged.groupby(level="datetime", sort=False):
            if len(day_frame) < 10 or day_frame["feature"].nunique(dropna=True) < 3:
                continue
            bottom = day_frame["feature"].quantile(0.2)
            top = day_frame["feature"].quantile(0.8)
            top_return = day_frame.loc[day_frame["feature"] >= top, "label"].mean()
            bottom_return = day_frame.loc[day_frame["feature"] <= bottom, "label"].mean()
            if pd.notna(top_return) and pd.notna(bottom_return):
                by_date.append((float(top_return), float(bottom_return)))
        if by_date:
            top_mean = float(np.mean([item[0] for item in by_date]))
            bottom_mean = float(np.mean([item[1] for item in by_date]))
        else:
            top_mean = 0.0
            bottom_mean = 0.0
        rows.append(
            {
                "feature_name": name,
                "label_corr": float(merged["feature"].corr(merged["label"]) or 0.0),
                "long_short_return": top_mean - bottom_mean,
                "top_quantile_return": top_mean,
                "bottom_quantile_return": bottom_mean,
                "valid_observations": int(len(merged)),
            }
        )
    rows = sorted(rows, key=lambda item: abs(float(item["long_short_return"])), reverse=True)
    return {
        "description": "基本面分析师专用体检：对基本面/风格相关特征单独计算与标签的相关性，以及按日横截面 top20% - bottom20% 的平均标签收益差，避免基本面特征被全局短期相关性排序淹没。",
        "features": rows,
        "top_long_short_features": [item["feature_name"] for item in rows[:top_k]],
    }


def build_summary_payload(stats: pd.DataFrame, corr: pd.DataFrame, label_name: str) -> dict[str, Any]:
    cfg = env_config()
    top_k = int(cfg.get("summary_top_k", 3))
    unstable_top_k = int(cfg.get("unstable_top_k", 3))
    high_corr_threshold = float(cfg.get("high_corr_threshold", 0.5))

    # 按照相关系数的绝对值从大到小排序
    stats_abs = stats.copy()
    stats_abs["abs_corr"] = stats_abs["label_corr"].abs()
    stats_sorted = stats_abs.sort_values("abs_corr", ascending=False)
    
    # 提取绝对值最大的前 top_k 个特征（不论正负）
    top_features = stats_sorted.head(top_k)["feature_name"].tolist()
    
    # 提取绝对值最小（最没关系）的前 top_k 个特征
    weak_features = stats_sorted.tail(top_k)["feature_name"].tolist()
    
    unstable_features = stats.nsmallest(unstable_top_k, "yearly_stability_score")["feature_name"].tolist()
    poor_quality = stats.loc[stats["quality_flag"] != "ok", "feature_name"].tolist()
    pairs: list[list[Any]] = []
    seen: set[tuple[str, str]] = set()
    for left in corr.index:
        for right in corr.columns:
            if left == right:
                continue
            key = tuple(sorted((str(left), str(right))))
            if key in seen:
                continue
            value = float(corr.loc[left, right])
            if math.isnan(value):
                continue
            if abs(value) >= high_corr_threshold:
                pairs.append([key[0], key[1], round(value, 6)])
                seen.add(key)
    pairs = sorted(pairs, key=lambda item: abs(item[2]), reverse=True)[:10]
    return {
        "target": label_name,
        "top_features": top_features,
        "weak_features": weak_features,
        "high_corr_pairs": pairs,
        "unstable_features": unstable_features,
        "poor_quality_features": poor_quality,
    }


def _balanced_json_candidate(text: str) -> str:
    in_string = False
    escape = False
    curly_balance = 0
    square_balance = 0
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            curly_balance += 1
        elif ch == "}":
            curly_balance = max(curly_balance - 1, 0)
        elif ch == "[":
            square_balance += 1
        elif ch == "]":
            square_balance = max(square_balance - 1, 0)
    return text + ("]" * square_balance) + ("}" * curly_balance)


def _repair_json_by_balancing(text: str, max_inserts: int = 64) -> str:
    candidate = _balanced_json_candidate(text)
    for _ in range(max_inserts):
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError as error:
            if error.pos <= 0 or error.pos >= len(candidate):
                return candidate
            if candidate[error.pos] != "{":
                return candidate
            left = error.pos - 1
            while left >= 0 and candidate[left].isspace():
                left -= 1
            if left >= 0 and candidate[left] == ",":
                # 常见错误：对象结束少一个 '}'，导致 ", {" 被解析为对象内部逗号后直接跟 '{'。
                candidate = candidate[:left] + "}" + candidate[left:]
                continue
            return candidate
    return candidate


def _loads_with_single_repair(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = _repair_json_by_balancing(text)
        if repaired != text:
            return json.loads(repaired)
        raise


def parse_json_text(text: str) -> Any:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return _loads_with_single_repair(stripped)
    object_match = re.search(r"(\{.*\})", stripped, flags=re.DOTALL)
    if object_match:
        return _loads_with_single_repair(object_match.group(1))
    array_match = re.search(r"(\[.*\])", stripped, flags=re.DOTALL)
    if array_match:
        return _loads_with_single_repair(array_match.group(1))
    raise ValueError("无法从大模型输出中提取 JSON")


ALLOWED_AST_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.USub,
    ast.UAdd,
)


WINDOW_OPERATOR_ARG_INDEXES = {
    "rolling_mean": [1],
    "rolling_std": [1],
    "rolling_sum": [1],
    "rolling_max": [1],
    "rolling_min": [1],
    "delay": [1],
    "delta": [1],
    "pct_change": [1],
    "ema": [1],
    "ts_rank": [1],
    "ts_zscore": [1],
    "rolling_corr": [2],
}


def generation_constraints(feature_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """提取 generation_constraints，将 {key: {value: ..., description: ...}} 格式转为 {key: value}。"""
    fp_cfg = feature_cfg or feature_pool_config()
    raw = dict(fp_cfg.get("generation_constraints", {}))
    result: dict[str, Any] = {}
    for key, val in raw.items():
        if isinstance(val, dict) and "value" in val:
            result[key] = val["value"]
        else:
            result[key] = val
    return result


def _number_constant_value(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
        and not isinstance(node.operand.value, bool)
    ):
        value = node.operand.value
        return value if isinstance(node.op, ast.UAdd) else -value
    raise TypeError("node 不是数字常量")


def _is_number_constant(node: ast.AST) -> bool:
    try:
        _number_constant_value(node)
        return True
    except TypeError:
        return False


def _formula_call_depth(node: ast.AST) -> int:
    child_depth = max((_formula_call_depth(child) for child in ast.iter_child_nodes(node)), default=0)
    if isinstance(node, ast.Call):
        return 1 + child_depth
    return child_depth


def _collect_formula_metadata(
    tree: ast.AST,
    allowed_features: set[str],
) -> dict[str, Any]:
    feature_counts: Counter[str] = Counter()
    operator_counts: Counter[str] = Counter()
    windows_used: list[int] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in allowed_features:
            feature_counts[node.id] += 1
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            operator_counts[node.func.id] += 1
            for arg_index in WINDOW_OPERATOR_ARG_INDEXES.get(node.func.id, []):
                if len(node.args) > arg_index and _is_number_constant(node.args[arg_index]):
                    windows_used.append(int(_number_constant_value(node.args[arg_index])))

    return {
        "feature_names": sorted(feature_counts.keys()),
        "feature_counts": dict(feature_counts),
        "operator_counts": dict(operator_counts),
        "windows_used": sorted(set(windows_used)),
        "call_depth": _formula_call_depth(tree),
    }


def formula_feature_names(formula: str, allowed_features: set[str]) -> set[str]:
    tree = ast.parse(formula, mode="eval")
    metadata = _collect_formula_metadata(tree, allowed_features)
    return set(metadata["feature_names"])


def _find_forbidden_operator_chains(
    node: ast.AST,
    forbidden_pairs: set[tuple[str, str]],
    call_stack: list[str] | None = None,
    violations: set[tuple[str, str]] | None = None,
) -> set[tuple[str, str]]:
    active_stack = list(call_stack or [])
    found = violations if violations is not None else set()
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        current = node.func.id
        for ancestor in active_stack:
            pair = (ancestor, current)
            if pair in forbidden_pairs:
                found.add(pair)
        active_stack.append(current)
    for child in ast.iter_child_nodes(node):
        _find_forbidden_operator_chains(child, forbidden_pairs, active_stack, found)
    return found


OPERATOR_ARITY: dict[str, int] = {
    "abs": 1,
    "log": 1,
    "sqrt": 1,
    "sign": 1,
    "rank": 1,
    "zscore": 1,
    "minmax": 1,
    "rolling_mean": 2,
    "rolling_std": 2,
    "rolling_sum": 2,
    "rolling_max": 2,
    "rolling_min": 2,
    "delay": 2,
    "delta": 2,
    "pct_change": 2,
    "ema": 2,
    "ts_rank": 2,
    "ts_zscore": 2,
    "rolling_corr": 3,
    "clip": 3,
    "winsorize": 3,
}


SERIES_FIRST_OPERATORS: set[str] = {
    "abs",
    "log",
    "sqrt",
    "sign",
    "rank",
    "zscore",
    "minmax",
    "rolling_mean",
    "rolling_std",
    "rolling_sum",
    "rolling_max",
    "rolling_min",
    "delay",
    "delta",
    "pct_change",
    "ema",
    "ts_rank",
    "ts_zscore",
    "rolling_corr",
    "clip",
    "winsorize",
}


def validate_formula(
    formula: str,
    allowed_features: set[str],
    allowed_operators: set[str],
    constraints: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        return False, f"语法错误: {exc}"

    active_constraints = constraints or {}
    allowed_windows = {
        int(value)
        for value in active_constraints.get("allowed_windows", [])
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }

    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_AST_NODES):
            return False, f"不支持的语法节点: {type(node).__name__}"
        if isinstance(node, ast.Constant) and not _is_number_constant(node):
            return False, "公式中只允许使用数字常量"
        if isinstance(node, ast.Name):
            name = node.id
            if name not in allowed_features and name not in allowed_operators:
                return False, f"使用了未授权名称: {name}"
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                return False, "只允许调用白名单函数"
            if node.func.id not in allowed_operators:
                return False, f"使用了未授权算子: {node.func.id}"
            if node.keywords:
                return False, f"{node.func.id} 不允许使用关键字参数"
            expected_arity = OPERATOR_ARITY.get(node.func.id)
            if expected_arity is not None and len(node.args) != expected_arity:
                return False, f"{node.func.id} 需要 {expected_arity} 个参数，当前传入 {len(node.args)} 个"
            if node.func.id in SERIES_FIRST_OPERATORS:
                if not node.args:
                    return False, f"{node.func.id} 缺少序列参数"
                first_arg = node.args[0]
                if _is_number_constant(first_arg):
                    return False, f"{node.func.id} 的第 1 个参数必须是特征/表达式序列，不能是数字常量"
            for arg_index in WINDOW_OPERATOR_ARG_INDEXES.get(node.func.id, []):
                if len(node.args) <= arg_index:
                    return False, f"{node.func.id} 缺少窗口参数"
                window_node = node.args[arg_index]
                if not _is_number_constant(window_node):
                    return False, f"{node.func.id} 的窗口参数必须是整数常量"
                window_value_raw = _number_constant_value(window_node)
                if not float(window_value_raw).is_integer():
                    return False, f"{node.func.id} 的窗口参数必须是整数常量"
                window_value = int(window_value_raw)
                if window_value <= 0:
                    return False, f"{node.func.id} 的窗口参数必须大于 0"
                if allowed_windows and window_value not in allowed_windows:
                    return False, f"{node.func.id} 的窗口 {window_value} 不在允许集合 {sorted(allowed_windows)}"
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Add) and "add" not in allowed_operators:
                return False, "当前不允许加法"
            if isinstance(node.op, ast.Sub) and "sub" not in allowed_operators:
                return False, "当前不允许减法"
            if isinstance(node.op, ast.Mult) and "mul" not in allowed_operators:
                return False, "当前不允许乘法"
            if isinstance(node.op, ast.Div) and "div" not in allowed_operators:
                return False, "当前不允许除法"

    metadata = _collect_formula_metadata(tree, allowed_features)
    if not metadata["feature_names"]:
        return False, "公式至少需要使用 1 个基础特征"

    max_depth = int(active_constraints.get("max_call_depth", 0) or 0)
    if max_depth > 0 and metadata["call_depth"] > max_depth:
        return False, f"公式算子嵌套层数为 {metadata['call_depth']}，超过限制 {max_depth}"

    max_feature_count = int(active_constraints.get("max_feature_count", 0) or 0)
    if max_feature_count > 0 and len(metadata["feature_names"]) > max_feature_count:
        return False, f"公式使用特征数为 {len(metadata['feature_names'])}，超过限制 {max_feature_count}"

    max_operator_count = int(active_constraints.get("max_operator_count", 0) or 0)
    total_operator_count = sum(int(value) for value in metadata["operator_counts"].values())
    if max_operator_count > 0 and total_operator_count > max_operator_count:
        return False, f"公式算子调用次数为 {total_operator_count}，超过限制 {max_operator_count}"

    max_same_feature_reuse = int(active_constraints.get("max_same_feature_reuse", 0) or 0)
    if max_same_feature_reuse > 0:
        for feature_name, use_count in metadata["feature_counts"].items():
            if use_count > max_same_feature_reuse:
                return False, f"特征 {feature_name} 在同一公式中重复使用 {use_count} 次，超过限制 {max_same_feature_reuse}"

    max_same_operator_reuse = int(active_constraints.get("max_same_operator_reuse", 0) or 0)
    if max_same_operator_reuse > 0:
        for operator_name, use_count in metadata["operator_counts"].items():
            if use_count > max_same_operator_reuse:
                return False, f"算子 {operator_name} 在同一公式中重复使用 {use_count} 次，超过限制 {max_same_operator_reuse}"

    forbidden_pairs = {
        (str(item[0]), str(item[1]))
        for item in active_constraints.get("forbidden_operator_chains", [])
        if isinstance(item, list) and len(item) == 2
    }
    if forbidden_pairs:
        violations = sorted(_find_forbidden_operator_chains(tree, forbidden_pairs))
        if violations:
            pair_text = "、".join(f"{left}->{right}" for left, right in violations)
            return False, f"公式包含被禁止的算子嵌套链: {pair_text}"

    return True, "ok"


def rolling_mean(series: pd.Series, window: int) -> pd.Series:
    win = int(window)
    return series.groupby(level="instrument").transform(
        lambda s: s.rolling(win, min_periods=win).mean()
    )


def rolling_std(series: pd.Series, window: int) -> pd.Series:
    win = int(window)
    return series.groupby(level="instrument").transform(
        lambda s: s.rolling(win, min_periods=win).std(ddof=0)
    )


def rolling_sum(series: pd.Series, window: int) -> pd.Series:
    win = int(window)
    return series.groupby(level="instrument").transform(
        lambda s: s.rolling(win, min_periods=win).sum()
    )


def delay(series: pd.Series, periods: int) -> pd.Series:
    lag = int(periods)
    return series.groupby(level="instrument").shift(lag)


def delta(series: pd.Series, periods: int) -> pd.Series:
    lag = int(periods)
    return series - delay(series, lag)


def pct_change(series: pd.Series, periods: int) -> pd.Series:
    lag = int(periods)
    return series.groupby(level="instrument").transform(
        lambda s: s.pct_change(periods=lag, fill_method=None)
    )


def ema(series: pd.Series, span: int) -> pd.Series:
    win = int(span)
    return series.groupby(level="instrument").transform(
        lambda s: s.ewm(span=win, adjust=False, min_periods=win).mean()
    )


def ts_rank(series: pd.Series, window: int) -> pd.Series:
    win = int(window)
    return series.groupby(level="instrument").transform(
        lambda s: s.rolling(win, min_periods=win).apply(
            lambda values: pd.Series(values).rank(pct=True).iloc[-1],
            raw=False,
        )
    )


def ts_zscore(series: pd.Series, window: int) -> pd.Series:
    win = int(window)
    grouped = series.groupby(level="instrument")
    mean = grouped.transform(lambda s: s.rolling(win, min_periods=win).mean())
    std = grouped.transform(lambda s: s.rolling(win, min_periods=win).std(ddof=0))
    return ((series - mean) / std.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)


def rolling_corr(left: pd.Series, right: pd.Series, window: int) -> pd.Series:
    win = int(window)

    def _calc(frame: pd.DataFrame) -> pd.Series:
        return frame["left"].rolling(win, min_periods=win).corr(frame["right"])

    frame = pd.concat([left.rename("left"), right.rename("right")], axis=1)
    return frame.groupby(level="instrument", group_keys=False).apply(_calc)


def rank(series: pd.Series) -> pd.Series:
    return series.groupby(level="datetime").rank(pct=True, method="average")


def zscore(series: pd.Series) -> pd.Series:
    def _normalize(group: pd.Series) -> pd.Series:
        std = group.std(ddof=0)
        if pd.isna(std) or std == 0:
            return pd.Series(0.0, index=group.index)
        return (group - group.mean()) / std

    return series.groupby(level="datetime").transform(_normalize)


def minmax(series: pd.Series) -> pd.Series:
    def _normalize(group: pd.Series) -> pd.Series:
        minimum = group.min()
        maximum = group.max()
        if pd.isna(minimum) or pd.isna(maximum) or minimum == maximum:
            return pd.Series(0.0, index=group.index)
        return (group - minimum) / (maximum - minimum)

    return series.groupby(level="datetime").transform(_normalize)


def winsorize(series: pd.Series, lower_quantile: float, upper_quantile: float) -> pd.Series:
    lower = float(lower_quantile)
    upper = float(upper_quantile)

    def _clip(group: pd.Series) -> pd.Series:
        lower_bound = group.quantile(lower)
        upper_bound = group.quantile(upper)
        return group.clip(lower=lower_bound, upper=upper_bound)

    return series.groupby(level="datetime").transform(_clip)


def mad_clip(series: pd.Series, n: float) -> pd.Series:
    threshold = float(n)

    def _clip(group: pd.Series) -> pd.Series:
        median = group.median()
        mad = (group - median).abs().median()
        scaled_mad = 1.4826 * mad
        if pd.isna(scaled_mad) or scaled_mad == 0:
            return group
        lower_bound = median - threshold * scaled_mad
        upper_bound = median + threshold * scaled_mad
        return group.clip(lower=lower_bound, upper=upper_bound)

    return series.groupby(level="datetime").transform(_clip)


def sigma_clip(series: pd.Series, n: float) -> pd.Series:
    threshold = float(n)

    def _clip(group: pd.Series) -> pd.Series:
        std = group.std(ddof=0)
        if pd.isna(std) or std == 0:
            return group
        mean = group.mean()
        lower_bound = mean - threshold * std
        upper_bound = mean + threshold * std
        return group.clip(lower=lower_bound, upper=upper_bound)

    return series.groupby(level="datetime").transform(_clip)


def _cross_section_regression_residual(
    score: pd.Series,
    design: pd.DataFrame,
) -> pd.Series:
    frame = pd.concat([score.rename("score"), design], axis=1)
    pieces: list[pd.Series] = []

    def _regress(group: pd.DataFrame) -> pd.Series:
        design_columns = [column for column in group.columns if column != "score"]
        valid = group[["score", *design_columns]].notna().all(axis=1)
        if valid.sum() <= len(design_columns):
            return group["score"]
        y = group.loc[valid, "score"].to_numpy(dtype=float)
        x = group.loc[valid, design_columns].to_numpy(dtype=float)
        if x.ndim != 2 or x.shape[0] == 0:
            return group["score"]
        beta, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
        fitted = x @ beta
        residual = pd.Series(np.nan, index=group.index, dtype=float)
        residual.loc[valid] = y - fitted
        return residual

    for _, group in frame.groupby(level="datetime", sort=False):
        pieces.append(_regress(group))
    if not pieces:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.concat(pieces).sort_index()


def neutralize_by_industry(
    series: pd.Series,
    industry_series: pd.Series,
) -> pd.Series:
    aligned = pd.concat(
        [series.rename("score"), industry_series.rename("industry")],
        axis=1,
    )

    datetime_index = aligned.index.get_level_values("datetime")
    group_mean = aligned.groupby([datetime_index, aligned["industry"]])["score"].transform("mean")
    result = aligned["score"].copy()
    valid = aligned["industry"].notna()
    result.loc[valid] = aligned.loc[valid, "score"] - group_mean.loc[valid]
    return result


def neutralize_by_market_cap(
    series: pd.Series,
    market_cap_series: pd.Series,
) -> pd.Series:
    log_market_cap = np.log(market_cap_series.where(market_cap_series > 0))
    design = pd.DataFrame(
        {
            "const": 1.0,
            "log_market_cap": log_market_cap,
        },
        index=series.index,
    )
    return _cross_section_regression_residual(series, design)


def neutralize_by_industry_and_market_cap(
    series: pd.Series,
    industry_series: pd.Series,
    market_cap_series: pd.Series,
) -> pd.Series:
    log_market_cap = np.log(market_cap_series.where(market_cap_series > 0))
    industry_dummies = pd.get_dummies(industry_series, prefix="industry", dummy_na=False, drop_first=True)
    design = pd.concat(
        [
            pd.Series(1.0, index=series.index, name="const"),
            log_market_cap.rename("log_market_cap"),
            industry_dummies.astype(float),
        ],
        axis=1,
    )
    return _cross_section_regression_residual(series, design)


def apply_factor_preprocess(
    factor_series: pd.Series,
    raw_frame: pd.DataFrame,
    config: dict[str, Any] | None = None,
) -> pd.Series:
    cfg = preprocess_config(config)
    score = factor_series.astype(float).copy()
    outlier_method = str(cfg.get("outlier_method", "none"))
    outlier_options = dict(cfg.get("outlier_options", {}))
    if outlier_method == "mad":
        score = mad_clip(score, float(outlier_options.get("n", 3.0)))
    elif outlier_method == "quantile":
        score = winsorize(
            score,
            float(outlier_options.get("lower_quantile", 0.01)),
            float(outlier_options.get("upper_quantile", 0.99)),
        )
    elif outlier_method == "sigma":
        score = sigma_clip(score, float(outlier_options.get("n", 3.0)))

    neutralization = str(cfg.get("neutralization", "none"))
    neutralization_options = dict(cfg.get("neutralization_options", {}))
    industry_field = str(neutralization_options.get("industry_field", "industry") or "industry").strip()
    market_cap_field = str(neutralization_options.get("market_cap_field", "market_cap") or "market_cap").strip()
    if neutralization == "industry":
        if industry_field not in raw_frame.columns:
            raise KeyError(f"行业中性化失败：原始数据中不存在字段 {industry_field}")
        score = neutralize_by_industry(score, raw_frame[industry_field])
    elif neutralization == "market_cap":
        if market_cap_field not in raw_frame.columns:
            raise KeyError(f"市值中性化失败：原始数据中不存在字段 {market_cap_field}")
        score = neutralize_by_market_cap(score, raw_frame[market_cap_field])
    elif neutralization == "industry_market_cap":
        if industry_field not in raw_frame.columns:
            raise KeyError(f"行业市值中性化失败：原始数据中不存在字段 {industry_field}")
        if market_cap_field not in raw_frame.columns:
            raise KeyError(f"行业市值中性化失败：原始数据中不存在字段 {market_cap_field}")
        score = neutralize_by_industry_and_market_cap(score, raw_frame[industry_field], raw_frame[market_cap_field])
    score = score.groupby(level="instrument").ffill()
    return score.rename(factor_series.name)


OPERATOR_ENV = {
    "add": lambda left, right: left + right,
    "sub": lambda left, right: left - right,
    "mul": lambda left, right: left * right,
    "div": lambda left, right: left / right.replace(0, np.nan) if isinstance(right, pd.Series) else left / right,
    "abs": lambda x: np.abs(x),
    "log": lambda x: np.log(x.replace(0, np.nan)),
    "sqrt": lambda x: np.sqrt(np.maximum(x, 0)),
    "sign": lambda x: np.sign(x),
    "rolling_mean": rolling_mean,
    "rolling_std": rolling_std,
    "rolling_sum": rolling_sum,
    "rolling_max": lambda series, window: series.groupby(level="instrument").rolling(window=window, min_periods=1).max().reset_index(level=0, drop=True),
    "rolling_min": lambda series, window: series.groupby(level="instrument").rolling(window=window, min_periods=1).min().reset_index(level=0, drop=True),
    "delay": delay,
    "delta": delta,
    "pct_change": pct_change,
    "ema": ema,
    "ts_rank": ts_rank,
    "ts_zscore": ts_zscore,
    "rolling_corr": rolling_corr,
    "rank": rank,
    "zscore": zscore,
    "minmax": minmax,
    "clip": lambda series, lower, upper: series.clip(lower=lower, upper=upper),
    "winsorize": winsorize,
}


def evaluate_formula(formula: str, data_frame: pd.DataFrame) -> pd.Series:
    formula = _normalize_pct_change_expr(formula)
    env = {column: data_frame[column] for column in data_frame.columns}
    env.update(OPERATOR_ENV)
    result = eval(formula, {"__builtins__": {}}, env)
    if not isinstance(result, pd.Series):
        raise TypeError("公式执行结果不是 pandas Series")
    return result


def _normalize_pct_change_expr(expr: str) -> str:
    """为表达式中的 Series.pct_change 显式补充 fill_method=None，避免 pandas FutureWarning。"""
    pattern = re.compile(r"\.pct_change\(([^)]*)\)")

    def _replace(match: re.Match[str]) -> str:
        args = match.group(1).strip()
        if "fill_method" in args:
            return match.group(0)
        if not args:
            return ".pct_change(fill_method=None)"
        return f".pct_change({args}, fill_method=None)"

    return pattern.sub(_replace, expr)


def _cross_section_corr(
    score: pd.Series,
    label: pd.Series,
    method: str = "pearson",
) -> float:
    import numpy as np
    frame = pd.concat([score.rename("score"), label.rename("label")], axis=1)
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    if len(frame) < 2:
        return float("nan")
    x = frame["score"].astype(float)
    y = frame["label"].astype(float)
    if method == "spearman":
        x = x.rank(method="average")
        y = y.rank(method="average")
    if x.nunique(dropna=True) < 2 or y.nunique(dropna=True) < 2:
        return float("nan")
    
    import warnings
    with np.errstate(invalid='ignore'):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return float(x.corr(y))


def analysis_observation_dates(
    dates: pd.Index,
    analysis_config: dict[str, Any] | None = None,
) -> list[pd.Timestamp]:
    return weekly_rebalance_dates(dates, analysis_config)


def factor_metrics_from_series(
    factor_name: str,
    factor_series: pd.Series,
    label_series: pd.Series,
    analysis_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = analysis_config or analysis_rule_config()
    merged = pd.concat([factor_series.rename("score"), label_series.rename("label")], axis=1)
    # Keep metric calculation strictly inside active run window.
    # This avoids warmup/forward-buffer labels diluting coverage statistics.
    merged = clip_to_active_window(merged, cfg)
    if merged.empty:
        return {
            "factor_name": factor_name,
            "mean_ic": 0.0,
            "mean_rank_ic": 0.0,
            "ic_ir": 0.0,
            "rank_ic_ir": 0.0,
            "positive_ic_ratio": 0.0,
            "coverage": 0.0,
            "observation_count": 0,
        }
    observation_dates = analysis_observation_dates(
        merged.index.get_level_values("datetime"),
        analysis_config,
    )
    observation_mask = merged.index.get_level_values("datetime").isin(observation_dates)
    observation_frame = merged.loc[observation_mask]
    if _dynamic_index_pool_enabled(cfg):
        dynamic_mask = build_dynamic_universe_mask(observation_frame.index, cfg)
        observation_frame = observation_frame.loc[dynamic_mask]
    if observation_frame.empty:
        return {
            "factor_name": factor_name,
            "mean_ic": 0.0,
            "mean_rank_ic": 0.0,
            "ic_ir": 0.0,
            "rank_ic_ir": 0.0,
            "positive_ic_ratio": 0.0,
            "coverage": 0.0,
            "observation_count": 0,
        }
    min_valid_ratio = float(cfg.get("min_valid_ratio_per_observation", 0.0) or 0.0)
    if min_valid_ratio > 0:
        valid_stats = (
            observation_frame.assign(
                _valid_pair=observation_frame[["score", "label"]].notna().all(axis=1),
                _label_available=observation_frame["label"].notna(),
            )
            .groupby(level="datetime")
            .agg(
                n_valid=("_valid_pair", "sum"),
                label_available=("_label_available", "sum"),
            )
        )
        keep_dates = valid_stats.loc[
            valid_stats["n_valid"] >= valid_stats["label_available"] * min_valid_ratio
        ].index
        observation_frame = observation_frame.loc[
            observation_frame.index.get_level_values("datetime").isin(keep_dates)
        ]
        if observation_frame.empty:
            return {
                "factor_name": factor_name,
                "mean_ic": 0.0,
                "mean_rank_ic": 0.0,
                "ic_ir": 0.0,
                "rank_ic_ir": 0.0,
                "positive_ic_ratio": 0.0,
                "coverage": 0.0,
                "observation_count": 0,
            }

    label_available_count = int(observation_frame["label"].notna().sum())
    valid_pair_count = int(observation_frame[["score", "label"]].notna().all(axis=1).sum())
    ic_series = observation_frame.groupby(level="datetime").apply(
        lambda frame: _cross_section_corr(frame["score"], frame["label"], method="pearson")
    ).dropna()
    rank_ic_series = observation_frame.groupby(level="datetime").apply(
        lambda frame: _cross_section_corr(frame["score"], frame["label"], method="spearman")
    ).dropna()
    mean_ic = float(ic_series.mean() or 0.0) if not ic_series.empty else 0.0
    mean_rank_ic = float(rank_ic_series.mean() or 0.0) if not rank_ic_series.empty else 0.0
    ic_std = float(ic_series.std(ddof=0) or 0.0) if not ic_series.empty else 0.0
    rank_ic_std = float(rank_ic_series.std(ddof=0) or 0.0) if not rank_ic_series.empty else 0.0
    return {
        "factor_name": factor_name,
        "mean_ic": mean_ic,
        "mean_rank_ic": mean_rank_ic,
        "ic_ir": float(mean_ic / ic_std) if ic_std else 0.0,
        "rank_ic_ir": float(mean_rank_ic / rank_ic_std) if rank_ic_std else 0.0,
        "positive_ic_ratio": float((rank_ic_series > 0).mean()) if not rank_ic_series.empty else 0.0,
        "coverage": float(valid_pair_count / label_available_count) if label_available_count else 0.0,
        "observation_count": int(rank_ic_series.count()),
    }


def weekly_rebalance_dates(
    dates: pd.Index,
    analysis_config: dict[str, Any] | None = None,
) -> list[pd.Timestamp]:
    cfg = analysis_config or analysis_rule_config()
    rebalance = str(cfg.get("rebalance", "weekly")).strip().lower()
    rebalance_interval = max(int(cfg.get("rebalance_interval", 1) or 1), 1)
    default_anchor = "first_trading_day_of_month" if rebalance == "monthly" else "first_trading_day_of_week"
    rebalance_anchor = str(cfg.get("rebalance_anchor", default_anchor)).strip().lower()
    unique_dates = pd.Index(pd.to_datetime(sorted(pd.unique(dates))))

    if rebalance == "daily":
        selected = [pd.Timestamp(item) for item in unique_dates.tolist()]
        return selected[::rebalance_interval]

    if rebalance == "weekly":
        allowed_anchors = {"first_trading_day_of_week", "last_trading_day_of_week"}
        week_starts = (unique_dates - pd.to_timedelta(unique_dates.weekday, unit="D")).normalize()
        
        if rebalance_anchor == "first_trading_day_of_week":
            if len(unique_dates) > 0 and unique_dates[0].weekday() != 0:
                first_week_start = week_starts[0]
                unique_dates = unique_dates[week_starts != first_week_start]
                if len(unique_dates) == 0:
                    return []
                week_starts = (unique_dates - pd.to_timedelta(unique_dates.weekday, unit="D")).normalize()
        elif rebalance_anchor == "last_trading_day_of_week":
            if len(unique_dates) > 0 and unique_dates[-1].weekday() != 4:
                last_week_start = week_starts[-1]
                unique_dates = unique_dates[week_starts != last_week_start]
                if len(unique_dates) == 0:
                    return []
                week_starts = (unique_dates - pd.to_timedelta(unique_dates.weekday, unit="D")).normalize()
        
        grouped = unique_dates.to_series().groupby(week_starts)
    elif rebalance == "monthly":
        allowed_anchors = {"first_trading_day_of_month", "last_trading_day_of_month"}
        
        if rebalance_anchor == "first_trading_day_of_month":
            if len(unique_dates) > 0:
                first_month = unique_dates[0].to_period("M")
                first_month_start = first_month.start_time
                if unique_dates[0] > first_month_start:
                    unique_dates = unique_dates[unique_dates.to_period("M") != first_month]
                    if len(unique_dates) == 0:
                        return []
        elif rebalance_anchor == "last_trading_day_of_month":
            if len(unique_dates) > 0:
                last_month = unique_dates[-1].to_period("M")
                last_month_end = last_month.end_time
                if unique_dates[-1] < last_month_end:
                    unique_dates = unique_dates[unique_dates.to_period("M") != last_month]
                    if len(unique_dates) == 0:
                        return []
        
        grouped = unique_dates.to_series().groupby(unique_dates.to_period("M"))
    else:
        raise ValueError(f"不支持的 rebalance 配置: {rebalance}")
    if rebalance_anchor not in allowed_anchors:
        allowed_text = ", ".join(sorted(allowed_anchors))
        raise ValueError(f"{rebalance} 模式下 rebalance_anchor 必须是: {allowed_text}")

    use_first = rebalance_anchor.startswith("first")
    selected = grouped.first().tolist() if use_first else grouped.last().tolist()
    rebalance_dates = [pd.Timestamp(item) for item in selected]
    return rebalance_dates[::rebalance_interval]


def compute_drawdown(nav_series: pd.Series) -> float:
    peak = nav_series.cummax()
    drawdown = nav_series / peak - 1
    return float(drawdown.min() or 0.0)


def annualized_return(nav_series: pd.Series) -> float:
    if len(nav_series) < 2:
        return 0.0
    days = (nav_series.index[-1] - nav_series.index[0]).days
    if days < 1:
        return 0.0
    total = float(nav_series.iloc[-1] / nav_series.iloc[0])
    return float(total ** (365.25 / days) - 1)


def sharpe_ratio(period_returns: pd.Series) -> float:
    std = float(period_returns.std(ddof=0) or 0.0)
    if std == 0:
        return 0.0
    return float(period_returns.mean() / std * np.sqrt(52))


def archive_outputs_bundle(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for relative in OUTPUT_ARTIFACTS:
        source = OUTPUT_DIR / relative
        if source.exists():
            target = target_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == ".parquet":
                pd.read_parquet(source).to_parquet(target, index=True)
            elif source.suffix == ".csv":
                pd.read_csv(source).to_csv(target, index=False)
            else:
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def archive_iteration_outputs(iteration: int, scope: str | None = None) -> None:
    relative_dir = Path(scope) / f"iter_{iteration:02d}" if scope else Path(f"iter_{iteration:02d}")
    archive_outputs_bundle(OUTPUT_DIR / relative_dir)
