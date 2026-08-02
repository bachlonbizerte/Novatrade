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
from src.ai_decision import decide, decide_capital
from src.capital_trading import handle_capital_buy, get_open_capital_positions
from src.telegram_notifier import (
    send_signal_notification, send_summary, send_message_simple,
    send_position_status, send_capital_signal_notification,
)
from src.paper_trading import (
    check_and_close_positions, get_stats, get_open_positions, touch_status_sent,
    open_position, suggest_position_size, get_account_state,
)
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


def _auto_buy(client, config: dict, symbol: str) -> str:
    if not client.dry_run:
        logger.warning("Auto-trading ignoré: le mode réel (DRY_RUN=false) exige toujours une "
                        "confirmation manuelle via Telegram, par sécurité.")
        return None

    sl_pct = config["risk"]["stop_loss_pct"]
    tp_min_pct = config["risk"]["take_profit_min_pct"]
    capital_usd = config["risk"].get("capital_usd", 100)
    allocation_pct = config["risk"].get("capital_allocation_pct", 80)
    max_concurrent = config["risk"].get("max_concurrent_positions", 2)

    state = get_account_state(capital_usd, allocation_pct, max_concurrent)
    if state["open_positions_count"] >= max_concurrent:
        return None

    amount = suggest_position_size(capital_usd, allocation_pct, max_concurrent)
    if not amount or amount <= 0:
        return None

    df = client.fetch_ohlcv(symbol, "15m", limit=1)
    current_price = float(df.iloc[-1]["close"])
    open_position(symbol, current_price, sl_pct, tp_min_pct, position_size_usdt=amount)

    return (f"🤖 *[AUTO-TRADE]* Position ouverte automatiquement pour {symbol} @ {current_price}\n"
            f"Montant: {amount} USDT · SL: -{sl_pct}% · TP: +{tp_min_pct}%")


def scan_once(config: dict, client, bot_token: str, chat_id: str):
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

    status_interval = config["risk"].get("position_status_interval_minutes", 30)
    if bot_token and chat_id:
        for pos in get_open_positions():
            last_sent = pos.get("last_status_sent_at") or pos["opened_at"]
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_sent)).total_seconds() / 60
            if elapsed >= status_interval:
                try:
                    df = client.fetch_ohlcv(pos["symbol"], "15m", limit=1)
                    current_price = float(df.iloc[-1]["close"])
                    send_position_status(bot_token, chat_id, pos, current_price)
                    touch_status_sent(pos["id"])
                except Exception as e:
                    logger.warning(f"Impossible d'envoyer le statut de {pos['symbol']}: {e}")

    symbols = config["watchlist"]["symbols"]
    timeframes = config["watchlist"].get("timeframes", ["15m", "1h", "4h"])
    tf_weights = config["watchlist"].get("timeframe_weights")
    notify_threshold = config["watchlist"].get("notify_score_threshold", 80)
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

    stats = get_stats()
    try:
        account_state = get_account_state(
            config["risk"].get("capital_usd", 100),
            config["risk"].get("capital_allocation_pct", 80),
            config["risk"].get("max_concurrent_positions", 2),
        )
        stats["current_capital"] = account_state["current_capital"]
        stats["allocated_budget"] = account_state["allocated_budget"]
    except Exception as e:
        logger.warning(f"Impossible de calculer l'état du capital: {e}")

    save_results(decisions, stats)

    if not bot_token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant — notifications désactivées.")
        return

    top_opportunities = [d for d in decisions if d["final_score"] >= notify_threshold]

    if not top_opportunities:
        logger.info(f"Aucune crypto n'atteint le seuil strict de {notify_threshold}/100 — aucune notif envoyée.")
    else:
        best = max(top_opportunities, key=lambda d: d["final_score"])

        if not can_send(max_per_day, min_interval):
            logger.info(f"Plafond quotidien atteint ou espacement de {min_interval} min pas encore écoulé "
                        f"— {best['symbol']} non envoyé cette fois (le scan continue, réessaie au prochain run).")
        else:
            best["ai_verdict"] = get_ai_verdict(best)
            auto_trade = config["watchlist"].get("auto_trade", False)

            if auto_trade and best["recommendation"] == "ACHETER" and client.dry_run:
                max_concurrent = config["risk"].get("max_concurrent_positions", 2)
                capital_usd = config["risk"].get("capital_usd", 100)
                allocation_pct = config["risk"].get("capital_allocation_pct", 80)
                state = get_account_state(capital_usd, allocation_pct, max_concurrent)

                if state["open_positions_count"] >= max_concurrent:
                    send_message_simple(bot_token, chat_id,
                                         f"ℹ️ Opportunité détectée: *{best['symbol']}* "
                                         f"(score {best['final_score']}/100, ACHETER) mais les "
                                         f"{max_concurrent} positions max sont déjà ouvertes — "
                                         f"aucune action, en attente qu'une position se libère.")
                    log_action(best["symbol"], "auto_buy_skipped_full", score=best["final_score"], success=False)
                else:
                    auto_msg = _auto_buy(client, config, best["symbol"])
                    if auto_msg:
                        send_message_simple(bot_token, chat_id,
                                             f"{auto_msg}\n\nScore: {best['final_score']}/100 "
                                             f"(confiance {best.get('confidence', '')})")
                        log_action(best["symbol"], "auto_buy", score=best["final_score"], success=True)
                    else:
                        send_message_simple(bot_token, chat_id,
                                             f"ℹ️ Opportunité détectée: *{best['symbol']}* "
                                             f"(score {best['final_score']}/100) mais budget disponible "
                                             f"insuffisant — aucune action.")
                        log_action(best["symbol"], "auto_buy_skipped_budget", score=best["final_score"], success=False)
            else:
                send_signal_notification(bot_token, chat_id, best)
                log_action(best["symbol"], "signal_sent", score=best["final_score"], success=True)

            record_sent()
            remaining = get_remaining(max_per_day)
            logger.info(f"Traitement fait pour {best['symbol']} (score {best['final_score']}). "
                        f"Notifications restantes aujourd'hui: {remaining}")

    if decisions and any(d["final_score"] > 80 for d in decisions):
        send_summary(bot_token, chat_id, decisions)


