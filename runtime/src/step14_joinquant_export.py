from __future__ import annotations

import argparse
import ast
import json
import textwrap
from pathlib import Path
from typing import Any

import yaml

from common import (
    OUTPUT_DIR,
    RUNTIME_ROOT,
    analysis_rule_config,
    backtest_rule_config,
    env_config,
    feature_pool_config,
    formula_feature_names,
    read_json,
)


def _find_factor(factor_name: str) -> dict[str, Any] | None:
    for filename in ["top3_factors.json", "factors_validated.json", "cross_window_top3_factors.json"]:
        path = OUTPUT_DIR / "backtest" / filename
        if not path.exists():
            path2 = OUTPUT_DIR / "llm" / filename
            if path2.exists():
                path = path2
            else:
                continue
        data = read_json(path)
        for item in data.get("factors", data.get("top3", [])):
            if str(item.get("factor_name", "")) == factor_name:
                return dict(item)
    return None


def _load_all_features() -> dict[str, str]:
    feature_cfg = feature_pool_config()
    features: dict[str, str] = {}
    for item in feature_cfg.get("base_features", []):
        features[str(item["name"])] = str(item["expr"])
    config_dir = Path(__file__).resolve().parents[1] / "config"
    for extra_name in ["feature_pool_extended.yaml", "feature_pool_combination.yaml"]:
        extra_path = config_dir / extra_name
        if extra_path.exists():
            with extra_path.open("r", encoding="utf-8") as f:
                extra_cfg = yaml.safe_load(f) or {}
            for item in extra_cfg.get("base_features", []):
                features[str(item["name"])] = str(item["expr"])
    return features


def _resolve_feature_deps(
    feature_name: str,
    all_features: dict[str, str],
    allowed_raw: set[str],
    resolved: dict[str, str] | None = None,
    visiting: set[str] | None = None,
) -> dict[str, str]:
    if resolved is None:
        resolved = {}
    if visiting is None:
        visiting = set()
    if feature_name in resolved:
        return resolved
    if feature_name in allowed_raw:
        return resolved
    if feature_name not in all_features:
        return resolved
    if feature_name in visiting:
        return resolved
    visiting.add(feature_name)
    expr = all_features[feature_name]
    resolved[feature_name] = expr
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in all_features and node.id not in allowed_raw:
            _resolve_feature_deps(node.id, all_features, allowed_raw, resolved, visiting)
    visiting.discard(feature_name)
    return resolved


def _topo_sort_features(features: dict[str, str], allowed_raw: set[str]) -> list[str]:
    in_degree: dict[str, int] = {name: 0 for name in features}
    graph: dict[str, list[str]] = {name: [] for name in features}
    for name, expr in features.items():
        tree = ast.parse(expr, mode="eval")
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in features and node.id != name:
                graph[node.id].append(name)
                in_degree[name] += 1
    queue = [name for name in features if in_degree[name] == 0]
    result: list[str] = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for dep in graph[node]:
            in_degree[dep] -= 1
            if in_degree[dep] == 0:
                queue.append(dep)
    remaining = [name for name in features if name not in set(result)]
    return result + remaining


def _has_chip_features(features: dict[str, str]) -> bool:
    for expr in features.values():
        if expr.startswith("chip."):
            return True
    return False


def _detect_chip_windows(features: dict[str, str]) -> list[int]:
    windows: set[int] = set()
    for name in features:
        if name.startswith("chip_concentration_") or name.startswith("profit_ratio_") or name.startswith("avg_cost_distance_") or name.startswith("peak_distance_"):
            parts = name.rsplit("_", 1)
            if len(parts) == 2:
                try:
                    windows.add(int(parts[1].replace("d", "")))
                except ValueError:
                    pass
    return sorted(windows) if windows else [90, 210]


def _convert_index_code(local_code: str) -> str:
    code = local_code.replace("SH", "").replace("SZ", "").replace(".", "")
    if local_code.startswith("SH"):
        return f"{code}.XSHG"
    if local_code.startswith("SZ"):
        return f"{code}.XSHE"
    return f"{code}.XSHG"


def _convert_stock_code(local_code: str) -> str:
    parts = local_code.split(".")
    if len(parts) != 2:
        return local_code
    code, suffix = parts
    if suffix == "SH":
        return f"{code}.XSHG"
    if suffix == "SZ":
        return f"{code}.XSHE"
    return local_code


def _generate_chip_code(windows: list[int]) -> str:
    return textwrap.dedent(f"""\
def _calculate_chip_distribution(high, low, close, volume, window=210, bins=100):
    import numpy as np
    n = len(high)
    if n < window:
        window = n
    if window < 2:
        return dict(concentration=1.0, profit_ratio=0.5, avg_cost=float(close.iloc[-1]), peak_price=float(close.iloc[-1]))
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
        bin_low = int(np.searchsorted(price_levels, day_low, side='left'))
        bin_high = int(np.searchsorted(price_levels, day_high, side='right'))
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
    concentration = float((p90 - p10) / avg_cost) if avg_cost > 0 else 1.0
    peak_idx = int(np.argmax(chip_ratio))
    peak_price = float(price_centers[peak_idx])
    return dict(concentration=concentration, profit_ratio=profit_ratio, avg_cost=avg_cost, peak_price=peak_price)


def _compute_chip_features_for_stock(hist_df, windows={windows}):
    import numpy as np
    import pandas as pd
    result = {{}}
    for window in windows:
        if len(hist_df) < window:
            result[f'chip_concentration_{{window}}d'] = np.nan
            result[f'profit_ratio_{{window}}d'] = np.nan
            result[f'avg_cost_distance_{{window}}d'] = np.nan
            result[f'peak_distance_{{window}}d'] = np.nan
            continue
        chip = _calculate_chip_distribution(
            hist_df['high'], hist_df['low'], hist_df['close'], hist_df['volume'],
            window=window, bins=100
        )
        current_close = float(hist_df['close'].iloc[-1])
        result[f'chip_concentration_{{window}}d'] = chip['concentration']
        result[f'profit_ratio_{{window}}d'] = chip['profit_ratio']
        result[f'avg_cost_distance_{{window}}d'] = (current_close - chip['avg_cost']) / chip['avg_cost'] if chip['avg_cost'] > 0 else np.nan
        result[f'peak_distance_{{window}}d'] = (current_close - chip['peak_price']) / chip['peak_price'] if chip['peak_price'] > 0 else np.nan
    return result
""")


