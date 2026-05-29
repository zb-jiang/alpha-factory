from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from common import (
    OUTPUT_DIR,
    analysis_rule_config,
    annualized_return,
    backtest_rule_config,
    build_ohlcv_map,
    compute_drawdown,
    env_config,
    feature_pool_config,
    load_raw_data,
    log_step_end,
    log_step_start,
    weekly_rebalance_dates,
    write_json,
    write_table,
)
from engine import BacktestEngine, MarketData
from engine.enhanced_indexing import EnhancedIndexingStrategy
from engine.market_timing import MarketTimingFilter
from engine.soft_topk import SoftTopKStrategy
from engine.topk_dropout import TopKDropoutStrategy


def _setup_backtest_logging(factor_name: str, execution_cfg: dict[str, Any]) -> bool:
    logger = logging.getLogger("backtest_engine")
    logger.propagate = False
    logger.disabled = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    enable_detailed_log = bool(execution_cfg.get("enable_detailed_backtest_log", False))
    if not enable_detailed_log:
        logger.setLevel(logging.WARNING)
        return False

    log_path = OUTPUT_DIR / "backtest" / f"{factor_name}_backtest.log"
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(str(log_path), mode="w", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s  - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    return True


@dataclass
class BacktestDataBundle:
    factor_name: str
    frames_by_instrument: dict[str, pd.DataFrame]
    factor_scores: dict[str, pd.Series]


def _load_factor_values() -> pd.DataFrame:
    path = OUTPUT_DIR / "backtest" / "factor_values.parquet"
    factor_values = pd.read_parquet(path)
    index_columns = ["factor_name", "instrument", "datetime"]
    if set(index_columns).issubset(factor_values.columns):
        factor_values = factor_values.copy()
        factor_values["datetime"] = pd.to_datetime(factor_values["datetime"])
        return factor_values.set_index(index_columns).sort_index()
    return factor_values.reset_index().set_index(index_columns).sort_index()


def _select_factor_name(factor_values: pd.DataFrame, config: dict[str, Any]) -> str:
    configured = str(config.get("sample_factor_name", "") or "").strip()
    all_names = sorted(str(item) for item in factor_values.index.get_level_values("factor_name").unique())
    if not all_names:
        raise RuntimeError("factor_values.parquet 中没有任何 factor_name")
    if configured:
        if configured not in all_names:
            raise ValueError(f"sample_factor_name={configured} 不存在于 factor_values.parquet")
        return configured
    return all_names[0]


def _extract_score_series(
    factor_values: pd.DataFrame,
    factor_name: str,
    score_sign: float = 1.0,
) -> pd.Series:
    factor_frame = factor_values.xs(factor_name, level="factor_name")
    score_column = "score" if "score" in factor_frame.columns else "raw_score"
    if score_column not in factor_frame.columns:
        raise KeyError(f"{factor_name} 不存在可用打分列（score/raw_score）")
    series = pd.to_numeric(factor_frame[score_column], errors="coerce")
    sign = -1.0 if float(score_sign) < 0 else 1.0
    return (series * sign).rename("factor_score")


def _build_data_bundle_for_factor(
    factor_name: str,
    factor_values: pd.DataFrame,
    ohlcv_map: dict[str, pd.DataFrame],
    score_sign: float = 1.0,
) -> BacktestDataBundle:
    score_series = _extract_score_series(factor_values, factor_name, score_sign=score_sign)

    merged: dict[str, pd.DataFrame] = {}
    scores_by_instrument: dict[str, pd.Series] = {}

    for instrument, ohlcv in ohlcv_map.items():
        try:
            score = score_series.xs(instrument, level="instrument")
        except KeyError:
            continue
        frame = ohlcv.join(score, how="left")
        frame = frame.replace([np.inf, -np.inf], np.nan)
        frame = frame.dropna(subset=["open", "high", "low", "close"])
        if frame.empty:
            continue
        merged[instrument] = frame
        if "factor_score" in frame.columns:
            scores_by_instrument[instrument] = frame["factor_score"]

    if not merged:
        raise RuntimeError("没有可用的数据（行情与因子分合并后为空）")

    return BacktestDataBundle(
        factor_name=factor_name,
        frames_by_instrument=merged,
        factor_scores=scores_by_instrument,
    )


def build_data_bundle(config: dict[str, Any] | None = None) -> BacktestDataBundle:
    cfg = config or env_config()
    raw_fields = list(feature_pool_config().get("raw_fields", []))
    rule = backtest_rule_config()
    mt_cfg = dict(rule.get("MarketTiming", {}))
    mt_enabled = bool(mt_cfg.get("enabled", False))
    warmup = 0
    if mt_enabled:
        ema_p = int(str(mt_cfg.get("market_indicator", "EMA_60")).split("_")[-1])
        rsi_p = int(mt_cfg.get("rsi_period", 14))
        stock_ema_p = int(mt_cfg.get("stock_ema_period", 60))
        warmup = max(ema_p, rsi_p + 1, stock_ema_p) + 10
    raw_frame = load_raw_data(cfg, raw_fields, warmup_trading_days=warmup)
    ohlcv_map = build_ohlcv_map(raw_frame, cfg)
    factor_values = _load_factor_values()
    factor_name = _select_factor_name(factor_values, cfg)
    return _build_data_bundle_for_factor(factor_name, factor_values, ohlcv_map)


def _build_rebalance_date_set(bundle: BacktestDataBundle) -> set[str]:
    first_frame = next(iter(bundle.frames_by_instrument.values()))
    rebalance_dates = weekly_rebalance_dates(
        pd.Index(first_frame.index),
        analysis_rule_config(),
    )
    return {pd.Timestamp(item).strftime("%Y-%m-%d") for item in rebalance_dates}


def _build_strategy(rule: dict[str, Any]):
    strategy_type = str(rule.get("strategy_type", "TopKDropout") or "TopKDropout").strip()
    execution_cfg = dict(rule.get("Execution", {}))
    cash_buffer_ratio = float(execution_cfg.get("cash_buffer_ratio", 0.02))
    suspend_action = str(execution_cfg.get("suspend_action", "skip") or "skip").strip().lower()
    limit_up_action = str(execution_cfg.get("limit_up_action", "skip_buy") or "skip_buy").strip().lower()
    limit_down_action = str(execution_cfg.get("limit_down_action", "delay_sell") or "delay_sell").strip().lower()

    if strategy_type == "TopKDropout":
        topk_cfg = dict(rule.get("TopKDropout", {}))
        return TopKDropoutStrategy(
            buy_top_n=int(topk_cfg.get("buy_top_n", 20)),
            sell_drop_to=int(topk_cfg.get("sell_drop_to", 40)),
            holding_count=int(topk_cfg.get("holding_count", 20)),
            max_drop_per_day=int(topk_cfg.get("max_drop_per_day", 5)),
            min_score_coverage=float(topk_cfg.get("min_score_coverage", 0.90)),
            cash_buffer_ratio=cash_buffer_ratio,
            suspend_action=suspend_action,
            limit_up_action=limit_up_action,
            limit_down_action=limit_down_action,
        )

    if strategy_type == "SoftTopK":
        soft_cfg = dict(rule.get("SoftTopK", {}))
        return SoftTopKStrategy(
            holding_count=int(soft_cfg.get("holding_count", 30)),
            weight_func=str(soft_cfg.get("weight_func", "softmax") or "softmax").strip().lower(),
            softmax_temperature=float(soft_cfg.get("softmax_temperature", 1.0)),
            rank_power_alpha=float(soft_cfg.get("rank_power_alpha", 1.0)),
            min_score_coverage=float(soft_cfg.get("min_score_coverage", 0.90)),
            cash_buffer_ratio=cash_buffer_ratio,
            suspend_action=suspend_action,
            limit_up_action=limit_up_action,
            limit_down_action=limit_down_action,
        )

    if strategy_type == "EnhancedIndexing":
        enhanced_cfg = dict(rule.get("EnhancedIndexing", {}))
        return EnhancedIndexingStrategy(
            holding_count=int(enhanced_cfg.get("holding_count", 30)),
            active_weight_bound=float(enhanced_cfg.get("active_weight_bound", 0.02)),
            min_score_coverage=float(enhanced_cfg.get("min_score_coverage", 0.90)),
            cash_buffer_ratio=cash_buffer_ratio,
            suspend_action=suspend_action,
            limit_up_action=limit_up_action,
            limit_down_action=limit_down_action,
        )

    raise ValueError(f"不支持的 strategy_type: {strategy_type}")


def _build_market_timing(rule: dict[str, Any], ohlcv_map: dict[str, pd.DataFrame]) -> MarketTimingFilter | None:
    mt_cfg = dict(rule.get("MarketTiming", {}))
    enabled = bool(mt_cfg.get("enabled", False))
    if not enabled:
        return None
    return MarketTimingFilter(
        market_indicator=str(mt_cfg.get("market_indicator", "EMA_60")),
        reduce_to=float(mt_cfg.get("reduce_to", 0.5)),
        stock_open_filter=str(mt_cfg.get("stock_open_filter", "none")),
        stock_ema_period=int(mt_cfg.get("stock_ema_period", 60)),
        rsi_period=int(mt_cfg.get("rsi_period", 14)),
        rsi_buy_max=float(mt_cfg.get("rsi_buy_max", 70.0)),
        ohlcv_map=ohlcv_map,
    )


def run_backtest_batch_export() -> None:
    log_step_start("08", "因子回测")
    cfg = env_config()
    rule = backtest_rule_config()
    raw_fields = list(feature_pool_config().get("raw_fields", []))
    mt_cfg = dict(rule.get("MarketTiming", {}))
    mt_enabled = bool(mt_cfg.get("enabled", False))
    warmup = 0
    if mt_enabled:
        ema_p = int(str(mt_cfg.get("market_indicator", "EMA_60")).split("_")[-1])
        rsi_p = int(mt_cfg.get("rsi_period", 14))
        stock_ema_p = int(mt_cfg.get("stock_ema_period", 60))
        warmup = max(ema_p, rsi_p + 1, stock_ema_p) + 10
    raw_frame = load_raw_data(cfg, raw_fields, warmup_trading_days=warmup)
    ohlcv_map = build_ohlcv_map(raw_frame, cfg)
    factor_values = _load_factor_values()
    factor_names = [str(item) for item in factor_values.index.get_level_values("factor_name").unique()]
    if not factor_names:
        raise RuntimeError("factor_values.parquet 中没有可回测因子")
    factor_metrics_path = OUTPUT_DIR / "backtest" / "factor_metrics.csv"
    factor_metrics = pd.read_csv(factor_metrics_path).set_index("factor_name") if factor_metrics_path.exists() else pd.DataFrame()
    min_rank_ic_to_backtest = float(cfg.get("min_rank_ic_to_backtest", 0.01))
    min_rank_ic_ir_to_backtest = float(cfg.get("min_rank_ic_ir_to_backtest", 0.1))
    min_positive_ic_ratio = float(cfg.get("min_positive_ic_ratio", 0.4))
    enable_direction_filter = bool(cfg.get("enable_direction_filter", False))

    execution_cfg = dict(rule.get("Execution", {}))
    initial_cash = float(execution_cfg.get("initial_cash", 1_000_000.0))
    buy_cost = float(execution_cfg.get("buy_cost", 0.0015))
    sell_cost = float(execution_cfg.get("sell_cost", 0.0025))
    stamp_duty = float(execution_cfg.get("stamp_duty", 0.001))
    slippage = float(execution_cfg.get("slippage", 0.0005))
    cash_buffer_ratio = float(execution_cfg.get("cash_buffer_ratio", 0.02))

    metric_rows: list[dict[str, float]] = []
    nav_rows: list[dict[str, Any]] = []
    order_rows: list[dict[str, Any]] = []
    position_rows: list[dict[str, Any]] = []
    skipped_factors: list[dict[str, str]] = []

    for factor_name in factor_names:
        score_sign = 1.0
        try:
            if not factor_metrics.empty and factor_name in factor_metrics.index:
                mean_rank_ic = float(factor_metrics.loc[factor_name, "mean_rank_ic"])
                rank_ic_ir = float(factor_metrics.loc[factor_name, "rank_ic_ir"])
                pos_ratio = float(factor_metrics.loc[factor_name, "positive_ic_ratio"])
                empirical_direction = str(factor_metrics.loc[factor_name, "empirical_direction"])
                llm_direction = str(factor_metrics.loc[factor_name, "llm_direction"])
                if abs(mean_rank_ic) < min_rank_ic_to_backtest:
                    reason = f"Rank IC too low (|{mean_rank_ic:.4f}| < {min_rank_ic_to_backtest})"
                    skipped_factors.append({"factor_name": factor_name, "reason": reason})
                    continue
                if abs(rank_ic_ir) < min_rank_ic_ir_to_backtest:
                    reason = f"Rank IC IR too low (|{rank_ic_ir:.4f}| < {min_rank_ic_ir_to_backtest})"
                    skipped_factors.append({"factor_name": factor_name, "reason": reason})
                    continue
                if enable_direction_filter and llm_direction != empirical_direction:
                    reason = f"Direction mismatch (LLM={llm_direction}, empirical={empirical_direction})"
                    skipped_factors.append({"factor_name": factor_name, "reason": reason})
                    continue
                is_negative_ic = mean_rank_ic < 0
                score_sign = -1.0 if is_negative_ic else 1.0
                win_rate = pos_ratio if not is_negative_ic else (1 - pos_ratio)
                if win_rate < min_positive_ic_ratio:
                    reason = f"Directional win rate too low ({win_rate:.2f} < {min_positive_ic_ratio})"
                    skipped_factors.append({"factor_name": factor_name, "reason": reason})
                    continue

            bundle = _build_data_bundle_for_factor(factor_name, factor_values, ohlcv_map, score_sign=score_sign)
            rebalance_dates = _build_rebalance_date_set(bundle)
            strategy = _build_strategy(rule)

            market_data = MarketData(
                ohlcv_map=bundle.frames_by_instrument,
                factor_scores=bundle.factor_scores,
            )

            market_timing = _build_market_timing(rule, bundle.frames_by_instrument)

            engine = BacktestEngine(
                market_data=market_data,
                strategy=strategy,
                initial_cash=initial_cash,
                buy_cost=buy_cost,
                sell_cost=sell_cost,
                stamp_duty=stamp_duty,
                slippage=slippage,
                cash_buffer_ratio=cash_buffer_ratio,
                market_timing=market_timing,
            )

            detailed_log_enabled = _setup_backtest_logging(factor_name, execution_cfg)
            if detailed_log_enabled:
                print(f"  回测: {factor_name} ({strategy.__class__.__name__}, 详细日志=开启)")
            else:
                print(f"  回测: {factor_name} ({strategy.__class__.__name__})")
            results = engine.run(rebalance_dates)

            metrics = results["metrics"]
            metrics["factor_name"] = factor_name
            metric_rows.append(metrics)

            nav_df = results["nav_curve"]
            if not nav_df.empty:
                nav_df["factor_name"] = factor_name
                nav_rows.extend(nav_df.to_dict(orient="records"))

            orders_df = results["orders"]
            if not orders_df.empty:
                orders_df["factor_name"] = factor_name
                order_rows.extend(orders_df.to_dict(orient="records"))

            positions_df = results["positions"]
            if not positions_df.empty:
                positions_df["factor_name"] = factor_name
                position_rows.extend(positions_df.to_dict(orient="records"))

        except Exception as exc:
            skipped_factors.append({"factor_name": factor_name, "reason": str(exc)})
            print(f"  跳过: {factor_name} ({exc})")

    metrics_df = pd.DataFrame(metric_rows).set_index("factor_name") if metric_rows else pd.DataFrame()
    nav_curve = pd.DataFrame(nav_rows) if nav_rows else pd.DataFrame()
    orders = pd.DataFrame(order_rows) if order_rows else pd.DataFrame()
    positions = pd.DataFrame(position_rows) if position_rows else pd.DataFrame()
    write_table(OUTPUT_DIR / "backtest" / "strategy_metrics.csv", metrics_df)
    write_table(OUTPUT_DIR / "backtest" / "positions.parquet", positions)
    write_table(OUTPUT_DIR / "backtest" / "orders.parquet", orders)
    write_table(OUTPUT_DIR / "backtest" / "nav_curve.parquet", nav_curve)
    write_json(OUTPUT_DIR / "backtest" / "skipped_factors.json", skipped_factors)
    log_step_end("08", "回测完成", details=[f"回测因子: {len(metrics_df)} 个, 跳过: {len(skipped_factors)} 个"])


if __name__ == "__main__":
    run_backtest_batch_export()
