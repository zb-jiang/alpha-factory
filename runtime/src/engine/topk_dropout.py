from __future__ import annotations

import numpy as np
import pandas as pd

from . import BaseStrategy, MarketData, Position, TradePlan


class TopKDropoutStrategy(BaseStrategy):
    def __init__(
        self,
        buy_top_n: int = 20,
        sell_drop_to: int = 40,
        holding_count: int = 20,
        max_drop_per_day: int = 5,
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
        self.buy_top_n = buy_top_n
        self.sell_drop_to = sell_drop_to
        self.holding_count = holding_count
        self.max_drop_per_day = max_drop_per_day

    def compute_trade_plan(
        self,
        scores: dict[str, float],
        current_holdings: list[str],
        portfolio_value: float,
        positions: dict[str, Position],
        market: MarketData,
        signal_date: pd.Timestamp,
    ) -> TradePlan:
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0][2:] if len(item[0]) > 2 and item[0][:2] in ("SH", "SZ") else item[0]))
        ranked_codes = [code for code, _ in ranked]
        keep_rank_set = set(ranked_codes[: self.sell_drop_to])

        drop_candidates = [code for code in current_holdings if code not in keep_rank_set]
        drop_candidates = sorted(drop_candidates, key=lambda code: (scores.get(code, -np.inf), code[2:] if len(code) > 2 and code[:2] in ("SH", "SZ") else code))

        to_sell_count = min(len(drop_candidates), self.max_drop_per_day)
        to_sell_set = set(drop_candidates[:to_sell_count])

        kept = [code for code in current_holdings if code not in to_sell_set]

        max_new_buys = to_sell_count if current_holdings else self.holding_count
        if current_holdings:
            shortfall = self.holding_count - len(current_holdings) + to_sell_count
            max_new_buys = max(max_new_buys, shortfall)

        new_buys: list[str] = []
        for code in ranked_codes:
            if code in kept:
                continue
            if len(kept) + len(new_buys) >= self.holding_count:
                break
            if len(new_buys) >= max_new_buys:
                break
            new_buys.append(code)

        to_sell_weights: dict[str, float] = {}
        for code in to_sell_set:
            to_sell_weights[code] = 0.0

        if not current_holdings:
            target_count = self.holding_count
        else:
            target_count = len(kept) + len(new_buys)
        if target_count <= 0:
            target_count = 1

        weight_per_stock = 1.0 / target_count
        to_buy_weights: dict[str, float] = {}
        for code in new_buys:
            to_buy_weights[code] = weight_per_stock

        target_weights: dict[str, float] = {}
        for code in to_sell_set:
            target_weights[code] = 0.0
        for code in kept:
            pos = positions.get(code)
            if pos is not None and pos.shares > 0 and not np.isnan(pos.avg_cost) and portfolio_value > 0:
                close = market.get_close(code, signal_date)
                if not np.isnan(close):
                    target_weights[code] = (pos.shares * close) / portfolio_value
                else:
                    target_weights[code] = 0.0
            else:
                target_weights[code] = 0.0
        for code in new_buys:
            target_weights[code] = weight_per_stock

        return TradePlan(
            to_sell=to_sell_weights,
            to_buy=to_buy_weights,
            target_weights=target_weights,
        )
