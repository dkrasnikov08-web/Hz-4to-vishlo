"""
backtest.py — Мультисимвольный бэктест 3 стратегий на топ-N альткоинов.

Запуск:
    python backtest.py --top 30 --start 2026-03-01 --end 2026-06-01
    python backtest.py --symbols ADAUSDT,DOTUSDT,LINKUSDT --start 2026-01-01
    python backtest.py --mode optimize --strategy momentum --top 20
    python backtest.py --mode list  # список доступных символов
"""

from __future__ import annotations

import argparse
import sys
import os
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))

from backtest.data_loader import (
    get_tradable_top_coins,
    load_all_symbols,
)
from backtest.engine import BacktestEngine, preload_symbol_info
from backtest.metrics import compute_report, print_report
from backtest.optimize import grid_search, PARAM_GRIDS
from strategies.momentum_breakout import MomentumBreakout
from strategies.volatility_expansion import VolatilityExpansion


STRATEGY_MAP = {
    "momentum": MomentumBreakout,
    "volatility": VolatilityExpansion,
}


def parse_args():
    today = date.today()
    p = argparse.ArgumentParser(description="Multi-symbol backtest for top-N altcoins")
    p.add_argument("--mode", default="backtest",
                   choices=["backtest", "optimize", "list"],
                   help="Режим: backtest, optimize, или list символов")
    p.add_argument("--top", type=int, default=30,
                   help="Количество топ-монет (по market cap)")
    p.add_argument("--symbols", default=None,
                   help="Список символов через запятую (ADAUSDT,DOTUSDT)")
    p.add_argument("--start", default=str(today - timedelta(days=90)),
                   help="YYYY-MM-DD")
    p.add_argument("--end", default=str(today - timedelta(days=1)),
                   help="YYYY-MM-DD")
    p.add_argument("--balance", type=float, default=100.0,
                   help="Начальный депозит USDT")
    p.add_argument("--risk", type=float, default=2.0,
                   help="Риск %% на сделку")
    p.add_argument("--max-pos", type=int, default=1,
                   help="Макс. одновременных позиций")
    p.add_argument("--strategies", default="momentum,volatility",
                   help="Стратегии через запятую")
    p.add_argument("--interval", default="15",
                   help="Таймфрейм в минутах (15)")
    p.add_argument("--no-cache", action="store_true",
                   help="Не использовать кэш CSV")
    p.add_argument("--strategy", default="momentum",
                   help="Стратегия для optimize")
    return p.parse_args()


def main():
    args = parse_args()

    # ─── Режим list ────────────────────────────────────────────────────
    if args.mode == "list":
        print("Получаю топ-50 монет с CoinGecko...")
        tradable = get_tradable_top_coins(top_n=50)
        print(f"\nДоступно для торговли на Bybit Linear: {len(tradable)}")
        for i, sym in enumerate(tradable, 1):
            print(f"  {i:>2}. {sym}")
        return

    # ─── Дата ──────────────────────────────────────────────────────────
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    end_dt = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=timezone.utc)

    # ─── Символы ──────────────────────────────────────────────────────
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        print(f"Получаю топ-{args.top} монет с CoinGecko...")
        symbols = get_tradable_top_coins(top_n=args.top)
        if not symbols:
            print("Нет доступных символов!")
            return
        print(f"Найдено {len(symbols)} символов\n")

    # ─── Стратегии ────────────────────────────────────────────────────
    strat_names = [s.strip().lower() for s in args.strategies.split(",")]
    strategies = []
    for name in strat_names:
        cls = STRATEGY_MAP.get(name)
        if cls:
            if name == "momentum":
                strategies.append(cls(lookback=20, breakout_atr_mult=0.6, vol_mult=1.3, stop_atr_mult=2.0, max_range_pct=0.04, min_body_pct=55))
            elif name == "volatility":
                strategies.append(cls(bw_lookback=50, bw_percentile=15, vol_mult=1.3, stop_atr_mult=1.5))
            else:
                strategies.append(cls())
            print(f"  + Стратегия: {name}")
    if not strategies:
        print("Нет валидных стратегий!")
        return

    # ─── Загрузка данных ──────────────────────────────────────────────
    print(f"\nЗагрузка {args.interval}m данных: {start_date} - {end_date}")
    warmup_start = start_dt - timedelta(days=3)
    data = load_all_symbols(
        symbols=symbols,
        start_dt=warmup_start,
        end_dt=end_dt,
        interval=args.interval,
        use_cache=not args.no_cache,
    )

    # Фильтр: удаляем символы без данных
    symbols_with_data = [s for s in symbols if len(data.get(s, [])) > 100]
    skipped = len(symbols) - len(symbols_with_data)
    if skipped:
        print(f"  Пропущено {skipped} символов (мало данных)")

    # Предзагрузка информации о символах (min_qty, qty_step)
    print("\nПредзагрузка информации о символах...")
    preload_symbol_info(symbols_with_data)

    engine = BacktestEngine(
        symbols=symbols_with_data,
        start_dt=start_dt,
        end_dt=end_dt,
        initial_balance=args.balance,
        risk_pct=args.risk,
        max_positions=args.max_pos,
        strategies=strategies,
    )
    engine.set_data(data)

    # ─── Режим backtest ───────────────────────────────────────────────
    if args.mode == "backtest":
        trades = engine.run()
        report = compute_report(trades, args.balance, engine.balance)
        print_report(report, args.balance)

        # Сохранение CSV
        csv_name = f"backtest_top{args.top}_{args.start}_{args.end}.csv"
        if trades:
            import csv as csv_module
            from dataclasses import asdict
            with open(csv_name, "w", newline="", encoding="utf-8") as f:
                w = csv_module.DictWriter(f, fieldnames=asdict(trades[0]).keys())
                w.writeheader()
                w.writerows(asdict(t) for t in trades)
            print(f"CSV сохранён: {csv_name}")

    # ─── Режим optimize ───────────────────────────────────────────────
    elif args.mode == "optimize":
        results = grid_search(engine, args.strategy, top_n=15)

        csv_name = f"optimize_{args.strategy}_{args.start}_{args.end}.csv"
        if results:
            import csv as csv_module
            with open(csv_name, "w", newline="", encoding="utf-8") as f:
                w = csv_module.DictWriter(f, fieldnames=results[0].keys())
                w.writeheader()
                w.writerows(results)
            print(f"CSV сохранён: {csv_name}")


if __name__ == "__main__":
    main()
