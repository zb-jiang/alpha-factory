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
        lines.append(f"{indent}{name} = feature_vals['{name}']")
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
    has_turnover: bool = False,
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
    if strategy_type == "SoftTopK":
        holding_count = int(soft_cfg.get("holding_count", 30))
    weight_mode = str(topk_cfg.get("weight_mode", "equal_weight"))
    max_drop_per_day = int(topk_cfg.get("max_drop_per_day", 5))
    min_score_coverage = float(topk_cfg.get("min_score_coverage", 0.90))
    weight_func = str(soft_cfg.get("weight_func", "softmax"))
    softmax_temperature = float(soft_cfg.get("softmax_temperature", 1.0))
    rank_power_alpha = float(soft_cfg.get("rank_power_alpha", 1.0))
    initial_cash = float(execution_cfg.get("initial_cash", 1_000_000))
    buy_cost = float(execution_cfg.get("buy_cost", 0.0015))
    sell_cost = float(execution_cfg.get("sell_cost", 0.0025))
    stamp_duty = float(execution_cfg.get("stamp_duty", 0.001))
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

    timing_init_code = ""
    timing_code = ""
    if timing_enabled:
        timing_init_code = ""
        timing_code = textwrap.dedent(f"""\
    all_dates = get_trade_days(end_date=context.current_dt.date(), count={ema_period + 30})
    pool_stocks_init = get_index_stocks(INDEX_CODE, date=context.current_dt.date())
    price_panel = get_price(pool_stocks_init, start_date=all_dates[0], end_date=context.current_dt, frequency='daily', fields=['close'], fq='pre')
    bench_series = pd.Series(dtype=float)
    if price_panel is not None:
        try:
            if hasattr(price_panel, 'items') and hasattr(price_panel, 'minor_axis'):
                if len(price_panel.items) == 1 and 'close' in price_panel.items:
                    bench_series = price_panel['close'].mean(axis=1)
                elif len(price_panel.minor_axis) == 1 and 'close' in price_panel.minor_axis:
                    bench_series = price_panel.minor_xs('close').mean(axis=1)
                else:
                    bench_series = price_panel[:,:,'close'].mean(axis=1)
            elif isinstance(price_panel, pd.DataFrame):
                if isinstance(price_panel.columns, pd.MultiIndex):
                    bench_series = price_panel.xs('close', axis=1, level=0).mean(axis=1)
                elif 'close' in price_panel.columns:
                    bench_series = price_panel['close']
            elif isinstance(price_panel, dict):
                all_closes = []
                for _sec, _df in price_panel.items():
                    if isinstance(_df, pd.DataFrame) and 'close' in _df.columns:
                        all_closes.append(_df['close'])
                    elif isinstance(_df, pd.Series):
                        all_closes.append(_df)
                if all_closes:
                    bench_series = pd.concat(all_closes, axis=1).mean(axis=1)
        except Exception as e:
            log.info('benchmark computation failed: %s' % str(e))
    if len(bench_series) > 0:
        _bench_df = pd.DataFrame(bench_series, columns=['close'])
        _bench_df.index = pd.to_datetime(_bench_df.index).normalize()
        benchmark_close = float(_bench_df['close'].iloc[-1])
        if len(_bench_df) >= {ema_period}:
            benchmark_ema = _bench_df['close'].ewm(span={ema_period}, adjust=False).mean()
            ema_val = float(benchmark_ema.iloc[-1])
            timing_active = benchmark_close < ema_val
        else:
            ema_val = 0.0
            timing_active = False
    else:
        benchmark_close = 0.0
        ema_val = 0.0
        timing_active = False
    exposure_scale = {timing_reduce_to} if timing_active else 1.0
    log.info('timing_active=%s, benchmark_close=%.2f, ema=%.2f, scale=%.2f' % (timing_active, benchmark_close, ema_val, exposure_scale))
""")

    timing_filter_code = ""
    if timing_enabled and stock_open_filter != "none":
        if stock_open_filter == "rsi":
            timing_filter_code = textwrap.indent(textwrap.dedent(f"""\
        stock_hist_rsi = get_price(code, end_date=context.current_dt, frequency='daily', fields=['close'], count={rsi_period + 30}, fq='pre')
        stock_hist_rsi = stock_hist_rsi.sort_index()
        delta = stock_hist_rsi['close'].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling({rsi_period}, min_periods={rsi_period}).mean()
        avg_loss = loss.rolling({rsi_period}, min_periods={rsi_period}).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi_val = 100 - (100 / (1 + rs))
        current_rsi = rsi_val.iloc[-1]
        log.info('stock filter (rsi): %s RSI=%.1f' % (code, current_rsi))
        if not np.isnan(current_rsi) and current_rsi > {rsi_buy_max}:
            skip_new_open = True
"""), "            ")
        elif stock_open_filter == "ema":
            timing_filter_code = textwrap.indent(textwrap.dedent(f"""\
        stock_hist_ema = get_price(code, end_date=context.current_dt, frequency='daily', fields=['close'], count={stock_ema_period + 30}, fq='pre')
        stock_hist_ema = stock_hist_ema.sort_index()
        stock_ema_val = stock_hist_ema['close'].ewm(span={stock_ema_period}, adjust=False, min_periods={stock_ema_period}).mean()
        stock_close_val = stock_hist_ema['close'].iloc[-1]
        log.info('stock filter (ema): %s close=%.2f, EMA=%.2f' % (code, stock_close_val, stock_ema_val.iloc[-1]))
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
            timing_enabled, timing_code, timing_filter_code, suspend_skip_code, limit_up_skip_code, limit_down_skip_code
        )
    elif strategy_type == "SoftTopK":
        strategy_logic = _gen_soft_topk_logic(
            soft_cfg, timing_enabled, timing_code, timing_filter_code, suspend_skip_code, limit_up_skip_code, limit_down_skip_code
        )
    elif strategy_type == "EnhancedIndexing":
        strategy_logic = _gen_enhanced_indexing_logic(
            enhanced_cfg, timing_enabled, timing_filter_code, suspend_skip_code, limit_up_skip_code, limit_down_skip_code
        )

    retry_schedule = "    run_daily(retry_limit_down_sells, time='09:30')" if limit_down_action == "delay_sell" else ""

    if has_turnover:
        turnover_prefetch = ""
        turnover_assign = """            # Fetch historical turnover ratio from JoinQuant
            hist['turnover'] = 0.0
            try:
                q = query(valuation.turnover_ratio).filter(valuation.code == code)
                turnover_df = get_fundamentals_continuously(q, end_date=context.current_dt, count=HISTORY_WINDOW, panel=False)
                if turnover_df is not None and not turnover_df.empty:
                    if 'day' in turnover_df.columns:
                        turnover_df = turnover_df.set_index('day')
                    if isinstance(turnover_df.columns, pd.MultiIndex):
                        turnover_df.columns = [c[0] if isinstance(c, tuple) else c for c in turnover_df.columns]
                    col_name = [c for c in turnover_df.columns if c != 'code' and 'turnover' in c.lower()]
                    col_name = col_name[0] if col_name else turnover_df.columns[0]
                    turnover_df.index = pd.to_datetime(turnover_df.index).strftime('%Y-%m-%d')
                    turnover_map = turnover_df[col_name].to_dict()
                    hist['turnover'] = hist.index.map(lambda d: turnover_map.get(str(d)[:10], 0.0) if pd.notna(turnover_map.get(str(d)[:10])) else 0.0)
                    log.info('code=%s, turnover nonzero=%d, sample=%s' % (code, int((hist['turnover'] != 0).sum()), str(hist['turnover'].tail(3).tolist())))
            except Exception as e:
                log.info('code=%s, turnover fetch failed: %s' % (code, str(e)))"""
    else:
        turnover_prefetch = ""
        turnover_assign = "            hist['turnover'] = 0.0"

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
MIN_SCORE_COVERAGE = {min_score_coverage}
WEIGHT_FUNC = '{weight_func}'
SOFTMAX_TEMPERATURE = {softmax_temperature}
RANK_POWER_ALPHA = {rank_power_alpha}

# Execution cost
BUY_COST = {buy_cost}
SELL_COST = {sell_cost}
STAMP_DUTY = {stamp_duty}
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
{turnover_prefetch}
    for code in stocks:
        try:
            hist = get_price(code, end_date=context.current_dt, frequency='daily',
                           fields=['open', 'high', 'low', 'close', 'volume', 'money'],
                           count=HISTORY_WINDOW, fq='pre')
            if hist.empty or len(hist) < 5:
                continue

            hist = hist.sort_index()
            hist['amount'] = hist['money']
            hist['vwap'] = hist['money'] / hist['volume'].replace(0, np.nan)
{turnover_assign}

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
            if isinstance(score, pd.Series):
                score = float(score.iloc[-1])
            if np.isnan(score) or np.isinf(score):
                continue
{direction_flip}
            scores[code] = score
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
        close_tax=STAMP_DUTY,
        open_commission=BUY_COST,
        close_commission=SELL_COST,
        close_today_commission=0,
        min_commission=5
    ), type='stock')
    set_slippage(PriceRelatedSlippage(SLIPPAGE))

    g.benchmark_code = '{index_code_jq}'
    g.current_holdings = []
    g.pending_limit_down_sells = []
{timing_init_code}

{rebalance_schedule}
{retry_schedule}


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
    timing_code: str,
    timing_filter_code: str,
    suspend_skip_code: str,
    limit_up_skip_code: str,
    limit_down_skip_code: str,
) -> str:
    timing_code_indented = textwrap.indent(timing_code, "    ") if timing_code else ""
    return (
        textwrap.dedent(f"""\
def compute_and_store_scores(context):
    log.info('=== compute_and_store_scores called at %s ===' % context.current_dt)
    scores = compute_factor_scores(context)
    log.info('scores count: %d' % len(scores))
    if not scores:
        log.info('no scores, return')
        g.stored_scores = {{}}
        g.stored_to_sell = []
        g.stored_new_buys = []
        return

    # min_score_coverage check
    pool_size = len(get_index_stocks(INDEX_CODE, date=context.current_dt.date()))
    if pool_size > 0 and len(scores) / pool_size < MIN_SCORE_COVERAGE:
        log.info('score coverage %.2f < %.2f, skip rebalance' % (len(scores) / pool_size, MIN_SCORE_COVERAGE))
        g.stored_scores = {{}}
        g.stored_to_sell = []
        g.stored_new_buys = []
        return

    # market timing: compute exposure_scale
    exposure_scale = 1.0
{timing_code_indented}

    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0].split('.')[0]))
    log.info('top 5 scores: %s' % str(ranked[:5]))
    ranked_codes = [code for code, _ in ranked]
    top_buy_codes = ranked_codes[:BUY_TOP_N]
    keep_rank_set = set(ranked_codes[:SELL_DROP_TO])

    current_holdings = list(context.portfolio.positions.keys())
    log.info('current_holdings: %s' % str(current_holdings))

    # determine sell set
    drop_candidates = [c for c in current_holdings if c not in keep_rank_set]
    drop_candidates = sorted(drop_candidates, key=lambda c: (scores.get(c, -np.inf), c.split('.')[0]))
    to_sell_count = min(len(drop_candidates), MAX_DROP_PER_DAY)
    to_sell_set = set(drop_candidates[:to_sell_count])
    kept = [c for c in current_holdings if c not in to_sell_set]

    # determine buy set with position replenishment
    max_new_buys = to_sell_count if current_holdings else HOLDING_COUNT
    if current_holdings:
        shortfall = HOLDING_COUNT - len(current_holdings) + to_sell_count
        max_new_buys = max(max_new_buys, shortfall)
    new_buys = []
    for code in top_buy_codes:
        if code in kept:
            continue
        if len(kept) + len(new_buys) >= HOLDING_COUNT:
            break
        if len(new_buys) >= max_new_buys:
            break
        new_buys.append(code)

    # stock-level timing filter: remove blocked new buys (no replacement)
    if TIMING_ENABLED:
        blocked_buys = []
        filtered_buys = []
        for code in new_buys:
            skip_new_open = False
{timing_filter_code}
            if skip_new_open:
                blocked_buys.append(code)
            else:
                filtered_buys.append(code)
        if blocked_buys:
            log.info('blocked new buys: %s, allowed: %s' % (str(sorted(blocked_buys)), str(sorted(filtered_buys))))
        new_buys = filtered_buys

    log.info('to_sell: %s' % str(list(to_sell_set)))
    log.info('new_buys: %s' % str(new_buys))
    log.info('kept: %d, sell: %d, buy: %d' % (len(kept), len(to_sell_set), len(new_buys)))

    g.stored_scores = scores
    g.stored_to_sell = list(to_sell_set)
    g.stored_new_buys = new_buys
    g.stored_target_count = len(kept) + len(new_buys)
    g.stored_exposure_scale = exposure_scale
    log.info('scores and trade plan stored')


