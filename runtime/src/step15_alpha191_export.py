from __future__ import annotations

import argparse
import ast
import textwrap
from pathlib import Path
from typing import Any

from common import (
    JOINQUANT_DIR,
    analysis_rule_config,
    backtest_rule_config,
    read_json,
)
from step14_joinquant_export import (
    _gen_soft_topk_logic,
    _gen_enhanced_indexing_logic,
    _convert_index_code,
    _generate_operator_defs,
)


def _generate_alpha191_operator_defs() -> str:
    """Generate 16 alpha191-specific operator definitions as Python code string."""
    return textwrap.dedent("""\
def _op_sma(x, n, m):
    n_val = int(n)
    m_val = float(m)
    alpha = m_val / n_val
    return x.ewm(alpha=alpha, adjust=False, min_periods=n_val).mean()

def _op_decay_linear(x, n):
    win = int(n)
    weights = np.arange(1, win + 1, dtype=float)
    weights = weights / weights.sum()
    return x.rolling(win, min_periods=win).apply(lambda vals: np.dot(vals, weights), raw=True)

def _op_wma(x, n):
    win = int(n)
    weights = np.arange(1, win + 1, dtype=float)
    weights = weights / weights.sum()
    return x.rolling(win, min_periods=win).apply(lambda vals: np.dot(vals, weights), raw=True)

def _op_regbeta(y, x, n):
    win = int(n)
    xy = x * y
    x2 = x * x
    sum_x = x.rolling(win, min_periods=win).sum()
    sum_y = y.rolling(win, min_periods=win).sum()
    sum_xy = xy.rolling(win, min_periods=win).sum()
    sum_x2 = x2.rolling(win, min_periods=win).sum()
    numerator = win * sum_xy - sum_x * sum_y
    denominator = win * sum_x2 - sum_x ** 2
    return numerator / denominator.replace(0, np.nan)

def _op_regbeta_seq(y, n):
    win = int(n)
    x_vals = np.arange(1, win + 1, dtype=float)
    x_mean = (win + 1) / 2.0
    x_centered = x_vals - x_mean
    x_ss = float(np.sum(x_centered ** 2))
    return y.rolling(win, min_periods=win).apply(lambda vals: np.dot(vals, x_centered) / x_ss, raw=True)

def _op_count(cond, n):
    win = int(n)
    return cond.astype(float).rolling(win, min_periods=win).sum()

def _op_sumif(x, n, cond):
    win = int(n)
    return (x * cond.astype(float)).rolling(win, min_periods=win).sum()

def _op_sumac(series):
    return series.cumsum()

def _op_prod(x, n):
    win = int(n)
    return x.rolling(win, min_periods=win).apply(np.prod, raw=True)

def _op_lowday(x, n):
    win = int(n)
    return x.rolling(win, min_periods=win).apply(lambda vals: float(win - 1 - np.argmin(vals)), raw=True)

def _op_highday(x, n):
    win = int(n)
    return x.rolling(win, min_periods=win).apply(lambda vals: float(win - 1 - np.argmax(vals)), raw=True)

def _op_covariance(x, y, n):
    win = int(n)
    xy = x * y
    sum_x = x.rolling(win, min_periods=win).sum()
    sum_y = y.rolling(win, min_periods=win).sum()
    sum_xy = xy.rolling(win, min_periods=win).sum()
    mean_x = sum_x / win
    mean_y = sum_y / win
    return sum_xy / win - mean_x * mean_y

def _op_maximum(left, right):
    return np.maximum(left, right)

def _op_minimum(left, right):
    return np.minimum(left, right)

def _op_filter_cond(x, cond):
    return x.where(cond.astype(bool), np.nan)

def _op_where(cond, x, y):
    result = np.where(cond, x, y)
    for obj in (cond, x, y):
        if isinstance(obj, pd.Series):
            return pd.Series(result, index=obj.index)
    return result

ALPHA191_OPERATOR_MAP = {
    'sma': _op_sma, 'decay_linear': _op_decay_linear, 'wma': _op_wma,
    'regbeta': _op_regbeta, 'regbeta_seq': _op_regbeta_seq,
    'count': _op_count, 'sumif': _op_sumif, 'sumac': _op_sumac,
    'prod': _op_prod, 'lowday': _op_lowday, 'highday': _op_highday,
    'covariance': _op_covariance, 'maximum': _op_maximum,
    'minimum': _op_minimum, 'filter_cond': _op_filter_cond,
    'where': _op_where,
}
OPERATOR_MAP.update(ALPHA191_OPERATOR_MAP)
""")


