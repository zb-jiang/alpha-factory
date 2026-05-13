from __future__ import annotations

import numpy as np

try:
    import backtrader as bt
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError(
        "缺少依赖 backtrader，请先执行: python -m pip install backtrader"
    ) from exc

from .base_strategy import BaseFactorStrategy


class EnhancedIndexingStrategy(BaseFactorStrategy):
    params = (
        ("rebalance_dates", None),
        ("strict_assertions", False),
        ("max_rebalance_logs", 0),
        ("benchmark_name", "__benchmark__"),
        ("trade_price_mode", "next_open"),
        ("suspend_action", "skip"),
        ("limit_up_action", "skip_buy"),
        ("limit_down_action", "delay_sell"),
        ("holding_count", 30),
        ("active_weight_bound", 0.02),
        ("benchmark_weights", None),
        ("min_score_coverage", 0.90),
    )

    def _compute_target_weights(self, scores: dict[str, float]) -> dict[str, float]:
        holding_count = max(int(self.p.holding_count), 1)
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        selected = [code for code, _ in ranked[:holding_count]]

        if not selected:
            return {code: 0.0 for code in scores}

        n = len(selected)
        bw = self.p.benchmark_weights or {}
        base_weight = 1.0 / n
        benchmark_w = np.array([float(bw.get(code, base_weight)) for code in selected], dtype=float)
        bw_sum = float(benchmark_w.sum())
        if not np.isfinite(bw_sum) or bw_sum <= 0:
            benchmark_w = np.full(n, base_weight)
        else:
            benchmark_w = benchmark_w / bw_sum

        bound = max(float(self.p.active_weight_bound), 0.0)
        values = np.array([float(scores[code]) for code in selected], dtype=float)
        std = float(values.std(ddof=0))
        if not np.isfinite(std) or std <= 1e-12:
            weights = benchmark_w.copy()
        else:
            z = (values - float(values.mean())) / std
            raw_tilt = z * bound
            raw_tilt = np.clip(raw_tilt, -bound, bound)
            weights = benchmark_w + raw_tilt
            weights = np.clip(weights, 0.0, None)

        denom = float(weights.sum())
        if not np.isfinite(denom) or denom <= 0:
            weights = benchmark_w.copy()
            denom = float(weights.sum())
            if not np.isfinite(denom) or denom <= 0:
                weights = np.full(n, 1.0 / n)
                denom = 1.0
        weights = weights / denom

        selected_set = set(selected)
        result: dict[str, float] = {}
        for code in scores:
            if code in selected_set:
                idx = selected.index(code)
                result[code] = float(weights[idx])
            else:
                result[code] = 0.0
        return result
