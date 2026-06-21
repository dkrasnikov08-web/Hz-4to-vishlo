"""
data_manager.py — Хранение OHLCV-свечей, вычисление ATR, построение диапазонов.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time, date
from typing import Deque, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import config
from logger_setup import get_logger

log = get_logger("data")

NY = ZoneInfo(config.NY_TZ)


@dataclass
class Candle:
    ts_ms: int          # открытие, Unix ms
    open:  float
    high:  float
    low:   float
    close: float
    volume: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low if self.high != self.low else 1e-10

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def dt_ny(self) -> datetime:
        return datetime.fromtimestamp(self.ts_ms / 1000, tz=NY)


@dataclass
class SymbolState:
    """Всё состояние бота по одному символу."""
    symbol: str

    # Кольцевой буфер свечей
    candles: Deque[Candle] = field(default_factory=lambda: deque(maxlen=config.MAX_CANDLE_HISTORY))

    # Текущая (ещё не закрытая) свеча
    current_candle: Optional[Candle] = None

    # === Ежедневные уровни ===
    # Сетап 1
    high_first5: Optional[float] = None
    low_first5:  Optional[float] = None
    first5_ready: bool = False

    # Сетап 2
    premarket_high: Optional[float] = None
    premarket_low:  Optional[float] = None
    premarket_ready: bool = False

    # Сетап 3 — gap-свеча
    gap_candles: Dict[str, Candle] = field(default_factory=dict)  # "up"|"down" -> candle

    # Пробитые уровни (чтобы не давать повторные сигналы)
    broken_up_first5:    bool = False
    broken_down_first5:  bool = False
    broken_up_pm:        bool = False
    broken_down_pm:      bool = False

    # Дата последнего сброса состояния
    last_reset_date: Optional[date] = None

    # Счётчик сделок за день
    trades_today: int = 0


class DataManager:
    def __init__(self, symbols: list[str] | None = None):
        syms = symbols if symbols is not None else config.SYMBOLS
        self.states: Dict[str, SymbolState] = {s: SymbolState(symbol=s) for s in syms}

    # ─── Обновление текущей свечи ────────────────────────────────────
    def update_candle(self, symbol: str, raw: dict) -> Optional[Candle]:
        """
        Принимает сырой kline-объект от Bybit WebSocket.
        Возвращает закрытую свечу (если она только что закрылась), иначе None.
        """
        state = self.states.get(symbol)
        if not state:
            return None

        is_closed = raw.get("confirm", False)
        candle = Candle(
            ts_ms  = int(raw["start"]),
            open   = float(raw["open"]),
            high   = float(raw["high"]),
            low    = float(raw["low"]),
            close  = float(raw["close"]),
            volume = float(raw["volume"]),
        )

        state.current_candle = candle

        if is_closed:
            state.candles.append(candle)
            self._check_daily_reset(state, candle)
            # _update_daily_ranges() обслуживает legacy-сетапы First5/Premarket/Gap
            # (signals.py), которые текущий бот (momentum/volatility) не использует.
            # Отключено, чтобы избежать AttributeError при несовпадении версий
            # config.py и падений на каждой свече. Раскомментировать только если
            # снова понадобятся сетапы из signals.py и config.py содержит все
            # *_M константы (SESSION_START_M, PREMARKET_START_M, FIRST5_START_M и т.д.)
            # self._update_daily_ranges(state, candle)
            return candle

        return None

    # ─── Ежедневный сброс ────────────────────────────────────────────
    def _check_daily_reset(self, state: SymbolState, candle: Candle):
        ny_date = candle.dt_ny.date()
        if state.last_reset_date != ny_date:
            log.info(f"[{state.symbol}] Новый торговый день {ny_date} — сброс состояния")
            state.high_first5      = None
            state.low_first5       = None
            state.first5_ready     = False
            state.premarket_high   = None
            state.premarket_low    = None
            state.premarket_ready  = False
            state.gap_candles      = {}
            state.broken_up_first5   = False
            state.broken_down_first5 = False
            state.broken_up_pm       = False
            state.broken_down_pm     = False
            state.trades_today       = 0
            state.last_reset_date    = ny_date

    # ─── Накопление диапазонов ────────────────────────────────────────
    def _update_daily_ranges(self, state: SymbolState, candle: Candle):
        dt = candle.dt_ny
        t  = dt.time()

        # Премаркет: 4:00 – 9:30 NY
        pm_start = time(config.PREMARKET_START_H, config.PREMARKET_START_M)
        pm_end   = time(config.PREMARKET_END_H,   config.PREMARKET_END_M)
        if pm_start <= t < pm_end:
            if state.premarket_high is None or candle.high > state.premarket_high:
                state.premarket_high = candle.high
            if state.premarket_low is None or candle.low < state.premarket_low:
                state.premarket_low = candle.low

        # First-5: 9:30 – 9:35 NY (после закрытия 9:34 свечи диапазон готов)
        f5_start = time(config.FIRST5_START_H, config.FIRST5_START_M)
        f5_end   = time(config.FIRST5_END_H,   config.FIRST5_END_M)
        if f5_start <= t < f5_end:
            if state.high_first5 is None or candle.high > state.high_first5:
                state.high_first5 = candle.high
            if state.low_first5 is None or candle.low < state.low_first5:
                state.low_first5 = candle.low

        # Фиксируем готовность диапазонов после окончания окон
        sess_start = time(config.SESSION_START_H, config.SESSION_START_M)
        if t >= sess_start:
            if state.premarket_high is not None and not state.premarket_ready:
                state.premarket_ready = True
                log.info(f"[{state.symbol}] Premarket: H={state.premarket_high} L={state.premarket_low}")
            if state.high_first5 is not None and not state.first5_ready:
                state.first5_ready = True
                log.info(f"[{state.symbol}] First5:    H={state.high_first5} L={state.low_first5}")

    # ─── ATR ─────────────────────────────────────────────────────────
    def atr(self, symbol: str, period: int = config.ATR_PERIOD) -> float:
        candles = list(self.states[symbol].candles)
        if len(candles) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(candles)):
            c, p = candles[i], candles[i - 1]
            trs.append(max(c.high - c.low,
                           abs(c.high - p.close),
                           abs(c.low  - p.close)))
        # Простое среднее последних `period` значений
        return sum(trs[-period:]) / period

    # ─── Вспомогательные геттеры ─────────────────────────────────────
    def state(self, symbol: str) -> SymbolState:
        return self.states[symbol]

    def last_candles(self, symbol: str, n: int = 5) -> list[Candle]:
        return list(self.states[symbol].candles)[-n:]
