from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    import rqdatac as rq
except Exception as exc:  # pragma: no cover
    raise RuntimeError("rqdatac is required in Ricequant Notebook environment") from exc

INTERNAL_FIELD_ALIAS: dict[str, str] = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    # Never map to native `vwap` to avoid unsupported-field errors in some RQ accounts.
    # Real VWAP is derived later from amount/volume when needed.
    "vwap": "total_turnover",
    "amount": "total_turnover",
    # Native daily `turnover_rate` may be unavailable on some RQ accounts.
    # `turnover` is derived in `_build_raw_frame` from total_turnover / market_cap.
    "turnover": "total_turnover",
}
INTERNAL_LOOKBACK_DAYS = 80
BASE_DATA_FIELDS = set(INTERNAL_FIELD_ALIAS.keys()) | {"market_cap", "industry"}
INTERNAL_FEATURE_FORMULAS: dict[str, str] = {
    "ret_1d": "close.pct_change(1)",
    "ret_5d": "close.pct_change(5)",
    "ret_20d": "close.pct_change(20)",
    "ret_60d": "close.pct_change(60)",
    "gap_open_ret": "open / close.shift(1) - 1",
    "intraday_ret": "close / open - 1",
    "high_low_range": "high / low - 1",
    "close_to_high": "close / high - 1",
    "close_to_low": "close / low - 1",
    "price_pos_20d": "(close - close.rolling(20).min()) / (close.rolling(20).max() - close.rolling(20).min())",
    "max_drawdown_20d": "close / close.rolling(20).max() - 1",
    "breakout_20d": "close / close.rolling(20).max().shift(1) - 1",
    "realized_vol_20d": "close.pct_change().rolling(20).std()",
    "volume_mean_20d": "volume.rolling(20).mean()",
    "volume_ratio_20d": "volume / volume_mean_20d",
    "volume_vol_20d": "volume.pct_change().rolling(20).std()",
    "amount_mean_20d": "amount.rolling(20).mean()",
    "amount_ratio_20d": "amount / amount_mean_20d",
    "close_to_vwap": "close / vwap - 1",
    "vwap_ret_20d": "vwap.pct_change(20)",
    "turnover_mean_20d": "turnover.rolling(20).mean()",
    "turnover_ratio_20d": "turnover / turnover_mean_20d",
    "market_cap_change_20d": "market_cap.pct_change(20)",
    "market_cap_stability_20d": "market_cap.pct_change().rolling(20).std()",
    "ret_10d": "close.pct_change(10)",
    "intraday_range_mean_5d": "high_low_range.rolling(5).mean()",
    "intraday_range_mean_20d": "high_low_range.rolling(20).mean()",
    "price_pos_10d": "(close - close.rolling(10).min()) / (close.rolling(10).max() - close.rolling(10).min())",
    "price_pos_60d": "(close - close.rolling(60).min()) / (close.rolling(60).max() - close.rolling(60).min())",
    "max_drawdown_10d": "close / close.rolling(10).max() - 1",
    "max_drawdown_60d": "close / close.rolling(60).max() - 1",
    "rebound_20d": "close / close.rolling(20).min().shift(1) - 1",
    "realized_vol_5d": "close.pct_change().rolling(5).std()",
    "realized_vol_60d": "close.pct_change().rolling(60).std()",
    "volume_mean_5d": "volume.rolling(5).mean()",
    "volume_mean_60d": "volume.rolling(60).mean()",
    "volume_ratio_5d": "volume / volume_mean_5d",
    "amount_mean_5d": "amount.rolling(5).mean()",
    "amount_vol_20d": "amount.pct_change().rolling(20).std()",
    "high_to_vwap": "high / vwap - 1",
    "low_to_vwap": "low / vwap - 1",
    "vwap_ret_5d": "vwap.pct_change(5)",
    "close_to_vwap_mean_5d": "close_to_vwap.rolling(5).mean()",
    "turnover_mean_5d": "turnover.rolling(5).mean()",
    "turnover_vol_20d": "turnover.pct_change().rolling(20).std()",
}

@dataclass
class ValidationResult:
    metrics: dict[str, Any]
    factor_series: pd.Series
    label_series: pd.Series


def _require_config(config: dict[str, Any]) -> None:
    required = [
        "start_date",
        "end_date",
        "stock_pool",
        "price_adjust",
        "rebalance",
        "rebalance_interval",
        "rebalance_anchor",
        "label",
        "preprocess",
    ]
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"Missing required config keys: {', '.join(missing)}")

    stock_pool = config.get("stock_pool")
    if not isinstance(stock_pool, dict):
        raise ValueError("config.stock_pool must be a dict")
    missing_pool = [key for key in ["type", "index_code", "dynamic_membership", "include_st", "include_new_stock", "new_stock_days"] if key not in stock_pool]
    if missing_pool:
        raise ValueError(f"Missing required config.stock_pool keys: {', '.join(missing_pool)}")

    label = config.get("label")
    if not isinstance(label, dict):
        raise ValueError("config.label must be a dict")
    missing_label = [key for key in ["name", "return_type", "price_field"] if key not in label]
    if missing_label:
        raise ValueError(f"Missing required config.label keys: {', '.join(missing_label)}")

    preprocess = config.get("preprocess")
    if not isinstance(preprocess, dict):
        raise ValueError("config.preprocess must be a dict")
    missing_preprocess = [key for key in ["outlier_method", "outlier_options", "neutralization", "neutralization_options"] if key not in preprocess]
    if missing_preprocess:
        raise ValueError(f"Missing required config.preprocess keys: {', '.join(missing_preprocess)}")


