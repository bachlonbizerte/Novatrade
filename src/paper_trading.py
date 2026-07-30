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
                   take_profit_pct: float, position_size_usdt: float,
                   score: int = None, path: str = TRADES_PATH) -> dict:
    """Enregistre une nouvelle position simulée en cours, avec le montant réellement alloué."""
    trades = _load(path)

    trade = {
        "id": f"{symbol.replace('/', '')}-{int(datetime.now(timezone.utc).timestamp())}",
        "symbol": symbol,
        "entry_price": entry_price,
        "position_size_usdt": position_size_usdt,
        "stop_loss_price": round(entry_price * (1 - stop_loss_pct / 100), 6),
        "take_profit_price": round(entry_price * (1 + take_profit_pct / 100), 6),
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
        "score_at_entry": score,
        "extended_minutes": 0,
        "last_status_sent_at": None,
    }
    trades.append(trade)
    _save(trades, path)
    logger.info(f"Position simulée ouverte: {symbol} @ {entry_price} ({position_size_usdt} USDT)")
    return trade


def check_and_close_positions(client, max_duration_minutes: float = None, path: str = TRADES_PATH) -> list:
    """
    Vérifie chaque position ouverte contre le prix actuel: la ferme si le
    stop loss ou le take profit est atteint, OU si elle est ouverte depuis
    plus de `max_duration_minutes`. Retourne la liste des trades fermés.
    """
    trades = _load(path)
    closed_now = []
    now = datetime.now(timezone.utc)

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

        opened_at = datetime.fromisoformat(trade["opened_at"])
        age_minutes = (now - opened_at).total_seconds() / 60
        effective_max_duration = (max_duration_minutes + trade.get("extended_minutes", 0)) if max_duration_minutes else None
        hit_time_limit = effective_max_duration and age_minutes >= effective_max_duration

        if hit_tp or hit_sl or hit_time_limit:
            trade["status"] = "closed"
            trade["exit_price"] = current_price
            trade["closed_at"] = now.isoformat()
            trade["exit_reason"] = "take_profit" if hit_tp else ("stop_loss" if hit_sl else "time_limit")
            trade["pnl_pct"] = round((current_price - trade["entry_price"]) / trade["entry_price"] * 100, 2)
            size = trade.get("position_size_usdt", 0) or 0
            trade["pnl_usd"] = round(size * trade["pnl_pct"] / 100, 2)
            closed_now.append(trade)
            logger.info(f"Position fermée: {trade['symbol']} ({trade['exit_reason']}, "
                        f"pnl={trade['pnl_pct']}% / {trade['pnl_usd']} USD)")

    if closed_now:
        _save(trades, path)

    return closed_now


def get_account_state(starting_capital: float, allocation_pct: float,
                       max_concurrent_positions: int, path: str = TRADES_PATH) -> dict:
    """Calcule l'état du "compte" simulé (capital courant, budget alloué/disponible)."""
    trades = _load(path)
    closed = [t for t in trades if t["status"] == "closed"]
    open_trades = [t for t in trades if t["status"] == "open"]

    realized_pnl_usd = sum(t.get("pnl_usd", 0) for t in closed)
    current_capital = starting_capital + realized_pnl_usd
    allocated_budget = current_capital * (allocation_pct / 100)
    open_allocated = sum(t.get("position_size_usdt", 0) for t in open_trades)
    available_budget = max(0, allocated_budget - open_allocated)

    return {
        "current_capital": round(current_capital, 2),
        "allocated_budget": round(allocated_budget, 2),
        "open_allocated": round(open_allocated, 2),
        "available_budget": round(available_budget, 2),
        "open_positions_count": len(open_trades),
        "max_concurrent_positions": max_concurrent_positions,
    }


def suggest_position_size(starting_capital: float, allocation_pct: float,
                           max_concurrent_positions: int, path: str = TRADES_PATH) -> float:
    """Montant à proposer pour une NOUVELLE position (budget réparti entre les positions max)."""
    state = get_account_state(starting_capital, allocation_pct, max_concurrent_positions, path)
    if state["open_positions_count"] >= max_concurrent_positions:
        return 0
    per_position_budget = state["allocated_budget"] / max_concurrent_positions
    return round(min(per_position_budget, state["available_budget"]), 2)


