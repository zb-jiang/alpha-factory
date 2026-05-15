from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

_backtest_logger = logging.getLogger("backtest_engine")


def _min_lot(code: str) -> int:
    pure = code[2:] if len(code) > 2 and code[:2] in ("SH", "SZ") else code
    if pure.startswith("688"):
        return 200
    return 100


def _round_to_lot(code: str, shares: int) -> int:
    lot = _min_lot(code)
    if shares <= 0:
        return 0
    lots = shares // lot
    return lots * lot


@dataclass
class Position:
    code: str
    shares: int = 0
    avg_cost: float = 0.0

    @property
    def is_empty(self) -> bool:
        return self.shares <= 0


@dataclass
class TradeRecord:
    trade_date: str
    action: str
    instrument: str
    shares: int
    price: float
    value: float
    commission: float


@dataclass
class TradePlan:
    to_sell: dict[str, float] = field(default_factory=dict)
    to_buy: dict[str, float] = field(default_factory=dict)
    target_weights: dict[str, float] = field(default_factory=dict)


class Portfolio:
    def __init__(self, initial_cash: float, buy_cost: float, sell_cost: float, stamp_duty: float, cash_buffer_ratio: float, slippage: float) -> None:
        self._cash = initial_cash
        self._initial_cash = initial_cash
        self._buy_cost = buy_cost
        self._sell_cost = sell_cost
        self._stamp_duty = stamp_duty
        self._cash_buffer_ratio = cash_buffer_ratio
        self._slippage = slippage
        self._positions: dict[str, Position] = {}

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> dict[str, Position]:
        return self._positions

    def get_position(self, code: str) -> Position:
        return self._positions.get(code, Position(code=code))

    def has_position(self, code: str) -> bool:
        pos = self._positions.get(code)
        return pos is not None and pos.shares > 0

    def position_codes(self) -> list[str]:
        return [code for code, pos in self._positions.items() if pos.shares > 0]

    def position_value(self, code: str, price: float) -> float:
        pos = self._positions.get(code)
        if pos is None or pos.shares <= 0:
            return 0.0
        return pos.shares * price

    def total_value(self, prices: dict[str, float]) -> float:
        value = self._cash
        for code, pos in self._positions.items():
            if pos.shares > 0:
                price = prices.get(code, pos.avg_cost)
                value += pos.shares * price
        return value

    def available_cash_after_buffer(self, total_value: float, pending_sell_value: float = 0.0) -> float:
        reserved = total_value * self._cash_buffer_ratio
        return max(0.0, self._cash + pending_sell_value - reserved)

    def buy(self, code: str, shares: int, price: float) -> tuple[float, float]:
        slippage_price = price * (1 + self._slippage)
        trade_value = shares * slippage_price
        commission = trade_value * self._buy_cost
        total_cost = trade_value + commission
        if total_cost > self._cash:
            max_affordable_shares = int(self._cash / (slippage_price * (1 + self._buy_cost)))
            shares = _round_to_lot(code, max_affordable_shares)
            if shares <= 0:
                return 0.0, 0.0
            trade_value = shares * slippage_price
            commission = trade_value * self._buy_cost
            total_cost = trade_value + commission
        self._cash -= total_cost
        pos = self._positions.get(code)
        if pos is None:
            pos = Position(code=code)
            self._positions[code] = pos
        old_total = pos.avg_cost * pos.shares
        pos.shares += shares
        pos.avg_cost = (old_total + trade_value) / pos.shares if pos.shares > 0 else 0.0
        return float(shares), commission

    def sell(self, code: str, shares: int, price: float) -> tuple[float, float]:
        pos = self._positions.get(code)
        if pos is None or pos.shares <= 0:
            return 0.0, 0.0
        shares = min(shares, pos.shares)
        slippage_price = price * (1 - self._slippage)
        trade_value = shares * slippage_price
        commission = trade_value * self._sell_cost + trade_value * self._stamp_duty
        self._cash += trade_value - commission
        pos.shares -= shares
        if pos.shares <= 0:
            pos.shares = 0
            pos.avg_cost = 0.0
        return float(shares), commission


