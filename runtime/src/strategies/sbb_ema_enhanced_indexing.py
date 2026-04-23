from __future__ import annotations

from typing import Any

try:
    import backtrader as bt
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError(
        "缺少依赖 backtrader，请先执行: python -m pip install backtrader"
    ) from exc

from .enhanced_indexing import EnhancedIndexingStrategy


class SBBEMAEnhancedIndexingStrategy(EnhancedIndexingStrategy):
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
            raise RuntimeError(f"SBBEMAEnhancedIndexingStrategy 缺少基准数据源: {benchmark_name}")
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

    def next(self) -> None:
        self._flush_pending_targets()
        if not self._is_rebalance_day():
            return
        scores = self._cross_section_scores()
        if not scores:
            return
        current_set = set(self._current_holdings())
        exposure_scale = max(0.0, min(1.0, float(self._target_exposure_scale())))
        target_weights = self._enhanced_weights(scores)
        filtered: dict[str, float] = {}
        for code, weight in target_weights.items():
            if code in current_set or self._allow_new_open(code):
                filtered[code] = float(weight)
        weight_sum = float(sum(filtered.values()))
        if weight_sum > 0:
            filtered = {code: weight / weight_sum for code, weight in filtered.items()}
        scaled = {code: weight * exposure_scale for code, weight in filtered.items()}
        for data in self.datas:
            code = str(data._name)
            if code == str(self.p.benchmark_name):
                continue
            self._submit_target_percent(data=data, target=float(scaled.get(code, 0.0)))