def _gen_topk_dropout_logic_aligned(
    buy_top_n: int,
    sell_drop_to: int,
    holding_count: int,
    max_drop_per_day: int,
    timing_enabled: bool,
    timing_code: str,
    timing_filter_code: str,
) -> str:
    """Generate TopKDropout strategy logic aligned with local engine.

    Key difference from step14: execute_trades iterates over ALL positions + target stocks,
    comparing target_weight vs current_weight with 0.5% threshold for rebalancing,
    exactly matching engine/__init__.py _on_trade_day logic.
    """
    timing_code_indented = textwrap.indent(timing_code, "    ") if timing_code else ""

    return textwrap.dedent(f"""\
def compute_and_store_scores(context):
    log.info('=== compute_and_store_scores called at %s ===' % context.current_dt)
    # Clear pending limit down sells at signal day (aligned with engine _on_signal_day)
    if hasattr(g, 'pending_limit_down_sells'):
        g.pending_limit_down_sells = []
    scores = compute_factor_scores(context)
    log.info('scores count: %d' % len(scores))
    if not scores:
        log.info('no scores, return')
        g.stored_target_weights = {{}}
        return

    # min_score_coverage check
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
    ranked_codes = [code for code, _ in ranked]
    keep_rank_set = set(ranked_codes[:SELL_DROP_TO])

    current_holdings = list(context.portfolio.positions.keys())
    log.info('current_holdings: %s' % str(current_holdings))

    # determine sell set (dropout logic)
    drop_candidates = [c for c in current_holdings if c not in keep_rank_set]
    drop_candidates = sorted(drop_candidates, key=lambda c: (scores.get(c, -float('inf')), c.split('.')[0]))
    to_sell_count = min(len(drop_candidates), MAX_DROP_PER_DAY)
    to_sell_set = set(drop_candidates[:to_sell_count])
    kept = [c for c in current_holdings if c not in to_sell_set]

    # determine buy set
    max_new_buys = to_sell_count if current_holdings else HOLDING_COUNT
    if current_holdings:
        shortfall = HOLDING_COUNT - len(current_holdings) + to_sell_count
        max_new_buys = max(max_new_buys, shortfall)
    new_buys = []
    for code in ranked_codes:
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

    # === Compute target_weights (aligned with engine/topk_dropout.py) ===
    # to_sell -> weight 0
    # kept -> signal day close weight (current portfolio weight at signal close)
    # new_buys -> equal weight = 1/target_count
    if not current_holdings:
        target_count = HOLDING_COUNT
    else:
        target_count = len(kept) + len(new_buys)
    if target_count <= 0:
        target_count = 1
    weight_per_stock = 1.0 / target_count

    # Get signal-day close prices for kept stocks
    total_value = context.portfolio.total_value
    target_weights = {{}}
    for code in to_sell_set:
        target_weights[code] = 0.0
    for code in kept:
        pos = context.portfolio.positions.get(code)
        if pos is not None and pos.total_amount > 0 and total_value > 0:
            # Use current position value as signal-day weight
            target_weights[code] = pos.value / total_value
        else:
            target_weights[code] = 0.0
    for code in new_buys:
        target_weights[code] = weight_per_stock

    # apply market timing scale
    if TIMING_ENABLED and exposure_scale < 1.0 - 1e-6:
        for code in list(target_weights.keys()):
            target_weights[code] *= exposure_scale
        log.info('timing scale applied: exposure_scale=%.2f' % exposure_scale)

    g.stored_target_weights = target_weights
    log.info('target_weights: %d stocks' % len(target_weights))


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
        retry_limit_down_sells(context)
        return

    target_weights = g.stored_target_weights
    current_data = get_current_data()

    # === Fetch today's open prices (aligned with engine using open prices) ===
    # In JQ backtest, current_data[code].last_price at 09:30 is previous close, not today's open.
    # Must actively fetch today's open via get_price to match local engine behavior.
    all_position_codes = list(context.portfolio.positions.keys())
    all_target_codes = list(target_weights.keys())
    all_price_codes = sorted(set(all_position_codes + all_target_codes))
    today_date = context.current_dt.date()
    open_prices = {{}}
    if all_price_codes:
        try:
            open_df = get_price(all_price_codes, start_date=today_date, end_date=today_date,
                                frequency='daily', fields=['open'], fq='pre')
            if open_df is not None and not open_df.empty:
                # Handle both Panel (deprecated) and DataFrame formats
                try:
                    if isinstance(open_df, pd.DataFrame) and isinstance(open_df.index, pd.DatetimeIndex):
                        for code in all_price_codes:
                            if code in open_df.columns:
                                val = open_df[code].iloc[0]
                                if not np.isnan(val):
                                    open_prices[code] = float(val)
                    elif hasattr(open_df, 'items'):
                        # Panel format: open_df.items = ['open'], open_df.major_axis = dates, open_df.minor_axis = codes
                        for code in all_price_codes:
                            if code in open_df.minor_axis:
                                val = open_df['open'][code].iloc[0]
                                if not np.isnan(val):
                                    open_prices[code] = float(val)
                except Exception as e2:
                    log.info('parse open prices failed: %s' % str(e2))
        except Exception as e:
            log.info('fetch open prices failed: %s, fallback to last_price' % str(e))

    # Compute total_value using open prices (aligned with engine total_value_open)
    cash = context.portfolio.available_cash
    positions_open_value = 0.0
    for code in all_position_codes:
        pos = context.portfolio.positions[code]
        if pos.total_amount > 0:
            price = open_prices.get(code, current_data[code].last_price)
            positions_open_value += pos.total_amount * price
    total_value = cash + positions_open_value
    log.info('total_value: %.2f' % total_value)

    # === Aligned with engine/__init__.py _on_trade_day ===
    # Iterate over ALL codes in target_weights + current positions
    all_codes = set(target_weights.keys()) | set(context.portfolio.positions.keys())

    sell_orders = []
    buy_orders = []
    limit_down_full_sells = []

    for code in all_codes:
        tw = target_weights.get(code, 0.0)
        pos = context.portfolio.positions.get(code)
        current_shares = pos.total_amount if pos and pos.total_amount > 0 else 0

        # Compute current_weight using open price (aligned with engine using open prices)
        current_value = 0.0
        current_weight = 0.0
        if current_shares > 0:
            open_price = open_prices.get(code)
            if open_price is None:
                continue
            current_value = current_shares * open_price
            current_weight = current_value / total_value if total_value > 0 else 0.0

        if tw < current_weight - 0.005:
            # Need to sell (partial or full)
            if current_shares <= 0:
                continue
            if current_data[code].paused:
                log.info('sell skip (paused): %s' % code)
                continue
            sell_price = open_prices.get(code)
            if sell_price is None:
                log.info('sell skip (no open price): %s' % code)
                continue
            if current_data[code].last_price <= current_data[code].low_limit:
                if tw <= 1e-6:
                    limit_down_full_sells.append(code)
                log.info('sell skip (limit_down): %s, will retry' % code)
                continue
            if tw <= 1e-6:
                sell_orders.append((code, current_shares))
            else:
                target_shares = _round_to_lot(code, int(tw * total_value / sell_price))
                shares_to_sell = max(0, current_shares - target_shares)
                shares_to_sell = _round_to_lot(code, shares_to_sell)
                if shares_to_sell > 0:
                    sell_orders.append((code, shares_to_sell))
                    log.info('partial sell: %s, current=%d, target=%d, sell=%d' % (code, current_shares, target_shares, shares_to_sell))

        elif tw > current_weight + 0.005:
            # Need to buy (partial or new)
            if current_data[code].paused:
                log.info('buy skip (paused): %s' % code)
                continue
            if current_data[code].last_price >= current_data[code].high_limit:
                log.info('buy skip (limit_up): %s' % code)
                continue
            buy_price = open_prices.get(code)
            if buy_price is None:
                continue
            target_value = tw * total_value
            delta = target_value - current_value
            if delta > 0:
                buy_orders.append((code, delta, buy_price))

    # Compute pending sell value and available cash
    pending_sell_value = 0.0
    for code, shares in sell_orders:
        sell_price = open_prices.get(code)
        if sell_price is not None:
            pending_sell_value += shares * sell_price

    available_cash = context.portfolio.available_cash
    reserved_cash = total_value * CASH_BUFFER_RATIO
    usable_cash = max(0, available_cash + pending_sell_value - reserved_cash)

    total_buy_value = sum(delta for _, delta, _ in buy_orders)
    scale = 1.0
    if total_buy_value > usable_cash and total_buy_value > 0:
        scale = usable_cash / total_buy_value
        log.info('cash scale: %.4f (buy_value=%.2f, available=%.2f)' % (scale, total_buy_value, usable_cash))

    # Execute sell orders
    sell_count = 0
    buy_count = 0
    for code, shares_to_sell in sell_orders:
        if code.startswith('688'):
            limit_price = current_data[code].last_price * 0.98
            order(code, -shares_to_sell, style=LimitOrderStyle(limit_price))
        else:
            order(code, -shares_to_sell)
        sell_count += 1
        log.info('sell order: %s, shares=%d' % (code, shares_to_sell))

    # Execute buy orders
    for code, delta, price in buy_orders:
        if current_data[code].paused:
            log.info('buy skip (paused): %s' % code)
            continue
        if current_data[code].last_price >= current_data[code].high_limit:
            log.info('buy skip (limit_up): %s' % code)
            continue
        adjusted_value = delta * scale
        buy_price = open_prices.get(code, current_data[code].last_price)
        price_for_calc = buy_price * 1.02 if code[:6].startswith('688') else buy_price
        actual_shares = _round_to_lot(code, int(adjusted_value / price_for_calc))
        if actual_shares <= 0:
            log.info('buy skip (shares < min_lot): %s, target_value=%.2f, price=%.4f' % (code, adjusted_value, buy_price))
            continue
        if code.startswith('688'):
            limit_price = buy_price * 1.02
            order(code, actual_shares, style=LimitOrderStyle(limit_price))
        else:
            order(code, actual_shares)
        buy_count += 1
        log.info('buy order: %s, shares=%d, value=%.2f' % (code, actual_shares, actual_shares * buy_price))

    # Handle limit down deferred sells
    for code in limit_down_full_sells:
        if not hasattr(g, 'pending_limit_down_sells'):
            g.pending_limit_down_sells = []
        g.pending_limit_down_sells.append(code)
        log.info('limit down sell deferred: %s' % code)

    g.stored_target_weights = {{}}
    log.info('rebalance done: sell=%d, buy=%d' % (sell_count, buy_count))
    retry_limit_down_sells(context)


def retry_limit_down_sells(context):
    '''跌停重试：每日开盘时重试之前因跌停未能卖出的股票'''
    if not hasattr(g, 'pending_limit_down_sells') or not g.pending_limit_down_sells:
        return
    current_data = get_current_data()
    # Fetch open prices for pending sells
    retry_codes = list(g.pending_limit_down_sells)
    retry_open_prices = {{}}
    today = context.current_dt.date()
    if retry_codes:
        try:
            retry_df = get_price(retry_codes, start_date=today, end_date=today,
                                 frequency='daily', fields=['open'], fq='pre')
            if retry_df is not None and not retry_df.empty:
                if isinstance(retry_df.index, pd.DatetimeIndex):
                    for code in retry_codes:
                        if code in retry_df.columns:
                            val = retry_df[code].iloc[0]
                            if not np.isnan(val):
                                retry_open_prices[code] = float(val)
        except Exception as e:
            log.info('retry fetch open prices failed: %s' % str(e))
    remaining = []
    for code in g.pending_limit_down_sells:
        if code not in context.portfolio.positions:
            continue
        if current_data[code].paused:
            remaining.append(code)
            continue
        if current_data[code].last_price <= current_data[code].low_limit:
            remaining.append(code)
            log.info('retry sell skip (still limit_down): %s' % code)
            continue
        retry_price = retry_open_prices.get(code)
        if retry_price is None:
            remaining.append(code)
            log.info('retry sell skip (no open price): %s' % code)
            continue
        pos = context.portfolio.positions[code]
        order(code, -pos.total_amount)
        log.info('retry sell success: %s' % code)
    g.pending_limit_down_sells = remaining
""")