def _min_lot(code):
    pure = code[:6]
    if pure.startswith('688'):
        return 200
    return 100

def _round_to_lot(code, shares):
    lot = _min_lot(code)
    if shares <= 0:
        return 0
    return (shares // lot) * lot


def execute_trades(context):
    log.info('=== execute_trades called at %s ===' % context.current_dt)
    if not hasattr(g, 'stored_scores') or not g.stored_scores:
        log.info('no stored scores, return')
        return

    to_sell = g.stored_to_sell
    new_buys = g.stored_new_buys
    target_count = max(getattr(g, 'stored_target_count', HOLDING_COUNT), 1)
    exposure_scale = getattr(g, 'stored_exposure_scale', 1.0)
    current_data = get_current_data()
    total_value = context.portfolio.total_value
    log.info('total_value: %.2f, exposure_scale: %.2f' % (total_value, exposure_scale))

    current_holdings = list(context.portfolio.positions.keys())
    sell_count = 0
    buy_count = 0
    sold_codes = []
    pending_sell_value = 0.0

    # sell: stocks in to_sell list (full sell)
    for code in to_sell:
        if code not in current_holdings:
            continue
        if current_data[code].paused:
            log.info('sell skip (paused): %s' % code)
            continue
        if current_data[code].last_price <= current_data[code].low_limit:
            log.info('sell skip (limit_down): %s, will retry' % code)
            if not hasattr(g, 'pending_limit_down_sells'):
                g.pending_limit_down_sells = []
            g.pending_limit_down_sells.append(code)
            continue
        pos = context.portfolio.positions[code]
        shares_to_sell = pos.total_amount
        if shares_to_sell > 0:
            if code.startswith('688'):
                limit_price = current_data[code].last_price * 0.98
                order(code, -shares_to_sell, style=LimitOrderStyle(limit_price))
            else:
                order(code, -shares_to_sell)
            pending_sell_value += shares_to_sell * current_data[code].last_price
            sell_count += 1
            sold_codes.append(code)
            log.info('sell order: %s, shares=%d' % (code, shares_to_sell))

    # timing: partial sell for kept stocks when exposure_scale < 1.0
    if TIMING_ENABLED and exposure_scale < 1.0 - 1e-6:
        kept_codes = [c for c in current_holdings if c not in set(to_sell)]
        for code in kept_codes:
            if code not in context.portfolio.positions:
                continue
            pos = context.portfolio.positions[code]
            if pos.total_amount <= 0:
                continue
            current_weight = pos.value / total_value if total_value > 0 else 0.0
            target_weight = current_weight * exposure_scale
            if current_weight > target_weight + 0.005:
                if current_data[code].paused:
                    continue
                if current_data[code].last_price <= current_data[code].low_limit:
                    continue
                target_shares = _round_to_lot(code, int(target_weight * total_value / current_data[code].last_price))
                shares_to_sell = pos.total_amount - target_shares
                shares_to_sell = _round_to_lot(code, shares_to_sell)
                if shares_to_sell > 0:
                    if code.startswith('688'):
                        limit_price = current_data[code].last_price * 0.98
                        order(code, -shares_to_sell, style=LimitOrderStyle(limit_price))
                    else:
                        order(code, -shares_to_sell)
                    pending_sell_value += shares_to_sell * current_data[code].last_price
                    sell_count += 1
                    log.info('timing partial sell: %s, current_weight=%.4f, target_weight=%.4f, shares=%d' % (code, current_weight, target_weight, shares_to_sell))

    # buy: stocks in new_buys list
    weight_per_stock = exposure_scale / target_count
    available_cash = context.portfolio.available_cash
    reserved_cash = total_value * CASH_BUFFER_RATIO
    usable_cash = max(0, available_cash + pending_sell_value - reserved_cash)

    buy_plan = []
    total_buy_value = 0.0
    for code in new_buys:
        target_value = weight_per_stock * total_value
        current_pos_value = 0.0
        if code in context.portfolio.positions:
            current_pos_value = context.portfolio.positions[code].value
        delta = target_value - current_pos_value
        if delta > 0:
            buy_plan.append((code, delta))
            total_buy_value += delta

    scale = 1.0
    if total_buy_value > usable_cash and total_buy_value > 0:
        scale = usable_cash / total_buy_value
        log.info('cash scale: %.4f (buy_value=%.2f, available=%.2f)' % (scale, total_buy_value, usable_cash))

    for code, delta in buy_plan:
        if current_data[code].paused:
            log.info('buy skip (paused): %s' % code)
            continue
        if current_data[code].last_price >= current_data[code].high_limit:
            log.info('buy skip (limit_up): %s' % code)
            continue
        adjusted_value = delta * scale
        price_for_calc = current_data[code].last_price * 1.02 if code[:6].startswith('688') else current_data[code].last_price
        actual_shares = _round_to_lot(code, int(adjusted_value / price_for_calc))
        if actual_shares <= 0:
            log.info('buy skip (shares < min_lot): %s, target_value=%.2f, price=%.4f' % (code, adjusted_value, current_data[code].last_price))
            continue
        if code.startswith('688'):
            limit_price = current_data[code].last_price * 1.02
            order(code, actual_shares, style=LimitOrderStyle(limit_price))
        else:
            order(code, actual_shares)
        buy_count += 1
        log.info('buy order: %s, shares=%d, value=%.2f' % (code, actual_shares, actual_shares * current_data[code].last_price))

    g.current_holdings = [c for c in current_holdings if c not in set(sold_codes)] + new_buys[:buy_count]
    g.stored_scores = {{}}
    g.stored_to_sell = []
    g.stored_new_buys = []
    log.info('rebalance done: sell=%d, buy=%d' % (sell_count, buy_count))


def retry_limit_down_sells(context):
    '''跌停重试：每日开盘时重试之前因跌停未能卖出的股票'''
    if not hasattr(g, 'pending_limit_down_sells') or not g.pending_limit_down_sells:
        return
    current_data = get_current_data()
    remaining = []
    for code in g.pending_limit_down_sells:
        if code not in context.portfolio.positions:
            continue
        if current_data[code].paused:
            continue
        if current_data[code].last_price <= current_data[code].low_limit:
            remaining.append(code)
            log.info('retry sell skip (still limit_down): %s' % code)
            continue
        if code.startswith('688'):
            limit_price = current_data[code].last_price * 0.98
            order(code, -context.portfolio.positions[code].total_amount, style=LimitOrderStyle(limit_price))
        else:
            order(code, -context.portfolio.positions[code].total_amount)
        log.info('retry sell success: %s' % code)
    g.pending_limit_down_sells = remaining
""")
    )


def _gen_soft_topk_logic(
    soft_cfg: dict,
    timing_enabled: bool,
    timing_code: str,
    timing_filter_code: str,
    suspend_skip_code: str,
    limit_up_skip_code: str,
    limit_down_skip_code: str,
) -> str:
    holding_count = int(soft_cfg.get("holding_count", 30))
    weight_func = str(soft_cfg.get("weight_func", "softmax"))
    softmax_temperature = float(soft_cfg.get("softmax_temperature", 1.0))
    rank_power_alpha = float(soft_cfg.get("rank_power_alpha", 1.0))

    timing_code_indented = textwrap.indent(timing_code, "    ") if timing_code else ""

    return (
        textwrap.dedent(f"""\
def compute_and_store_scores(context):
    log.info('=== compute_and_store_scores called at %s ===' % context.current_dt)
    scores = compute_factor_scores(context)
    log.info('scores count: %d' % len(scores))
    if not scores:
        log.info('no scores, return')
        g.stored_target_weights = {{}}
        return

    pool_size = len(get_index_stocks(INDEX_CODE, date=context.current_dt.date()))
    if pool_size > 0 and len(scores) / pool_size < MIN_SCORE_COVERAGE:
        log.info('score coverage %.2f < %.2f, skip rebalance' % (len(scores) / pool_size, MIN_SCORE_COVERAGE))
        g.stored_target_weights = {{}}
        return

    # market timing: compute exposure_scale
    exposure_scale = 1.0
{timing_code_indented}

    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0].split('.')[0]))
    log.info('top 5 scores: %s' % str(ranked[:5]))
    selected = ranked[:HOLDING_COUNT]
    if not selected:
        g.stored_target_weights = {{}}
        return

    codes = [c for c, _ in selected]
    values = np.array([v for _, v in selected], dtype=float)

    if WEIGHT_FUNC == 'rank_power':
        alpha = max(RANK_POWER_ALPHA, 1e-9)
        ranks = np.arange(1, len(codes) + 1, dtype=float)
        raw = 1.0 / np.power(ranks, alpha)
    else:
        temperature = max(SOFTMAX_TEMPERATURE, 1e-9)
        shifted = (values - float(values.max())) / temperature
        shifted = np.clip(shifted, -60.0, 60.0)
        raw = np.exp(shifted)

    denom = float(raw.sum())
    if not np.isfinite(denom) or denom <= 0:
        equal_w = 1.0 / len(codes)
        target_weights = {{c: equal_w for c in codes}}
    else:
        normalized = raw / denom
        target_weights = {{c: float(w) for c, w in zip(codes, normalized)}}

    # apply market timing: scale all weights by exposure_scale
    if TIMING_ENABLED and exposure_scale < 1.0 - 1e-6:
        for code in list(target_weights.keys()):
            target_weights[code] *= exposure_scale
        log.info('timing scale applied: exposure_scale=%.2f' % exposure_scale)

    # apply stock-level timing filter: block new opens
    if TIMING_ENABLED:
        current_holdings_list = list(context.portfolio.positions.keys())
        blocked = []
        for code in list(target_weights.keys()):
            if code in current_holdings_list:
                continue
            if target_weights[code] <= 0:
                continue
            skip_new_open = False
{timing_filter_code}
            if skip_new_open:
                blocked.append(code)
        if blocked:
            log.info('timing stock filter blocked: %s' % str(blocked))
            for code in blocked:
                target_weights[code] = 0.0
            if exposure_scale < 1.0 - 1e-6:
                log.info('RISK STATE: skip renormalize, kept stocks keep reduced weights')
            else:
                remaining_sum = sum(w for w in target_weights.values() if w > 0)
                original_sum = exposure_scale
                if remaining_sum > 0 and original_sum > 0 and abs(remaining_sum - original_sum) > 0.001:
                    factor = original_sum / remaining_sum
                    log.info('timing renormalize factor: %.3f' % factor)
                    for code in target_weights:
                        if target_weights[code] > 0:
                            target_weights[code] *= factor

    current_holdings = list(context.portfolio.positions.keys())
    for code in current_holdings:
        if code not in target_weights:
            target_weights[code] = 0.0

    log.info('target_weights: %d stocks' % len(target_weights))
    g.stored_target_weights = target_weights


