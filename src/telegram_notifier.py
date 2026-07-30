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
from datetime import datetime, timezone
import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def send_message_simple(bot_token: str, chat_id: str, text: str) -> dict:
    """Envoi d'un message texte simple, sans boutons (ex: notif de clôture de position)."""
    url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
    resp = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=15)
    return resp.json()


def send_position_status(bot_token: str, chat_id: str, trade: dict, current_price: float) -> dict:
    """
    Envoie un point de statut pour une position ouverte: prix actuel, PnL,
    temps écoulé — avec 2 boutons pour agir directement dessus.
    """
    pnl_pct = round((current_price - trade["entry_price"]) / trade["entry_price"] * 100, 2)
    emoji = "🟢" if pnl_pct >= 0 else "🔴"

    opened_at = datetime.fromisoformat(trade["opened_at"])
    elapsed_min = int((datetime.now(timezone.utc) - opened_at).total_seconds() / 60)

    text = (
        f"{emoji} *{trade['symbol']}* — position en cours ({pnl_pct:+.2f}%)\n\n"
        f"Entrée : `{trade['entry_price']}`\n"
        f"Actuel : `{current_price}`\n"
        f"Stop : `{trade['stop_loss_price']}`\n"
        f"Objectif : `{trade['take_profit_price']}`\n"
        f"Ouverte depuis {elapsed_min} min"
    )

    reply_markup = {
        "inline_keyboard": [[
            {"text": "🔴 Clôturer maintenant", "callback_data": f"close|{trade['id']}"},
            {"text": "⏱ Prolonger +30min", "callback_data": f"extend|{trade['id']}"},
        ]]
    }

    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "reply_markup": reply_markup}
    url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
    resp = requests.post(url, json=payload, timeout=15)
    return resp.json()


def send_signal_notification(bot_token: str, chat_id: str, decision: dict) -> dict:
    """
    Envoie une notification formatée pour une opportunité détectée,
    avec 3 boutons inline: Acheter / Ignorer / Rescan.
    `decision` est le dict retourné par src.ai_decision.decide().
    """
    symbol = decision["symbol"]
    score = decision["final_score"]
    reco = decision["recommendation"]
    confidence = decision.get("confidence", "")
    entry = decision.get("entry_price")
    stop = decision.get("stop_price")
    target = decision.get("target_price")
    amount = decision.get("suggested_amount_usdt")
    horizon = decision.get("holding_horizon")

    emoji = "🟢" if reco == "ACHETER" else ("🟡" if reco == "ATTENDRE" else "🔴")

    text = (
        f"🚨 *NOVA AI SIGNAL*\n\n"
        f"{emoji} *{symbol.replace('/', '')}*\n"
        f"Score : *{score}/100* ({reco}, confiance {confidence})\n\n"
        f"Entrée : `{entry}`\n"
        f"Stop : `{stop}`\n"
        f"Objectif : `{target}`\n"
        f"Montant suggéré : `{amount} USDT`\n"
        f"Horizon : {horizon}"
    )

    ai_verdict = decision.get("ai_verdict")
    if ai_verdict:
        text += f"\n\n🤖 *Avis IA (Claude)* :\n{ai_verdict}"

    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ ACHETER", "callback_data": f"buy|{symbol}"},
            {"text": "❌ IGNORER", "callback_data": f"ignore|{symbol}"},
            {"text": "🔄 RESCAN", "callback_data": f"rescan|{symbol}"},
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
