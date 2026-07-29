"""
Wrapper autour de ccxt pour interagir avec l'exchange :
- récupération des chandeliers (OHLCV)
- passage d'ordres (avec mode DRY_RUN pour ne rien exécuter réellement)
"""

import os
import logging
import ccxt
import pandas as pd

logger = logging.getLogger(__name__)


class ExchangeClient:
    def __init__(self, exchange_name: str, api_key: str = "", api_secret: str = "", dry_run: bool = True):
        self.dry_run = dry_run
        exchange_class = getattr(ccxt, exchange_name)
        self.exchange = exchange_class({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })
        logger.info(f"Exchange initialisé: {exchange_name} (dry_run={dry_run})")

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        """Récupère les chandeliers OHLCV et les retourne sous forme de DataFrame."""
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    def fetch_balance(self) -> dict:
        if self.dry_run:
            logger.info("[DRY_RUN] fetch_balance ignoré")
            return {}
        return self.exchange.fetch_balance()

    def create_market_order(self, symbol: str, side: str, amount: float):
        """
        Passe un ordre au marché. En mode dry_run, on ne fait que logger
        l'action pour pouvoir tester la logique sans risquer de vrais fonds.
        """
        if self.dry_run:
            logger.info(f"[DRY_RUN] Ordre simulé: {side.upper()} {amount} {symbol}")
            return {"status": "simulated", "side": side, "amount": amount, "symbol": symbol}

        logger.info(f"Passage d'un ordre réel: {side.upper()} {amount} {symbol}")
        return self.exchange.create_order(symbol=symbol, type="market", side=side, amount=amount)