def scan_capital_once(config: dict, capital_client, bot_token: str, chat_id: str):
    cap_config = config.get("capital", {})
    if not cap_config.get("enabled", False):
        return

    symbols = cap_config.get("symbols", [])
    timeframes = config["watchlist"].get("timeframes", ["15m", "1h", "4h"])
    tf_weights = config["watchlist"].get("timeframe_weights")
    notify_threshold = cap_config.get("notify_score_threshold", 60)

    results = []
    for epic in symbols:
        try:
            logger.info(f"[CAPITAL] Analyse de {epic} sur {timeframes}...")
            decision = decide_capital(capital_client, epic, timeframes, tf_weights,
                                       stop_loss_pct=config["risk"]["stop_loss_pct"],
                                       take_profit_min_pct=config["risk"]["take_profit_min_pct"],
                                       take_profit_max_pct=config["risk"]["take_profit_max_pct"])
            results.append(decision)
            logger.info(f"[CAPITAL] {epic}: score={decision['final_score']} ({decision['recommendation']})")
        except Exception as e:
            logger.error(f"[CAPITAL] Erreur lors de l'analyse de {epic}: {e}")

    if not bot_token or not chat_id or not results:
        return

    top = [d for d in results if d["final_score"] >= notify_threshold]
    if top:
        best = max(top, key=lambda d: d["final_score"])
        auto_trade = cap_config.get("auto_trade", False)

        if auto_trade and best["recommendation"] == "ACHETER" and capital_client.demo:
            max_concurrent = cap_config.get("max_concurrent_positions", 2)
            open_positions = get_open_capital_positions(capital_client)

            if len(open_positions) >= max_concurrent:
                send_message_simple(bot_token, chat_id,
                                     f"ℹ️ *[CAPITAL.COM]* Opportunité détectée: *{best['symbol']}* "
                                     f"(score {best['final_score']}/100, ACHETER) mais les "
                                     f"{max_concurrent} positions max sont déjà ouvertes — "
                                     f"aucune action, en attente qu'une position se libère.")
                log_action(best["symbol"], "capital_auto_buy_skipped_full",
                           score=best["final_score"], success=False)
            else:
                confirmation = handle_capital_buy(capital_client, config, best["symbol"])
                send_message_simple(bot_token, chat_id,
                                     f"🤖 {confirmation}\n\nScore: {best['final_score']}/100")
                log_action(best["symbol"], "capital_auto_buy", score=best["final_score"], success=True)
        else:
            send_capital_signal_notification(bot_token, chat_id, best)
            log_action(best["symbol"], "capital_signal_sent", score=best["final_score"], success=True)


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

    timeframes_for_poll = config["watchlist"].get("timeframes", ["15m", "1h", "4h"])
    tf_weights_for_poll = config["watchlist"].get("timeframe_weights")
    try:
        poll_and_handle_updates(bot_token, client, config, timeframes_for_poll, tf_weights_for_poll)
    except Exception as e:
        logger.error(f"Erreur dans le traitement des clics Telegram (le scan continue normalement): {e}")

    scan_once(config, client, bot_token, chat_id)


if __name__ == "__main__":
    main()