def _detect_max_window(formula: str) -> int:
    """Scan formula for the largest rolling window argument."""
    max_window = 300
    try:
        tree = ast.parse(formula, mode="eval")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, int) and arg.value > 1:
                        max_window = max(max_window, arg.value + 10)
    except Exception:
        pass
    return max_window


def _generate_strategy_code(
    factor_name: str,
    formula: str,
    direction: int,
    analysis_cfg: dict[str, Any],
    backtest_cfg: dict[str, Any],
) -> str:
    # --- Extract analysis config ---
    stock_pool = analysis_cfg.get("stock_pool", {})
    index_code_local = str(stock_pool.get("index_code", "SH000300"))
    index_code_jq = _convert_index_code(index_code_local)
    dynamic_membership = bool(stock_pool.get("dynamic_membership", True))
    include_st = bool(stock_pool.get("include_st", False))
    new_stock_days = int(stock_pool.get("new_stock_days", 60))

    rebalance = str(analysis_cfg.get("rebalance", "weekly"))
    rebalance_anchor = str(analysis_cfg.get("rebalance_anchor", "first_trading_day_of_week"))

    # --- Extract backtest config ---
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
    buy_cost = float(execution_cfg.get("buy_cost", 0.0015))
    sell_cost = float(execution_cfg.get("sell_cost", 0.0025))
    stamp_duty = float(execution_cfg.get("stamp_duty", 0.001))
    slippage_val = float(execution_cfg.get("slippage", 0.0005))
    cash_buffer_ratio = float(execution_cfg.get("cash_buffer_ratio", 0.02))

    timing_reduce_to = float(market_timing_cfg.get("reduce_to", 0.5))
    ema_period = int(str(market_timing_cfg.get("market_indicator", "EMA_60")).replace("EMA_", "") or 60)
    stock_open_filter = str(market_timing_cfg.get("stock_open_filter", "rsi"))
    stock_ema_period = int(market_timing_cfg.get("stock_ema_period", 60))
    rsi_period = int(market_timing_cfg.get("rsi_period", 14))
    rsi_buy_max = float(market_timing_cfg.get("rsi_buy_max", 70.0))

    suspend_action = str(execution_cfg.get("suspend_action", "skip"))
    limit_up_action = str(execution_cfg.get("limit_up_action", "skip_buy"))
    limit_down_action = str(execution_cfg.get("limit_down_action", "delay_sell"))

    # --- Compute HISTORY_WINDOW ---
    history_window = _detect_max_window(formula)

    # --- Direction ---
    direction_label = "higher_better" if direction == 1 else "lower_better"
    direction_flip = ""
    if direction == -1:
        direction_flip = "            score = -score  # direction=-1: flip\n"

    # --- Rebalance comment ---
    if rebalance == "weekly":
        if rebalance_anchor == "first_trading_day_of_week":
            rebalance_comment = "每周首个交易日调仓"
        else:
            rebalance_comment = "每周最后一个交易日调仓"
    elif rebalance == "monthly":
        rebalance_comment = "每月调仓"
    else:
        rebalance_comment = "每日调仓"

    # --- Generate timing code ---
    timing_init_code = ""
    timing_code = ""
    if timing_enabled:
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

    # --- Generate timing filter code ---
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

    # --- Generate suspend/limit codes ---
    suspend_skip_code = ""
    if suspend_action == "skip":
        suspend_skip_code = "if current_data[code].paused: continue"

    limit_up_skip_code = ""
    if limit_up_action == "skip_buy":
        limit_up_skip_code = "if current_data[code].last_price >= current_data[code].high_limit: continue"

    limit_down_skip_code = ""
    if limit_down_action == "delay_sell":
        limit_down_skip_code = "if current_data[code].last_price <= current_data[code].low_limit: continue"

    # --- Generate strategy logic ---
    strategy_logic = ""
    if strategy_type == "TopKDropout":
        strategy_logic = _gen_topk_dropout_logic_aligned(
            buy_top_n, sell_drop_to, holding_count, max_drop_per_day,
            timing_enabled, timing_code, timing_filter_code,
        )
    elif strategy_type == "SoftTopK":
        strategy_logic = _gen_soft_topk_logic(
            soft_cfg, timing_enabled, timing_code, timing_filter_code,
            suspend_skip_code, limit_up_skip_code, limit_down_skip_code,
        )
    elif strategy_type == "EnhancedIndexing":
        strategy_logic = _gen_enhanced_indexing_logic(
            enhanced_cfg, timing_enabled, timing_filter_code,
            suspend_skip_code, limit_up_skip_code, limit_down_skip_code,
        )

    # --- Generate rebalance schedule ---
    retry_schedule = (
        "    run_daily(retry_limit_down_sells, time='09:30')"
        if limit_down_action == "delay_sell"
        else ""
    )

    rebalance_schedule = ""
    if rebalance == "weekly":
        if rebalance_anchor == "first_trading_day_of_week":
            rebalance_schedule = (
                "    run_weekly(compute_and_store_scores, weekday=1, time='15:01')\n"
                "    run_weekly(execute_trades, weekday=2, time='09:30')"
            )
        else:
            rebalance_schedule = (
                "    run_weekly(compute_and_store_scores, weekday=5, time='15:01')\n"
                "    run_weekly(execute_trades, weekday=1, time='09:30')"
            )
    elif rebalance == "monthly":
        rebalance_schedule = (
            "    run_monthly(compute_and_store_scores, monthday=1, time='15:01')\n"
            "    run_monthly(execute_trades, monthday=2, time='09:30')"
        )
    else:
        rebalance_schedule = (
            "    run_daily(compute_and_store_scores, time='15:01')\n"
            "    run_daily(execute_trades, time='09:30')"
        )

    # --- Assemble operator code ---
    base_operators = _generate_operator_defs()
    alpha191_operators = _generate_alpha191_operator_defs()

    # --- Assemble full strategy code ---
    code = textwrap.dedent(f"""\
# ============================================================
# JoinQuant Strategy Code for alpha191: {factor_name}
# Generated by step15_alpha191_export
# Formula: {formula}
# Direction: {direction_label}
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
HISTORY_WINDOW = {history_window}

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

# ==================== Operators (41 total: 25 base + 16 alpha191) ====================
{base_operators}

# === Alpha191 Operators (16) ===
{alpha191_operators}

# ==================== Factor Computation ====================
FACTOR_FORMULA = {repr(formula)}

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

    # Fetch benchmark index data
    bench = get_price(INDEX_CODE, end_date=context.current_dt, frequency='daily',
                      fields=['open', 'high', 'low', 'close'], count=HISTORY_WINDOW, fq='pre')
    if bench is None or bench.empty:
        log.info('benchmark data empty, skip')
        return {{}}
    bench = bench.sort_index()

    scores = {{}}
    log.info('compute_factor_scores: stocks count=%d' % len(stocks))
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

            # Compute auxiliary features
            hist['dtm'] = (hist['open'] - hist['open'].shift(1)).clip(lower=0)
            hist['dbm'] = (hist['open'].shift(1) - hist['open']).clip(lower=0)
            _hd_raw = (hist['high'] - hist['high'].shift(1)).clip(lower=0)
            _ld_raw = (hist['low'].shift(1) - hist['low']).clip(lower=0)
            hist['hd'] = _hd_raw.where(_hd_raw > _ld_raw, 0)
            hist['ld'] = _ld_raw.where(_ld_raw > _hd_raw, 0)
            _tr1 = hist['high'] - hist['low']
            _tr2 = (hist['high'] - hist['close'].shift(1)).abs()
            _tr3 = (hist['low'] - hist['close'].shift(1)).abs()
            hist['tr'] = pd.concat([_tr1, _tr2, _tr3], axis=1).max(axis=1)
            hist['ret_1d'] = hist['close'].pct_change(1, fill_method=None)

            # Add benchmark columns (aligned by date)
            hist['benchmark_open'] = bench['open'].reindex(hist.index)
            hist['benchmark_close'] = bench['close'].reindex(hist.index)
            hist['benchmark_high'] = bench['high'].reindex(hist.index)
            hist['benchmark_low'] = bench['low'].reindex(hist.index)

            # Set up eval env with all raw fields and operators
            env = {{col: hist[col] for col in hist.columns}}
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

    # === Diagnostic: log top/bottom scores ===
    if scores:
        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        log.info('=== [DIAG] date=%s, pool=%d, valid_scores=%d ===' % (context.current_dt.date(), len(stocks), len(scores)))
        log.info('[DIAG] top10: %s' % str([(c, round(v, 6)) for c, v in ranked[:10]]))
        log.info('[DIAG] bottom5: %s' % str([(c, round(v, 6)) for c, v in ranked[-5:]]))
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
    dt = context.current_dt.date()
    total = context.portfolio.total_value
    cash = context.portfolio.available_cash
    positions = context.portfolio.positions
    pos_count = len(positions)
    pos_value = sum(p.value for p in positions.values())
    log.info('[DIAG] daily date=%s, total=%.2f, cash=%.2f, positions_value=%.2f, pos_count=%d' % (dt, total, cash, pos_value, pos_count))
    if pos_count > 0:
        pos_detail = [(c, p.total_amount, round(p.value, 0)) for c, p in sorted(positions.items())[:10]]
        log.info('[DIAG] top10_positions: %s' % str(pos_detail))
""")
    return code


