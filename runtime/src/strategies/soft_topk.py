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
    )

    def _compute_target_holdings(
        self,
        scores: dict[str, float],
        current_holdings: list[str],
    ) -> list[str]:
        del current_holdings
        holding_count = max(int(self.p.holding_count), 1)
        top_n = max(int(self.p.top_n), 1)
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [code for code, _ in ranked[: min(top_n, holding_count)]]

    def _weighted_targets(self, scores: dict[str, float]) -> dict[str, float]:
        holding_count = max(int(self.p.holding_count), 1)
        top_n = max(int(self.p.top_n), 1)
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        selected = ranked[: min(top_n, holding_count)]
        if not selected:
            return {}

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
            # 防止极端值导致 exp 溢出
            shifted = np.clip(shifted, -60.0, 60.0)
            raw = np.exp(shifted)

        denom = float(raw.sum())
        if not math.isfinite(denom) or denom <= 0:
            equal = 1.0 / len(codes)
            return {code: equal for code in codes}
        normalized = raw / denom
        return {code: float(weight) for code, weight in zip(codes, normalized)}

    def next(self) -> None:
        self._flush_pending_targets()
        if not self._is_rebalance_day():
            return
        scores = self._cross_section_scores()
        if not scores:
            return

        exposure_scale = max(0.0, min(1.0, float(self._target_exposure_scale())))
        target_weights = self._weighted_targets(scores)
        scaled = {code: weight * exposure_scale for code, weight in target_weights.items()}

        # 应用资金缓冲
        target_codes = sorted(scaled.keys())
        scaled = self._apply_cash_buffer(scaled, target_codes)

        for data in self.datas:
            code = str(data._name)
            if code == str(self.p.benchmark_name):
                continue
            self._submit_target_percent(data=data, target=float(scaled.get(code, 0.0)))