def normalize_index_code(index_code: str) -> str:
    code = str(index_code).strip().upper()
    if code.endswith(".XSHG") or code.endswith(".XSHE"):
        return code
    if code.startswith("SH") and len(code) == 8:
        return f"{code[2:]}.XSHG"
    if code.startswith("SZ") and len(code) == 8:
        return f"{code[2:]}.XSHE"
    if len(code) == 6:
        return f"{code}.XSHG" if code.startswith(("0", "9")) else f"{code}.XSHE"
    return code


def normalize_stock_code(code: str) -> str:
    text = str(code).strip().upper()
    if text.endswith(".XSHG") or text.endswith(".XSHE"):
        return text
    if text.startswith("SH") and len(text) == 8:
        return f"{text[2:]}.XSHG"
    if text.startswith("SZ") and len(text) == 8:
        return f"{text[2:]}.XSHE"
    if len(text) == 6:
        return f"{text}.XSHG" if text.startswith(("5", "6", "9")) else f"{text}.XSHE"
    return text


def active_window(config: dict[str, Any]) -> tuple[pd.Timestamp, pd.Timestamp]:
    if "start_date" not in config or "end_date" not in config:
        raise ValueError("config must include start_date and end_date")
    return pd.Timestamp(config["start_date"]), pd.Timestamp(config["end_date"])


def _rolling_group(series: pd.Series, window: int, method: str) -> pd.Series:
    window = int(window)
    return series.groupby(level="instrument", group_keys=False).apply(
        lambda s: getattr(s.rolling(window=window, min_periods=1), method)()
    )


def _rolling_rank(series: pd.Series, window: int) -> pd.Series:
    return series.groupby(level="instrument", group_keys=False).apply(
        lambda s: s.rolling(window=window, min_periods=1).apply(
            lambda x: pd.Series(x).rank(method="average").iloc[-1], raw=False
        )
    )


def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    mean = _rolling_group(series, window, "mean")
    std = _rolling_group(series, window, "std").replace(0, np.nan)
    return (series - mean) / std


def _rolling_corr(left: pd.Series, right: pd.Series, window: int) -> pd.Series:
    frame = pd.concat([left.rename("left"), right.rename("right")], axis=1)
    out: list[pd.Series] = []
    for _, group in frame.groupby(level="instrument", sort=False):
        local = group.droplevel("instrument")
        corr = local["left"].rolling(window=window, min_periods=1).corr(local["right"])
        corr.index = group.index
        out.append(corr)
    return pd.concat(out).sort_index()


def _cross_section_zscore(series: pd.Series) -> pd.Series:
    dt = series.index.get_level_values("datetime")
    mean = series.groupby(dt).transform("mean")
    std = series.groupby(dt).transform("std").replace(0, np.nan)
    return (series - mean) / std


def _cross_section_minmax(series: pd.Series) -> pd.Series:
    dt = series.index.get_level_values("datetime")
    low = series.groupby(dt).transform("min")
    high = series.groupby(dt).transform("max")
    return (series - low) / (high - low).replace(0, np.nan)


def winsorize(series: pd.Series, lower_quantile: float = 0.01, upper_quantile: float = 0.99) -> pd.Series:
    dt = series.index.get_level_values("datetime")
    lower = series.groupby(dt).transform(lambda s: s.quantile(lower_quantile))
    upper = series.groupby(dt).transform(lambda s: s.quantile(upper_quantile))
    return series.clip(lower=lower, upper=upper)


def mad_clip(series: pd.Series, n: float = 3.0) -> pd.Series:
    dt = series.index.get_level_values("datetime")
    median = series.groupby(dt).transform("median")
    mad = (series - median).abs().groupby(dt).transform("median")
    scale = 1.4826 * mad
    lower = median - n * scale
    upper = median + n * scale
    return series.clip(lower=lower, upper=upper)


def sigma_clip(series: pd.Series, n: float = 3.0) -> pd.Series:
    dt = series.index.get_level_values("datetime")
    mean = series.groupby(dt).transform("mean")
    std = series.groupby(dt).transform("std")
    lower = mean - n * std
    upper = mean + n * std
    return series.clip(lower=lower, upper=upper)


OPERATOR_ENV = {
    "add": lambda left, right: left + right,
    "sub": lambda left, right: left - right,
    "mul": lambda left, right: left * right,
    "div": lambda left, right: left / right.replace(0, np.nan) if isinstance(right, pd.Series) else left / right,
    "abs": lambda x: np.abs(x),
    "log": lambda x: np.log(x.replace(0, np.nan)),
    "sqrt": lambda x: np.sqrt(np.maximum(x, 0)),
    "sign": lambda x: np.sign(x),
    "rolling_mean": lambda series, window: _rolling_group(series, window, "mean"),
    "rolling_std": lambda series, window: _rolling_group(series, window, "std"),
    "rolling_sum": lambda series, window: _rolling_group(series, window, "sum"),
    "rolling_max": lambda series, window: _rolling_group(series, window, "max"),
    "rolling_min": lambda series, window: _rolling_group(series, window, "min"),
    "delay": lambda series, period: series.groupby(level="instrument").shift(int(period)),
    "delta": lambda series, period: series - series.groupby(level="instrument").shift(int(period)),
    "pct_change": lambda series, period=1: series.groupby(level="instrument").pct_change(int(period), fill_method=None),
    "ema": lambda series, span: series.groupby(level="instrument").transform(lambda x: x.ewm(span=span, adjust=False).mean()),
    "ts_rank": lambda series, window: _rolling_rank(series, int(window)),
    "ts_zscore": lambda series, window: _rolling_zscore(series, int(window)),
    "rolling_corr": lambda left, right, window: _rolling_corr(left, right, int(window)),
    "rank": lambda series: series.groupby(level="datetime").rank(method="average"),
    "zscore": _cross_section_zscore,
    "minmax": _cross_section_minmax,
    "clip": lambda series, lower, upper: series.clip(lower=lower, upper=upper),
    "winsorize": lambda series, lower=0.01, upper=0.99: winsorize(series, lower, upper),
}


