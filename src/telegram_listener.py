"""
Processus à part, qui doit tourner EN CONTINU (contrairement au scanner,
qui lui s'exécute par cron). Il écoute les clics sur les boutons Telegram
(long polling) et déclenche l'action correspondante : ACHETER / IGNORER / RESCAN.

⚠️ Ce script ne peut PAS tourner sur GitHub Actions (qui est fait pour des
jobs courts et ponctuels). Options pour l'héberger gratuitement :
- Railway / Render (background worker, free tier)
- Une petite VM Oracle Cloud Free Tier
- Plus tard : ton VPS payant

Lancement: python -m src.telegram_listener
"""

import os
import time
import logging
import yaml
import requests
from dotenv import load_dotenv

from src.exchange_client import ExchangeClient
from src.paper_trading import open_position, suggest_position_size, get_account_state
from src.ai_decision import decide
from src.telegram_notifier import send_signal_notification

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def answer_callback(bot_token: str, callback_query_id: str, text: str):
    url = TELEGRAM_API.format(token=bot_token, method="answerCallbackQuery")
    requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=10)


def send_message(bot_token: str, chat_id: str, text: str):
    url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)


def handle_buy(client: ExchangeClient, config: dict, symbol: str) -> str:
    sl_pct = config["risk"]["stop_loss_pct"]
    tp_min_pct = config["risk"]["take_profit_min_pct"]
    capital_usd = config["risk"].get("capital_usd", 100)
    allocation_pct = config["risk"].get("capital_allocation_pct", 80)
    max_concurrent = config["risk"].get("max_concurrent_positions", 2)

    # Recalcul en temps réel au moment du clic (l'état a pu changer depuis la notification)
    state = get_account_state(capital_usd, allocation_pct, max_concurrent)
    if state["open_positions_count"] >= max_concurrent:
        return (f"⛔ Impossible d'ouvrir {symbol} : {max_concurrent} positions déjà ouvertes "
                f"(max autorisé). Attends qu'une position se clôture.")

    amount = suggest_position_size(capital_usd, allocation_pct, max_concurrent)
    if not amount or amount <= 0:
        return f"⛔ Impossible d'ouvrir {symbol} : budget disponible insuffisant ({state['available_budget']} USDT restants)."

    df = client.fetch_ohlcv(symbol, "15m", limit=1)
    current_price = float(df.iloc[-1]["close"])

    if client.dry_run:
        # En mode DRY_RUN: on ouvre une position simulée SUIVIE (avec SL/TP)
        # au lieu d'un simple ordre "tiré et oublié" — ça permet de mesurer
        # la performance réelle de la stratégie dans le temps.
        open_position(symbol, current_price, sl_pct, tp_min_pct, position_size_usdt=amount)
        return (f"✅ [SIMULATION] Position ouverte pour {symbol} @ {current_price}\n"
                f"Montant: {amount} USDT · SL: -{sl_pct}% · TP: +{tp_min_pct}%\n"
                f"Capital courant: {state['current_capital']} USD · Budget restant après ce trade: "
                f"{round(state['available_budget'] - amount, 2)} USDT")
    else:
        client.create_market_order(symbol, "buy", amount)
        return f"✅ Ordre d'achat réel envoyé pour {symbol} ({amount} USDT)"


def run_listener():
    load_dotenv()
    with open("config/config.yaml", "r") as f:
        config = yaml.safe_load(f)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    timeframes = config["watchlist"].get("timeframes", ["15m", "1h", "4h"])
    tf_weights = config["watchlist"].get("timeframe_weights")

    client = ExchangeClient(
        exchange_name=config["exchange"]["name"],
        api_key=os.getenv("EXCHANGE_API_KEY", ""),
        api_secret=os.getenv("EXCHANGE_API_SECRET", ""),
        dry_run=dry_run,
        fallback_exchanges=config["exchange"].get("fallback", []),
    )

    offset = None
    logger.info("Listener Telegram démarré (long polling)...")

    while True:
        try:
            url = TELEGRAM_API.format(token=bot_token, method="getUpdates")
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset

            resp = requests.get(url, params=params, timeout=35).json()

            for update in resp.get("result", []):
                offset = update["update_id"] + 1

                callback = update.get("callback_query")
                if not callback:
                    continue

                data = callback["data"]  # ex: "buy|BTC/USDT"
                action, symbol = data.split("|", 1)
                chat_id = callback["message"]["chat"]["id"]

                if action == "buy":
                    confirmation = handle_buy(client, config, symbol)
                    answer_callback(bot_token, callback["id"], "Position enregistrée ✅")
                    send_message(bot_token, chat_id, confirmation)
                    logger.info(confirmation)

                elif action == "ignore":
                    answer_callback(bot_token, callback["id"], "Signal ignoré")
                    logger.info(f"{symbol} ignoré par l'utilisateur.")

                elif action == "rescan":
                    # Relance une analyse fraîche du symbole et renvoie une notification à jour
                    answer_callback(bot_token, callback["id"], "Rescan en cours...")
                    try:
                        decision = decide(
                            client, symbol, timeframes, tf_weights,
                            stop_loss_pct=config["risk"]["stop_loss_pct"],
                            take_profit_min_pct=config["risk"]["take_profit_min_pct"],
                            take_profit_max_pct=config["risk"]["take_profit_max_pct"],
                            capital_usd=config["risk"].get("capital_usd", 100),
                            capital_allocation_pct=config["risk"].get("capital_allocation_pct", 80),
                            max_concurrent_positions=config["risk"].get("max_concurrent_positions", 2),
                        )
                        send_signal_notification(bot_token, chat_id, decision)
                        logger.info(f"Rescan de {symbol}: score={decision['final_score']}")
                    except Exception as e:
                        send_message(bot_token, chat_id, f"❌ Erreur lors du rescan de {symbol}: {e}")
                        logger.error(f"Erreur rescan {symbol}: {e}")

        except Exception as e:
            logger.error(f"Erreur dans la boucle du listener: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_listener()
