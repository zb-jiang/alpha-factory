from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from common import (
    OUTPUT_DIR,
    active_run_window,
    analysis_observation_dates,
    analysis_profile,
    apply_factor_preprocess,
    build_feature_frame,
    build_dynamic_universe_mask,
    env_config,
    estimate_label_forward_days,
    estimate_required_warmup,
    evaluate_formula,
    factor_metrics_from_series,
    feature_pool_config,
    get_data_provider,
    inspect_feature_frame_cache,
    label_name,
    label_signature,
    load_raw_data,
    log_step_end,
    log_step_start,
    market_context_config,
    preprocess_config,
    preprocess_signature,
    read_json,
    score_config,
    write_table,
)
from market_regime import build_daily_regime_labels, compute_daily_market

_ALL_REGIME_DIMENSIONS = [
    "trend",
    "volatility",
    "liquidity",
    "dispersion",
    "breadth",
    "style",
    "northbound",
    "leverage",
    "capital_structure",
    "rate",
    "macro_liquidity",
    "economy",
    "inflation",
]


def _build_factor_value_chunk(
    factor_name: str,
    raw_score: pd.Series,
    score: pd.Series,
) -> pd.DataFrame:
    frame = pd.concat([raw_score.rename("raw_score"), score.rename("score")], axis=1).reset_index()
    frame.insert(0, "factor_name", factor_name)
    frame["raw_score"] = pd.to_numeric(frame["raw_score"], errors="coerce").astype("float32")
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce").astype("float32")
    return frame


class FactorValueParquetWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._writer = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()

    def write(self, chunk: pd.DataFrame) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pandas(chunk, preserve_index=False)
        if self._writer is None:
            self._writer = pq.ParquetWriter(str(self.path), table.schema, compression="snappy")
        self._writer.write_table(table)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()


def _build_regime_raw_fields(
    feature_cfg: dict[str, Any],
    mc_cfg: dict[str, Any],
    config: dict[str, Any],
) -> list[str]:
    raw_fields = list(feature_cfg.get("raw_fields", []))
    fields = dict(mc_cfg.get("fields", {}))
    for required in [
        fields.get("close", "close"),
        fields.get("turnover", "turnover"),
        fields.get("amount", "amount"),
        fields.get("market_cap", "market_cap"),
    ]:
        field_name = str(required or "").strip()
        if field_name and field_name not in raw_fields:
            raw_fields.append(field_name)
    preprocess_cfg = preprocess_config(config)
    neutralization_options = dict(preprocess_cfg.get("neutralization_options", {}))
    for required in [
        neutralization_options.get("industry_field", "industry"),
        neutralization_options.get("market_cap_field", "market_cap"),
    ]:
        field_name = str(required or "").strip()
        if field_name and field_name not in raw_fields:
            raw_fields.append(field_name)
    return raw_fields


def _load_regime_label_frame(config: dict[str, Any], raw_frame: pd.DataFrame) -> pd.DataFrame:
    mc_cfg = market_context_config()
    windows = dict(mc_cfg.get("windows", {}))
    thresholds = dict(mc_cfg.get("thresholds", {}))
    fields = dict(mc_cfg.get("fields", {}))
    if raw_frame.empty or not windows or not thresholds or not fields:
        return pd.DataFrame()

    provider = get_data_provider(config)
    provider.initialize()
    raw_dates = pd.to_datetime(raw_frame.index.get_level_values("datetime"))
    market_indicator_frame = provider.get_market_daily_indicators(
        start_date=str(pd.Timestamp(raw_dates.min()).date()),
        end_date=str(pd.Timestamp(raw_dates.max()).date()),
    )

    macro_frame = pd.DataFrame()
    try:
        macro_frame = provider.get_macro_indicators(
            start_date=str(pd.Timestamp(raw_dates.min()).date()),
            end_date=str(pd.Timestamp(raw_dates.max()).date()),
        )
    except Exception as exc:
        print(f"  警告: 构建 regime 标签时获取宏观指标失败，将按未知标签降级: {exc}", flush=True)

    daily_market = compute_daily_market(
        raw_frame,
        fields=fields,
        windows=windows,
        external_market_data=market_indicator_frame,
    )
    start_time, end_time = active_run_window(config)
    active_daily_market = daily_market.loc[(daily_market.index >= start_time) & (daily_market.index <= end_time)]
    return build_daily_regime_labels(
        active_daily_market,
        thresholds,
        macro_data=macro_frame,
        windows=windows,
    )