def _generate_operator_defs() -> str:
    return textwrap.dedent("""\
def _op_add(left, right): return left + right
def _op_sub(left, right): return left - right
def _op_mul(left, right): return left * right
def _op_div(left, right): return left / right.replace(0, np.nan) if isinstance(right, pd.Series) else (left / right if right != 0 else np.nan)
def _op_abs(x): return np.abs(x)
def _op_log(x): return np.log(x.replace(0, np.nan)) if isinstance(x, pd.Series) else (np.log(x) if x > 0 else np.nan)
def _op_sqrt(x): return np.sqrt(np.maximum(x, 0))
def _op_sign(x): return np.sign(x)
def _op_rolling_mean(series, window): return series.rolling(window=int(window), min_periods=int(window)).mean()
def _op_rolling_std(series, window): return series.rolling(window=int(window), min_periods=int(window)).std(ddof=0)
def _op_rolling_sum(series, window): return series.rolling(window=int(window), min_periods=int(window)).sum()
def _op_rolling_max(series, window): return series.rolling(window=int(window), min_periods=int(window)).max()
def _op_rolling_min(series, window): return series.rolling(window=int(window), min_periods=int(window)).min()
def _op_delay(series, periods): return series.shift(int(periods))
def _op_delta(series, periods): return series - series.shift(int(periods))
def _op_pct_change(series, periods): return series.pct_change(periods=int(periods), fill_method=None)
def _op_ema(series, span): return series.ewm(span=int(span), adjust=False, min_periods=int(span)).mean()
def _op_ts_rank(series, window):
    win = int(window)
    return series.rolling(win, min_periods=win).apply(lambda v: pd.Series(v).rank(pct=True).iloc[-1], raw=False)
def _op_ts_zscore(series, window):
    win = int(window)
    mean = series.rolling(win, min_periods=win).mean()
    std = series.rolling(win, min_periods=win).std(ddof=0)
    return ((series - mean) / std.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
def _op_rolling_corr(left, right, window):
    win = int(window)
    return left.rolling(win, min_periods=win).corr(right)
def _op_rank(series): return series.rank(pct=True, method='average')
def _op_zscore(series):
    std = series.std(ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series(0.0, index=series.index)
    return (series - series.mean()) / std
def _op_minmax(series):
    minimum = series.min()
    maximum = series.max()
    if pd.isna(minimum) or pd.isna(maximum) or minimum == maximum:
        return pd.Series(0.0, index=series.index)
    return (series - minimum) / (maximum - minimum)
def _op_clip(series, lower, upper): return series.clip(lower=lower, upper=upper)
def _op_winsorize(series, lower_quantile, upper_quantile):
    lower = float(lower_quantile)
    upper = float(upper_quantile)
    lower_bound = series.quantile(lower)
    upper_bound = series.quantile(upper)
    return series.clip(lower=lower_bound, upper=upper_bound)

OPERATOR_MAP = {
    'add': _op_add, 'sub': _op_sub, 'mul': _op_mul, 'div': _op_div,
    'abs': _op_abs, 'log': _op_log, 'sqrt': _op_sqrt, 'sign': _op_sign,
    'rolling_mean': _op_rolling_mean, 'rolling_std': _op_rolling_std,
    'rolling_sum': _op_rolling_sum, 'rolling_max': _op_rolling_max,
    'rolling_min': _op_rolling_min, 'delay': _op_delay, 'delta': _op_delta,
    'pct_change': _op_pct_change, 'ema': _op_ema, 'ts_rank': _op_ts_rank,
    'ts_zscore': _op_ts_zscore, 'rolling_corr': _op_rolling_corr,
    'rank': _op_rank, 'zscore': _op_zscore, 'minmax': _op_minmax,
    'clip': _op_clip, 'winsorize': _op_winsorize,
}
""")


def _translate_expr_to_single_stock(expr: str) -> str:
    import re
    expr = re.sub(r'\.pct_change\(([^)]*)\)', lambda m: '.pct_change(fill_method=None)' if not m.group(1).strip() else f'.pct_change({m.group(1)}, fill_method=None)' if 'fill_method' not in m.group(1) else m.group(0), expr)
    # Replace 'open' with 'open_' to avoid conflict with Python built-in function
    # Use word boundary to avoid replacing 'open' inside other words
    expr = re.sub(r'\bopen\b', 'open_', expr)
    return expr


