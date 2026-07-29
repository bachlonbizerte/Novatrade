"""
Point d'entrée du bot. Conçu pour être exécuté à chaque tick par un
cron (GitHub Actions) : une exécution = un "check" du marché +
décision + action éventuelle, puis le script se termine.
"""

import os
import logging
import yaml
from dotenv import load_dotenv

from src.exchange_client import ExchangeClient
from src.strategy import generate_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def load_config(path: str = "config/config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def main():
    load_dotenv()

    config = load_config()
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

    client = ExchangeClient(
        exchange_name=config["exchange"]["name"],
        api_key=os.getenv("EXCHANGE_API_KEY", ""),
        api_secret=os.getenv("EXCHANGE_API_SECRET", ""),
        dry_run=dry_run,
    )

    symbol = config["exchange"]["symbol"]
    timeframe = config["exchange"]["timeframe"]
    fast = config["strategy"]["fast_period"]
    slow = config["strategy"]["slow_period"]
    trade_amount = config["risk"]["trade_amount_usdt"]

    logger.info(f"Vérification du marché: {symbol} ({timeframe})")
    df = client.fetch_ohlcv(symbol, timeframe, limit=max(slow * 3, 100))

    signal = generate_signal(df, fast_period=fast, slow_period=slow)
    logger.info(f"Signal généré: {signal}")

    if signal == "buy":
        client.create_market_order(symbol, "buy", trade_amount)
    elif signal == "sell":
        client.create_market_order(symbol, "sell", trade_amount)
    else:
        logger.info("Aucune action à prendre.")


if __name__ == "__main__":
    main()
