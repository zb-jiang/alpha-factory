from __future__ import annotations

import pandas as pd

from common import (
    OUTPUT_DIR,
    env_config,
    get_data_provider,
    load_raw_data,
    log_step_end,
    log_step_start,
    market_context_config,
    write_json,
)
from market_regime import build_train_context, compute_daily_market


def _cfg_int(payload: dict[str, object], key: str, default: int) -> int:
    value = payload.get(key, default)
    return int(default if value is None else value)


def run() -> None:
    log_step_start("03", "市场环境分析")
    config = env_config()
    mc_cfg = market_context_config()
    windows = dict(mc_cfg.get("windows", {}))
    thresholds = dict(mc_cfg.get("thresholds", {}))
    fields = dict(mc_cfg.get("fields", {}))

    train_start = pd.Timestamp(str(config.get("train_start_date"))).normalize()
    train_end = pd.Timestamp(str(config.get("train_end_date"))).normalize()
    # Step03 只依赖市场环境计算所需的最小字段集合，避免为了环境分析加载整套特征底层字段。
    raw_fields: list[str] = []
    for required in [
        fields.get("close", "close"),
        fields.get("turnover", "turnover"),
        fields.get("amount", "amount"),
        fields.get("market_cap", "market_cap"),
    ]:
        field_name = str(required or "").strip()
        if field_name and field_name not in raw_fields:
            raw_fields.append(field_name)

    context_cfg = dict(config)
    context_cfg["run_mode"] = "train"
    context_cfg["train_start_date"] = str(train_start.date())
    context_cfg["train_end_date"] = str(train_end.date())

    rank_days = _cfg_int(windows, "rank_lookback_days", 250)
    flow_rank_days = _cfg_int(windows, "flow_rank_lookback_days", rank_days)
    northbound_days = _cfg_int(windows, "northbound_days", 5)
    margin_days = _cfg_int(windows, "margin_days", 5)
    warmup_days = _cfg_int(
        windows,
        "warmup_trading_days",
        max(rank_days, flow_rank_days, northbound_days, margin_days) + 10,
    )
    raw_frame = load_raw_data(context_cfg, raw_fields=raw_fields, warmup_trading_days=warmup_days)
    provider = get_data_provider(context_cfg)
    provider.initialize()
    raw_dates = raw_frame.index.get_level_values("datetime")
    market_indicator_frame = provider.get_market_daily_indicators(
        start_date=str(pd.Timestamp(raw_dates.min()).date()),
        end_date=str(pd.Timestamp(raw_dates.max()).date()),
    ) if not raw_frame.empty else pd.DataFrame()

    # 获取宏观经济指标（Shibor, M2, PMI, CPI, PPI）
    macro_frame = pd.DataFrame()
    try:
        macro_frame = provider.get_macro_indicators(
            start_date=str(pd.Timestamp(raw_dates.min()).date()),
            end_date=str(pd.Timestamp(raw_dates.max()).date()),
        )
        if not macro_frame.empty:
            # 过滤出训练窗口内的宏观数据
            train_start_month = train_start.strftime("%Y%m")
            train_end_month = train_end.strftime("%Y%m")
            macro_frame = macro_frame[
                (macro_frame["month"] >= train_start_month) & (macro_frame["month"] <= train_end_month)
            ]
    except Exception as e:
        print(f"警告: 获取宏观经济指标失败，宏观标签将以'未知'降级: {e}")

    daily_market = compute_daily_market(
        raw_frame,
        fields=fields,
        windows=windows,
        external_market_data=market_indicator_frame,
    )
    train_window = daily_market.loc[(daily_market.index >= train_start) & (daily_market.index <= train_end)]

    market_context = {
        "meta": {
            "train_start_date": str(train_start.date()),
            "train_end_date": str(train_end.date()),
            "config_source": "runtime/config/market_context.yaml",
            "funding_sources": ["moneyflow_hsgt", "margin"],
            "macro_sources": ["shibor", "cn_m", "cn_pmi", "cn_cpi", "cn_ppi"],
        },
        "train_context": build_train_context(train_window, thresholds, macro_data=macro_frame, windows=windows),
    }
    write_json(OUTPUT_DIR / "health" / "market_context.json", market_context)
    train_ctx = market_context.get("train_context", {})
    summary_text = train_ctx.get("summary_text", "")
    regime_label = ""
    if train_ctx.get("labels"):
        regime_label = ", ".join(f"{k}={v}" for k, v in train_ctx["labels"].items())
    details = []
    if summary_text:
        details.append(f"市场状态: {summary_text}")
    if regime_label:
        details.append(f"环境标签: {regime_label}")
    log_step_end("03", "市场环境分析完成", details=details)


if __name__ == "__main__":
    run()
