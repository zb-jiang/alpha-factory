from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from common import (
    OUTPUT_DIR,
    analysis_rule_config,
    annualized_return,
    backtest_rule_config,
    compute_drawdown,
    env_config,
    feature_pool_config,
    load_raw_data,
    sharpe_ratio,
    weekly_rebalance_dates,
    write_table,
)


@dataclass
class StrategyResult:
    metrics: dict[str, float]
    positions: pd.DataFrame
    orders: pd.DataFrame
    nav: pd.DataFrame


def _next_available_date(all_dates: list[pd.Timestamp], date: pd.Timestamp) -> pd.Timestamp | None:
    for item in all_dates:
        if item > date:
            return item
    return None


def _resolve_trade_price_mode(rule: dict[str, float]) -> str:
    trade_price = str(rule.get("trade_price", "next_open")).strip().lower()
    if trade_price not in {"next_open", "next_close"}:
        raise ValueError(f"不支持的 trade_price 配置: {trade_price}")
    return trade_price


def _trade_date_from_signal(
    dates: list[pd.Timestamp],
    signal_date: pd.Timestamp,
    trade_price: str,
) -> pd.Timestamp | None:
    if trade_price == "next_open":
        return _next_available_date(dates, signal_date)
    if trade_price == "next_close":
        return _next_available_date(dates, signal_date)
    raise ValueError(f"不支持的 trade_price 配置: {trade_price}")


def _load_factor_values() -> pd.DataFrame:
    factor_values = pd.read_parquet(OUTPUT_DIR / "backtest" / "factor_values.parquet")
    index_columns = ["factor_name", "instrument", "datetime"]
    if set(index_columns).issubset(factor_values.columns):
        factor_values["datetime"] = pd.to_datetime(factor_values["datetime"])
        return factor_values.set_index(index_columns).sort_index()
    return factor_values.reset_index().set_index(index_columns).sort_index()


def simulate_factor(
    factor_name: str,
    factor_frame: pd.DataFrame,
    trade_prices: pd.DataFrame,
    rule: dict[str, float],
    is_negative_ic: bool = False,
) -> StrategyResult:
    dates = sorted(pd.to_datetime(trade_prices.index))
    rebalance_dates = weekly_rebalance_dates(
        factor_frame.index.get_level_values("datetime"),
        analysis_rule_config(),
    )
    holding_count = int(rule["holding_count"])
    buy_top_n = int(rule["buy_top_n"])
    sell_drop_to = int(rule["sell_drop_to"])
    buy_cost = float(rule["buy_cost"])
    sell_cost = float(rule["sell_cost"])
    slippage = float(rule["slippage"])
    trade_price = _resolve_trade_price_mode(rule)
    holdings: list[str] = []
    nav = 1.0
    nav_rows: list[dict[str, float]] = [{"datetime": dates[0], "nav": nav}]
    position_rows: list[dict[str, object]] = []
    order_rows: list[dict[str, object]] = []
    period_returns: list[float] = []

    for rebalance_date in rebalance_dates:
        trade_date = _trade_date_from_signal(dates, rebalance_date, trade_price)
        next_rebalance = _next_available_date(rebalance_dates, rebalance_date)
        exit_date = _trade_date_from_signal(dates, next_rebalance, trade_price) if next_rebalance is not None else dates[-1]
        if trade_date is None or exit_date is None or trade_date >= exit_date:
            continue
        factor_slice = factor_frame.xs(rebalance_date, level="datetime")
        score_column = "score" if "score" in factor_slice.columns else "raw_score"
        score_slice = factor_slice[score_column].dropna()
        if is_negative_ic:
            score_slice = -score_slice
        score_slice = score_slice.sort_values(ascending=False)
        ranked_codes = score_slice.index.tolist()
        keep_set = set(ranked_codes[:sell_drop_to])
        next_holdings = [code for code in holdings if code in keep_set]
        for code in ranked_codes[:buy_top_n]:
            if code not in next_holdings and len(next_holdings) < holding_count:
                next_holdings.append(code)
        buy_codes = [code for code in next_holdings if code not in holdings]
        sell_codes = [code for code in holdings if code not in next_holdings]
        for code in buy_codes:
            order_rows.append({"factor_name": factor_name, "rebalance_date": rebalance_date, "trade_date": trade_date, "action": "buy", "instrument": code})
        for code in sell_codes:
            order_rows.append({"factor_name": factor_name, "rebalance_date": rebalance_date, "trade_date": trade_date, "action": "sell", "instrument": code})
        holdings = next_holdings
        if not holdings:
            nav_rows.append({"datetime": exit_date, "nav": nav})
            continue
        start_price = trade_prices.loc[trade_date, holdings].dropna()
        end_price = trade_prices.loc[exit_date, start_price.index].dropna()
        aligned = pd.concat([start_price.rename("start"), end_price.rename("end")], axis=1).dropna()
        if aligned.empty:
            nav_rows.append({"datetime": exit_date, "nav": nav})
            continue
        gross_return = float((aligned["end"] / aligned["start"] - 1).mean())
        turnover = (len(buy_codes) + len(sell_codes)) / max(len(holdings), 1)
        cost = len(buy_codes) / max(len(holdings), 1) * buy_cost + len(sell_codes) / max(len(holdings), 1) * sell_cost
        cost += turnover * slippage
        net_return = gross_return - cost
        nav *= 1 + net_return
        period_returns.append(net_return)
        nav_rows.append({"datetime": exit_date, "nav": nav})
        for code in holdings:
            position_rows.append(
                {
                    "factor_name": factor_name,
                    "rebalance_date": rebalance_date,
                    "trade_date": trade_date,
                    "exit_date": exit_date,
                    "instrument": code,
                    "weight": 1 / len(holdings),
                }
            )

    nav_frame = pd.DataFrame(nav_rows).drop_duplicates(subset=["datetime"]).sort_values("datetime")
    nav_series = nav_frame.set_index("datetime")["nav"]
    returns = pd.Series(period_returns, dtype=float)
    metrics = {
        "factor_name": factor_name,
        "annualized_return": annualized_return(nav_series),
        "max_drawdown": compute_drawdown(nav_series),
        "sharpe": sharpe_ratio(returns),
        "turnover": float(returns.count() and (len(order_rows) / max(len(position_rows), 1)) or 0.0),
        "final_nav": float(nav_series.iloc[-1]) if not nav_series.empty else 1.0,
    }
    positions = pd.DataFrame(position_rows)
    orders = pd.DataFrame(order_rows)
    return StrategyResult(metrics=metrics, positions=positions, orders=orders, nav=nav_frame)


