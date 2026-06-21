from __future__ import annotations

import itertools
from typing import Optional

from backtest.engine import BacktestEngine, TradeResult
from backtest.metrics import compute_report
from strategies.base import BaseStrategy
from strategies.momentum_breakout import MomentumBreakout
from strategies.volatility_expansion import VolatilityExpansion


PARAM_GRIDS: dict[str, dict[str, list]] = {
    "momentum": {
        "breakout_atr_mult": [0.4, 0.6],
        "stop_atr_mult": [1.5, 2.0, 2.5],
        "vol_mult": [1.3, 1.5],
        "max_range_pct": [0.04],
        "min_adx": [0, 20],
    },
    "volatility": {
        "bw_percentile": [15, 20, 25],
        "stop_atr_mult": [1.5, 2.0, 2.5],
        "vol_mult": [1.3, 1.5],
        "min_adx": [0, 20],
    },
}


def create_strategy(name: str, params: dict) -> Optional[BaseStrategy]:
    if name == "momentum":
        base = {"lookback": 20, "min_body_pct": 55}
        base.update(params)
        return MomentumBreakout(**base)
    elif name == "volatility":
        base = {"bw_lookback": 50}
        base.update(params)
        return VolatilityExpansion(**base)
    return None


def grid_search(
    engine: BacktestEngine,
    strategy_name: str,
    param_grid: Optional[dict[str, list]] = None,
    top_n: int = 10,
) -> list[dict]:
    if param_grid is None:
        param_grid = PARAM_GRIDS.get(strategy_name, {})

    if not param_grid:
        print(f"Нет param_grid для стратегии {strategy_name}")
        return []

    keys = list(param_grid.keys())
    values = list(param_grid.values())
    results: list[dict] = []

    total = 1
    for v in values:
        total *= len(v)
    print(f"\nGrid search для {strategy_name}: {total} комбинаций\n")

    for combo in itertools.product(*values):
        params = dict(zip(keys, combo))
        strategy = create_strategy(strategy_name, params)
        if strategy is None:
            continue

        engine.strategies = [strategy]
        trades = engine.run(engine.data, quiet=True)
        report = compute_report(trades, engine.initial_balance, engine.balance)

        entry = {"strategy": strategy_name, **params}
        entry.update({
            "total_trades": report.total_trades,
            "win_rate": report.win_rate,
            "profit_factor": report.profit_factor,
            "avg_r": report.avg_r,
            "max_dd": report.max_drawdown_pct,
            "total_return": report.total_return_pct,
            "sharpe": report.sharpe,
            "final_balance": report.final_balance,
        })
        results.append(entry)

        print(f"  {params}  ->  WR={report.win_rate:.1f}% PF={report.profit_factor} "
              f"R={report.avg_r:+.4f} DD=-{report.max_drawdown_pct}% "
              f"Ret={report.total_return_pct:+.2f}%")

        engine.balance = engine.initial_balance
        engine.trades = []

    results.sort(key=lambda r: r.get("profit_factor", 0), reverse=True)
    print(f"\n{'='*55}")
    print(f"  ТОП-{top_n} результатов для {strategy_name}")
    print(f"{'='*55}")
    for r in results[:top_n]:
        pf = r.get("profit_factor", 0)
        wr = r.get("win_rate", 0)
        avg_r = r.get("avg_r", 0)
        dd = r.get("max_dd", 0)
        ret = r.get("total_return", 0)
        params_str = ", ".join(f"{k}={v}" for k, v in r.items()
                               if k in keys)
        print(f"  PF={pf:.2f} WR={wr:.1f}% R={avg_r:+.4f} "
              f"DD=-{dd}% Ret={ret:+.2f}%  |  {params_str}")

    return results
