"""
Stratégie de base : croisement de moyennes mobiles (SMA crossover).

Signal:
- "buy"  quand la SMA rapide croise AU-DESSUS de la SMA lente
- "sell" quand la SMA rapide croise EN-DESSOUS de la SMA lente
- "hold" sinon

Facile à remplacer par une autre stratégie: garde la même interface
(une fonction generate_signal(df) qui retourne "buy" / "sell" / "hold").
"""

import pandas as pd


def add_indicators(df: pd.DataFrame, fast_period: int, slow_period: int) -> pd.DataFrame:
    df = df.copy()
    df["sma_fast"] = df["close"].rolling(window=fast_period).mean()
    df["sma_slow"] = df["close"].rolling(window=slow_period).mean()
    return df


def generate_signal(df: pd.DataFrame, fast_period: int = 9, slow_period: int = 21) -> str:
    """Retourne le signal ('buy', 'sell', 'hold') basé sur les 2 dernières bougies."""
    df = add_indicators(df, fast_period, slow_period)
    df = df.dropna(subset=["sma_fast", "sma_slow"])

    if len(df) < 2:
        return "hold"

    prev, last = df.iloc[-2], df.iloc[-1]

    crossed_up = prev["sma_fast"] <= prev["sma_slow"] and last["sma_fast"] > last["sma_slow"]
    crossed_down = prev["sma_fast"] >= prev["sma_slow"] and last["sma_fast"] < last["sma_slow"]

    if crossed_up:
        return "buy"
    if crossed_down:
        return "sell"
    return "hold"
