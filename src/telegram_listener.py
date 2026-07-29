"""
Processus à part, qui doit tourner EN CONTINU (contrairement au scanner,
qui lui s'exécute par cron). Il écoute les clics sur les boutons Telegram
(long polling) et déclenche l'action correspondante.

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
from src.paper_trading import open_position

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def answer_callback(bot_token: str, callback_query_id: str, text: str):
    url = TELEGRAM_API.format(token=bot_token, method="answerCallbackQuery")
    requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=10)


def send_message(bot_token: str, chat_id: str, text: str):
    url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)


def handle_action(client: ExchangeClient, config: dict, action: str, symbol: str) -> str:
    """Exécute l'action choisie par l'utilisateur et retourne un message de confirmation."""
    if action == "buy":
        amount = config["risk"]["trade_amount_usdt"]
        sl_pct = config["risk"]["stop_loss_pct"]
        tp_pct = config["risk"]["take_profit_pct"]

        # En mode DRY_RUN: on ouvre une position simulée SUIVIE (avec SL/TP)
        # au lieu d'un simple ordre "tiré et oublié" — ça permet de mesurer
        # la performance réelle de la stratégie dans le temps.
        df = client.fetch_ohlcv(symbol, "15m", limit=1)
        current_price = float(df.iloc[-1]["close"])

        if client.dry_run:
            open_position(symbol, current_price, sl_pct, tp_pct)
            return (f"✅ [SIMULATION] Position ouverte pour {symbol} @ {current_price}\n"
                    f"SL: -{sl_pct}% · TP: +{tp_pct}% — suivie automatiquement, "
                    f"tu seras notifié à la clôture.")
        else:
            client.create_market_order(symbol, "buy", amount)
            return f"✅ Ordre d'achat réel envoyé pour {symbol} ({amount} USDT)"
    elif action == "wait":
        return f"⏳ OK, on garde {symbol} à l'œil, aucune action prise."
    elif action == "pass":
        return f"❌ {symbol} ignoré."
    return "Action inconnue."


def run_listener():
    load_dotenv()
    with open("config/config.yaml", "r") as f:
        config = yaml.safe_load(f)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

    client = ExchangeClient(
        exchange_name=config["exchange"]["name"],
        api_key=os.getenv("EXCHANGE_API_KEY", ""),
        api_secret=os.getenv("EXCHANGE_API_SECRET", ""),
        dry_run=dry_run,
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

                confirmation = handle_action(client, config, action, symbol)
                answer_callback(bot_token, callback["id"], confirmation)
                send_message(bot_token, chat_id, confirmation)
                logger.info(confirmation)

        except Exception as e:
            logger.error(f"Erreur dans la boucle du listener: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_listener()
