"""Teknik gostergeler — deterministik, pandas ile. LLM'e girdi olur."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder yumusatmasi
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    # 0'a bolunmeyi engelle; np.nan kullaniyoruz ki seri float dtype'ta kalsin
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_indicators(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """df: ts, open, high, low, close, volume (ts artan sirali)."""
    cfg = cfg or {}
    out = df.copy()
    if out.empty or "close" not in out:
        return out
    out = out.sort_values("ts").reset_index(drop=True)
    close = out["close"].astype(float)

    for w in cfg.get("sma", [20, 50, 200]):
        out[f"sma{w}"] = close.rolling(w, min_periods=max(2, w // 2)).mean()
    for w in cfg.get("ema", [12, 26]):
        out[f"ema{w}"] = close.ewm(span=w, adjust=False).mean()

    out["rsi"] = _rsi(close, int(cfg.get("rsi_period", 14)))
    out["ret_1d"] = close.pct_change() * 100
    out["ret_5d"] = close.pct_change(5) * 100
    out["ret_20d"] = close.pct_change(20) * 100
    out["vol_20d"] = close.pct_change().rolling(20).std() * (252 ** 0.5) * 100

    if "volume" in out:
        vol = out["volume"].astype(float)
        out["vol_avg20"] = vol.rolling(20, min_periods=5).mean()
        out["vol_ratio"] = vol / out["vol_avg20"]

    return out


def technical_snapshot(symbol: str, df: pd.DataFrame) -> dict:
    """Son bar icin kompakt ozet — LLM prompt'una girecek olan sey."""
    if df.empty:
        return {"symbol": symbol, "status": "veri yok"}
    last = df.iloc[-1]

    def f(key, nd=2):
        v = last.get(key)
        try:
            return None if pd.isna(v) else round(float(v), nd)
        except (TypeError, ValueError):
            return None

    close = f("close")
    sma50, sma200 = f("sma50"), f("sma200")

    trend = "belirsiz"
    if None not in (close, sma50, sma200):
        if close > sma50 > sma200:
            trend = "yukselis (fiyat > SMA50 > SMA200)"
        elif close < sma50 < sma200:
            trend = "dusus (fiyat < SMA50 < SMA200)"
        else:
            trend = "yatay/kararsiz"

    rsi = f("rsi", 1)
    rsi_note = None
    if rsi is not None:
        rsi_note = "asiri alim" if rsi >= 70 else ("asiri satim" if rsi <= 30 else "notr")

    return {
        "symbol": symbol,
        "son_tarih": str(last.get("ts")),
        "kapanis": close,
        "getiri_1g_%": f("ret_1d"),
        "getiri_5g_%": f("ret_5d"),
        "getiri_20g_%": f("ret_20d"),
        "sma20": f("sma20"), "sma50": sma50, "sma200": sma200,
        "rsi14": rsi, "rsi_yorum": rsi_note,
        "yillik_volatilite_%": f("vol_20d", 1),
        "hacim_orani_20g": f("vol_ratio"),
        "trend": trend,
    }
