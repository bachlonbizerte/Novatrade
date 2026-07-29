"""
"IA de décision" — en réalité un moteur d'ensemble pondéré basé sur des
règles (pas un modèle de machine learning entraîné). C'est volontairement
transparent: il combine plusieurs sources de signal pour réduire le bruit
d'un seul indicateur, avec un raisonnement lisible.

Sources combinées:
1. Score technique interne multi-timeframe (RSI/MACD/tendance/volume/volatilité)
2. Signal externe TradingView (consensus de dizaines d'indicateurs tiers)
3. Bonus/malus de cohérence: si nos deux sources sont d'accord, on renforce
   la confiance ; si elles se contredisent, on réduit le score (incertitude).
"""

from src.analyzers import analyze_multi_timeframe
from src.tradingview_module import get_tradingview_signal


def decide(client, symbol: str, timeframes: list, tf_weights: dict = None,
           internal_weight: float = 0.6, tradingview_weight: float = 0.4) -> dict:
    """
    Retourne la décision finale pour un symbole:
    {symbol, final_score, recommendation, confidence, reasoning, breakdown}
    """
    mtf = analyze_multi_timeframe(client, symbol, timeframes, tf_weights)
    internal_score = mtf["consolidated_score"]

    # Signal TradingView pris sur la timeframe la plus courte de la liste (réactivité)
    tv = get_tradingview_signal(symbol, timeframe=timeframes[0])
    tv_score = tv["score"]

    final_score = round(internal_score * internal_weight + tv_score * tradingview_weight)

    # Cohérence entre nos deux sources (les deux du même "côté" du marché ?)
    internal_bullish = internal_score >= 55
    tv_bullish = tv_score >= 55
    agree = internal_bullish == tv_bullish

    reasoning = []
    reasoning.append(f"Analyse interne multi-timeframe: {internal_score}/100 (consensus: {mtf['consensus']})")
    reasoning.append(f"TradingView: {tv['recommendation']} ({tv['buy_signals']} achat / "
                      f"{tv['sell_signals']} vente / {tv['neutral_signals']} neutre)")

    if agree:
        final_score = min(100, final_score + 5)
        reasoning.append("Les deux sources s'accordent → confiance renforcée")
        confidence = "haute"
    else:
        final_score = max(0, final_score - 10)
        reasoning.append("Signaux contradictoires entre analyse interne et TradingView → prudence")
        confidence = "basse"

    if final_score >= 70:
        recommendation = "ACHETER"
    elif final_score >= 45:
        recommendation = "ATTENDRE"
    else:
        recommendation = "PASSER"

    return {
        "symbol": symbol,
        "final_score": final_score,
        "recommendation": recommendation,
        "confidence": confidence,
        "reasoning": reasoning,
        "breakdown": {
            "internal_multi_timeframe": mtf,
            "tradingview": tv,
        },
    }