def _generate_feature_compute_block(
    required_features: dict[str, str],
    sorted_names: list[str],
    allowed_raw: set[str],
    has_chip: bool,
    chip_windows: list[int],
    indent: str = "            ",
) -> str:
    lines = []
    lines.append(f"{indent}feature_vals = {{}}")
    for name in sorted_names:
        expr = required_features[name]
        if expr.startswith("chip."):
            lines.append(f"{indent}# {name}: computed by chip distribution")
            continue
        translated = _translate_expr_to_single_stock(expr)
        lines.append(f"{indent}feature_vals['{name}'] = {translated}")
    return "\n".join(lines)


def _generate_strategy_code(
    factor: dict[str, Any],
    analysis_cfg: dict[str, Any],
    backtest_cfg: dict[str, Any],
    required_features: dict[str, str],
    sorted_feature_names: list[str],
    allowed_raw: set[str],
    has_chip: bool,
    chip_windows: list[int],
) -> str:
    factor_name = str(factor["factor_name"])
    formula = str(factor["formula"])
    direction = str(factor.get("llm_direction", "higher_better"))

    stock_pool = analysis_cfg.get("stock_pool", {})
    index_code_local = str(stock_pool.get("index_code", "SH000300"))
    index_code_jq = _convert_index_code(index_code_local)
    dynamic_membership = bool(stock_pool.get("dynamic_membership", True))
    include_st = bool(stock_pool.get("include_st", False))
    new_stock_days = int(stock_pool.get("new_stock_days", 60))

    run_mode = str(analysis_cfg.get("run_mode", "test"))
    if run_mode == "test":
        start_date = str(analysis_cfg.get("test_start_date", "2020-01-01"))
        end_date = str(analysis_cfg.get("test_end_date", "2025-12-31"))
    else:
        start_date = str(analysis_cfg.get("train_start_date", "2020-01-01"))
        end_date = str(analysis_cfg.get("train_end_date", "2025-12-31"))

    rebalance = str(analysis_cfg.get("rebalance", "weekly"))
    rebalance_anchor = str(analysis_cfg.get("rebalance_anchor", "first_trading_day_of_week"))

    strategy_type = str(backtest_cfg.get("strategy_type", "TopKDropout"))
    topk_cfg = dict(backtest_cfg.get("TopKDropout", {}))
    soft_cfg = dict(backtest_cfg.get("SoftTopK", {}))
    enhanced_cfg = dict(backtest_cfg.get("EnhancedIndexing", {}))
    market_timing_cfg = dict(backtest_cfg.get("MarketTiming", {}))
    execution_cfg = dict(backtest_cfg.get("Execution", {}))

    timing_enabled = bool(market_timing_cfg.get("enabled", False))
    buy_top_n = int(topk_cfg.get("buy_top_n", 20))
    sell_drop_to = int(topk_cfg.get("sell_drop_to", 40))
    holding_count = int(topk_cfg.get("holding_count", 20))
    weight_mode = str(topk_cfg.get("weight_mode", "equal_weight"))
    max_drop_per_day = int(topk_cfg.get("max_drop_per_day", 5))
    initial_cash = float(execution_cfg.get("initial_cash", 1_000_000))
    buy_cost = float(execution_cfg.get("buy_cost", 0.0015))
    sell_cost = float(execution_cfg.get("sell_cost", 0.0025))
    slippage_val = float(execution_cfg.get("slippage", 0.0005))
    cash_buffer_ratio = float(execution_cfg.get("cash_buffer_ratio", 0.02))
    trade_price = str(execution_cfg.get("trade_price", "next_open"))

    timing_reduce_to = float(market_timing_cfg.get("reduce_to", 0.5))
    ema_period = int(market_timing_cfg.get("market_indicator", "EMA_60").replace("EMA_", "") or 60)
    stock_open_filter = str(market_timing_cfg.get("stock_open_filter", "rsi"))
    stock_ema_period = int(market_timing_cfg.get("stock_ema_period", 60))
    rsi_period = int(market_timing_cfg.get("rsi_period", 14))
    rsi_buy_max = float(market_timing_cfg.get("rsi_buy_max", 70.0))

    suspend_action = str(execution_cfg.get("suspend_action", "skip"))
    limit_up_action = str(execution_cfg.get("limit_up_action", "skip_buy"))
    limit_down_action = str(execution_cfg.get("limit_down_action", "delay_sell"))

    max_window = 250
    for name in sorted_feature_names:
        expr = required_features[name]
        tree = ast.parse(expr, mode="eval")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ("rolling_mean", "rolling_std", "rolling_sum", "rolling_max", "rolling_min", "ema", "ts_rank", "ts_zscore", "rolling_corr"):
                    for arg in node.args[1:]:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                            max_window = max(max_window, arg.value + 10)
    if has_chip:
        max_window = max(max_window, max(chip_windows) + 10)

    rebalance_comment = ""
    if rebalance == "weekly":
        if rebalance_anchor == "first_trading_day_of_week":
            rebalance_comment = "每周首个交易日调仓"
        else:
            rebalance_comment = "每周最后一个交易日调仓"
    elif rebalance == "monthly":
        rebalance_comment = "每月调仓"
    else:
        rebalance_comment = "每日调仓"

    chip_code = _generate_chip_code(chip_windows) if has_chip else ""
    operator_code = _generate_operator_defs()

    feature_compute_lines = _generate_feature_compute_block(
        required_features, sorted_feature_names, allowed_raw, has_chip, chip_windows
    )

    chip_compute_in_loop = ""
    if has_chip:
        chip_compute_in_loop = textwrap.indent(textwrap.dedent(f"""\
            chip_result = _compute_chip_features_for_stock(hist, windows={chip_windows})
            for k, v in chip_result.items():
                feature_vals[k] = v
        """), "            ")

    direction_flip = ""
    if direction == "lower_better":
        direction_flip = "            score = -score  # lower_better: flip direction\n"

    timing_code = ""
    if timing_enabled:
        timing_code = textwrap.dedent(f"""\
    benchmark_hist = get_bars(g.benchmark_code, count={ema_period + 10}, unit='1d', fields=['date', 'close'], include_now=True, df=True)
    benchmark_hist = benchmark_hist.set_index('date')
    benchmark_ema = benchmark_hist['close'].ewm(span={ema_period}, adjust=False, min_periods={ema_period}).mean()
    benchmark_close = benchmark_hist['close'].iloc[-1]
    timing_active = benchmark_close < benchmark_ema.iloc[-1]
    exposure_scale = {timing_reduce_to} if timing_active else 1.0
    log.info('timing_active=%s, benchmark_close=%.2f, ema=%.2f, scale=%.2f' %% (timing_active, benchmark_close, benchmark_ema.iloc[-1], exposure_scale))
""")

    timing_filter_code = ""
    if timing_enabled and stock_open_filter != "none":
        if stock_open_filter == "rsi":
            timing_filter_code = textwrap.indent(textwrap.dedent(f"""\
        stock_hist_rsi = get_bars(code, count={rsi_period + 10}, unit='1d', fields=['date', 'close'], include_now=True, df=True)
        stock_hist_rsi = stock_hist_rsi.set_index('date')
        delta = stock_hist_rsi['close'].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling({rsi_period}, min_periods={rsi_period}).mean()
        avg_loss = loss.rolling({rsi_period}, min_periods={rsi_period}).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi_val = 100 - (100 / (1 + rs))
        current_rsi = rsi_val.iloc[-1]
        if not np.isnan(current_rsi) and current_rsi > {rsi_buy_max}:
            skip_new_open = True
"""), "            ")
        elif stock_open_filter == "ema":
            timing_filter_code = textwrap.indent(textwrap.dedent(f"""\
        stock_hist_ema = get_bars(code, count={stock_ema_period + 10}, unit='1d', fields=['date', 'close'], include_now=True, df=True)
        stock_hist_ema = stock_hist_ema.set_index('date')
        stock_ema_val = stock_hist_ema['close'].ewm(span={stock_ema_period}, adjust=False, min_periods={stock_ema_period}).mean()
        stock_close_val = stock_hist_ema['close'].iloc[-1]
        if stock_close_val < stock_ema_val.iloc[-1]:
            skip_new_open = True
"""), "            ")

    suspend_skip_code = ""
    if suspend_action == "skip":
        suspend_skip_code = "if current_data[code].paused: continue"

    limit_up_skip_code = ""
    if limit_up_action == "skip_buy":
        limit_up_skip_code = "if current_data[code].last_price >= current_data[code].high_limit: continue"

    limit_down_skip_code = ""
    if limit_down_action == "delay_sell":
        limit_down_skip_code = "if current_data[code].last_price <= current_data[code].low_limit: continue"

    strategy_logic = ""
    if strategy_type == "TopKDropout":
        strategy_logic = _gen_topk_dropout_logic(
            buy_top_n, sell_drop_to, holding_count, weight_mode, max_drop_per_day,
            timing_enabled, timing_filter_code, suspend_skip_code, limit_up_skip_code, limit_down_skip_code
        )
    elif strategy_type == "SoftTopK":
        strategy_logic = _gen_soft_topk_logic(
            soft_cfg, timing_enabled, timing_filter_code, suspend_skip_code, limit_up_skip_code, limit_down_skip_code
        )
    elif strategy_type == "EnhancedIndexing":
        strategy_logic = _gen_enhanced_indexing_logic(
            enhanced_cfg, timing_enabled, timing_filter_code, suspend_skip_code, limit_up_skip_code, limit_down_skip_code
        )

    rebalance_schedule = ""
    if rebalance == "weekly":
        if rebalance_anchor == "first_trading_day_of_week":
            rebalance_schedule = "    run_weekly(compute_and_store_scores, weekday=1, time='15:01')\n    run_weekly(execute_trades, weekday=2, time='09:30')"
        else:
            rebalance_schedule = "    run_weekly(compute_and_store_scores, weekday=5, time='15:01')\n    run_weekly(execute_trades, weekday=1, time='09:30')"
    elif rebalance == "monthly":
        rebalance_schedule = "    run_monthly(compute_and_store_scores, monthday=1, time='15:01')\n    run_monthly(execute_trades, monthday=2, time='09:30')"
    else:
        rebalance_schedule = "    run_daily(compute_and_store_scores, time='15:01')\n    run_daily(execute_trades, time='09:30')"

    code = textwrap.dedent(f"""\
# ============================================================
# JoinQuant Strategy Code
# Generated by factor-factory step14_joinquant_export
# Factor: {factor_name}
# Formula: {formula}
# Direction: {direction}
# Strategy: {strategy_type}
# Rebalance: {rebalance} ({rebalance_comment})
# ============================================================

import numpy as np
import pandas as pd
from jqdata import *

# ==================== Configuration ====================
INDEX_CODE = '{index_code_jq}'
DYNAMIC_MEMBERSHIP = {dynamic_membership}
INCLUDE_ST = {include_st}
NEW_STOCK_DAYS = {new_stock_days}
HISTORY_WINDOW = {max_window}

# Strategy parameters
BUY_TOP_N = {buy_top_n}
SELL_DROP_TO = {sell_drop_to}
HOLDING_COUNT = {holding_count}
MAX_DROP_PER_DAY = {max_drop_per_day}

# Execution cost
BUY_COST = {buy_cost}
SELL_COST = {sell_cost}
SLIPPAGE = {slippage_val}
CASH_BUFFER_RATIO = {cash_buffer_ratio}

# Execution constraints
SUSPEND_ACTION = '{suspend_action}'
LIMIT_UP_ACTION = '{limit_up_action}'
LIMIT_DOWN_ACTION = '{limit_down_action}'

# Market timing
TIMING_ENABLED = {timing_enabled}
TIMING_REDUCE_TO = {timing_reduce_to}
EMA_PERIOD = {ema_period}
STOCK_OPEN_FILTER = '{stock_open_filter}'
STOCK_EMA_PERIOD = {stock_ema_period}
RSI_PERIOD = {rsi_period}
RSI_BUY_MAX = {rsi_buy_max}

{chip_code}
{operator_code}

FACTOR_FORMULA = '{formula}'

# ==================== Factor Computation ====================
def compute_factor_scores(context):
    \"\"\"Compute factor scores for all stocks in the pool.\"\"\"
    if DYNAMIC_MEMBERSHIP:
        stocks = get_index_stocks(INDEX_CODE, date=context.current_dt.date())
    else:
        stocks = get_index_stocks(INDEX_CODE, date=context.current_dt.date())

    if not INCLUDE_ST:
        current_data = get_current_data()
        stocks = [s for s in stocks if not current_data[s].name.startswith(('ST', '*ST'))]

    if NEW_STOCK_DAYS > 0:
        today = context.current_dt.date()
        stocks = [s for s in stocks if (today - get_security_info(s).start_date).days >= NEW_STOCK_DAYS]

    scores = {{}}
    log.info('compute_factor_scores: stocks count=%d' % len(stocks))
    for code in stocks:
        try:
            hist = get_bars(code, count=HISTORY_WINDOW, unit='1d',
                           fields=['date', 'open', 'high', 'low', 'close', 'volume', 'money'],
                           include_now=True, df=True)
            log.info('code=%s, hist_len=%d' % (code, len(hist)))
            if hist.empty or len(hist) < 5:
                log.info('code=%s, hist too short, skip' % code)
                continue

            hist = hist.set_index('date')
            hist['amount'] = hist['money']
            hist['vwap'] = hist['money'] / hist['volume'].replace(0, np.nan)
            hist['turnover'] = 0.0

            close = hist['close']
            open_ = hist['open']
            high = hist['high']
            low = hist['low']
            volume = hist['volume']
            amount = hist['amount']
            vwap = hist['vwap']
            turnover = hist['turnover']

{feature_compute_lines}
{chip_compute_in_loop}
            env = dict(feature_vals)
            env.update(OPERATOR_MAP)
            env['np'] = np
            env['pd'] = pd
            score = eval(FACTOR_FORMULA, {{"__builtins__": {{}}}}, env)
            log.info('code=%s, score_type=%s' % (code, type(score).__name__))
            if isinstance(score, pd.Series):
                score = float(score.iloc[-1])
            log.info('code=%s, score=%s' % (code, str(score)))
            if np.isnan(score) or np.isinf(score):
                log.info('code=%s, score is nan/inf, skip' % code)
                continue
{direction_flip}
            scores[code] = score
            log.info('code=%s, final score=%.4f' % (code, score))
        except Exception as e:
            log.info('code=%s, exception: %s' % (code, str(e)))
            continue

    return scores


# ==================== Strategy Logic ====================
{strategy_logic}


# ==================== Main Entry ====================
def initialize(context):
    set_benchmark('{index_code_jq}')
    set_option('use_real_price', True)
    set_order_cost(OrderCost(
        open_tax=0,
        close_tax=0.001,
        open_commission=BUY_COST,
        close_commission=SELL_COST,
        close_today_commission=0,
        min_commission=5
    ), type='stock')
    set_slippage(FixedSlippage(SLIPPAGE * 100))

    g.benchmark_code = '{index_code_jq}'
    g.current_holdings = []

{rebalance_schedule}


def after_trading_end(context):
    pass
""")
    return code


