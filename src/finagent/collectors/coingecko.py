"""
CoinGecko — kriptonun "temel veri" karsiligi ve kimlik cozumu.

KRIPTODA TEMEL ANALIZ NE DEGILDIR
---------------------------------
Hissede temel analiz = kar, marj, nakit akisi (SEC/XBRL). Kriptoda BUNLARIN
KARSILIGI YOKTUR. Coin'in ne cirosu ne kari vardir; F/K, ROE, marj gibi
oranlar TANIMSIZDIR. Bu modulun topladigi sey temel analiz degil,
TOKENOMIKTIR:

    piyasa degeri            — dolasimdaki arz x fiyat
    tam seyreltilmis deger   — toplam arz x fiyat (FDV)
    dolasimdaki / toplam arz — kilitli arzin ne kadari acilacak
    ATH / ATL uzakligi       — tarihsel konum
    hacim / piyasa degeri    — likidite ve devir hizi

Bunlar isletme performansi degil, ARZ VE FIYATLAMA yapisidir. Sistem promptu
bu ayrimi acikca yapmak zorunda; yoksa model "NVDA'nin marji" der gibi
"BTC'nin marji" demeye calisir.

ANAHTAR GEREKMIYOR
------------------
Ucretsiz uc, kayit yok (2026-08-15'te olculdu). Dakikada ~30 istek siniri
var, bu yuzden tum semboller TEK istekte toplu cekiliyor.

`fundamentals` TABLOSUNA NEDEN YAZILIYOR
---------------------------------------
Tablo zaten "kavram + deger + donem" seklinde genel. Tokenomik degerleri
ANLIK olculerdir (donem uzunlugu yok) -> `days IS NULL`. Mevcut
`finansal_seri(donem="anlik")` bunlari dogrudan okuyabiliyor; ayri tablo
acmak ayni seyi ikinci kez modellemek olurdu.
"""
from __future__ import annotations

import logging

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

CG_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"

# CoinGecko alani -> (bizim kavram adi, birim)
KAVRAMLAR = {
    "current_price":                    ("Fiyat", "USD"),
    "market_cap":                       ("PiyasaDegeri", "USD"),
    "fully_diluted_valuation":          ("TamSeyreltilmisDeger", "USD"),
    "total_volume":                     ("Hacim24s", "USD"),
    "circulating_supply":               ("DolasimdakiArz", "adet"),
    "total_supply":                     ("ToplamArz", "adet"),
    "max_supply":                       ("AzamiArz", "adet"),
    "ath":                              ("TarihiZirve", "USD"),
    "ath_change_percentage":            ("ZirveyeUzaklik", "%"),
    "atl":                              ("TarihiDip", "USD"),
    "market_cap_rank":                  ("PiyasaDegeriSiralamasi", "sira"),
    "price_change_percentage_24h":      ("Degisim24s", "%"),
}


class CoinGeckoCollector(BaseCollector):
    name = "coingecko"
    needs_browser = False

    def collect(self) -> CollectorResult:
        import httpx

        hedefler = self.db.research_targets(kripto=True)
        if not hedefler:
            return CollectorResult(self.name, "skipped", 0, "kripto hedefi yok")

        kimlikler = {r["symbol"]: r for r in self.db.identities()}
        eslesme: dict[str, int] = {}          # coingecko_id -> instrument_id
        atlanan = []
        for h in hedefler:
            k = kimlikler.get(h["symbol"])
            # OLCUT `coingecko_id`, `status` DEGIL.
            #
            # Kapi eskiden `status == 'dogrulandi'` istiyordu. Ama
            # 'dogrulandi' BINANCE CIFTININ varligini anlatiyor —
            # CoinGecko kaydiyla ilgisi yok. Referans coinler (venue
            # 'CRYPTO': HYPE, XMR, CRO, KAS, OKB...) tanimi geregi
            # `cift_yok` ve bu kapidan hep dusuyorlardi. Olculdu
            # (2026-08-20): 24 referans coinin tokenomigi BUTUNUYLE
            # eksikti — evrenin varolus sebebi olan katman bostu.
            #
            # `coingecko_id` zaten AD DOGRULAMASINDAN gecerek yaziliyor
            # (bkz. `CryptoResolver.coz`), yani id'nin varligi
            # dogrulamanin ta kendisi. `eslesmedi` olan bir kayda id
            # HIC yazilmadigi icin bu kapi daha gevsek degil, daha
            # DOGRU: klon-coin hala giremiyor.
            cg = (k["coingecko_id"]
                  if k is not None and "coingecko_id" in k.keys() else None)
            if not cg:
                durum = k["status"] if k is not None else None
                # fiat ve stabilcoin BEKLENEN atlamalardir, ariza degil:
                # EUR bir coin degil, USDT'nin tokenomigi anlamsiz.
                if durum not in ("fiat", "stabil"):
                    atlanan.append(f"{h['symbol']}({durum or 'kimliksiz'})")
                continue
            eslesme[cg] = h["id"]

        if not eslesme:
            return CollectorResult(self.name, "skipped", 0,
                                   "dogrulanmis kripto kimligi yok")

        with httpx.Client(timeout=45, follow_redirects=True,
                          headers={"User-Agent": "finagent/1.0"}) as http:
            r = http.get(CG_MARKETS, params={
                "vs_currency": "usd",
                "ids": ",".join(sorted(eslesme)),
                "order": "market_cap_desc",
                "per_page": 250,
            })
            r.raise_for_status()
            coinler = r.json() or []

        toplam = 0
        for c in coinler:
            iid = eslesme.get(c.get("id"))
            if iid is None:
                continue
            # `last_updated` CoinGecko'nun olcum ani; kendi saatimizi degil
            # KAYNAGIN zaman damgasini kullaniyoruz.
            an = (c.get("last_updated") or "")[:10] or None
            # upsert_fundamentals KONUMSAL demet bekliyor:
            # (instrument_id, concept, unit, period_start, period_end, days,
            #  val, form, fy, fp, frame, filed, accn)
            # days=None -> ANLIK olcum (donem uzunlugu yok), tokenomikte dogru.
            satirlar = [
                (iid, kavram, birim, None, an, None, float(c[alan]),
                 "coingecko", None, None, None, an, c.get("id"))
                for alan, (kavram, birim) in KAVRAMLAR.items()
                if c.get(alan) is not None
            ]
            toplam += self.db.upsert_fundamentals(satirlar)

        notlar = f"{len(coinler)} coin"
        if atlanan:
            notlar += f" · kimlik yok, atlandi: {', '.join(atlanan[:8])}"
        return CollectorResult(self.name, "partial" if atlanan else "ok",
                               toplam, notlar)
