"""
Exécution de positions sur Capital.com (compte Demo). Volontairement séparé
du système de paper trading crypto (paper_trading.py) — ici on s'appuie
directement sur le compte Demo de Capital.com comme source de vérité pour
les positions ouvertes, au lieu de tenir notre propre registre local.
Ça donne des résultats plus réalistes (vrais spreads/slippage simulés par
leur moteur) et évite de dupliquer la logique de suivi.
"""

import os
import json
import logging

from src.ai_decision import decide_capital

logger = logging.getLogger(__name__)

CAPITAL_SNAPSHOT_PATH = "docs/data/capital_positions_snapshot.json"


def get_open_capital_positions(capital_client) -> list:
    try:
        return capital_client.get_positions()
    except Exception as e:
        logger.error(f"Impossible de récupérer les positions Capital.com: {e}")
        return []


def handle_capital_buy(capital_client, config: dict, epic: str) -> str:
    max_concurrent = config.get("capital", {}).get("max_concurrent_positions", 2)

    open_positions = get_open_capital_positions(capital_client)
    if len(open_positions) >= max_concurrent:
        return (f"⛔ Impossible d'ouvrir {epic} : {max_concurrent} positions Capital.com déjà "
                f"ouvertes (max autorisé). Attends qu'une position se clôture.")

    timeframes = config["watchlist"].get("timeframes", ["15m", "1h", "4h"])
    tf_weights = config["watchlist"].get("timeframe_weights")

    try:
        decision = decide_capital(capital_client, epic, timeframes, tf_weights,
                                   stop_loss_pct=config["risk"]["stop_loss_pct"],
                                   take_profit_min_pct=config["risk"]["take_profit_min_pct"],
                                   take_profit_max_pct=config["risk"]["take_profit_max_pct"])
    except Exception as e:
        return f"❌ Impossible de recalculer les niveaux pour {epic}: {e}"

    try:
        details = capital_client.get_market_details(epic)
        min_size = details.get("dealingRules", {}).get("minDealSize", {}).get("value", 1)
    except Exception as e:
        return f"❌ Impossible de récupérer la taille minimale pour {epic}: {e}"

    try:
        result = capital_client.create_position(
            epic=epic, direction="BUY", size=min_size,
            stop_level=decision["stop_price"], profit_level=decision["target_price"],
        )
        return (f"✅ *[CAPITAL.COM DEMO]* Position ouverte pour {epic}\n"
                f"Taille: {min_size} · Entrée: `{decision['entry_price']}` · "
                f"Stop: `{decision['stop_price']}` · Objectif: `{decision['target_price']}`\n"
                f"Référence: `{result.get('dealReference', 'N/A')}`")
    except Exception as e:
        return f"❌ Échec de l'ouverture de la position {epic}: {e}"


def handle_capital_close(capital_client, deal_id: str) -> str:
    try:
        capital_client.close_position(deal_id)
        return f"🔴 Position Capital.com clôturée (deal `{deal_id}`)."
    except Exception as e:
        return f"❌ Échec de la clôture: {e}"


def _load_snapshot(path: str = CAPITAL_SNAPSHOT_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def _save_snapshot(snapshot: dict, path: str = CAPITAL_SNAPSHOT_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)


def check_capital_closed_positions(capital_client, path: str = CAPITAL_SNAPSHOT_PATH) -> list:
    """
    Détecte les positions Capital.com fermées depuis le dernier passage.
    Comme c'est le moteur de Capital.com (pas notre code) qui applique le
    stop/take-profit, on ne peut pas "savoir" qu'une position s'est fermée
    autrement qu'en comparant l'état actuel à l'instantané du cycle précédent
    — une position présente avant et absente maintenant = fermée entre-temps.
    Le PnL rapporté est la dernière valeur connue avant disparition (Capital.com
    ne donne pas facilement le PnL exact de clôture sans requête supplémentaire),
    donc une légère approximation, clairement indiquée comme telle.
    """
    current_positions = get_open_capital_positions(capital_client)
    current_snapshot = {}
    for p in current_positions:
        pos = p.get("position", {})
        deal_id = pos.get("dealId")
        if deal_id:
            current_snapshot[deal_id] = {
                "epic": p.get("market", {}).get("epic", "?"),
                "profit": pos.get("profit"),
                "direction": pos.get("direction"),
                "level": pos.get("level"),
                "size": pos.get("size"),
            }

    previous_snapshot = _load_snapshot(path)
    closed = []
    for deal_id, info in previous_snapshot.items():
        if deal_id not in current_snapshot:
            closed.append({"deal_id": deal_id, **info})

    _save_snapshot(current_snapshot, path)
    return closed
