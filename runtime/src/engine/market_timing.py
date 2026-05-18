from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from . import MarketData, Position, TradePlan

_mt_logger = logging.getLogger("backtest_engine")


def _compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _compute_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(period, min_periods=period).mean()
    avg_loss = loss.rolling(period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi


class MarketTimingFilter:
    def __init__(
        self,
        market_indicator: str = "EMA_60",
        reduce_to: float = 0.5,
        stock_open_filter: str = "none",
        stock_ema_period: int = 60,
        rsi_period: int = 14,
        rsi_buy_max: float = 70.0,
        ohlcv_map: dict[str, pd.DataFrame] | None = None,
    ) -> None:
        self.market_indicator = market_indicator
        self.reduce_to = reduce_to
        self.stock_open_filter = stock_open_filter
        self.stock_ema_period = stock_ema_period
        self.rsi_period = rsi_period
        self.rsi_buy_max = rsi_buy_max

        self._benchmark_close: pd.Series = pd.Series(dtype=float)
        self._benchmark_ema: pd.Series = pd.Series(dtype=float)
        self._stock_ema: dict[str, pd.Series] = {}
        self._stock_rsi: dict[str, pd.Series] = {}
        self._ohlcv_map: dict[str, pd.DataFrame] = {}

        if ohlcv_map is not None:
            self._ohlcv_map = ohlcv_map
            self._precompute(ohlcv_map)

    def _precompute(self, ohlcv_map: dict[str, pd.DataFrame]) -> None:
        self._precompute_benchmark(ohlcv_map)
        if self.stock_open_filter == "ema":
            self._precompute_stock_ema(ohlcv_map)
        elif self.stock_open_filter == "rsi":
            self._precompute_stock_rsi(ohlcv_map)

    def _precompute_benchmark(self, ohlcv_map: dict[str, pd.DataFrame]) -> None:
        if not ohlcv_map:
            return
        all_closes: dict[pd.Timestamp, list[float]] = {}
        for frame in ohlcv_map.values():
            for dt in frame.index:
                val = frame.loc[dt, "close"]
                if not np.isnan(val):
                    all_closes.setdefault(pd.Timestamp(dt), []).append(float(val))
        if not all_closes:
            return
        records = [(dt, sum(vals) / len(vals)) for dt, vals in all_closes.items()]
        records.sort(key=lambda x: x[0])
        self._benchmark_close = pd.Series(
            [v for _, v in records],
            index=pd.DatetimeIndex([dt for dt, _ in records]),
        )
        ema_period = self._ema_period_from_indicator()
        if ema_period is not None and len(self._benchmark_close) >= ema_period:
            self._benchmark_ema = _compute_ema(self._benchmark_close, ema_period)

    def _ema_period_from_indicator(self) -> int | None:
        if self.market_indicator.startswith("EMA_"):
            try:
                return int(self.market_indicator.split("_")[1])
            except (ValueError, IndexError):
                return None
        return None

    def _precompute_stock_ema(self, ohlcv_map: dict[str, pd.DataFrame]) -> None:
        for code, frame in ohlcv_map.items():
            if "close" in frame.columns and len(frame) >= self.stock_ema_period:
                self._stock_ema[code] = _compute_ema(frame["close"], self.stock_ema_period)

    def _precompute_stock_rsi(self, ohlcv_map: dict[str, pd.DataFrame]) -> None:
        for code, frame in ohlcv_map.items():
            if "close" in frame.columns and len(frame) >= self.rsi_period + 1:
                self._stock_rsi[code] = _compute_rsi(frame["close"], self.rsi_period)

    def apply(
        self,
        plan: TradePlan,
        market: MarketData,
        positions: dict[str, Position],
        signal_date: pd.Timestamp,
    ) -> TradePlan:
        plan = self._apply_market_level(plan, market, signal_date)
        plan = self._apply_stock_level(plan, market, positions, signal_date)
        return plan

    def _is_risk_state(self, signal_date: pd.Timestamp) -> bool:
        if self._benchmark_close.empty or self._benchmark_ema.empty:
            return False
        dt = pd.Timestamp(signal_date)
        if dt not in self._benchmark_close.index:
            return False
        close_val = self._benchmark_close.loc[dt]
        ema_val = self._benchmark_ema.loc[dt]
        if np.isnan(close_val) or np.isnan(ema_val):
            return False
        return float(close_val) < float(ema_val)

    def _apply_market_level(
        self,
        plan: TradePlan,
        market: MarketData,
        signal_date: pd.Timestamp,
    ) -> TradePlan:
        is_risk = self._is_risk_state(signal_date)
        bench_close = self._get_benchmark_close(signal_date)
        bench_ema = self._get_benchmark_ema(signal_date)

        if not is_risk:
            _mt_logger.info(
                "[MarketTiming] benchmark_close=%.2f, EMA=%.2f → NORMAL STATE",
                bench_close, bench_ema,
            )
            return plan

        _mt_logger.info(
            "[MarketTiming] benchmark_close=%.2f, EMA=%.2f → RISK STATE, reduce_to=%.2f",
            bench_close, bench_ema, self.reduce_to,
        )

        new_target_weights = {}
        for code, weight in plan.target_weights.items():
            new_target_weights[code] = weight * self.reduce_to

        new_to_sell = {}
        for code, weight in plan.to_sell.items():
            new_to_sell[code] = weight * self.reduce_to

        new_to_buy = {}
        for code, weight in plan.to_buy.items():
            new_to_buy[code] = weight * self.reduce_to

        return TradePlan(
            to_sell=new_to_sell,
            to_buy=new_to_buy,
            target_weights=new_target_weights,
            timing_risk_state=True,
            timing_reduce_to=self.reduce_to,
        )

    def _apply_stock_level(
        self,
        plan: TradePlan,
        market: MarketData,
        positions: dict[str, Position],
        signal_date: pd.Timestamp,
    ) -> TradePlan:
        if self.stock_open_filter == "none":
            return plan

        blocked: list[str] = []
        allowed: list[str] = []
        filter_details: list[str] = []

        new_to_buy = {}
        for code, weight in plan.to_buy.items():
            pos = positions.get(code)
            if pos is not None and pos.shares > 0:
                new_to_buy[code] = weight
                allowed.append(code)
                continue

            is_allowed, detail = self._check_stock_open_allowed(code, signal_date)
            if is_allowed:
                new_to_buy[code] = weight
                allowed.append(code)
                filter_details.append(f"{code} {detail} → ALLOW")
            else:
                blocked.append(code)
                filter_details.append(f"{code} {detail} → BLOCK")

        if not blocked:
            return plan

        for detail in filter_details:
            _mt_logger.info("[MarketTiming] stock filter (%s): %s", self.stock_open_filter, detail)
        _mt_logger.info(
            "[MarketTiming] blocked new buys: %s, allowed: %s",
            sorted(blocked), sorted(allowed),
        )

        blocked_set = set(blocked)
        new_target_weights = {}
        for code, weight in plan.target_weights.items():
            if code in blocked_set:
                new_target_weights[code] = 0.0
            else:
                new_target_weights[code] = weight

        if plan.timing_risk_state:
            _mt_logger.info(
                "[MarketTiming] RISK STATE: skip renormalize, kept stocks keep reduced weights"
            )
        else:
            remaining_sum = sum(w for w in new_target_weights.values() if w > 0)
            original_sum = sum(plan.target_weights.values())
            if remaining_sum > 0 and original_sum > 0 and not math.isclose(remaining_sum, original_sum):
                factor = original_sum / remaining_sum
                _mt_logger.info("[MarketTiming] weight renormalize factor: %.3f", factor)
                for code in new_target_weights:
                    if new_target_weights[code] > 0:
                        new_target_weights[code] *= factor

        new_to_sell = dict(plan.to_sell)
        for code in blocked_set:
            if code in new_to_sell:
                del new_to_sell[code]

        return TradePlan(
            to_sell=new_to_sell,
            to_buy=new_to_buy,
            target_weights=new_target_weights,
            timing_risk_state=plan.timing_risk_state,
            timing_reduce_to=plan.timing_reduce_to,
        )

    def _check_stock_open_allowed(self, code: str, signal_date: pd.Timestamp) -> tuple[bool, str]:
        dt = pd.Timestamp(signal_date)
        if self.stock_open_filter == "ema":
            return self._check_ema_filter(code, dt)
        elif self.stock_open_filter == "rsi":
            return self._check_rsi_filter(code, dt)
        return True, ""

    def _check_ema_filter(self, code: str, dt: pd.Timestamp) -> tuple[bool, str]:
        ema_series = self._stock_ema.get(code)
        if ema_series is None or ema_series.empty:
            return True, "EMA=N/A"
        if dt not in ema_series.index:
            return True, "EMA=N/A"
        ema_val = float(ema_series.loc[dt])
        if np.isnan(ema_val):
            return True, "EMA=NaN"

        frame = self._ohlcv_map.get(code)
        if frame is None or dt not in frame.index:
            return True, f"close=N/A, EMA={ema_val:.2f}"
        close_val = float(frame.loc[dt, "close"])
        if np.isnan(close_val):
            return True, f"close=NaN, EMA={ema_val:.2f}"

        allowed = close_val >= ema_val
        detail = f"close={close_val:.2f}, EMA={ema_val:.2f}"
        return allowed, detail

    def _check_rsi_filter(self, code: str, dt: pd.Timestamp) -> tuple[bool, str]:
        rsi_series = self._stock_rsi.get(code)
        if rsi_series is None or rsi_series.empty:
            return True, "RSI=N/A"
        if dt not in rsi_series.index:
            return True, "RSI=N/A"
        rsi_val = float(rsi_series.loc[dt])
        if np.isnan(rsi_val):
            return True, "RSI=NaN"

        allowed = rsi_val <= self.rsi_buy_max
        detail = f"RSI={rsi_val:.1f}"
        return allowed, detail

    def _get_benchmark_close(self, signal_date: pd.Timestamp) -> float:
        dt = pd.Timestamp(signal_date)
        if dt in self._benchmark_close.index:
            return float(self._benchmark_close.loc[dt])
        return np.nan

    def _get_benchmark_ema(self, signal_date: pd.Timestamp) -> float:
        dt = pd.Timestamp(signal_date)
        if dt in self._benchmark_ema.index:
            return float(self._benchmark_ema.loc[dt])
        return np.nan
