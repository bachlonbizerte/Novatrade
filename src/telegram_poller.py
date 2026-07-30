"""
Alternative 100% gratuite au listener en continu : au lieu d'un process
qui tourne 24/7 (nécessitant un hébergement à part comme Railway/Render),
cette fonction vérifie une seule fois s'il y a eu des clics de boutons
Telegram depuis le dernier passage, et les traite immédiatement.

Appelée à chaque run du scanner (donc chaque minute) — le délai de réaction
à un clic est donc d'environ 1 minute max, largement suffisant pour une
confirmation manuelle. Aucun serveur à part n'est nécessaire.

L'offset (position dans la liste des updates Telegram) est sauvegardé dans
docs/data/telegram_offset.json pour ne jamais traiter deux fois le même clic.
"""

import os
import json
import logging
import requests

from src.paper_trading import open_position, suggest_position_size, get_account_state
from src.ai_decision import decide
from src.telegram_notifier import send_signal_notification

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
OFFSET_PATH = "docs/data/telegram_offset.json"


def _load_offset(path: str = OFFSET_PATH):
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f).get("offset")


def _save_offset(offset: int, path: str = OFFSET_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"offset": offset}, f)


def _answer_callback(bot_token: str, callback_query_id: str, text: str):
    url = TELEGRAM_API.format(token=bot_token, method="answerCallbackQuery")
    requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=10)


def _send_message(bot_token: str, chat_id: str, text: str):
    url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)


def _handle_buy(client, config: dict, symbol: str) -> str:
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


def poll_and_handle_updates(bot_token: str, client, config: dict, timeframes: list, tf_weights: dict = None):
    """Récupère les nouveaux clics de boutons depuis le dernier passage et les traite."""
    if not bot_token:
        return

    offset = _load_offset()
    params = {"timeout": 0}
    if offset:
        params["offset"] = offset

    try:
        url = TELEGRAM_API.format(token=bot_token, method="getUpdates")
        resp = requests.get(url, params=params, timeout=15).json()
    except Exception as e:
        logger.warning(f"Impossible de vérifier les clics Telegram: {e}")
        return

    last_offset = offset
    for update in resp.get("result", []):
        last_offset = update["update_id"] + 1

        callback = update.get("callback_query")
        if not callback:
            continue

        try:
            data = callback["data"]  # ex: "buy|BTC/USDT"
            action, symbol = data.split("|", 1)
            chat_id = callback["message"]["chat"]["id"]

            if action == "buy":
                confirmation = _handle_buy(client, config, symbol)
                _answer_callback(bot_token, callback["id"], "Position enregistrée ✅")
                _send_message(bot_token, chat_id, confirmation)
                logger.info(confirmation)

            elif action == "ignore":
                _answer_callback(bot_token, callback["id"], "Signal ignoré")
                logger.info(f"{symbol} ignoré par l'utilisateur.")

            elif action == "rescan":
                _answer_callback(bot_token, callback["id"], "Rescan en cours...")
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
            # Un clic qui plante ne doit JAMAIS bloquer les suivants — on log,
            # on prévient l'utilisateur si possible, et on continue.
            logger.error(f"Erreur en traitant le clic (update {update.get('update_id')}): {e}")
            try:
                chat_id_fallback = callback.get("message", {}).get("chat", {}).get("id")
                if chat_id_fallback:
                    _send_message(bot_token, chat_id_fallback, f"❌ Erreur lors du traitement de ce clic: {e}")
            except Exception:
                pass  # on ne laisse jamais une erreur secondaire remonter

    if last_offset != offset:
        _save_offset(last_offset)
