from __future__ import annotations

import csv
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

from data_manager import Candle

API_COINGECKO = "https://api.coingecko.com/api/v3"
CACHE_DIR = "cache"

EXCLUDE_COINS = {
    "BTC", "ETH", "SOL",
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDD", "FRAX",
    "LUSD", "ALUSD", "MIM", "USD1", "USDE", "RLUSD", "USDS", "GHO",
    "XAUT", "PAXG",  # gold tokens
    "STETH", "WETH", "WBTC", "RETH", "CBETH", "WSTETH",  # derivatives
    "LEO", "STX",  # low-volume / niche
}


def _cache_path(symbol: str, start_date: str, end_date: str, interval: str) -> str:
    return os.path.join(CACHE_DIR, f"{symbol}_{start_date}_{end_date}_{interval}.csv")


def get_top50_coins(
    top_n: int = 50,
    exclude: Optional[set] = None,
) -> list[dict]:
    if exclude is None:
        exclude = EXCLUDE_COINS
    url = f"{API_COINGECKO}/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": min(top_n + 20, 250),
        "page": 1,
        "sparkline": "false",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    coins = resp.json()

    filtered = []
    for c in coins:
        sym = c["symbol"].upper()
        if sym in exclude:
            continue
        filtered.append({
            "id": c["id"],
            "symbol": sym,
            "name": c["name"],
            "market_cap": c.get("market_cap", 0),
            "current_price": c.get("current_price", 0),
            "total_volume": c.get("total_volume", 0),
        })
        if len(filtered) >= top_n:
            break
    return filtered


def get_bybit_linear_symbols() -> set[str]:
    from pybit.unified_trading import HTTP
    session = HTTP(testnet=False)
    symbols: set[str] = set()
    cursor = None
    while True:
        params = {"category": "linear", "status": "Trading", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        resp = session.get_instruments_info(**params)
        data = resp["result"]
        for item in data["list"]:
            if item["contractType"] == "LinearPerpetual":
                symbols.add(item["symbol"])
        cursor = data.get("nextPageCursor", "")
        if not cursor:
            break
    return symbols


def get_tradable_top_coins(
    top_n: int = 30,
    min_volume_usdt: float = 5_000_000,
) -> list[str]:
    top = get_top50_coins(top_n)
    bybit_syms = get_bybit_linear_symbols()

    result = []
    for c in top:
        sym = f"{c['symbol']}USDT"
        if sym in bybit_syms and c["total_volume"] >= min_volume_usdt:
            result.append(sym)
    return result


def download_klines(
    symbol: str,
    start_dt: datetime,
    end_dt: datetime,
    interval: str = "15",
    testnet: bool = False,
) -> list[dict]:
    from pybit.unified_trading import HTTP
    session = HTTP(testnet=testnet)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    interval_min = int(interval)
    chunk_size = interval_min * 60 * 1000 * 1000

    all_rows: list[list] = []
    cursor_end = end_ms

    while cursor_end > start_ms:
        cursor_start = max(start_ms, cursor_end - chunk_size)
        try:
            resp = session.get_kline(
                category="linear",
                symbol=symbol,
                interval=interval,
                start=cursor_start,
                end=cursor_end,
                limit=1000,
            )
            rows = resp["result"]["list"]
            if not rows:
                break
            all_rows.extend(rows)
            oldest_ts = int(rows[-1][0])
            cursor_end = oldest_ts - 1
            time.sleep(0.15)
        except Exception as e:
            print(f"  [!] {symbol}: {e}")
            time.sleep(1)
            continue

    all_rows.sort(key=lambda r: int(r[0]))
    result = []
    for row in all_rows:
        ts = int(row[0])
        if ts < start_ms or ts > end_ms:
            continue
        result.append({
            "start": ts,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })
    return result


def _save_cache(symbol: str, start_str: str, end_str: str, interval: str, data: list[dict]):
    path = _cache_path(symbol, start_str, end_str, interval)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["start", "open", "high", "low", "close", "volume"])
        w.writeheader()
        w.writerows(data)


def _load_cache(symbol: str, start_str: str, end_str: str, interval: str) -> Optional[list[dict]]:
    path = _cache_path(symbol, start_str, end_str, interval)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        data = []
        for row in reader:
            data.append({
                "start": int(row["start"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            })
    return data if data else None


def load_symbol_data(
    symbol: str,
    start_dt: datetime,
    end_dt: datetime,
    interval: str = "15",
    testnet: bool = False,
    use_cache: bool = True,
) -> list[Candle]:
    start_str = start_dt.strftime("%Y%m%d")
    end_str = end_dt.strftime("%Y%m%d")

    if use_cache:
        cached = _load_cache(symbol, start_str, end_str, interval)
        if cached is not None:
            return [Candle(ts_ms=r["start"], open=r["open"], high=r["high"],
                           low=r["low"], close=r["close"], volume=r["volume"])
                    for r in cached]

    raw = download_klines(symbol, start_dt, end_dt, interval, testnet)
    if use_cache and raw:
        _save_cache(symbol, start_str, end_str, interval, raw)

    return [Candle(ts_ms=r["start"], open=r["open"], high=r["high"],
                   low=r["low"], close=r["close"], volume=r["volume"])
            for r in raw]


def load_all_symbols(
    symbols: list[str],
    start_dt: datetime,
    end_dt: datetime,
    interval: str = "15",
    testnet: bool = False,
    use_cache: bool = True,
) -> dict[str, list[Candle]]:
    result: dict[str, list[Candle]] = {}
    total = len(symbols)
    for i, sym in enumerate(symbols):
        print(f"  [{i+1}/{total}] {sym}...", end=" ", flush=True)
        try:
            candles = load_symbol_data(sym, start_dt, end_dt, interval, testnet, use_cache)
            result[sym] = candles
            print(f"{len(candles)} свечей")
        except Exception as e:
            print(f"ОШИБКА: {e}")
    return result
