"""
Récupère le signal d'analyse technique officiel de TradingView pour une paire
donnée, via la librairie `tradingview-ta` (scraping du widget public
"Technical Analysis" de TradingView — pas d'API officielle disponible).

Ce signal est ensuite combiné à notre propre score dans ai_decision.py.

Mis en cache 5 minutes: le processus VPS tourne en continu (contrairement à
GitHub Actions où chaque run repartait de zéro), donc ce cache en mémoire
survit d'un cycle de scan à l'autre. Sans ça, un scan toutes les 60s envoie
5x plus de requêtes qu'avant vers ce service non-officiel, qui nous limite
en retour (erreur 429) — le signal TradingView n'a de toute façon aucune
raison de changer d'une minute à l'autre.
"""

import time
import logging
from tradingview_ta import TA_Handler, Interval

logger = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "15m": Interval.INTERVAL_15_MINUTES,
    "1h": Interval.INTERVAL_1_HOUR,
    "4h": Interval.INTERVAL_4_HOURS,
    "1d": Interval.INTERVAL_1_DAY,
}

RECOMMENDATION_SCORE = {
    "STRONG_BUY": 100,
    "BUY": 75,
    "NEUTRAL": 50,
    "SELL": 25,
    "STRONG_SELL": 0,
}

_CACHE = {}  # (symbol, timeframe) -> (résultat, timestamp_unix)
_CACHE_TTL_SECONDS = 300  # 5 min


def _to_tradingview_symbol(symbol: str) -> str:
    """Convertit 'BTC/USDT' -> 'BTCUSDT' (format attendu par TradingView)."""
    return symbol.replace("/", "")


def get_tradingview_signal(symbol: str, timeframe: str = "15m",
                            exchange: str = "BINANCE", screener: str = "crypto") -> dict:
    """
    Retourne le résumé TradingView pour ce symbole/timeframe:
    {recommendation, score, buy_signals, sell_signals, neutral_signals}
    """
    cache_key = (symbol, timeframe)
    cached = _CACHE.get(cache_key)
    if cached and (time.time() - cached[1]) < _CACHE_TTL_SECONDS:
        return cached[0]

    interval = TIMEFRAME_MAP.get(timeframe, Interval.INTERVAL_15_MINUTES)
    tv_symbol = _to_tradingview_symbol(symbol)

    try:
        handler = TA_Handler(
            symbol=tv_symbol,
            exchange=exchange,
            screener=screener,
            interval=interval,
        )
        analysis = handler.get_analysis()
        summary = analysis.summary

        recommendation = summary.get("RECOMMENDATION", "NEUTRAL")
        result = {
            "recommendation": recommendation,
            "score": RECOMMENDATION_SCORE.get(recommendation, 50),
            "buy_signals": summary.get("BUY", 0),
            "sell_signals": summary.get("SELL", 0),
            "neutral_signals": summary.get("NEUTRAL", 0),
        }
        _CACHE[cache_key] = (result, time.time())
        return result
    except Exception as e:
        logger.warning(f"TradingView indisponible pour {symbol} ({timeframe}): {e}")
        result = {"recommendation": "INDISPONIBLE", "score": 50, "buy_signals": 0, "sell_signals": 0, "neutral_signals": 0}
        _CACHE[cache_key] = (result, time.time())
        return result