def _gen_topk_dropout_logic(
    buy_top_n: int,
    sell_drop_to: int,
    holding_count: int,
    weight_mode: str,
    max_drop_per_day: int,
    timing_enabled: bool,
    timing_filter_code: str,
    suspend_skip_code: str,
    limit_up_skip_code: str,
    limit_down_skip_code: str,
) -> str:
    weight_calc = "    weight = 1.0 / max(len(target_holdings), 1)"

    sell_block_lines = ["is_buy_action = False"]
    if suspend_skip_code:
        sell_block_lines.append(suspend_skip_code)
    if limit_down_skip_code:
        sell_block_lines.append(limit_down_skip_code)
    sell_block_lines.append("order_target_value(code, 0)")
    sell_block_lines.append("sell_count += 1")
    sell_block_lines.append("log.info('sell order: %s' % code)")
    sell_block = textwrap.indent("\n".join(sell_block_lines), "            ")

    buy_block_lines = []
    buy_block_lines.append("is_buy_action = code not in set(current_holdings)")
    if suspend_skip_code:
        buy_block_lines.append(suspend_skip_code)
    if limit_up_skip_code:
        buy_block_lines.append(limit_up_skip_code)
    buy_block_lines.append("order_target_value(code, target_value)")
    buy_block_lines.append("if is_buy_action:")
    buy_block_lines.append("    buy_count += 1")
    buy_block_lines.append("    log.info('buy order: %s, value=%.2f' % (code, target_value))")
    buy_block = textwrap.indent("\n".join(buy_block_lines), "        ")

    return (
        textwrap.dedent(f"""\
def compute_and_store_scores(context):
    log.info('=== compute_and_store_scores called at %s ===' % context.current_dt)
    scores = compute_factor_scores(context)
    log.info('scores count: %d' % len(scores))
    if not scores:
        log.info('no scores, return')
        g.stored_scores = {{}}
        return

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    log.info('top 5 scores: %s' % str(ranked[:5]))
    ranked_codes = [code for code, _ in ranked]
    top_buy_codes = ranked_codes[:BUY_TOP_N]
    keep_rank_set = set(ranked_codes[:SELL_DROP_TO])

    current_holdings = list(context.portfolio.positions.keys())
    log.info('current_holdings: %s' % str(current_holdings))
    drop_candidates = [c for c in current_holdings if c not in keep_rank_set]
    drop_candidates = sorted(drop_candidates, key=lambda c: scores.get(c, -np.inf))
    to_sell_set = set(drop_candidates[:MAX_DROP_PER_DAY])
    kept = [c for c in current_holdings if c not in to_sell_set]

    max_new_buys = len(to_sell_set) if current_holdings else HOLDING_COUNT
    added = 0
    for code in top_buy_codes:
        if code in kept:
            continue
        if len(kept) >= HOLDING_COUNT:
            break
        if current_holdings and added >= max_new_buys:
            break
        if TIMING_ENABLED:
            skip_new_open = False
{timing_filter_code}
            if skip_new_open:
                continue
        kept.append(code)
        added += 1

    target_holdings = kept[:HOLDING_COUNT]
    log.info('target_holdings: %s' % str(target_holdings))

    g.stored_scores = scores
    g.stored_target_holdings = target_holdings
    log.info('scores and target_holdings stored')


def execute_trades(context):
    log.info('=== execute_trades called at %s ===' % context.current_dt)
    if not hasattr(g, 'stored_scores') or not g.stored_scores:
        log.info('no stored scores, return')
        return

    scores = g.stored_scores
    target_holdings = g.stored_target_holdings
    target_set = set(target_holdings)
    log.info('target_holdings: %s' % str(target_holdings))

    if not target_set:
        log.info('target_set empty, clear all positions')
        for code in list(context.portfolio.positions.keys()):
            order_target(code, 0)
        g.current_holdings = []
        g.stored_scores = {{}}
        return

{weight_calc}
    current_data = get_current_data()
    total_value = context.portfolio.total_value
    log.info('total_value: %.2f' % total_value)

    current_holdings = list(context.portfolio.positions.keys())
    sell_count = 0
    buy_count = 0
    for code in current_holdings:
        if code not in target_set:
""")
        + "            is_buy_action = False\n"
        + ("            " + suspend_skip_code + "\n" if suspend_skip_code else "")
        + ("            " + limit_down_skip_code + "\n" if limit_down_skip_code else "")
        + "            order_target_value(code, 0)\n"
        + "            sell_count += 1\n"
        + "            log.info('sell order: %s' % code)\n"
        + "    # 计算可用资金，预留缓冲\n"
        + "    available_cash = context.portfolio.available_cash\n"
        + "    reserved_cash = total_value * CASH_BUFFER_RATIO\n"
        + "    usable_cash = max(0, available_cash - reserved_cash)\n"
        + "    \n"
        + "    # 计算买入需要的总资金\n"
        + "    buy_codes = [code for code in target_holdings if code not in set(current_holdings)]\n"
        + "    total_buy_value = sum(weight * total_value for _ in buy_codes)\n"
        + "    \n"
        + "    # 如果资金不足，等比例缩放\n"
        + "    scale = 1.0\n"
        + "    if total_buy_value > usable_cash and total_buy_value > 0:\n"
        + "        scale = usable_cash / total_buy_value\n"
        + "    \n"
        + "    for code in target_holdings:\n"
        + "        if weight is not None:\n"
        + "            target_value = weight * total_value\n"
        + "        else:\n"
        + "            target_value = weights_arr[target_holdings.index(code)] * total_value\n"
        + "        \n"
        + "        # 如果是买入操作，应用缩放\n"
        + "        is_buy_action = code not in set(current_holdings)\n"
        + "        if is_buy_action:\n"
        + "            target_value = target_value * scale\n"
        + "        \n"
        + ("        " + suspend_skip_code + "\n" if suspend_skip_code else "")
        + ("        " + limit_up_skip_code + "\n" if limit_up_skip_code else "")
        + "        order_target_value(code, target_value)\n"
        + "        if is_buy_action:\n"
        + "            buy_count += 1\n"
        + "            log.info('buy order: %s, value=%.2f' % (code, target_value))\n"
        + "    g.current_holdings = target_holdings\n"
        + "    g.stored_scores = {}\n"
        + "    log.info('rebalance done: holdings=%d, sell=%d, buy=%d' % (len(target_holdings), sell_count, buy_count))\n"
    )


