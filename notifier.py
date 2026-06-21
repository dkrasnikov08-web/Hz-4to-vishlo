"""
notifier.py — Отправка уведомлений в Telegram.
"""

from __future__ import annotations

import asyncio
import threading
from queue import Queue

import requests

import config
from logger_setup import get_logger

log = get_logger("notifier")


def _send_sync(text: str) -> bool:
    if not config.TG_TOKEN or not config.TG_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{config.TG_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": config.TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        if resp.status_code != 200:
            log.warning(f"Telegram API error: {resp.status_code} {resp.text[:200]}")
        return resp.status_code == 200
    except requests.RequestException as e:
        log.warning(f"Telegram send failed: {e}")
        return False


class Notifier:
    """Асинхронный отправитель уведомлений (не блокирует основной поток)."""

    def __init__(self):
        self._queue: Queue = Queue()
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self):
        if not config.TG_TOKEN or not config.TG_CHAT_ID:
            log.info("Telegram не настроен (TG_TOKEN/TG_CHAT_ID) — уведомления отключены")
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        log.info("Notifier запущен")

    def stop(self):
        self._running = False
        self._queue.put(None)

    def send(self, text: str):
        self._queue.put(text)

    def _worker(self):
        while self._running:
            item = self._queue.get()
            if item is None:
                break
            _send_sync(item)

    def notify_trade_open(self, symbol: str, strategy: str, direction: str,
                          entry: float, stop: float, tp: float, qty: float,
                          balance: float, risk_pct: float):
        rr = abs(tp - entry) / abs(entry - stop) if abs(entry - stop) > 0 else 0
        risk_usdt = balance * risk_pct / 100
        text = (
            f"\U0001f534 <b>СДЕЛКА ОТКРЫТА</b>\n"
            f"\U0001f4cc {symbol} | {strategy.upper()}\n"
            f"{'🟢' if direction == 'long' else '🔴'} {direction.upper()}\n"
            f"Вход: {entry:.4f}\n"
            f"SL:   {stop:.4f}\n"
            f"TP:   {tp:.4f}\n"
            f"RR:   1:{rr:.2f}\n"
            f"Объём: {qty:.4f}\n"
            f"Риск: {risk_usdt:.2f} USDT\n"
            f"Баланс: {balance:.2f} USDT"
        )
        self.send(text)

    def notify_trade_close(self, symbol: str, strategy: str, direction: str,
                           entry: float, exit_price: float, pnl_usdt: float,
                           outcome: str, balance: float):
        emoji = "\u2705" if outcome == "TP" else "\u274c"
        text = (
            f"{emoji} <b>СДЕЛКА ЗАКРЫТА</b>\n"
            f"\U0001f4cc {symbol} | {strategy.upper()}\n"
            f"{'🟢' if direction == 'long' else '🔴'} {direction.upper()}\n"
            f"Вход: {entry:.4f}  Выход: {exit_price:.4f}\n"
            f"Результат: {outcome}\n"
            f"{'📈' if pnl_usdt >= 0 else '📉'} PnL: {pnl_usdt:+.2f} USDT\n"
            f"Баланс: {balance:.2f} USDT"
        )
        self.send(text)

    def notify_error(self, msg: str):
        self.send(f"\u26a0\ufe0f <b>ОШИБКА</b>\n{msg}")

    def notify_warning(self, msg: str):
        self.send(f"\u26a0\ufe0f {msg}")

    def notify_info(self, msg: str):
        self.send(f"\u2139\ufe0f {msg}")


# Глобальный экземпляр (singleton)
notifier = Notifier()
