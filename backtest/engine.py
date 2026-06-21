from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from data_manager import Candle
from strategies.base import BaseStrategy, Signal


# ─── Расчёт qty ────────────────────────────────────────────────────────

def calc_qty(
    balance_usdt: float,
    entry_price: float,
    stop_price: float,
    risk_pct: float,
    min_qty: float = 0.001,
    qty_step: float = 0.001,
) -> float:
    risk_usdt = balance_usdt * (risk_pct / 100.0)
    dist = abs(entry_price - stop_price)
    if dist == 0:
        return 0.0
    qty = risk_usdt / dist
    qty = math.floor(qty / qty_step) * qty_step
    qty = round(qty, 8)
    return qty if qty >= min_qty else 0.0


@dataclass
class Position:
    symbol: str
    direction: str
    entry_price: float
    stop_price: float
    tp_price: float
    qty: float
    entry_ms: int
    strategy: str
    risk_pct: float
    balance_before: float = 0.0


@dataclass
class TradeResult:
    symbol: str
    strategy: str
    direction: str
    entry_ms: int
    exit_ms: int
    entry_price: float
    stop_price: float
    tp_price: float
    exit_price: float
    qty: float
    outcome: str          # "TP" | "SL" | "EOD"
    r_multiple: float
    pnl_usdt: float
    balance_before: float
    balance_after: float


# ─── Индикаторы ────────────────────────────────────────────────────────

@dataclass
class IndicatorState:
    candles: deque = field(default_factory=lambda: deque(maxlen=300))
    atr: float = 0.0
    rsi: float = 50.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0
    bb_width_min: float = float("inf")
    bb_width_max: float = 0.0
    volume_sma: float = 0.0
    ema_9: float = 0.0
    ema_20: float = 0.0
    ema_50: float = 0.0
    ema_200: float = 0.0
    trend_ema: float = 0.0
    adx: float = 25.0
    plus_di: float = 25.0
    minus_di: float = 25.0
    vwap: float = 0.0
    ready: bool = False


def get_sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return sum(values) / len(values) if values else 0.0
    return sum(values[-period:]) / period


def get_ema(prev_ema: float, price: float, period: int) -> float:
    k = 2 / (period + 1)
    return price * k + prev_ema * (1 - k)


def get_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def get_adx(state: IndicatorState, candle: Candle, candles_list: list) -> tuple[float, float, float]:
    if len(candles_list) < 15:
        return 25.0, 25.0, 25.0
    prev = candles_list[-2]
    up_move = candle.high - prev.high
    down_move = prev.low - candle.low
    if up_move > down_move and up_move > 0:
        plus_dm = up_move
        minus_dm = 0
    elif down_move > up_move and down_move > 0:
        plus_dm = 0
        minus_dm = down_move
    else:
        plus_dm = 0
        minus_dm = 0
    tr = max(candle.high - candle.low, abs(candle.high - prev.close), abs(candle.low - prev.close))
    if tr == 0:
        return state.adx, state.plus_di, state.minus_di
    if state.plus_di == 25.0 and state.minus_di == 25.0:
        pdi = (plus_dm / tr) * 100
        mdi = (minus_dm / tr) * 100
        adx_val = abs(pdi - mdi) / (pdi + mdi) * 100 if (pdi + mdi) > 0 else 0
        return adx_val, pdi, mdi
    smoothed_tr = state.atr * 14
    pdi = ((state.plus_di * (13 / 14) * (state.atr * 14) + plus_dm) / smoothed_tr) * 100 if smoothed_tr > 0 else 0
    mdi = ((state.minus_di * (13 / 14) * (state.atr * 14) + minus_dm) / smoothed_tr) * 100 if smoothed_tr > 0 else 0
    dx = abs(pdi - mdi) / (pdi + mdi) * 100 if (pdi + mdi) > 0 else 0
    adx_val = (state.adx * 13 + dx) / 14
    return adx_val, pdi, mdi