def _find_trade(trades: list, trade_id: str):
    for t in trades:
        if t["id"] == trade_id:
            return t
    return None


def get_trade_by_id(trade_id: str, path: str = TRADES_PATH) -> dict:
    return _find_trade(_load(path), trade_id)


def get_open_positions(path: str = TRADES_PATH) -> list:
    return [t for t in _load(path) if t["status"] == "open"]


def close_position_manually(trade_id: str, current_price: float, path: str = TRADES_PATH) -> dict:
    """Clôture immédiate d'une position à la demande de l'utilisateur (bouton Telegram)."""
    trades = _load(path)
    trade = _find_trade(trades, trade_id)
    if not trade or trade["status"] != "open":
        return None

    trade["status"] = "closed"
    trade["exit_price"] = current_price
    trade["closed_at"] = datetime.now(timezone.utc).isoformat()
    trade["exit_reason"] = "manual"
    trade["pnl_pct"] = round((current_price - trade["entry_price"]) / trade["entry_price"] * 100, 2)
    size = trade.get("position_size_usdt", 0) or 0
    trade["pnl_usd"] = round(size * trade["pnl_pct"] / 100, 2)
    _save(trades, path)
    logger.info(f"Position clôturée manuellement: {trade['symbol']} (pnl={trade['pnl_pct']}%)")
    return trade


def extend_position(trade_id: str, extra_minutes: int = 30, path: str = TRADES_PATH) -> dict:
    """Repousse la limite de durée max d'une position spécifique (bouton Prolonger)."""
    trades = _load(path)
    trade = _find_trade(trades, trade_id)
    if not trade or trade["status"] != "open":
        return None

    trade["extended_minutes"] = trade.get("extended_minutes", 0) + extra_minutes
    _save(trades, path)
    logger.info(f"Position prolongée: {trade['symbol']} (+{extra_minutes} min, "
                f"total prolongation: {trade['extended_minutes']} min)")
    return trade


def touch_status_sent(trade_id: str, path: str = TRADES_PATH):
    """Marque qu'un message de statut vient d'être envoyé pour cette position (espacement)."""
    trades = _load(path)
    trade = _find_trade(trades, trade_id)
    if trade:
        trade["last_status_sent_at"] = datetime.now(timezone.utc).isoformat()
        _save(trades, path)


def get_symbol_stats(symbol: str, path: str = TRADES_PATH) -> dict:
    """Statistiques de performance passées pour UN symbole précis (utilisé par ai_decision)."""
    trades = _load(path)
    closed = [t for t in trades if t["status"] == "closed" and t["symbol"] == symbol]
    if not closed:
        return {"num_trades": 0, "win_rate_pct": None}
    wins = [t for t in closed if t["pnl_pct"] > 0]
    return {"num_trades": len(closed), "win_rate_pct": round(len(wins) / len(closed) * 100, 1)}


def get_stats(path: str = TRADES_PATH) -> dict:
    """Calcule les statistiques de performance sur toutes les positions fermées."""
    trades = _load(path)
    closed = [t for t in trades if t["status"] == "closed"]
    open_positions = [t for t in trades if t["status"] == "open"]

    if not closed:
        return {"total_trades": 0, "open_positions": len(open_positions), "win_rate_pct": 0,
                "avg_pnl_pct": 0, "cumulative_pnl_pct": 0, "cumulative_pnl_usd": 0}

    wins = [t for t in closed if t["pnl_pct"] > 0]
    cumulative_pct = sum(t["pnl_pct"] for t in closed)
    cumulative_usd = sum(t.get("pnl_usd", 0) for t in closed)

    return {
        "total_trades": len(closed),
        "open_positions": len(open_positions),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1),
        "avg_pnl_pct": round(cumulative_pct / len(closed), 2),
        "cumulative_pnl_pct": round(cumulative_pct, 2),
        "cumulative_pnl_usd": round(cumulative_usd, 2),
    }
