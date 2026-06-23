from __future__ import annotations

from typing import Any

import pandas as pd

from common import (
    OUTPUT_DIR,
    build_feature_frame,
    env_config,
    factor_metrics_from_series,
    feature_pool_config,
    label_name,
    load_raw_data,
    log_step_end,
    log_step_start,
    read_json,
    write_json,
)
from llm_agents.analyst_team import ANALYST_AGENT_IDS, ANALYST_FEATURE_GROUPS


def _feature_info_map(feature_cfg: dict[str, Any]) -> dict[str, dict[str, str]]:
    info_map: dict[str, dict[str, str]] = {}
    for item in feature_cfg.get("base_features", []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        info_map[name] = {
            "description": str(item.get("description", "")),
            "expr": str(item.get("expr", "")),
        }
    return info_map


def _select_focus_features(
    stats: pd.DataFrame,
    fundamental_health: dict[str, Any],
) -> dict[str, list[str]]:
    cfg = env_config()
    top_k = max(int(cfg.get("summary_top_k", 3) or 3), 1)
    stats_by_name = (
        stats.set_index("feature_name", drop=False)
        if not stats.empty and "feature_name" in stats.columns
        else pd.DataFrame()
    )
    fundamental_rows = fundamental_health.get("features", []) if isinstance(fundamental_health, dict) else []
    fundamental_score = {
        str(item.get("feature_name", "")): abs(float(item.get("long_short_return", 0.0) or 0.0))
        for item in fundamental_rows
    }

    focus_by_analyst: dict[str, list[str]] = {}
    for agent_id in ANALYST_AGENT_IDS:
        candidates = [name for name in ANALYST_FEATURE_GROUPS.get(agent_id, []) if name in stats_by_name.index]
        if not candidates:
            focus_by_analyst[agent_id] = []
            continue

        def _score(name: str) -> tuple[int, float, float]:
            row = stats_by_name.loc[name]
            quality_ok = 1 if str(row.get("quality_flag", "review")) == "ok" else 0
            label_corr = abs(float(row.get("label_corr", 0.0) or 0.0))
            if agent_id.startswith("fundamental_"):
                return quality_ok, fundamental_score.get(name, 0.0), label_corr
            return quality_ok, label_corr, float(row.get("yearly_stability_score", 0.0) or 0.0)

        ranked = sorted(candidates, key=_score, reverse=True)
        focus_by_analyst[agent_id] = ranked[:top_k]
    return focus_by_analyst


def _compute_quantile_returns(feature_series: pd.Series, label_series: pd.Series) -> dict[str, float | int]:
    merged = pd.concat([feature_series.rename("feature"), label_series.rename("label")], axis=1).dropna()
    if merged.empty:
        return {
            "valid_observations": 0,
            "top_quantile_return": 0.0,
            "bottom_quantile_return": 0.0,
            "long_short_return": 0.0,
        }

    by_date: list[tuple[float, float]] = []
    for _, day_frame in merged.groupby(level="datetime", sort=False):
        if len(day_frame) < 10 or day_frame["feature"].nunique(dropna=True) < 3:
            continue
        bottom = day_frame["feature"].quantile(0.2)
        top = day_frame["feature"].quantile(0.8)
        top_return = day_frame.loc[day_frame["feature"] >= top, "label"].mean()
        bottom_return = day_frame.loc[day_frame["feature"] <= bottom, "label"].mean()
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


def _high_corr_neighbors(
    feature_name: str,
    corr: pd.DataFrame,
    threshold: float,
    limit: int = 5,
) -> list[list[Any]]:
    if corr.empty or feature_name not in corr.index:
        return []
    corr_row = corr.loc[feature_name]
    if isinstance(corr_row, pd.DataFrame):
        corr_row = corr_row.iloc[0]
    corr_row = corr_row.drop(labels=[feature_name], errors="ignore").dropna()
    if corr_row.empty:
        return []
    filtered = corr_row.loc[corr_row.abs() >= threshold]
    if filtered.empty:
        return []
    ranked = filtered.reindex(filtered.abs().sort_values(ascending=False).index).head(limit)
    return [[str(name), round(float(value), 6)] for name, value in ranked.items()]


def build_feature_evidence() -> dict[str, Any]:
    cfg = env_config()
    feature_cfg = feature_pool_config()
    stats = pd.read_csv(OUTPUT_DIR / "health" / "feature_stats.csv")
    corr = pd.read_csv(OUTPUT_DIR / "health" / "feature_corr.csv", index_col=0)
    fundamental_health_path = OUTPUT_DIR / "health" / "fundamental_feature_health.json"
    fundamental_health = read_json(fundamental_health_path) if fundamental_health_path.exists() else {}
    focus_by_analyst = _select_focus_features(stats, fundamental_health)
    selected_features = sorted({name for names in focus_by_analyst.values() for name in names})

    raw_frame = load_raw_data(cfg, list(feature_cfg.get("raw_fields", [])))
    feature_frame = build_feature_frame(raw_frame, cfg, feature_cfg)
    label_column = label_name(cfg)
    label_series = feature_frame[label_column]
    stats_by_name = stats.set_index("feature_name", drop=False)
    info_map = _feature_info_map(feature_cfg)
    high_corr_threshold = float(cfg.get("high_corr_threshold", 0.5))

    evidence_by_feature: dict[str, dict[str, Any]] = {}
    for feature_name in selected_features:
        if feature_name not in feature_frame.columns or feature_name not in stats_by_name.index:
            continue
        stats_row = stats_by_name.loc[feature_name]
        feature_series = feature_frame[feature_name]
        factor_metrics = factor_metrics_from_series(feature_name, feature_series, label_series, cfg)
        quantile_metrics = _compute_quantile_returns(feature_series, label_series)
        info = info_map.get(feature_name, {})
        evidence_by_feature[feature_name] = {
            "feature_name": feature_name,
            "description": info.get("description", ""),
            "expr": info.get("expr", ""),
            "valid_observations": int(quantile_metrics["valid_observations"]),
            "missing_ratio": float(stats_row.get("missing_ratio", 0.0) or 0.0),
            "label_corr": float(stats_row.get("label_corr", 0.0) or 0.0),
            "mean_rank_ic": float(factor_metrics.get("mean_rank_ic", 0.0) or 0.0),
            "rank_ic_ir": float(factor_metrics.get("rank_ic_ir", 0.0) or 0.0),
            "positive_ic_ratio": float(factor_metrics.get("positive_ic_ratio", 0.0) or 0.0),
            "top_quantile_return": float(quantile_metrics["top_quantile_return"]),
            "bottom_quantile_return": float(quantile_metrics["bottom_quantile_return"]),
            "long_short_return": float(quantile_metrics["long_short_return"]),
            "yearly_stability_score": float(stats_row.get("yearly_stability_score", 0.0) or 0.0),
            "coverage_ratio": float(factor_metrics.get("coverage", 0.0) or 0.0),
            "high_corr_neighbors": _high_corr_neighbors(feature_name, corr, high_corr_threshold),
            "poor_quality_flag": str(stats_row.get("quality_flag", "review")) != "ok",
        }

    return {
        "meta": {
            "target": label_column,
            "focus_feature_count": len(evidence_by_feature),
            "selection_top_k_per_analyst": max(int(cfg.get("summary_top_k", 3) or 3), 1),
        },
        "focus_features_by_analyst": focus_by_analyst,
        "feature_evidence": evidence_by_feature,
    }


def run() -> None:
    log_step_start("04.5", "构建 LLM 特征证据包")
    evidence = build_feature_evidence()
    write_json(OUTPUT_DIR / "health" / "llm_feature_evidence.json", evidence)
    log_step_end(
        "04.5",
        "LLM 特征证据包构建完成",
        details=[
            f"重点特征数: {int(evidence.get('meta', {}).get('focus_feature_count', 0))}",
        ],
    )


if __name__ == "__main__":
    run()