class MarketData:
    def __init__(self, ohlcv_map: dict[str, pd.DataFrame], factor_scores: dict[str, pd.Series]) -> None:
        self._ohlcv = ohlcv_map
        self._scores = factor_scores
        self._all_dates: list[pd.Timestamp] = []
        if ohlcv_map:
            first_frame = next(iter(ohlcv_map.values()))
            self._all_dates = sorted(first_frame.index.tolist())
        self._date_set = {pd.Timestamp(d): True for d in self._all_dates}

    @property
    def all_dates(self) -> list[pd.Timestamp]:
        return self._all_dates

    @property
    def instruments(self) -> list[str]:
        return list(self._ohlcv.keys())

    def has_date(self, date: pd.Timestamp) -> bool:
        return pd.Timestamp(date) in self._date_set

    def get_price(self, code: str, date: pd.Timestamp, field: str = "close") -> float:
        frame = self._ohlcv.get(code)
        if frame is None:
            return np.nan
        if pd.Timestamp(date) not in frame.index:
            return np.nan
        val = frame.loc[pd.Timestamp(date), field]
        if pd.isna(val):
            return np.nan
        return float(val)

    def get_open(self, code: str, date: pd.Timestamp) -> float:
        return self.get_price(code, date, "open")

    def get_close(self, code: str, date: pd.Timestamp) -> float:
        return self.get_price(code, date, "close")

    def get_prev_close(self, code: str, date: pd.Timestamp) -> float:
        frame = self._ohlcv.get(code)
        if frame is None:
            return np.nan
        loc = frame.index.get_loc(pd.Timestamp(date)) if pd.Timestamp(date) in frame.index else -1
        if loc <= 0:
            return np.nan
        val = frame.iloc[loc - 1]["close"]
        if pd.isna(val):
            return np.nan
        return float(val)

    def get_volume(self, code: str, date: pd.Timestamp) -> float:
        return self.get_price(code, date, "volume")

    def get_score(self, code: str, date: pd.Timestamp) -> float:
        series = self._scores.get(code)
        if series is None:
            return np.nan
        if pd.Timestamp(date) not in series.index:
            return np.nan
        val = series.loc[pd.Timestamp(date)]
        if pd.isna(val):
            return np.nan
        return float(val)

    def is_suspended(self, code: str, date: pd.Timestamp) -> bool:
        frame = self._ohlcv.get(code)
        if frame is None:
            return True
        if pd.Timestamp(date) not in frame.index:
            return True
        volume = frame.loc[pd.Timestamp(date), "volume"]
        if pd.isna(volume) or float(volume) <= 0:
            return True
        return False

    def is_limit_up(self, code: str, date: pd.Timestamp) -> bool:
        open_price = self.get_open(code, date)
        prev_close = self.get_prev_close(code, date)
        if any(np.isnan(v) for v in [open_price, prev_close]) or prev_close <= 0:
            return False
        threshold = 0.098
        pure_code = code[2:] if len(code) > 2 and code[:2] in ("SH", "SZ") else code
        if pure_code.startswith("688") or pure_code.startswith("300"):
            threshold = 0.198
        high_limit = prev_close * (1 + threshold)
        return open_price >= high_limit

    def is_limit_down(self, code: str, date: pd.Timestamp) -> bool:
        open_price = self.get_open(code, date)
        prev_close = self.get_prev_close(code, date)
        if any(np.isnan(v) for v in [open_price, prev_close]) or prev_close <= 0:
            return False
        threshold = -0.098
        pure_code = code[2:] if len(code) > 2 and code[:2] in ("SH", "SZ") else code
        if pure_code.startswith("688") or pure_code.startswith("300"):
            threshold = -0.198
        low_limit = prev_close * (1 + threshold)
        return open_price <= low_limit

    def cross_section_scores(self, date: pd.Timestamp) -> dict[str, float]:
        scores: dict[str, float] = {}
        for code in self.instruments:
            score = self.get_score(code, date)
            close = self.get_close(code, date)
            if np.isnan(score) or np.isnan(close):
                continue
            scores[code] = score
        return scores

    def score_coverage(self, date: pd.Timestamp) -> float:
        total = len(self.instruments)
        if total == 0:
            return 0.0
        valid = 0
        for code in self.instruments:
            score = self.get_score(code, date)
            if not np.isnan(score):
                valid += 1
        return valid / total

    def benchmark_close(self, date: pd.Timestamp) -> float:
        return np.nan

    def benchmark_ema(self, date: pd.Timestamp, period: int) -> float:
        return np.nan


