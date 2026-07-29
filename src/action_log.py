"""
Journal complet de chaque action prise via les boutons Telegram — y compris
les échecs (ex: ordre réel refusé par l'exchange, erreur réseau, etc.).
Sert de mémoire brute pour analyser après-coup pourquoi une décision a été
prise, en complément de `paper_trading.py` qui ne suit que les positions.
"""

import os
import json
from datetime import datetime, timezone

LOG_PATH = "docs/data/action_log.json"


def log_action(symbol: str, action: str, score: int = None,
                success: bool = True, detail: str = "", path: str = LOG_PATH) -> dict:
    entries = []
    if os.path.exists(path):
        with open(path, "r") as f:
            entries = json.load(f)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "action": action,
        "score": score,
        "success": success,
        "detail": detail,
    }
    entries.append(entry)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(entries, f, indent=2, default=str)

    return entry


def get_history(symbol: str = None, path: str = LOG_PATH) -> list:
    """Retourne tout l'historique, ou seulement celui d'un symbole si précisé."""
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        entries = json.load(f)
    if symbol:
        return [e for e in entries if e["symbol"] == symbol]
    return entries
