"""Teknik gostergeler — deterministik, pandas ile. LLM'e girdi olur."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


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

    # AYNI GUNUN IKI BARI SESSIZCE SIFIR GETIRI URETIR.
    #
    # `prices`in birincil anahtari (instrument_id, ts, source): ayni gun
    # iki KAYNAKTAN gelirse iki satir olur. 2026-08-17'de tam bu oldu —
    # isyatirim ve midas ayni gunu yazdi, seride tarih iki kez gorundu ve
    # `pct_change()` son iki ozdes kapanisi bolup 0,00 dondurdu. BIST
    # tablosunun 10 sembolunun 10'unda "gunluk getiri 0,00" cikti; SISE
    # gercekte -%6,67 dusmustu. Hacim de bozuluyor: son satirin hacmi
    # obur kaynagin olcegindeydi, 20 gunluk ortalama otekinin.
    #
    # Dogru cozum cagiran tarafta (`db.fiyat_serisi()` tek kaynak secer),
    # burasi IKINCI savunma: bu fonksiyona kirli bir seri gelirse sessizce
    # yanlis hesaplamak yerine son bari tutup GURULTU cikarir.
    if "ts" in out and out["ts"].duplicated().any():
        yinelenen = int(out["ts"].duplicated().sum())
        out = out.drop_duplicates(subset="ts", keep="last").reset_index(drop=True)
        log.warning("compute_indicators: %d yinelenen tarih dusuruldu "
                    "(seri birden fazla kaynaktan geliyor olabilir)", yinelenen)

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

    def ham(key):
        """Yuvarlanmamis deger — KARSILASTIRMA icin."""
        v = last.get(key)
        try:
            return None if pd.isna(v) else float(v)
        except (TypeError, ValueError):
            return None

    # Fiyat basamagi VARLIGA GORE secilir. Sabit 2 hane kriptoda veriyi
    # yok ediyordu: ROSE 0.0055 USD iken kapanis da SMA20/50/200 de "0.01"
    # olarak cikiyor, seviye analizi imkansizlasiyordu. ~6 anlamli hane
    # birakiliyor; hisse tarafinda sonuc pratikte degismiyor.
    p = ham("close") or 0.0
    a = abs(p)
    nd_fiyat = 2 if a >= 100 else 4 if a >= 1 else 6 if a >= 0.01 else 8

    def f(key, nd=None):
        v = ham(key)
        return None if v is None else round(v, nd_fiyat if nd is None else nd)

    close = f("close")
    sma50, sma200 = f("sma50"), f("sma200")

    # TREND HAM DEGERLERLE belirlenir. Yuvarlanmis degerlerle karsilastirma
    # ucuz varliklarda hepsini esitliyor ve trendi zorla "yatay" yapiyordu.
    h_close, h_sma50, h_sma200 = ham("close"), ham("sma50"), ham("sma200")
    trend = "belirsiz"
    if None not in (h_close, h_sma50, h_sma200):
        if h_close > h_sma50 > h_sma200:
            trend = "yukselis (fiyat > SMA50 > SMA200)"
        elif h_close < h_sma50 < h_sma200:
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
        "getiri_1g_%": f("ret_1d", 2),
        "getiri_5g_%": f("ret_5d", 2),
        "getiri_20g_%": f("ret_20d", 2),
        "sma20": f("sma20"), "sma50": sma50, "sma200": sma200,
        "rsi14": rsi, "rsi_yorum": rsi_note,
        "yillik_volatilite_%": f("vol_20d", 1),
        "hacim_orani_20g": f("vol_ratio", 2),
        "trend": trend,
    }
