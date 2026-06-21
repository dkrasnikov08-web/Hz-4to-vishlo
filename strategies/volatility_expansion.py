from __future__ import annotations

from typing import Optional

from strategies.base import BaseStrategy, Signal


class VolatilityExpansion(BaseStrategy):
    name = "volatility"
    timeframe_min = 15

    def __init__(
        self,
        bw_lookback: int = 50,
        bw_percentile: float = 20,
        vol_mult: float = 1.5,
        stop_atr_mult: float = 2.0,
        min_adx: float = 20.0,
        use_trend_filter: bool = True,
    ):
        self.bw_lookback = bw_lookback
        self.bw_percentile = bw_percentile
        self.vol_mult = vol_mult
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
        if len(candles) < self.bw_lookback + 1:
            return None

        adx = analysis.get("adx", 0)
        if self.min_adx > 0 and adx < self.min_adx:
            return None

        bw = analysis.get("bb_width", 0)
        if bw <= 0:
            return None

        bw_min = analysis.get("bb_width_min", 0)
        bw_max = analysis.get("bb_width_max", 0)
        if bw_max <= bw_min:
            return None

        bw_pct = (bw - bw_min) / (bw_max - bw_min) * 100
        if bw_pct > self.bw_percentile:
            return None

        atr = analysis.get("atr", 0)
        vol_sma = analysis.get("volume_sma", 0)
        if atr <= 0 or vol_sma <= 0:
            return None

        c = candles[-1]
        vol_ok = c.volume >= vol_sma * self.vol_mult
        ema_200 = analysis.get("ema_200", 0)
        trend_long = (not self.use_trend_filter or ema_200 <= 0 or c.close > ema_200)
        trend_short = (not self.use_trend_filter or ema_200 <= 0 or c.close < ema_200)

        if c.close > analysis.get("bb_upper", 0) and c.is_bullish and vol_ok and trend_long:
            stop = c.close - atr * self.stop_atr_mult
            if stop >= c.close:
                return None
            risk = c.close - stop
            tp = c.close + risk * 2
            return Signal("long", c.close, stop, tp, ts_ms, self.name, symbol)

        if c.close < analysis.get("bb_lower", 0) and not c.is_bullish and vol_ok and trend_short:
            stop = c.close + atr * self.stop_atr_mult
            if stop <= c.close:
                return None
            risk = stop - c.close
            tp = c.close - risk * 2
            return Signal("short", c.close, stop, tp, ts_ms, self.name, symbol)

        return None
