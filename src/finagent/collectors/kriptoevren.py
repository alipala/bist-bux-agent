"""
Kripto EVREN kurucusu — analiz edilecek coin listesini belirler.

Hisse tarafinda bu isi `indices` (endeks uyeleri) ve `bist` (KAP listesi)
yapiyor. Kriptoda resmi bir "endeks uyeligi" yok; en yakin karsilik
PIYASA DEGERI SIRALAMASI. Bu collector ilk N coin'i alip Binance'te
gercekten islem goren ve analiz edilebilir olanlari izleme listesine
yaziyor. Sonrasini mevcut zincir hallediyor:

    kriptoevren -> kripto (kimlik) -> binance (fiyat) -> coingecko (tokenomik)

UC SUZGEC, UCU DE KAYNAKTAN — TAHMINDEN DEGIL
---------------------------------------------
1. BINANCE'TE ISLEM GORUYOR MU: `exchangeInfo` icinden USDT spot
   paritesi, `status=TRADING` ve `isSpotTradingAllowed`. Sembolun
   Binance'te oldugunu VARSAYMAK yerine borsanin kendi listesine
   bakiliyor. Ilk 100'un yalnizca 50'si bu testi geciyor: LEO, OKB,
   CRO, KCS, GT, WBT, BGB, HTX borsa tokenlari kendi borsalarinda;
   BUIDL, USYC, USTB, JTRSY tokenlastirilmis fon; XMR, KAS, PI,
   XDC, FLR ise Binance'te listelenmemis.

2. STABLECOIN MI: CoinGecko'nun kendi `stablecoins` KATEGORISI
   kullaniliyor, elle yazilmis bir liste degil. Sebep: "adi USD ile
   bitenler" gibi bir kural DAI, GHO, BOLD, FRAX, U'yu kacirir ve
   USD1/USDe gibi yenileri her ay elle eklemek gerekirdi. Stablecoin'de
   teknik analiz anlamsizdir — RSI, SMA, trend hepsi 1,00 dolayinda
   gurultu uretir ve tarayiciyi doldurur.

3. GERCEK BIR PIYASASI VAR MI: 24 saatlik hacim esigi. BIST tarafindaki
   likidite suzgeciyle ayni gerekce — esigin altinda %5'lik hareket
   bilgi degil tek bir emrin izidir.

FAVORILER VE POZISYONLAR HER ZAMAN ICERIDE
------------------------------------------
Ilk N'e girmese de kullanicinin favorisi/pozisyonu taranir. ROSE (#474)
ve ENJ ilk 100'de degil ama portfoyde/favoride; onlari elemek "senin
paranin durdugu yeri analiz etmiyorum" demek olurdu.
"""
from __future__ import annotations

import logging

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

MARKETS = "https://api.coingecko.com/api/v3/coins/markets"
EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"

VARSAYILAN_N = 100
VARSAYILAN_HACIM = 5_000_000        # USD, 24 saat — ALINABILIR coin icin
VARSAYILAN_DEVIR = 0.10             # %, 24s hacim / piyasa degeri — REFERANS icin


