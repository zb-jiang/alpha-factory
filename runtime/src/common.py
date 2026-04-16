from __future__ import annotations

import ast
from collections import Counter
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = RUNTIME_ROOT / "config"
DATA_DIR = RUNTIME_ROOT / "data"
OUTPUT_DIR = RUNTIME_ROOT / "outputs"
SRC_DIR = RUNTIME_ROOT / "src"
RUNTIME_CONTEXT_PATH = OUTPUT_DIR / "_runtime" / "active_context.json"
OUTPUT_ARTIFACTS = [
    Path("health") / "sample_preview.csv",
    Path("health") / "feature_stats.csv",
    Path("health") / "feature_corr.csv",
    Path("health") / "llm_summary.json",
    Path("llm") / "raw_response.json",
    Path("llm") / "factors_validated.json",
    Path("llm") / "factors_rejected.json",
    Path("backtest") / "factor_values.parquet",
    Path("backtest") / "factor_metrics.csv",
    Path("backtest") / "strategy_metrics.csv",
    Path("backtest") / "final_score.csv",
    Path("backtest") / "top3_factors.json",
    Path("backtest") / "skipped_factors.json",
    Path("backtest") / "iteration_context.json",
]

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
        "llm_model",
        "llm_base_url",
        "llm_api_key",
        "iteration_count",
        "llm_candidate_count",
        "summary_top_k",
        "unstable_top_k",
        "high_corr_threshold",
        "max_missing_ratio",
        "min_rank_ic_to_backtest",
        "min_rank_ic_ir_to_backtest",
        "min_positive_ic_ratio",
        "enable_direction_filter",
    },
    "analysis_rule": {
        "run_mode",
        "train_start_date",
        "train_end_date",
        "test_start_date",
        "test_end_date",
        "training_workflow",
        "stock_pool",
        "rebalance",
        "rebalance_interval",
        "rebalance_anchor",
        "label",
        "preprocess",
    },
    "backtest_rule": {
        "buy_top_n",
        "sell_drop_to",
        "holding_count",
        "weight_mode",
        "trade_price",
        "buy_cost",
        "sell_cost",
        "slippage",
        "suspend_action",
        "limit_up_action",
        "limit_down_action",
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


def env_config() -> dict[str, Any]:
    env_cfg, analysis_cfg, _ = _load_layered_configs()
    config = _deep_merge(env_cfg, analysis_cfg)
    context = runtime_context()
    if context:
        config = _deep_merge(config, context)
    return _normalize_preprocess_block(_normalize_label_block(config))


def analysis_rule_config() -> dict[str, Any]:
    _, analysis_cfg, _ = _load_layered_configs()
    context = runtime_context()
    if context:
        analysis_keys = set(analysis_cfg.keys())
        analysis_context = {key: value for key, value in context.items() if key in analysis_keys}
        if analysis_context:
            analysis_cfg = _deep_merge(analysis_cfg, analysis_context)
    return _normalize_preprocess_block(_normalize_label_block(analysis_cfg))


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
    return load_yaml_file(CONFIG_DIR / "feature_pool.yaml")


def backtest_rule_config() -> dict[str, Any]:
    _, _, backtest_cfg = _load_layered_configs()
    return backtest_cfg


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
                direct_windows += int(node.args[0].value)
        elif method_name in {"rolling", "shift"}:
            if node.args and _is_number_constant(node.args[0]):
                direct_windows += int(node.args[0].value)
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
                total_windows += int(node.args[arg_index].value)
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
        if validation_end > train_end:
            raise ValueError("static_split.validation_end_date 不能晚于 train_end_date")
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
            validation_start = discovery_end + pd.Timedelta(days=1)
            validation_end = validation_start + pd.DateOffset(months=validation_months) - pd.Timedelta(days=1)
            if validation_end > train_end:
                break
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
            cursor = cursor + pd.DateOffset(months=step_months)
            index += 1
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


def raw_field_tokens(raw_fields: list[str]) -> list[str]:
    return [field if field.startswith("$") else f"${field}" for field in raw_fields]


def load_raw_data(
    config: dict[str, Any] | None = None,
    raw_fields: list[str] | None = None,
    warmup_trading_days: int = 0,
    forward_trading_days: int = 0,
) -> pd.DataFrame:
    cfg = config or env_config()
    fp_cfg = feature_pool_config()
    fields = raw_fields or list(fp_cfg.get("raw_fields", []))
    
    start_timestamp, end_timestamp = active_run_window(cfg)
    if warmup_trading_days > 0:
        # 预热期只用于计算时序特征，不改变最终统计区间。
        start_timestamp = (start_timestamp - pd.offsets.BDay(int(warmup_trading_days) + 5)).normalize()
    if forward_trading_days > 0:
        # 向后缓冲仅用于构建“下一期收益标签”，最终评估窗口仍由 clip_to_active_window 控制。
        end_timestamp = (end_timestamp + pd.offsets.BDay(int(forward_trading_days) + 5)).normalize()
    start_time = _date_text(start_timestamp)
    end_time = _date_text(end_timestamp)
    
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


def _compute_group_features(group: pd.DataFrame, base_features: list[dict[str, str]]) -> pd.DataFrame:
    local = group.droplevel("instrument").copy()
    env = {column: local[column] for column in local.columns}
    values: dict[str, pd.Series] = {}
    for item in base_features:
        name = str(item["name"])
        expr = _normalize_pct_change_expr(str(item["expr"]))
        values[name] = eval(expr, {"__builtins__": {}}, env)
        env[name] = values[name]
    result = pd.DataFrame(values, index=local.index)
    result.index.name = "datetime"
    result["instrument"] = group.index.get_level_values("instrument")[0]
    result = result.set_index("instrument", append=True).reorder_levels(["instrument", "datetime"])
    return result


def build_feature_frame(
    raw_frame: pd.DataFrame,
    config: dict[str, Any] | None = None,
    feature_cfg: dict[str, Any] | None = None,
) -> pd.DataFrame:
    cfg = config or env_config()
    fp_cfg = feature_cfg or feature_pool_config()
    base_features = list(fp_cfg.get("base_features", []))
    frames = []
    for _, group in raw_frame.groupby(level="instrument", sort=False):
        frames.append(_compute_group_features(group, base_features))
    feature_frame = pd.concat(frames).sort_index()
    label = build_label_series(raw_frame, cfg)
    feature_frame[label.name] = label
    return clip_to_active_window(feature_frame, cfg)


def build_label_series(
    raw_frame: pd.DataFrame,
    config: dict[str, Any] | None = None,
) -> pd.Series:
    cfg = config or env_config()
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
    label = pd.Series(index=prices.index, dtype=float, name=label_name(cfg))
    label.loc[period_return.index] = period_return
    return label.rename(label_name(cfg))


def build_analysis_label_series(
    raw_frame: pd.DataFrame,
    config: dict[str, Any] | None = None,
) -> pd.Series:
    return build_label_series(raw_frame, config)


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


def parse_json_text(text: str) -> Any:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    object_match = re.search(r"(\{.*\})", stripped, flags=re.DOTALL)
    if object_match:
        return json.loads(object_match.group(1))
    array_match = re.search(r"(\[.*\])", stripped, flags=re.DOTALL)
    if array_match:
        return json.loads(array_match.group(1))
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
    fp_cfg = feature_cfg or feature_pool_config()
    return dict(fp_cfg.get("generation_constraints", {}))


def _is_number_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)


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
                    windows_used.append(int(node.args[arg_index].value))

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
            for arg_index in WINDOW_OPERATOR_ARG_INDEXES.get(node.func.id, []):
                if len(node.args) <= arg_index:
                    return False, f"{node.func.id} 缺少窗口参数"
                window_node = node.args[arg_index]
                if not _is_number_constant(window_node) or not float(window_node.value).is_integer():
                    return False, f"{node.func.id} 的窗口参数必须是整数常量"
                window_value = int(window_node.value)
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
    frame = pd.concat([score.rename("score"), label.rename("label")], axis=1).dropna()
    if len(frame) < 2:
        return float("nan")
    x = frame["score"].astype(float)
    y = frame["label"].astype(float)
    if method == "spearman":
        x = x.rank(method="average")
        y = y.rank(method="average")
    if x.nunique(dropna=True) < 2 or y.nunique(dropna=True) < 2:
        return float("nan")
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
    merged = pd.concat([factor_series.rename("score"), label_series.rename("label")], axis=1)
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
        grouped = unique_dates.to_series().groupby(week_starts)
    elif rebalance == "monthly":
        allowed_anchors = {"first_trading_day_of_month", "last_trading_day_of_month"}
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
