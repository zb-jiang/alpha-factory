from __future__ import annotations

from typing import Any

try:
    import backtrader as bt
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError(
        "缺少依赖 backtrader，请先执行: python -m pip install backtrader"
    ) from exc

from .soft_topk import SoftTopkStrategy


class SBBEMASoftTopkStrategy(SoftTopkStrategy):
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
        ("ema_period", 60),
        ("timing_reduce_to", 0.5),
        ("stock_open_filter", "rsi"),
        ("stock_ema_period", 60),
        ("rsi_period", 14),
        ("rsi_buy_max", 70.0),
    )

    def __init__(self) -> None:
        super().__init__()
        benchmark_name = str(self.p.benchmark_name)
        benchmark_data = None
        stock_ema: dict[str, Any] = {}
        for data in self.datas:
            if str(data._name) == benchmark_name:
                benchmark_data = data
                break
        for data in self.datas:
            name = str(data._name)
            if name == benchmark_name:
                continue
            stock_ema[name] = bt.indicators.EMA(data.close, period=int(self.p.stock_ema_period))
        if benchmark_data is None:
            raise RuntimeError(f"SBBEMASoftTopkStrategy 缺少基准数据源: {benchmark_name}")
        self._benchmark_data = benchmark_data
        self._ema = bt.indicators.EMA(self._benchmark_data.close, period=int(self.p.ema_period))
        self._stock_ema = stock_ema
        stock_rsi: dict[str, Any] = {}
        if str(self.p.stock_open_filter).strip().lower() == "rsi":
            for data in self.datas:
                name = str(data._name)
                if name == benchmark_name:
                    continue
                stock_rsi[name] = bt.indicators.RSI(data.close, period=int(self.p.rsi_period))
        self._stock_rsi = stock_rsi

    def _is_timing_active(self) -> bool:
        close = float(self._benchmark_data.close[0])
        ema_value = float(self._ema[0])
        return close < ema_value

    def _target_exposure_scale(self) -> float:
        return float(self.p.timing_reduce_to) if self._is_timing_active() else 1.0

    def _stock_in_uptrend(self, code: str) -> bool:
        data_ref = None
        for data in self.datas:
            if str(data._name) == code:
                data_ref = data
                break
        if data_ref is None:
            return False
        close = float(data_ref.close[0])
        ema_value = float(self._stock_ema[code][0])
        return close >= ema_value

    def _stock_rsi_ok(self, code: str) -> bool:
        indicator = self._stock_rsi.get(code)
        if indicator is None:
            return False
        value = float(indicator[0])
        if value != value:
            return False
        return value <= float(self.p.rsi_buy_max)

    def _allow_new_open(self, code: str) -> bool:
        mode = str(self.p.stock_open_filter).strip().lower()
        if mode == "none":
            return True
        if mode == "ema":
            return self._stock_in_uptrend(code)
        if mode == "rsi":
            return self._stock_rsi_ok(code)
        return self._stock_in_uptrend(code)

    def _compute_target_weights(self, scores: dict[str, float]) -> dict[str, float]:
        base_weights = super()._compute_target_weights(scores)
        current_set = set(self._current_holdings())
        filtered_weights: dict[str, float] = {}
        for code, weight in base_weights.items():
            if weight > 0 and code not in current_set and not self._allow_new_open(code):
                filtered_weights[code] = 0.0
            else:
                filtered_weights[code] = weight
        positive_weights = {c: w for c, w in filtered_weights.items() if w > 0}
        if positive_weights:
            weight_sum = sum(positive_weights.values())
            if weight_sum > 0:
                scale = 1.0 / weight_sum
                for code in positive_weights:
                    filtered_weights[code] = filtered_weights[code] * scale
        return filtered_weights