def _gen_soft_topk_logic(
    soft_cfg: dict,
    timing_enabled: bool,
    timing_filter_code: str,
    suspend_skip_code: str,
    limit_up_skip_code: str,
    limit_down_skip_code: str,
) -> str:
    top_n = int(soft_cfg.get("top_n", 30))
    holding_count = int(soft_cfg.get("holding_count", 30))
    weight_func = str(soft_cfg.get("weight_func", "softmax"))

    timing_var = ""
    if timing_enabled:
        timing_var = textwrap.indent(textwrap.dedent("""\
    benchmark_hist = get_bars(g.benchmark_code, count=EMA_PERIOD + 10, unit='1d', fields=['date', 'close'], include_now=True, df=True)
    benchmark_hist = benchmark_hist.set_index('date')
    benchmark_ema = benchmark_hist['close'].ewm(span=EMA_PERIOD, adjust=False, min_periods=EMA_PERIOD).mean()
    benchmark_close = benchmark_hist['close'].iloc[-1]
    timing_active = benchmark_close < benchmark_ema.iloc[-1]
    exposure_scale = TIMING_REDUCE_TO if timing_active else 1.0
"""), "    ")

    suspend_skip = suspend_skip_code

    order_lines = ["order_target_value(code, w * total_value)"]
    if suspend_skip_code:
        order_lines.insert(0, suspend_skip_code)
    order_block = textwrap.indent("\n".join(order_lines), "        ")

    order_head = "    for code, w in weights.items():\n"
    order_tail = "\n    g.current_holdings = list(weights.keys())\n    log.info('soft_topk: holdings=%d' % len(weights))\n"

    return (
        textwrap.dedent(f"""\
def rebalance(context):
    scores = compute_factor_scores(context)
    if not scores:
        return

{timing_var}
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    selected = ranked[:min({top_n}, {holding_count})]
    if not selected:
        return

    codes = [c for c, _ in selected]
    values = np.array([v for _, v in selected], dtype=float)

    if '{weight_func}' == 'rank_power':
        ranks = np.arange(1, len(codes) + 1, dtype=float)
        raw = 1.0 / np.power(ranks, 1.0)
    else:
        temperature = 1.0
        shifted = (values - float(values.max())) / temperature
        shifted = np.clip(shifted, -60.0, 60.0)
        raw = np.exp(shifted)

    denom = float(raw.sum())
    if not np.isfinite(denom) or denom <= 0:
        equal_w = 1.0 / len(codes)
        weights = {{c: equal_w for c in codes}}
    else:
        normalized = raw / denom
        weights = {{c: float(w) for c, w in zip(codes, normalized)}}

    if TIMING_ENABLED:
        current_holdings = list(context.portfolio.positions.keys())
        filtered = {{}}
        for c, w in weights.items():
            skip_new_open = False
            if c not in current_holdings:
{timing_filter_code}
                pass
            if not skip_new_open:
                filtered[c] = w
        weight_sum = sum(filtered.values())
        if weight_sum > 0:
            filtered = {{c: w / weight_sum for c, w in filtered.items()}}
        weights = filtered
        for c, w in weights.items():
            weights[c] = w * exposure_scale

    current_data = get_current_data()
    total_value = context.portfolio.total_value
    for code in list(context.portfolio.positions.keys()):
        if code not in weights:
            order_target_value(code, 0)
""")
        + order_head + order_block + order_tail
    )


