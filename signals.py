"""
signals.py — Три сетапа: First5, Premarket, Gap.
Каждая функция возвращает SignalResult или None.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Optional
from zoneinfo import ZoneInfo

import config
from data_manager import Candle, DataManager, SymbolState
from logger_setup import get_logger

log = get_logger("signals")
NY  = ZoneInfo(config.NY_TZ)


@dataclass
class SignalResult:
    setup:       str          # "first5" | "premarket" | "gap"
    direction:   str          # "long" | "short"
    symbol:      str
    entry_price: float
    stop_price:  float
    tp_price:    float
    candle:      Candle       # сигнальная свеча


# ─── Общие хелперы ────────────────────────────────────────────────────

def _is_session(candle: Candle) -> bool:
    """Свеча внутри торговой сессии (9:35 – 16:00 NY)."""
    t = candle.dt_ny.time()
    return time(config.SESSION_START_H, config.SESSION_START_M) <= t < time(config.SESSION_END_H, config.SESSION_END_M)


def _strong_bull(c: Candle) -> bool:
    """Сильная бычья свеча: тело >= STRONG_BODY_PCT % диапазона, верхний хвост <= STRONG_WICK_PCT %."""
    if not c.is_bullish:
        return False
    body_pct  = (c.body   / c.range) * 100
    wick_pct  = (c.upper_wick / c.range) * 100
    return body_pct >= config.STRONG_BODY_PCT and wick_pct <= config.STRONG_WICK_PCT


def _strong_bear(c: Candle) -> bool:
    """Сильная медвежья свеча: тело >= STRONG_BODY_PCT %, нижний хвост <= STRONG_WICK_PCT %."""
    if c.is_bullish:
        return False
    body_pct  = (c.body   / c.range) * 100
    wick_pct  = (c.lower_wick / c.range) * 100
    return body_pct >= config.STRONG_BODY_PCT and wick_pct <= config.STRONG_WICK_PCT


def _in_zone(price: float, level: float) -> bool:
    """Свеча дотронулась до zone ± RETEST_TOLERANCE_PCT."""
    tol = level * config.RETEST_TOLERANCE_PCT
    return (level - tol) <= price <= (level + tol)


def _long_levels(entry: float, stop: float):
    """TP = entry + 2*(entry-stop)."""
    risk = entry - stop
    tp   = entry + 2 * risk
    return tp


def _short_levels(entry: float, stop: float):
    """TP = entry - 2*(stop-entry)."""
    risk = stop - entry
    tp   = entry - 2 * risk
    return tp


# ─── Сетап 1: First 5-Min ─────────────────────────────────────────────

def check_first5(dm: DataManager, symbol: str, closed: Candle) -> Optional[SignalResult]:
    """
    После 9:35 NY:
    ЛОНГ: предыдущее close пробило high_first5 → текущая ретестирует его → сильный бычий бар.
    ШОРТ: аналогично для low_first5.
    """
    state = dm.state(symbol)
    if not state.first5_ready or not _is_session(closed):
        return None

    candles = dm.last_candles(symbol, 3)
    if len(candles) < 2:
        return None

    prev, cur = candles[-2], candles[-1]
    level_h = state.high_first5
    level_l = state.low_first5

    # ── ЛОНГ ──
    if (not state.broken_up_first5 and
            prev.close > level_h and                  # пробой телом предыдущей свечи
            (cur.low <= level_h) and                  # ретест (зашли под уровень)
            _strong_bull(cur)):                       # сильный бычий бар
        entry = cur.close
        stop  = min(cur.low, prev.low) * (1 - config.STOP_BUFFER_PCT)
        if stop >= entry:
            return None
        tp = _long_levels(entry, stop)
        state.broken_up_first5 = True
        log.info(f"[{symbol}] СИГНАЛ First5 ЛОНГ | E={entry:.4f} SL={stop:.4f} TP={tp:.4f}")
        return SignalResult("first5", "long", symbol, entry, stop, tp, cur)

    # ── ШОРТ ──
    if (not state.broken_down_first5 and
            prev.close < level_l and
            (cur.high >= level_l) and
            _strong_bear(cur)):
        entry = cur.close
        stop  = max(cur.high, prev.high) * (1 + config.STOP_BUFFER_PCT)
        if stop <= entry:
            return None
        tp = _short_levels(entry, stop)
        state.broken_down_first5 = True
        log.info(f"[{symbol}] СИГНАЛ First5 ШОРТ | E={entry:.4f} SL={stop:.4f} TP={tp:.4f}")
        return SignalResult("first5", "short", symbol, entry, stop, tp, cur)

    return None


# ─── Сетап 2: Premarket High / Low ────────────────────────────────────

def check_premarket(dm: DataManager, symbol: str, closed: Candle) -> Optional[SignalResult]:
    """
    После 9:30 NY:
    ЛОНГ: пробой premarket_high телом → ретест → сильный бычий бар.
    ШОРТ: пробой premarket_low → ретест → сильный медвежий бар.
    """
    state = dm.state(symbol)
    if not state.premarket_ready or not _is_session(closed):
        return None

    candles = dm.last_candles(symbol, 3)
    if len(candles) < 2:
        return None

    prev, cur = candles[-2], candles[-1]
    pm_h = state.premarket_high
    pm_l = state.premarket_low

    # ── ЛОНГ ──
    if (not state.broken_up_pm and
            prev.close > pm_h and
            cur.low <= pm_h and
            _strong_bull(cur)):
        entry = cur.close
        stop  = min(cur.low, prev.low) * (1 - config.STOP_BUFFER_PCT)
        if stop >= entry:
            return None
        tp = _long_levels(entry, stop)
        state.broken_up_pm = True
        log.info(f"[{symbol}] СИГНАЛ Premarket ЛОНГ | E={entry:.4f} SL={stop:.4f} TP={tp:.4f}")
        return SignalResult("premarket", "long", symbol, entry, stop, tp, cur)

    # ── ШОРТ ──
    if (not state.broken_down_pm and
            prev.close < pm_l and
            cur.high >= pm_l and
            _strong_bear(cur)):
        entry = cur.close
        stop  = max(cur.high, prev.high) * (1 + config.STOP_BUFFER_PCT)
        if stop <= entry:
            return None
        tp = _short_levels(entry, stop)
        state.broken_down_pm = True
        log.info(f"[{symbol}] СИГНАЛ Premarket ШОРТ | E={entry:.4f} SL={stop:.4f} TP={tp:.4f}")
        return SignalResult("premarket", "short", symbol, entry, stop, tp, cur)

    return None


# ─── Сетап 3: Gap-Breakout ────────────────────────────────────────────

def check_gap(dm: DataManager, symbol: str, closed: Candle) -> Optional[SignalResult]:
    """
    Fallback: цена ушла далеко от уровней без ретеста (> GAP_MIN_ATR_MULT * ATR).
    Ищем первую контр-свечу (откатный бар) и ждём ретеста её диапазона.

    Алгоритм:
    1. Если цена пробила уровень и ушла > 1.5 ATR — фиксируем тренд.
    2. Первая свеча против тренда → её [low, high] = gap_zone.
    3. Следующие свечи: если цена возвращается в gap_zone + сильный бар по тренду → сигнал.
    """
    state = dm.state(symbol)
    if not _is_session(closed):
        return None

    atr = dm.atr(symbol)
    if atr <= 0:
        return None

    candles = dm.last_candles(symbol, 10)
    if len(candles) < 3:
        return None

    # Определяем, есть ли уровень и насколько далеко ушла цена
    ref_levels = []
    if state.first5_ready:
        ref_levels += [state.high_first5, state.low_first5]
    if state.premarket_ready:
        ref_levels += [state.premarket_high, state.premarket_low]

    if not ref_levels:
        return None

    # ── Поиск gap_candle (контр-трендовой свечи) ──
    # Восходящий тренд: цена >> уровня сверху — ищем первый красный бар
    # Нисходящий тренд: цена << уровня снизу — ищем первый зелёный бар
    for trend_dir, level_key in [("up", "up"), ("down", "down")]:
        gap_key = trend_dir  # "up" | "down"

        # Уже есть gap_candle для этого направления — смотрим ретест
        if gap_key in state.gap_candles:
            gc = state.gap_candles[gap_key]  # gap_candle

            # Цена вернулась в диапазон gap_candle
            in_zone = gc.low <= closed.close <= gc.high or gc.low <= closed.low <= gc.high

            if trend_dir == "up" and in_zone and _strong_bull(closed):
                entry = closed.close
                stop  = gc.low * (1 - config.STOP_BUFFER_PCT)
                if stop >= entry:
                    continue
                tp = _long_levels(entry, stop)
                # Сбрасываем, чтобы не дублировать
                del state.gap_candles[gap_key]
                log.info(f"[{symbol}] СИГНАЛ Gap ЛОНГ | E={entry:.4f} SL={stop:.4f} TP={tp:.4f}")
                return SignalResult("gap", "long", symbol, entry, stop, tp, closed)

            if trend_dir == "down" and in_zone and _strong_bear(closed):
                entry = closed.close
                stop  = gc.high * (1 + config.STOP_BUFFER_PCT)
                if stop <= entry:
                    continue
                tp = _short_levels(entry, stop)
                del state.gap_candles[gap_key]
                log.info(f"[{symbol}] СИГНАЛ Gap ШОРТ | E={entry:.4f} SL={stop:.4f} TP={tp:.4f}")
                return SignalResult("gap", "short", symbol, entry, stop, tp, closed)

            continue

        # Gap_candle ещё не найдена — ищем
        nearest_level = min(ref_levels, key=lambda l: abs(closed.close - l))
        dist = abs(closed.close - nearest_level)

        if dist < config.GAP_MIN_ATR_MULT * atr:
            continue  # цена ещё близко к уровню — ждём

        # Цена далеко. Ищем контр-свечу среди последних баров
        for i in range(len(candles) - 1, 0, -1):
            c = candles[i]
            # В аптренде (выше уровня) → первая красная = контр-свеча
            if trend_dir == "up" and closed.close > nearest_level and not c.is_bullish:
                # Проверяем, что после неё были зелёные (продолжение роста)
                if i < len(candles) - 1:  # не последняя
                    state.gap_candles[gap_key] = c
                    log.info(f"[{symbol}] Gap контр-свеча UP: H={c.high} L={c.low}")
                    break
            # В даунтренде → первая зелёная
            if trend_dir == "down" and closed.close < nearest_level and c.is_bullish:
                if i < len(candles) - 1:
                    state.gap_candles[gap_key] = c
                    log.info(f"[{symbol}] Gap контр-свеча DOWN: H={c.high} L={c.low}")
                    break

    return None
