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
    read_json,
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


def _optional_threshold(cfg: dict[str, Any], key: str) -> float | None:
    if key not in cfg:
        return None
    value = cfg.get(key)
    if value in {None, ""}:
        return None
    return float(value)


def _append_skip(skipped_factors: list[dict[str, str]], factor_name: str, formula_map: dict[str, str], reason: str) -> None:
    skipped_factors.append(
        {
            "factor_name": factor_name,
            "formula": formula_map.get(factor_name, ""),
            "reason": reason,
        }
    )


def _format_screening_reasons(reasons: list[str]) -> str:
    if not reasons:
        return ""
    numbered = "；".join(f"{idx + 1}) {item}" for idx, item in enumerate(reasons))
    return f"训练期因子预筛未通过，共命中 {len(reasons)} 条淘汰条件：{numbered}"


def _descriptive_metric_reason(
    metric_name: str,
    value: float,
    threshold: float,
    direction: str,
    config_key: str | None = None,
) -> str:
    config_hint = f" 对应配置键：`{config_key}`。" if config_key else ""
    if metric_name == "mean_rank_ic_abs":
        return (
            "统计有效性检查失败：因子在每个调仓观察日上对股票排序后，与未来一期收益排序的 Spearman 相关系数（Rank IC）的绝对值。"
            "该值衡量因子分数对股票未来收益的排序能力强弱，越接近 0 说明因子越像随机噪声。"
            f" 当前为 {value:.4f}，低于阈值 {threshold:.4f}（因子排序能力不足）。{config_hint}"
        )
    if metric_name == "rank_ic_ir_abs":
        return (
            "统计稳定性检查失败：因子在各调仓观察日上 Rank IC 序列的均值与标准差之比（Rank IC IR）的绝对值。"
            "该值衡量因子预测能力的稳定程度，越接近 0 说明信号时有时无、不可靠。"
            f" 当前为 {value:.4f}，低于阈值 {threshold:.4f}（因子信号不稳定）。{config_hint}"
        )
    if metric_name == "directional_win_rate":
        return (
            "方向胜率检查失败：因子根据样本内 Rank IC 符号确定经验方向（IC 为正则因子是'高分更好'，为负则'低分更好'）后，"
            "在经验方向上 Rank IC 符号与经验方向一致的调仓观察日占比。"
            "该值衡量因子在大多数时候方向是否站得住，50% 表示与抛硬币无异。"
            f" 当前为 {value:.4f}，低于阈值 {threshold:.4f}（方向预测能力不足）。{config_hint}"
        )
    if metric_name == "monotonicity_score":
        return (
            "单调性检查失败：先按经验方向统一成'高分对应更好未来收益'的口径，再在每个调仓观察日把股票按因子值从小到大切成 5 组（Q1~Q5），"
            "计算各组未来收益均值后聚合得到分组收益曲线，综合该曲线与组号的 Spearman 相关性、以及相邻组收益递增比例得到的 0~1 评分。"
            "该值衡量因子是否具备'分数越高收益越好'的单调分层结构，而非只在头尾碰巧有效。"
            f" 当前为 {value:.4f}，低于阈值 {threshold:.4f}（分层结构不清晰）。{config_hint}"
        )
    if metric_name == "yearly_stability_score":
        return (
            "跨年稳定性检查失败：先按经验方向统一 Rank IC 符号，再按自然年聚合计算每年平均 Rank IC，"
            "综合'方向正确的年份占比'和'年度间有效性均值与波动的比值'得到的 0~1 评分。"
            "该值衡量因子是否依赖单一年份的市场环境，越低说明跨年表现断裂越严重。"
            f" 当前为 {value:.4f}，低于阈值 {threshold:.4f}（跨年不稳定）。{config_hint}"
        )
    if metric_name == "neutralized_ic_retention":
        return (
            "中性化保真度检查失败：同一因子在不做中性化处理与做行业+市值中性化处理两种口径下，"
            "后者 |Rank IC| 相对前者的保留比例。"
            "如果中性化后 IC 明显塌掉，说明因子表面有效但本质上是行业偏离或大小盘暴露，而非干净的选股 Alpha。"
            f" 当前为 {value:.4f}，低于阈值 {threshold:.4f}（有效性可能主要来自风险暴露而非因子本身）。{config_hint}"
        )
    if metric_name == "monotonicity_violation_ratio":
        return (
            "单调性反向比例检查失败：基于上述 5 组平均收益曲线，统计相邻组收益未按'高组不差于低组'排列的比例。"
            "例如 Q3 收益低于 Q2 就算一次反向，4 个相邻关系中反向的占比即为该值。"
            "该值越高说明分组曲线局部越混乱，因子研究解释性越差。"
            f" 当前为 {value:.4f}，高于上限 {threshold:.4f}（局部反向过多）。{config_hint}"
        )
    comparator = "低于" if direction == "min" else "高于"
    return f"指标 `{metric_name}` 当前为 {value:.4f}，{comparator} 阈值 {threshold:.4f}。{config_hint}"