def _gen_enhanced_indexing_logic(
    enhanced_cfg: dict,
    timing_enabled: bool,
    timing_filter_code: str,
    suspend_skip_code: str,
    limit_up_skip_code: str,
    limit_down_skip_code: str,
) -> str:
    holding_count = int(enhanced_cfg.get("holding_count", 30))
    weight_mode = str(enhanced_cfg.get("weight_mode", "benchmark_tilt"))
    active_weight_bound = float(enhanced_cfg.get("active_weight_bound", 0.02))
    tracking_error_limit = float(enhanced_cfg.get("tracking_error_limit", 0.05))

    timing_var = ""
    if timing_enabled:
        timing_var = textwrap.indent(textwrap.dedent("""\
    benchmark_hist = get_bars(g.benchmark_code, count=EMA_PERIOD + 10, unit='1d', fields=['date', 'close'], include_now=True, df=True)
    benchmark_hist = benchmark_hist.set_index('date')
    benchmark_ema = benchmark_hist['close'].ewm(span=EMA_PERIOD, adjust=False, min_periods=EMA_PERIOD).mean()
    benchmark_close = benchmark_hist['close'].iloc[-1]
    timing_active = benchmark_close < benchmark_ema.iloc[-1]
    exposure_scale = TIMING_REDUCE_TO if timing_active else 1.0
"""), "    ")

    suspend_skip = suspend_skip_code

    order_lines = ["order_target_value(code, w * total_value)"]
    if suspend_skip_code:
        order_lines.insert(0, suspend_skip_code)
    order_block = textwrap.indent("\n".join(order_lines), "        ")

    order_head = "    for code, w in target_weights.items():\n"
    order_tail = "\n    g.current_holdings = list(target_weights.keys())\n    log.info('enhanced_indexing: holdings=%d' % len(target_weights))\n"

    return (
        textwrap.dedent(f"""\
def rebalance(context):
    scores = compute_factor_scores(context)
    if not scores:
        return

{timing_var}
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    selected_codes = [c for c, _ in ranked[:{holding_count}]]
    if not selected_codes:
        return

    n = len(selected_codes)
    base_weight = 1.0 / n
    values = np.array([float(scores.get(c, 0)) for c in selected_codes], dtype=float)

    if '{weight_mode}' == 'equal_weight_enhanced':
        target_weights = {{c: base_weight for c in selected_codes}}
    else:
        bound = {active_weight_bound}
        if '{weight_mode}' == 'score_tilt':
            min_value = float(np.nanmin(values))
            shifted = np.where(np.isfinite(values), values - min_value + 1e-12, 0.0)
            denom = float(np.nansum(shifted))
            if not np.isfinite(denom) or denom <= 0:
                target_weights = {{c: base_weight for c in selected_codes}}
            else:
                raw = shifted / denom
                tilt = raw - base_weight
                raw_tilt = np.clip(tilt, -bound, bound)
                current_te = float(np.sqrt(np.mean(np.square(raw_tilt))))
                te_limit = {tracking_error_limit}
                if te_limit > 0 and current_te > te_limit:
                    raw_tilt = raw_tilt * (te_limit / current_te)
                weights_arr = base_weight + raw_tilt
                weights_arr = np.clip(weights_arr, 0.0, None)
                denom2 = float(weights_arr.sum())
                if not np.isfinite(denom2) or denom2 <= 0:
                    target_weights = {{c: base_weight for c in selected_codes}}
                else:
                    weights_arr = weights_arr / denom2
                    target_weights = {{c: float(w) for c, w in zip(selected_codes, weights_arr)}}
        else:
            std = float(values.std(ddof=0))
            if not np.isfinite(std) or std <= 1e-12:
                target_weights = {{c: base_weight for c in selected_codes}}
            else:
                z = (values - float(values.mean())) / std
                raw_tilt = z * bound
                raw_tilt = np.clip(raw_tilt, -bound, bound)
                current_te = float(np.sqrt(np.mean(np.square(raw_tilt))))
                te_limit = {tracking_error_limit}
                if te_limit > 0 and current_te > te_limit:
                    raw_tilt = raw_tilt * (te_limit / current_te)
                weights_arr = base_weight + raw_tilt
                weights_arr = np.clip(weights_arr, 0.0, None)
                denom2 = float(weights_arr.sum())
                if not np.isfinite(denom2) or denom2 <= 0:
                    target_weights = {{c: base_weight for c in selected_codes}}
                else:
                    weights_arr = weights_arr / denom2
                    target_weights = {{c: float(w) for c, w in zip(selected_codes, weights_arr)}}

    if TIMING_ENABLED:
        current_holdings = list(context.portfolio.positions.keys())
        filtered = {{}}
        for c, w in target_weights.items():
            skip_new_open = False
            if c not in current_holdings:
{timing_filter_code}
                pass
            if not skip_new_open:
                filtered[c] = w
        weight_sum = sum(filtered.values())
        if weight_sum > 0:
            filtered = {{c: w / weight_sum for c, w in filtered.items()}}
        target_weights = {{c: w * exposure_scale for c, w in filtered.items()}}

    current_data = get_current_data()
    total_value = context.portfolio.total_value
    for code in list(context.portfolio.positions.keys()):
        if code not in target_weights:
            order_target_value(code, 0)
""")
        + order_head + order_block + order_tail
    )


