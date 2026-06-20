from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from common import OUTPUT_DIR, env_config, feature_pool_config, get_data_provider, load_raw_data, market_context_config, selector_config, write_json
from market_regime import build_window_context, compute_daily_market


REGIME_FEATURE_KEYS = [
    "trend_20d_mean",
    "vol_rank_250d_mean",
    "amount_rank_250d_mean",
    "dispersion_rank_250d_mean",
    "breadth_up_ratio_mean",
    "size_style_spread_mean",
    "northbound_flow_rank_mean",
    "leverage_composite_mean",
    "capital_structure_score",
    "shibor_1y_change",
    "m2_yoy_change",
    "pmi_current",
    "inflation_composite_mean",
]


def _log(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[selector {now}] {message}", flush=True)


def _encode_capital_structure(label: str) -> float:
    mapping = {
        "同向进攻": 2.0,
        "外资积极/杠杆收缩": 1.0,
        "中性": 0.0,
        "外资谨慎/杠杆激进": -1.0,
        "同向防守": -2.0,
    }
    return mapping.get(label, 0.0)


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
    # raw_fields 中的元素可能是 dict（含 name/description），提取 name
    raw_fields = [
        f["name"] if isinstance(f, dict) else f
        for f in feature_cfg.get("raw_fields", [])
    ]
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
    macro_data: pd.DataFrame | None = None,
    windows: dict[str, Any] | None = None,
) -> pd.DataFrame:
    months = pd.date_range(
        start=_normalize_month_start(history_start),
        end=_normalize_month_start(history_end),
        freq="MS",
    )
    total_months = len(months)
    _log(
        "开始逐月汇总历史市场状态（趋势/波动/资金/宏观等）："
        f"{history_start.strftime('%Y-%m-%d')} ~ {history_end.strftime('%Y-%m-%d')}，"
        f"共 {total_months} 个月"
    )
    rows: list[dict[str, Any]] = []
    for idx, month_start in enumerate(months, start=1):
        month_end = _month_end(month_start)
        window = daily_market.loc[(daily_market.index >= month_start) & (daily_market.index <= month_end)]
        if window.empty:
            continue
        month_macro = None
        if macro_data is not None and not macro_data.empty:
            month_str = month_start.strftime("%Y%m")
            month_macro = macro_data[macro_data["month"] <= month_str].copy()
        context = build_window_context(window, thresholds, macro_data=month_macro, windows=windows)
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
            "northbound_label": labels.get("northbound", ""),
            "leverage_label": labels.get("leverage", ""),
            "capital_structure_label": labels.get("capital_structure", ""),
            "rate_label": labels.get("rate", ""),
            "macro_liquidity_label": labels.get("macro_liquidity", ""),
            "economy_label": labels.get("economy", ""),
            "inflation_label": labels.get("inflation", ""),
        }
        for key in REGIME_FEATURE_KEYS:
            row[key] = stats.get(key)

        # 合成/编码特征（若 stats 中未直接提供则自行计算）
        margin_flow = stats.get("margin_flow_rank_mean")
        margin_balance = stats.get("margin_balance_rank_mean")
        if margin_flow is not None and margin_balance is not None:
            row["leverage_composite_mean"] = (margin_flow + margin_balance) / 2.0
        else:
            row["leverage_composite_mean"] = None

        row["capital_structure_score"] = _encode_capital_structure(labels.get("capital_structure", "未知"))

        cpi_change = stats.get("cpi_yoy_change")
        ppi_change = stats.get("ppi_yoy_change")
        if cpi_change is not None and ppi_change is not None:
            row["inflation_composite_mean"] = (cpi_change + ppi_change) / 2.0
        else:
            row["inflation_composite_mean"] = None

        rows.append(row)
        if idx % 12 == 0 or idx == total_months:
            _log(f"历史月份汇总进度：{idx}/{total_months}")
    frame = pd.DataFrame(rows)
    if frame.empty:
        _log("历史月份汇总结果为空")
        return frame
    # 与 test 窗口处理逻辑一致：宏观指标缺失的月份用中性值 0.0 填充，而非直接丢弃
    missing_before = frame[REGIME_FEATURE_KEYS].isna().any(axis=1).sum()
    if missing_before > 0:
        _log(
            f"历史月份中有 {int(missing_before)} 个月宏观指标缺失，"
            f"已用中性值 0.0 填充（与待预测期处理方式一致）"
        )
    for key in REGIME_FEATURE_KEYS:
        frame[key] = frame[key].fillna(0.0)
    frame = frame.sort_values("month_start").reset_index(drop=True)
    _log(f"历史月份汇总完成：共 {len(frame)} 个月可用")
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
    _log(f"开始为待预测期 {test_start.strftime('%Y-%m-%d')} ~ {test_end.strftime('%Y-%m-%d')} 寻找最相似的历史训练窗口")
    if test_end < test_start:
        raise ValueError("test_end_date 必须晚于或等于 test_start_date")

    history_start = (test_start - pd.DateOffset(years=selector_cfg.lookback_years)).normalize()
    history_end = (test_start - pd.Timedelta(days=1)).normalize()
    fetch_start = history_start
    fetch_end = test_end
    _log(
        f"历史参考区间为过去 {selector_cfg.lookback_years} 年："
        f"{history_start.strftime('%Y-%m-%d')} ~ {history_end.strftime('%Y-%m-%d')}"
        f"（即待预测期开始前 {selector_cfg.lookback_years} 年）"
    )
    _log(f"待预测期：{test_start.strftime('%Y-%m-%d')} ~ {test_end.strftime('%Y-%m-%d')}")
    _log(
        "为完成相似度计算，需拉取 "
        f"{fetch_start.strftime('%Y-%m-%d')} ~ {fetch_end.strftime('%Y-%m-%d')} 的行情数据"
        "（含待预测期）"
    )
    if history_end < history_start:
        raise ValueError("历史回看区间非法，请检查 test_start_date")

    raw_fields = _prepare_required_raw_fields(feature_cfg, fields)
    _log("开始加载行情数据（含日线行情、财务指标、行业分类等）...")
    load_cfg = dict(base_cfg)
    # selector 直接按“test字段语义”组织加载窗口，避免混淆 train/test 口径。
    load_cfg["run_mode"] = "test"
    load_cfg["test_start_date"] = str(fetch_start.date())
    load_cfg["test_end_date"] = str(fetch_end.date())
    stock_pool_cfg = dict(load_cfg.get("stock_pool", {}) or {})
    stock_pool_cfg["dynamic_membership"] = not selector_cfg.disable_dynamic_membership
    load_cfg["stock_pool"] = stock_pool_cfg
    if selector_cfg.disable_dynamic_membership:
        _log("已关闭指数成分股动态回溯（可显著减少数据请求、加速加载）")
    else:
        _log("已开启指数成分股动态回溯（口径更严格，但耗时更长）")
    raw_frame = load_raw_data(load_cfg, raw_fields=raw_fields, warmup_trading_days=warmup_days)
    instrument_count = (
        int(raw_frame.index.get_level_values(0).nunique()) if isinstance(raw_frame.index, pd.MultiIndex) else 1
    )
    _log(f"行情数据加载完成：共 {len(raw_frame):,} 条记录，覆盖 {instrument_count} 只股票")

    provider = get_data_provider(load_cfg)
    provider.initialize()
    raw_dates = raw_frame.index.get_level_values("datetime")
    market_indicator_frame = provider.get_market_daily_indicators(
        start_date=str(pd.Timestamp(raw_dates.min()).date()),
        end_date=str(pd.Timestamp(raw_dates.max()).date()),
    ) if not raw_frame.empty else pd.DataFrame()

    macro_frame = pd.DataFrame()
    try:
        macro_frame = provider.get_macro_indicators(
            start_date=str(pd.Timestamp(raw_dates.min()).date()),
            end_date=str(pd.Timestamp(raw_dates.max()).date()),
        )
    except Exception as e:
        _log(f"警告：获取宏观经济指标（Shibor/M2/PMI/CPI/PPI）失败，宏观维度将以'未知'降级：{e}")

    _log("开始计算每日市场状态（趋势、波动、流动性、资金流向等）...")
    daily_market = compute_daily_market(
        raw_frame,
        fields=fields,
        windows=windows,
        external_market_data=market_indicator_frame,
    )
    _log(f"每日市场状态计算完成：共 {len(daily_market)} 个交易日")

    test_window = daily_market.loc[(daily_market.index >= test_start) & (daily_market.index <= test_end)]
    if test_window.empty:
        raise RuntimeError("待预测期没有可用市场状态数据，无法进行训练窗口推荐")
    _log(f"待预测期包含 {len(test_window)} 个交易日")

    test_end_month_str = test_end.strftime("%Y%m")
    test_macro = macro_frame[macro_frame["month"] <= test_end_month_str].copy() if not macro_frame.empty else None
    test_context = build_window_context(test_window, thresholds, macro_data=test_macro, windows=windows)
    test_summary = str(test_context.get("summary_text", "")).strip()
    if test_summary:
        _log(f"待预测期市场状态摘要：{test_summary}")
    test_stats = dict(test_context.get("stats", {}))
    test_labels = dict(test_context.get("labels", {}))
    # 补充合成/编码特征（与 _build_monthly_regime_vectors 逻辑一致）
    margin_flow = test_stats.get("margin_flow_rank_mean")
    margin_balance = test_stats.get("margin_balance_rank_mean")
    if margin_flow is not None and margin_balance is not None:
        test_stats["leverage_composite_mean"] = (margin_flow + margin_balance) / 2.0
    test_stats["capital_structure_score"] = _encode_capital_structure(test_labels.get("capital_structure", "未知"))
    cpi_change = test_stats.get("cpi_yoy_change")
    ppi_change = test_stats.get("ppi_yoy_change")
    if cpi_change is not None and ppi_change is not None:
        test_stats["inflation_composite_mean"] = (cpi_change + ppi_change) / 2.0
    missing_keys: list[str] = []
    for key in REGIME_FEATURE_KEYS:
        value = test_stats.get(key)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            missing_keys.append(key)
    if missing_keys:
        _log(
            "待预测期部分宏观指标暂未发布，将用中性值替代，不影响相似度排名："
            f"{missing_keys}"
        )
        for key in missing_keys:
            test_stats[key] = 0.0

    monthly_vectors = _build_monthly_regime_vectors(
        daily_market, thresholds, history_start, history_end,
        macro_data=macro_frame, windows=windows,
    )
    if monthly_vectors.empty:
        raise RuntimeError("历史月份汇总结果为空，无法进行相似度推荐")

    _log("开始计算每个月份与待预测期的市场相似度...")
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
    _log(f"相似度计算完成：共 {len(monthly_scaled)} 个月参与比较")
    top_preview = monthly_scaled.head(selector_cfg.top_k_similar_months)
    if not top_preview.empty:
        top_text = "；".join(
            f"{row['month']}（相似度 {float(row['similarity_score']):.4f}，"
            f"趋势{row.get('trend_label', '未知')}，波动{row.get('volatility_label', '未知')}，"
            f"资金结构{row.get('capital_structure_label', '未知')}）"
            for _, row in top_preview.iterrows()
        )
        _log(f"最相似的 Top-{selector_cfg.top_k_similar_months} 历史月份：{top_text}")

    all_scored_frames: list[pd.DataFrame] = []
    span_reports: list[dict[str, Any]] = []
    for span_months in selector_cfg.recommend_span_months_options:
        _log(f"开始评估候选训练窗口（窗口长度 {span_months} 个月）...")
        scored_windows = _score_candidate_windows(monthly_scaled, test_start, span_months, selector_cfg)
        if scored_windows.empty:
            _log(f"窗口长度 {span_months} 个月：无可用候选窗口，跳过")
            continue
        scored_windows = scored_windows.copy()
        scored_windows["recommend_span_months"] = span_months
        all_scored_frames.append(scored_windows)
        _log(
            f"窗口长度 {span_months} 个月：共 {len(scored_windows)} 个候选窗口，"
            f"最优 = "
            f"{scored_windows.iloc[0]['train_start_date'].strftime('%Y-%m-%d')} ~ "
            f"{scored_windows.iloc[0]['train_end_date'].strftime('%Y-%m-%d')}"
            f"（综合得分 {scored_windows.iloc[0]['total_score']:.4f}）"
        )
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
    _log(
        f"所有候选窗口评估完成：共 {len(scored_windows)} 个窗口"
        f"（{len(selector_cfg.recommend_span_months_options)} 种长度合计）"
    )

    selector_dir = OUTPUT_DIR / "selector"
    selector_dir.mkdir(parents=True, exist_ok=True)

    monthly_output = monthly_scaled.copy()
    monthly_output["month_start"] = monthly_output["month_start"].dt.strftime("%Y-%m-%d")
    monthly_output["month_end"] = monthly_output["month_end"].dt.strftime("%Y-%m-%d")
    monthly_output.to_csv(selector_dir / "historical_month_similarity.csv", index=False, encoding="utf-8-sig")
    _log(f"已保存历史月份相似度明细：{selector_dir / 'historical_month_similarity.csv'}")

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
    _log(f"已保存待预测期市场状态报告：{selector_dir / 'test_market_context.json'}")

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
                "报告仅供人工决策参考。"
            ),
        },
    )
    _log(
        "推荐训练窗口（与待预测期市场状态最相似）："
        f"{best['train_start_date'].strftime('%Y-%m-%d')} ~ {best['train_end_date'].strftime('%Y-%m-%d')}"
        f"（长度 {int(best['recommend_span_months'])} 个月，"
        f"综合得分 {float(best['total_score']):.4f}）"
    )
    _log(
        f"共评估 {len(selector_cfg.recommend_span_months_options)} 种窗口长度："
        f"{selector_cfg.recommend_span_months_options} 个月，"
        "完整报告已保存，供人工决策参考。"
    )


if __name__ == "__main__":
    run()
