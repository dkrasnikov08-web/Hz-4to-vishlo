"""
bot.py — Live-торговля на Bybit Testnet: WebSocket → Data → Strategies → Orders.
Использует MomentumBreakout + VolatilityExpansion с ADX/EMA200 фильтрами.
"""

from __future__ import annotations

import time
import traceback
import threading
from typing import Optional

from pybit.unified_trading import WebSocket, WebSocketError

import config
from data_manager import DataManager, Candle
from risk_manager import calc_qty
from order_manager import OrderManager
from logger_setup import get_logger

from strategies.momentum_breakout import MomentumBreakout
from strategies.volatility_expansion import VolatilityExpansion
from strategies.base import Signal
from backtest.engine import (
    update_indicators,
    build_analysis,
    IndicatorState,
)

log = get_logger("bot")

RECONNECT_DELAY = 5  # секунд перед переподключением


def _build_strategies() -> list:
    return [
        MomentumBreakout(
            lookback=config.MOMENTUM_LOOKBACK,
            breakout_atr_mult=config.MOMENTUM_BREAKOUT_ATR,
            vol_mult=config.MOMENTUM_VOL_MULT,
            min_body_pct=config.MOMENTUM_MIN_BODY_PCT,
            stop_atr_mult=config.MOMENTUM_STOP_ATR,
            max_range_pct=config.MOMENTUM_MAX_RANGE_PCT,
            min_adx=config.MIN_ADX,
            use_trend_filter=config.USE_TREND_FILTER,
        ),
        VolatilityExpansion(
            bw_lookback=config.VOLATILITY_BW_LOOKBACK,
            bw_percentile=config.VOLATILITY_BW_PERCENTILE,
            vol_mult=config.VOLATILITY_VOL_MULT,
            stop_atr_mult=config.VOLATILITY_STOP_ATR,
            min_adx=config.MIN_ADX,
            use_trend_filter=config.USE_TREND_FILTER,
        ),
    ]


