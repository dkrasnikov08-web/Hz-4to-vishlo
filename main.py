"""
main.py — Точка входа. Запустить: python main.py
Переменные окружения:
    BYBIT_API_KEY=...
    BYBIT_API_SECRET=...
    BYBIT_TESTNET=true   (или false для реального)
"""

import sys
import os

# Добавляем корневую директорию в путь (если запускаем не из папки)
sys.path.insert(0, os.path.dirname(__file__))

from logger_setup import get_logger
from bot import TradingBot

log = get_logger("main")


def main():
    log.info("=" * 55)
    log.info("  Bybit Trading Bot — Momentum / Volatility + ADX/EMA200")
    log.info("=" * 55)

    bot = TradingBot()

    # Прогрев: скачиваем исторические свечи для ATR
    log.info("Прогрев данных...")
    bot.warmup()
    log.info("Прогрев завершён. Запускаем WebSocket...")

    # Основной цикл
    bot.start()


if __name__ == "__main__":
    main()
