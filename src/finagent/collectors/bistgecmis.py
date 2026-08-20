"""
BIST DERIN GECMISI — backtest'in on kosulu.

NEDEN AYRI BIR COLLECTOR
------------------------
`isyatirim` BIST'in BIRINCIL kaynagi ve oyle kalmali: fiyatin yaninda
hisse sayisi, gunluk TL hacim ve XU100 endeksini de getiriyor — yan
urunlerin hicbiri baska yerde yok. Ama DERINLIGI dar: olculdu
2026-08-20, 365 sembolde ~287 bar, yani 13,5 ay.

13,5 ay TEK bir makro rejimdir. Bir stratejiyi sinamak icin en az IKI
farkli rejim gerekir (2018 TL krizi, 2020 COVID, 2021-22 enflasyon ve
faiz dongusu, 2023-24 normallesme). Bu yuzden backtest bu projede
"acik madde" olarak duruyordu — veri yoktu.

Yahoo ayni kagitlari `.IS` sonekiyle ve COK daha derin veriyor:
DEVA 6.756 bar (2000'den), THYAO 6.756, KGYO 6.757, TRALT 4.238.
Fiyatlar isyatirim ile BIREBIR ayni — yedi sembolde de %0,00 fark
olculdu, yani bu bir "ikinci gorus" degil AYNI serinin uzunu.

`.IS` SONEKI AMBIGU DEGIL: sade "DEVA" Yahoo'da baska sirkete denk
gelebilir (AVTX ve RBOT'ta tam bu oldu) ama "DEVA.IS" tek bir
kotasyonu gosterir — dogrulandi, ad da tutuyor (DEVA HOLDING).

NEDEN GUNLUK KOSUYOR, TEK SEFERLIK DEGIL
----------------------------------------
`fiyat_kaynagi()` ayni para birimindeki kaynaklar arasinda EN TAZE
olani seciyor, esitlikte en cok barli. Tek seferlik bir dolum ertesi
gun bayatlar ve `isyatirim` (gunluk tazelenen) secilir — yani 6.756
barlik seri yazilir ama HIC KULLANILMAZ. Gunluk kosunca hem taze hem
derin olur ve secim dogal olarak buna duser.

Maliyeti kucuk: 365 sembol tek `download` cagrisinda ~26 saniye.
"""
from __future__ import annotations

import logging

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

# Yahoo'nun BIST soneki.
SONEK = ".IS"

# `prices` birincil anahtari (instrument_id, ts, source) ve para birimi
# ANAHTARDA YOK. Ayri kaynak adi sart: `isyatirim` ile ayni ada yazmak
# onun serisini EZERDI ve yan urunleri (hacim, hisse sayisi) uretmeyen
# bir seri onun yerine gecerdi.
KAYNAK = "yahoo_bist"