class TradingBot:
    def __init__(self):
        self.dm = DataManager()
        self.om = OrderManager()
        self.lock = threading.Lock()
        self._ws: Optional[WebSocket] = None
        self._running = False
        self.strategies = _build_strategies()
        self.indicators: dict[str, IndicatorState] = {}
        self.closes: dict[str, list[float]] = {}

        # ── Защита от просадки ──────────────────────────────────────
        # peak_balance: максимальный баланс, наблюдавшийся ботом (для PORTFOLIO_DRAWDOWN_LIMIT)
        # day_start_balance / day_date: для DAILY_LOSS_LIMIT_PCT
        self.peak_balance: Optional[float] = None
        self.day_start_balance: Optional[float] = None
        self.day_date = None
        self.trading_halted = False  # True = бот видит сигналы, но не открывает позиции

    def start(self):
        log.info(f"Бот запускается | testnet={config.TESTNET} | символы={config.SYMBOLS}")
        self._running = True

        while self._running:
            try:
                self._connect_ws()
                while self._running:
                    time.sleep(config.SLEEP_SECONDS)
            except WebSocketError as e:
                log.error(f"WebSocket ошибка: {e}. Переподключение через {RECONNECT_DELAY}с...")
            except KeyboardInterrupt:
                log.info("KeyboardInterrupt — останавливаем бота")
                self.stop()
                break
            except Exception as e:
                log.error(f"Критическая ошибка: {e}\n{traceback.format_exc()}")
                log.info(f"Перезапуск через {RECONNECT_DELAY}с...")
            finally:
                self._disconnect_ws()

            if self._running:
                time.sleep(RECONNECT_DELAY)
                self._reset_state()

    def _disconnect_ws(self):
        try:
            if self._ws:
                self._ws.exit()
        except Exception as e:
            log.warning(f"Ошибка при отключении WebSocket: {e}")
        self._ws = None

    def _reset_state(self):
        """Сброс индикаторов и данных при переподключении."""
        self.indicators.clear()
        self.closes.clear()
        self.dm = DataManager()
        log.info("Состояние сброшено для переподключения")

    def _check_drawdown_guards(self, balance: float) -> bool:
        """
        Проверяет PORTFOLIO_DRAWDOWN_LIMIT и DAILY_LOSS_LIMIT_PCT.
        Возвращает True, если торговля разрешена, False — если лимит превышен
        (новые позиции не открываются; открытые позиции бот не закрывает
        принудительно — это уже делает TP/SL на бирже).
        """
        import datetime as _dt

        today = _dt.date.today()

        # Обновляем дневной старт-баланс при смене дня
        if self.day_date != today:
            self.day_date = today
            self.day_start_balance = balance
            if self.trading_halted:
                log.info("Новый день — сбрасываем дневной стоп-флаг (PORTFOLIO лимит сохраняется отдельно)")

        # Обновляем исторический пик баланса
        if self.peak_balance is None or balance > self.peak_balance:
            self.peak_balance = balance

        # ── Лимит просадки всего портфеля ──
        if self.peak_balance and self.peak_balance > 0:
            portfolio_dd_pct = (self.peak_balance - balance) / self.peak_balance * 100.0
            if portfolio_dd_pct >= config.PORTFOLIO_DRAWDOWN_LIMIT:
                if not self.trading_halted:
                    log.error(
                        f"СТОП ТОРГОВЛИ: просадка портфеля {portfolio_dd_pct:.2f}% "
                        f">= лимита {config.PORTFOLIO_DRAWDOWN_LIMIT}% "
                        f"(пик={self.peak_balance:.2f}, баланс={balance:.2f}). "
                        f"Новые позиции открываться не будут до ручного вмешательства."
                    )
                self.trading_halted = True
                return False

        # ── Дневной лимит убытков ──
        if self.day_start_balance and self.day_start_balance > 0:
            daily_loss_pct = (self.day_start_balance - balance) / self.day_start_balance * 100.0
            if daily_loss_pct >= config.DAILY_LOSS_LIMIT_PCT:
                log.warning(
                    f"Дневной лимит убытков достигнут: {daily_loss_pct:.2f}% "
                    f">= {config.DAILY_LOSS_LIMIT_PCT}% (старт дня={self.day_start_balance:.2f}, "
                    f"баланс={balance:.2f}). Новые позиции на сегодня не открываются."
                )
                return False

        if self.trading_halted:
            return False

        return True

    def stop(self):
        log.info("Получен сигнал остановки")
        self._running = False
        self._disconnect_ws()
        log.info("Бот остановлен")

    def _connect_ws(self):
        self._ws = WebSocket(
            testnet=config.TESTNET,
            channel_type="linear",
        )
        for symbol in config.SYMBOLS:
            self._ws.kline_stream(
                interval=config.CANDLE_INTERVAL,
                symbol=symbol,
                callback=self._make_handler(symbol),
            )
            log.info(f"WebSocket подписан: {symbol} M{config.CANDLE_INTERVAL}")

    def _make_handler(self, symbol: str):
        def handler(msg: dict):
            try:
                for raw_kline in msg.get("data", []):
                    self._on_kline(symbol, raw_kline)
            except Exception as e:
                log.error(f"[{symbol}] Ошибка в callback: {e}", exc_info=True)
        return handler

    def _on_kline(self, symbol: str, raw: dict):
        try:
            closed: Optional[Candle] = self.dm.update_candle(symbol, raw)
            if closed is None:
                return

            state = self.dm.state(symbol)

            # ── Дневной лимит ──
            if state.trades_today >= config.MAX_TRADES_PER_SYMBOL_DAY:
                return
            total_today = sum(self.dm.state(s).trades_today for s in config.SYMBOLS)
            if total_today >= config.MAX_TRADES_TOTAL_DAY:
                return

            # ── Открытая позиция ──
            with self.lock:
                try:
                    if self.om.has_open_position(symbol):
                        return
                except Exception as e:
                    log.warning(f"[{symbol}] Ошибка проверки позиции: {e}")
                    return

            # ── Индикаторы ──
            if symbol not in self.indicators:
                self.indicators[symbol] = IndicatorState()
                self.closes[symbol] = []
            update_indicators(self.indicators[symbol], closed, self.closes[symbol])

            ind = self.indicators[symbol]
            if not ind.ready or ind.atr <= 0:
                return
            if ind.atr / closed.close < config.MIN_ATR_FILTER:
                return

            analysis = build_analysis(ind, closed, self.closes[symbol])
            candles = list(self.dm.state(symbol).candles)

            # ── Стратегии ──
            signal: Optional[Signal] = None
            for strategy in self.strategies:
                try:
                    signal = strategy.check(symbol, candles, analysis, closed.ts_ms)
                    if signal:
                        log.info(f"[{symbol}] {signal.strategy_name} сигнал: {signal.direction} @ {signal.entry_price:.4f}")
                        break
                except Exception as e:
                    log.error(f"[{symbol}] Ошибка в стратегии {strategy.name}: {e}")
                    continue

            if signal is None:
                return

            # ── Исполнение ──
            try:
                balance = self.om.get_balance()
            except Exception as e:
                log.error(f"[{symbol}] Ошибка получения баланса: {e}")
                return
            if balance <= 0:
                log.warning(f"[{symbol}] Нулевой баланс ({balance})")
                return

            # ── Защита от просадки (PORTFOLIO_DRAWDOWN_LIMIT / DAILY_LOSS_LIMIT_PCT) ──
            if not self._check_drawdown_guards(balance):
                return

            try:
                info = self.om.get_symbol_info(symbol)
            except Exception as e:
                log.error(f"[{symbol}] Ошибка получения info: {e}")
                return

            qty = calc_qty(
                balance_usdt=balance,
                entry_price=signal.entry_price,
                stop_price=signal.stop_price,
                min_qty=info["min_qty"],
                qty_step=info["qty_step"],
            )
            if qty <= 0:
                log.warning(f"[{symbol}] qty=0, пропускаем")
                return

            with self.lock:
                try:
                    order_id = self.om.place_order(signal, qty)
                    if order_id:
                        state.trades_today += 1
                        _log_trade(signal, qty, balance, ind.atr)
                    else:
                        log.warning(f"[{symbol}] Ордер не создан (place_order вернул None)")
                except Exception as e:
                    log.error(f"[{symbol}] Ошибка place_order: {e}")

        except Exception as e:
            log.error(f"[{symbol}] Необработанная ошибка: {e}\n{traceback.format_exc()}")

    def warmup(self):
        from pybit.unified_trading import HTTP
        rest = HTTP(
            testnet=config.TESTNET,
            api_key=config.API_KEY,
            api_secret=config.API_SECRET,
        )
        for symbol in config.SYMBOLS:
            try:
                resp = rest.get_kline(
                    category="linear",
                    symbol=symbol,
                    interval=str(config.CANDLE_INTERVAL),
                    limit=config.MAX_CANDLE_HISTORY,
                )
                raw_list = resp["result"]["list"]
                raw_list.reverse()
                for row in raw_list:
                    fake = {
                        "start": int(row[0]),
                        "open": row[1],
                        "high": row[2],
                        "low": row[3],
                        "close": row[4],
                        "volume": row[5],
                        "confirm": True,
                    }
                    closed = self.dm.update_candle(symbol, fake)
                    if closed:
                        if symbol not in self.indicators:
                            self.indicators[symbol] = IndicatorState()
                            self.closes[symbol] = []
                        update_indicators(self.indicators[symbol], closed, self.closes[symbol])
                log.info(f"[{symbol}] Warmup: {len(raw_list)} свечей")
            except Exception as e:
                log.error(f"[{symbol}] Warmup ошибка: {e}")


def _log_trade(signal: Signal, qty: float, balance: float, atr: float):
    risk_usdt = abs(signal.entry_price - signal.stop_price) * qty
    rr = abs(signal.tp_price - signal.entry_price) / abs(signal.entry_price - signal.stop_price)
    log.info(
        f"\n{'='*55}\n"
        f"  СДЕЛКА ОТКРЫТА\n"
        f"  Символ:    {signal.symbol}\n"
        f"  Стратегия: {signal.strategy_name}\n"
        f"  Направл.:  {signal.direction.upper()}\n"
        f"  Вход:      {signal.entry_price:.4f}\n"
        f"  Stop:      {signal.stop_price:.4f}\n"
        f"  TP:        {signal.tp_price:.4f}\n"
        f"  RR:        1:{rr:.2f}\n"
        f"  Объём:     {qty}\n"
        f"  Риск:      {risk_usdt:.2f} USDT\n"
        f"  Баланс:    {balance:.2f} USDT\n"
        f"  ATR:       {atr:.4f}\n"
        f"{'='*55}"
    )
