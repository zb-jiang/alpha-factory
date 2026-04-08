from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from common import (
    OUTPUT_DIR,
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


def simulate_factor(
    factor_name: str,
    factor_frame: pd.DataFrame,
    open_prices: pd.DataFrame,
    rule: dict[str, float],
    is_negative_ic: bool = False,
) -> StrategyResult:
    dates = sorted(pd.to_datetime(open_prices.index))
    rebalance_dates = weekly_rebalance_dates(factor_frame.index.get_level_values("datetime"))
    holding_count = int(rule["holding_count"])
    buy_top_n = int(rule["buy_top_n"])
    sell_drop_to = int(rule["sell_drop_to"])
    buy_cost = float(rule["buy_cost"])
    sell_cost = float(rule["sell_cost"])
    slippage = float(rule["slippage"])
    holdings: list[str] = []
    nav = 1.0
    nav_rows: list[dict[str, float]] = [{"datetime": dates[0], "nav": nav}]
    position_rows: list[dict[str, object]] = []
    order_rows: list[dict[str, object]] = []
    period_returns: list[float] = []

    for rebalance_date in rebalance_dates:
        trade_date = _next_available_date(dates, rebalance_date)
        next_rebalance = _next_available_date(rebalance_dates, rebalance_date)
        exit_date = _next_available_date(dates, next_rebalance) if next_rebalance is not None else dates[-1]
        if trade_date is None or exit_date is None or trade_date >= exit_date:
            continue
        score_slice = factor_frame.xs(rebalance_date, level="datetime")["score"].dropna()
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
        start_open = open_prices.loc[trade_date, holdings].dropna()
        end_open = open_prices.loc[exit_date, start_open.index].dropna()
        aligned = pd.concat([start_open.rename("start"), end_open.rename("end")], axis=1).dropna()
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
    else:
        raw_frame["adj_open"] = raw_frame["open"]
        
    open_prices = (
        raw_frame[["adj_open"]]
        .reset_index()
        .pivot(index="datetime", columns="instrument", values="adj_open")
        .sort_index()
    )
    factor_values = pd.read_parquet(OUTPUT_DIR / "backtest" / "factor_values.parquet")
    factor_values = factor_values.reset_index().set_index(["factor_name", "instrument", "datetime"]).sort_index()
    factor_metrics_path = OUTPUT_DIR / "backtest" / "factor_metrics.csv"
    if factor_metrics_path.exists():
        factor_metrics = pd.read_csv(factor_metrics_path).set_index("factor_name")
    else:
        factor_metrics = pd.DataFrame()
        
    min_rank_ic_to_backtest = float(config.get("min_rank_ic_to_backtest", 0.01))
    min_rank_ic_ir_to_backtest = float(config.get("min_rank_ic_ir_to_backtest", 0.1))
    min_positive_ic_ratio = float(config.get("min_positive_ic_ratio", 0.4))

    rule = backtest_rule_config()
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
                
            # 胜率过滤：如果是正向因子，胜率要大于阈值；如果是负向因子，(1 - 胜率)要大于阈值
            is_negative_ic = mean_rank_ic < 0
            win_rate = pos_ratio if not is_negative_ic else (1 - pos_ratio)
            if win_rate < min_positive_ic_ratio:
                reason = f"Directional win rate too low ({win_rate:.2f} < {min_positive_ic_ratio})"
                print(f"Skipping {factor_name}: {reason}")
                skipped_factors.append({"factor_name": factor_name, "reason": reason})
                continue

        factor_frame = factor_values.xs(factor_name, level="factor_name")
        result = simulate_factor(str(factor_name), factor_frame, open_prices, rule, is_negative_ic)
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
