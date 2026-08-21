"""
KORUMA SEVIYESI — pozisyon basina 2N-ATR stop ve kirilim alarmi.

NEDEN BU, "AL/SAT SINYALI" DEGIL
--------------------------------
Sistemin olculmus bir kenari YOK: 2026-08-20 backtest'i tarayici
sinyallerinin 24 hucresinin 22'sinde sifirdan ayirt edilemedigini
gosterdi. Yani "al" demek icin gereken kanit elde yok.

Koruma seviyesi bu kaniti GEREKTIRMEZ. Bir tahmin degil, bir OLCUM:
"bu kagidin son 20 gunluk ortalama gunluk salinimi N; fiyat bunun iki
kati asagi inerse, bu artik siradan gurultu degildir." Isabet orani
bilinmeden de durustce sunulabilir — tez bozulmasiyla ayni epistemik
sinifta (bkz. `pulse/tez.py`).

NEDEN 2N
--------
Kaplumbaga sisteminin stop kurali. N = 20 gunluk ATR, yani kagidin
KENDI gunluk salinimi. Sabit yuzde (or. "%10 dusunce") kripto mikro-kapta
her hafta, AEX'te hicbir zaman tetiklenir. 2N, oynakliga gore olcekli
tek esiktir ve backtest'te de bu kullanildi — iki katmanda iki farkli
stop tanimi olmasi, `analysis.trend_takip` ile bu modulun sessizce
ayrismasi demekti.

UC KURAL
--------
1. STOP YALNIZCA YUKARI HAREKET EDER. Fiyat yukseldikce 2N asagisi da
   yukselir ve kazanci kilitler; fiyat duserse seviye YERINDE KALIR.
   Asagi da inseydi stop hicbir zaman kirilmaz, kendi kendini gecersiz
   kilan bir koruma olurdu.
2. BIR KEZ CALAR. Kirilinca damgalanir; esigin altinda salinan bir
   kagit her kosuda alarm uretirse kullanici bildirimleri kapatir ve
   alarmin degeri nadirliginden gelir.
3. TOPARLANINCA YENIDEN KURULUR. Kirilan bir seviye sonsuza dek olu
   kalirsa pozisyon o gunden sonra KORUMASIZ olur. Fiyat stop'un
   `TOPARLANMA_PAYI` kadar ustune donerse seviye yeniden kurulur; pay,
   esik etrafinda salinan bir kagidin alarmi yakip sondurmesini onler.

EMIR YOK. Bu katman hicbir zaman emir gondermez ve gonderemez
(`risk.allow_order_execution: false`). Seviye bir olcumdur; ne yapacagi
kullanicinin karari.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Stop mesafesi: giristen kac ATR asagi.
STOP_N = 2.0

# ATR icin gereken en az bar. `_atr` 20 gunluk pencere + 1 onceki
# kapanis istiyor; 25 pay birakiyor.
ASGARI_BAR = 25

# Kirilan seviye, fiyat stop'un bu kadar USTUNE dondugunde yeniden
# kurulur. %2 secildi: tipik bir 2N mesafesi %3-9 (olculdu 2026-08-21,
# Ali'nin pozisyonlari: BNB %3,9 · CNDX %3,3 · NVDA %6,4 · ASML %8,8),
# yani %2 esigin hemen ustunde salinmayi filtreliyor ama gercek bir
# toparlanmayi geciktirmiyor.
TOPARLANMA_PAYI = 0.02

# Seri bu kadar gunden bayatsa seviye GUNCELLENMEZ ve kirilim ILAN
# EDILMEZ. Bayat bir kapanisla "stop kirildi" demek, olmayan bir olayi
# bildirmek olurdu.
AZAMI_BAYATLIK_GUN = 5


def _bugun_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _yas_gun(ts) -> int | None:
    from datetime import date
    try:
        return (date.today() - date.fromisoformat(str(ts)[:10])).days
    except (TypeError, ValueError):
        return None


class Koruma:
    """Bir sahibin pozisyonlari icin koruma seviyelerini yonetir."""

    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------
    def _seviye_hesapla(self, instrument_id: int) -> dict | None:
        """
        Son kapanis ve 2N stop. Hesaplanamıyorsa None (sessizce atlanir —
        her kagitta ATR hesaplanamaz ve bu bir ariza degil, kapsamdir).
        """
        from ..analysis.karsilastirma import borsa_limiti, son_kesintisiz
        from ..analysis.trend_takip import _atr

        r = self.db.query("SELECT venue FROM instruments WHERE id = ?",
                          (instrument_id,))
        venue = r[0]["venue"] if r else None
        seri = [dict(x) for x in self.db.fiyat_serisi(instrument_id, 120)]
        # SERMAYE ISLEMI KAPISI: bolunmeyi asan bir ATR, kagidin gunluk
        # salinimi degil bolunmenin buyuklugudur ve stop'u ABSURT genis
        # yapar. Son kesintisiz segmentte calis (bkz. A3, 2026-08-21).
        seri, _ = son_kesintisiz(seri, borsa_limiti(venue))
        if len(seri) < ASGARI_BAR:
            return None
        yas = _yas_gun(seri[-1]["ts"])
        if yas is not None and yas > AZAMI_BAYATLIK_GUN:
            return None                      # bayat seriyle alarm kurulmaz
        n = _atr(seri, len(seri) - 1)
        kapanis = seri[-1]["close"]
        if not n or n <= 0 or not kapanis or kapanis <= 0:
            return None
        return {"kapanis": kapanis, "n": n, "stop": kapanis - STOP_N * n,
                "para_birimi": seri[-1].get("currency"),
                "bar_ts": str(seri[-1]["ts"])[:10]}

    # ------------------------------------------------------------------
    def guncelle(self, sahip: str) -> dict:
        """
        Sahibin pozisyonlari icin seviyeleri kurar/yukseltir.
        MESAJ GONDERMEZ, alarm URETMEZ — yalnizca seviye bakimi.

        Doner: {"kurulan", "yukseltilen", "yeniden_kurulan", "atlanan"}
        """
        if not sahip:
            raise ValueError("Koruma.guncelle: sahip zorunlu")
        rapor = {"kurulan": 0, "yukseltilen": 0, "yeniden_kurulan": 0,
                 "atlanan": []}
        mevcut = {(r["hesap"], r["instrument_id"]): r for r in self.db.query(
            "SELECT * FROM koruma WHERE sahip = ?", (sahip,))}
        simdi = _bugun_iso()
        yazim = []

        for hesap in self.db.hesaplar(sahip):
            for p in self.db.latest_positions(hesap, sahip):
                iid = p["instrument_id"]
                if (p["quantity"] or 0) <= 0:
                    continue
                s = self._seviye_hesapla(iid)
                if not s:
                    rapor["atlanan"].append(p["symbol"])
                    continue
                eski = mevcut.get((hesap, iid))
                if eski is None:
                    yazim.append((sahip, hesap, iid, simdi, simdi,
                                  s["kapanis"], s["n"], s["stop"],
                                  s["para_birimi"], None))
                    rapor["kurulan"] += 1
                    continue
                if eski["bozuldu_ts"]:
                    # TOPARLANMA: kirilan seviye ancak fiyat esigin
                    # belirgin ustune dondugunde yeniden kurulur.
                    if s["kapanis"] > eski["stop"] * (1 + TOPARLANMA_PAYI):
                        yazim.append((sahip, hesap, iid, eski["kuruldu_ts"],
                                      simdi, s["kapanis"], s["n"], s["stop"],
                                      s["para_birimi"], None))
                        rapor["yeniden_kurulan"] += 1
                    continue
                # RATCHET: yalnizca yukari.
                if s["stop"] > eski["stop"]:
                    yazim.append((sahip, hesap, iid, eski["kuruldu_ts"], simdi,
                                  s["kapanis"], s["n"], s["stop"],
                                  s["para_birimi"], None))
                    rapor["yukseltilen"] += 1

        if yazim:
            with self.db.tx() as c:
                c.executemany(
                    """INSERT INTO koruma (sahip, hesap, instrument_id,
                         kuruldu_ts, guncellendi_ts, referans_fiyat, n, stop,
                         para_birimi, bozuldu_ts)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(sahip, hesap, instrument_id) DO UPDATE SET
                         guncellendi_ts=excluded.guncellendi_ts,
                         referans_fiyat=excluded.referans_fiyat,
                         n=excluded.n, stop=excluded.stop,
                         para_birimi=excluded.para_birimi,
                         bozuldu_ts=excluded.bozuldu_ts""", yazim)
        return rapor

    # ------------------------------------------------------------------
    def kontrol(self, sahip: str) -> list[dict]:
        """
        KIRILAN seviyeler. DAMGALAMAZ — `damgala()` teslimattan SONRA.

        Sira sozlesmesi tez alarmiyla AYNI ve ayni sebeple: 2026-08-21
        sabahinda damga teslimattan once atildigi icin ROSE'un alarmi
        kalici olarak kayboldu. Tespit -> TESLIMAT -> damga.
        """
        if not sahip:
            raise ValueError("Koruma.kontrol: sahip zorunlu")
        out = []
        for r in self.db.query(
                """SELECT k.*, i.symbol, i.venue FROM koruma k
                   JOIN instruments i ON i.id = k.instrument_id
                   WHERE k.sahip = ? AND k.bozuldu_ts IS NULL""", (sahip,)):
            s = self._seviye_hesapla(r["instrument_id"])
            if not s:
                continue                      # bayat/eksik seri: ilan yok
            if s["kapanis"] >= r["stop"]:
                continue
            out.append({
                "sahip": sahip, "hesap": r["hesap"],
                "instrument_id": r["instrument_id"], "sembol": r["symbol"],
                "stop": r["stop"], "kapanis": s["kapanis"], "n": r["n"],
                "para_birimi": r["para_birimi"], "bar_ts": s["bar_ts"],
                "mesafe_pct": (s["kapanis"] / r["stop"] - 1) * 100,
                "kuruldu_ts": r["kuruldu_ts"],
            })
        if out:
            log.info("[koruma] stop kirildi (HENUZ DAMGALANMADI): %s",
                     [x["sembol"] for x in out])
        return out

    def damgala(self, kayitlar: list[dict]) -> int:
        """Teslim edilmis kirilimlari isaretler. Yalnizca teslimat sonrasi."""
        if not kayitlar:
            return 0
        simdi = _bugun_iso()
        with self.db.tx() as c:
            c.executemany(
                """UPDATE koruma SET bozuldu_ts = ?
                   WHERE sahip = ? AND hesap = ? AND instrument_id = ?
                     AND bozuldu_ts IS NULL""",
                [(simdi, k["sahip"], k["hesap"], k["instrument_id"])
                 for k in kayitlar])
        log.info("[koruma] kirilim teslim edildi ve damgalandi: %s",
                 [k["sembol"] for k in kayitlar])
        return len(kayitlar)

    # ------------------------------------------------------------------
    def ozet(self, sahip: str) -> list[dict]:
        """Aktif seviyeler — `/koruma` komutu ve sohbet araci icin."""
        out = []
        for r in self.db.query(
                """SELECT k.*, i.symbol FROM koruma k
                   JOIN instruments i ON i.id = k.instrument_id
                   WHERE k.sahip = ? ORDER BY i.symbol""", (sahip,)):
            s = self._seviye_hesapla(r["instrument_id"])
            out.append({
                "sembol": r["symbol"], "hesap": r["hesap"],
                "stop": round(r["stop"], 6), "n": round(r["n"], 6),
                "para_birimi": r["para_birimi"],
                "guncel_kapanis": round(s["kapanis"], 6) if s else None,
                # MESAFE YUZDE OLARAK: seri para birimi pozisyonunkinden
                # farkli olabilir (TSLA serisi USD, pozisyon EUR) ve
                # yuzde mesafe para biriminden BAGIMSIZDIR.
                "mesafe_pct": (round((s["kapanis"] / r["stop"] - 1) * 100, 1)
                               if s and r["stop"] else None),
                "bozuldu_ts": r["bozuldu_ts"],
            })
        return out
