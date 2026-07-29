"""
Limite le nombre de notifications Telegram envoyées par jour (compteur
persistant dans un fichier JSON, remis à zéro automatiquement au changement
de date UTC) ET impose un espacement minimum entre deux notifications
(ex: pas plus d'une toutes les 5 minutes, même si le scanner tourne
chaque minute).
"""

import os
import json
from datetime import datetime, timezone

COUNTER_PATH = "docs/data/notification_count.json"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load(path: str = COUNTER_PATH) -> dict:
    if not os.path.exists(path):
        return {"date": _today(), "count": 0, "last_sent_at": None}
    with open(path, "r") as f:
        data = json.load(f)
    if data.get("date") != _today():
        return {"date": _today(), "count": 0, "last_sent_at": data.get("last_sent_at")}  # nouveau jour -> reset du compteur (mais pas de l'espacement)
    data.setdefault("last_sent_at", None)
    return data


def _save(data: dict, path: str = COUNTER_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def can_send(max_per_day: int, min_interval_minutes: float = 0, path: str = COUNTER_PATH) -> bool:
    """Vrai si le plafond quotidien n'est pas atteint ET que l'espacement minimum est respecté."""
    data = _load(path)
    if data["count"] >= max_per_day:
        return False
    if min_interval_minutes and data.get("last_sent_at"):
        last = datetime.fromisoformat(data["last_sent_at"])
        elapsed_minutes = (datetime.now(timezone.utc) - last).total_seconds() / 60
        if elapsed_minutes < min_interval_minutes:
            return False
    return True


def record_sent(path: str = COUNTER_PATH) -> int:
    data = _load(path)
    data["count"] += 1
    data["last_sent_at"] = datetime.now(timezone.utc).isoformat()
    _save(data, path)
    return data["count"]


def get_remaining(max_per_day: int, path: str = COUNTER_PATH) -> int:
    data = _load(path)
    return max(0, max_per_day - data["count"])
