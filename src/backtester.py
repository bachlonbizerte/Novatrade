"""
Backtester simple: simule la stratégie sur des données historiques
et calcule la performance (PnL, nombre de trades, drawdown).
"""

import pandas as pd
from src.strategy import add_indicators


def run_backtest(df: pd.DataFrame, fast_period: int, slow_period: int,
                  initial_balance: float = 1000.0, fee_pct: float = 0.1) -> dict:
    df = add_indicators(df, fast_period, slow_period).dropna(subset=["sma_fast", "sma_slow"]).reset_index(drop=True)

    balance = initial_balance
    position = None  # None ou {"entry_price": ...}
    trades = []
    equity_curve = []

    for i in range(1, len(df)):
        prev, row = df.iloc[i - 1], df.iloc[i]
        price = row["close"]

        crossed_up = prev["sma_fast"] <= prev["sma_slow"] and row["sma_fast"] > row["sma_slow"]
        crossed_down = prev["sma_fast"] >= prev["sma_slow"] and row["sma_fast"] < row["sma_slow"]

        if crossed_up and position is None:
            position = {"entry_price": price}
        elif crossed_down and position is not None:
            entry = position["entry_price"]
            pnl_pct = (price - entry) / entry * 100 - fee_pct * 2
            balance *= (1 + pnl_pct / 100)
            trades.append({"entry": entry, "exit": price, "pnl_pct": pnl_pct})
            position = None

        equity_curve.append(balance)

    # Clôture d'une position encore ouverte à la fin de la période testée
    if position is not None:
        last_price = df.iloc[-1]["close"]
        entry = position["entry_price"]
        pnl_pct = (last_price - entry) / entry * 100 - fee_pct * 2
        balance *= (1 + pnl_pct / 100)
        trades.append({"entry": entry, "exit": last_price, "pnl_pct": pnl_pct})

    wins = [t for t in trades if t["pnl_pct"] > 0]

    return {
        "initial_balance": initial_balance,
        "final_balance": round(balance, 2),
        "total_return_pct": round((balance - initial_balance) / initial_balance * 100, 2),
        "num_trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "trades": trades,
    }
