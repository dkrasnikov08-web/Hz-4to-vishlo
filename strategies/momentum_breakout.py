from __future__ import annotations

from typing import Optional

from strategies.base import BaseStrategy, Signal


class MomentumBreakout(BaseStrategy):
    name = "momentum"
    timeframe_min = 15

    def __init__(
        self,
        lookback: int = 20,
        breakout_atr_mult: float = 0.4,
        max_range_pct: float = 0.04,
        vol_mult: float = 1.5,
        min_body_pct: float = 55,
        stop_atr_mult: float = 2.0,
        min_adx: float = 20.0,
        use_trend_filter: bool = True,
    ):
        self.lookback = lookback
        self.breakout_atr_mult = breakout_atr_mult
        self.max_range_pct = max_range_pct
        self.vol_mult = vol_mult
        self.min_body_pct = min_body_pct
        self.stop_atr_mult = stop_atr_mult
        self.min_adx = min_adx
        self.use_trend_filter = use_trend_filter

    def check(
        self,
        symbol: str,
        candles: list,
        analysis: dict,
        ts_ms: int,
    ) -> Optional[Signal]:
        if len(candles) < self.lookback + 2:
            return None

        atr = analysis.get("atr", 0)
        vol_sma = analysis.get("volume_sma", 0)
        if atr <= 0 or vol_sma <= 0:
            return None

        adx = analysis.get("adx", 0)
        if self.min_adx > 0 and adx < self.min_adx:
            return None

        c = candles[-1]

        ema_200 = analysis.get("ema_200", 0)

        prev_candles = candles[-(self.lookback + 1):-1]
        range_high = max(x.high for x in prev_candles)
        range_low = min(x.low for x in prev_candles)
        range_mid = (range_high + range_low) / 2
        range_pct = (range_high - range_low) / range_mid if range_mid > 0 else 0

        if range_pct > self.max_range_pct or range_pct < atr / c.close:
            return None

        body_pct = (c.body / c.range * 100) if c.range > 0 else 0
        vol_ok = c.volume >= vol_sma * self.vol_mult

        trend_long = (not self.use_trend_filter or ema_200 <= 0 or c.close > ema_200)
        trend_short = (not self.use_trend_filter or ema_200 <= 0 or c.close < ema_200)

        if (c.close > range_high + atr * self.breakout_atr_mult
                and body_pct >= self.min_body_pct
                and c.is_bullish
                and vol_ok
                and trend_long):
            stop = c.close - atr * self.stop_atr_mult
            risk = c.close - stop
            tp = c.close + risk * 2
            return Signal("long", c.close, stop, tp, ts_ms, self.name, symbol)

        if (c.close < range_low - atr * self.breakout_atr_mult
                and body_pct >= self.min_body_pct
                and not c.is_bullish
                and vol_ok
                and trend_short):
            stop = c.close + atr * self.stop_atr_mult
            risk = stop - c.close
            tp = c.close - risk * 2
            return Signal("short", c.close, stop, tp, ts_ms, self.name, symbol)

        return None
