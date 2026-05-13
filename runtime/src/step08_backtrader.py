from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    weekly_rebalance_dates,
    write_table,
)
from strategies import (
    EnhancedIndexingStrategy,
    SBBEMAEnhancedIndexingStrategy,
    SBBEMASoftTopkStrategy,
    SBBStrategyEMA,
    SoftTopkStrategy,
    TopkDropoutStrategy,
)

try:
    import backtrader as bt
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError(
        "缺少依赖 backtrader，请先执行: python -m pip install backtrader"
    ) from exc


@dataclass
class BacktraderDataBundle:
    factor_name: str
    benchmark_frame: pd.DataFrame
    frames_by_instrument: dict[str, pd.DataFrame]


class FactorPandasData(bt.feeds.PandasData):
    lines = ("factor_score",)
    params = (
        ("datetime", None),
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("close", "close"),
        ("volume", "volume"),
        ("openinterest", "openinterest"),
        ("factor_score", "factor_score"),
    )


class BuySellCommissionInfo(bt.CommInfoBase):
    params = (
        ("buy_cost", 0.0015),
        ("sell_cost", 0.0025),
        ("stamp_duty", 0.001),
    )

    def _getcommission(self, size: float, price: float, pseudoexec: bool) -> float:
        rate = self.p.buy_cost if size > 0 else self.p.sell_cost
        commission = abs(size) * price * rate
        # 卖出时加收印花税
        if size < 0:
            commission += abs(size) * price * self.p.stamp_duty
        return commission


class TurnoverAnalyzer(bt.Analyzer):
    def start(self) -> None:
        self._trade_value = 0.0
        self._portfolio_values: list[float] = []

    def notify_order(self, order: bt.Order) -> None:
        if order.status == order.Completed:
            self._trade_value += abs(float(order.executed.value or 0.0))

    def next(self) -> None:
        self._portfolio_values.append(float(self.strategy.broker.getvalue()))

    def get_analysis(self) -> dict[str, float]:
        if not self._portfolio_values:
            return {"turnover": 0.0}
        avg_value = float(np.mean(self._portfolio_values) or 0.0)
        if avg_value <= 0:
            return {"turnover": 0.0}
        return {"turnover": float(self._trade_value / avg_value)}


class OrderRecorderAnalyzer(bt.Analyzer):
    def start(self) -> None:
        self._rows: list[dict[str, Any]] = []

    def notify_order(self, order: bt.Order) -> None:
        if order.status != order.Completed:
            return
        dt = bt.num2date(order.executed.dt).strftime("%Y-%m-%d") if order.executed.dt else ""
        size = float(order.executed.size or 0.0)
        action = "buy" if size > 0 else "sell"
        self._rows.append(
            {
                "trade_date": dt,
                "action": action,
                "instrument": str(order.data._name),
                "size": abs(size),
                "price": float(order.executed.price or 0.0),
                "value": abs(float(order.executed.value or 0.0)),
                "commission": float(order.executed.comm or 0.0),
            }
        )

    def get_analysis(self) -> dict[str, Any]:
        return {"orders": self._rows}


class DummyStrategy(bt.Strategy):
    params = (("max_print_rows", 10),)

    def __init__(self) -> None:
        self._printed_rows = 0

    def next(self) -> None:
        if self._printed_rows >= int(self.p.max_print_rows):
            return
        data0 = self.datas[0]
        score = float(data0.factor_score[0])
        if np.isnan(score):
            raise ValueError("DummyStrategy 检测到 factor_score 为 NaN，数据注入失败")
        close = float(data0.close[0])
        dt = bt.num2date(data0.datetime[0]).strftime("%Y-%m-%d")
        print(f"[Dummy] date={dt}, instrument={data0._name}, close={close:.4f}, factor_score={score:.6f}")
        self._printed_rows += 1


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


