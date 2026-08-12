"""
Exécution de positions sur Capital.com (compte Demo). Volontairement séparé
du système de paper trading crypto (paper_trading.py) — ici on s'appuie
directement sur le compte Demo de Capital.com comme source de vérité pour
les positions ouvertes, au lieu de tenir notre propre registre local.
Ça donne des résultats plus réalistes (vrais spreads/slippage simulés par
leur moteur) et évite de dupliquer la logique de suivi.

En plus du suivi des positions ouvertes/fermées, on garde un historique
persistant des trades clôturés (docs/data/capital_trades_history.json)
pour calculer de vraies statistiques (taux de réussite, PnL cumulé) —
le capital total, lui, est lu directement depuis le compte Capital.com
(plus fiable que de le recalculer nous-mêmes).
"""

import os
import json
import logging

from src.ai_decision import decide_capital
from src.telegram_notifier import send_message_simple

logger = logging.getLogger(__name__)

CAPITAL_SNAPSHOT_PATH = "docs/data/capital_positions_snapshot.json"
CAPITAL_HISTORY_PATH = "docs/data/capital_trades_history.json"


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


def _remove_from_snapshot(deal_id: str, path: str = CAPITAL_SNAPSHOT_PATH):
    """Retire une position du snapshot immédiatement après qu'on l'a nous-mêmes
    clôturée — évite que le prochain scan la re-détecte comme "fermée" et
    envoie une 2ème notification en double pour le même événement."""
    snapshot = _load_snapshot(path)
    if deal_id in snapshot:
        del snapshot[deal_id]
        _save_snapshot(snapshot, path)


def _load_history(path: str = CAPITAL_HISTORY_PATH) -> list:
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def record_capital_trade_closed(trade_info: dict, path: str = CAPITAL_HISTORY_PATH):
    """Ajoute un trade clôturé à l'historique persistant Capital.com."""
    history = _load_history(path)
    history.append(trade_info)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(history, f, indent=2, default=str)


def get_capital_stats(capital_client, path: str = CAPITAL_HISTORY_PATH) -> dict:
    """
    Statistiques Capital.com: taux de réussite et PnL cumulé calculés à
    partir de notre historique local des trades clôturés, mais le capital
    total est lu en direct depuis le compte Capital.com (plus fiable).
    """
    history = _load_history(path)
    open_positions = get_open_capital_positions(capital_client)

    balance = {}
    try:
        balance = capital_client.get_account_balance()
    except Exception as e:
        logger.warning(f"Impossible de récupérer le solde Capital.com: {e}")

    if not history:
        return {
            "total_trades": 0, "open_positions": len(open_positions), "win_rate_pct": 0,
            "cumulative_pnl_usd": 0, "current_capital": balance.get("balance"),
            "available": balance.get("available"),
        }

    wins = [t for t in history if (t.get("profit") or 0) > 0]
    cumulative_usd = sum(t.get("profit", 0) or 0 for t in history)

    return {
        "total_trades": len(history),
        "open_positions": len(open_positions),
        "win_rate_pct": round(len(wins) / len(history) * 100, 1),
        "cumulative_pnl_usd": round(cumulative_usd, 2),
        "current_capital": balance.get("balance"),
        "available": balance.get("available"),
    }


def check_capital_closed_positions(capital_client, path: str = CAPITAL_SNAPSHOT_PATH) -> list:
    """
    Détecte les positions Capital.com fermées depuis le dernier passage
    (par le moteur natif de Capital.com — SL/TP — puisque notre code n'est
    pas à l'origine de cette fermeture-là). On compare l'instantané actuel
    à celui du cycle précédent: une position présente avant et absente
    maintenant = fermée entre-temps. La raison (stop/objectif) est déduite
    du dernier PnL connu avant disparition. Chaque fermeture détectée est
    aussi ajoutée à l'historique persistant pour les statistiques.
    """
    current_positions = get_open_capital_positions(capital_client)
    current_snapshot = {}
    for p in current_positions:
        pos = p.get("position", {})
        deal_id = pos.get("dealId")
        if deal_id:
            current_snapshot[deal_id] = {
                "epic": p.get("market", {}).get("epic", "?"),
                "profit": pos.get("upl"),
                "direction": pos.get("direction"),
                "level": pos.get("level"),
                "size": pos.get("size"),
                "stop_level": pos.get("stopLevel"),
                "profit_level": pos.get("profitLevel"),
            }

    previous_snapshot = _load_snapshot(path)
    closed = []
    for deal_id, info in previous_snapshot.items():
        if deal_id not in current_snapshot:
            profit = info.get("profit")
            if profit is None:
                reason = "inconnue"
            elif profit >= 0:
                reason = "objectif atteint (probable)"
            else:
                reason = "stop touché (probable)"
            entry = {"deal_id": deal_id, "reason": reason, **info}
            closed.append(entry)
            record_capital_trade_closed(entry)

    _save_snapshot(current_snapshot, path)
    return closed


def monitor_capital_positions_quick(capital_client, bot_token: str, chat_id: str):
    """
    Vérification rapide et légère (appelée toutes les ~30s, indépendamment
    du scan complet toutes les 90s): dès qu'une position ouverte passe en
    positif, on la clôture immédiatement pour sécuriser un petit gain,
    plutôt que d'attendre l'objectif complet ou de risquer un retournement.
    On ne touche PAS aux positions négatives — le stop-loss natif de
    Capital.com reste seul responsable de la protection à la baisse.
    """
    positions = get_open_capital_positions(capital_client)
    for p in positions:
        pos = p.get("position", {})
        profit = pos.get("upl")
        deal_id = pos.get("dealId")
        epic = p.get("market", {}).get("epic", "?")

        if profit is not None and profit > 0 and deal_id:
            try:
                capital_client.close_position(deal_id)
                _remove_from_snapshot(deal_id)
                record_capital_trade_closed({
                    "deal_id": deal_id, "epic": epic, "profit": profit,
                    "direction": pos.get("direction"), "level": pos.get("level"),
                    "size": pos.get("size"), "reason": "prise de profit rapide",
                })
                if bot_token and chat_id:
                    send_message_simple(bot_token, chat_id,
                                         f"🟢 *[CAPITAL.COM]* {epic} clôturée rapidement en profit.\n"
                                         f"PnL: +{profit} USD (prise de profit anticipée)")
                logger.info(f"[CAPITAL] Prise de profit rapide: {epic} (+{profit} USD)")
            except Exception as e:
                logger.error(f"[CAPITAL] Erreur lors de la clôture rapide de {epic}: {e}")
