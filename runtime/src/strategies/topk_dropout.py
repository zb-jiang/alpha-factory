from __future__ import annotations

from typing import Any

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
    )

    def _place_target_orders(
        self,
        target_holdings: list[str],
        scores: dict[str, float] | None = None,
    ) -> None:
        target_set = set(target_holdings)
        target_codes = sorted(target_set)
        exposure_scale = float(self._target_exposure_scale())
        exposure_scale = max(0.0, min(1.0, exposure_scale))
        if not target_set:
            for data in self.datas:
                if str(data._name) == str(self.p.benchmark_name):
                    continue
                if self.getposition(data).size > 0:
                    self._submit_target_percent(data=data, target=0.0)
            return

        mode = str(getattr(self.p, "weight_mode", "equal_weight") or "equal_weight").strip().lower()
        if mode != "score_weight" or not scores:
            equal_weight = exposure_scale / len(target_set)
            weights = {code: equal_weight for code in target_codes}
        else:
            values = np.array([float(scores.get(code, np.nan)) for code in target_codes], dtype=float)
            valid_mask = np.isfinite(values)
            if not valid_mask.any():
                equal_weight = exposure_scale / len(target_set)
                weights = {code: equal_weight for code in target_codes}
            else:
                min_value = float(np.min(values[valid_mask]))
                adjusted = np.where(valid_mask, values - min_value + 1e-12, 0.0)
                denom = float(np.sum(adjusted))
                if not np.isfinite(denom) or denom <= 0:
                    equal_weight = exposure_scale / len(target_set)
                    weights = {code: equal_weight for code in target_codes}
                else:
                    weights = {
                        code: float(adjusted[idx] / denom * exposure_scale)
                        for idx, code in enumerate(target_codes)
                    }

        # 应用资金缓冲
        weights = self._apply_cash_buffer(weights, target_codes)

        for data in self.datas:
            code = str(data._name)
            if code == str(self.p.benchmark_name):
                continue
            self._submit_target_percent(data=data, target=float(weights.get(code, 0.0)))

    def _compute_target_holdings(
        self,
        scores: dict[str, float],
        current_holdings: list[str],
    ) -> list[str]:
        buy_top_n = int(self.p.buy_top_n)
        sell_drop_to = int(self.p.sell_drop_to)
        holding_count = int(self.p.holding_count)
        max_drop_per_day = int(self.p.max_drop_per_day)

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        ranked_codes = [code for code, _ in ranked]
        top_buy_codes = ranked_codes[:buy_top_n]
        keep_rank_set = set(ranked_codes[:sell_drop_to])

        # 限制每日最多卖出 max_drop_per_day：对跌出阈值的现有持仓按分数从低到高卖出。
        drop_candidates = [code for code in current_holdings if code not in keep_rank_set]
        drop_candidates = sorted(drop_candidates, key=lambda code: scores.get(code, -np.inf))
        to_sell_set = set(drop_candidates[:max_drop_per_day])
        kept = [code for code in current_holdings if code not in to_sell_set]

        max_new_buys = len(to_sell_set) if current_holdings else holding_count
        added = 0
        for code in top_buy_codes:
            if code in kept:
                continue
            if len(kept) >= holding_count:
                break
            if added >= max_new_buys:
                break
            kept.append(code)
            added += 1

        target = kept[:holding_count]

        if self.p.strict_assertions:
            sell_count = len(set(current_holdings) - set(target))
            buy_count = len(set(target) - set(current_holdings))
            if len(target) > buy_top_n:
                raise AssertionError(
                    f"阶段三校验失败：持仓数 {len(target)} 超过 buy_top_n={buy_top_n}"
                )
            if current_holdings and sell_count > max_drop_per_day:
                raise AssertionError(
                    f"阶段三校验失败：单日换手 {sell_count} 超过 max_drop_per_day={max_drop_per_day}"
                )
            self._log_rebalance(target, sell_count=sell_count, buy_count=buy_count)

        return target
