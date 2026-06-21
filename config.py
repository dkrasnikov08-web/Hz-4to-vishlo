"""
config.py — Конфигурация Bybit-бота (крипто-агенты, топ-50 альткоинов).
Все чувствительные данные берутся из переменных окружения или .env файла.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API ──────────────────────────────────────────────────────────────
API_KEY    = os.getenv("BYBIT_API_KEY", "YOUR_KEY_HERE")
API_SECRET = os.getenv("BYBIT_API_SECRET", "YOUR_SECRET_HERE")
TESTNET    = os.getenv("BYBIT_TESTNET", "true").lower() == "true"

# ── Инструменты (отобраны бэктестом: PF 1.14-1.23 на Q1+Q2) ────────
SYMBOLS = [
    "BNBUSDT", "SKYUSDT", "XRPUSDT", "ADAUSDT", "ASTERUSDT",
    "MORPHOUSDT", "WLFIUSDT", "HYPEUSDT", "XLMUSDT", "ONDOUSDT",
    "DOTUSDT", "DOGEUSDT", "ETCUSDT", "SUIUSDT", "LINKUSDT",
    "AAVEUSDT", "WLDUSDT",
]

# ── Риск и позиция ───────────────────────────────────────────────────
RISK_PCT     = 1.0    # % от депозита на одну сделку (было 2.0% — снижено для уменьшения просадки)
LEVERAGE     = 10
MARGIN_MODE  = "ISOLATED"
MAX_POSITIONS = 1      # макс. одновременных позиций

# ── Ограничения ──────────────────────────────────────────────────────
MAX_TRADES_PER_SYMBOL_DAY = 2
MAX_TRADES_TOTAL_DAY      = 5
DAILY_LOSS_LIMIT_PCT      = 6.0    # стоп торговли на день при -%
PORTFOLIO_DRAWDOWN_LIMIT  = 20.0   # стоп всего бота при -%

# ── ATR ──────────────────────────────────────────────────────────────
ATR_PERIOD     = 14
MIN_ATR_FILTER = 0.0005

# ── Стратегии ────────────────────────────────────────────────────────
ACTIVE_STRATEGIES = ["momentum", "volatility"]

# Momentum Breakout (оптимизировано: PF 1.32)
MOMENTUM_LOOKBACK       = 20
MOMENTUM_BREAKOUT_ATR   = 0.6
MOMENTUM_VOL_MULT       = 1.3
MOMENTUM_MIN_BODY_PCT   = 55
MOMENTUM_STOP_ATR       = 2.0
MOMENTUM_MAX_RANGE_PCT  = 0.04

# Volatility Expansion (оптимизировано: PF 1.47)
VOLATILITY_BW_LOOKBACK   = 50
VOLATILITY_BW_PERCENTILE = 15
VOLATILITY_VOL_MULT      = 1.3
VOLATILITY_STOP_ATR      = 1.5

# ADX + трендовый фильтр (обязательные)
MIN_ADX              = 20.0
USE_TREND_FILTER     = True

# ── Таймфрейм ────────────────────────────────────────────────────────
CANDLE_INTERVAL    = 15       # M15
MAX_CANDLE_HISTORY = 500

# ── Логирование ──────────────────────────────────────────────────────
LOG_FILE  = "bybit_bot.log"
LOG_LEVEL = "INFO"

# ── NY-сессия (для legacy data_manager) ───────────────────────────────
NY_TZ                = "America/New_York"
SESSION_START_H      = 9
SESSION_START_M      = 30
SESSION_END_H        = 16
SESSION_END_M        = 0
PREMARKET_START_H    = 4
PREMARKET_START_M    = 0
PREMARKET_END_H      = 9
PREMARKET_END_M      = 30
FIRST5_START_H       = 9
FIRST5_START_M       = 30
FIRST5_END_H         = 9
FIRST5_END_M         = 35

# ── Telegram ─────────────────────────────────────────────────────────
TG_TOKEN   = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

# ── Безопасность ────────────────────────────────────────────────────
STARTUP_VALIDATION = True   # проверять API-ключи и баланс при старте
POSITION_MONITOR_INTERVAL = 60  # сек, проверка закрытых позиций

# ── Разное ───────────────────────────────────────────────────────────
RECV_WINDOW   = 5000
SLEEP_SECONDS = 0.1