def _descriptive_regime_reason(factor_row: pd.Series, gate: str, config_key: str) -> str:
    original_status = str(factor_row.get("regime_consistency_status", "") or "")
    declared_regime = str(factor_row.get("expected_failure_regime", "") or "")
    consistency_summary = str(factor_row.get("regime_consistency_summary", "") or "")
    status_label = {
        "consistent": "一致（LLM 声明的失效环境在数据中确实表现出变差）",
        "inconsistent": "不一致（LLM 声明的失效环境在数据中并未变差，假设不成立）",
        "neutral": "中性（部分维度验证通过、部分未通过，无法给出明确结论）",
        "no_declared_regime": "中性（LLM 未提供可解析的 expected_failure_regime）",
        "missing_regime_data": "中性（缺少 regime 标签数据，无法验证）",
    }
    label = status_label.get(original_status, original_status or "未知")
    gate_desc = "inconsistent（拦截不一致）" if gate == "inconsistent" else "strict（拦截不一致+中性）"
    parts = [
        f"regime 一致性检查失败：该因子在生成时声明的预期失效环境为 `{declared_regime}`。",
        f"step07 对该声明逐一验证后，结论为 `{original_status}`（{label}）。",
    ]
    if consistency_summary:
        parts.append(f"各维度验证明细：{consistency_summary}。")
    parts.append(f"当前门槛模式为 `{gate_desc}`，该状态被拦截。对应配置键：`{config_key}`。")
    return "".join(parts)