def run() -> None:
    parser = argparse.ArgumentParser(
        description="Generate JoinQuant strategy code for alpha191 factors"
    )
    parser.add_argument(
        "--alpha191", required=True,
        help="Path to alpha191_formulas.json",
    )
    parser.add_argument(
        "--factor", required=True,
        help="Factor name like 'alpha40'",
    )
    parser.add_argument(
        "--staging", required=False,
        help="Staging directory path (or set STAGING_DIR env var)",
    )
    args = parser.parse_args()

    factor_name = args.factor

    # Read alpha191 formulas
    alpha191_path = Path(args.alpha191)
    if not alpha191_path.exists():
        print(f"Error: alpha191 formulas file not found: {alpha191_path}")
        return

    formulas = read_json(alpha191_path)
    factor_item: dict[str, Any] | None = None
    for item in formulas:
        if str(item.get("name", "")) == factor_name:
            factor_item = dict(item)
            break

    if factor_item is None:
        print(f"Error: factor '{factor_name}' not found in {alpha191_path}")
        return

    formula = str(factor_item.get("formula", ""))
    direction = int(factor_item.get("direction", 1))

    if not formula:
        print(f"Error: factor '{factor_name}' has no formula")
        return

    print(f"Found alpha191 factor: {factor_name}")
    print(f"  formula: {formula}")
    print(f"  direction: {direction}")

    analysis_cfg = analysis_rule_config()
    backtest_cfg = backtest_rule_config()

    code = _generate_strategy_code(
        factor_name=factor_name,
        formula=formula,
        direction=direction,
        analysis_cfg=analysis_cfg,
        backtest_cfg=backtest_cfg,
    )

    output_dir = JOINQUANT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{factor_name}.py"
    output_path.write_text(code, encoding="utf-8")
    print(f"\nJoinQuant strategy code saved to: {output_path}")
    print("Copy this code to JoinQuant platform to run backtest.")


if __name__ == "__main__":
    run()
