"""
Écoute en continu les clics sur les boutons Telegram (ACHETER / IGNORER /
RESCAN / Clôturer / Prolonger) via long-polling — réaction quasi instantanée.

Fait pour tourner 24/7 sur un VPS (contrairement à telegram_poller.py, conçu
pour un contexte "un seul passage" comme GitHub Actions). Sur le VPS, ce
script tourne en parallèle de run_forever.py (le scan périodique), géré par
systemd (nova-listener.service).

Lancement : python -m src.telegram_listener
"""

import os
import time
import logging
import yaml
import requests
from dotenv import load_dotenv

from src.exchange_client import ExchangeClient
from src.capital_client import CapitalClient
from src.capital_trading import handle_capital_buy
from src.paper_trading import (
    open_position, suggest_position_size, get_account_state,
    close_position_manually, extend_position, get_trade_by_id, get_open_positions,
)
from src.ai_decision import decide
from src.telegram_notifier import send_signal_notification, send_position_status

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def answer_callback(bot_token: str, callback_query_id: str, text: str):
    url = TELEGRAM_API.format(token=bot_token, method="answerCallbackQuery")
    requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=10)


def send_message(bot_token: str, chat_id: str, text: str, keyboard: bool = False):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = {
            "keyboard": [[{"text": "📊 Statut des positions"}]],
            "resize_keyboard": True,
        }
    url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
    requests.post(url, json=payload, timeout=10)


def handle_status_command(client: ExchangeClient, bot_token: str, chat_id: str):
    """Répond à /status ou au bouton 'Statut des positions': état de chaque position ouverte."""
    open_positions = get_open_positions()

    if not open_positions:
        send_message(bot_token, chat_id, "Aucune position ouverte actuellement.")
        return

    for pos in open_positions:
        try:
            df = client.fetch_ohlcv(pos["symbol"], "15m", limit=1)
            current_price = float(df.iloc[-1]["close"])
            send_position_status(bot_token, chat_id, pos, current_price)
        except Exception as e:
            send_message(bot_token, chat_id, f"❌ Impossible de récupérer le statut de {pos['symbol']}: {e}")


