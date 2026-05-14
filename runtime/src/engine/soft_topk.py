from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import BaseStrategy, MarketData, Position, TradePlan


class SoftTopKStrategy(BaseStrategy):
    def __init__(
        self,
        holding_count: int = 30,
        weight_func: str = "softmax",
        softmax_temperature: float = 1.0,
        rank_power_alpha: float = 1.0,
        min_score_coverage: float = 0.90,
        cash_buffer_ratio: float = 0.02,
        suspend_action: str = "skip",
        limit_up_action: str = "skip_buy",
        limit_down_action: str = "delay_sell",
    ) -> None:
        super().__init__(
            min_score_coverage=min_score_coverage,
            cash_buffer_ratio=cash_buffer_ratio,
            suspend_action=suspend_action,
            limit_up_action=limit_up_action,
            limit_down_action=limit_down_action,
        )
        self.holding_count = holding_count
        self.weight_func = weight_func
        self.softmax_temperature = softmax_temperature
        self.rank_power_alpha = rank_power_alpha

    def compute_trade_plan(
        self,
        scores: dict[str, float],
        current_holdings: list[str],
        portfolio_value: float,
        positions: dict[str, Position],
        market: MarketData,
        signal_date: pd.Timestamp,
    ) -> TradePlan:
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        selected = ranked[: self.holding_count]

        if not selected:
            to_sell: dict[str, float] = {}
            for code in current_holdings:
                to_sell[code] = 0.0
            return TradePlan(to_sell=to_sell, to_buy={}, target_weights={})

        codes = [code for code, _ in selected]
        values = np.array([float(value) for _, value in selected], dtype=float)

        if self.weight_func == "rank_power":
            alpha = max(float(self.rank_power_alpha), 1e-9)
            ranks = np.arange(1, len(codes) + 1, dtype=float)
            raw = 1.0 / np.power(ranks, alpha)
        else:
            temperature = max(float(self.softmax_temperature), 1e-9)
            shifted = (values - float(values.max())) / temperature
            shifted = np.clip(shifted, -60.0, 60.0)
            raw = np.exp(shifted)

        denom = float(raw.sum())
        if not math.isfinite(denom) or denom <= 0:
            equal = 1.0 / len(codes)
            normalized = np.full(len(codes), equal)
        else:
            normalized = raw / denom

        selected_set = set(codes)
        target_weights: dict[str, float] = {}
        for i, code in enumerate(codes):
            target_weights[code] = float(normalized[i])
        for code in current_holdings:
            if code not in selected_set:
                target_weights[code] = 0.0

        to_sell_weights: dict[str, float] = {}
        for code in current_holdings:
            if code not in selected_set:
                to_sell_weights[code] = 0.0
            else:
                pos = positions.get(code)
                if pos is not None and pos.shares > 0 and portfolio_value > 0:
                    close = market.get_close(code, signal_date)
                    if not np.isnan(close):
                        current_weight = (pos.shares * close) / portfolio_value
                        target_weight = target_weights.get(code, 0.0)
                        if current_weight > target_weight + 1e-6:
                            to_sell_weights[code] = target_weight

        to_buy_weights: dict[str, float] = {}
        for code in codes:
            pos = positions.get(code)
            current_weight = 0.0
            if pos is not None and pos.shares > 0 and portfolio_value > 0:
                close = market.get_close(code, signal_date)
                if not np.isnan(close):
                    current_weight = (pos.shares * close) / portfolio_value
            target_weight = target_weights.get(code, 0.0)
            if target_weight > current_weight + 1e-6:
                to_buy_weights[code] = target_weight

        return TradePlan(
            to_sell=to_sell_weights,
            to_buy=to_buy_weights,
            target_weights=target_weights,
        )