def _cross_section_regression_residual(series: pd.Series, design: pd.DataFrame) -> pd.Series:
    out = pd.Series(np.nan, index=series.index, dtype=float)
    for dt, idx in series.groupby(level="datetime").groups.items():
        y = series.loc[idx].astype(float)
        x = design.loc[idx].astype(float)
        valid = y.notna() & x.notna().all(axis=1)
        if valid.sum() <= x.shape[1]:
            continue
        yv = y.loc[valid].to_numpy()
        xv = x.loc[valid].to_numpy()
        beta, _, _, _ = np.linalg.lstsq(xv, yv, rcond=None)
        resid = yv - xv @ beta
        out.loc[y.loc[valid].index] = resid
    return out


def apply_factor_preprocess(factor_series: pd.Series, raw_frame: pd.DataFrame, config: dict[str, Any]) -> pd.Series:
    preprocess = dict(config.get("preprocess", {}))
    outlier_method = str(preprocess.get("outlier_method", "none")).strip().lower()
    outlier_options = dict(preprocess.get("outlier_options", {}))
    score = factor_series.astype(float).copy()
    if outlier_method == "mad":
        score = mad_clip(score, float(outlier_options.get("n", 3.0)))
    elif outlier_method == "quantile":
        score = winsorize(
            score,
            float(outlier_options.get("lower_quantile", 0.01)),
            float(outlier_options.get("upper_quantile", 0.99)),
        )
    elif outlier_method == "sigma":
        score = sigma_clip(score, float(outlier_options.get("n", 3.0)))

    neutralization = str(preprocess.get("neutralization", "none")).strip().lower()
    opts = dict(preprocess.get("neutralization_options", {}))
    industry_field = str(opts.get("industry_field", "industry")).strip()
    market_cap_field = str(opts.get("market_cap_field", "market_cap")).strip()
    if neutralization == "industry":
        industries = raw_frame[industry_field]
        dt = score.index.get_level_values("datetime")
        group_mean = pd.concat([score.rename("score"), industries.rename("industry")], axis=1).groupby([dt, industries])["score"].transform("mean")
        score = score - group_mean
    elif neutralization == "market_cap":
        market_cap = raw_frame[market_cap_field]
        design = pd.DataFrame({"const": 1.0, "log_market_cap": np.log(market_cap.where(market_cap > 0))}, index=score.index)
        score = _cross_section_regression_residual(score, design)
    elif neutralization == "industry_market_cap":
        industries = raw_frame[industry_field]
        market_cap = raw_frame[market_cap_field]
        dummies = pd.get_dummies(industries, prefix="industry", drop_first=True, dummy_na=False).astype(float)
        design = pd.concat(
            [pd.Series(1.0, index=score.index, name="const"), np.log(market_cap.where(market_cap > 0)).rename("log_market_cap"), dummies],
            axis=1,
        )
        score = _cross_section_regression_residual(score, design)
    return score.rename(factor_series.name)


def _cross_section_corr(score: pd.Series, label: pd.Series, method: str = "pearson") -> float:
    frame = pd.concat([score.rename("score"), label.rename("label")], axis=1).dropna()
    if len(frame) < 2:
        return float("nan")
    x = frame["score"].astype(float)
    y = frame["label"].astype(float)
    if method == "spearman":
        x = x.rank(method="average")
        y = y.rank(method="average")
    if x.nunique(dropna=True) < 2 or y.nunique(dropna=True) < 2:
        return float("nan")
    return float(x.corr(y))


def analysis_observation_dates(dates: pd.Index, config: dict[str, Any]) -> list[pd.Timestamp]:
    unique_dates = pd.Index(pd.to_datetime(sorted(pd.unique(dates))))
    rebalance = str(config.get("rebalance", "weekly")).strip().lower()
    interval = max(int(config.get("rebalance_interval", 1) or 1), 1)
    default_anchor = "first_trading_day_of_month" if rebalance == "monthly" else "first_trading_day_of_week"
    anchor = str(config.get("rebalance_anchor", default_anchor)).strip().lower()

    if rebalance == "daily":
        return [pd.Timestamp(d) for d in unique_dates[::interval]]

    if rebalance == "weekly":
        week_keys = (unique_dates - pd.to_timedelta(unique_dates.weekday, unit="D")).normalize()
        grouped = unique_dates.to_series().groupby(week_keys)
        selected = grouped.first().tolist() if anchor.startswith("first") else grouped.last().tolist()
        return [pd.Timestamp(d) for d in selected][::interval]

    if rebalance == "monthly":
        grouped = unique_dates.to_series().groupby(unique_dates.to_period("M"))
        selected = grouped.first().tolist() if anchor.startswith("first") else grouped.last().tolist()
        return [pd.Timestamp(d) for d in selected][::interval]

    raise ValueError(f"Unsupported rebalance: {rebalance}")


def _extract_formula_tokens(formula: str) -> set[str]:
    tree = ast.parse(formula, mode="eval")
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
    reserved = set(OPERATOR_ENV.keys()) | {"np", "pd", "True", "False", "None"}
    return {x for x in names if x not in reserved}