def run() -> None:
    config = env_config()
    feature_cfg = feature_pool_config()
    raw_frame = load_raw_data(config, list(feature_cfg.get("raw_fields", [])))

    if "factor" in raw_frame.columns:
        raw_frame["adj_open"] = raw_frame["open"] / raw_frame["factor"]
        raw_frame["adj_close"] = raw_frame["close"] / raw_frame["factor"]
    else:
        raw_frame["adj_open"] = raw_frame["open"]
        raw_frame["adj_close"] = raw_frame["close"]

    rule = backtest_rule_config()
    strategy_type = str(rule.get("strategy_type", "TopKDropout"))
    if strategy_type != "TopKDropout":
        raise NotImplementedError(
            f"step08_backtest.py 当前仅支持 strategy_type=TopKDropout，收到: {strategy_type}"
        )
    trade_price = _resolve_trade_price_mode(rule)
    price_field = "adj_close" if trade_price == "next_close" else "adj_open"
    trade_prices = (
        raw_frame[[price_field]]
        .reset_index()
        .pivot(index="datetime", columns="instrument", values=price_field)
        .sort_index()
    )
    factor_values = _load_factor_values()
    factor_metrics_path = OUTPUT_DIR / "backtest" / "factor_metrics.csv"
    if factor_metrics_path.exists():
        factor_metrics = pd.read_csv(factor_metrics_path).set_index("factor_name")
    else:
        factor_metrics = pd.DataFrame()
        
    min_rank_ic_to_backtest = float(config.get("min_rank_ic_to_backtest", 0.01))
    min_rank_ic_ir_to_backtest = float(config.get("min_rank_ic_ir_to_backtest", 0.1))
    min_positive_ic_ratio = float(config.get("min_positive_ic_ratio", 0.4))
    enable_direction_filter = bool(config.get("enable_direction_filter", False))
    metric_rows: list[dict[str, float]] = []
    position_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    nav_frames: list[pd.DataFrame] = []
    
    # 用于记录被淘汰的因子及其原因
    skipped_factors: list[dict[str, str]] = []
    
    for factor_name in factor_values.index.get_level_values("factor_name").unique():
        is_negative_ic = False
        if not factor_metrics.empty and factor_name in factor_metrics.index:
            mean_rank_ic = factor_metrics.loc[factor_name, "mean_rank_ic"]
            rank_ic_ir = factor_metrics.loc[factor_name, "rank_ic_ir"]
            pos_ratio = factor_metrics.loc[factor_name, "positive_ic_ratio"]
            empirical_direction = factor_metrics.loc[factor_name, "empirical_direction"]
            llm_direction = factor_metrics.loc[factor_name, "llm_direction"]
            
            # 使用 Rank IC 和 Rank IC IR 作为主要过滤指标（相比于普通 IC 更稳健）
            if abs(mean_rank_ic) < min_rank_ic_to_backtest:
                reason = f"Rank IC too low (|{mean_rank_ic:.4f}| < {min_rank_ic_to_backtest})"
                print(f"Skipping {factor_name}: {reason}")
                skipped_factors.append({"factor_name": factor_name, "reason": reason})
                continue
                
            if abs(rank_ic_ir) < min_rank_ic_ir_to_backtest:
                reason = f"Rank IC IR too low (|{rank_ic_ir:.4f}| < {min_rank_ic_ir_to_backtest})"
                print(f"Skipping {factor_name}: {reason}")
                skipped_factors.append({"factor_name": factor_name, "reason": reason})
                continue

            if enable_direction_filter and llm_direction != empirical_direction:
                reason = f"Direction mismatch (LLM={llm_direction}, empirical={empirical_direction})"
                print(f"Skipping {factor_name}: {reason}")
                skipped_factors.append({"factor_name": factor_name, "reason": reason})
                continue
                
            # 胜率过滤：如果是正向因子，胜率要大于阈值；如果是负向因子，(1 - 胜率)要大于阈值
            is_negative_ic = mean_rank_ic < 0
            win_rate = pos_ratio if not is_negative_ic else (1 - pos_ratio)
            if win_rate < min_positive_ic_ratio:
                reason = f"Directional win rate too low ({win_rate:.2f} < {min_positive_ic_ratio})"
                print(f"Skipping {factor_name}: {reason}")
                skipped_factors.append({"factor_name": factor_name, "reason": reason})
                continue

        factor_frame = factor_values.xs(factor_name, level="factor_name")
        result = simulate_factor(str(factor_name), factor_frame, trade_prices, rule, is_negative_ic)
        metric_rows.append(result.metrics)
        if not result.positions.empty:
            position_frames.append(result.positions)
        if not result.orders.empty:
            order_frames.append(result.orders)
        if not result.nav.empty:
            nav = result.nav.copy()
            nav["factor_name"] = factor_name
            nav_frames.append(nav)
    from common import write_json
    # 保存被淘汰因子的记录，供大模型下一轮参考
    write_json(OUTPUT_DIR / "backtest" / "skipped_factors.json", skipped_factors)

    if not metric_rows:
        print(f"警告: 所有 {len(skipped_factors)} 个因子都被筛选掉了，没有因子进入回测")
        print("请检查筛选阈值或因子质量：")
        print(f"  - min_rank_ic_to_backtest: {min_rank_ic_to_backtest}")
        print(f"  - min_rank_ic_ir_to_backtest: {min_rank_ic_ir_to_backtest}")
        print(f"  - min_positive_ic_ratio: {min_positive_ic_ratio}")
        metrics = pd.DataFrame()
        positions = pd.DataFrame()
        orders = pd.DataFrame()
        nav_curve = pd.DataFrame()
    else:
        metrics = pd.DataFrame(metric_rows).set_index("factor_name").sort_index()
        positions = pd.concat(position_frames, ignore_index=True) if position_frames else pd.DataFrame()
        orders = pd.concat(order_frames, ignore_index=True) if order_frames else pd.DataFrame()
        nav_curve = pd.concat(nav_frames, ignore_index=True) if nav_frames else pd.DataFrame()
    write_table(OUTPUT_DIR / "backtest" / "strategy_metrics.csv", metrics)
    write_table(OUTPUT_DIR / "backtest" / "positions.parquet", positions)
    write_table(OUTPUT_DIR / "backtest" / "orders.parquet", orders)
    write_table(OUTPUT_DIR / "backtest" / "nav_curve.parquet", nav_curve)
    print(f"backtest ok, factors={len(metrics)}")


if __name__ == "__main__":
    run()