def update_indicators(state: IndicatorState, candle: Candle, closes: list[float]):
    c = candle
    state.candles.append(c)

    if len(state.candles) < 2:
        return

    closes.append(c.close)

    prices = [x.close for x in state.candles]

    # ATR (14)
    if len(state.candles) >= 2:
        prev = state.candles[-2]
        tr = max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close))
        if state.atr == 0:
            state.atr = tr
        else:
            state.atr = (state.atr * 13 + tr) / 14

    # EMA 9
    if state.ema_9 == 0:
        state.ema_9 = c.close
    else:
        state.ema_9 = get_ema(state.ema_9, c.close, 9)

    # EMA 20
    if state.ema_20 == 0:
        state.ema_20 = c.close
    else:
        state.ema_20 = get_ema(state.ema_20, c.close, 20)

    # EMA 50
    if state.ema_50 == 0:
        state.ema_50 = c.close
    else:
        state.ema_50 = get_ema(state.ema_50, c.close, 50)

    # EMA 200
    if state.ema_200 == 0:
        state.ema_200 = c.close
    else:
        state.ema_200 = get_ema(state.ema_200, c.close, 200)

    state.trend_ema = state.ema_20 - state.ema_50

    # Volume SMA (20)
    vols = [x.volume for x in state.candles]
    state.volume_sma = get_sma(vols, 20)

    # Bollinger Bands (20, 2)
    if len(prices) >= 20:
        recent = prices[-20:]
        mean = sum(recent) / 20
        variance = sum((x - mean) ** 2 for x in recent) / 20
        std = math.sqrt(variance)
        state.bb_middle = mean
        state.bb_upper = mean + 2 * std
        state.bb_lower = mean - 2 * std
        state.bb_width = (state.bb_upper - state.bb_lower) / mean if mean > 0 else 0

        if state.bb_width > 0:
            if state.bb_width < state.bb_width_min:
                state.bb_width_min = state.bb_width
            if state.bb_width > state.bb_width_max:
                state.bb_width_max = state.bb_width

    # VWAP (rolling over 24h = 96 bars for 15m)
    if len(state.candles) >= 1:
        lookback_vwap = min(96, len(state.candles))
        recent_vwap = list(state.candles)[-lookback_vwap:]
        pv_sum = sum(x.close * x.volume for x in recent_vwap)
        v_sum = sum(x.volume for x in recent_vwap)
        state.vwap = pv_sum / v_sum if v_sum > 0 else c.close

    # ADX (14)
    candles_list = list(state.candles)
    if len(candles_list) >= 14:
        state.adx, state.plus_di, state.minus_di = get_adx(state, c, candles_list)

    if len(state.candles) >= 50:
        state.ready = True


def build_analysis(state: IndicatorState, candle: Candle, closes: list[float]) -> dict:
    return {
        "atr": state.atr,
        "rsi": get_rsi(closes, 14),
        "bb_upper": state.bb_upper,
        "bb_middle": state.bb_middle,
        "bb_lower": state.bb_lower,
        "bb_width": state.bb_width,
        "bb_width_min": state.bb_width_min,
        "bb_width_max": state.bb_width_max,
        "volume_sma": state.volume_sma,
        "ema_9": state.ema_9,
        "ema_20": state.ema_20,
        "ema_50": state.ema_50,
        "ema_200": state.ema_200,
        "trend_ema": state.trend_ema,
        "adx": state.adx,
        "plus_di": state.plus_di,
        "minus_di": state.minus_di,
        "vwap": state.vwap,
        "ema_fast": state.ema_9,
        "ema_slow": state.ema_20,
    }


# ─── Симуляция выхода ──────────────────────────────────────────────────

def simulate_exit(
    position: Position,
    future_candles: list[Candle],
    max_hold_bars: int = 672,
) -> Optional[TradeResult]:
    for bar_idx, candle in enumerate(future_candles):
        sl_hit = (candle.low <= position.stop_price) if position.direction == "long" else (candle.high >= position.stop_price)
        tp_hit = (candle.high >= position.tp_price) if position.direction == "long" else (candle.low <= position.tp_price)

        if bar_idx >= max_hold_bars:
            exit_price = candle.close
            outcome = "EOD"
        elif sl_hit:
            exit_price = position.stop_price
            outcome = "SL"
        elif tp_hit:
            exit_price = position.tp_price
            outcome = "TP"
        else:
            continue

        if position.direction == "long":
            r_mult = (exit_price - position.entry_price) / abs(position.entry_price - position.stop_price)
        else:
            r_mult = (position.entry_price - exit_price) / abs(position.entry_price - position.stop_price)

        pnl = r_mult * (position.risk_pct / 100) * position.balance_before
        bal_after = position.balance_before + pnl

        return TradeResult(
            symbol=position.symbol,
            strategy=position.strategy,
            direction=position.direction,
            entry_ms=position.entry_ms,
            exit_ms=candle.ts_ms,
            entry_price=position.entry_price,
            stop_price=position.stop_price,
            tp_price=position.tp_price,
            exit_price=exit_price,
            qty=position.qty,
            outcome=outcome,
            r_multiple=round(r_mult, 4),
            pnl_usdt=round(pnl, 2),
            balance_before=round(position.balance_before, 2),
            balance_after=round(bal_after, 2),
        )

    return None