def _build_ohlcv_by_instrument(raw_frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    required = ["open", "high", "low", "close", "volume"]
    missing = [item for item in required if item not in raw_frame.columns]
    if missing:
        raise KeyError(f"原始行情缺少必要列: {missing}")

    result: dict[str, pd.DataFrame] = {}
    for instrument, group in raw_frame.groupby(level="instrument", sort=False):
        frame = group.droplevel("instrument")[required].copy().sort_index()
        # 对 OHLCV 做最小清洗，确保可注入 Backtrader
        frame["high"] = frame["high"].fillna(frame["close"])
        frame["low"] = frame["low"].fillna(frame["close"])
        frame["open"] = frame["open"].fillna(frame["close"])
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
        frame = frame[~frame.index.duplicated(keep="first")]
        result[str(instrument)] = frame
    return result


def _build_backtrader_bundle_for_factor(
    factor_name: str,
    factor_values: pd.DataFrame,
    ohlcv_map: dict[str, pd.DataFrame],
    score_sign: float = 1.0,
) -> BacktraderDataBundle:
    score_series = _extract_score_series(factor_values, factor_name, score_sign=score_sign)

    merged: dict[str, pd.DataFrame] = {}
    for instrument, ohlcv in ohlcv_map.items():
        try:
            score = score_series.xs(instrument, level="instrument")
        except KeyError:
            continue
        frame = ohlcv.join(score, how="left")
        frame = frame.replace([np.inf, -np.inf], np.nan)
        frame["openinterest"] = 0.0
        frame = frame.dropna(subset=["open", "high", "low", "close", "factor_score"])
        if frame.empty:
            continue
        merged[instrument] = frame

    if not merged:
        raise RuntimeError("没有可用的 DataFeed（行情与因子分合并后为空）")

    benchmark_frame = (
        pd.concat(merged.values(), axis=0)
        .groupby(level=0)[["open", "high", "low", "close", "volume", "openinterest"]]
        .mean()
        .sort_index()
    )
    benchmark_frame["factor_score"] = 0.0
    return BacktraderDataBundle(
        factor_name=factor_name,
        benchmark_frame=benchmark_frame,
        frames_by_instrument=merged,
    )


def build_backtrader_bundle(config: dict[str, Any] | None = None) -> BacktraderDataBundle:
    cfg = config or env_config()
    raw_fields = list(feature_pool_config().get("raw_fields", []))
    raw_frame = load_raw_data(cfg, raw_fields)
    ohlcv_map = _build_ohlcv_by_instrument(raw_frame)
    factor_values = _load_factor_values()
    factor_name = _select_factor_name(factor_values, cfg)
    return _build_backtrader_bundle_for_factor(factor_name, factor_values, ohlcv_map)


def build_cerebro(rule: dict[str, Any] | None = None) -> bt.Cerebro:
    cfg = rule or backtest_rule_config()
    cerebro = bt.Cerebro()
    initial_cash = float(cfg.get("initial_cash", 1_000_000.0))
    if initial_cash <= 0:
        raise ValueError(f"initial_cash 必须大于 0，收到: {initial_cash}")
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.addcommissioninfo(
        BuySellCommissionInfo(
            buy_cost=float(cfg.get("buy_cost", 0.0015)),
            sell_cost=float(cfg.get("sell_cost", 0.0025)),
            stamp_duty=float(cfg.get("stamp_duty", 0.001)),
        )
    )
    slippage = float(cfg.get("slippage", 0.0005))
    if slippage > 0:
        cerebro.broker.set_slippage_perc(perc=slippage)
    return cerebro


def _add_analyzers(cerebro: bt.Cerebro) -> None:
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", timeframe=bt.TimeFrame.Days, annualize=True)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="timereturn")
    cerebro.addanalyzer(TurnoverAnalyzer, _name="turnover")
    cerebro.addanalyzer(OrderRecorderAnalyzer, _name="orders")


def _build_rebalance_date_set(bundle: BacktraderDataBundle) -> set[str]:
    first_frame = next(iter(bundle.frames_by_instrument.values()))
    rebalance_dates = weekly_rebalance_dates(
        pd.Index(first_frame.index),
        analysis_rule_config(),
    )
    return {pd.Timestamp(item).strftime("%Y-%m-%d") for item in rebalance_dates}


def run_dummy(max_print_rows: int = 10) -> None:
    rule = backtest_rule_config()
    bundle = build_backtrader_bundle(env_config())
    cerebro = build_cerebro(rule)
    benchmark_data = FactorPandasData(dataname=bundle.benchmark_frame, name="__benchmark__")
    cerebro.adddata(benchmark_data)
    for instrument, frame in bundle.frames_by_instrument.items():
        data = FactorPandasData(dataname=frame, name=instrument)
        cerebro.adddata(data)
    cerebro.addstrategy(DummyStrategy, max_print_rows=max_print_rows)
    print(
        f"backtrader dummy run start: factor={bundle.factor_name}, "
        f"instruments={len(bundle.frames_by_instrument)}"
    )
    cerebro.run(runonce=False)
    print("backtrader dummy run ok")


def run_topkdropout_validation(max_rebalance_logs: int = 20) -> None:
    rule = backtest_rule_config()
    strategy_type = str(rule.get("strategy_type", "TopKDropout"))
    if strategy_type != "TopKDropout":
        raise NotImplementedError(
            f"当前阶段三仅实现 TopKDropoutStrategy，收到 strategy_type={strategy_type}"
        )

    bundle = build_backtrader_bundle(env_config())
    cerebro = build_cerebro(rule)
    benchmark_data = FactorPandasData(dataname=bundle.benchmark_frame, name="__benchmark__")
    cerebro.adddata(benchmark_data)
    for instrument, frame in bundle.frames_by_instrument.items():
        data = FactorPandasData(dataname=frame, name=instrument)
        cerebro.adddata(data)

    topk_cfg = dict(rule.get("TopKDropout", {}))
    execution_cfg = dict(rule.get("Execution", {}))
    rebalance_dates = _build_rebalance_date_set(bundle)
    cerebro.addstrategy(
        TopkDropoutStrategy,
        buy_top_n=int(topk_cfg.get("buy_top_n", rule.get("buy_top_n", 20))),
        sell_drop_to=int(topk_cfg.get("sell_drop_to", rule.get("sell_drop_to", 40))),
        holding_count=int(topk_cfg.get("holding_count", rule.get("holding_count", 20))),
        weight_mode=str(topk_cfg.get("weight_mode", "equal_weight") or "equal_weight").strip().lower(),
        max_drop_per_day=int(topk_cfg.get("max_drop_per_day", 5)),
        rebalance_dates=rebalance_dates,
        strict_assertions=True,
        max_rebalance_logs=max_rebalance_logs,
        benchmark_name="__benchmark__",
        suspend_action=str(execution_cfg.get("suspend_action", "skip") or "skip").strip().lower(),
        limit_up_action=str(execution_cfg.get("limit_up_action", "skip_buy") or "skip_buy").strip().lower(),
        limit_down_action=str(execution_cfg.get("limit_down_action", "delay_sell") or "delay_sell").strip().lower(),
    )
    print(
        f"topkdropout validation run start: factor={bundle.factor_name}, "
        f"instruments={len(bundle.frames_by_instrument)}, "
        f"rebalance_dates={len(rebalance_dates)}"
    )
    cerebro.run(runonce=False)
    print("topkdropout validation run ok")


def run_sbb_ema_validation(max_rebalance_logs: int = 20) -> None:
    rule = backtest_rule_config()
    strategy_type = str(rule.get("strategy_type", "TopKDropout"))
    if strategy_type != "TopKDropout":
        raise NotImplementedError(
            f"阶段四基于 TopKDropout + 择时过滤，当前 strategy_type 应为 TopKDropout，收到: {strategy_type}"
        )
    market_timing = dict(rule.get("MarketTiming", {}))
    market_indicator = str(
        market_timing.get("market_indicator", market_timing.get("indicator", "EMA_60")) or "EMA_60"
    ).strip().upper()
    if market_indicator != "EMA_60":
        raise NotImplementedError(f"当前仅支持 EMA_60，收到: {market_indicator}")

    bundle = build_backtrader_bundle(env_config())
    cerebro = build_cerebro(rule)
    benchmark_data = FactorPandasData(dataname=bundle.benchmark_frame, name="__benchmark__")
    cerebro.adddata(benchmark_data)
    for instrument, frame in bundle.frames_by_instrument.items():
        data = FactorPandasData(dataname=frame, name=instrument)
        cerebro.adddata(data)

    topk_cfg = dict(rule.get("TopKDropout", {}))
    execution_cfg = dict(rule.get("Execution", {}))
    rebalance_dates = _build_rebalance_date_set(bundle)
    reduce_to = float(market_timing.get("reduce_to", 0.5))
    # 阶段四验收要求是“开启 MarketTiming”，此处以运行参数强制打开，避免依赖 YAML 当前开关状态。
    cerebro.addstrategy(
        SBBStrategyEMA,
        buy_top_n=int(topk_cfg.get("buy_top_n", rule.get("buy_top_n", 20))),
        sell_drop_to=int(topk_cfg.get("sell_drop_to", rule.get("sell_drop_to", 40))),
        holding_count=int(topk_cfg.get("holding_count", rule.get("holding_count", 20))),
        weight_mode=str(topk_cfg.get("weight_mode", "equal_weight") or "equal_weight").strip().lower(),
        max_drop_per_day=int(topk_cfg.get("max_drop_per_day", 5)),
        rebalance_dates=rebalance_dates,
        strict_assertions=True,
        max_rebalance_logs=max_rebalance_logs,
        benchmark_name="__benchmark__",
        ema_period=60,
        timing_reduce_to=reduce_to,
        timing_strict_assertions=True,
        suspend_action=str(execution_cfg.get("suspend_action", "skip") or "skip").strip().lower(),
        limit_up_action=str(execution_cfg.get("limit_up_action", "skip_buy") or "skip_buy").strip().lower(),
        limit_down_action=str(execution_cfg.get("limit_down_action", "delay_sell") or "delay_sell").strip().lower(),
    )
    print(
        f"sbb_ema validation run start: factor={bundle.factor_name}, "
        f"instruments={len(bundle.frames_by_instrument)}, "
        f"rebalance_dates={len(rebalance_dates)}, "
        f"timing_reduce_to={reduce_to:.3f}"
    )
    cerebro.run(runonce=False)
    print("sbb_ema validation run ok")


def _build_strategy_kwargs(rule: dict[str, Any], rebalance_dates: set[str]) -> tuple[type[bt.Strategy], dict[str, Any]]:
    strategy_type = str(rule.get("strategy_type", "TopKDropout") or "TopKDropout").strip()
    market_timing = dict(rule.get("MarketTiming", {}))
    timing_enabled = bool(market_timing.get("enabled", False))
    market_indicator = str(
        market_timing.get("market_indicator", market_timing.get("indicator", "EMA_60")) or "EMA_60"
    ).strip().upper()
    if timing_enabled and market_indicator != "EMA_60":
        raise NotImplementedError(f"当前仅支持 EMA_60，收到: {market_indicator}")
    timing_kwargs = (
        {
            "ema_period": 60,
            "timing_reduce_to": float(market_timing.get("reduce_to", 0.5)),
            "stock_open_filter": str(market_timing.get("stock_open_filter", "rsi") or "rsi").strip().lower(),
            "stock_ema_period": int(market_timing.get("stock_ema_period", 60) or 60),
            "rsi_period": int(market_timing.get("rsi_period", 14) or 14),
            "rsi_buy_max": float(market_timing.get("rsi_buy_max", 70.0) or 70.0),
        }
        if timing_enabled
        else {}
    )
    topk_cfg = dict(rule.get("TopKDropout", {}))
    trade_price_mode = str(rule.get("trade_price", "next_open") or "next_open").strip().lower()
    execution_constraint_kwargs: dict[str, Any] = {
        "suspend_action": str(rule.get("suspend_action", "skip") or "skip").strip().lower(),
        "limit_up_action": str(rule.get("limit_up_action", "skip_buy") or "skip_buy").strip().lower(),
        "limit_down_action": str(rule.get("limit_down_action", "delay_sell") or "delay_sell").strip().lower(),
    }
    topk_kwargs: dict[str, Any] = {
        "buy_top_n": int(topk_cfg.get("buy_top_n", rule.get("buy_top_n", 20))),
        "sell_drop_to": int(topk_cfg.get("sell_drop_to", rule.get("sell_drop_to", 40))),
        "holding_count": int(topk_cfg.get("holding_count", rule.get("holding_count", 20))),
        "weight_mode": str(topk_cfg.get("weight_mode", "equal_weight") or "equal_weight").strip().lower(),
        "max_drop_per_day": int(topk_cfg.get("max_drop_per_day", 5)),
        "min_score_coverage": float(topk_cfg.get("min_score_coverage", 0.90)),
        "cash_buffer_ratio": float(rule.get("cash_buffer_ratio", 0.02)),
        "rebalance_dates": rebalance_dates,
        "strict_assertions": False,
        "max_rebalance_logs": 0,
        "benchmark_name": "__benchmark__",
        "trade_price_mode": trade_price_mode,
        **execution_constraint_kwargs,
    }
    if strategy_type == "TopKDropout":
        if timing_enabled:
            topk_kwargs.update({**timing_kwargs, "timing_strict_assertions": False})
            return SBBStrategyEMA, topk_kwargs
        return TopkDropoutStrategy, topk_kwargs

    if strategy_type == "SoftTopK":
        soft_cfg = dict(rule.get("SoftTopK", {}))
        kwargs: dict[str, Any] = {
            "top_n": int(soft_cfg.get("top_n", 30)),
            "holding_count": int(soft_cfg.get("holding_count", 30)),
            "weight_func": str(soft_cfg.get("weight_func", "softmax") or "softmax").strip().lower(),
            "min_score_coverage": float(soft_cfg.get("min_score_coverage", 0.90)),
            "cash_buffer_ratio": float(rule.get("cash_buffer_ratio", 0.02)),
            "rebalance_dates": rebalance_dates,
            "strict_assertions": False,
            "max_rebalance_logs": 0,
            "benchmark_name": "__benchmark__",
            "trade_price_mode": trade_price_mode,
            **execution_constraint_kwargs,
        }
        if timing_enabled:
            kwargs.update(timing_kwargs)
            return SBBEMASoftTopkStrategy, kwargs
        return SoftTopkStrategy, kwargs

    if strategy_type == "EnhancedIndexing":
        enhanced_cfg = dict(rule.get("EnhancedIndexing", {}))
        kwargs = {
            "holding_count": int(enhanced_cfg.get("holding_count", 30)),
            "active_weight_bound": float(enhanced_cfg.get("active_weight_bound", 0.02)),
            "benchmark_weights": dict(rule.get("benchmark_weights", {})) if rule.get("benchmark_weights") else None,
            "min_score_coverage": float(enhanced_cfg.get("min_score_coverage", 0.90)),
            "cash_buffer_ratio": float(rule.get("cash_buffer_ratio", 0.02)),
            "rebalance_dates": rebalance_dates,
            "strict_assertions": False,
            "max_rebalance_logs": 0,
            "benchmark_name": "__benchmark__",
            "trade_price_mode": trade_price_mode,
            **execution_constraint_kwargs,
        }
        if timing_enabled:
            kwargs.update(timing_kwargs)
            return SBBEMAEnhancedIndexingStrategy, kwargs
        return EnhancedIndexingStrategy, kwargs

    raise NotImplementedError(f"未实现的 strategy_type: {strategy_type}")


def _nav_from_timereturn(returns_map: dict[Any, float]) -> pd.Series:
    if not returns_map:
        return pd.Series(dtype=float)
    returns = pd.Series({pd.Timestamp(key): float(value) for key, value in returns_map.items()}).sort_index()
    if returns.empty:
        return pd.Series(dtype=float)
    nav = (1.0 + returns).cumprod()
    nav.name = "nav"
    return nav


def run_backtrader_batch_export() -> None:
    cfg = env_config()
    rule = backtest_rule_config()
    raw_fields = list(feature_pool_config().get("raw_fields", []))
    raw_frame = load_raw_data(cfg, raw_fields)
    ohlcv_map = _build_ohlcv_by_instrument(raw_frame)
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
                    print(f"skip factor={factor_name}, reason={reason}")
                    continue
                if abs(rank_ic_ir) < min_rank_ic_ir_to_backtest:
                    reason = f"Rank IC IR too low (|{rank_ic_ir:.4f}| < {min_rank_ic_ir_to_backtest})"
                    skipped_factors.append({"factor_name": factor_name, "reason": reason})
                    print(f"skip factor={factor_name}, reason={reason}")
                    continue
                if enable_direction_filter and llm_direction != empirical_direction:
                    reason = f"Direction mismatch (LLM={llm_direction}, empirical={empirical_direction})"
                    skipped_factors.append({"factor_name": factor_name, "reason": reason})
                    print(f"skip factor={factor_name}, reason={reason}")
                    continue
                is_negative_ic = mean_rank_ic < 0
                score_sign = -1.0 if is_negative_ic else 1.0
                win_rate = pos_ratio if not is_negative_ic else (1 - pos_ratio)
                if win_rate < min_positive_ic_ratio:
                    reason = f"Directional win rate too low ({win_rate:.2f} < {min_positive_ic_ratio})"
                    skipped_factors.append({"factor_name": factor_name, "reason": reason})
                    print(f"skip factor={factor_name}, reason={reason}")
                    continue
            bundle = _build_backtrader_bundle_for_factor(factor_name, factor_values, ohlcv_map, score_sign=score_sign)
            rebalance_dates = _build_rebalance_date_set(bundle)
            strategy_cls, kwargs = _build_strategy_kwargs(rule, rebalance_dates)
            cerebro = build_cerebro(rule)
            _add_analyzers(cerebro)
            benchmark_data = FactorPandasData(dataname=bundle.benchmark_frame, name="__benchmark__")
            cerebro.adddata(benchmark_data)
            for instrument, frame in bundle.frames_by_instrument.items():
                data = FactorPandasData(dataname=frame, name=instrument)
                cerebro.adddata(data)
            cerebro.addstrategy(strategy_cls, **kwargs)
            print(f"backtrader batch run: factor={factor_name}, strategy={strategy_cls.__name__}")
            strategies = cerebro.run(runonce=False)
            strategy = strategies[0]
            sharpe_val = float(strategy.analyzers.sharpe.get_analysis().get("sharperatio", 0.0) or 0.0)
            drawdown_pct = float(
                strategy.analyzers.drawdown.get_analysis().get("max", {}).get("drawdown", 0.0) or 0.0
            )
            time_returns = strategy.analyzers.timereturn.get_analysis()
            nav_series = _nav_from_timereturn(time_returns)
            turnover_val = float(strategy.analyzers.turnover.get_analysis().get("turnover", 0.0) or 0.0)
            completed_orders = list(strategy.analyzers.orders.get_analysis().get("orders", []))
            final_nav = float(nav_series.iloc[-1]) if not nav_series.empty else 1.0
            max_dd = compute_drawdown(nav_series) if not nav_series.empty else float(-drawdown_pct / 100.0)
            metric_rows.append(
                {
                    "factor_name": factor_name,
                    "annualized_return": annualized_return(nav_series) if not nav_series.empty else 0.0,
                    "max_drawdown": float(max_dd),
                    "sharpe": sharpe_val,
                    "sharpe_ratio": sharpe_val,
                    "turnover": turnover_val,
                    "final_nav": final_nav,
                }
            )
            if not nav_series.empty:
                nav_df = nav_series.reset_index()
                nav_df.columns = ["datetime", "nav"]
                nav_df["factor_name"] = factor_name
                nav_rows.extend(nav_df.to_dict(orient="records"))
            if completed_orders:
                for row in completed_orders:
                    row["factor_name"] = factor_name
                    order_rows.append(row)
            portfolio_value = float(strategy.broker.getvalue() or 0.0)
            for data in strategy.datas:
                code = str(data._name)
                if code == "__benchmark__":
                    continue
                position = strategy.getposition(data)
                size = float(position.size or 0.0)
                if size == 0:
                    continue
                price = float(data.close[0] or 0.0)
                value = size * price
                weight = value / portfolio_value if portfolio_value > 0 else 0.0
                position_rows.append(
                    {
                        "factor_name": factor_name,
                        "datetime": bt.num2date(data.datetime[0]).strftime("%Y-%m-%d"),
                        "instrument": code,
                        "size": size,
                        "price": price,
                        "value": value,
                        "weight": weight,
                    }
                )
        except Exception as exc:
            skipped_factors.append({"factor_name": factor_name, "reason": str(exc)})
            print(f"skip factor={factor_name}, reason={exc}")

    metrics = pd.DataFrame(metric_rows).set_index("factor_name") if metric_rows else pd.DataFrame()
    nav_curve = pd.DataFrame(nav_rows) if nav_rows else pd.DataFrame()
    orders = pd.DataFrame(order_rows) if order_rows else pd.DataFrame()
    positions = pd.DataFrame(position_rows) if position_rows else pd.DataFrame()
    write_table(OUTPUT_DIR / "backtest" / "strategy_metrics.csv", metrics)
    write_table(OUTPUT_DIR / "backtest" / "positions.parquet", positions)
    write_table(OUTPUT_DIR / "backtest" / "orders.parquet", orders)
    write_table(OUTPUT_DIR / "backtest" / "nav_curve.parquet", nav_curve)
    # 与原有 step08 约定保持一致，记录被跳过因子。
    from common import write_json

    write_json(OUTPUT_DIR / "backtest" / "skipped_factors.json", skipped_factors)
    print(f"backtrader batch export ok, factors={len(metrics)}, skipped={len(skipped_factors)}")


if __name__ == "__main__":
    run_backtrader_batch_export()
