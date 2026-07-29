"""
Fait appel à un modèle Claude (Anthropic) pour donner un second avis
qualitatif et lisible sur un signal déjà généré par le moteur de règles
(ai_decision.py). Ce n'est pas un remplacement du scoring — c'est une
couche d'aide à la décision supplémentaire, en langage naturel, qui peut
souligner des nuances qu'un score seul ne capture pas.

Entièrement optionnel: si ANTHROPIC_API_KEY n'est pas défini ou que le
package `anthropic` n'est pas installé, cette fonction retourne simplement
une chaîne vide et le reste du bot continue de fonctionner normalement.

Installation: pip install anthropic
Variable d'environnement: ANTHROPIC_API_KEY=sk-ant-...
"""

import os
import logging

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"


def get_ai_verdict(decision: dict, model: str = DEFAULT_MODEL) -> str:
    """Retourne un court avis qualitatif (3-4 phrases) sur la décision, ou "" si indisponible."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""

    try:
        import anthropic
    except ImportError:
        logger.warning("Package 'anthropic' non installé — pip install anthropic pour activer le second avis IA.")
        return ""

    breakdown = decision.get("breakdown", {})
    mtf = breakdown.get("internal_multi_timeframe", {})
    tv = breakdown.get("tradingview", {})
    hist = decision.get("symbol_history", {})

    prompt = f"""Tu es un analyste financier expérimenté qui donne un second avis bref sur un
signal de trading crypto généré par un système de règles techniques automatisé.
Sois concis (3-4 phrases maximum), factuel, souligne les incertitudes ou signaux
faibles s'il y en a. N'invente aucune donnée non fournie ci-dessous. Ne donne
jamais de conseil financier personnalisé — reste analytique et neutre.

Symbole: {decision['symbol']}
Score technique final: {decision['final_score']}/100
Recommandation du système: {decision['recommendation']}
Consensus multi-timeframe: {mtf.get('consensus')}
Signal TradingView: {tv.get('recommendation')} ({tv.get('buy_signals')} achat / {tv.get('sell_signals')} vente)
Historique de performance sur ce symbole: {hist if hist.get('num_trades') else "aucun trade passé enregistré"}

Donne ton avis: es-tu d'accord avec cette recommandation ? Quel est le principal risque à surveiller ?"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"Erreur lors de l'appel à l'agent IA (Claude): {e}")
        return ""
