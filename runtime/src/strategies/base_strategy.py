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
    )

    def __init__(self) -> None:
        raw_dates = self.p.rebalance_dates or set()
        self._rebalance_dates = {str(item) for item in raw_dates}
        self._log_count = 0
        self._pending_targets: dict[str, float] = {}
        self._data_by_name = {str(data._name): data for data in self.datas}

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

    def _compute_target_holdings(
        self,
        scores: dict[str, float],
        current_holdings: list[str],
    ) -> list[str]:
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

    def _submit_target_percent(self, data: bt.feeds.DataBase, target: float) -> None:
        code = str(data._name)
        current_weight = self._current_weight(data)
        delta = float(target) - float(current_weight)
        if abs(delta) < 1e-8:
            self._pending_targets.pop(code, None)
            return

        if self._is_suspended(data):
            action = str(self.p.suspend_action or "skip").strip().lower()
            if action == "queue":
                self._pending_targets[code] = float(target)
            return

        is_buy = delta > 0
        if is_buy and self._is_limit_up(data):
            action = str(self.p.limit_up_action or "skip_buy").strip().lower()
            if action == "queue_buy":
                self._pending_targets[code] = float(target)
                return
            if action == "allow_buy":
                pass
            else:
                return
        if (not is_buy) and self._is_limit_down(data):
            action = str(self.p.limit_down_action or "delay_sell").strip().lower()
            if action == "delay_sell":
                self._pending_targets[code] = float(target)
                return
            if action == "force_sell":
                pass
            else:
                return

        mode = str(self.p.trade_price_mode or "next_open").strip().lower()
        kwargs: dict[str, Any] = {}
        if mode == "next_close":
            kwargs["exectype"] = bt.Order.Close

        price = self.datas[0].close[0] if code == self.p.benchmark_name else data.close[0]
        portfolio_value = self.broker.getvalue()
        target_value = abs(float(target)) * portfolio_value
        if target_value <= 0:
            return
        shares_needed = target_value / price
        if is_buy and shares_needed < 100:
            return
        if not is_buy and target > 0 and shares_needed < 100:
            return

        # 科创板保护限价：科创板股票(688开头)使用限价单
        # 买入保护限价 = 当前价格 * 1.02 (允许2%的上涨空间)
        # 卖出保护限价 = 当前价格 * 0.98 (允许2%的下跌空间)
        if code.startswith("SH688") and code != self.p.benchmark_name:
            kwargs["exectype"] = bt.Order.Limit
            if is_buy:
                kwargs["price"] = price * 1.02
            else:
                kwargs["price"] = price * 0.98

        self.order_target_percent(data=data, target=float(target), **kwargs)
        self._pending_targets.pop(code, None)

    def _flush_pending_targets(self) -> None:
        if not self._pending_targets:
            return
        for code, target in list(self._pending_targets.items()):
            data = self._data_by_name.get(str(code))
            if data is None:
                self._pending_targets.pop(code, None)
                continue
            self._submit_target_percent(data=data, target=float(target))

    def _apply_cash_buffer(
        self,
        weights: dict[str, float],
        target_codes: list[str],
    ) -> dict[str, float]:
        """应用资金缓冲：预留部分资金防止因手续费导致订单失败"""
        cash = self.broker.getcash()
        portfolio_value = self.broker.getvalue()

        # 从配置读取资金缓冲比例，默认2%
        cash_buffer_ratio = float(getattr(self.p, 'cash_buffer_ratio', 0.02))
        reserved_cash = portfolio_value * cash_buffer_ratio
        available_cash = max(0, cash - reserved_cash)

        # 计算目标持仓需要的总资金
        total_target_value = sum(
            weights.get(code, 0.0) * portfolio_value
            for code in target_codes
        )

        # 如果目标资金超过可用资金，等比例缩放权重
        if total_target_value > available_cash and total_target_value > 0:
            scale = available_cash / total_target_value
            weights = {code: w * scale for code, w in weights.items()}

        return weights

    def _place_target_orders(
        self,
        target_holdings: list[str],
        scores: dict[str, float] | None = None,
    ) -> None:
        del scores
        target_set = set(target_holdings)
        exposure_scale = float(self._target_exposure_scale())
        exposure_scale = max(0.0, min(1.0, exposure_scale))
        if not target_set:
            for data in self.datas:
                if str(data._name) == str(self.p.benchmark_name):
                    continue
                if self.getposition(data).size > 0:
                    self._submit_target_percent(data=data, target=0.0)
            return
        weight = exposure_scale / len(target_set)
        for data in self.datas:
            code = str(data._name)
            if code == str(self.p.benchmark_name):
                continue
            target = weight if code in target_set else 0.0
            self._submit_target_percent(data=data, target=target)

    def _log_rebalance(self, holdings: list[str], sell_count: int, buy_count: int) -> None:
        if self._log_count >= int(self.p.max_rebalance_logs):
            return
        print(
            f"[TopkDropout] date={self._today()}, holdings={len(holdings)}, "
            f"sell_count={sell_count}, buy_count={buy_count}, sample={holdings[:10]}"
        )
        self._log_count += 1

    def prenext(self) -> None:
        self.next()

    def next(self) -> None:
        self._flush_pending_targets()
        if not self._is_rebalance_day():
            return
        scores = self._cross_section_scores()
        if not scores:
            return
        current = self._current_holdings()
        target = self._compute_target_holdings(scores, current)
        self._place_target_orders(target, scores=scores)
