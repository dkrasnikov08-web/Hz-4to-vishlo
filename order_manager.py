"""
order_manager.py — Обёртка над Bybit V5 REST API.
Открытие позиций с мгновенным TP/SL, установка плеча и маржин-режима.
"""

from __future__ import annotations

from typing import Optional
from pybit.unified_trading import HTTP

import config
from logger_setup import get_logger
from strategies.base import Signal

log = get_logger("orders")


class OrderManager:
    def __init__(self):
        self.session = HTTP(
            testnet=config.TESTNET,
            api_key=config.API_KEY,
            api_secret=config.API_SECRET,
            recv_window=config.RECV_WINDOW,
        )
        self._configured_symbols: set[str] = set()

    # ─── Первичная настройка символа ────────────────────────────────
    def _ensure_symbol_configured(self, symbol: str):
        if symbol in self._configured_symbols:
            return
        try:
            # Устанавливаем плечо
            self.session.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(config.LEVERAGE),
                sellLeverage=str(config.LEVERAGE),
            )
            # Устанавливаем маржин-режим (ISOLATED / CROSS)
            self.session.switch_margin_mode(
                category="linear",
                symbol=symbol,
                tradeMode=1 if config.MARGIN_MODE == "ISOLATED" else 0,
                buyLeverage=str(config.LEVERAGE),
                sellLeverage=str(config.LEVERAGE),
            )
            self._configured_symbols.add(symbol)
            log.info(f"[{symbol}] Настроено: leverage={config.LEVERAGE}, mode={config.MARGIN_MODE}")
        except Exception as e:
            log.warning(f"[{symbol}] Не удалось настроить параметры: {e}")

    # ─── Получение баланса ────────────────────────────────────────────
    def get_balance(self) -> float:
        try:
            resp = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            coins = resp["result"]["list"][0]["coin"]
            for c in coins:
                if c["coin"] == "USDT":
                    return float(c["availableToWithdraw"])
        except Exception as e:
            log.error(f"Ошибка получения баланса: {e}")
        return 0.0

    # ─── Получение шагов символа ─────────────────────────────────────
    def get_symbol_info(self, symbol: str) -> dict:
        """Возвращает min_qty, qty_step, price_step для символа."""
        try:
            resp = self.session.get_instruments_info(category="linear", symbol=symbol)
            info = resp["result"]["list"][0]["lotSizeFilter"]
            price_filter = resp["result"]["list"][0]["priceFilter"]
            return {
                "min_qty":    float(info["minOrderQty"]),
                "qty_step":   float(info["qtyStep"]),
                "price_step": float(price_filter["tickSize"]),
            }
        except Exception as e:
            log.error(f"[{symbol}] Ошибка get_symbol_info: {e}")
            return {"min_qty": 0.001, "qty_step": 0.001, "price_step": 0.1}

    # ─── Округление цены ─────────────────────────────────────────────
    @staticmethod
    def round_price(price: float, step: float) -> str:
        import math
        decimals = max(0, -int(math.log10(step))) if step < 1 else 0
        rounded  = round(round(price / step) * step, decimals)
        return f"{rounded:.{decimals}f}"

    # ─── Открытие сделки ─────────────────────────────────────────────
    def place_order(self, signal: Signal, qty: float) -> Optional[str]:
        symbol = signal.symbol
        self._ensure_symbol_configured(symbol)

        info       = self.get_symbol_info(symbol)
        price_step = info["price_step"]

        side    = "Buy" if signal.direction == "long" else "Sell"
        tp_str  = self.round_price(signal.tp_price, price_step)
        sl_str  = self.round_price(signal.stop_price, price_step)

        try:
            resp = self.session.place_order(
                category          = "linear",
                symbol            = symbol,
                side              = side,
                orderType         = "Market",
                qty               = str(qty),
                timeInForce       = "GoodTillCancel",
                takeProfit        = tp_str,
                stopLoss          = sl_str,
                tpTriggerBy       = "LastPrice",
                slTriggerBy       = "LastPrice",
                tpslMode          = "Full",
                reduceOnly        = False,
                closeOnTrigger    = False,
                positionIdx       = 0,
            )
            order_id = resp["result"]["orderId"]
            log.info(
                f"[{symbol}] Ордер создан | {side} {qty} | "
                f"TP={tp_str} SL={sl_str} | ID={order_id} | strategy={signal.strategy_name}"
            )
            return order_id
        except Exception as e:
            log.error(f"[{symbol}] Ошибка place_order: {e}")
            return None

    # ─── Проверка открытых позиций ───────────────────────────────────
    def has_open_position(self, symbol: str) -> bool:
        try:
            resp = self.session.get_positions(category="linear", symbol=symbol)
            for pos in resp["result"]["list"]:
                if float(pos.get("size", 0)) > 0:
                    return True
        except Exception as e:
            log.error(f"[{symbol}] Ошибка get_positions: {e}")
        return False

    def get_open_positions(self) -> list[dict]:
        """Возвращает список всех открытых позиций."""
        try:
            resp = self.session.get_positions(category="linear", settleCoin="USDT")
            positions = []
            for pos in resp["result"]["list"]:
                size = float(pos.get("size", 0))
                if size > 0:
                    positions.append({
                        "symbol": pos["symbol"],
                        "side": "long" if pos["side"] == "Buy" else "short",
                        "size": size,
                        "entry_price": float(pos.get("avgPrice", 0)),
                        "unrealised_pnl": float(pos.get("unrealisedPnl", 0)),
                        "mark_price": float(pos.get("markPrice", 0)),
                    })
            return positions
        except Exception as e:
            log.error(f"Ошибка get_open_positions: {e}")
            return []