def _min_lot(code):
    pure = code[:6]
    if pure.startswith('688'):
        return 200
    return 100

def _round_to_lot(code, shares):
    lot = _min_lot(code)
    if shares <= 0:
        return 0
    return (shares // lot) * lot


def execute_trades(context):
    log.info('=== execute_trades called at %s ===' % context.current_dt)
    if not hasattr(g, 'stored_target_weights') or not g.stored_target_weights:
        log.info('no stored target weights, return')
        return

    target_weights = g.stored_target_weights
    current_data = get_current_data()
    total_value = context.portfolio.total_value
    log.info('total_value: %.2f' % total_value)

    sell_orders = []
    buy_orders = []
    limit_down_full_sells = []

    for code, target_weight in target_weights.items():
        if code not in context.portfolio.positions:
            continue
        pos = context.portfolio.positions[code]
        if pos.total_amount <= 0:
            continue
        current_value = pos.value
        current_weight = current_value / total_value if total_value > 0 else 0.0

        if target_weight < current_weight - 0.005:
            if current_data[code].paused:
                continue
            if current_data[code].last_price <= current_data[code].low_limit:
                if target_weight <= 1e-6:
                    limit_down_full_sells.append(code)
                continue

            target_value = target_weight * total_value
            if target_weight > 1e-6:
                target_shares = _round_to_lot(code, int(target_value / current_data[code].last_price))
            else:
                target_shares = 0
            shares_to_sell = pos.total_amount - target_shares
            shares_to_sell = _round_to_lot(code, shares_to_sell)
            if shares_to_sell > 0:
                sell_orders.append((code, shares_to_sell))

    pending_sell_value = 0.0
    for code, shares in sell_orders:
        pending_sell_value += shares * current_data[code].last_price

    available_cash = context.portfolio.available_cash
    reserved_cash = total_value * CASH_BUFFER_RATIO
    usable_cash = max(0, available_cash + pending_sell_value - reserved_cash)

    for code, target_weight in target_weights.items():
        if target_weight <= 0:
            continue
        current_value = 0.0
        if code in context.portfolio.positions:
            current_value = context.portfolio.positions[code].value
        current_weight = current_value / total_value if total_value > 0 else 0.0

        if target_weight > current_weight + 0.005:
            if current_data[code].paused:
                continue
            if current_data[code].last_price >= current_data[code].high_limit:
                continue
            delta = target_weight * total_value - current_value
            buy_orders.append((code, delta))

    total_buy_value = sum(delta for _, delta in buy_orders)
    scale = 1.0
    if total_buy_value > usable_cash and total_buy_value > 0:
        scale = usable_cash / total_buy_value
        log.info('cash scale: %.4f (buy_value=%.2f, available=%.2f)' % (scale, total_buy_value, usable_cash))

    for code, shares_to_sell in sell_orders:
        if code.startswith('688'):
            limit_price = current_data[code].last_price * 0.98
            order(code, -shares_to_sell, style=LimitOrderStyle(limit_price))
        else:
            order(code, -shares_to_sell)
        log.info('sell order: %s, shares=%d' % (code, shares_to_sell))

    for code, delta in buy_orders:
        if current_data[code].paused:
            log.info('buy skip (paused): %s' % code)
            continue
        if current_data[code].last_price >= current_data[code].high_limit:
            log.info('buy skip (limit_up): %s' % code)
            continue
        adjusted_value = delta * scale
        price_for_calc = current_data[code].last_price * 1.02 if code[:6].startswith('688') else current_data[code].last_price
        actual_shares = _round_to_lot(code, int(adjusted_value / price_for_calc))
        if actual_shares <= 0:
            log.info('buy skip (shares < min_lot): %s, target_value=%.2f, price=%.4f' % (code, adjusted_value, current_data[code].last_price))
            continue
        if code.startswith('688'):
            limit_price = current_data[code].last_price * 1.02
            order(code, actual_shares, style=LimitOrderStyle(limit_price))
        else:
            order(code, actual_shares)
        log.info('buy order: %s, shares=%d, value=%.2f' % (code, actual_shares, actual_shares * current_data[code].last_price))

    for code in limit_down_full_sells:
        if not hasattr(g, 'pending_limit_down_sells'):
            g.pending_limit_down_sells = []
        g.pending_limit_down_sells.append(code)
        log.info('limit down sell deferred: %s' % code)

    g.stored_target_weights = {{}}
    retry_limit_down_sells(context)


def retry_limit_down_sells(context):
    '''跌停重试：每日开盘时重试之前因跌停未能卖出的股票'''
    if not hasattr(g, 'pending_limit_down_sells') or not g.pending_limit_down_sells:
        return
    current_data = get_current_data()
    remaining = []
    for code in g.pending_limit_down_sells:
        if code not in context.portfolio.positions:
            continue
        if current_data[code].paused:
            continue
        if current_data[code].last_price <= current_data[code].low_limit:
            remaining.append(code)
            log.info('retry sell skip (still limit_down): %s' % code)
            continue
        if code.startswith('688'):
            limit_price = current_data[code].last_price * 0.98
            order(code, -context.portfolio.positions[code].total_amount, style=LimitOrderStyle(limit_price))
        else:
            order(code, -context.portfolio.positions[code].total_amount)
        log.info('retry sell success: %s' % code)
    g.pending_limit_down_sells = remaining
""")
    )


def _gen_enhanced_indexing_logic(
    enhanced_cfg: dict,
    timing_enabled: bool,
    timing_filter_code: str,
    suspend_skip_code: str,
    limit_up_skip_code: str,
    limit_down_skip_code: str,
) -> str:
    return textwrap.dedent("""\
def compute_and_store_scores(context):
    log.info('EnhancedIndexing strategy not implemented yet')
    g.stored_target_weights = {}

def execute_trades(context):
    pass
""")


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
    has_turnover = any("turnover" in name for name in sorted_feature_names) or any("turnover" in expr for expr in required_features.values())

    print(f"  required features: {sorted_feature_names}")
    print(f"  has chip features: {has_chip}")
    print(f"  has turnover features: {has_turnover}")
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
        has_turnover=has_turnover,
    )

    output_dir = RUNTIME_ROOT / "joinquant"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{factor_name}.py"
    output_path.write_text(code, encoding="utf-8")
    print(f"\nJoinQuant strategy code saved to: {output_path}")
    print("Copy this code to JoinQuant platform to run backtest.")


if __name__ == "__main__":
    run()