def _to_frame_date_stock(obj: Any, value_name: str) -> pd.DataFrame:
    def _is_date_like_index(idx: pd.Index) -> bool:
        sample = pd.Index(idx[: min(len(idx), 50)])
        parsed = pd.to_datetime(sample, errors="coerce")
        return bool(parsed.notna().mean() >= 0.8) if len(sample) else False

    # Unwrap common container payloads from rqdatac (dict/xarray-like wrappers).
    if isinstance(obj, dict) and obj:
        # Prefer exact key hit, otherwise first value.
        if value_name in obj:
            obj = obj[value_name]
        else:
            obj = next(iter(obj.values()))

    if hasattr(obj, "to_pandas") and not isinstance(obj, (pd.Series, pd.DataFrame)):
        try:
            obj = obj.to_pandas()
        except Exception:
            pass

    if isinstance(obj, pd.Series):
        if isinstance(obj.index, pd.MultiIndex):
            names = [str(n).lower() for n in obj.index.names]
            if "order_book_id" in names and ("date" in names or "datetime" in names):
                date_level = [i for i, n in enumerate(names) if n in ("date", "datetime")][0]
                id_level = [i for i, n in enumerate(names) if n == "order_book_id"][0]
                s = obj.reorder_levels([date_level, id_level]).sort_index()
                return s.unstack(level=1)
            if obj.index.nlevels == 2:
                lv0 = obj.index.get_level_values(0)
                lv1 = obj.index.get_level_values(1)
                if _is_date_like_index(pd.Index(lv0)) and not _is_date_like_index(pd.Index(lv1)):
                    return obj.unstack(level=1).sort_index()
                if _is_date_like_index(pd.Index(lv1)) and not _is_date_like_index(pd.Index(lv0)):
                    s = obj.reorder_levels([1, 0]).sort_index()
                    return s.unstack(level=1)
        return obj.to_frame(name=value_name)

    if isinstance(obj, pd.DataFrame):
        # Common case: date index x instrument columns.
        if not isinstance(obj.index, pd.MultiIndex):
            if isinstance(obj.columns, pd.MultiIndex):
                col_names = [str(n).lower() for n in obj.columns.names]
                # Try to select the instrument level if one level is factor-like.
                if "order_book_id" in col_names:
                    id_level = [i for i, n in enumerate(col_names) if n == "order_book_id"][0]
                    if obj.columns.nlevels == 2:
                        if id_level == 0:
                            return obj.droplevel(1, axis=1).sort_index()
                        return obj.droplevel(0, axis=1).sort_index()
            return obj.sort_index()
        names = [str(n).lower() for n in obj.index.names]
        if "order_book_id" in names and ("date" in names or "datetime" in names):
            date_level = [i for i, n in enumerate(names) if n in ("date", "datetime")][0]
            id_level = [i for i, n in enumerate(names) if n == "order_book_id"][0]
            s = obj.iloc[:, 0]
            s = s.reorder_levels([date_level, id_level]).sort_index()
            return s.unstack(level=1)
        # Fallback for unnamed 2-level MultiIndex payloads from some rq.get_factor responses.
        if obj.index.nlevels == 2:
            lv0 = obj.index.get_level_values(0)
            lv1 = obj.index.get_level_values(1)
            if _is_date_like_index(pd.Index(lv0)) and not _is_date_like_index(pd.Index(lv1)):
                s = obj.iloc[:, 0]
                return s.unstack(level=1).sort_index()
            if _is_date_like_index(pd.Index(lv1)) and not _is_date_like_index(pd.Index(lv0)):
                s = obj.iloc[:, 0]
                s = s.reorder_levels([1, 0]).sort_index()
                return s.unstack(level=1)
    # Last-resort attempt for custom objects exposing `.to_frame()`.
    if hasattr(obj, "to_frame"):
        try:
            casted = obj.to_frame()
            if isinstance(casted, pd.DataFrame):
                return _to_frame_date_stock(casted, value_name)
        except Exception:
            pass

    raise ValueError(f"Unsupported data shape for {value_name}: type={type(obj)}")


def _get_price_field(order_book_ids: list[str], start_date: str, end_date: str, field_name: str, adjust_type: str) -> pd.DataFrame:
    if isinstance(field_name, (list, tuple)):
        if len(field_name) != 1:
            raise ValueError(f"field_name must be a single field, got: {field_name}")
        field_name = str(field_name[0])
    else:
        field_name = str(field_name)
    field_name = field_name.strip()

    # Final safety fallback: never request native daily `vwap` from rq.get_price.
    # Any field token containing `vwap` is routed to derived VWAP.
    normalized = re.sub(r"[^a-z_]", "", field_name.lower())
    if "vwap" in normalized:
        return _get_vwap_field(order_book_ids, start_date, end_date, adjust_type)
    try:
        payload = rq.get_price(
            order_book_ids,
            start_date=start_date,
            end_date=end_date,
            frequency="1d",
            fields=[field_name],
            adjust_type=adjust_type,
            expect_df=True,
        )
    except ValueError as exc:
        msg = str(exc).lower()
        if "invalided value vwap" in msg:
            return _get_vwap_field(order_book_ids, start_date, end_date, adjust_type)
        raise
    frame = _to_frame_date_stock(payload, field_name)
    return frame.sort_index()


