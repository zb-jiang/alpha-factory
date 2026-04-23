from __future__ import annotations

import math

import numpy as np

try:
    import backtrader as bt
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError(
        "缺少依赖 backtrader，请先执行: python -m pip install backtrader"
    ) from exc

from .topk_dropout import BaseFactorStrategy


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
        ("weight_mode", "benchmark_tilt"),
        ("active_weight_bound", 0.02),
        ("tracking_error_limit", 0.05),
    )

    def _compute_target_holdings(
        self,
        scores: dict[str, float],
        current_holdings: list[str],
    ) -> list[str]:
        del current_holdings
        holding_count = max(int(self.p.holding_count), 1)
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [code for code, _ in ranked[:holding_count]]

    def _enhanced_weights(self, scores: dict[str, float]) -> dict[str, float]:
        selected = self._compute_target_holdings(scores, current_holdings=[])
        if not selected:
            return {}

        n = len(selected)
        base_weight = 1.0 / n
        mode = str(getattr(self.p, "weight_mode", "benchmark_tilt") or "benchmark_tilt").strip().lower()
        if mode == "equal_weight_enhanced":
            return {code: base_weight for code in selected}

        bound = max(float(self.p.active_weight_bound), 0.0)
        te_limit = max(float(self.p.tracking_error_limit), 0.0)
        values = np.array([float(scores[code]) for code in selected], dtype=float)
        if mode == "score_tilt":
            min_value = float(np.nanmin(values))
            shifted = np.where(np.isfinite(values), values - min_value + 1e-12, 0.0)
            denom = float(np.nansum(shifted))
            if not math.isfinite(denom) or denom <= 0:
                return {code: base_weight for code in selected}
            raw = shifted / denom
            tilt = raw - base_weight
            raw_tilt = np.clip(tilt, -bound, bound)
        else:
            std = float(values.std(ddof=0))
            if not math.isfinite(std) or std <= 1e-12:
                return {code: base_weight for code in selected}
            z = (values - float(values.mean())) / std
            raw_tilt = z * bound
            raw_tilt = np.clip(raw_tilt, -bound, bound)

        # 简化 tracking-error 约束：若偏离过大，对 tilt 做统一缩放。
        current_te = float(np.sqrt(np.mean(np.square(raw_tilt))))
        if te_limit > 0 and current_te > te_limit:
            raw_tilt = raw_tilt * (te_limit / current_te)

        weights = base_weight + raw_tilt
        weights = np.clip(weights, 0.0, None)
        denom = float(weights.sum())
        if not math.isfinite(denom) or denom <= 0:
            return {code: base_weight for code in selected}
        weights = weights / denom
        return {code: float(weight) for code, weight in zip(selected, weights)}

    def next(self) -> None:
        self._flush_pending_targets()
        if not self._is_rebalance_day():
            return
        scores = self._cross_section_scores()
        if not scores:
            return

        exposure_scale = max(0.0, min(1.0, float(self._target_exposure_scale())))
        target_weights = self._enhanced_weights(scores)
        scaled = {code: weight * exposure_scale for code, weight in target_weights.items()}

        for data in self.datas:
            code = str(data._name)
            if code == str(self.p.benchmark_name):
                continue
            self._submit_target_percent(data=data, target=float(scaled.get(code, 0.0)))