def run() -> None:
    parser = argparse.ArgumentParser(description="Generate JoinQuant strategy code for a specific factor")
    parser.add_argument("--factor", required=True, help="Factor name to generate JoinQuant code for")
    args = parser.parse_args()

    factor_name = args.factor
    factor = _find_factor(factor_name)
    if factor is None:
        print(f"Error: factor '{factor_name}' not found in output files")
        print("Searched: outputs/backtest/top3_factors.json, outputs/llm/factors_validated.json, outputs/backtest/cross_window_top3_factors.json")
        return

    formula = str(factor.get("formula", ""))
    if not formula:
        print(f"Error: factor '{factor_name}' has no formula")
        return

    print(f"Found factor: {factor_name}")
    print(f"  formula: {formula}")
    print(f"  direction: {factor.get('llm_direction', 'N/A')}")

    all_features = _load_all_features()
    feature_cfg = feature_pool_config()
    raw_items = feature_cfg.get("raw_fields", [])
    allowed_raw = {str(item) if isinstance(item, str) else str(item.get("name", "")) for item in raw_items}
    allowed_feature_names = set(all_features.keys()) | allowed_raw

    used_feature_names = formula_feature_names(formula, allowed_feature_names)
    required_features: dict[str, str] = {}
    for feat_name in used_feature_names:
        _resolve_feature_deps(feat_name, all_features, allowed_raw, required_features)

    sorted_feature_names = _topo_sort_features(required_features, allowed_raw)

    has_chip = _has_chip_features(required_features)
    chip_windows = _detect_chip_windows(required_features) if has_chip else []

    print(f"  required features: {sorted_feature_names}")
    print(f"  has chip features: {has_chip}")
    if has_chip:
        print(f"  chip windows: {chip_windows}")

    analysis_cfg = analysis_rule_config()
    backtest_cfg = backtest_rule_config()

    code = _generate_strategy_code(
        factor=factor,
        analysis_cfg=analysis_cfg,
        backtest_cfg=backtest_cfg,
        required_features=required_features,
        sorted_feature_names=sorted_feature_names,
        allowed_raw=allowed_raw,
        has_chip=has_chip,
        chip_windows=chip_windows,
    )

    output_dir = RUNTIME_ROOT / "joinquant"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{factor_name}.py"
    output_path.write_text(code, encoding="utf-8")
    print(f"\nJoinQuant strategy code saved to: {output_path}")
    print("Copy this code to JoinQuant platform to run backtest.")


if __name__ == "__main__":
    run()