def _derive_vwap(amount: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    safe_volume = volume.apply(pd.to_numeric, errors="coerce")
    # Fixed RQ logic: total_turnover / volume
    return amount / (safe_volume + 1e-8)


def _get_adjust_multiplier(
    order_book_ids: list[str],
    start_date: str,
    end_date: str,
    adjust_type: str,
) -> pd.DataFrame:
    mode = str(adjust_type or "none").strip().lower()
    if mode not in {"pre", "post"}:
        base_close = _get_price_field(order_book_ids, start_date, end_date, "close", "none")
        return pd.DataFrame(1.0, index=base_close.index, columns=base_close.columns)

    close_raw = _get_price_field(order_book_ids, start_date, end_date, "close", "none")
    close_adj = _get_price_field(order_book_ids, start_date, end_date, "close", mode)
    multiplier = close_adj.div(close_raw.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    # Align with local logic: missing adjustment factors are filled within each instrument timeline.
    return multiplier.ffill().bfill()


def _get_vwap_field(
    order_book_ids: list[str],
    start_date: str,
    end_date: str,
    adjust_type: str,
) -> pd.DataFrame:
    # Some RQ accounts do not expose native `vwap` in daily fields.
    # Fallback to derived VWAP from raw total_turnover / volume, then apply explicit adjustment.
    amount = _get_price_field(order_book_ids, start_date, end_date, "total_turnover", "none")
    volume = _get_price_field(order_book_ids, start_date, end_date, "volume", "none")
    vwap = _derive_vwap(amount, volume)
    multiplier = _get_adjust_multiplier(order_book_ids, start_date, end_date, adjust_type)
    vwap = vwap * multiplier
    return vwap.sort_index()


def _get_market_cap(order_book_ids: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    factor_candidates = ["market_cap", "a_share_market_val", "total_market_cap"]
    last_error: Exception | None = None
    for factor_name in factor_candidates:
        try:
            payload = rq.get_factor(order_book_ids, factor_name, start_date, end_date)
            return _to_frame_date_stock(payload, factor_name)
        except Exception as exc:  # pragma: no cover
            last_error = exc
    raise RuntimeError("Cannot fetch market_cap from rqdatac.get_factor") from last_error


def _get_turnover_field(order_book_ids: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    # Prefer native turnover factor to align with local `turnover_rate` semantics.
    factor_candidates = ["turnover_rate", "free_turnover_rate", "turnover"]
    for factor_name in factor_candidates:
        try:
            payload = rq.get_factor(order_book_ids, factor_name, start_date, end_date)
            frame = _to_frame_date_stock(payload, factor_name)
            if not frame.empty:
                return frame.sort_index()
        except Exception:
            continue

    # Fallback for accounts without turnover factors: amount / market_cap * 100.
    amount = _get_price_field(order_book_ids, start_date, end_date, "total_turnover", "none")
    cap = _get_market_cap(order_book_ids, start_date, end_date)
    return amount.div(cap.replace(0, np.nan)).mul(100.0).sort_index()


def _get_industry_map(order_book_ids: list[str], date: pd.Timestamp) -> dict[str, str]:
    if hasattr(rq, "shenwan_instrument_industry"):
        payload = rq.shenwan_instrument_industry(order_book_ids, date=date)
        if isinstance(payload, pd.DataFrame):
            possible_cols = [c for c in payload.columns if "industry" in str(c).lower()]
            col = possible_cols[0] if possible_cols else payload.columns[-1]
            if "order_book_id" in payload.columns:
                return payload.set_index("order_book_id")[col].astype(str).to_dict()
            if payload.index.name == "order_book_id":
                return payload[col].astype(str).to_dict()
        if isinstance(payload, dict):
            return {str(k): str(v) for k, v in payload.items()}
    raise RuntimeError("Cannot fetch industry classification, check shenwan_instrument_industry availability")


def _get_listed_date_map(order_book_ids: list[str]) -> dict[str, pd.Timestamp]:
    listed: dict[str, pd.Timestamp] = {}
    for code in order_book_ids:
        ins = rq.instruments(code)
        date_text = getattr(ins, "listed_date", None) or getattr(ins, "listed_datetime", None)
        listed[code] = pd.Timestamp(date_text).normalize() if date_text else pd.Timestamp("1900-01-01")
    return listed


def _get_dynamic_stock_pool(config: dict[str, Any], observation_dates: list[pd.Timestamp]) -> dict[pd.Timestamp, set[str]]:
    pool_cfg = dict(config.get("stock_pool", {}))
    pool_type = str(pool_cfg.get("type", "index_components")).strip().lower()
    dynamic_membership = bool(pool_cfg.get("dynamic_membership", True))

    if pool_type == "custom":
        codes = {normalize_stock_code(code) for code in pool_cfg.get("custom_instruments", [])}
        return {pd.Timestamp(d).normalize(): set(codes) for d in observation_dates}

    if pool_type == "all_market":
        universe = rq.all_instruments(type="CS", date=observation_dates[-1])
        codes = set(universe["order_book_id"].astype(str).tolist())
        return {pd.Timestamp(d).normalize(): set(codes) for d in observation_dates}

    index_code = normalize_index_code(pool_cfg.get("index_code", "SH000300"))
    if dynamic_membership:
        return {pd.Timestamp(d).normalize(): set(rq.index_components(index_code, date=d)) for d in observation_dates}

    fixed = set(rq.index_components(index_code, date=observation_dates[-1]))
    return {pd.Timestamp(d).normalize(): set(fixed) for d in observation_dates}


def _apply_pool_filters(
    component_map: dict[pd.Timestamp, set[str]],
    config: dict[str, Any],
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> dict[pd.Timestamp, set[str]]:
    pool_cfg = dict(config.get("stock_pool", {}))
    include_st = bool(pool_cfg.get("include_st", True))
    include_new = bool(pool_cfg.get("include_new_stock", True))
    new_stock_days = int(pool_cfg.get("new_stock_days", 60) or 60)
    if include_st and include_new:
        return component_map

    all_codes = sorted({code for codes in component_map.values() for code in codes})
    listed_map = _get_listed_date_map(all_codes) if not include_new else {}
    st_table: pd.DataFrame | None = None
    if not include_st:
        st_table = rq.is_st_stock(all_codes, start_date=start_date, end_date=end_date).astype(bool)
        st_table.index = pd.to_datetime(st_table.index).normalize()

    out: dict[pd.Timestamp, set[str]] = {}
    for dt, codes in component_map.items():
        keep: set[str] = set()
        cutoff = pd.Timestamp(dt).normalize()
        for code in codes:
            if not include_new:
                listed_date = listed_map.get(code, pd.Timestamp("1900-01-01"))
                if (cutoff - listed_date).days < new_stock_days:
                    continue
            if st_table is not None and code in st_table.columns:
                if bool(st_table.loc[cutoff, code]):
                    continue
            keep.add(code)
        out[cutoff] = keep
    return out


def _build_membership_mask(index: pd.MultiIndex, component_map: dict[pd.Timestamp, set[str]]) -> pd.Series:
    dt = pd.to_datetime(index.get_level_values("datetime")).normalize()
    ins = index.get_level_values("instrument")
    values = [instrument in component_map.get(date, set()) for instrument, date in zip(ins, dt)]
    return pd.Series(values, index=index, dtype=bool)


def build_label_series(raw_frame: pd.DataFrame, config: dict[str, Any], component_map: dict[pd.Timestamp, set[str]]) -> pd.Series:
    price_field = str(config.get("label", {}).get("price_field", "close")).strip()
    if price_field not in raw_frame.columns:
        raise KeyError(f"label price field not found: {price_field}")
    prices = raw_frame[price_field].sort_index()
    observation_dates = analysis_observation_dates(prices.index.get_level_values("datetime"), config)
    if len(observation_dates) < 2:
        return pd.Series(index=prices.index, dtype=float, name=str(config.get("label", {}).get("name", "label")))
    obs_idx = pd.Index(pd.to_datetime(observation_dates))
    observation_prices = prices.loc[prices.index.get_level_values("datetime").isin(obs_idx)]
    period_return = observation_prices.groupby(level="instrument").shift(-1) / observation_prices - 1
    dynamic_mask = _build_membership_mask(period_return.index, component_map)
    period_return = period_return.loc[dynamic_mask]
    label_name = str(config.get("label", {}).get("name", "rebalance_period_return"))
    label = pd.Series(index=prices.index, dtype=float, name=label_name)
    label.loc[period_return.index] = period_return
    return label


def factor_metrics_from_series(
    factor_name: str,
    factor_series: pd.Series,
    label_series: pd.Series,
    config: dict[str, Any],
    component_map: dict[pd.Timestamp, set[str]],
) -> dict[str, Any]:
    start_date, end_date = active_window(config)
    merged = pd.concat([factor_series.rename("score"), label_series.rename("label")], axis=1)
    window_mask = (merged.index.get_level_values("datetime") >= start_date) & (
        merged.index.get_level_values("datetime") <= end_date
    )
    merged = merged.loc[window_mask]
    if merged.empty:
        return {
            "factor_name": factor_name,
            "mean_ic": 0.0,
            "mean_rank_ic": 0.0,
            "ic_ir": 0.0,
            "rank_ic_ir": 0.0,
            "positive_ic_ratio": 0.0,
            "coverage": 0.0,
            "observation_count": 0,
        }
    observation_dates = analysis_observation_dates(merged.index.get_level_values("datetime"), config)
    mask = merged.index.get_level_values("datetime").isin(observation_dates)
    observation_frame = merged.loc[mask]
    observation_frame = observation_frame.loc[_build_membership_mask(observation_frame.index, component_map)]
    if observation_frame.empty:
        return {
            "factor_name": factor_name,
            "mean_ic": 0.0,
            "mean_rank_ic": 0.0,
            "ic_ir": 0.0,
            "rank_ic_ir": 0.0,
            "positive_ic_ratio": 0.0,
            "coverage": 0.0,
            "observation_count": 0,
        }

    label_available_count = int(observation_frame["label"].notna().sum())
    valid_pair_count = int(observation_frame[["score", "label"]].notna().all(axis=1).sum())
    ic_series = observation_frame.groupby(level="datetime").apply(lambda x: _cross_section_corr(x["score"], x["label"], "pearson")).dropna()
    rank_ic_series = observation_frame.groupby(level="datetime").apply(lambda x: _cross_section_corr(x["score"], x["label"], "spearman")).dropna()
    mean_ic = float(ic_series.mean() or 0.0) if not ic_series.empty else 0.0
    mean_rank_ic = float(rank_ic_series.mean() or 0.0) if not rank_ic_series.empty else 0.0
    ic_std = float(ic_series.std(ddof=0) or 0.0) if not ic_series.empty else 0.0
    rank_ic_std = float(rank_ic_series.std(ddof=0) or 0.0) if not rank_ic_series.empty else 0.0
    return {
        "factor_name": factor_name,
        "mean_ic": mean_ic,
        "mean_rank_ic": mean_rank_ic,
        "ic_ir": float(mean_ic / ic_std) if ic_std else 0.0,
        "rank_ic_ir": float(mean_rank_ic / rank_ic_std) if rank_ic_std else 0.0,
        "positive_ic_ratio": float((rank_ic_series > 0).mean()) if not rank_ic_series.empty else 0.0,
        "coverage": float(valid_pair_count / label_available_count) if label_available_count else 0.0,
        "observation_count": int(rank_ic_series.count()),
    }


def evaluate_formula(formula: str, data_frame: pd.DataFrame) -> pd.Series:
    normalized = _normalize_pct_change_expr(formula)
    env = {column: data_frame[column] for column in data_frame.columns}
    env.update(OPERATOR_ENV)
    result = eval(normalized, {"__builtins__": {}}, env)
    if not isinstance(result, pd.Series):
        raise TypeError("formula result is not Series")
    return result


def _normalize_pct_change_expr(expr: str) -> str:
    # Convert pandas method-chain calls into operator-form calls so all
    # time-series operations use instrument-wise grouping in OPERATOR_ENV.
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return expr

    class _PctChangeRewriter(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.AST:
            node = self.generic_visit(node)
            if not isinstance(node.func, ast.Attribute):
                return node
            attr = node.func.attr

            if attr == "pct_change":
                period_arg: ast.AST = ast.Constant(value=1)
                if node.args:
                    period_arg = node.args[0]
                else:
                    for kw in node.keywords:
                        if kw.arg == "periods":
                            period_arg = kw.value
                            break
                return ast.copy_location(
                    ast.Call(
                        func=ast.Name(id="pct_change", ctx=ast.Load()),
                        args=[node.func.value, period_arg],
                        keywords=[],
                    ),
                    node,
                )

            if attr == "shift":
                period_arg: ast.AST = ast.Constant(value=1)
                if node.args:
                    period_arg = node.args[0]
                else:
                    for kw in node.keywords:
                        if kw.arg == "periods":
                            period_arg = kw.value
                            break
                return ast.copy_location(
                    ast.Call(
                        func=ast.Name(id="delay", ctx=ast.Load()),
                        args=[node.func.value, period_arg],
                        keywords=[],
                    ),
                    node,
                )

            agg_to_op = {
                "mean": "rolling_mean",
                "std": "rolling_std",
                "sum": "rolling_sum",
                "max": "rolling_max",
                "min": "rolling_min",
            }
            op_name = agg_to_op.get(attr)
            if op_name and isinstance(node.func.value, ast.Call) and isinstance(node.func.value.func, ast.Attribute):
                rolling_call = node.func.value
                if rolling_call.func.attr == "rolling":
                    series_expr = rolling_call.func.value
                    window_arg: ast.AST | None = None
                    if rolling_call.args:
                        window_arg = rolling_call.args[0]
                    else:
                        for kw in rolling_call.keywords:
                            if kw.arg == "window":
                                window_arg = kw.value
                                break
                    if window_arg is not None:
                        return ast.copy_location(
                            ast.Call(
                                func=ast.Name(id=op_name, ctx=ast.Load()),
                                args=[series_expr, window_arg],
                                keywords=[],
                            ),
                            node,
                        )
            return node

    rewritten = _PctChangeRewriter().visit(tree)
    ast.fix_missing_locations(rewritten)
    try:
        return ast.unparse(rewritten)
    except Exception:
        return expr


def _load_feature_formula_map() -> dict[str, str]:
    feature_map = dict(INTERNAL_FEATURE_FORMULAS)
    candidate_paths = [
        Path("runtime/config/feature_pool.yaml"),
        Path("runtime/config/feature_pool_extended.yaml"),
        Path("feature_pool.yaml"),
        Path("feature_pool_extended.yaml"),
    ]
    for path in candidate_paths:
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            raw = lines[i].strip()
            if not raw.startswith("- name:"):
                i += 1
                continue
            name = raw.split(":", 1)[1].split("#", 1)[0].strip()
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("expr:"):
                j += 1
            if j < len(lines):
                expr = lines[j].split(":", 1)[1].split("#", 1)[0].strip()
                if name and expr:
                    feature_map[name] = expr
            i = j + 1
    return feature_map


def _expand_formula_dependencies(
    formula: str,
    feature_map: dict[str, str],
) -> str:
    if not feature_map:
        return formula

    cache: dict[str, str] = {}
    expr_map = dict(feature_map)

    def _resolve_factor(name: str, stack: set[str]) -> str:
        if name in cache:
            return cache[name]
        if name in stack:
            chain = " -> ".join(list(stack) + [name])
            raise ValueError(f"Cyclic factor dependency detected: {chain}")
        expr = expr_map.get(name)
        if not expr:
            raise KeyError(f"Missing formula for dependency factor: {name}")
        stack.add(name)
        expanded = _expand_expr(expr, stack)
        stack.remove(name)
        cache[name] = expanded
        return expanded

    def _expand_expr(expr: str, stack: set[str]) -> str:
        tokens = _extract_formula_tokens(expr)
        deps = sorted([t for t in tokens if t in expr_map], key=len, reverse=True)
        out = expr
        for dep in deps:
            dep_expr = _resolve_factor(dep, stack)
            out = re.sub(rf"\b{re.escape(dep)}\b", f"({dep_expr})", out)
        return out

    return _expand_expr(formula, set())


def _build_raw_frame(
    config: dict[str, Any],
    factor_formula: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    instruments: list[str],
) -> pd.DataFrame:
    fields = _extract_formula_tokens(factor_formula)
    needs_vwap = "vwap" in fields
    if needs_vwap:
        # Never request native `vwap` from rq.get_price because some accounts do not support it.
        # Pull amount/volume and derive vwap locally.
        fields.discard("vwap")
        fields.add("amount")
        fields.add("volume")
    unresolved = sorted([name for name in fields if name not in BASE_DATA_FIELDS])
    if unresolved:
        raise ValueError(
            "Formula contains unresolved dependencies (not raw data fields): "
            f"{', '.join(unresolved)}. "
            "Please ensure these names are defined in feature_pool.yaml/feature_pool_extended.yaml, "
            "or pass a fully expanded formula."
        )
    fields.add(str(config.get("label", {}).get("price_field", "close")))
    preprocess_cfg = dict(config.get("preprocess", {}))
    neutralization = str(preprocess_cfg.get("neutralization", "none")).strip().lower()
    if neutralization in {"market_cap", "industry_market_cap"}:
        fields.add(str(preprocess_cfg.get("neutralization_options", {}).get("market_cap_field", "market_cap")))
    if neutralization in {"industry", "industry_market_cap"}:
        fields.add(str(preprocess_cfg.get("neutralization_options", {}).get("industry_field", "industry")))

    field_alias = dict(INTERNAL_FIELD_ALIAS)
    adjust_type = str(config.get("price_adjust", "none")).strip().lower()
    non_adjust_fields = {"amount", "volume", "turnover"}
    lookback = int(INTERNAL_LOOKBACK_DAYS)
    rebalance = str(config.get("rebalance", "weekly")).strip().lower()
    interval = max(int(config.get("rebalance_interval", 1) or 1), 1)
    if rebalance == "daily":
        forward_days = interval
    elif rebalance == "monthly":
        forward_days = 22 * interval
    else:
        forward_days = 5 * interval
    fetch_start = (start_date - pd.Timedelta(days=lookback)).strftime("%Y-%m-%d")
    fetch_end = (end_date + pd.offsets.BDay(int(forward_days) + 5)).strftime("%Y-%m-%d")

    raw_cols: dict[str, pd.Series] = {}
    for local_name in sorted(fields):
        if local_name == "market_cap":
            cap = _get_market_cap(instruments, fetch_start, fetch_end)
            raw_cols[local_name] = cap.stack(dropna=False).rename(local_name)
            continue
        if local_name == "turnover":
            turnover = _get_turnover_field(instruments, fetch_start, fetch_end)
            raw_cols[local_name] = turnover.stack(dropna=False).rename(local_name)
            continue
        if local_name == "vwap":
            vwap = _get_vwap_field(instruments, fetch_start, fetch_end, adjust_type)
            raw_cols[local_name] = vwap.stack(dropna=False).rename(local_name)
            continue
        if local_name == "industry":
            industry_map = _get_industry_map(instruments, end_date)
            frame = pd.DataFrame(index=pd.date_range(fetch_start, fetch_end, freq="D"), columns=instruments, dtype=object)
            frame[:] = np.nan
            for code, value in industry_map.items():
                if code in frame.columns:
                    frame[code] = value
            frame = frame.loc[rq.get_trading_dates(fetch_start, fetch_end)]
            raw_cols[local_name] = frame.stack(dropna=False).rename(local_name)
            continue

        rq_field = field_alias.get(local_name, local_name)
        if str(rq_field).strip().lower() == "vwap":
            vwap = _get_vwap_field(instruments, fetch_start, fetch_end, adjust_type)
            raw_cols[local_name] = vwap.stack(dropna=False).rename(local_name)
            continue
        field_adjust_type = "none" if local_name in non_adjust_fields else adjust_type
        price_frame = _get_price_field(instruments, fetch_start, fetch_end, rq_field, field_adjust_type)
        raw_cols[local_name] = price_frame.stack(dropna=False).rename(local_name)

    raw_frame = pd.concat(raw_cols.values(), axis=1)
    raw_frame.index = raw_frame.index.set_names(["datetime", "instrument"])
    raw_frame = raw_frame.sort_index()
    if needs_vwap:
        vwap = _get_vwap_field(instruments, fetch_start, fetch_end, adjust_type)
        raw_frame["vwap"] = vwap.stack(dropna=False).reindex(raw_frame.index)
    return raw_frame


def run_validation(
    factor_name: str,
    formula: str | None = None,
    config_override: dict[str, Any] | None = None,
) -> ValidationResult:
    if not config_override:
        raise ValueError("config_override is required and cannot be empty")
    config = json.loads(json.dumps(config_override))
    _require_config(config)
    start_date, end_date = active_window(config)
    dates = rq.get_trading_dates(start_date, end_date)
    observation_dates = analysis_observation_dates(pd.Index(pd.to_datetime(dates)), config)
    if not observation_dates:
        raise ValueError("No observation dates found in selected window")

    component_map = _get_dynamic_stock_pool(config, observation_dates)
    component_map = _apply_pool_filters(component_map, config, start_date, end_date)
    instruments = sorted({code for v in component_map.values() for code in v})
    if not instruments:
        raise ValueError("No instruments in stock pool after filters")

    feature_map = _load_feature_formula_map()
    if not formula:
        raise ValueError("formula is required. Please pass the factor formula explicitly.")
    use_formula = _expand_formula_dependencies(str(formula), feature_map)
    raw_frame = _build_raw_frame(config, use_formula, start_date, end_date, instruments)
    raw_frame = raw_frame.sort_index()

    factor_series = evaluate_formula(use_formula, raw_frame).rename(factor_name)
    factor_series = apply_factor_preprocess(factor_series, raw_frame, config)
    label_series = build_label_series(raw_frame, config, component_map)
    metrics = factor_metrics_from_series(factor_name, factor_series, label_series, config, component_map)
    return ValidationResult(metrics=metrics, factor_series=factor_series, label_series=label_series)


if __name__ == "__main__":
    # Example:
    # result = run_validation("breakout_drawdown_ratio_v1")
    # print(pd.Series(result.metrics))
    pass
