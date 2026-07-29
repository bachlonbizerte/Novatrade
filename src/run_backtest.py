"""
Usage:
    python -m src.run_backtest --csv data/BTCUSDT_15m.csv

Le CSV doit avoir les colonnes: timestamp, open, high, low, close, volume
(c'est exactement le format retourné par ExchangeClient.fetch_ohlcv,
que tu peux sauvegarder avec df.to_csv()).
"""

import argparse
import yaml
import pandas as pd

from src.backtester import run_backtest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Chemin vers le CSV de données historiques")
    parser.add_argument("--config", default="config/config.example.yaml")
    args = parser.parse_args()

    with open(args.config, "r") as f:
        config = yaml.safe_load(f)

    df = pd.read_csv(args.csv, parse_dates=["timestamp"])

    result = run_backtest(
        df,
        fast_period=config["strategy"]["fast_period"],
        slow_period=config["strategy"]["slow_period"],
        initial_balance=config["backtest"]["initial_balance"],
    )

    print("\n=== Résultat du backtest ===")
    print(f"Solde initial : {result['initial_balance']}")
    print(f"Solde final   : {result['final_balance']}")
    print(f"Performance   : {result['total_return_pct']}%")
    print(f"Nb trades     : {result['num_trades']}")
    print(f"Taux de gain  : {result['win_rate_pct']}%")


if __name__ == "__main__":
    main()