def _collect_metric_threshold_reason(
    metric_name: str,
    factor_row: pd.Series,
    cfg: dict[str, Any],
    min_key: str | None = None,
    max_key: str | None = None,
) -> str | None:
    value = pd.to_numeric(pd.Series([factor_row.get(metric_name)]), errors="coerce").iloc[0]
    if pd.isna(value):
        configured = []
        if min_key and _optional_threshold(cfg, min_key) is not None:
            configured.append(min_key)
        if max_key and _optional_threshold(cfg, max_key) is not None:
            configured.append(max_key)
        if configured:
            return (
                f"指标数据缺失：`{metric_name}` 在本轮评估结果中为空，"
                f"但它被 {', '.join(f'`{item}`' for item in configured)} 配置为强制门槛，因此该因子无法进入回测。"
            )
        return None

    if min_key:
        minimum = _optional_threshold(cfg, min_key)
        if minimum is not None and float(value) < minimum:
            return _descriptive_metric_reason(metric_name, float(value), minimum, "min", config_key=min_key)
    if max_key:
        maximum = _optional_threshold(cfg, max_key)
        if maximum is not None and float(value) > maximum:
            return _descriptive_metric_reason(metric_name, float(value), maximum, "max", config_key=max_key)
    return None


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
    # 构建 factor_name -> formula 映射，用于 skipped_factors 输出
    _formula_map: dict[str, str] = {}
    _validated_path = OUTPUT_DIR / "llm" / "factors_validated.json"
    if _validated_path.exists():
        for _f in read_json(_validated_path).get("factors", []):
            if isinstance(_f, dict) and _f.get("factor_name"):
                _formula_map[str(_f["factor_name"])] = str(_f.get("formula", ""))
    factor_metrics_path = OUTPUT_DIR / "backtest" / "factor_metrics.csv"
    factor_metrics = pd.read_csv(factor_metrics_path).set_index("factor_name") if factor_metrics_path.exists() else pd.DataFrame()
    min_rank_ic_to_backtest = float(cfg.get("min_rank_ic_to_backtest", 0.01))
    min_rank_ic_ir_to_backtest = float(cfg.get("min_rank_ic_ir_to_backtest", 0.1))
    min_positive_ic_ratio = float(cfg.get("min_positive_ic_ratio", 0.4))
    enable_direction_filter = bool(cfg.get("enable_direction_filter", False))
    regime_consistency_gate = str(cfg.get("regime_consistency_gate", "none") or "none").strip().lower()

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
                factor_row = factor_metrics.loc[factor_name]
                mean_rank_ic = float(factor_row["mean_rank_ic"])
                rank_ic_ir = float(factor_row["rank_ic_ir"])
                pos_ratio = float(factor_row["positive_ic_ratio"])
                empirical_direction = str(factor_row["empirical_direction"])
                llm_direction = str(factor_row["llm_direction"])
                screening_reasons: list[str] = []
                if abs(mean_rank_ic) < min_rank_ic_to_backtest:
                    screening_reasons.append(
                        _descriptive_metric_reason(
                            "mean_rank_ic_abs",
                            abs(mean_rank_ic),
                            min_rank_ic_to_backtest,
                            "min",
                            config_key="min_rank_ic_to_backtest",
                        )
                    )
                if abs(rank_ic_ir) < min_rank_ic_ir_to_backtest:
                    screening_reasons.append(
                        _descriptive_metric_reason(
                            "rank_ic_ir_abs",
                            abs(rank_ic_ir),
                            min_rank_ic_ir_to_backtest,
                            "min",
                            config_key="min_rank_ic_ir_to_backtest",
                        )
                    )
                if enable_direction_filter and llm_direction != empirical_direction:
                    screening_reasons.append(
                        "方向一致性检查失败：配置 `enable_direction_filter=true` 时，要求 LLM 预判方向与样本内经验方向一致；"
                        f" 当前 LLM 方向为 `{llm_direction}`，样本内经验方向为 `{empirical_direction}`。"
                        " 对应配置键：`enable_direction_filter`。"
                    )
                is_negative_ic = mean_rank_ic < 0
                score_sign = -1.0 if is_negative_ic else 1.0
                win_rate = pos_ratio if not is_negative_ic else (1 - pos_ratio)
                if win_rate < min_positive_ic_ratio:
                    screening_reasons.append(
                        _descriptive_metric_reason(
                            "directional_win_rate",
                            win_rate,
                            min_positive_ic_ratio,
                            "min",
                            config_key="min_positive_ic_ratio",
                        )
                    )
                reason = _collect_metric_threshold_reason(
                    "monotonicity_score",
                    factor_row,
                    cfg,
                    min_key="min_monotonicity_score_to_backtest",
                )
                if reason:
                    screening_reasons.append(reason)
                reason = _collect_metric_threshold_reason(
                    "yearly_stability_score",
                    factor_row,
                    cfg,
                    min_key="min_yearly_stability_score_to_backtest",
                )
                if reason:
                    screening_reasons.append(reason)
                reason = _collect_metric_threshold_reason(
                    "neutralized_ic_retention",
                    factor_row,
                    cfg,
                    min_key="min_neutralized_ic_retention_to_backtest",
                )
                if reason:
                    screening_reasons.append(reason)
                reason = _collect_metric_threshold_reason(
                    "monotonicity_violation_ratio",
                    factor_row,
                    cfg,
                    max_key="max_monotonicity_violation_ratio_to_backtest",
                )
                if reason:
                    screening_reasons.append(reason)

                if regime_consistency_gate != "none":
                    regime_status = str(factor_row.get("regime_consistency_status", "") or "")
                    normalized_status = "neutral" if regime_status in ("no_declared_regime", "missing_regime_data") else regime_status
                    skip_statuses = {"inconsistent"} if regime_consistency_gate == "inconsistent" else {"inconsistent", "neutral"}
                    if normalized_status in skip_statuses:
                        screening_reasons.append(
                            _descriptive_regime_reason(
                                factor_row,
                                regime_consistency_gate,
                                config_key="regime_consistency_gate",
                            )
                        )

                if screening_reasons:
                    _append_skip(
                        skipped_factors,
                        factor_name,
                        _formula_map,
                        _format_screening_reasons(screening_reasons),
                    )
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
            _append_skip(
                skipped_factors,
                factor_name,
                _formula_map,
                f"回测执行阶段发生异常，导致该因子无法进入结果汇总：{exc}",
            )
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