def _compute_quantile_returns(score_series: pd.Series, label_series: pd.Series) -> dict[str, float | int]:
    merged = pd.concat([score_series.rename("score"), label_series.rename("label")], axis=1).dropna()
    if merged.empty:
        return {
            "valid_observations": 0,
            "top_quantile_return": 0.0,
            "bottom_quantile_return": 0.0,
            "long_short_return": 0.0,
        }

    by_date: list[tuple[float, float]] = []
    for _, day_frame in merged.groupby(level="datetime", sort=False):
        if len(day_frame) < 10 or day_frame["score"].nunique(dropna=True) < 3:
            continue
        bottom = day_frame["score"].quantile(0.2)
        top = day_frame["score"].quantile(0.8)
        top_return = day_frame.loc[day_frame["score"] >= top, "label"].mean()
        bottom_return = day_frame.loc[day_frame["score"] <= bottom, "label"].mean()
        if pd.notna(top_return) and pd.notna(bottom_return):
            by_date.append((float(top_return), float(bottom_return)))

    if by_date:
        top_mean = float(sum(item[0] for item in by_date) / len(by_date))
        bottom_mean = float(sum(item[1] for item in by_date) / len(by_date))
    else:
        top_mean = 0.0
        bottom_mean = 0.0

    return {
        "valid_observations": int(len(merged)),
        "top_quantile_return": top_mean,
        "bottom_quantile_return": bottom_mean,
        "long_short_return": top_mean - bottom_mean,
    }


