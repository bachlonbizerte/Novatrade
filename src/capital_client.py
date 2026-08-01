"""
Client pour l'API Capital.com (REST). Gère l'authentification par session
(différente de Binance: pas juste une clé fixe, mais un jeton de session qui
expire après 10 min d'inactivité — donc on réauthentifie automatiquement si
besoin avant chaque appel).

Utilisé pour les instruments non-crypto (métaux, indices...) en complément
d'ExchangeClient (Binance/Bybit/Kraken) qui reste utilisé pour les cryptos.
"""

import time
import logging
import requests
import pandas as pd

logger = logging.getLogger(__name__)

DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com"
LIVE_BASE_URL = "https://api-capital.backend-capital.com"

SESSION_REFRESH_MARGIN_SECONDS = 8 * 60


RESOLUTION_MAP = {
    "1m": "MINUTE", "5m": "MINUTE_5", "15m": "MINUTE_15",
    "1h": "HOUR", "4h": "HOUR_4", "1d": "DAY",
}


class CapitalClient:
    def __init__(self, api_key: str, identifier: str, api_password: str, demo: bool = True):
        self.api_key = api_key
        self.identifier = identifier
        self.api_password = api_password
        self.base_url = DEMO_BASE_URL if demo else LIVE_BASE_URL
        self.demo = demo

        self.cst = None
        self.security_token = None
        self._last_auth_time = 0

    def _headers(self) -> dict:
        return {
            "X-CAP-API-KEY": self.api_key,
            "CST": self.cst or "",
            "X-SECURITY-TOKEN": self.security_token or "",
            "Content-Type": "application/json",
        }

    def _login(self):
        url = f"{self.base_url}/api/v1/session"
        payload = {"identifier": self.identifier, "password": self.api_password, "encryptedPassword": False}
        resp = requests.post(url, json=payload, headers={"X-CAP-API-KEY": self.api_key}, timeout=15)
        resp.raise_for_status()

        self.cst = resp.headers.get("CST")
        self.security_token = resp.headers.get("X-SECURITY-TOKEN")
        self._last_auth_time = time.time()
        logger.info(f"Session Capital.com ouverte ({'DEMO' if self.demo else 'LIVE'})")

    def _ensure_session(self):
        if not self.cst or (time.time() - self._last_auth_time) > SESSION_REFRESH_MARGIN_SECONDS:
            self._login()

    def search_markets(self, term: str) -> list:
        """Cherche des instruments par nom/mot-clé — utile pour trouver le bon 'epic'."""
        self._ensure_session()
        url = f"{self.base_url}/api/v1/markets"
        resp = requests.get(url, headers=self._headers(), params={"searchTerm": term}, timeout=15)
        resp.raise_for_status()
        return resp.json().get("markets", [])

    def get_prices(self, epic: str, resolution: str = "MINUTE_15", max_points: int = 250) -> pd.DataFrame:
        """
        Récupère l'historique de prix pour un epic, retourné dans le même format
        de DataFrame que ExchangeClient.fetch_ohlcv (compatible avec analyzers.py).
        resolution: MINUTE, MINUTE_5, MINUTE_15, HOUR, HOUR_4, DAY...
        """
        self._ensure_session()
        url = f"{self.base_url}/api/v1/prices/{epic}"
        resp = requests.get(url, headers=self._headers(),
                             params={"resolution": resolution, "max": max_points}, timeout=15)
        resp.raise_for_status()
        raw = resp.json().get("prices", [])

        rows = []
        for p in raw:
            o = (p["openPrice"]["bid"] + p["openPrice"]["ask"]) / 2
            h = (p["highPrice"]["bid"] + p["highPrice"]["ask"]) / 2
            l = (p["lowPrice"]["bid"] + p["lowPrice"]["ask"]) / 2
            c = (p["closePrice"]["bid"] + p["closePrice"]["ask"]) / 2
            v = p.get("lastTradedVolume", 0) or 0
            rows.append({"timestamp": p["snapshotTimeUTC"], "open": o, "high": h, "low": l, "close": c, "volume": v})

        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        """
        Alias compatible avec l'interface d'ExchangeClient (même signature), pour
        que analyzers.py/ai_decision.py puissent utiliser CapitalClient exactement
        comme ExchangeClient, sans code séparé pour les deux sources de données.
        """
        resolution = RESOLUTION_MAP.get(timeframe, "MINUTE_15")
        return self.get_prices(symbol, resolution=resolution, max_points=limit)

    def create_position(self, epic: str, direction: str, size: float,
                         stop_level: float = None, profit_level: float = None) -> dict:
        """Ouvre une position (BUY/SELL) sur le compte Demo, avec stop/objectif optionnels."""
        self._ensure_session()
        url = f"{self.base_url}/api/v1/positions"
        payload = {"epic": epic, "direction": direction.upper(), "size": size}
        if stop_level is not None:
            payload["stopLevel"] = stop_level
        if profit_level is not None:
            payload["profitLevel"] = profit_level

        resp = requests.post(url, headers=self._headers(), json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_positions(self) -> list:
        self._ensure_session()
        url = f"{self.base_url}/api/v1/positions"
        resp = requests.get(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json().get("positions", [])

    def close_position(self, deal_id: str) -> dict:
        self._ensure_session()
        url = f"{self.base_url}/api/v1/positions/{deal_id}"
        resp = requests.delete(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()
