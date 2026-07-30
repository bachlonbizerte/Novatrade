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
    def __init__(self, exchange_name: str, api_key: str = "", api_secret: str = "",
                 dry_run: bool = True, fallback_exchanges: list = None):
        """
        Initialise l'exchange principal. Si celui-ci est inaccessible (ex: Binance
        bloque les IP de datacenter comme celles de GitHub Actions — erreur 451
        "restricted location"), bascule automatiquement sur le premier exchange
        de secours fonctionnel dans `fallback_exchanges`.
        """
        self.dry_run = dry_run
        candidates = [exchange_name] + (fallback_exchanges or [])
        last_error = None

        for name in candidates:
            try:
                exchange_class = getattr(ccxt, name)
                config = {"enableRateLimit": True}
                if not dry_run:
                    # Clés API transmises uniquement en mode réel (voir note plus bas)
                    config["apiKey"] = api_key
                    config["secret"] = api_secret

                exchange = exchange_class(config)
                exchange.fetch_ticker("BTC/USDT")  # test d'accès rapide avant de valider ce choix

                self.exchange = exchange
                self.exchange_name = name
                if name != exchange_name:
                    logger.warning(f"⚠️ {exchange_name} inaccessible depuis cet environnement "
                                    f"(probable restriction géographique) — bascule automatique sur {name}")
                logger.info(f"Exchange initialisé: {name} (dry_run={dry_run})")
                return
            except Exception as e:
                last_error = e
                logger.warning(f"{name} indisponible: {e}")

        raise RuntimeError(f"Aucun exchange accessible parmi {candidates}. Dernière erreur: {last_error}")

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