# ─── Merge chronological ────────────────────────────────────────────────

@dataclass
class CandleEvent:
    symbol: str
    candle: Candle
    idx: int


def merge_candles_chronologically(data: dict[str, list[Candle]]) -> list[CandleEvent]:
    events = []
    for sym, candles in data.items():
        for i, c in enumerate(candles):
            events.append(CandleEvent(sym, c, i))
    events.sort(key=lambda e: e.candle.ts_ms)
    return events


# ─── Backtest Engine ────────────────────────────────────────────────────

class BacktestEngine:
    def __init__(
        self,
        symbols: list[str],
        start_dt: datetime,
        end_dt: datetime,
        initial_balance: float = 100.0,
        risk_pct: float = 2.0,
        max_positions: int = 1,
        strategies: Optional[list[BaseStrategy]] = None,
    ):
        self.symbols = symbols
        self.start_dt = start_dt
        self.end_dt = end_dt
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.risk_pct = risk_pct
        self.max_positions = max_positions
        self.strategies = strategies or []
        self.trades: list[TradeResult] = []
        self.peak_balance = initial_balance
        self.data: dict[str, list[Candle]] = {}

    def set_data(self, data: dict[str, list[Candle]]):
        self.data = data

    def run(self, data: Optional[dict[str, list[Candle]]] = None, quiet: bool = False) -> list[TradeResult]:
        if data is not None:
            self.data = data

        if not self.data:
            raise ValueError("Нет данных. Загрузи через data_loader или set_data().")

        events = merge_candles_chronologically(self.data)
        indicators: dict[str, IndicatorState] = {}
        closes: dict[str, list[float]] = {}
        positions: dict[str, Position] = {}
        symbol_candle_idx: dict[str, int] = {}
        daily_trades: dict[date, int] = {}
        daily_pnl: dict[date, float] = {}

        for sym in self.symbols:
            indicators[sym] = IndicatorState()
            closes[sym] = []

        if not quiet:
            print(f"\nЗапуск бэктеста: {len(self.symbols)} символов, "
                  f"{len(events)} свечей, {len(self.strategies)} стратегий")
            print(f"Баланс: ${self.initial_balance:.2f}, риск: {self.risk_pct}%, "
                  f"макс позиций: {self.max_positions}\n")

        processed = 0
        last_pct = -1

        for event in events:
            sym = event.symbol
            candle = event.candle
            symbol_candle_idx[sym] = event.idx

            processed += 1
            if not quiet:
                pct = processed * 100 // len(events) if events else 0
                if pct > last_pct and pct % 5 == 0:
                    print(f"  Прогресс: {pct}% | Баланс: ${self.balance:.2f} | "
                          f"Сделок: {len(self.trades)}")
                    last_pct = pct

            # Обновление индикаторов
            update_indicators(indicators[sym], candle, closes[sym])

            # Проверка открытых позиций
            if sym in positions:
                pos = positions[sym]
                idx_start = symbol_candle_idx.get(sym, 0)
                future = self.data[sym][idx_start:]
                if future:
                    result = simulate_exit(pos, future)
                    if result is not None:
                        self.trades.append(result)
                        self.balance = result.balance_after
                        self.peak_balance = max(self.peak_balance, self.balance)
                        del positions[sym]
                        dt = datetime.fromtimestamp(result.exit_ms / 1000, tz=timezone.utc)
                        d = dt.date()
                        daily_pnl[d] = daily_pnl.get(d, 0) + result.pnl_usdt
                        if not quiet:
                            _log_trade(result)

            # Если позиций больше нет по этому символу
            if sym in positions:
                continue

            # Лимит позиций
            if len(positions) >= self.max_positions:
                continue

            # Баланс
            if self.balance <= 0:
                break

            # Дневной лимит убытка
            candle_dt = datetime.fromtimestamp(candle.ts_ms / 1000, tz=timezone.utc)
            day = candle_dt.date()
            daily_pnl_day = daily_pnl.get(day, 0)
            if daily_pnl_day < 0 and abs(daily_pnl_day) >= self.balance * 0.06:
                continue

            # Индикаторы готовы
            ind = indicators[sym]
            if not ind.ready or ind.atr <= 0:
                continue

            # ATR filter: not too low volatility
            if ind.atr / candle.close < 0.0005:
                continue

            analysis = build_analysis(ind, candle, closes[sym])

            for strategy in self.strategies:
                signal = strategy.check(sym, list(ind.candles), analysis, candle.ts_ms)
                if signal is None:
                    continue

                info = _get_symbol_defaults(sym)
                qty = calc_qty(
                    balance_usdt=self.balance,
                    entry_price=signal.entry_price,
                    stop_price=signal.stop_price,
                    risk_pct=self.risk_pct,
                    min_qty=info["min_qty"],
                    qty_step=info["qty_step"],
                )
                if qty <= 0:
                    continue

                pos = Position(
                    symbol=sym,
                    direction=signal.direction,
                    entry_price=signal.entry_price,
                    stop_price=signal.stop_price,
                    tp_price=signal.tp_price,
                    qty=qty,
                    entry_ms=candle.ts_ms,
                    strategy=signal.strategy_name,
                    risk_pct=self.risk_pct,
                    balance_before=round(self.balance, 2),
                )
                positions[sym] = pos

                risk_usdt = self.balance * (self.risk_pct / 100)
                if not quiet:
                    print(f"  >>> СИГНАЛ {signal.strategy_name} {signal.direction.upper()} "
                          f"{sym} @ {signal.entry_price:.4f} "
                          f"SL={signal.stop_price:.4f} TP={signal.tp_price:.4f} "
                          f"qty={qty} риск=${risk_usdt:.2f}")
                break  # одна стратегия за свечу

        if not quiet:
            print(f"\n{'='*50}")
            print(f"  БЭКТЕСТ ЗАВЕРШЁН")
            print(f"  Итого сделок: {len(self.trades)}")
            print(f"  Фин. баланс: ${self.balance:.2f} "
                  f"(+{((self.balance - self.initial_balance) / self.initial_balance * 100):+.2f}%)")
            print(f"{'='*50}")

        return self.trades


