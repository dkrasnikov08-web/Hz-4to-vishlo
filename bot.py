"""
bot.py — Live-торговля на Bybit Testnet: WebSocket → Data → Strategies → Orders.
Использует MomentumBreakout + VolatilityExpansion с ADX/EMA200 фильтрами.
"""

from __future__ import annotations

import os
import time
import traceback
import threading
from typing import Optional

import requests

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
from notifier import Notifier
from runtime_config import runtime

log = get_logger("bot")

RECONNECT_DELAY = 5  # секунд перед переподключением


def _build_strategies() -> list:
    _active = runtime.get_list("active_strategies", config.ACTIVE_STRATEGIES)
    _min_adx = runtime.get_float("min_adx", config.MIN_ADX)
    _trend = runtime.get_bool("use_trend_filter", config.USE_TREND_FILTER)
    strategies = []
    if "momentum" in _active:
        strategies.append(
            MomentumBreakout(
                lookback=config.MOMENTUM_LOOKBACK,
                breakout_atr_mult=config.MOMENTUM_BREAKOUT_ATR,
                vol_mult=config.MOMENTUM_VOL_MULT,
                min_body_pct=config.MOMENTUM_MIN_BODY_PCT,
                stop_atr_mult=config.MOMENTUM_STOP_ATR,
                max_range_pct=config.MOMENTUM_MAX_RANGE_PCT,
                min_adx=_min_adx,
                use_trend_filter=_trend,
            )
        )
    if "volatility" in _active:
        strategies.append(
            VolatilityExpansion(
                bw_lookback=config.VOLATILITY_BW_LOOKBACK,
                bw_percentile=config.VOLATILITY_BW_PERCENTILE,
                vol_mult=config.VOLATILITY_VOL_MULT,
                stop_atr_mult=config.VOLATILITY_STOP_ATR,
                min_adx=_min_adx,
                use_trend_filter=_trend,
            )
        )
    return strategies


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

        # ── Telegram нотификатор ─────────────────────────────────────
        self.notifier = Notifier()

        # ── Мониторинг закрытых позиций ──────────────────────────────
        self._tracked_positions: dict[str, dict] = {}  # symbol -> open_pos info

        # ── Защита от просадки ──────────────────────────────────────
        # peak_balance: максимальный баланс, наблюдавшийся ботом (для PORTFOLIO_DRAWDOWN_LIMIT)
        # day_start_balance / day_date: для DAILY_LOSS_LIMIT_PCT
        self.peak_balance: Optional[float] = None
        self.day_start_balance: Optional[float] = None
        self.day_date = None
        self.trading_halted = False  # True = бот видит сигналы, но не открывает позиции

    def start(self):
        _symbols = runtime.get_list("symbols", config.SYMBOLS)
        log.info(f"Бот запускается | testnet={config.TESTNET} | символы={_symbols}")
        self.notifier.start()

        if config.STARTUP_VALIDATION:
            self._startup_validation()

        self._running = True
        threading.Thread(target=self._monitor_positions, daemon=True).start()
        threading.Thread(target=self._telegram_cmd_poller, daemon=True).start()

        self.notifier.notify_info(
            f"🤖 <b>Бот запущен</b>\n"
            f"Режим: {'🧪 TESTNET' if config.TESTNET else '🔥 MAINNET'}\n"
            f"Символы: {len(_symbols)}\n"
            f"Стратегии: {', '.join(runtime.get_list('active_strategies', config.ACTIVE_STRATEGIES))}"
        )

        while self._running:
            try:
                self._connect_ws()
                while self._running:
                    time.sleep(config.SLEEP_SECONDS)
            except WebSocketError as e:
                log.error(f"WebSocket ошибка: {e}. Переподключение через {RECONNECT_DELAY}с...")
                self.notifier.notify_error(f"WebSocket ошибка: {e}. Переподключение...")
            except KeyboardInterrupt:
                log.info("KeyboardInterrupt — останавливаем бота")
                self.stop()
                break
            except Exception as e:
                log.error(f"Критическая ошибка: {e}\n{traceback.format_exc()}")
                log.info(f"Перезапуск через {RECONNECT_DELAY}с...")
                self.notifier.notify_error(f"Критическая ошибка: {e}. Перезапуск...")
            finally:
                self._disconnect_ws()

            if self._running:
                time.sleep(RECONNECT_DELAY)
                self._reset_state()

    def _reconnect_ws(self):
        """Переподключает WebSocket с текущим списком символов."""
        self._disconnect_ws()
        self._reset_state()
        self._connect_ws()

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
        portfolio_dd_limit = runtime.get_float("portfolio_drawdown_limit", config.PORTFOLIO_DRAWDOWN_LIMIT)
        if self.peak_balance and self.peak_balance > 0:
            portfolio_dd_pct = (self.peak_balance - balance) / self.peak_balance * 100.0
            if portfolio_dd_pct >= portfolio_dd_limit:
                if not self.trading_halted:
                    log.error(
                        f"СТОП ТОРГОВЛИ: просадка портфеля {portfolio_dd_pct:.2f}% "
                        f">= лимита {portfolio_dd_limit}% "
                        f"(пик={self.peak_balance:.2f}, баланс={balance:.2f}). "
                        f"Новые позиции открываться не будут до ручного вмешательства."
                    )
                    self.notifier.notify_error(
                        f"🚨 <b>СТОП ТОРГОВЛИ</b>\n"
                        f"Просадка портфеля {portfolio_dd_pct:.2f}% ≥ {portfolio_dd_limit}%\n"
                        f"Пик: {self.peak_balance:.2f} USDT\n"
                        f"Текущий: {balance:.2f} USDT"
                    )
                self.trading_halted = True
                return False

        # ── Дневной лимит убытков ──
        daily_loss_limit = runtime.get_float("daily_loss_limit_pct", config.DAILY_LOSS_LIMIT_PCT)
        if self.day_start_balance and self.day_start_balance > 0:
            daily_loss_pct = (self.day_start_balance - balance) / self.day_start_balance * 100.0
            if daily_loss_pct >= daily_loss_limit:
                log.warning(
                    f"Дневной лимит убытков достигнут: {daily_loss_pct:.2f}% "
                    f">= {daily_loss_limit}% (старт дня={self.day_start_balance:.2f}, "
                    f"баланс={balance:.2f}). Новые позиции на сегодня не открываются."
                )
                return False

        if self.trading_halted:
            return False

        return True

    def _startup_validation(self):
        """Проверяет API-ключи, баланс, настройки перед стартом."""
        import sys

        # ── Проверка ключей (не пустые и не плейсхолдеры) ──
        key = config.API_KEY or ""
        secret = config.API_SECRET or ""
        if not key or "your_key" in key.lower() or "your_secret" in key.lower():
            msg = "API_KEY не задан! Укажите BYBIT_API_KEY в .env"
            log.critical(msg)
            self.notifier.notify_error(msg)
            sys.exit(1)
        if not secret or "your_key" in secret.lower() or "your_secret" in secret.lower():
            msg = "API_SECRET не задан! Укажите BYBIT_API_SECRET в .env"
            log.critical(msg)
            self.notifier.notify_error(msg)
            sys.exit(1)

        # ── Проверка Mainnet ⚠️ ──
        if not config.TESTNET:
            log.warning("═" * 50)
            log.warning("⚠️  РЕЖИМ MAINNET! Бот будет торговать реальными деньгами!")
            log.warning("═" * 50)
            self.notifier.notify_warning("🔴 <b>MAINNET РЕЖИМ!</b> Бот торгует реальными деньгами!")

        # ── Проверка Telegram ──
        _ph_patterns = ("your_tg", "123456", "your_chat", "bot_token")
        if any(p in (config.TG_TOKEN or "").lower() for p in _ph_patterns) or \
           any(p in (config.TG_CHAT_ID or "").lower() for p in _ph_patterns):
            log.warning("Telegram TG_TOKEN/TG_CHAT_ID содержат плейсхолдеры. Уведомления не будут отправлены.")

        # ── Проверка баланса ──
        try:
            balance = self.om.get_balance()
            log.info(f"Баланс: {balance:.2f} USDT")
            if balance <= 0:
                msg = f"Нулевой баланс ({balance:.2f} USDT). Проверьте кошелёк."
                log.warning(msg)
                self.notifier.notify_warning(msg)
        except Exception as e:
            log.warning(f"Не удалось получить баланс при старте: {e}")

        # ── Проверка символов ──
        rest = None
        _symbols = runtime.get_list("symbols", config.SYMBOLS)
        for symbol in _symbols:
            try:
                if rest is None:
                    from pybit.unified_trading import HTTP
                    rest = HTTP(testnet=config.TESTNET, api_key=config.API_KEY, api_secret=config.API_SECRET)
                rest.get_instruments_info(category="linear", symbol=symbol)
            except Exception as e:
                log.warning(f"[{symbol}] Не найден на бирже: {e}")

    def _monitor_positions(self):
        """Фоновый поток: каждые N секунд проверяет открытые позиции и
        отправляет уведомление при закрытии (TP/SL)."""
        while getattr(self, '_running', False):
            try:
                time.sleep(config.POSITION_MONITOR_INTERVAL)
                if not self._running:
                    break
                current_positions = self.om.get_open_positions()
                current_symbols = {p["symbol"] for p in current_positions}

                # Проверяем, какие из отслеживаемых позиций закрылись
                for symbol, prev in list(self._tracked_positions.items()):
                    if symbol not in current_symbols:
                        # Позиция закрылась — узнаём результат
                        outcome, pnl, exit_price = self._get_closed_position_info(prev)
                        self.notifier.notify_trade_close(
                            symbol=symbol,
                            strategy=prev.get("strategy", "?"),
                            direction=prev["side"],
                            entry=prev["entry_price"],
                            exit_price=exit_price,
                            pnl_usdt=pnl,
                            outcome=outcome,
                            balance=self.om.get_balance(),
                        )
                        del self._tracked_positions[symbol]

                # Обновляем отслеживаемые (добавляем новые)
                for pos in current_positions:
                    if pos["symbol"] not in self._tracked_positions:
                        self._tracked_positions[pos["symbol"]] = pos

            except Exception as e:
                log.error(f"Ошибка в мониторинге позиций: {e}")

    def _get_closed_position_info(self, prev: dict) -> tuple[str, float, float]:
        """Пытается получить результат закрытой позиции через историю сделок.
        Возвращает (outcome, pnl_usdt, exit_price)."""
        try:
            resp = self.om.session.get_executions(
                category="linear",
                symbol=prev["symbol"],
                limit=10,
            )
            for exec_ in resp["result"]["list"]:
                side = exec_.get("side", "")
                exec_type = exec_.get("execType", "")
                # Ищем сделку закрытия (TakeProfit или StopLoss)
                if exec_type in ("TakeProfit", "StopLoss"):
                    pnl = float(exec_.get("closedPnl", 0))
                    price = float(exec_.get("execPrice", prev["entry_price"]))
                    outcome = "TP" if pnl > 0 else "SL"
                    return outcome, pnl, price
            # Если не нашли — возвращаем по PnL
            return ("SL" if prev.get("unrealised_pnl", 0) < 0 else "TP",
                    prev.get("unrealised_pnl", 0), prev["entry_price"])
        except Exception as e:
            log.warning(f"Не удалось получить информацию о закрытии: {e}")
            pnl = prev.get("unrealised_pnl", 0)
            return ("SL" if pnl < 0 else "TP", pnl, prev["entry_price"])

    # ─── Telegram команды ─────────────────────────────────────────────

    def _telegram_cmd_poller(self):
        """Фоновый поток: poll Telegram для команд управления ботом."""
        if not config.TG_TOKEN or not config.TG_CHAT_ID:
            return
        last_update_id = 0
        while self._running:
            try:
                time.sleep(5)
                url = f"https://api.telegram.org/bot{config.TG_TOKEN}/getUpdates"
                resp = requests.post(url, json={
                    "offset": last_update_id + 1,
                    "timeout": 10,
                }, timeout=15)
                if resp.status_code != 200:
                    continue
                for update in resp.json().get("result", []):
                    last_update_id = update["update_id"]
                    msg = update.get("message", {})
                    text = (msg.get("text") or "").strip()
                    chat_id = msg.get("chat", {}).get("id")
                    if chat_id and str(chat_id) == str(config.TG_CHAT_ID):
                        self._handle_telegram_command(text)
            except Exception as e:
                log.warning(f"Telegram poll error: {e}")

    def _handle_telegram_command(self, text: str):
        text = text.strip()
        if not text.startswith("/"):
            return
        parts = text.split()
        cmd = parts[0].lower()

        if cmd == "/status":
            self._cmd_status()
        elif cmd == "/config" and len(parts) >= 2 and parts[1] == "diff":
            self._cmd_config_diff()
        elif cmd == "/config" and len(parts) >= 2 and parts[1] == "reset":
            runtime.reset_all()
            self.notifier.notify_info("Все переопределения сброшены")
        elif cmd == "/config" and len(parts) >= 4 and parts[1] == "set":
            key, val = parts[2], " ".join(parts[3:])
            ok, msg = runtime.set(key, val)
            if ok:
                self.notifier.notify_info(f"✅ {msg}")
                self._apply_runtime_change(key)
            else:
                self.notifier.notify_error(f"❌ {msg}")
        elif cmd == "/config" and len(parts) >= 3 and parts[1] == "del":
            ok, msg = runtime.delete(parts[2])
            self.notifier.notify_info(f"{'✅' if ok else '❌'} {msg}")
            if ok:
                self._apply_runtime_change(parts[2])
        elif cmd == "/config":
            self._cmd_config_all()
        elif cmd == "/halt":
            self.trading_halted = True
            self.notifier.notify_warning("⏸ Торговля приостановлена вручную (команда /halt)")
        elif cmd == "/resume":
            self.trading_halted = False
            self.notifier.notify_info("▶ Торговля возобновлена (/resume)")
        else:
            self.notifier.notify_info(
                "Доступные команды:\n"
                "/status — состояние бота\n"
                "/config — все настройки\n"
                "/config diff — только изменённые\n"
                "/config set <key> <value> — изменить\n"
                "/config del <key> — сбросить\n"
                "/config reset — сбросить всё\n"
                "/halt — стоп торговли\n"
                "/resume — возобновить"
            )

    def _cmd_status(self):
        try:
            balance = self.om.get_balance()
            positions = self.om.get_open_positions()
            pos_lines = "\n".join(
                f"  {p['symbol']} {p['side']} {p['size']} " +
                f"@{p['entry_price']:.4f} PnL={p['unrealised_pnl']:+.2f}"
                for p in positions
            ) or "  —"
            key = os.getenv("BYBIT_API_KEY", "?")
            masked = key[:6] + "..." + key[-4:] if len(key) > 10 else "?"
            self.notifier.notify_info(
                f"📊 <b>СТАТУС БОТА</b>\n"
                f"Режим: {'🧪 TESTNET' if config.TESTNET else '🔥 MAINNET'}\n"
                f"Баланс: {balance:.2f} USDT\n"
                f"API: {masked}\n"
                f"Открыто позиций: {len(positions)}\n"
                f"{pos_lines}\n"
                f"Торговля: {'⏸ приостановлена' if self.trading_halted else '✅ активна'}"
            )
        except Exception as e:
            self.notifier.notify_error(f"/status ошибка: {e}")

    def _cmd_config_all(self):
        all_cfg = runtime.get_all()
        lines = []
        for key, info in all_cfg.items():
            marker = " ⚡" if info["overridden"] else ""
            lines.append(f"  {key}: {info['value']}{marker}")
        self.notifier.notify_info(f"⚙️ <b>ТЕКУЩИЕ НАСТРОЙКИ</b>\n" + "\n".join(lines))

    def _cmd_config_diff(self):
        diff = runtime.get_diff()
        if not diff:
            self.notifier.notify_info("Нет активных переопределений")
            return
        lines = [f"  {d['param']}: {d['base']} → {d['current']}" for d in diff]
        self.notifier.notify_info(f"⚙️ <b>ИЗМЕНЁННЫЕ НАСТРОЙКИ</b>\n" + "\n".join(lines))

    def _apply_runtime_change(self, key: str):
        """Применяет изменение настроек без перезапуска."""
        if key in ("active_strategies", "min_adx", "use_trend_filter"):
            self.strategies = _build_strategies()
            log.info(f"Стратегии перестроены (изменён {key})")
        elif key == "symbols":
            self._reconnect_ws()
            log.info("WebSocket переподключён (изменён список symbols)")

    def stop(self):
        log.info("Получен сигнал остановки")
        self._running = False
        self._disconnect_ws()
        self.notifier.notify_info("🛑 <b>Бот остановлен</b>")
        self.notifier.stop()
        log.info("Бот остановлен")

    def _connect_ws(self):
        self._ws = WebSocket(
            testnet=config.TESTNET,
            channel_type="linear",
        )
        symbols = runtime.get_list("symbols", config.SYMBOLS)
        for symbol in symbols:
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
            _symbols = runtime.get_list("symbols", config.SYMBOLS)

            # ── Дневной лимит ──
            if state.trades_today >= runtime.get_int("max_trades_per_symbol_day", config.MAX_TRADES_PER_SYMBOL_DAY):
                return
            total_today = sum(self.dm.state(s).trades_today for s in _symbols if s in self.dm._states)
            if total_today >= runtime.get_int("max_trades_total_day", config.MAX_TRADES_TOTAL_DAY):
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
            if ind.atr / closed.close < runtime.get_float("min_atr_filter", config.MIN_ATR_FILTER):
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
                        self.notifier.notify_trade_open(
                            symbol=signal.symbol,
                            strategy=signal.strategy_name,
                            direction=signal.direction,
                            entry=signal.entry_price,
                            stop=signal.stop_price,
                            tp=signal.tp_price,
                            qty=qty,
                            balance=balance,
                            risk_pct=runtime.get_float("risk_pct", config.RISK_PCT),
                        )
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
        _symbols = runtime.get_list("symbols", config.SYMBOLS)
        for symbol in _symbols:
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
