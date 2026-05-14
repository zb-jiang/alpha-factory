from __future__ import annotations

from . import BaseStrategy, MarketData, Position, TradePlan


class EnhancedIndexingStrategy(BaseStrategy):
    def __init__(
        self,
        holding_count: int = 30,
        active_weight_bound: float = 0.02,
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
        self.holding_count = holding_count
        self.active_weight_bound = active_weight_bound

    def compute_trade_plan(
        self,
        scores: dict[str, float],
        current_holdings: list[str],
        portfolio_value: float,
        positions: dict[str, Position],
        market: MarketData,
        signal_date,
    ) -> TradePlan:
        raise NotImplementedError("EnhancedIndexing 策略暂未实现")
