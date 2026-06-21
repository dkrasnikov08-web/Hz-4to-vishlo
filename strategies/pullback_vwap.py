from __future__ import annotations

from typing import Optional

from strategies.base import BaseStrategy, Signal


class PullbackVWAP(BaseStrategy):
    name = "pullback"
    timeframe_min = 15

    def __init__(
        self,
        ema_period: int = 20,
        min_impulse_pct: float = 0.02,
        pullback_tolerance_atr: float = 0.5,
        stop_atr_mult: float = 1.5,
    ):
        self.ema_period = ema_period
        self.min_impulse_pct = min_impulse_pct
        self.pullback_tolerance_atr = pullback_tolerance_atr
        self.stop_atr_mult = stop_atr_mult

    def check(
        self,
        symbol: str,
        candles: list,
        analysis: dict,
        ts_ms: int,
    ) -> Optional[Signal]:
        if len(candles) < self.ema_period + 5:
            return None

        ema = analysis.get("ema_20", 0)
        atr = analysis.get("atr", 0)
        if ema <= 0 or atr <= 0:
            return None

        c = candles[-1]
        trend_up = c.close > ema and analysis.get("trend_ema", 0) > 0
        trend_down = c.close < ema and analysis.get("trend_ema", 0) < 0
        if not trend_up and not trend_down:
            return None

        if trend_up:
            impulse = (c.close - candles[-10].low) / candles[-10].low
            if impulse < self.min_impulse_pct:
                return None
            if c.low > ema + atr * self.pullback_tolerance_atr:
                return None
            if not c.is_bullish or c.lower_wick < c.body * 0.3:
                return None
            stop = c.close - atr * self.stop_atr_mult
            if stop >= c.close:
                return None
            risk = c.close - stop
            tp = c.close + risk * 2
            return Signal("long", c.close, stop, tp, ts_ms, self.name, symbol)

        if trend_down:
            impulse = (candles[-10].high - c.close) / candles[-10].high
            if impulse < self.min_impulse_pct:
                return None
            if c.high < ema - atr * self.pullback_tolerance_atr:
                return None
            if c.is_bullish or c.upper_wick < c.body * 0.3:
                return None
            stop = c.close + atr * self.stop_atr_mult
            if stop <= c.close:
                return None
            risk = stop - c.close
            tp = c.close - risk * 2
            return Signal("short", c.close, stop, tp, ts_ms, self.name, symbol)

        return None