def _parse_expected_failure_regime(text: str) -> dict[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return {}

    cleaned_chars: list[str] = []
    depth = 0
    for ch in raw:
        if ch in {"(", "（"}:
            depth += 1
            continue
        if ch in {")", "）"}:
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            cleaned_chars.append(ch)

    normalized = "".join(cleaned_chars)
    for old in ["，", ",", "+", ";", "；", "/", "|"]:
        normalized = normalized.replace(old, "、")

    parsed: dict[str, str] = {}
    for part in normalized.split("、"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        dimension = key.strip()
        label = value.strip()
        if dimension and label:
            parsed[dimension] = label
    return parsed


def _directional_win_rate(positive_ic_ratio: float, score_sign: float) -> float:
    ratio = float(positive_ic_ratio or 0.0)
    return ratio if score_sign >= 0 else 1.0 - ratio


def _subset_series_by_dates(series: pd.Series, dates: pd.Index) -> pd.Series:
    if len(dates) == 0:
        return series.iloc[0:0]
    mask = series.index.get_level_values("datetime").isin(pd.to_datetime(dates))
    return series.loc[mask]


def _prepare_observation_frame(
    score_series: pd.Series,
    label_series: pd.Series,
    config: dict[str, Any],
) -> pd.DataFrame:
    merged = pd.concat([score_series.rename("score"), label_series.rename("label")], axis=1)
    start_time, end_time = active_run_window(config)
    datetimes = pd.to_datetime(merged.index.get_level_values("datetime"))
    merged = merged.loc[(datetimes >= start_time) & (datetimes <= end_time)]
    if merged.empty:
        return merged

    observation_dates = analysis_observation_dates(merged.index.get_level_values("datetime"), config)
    merged = merged.loc[merged.index.get_level_values("datetime").isin(observation_dates)]
    if merged.empty:
        return merged

    stock_pool_cfg = dict(config.get("stock_pool", {}))
    if bool(stock_pool_cfg.get("dynamic_membership", False)):
        dynamic_mask = build_dynamic_universe_mask(merged.index, config)
        merged = merged.loc[dynamic_mask]
        if merged.empty:
            return merged

    min_valid_ratio = float(config.get("min_valid_ratio_per_observation", 0.0) or 0.0)
    if min_valid_ratio > 0:
        valid_stats = (
            merged.assign(
                _valid_pair=merged[["score", "label"]].notna().all(axis=1),
                _label_available=merged["label"].notna(),
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
        merged = merged.loc[merged.index.get_level_values("datetime").isin(keep_dates)]
    return merged


def _quantile_bucket(day_score: pd.Series, quantile_count: int) -> pd.Series | None:
    unique_count = int(day_score.nunique(dropna=True))
    if len(day_score) < max(10, quantile_count * 2) or unique_count < min(3, quantile_count):
        return None
    ranked = day_score.rank(method="first")
    try:
        buckets = pd.qcut(ranked, q=quantile_count, labels=False, duplicates="drop")
    except ValueError:
        return None
    if buckets is None:
        return None
    bucket_series = pd.Series(buckets, index=day_score.index).astype("float64") + 1.0
    if int(bucket_series.nunique(dropna=True)) < quantile_count:
        return None
    return bucket_series.astype("int64")


def _build_monotonic_group_rows(
    factor_name: str,
    oriented_score: pd.Series,
    label_series: pd.Series,
    quantile_count: int = 5,
) -> list[dict[str, Any]]:
    merged = pd.concat([oriented_score.rename("score"), label_series.rename("label")], axis=1).dropna()
    if merged.empty:
        return []

    rows: list[dict[str, Any]] = []
    for trade_date, day_frame in merged.groupby(level="datetime", sort=False):
        buckets = _quantile_bucket(day_frame["score"], quantile_count)
        if buckets is None:
            continue
        grouped = day_frame.assign(group_id=buckets.values).groupby("group_id")["label"].mean()
        if int(grouped.index.nunique()) < quantile_count:
            continue
        for group_id, mean_return in grouped.items():
            rows.append(
                {
                    "factor_name": factor_name,
                    "variant": "base",
                    "datetime": pd.Timestamp(trade_date),
                    "group_id": int(group_id),
                    "mean_return": float(mean_return),
                }
            )
    return rows


def _compute_monotonicity_metrics(
    factor_name: str,
    oriented_score: pd.Series,
    label_series: pd.Series,
    quantile_count: int = 5,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    group_rows = _build_monotonic_group_rows(factor_name, oriented_score, label_series, quantile_count=quantile_count)
    if not group_rows:
        return {
            "monotonicity_score": 0.0,
            "monotonicity_spearman": 0.0,
            "monotonicity_step_ratio": 0.0,
            "monotonicity_violation_ratio": 1.0,
            "monotonicity_sample_days": 0,
        }, []

    group_frame = pd.DataFrame(group_rows)
    group_means = group_frame.groupby("group_id")["mean_return"].mean().sort_index()
    if len(group_means) < quantile_count:
        return {
            "monotonicity_score": 0.0,
            "monotonicity_spearman": 0.0,
            "monotonicity_step_ratio": 0.0,
            "monotonicity_violation_ratio": 1.0,
            "monotonicity_sample_days": int(group_frame["datetime"].nunique()),
        }, group_rows

    bucket_positions = pd.Series(group_means.index.astype(float), index=group_means.index)
    spearman = float(bucket_positions.corr(group_means, method="spearman") or 0.0)
    spearman_score = max(0.0, min(1.0, (spearman + 1.0) / 2.0))
    adjacent_diffs = group_means.diff().dropna()
    step_ratio = float((adjacent_diffs >= 0).mean()) if not adjacent_diffs.empty else 0.0
    violation_ratio = 1.0 - step_ratio
    monotonicity_score = float((spearman_score + step_ratio) / 2.0)
    return {
        "monotonicity_score": monotonicity_score,
        "monotonicity_spearman": spearman,
        "monotonicity_step_ratio": step_ratio,
        "monotonicity_violation_ratio": violation_ratio,
        "monotonicity_sample_days": int(group_frame["datetime"].nunique()),
    }, group_rows


def _compute_yearly_health_metrics(
    factor_name: str,
    score: pd.Series,
    label_series: pd.Series,
    score_sign: float,
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    observation_frame = _prepare_observation_frame(score, label_series, config).dropna()
    if observation_frame.empty:
        return {
            "yearly_stability_score": 0.0,
            "yearly_positive_ratio": 0.0,
            "yearly_rank_ic_mean": 0.0,
            "yearly_rank_ic_std": 0.0,
            "yearly_observation_years": 0,
        }, []

    daily_rank_ic = observation_frame.groupby(level="datetime").apply(
        lambda frame: frame["score"].corr(frame["label"], method="spearman")
    ).dropna()
    if daily_rank_ic.empty:
        return {
            "yearly_stability_score": 0.0,
            "yearly_positive_ratio": 0.0,
            "yearly_rank_ic_mean": 0.0,
            "yearly_rank_ic_std": 0.0,
            "yearly_observation_years": 0,
        }, []

    oriented_daily_rank_ic = daily_rank_ic * score_sign
    yearly_rows: list[dict[str, Any]] = []
    yearly_series: list[float] = []
    for year, year_frame in oriented_daily_rank_ic.groupby(oriented_daily_rank_ic.index.year):
        mean_ic = float(year_frame.mean() or 0.0)
        yearly_series.append(mean_ic)
        yearly_rows.append(
            {
                "factor_name": factor_name,
                "year": int(year),
                "mean_rank_ic": float((daily_rank_ic.loc[year_frame.index]).mean() or 0.0),
                "oriented_mean_rank_ic": mean_ic,
                "observation_count": int(year_frame.count()),
                "positive_observation_ratio": float((year_frame > 0).mean()) if len(year_frame) else 0.0,
            }
        )

    yearly_ic = pd.Series(yearly_series, dtype="float64")
    positive_ratio = float((yearly_ic > 0).mean()) if not yearly_ic.empty else 0.0
    mean_oriented_ic = max(float(yearly_ic.mean() or 0.0), 0.0)
    std_oriented_ic = float(yearly_ic.std(ddof=0) or 0.0) if not yearly_ic.empty else 0.0
    core_score = (
        float(mean_oriented_ic / (mean_oriented_ic + std_oriented_ic))
        if (mean_oriented_ic + std_oriented_ic) > 0
        else 0.0
    )
    return {
        "yearly_stability_score": positive_ratio * core_score,
        "yearly_positive_ratio": positive_ratio,
        "yearly_rank_ic_mean": mean_oriented_ic,
        "yearly_rank_ic_std": std_oriented_ic,
        "yearly_observation_years": int(len(yearly_ic)),
    }, yearly_rows


def _neutralization_compare_scores(
    raw_score: pd.Series,
    raw_frame: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[pd.Series, pd.Series]:
    preprocess_cfg = preprocess_config(config)
    base_config = dict(config)
    base_config["preprocess"] = {
        **preprocess_cfg,
        "neutralization": "none",
    }
    neutralized_config = dict(config)
    neutralized_config["preprocess"] = {
        **preprocess_cfg,
        "neutralization": "industry_market_cap",
    }
    return (
        apply_factor_preprocess(raw_score, raw_frame, base_config),
        apply_factor_preprocess(raw_score, raw_frame, neutralized_config),
    )


def _compute_neutralization_health_metrics(
    factor_name: str,
    raw_score: pd.Series,
    raw_frame: pd.DataFrame,
    label_series: pd.Series,
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    base_score, neutralized_score = _neutralization_compare_scores(raw_score, raw_frame, config)
    base_metrics = factor_metrics_from_series(factor_name, base_score, label_series, config)
    neutralized_metrics = factor_metrics_from_series(factor_name, neutralized_score, label_series, config)
    base_abs_rank_ic = abs(float(base_metrics.get("mean_rank_ic", 0.0) or 0.0))
    neutralized_abs_rank_ic = abs(float(neutralized_metrics.get("mean_rank_ic", 0.0) or 0.0))
    retention = (
        float(neutralized_abs_rank_ic / base_abs_rank_ic)
        if base_abs_rank_ic > 1e-12
        else 0.0
    )
    neutralization_row = {
        "factor_name": factor_name,
        "base_mean_rank_ic": float(base_metrics.get("mean_rank_ic", 0.0) or 0.0),
        "base_rank_ic_ir": float(base_metrics.get("rank_ic_ir", 0.0) or 0.0),
        "neutralized_mean_rank_ic": float(neutralized_metrics.get("mean_rank_ic", 0.0) or 0.0),
        "neutralized_rank_ic_ir": float(neutralized_metrics.get("rank_ic_ir", 0.0) or 0.0),
        "neutralized_ic_retention": retention,
        "neutralization_mode": "industry_market_cap",
    }
    return {"neutralized_ic_retention": retention}, neutralization_row


def _compute_factor_health_metrics(
    factor_name: str,
    raw_score: pd.Series,
    score: pd.Series,
    raw_frame: pd.DataFrame,
    label_series: pd.Series,
    metrics: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    score_sign = 1.0 if float(metrics.get("mean_rank_ic", 0.0) or 0.0) >= 0 else -1.0
    oriented_score = (score * score_sign).rename(score.name)
    monotonicity_metrics, group_rows = _compute_monotonicity_metrics(factor_name, oriented_score, label_series)
    yearly_metrics, yearly_rows = _compute_yearly_health_metrics(
        factor_name,
        score,
        label_series,
        score_sign=score_sign,
        config=config,
    )
    neutralization_metrics, neutralization_row = _compute_neutralization_health_metrics(
        factor_name,
        raw_score,
        raw_frame,
        label_series,
        config,
    )
    return (
        {
            **monotonicity_metrics,
            **yearly_metrics,
            **neutralization_metrics,
        },
        group_rows,
        yearly_rows,
        neutralization_row,
    )


def _build_slice_payload(
    factor_name: str,
    score: pd.Series,
    label_series: pd.Series,
    regime_dates: pd.Index,
    config: dict[str, Any],
    score_sign: float,
    regime_cfg: dict[str, Any],
) -> dict[str, float | int]:
    score_subset = _subset_series_by_dates(score, regime_dates)
    label_subset = _subset_series_by_dates(label_series, regime_dates)
    regime_analysis_config = dict(config)
    regime_analysis_config["min_valid_ratio_per_observation"] = float(
        regime_cfg.get(
            "regime_min_valid_ratio_per_observation",
            config.get("min_valid_ratio_per_observation", 0.0) or 0.0,
        )
    )
    metrics = factor_metrics_from_series(factor_name, score_subset, label_subset, regime_analysis_config)
    quantiles = _compute_quantile_returns(score_subset, label_subset)
    directional_win_rate = _directional_win_rate(float(metrics.get("positive_ic_ratio", 0.0) or 0.0), score_sign)
    oriented_long_short = float(quantiles["long_short_return"]) * score_sign
    oriented_rank_ic = float(metrics.get("mean_rank_ic", 0.0) or 0.0) * score_sign
    return {
        "sample_days": int(len(pd.Index(pd.to_datetime(regime_dates)).unique())),
        "observation_count": int(metrics.get("observation_count", 0) or 0),
        "valid_observations": int(quantiles["valid_observations"]),
        "mean_rank_ic": float(metrics.get("mean_rank_ic", 0.0) or 0.0),
        "rank_ic_ir": float(metrics.get("rank_ic_ir", 0.0) or 0.0),
        "positive_ic_ratio": float(metrics.get("positive_ic_ratio", 0.0) or 0.0),
        "directional_win_rate": directional_win_rate,
        "top_quantile_return": float(quantiles["top_quantile_return"]),
        "bottom_quantile_return": float(quantiles["bottom_quantile_return"]),
        "long_short_return": float(quantiles["long_short_return"]),
        "oriented_long_short_return": oriented_long_short,
        "oriented_rank_ic": oriented_rank_ic,
    }


def _regime_eval_config() -> dict[str, Any]:
    regime_cfg = dict(score_config().get("regime_analysis", {}))
    scoring_dimensions = [
        str(item).strip()
        for item in regime_cfg.get(
            "scoring_dimensions",
            ["trend", "volatility", "style", "breadth", "capital_structure"],
        )
        if str(item).strip()
    ]
    report_dimensions = [
        str(item).strip()
        for item in regime_cfg.get("report_dimensions", _ALL_REGIME_DIMENSIONS)
        if str(item).strip()
    ]
    return {
        "scoring_dimensions": scoring_dimensions,
        "report_dimensions": report_dimensions,
        "regime_min_valid_ratio_per_observation": float(
            regime_cfg.get(
                "regime_min_valid_ratio_per_observation",
                regime_cfg.get("min_valid_ratio_per_observation", 0.8),
            )
        ),
        "neutral_score": float(regime_cfg.get("neutral_score", 0.5)),
        "min_observation_count": int(regime_cfg.get("min_observation_count", 4)),
        "ic_tolerance": float(regime_cfg.get("ic_tolerance", 0.002)),
        "win_rate_tolerance": float(regime_cfg.get("win_rate_tolerance", 0.05)),
        "long_short_tolerance": float(regime_cfg.get("long_short_tolerance", 0.001)),
    }


def _comparison_score(subgroup_value: float, baseline_value: float, tolerance: float, neutral_score: float) -> float:
    delta = baseline_value - subgroup_value
    if delta > tolerance:
        return 1.0
    if delta < -tolerance:
        return 0.0
    return neutral_score


def _consistency_status(score_value: float, neutral_score: float) -> str:
    if score_value >= neutral_score + 0.15:
        return "consistent"
    if score_value <= neutral_score - 0.15:
        return "inconsistent"
    return "neutral"


def _consistency_reason_tag(reason: str) -> str:
    mapping = {
        "missing_dimension": "missing_dimension",
        "declared_label_not_observed": "declared_label_not_observed",
        "insufficient_subgroup_observation": "insufficient_subgroup_observation",
        "scored": "scored",
    }
    return mapping.get(reason, reason or "unknown")


def _analyze_regime_consistency(
    factor_name: str,
    score: pd.Series,
    label_series: pd.Series,
    expected_failure_regime: str,
    overall_metrics: dict[str, Any],
    regime_label_frame: pd.DataFrame,
    config: dict[str, Any],
    regime_cfg: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    neutral_score = float(regime_cfg["neutral_score"])
    if regime_label_frame.empty:
        return {
            "expected_failure_regime": expected_failure_regime,
            "regime_declared_dimension_count": 0,
            "regime_scored_dimension_count": 0,
            "regime_consistency_score": neutral_score,
            "regime_consistency_status": "missing_regime_data",
            "regime_consistency_summary": "缺少可用的 regime 标签数据",
        }, [], []

    score_sign = 1.0 if float(overall_metrics.get("mean_rank_ic", 0.0) or 0.0) >= 0 else -1.0
    overall_quantiles = _compute_quantile_returns(score, label_series)
    overall_baseline = {
        "sample_days": int(score.index.get_level_values("datetime").nunique()),
        "observation_count": int(overall_metrics.get("observation_count", 0) or 0),
        "valid_observations": int(overall_quantiles["valid_observations"]),
        "oriented_rank_ic": abs(float(overall_metrics.get("mean_rank_ic", 0.0) or 0.0)),
        "directional_win_rate": _directional_win_rate(float(overall_metrics.get("positive_ic_ratio", 0.0) or 0.0), score_sign),
        "oriented_long_short_return": float(overall_quantiles["long_short_return"]) * score_sign,
        "mean_rank_ic": float(overall_metrics.get("mean_rank_ic", 0.0) or 0.0),
        "rank_ic_ir": float(overall_metrics.get("rank_ic_ir", 0.0) or 0.0),
        "long_short_return": float(overall_quantiles["long_short_return"]),
    }

    slice_rows: list[dict[str, Any]] = []
    consistency_rows: list[dict[str, Any]] = []
    labels_by_dimension: dict[str, pd.Series] = {}

    for dimension in regime_cfg["report_dimensions"]:
        if dimension not in regime_label_frame.columns:
            continue
        dimension_labels = regime_label_frame[dimension].dropna().astype(str)
        dimension_labels = dimension_labels[dimension_labels != "未知"]
        if dimension_labels.empty:
            continue
        labels_by_dimension[dimension] = dimension_labels
        unique_labels = sorted(dimension_labels.unique().tolist())
        total_dimension_days = int(dimension_labels.index.nunique())
        for regime_label in unique_labels:
            regime_dates = dimension_labels.index[dimension_labels == regime_label]
            payload = _build_slice_payload(
                factor_name,
                score,
                label_series,
                regime_dates,
                config,
                score_sign,
                regime_cfg,
            )
            slice_rows.append(
                {
                    "factor_name": factor_name,
                    "regime_dimension": dimension,
                    "regime_label": regime_label,
                    "sample_share": float(payload["sample_days"] / total_dimension_days) if total_dimension_days else 0.0,
                    **payload,
                }
            )

    declared = _parse_expected_failure_regime(expected_failure_regime)
    declared_pairs = [
        (dimension, declared[dimension])
        for dimension in regime_cfg["scoring_dimensions"]
        if dimension in declared
    ]
    if not declared_pairs:
        return {
            "expected_failure_regime": expected_failure_regime,
            "regime_declared_dimension_count": 0,
            "regime_scored_dimension_count": 0,
            "regime_consistency_score": neutral_score,
            "regime_consistency_status": "no_declared_regime",
            "regime_consistency_summary": "未解析到可评分的 expected_failure_regime 维度",
        }, slice_rows, consistency_rows

    min_obs = int(regime_cfg["min_observation_count"])
    per_dimension_scores: list[float] = []
    summary_parts: list[str] = []

    for dimension, declared_label in declared_pairs:
        dimension_labels = labels_by_dimension.get(dimension)
        if dimension_labels is None:
            dimension_reason = "missing_dimension"
            consistency_rows.append(
                {
                    "factor_name": factor_name,
                    "regime_dimension": dimension,
                    "declared_failure_label": declared_label,
                    "baseline_source": "overall",
                    "dimension_score": neutral_score,
                    "dimension_status": "missing_dimension",
                    "dimension_reason": dimension_reason,
                    "subgroup_sample_days": 0,
                    "subgroup_observation_count": 0,
                    "subgroup_oriented_rank_ic": 0.0,
                    "baseline_oriented_rank_ic": overall_baseline["oriented_rank_ic"],
                    "subgroup_directional_win_rate": 0.0,
                    "baseline_directional_win_rate": overall_baseline["directional_win_rate"],
                    "subgroup_oriented_long_short_return": 0.0,
                    "baseline_oriented_long_short_return": overall_baseline["oriented_long_short_return"],
                    "score_components": "missing_dimension",
                }
            )
            summary_parts.append(f"{dimension}={declared_label}:missing[{_consistency_reason_tag(dimension_reason)}]")
            continue

        subgroup_dates = dimension_labels.index[dimension_labels == declared_label]
        subgroup = _build_slice_payload(
            factor_name,
            score,
            label_series,
            subgroup_dates,
            config,
            score_sign,
            regime_cfg,
        )
        complement_dates = dimension_labels.index[dimension_labels != declared_label]
        complement = _build_slice_payload(
            factor_name,
            score,
            label_series,
            complement_dates,
            config,
            score_sign,
            regime_cfg,
        )
        use_complement = int(complement["observation_count"]) >= min_obs
        baseline = complement if use_complement else overall_baseline
        baseline_source = "complement" if use_complement else "overall"

        if int(subgroup["sample_days"]) == 0:
            dimension_score = neutral_score
            component_summary = "declared_label_not_observed"
            dimension_reason = "declared_label_not_observed"
        elif int(subgroup["observation_count"]) < min_obs:
            dimension_score = neutral_score
            component_summary = "insufficient_subgroup_observation"
            dimension_reason = "insufficient_subgroup_observation"
        else:
            score_components = [
                _comparison_score(
                    float(subgroup["oriented_rank_ic"]),
                    float(baseline["oriented_rank_ic"]),
                    float(regime_cfg["ic_tolerance"]),
                    neutral_score,
                ),
                _comparison_score(
                    float(subgroup["directional_win_rate"]),
                    float(baseline["directional_win_rate"]),
                    float(regime_cfg["win_rate_tolerance"]),
                    neutral_score,
                ),
                _comparison_score(
                    float(subgroup["oriented_long_short_return"]),
                    float(baseline["oriented_long_short_return"]),
                    float(regime_cfg["long_short_tolerance"]),
                    neutral_score,
                ),
            ]
            dimension_score = float(sum(score_components) / len(score_components))
            component_summary = ",".join(f"{item:.2f}" for item in score_components)
            dimension_reason = "scored"

        per_dimension_scores.append(dimension_score)
        dimension_status = _consistency_status(dimension_score, neutral_score)
        consistency_rows.append(
            {
                "factor_name": factor_name,
                "regime_dimension": dimension,
                "declared_failure_label": declared_label,
                "baseline_source": baseline_source,
                "dimension_score": dimension_score,
                "dimension_status": dimension_status,
                "dimension_reason": dimension_reason,
                "subgroup_sample_days": int(subgroup["sample_days"]),
                "subgroup_observation_count": int(subgroup["observation_count"]),
                "subgroup_oriented_rank_ic": float(subgroup["oriented_rank_ic"]),
                "baseline_oriented_rank_ic": float(baseline["oriented_rank_ic"]),
                "subgroup_directional_win_rate": float(subgroup["directional_win_rate"]),
                "baseline_directional_win_rate": float(baseline["directional_win_rate"]),
                "subgroup_oriented_long_short_return": float(subgroup["oriented_long_short_return"]),
                "baseline_oriented_long_short_return": float(baseline["oriented_long_short_return"]),
                "score_components": component_summary,
            }
        )
        summary_parts.append(
            f"{dimension}={declared_label}:{dimension_status}[{_consistency_reason_tag(dimension_reason)}]({dimension_score:.2f})"
        )

    final_score = float(sum(per_dimension_scores) / len(per_dimension_scores)) if per_dimension_scores else neutral_score
    return {
        "expected_failure_regime": expected_failure_regime,
        "regime_declared_dimension_count": int(len(declared_pairs)),
        "regime_scored_dimension_count": int(len(per_dimension_scores)),
        "regime_consistency_score": final_score,
        "regime_consistency_status": _consistency_status(final_score, neutral_score),
        "regime_consistency_summary": "; ".join(summary_parts),
    }, slice_rows, consistency_rows
def run() -> None:
    log_step_start("07", "因子评估 (IC/IR)")
    config = env_config()
    mc_cfg = market_context_config()
    regime_cfg = _regime_eval_config()
    feature_cfg = feature_pool_config()
    validated = read_json(OUTPUT_DIR / "llm" / "factors_validated.json").get("factors", [])
    formulas = [str(item.get("formula", "")) for item in validated if item.get("formula")]
    warmup_days = estimate_required_warmup(feature_cfg, formulas)
    forward_days = estimate_label_forward_days(config)
    print("  准备评估环境: 加载原始行情与特征所需字段...", flush=True)
    raw_frame = load_raw_data(
        config,
        _build_regime_raw_fields(feature_cfg, mc_cfg, config),
        warmup_trading_days=warmup_days,
        forward_trading_days=forward_days,
    )
    print("  原始数据加载完成，开始构建特征面板...", flush=True)
    feature_cache_hit, feature_cache_reason = inspect_feature_frame_cache(raw_frame, config, feature_cfg)
    if feature_cache_hit:
        print(f"  特征面板缓存命中: {feature_cache_reason}", flush=True)
    else:
        print(f"  特征面板缓存未命中: {feature_cache_reason}", flush=True)
    feature_frame = build_feature_frame(raw_frame, config, feature_cfg)
    metric_rows: list[dict[str, object]] = []
    print("  特征面板构建完成，开始构建收益标签...", flush=True)
    label_column = label_name(config)
    label_series = feature_frame[label_column]
    factor_input = feature_frame[[item["name"] for item in feature_cfg.get("base_features", [])]]
    active_analysis_profile = analysis_profile(config)
    active_label_mode = label_signature(config)
    active_preprocess = preprocess_signature(config)
    regime_label_frame = _load_regime_label_frame(config, raw_frame)
    factor_writer = FactorValueParquetWriter(OUTPUT_DIR / "backtest" / "factor_values.parquet")
    wrote_factor_values = False
    health_group_rows: list[dict[str, Any]] = []
    health_yearly_rows: list[dict[str, Any]] = []
    health_neutralization_rows: list[dict[str, Any]] = []
    regime_slice_rows: list[dict[str, Any]] = []
    regime_consistency_rows: list[dict[str, Any]] = []
    total = len(validated)
    print(f"  公共准备完成，开始逐因子评估: {total} 个候选因子", flush=True)
    try:
        for i, item in enumerate(validated, start=1):
            factor_name = str(item["factor_name"])
            raw_score = evaluate_formula(str(item["formula"]), factor_input)
            score = apply_factor_preprocess(raw_score, raw_frame, config)
            metrics = factor_metrics_from_series(factor_name, score, label_series, config)
            health_metrics, group_rows, yearly_rows, neutralization_row = _compute_factor_health_metrics(
                factor_name,
                raw_score,
                score,
                raw_frame,
                label_series,
                metrics,
                config,
            )
            metrics["llm_direction"] = str(item["llm_direction"])
            metrics["empirical_direction"] = "higher_better" if metrics["mean_rank_ic"] >= 0 else "lower_better"
            metrics["analysis_profile"] = active_analysis_profile
            metrics["label_mode"] = active_label_mode
            metrics["preprocess_signature"] = active_preprocess
            metrics.update(health_metrics)
            regime_summary, slice_rows, consistency_rows = _analyze_regime_consistency(
                factor_name,
                score,
                label_series,
                str(item.get("expected_failure_regime", "")),
                metrics,
                regime_label_frame,
                config,
                regime_cfg,
            )
            metrics.update(regime_summary)
            factor_writer.write(_build_factor_value_chunk(factor_name, raw_score, score))
            wrote_factor_values = True
            metric_rows.append(metrics)
            health_group_rows.extend(group_rows)
            health_yearly_rows.extend(yearly_rows)
            health_neutralization_rows.append(neutralization_row)
            regime_slice_rows.extend(slice_rows)
            regime_consistency_rows.extend(consistency_rows)
            rank_ic = metrics.get("mean_rank_ic", 0.0)
            rank_ir = metrics.get("rank_ic_ir", 0.0)
            regime_score = float(metrics.get("regime_consistency_score", regime_cfg["neutral_score"]))
            print(
                f"  [{i}/{total}] {factor_name}: rank_ic={rank_ic:.4f}, rank_ir={rank_ir:.4f}, regime_score={regime_score:.2f}",
                flush=True,
            )
    finally:
        factor_writer.close()
    if not wrote_factor_values:
        raise RuntimeError("没有可评估的合法因子")
    metrics = pd.DataFrame(metric_rows).set_index("factor_name").sort_index()
    write_table(OUTPUT_DIR / "backtest" / "factor_metrics.csv", metrics)
    write_table(
        OUTPUT_DIR / "backtest" / "factor_health_metrics.csv",
        metrics[
            [
                "monotonicity_score",
                "monotonicity_spearman",
                "monotonicity_step_ratio",
                "monotonicity_violation_ratio",
                "monotonicity_sample_days",
                "yearly_stability_score",
                "yearly_positive_ratio",
                "yearly_rank_ic_mean",
                "yearly_rank_ic_std",
                "yearly_observation_years",
                "neutralized_ic_retention",
            ]
        ],
    )
    write_table(OUTPUT_DIR / "backtest" / "factor_health_group_returns.csv", pd.DataFrame(health_group_rows))
    write_table(OUTPUT_DIR / "backtest" / "factor_health_yearly.csv", pd.DataFrame(health_yearly_rows))
    write_table(
        OUTPUT_DIR / "backtest" / "factor_health_neutralization.csv",
        pd.DataFrame(health_neutralization_rows).set_index("factor_name")
        if health_neutralization_rows
        else pd.DataFrame(),
    )
    write_table(OUTPUT_DIR / "backtest" / "factor_regime_slices.csv", pd.DataFrame(regime_slice_rows))
    write_table(OUTPUT_DIR / "backtest" / "factor_regime_consistency.csv", pd.DataFrame(regime_consistency_rows))
    log_step_end(
        "07",
        "因子评估完成",
        details=[
            f"评估因子: {len(metrics)} 个",
            f"regime 评分维度: {', '.join(regime_cfg['scoring_dimensions'])}",
            f"regime 报表维度: {len(regime_cfg['report_dimensions'])} 个",
        ],
    )


if __name__ == "__main__":
    run()
