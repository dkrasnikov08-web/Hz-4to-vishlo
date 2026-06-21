# Bybit Trading Bot — First5 / Premarket / Gap

Бот реализует три сетапа торговли по уровням Нью-Йоркской сессии на Bybit Linear Perpetuals (USDT).

---

## Структура

```
bybit_bot/
├── main.py          ← Точка входа
├── config.py        ← Все параметры (символы, риск, плечо, тайминги)
├── bot.py           ← Оркестратор: WebSocket → Сигналы → Ордера
├── data_manager.py  ← OHLCV-буфер, ATR, диапазоны First5/Premarket
├── signals.py       ← Три функции сигналов
├── risk_manager.py  ← Расчёт qty под 1R
├── order_manager.py ← Обёртка Bybit V5 API
├── logger_setup.py  ← Логирование в файл + консоль
└── requirements.txt
```

---

## Установка

```bash
cd bybit_bot
pip install -r requirements.txt
```

---

## Настройка

### 1. API-ключи (переменные окружения)

```bash
export BYBIT_API_KEY="ВАШ_КЛЮЧ"
export BYBIT_API_SECRET="ВАШ_СЕКРЕТ"
export BYBIT_TESTNET="true"   # false — для реального аккаунта
```

На Windows:
```powershell
$env:BYBIT_API_KEY="ВАШ_КЛЮЧ"
$env:BYBIT_API_SECRET="ВАШ_СЕКРЕТ"
$env:BYBIT_TESTNET="true"
```

Права ключа: **Contracts — Read/Write** (TP/SL — включены).

### 2. Параметры в config.py

| Параметр | Описание | По умолчанию |
|---|---|---|
| `SYMBOLS` | Список инструментов | `["BTCUSDT", "ETHUSDT", "SOLUSDT"]` |
| `RISK_PCT` | Риск на сделку (% депозита) | `1.0` |
| `LEVERAGE` | Плечо | `10` |
| `MARGIN_MODE` | ISOLATED / CROSS | `ISOLATED` |
| `MAX_TRADES_PER_SYMBOL_DAY` | Макс. сделок в день на символ | `2` |
| `MAX_TRADES_TOTAL_DAY` | Суммарный лимит сделок в день | `5` |
| `STRONG_BODY_PCT` | Мин. тело «сильной» свечи (% диапазона) | `60` |
| `ATR_PERIOD` | Период ATR | `14` |
| `MIN_ATR_FILTER` | Мин. ATR/цена (фильтр волатильности) | `0.0003` |
| `GAP_MIN_ATR_MULT` | Мин. уход от уровня для активации Gap-сетапа (× ATR) | `1.5` |

---

## Запуск

```bash
python main.py
```

Бот:
1. Скачивает последние 300 M1-свечей по каждому символу (warmup → ATR сразу готов).
2. Подключается к WebSocket и слушает закрытие каждой M1-свечи.
3. Строит диапазоны Premarket (4:00–9:30 NY) и First5 (9:30–9:35 NY).
4. После 9:35 NY проверяет три сетапа в порядке приоритета: First5 → Premarket → Gap.
5. При сигнале: рассчитывает qty, открывает Market-ордер с TP=2R и SL=1R через Bybit V5 API.

---

## Сетапы

### Сетап 1 — First 5-Min

- Строим High/Low первых 5 минут (9:30–9:35 NY).
- После 9:35: пробой уровня телом предыдущей свечи → ретест уровня текущей → сильный бар по направлению пробоя → вход.

### Сетап 2 — Premarket High/Low

- Строим High/Low премаркета (4:00–9:30 NY).
- Та же логика: пробой → ретест → сильный бар → вход.

### Сетап 3 — Gap Breakout

- Fallback: цена ушла от уровня > 1.5 × ATR без ретеста.
- Ищем первую контр-трендовую свечу (gap_candle) — её диапазон становится зоной входа.
- При возврате цены в эту зону + сильный бар по тренду → вход.

### Параметры позиции

```
Entry  = close сигнальной свечи
Stop   = swing low/high ± 0.05%
TP     = Entry + 2 × (Entry − Stop)   # TP=2R
```

---

## Риск-менеджмент

```
risk_usdt = balance × RISK_PCT / 100
qty       = risk_usdt / |entry − stop|
```

Плечо задаётся на бирже; бот передаёт qty в монетах, Bybit рассчитывает маржу сам.

---

## Логирование

Все сигналы и сделки пишутся в `bybit_bot.log` и дублируются в консоль.

---

## Бэктест

```bash
# Базовый запуск — последние 90 дней BTCUSDT
python backtest.py

# Кастомный период и инструмент
python backtest.py --symbol ETHUSDT --start 2024-09-01 --end 2025-01-31

# Изменить риск и начальный депозит
python backtest.py --symbol SOLUSDT --start 2024-06-01 --end 2025-01-31 --risk 1.5 --balance 5000

# Сохранить CSV с другим именем
python backtest.py --symbol BTCUSDT --start 2024-01-01 --end 2024-12-31 --out btc_2024.csv
```

**Что показывает отчёт:**

| Метрика | Описание |
|---|---|
| Win-rate | % сделок, закрытых по TP |
| Avg R | Среднее R-кратное на сделку |
| Profit factor | Gross profit / Gross loss в R |
| Max drawdown | Максимальная просадка по балансу |
| Доходность | % прироста депозита за период |
| По сетапам | WR отдельно для first5 / premarket / gap |

**Модель выхода:** внутри свечи SL приоритетнее TP (пессимистичная). Если позиция не закрылась до 16:00 NY — EOD-выход по close.

Результат сохраняется в CSV с колонками: `symbol, setup, direction, entry_dt, exit_dt, entry_price, stop_price, tp_price, exit_price, outcome, r_multiple, pnl_pct, balance_after`.

---

## Важные замечания

- Тестируй сначала на **Testnet** (`BYBIT_TESTNET=true`).
- Бот работает в **One-Way Mode** (`positionIdx=0`). Убедись, что в настройках Bybit-аккаунта выключен Hedge Mode.
- Один вход на символ (не усредняет). Если позиция открыта — новых сигналов не даёт.
- Остановить бот: `Ctrl+C`.
