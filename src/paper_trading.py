"""
Suivi des trades simulés (paper trading). Objectif: garder une trace de
chaque décision "Acheter" pour pouvoir mesurer, dans le temps, si le bot
prend de bonnes décisions — avant de risquer de l'argent réel.

Stockage: docs/data/trades.json (liste simple, suffisante pour ce volume).
"""

import os
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TRADES_PATH = "docs/data/trades.json"


def _load(path: str = TRADES_PATH) -> list:
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def _save(trades: list, path: str = TRADES_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(trades, f, indent=2, default=str)


def open_position(symbol: str, entry_price: float, stop_loss_pct: float,
                   take_profit_pct: float, score: int = None, path: str = TRADES_PATH) -> dict:
    """Enregistre une nouvelle position simulée en cours."""
    trades = _load(path)

    trade = {
        "id": f"{symbol.replace('/', '')}-{int(datetime.now(timezone.utc).timestamp())}",
        "symbol": symbol,
        "entry_price": entry_price,
        "stop_loss_price": round(entry_price * (1 - stop_loss_pct / 100), 6),
        "take_profit_price": round(entry_price * (1 + take_profit_pct / 100), 6),
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
        "score_at_entry": score,
    }
    trades.append(trade)
    _save(trades, path)
    logger.info(f"Position simulée ouverte: {symbol} @ {entry_price}")
    return trade


def check_and_close_positions(client, path: str = TRADES_PATH) -> list:
    """
    Vérifie chaque position ouverte contre le prix actuel: la ferme si le
    stop loss ou le take profit est atteint. Retourne la liste des trades
    fermés lors de cet appel (pour notification éventuelle).
    """
    trades = _load(path)
    closed_now = []

    for trade in trades:
        if trade["status"] != "open":
            continue
        try:
            df = client.fetch_ohlcv(trade["symbol"], "15m", limit=1)
            current_price = float(df.iloc[-1]["close"])
        except Exception as e:
            logger.error(f"Impossible de récupérer le prix pour {trade['symbol']}: {e}")
            continue

        hit_tp = current_price >= trade["take_profit_price"]
        hit_sl = current_price <= trade["stop_loss_price"]

        if hit_tp or hit_sl:
            trade["status"] = "closed"
            trade["exit_price"] = current_price
            trade["closed_at"] = datetime.now(timezone.utc).isoformat()
            trade["exit_reason"] = "take_profit" if hit_tp else "stop_loss"
            trade["pnl_pct"] = round((current_price - trade["entry_price"]) / trade["entry_price"] * 100, 2)
            closed_now.append(trade)
            logger.info(f"Position fermée: {trade['symbol']} ({trade['exit_reason']}, pnl={trade['pnl_pct']}%)")

    if closed_now:
        _save(trades, path)

    return closed_now


def get_stats(path: str = TRADES_PATH) -> dict:
    """Calcule les statistiques de performance sur toutes les positions fermées."""
    trades = _load(path)
    closed = [t for t in trades if t["status"] == "closed"]
    open_positions = [t for t in trades if t["status"] == "open"]

    if not closed:
        return {"total_trades": 0, "open_positions": len(open_positions), "win_rate_pct": 0,
                "avg_pnl_pct": 0, "cumulative_pnl_pct": 0}

    wins = [t for t in closed if t["pnl_pct"] > 0]
    cumulative = sum(t["pnl_pct"] for t in closed)

    return {
        "total_trades": len(closed),
        "open_positions": len(open_positions),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1),
        "avg_pnl_pct": round(cumulative / len(closed), 2),
        "cumulative_pnl_pct": round(cumulative, 2),
    }
