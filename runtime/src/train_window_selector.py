from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from common import OUTPUT_DIR, env_config, feature_pool_config, load_raw_data, market_context_config, selector_config, write_json
from market_regime import build_window_context, compute_daily_market


REGIME_FEATURE_KEYS = [
    "trend_20d_mean",
    "vol_rank_250d_mean",
    "amount_rank_250d_mean",
    "dispersion_rank_250d_mean",
    "breadth_up_ratio_mean",
    "size_style_spread_mean",
]


def _log(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[selector {now}] {message}", flush=True)


def _serialize_window_record(row: pd.Series | dict[str, Any]) -> dict[str, Any]:
    record = dict(row)
    return {
        "recommend_span_months": int(record["recommend_span_months"]),
        "train_start_date": (
            record["train_start_date"].strftime("%Y-%m-%d")
            if hasattr(record["train_start_date"], "strftime")
            else str(record["train_start_date"])
        ),
        "train_end_date": (
            record["train_end_date"].strftime("%Y-%m-%d")
            if hasattr(record["train_end_date"], "strftime")
            else str(record["train_end_date"])
        ),
        "window_month_count": int(record["window_month_count"]),
        "mean_similarity_score": float(record["mean_similarity_score"]),
        "top_similar_hit_count": int(record["top_similar_hit_count"]),
        "top_similar_coverage_score": float(record["top_similar_coverage_score"]),
        "total_score": float(record["total_score"]),
    }


@dataclass
class SelectorConfig:
    lookback_years: int = 10
    recommend_span_months_options: list[int] = field(default_factory=lambda: [12])
    top_k_similar_months: int = 4
    score_similarity_weight: float = 0.7
    score_coverage_weight: float = 0.3
    disable_dynamic_membership: bool = True


def _cfg_int(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    return int(default if value is None else value)


def _cfg_float(payload: dict[str, Any], key: str, default: float) -> float:
    value = payload.get(key, default)
    return float(default if value is None else value)


def _cfg_int_list(payload: dict[str, Any], key: str, default: list[int]) -> list[int]:
    value = payload.get(key, default)
    if value is None:
        return list(default)
    if isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        candidates = [value]
    normalized: list[int] = []
    for item in candidates:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in normalized:
            normalized.append(number)
    return normalized or list(default)


def _cfg_bool(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _load_selector_config() -> SelectorConfig:
    payload = selector_config()
    return SelectorConfig(
        lookback_years=_cfg_int(payload, "lookback_years", 10),
        recommend_span_months_options=_cfg_int_list(payload, "recommend_span_months", [12]),
        top_k_similar_months=_cfg_int(payload, "top_k_similar_months", 4),
        score_similarity_weight=_cfg_float(payload, "score_similarity_weight", 0.7),
        score_coverage_weight=_cfg_float(payload, "score_coverage_weight", 0.3),
        disable_dynamic_membership=_cfg_bool(payload, "disable_dynamic_membership", True),
    )


def _normalize_month_start(value: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(value).normalize().replace(day=1)


def _month_end(month_start: pd.Timestamp) -> pd.Timestamp:
    return (month_start + pd.offsets.MonthEnd(0)).normalize()


def _prepare_required_raw_fields(feature_cfg: dict[str, Any], fields: dict[str, Any]) -> list[str]:
    raw_fields = list(feature_cfg.get("raw_fields", []))
    required = [
        fields.get("close", "close"),
        fields.get("turnover", "turnover"),
        fields.get("amount", "amount"),
        fields.get("market_cap", "market_cap"),
    ]
    for item in required:
        item_text = str(item)
        if item_text not in raw_fields:
            raw_fields.append(item_text)
    return raw_fields


def _build_monthly_regime_vectors(
    daily_market: pd.DataFrame,
    thresholds: dict[str, Any],
    history_start: pd.Timestamp,
    history_end: pd.Timestamp,
) -> pd.DataFrame:
    months = pd.date_range(
        start=_normalize_month_start(history_start),
        end=_normalize_month_start(history_end),
        freq="MS",
    )
    total_months = len(months)
    _log(
        "开始构建历史月度状态向量: "
        f"{history_start.strftime('%Y-%m-%d')} ~ {history_end.strftime('%Y-%m-%d')}，"
        f"共 {total_months} 个月"
    )
    rows: list[dict[str, Any]] = []
    for idx, month_start in enumerate(months, start=1):
        month_end = _month_end(month_start)
        window = daily_market.loc[(daily_market.index >= month_start) & (daily_market.index <= month_end)]
        if window.empty:
            continue
        context = build_window_context(window, thresholds)
        stats = dict(context.get("stats", {}))
        labels = dict(context.get("labels", {}))
        row = {
            "month_start": month_start.normalize(),
            "month_end": month_end.normalize(),
            "month": month_start.strftime("%Y-%m"),
            "summary_text": context.get("summary_text", ""),
            "trend_label": labels.get("trend", ""),
            "volatility_label": labels.get("volatility", ""),
            "liquidity_label": labels.get("liquidity", ""),
            "dispersion_label": labels.get("dispersion", ""),
            "breadth_label": labels.get("breadth", ""),
            "style_label": labels.get("style", ""),
        }
        for key in REGIME_FEATURE_KEYS:
            row[key] = stats.get(key)
        rows.append(row)
        if idx % 12 == 0 or idx == total_months:
            _log(f"历史月度状态向量进度: {idx}/{total_months}")
    frame = pd.DataFrame(rows)
    if frame.empty:
        _log("历史月度状态向量为空")
        return frame
    frame = frame.dropna(subset=REGIME_FEATURE_KEYS).sort_values("month_start").reset_index(drop=True)
    _log(f"历史月度状态向量构建完成: 有效 {len(frame)} 个月")
    return frame


def _regime_distance(row: pd.Series, target_scaled: pd.Series, weights: dict[str, float]) -> float:
    total = 0.0
    for key in REGIME_FEATURE_KEYS:
        w = float(weights.get(key, 1.0))
        delta = float(row[key]) - float(target_scaled[key])
        total += w * delta * delta
    return math.sqrt(total)


def _score_candidate_windows(
    monthly: pd.DataFrame,
    test_start: pd.Timestamp,
    span_months: int,
    cfg: SelectorConfig,
) -> pd.DataFrame:
    if monthly.empty:
        return pd.DataFrame()
    available = monthly.set_index("month_start", drop=False)
    available_months = set(available.index.tolist())
    max_window_start = _normalize_month_start(test_start) - pd.offsets.MonthBegin(span_months)
    candidate_starts = pd.date_range(
        start=available.index.min(),
        end=max_window_start,
        freq="MS",
    )
    top_months = monthly.head(cfg.top_k_similar_months)["month_start"].tolist()
    top_months_set = set(top_months)
    rows: list[dict[str, Any]] = []
    for start in candidate_starts:
        window_months = [start + pd.offsets.MonthBegin(i) for i in range(span_months)]
        if any(month not in available_months for month in window_months):
            continue
        window_df = available.loc[window_months]
        mean_similarity = float(window_df["similarity_score"].mean())
        hit_count = int(sum(month in top_months_set for month in window_months))
        coverage_score = float(hit_count / max(cfg.top_k_similar_months, 1))
        total_score = cfg.score_similarity_weight * mean_similarity + cfg.score_coverage_weight * coverage_score
        rows.append(
            {
                "train_start_date": pd.Timestamp(start).normalize(),
                "train_end_date": _month_end(start + pd.offsets.MonthBegin(span_months - 1)),
                "window_month_count": span_months,
                "mean_similarity_score": mean_similarity,
                "top_similar_hit_count": hit_count,
                "top_similar_coverage_score": coverage_score,
                "total_score": total_score,
            }
        )
    if not rows:
        return pd.DataFrame()
    scored = pd.DataFrame(rows).sort_values("total_score", ascending=False).reset_index(drop=True)
    return scored


def run() -> None:
    base_cfg = env_config()
    mc_cfg = market_context_config()
    feature_cfg = feature_pool_config()
    selector_cfg = _load_selector_config()

    windows = dict(mc_cfg.get("windows", {}))
    thresholds = dict(mc_cfg.get("thresholds", {}))
    fields = dict(mc_cfg.get("fields", {}))
    rank_days = _cfg_int(windows, "rank_lookback_days", 250)
    warmup_days = _cfg_int(windows, "warmup_trading_days", rank_days + 10)

    test_start = pd.Timestamp(str(base_cfg.get("test_start_date"))).normalize()
    test_end = pd.Timestamp(str(base_cfg.get("test_end_date"))).normalize()
    _log(f"启动训练窗口选择: test={test_start.strftime('%Y-%m-%d')} ~ {test_end.strftime('%Y-%m-%d')}")
    if test_end < test_start:
        raise ValueError("test_end_date 必须晚于或等于 test_start_date")

    history_start = (test_start - pd.DateOffset(years=selector_cfg.lookback_years)).normalize()
    history_end = (test_start - pd.Timedelta(days=1)).normalize()
    fetch_start = history_start
    fetch_end = test_end
    _log(
        f"历史回看区间: {history_start.strftime('%Y-%m-%d')} ~ {history_end.strftime('%Y-%m-%d')}"
        f"（lookback_years={selector_cfg.lookback_years}）"
    )
    _log(f"目标 test 区间: {test_start.strftime('%Y-%m-%d')} ~ {test_end.strftime('%Y-%m-%d')}")
    _log(f"实际数据拉取区间(含 test 计算): {fetch_start.strftime('%Y-%m-%d')} ~ {fetch_end.strftime('%Y-%m-%d')}")
    if history_end < history_start:
        raise ValueError("历史回看区间非法，请检查 test_start_date")

    raw_fields = _prepare_required_raw_fields(feature_cfg, fields)
    _log(
        "开始加载原始行情数据: "
        f"fields={raw_fields}, warmup_trading_days={warmup_days}, "
        f"recommend_span_months={selector_cfg.recommend_span_months_options}"
    )
    load_cfg = dict(base_cfg)
    # selector 直接按“test字段语义”组织加载窗口，避免混淆 train/test 口径。
    load_cfg["run_mode"] = "test"
    load_cfg["test_start_date"] = str(fetch_start.date())
    load_cfg["test_end_date"] = str(fetch_end.date())
    stock_pool_cfg = dict(load_cfg.get("stock_pool", {}) or {})
    if selector_cfg.disable_dynamic_membership and bool(stock_pool_cfg.get("dynamic_membership", False)):
        stock_pool_cfg["dynamic_membership"] = False
        load_cfg["stock_pool"] = stock_pool_cfg
        _log(
            "selector 已临时关闭 dynamic_membership，"
            "避免历史逐观测日成分回溯导致长时间 Tushare 频控等待"
        )
    raw_frame = load_raw_data(load_cfg, raw_fields=raw_fields, warmup_trading_days=warmup_days)
    instrument_count = (
        int(raw_frame.index.get_level_values(0).nunique()) if isinstance(raw_frame.index, pd.MultiIndex) else 1
    )
    _log(f"原始行情加载完成: rows={len(raw_frame)}, instruments={instrument_count}")

    _log("开始计算日级市场状态向量")
    daily_market = compute_daily_market(raw_frame, fields=fields, windows=windows)
    _log(f"日级市场状态计算完成: {len(daily_market)} 个交易日")

    test_window = daily_market.loc[(daily_market.index >= test_start) & (daily_market.index <= test_end)]
    if test_window.empty:
        raise RuntimeError("test 窗口没有可用市场状态数据，无法进行训练窗口推荐")
    _log(f"test 窗口交易日: {len(test_window)}")
    test_context = build_window_context(test_window, thresholds)
    test_stats = dict(test_context.get("stats", {}))
    for key in REGIME_FEATURE_KEYS:
        value = test_stats.get(key)
        if value is None or pd.isna(value):
            raise RuntimeError(f"test 窗口状态向量存在缺失: {key}")

    monthly_vectors = _build_monthly_regime_vectors(daily_market, thresholds, history_start, history_end)
    if monthly_vectors.empty:
        raise RuntimeError("历史月度状态向量为空，无法进行相似性推荐")

    _log("开始计算历史月度相似度")
    scale_mean = monthly_vectors[REGIME_FEATURE_KEYS].mean()
    scale_std = monthly_vectors[REGIME_FEATURE_KEYS].std(ddof=0).replace(0, 1.0).fillna(1.0)
    monthly_scaled = monthly_vectors.copy()
    monthly_scaled[REGIME_FEATURE_KEYS] = (monthly_vectors[REGIME_FEATURE_KEYS] - scale_mean) / scale_std

    target_raw = pd.Series({key: float(test_stats[key]) for key in REGIME_FEATURE_KEYS})
    target_scaled = (target_raw - scale_mean) / scale_std
    weights = {key: 1.0 for key in REGIME_FEATURE_KEYS}
    monthly_scaled["distance_score"] = monthly_scaled.apply(
        lambda row: _regime_distance(row, target_scaled, weights),
        axis=1,
    )
    monthly_scaled["similarity_score"] = 1.0 / (1.0 + monthly_scaled["distance_score"])
    monthly_scaled = monthly_scaled.sort_values("distance_score", ascending=True).reset_index(drop=True)
    _log(f"历史月度相似度计算完成: {len(monthly_scaled)} 个月")

    all_scored_frames: list[pd.DataFrame] = []
    span_reports: list[dict[str, Any]] = []
    for span_months in selector_cfg.recommend_span_months_options:
        _log(f"开始打分连续{span_months}个月候选训练窗口")
        scored_windows = _score_candidate_windows(monthly_scaled, test_start, span_months, selector_cfg)
        if scored_windows.empty:
            _log(f"连续{span_months}个月候选训练窗口为空，跳过")
            continue
        scored_windows = scored_windows.copy()
        scored_windows["recommend_span_months"] = span_months
        all_scored_frames.append(scored_windows)
        _log(f"连续{span_months}个月候选训练窗口打分完成: {len(scored_windows)} 个窗口")
        span_top = scored_windows.head(3).copy()
        span_top_records = [_serialize_window_record(row) for _, row in span_top.iterrows()]
        span_reports.append(
            {
                "recommend_span_months": span_months,
                "best_window": span_top_records[0],
                "top_windows": span_top_records,
            }
        )
    if not all_scored_frames:
        raise RuntimeError("没有生成任何可用的候选训练窗口")
    scored_windows = pd.concat(all_scored_frames, ignore_index=True)
    scored_windows = scored_windows.sort_values("total_score", ascending=False).reset_index(drop=True)
    _log(f"候选训练窗口总打分完成: {len(scored_windows)} 个窗口")

    selector_dir = OUTPUT_DIR / "selector"
    selector_dir.mkdir(parents=True, exist_ok=True)

    monthly_output = monthly_scaled.copy()
    monthly_output["month_start"] = monthly_output["month_start"].dt.strftime("%Y-%m-%d")
    monthly_output["month_end"] = monthly_output["month_end"].dt.strftime("%Y-%m-%d")
    monthly_output.to_csv(selector_dir / "historical_month_similarity.csv", index=False, encoding="utf-8-sig")
    _log(f"已写出 historical_month_similarity.csv -> {selector_dir}")

    write_json(
        selector_dir / "test_market_context.json",
        {
            "meta": {
                "test_start_date": str(test_start.date()),
                "test_end_date": str(test_end.date()),
                "config_source": "runtime/config/market_context.yaml",
            },
            "test_context": test_context,
            "regime_vector_keys": REGIME_FEATURE_KEYS,
        },
    )
    _log(f"已写出 test_market_context.json -> {selector_dir}")

    best = scored_windows.iloc[0]
    top_months = monthly_scaled.head(selector_cfg.top_k_similar_months)
    alternatives = scored_windows.head(3).copy()
    alternatives_records = [_serialize_window_record(row) for _, row in alternatives.iterrows()]
    write_json(
        selector_dir / "recommended_train_window.json",
        {
            "method": "historical_mirror_monthly_similarity",
            "parameters": {
                "lookback_years": selector_cfg.lookback_years,
                "recommend_span_months": selector_cfg.recommend_span_months_options,
                "top_k_similar_months": selector_cfg.top_k_similar_months,
                "score_similarity_weight": selector_cfg.score_similarity_weight,
                "score_coverage_weight": selector_cfg.score_coverage_weight,
                "disable_dynamic_membership": selector_cfg.disable_dynamic_membership,
            },
            "target_test_window": {
                "test_start_date": str(test_start.date()),
                "test_end_date": str(test_end.date()),
            },
            "recommended_train_window": {
                "recommend_span_months": int(best["recommend_span_months"]),
                "train_start_date": best["train_start_date"].strftime("%Y-%m-%d"),
                "train_end_date": best["train_end_date"].strftime("%Y-%m-%d"),
                "total_score": float(best["total_score"]),
                "mean_similarity_score": float(best["mean_similarity_score"]),
                "top_similar_hit_count": int(best["top_similar_hit_count"]),
                "top_similar_coverage_score": float(best["top_similar_coverage_score"]),
            },
            "top_similar_months": [
                {
                    "month": str(row["month"]),
                    "month_start": pd.Timestamp(row["month_start"]).strftime("%Y-%m-%d"),
                    "month_end": pd.Timestamp(row["month_end"]).strftime("%Y-%m-%d"),
                    "distance_score": float(row["distance_score"]),
                    "similarity_score": float(row["similarity_score"]),
                }
                for _, row in top_months.iterrows()
            ],
            "span_reports": span_reports,
            "alternative_windows": alternatives_records,
            "summary": (
                "已基于 test 窗口市场状态与历史月度状态相似度完成多种连续训练窗口长度推荐，"
                "报告仅供人工决策参考，不会自动修改 analysis_rule.yaml。"
            ),
        },
    )
    _log(
        "训练窗口推荐完成: "
        f"{int(best['recommend_span_months'])}个月 / "
        f"{best['train_start_date'].strftime('%Y-%m-%d')} ~ {best['train_end_date'].strftime('%Y-%m-%d')}"
    )


if __name__ == "__main__":
    run()