class KriptoEvrenCollector(BaseCollector):
    name = "kriptoevren"
    needs_browser = False

    def collect(self) -> CollectorResult:
        import httpx

        n = int(self.s.get("sources.kriptoevren.top_n", VARSAYILAN_N))
        hacim_esigi = float(self.s.get("sources.kriptoevren.min_volume_usd",
                                       VARSAYILAN_HACIM))
        devir_esigi = float(self.s.get("sources.kriptoevren.min_turnover_pct",
                                       VARSAYILAN_DEVIR))

        with httpx.Client(timeout=45, follow_redirects=True,
                          headers={"User-Agent": "finagent/1.0"}) as http:
            piyasa = self._ilk_n(http, n)
            if not piyasa:
                return CollectorResult(self.name, "error", 0,
                                       "CoinGecko ilk N alinamadi")
            stabil = self._stablecoinler(http)
            spot = self._binance_spot(http)

        if not spot:
            return CollectorResult(self.name, "error", 0,
                                   "Binance exchangeInfo alinamadi")

        # STABLECOIN SUZGECI ALINAMADIYSA HIC YAZMA.
        #
        # Once "suzgec devre disi kalsin, elenmeyen stablecoin gorunur"
        # diye tasarlanmisti. YANLISTI ve sahada goruldu (2026-08-16, hiz
        # siniri): 13 stablecoin izleme listesine girdi. Gerekcenin
        # kacirdigi sey YAZMANIN KALICI olmasi — bir kez giren coin
        # kaliyor, ustelik budama yalnizca ilk N DISINA dusenleri
        # temizliyor ve stablecoin'ler tam da ilk 100'un icinde.
        # Yani "gorunur olur, fark edilir" degil, "sessizce kalir".
        #
        # Eksik suzgecle yazmaktansa HIC yazmamak dogru: evren zaten
        # dunden duruyor, bir gun tazelenmemek zarar vermez.
        if not stabil:
            return CollectorResult(self.name, "error", 0,
                                   "stablecoin kategorisi alinamadi — evren "
                                   "YAZILMADI (eksik suzgecle yazmak "
                                   "listeye kalici stablecoin sokar)")

        from ..storage.db import sembol_gecersiz

        eklenen, referans = [], []
        elenen = {"stablecoin": 0, "hacim": 0, "gecersiz_sembol": 0}
        for c in piyasa:
            sem = (c.get("symbol") or "").upper()
            if not sem:
                continue
            # BICIM DISI SEMBOL EVRENE GIRMEZ. `币安人生` (BinanceLife)
            # tam buradan girdi ve sonrasinda her `kripto` toplamasi
            # URL'ye kodlanan bu sembol yuzunden 400 aldi — TEK satir,
            # kimlik zincirinin TAMAMINI durdurdu.
            neden = sembol_gecersiz(sem)
            if neden:
                elenen["gecersiz_sembol"] += 1
                log.warning("[kriptoevren] sembol atlandi %r: %s", sem, neden)
                continue
            if sem in stabil:
                elenen["stablecoin"] += 1
                continue
            alinabilir = sem in spot
            if not self._piyasasi_canli(c, alinabilir, hacim_esigi, devir_esigi):
                elenen["hacim"] += 1
                continue

            venue = "BINANCE" if alinabilir else "CRYPTO"
            iid = self.db.upsert_instrument(
                sem, venue, c.get("name"), "crypto",
                "USDT" if alinabilir else "USD")
            self.db.query(
                "INSERT OR IGNORE INTO watchlist (instrument_id, kind, note) "
                "VALUES (?,?,?)",
                (iid, "evren" if alinabilir else "referans",
                 f"CoinGecko ilk {n} · sira {c.get('market_cap_rank')}"
                 + ("" if alinabilir else " · Binance'te YOK, referans")))
            (eklenen if alinabilir else referans).append(sem)

            # REFERANS COIN'IN KIMLIGI BURADA YAZILIR. `kripto` collector'i
            # Binance ciftini arar ve bulamayinca "cift_yok" der; oysa bu
            # coin'in fiyati CoinGecko'dan gelecek, ihtiyaci olan tek sey
            # coingecko_id — ve o zaten bu yanitin icinde geliyor.
            if not alinabilir and c.get("id"):
                self.db.query(
                    """INSERT INTO identities
                       (instrument_id, sec_ticker, sec_name, exchange, status,
                        method, note, coingecko_id, resolved_at)
                       VALUES (?,?,?,'COINGECKO','dogrulandi','kriptoevren',?,?,
                               datetime('now'))
                       ON CONFLICT(instrument_id) DO UPDATE SET
                         sec_name=excluded.sec_name, status='dogrulandi',
                         coingecko_id=excluded.coingecko_id,
                         note=excluded.note, resolved_at=excluded.resolved_at""",
                    (iid, sem, c.get("name"),
                     f"ilk {n} sirasi {c.get('market_cap_rank')}, "
                     "Binance'te listelenmemis", c["id"]))

        dusen = self._ilk_n_disina_dusenler(eklenen + referans)
        self.db._conn.commit()

        notlar = (f"alinabilir {len(eklenen)} · referans {len(referans)} · "
                  f"elendi: stablecoin {elenen['stablecoin']}, "
                  f"hacim<{hacim_esigi:,.0f}$ {elenen['hacim']}")
        if dusen:
            notlar += f" · ilk {n} disina dusen: {', '.join(dusen)}"
        return CollectorResult(self.name, "ok" if eklenen else "partial",
                               len(eklenen) + len(referans), notlar)

    # ------------------------------------------------------------------
    @staticmethod
    def _piyasasi_canli(c: dict, alinabilir: bool, hacim_esigi: float,
                        devir_esigi: float) -> bool:
        """
        Iki farkli esik, cunku iki farkli soru soruluyor.

        ALINABILIR coin'de soru "bu emri gecirebilir miyim" — cevabi MUTLAK
        hacim belirler. 5M$'lik bir piyasada 500$'lik emir kaybolur, 500K$'lik
        piyasada fiyati kendisi oynatir.

        REFERANS coin'de emir gecirmiyoruz; soru "bu fiyat gercek mi". Onu
        DEVIR HIZI (24s hacim / piyasa degeri) soyler. Mutlak esik burada
        yanlis olcer, cunku piyasa degerleri uc buyukluk mertebesine
        yayiliyor. Olculdu 2026-08-16: LEO 8,6 mlr $ degerinde ama gunde
        521 bin $ islem goruyor (%0,006 devir) — buyuk bir sayi, piyasa
        degil; tokenlastirilmis fonlarin (BUIDL, USYC, JAAA) hacmi tam
        olarak SIFIR. KAS ise 741 mn $ ile daha kucuk ama %0,649 devirle
        yuz kat daha canli. Karsilastirma icin: BTC gunde %2-5 doner.
        """
        hacim = float(c.get("total_volume") or 0)
        if alinabilir:
            return hacim >= hacim_esigi
        pd = float(c.get("market_cap") or 0)
        return pd > 0 and (hacim / pd * 100) >= devir_esigi

    def _ilk_n_disina_dusenler(self, guncel: list[str]) -> list[str]:
        """
        Ilk N'den dusen coin'i izlemeden cikarir — BIST'te endeks uyeligi
        degistiginde olanin aynisi. Cikarmazsak liste yalnizca BUYUR:
        bir kez ilk 100'e girmis her coin sonsuza dek her gun cekilir.

        UC KORUMA: yalnizca `kind='evren'` satirlari silinir, yani
        kullanicinin elle ekledigi favori (`aday`) ve portfoyde pozisyonu
        olan coin DOKUNULMAZ. Gecmis barlar da SILINMEZ — coin yalnizca
        tazelenmeyi birakir, bugune kadar biriken serisi analiz icin
        yerinde kalir.
        """
        if not guncel:
            return []
        isaret = ",".join("?" * len(guncel))
        satirlar = self.db.query(
            f"""SELECT i.id, i.symbol FROM watchlist w
                JOIN instruments i ON i.id = w.instrument_id
                WHERE w.kind IN ('evren','referans')
                  AND i.venue IN ('BINANCE','CRYPTO')
                  AND i.symbol NOT IN ({isaret})
                  AND NOT EXISTS (SELECT 1 FROM positions p
                                  WHERE p.instrument_id = i.id)""",
            tuple(guncel))
        for r in satirlar:
            self.db.query("DELETE FROM watchlist WHERE instrument_id = ?",
                          (r["id"],))
        return [r["symbol"] for r in satirlar]
    @staticmethod
    def _ilk_n(http, n: int) -> list[dict]:
        out: list[dict] = []
        for sayfa in range(1, (n // 250) + 2):
            r = http.get(MARKETS, params={
                "vs_currency": "usd", "order": "market_cap_desc",
                "per_page": min(250, n - len(out)), "page": sayfa})
            r.raise_for_status()
            parca = r.json() or []
            out.extend(parca)
            if len(parca) < 250 or len(out) >= n:
                break
        return out[:n]

    @staticmethod
    def _stablecoinler(http) -> set[str]:
        """
        CoinGecko'nun `stablecoins` kategorisi. Kategori alinamazsa BOS
        DONER ve suzgec devre disi kalir — yanlis bir listeyle eleme
        yapmaktansa elememek dogru: elenen coin sessizce kaybolurdu,
        elenmeyen stablecoin ise tarayicida gorunur ve fark edilir.
        """
        try:
            r = http.get(MARKETS, params={
                "vs_currency": "usd", "category": "stablecoins",
                "per_page": 250, "page": 1})
            r.raise_for_status()
            return {(c.get("symbol") or "").upper() for c in (r.json() or [])}
        except Exception as e:                        # noqa: BLE001
            log.warning("[kriptoevren] stablecoin kategorisi alinamadi: %s", e)
            return set()

    @staticmethod
    def _binance_spot(http) -> set[str]:
        try:
            r = http.get(EXCHANGE_INFO)
            r.raise_for_status()
            return {s["baseAsset"] for s in (r.json().get("symbols") or [])
                    if s.get("quoteAsset") == "USDT"
                    and s.get("status") == "TRADING"
                    and s.get("isSpotTradingAllowed")}
        except Exception as e:                        # noqa: BLE001
            log.warning("[kriptoevren] exchangeInfo alinamadi: %s", e)
            return set()
