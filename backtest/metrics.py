from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from backtest.engine import TradeResult


@dataclass
class BacktestReport:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    eod_exits: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    max_drawdown_pct: float = 0.0
    total_return_pct: float = 0.0
    final_balance: float = 0.0
    sharpe: float = 0.0
    by_strategy: dict = field(default_factory=dict)
    by_symbol: dict = field(default_factory=dict)
    equity_curve: list[float] = field(default_factory=list)
    max_consecutive_losses: int = 0
    avg_holding_ms: float = 0.0


def compute_report(
    trades: list[TradeResult],
    initial_balance: float,
    final_balance: float,
) -> BacktestReport:
    if not trades:
        return BacktestReport(final_balance=final_balance)

    report = BacktestReport()
    report.total_trades = len(trades)
    report.final_balance = final_balance
    report.total_return_pct = round(
        (final_balance - initial_balance) / initial_balance * 100, 2
    )

    wins = [t for t in trades if t.outcome == "TP"]
    losses = [t for t in trades if t.outcome == "SL"]
    eod = [t for t in trades if t.outcome == "EOD"]
    report.wins = len(wins)
    report.losses = len(losses)
    report.eod_exits = len(eod)
    report.win_rate = round(len(wins) / len(trades) * 100, 2) if trades else 0.0

    gross_profit = sum(t.r_multiple for t in trades if t.r_multiple > 0)
    gross_loss = abs(sum(t.r_multiple for t in trades if t.r_multiple < 0))
    report.profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")

    total_r = sum(t.r_multiple for t in trades)
    report.avg_r = round(total_r / len(trades), 4)

    # Drawdown
    peak = initial_balance
    max_dd = 0.0
    balance = initial_balance
    equity = [initial_balance]
    for t in trades:
        balance = t.balance_after
        equity.append(balance)
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100
        max_dd = max(max_dd, dd)
    report.max_drawdown_pct = round(max_dd, 2)
    report.equity_curve = equity

    # Sharpe (по R-кратным)
    r_values = [t.r_multiple for t in trades]
    if len(r_values) > 1:
        mean_r = sum(r_values) / len(r_values)
        var_r = sum((x - mean_r) ** 2 for x in r_values) / len(r_values)
        std_r = var_r ** 0.5
        report.sharpe = round(mean_r / std_r * (252 ** 0.5), 2) if std_r > 0 else 0.0

    # Max consecutive losses
    streak = 0
    max_streak = 0
    for t in trades:
        if t.outcome == "SL":
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    report.max_consecutive_losses = max_streak

    # Avg holding time
    if trades:
        diffs = [t.exit_ms - t.entry_ms for t in trades if t.exit_ms > t.entry_ms]
        report.avg_holding_ms = sum(diffs) / len(diffs) if diffs else 0.0

    # By strategy
    by_strat: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0})
    for t in trades:
        by_strat[t.strategy]["trades"] += 1
        if t.outcome == "TP":
            by_strat[t.strategy]["wins"] += 1
    for k, v in by_strat.items():
        wr = round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0
        by_strat[k]["wr"] = wr
    report.by_strategy = dict(by_strat)

    # By symbol
    by_sym: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0})
    for t in trades:
        by_sym[t.symbol]["trades"] += 1
        if t.outcome == "TP":
            by_sym[t.symbol]["wins"] += 1
    for k, v in by_sym.items():
        wr = round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0
        by_sym[k]["wr"] = wr
    report.by_symbol = dict(by_sym)

    return report


def print_report(report: BacktestReport, initial_balance: float):
    sep = "=" * 55
    print(f"\n{'='*55}")
    print(f"  ОТЧЕТ БЭКТЕСТА")
    print(f"{'='*55}")
    print(f"  Всего сделок:     {report.total_trades}")
    print(f"  Побед (TP):       {report.wins}")
    print(f"  Убытков (SL):     {report.losses}")
    print(f"  EOD:              {report.eod_exits}")
    print(sep)
    print(f"  Win-rate:         {report.win_rate:.1f}%")
    print(f"  Profit Factor:    {report.profit_factor}")
    print(f"  Avg R:            {report.avg_r:+.4f}R")
    print(f"  Sharpe (год):     {report.sharpe}")
    print(f"  Max Consec Loss:  {report.max_consecutive_losses}")
    print(sep)
    print(f"  Начальный депозит: ${initial_balance:.2f}")
    print(f"  Итоговый баланс:   ${report.final_balance:.2f}")
    print(f"  Доходность:        {report.total_return_pct:+.2f}%")
    print(f"  Max Drawdown:      -{report.max_drawdown_pct}%")
    print(sep)
    if report.avg_holding_ms > 0:
        hours = report.avg_holding_ms / 3_600_000
        print(f"  Средн. удержание:  {hours:.1f} ч")
        print(sep)

    if report.by_strategy:
        print("  По стратегиям:")
        for sname, d in sorted(report.by_strategy.items(), key=lambda x: -x[1]["trades"]):
            print(f"    {sname:<14} {d['trades']:>4} сд.  {d['wins']:>4} поб.  WR {d['wr']:.1f}%")
        print(sep)

    if report.by_symbol:
        print("  По символам (топ-10):")
        for sym, d in sorted(report.by_symbol.items(), key=lambda x: -x[1]["trades"])[:10]:
            print(f"    {sym:<10} {d['trades']:>4} сд.  {d['wins']:>4} поб.  WR {d['wr']:.1f}%")
        print(sep)

    print(f"{'='*55}\n")
