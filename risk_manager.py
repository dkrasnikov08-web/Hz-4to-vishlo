"""
risk_manager.py — Расчёт размера позиции (qty) под фиксированный риск 1R.
"""

from __future__ import annotations

import math
from logger_setup import get_logger
import config

log = get_logger("risk")


def calc_qty(
    balance_usdt: float,
    entry_price:  float,
    stop_price:   float,
    min_qty:      float = 0.001,
    qty_step:     float = 0.001,
) -> float:
    """
    Рассчитывает размер позиции в монетах так, чтобы риск = RISK_PCT % депозита.

    qty = (balance * RISK_PCT/100) / |entry - stop|

    Плечо уже учтено через margin_mode=ISOLATED + установленное leverage на бирже:
    нам не нужно умножать qty на leverage — Bybit принимает qty в монетах и сам
    рассчитывает требуемую маржу.
    """
    risk_usdt = balance_usdt * (config.RISK_PCT / 100.0)
    dist = abs(entry_price - stop_price)
    if dist == 0:
        log.warning("calc_qty: entry == stop, возвращаю 0")
        return 0.0

    qty = risk_usdt / dist

    # Округление вниз до шага символа
    qty = math.floor(qty / qty_step) * qty_step
    qty = round(qty, 8)

    if qty < min_qty:
        log.warning(f"Рассчитанный qty={qty} < min_qty={min_qty}. Позиция не будет открыта.")
        return 0.0

    log.info(f"Риск: {risk_usdt:.2f} USDT | dist={dist:.4f} | qty={qty}")
    return qty
