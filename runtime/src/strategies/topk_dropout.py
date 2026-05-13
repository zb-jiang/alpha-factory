from __future__ import annotations

import numpy as np

try:
    import backtrader as bt
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError(
        "缺少依赖 backtrader，请先执行: python -m pip install backtrader"
    ) from exc

from .base_strategy import BaseFactorStrategy


class TopkDropoutStrategy(BaseFactorStrategy):
    params = (
        ("rebalance_dates", None),
        ("strict_assertions", True),
        ("max_rebalance_logs", 20),
        ("benchmark_name", "__benchmark__"),
        ("trade_price_mode", "next_open"),
        ("suspend_action", "skip"),
        ("limit_up_action", "skip_buy"),
        ("limit_down_action", "delay_sell"),
        ("buy_top_n", 20),
        ("sell_drop_to", 40),
        ("holding_count", 20),
        ("weight_mode", "equal_weight"),
        ("max_drop_per_day", 5),
        ("min_score_coverage", 0.90),
    )

    def _compute_target_weights(self, scores: dict[str, float]) -> dict[str, float]:
        buy_top_n = int(self.p.buy_top_n)
        sell_drop_to = int(self.p.sell_drop_to)
        holding_count = int(self.p.holding_count)
        max_drop_per_day = int(self.p.max_drop_per_day)

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        ranked_codes = [code for code, _ in ranked]
        keep_rank_set = set(ranked_codes[:sell_drop_to])

        current_holdings = self._current_holdings()
        drop_candidates = [code for code in current_holdings if code not in keep_rank_set]
        drop_candidates = sorted(drop_candidates, key=lambda code: scores.get(code, -np.inf))

        to_sell_count = min(len(drop_candidates), max_drop_per_day)
        to_sell_set = set(drop_candidates[:to_sell_count])

        kept = [code for code in current_holdings if code not in to_sell_set]

        max_new_buys = to_sell_count if current_holdings else holding_count
        if current_holdings:
            shortfall = holding_count - len(current_holdings) + to_sell_count
            max_new_buys = max(max_new_buys, shortfall)
        new_buys: list[str] = []
        for code in ranked_codes[:buy_top_n]:
            if code in kept:
                continue
            if len(kept) + len(new_buys) >= holding_count:
                break
            if len(new_buys) >= max_new_buys:
                break
            new_buys.append(code)

        weights: dict[str, float] = {}
        for code in to_sell_set:
            weights[code] = 0.0

        if not current_holdings:
            target_count = holding_count
        else:
            target_count = len(kept) + len(new_buys)
        if target_count <= 0:
            target_count = 1

        weight_per_stock = 1.0 / target_count
        for code in new_buys:
            weights[code] = weight_per_stock

        return weights
