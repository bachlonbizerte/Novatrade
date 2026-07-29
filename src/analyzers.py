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


def analyze(df: pd.DataFrame, symbol: str) -> dict:
    """
    Analyse un DataFrame OHLCV et retourne un dict avec le score composite
    et le détail de chaque sous-indicateur, pour un symbole donné.
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

    score = 0
    details = {}

    # 1. RSI : idéal dans zone de reprise depuis survente (30-50), mauvais si surachat (>70)
    rsi_val = last["rsi"]
    if 30 <= rsi_val <= 50:
        rsi_score = 25
    elif 50 < rsi_val <= 65:
        rsi_score = 15
    elif rsi_val < 30:
        rsi_score = 20  # survente = opportunité mais risquée
    else:
        rsi_score = 0  # surachat
    score += rsi_score
    details["rsi"] = round(rsi_val, 1)

    # 2. MACD : histogramme qui vient de passer positif = momentum haussier naissant
    macd_score = 0
    if prev["macd_hist"] <= 0 and last["macd_hist"] > 0:
        macd_score = 25  # croisement fraîchement haussier
    elif last["macd_hist"] > 0:
        macd_score = 15  # déjà haussier
    score += macd_score
    details["macd_hist"] = round(last["macd_hist"], 4)

    # 3. Tendance de fond : EMA50 > EMA200 (golden cross zone) = favorable
    trend_score = 20 if last["ema50"] > last["ema200"] else 0
    score += trend_score
    details["trend"] = "haussière" if last["ema50"] > last["ema200"] else "baissière"

    # 4. Volume : confirmation par un volume au-dessus de la moyenne
    volume_score = 0
    if last["volume_avg"] and last["volume"] > 1.3 * last["volume_avg"]:
        volume_score = 15
    elif last["volume_avg"] and last["volume"] > last["volume_avg"]:
        volume_score = 8
    score += volume_score
    details["volume_ratio"] = round(last["volume"] / last["volume_avg"], 2) if last["volume_avg"] else None

    # 5. Volatilité : pénalise les marchés trop erratiques (ATR élevé relatif au prix)
    volatility_score = 0
    atr_pct = (last["atr"] / last["close"]) * 100 if last["close"] else 0
    if atr_pct < 3:
        volatility_score = 15
    elif atr_pct < 6:
        volatility_score = 8
    score += volatility_score
    details["volatility_pct"] = round(atr_pct, 2)

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
        "price": round(last["close"], 4),
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
