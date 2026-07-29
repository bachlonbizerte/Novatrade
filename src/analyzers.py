"""
Analyseur technique approfondi. Calcule plusieurs indicateurs et les combine
en un score de 0 à 100 représentant la qualité de l'opportunité d'achat.

Indicateurs utilisés:
- RSI (surachat / survente)
- MACD (momentum / tendance)
- EMA 50 vs EMA 200 (tendance de fond)
- Volume relatif (spike de volume = confirmation)
- Volatilité (ATR simplifié, pour éviter les marchés trop erratiques)
"""

import pandas as pd
import numpy as np


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def support_resistance(df: pd.DataFrame, lookback: int = 50):
    """Support/résistance simplifiés: plus bas et plus haut sur les N dernières bougies."""
    window = df.tail(lookback)
    return float(window["low"].min()), float(window["high"].max())


def analyze(df: pd.DataFrame, symbol: str) -> dict:
    """
    Analyse un DataFrame OHLCV et retourne un score /100 découpé en 5 catégories :
      - Trend (tendance de fond, EMA50/EMA200)          .... /25
      - Momentum (RSI + MACD)                           .... /20
      - Volume (confirmation par le volume)              .... /20
      - Structure (position par rapport au support/résistance) .... /20
      - Risk (volatilité — pénalise les marchés erratiques)     .... /15
    """
    df = df.copy()
    if len(df) < 210:
        return {"symbol": symbol, "score": 0, "recommendation": "DONNÉES INSUFFISANTES", "details": {}}

    df["rsi"] = rsi(df["close"])
    macd_line, signal_line, hist = macd(df["close"])
    df["macd_hist"] = hist
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["atr"] = atr(df)
    df["volume_avg"] = df["volume"].rolling(20).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = last["close"]

    details = {}

    # 1. TREND /25 — tendance de fond (EMA50 vs EMA200) + confirmation par le prix
    trend_score = 0
    bullish_trend = last["ema50"] > last["ema200"]
    if bullish_trend:
        trend_score = 15
        if price > last["ema50"]:  # le prix confirme aussi la tendance court-terme
            trend_score += 10
    score_trend = trend_score
    details["trend"] = "haussière" if bullish_trend else "baissière"

    # 2. MOMENTUM /20 — RSI (jusqu'à 10) + MACD (jusqu'à 10)
    rsi_val = last["rsi"]
    if 30 <= rsi_val <= 50:
        rsi_part = 10
    elif 50 < rsi_val <= 65:
        rsi_part = 6
    elif rsi_val < 30:
        rsi_part = 8  # survente = opportunité mais risquée
    else:
        rsi_part = 0  # surachat

    if prev["macd_hist"] <= 0 and last["macd_hist"] > 0:
        macd_part = 10  # croisement fraîchement haussier
    elif last["macd_hist"] > 0:
        macd_part = 6
    else:
        macd_part = 0
    score_momentum = rsi_part + macd_part
    details["rsi"] = round(rsi_val, 1)
    details["macd_hist"] = round(last["macd_hist"], 4)

    # 3. VOLUME /20 — confirmation par un volume au-dessus de la moyenne
    score_volume = 0
    if last["volume_avg"] and last["volume"] > 1.3 * last["volume_avg"]:
        score_volume = 20
    elif last["volume_avg"] and last["volume"] > last["volume_avg"]:
        score_volume = 12
    details["volume_ratio"] = round(last["volume"] / last["volume_avg"], 2) if last["volume_avg"] else None

    # 4. STRUCTURE /20 — position du prix entre support et résistance récents.
    #    Proche du support avec de la marge avant la résistance = bon point d'entrée.
    #    Proche de la résistance = upside limité, on pénalise.
    support, resistance = support_resistance(df)
    price_range = resistance - support
    position_in_range = (price - support) / price_range if price_range > 0 else 0.5

    if position_in_range <= 0.3:
        score_structure = 20
    elif position_in_range <= 0.5:
        score_structure = 14
    elif position_in_range <= 0.7:
        score_structure = 8
    else:
        score_structure = 0
    details["support"] = round(support, 4)
    details["resistance"] = round(resistance, 4)
    details["position_in_range_pct"] = round(position_in_range * 100, 1)

    # 5. RISK /15 — pénalise les marchés trop volatils (ATR élevé relatif au prix)
    atr_pct = (last["atr"] / price) * 100 if price else 0
    if atr_pct < 3:
        score_risk = 15
    elif atr_pct < 6:
        score_risk = 8
    else:
        score_risk = 0
    details["volatility_pct"] = round(atr_pct, 2)

    score = score_trend + score_momentum + score_volume + score_structure + score_risk
    details["breakdown"] = {
        "trend": f"{score_trend}/25",
        "momentum": f"{score_momentum}/20",
        "volume": f"{score_volume}/20",
        "structure": f"{score_structure}/20",
        "risk": f"{score_risk}/15",
    }

    if score >= 70:
        recommendation = "ACHETER"
    elif score >= 45:
        recommendation = "ATTENDRE"
    else:
        recommendation = "PASSER"

    return {
        "symbol": symbol,
        "score": score,
        "recommendation": recommendation,
        "price": round(price, 4),
        "details": details,
    }


def analyze_multi_timeframe(client, symbol: str, timeframes: list, weights: dict = None) -> dict:
    """
    Lance analyze() sur plusieurs timeframes (ex: 15m, 1h, 4h) pour le même
    symbole, puis combine les scores en un score consolidé pondéré.

    weights: ex {"15m": 0.2, "1h": 0.3, "4h": 0.5} — donne plus de poids
    aux timeframes longues (tendance de fond plus fiable que le bruit court-terme).
    Si non fourni, poids égaux.
    """
    if weights is None:
        weights = {tf: 1 / len(timeframes) for tf in timeframes}

    per_tf_results = {}
    weighted_sum = 0
    total_weight = 0

    for tf in timeframes:
        try:
            df = client.fetch_ohlcv(symbol, tf, limit=250)
            result = analyze(df, symbol)
            per_tf_results[tf] = result
            w = weights.get(tf, 1 / len(timeframes))
            weighted_sum += result["score"] * w
            total_weight += w
        except Exception as e:
            per_tf_results[tf] = {"symbol": symbol, "score": 0, "recommendation": "ERREUR", "error": str(e)}

    consolidated_score = round(weighted_sum / total_weight) if total_weight else 0

    # Consensus: est-ce que toutes les timeframes s'accordent sur la même direction ?
    recos = [r["recommendation"] for r in per_tf_results.values() if "recommendation" in r]
    consensus = recos[0] if recos and all(r == recos[0] for r in recos) else "MIXTE"

    return {
        "symbol": symbol,
        "consolidated_score": consolidated_score,
        "consensus": consensus,
        "per_timeframe": per_tf_results,
    }
