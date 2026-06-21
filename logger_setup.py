"""
logger_setup.py — Настройка логгера: файл + консоль.
"""

import logging
import sys
from config import LOG_FILE, LOG_LEVEL


def get_logger(name: str = "bot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    # ── Защита секретов в логах ──────────────────────────────────────
    # pybit на уровне DEBUG может логировать полные HTTP-запросы, включая
    # заголовки с подписью (signature) и параметры запроса. Жёстко
    # ограничиваем уровень pybit-логгера до WARNING независимо от
    # LOG_LEVEL в config.py, чтобы случайное включение DEBUG не привело
    # к утечке ключей/подписей в bybit_bot.log.
    logging.getLogger("pybit").setLevel(logging.WARNING)
    logging.getLogger("pybit.unified_trading").setLevel(logging.WARNING)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Файловый хендлер
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Консольный хендлер
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger
