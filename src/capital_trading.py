"""
Exécution de positions sur Capital.com (compte Demo). Volontairement séparé
du système de paper trading crypto (paper_trading.py) — ici on s'appuie
directement sur le compte Demo de Capital.com comme source de vérité pour
les positions ouvertes, au lieu de tenir notre propre registre local.
"""

import logging

from src.ai_decision import decide_capital

logger = logging.getLogger(__name__)


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
