from __future__ import annotations

from typing import Any

try:
    import backtrader as bt
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError(
        "缺少依赖 backtrader，请先执行: python -m pip install backtrader"
    ) from exc

from .topk_dropout import TopkDropoutStrategy


class SBBStrategyEMA(TopkDropoutStrategy):
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
        ("ema_period", 60),
        ("timing_reduce_to", 0.5),
        ("timing_strict_assertions", True),
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
            raise RuntimeError(f"SBBStrategyEMA 缺少基准数据源: {benchmark_name}")
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
        self._timing_log_count = 0

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
        if value != value:  # NaN
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

    def _compute_target_holdings(
        self,
        scores: dict[str, float],
        current_holdings: list[str],
    ) -> list[str]:
        base_target = super()._compute_target_holdings(scores, current_holdings)
        current_set = set(current_holdings)
        filtered: list[str] = []
        for code in base_target:
            # 个股择时：已有持仓允许继续持有；仅对新开仓做趋势过滤。
            if code in current_set or self._allow_new_open(code):
                filtered.append(code)
        return filtered

    def _log_rebalance(self, holdings: list[str], sell_count: int, buy_count: int) -> None:
        super()._log_rebalance(holdings, sell_count, buy_count)
        if self._timing_log_count >= int(self.p.max_rebalance_logs):
            return
        date_text = self._today()
        close = float(self._benchmark_data.close[0])
        ema_value = float(self._ema[0])
        scale = self._target_exposure_scale()
        print(
            f"[SBB_EMA] date={date_text}, benchmark_close={close:.4f}, "
            f"ema={ema_value:.4f}, exposure_scale={scale:.3f}"
        )
        self._timing_log_count += 1

    def next(self) -> None:
        super().next()
        if not self.p.timing_strict_assertions:
            return
        if not self._is_rebalance_day():
            return
        scale = self._target_exposure_scale()
        if self._is_timing_active():
            expected = float(self.p.timing_reduce_to)
            if abs(scale - expected) > 1e-9:
                raise AssertionError(
                    f"阶段四校验失败：择时触发后 exposure_scale={scale}，预期={expected}"
                )
