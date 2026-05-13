from __future__ import annotations

from typing import Any

import numpy as np

try:
    import backtrader as bt
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError(
        "缺少依赖 backtrader，请先执行: python -m pip install backtrader"
    ) from exc


class BaseFactorStrategy(bt.Strategy):
    params = (
        ("rebalance_dates", None),
        ("strict_assertions", True),
        ("max_rebalance_logs", 20),
        ("benchmark_name", "__benchmark__"),
        ("trade_price_mode", "next_open"),
        ("suspend_action", "skip"),
        ("limit_up_action", "skip_buy"),
        ("limit_down_action", "delay_sell"),
        ("cash_buffer_ratio", 0.02),
        ("min_score_coverage", 0.0),
    )

    def __init__(self) -> None:
        raw_dates = self.p.rebalance_dates or set()
        self._rebalance_dates = {str(item) for item in raw_dates}
        self._log_count = 0
        self._data_by_name = {str(data._name): data for data in self.datas}
        self._last_target_weights: dict[str, float] = {}
        self._open_orders: dict[str, list[bt.Order]] = {}

    def _today(self) -> str:
        return bt.num2date(self.datas[0].datetime[0]).strftime("%Y-%m-%d")

    def _is_rebalance_day(self) -> bool:
        if not self._rebalance_dates:
            return True
        return self._today() in self._rebalance_dates

    def _cross_section_scores(self) -> dict[str, float]:
        scores: dict[str, float] = {}
        for data in self.datas:
            if str(data._name) == str(self.p.benchmark_name):
                continue
            score = float(data.factor_score[0])
            close = float(data.close[0])
            if np.isnan(score) or np.isnan(close):
                continue
            scores[str(data._name)] = score
        return scores

    def _current_holdings(self) -> list[str]:
        holdings: list[str] = []
        for data in self.datas:
            if str(data._name) == str(self.p.benchmark_name):
                continue
            if self.getposition(data).size > 0:
                holdings.append(str(data._name))
        return holdings

    def _compute_target_weights(self, scores: dict[str, float]) -> dict[str, float]:
        raise NotImplementedError

    def _target_exposure_scale(self) -> float:
        return 1.0

    def _is_suspended(self, data: bt.feeds.DataBase) -> bool:
        close = float(data.close[0])
        volume = float(data.volume[0])
        if np.isnan(close) or np.isnan(volume):
            return True
        return volume <= 0

    def _is_limit_up(self, data: bt.feeds.DataBase) -> bool:
        if len(data) < 2:
            return False
        prev_close = float(data.close[-1])
        close = float(data.close[0])
        high = float(data.high[0])
        if any(np.isnan(item) for item in [prev_close, close, high]) or prev_close <= 0:
            return False
        pct_change = (close - prev_close) / prev_close
        return pct_change >= 0.098 and abs(close - high) <= max(0.01, abs(close) * 1e-4)

    def _is_limit_down(self, data: bt.feeds.DataBase) -> bool:
        if len(data) < 2:
            return False
        prev_close = float(data.close[-1])
        close = float(data.close[0])
        low = float(data.low[0])
        if any(np.isnan(item) for item in [prev_close, close, low]) or prev_close <= 0:
            return False
        pct_change = (close - prev_close) / prev_close
        return pct_change <= -0.098 and abs(close - low) <= max(0.01, abs(close) * 1e-4)

    def _current_weight(self, data: bt.feeds.DataBase) -> float:
        portfolio_value = float(self.broker.getvalue() or 0.0)
        if portfolio_value <= 0:
            return 0.0
        position = self.getposition(data)
        close = float(data.close[0])
        if np.isnan(close):
            return 0.0
        return float(position.size * close / portfolio_value)

    def _apply_cash_buffer(
        self,
        weights: dict[str, float],
        target_codes: list[str],
    ) -> dict[str, float]:
        cash = self.broker.getcash()
        portfolio_value = self.broker.getvalue()
        cash_buffer_ratio = float(getattr(self.p, 'cash_buffer_ratio', 0.02))
        reserved_cash = portfolio_value * cash_buffer_ratio
        available_cash = max(0, cash - reserved_cash)
        additional_cash_needed = 0.0
        for code in target_codes:
            target_value = weights.get(code, 0.0) * portfolio_value
            current_value = 0.0
            data = self._data_by_name.get(code)
            if data is not None:
                pos = self.getposition(data)
                close = float(data.close[0])
                if not np.isnan(close):
                    current_value = float(pos.size * close)
            delta = target_value - current_value
            if delta > 0:
                additional_cash_needed += delta
        if additional_cash_needed > available_cash and additional_cash_needed > 0:
            scale = available_cash / additional_cash_needed
            new_weights: dict[str, float] = {}
            for code, w in weights.items():
                if w <= 0:
                    new_weights[code] = w
                    continue
                target_value = w * portfolio_value
                data = self._data_by_name.get(code)
                current_value = 0.0
                if data is not None:
                    pos = self.getposition(data)
                    close = float(data.close[0])
                    if not np.isnan(close):
                        current_value = float(pos.size * close)
                delta = target_value - current_value
                if delta > 0:
                    reduced_target_value = current_value + delta * scale
                    new_weights[code] = reduced_target_value / portfolio_value
                else:
                    new_weights[code] = w
            return new_weights
        return weights

    def _check_score_coverage(self, scores: dict[str, float]) -> bool:
        min_coverage = float(self.p.min_score_coverage or 0.0)
        if min_coverage <= 0:
            return True
        total_count = 0
        for data in self.datas:
            if str(data._name) == str(self.p.benchmark_name):
                continue
            total_count += 1
        if total_count == 0:
            return True
        valid_count = len(scores)
        coverage = valid_count / total_count
        return coverage >= min_coverage

    def notify_order(self, order: bt.Order) -> None:
        if order is None:
            return
        code = str(order.data._name)
        if order.status in (order.Completed, order.Canceled, order.Margin, order.Rejected):
            orders = self._open_orders.get(code)
            if orders is not None:
                self._open_orders[code] = [o for o in orders if o is not None and o.ref != order.ref]
                if not self._open_orders[code]:
                    del self._open_orders[code]

    def _has_open_order(self, code: str) -> bool:
        orders = self._open_orders.get(code)
        return bool(orders)

    def _cancel_open_orders(self, code: str) -> None:
        orders = self._open_orders.pop(code, None)
        if orders:
            for order in orders:
                if order is not None:
                    self.cancel(order)

    def _set_target_weight(self, data: bt.feeds.DataBase, target: float) -> bool:
        code = str(data._name)

        if self._is_suspended(data):
            action = str(self.p.suspend_action or "skip").strip().lower()
            if action != "force":
                return False

        current_weight = self._current_weight(data)
        delta = float(target) - float(current_weight)
        if abs(delta) < 1e-8:
            return False

        is_buy = delta > 0
        if is_buy and self._is_limit_up(data):
            action = str(self.p.limit_up_action or "skip_buy").strip().lower()
            if action != "allow_buy":
                return False

        if (not is_buy) and self._is_limit_down(data):
            action = str(self.p.limit_down_action or "delay_sell").strip().lower()
            if action != "force_sell":
                return False

        mode = str(self.p.trade_price_mode or "next_open").strip().lower()
        kwargs: dict[str, Any] = {}
        if mode == "next_close":
            kwargs["exectype"] = bt.Order.Close

        pure_code = code[2:] if len(code) > 2 and code[:2] in ("SH", "SZ") else code
        if pure_code.startswith("688"):
            if is_buy:
                kwargs["exectype"] = bt.Order.Limit
                kwargs["price"] = float(data.close[0]) * 1.02
            else:
                kwargs["exectype"] = bt.Order.Limit
                kwargs["price"] = float(data.close[0]) * 0.98

        price = self.datas[0].close[0] if code == self.p.benchmark_name else data.close[0]
        portfolio_value = self.broker.getvalue()
        target_value = abs(float(target)) * portfolio_value
        if is_buy and target_value <= 0:
            return False
        shares_needed = target_value / price
        if is_buy and shares_needed < 100:
            return False
        if not is_buy and target > 0 and shares_needed < 100:
            return False

        self._cancel_open_orders(code)
        order = self.order_target_percent(data=data, target=float(target), **kwargs)
        if order is not None:
            self._open_orders.setdefault(code, []).append(order)
        return True

    def _retry_limit_down_sells(self) -> None:
        if not self._last_target_weights:
            return
        for data in self.datas:
            code = str(data._name)
            if code == str(self.p.benchmark_name):
                continue
            if self.getposition(data).size <= 0:
                continue
            if self._has_open_order(code):
                continue
            if code not in self._last_target_weights:
                continue
            last_target = self._last_target_weights[code]
            if last_target > 0:
                continue
            self._set_target_weight(data, 0.0)

    def _log_rebalance(self, target_weights: dict[str, float]) -> None:
        if self._log_count >= int(self.p.max_rebalance_logs):
            return
        sell_codes = [c for c, w in target_weights.items() if w <= 0 and c in self._current_holdings()]
        buy_codes = [c for c, w in target_weights.items() if w > 0 and c not in self._current_holdings()]
        print(
            f"[{self.__class__.__name__}] date={self._today()}, "
            f"sell_count={len(sell_codes)}, buy_count={len(buy_codes)}, "
            f"sells={sell_codes[:10]}, buys={buy_codes[:10]}"
        )
        self._log_count += 1

    def prenext(self) -> None:
        self.next()

    def next(self) -> None:
        if self._is_rebalance_day():
            scores = self._cross_section_scores()
            if not scores:
                self._retry_limit_down_sells()
                return
            if not self._check_score_coverage(scores):
                self._retry_limit_down_sells()
                return

            target_weights = self._compute_target_weights(scores)
            self._last_target_weights = dict(target_weights)

            exposure_scale = max(0.0, min(1.0, float(self._target_exposure_scale())))
            if exposure_scale < 1.0:
                target_weights = {code: w * exposure_scale for code, w in target_weights.items()}

            target_codes = [c for c, w in target_weights.items() if w > 0]
            target_weights = self._apply_cash_buffer(target_weights, target_codes)

            for code, weight in target_weights.items():
                data = self._data_by_name.get(code)
                if data is None:
                    continue
                self._set_target_weight(data, float(weight))

            if self.p.strict_assertions:
                self._log_rebalance(target_weights)
        else:
            self._retry_limit_down_sells()