def handle_buy(client: ExchangeClient, config: dict, symbol: str) -> str:
    sl_pct = config["risk"]["stop_loss_pct"]
    tp_min_pct = config["risk"]["take_profit_min_pct"]
    capital_usd = config["risk"].get("capital_usd", 100)
    allocation_pct = config["risk"].get("capital_allocation_pct", 80)
    max_concurrent = config["risk"].get("max_concurrent_positions", 2)

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
        open_position(symbol, current_price, sl_pct, tp_min_pct, position_size_usdt=amount)
        return (f"✅ [SIMULATION] Position ouverte pour {symbol} @ {current_price}\n"
                f"Montant: {amount} USDT · SL: -{sl_pct}% · TP: +{tp_min_pct}%\n"
                f"Capital courant: {state['current_capital']} USD · Budget restant: "
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

    capital_client = None
    if config.get("capital", {}).get("enabled", False):
        capital_client = CapitalClient(
            api_key=os.getenv("CAPITAL_API_KEY", ""),
            identifier=os.getenv("CAPITAL_IDENTIFIER", ""),
            api_password=os.getenv("CAPITAL_API_PASSWORD", ""),
            demo=True,
        )

    offset = None
    logger.info("Listener Telegram démarré (long polling, réaction instantanée)...")

    chat_id_env = os.getenv("TELEGRAM_CHAT_ID", "")
    if chat_id_env:
        send_message(bot_token, chat_id_env,
                      "🤖 NOVA est en ligne. Utilise le bouton ci-dessous ou tape /status "
                      "pour voir l'état de tes positions ouvertes à tout moment.", keyboard=True)

    while True:
        try:
            url = TELEGRAM_API.format(token=bot_token, method="getUpdates")
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset

            resp = requests.get(url, params=params, timeout=35).json()

            for update in resp.get("result", []):
                offset = update["update_id"] + 1

                message = update.get("message")
                if message and message.get("text", "").strip() in ("/status", "📊 Statut des positions"):
                    try:
                        handle_status_command(client, bot_token, message["chat"]["id"])
                    except Exception as e:
                        logger.error(f"Erreur lors du /status: {e}")
                    continue

                callback = update.get("callback_query")
                if not callback:
                    continue

                try:
                    data = callback["data"]
                    action, payload_id = data.split("|", 1)
                    chat_id = callback["message"]["chat"]["id"]

                    if action == "buy":
                        confirmation = handle_buy(client, config, payload_id)
                        answer_callback(bot_token, callback["id"], "Position enregistrée ✅")
                        send_message(bot_token, chat_id, confirmation)
                        logger.info(confirmation)

                    elif action == "ignore":
                        answer_callback(bot_token, callback["id"], "Signal ignoré")
                        logger.info(f"{payload_id} ignoré par l'utilisateur.")

                    elif action == "rescan":
                        answer_callback(bot_token, callback["id"], "Rescan en cours...")
                        decision = decide(
                            client, payload_id, timeframes, tf_weights,
                            stop_loss_pct=config["risk"]["stop_loss_pct"],
                            take_profit_min_pct=config["risk"]["take_profit_min_pct"],
                            take_profit_max_pct=config["risk"]["take_profit_max_pct"],
                            capital_usd=config["risk"].get("capital_usd", 100),
                            capital_allocation_pct=config["risk"].get("capital_allocation_pct", 80),
                            max_concurrent_positions=config["risk"].get("max_concurrent_positions", 2),
                        )
                        send_signal_notification(bot_token, chat_id, decision)
                        logger.info(f"Rescan de {payload_id}: score={decision['final_score']}")

                    elif action == "close":
                        trade = get_trade_by_id(payload_id)
                        if not trade:
                            answer_callback(bot_token, callback["id"], "Position introuvable (déjà clôturée ?)")
                        else:
                            df = client.fetch_ohlcv(trade["symbol"], "15m", limit=1)
                            current_price = float(df.iloc[-1]["close"])
                            closed = close_position_manually(payload_id, current_price)
                            answer_callback(bot_token, callback["id"], "Position clôturée ✅")
                            send_message(bot_token, chat_id,
                                         f"🔴 {closed['symbol']} clôturée manuellement.\n"
                                         f"PnL: {closed['pnl_pct']:+.2f}% ({closed.get('pnl_usd', 0):+.2f} USD)")
                            logger.info(f"Position clôturée manuellement: {closed['symbol']}")

                    elif action == "extend":
                        trade = extend_position(payload_id, extra_minutes=30)
                        if not trade:
                            answer_callback(bot_token, callback["id"], "Position introuvable (déjà clôturée ?)")
                        else:
                            answer_callback(bot_token, callback["id"], "Prolongée de 30 min ✅")
                            send_message(bot_token, chat_id,
                                         f"⏱ {trade['symbol']} prolongée de 30 min supplémentaires "
                                         f"(total: +{trade['extended_minutes']} min).")
                            logger.info(f"Position prolongée: {trade['symbol']}")

                    elif action == "capbuy":
                        if not capital_client:
                            answer_callback(bot_token, callback["id"], "Capital.com non configuré")
                        else:
                            confirmation = handle_capital_buy(capital_client, config, payload_id)
                            answer_callback(bot_token, callback["id"], "Traité ✅")
                            send_message(bot_token, chat_id, confirmation)
                            logger.info(confirmation)

                    elif action == "capignore":
                        answer_callback(bot_token, callback["id"], "Signal ignoré")
                        logger.info(f"[CAPITAL] {payload_id} ignoré par l'utilisateur.")

                except Exception as e:
                    logger.error(f"Erreur en traitant le clic (update {update.get('update_id')}): {e}")
                    try:
                        chat_id_fallback = callback.get("message", {}).get("chat", {}).get("id")
                        if chat_id_fallback:
                            send_message(bot_token, chat_id_fallback, f"❌ Erreur lors du traitement de ce clic: {e}")
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"Erreur dans la boucle du listener: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_listener()