# ─── Helpers ───────────────────────────────────────────────────────────

_SYMBOL_DEFAULTS: dict[str, dict] = {}


def _get_symbol_defaults(symbol: str) -> dict:
    if symbol not in _SYMBOL_DEFAULTS:
        _SYMBOL_DEFAULTS[symbol] = {"min_qty": 0.001, "qty_step": 0.001}
    return _SYMBOL_DEFAULTS[symbol]


def preload_symbol_info(symbols: list[str]):
    try:
        from pybit.unified_trading import HTTP
        session = HTTP(testnet=False)
        for sym in symbols:
            try:
                resp = session.get_instruments_info(category="linear", symbol=sym)
                info = resp["result"]["list"][0]["lotSizeFilter"]
                _SYMBOL_DEFAULTS[sym] = {
                    "min_qty": float(info["minOrderQty"]),
                    "qty_step": float(info["qtyStep"]),
                }
            except Exception:
                _SYMBOL_DEFAULTS[sym] = {"min_qty": 0.001, "qty_step": 0.001}
    except Exception:
        for sym in symbols:
            _SYMBOL_DEFAULTS[sym] = {"min_qty": 0.001, "qty_step": 0.001}


# ─── Logging ───────────────────────────────────────────────────────────

def _log_trade(t: TradeResult):
    dt = datetime.fromtimestamp(t.exit_ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
    emoji = "TP" if t.outcome == "TP" else "SL" if t.outcome == "SL" else "EOD"
    pnl_s = f"{t.pnl_usdt:+.2f}" if t.pnl_usdt > 0 else f"{t.pnl_usdt:.2f}"
    print(f"  [{dt}] {t.symbol:>8} {t.strategy:>10} {t.direction:>5} "
          f"R={t.r_multiple:+.2f} PnL={pnl_s} "
          f"Bal=${t.balance_after:.2f} ({emoji})")
