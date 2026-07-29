"""
Envoi de notifications Telegram avec boutons inline via l'API HTTP Telegram
(pas besoin de librairie lourde, juste `requests`).

Pour créer un bot Telegram et récupérer ton token :
1. Parle à @BotFather sur Telegram -> /newbot -> suis les instructions
2. Récupère le token fourni (format: 123456:ABC-DEF...)
3. Pour trouver ton chat_id : envoie un message à ton bot, puis va sur
   https://api.telegram.org/bot<TOKEN>/getUpdates et repère "chat":{"id": ...}
"""

import logging
import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def send_message_simple(bot_token: str, chat_id: str, text: str) -> dict:
    """Envoi d'un message texte simple, sans boutons (ex: notif de clôture de position)."""
    url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
    resp = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=15)
    return resp.json()


def send_signal_notification(bot_token: str, chat_id: str, decision: dict) -> dict:
    """
    Envoie une notification formatée pour une opportunité détectée,
    avec 3 boutons inline: Acheter / Attendre / Passer.
    `decision` est le dict retourné par src.ai_decision.decide().
    """
    symbol = decision["symbol"]
    score = decision["final_score"]
    reco = decision["recommendation"]
    confidence = decision.get("confidence", "")
    reasoning = decision.get("reasoning", [])

    emoji = "🟢" if reco == "ACHETER" else ("🟡" if reco == "ATTENDRE" else "🔴")
    reasoning_text = "\n".join(f"• {r}" for r in reasoning)

    text = (
        f"{emoji} *{symbol}* — Score IA: *{score}/100* ({reco})\n"
        f"Confiance: {confidence}\n\n"
        f"{reasoning_text}"
    )

    # callback_data encode le symbole + l'action pour que le listener sache quoi faire
    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ Acheter", "callback_data": f"buy|{symbol}"},
            {"text": "⏳ Attendre", "callback_data": f"wait|{symbol}"},
            {"text": "❌ Passer", "callback_data": f"pass|{symbol}"},
        ]]
    }

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": reply_markup,
    }

    url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
    resp = requests.post(url, json=payload, timeout=15)

    if resp.status_code != 200:
        logger.error(f"Échec envoi Telegram pour {symbol}: {resp.text}")
    else:
        logger.info(f"Notification Telegram envoyée pour {symbol} (score={score})")

    return resp.json()


def send_summary(bot_token: str, chat_id: str, decisions: list) -> dict:
    """Envoie un résumé texte simple du scan complet (tous scores, triés)."""
    sorted_decisions = sorted(decisions, key=lambda r: r["final_score"], reverse=True)
    lines = ["*📊 Scan terminé*\n"]
    for r in sorted_decisions:
        emoji = "🟢" if r["recommendation"] == "ACHETER" else ("🟡" if r["recommendation"] == "ATTENDRE" else "🔴")
        lines.append(f"{emoji} {r['symbol']}: {r['final_score']}/100 ({r['recommendation']})")

    text = "\n".join(lines)
    url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
    resp = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=15)
    return resp.json()
