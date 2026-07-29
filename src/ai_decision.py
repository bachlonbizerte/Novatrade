"""
"IA de décision" — un moteur d'ensemble pondéré basé sur des règles
(pas un modèle de machine learning entraîné), enrichi d'un historique de
performance par crypto et, en option, d'un second avis qualitatif via un
agent IA (Claude). C'est volontairement transparent: chaque ajustement est
tracé dans `reasoning` pour que la décision reste explicable.

Sources combinées:
1. Score technique interne multi-timeframe (RSI/MACD/tendance/volume/structure/risque)
2. Signal externe TradingView (consensus de dizaines d'indicateurs tiers)
3. Bonus/malus de cohérence entre les deux sources ci-dessus
4. Bonus/malus basé sur l'historique réel de performance sur CE symbole
   (paper trading) — le bot devient plus prudent sur les cryptos qui lui
   ont mal réussi, et plus confiant sur celles qui ont bien fonctionné.

Le montant suggéré par trade suit un modèle d'allocation de capital (voir
src/paper_trading.py → suggest_position_size) : seul un % du capital
courant est utilisable (le reste reste en sécurité), réparti entre le
nombre max de positions simultanées autorisées. Le capital de référence
évolue lui-même avec les gains/pertes réalisés en simulation.
"""

from src.analyzers import analyze_multi_timeframe
from src.tradingview_module import get_tradingview_signal
from src.paper_trading import get_symbol_stats, suggest_position_size


def _dynamic_take_profit(client, symbol: str, timeframe: str,
                          tp_min_pct: float, tp_max_pct: float) -> float:
    """
    Calcule un take-profit entre tp_min_pct et tp_max_pct en fonction de la
    volatilité actuelle (ATR%): marché volatil -> objectif plus large,
    marché calme -> objectif plus proche (plus vite atteignable).
    """
    from src.analyzers import atr
    try:
        df = client.fetch_ohlcv(symbol, timeframe, limit=50)
        atr_series = atr(df, period=14)
        last_atr = atr_series.iloc[-1]
        last_price = df.iloc[-1]["close"]
        atr_pct = (last_atr / last_price) * 100 if last_price else tp_min_pct
        return round(min(tp_max_pct, max(tp_min_pct, atr_pct)), 2)
    except Exception:
        return tp_min_pct  # en cas de doute, on reste sur l'objectif le plus prudent


HORIZON_BY_TIMEFRAME = {
    "15m": "quelques heures (position courte, à surveiller de près)",
    "1h": "environ 1 jour (intraday)",
    "4h": "1 à 4 jours (swing court terme)",
    "1d": "plusieurs jours à 1-2 semaines (swing)",
}


def _estimate_horizon(timeframes: list, weights: dict = None) -> str:
    """Estime combien de temps garder la position, basé sur la timeframe dominante."""
    if not weights:
        dominant = timeframes[-1]  # à défaut, on suppose la plus longue = la plus significative
    else:
        dominant = max(weights, key=weights.get)
    return HORIZON_BY_TIMEFRAME.get(dominant, "durée non déterminée")


def decide(client, symbol: str, timeframes: list, tf_weights: dict = None,
           internal_weight: float = 0.6, tradingview_weight: float = 0.4,
           stop_loss_pct: float = 1.0, take_profit_min_pct: float = 2.0,
           take_profit_max_pct: float = 5.0, capital_usd: float = 100,
           capital_allocation_pct: float = 80, max_concurrent_positions: int = 2) -> dict:
    """
    Retourne la décision finale pour un symbole:
    {symbol, final_score, recommendation, confidence, reasoning, breakdown,
     entry_price, stop_price, target_price, dynamic_take_profit_pct,
     suggested_amount_usdt, holding_horizon}
    """
    mtf = analyze_multi_timeframe(client, symbol, timeframes, tf_weights)
    internal_score = mtf["consolidated_score"]

    entry_price = mtf["per_timeframe"].get(timeframes[0], {}).get("price")

    tv = get_tradingview_signal(symbol, timeframe=timeframes[0])
    tv_score = tv["score"]

    final_score = round(internal_score * internal_weight + tv_score * tradingview_weight)

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

    # Ajustement par l'historique réel de performance sur ce symbole (paper trading)
    hist = get_symbol_stats(symbol)
    if hist["num_trades"] >= 3:
        if hist["win_rate_pct"] < 40:
            final_score = max(0, final_score - 5)
            reasoning.append(f"Historique {symbol}: {hist['win_rate_pct']}% de réussite sur "
                              f"{hist['num_trades']} trades passés → score réduit par prudence")
            confidence = "basse"
        elif hist["win_rate_pct"] > 70:
            final_score = min(100, final_score + 5)
            reasoning.append(f"Historique {symbol}: {hist['win_rate_pct']}% de réussite sur "
                              f"{hist['num_trades']} trades passés → confiance renforcée")

    if final_score >= 70:
        recommendation = "ACHETER"
    elif final_score >= 45:
        recommendation = "ATTENDRE"
    else:
        recommendation = "PASSER"

    dynamic_tp_pct = _dynamic_take_profit(client, symbol, timeframes[0], take_profit_min_pct, take_profit_max_pct)
    suggested_amount = suggest_position_size(capital_usd, capital_allocation_pct, max_concurrent_positions)
    horizon = _estimate_horizon(timeframes, tf_weights)

    if suggested_amount:
        reasoning.append(f"Montant suggéré: {suggested_amount} USDT (part du budget alloué, "
                          f"{capital_allocation_pct}% du capital réparti sur {max_concurrent_positions} "
                          f"positions max)")
    elif recommendation == "ACHETER":
        reasoning.append("⚠️ Budget disponible insuffisant ou nombre max de positions déjà atteint")

    return {
        "symbol": symbol,
        "final_score": final_score,
        "recommendation": recommendation,
        "confidence": confidence,
        "reasoning": reasoning,
        "entry_price": entry_price,
        "stop_price": round(entry_price * (1 - stop_loss_pct / 100), 6) if entry_price else None,
        "target_price": round(entry_price * (1 + dynamic_tp_pct / 100), 6) if entry_price else None,
        "dynamic_take_profit_pct": dynamic_tp_pct,
        "suggested_amount_usdt": suggested_amount,
        "holding_horizon": horizon,
        "symbol_history": hist,
        "breakdown": {
            "internal_multi_timeframe": mtf,
            "tradingview": tv,
        },
    }
