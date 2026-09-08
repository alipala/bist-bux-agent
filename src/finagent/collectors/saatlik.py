"""
SAATLIK HISSE SERISI — gun ici katmanin veri temeli.

NEDEN VAR
---------
Saatlik bar bugune kadar YALNIZCA kriptoda vardi (Binance klines).
Hisse tarafinda en ince cozunurluk GUNLUK kapanisti, yani "bugun gun
icinde ne oldu" sorusunun cevabi yoktu ve bir koruma seviyesi ancak
ertesi gunun kapanisiyla kontrol edilebiliyordu.

KAPSAM: BIST (`.IS`, TRY) ve ABD (ham sembol, USD). Avrupa kotasyonlari
(ASML/ADYEN/INGA gibi `.AS`) BILEREK DISARIDA — gunluk ritimde kaliyorlar.
Kapsami genisletmek sembol eslemesi degil, DOGRULAMA isi: her yeni
kotasyon icin para biriminin gunluk seriyle esleştigini gostermek gerekir.

PARA BIRIMI GUNLUK SERIYLE ESLESMEK ZORUNDA
-------------------------------------------
Bu katmanin en tehlikeli hata bicimi, saatlik seriyi gunlukten FARKLI
bir para biriminde yazmak olurdu: iki seri yan yana konunca (or. gun ici
hareketi gunluk stop seviyesiyle karsilastirirken) sayilar sessizce
yanlis cikar. Gunluk tarafta bu tam olarak yasandi — TSLA'nin serisinde
4,07 EUR ile 489,88 USD yan yana duruyordu.

Bu yuzden her sembol icin beklenen para birimi `db.fiyat_kaynagi()`den
okunuyor ve UYUSMAZSA SEMBOL ATLANIYOR. Eksik saatlik seri, yanlis
saatlik seriden iyidir.

ZAMAN DAMGASI UTC VE DAKIKA KORUNUYOR
-------------------------------------
yfinance barlari BORSA yerel saatinde ve tz-bilincli donduruyor
(GARAN.IS -> Europe/Istanbul, NVDA -> America/New_York). UTC'ye
cevriliyor. Dakika KIRPILMIYOR: BIST'in 60 dakikalik barlari yerel
09:30/10:30/11:30, yani UTC 06:30/07:30/08:30 — `%H:00` ile yazsaydik
ucu de :00'a kirpilir ve bar 30 dakika yanlis etiketlenirdi.
"""
from __future__ import annotations

import logging

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

KAYNAK = "yahoo_saatlik"

# Borsa -> (Yahoo soneki, beklenen para birimi).
#
# ELLE YAZILAN LISTE DEGIL, DOGRULANMIS ESLEME: para birimi burada
# BEKLENEN, `fiyat_kaynagi` ise GERCEK. Ikisi tutmuyorsa sembol atlanir.
BORSA = {
    "BIST": (".IS", "TRY"),
    "BUX": ("", "USD"),       # yalnizca USD kotasyonlu ABD hisseleri
}


def _utc_damga(idx) -> str | None:
    """tz-bilincli damga -> 'YYYY-MM-DD HH:MM' (UTC). Dakika korunur."""
    try:
        if idx.tzinfo is None:
            return None            # tz'siz damga hangi borsanin saati BILINMEZ
        return idx.tz_convert("UTC").strftime("%Y-%m-%d %H:%M")
    except Exception:              # noqa: BLE001
        return None