class BistGecmisCollector(BaseCollector):
    name = "bistgecmis"
    needs_browser = False

    def collect(self) -> CollectorResult:
        import warnings

        import yfinance as yf

        # yfinance'in kendi gurultusu: kapanan/degisen kodlarda ERROR
        # basiyor ve bunlar bizde zaten `atlanan` olarak raporlaniyor.
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        warnings.filterwarnings("ignore")

        semboller = self._semboller()
        if not semboller:
            return CollectorResult(self.name, "skipped", 0, "BIST evreni bos")

        period = self.s.get("sources.bistgecmis.period", "10y")
        kisa = self.s.get("sources.bistgecmis.gunluk_period", "5d")
        parca_boy = int(self.s.get("sources.bistgecmis.parca", 120))

        # DERIN DOLUM BIR KEZ, TAZELEME HER GUN.
        #
        # Ilk kosu 365 sembol icin 595.713 bar yaziyor ve 87 saniye
        # suruyor. Bunu her gece tekrarlamak 595 bin satirlik bosa
        # upsert demekti — derin gecmis DEGISMIYOR, yalnizca son bar
        # ekleniyor. Gecmisi ZATEN dolu olan sembol icin son birkac gun
        # cekilir; eksik olan icin tam period.
        #
        # Olcut bar sayisi DEGIL, ILK BARIN TARIHI: yeni kote edilmis
        # bir kagitta (MASFN 30 Tem 2026, QUICK 6 Agu 2026) bar sayisi
        # kalici olarak dusuktur ve sayiya bakan bir olcut onlari
        # SONSUZA KADAR tam cekerdi. Gecmis eksik degil, YOK.
        mevcut = {}
        for r in self.db.query(
                """SELECT i.symbol s, MIN(p.ts) ilk, MAX(p.ts) son
                   FROM prices p JOIN instruments i ON i.id = p.instrument_id
                   WHERE i.venue = 'BIST' AND p.source = ?
                   GROUP BY i.symbol""", (KAYNAK,)):
            mevcut[r["s"]] = (r["ilk"], r["son"])

        derin = [s for s in semboller if s not in mevcut]
        taze = [s for s in semboller if s in mevcut]
        log.info("[%s] %d sembol derin dolum, %d sembol tazeleme",
                 self.name, len(derin), len(taze))

        # IKI AYRI KATEGORI, BILEREK.
        #
        # `yok`  — sembolu Yahoo TASIMIYOR. Ariza degil, KAPSAM. DMLKTG
        #          boyle: gercek bir BIST kodu (Midas'ta bari var) ama
        #          `DMLKTG.IS` Yahoo'da yok. Bunu `partial` saymak,
        #          collector'i HER KOSUDA partial yapardi ve bekcinin
        #          besinci olcutu surekli calardi — yani bugun dort
        #          collector'da kapatilan SAHTE ALARM sinifini burada
        #          yeniden acardi.
        # `hata` — parca duzeyinde istisna (ag, API). GERCEK ariza.
        #
        # SESSIZ ATLAMA YOK: ikisi de nota yaziliyor. Ve `yok` orani
        # esigi asarsa bu artik "birkac kod" degil YAPISAL bir sorundur
        # (Yahoo BIST'i kesti, sonek degisti) — o zaman partial doner.
        toplam, yok, hata = 0, [], []
        for grup, p in ((derin, period), (taze, kisa)):
            for i in range(0, len(grup), parca_boy):
                parca = grup[i:i + parca_boy]
                kodlar = [f"{s}{SONEK}" for s in parca]
                try:
                    df = yf.download(kodlar, period=p, interval="1d",
                                     group_by="ticker", progress=False,
                                     auto_adjust=False, threads=True)
                except Exception as e:                # noqa: BLE001
                    # PARCANIN HATASI DIGERLERINI DUSURMEZ.
                    log.warning("[%s] parca alinamadi (%d sembol): %s",
                                self.name, len(parca), e)
                    hata += parca
                    continue
                for sembol, kod in zip(parca, kodlar):
                    try:
                        n = self._yaz(df, sembol, kod)
                    except Exception as e:            # noqa: BLE001
                        log.debug("[%s] %s yazilamadi: %s", self.name, sembol, e)
                        hata.append(sembol)
                        continue
                    if n:
                        toplam += n
                    else:
                        yok.append(sembol)

        esik = float(self.s.get("sources.bistgecmis.azami_kayip_orani", 0.10))
        yapisal = len(yok) > max(1, int(len(semboller) * esik))

        notlar = [f"derin {len(derin)} · taze {len(taze)}"]
        if yok:
            notlar.append(f"Yahoo'da yok ({len(yok)}): " + ", ".join(yok[:8]))
        if hata:
            notlar.append(f"HATA ({len(hata)}): " + ", ".join(hata[:8]))
        if not toplam:
            durum = "error"
        elif hata or yapisal:
            durum = "partial"
        else:
            durum = "ok"
        return CollectorResult(self.name, durum, toplam, " · ".join(notlar))

    # ------------------------------------------------------------------
    def _semboller(self) -> list[str]:
        """
        BIST evreni — `isyatirim` ile AYNI liste.

        TEK TANIM: evreni burada yeniden kurmak, iki yerde beyan edilen
        gercegin ayrismasi demekti (likidite esigi bir tarafta
        guncellenir, digeri bayatlar). Bu projenin tekrar eden kusur
        sinifi tam olarak odur.
        """
        from .isyatirim import IsYatirimCollector

        return IsYatirimCollector(self.s, self.db)._semboller()

    def _yaz(self, df, sembol: str, kod: str) -> int:
        if kod not in df.columns.get_level_values(0):
            return 0
        alt = df[kod]
        satirlar = []
        for idx, r in alt.iterrows():
            kapanis = r.get("Close")
            # Kapanissiz bar teknik gostergeyi bozar — yazma.
            if kapanis is None or kapanis != kapanis:
                continue

            def _s(alan, _r=r):
                v = _r.get(alan)
                return None if v is None or v != v else float(v)

            satirlar.append({
                "ts": idx.date().isoformat(),
                "open": _s("Open"), "high": _s("High"), "low": _s("Low"),
                "close": float(kapanis), "volume": _s("Volume"),
            })
        if not satirlar:
            return 0
        # `venue='BIST'` SART: araci kurum bir piyasa degildir ve ayni
        # kagit icin ikinci bir enstruman acmak degerlemeyi korlestirir.
        iid = self.db.upsert_instrument(sembol, "BIST", asset_type="equity",
                                        currency="TRY")
        return self.db.upsert_prices(iid, satirlar, KAYNAK, currency="TRY")
