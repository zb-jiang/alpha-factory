from __future__ import annotations

import math

import numpy as np

try:
    import backtrader as bt
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError(
        "缺少依赖 backtrader，请先执行: python -m pip install backtrader"
    ) from exc

from .base_strategy import BaseFactorStrategy


class SoftTopkStrategy(BaseFactorStrategy):
    params = (
        ("rebalance_dates", None),
        ("strict_assertions", False),
        ("max_rebalance_logs", 0),
        ("benchmark_name", "__benchmark__"),
        ("trade_price_mode", "next_open"),
        ("suspend_action", "skip"),
        ("limit_up_action", "skip_buy"),
        ("limit_down_action", "delay_sell"),
        ("top_n", 30),
        ("holding_count", 30),
        ("weight_func", "softmax"),
        ("softmax_temperature", 1.0),
        ("rank_power_alpha", 1.0),
        ("min_score_coverage", 0.90),
    )

    def _compute_target_weights(self, scores: dict[str, float]) -> dict[str, float]:
        holding_count = max(int(self.p.holding_count), 1)
        top_n = max(int(self.p.top_n), 1)
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        selected = ranked[: min(top_n, holding_count)]

        if not selected:
            return {code: 0.0 for code in scores}

        codes = [code for code, _ in selected]
        values = np.array([float(value) for _, value in selected], dtype=float)
        weight_func = str(self.p.weight_func or "softmax").strip().lower()

        if weight_func == "rank_power":
            alpha = max(float(self.p.rank_power_alpha), 1e-9)
            ranks = np.arange(1, len(codes) + 1, dtype=float)
            raw = 1.0 / np.power(ranks, alpha)
        else:
            temperature = max(float(self.p.softmax_temperature), 1e-9)
            shifted = (values - float(values.max())) / temperature
            shifted = np.clip(shifted, -60.0, 60.0)
            raw = np.exp(shifted)

        denom = float(raw.sum())
        if not math.isfinite(denom) or denom <= 0:
            equal = 1.0 / len(codes)
            selected_set = set(codes)
            return {code: equal if code in selected_set else 0.0 for code in scores}
        normalized = raw / denom

        selected_set = set(codes)
        result: dict[str, float] = {}
        for code in scores:
            if code in selected_set:
                idx = codes.index(code)
                result[code] = float(normalized[idx])
            else:
                result[code] = 0.0
        return result