class SaatlikCollector(BaseCollector):
    name = "saatlik"
    needs_browser = False

    def collect(self) -> CollectorResult:
        import warnings

        import yfinance as yf

        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        warnings.filterwarnings("ignore")

        hedefler = self._hedefler()
        if not hedefler:
            return CollectorResult(self.name, "skipped", 0,
                                   "kapsamda saatlik hedefi yok — once "
                                   "portfoy/izleme dolmali")

        period = self.s.get("sources.saatlik.period", "1mo")
        parca_boy = int(self.s.get("sources.saatlik.parca", 40))

        toplam, yok, hata = 0, [], []
        kodlar = [h["kod"] for h in hedefler]
        kod_hedef = {h["kod"]: h for h in hedefler}

        for i in range(0, len(kodlar), parca_boy):
            parca = kodlar[i:i + parca_boy]
            try:
                # `threads=False`: threads=True her cagrida ~40 dosya
                # tanitici sizdiriyor (olculdu 2026-09-08; gerekce ve
                # sayilar `bistgecmis.py`). Bu collector kapanis ve
                # nabizda, ayni surecte bistgecmis'ten SONRA kosuyor —
                # sinir 256, ikisi birlikte asiyordu.
                df = yf.download(parca, period=period, interval="60m",
                                 group_by="ticker", progress=False,
                                 auto_adjust=False, threads=False)
            except Exception as e:                # noqa: BLE001
                # PARCANIN HATASI DIGERLERINI DUSURMEZ.
                log.warning("[%s] parca alinamadi (%d sembol): %s",
                            self.name, len(parca), e)
                hata += parca
                continue
            for kod in parca:
                h = kod_hedef[kod]
                try:
                    n = self._yaz(df, kod, h)
                except Exception as e:            # noqa: BLE001
                    log.debug("[%s] %s yazilamadi: %s", self.name, kod, e)
                    hata.append(kod)
                    continue
                if n:
                    toplam += n
                else:
                    yok.append(kod)

        notlar = [f"{len(hedefler)} sembol · period {period}"]
        if yok:
            notlar.append(f"bar donmedi ({len(yok)}): " + ", ".join(yok[:8]))
        if hata:
            notlar.append(f"HATA ({len(hata)}): " + ", ".join(hata[:8]))
        if self._atlanan:
            # SAYI DA YAZILIYOR: liste ilk 8'de kesiliyor ve yalnizca
            # ornek gosterilirse 9. atlanan sembol GORUNMEZ olur.
            # Olculdu: `SHELL.AS` tam boyle gizlendi ve ancak veriye
            # bakarak fark edildi.
            notlar.append(f"ATLANAN {len(self._atlanan)}: "
                          + ", ".join(self._atlanan[:8])
                          + (" …" if len(self._atlanan) > 8 else ""))

        # BEKLENEN ATLAMA ARIZA DEGILDIR: kapali borsada yeni bar
        # olmamasi normaldir ve `partial` uretirse bekcinin besinci
        # olcutu her kosuda calar (kalici sahte alarm, gercek arizayi
        # gomer). Yalnizca GERCEK hata ya da toptan bos donus partial.
        if not toplam and (hata or yok):
            durum = "error" if hata else "partial"
        elif hata:
            durum = "partial"
        else:
            durum = "ok"
        return CollectorResult(self.name, durum, toplam, " · ".join(notlar))

    # ------------------------------------------------------------------
    _atlanan: list[str] = []

    def _hedefler(self) -> list[dict]:
        """
        Kapsam: POZISYON ∪ IZLEME. Katalogun tamami DEGIL.

        Saatlik veri gun ici karar icin toplaniyor; sahip olunmayan ve
        izlenmeyen 1.600 enstrumanin saatlik serisi ne okunur ne
        kullanilir, yalnizca disk ve istek harcar.
        """
        self._atlanan = []
        out = []
        for venue, (sonek, beklenen) in BORSA.items():
            for r in self.db.query(
                    """SELECT i.id, i.symbol FROM instruments i
                       WHERE i.venue = ? AND (
                         i.id IN (SELECT instrument_id FROM positions)
                         OR i.id IN (SELECT instrument_id FROM watchlist))
                       ORDER BY i.symbol""", (venue,)):
                k = self.db.fiyat_kaynagi(r["id"])
                gercek = (k or {}).get("currency")
                if gercek != beklenen:
                    # SESSIZ ATLAMA YOK: EUR kotasyonlu bir BUX hissesi
                    # (ASML) burada duser ve bu KAPSAM karari, ariza degil.
                    self._atlanan.append(f"{r['symbol']}({gercek or 'seri yok'})")
                    continue
                # SONEK TASIYAN SEMBOL, ABD KOTASYONU DEGILDIR.
                #
                # OLCULDU ilk kosuda: `SHELL.AS` gunluk serisinde USD
                # (ADR) oldugu icin kapiyi gecti, ama Yahoo'da "SHELL.AS"
                # AMSTERDAM kotasyonudur ve EUR doner — 40,28 EUR, USD
                # etiketiyle yazilacakti. Gunluk tarafta ayni sinif
                # TSLA'nin serisine 4,07 EUR ile 489,88 USD'yi yan yana
                # koymustu.
                #
                # `sonek` EKLEYEN borsalarda (BIST) sorun yok: sonek
                # ZATEN kotasyonu tekillestiriyor. Sorun sonek EKLEMEYEN
                # daldadir (ABD) — orada sembolun kendi noktasi baska bir
                # borsayi isaret eder.
                if not sonek and "." in r["symbol"]:
                    self._atlanan.append(f"{r['symbol']}(sonekli sembol, "
                                         "ABD kotasyonu degil)")
                    continue
                out.append({"id": r["id"], "sembol": r["symbol"],
                            "kod": f"{r['symbol']}{sonek}",
                            "para_birimi": beklenen,
                            "gunluk_kapanis": (k or {}).get("son_kapanis")})

        # ENDEKS VEKILLERI — GUN ICI KIYASIN REFERANSI (2026-09-01).
        #
        # NEDEN AYRI DONGU: yukaridaki dongu VENUE anahtarli ve her
        # venue'ye TEK sonek/para birimi dusuyor. `INDEX` venue'su ise
        # karisik — XU100 (TRY, `.IS`), QQQ (USD, soneksiz), AEX (EUR,
        # `^` onekli). Ustelik endeksler pozisyon/izleme listesinde
        # OLMADIGI icin oradaki kapiya da takilirlar.
        #
        # NEDEN GEREKLI: `gunici_tarayici.endeks_karsilastir` "bu hareket
        # hisseye mi ozgu, piyasa geneli mi" diye soruyor ve cevabi
        # verecek referans YOKTU — endekslerin hic gun ici bari yoktu
        # (olculdu: XU100/QQQ/AEX icin 0 saatlik bar). Kiyas bu yuzden
        # her adayda "bugune ait deger yok" diyordu.
        #
        # SEMBOL ESLEMESI TEK KAYNAKTAN: `prices.ENDEKSLER`. Ikinci bir
        # eslemesi yazmak, kopyalarin zamanla ayrisma kusurunu davet
        # ederdi.
        from .prices import ENDEKSLER
        istenen = self.s.get("sources.saatlik.endeksler") or []
        for kod in istenen:
            tanim = ENDEKSLER.get(kod)
            if not tanim:
                # SESSIZ ATLAMA YOK: ayarda yazan ama eslemesi olmayan
                # bir kod, sessizce yok sayilirsa "neden referans yok"
                # sorusu cevapsiz kalir.
                self._atlanan.append(f"{kod}(endeks eslemesi yok)")
                continue
            yahoo, ad, ccy = tanim
            iid = self.db.upsert_instrument(kod, "INDEX", ad, "index", ccy)
            out.append({"id": iid, "sembol": kod, "kod": yahoo,
                        "para_birimi": ccy, "gunluk_kapanis": None})
        return out

    # Gunluk seriden izin verilen en buyuk sapma. %25 secildi (gunluk
    # `prices` kapisindaki %10'dan genis) cunku burada kiyaslanan sey
    # AYNI GUNUN kapanisi degil: saatlik son bar gun ici, gunluk son bar
    # dunun kapanisi olabilir ve arada mesru bir hareket vardir. Amac
    # gunluk gurultuyu elemek degil, YANLIS ENSTRUMANI yakalamak —
    # sertifika/baska borsa farklari kat kat buyuktur (SHELL.AS: 40 EUR
    # vs 59-94 USD, TSLA sertifikasi: 4 EUR vs 430 USD).
    SAPMA_ESIGI = 0.25

    def _makul(self, hedef: dict, kapanis: float) -> bool:
        """
        Saatlik kapanis, GUNLUK seriyle ayni buyukluk mertebesinde mi?

        Ikinci savunma hatti: sonek kontrolu bilinen bicimi yakaliyor,
        bu ise BILINMEYENI. Referans yoksa KABUL EDILIR — bu kapi
        yanlis enstrumani elemek icin, veri yoklugunu cezalandirmak icin
        degil.
        """
        r = self.db.query(
            """SELECT close FROM prices
               WHERE instrument_id = ? AND currency = ? AND close > 0
               ORDER BY ts DESC LIMIT 1""",
            (hedef["id"], hedef["para_birimi"]))
        if not r:
            return True
        beklenen = r[0]["close"]
        sapma = abs(kapanis / beklenen - 1)
        if sapma > self.SAPMA_ESIGI:
            log.warning("[%s] %s ATLANDI: saatlik %.4f %s, gunluk seri %.4f "
                        "(sapma %%%.1f) — buyuk olasilikla BASKA BIR "
                        "KOTASYON", self.name, hedef["kod"], kapanis,
                        hedef["para_birimi"], beklenen, sapma * 100)
            self._atlanan.append(
                f"{hedef['kod']}(fiyat tutmuyor %{sapma * 100:.0f})")
            return False
        return True

    def _yaz(self, df, kod: str, hedef: dict) -> int:
        if kod not in df.columns.get_level_values(0):
            return 0
        alt = df[kod]
        satirlar = []
        for idx, r in alt.iterrows():
            kapanis = r.get("Close")
            if kapanis is None or kapanis != kapanis:
                continue                       # kapanissiz bar yazilmaz
            ts = _utc_damga(idx)
            if not ts:
                continue

            def _s(alan, _r=r):
                v = _r.get(alan)
                return None if v is None or v != v else float(v)

            satirlar.append({
                "ts": ts, "open": _s("Open"), "high": _s("High"),
                "low": _s("Low"), "close": float(kapanis),
                "volume": _s("Volume"),
            })
        if not satirlar:
            return 0
        # YAZMADAN ONCE MAKULLUK: yanlis kotasyondan gelen barlar
        # yazilirsa geri almak icin veri temizligi gerekir ve o, bu
        # projenin "kayit temizligi duzeltme degildir" dersinin ta
        # kendisi. Kapi YAZIM ONUNDE.
        if not self._makul(hedef, satirlar[-1]["close"]):
            return 0
        return self.db.upsert_prices_hourly(
            hedef["id"], satirlar, KAYNAK, currency=hedef["para_birimi"])
