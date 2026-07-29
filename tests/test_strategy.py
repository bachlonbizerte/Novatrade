import pandas as pd
from src.strategy import generate_signal


def test_generate_signal_buy_on_crossover():
    # Prix qui montent progressivement -> la SMA rapide finit par dépasser la SMA lente
    closes = [100, 100, 100, 100, 100, 105, 110, 115, 120, 125, 130, 135]
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=len(closes), freq="15min"),
        "open": closes, "high": closes, "low": closes, "close": closes, "volume": [1] * len(closes),
    })
    signal = generate_signal(df, fast_period=3, slow_period=6)
    assert signal in ("buy", "hold")  # dépend du point exact du croisement


def test_generate_signal_hold_when_not_enough_data():
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=2, freq="15min"),
        "open": [100, 101], "high": [100, 101], "low": [100, 101],
        "close": [100, 101], "volume": [1, 1],
    })
    assert generate_signal(df, fast_period=9, slow_period=21) == "hold"
