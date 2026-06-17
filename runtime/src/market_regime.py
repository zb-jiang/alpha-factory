from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _cfg_int(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    return int(default if value is None else value)


def _cfg_float(payload: dict[str, Any], key: str, default: float) -> float:
    value = payload.get(key, default)
    return float(default if value is None else value)


def _safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "未知"
    return f"{value * 100:.{digits}f}%"


def _fmt_num(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "未知"
    return f"{value:.{digits}f}"


def _rolling_last_rank_percentile(values: pd.Series, window: int, min_periods: int) -> pd.Series:
    def _last_rank(window_values: pd.Series) -> float:
        sample = pd.Series(window_values).dropna()
        if sample.empty:
            return float("nan")
        return float(sample.rank(pct=True).iloc[-1])

    return values.rolling(window, min_periods=min_periods).apply(_last_rank, raw=False)


def _classify_trend(value: float | None, up: float, down: float) -> str:
    if value is None:
        return "未知"
    if value >= up:
        return "上行"
    if value <= down:
        return "下行"
    return "震荡"


def _classify_rank_level(value: float | None, high: float, low: float) -> str:
    if value is None:
        return "未知"
    if value >= high:
        return "高"
    if value <= low:
        return "低"
    return "中"


def _classify_breadth(value: float | None, risk_on: float, risk_off: float) -> str:
    if value is None:
        return "未知"
    if value >= risk_on:
        return "普涨"
    if value <= risk_off:
        return "普跌"
    return "分化"


def _classify_capital_flow(value: float | None, high: float, low: float) -> str:
    if value is None:
        return "未知"
    if value >= high:
        return "偏流入"
    if value <= low:
        return "偏流出"
    return "中性"


def _classify_leverage(flow_rank: float | None, balance_rank: float | None, high: float, low: float) -> str:
    samples = [value for value in [flow_rank, balance_rank] if value is not None]
    if not samples:
        return "未知"
    composite = float(sum(samples) / len(samples))
    if composite >= high:
        return "升温"
    if composite <= low:
        return "降温"
    return "平稳"


def _classify_capital_structure(northbound_label: str, leverage_label: str) -> str:
    north_in = northbound_label == "偏流入"
    north_out = northbound_label == "偏流出"
    leverage_hot = leverage_label == "升温"
    leverage_cold = leverage_label == "降温"
    if north_in and leverage_hot:
        return "同向进攻"
    if north_out and leverage_cold:
        return "同向防守"
    if north_out and leverage_hot:
        return "外资谨慎/杠杆激进"
    if north_in and leverage_cold:
        return "外资积极/杠杆收缩"
    if northbound_label == "未知" or leverage_label == "未知":
        return "未知"
    return "中性"


def _classify_alignment(value: float | None) -> str:
    if value is None:
        return "未知"
    if value > 0.2:
        return "同向"
    if value < -0.2:
        return "背离"
    return "中性"


def _classify_rate(shibor_change: float | None, easing_threshold: float, tightening_threshold: float) -> str:
    """根据 Shibor 变化量判断利率方向。"""
    if shibor_change is None:
        return "未知"
    if shibor_change <= easing_threshold:
        return "宽松"
    if shibor_change >= tightening_threshold:
        return "收紧"
    return "中性"


def _classify_macro_liquidity(m2_yoy_change: float | None, threshold: float) -> str:
    """根据 M2 同比变化趋势判断宏观流动性。"""
    if m2_yoy_change is None:
        return "未知"
    if m2_yoy_change >= threshold:
        return "扩张"
    if m2_yoy_change <= -threshold:
        return "收缩"
    return "中性"


def _classify_economy(pmi_value: float | None, pmi_change: float | None, expansion_threshold: float) -> str:
    """根据 PMI 水平和趋势判断经济周期。"""
    if pmi_value is None:
        return "未知"
    if pmi_value > expansion_threshold and (pmi_change is None or pmi_change >= 0):
        return "扩张"
    if pmi_value < expansion_threshold and (pmi_change is None or pmi_change <= 0):
        return "收缩"
    return "中性"


def _classify_inflation(cpi_change: float | None, ppi_change: float | None, threshold: float) -> str:
    """根据 CPI 和 PPI 同比变化趋势判断通胀方向。"""
    signals = []
    for change in [cpi_change, ppi_change]:
        if change is not None:
            if change >= threshold:
                signals.append(1)
            elif change <= -threshold:
                signals.append(-1)
            else:
                signals.append(0)
    if not signals:
        return "未知"
    composite = sum(signals) / len(signals)
    if composite > 0:
        return "上行"
    if composite < 0:
        return "下行"
    return "中性"


def _rolling_min_periods(window: int, fallback: int) -> int:
    return max(1, min(window, fallback, max(3, window // 2)))


def _build_mapping_explain(
    trend_value: float | None,
    vol_rank_value: float | None,
    amount_rank_value: float | None,
    dispersion_rank_value: float | None,
    breadth_value: float | None,
    style_value: float | None,
    northbound_rank_value: float | None,
    margin_flow_rank_value: float | None,
    margin_balance_rank_value: float | None,
    capital_structure_label: str,
    labels: dict[str, str],
    thresholds: dict[str, Any],
    macro_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trend_up = _cfg_float(thresholds, "trend_up", 0.03)
    trend_down = _cfg_float(thresholds, "trend_down", -0.03)
    rank_high = _cfg_float(thresholds, "rank_high", 0.67)
    rank_low = _cfg_float(thresholds, "rank_low", 0.33)
    breadth_on = _cfg_float(thresholds, "breadth_risk_on", 0.62)
    breadth_off = _cfg_float(thresholds, "breadth_risk_off", 0.38)
    north_in_high = _cfg_float(thresholds, "northbound_inflow_high", rank_high)
    north_out_low = _cfg_float(thresholds, "northbound_outflow_low", rank_low)
    leverage_hot = _cfg_float(thresholds, "leverage_hot_high", rank_high)
    leverage_cold = _cfg_float(thresholds, "leverage_cold_low", rank_low)
    rate_easing = _cfg_float(thresholds, "rate_easing_threshold", -0.0025)
    rate_tightening = _cfg_float(thresholds, "rate_tightening_threshold", 0.0025)
    m2_change_threshold = _cfg_float(thresholds, "m2_yoy_change_threshold", 0.3)
    pmi_expansion = _cfg_float(thresholds, "pmi_expansion_threshold", 50.0)
    inflation_change_threshold = _cfg_float(thresholds, "inflation_yoy_change_threshold", 0.3)
    return {
        "trend": {
            "value": trend_value,
            "rule": f">={trend_up} 上行, <={trend_down} 下行, 其余震荡",
            "label": labels.get("trend", "未知"),
        },
        "volatility": {
            "value": vol_rank_value,
            "rule": f">={rank_high} 高, <={rank_low} 低, 其余中",
            "label": labels.get("volatility", "未知"),
        },
        "liquidity": {
            "value": amount_rank_value,
            "rule": f">={rank_high} 高, <={rank_low} 低, 其余中",
            "label": labels.get("liquidity", "未知"),
        },
        "dispersion": {
            "value": dispersion_rank_value,
            "rule": f">={rank_high} 高, <={rank_low} 低, 其余中",
            "label": labels.get("dispersion", "未知"),
        },
        "breadth": {
            "value": breadth_value,
            "rule": f">={breadth_on} 普涨, <={breadth_off} 普跌, 其余分化",
            "label": labels.get("breadth", "未知"),
        },
        "style": {
            "value": style_value,
            "rule": ">0 大盘占优, <0 小盘占优",
            "label": labels.get("style", "未知"),
        },
        "northbound": {
            "value": northbound_rank_value,
            "rule": f">={north_in_high} 偏流入, <={north_out_low} 偏流出, 其余中性",
            "label": labels.get("northbound", "未知"),
        },
        "leverage": {
            "value": {
                "flow_rank": margin_flow_rank_value,
                "balance_rank": margin_balance_rank_value,
            },
            "rule": f"融资净流/融资余额综合分位 >={leverage_hot} 升温, <={leverage_cold} 降温, 其余平稳",
            "label": labels.get("leverage", "未知"),
        },
        "capital_structure": {
            "value": capital_structure_label,
            "rule": "结合北向资金方向与杠杆情绪共同判定资金结构",
            "label": labels.get("capital_structure", "未知"),
        },
        "rate": {
            "value": {
                "shibor_1y_current": macro_stats.get("shibor_1y_current") if macro_stats else None,
                "shibor_1y_change": macro_stats.get("shibor_1y_change") if macro_stats else None,
            },
            "rule": f"Shibor 1Y 变化量 <={rate_easing} 宽松, >={rate_tightening} 收紧, 其余中性",
            "label": labels.get("rate", "未知"),
        },
        "macro_liquidity": {
            "value": {
                "m2_yoy_current": macro_stats.get("m2_yoy_current") if macro_stats else None,
                "m2_yoy_change": macro_stats.get("m2_yoy_change") if macro_stats else None,
            },
            "rule": f"M2 同比变化 >={m2_change_threshold} 扩张, <=-{m2_change_threshold} 收缩, 其余中性",
            "label": labels.get("macro_liquidity", "未知"),
        },
        "economy": {
            "value": {
                "pmi_current": macro_stats.get("pmi_current") if macro_stats else None,
                "pmi_change": macro_stats.get("pmi_change") if macro_stats else None,
            },
            "rule": f"PMI >{pmi_expansion} 且趋势向上 扩张, PMI <{pmi_expansion} 且趋势向下 收缩, 其余中性",
            "label": labels.get("economy", "未知"),
        },
        "inflation": {
            "value": {
                "cpi_yoy_current": macro_stats.get("cpi_yoy_current") if macro_stats else None,
                "cpi_yoy_change": macro_stats.get("cpi_yoy_change") if macro_stats else None,
                "ppi_yoy_current": macro_stats.get("ppi_yoy_current") if macro_stats else None,
                "ppi_yoy_change": macro_stats.get("ppi_yoy_change") if macro_stats else None,
            },
            "rule": f"CPI/PPI 同比变化 >={inflation_change_threshold} 上行, <=-{inflation_change_threshold} 下行, 其余中性",
            "label": labels.get("inflation", "未知"),
        },
    }


def compute_daily_market(
    raw_frame: pd.DataFrame,
    fields: dict[str, str],
    windows: dict[str, Any],
    external_market_data: pd.DataFrame | None = None,
) -> pd.DataFrame:
    close_col = str(fields.get("close", "close"))
    turnover_col = str(fields.get("turnover", "turnover"))
    amount_col = str(fields.get("amount", "amount"))
    market_cap_col = str(fields.get("market_cap", "market_cap"))

    trend_days = _cfg_int(windows, "trend_days", 20)
    vol_days = _cfg_int(windows, "volatility_days", 20)
    disp_days = _cfg_int(windows, "dispersion_days", 20)
    rank_days = _cfg_int(windows, "rank_lookback_days", 250)
    min_size_sample = _cfg_int(windows, "min_size_style_sample", 20)
    min_roll = _cfg_int(windows, "min_rolling_periods", max(10, trend_days // 2))
    min_rank = _cfg_int(windows, "min_rank_periods", max(20, rank_days // 5))
    northbound_days = _cfg_int(windows, "northbound_days", 5)
    margin_days = _cfg_int(windows, "margin_days", 5)
    flow_rank_days = _cfg_int(windows, "flow_rank_lookback_days", rank_days)

    frame = raw_frame.sort_index().copy()
    frame["ret_1d"] = frame[close_col].groupby(level="instrument").pct_change(fill_method=None)
    frame["up_flag"] = (frame["ret_1d"] > 0).astype(float)
    frame = frame.reset_index()

    daily_market = frame.groupby("datetime").agg(
        market_return=("ret_1d", "mean"),
        cross_dispersion=("ret_1d", "std"),
        market_turnover=(turnover_col, "mean"),
        market_amount=(amount_col, "sum"),
        breadth_up_ratio=("up_flag", "mean"),
    )

    def _size_style_return(group: pd.DataFrame) -> float:
        data = group[[market_cap_col, "ret_1d"]].dropna()
        if len(data) < min_size_sample:
            return float("nan")
        threshold = float(data[market_cap_col].median())
        large = data.loc[data[market_cap_col] >= threshold, "ret_1d"]
        small = data.loc[data[market_cap_col] < threshold, "ret_1d"]
        if large.empty or small.empty:
            return float("nan")
        return float(large.mean() - small.mean())

    daily_market["size_style_spread"] = (
        frame.groupby("datetime", group_keys=False)[[market_cap_col, "ret_1d"]]
        .apply(_size_style_return)
    )
    daily_market["trend_20d"] = daily_market["market_return"].rolling(trend_days, min_periods=min_roll).sum()
    daily_market["volatility_20d"] = daily_market["market_return"].rolling(vol_days, min_periods=min_roll).std()
    daily_market["dispersion_20d"] = daily_market["cross_dispersion"].rolling(disp_days, min_periods=min_roll).mean()
    daily_market["turnover_rank_250d"] = _rolling_last_rank_percentile(daily_market["market_turnover"], rank_days, min_rank)
    daily_market["amount_rank_250d"] = _rolling_last_rank_percentile(daily_market["market_amount"], rank_days, min_rank)
    daily_market["vol_rank_250d"] = _rolling_last_rank_percentile(daily_market["volatility_20d"], rank_days, min_rank)
    daily_market["dispersion_rank_250d"] = _rolling_last_rank_percentile(daily_market["dispersion_20d"], rank_days, min_rank)

    market_columns = [
        "ggt_ss",
        "ggt_sz",
        "hgt",
        "sgt",
        "north_money",
        "south_money",
        "rzye",
        "rzmre",
        "rzche",
        "rqye",
        "rqmcl",
        "rzrqye",
        "rqyl",
    ]
    if external_market_data is not None and not external_market_data.empty:
        external = external_market_data.copy()
        external.index = pd.to_datetime(external.index)
        for column in market_columns:
            if column not in external.columns:
                external[column] = np.nan
            external[column] = pd.to_numeric(external[column], errors="coerce")
        daily_market = daily_market.join(external[market_columns], how="left")
    else:
        for column in market_columns:
            daily_market[column] = np.nan

    north_min_periods = _rolling_min_periods(northbound_days, min_roll)
    margin_min_periods = _rolling_min_periods(margin_days, min_roll)
    flow_min_rank = max(1, min(flow_rank_days, max(min_rank, flow_rank_days // 5)))
    daily_market["northbound_flow_window"] = daily_market["north_money"].rolling(
        northbound_days,
        min_periods=north_min_periods,
    ).sum()
    daily_market["northbound_flow_rank"] = _rolling_last_rank_percentile(
        daily_market["northbound_flow_window"],
        flow_rank_days,
        flow_min_rank,
    )
    daily_market["northbound_inflow_ratio"] = (
        (daily_market["north_money"] > 0)
        .astype(float)
        .rolling(northbound_days, min_periods=north_min_periods)
        .mean()
    )
    trend_sign = np.sign(daily_market["trend_20d"])
    north_sign = np.sign(daily_market["northbound_flow_window"])
    daily_market["northbound_trend_alignment"] = north_sign * trend_sign

    daily_market["margin_net_flow"] = daily_market["rzmre"] - daily_market["rzche"]
    daily_market["margin_flow_window"] = daily_market["margin_net_flow"].rolling(
        margin_days,
        min_periods=margin_min_periods,
    ).sum()
    daily_market["margin_flow_rank"] = _rolling_last_rank_percentile(
        daily_market["margin_flow_window"],
        flow_rank_days,
        flow_min_rank,
    )
    daily_market["margin_balance_rank"] = _rolling_last_rank_percentile(
        daily_market["rzye"],
        flow_rank_days,
        flow_min_rank,
    )
    daily_market["short_balance_change"] = daily_market["rqye"].pct_change(
        periods=margin_days,
        fill_method=None,
    )
    return daily_market.sort_index()


def build_window_context(
    window_frame: pd.DataFrame,
    thresholds: dict[str, Any],
    macro_data: pd.DataFrame | None = None,
    windows: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if window_frame.empty:
        return {"summary_text": "当前样本期无有效市场数据。", "labels": {}, "stats": {}}

    trend_up = _cfg_float(thresholds, "trend_up", 0.03)
    trend_down = _cfg_float(thresholds, "trend_down", -0.03)
    rank_high = _cfg_float(thresholds, "rank_high", 0.67)
    rank_low = _cfg_float(thresholds, "rank_low", 0.33)
    breadth_on = _cfg_float(thresholds, "breadth_risk_on", 0.62)
    breadth_off = _cfg_float(thresholds, "breadth_risk_off", 0.38)
    north_in_high = _cfg_float(thresholds, "northbound_inflow_high", rank_high)
    north_out_low = _cfg_float(thresholds, "northbound_outflow_low", rank_low)
    leverage_hot = _cfg_float(thresholds, "leverage_hot_high", rank_high)
    leverage_cold = _cfg_float(thresholds, "leverage_cold_low", rank_low)

    trend_mean = _safe_float(window_frame["trend_20d"].mean())
    vol_rank_mean = _safe_float(window_frame["vol_rank_250d"].mean())
    amount_rank_mean = _safe_float(window_frame["amount_rank_250d"].mean())
    dispersion_rank_mean = _safe_float(window_frame["dispersion_rank_250d"].mean())
    breadth_mean = _safe_float(window_frame["breadth_up_ratio"].mean())
    style_mean = _safe_float(window_frame["size_style_spread"].mean())
    northbound_rank_mean = _safe_float(window_frame["northbound_flow_rank"].mean())
    northbound_inflow_ratio_mean = _safe_float(window_frame["northbound_inflow_ratio"].mean())
    northbound_alignment_mean = _safe_float(window_frame["northbound_trend_alignment"].mean())
    margin_flow_rank_mean = _safe_float(window_frame["margin_flow_rank"].mean())
    margin_balance_rank_mean = _safe_float(window_frame["margin_balance_rank"].mean())
    short_balance_change_mean = _safe_float(window_frame["short_balance_change"].mean())

    style_label = "未知" if style_mean is None else ("大盘占优" if style_mean > 0 else "小盘占优")
    labels = {
        "trend": _classify_trend(trend_mean, trend_up, trend_down),
        "volatility": _classify_rank_level(vol_rank_mean, rank_high, rank_low),
        "liquidity": _classify_rank_level(amount_rank_mean, rank_high, rank_low),
        "dispersion": _classify_rank_level(dispersion_rank_mean, rank_high, rank_low),
        "breadth": _classify_breadth(breadth_mean, breadth_on, breadth_off),
        "style": style_label,
        "northbound": _classify_capital_flow(northbound_rank_mean, north_in_high, north_out_low),
        "leverage": _classify_leverage(margin_flow_rank_mean, margin_balance_rank_mean, leverage_hot, leverage_cold),
    }
    labels["capital_structure"] = _classify_capital_structure(labels["northbound"], labels["leverage"])
    north_alignment_label = _classify_alignment(northbound_alignment_mean)

    # ── 宏观维度标签 ──────────────────────────────────────────
    macro_stats: dict[str, Any] = {}
    rate_label = "未知"
    macro_liquidity_label = "未知"
    economy_label = "未知"
    inflation_label = "未知"

    if macro_data is not None and not macro_data.empty and windows is not None:
        rate_easing = _cfg_float(thresholds, "rate_easing_threshold", -0.0025)
        rate_tightening = _cfg_float(thresholds, "rate_tightening_threshold", 0.0025)
        m2_change_threshold = _cfg_float(thresholds, "m2_yoy_change_threshold", 0.3)
        pmi_expansion = _cfg_float(thresholds, "pmi_expansion_threshold", 50.0)
        inflation_change_threshold = _cfg_float(thresholds, "inflation_yoy_change_threshold", 0.3)

        m2_trend_months = _cfg_int(windows, "m2_trend_months", 3)
        pmi_trend_months = _cfg_int(windows, "pmi_trend_months", 3)
        inflation_trend_months = _cfg_int(windows, "inflation_trend_months", 3)

        # 取训练窗口末尾对应的宏观数据
        # 宏观数据是月频，取最后 N 个月来计算趋势
        macro_sorted = macro_data.sort_values("month").reset_index(drop=True)
        last_row = macro_sorted.iloc[-1] if not macro_sorted.empty else None

        # 10. 利率方向：Shibor 1Y 变化
        if "shibor_1y" in macro_sorted.columns and last_row is not None:
            shibor_current = _safe_float(last_row.get("shibor_1y"))
            if shibor_current is not None and len(macro_sorted) >= 2:
                # 找 shibor_trend_days 天前的 Shibor
                shibor_trend_days = _cfg_int(windows, "shibor_trend_days", 20)
                # Shibor 是月均值，往前找约 shibor_trend_days/22 个月
                lookback_months = max(1, shibor_trend_days // 22)
                if len(macro_sorted) > lookback_months:
                    shibor_prev = _safe_float(macro_sorted.iloc[-(lookback_months + 1)].get("shibor_1y"))
                    if shibor_prev is not None:
                        shibor_change = shibor_current - shibor_prev
                        rate_label = _classify_rate(shibor_change, rate_easing, rate_tightening)
                        macro_stats["shibor_1y_current"] = shibor_current
                        macro_stats["shibor_1y_change"] = shibor_change
                    else:
                        macro_stats["shibor_1y_current"] = shibor_current
                        macro_stats["shibor_1y_change"] = None
                else:
                    macro_stats["shibor_1y_current"] = shibor_current
                    macro_stats["shibor_1y_change"] = None
            else:
                macro_stats["shibor_1y_current"] = shibor_current if shibor_current else None
                macro_stats["shibor_1y_change"] = None

        # 11. 宏观流动性：M2 同比变化趋势
        if "m2_yoy" in macro_sorted.columns and last_row is not None:
            m2_yoy_current = _safe_float(last_row.get("m2_yoy"))
            if m2_yoy_current is not None and len(macro_sorted) > m2_trend_months:
                m2_yoy_prev = _safe_float(macro_sorted.iloc[-(m2_trend_months + 1)].get("m2_yoy"))
                if m2_yoy_prev is not None:
                    m2_yoy_change = m2_yoy_current - m2_yoy_prev
                    macro_liquidity_label = _classify_macro_liquidity(m2_yoy_change, m2_change_threshold)
                    macro_stats["m2_yoy_current"] = m2_yoy_current
                    macro_stats["m2_yoy_change"] = m2_yoy_change
                else:
                    macro_stats["m2_yoy_current"] = m2_yoy_current
                    macro_stats["m2_yoy_change"] = None
            else:
                macro_stats["m2_yoy_current"] = m2_yoy_current
                macro_stats["m2_yoy_change"] = None

        # 12. 经济周期：PMI 水平和趋势
        if "pmi_manufacturing" in macro_sorted.columns and last_row is not None:
            pmi_current = _safe_float(last_row.get("pmi_manufacturing"))
            if pmi_current is not None and len(macro_sorted) > pmi_trend_months:
                pmi_prev = _safe_float(macro_sorted.iloc[-(pmi_trend_months + 1)].get("pmi_manufacturing"))
                if pmi_prev is not None:
                    pmi_change = pmi_current - pmi_prev
                    economy_label = _classify_economy(pmi_current, pmi_change, pmi_expansion)
                    macro_stats["pmi_current"] = pmi_current
                    macro_stats["pmi_change"] = pmi_change
                else:
                    economy_label = _classify_economy(pmi_current, None, pmi_expansion)
                    macro_stats["pmi_current"] = pmi_current
                    macro_stats["pmi_change"] = None
            else:
                economy_label = _classify_economy(pmi_current, None, pmi_expansion) if pmi_current is not None else "未知"
                macro_stats["pmi_current"] = pmi_current
                macro_stats["pmi_change"] = None

        # 13. 通胀方向：CPI/PPI 同比变化趋势
        cpi_change_val = None
        ppi_change_val = None
        if "cpi_yoy" in macro_sorted.columns and last_row is not None:
            cpi_current = _safe_float(last_row.get("cpi_yoy"))
            if cpi_current is not None and len(macro_sorted) > inflation_trend_months:
                cpi_prev = _safe_float(macro_sorted.iloc[-(inflation_trend_months + 1)].get("cpi_yoy"))
                if cpi_prev is not None:
                    cpi_change_val = cpi_current - cpi_prev
                    macro_stats["cpi_yoy_current"] = cpi_current
                    macro_stats["cpi_yoy_change"] = cpi_change_val
                else:
                    macro_stats["cpi_yoy_current"] = cpi_current
                    macro_stats["cpi_yoy_change"] = None
            else:
                macro_stats["cpi_yoy_current"] = cpi_current
                macro_stats["cpi_yoy_change"] = None

        if "ppi_yoy" in macro_sorted.columns and last_row is not None:
            ppi_current = _safe_float(last_row.get("ppi_yoy"))
            if ppi_current is not None and len(macro_sorted) > inflation_trend_months:
                ppi_prev = _safe_float(macro_sorted.iloc[-(inflation_trend_months + 1)].get("ppi_yoy"))
                if ppi_prev is not None:
                    ppi_change_val = ppi_current - ppi_prev
                    macro_stats["ppi_yoy_current"] = ppi_current
                    macro_stats["ppi_yoy_change"] = ppi_change_val
                else:
                    macro_stats["ppi_yoy_current"] = ppi_current
                    macro_stats["ppi_yoy_change"] = None
            else:
                macro_stats["ppi_yoy_current"] = ppi_current
                macro_stats["ppi_yoy_change"] = None

        inflation_label = _classify_inflation(cpi_change_val, ppi_change_val, inflation_change_threshold)

    labels["rate"] = rate_label
    labels["macro_liquidity"] = macro_liquidity_label
    labels["economy"] = economy_label
    labels["inflation"] = inflation_label

    summary_text = (
        f"当前市场状态：趋势{labels['trend']}（20日累计收益均值 {_fmt_pct(trend_mean)}），"
        f"波动{labels['volatility']}（分位 {_fmt_num(vol_rank_mean)}），"
        f"成交活跃度{labels['liquidity']}（分位 {_fmt_num(amount_rank_mean)}），"
        f"个股分化{labels['dispersion']}（分位 {_fmt_num(dispersion_rank_mean)}），"
        f"市场广度{labels['breadth']}（上涨占比均值 {_fmt_pct(breadth_mean)}），"
        f"风格偏{labels['style']}（大小盘收益差均值 {_fmt_pct(style_mean, 3)}），"
        f"北向资金{labels['northbound']}（强度分位 {_fmt_num(northbound_rank_mean)}，净流入日占比 {_fmt_pct(northbound_inflow_ratio_mean)}，与趋势{north_alignment_label}），"
        f"两融情绪{labels['leverage']}（融资净流分位 {_fmt_num(margin_flow_rank_mean)}，融资余额分位 {_fmt_num(margin_balance_rank_mean)}，融券余额变化 {_fmt_pct(short_balance_change_mean)}），"
        f"资金结构{labels['capital_structure']}，"
        f"利率{labels['rate']}（Shibor 1Y {_fmt_num(macro_stats.get('shibor_1y_current'), 4)}%，变化 {_fmt_pct(macro_stats.get('shibor_1y_change'), 4)}），"
        f"宏观流动性{labels['macro_liquidity']}（M2同比 {_fmt_num(macro_stats.get('m2_yoy_current'), 2)}%，变化 {_fmt_num(macro_stats.get('m2_yoy_change'), 2)}），"
        f"经济周期{labels['economy']}（PMI {_fmt_num(macro_stats.get('pmi_current'), 2)}），"
        f"通胀{labels['inflation']}（CPI同比 {_fmt_num(macro_stats.get('cpi_yoy_current'), 2)}%，PPI同比 {_fmt_num(macro_stats.get('ppi_yoy_current'), 2)}%）。"
    )
    return {
        "summary_text": summary_text,
        "labels": labels,
        "label_notes": {
            "trend": f"{labels['trend']}（依据 trend_20d_mean 与趋势阈值映射）",
            "volatility": f"{labels['volatility']}（依据 vol_rank_250d_mean 的历史分位映射）",
            "liquidity": f"{labels['liquidity']}（依据 amount_rank_250d_mean 的历史分位映射）",
            "dispersion": f"{labels['dispersion']}（依据 dispersion_rank_250d_mean 的历史分位映射）",
            "breadth": f"{labels['breadth']}（依据 breadth_up_ratio_mean 与广度阈值映射）",
            "style": f"{labels['style']}（依据 size_style_spread_mean 正负映射）",
            "northbound": f"{labels['northbound']}（依据 northbound_flow_rank_mean 的历史分位映射）",
            "leverage": f"{labels['leverage']}（综合融资净流与融资余额分位映射）",
            "capital_structure": f"{labels['capital_structure']}（结合北向资金方向与杠杆情绪共同判断）",
            "rate": f"{labels['rate']}（依据 Shibor 1Y 变化量与利率阈值映射）",
            "macro_liquidity": f"{labels['macro_liquidity']}（依据 M2 同比变化趋势映射）",
            "economy": f"{labels['economy']}（依据 PMI 水平与趋势映射）",
            "inflation": f"{labels['inflation']}（综合 CPI/PPI 同比变化趋势映射）",
        },
        "mapping_explain": _build_mapping_explain(
            trend_mean,
            vol_rank_mean,
            amount_rank_mean,
            dispersion_rank_mean,
            breadth_mean,
            style_mean,
            northbound_rank_mean,
            margin_flow_rank_mean,
            margin_balance_rank_mean,
            labels["capital_structure"],
            labels,
            thresholds,
            macro_stats,
        ),
        "stats": {
            "trend_20d_mean": trend_mean,
            "vol_rank_250d_mean": vol_rank_mean,
            "amount_rank_250d_mean": amount_rank_mean,
            "turnover_rank_250d_mean": _safe_float(window_frame["turnover_rank_250d"].mean()),
            "dispersion_rank_250d_mean": dispersion_rank_mean,
            "breadth_up_ratio_mean": breadth_mean,
            "size_style_spread_mean": style_mean,
            "northbound_flow_rank_mean": northbound_rank_mean,
            "northbound_inflow_ratio_mean": northbound_inflow_ratio_mean,
            "northbound_trend_alignment_mean": northbound_alignment_mean,
            "margin_flow_rank_mean": margin_flow_rank_mean,
            "margin_balance_rank_mean": margin_balance_rank_mean,
            "short_balance_change_mean": short_balance_change_mean,
            **macro_stats,
        },
    }


def build_train_context(
    train_window: pd.DataFrame,
    thresholds: dict[str, Any],
    macro_data: pd.DataFrame | None = None,
    windows: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_window_context(train_window, thresholds, macro_data=macro_data, windows=windows)


__all__ = ["build_train_context", "build_window_context", "compute_daily_market"]
