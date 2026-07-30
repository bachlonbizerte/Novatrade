"""
Point d'entrée pour le cron GitHub Actions:
1. Récupère les données pour chaque paire configurée (multi-timeframe)
2. Combine analyse technique interne + TradingView via le moteur de décision IA
3. Envoie une notification Telegram avec boutons pour les meilleures opportunités
4. Sauvegarde les résultats dans data/latest_scan.json (lu par le dashboard web)
"""

import os
import json
import math
import logging
from datetime import datetime, timezone
import yaml
from dotenv import load_dotenv

from src.exchange_client import ExchangeClient
from src.ai_decision import decide
from src.telegram_notifier import send_signal_notification, send_summary, send_message_simple
from src.paper_trading import check_and_close_positions, get_stats
from src.notification_limiter import can_send, record_sent, get_remaining
from src.ai_agent import get_ai_verdict
from src.action_log import log_action
from src.telegram_poller import poll_and_handle_updates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_config(path: str = "config/config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def _sanitize_for_json(obj):
    """
    Remplace récursivement les NaN/Infinity (valides en Python, mais PAS en
    JSON strict) par null. Sans ça, un calcul foireux quelque part (ex:
    division par zéro sur peu de données) produit un fichier que Python lit
    sans broncher mais que JavaScript (le dashboard) ne peut pas parser du
    tout — silencieusement, jusqu'à ce qu'on ouvre la console du navigateur.
    """
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def save_results(decisions: list, stats: dict, path: str = "docs/data/latest_scan.json"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = _sanitize_for_json({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": decisions,
        "performance": stats,
    })
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str, allow_nan=False)
    logger.info(f"Résultats sauvegardés dans {path}")


def main():
    load_dotenv()
    config = load_config()

    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    client = ExchangeClient(
        exchange_name=config["exchange"]["name"],
        api_key=os.getenv("EXCHANGE_API_KEY", ""),
        api_secret=os.getenv("EXCHANGE_API_SECRET", ""),
        dry_run=dry_run,
        fallback_exchanges=config["exchange"].get("fallback", []),
    )

    # 0. Traite d'abord les clics de boutons Telegram en attente (Acheter/Ignorer/Rescan)
    #    — remplace le besoin d'un listener hébergé à part, tournant en continu.
    timeframes_for_poll = config["watchlist"].get("timeframes", ["15m", "1h", "4h"])
    tf_weights_for_poll = config["watchlist"].get("timeframe_weights")
    poll_and_handle_updates(bot_token, client, config, timeframes_for_poll, tf_weights_for_poll)

    # 1. Vérifie d'abord si des positions simulées ouvertes doivent être clôturées (SL/TP/durée max atteints)
    max_duration = config["risk"].get("max_position_duration_minutes", 60)
    closed_trades = check_and_close_positions(client, max_duration_minutes=max_duration)
    if closed_trades and bot_token and chat_id:
        for t in closed_trades:
            emoji = "🟢" if t["pnl_pct"] > 0 else "🔴"
            reason_fr = {"take_profit": "objectif atteint", "stop_loss": "stop touché",
                         "time_limit": f"durée max ({max_duration} min) atteinte"}.get(t["exit_reason"], t["exit_reason"])
            msg = (f"{emoji} Position clôturée: {t['symbol']}\n"
                   f"Raison: {reason_fr} — PnL: {t['pnl_pct']}% ({t.get('pnl_usd', 0)} USD)")
            send_message_simple(bot_token, chat_id, msg)

    symbols = config["watchlist"]["symbols"]
    timeframes = config["watchlist"].get("timeframes", ["15m", "1h", "4h"])
    tf_weights = config["watchlist"].get("timeframe_weights")
    notify_threshold = config["watchlist"].get("notify_score_threshold", 90)
    max_per_day = config["watchlist"].get("max_notifications_per_day", 15)
    min_interval = config["watchlist"].get("min_minutes_between_notifications", 5)

    decisions = []
    for symbol in symbols:
        try:
            logger.info(f"Analyse IA de {symbol} sur {timeframes}...")
            decision = decide(client, symbol, timeframes, tf_weights,
                               stop_loss_pct=config["risk"]["stop_loss_pct"],
                               take_profit_min_pct=config["risk"]["take_profit_min_pct"],
                               take_profit_max_pct=config["risk"]["take_profit_max_pct"],
                               capital_usd=config["risk"].get("capital_usd", 100),
                               capital_allocation_pct=config["risk"].get("capital_allocation_pct", 80),
                               max_concurrent_positions=config["risk"].get("max_concurrent_positions", 2))
            decisions.append(decision)
            logger.info(f"{symbol}: score final={decision['final_score']} ({decision['recommendation']})")
        except Exception as e:
            logger.error(f"Erreur lors de l'analyse de {symbol}: {e}")

    save_results(decisions, get_stats())

    if not bot_token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant — notifications désactivées.")
        return

    top_opportunities = [d for d in decisions if d["final_score"] >= notify_threshold]

    if not top_opportunities:
        logger.info(f"Aucune crypto n'atteint le seuil strict de {notify_threshold}/100 — aucune notif envoyée.")
    else:
        # On ne garde QUE le meilleur marché du scan, pas tous ceux au-dessus du seuil
        best = max(top_opportunities, key=lambda d: d["final_score"])

        if not can_send(max_per_day, min_interval):
            logger.info(f"Plafond quotidien atteint ou espacement de {min_interval} min pas encore écoulé "
                        f"— {best['symbol']} non envoyé cette fois (le scan continue, réessaie au prochain run).")
        else:
            best["ai_verdict"] = get_ai_verdict(best)  # second avis IA, silencieux si non configuré
            send_signal_notification(bot_token, chat_id, best)
            record_sent()
            log_action(best["symbol"], "signal_sent", score=best["final_score"], success=True)
            remaining = get_remaining(max_per_day)
            logger.info(f"Signal envoyé pour {best['symbol']} (score {best['final_score']}). "
                        f"Notifications restantes aujourd'hui: {remaining}")

    if config["watchlist"].get("send_daily_summary", False) and decisions:
        send_summary(bot_token, chat_id, decisions)


if __name__ == "__main__":
    main()
