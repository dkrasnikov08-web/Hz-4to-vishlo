from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Signal:
    direction: str       # "long" | "short"
    entry_price: float
    stop_price: float
    tp_price: float
    ts_ms: int
    strategy_name: str
    symbol: str

    @property
    def risk_dist(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def rr(self) -> float:
        return abs(self.tp_price - self.entry_price) / self.risk_dist if self.risk_dist > 0 else 0


class BaseStrategy:
    name: str = "base"
    timeframe_min: int = 15

    def check(
        self,
        symbol: str,
        candles: list,
        analysis: dict,
        ts_ms: int,
    ) -> Optional[Signal]:
        raise NotImplementedError