class BacktestEngine:
    def __init__(
        self,
        market_data: MarketData,
        strategy: BaseStrategy,
        initial_cash: float = 1_000_000.0,
        buy_cost: float = 0.0015,
        sell_cost: float = 0.0025,
        stamp_duty: float = 0.001,
        slippage: float = 0.0005,
        cash_buffer_ratio: float = 0.02,
    ) -> None:
        self._market = market_data
        self._strategy = strategy
        self._portfolio = Portfolio(
            initial_cash=initial_cash,
            buy_cost=buy_cost,
            sell_cost=sell_cost,
            stamp_duty=stamp_duty,
            cash_buffer_ratio=cash_buffer_ratio,
            slippage=slippage,
        )
        self._initial_cash = initial_cash
        self._nav_records: list[dict[str, Any]] = []
        self._trade_records: list[TradeRecord] = []
        self._position_records: list[dict[str, Any]] = []
        self._pending_limit_down_sells: list[str] = []
        self._stored_trade_plan: TradePlan | None = None

    def run(self, rebalance_dates: set[str]) -> dict[str, Any]:
        sorted_dates = sorted(self._market.all_dates)
        if not sorted_dates:
            return self._build_results()

        rebalance_ts = {pd.Timestamp(d) for d in rebalance_dates}
        trade_date_set: set[pd.Timestamp] = set()
        for i, date in enumerate(sorted_dates):
            if date in rebalance_ts and i + 1 < len(sorted_dates):
                trade_date_set.add(sorted_dates[i + 1])

        for current_date in sorted_dates:
            is_signal = current_date in rebalance_ts
            is_trade = current_date in trade_date_set

            if is_trade:
                self._on_trade_day(current_date)

            if is_signal:
                self._on_signal_day(current_date)

            if not is_signal and not is_trade:
                self._on_non_rebalance_day(current_date)

            self._record_nav(current_date)

        self._record_final_positions(sorted_dates[-1])
        return self._build_results()

    def _on_signal_day(self, date: pd.Timestamp) -> None:
        _backtest_logger.info("=== compute_and_store_scores called at %s ===", date)
        scores = self._market.cross_section_scores(date)
        if not scores:
            _backtest_logger.info("no scores, return")
            self._stored_trade_plan = None
            return

        coverage = self._market.score_coverage(date)
        _backtest_logger.info("scores count=%d, coverage=%.4f", len(scores), coverage)
        if coverage < self._strategy.min_score_coverage:
            _backtest_logger.info("score coverage %.2f < %.2f, skip rebalance", coverage, self._strategy.min_score_coverage)
            self._stored_trade_plan = None
            return

        current_holdings = self._portfolio.position_codes()
        _backtest_logger.info("current_holdings: %s", current_holdings)

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0][2:] if len(item[0]) > 2 and item[0][:2] in ("SH", "SZ") else item[0]))
        _backtest_logger.info("top 5 scores: %s", ranked[:5])

        plan = self._strategy.compute_trade_plan(
            scores=scores,
            current_holdings=current_holdings,
            portfolio_value=self._current_total_value(date),
            positions=self._portfolio.positions,
            market=self._market,
            signal_date=date,
        )

        to_sell_codes = sorted(plan.to_sell.keys())
        to_buy_codes = sorted(plan.to_buy.keys())
        kept = [c for c in current_holdings if c not in plan.to_sell]
        _backtest_logger.info("to_sell: %s", to_sell_codes)
        _backtest_logger.info("new_buys: %s", to_buy_codes)
        _backtest_logger.info("kept: %d, sell: %d, buy: %d", len(kept), len(to_sell_codes), len(to_buy_codes))

        _backtest_logger.info("scores and trade plan stored")
        self._stored_trade_plan = plan

    def _on_trade_day(self, date: pd.Timestamp) -> None:
        _backtest_logger.info("=== execute_trades called at %s ===", date)
        plan = self._stored_trade_plan
        if plan is None:
            _backtest_logger.info("no stored trade plan, return")
            self._retry_limit_down_sells(date)
            return

        target_weights = plan.target_weights
        if not target_weights:
            _backtest_logger.info("no target weights, return")
            self._stored_trade_plan = None
            self._retry_limit_down_sells(date)
            return

        total_value = self._current_total_value(date)
        prices = self._get_all_prices(date)
        _backtest_logger.info("total_value: %.2f", total_value)

        sell_orders: list[tuple[str, int]] = []
        buy_orders: list[tuple[str, float, float]] = []
        limit_down_full_sells: list[str] = []

        to_sell_codes = set(plan.to_sell.keys())
        to_buy_codes = set(plan.to_buy.keys())

        for code in to_sell_codes:
            pos = self._portfolio.get_position(code)
            current_shares = pos.shares if not pos.is_empty else 0
            if current_shares <= 0:
                continue
            open_price = self._market.get_open(code, date)
            if np.isnan(open_price):
                _backtest_logger.info("sell skip (no open price): %s", code)
                continue
            if self._market.is_suspended(code, date):
                _backtest_logger.info("sell skip (paused): %s", code)
                continue
            if self._market.is_limit_down(code, date):
                _backtest_logger.info("sell skip (limit_down): %s, will retry", code)
                limit_down_full_sells.append(code)
                continue
            sell_orders.append((code, current_shares))

        pending_sell_value = 0.0
        for code, shares in sell_orders:
            open_price = self._market.get_open(code, date)
            if not np.isnan(open_price):
                pending_sell_value += shares * open_price

        available_cash = self._portfolio.available_cash_after_buffer(total_value, pending_sell_value)

        for code in to_buy_codes:
            target_weight = plan.to_buy.get(code, 0.0)
            if target_weight <= 0:
                continue
            open_price = self._market.get_open(code, date)
            if np.isnan(open_price):
                continue
            if self._market.is_suspended(code, date):
                _backtest_logger.info("buy skip (paused): %s", code)
                continue
            if self._market.is_limit_up(code, date):
                _backtest_logger.info("buy skip (limit_up): %s", code)
                continue
            pos = self._portfolio.get_position(code)
            current_value = pos.value if not pos.is_empty else 0.0
            target_value = target_weight * total_value
            delta = target_value - current_value
            if delta > 0:
                buy_orders.append((code, delta, open_price))

        total_buy_value = sum(delta for _, delta, _ in buy_orders)
        scale = 1.0
        if total_buy_value > available_cash and total_buy_value > 0:
            scale = available_cash / total_buy_value
            _backtest_logger.info("cash scale: %.4f (buy_value=%.2f, available=%.2f)", scale, total_buy_value, available_cash)

        sell_count = 0
        buy_count = 0

        for code, shares_to_sell in sell_orders:
            open_price = self._market.get_open(code, date)
            actual_shares, commission = self._portfolio.sell(code, shares_to_sell, open_price)
            if actual_shares > 0:
                self._trade_records.append(TradeRecord(
                    trade_date=date.strftime("%Y-%m-%d"),
                    action="sell",
                    instrument=code,
                    shares=int(actual_shares),
                    price=open_price,
                    value=actual_shares * open_price,
                    commission=commission,
                ))
                sell_count += 1
                _backtest_logger.info("sell order: %s, shares=%d, price=%.4f", code, int(actual_shares), open_price)

        for code, delta, open_price in buy_orders:
            adjusted_value = delta * scale
            price_for_calc = open_price * 1.02 if code[2:].startswith("688") else open_price
            shares_needed = _round_to_lot(code, int(adjusted_value / price_for_calc))
            if shares_needed > 0:
                actual_shares, commission = self._portfolio.buy(code, shares_needed, open_price)
                if actual_shares > 0:
                    self._trade_records.append(TradeRecord(
                        trade_date=date.strftime("%Y-%m-%d"),
                        action="buy",
                        instrument=code,
                        shares=int(actual_shares),
                        price=open_price,
                        value=actual_shares * open_price,
                        commission=commission,
                    ))
                    buy_count += 1
                    _backtest_logger.info("buy order: %s, value=%.2f, shares=%d, price=%.4f", code, actual_shares * open_price, int(actual_shares), open_price)
            else:
                _backtest_logger.info("buy skip (shares < min_lot): %s, target_value=%.2f, price=%.4f", code, adjusted_value, open_price)

        if limit_down_full_sells:
            _backtest_logger.info("pending_limit_down_sells: %s", limit_down_full_sells)
        self._pending_limit_down_sells.extend(limit_down_full_sells)
        self._stored_trade_plan = None
        _backtest_logger.info("rebalance done: sell=%d, buy=%d", sell_count, buy_count)
        self._retry_limit_down_sells(date)

    def _on_non_rebalance_day(self, date: pd.Timestamp) -> None:
        self._retry_limit_down_sells(date)

    def _retry_limit_down_sells(self, date: pd.Timestamp) -> None:
        if not self._pending_limit_down_sells:
            return
        _backtest_logger.info("retry_limit_down_sells at %s, pending=%s", date, self._pending_limit_down_sells)
        remaining: list[str] = []
        for code in self._pending_limit_down_sells:
            if not self._portfolio.has_position(code):
                _backtest_logger.info("retry sell skip (no position): %s", code)
                continue
            if self._market.is_suspended(code, date):
                _backtest_logger.info("retry sell skip (paused): %s", code)
                remaining.append(code)
                continue
            open_price = self._market.get_open(code, date)
            if np.isnan(open_price):
                _backtest_logger.info("retry sell skip (no open price): %s", code)
                remaining.append(code)
                continue
            if self._market.is_limit_down(code, date):
                _backtest_logger.info("retry sell skip (still limit_down): %s", code)
                remaining.append(code)
                continue
            pos = self._portfolio.get_position(code)
            shares_to_sell = pos.shares
            if shares_to_sell > 0:
                actual_shares, commission = self._portfolio.sell(code, shares_to_sell, open_price)
                if actual_shares > 0:
                    self._trade_records.append(TradeRecord(
                        trade_date=date.strftime("%Y-%m-%d"),
                        action="sell",
                        instrument=code,
                        shares=int(actual_shares),
                        price=open_price,
                        value=actual_shares * open_price,
                        commission=commission,
                    ))
                    _backtest_logger.info("retry sell success: %s, shares=%d, price=%.4f", code, int(actual_shares), open_price)
                if actual_shares < shares_to_sell:
                    remaining.append(code)
        self._pending_limit_down_sells = remaining

    def _current_total_value(self, date: pd.Timestamp) -> float:
        prices = self._get_all_prices(date)
        return self._portfolio.total_value(prices)

    def _get_all_prices(self, date: pd.Timestamp) -> dict[str, float]:
        prices: dict[str, float] = {}
        for code in self._market.instruments:
            close = self._market.get_close(code, date)
            if not np.isnan(close):
                prices[code] = close
        return prices

    def _record_nav(self, date: pd.Timestamp) -> None:
        total_value = self._current_total_value(date)
        self._nav_records.append({
            "datetime": date,
            "nav": total_value / self._initial_cash,
        })

    def _record_final_positions(self, date: pd.Timestamp) -> None:
        prices = self._get_all_prices(date)
        total_value = self._portfolio.total_value(prices)
        for code, pos in self._portfolio.positions.items():
            if pos.shares <= 0:
                continue
            price = prices.get(code, pos.avg_cost)
            value = pos.shares * price
            weight = value / total_value if total_value > 0 else 0.0
            self._position_records.append({
                "datetime": date.strftime("%Y-%m-%d"),
                "instrument": code,
                "size": pos.shares,
                "price": price,
                "value": value,
                "weight": weight,
            })

    def _build_results(self) -> dict[str, Any]:
        nav_series = pd.Series(dtype=float)
        if self._nav_records:
            nav_df = pd.DataFrame(self._nav_records)
            nav_series = nav_df.set_index("datetime")["nav"]

        order_rows = [
            {
                "trade_date": t.trade_date,
                "action": t.action,
                "instrument": t.instrument,
                "size": t.shares,
                "price": t.price,
                "value": t.value,
                "commission": t.commission,
            }
            for t in self._trade_records
        ]

        from common import annualized_return, compute_drawdown

        final_nav = float(nav_series.iloc[-1]) if not nav_series.empty else 1.0
        max_dd = compute_drawdown(nav_series) if not nav_series.empty else 0.0
        ann_ret = annualized_return(nav_series) if not nav_series.empty else 0.0

        period_returns = nav_series.pct_change().dropna() if len(nav_series) > 1 else pd.Series(dtype=float)
        std = float(period_returns.std(ddof=0) or 0.0)
        sharpe = float(period_returns.mean() / std * np.sqrt(52)) if std > 0 else 0.0

        trade_value = sum(t.value for t in self._trade_records)
        avg_portfolio = float(nav_series.mean() * self._initial_cash) if not nav_series.empty else self._initial_cash
        turnover = trade_value / avg_portfolio if avg_portfolio > 0 else 0.0

        return {
            "metrics": {
                "annualized_return": ann_ret,
                "max_drawdown": max_dd,
                "sharpe": sharpe,
                "sharpe_ratio": sharpe,
                "turnover": turnover,
                "final_nav": final_nav,
            },
            "nav_curve": pd.DataFrame(self._nav_records) if self._nav_records else pd.DataFrame(),
            "orders": pd.DataFrame(order_rows) if order_rows else pd.DataFrame(),
            "positions": pd.DataFrame(self._position_records) if self._position_records else pd.DataFrame(),
        }


class BaseStrategy:
    def __init__(
        self,
        min_score_coverage: float = 0.90,
        cash_buffer_ratio: float = 0.02,
        suspend_action: str = "skip",
        limit_up_action: str = "skip_buy",
        limit_down_action: str = "delay_sell",
    ) -> None:
        self.min_score_coverage = min_score_coverage
        self.cash_buffer_ratio = cash_buffer_ratio
        self.suspend_action = suspend_action
        self.limit_up_action = limit_up_action
        self.limit_down_action = limit_down_action

    def compute_trade_plan(
        self,
        scores: dict[str, float],
        current_holdings: list[str],
        portfolio_value: float,
        positions: dict[str, Position],
        market: MarketData,
        signal_date: pd.Timestamp,
    ) -> TradePlan:
        raise NotImplementedError
