"""
Fiyat gecmisi (OHLCV) — teknik analizin on kosulu.

NEDEN TARAYICI DEGIL (ARTIK)
----------------------------
Yahoo'nun chart ucu betik erisimine KAPALI: olculdu 2026-08-20, temiz bir
IP'den duz httpx ile v8/chart, v7/quote ve v1/search'in UCU DE ILK ISTEKTE
`429 Too Many Requests` dondu. Sebep kimlik degil, cerez/baslik eksikligi.
Bu yuzden uzun sure Playwright aciliyor, once bir "isinma sayfasi"
geziliyordu — ve bu, `prices` ile `makro` collector'larinin tarayici
bagimliliginin TEK sebebiydi.

Artik `yfinance` kullaniliyor: ayni ucu cagiriyor ama cerez/crumb
dongusunu kendisi yonetiyor. Olculdu ayni gun: 51 sembol (ABD +
Amsterdam + LSE + BIST) 1,9 saniyede, 51/51 basarili — Playwright'in
TEK sembolde harcadigi sureden az. Tarayici hic acilmiyor.

(Stooq alternatifi denendi: proof-of-work engeli, ardindan "Access denied".)

SEMBOL ESLESTIRMESI
-------------------
Yahoo ABD kotasyonlarini sade ticker ile ("NVDA"), Avrupa kotasyonlarini
borsa sonekiyle ("ADYEN.AS", "AIR.PA") tanir. Kimligi SEC'de dogrulanmis
enstrumanlarda sec_ticker kullanilir (ABD kotasyonu kesin); digerlerinde
katalog sembolu zaten sonekli gelir.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from .base import BaseCollector, CollectorResult


def _bugun_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# AD KARSILASTIRMASI ARTIK BURADA TANIMLI DEGIL — TEK KAYNAK
# `research.identity.ayni_sirket`.
#
# Burada `ad_ortusuyor` diye AYRI bir uygulama vardi ve iki kural
# birbirinden AYRISMISTI. 2026-08-21'de "Lilly (Eli)" vs "Eli Lilly and
# Company" hatasi BURADA gorulup BURADA duzeltildi; ayni hatanin
# `identity` tarafindaki ikizi ise iki gun daha yasadi ve 2026-08-23'te
# LLY'nin SEC dosyalamalarini sessizce dusurdu.
#
# Ustelik buradaki surum de eksikti: hukuki ekleri temizlemedigi icin
# "Microsoft Corporation" ile "MICROSOFT CORP" ONUN gozunde BASKA
# sirketlerdi (canli katalogda 121 kimligin 5'i: MSFT, NVDA, KO, COST,
# VRTX). Yani iki kopya iki FARKLI yanlis cevap veriyordu.
#
# Ad KORUNUYOR: `bot/tools.py` bu adla ice aktariyor ve daha onemlisi
# bu dosyadaki cagri yerleri "bizim ad / onlarin ad" okunusunu
# tasiyor. Degisen sey UYGULAMA, arayuz degil.
from ..research.identity import ayni_sirket as ad_ortusuyor

log = logging.getLogger(__name__)

# NOT: chart URL'si ve "isinma sayfasi" sabiti KALDIRILDI — cagriyi
# artik `yfinance` yapiyor ve kendi uc noktasini kendisi biliyor.
# Burada tutulan olu bir URL, ilerde "biz hangi ucu cagiriyoruz"
# sorusuna YANLIS cevap verirdi.

# PIYASA VEKILLERI — olay calismasindaki piyasa modeli (alfa/beta) icin.
#
# Neden Yahoo, Alpha Vantage degil: AV'de ham endeks sembolu calismiyor
# (`^NDX` bos donuyor) ve ucretsiz kota gunde 25 istek — endeks serisi
# her gun tazelenmesi gereken uzun bir seri, kotayi bosa yer. Yahoo'da
# kota yok ve HISSE SERILERIMIZ DE Yahoo'dan geliyor: piyasa modeli iki
# seriyi ayni gunlerde eslestirmek zorunda, ayni kaynak olmasi hizalamayi
# garantiler.
ENDEKSLER = {
    "QQQ":  ("QQQ",   "Nasdaq 100 (QQQ vekil)",         "USD"),
    "SPX":  ("^GSPC", "S&P 500",                        "USD"),
    "AEX":  ("^AEX",  "AEX",                            "EUR"),
    # XU100 GUNLUK tarafta Is Yatirim'in END_DEGER alanindan geliyor ve
    # oyle kalacak (ek istek yok, tum hisse cagrilarinda zaten donuyor).
    # Buradaki kayit SAATLIK tarafi icin: gun ici kiyasin referansi
    # Yahoo'dan `XU100.IS` olarak cekiliyor (olculdu 2026-09-01: 39
    # saatlik bar, o anki degeri tasiyor).
    #
    # `sources.prices.indices` listesinde OLMADIGI icin gunluk collector
    # bunu cekmeye BASLAMIYOR — davranis degismedi.
    "XU100": ("XU100.IS", "BIST 100",                   "TRY"),
}


class PriceCollector(BaseCollector):
    name = "prices"
    # TARAYICI ARTIK GEREKMIYOR — bkz. `yahoo_veri`. Yahoo betik
    # erisimini 429 ile kapatiyor ve bu collector SIRF o yuzden
    # Playwright aciyordu; `yfinance` cerez/crumb dongusunu kendisi
    # yonettigi icin tarayici bagimliligi tamamen kalkti.
    needs_browser = False

    # --- ALT SINIF ICIN UC KANCA ---------------------------------------
    #
    # `strateji_fiyat` bu collector'in TAM govdesini paylasiyor: ad
    # kapisi, sinif soneki varyanti, BIST/MAKRO elemesi, hata
    # siniflandirmasi, kirpma bildirimi. Ikinci bir kopya yazmak bu
    # deponun tekrar eden kusur sinifi olurdu — kopyalar AYRISIR
    # (`[[ayni-kural-iki-kopya]]`: LLY prices'ta duzeltildi, ikizi
    # identity'de SEC dosyalamalarini sessizce dusurmeye devam etti).
    #
    # Ayrisan tek sey UC nokta: HANGI hedefler, bos kalinca NE denir,
    # ve endeks/kotasyon gibi EK isler kime ait.

    def _hedefleri_hazirla(self) -> list:
        """Cekilecek enstrumanlar — ve (varsa) tazeleme planinin kurulumu."""
        return list(self.db.research_targets())

    def _bos_sebep(self) -> str:
        return "arastirma hedefi yok"

    def _ek_seriler(self, hedefler) -> tuple[int, list]:
        """Endeks serileri + yerel borsa kotasyonlari."""
        toplam, basarisiz = 0, []
        for kod, n in self._endeksleri_cek(self._genel_aralik()).items():
            toplam += n
            if not n:
                basarisiz.append(f"endeks:{kod}")
        return toplam + self._borsa_kotasyonlari(hedefler), basarisiz

    def collect(self) -> CollectorResult:
        hedefler = self._hedefleri_hazirla()
        if not hedefler:
            return CollectorResult(self.name, "skipped", 0, self._bos_sebep())

        kimlikler = self.db.kimlik_haritasi()

        toplam, basarisiz = 0, []
        for h in hedefler:
            # BASKA COLLECTOR'IN ISI ARIZA DEGILDIR.
            #
            # BIST kagitlarinin serisini `isyatirim` cekiyor ve Yahoo
            # sade BIST sembolunu zaten REDDEDIYOR (bkz.
            # `_yahoo_sembolu`: soneksiz sembol baska sirkete denk
            # gelebilir). Ama bu red her kosuda "alinamadi" diye
            # raporlaniyordu: olculdu (2026-08-20), `prices` 23
            # kosunun 19'unda SIRF bu yuzden `partial` dondu ve
            # "ALTIN_GRAM, DEVA, KGYO, MASFN, QUICK, TERA, TRALT
            # (sembol yok)" satiri her seferinde tekrarladi.
            # Kalici bir sahte alarm, GERCEK arizayi gomer.
            # MAKRO da ayni: ALTIN_GRAM paritesini `makro` cekiyor,
            # Yahoo'da o sembol zaten yok.
            if (h["venue"] or "").upper() in ("BIST", "MAKRO"):
                continue
            yahoo = self._yahoo_sembolu(h, kimlikler.get(h["id"]))
            if not yahoo:
                # SADE SEMBOL REDDEDILDI — VAZGECMEDEN ONCE ADI
                # DOGRULAYARAK DENE.
                #
                # `_yahoo_sembolu` soneksiz sembolu reddediyor cunku o
                # bir TAHMINDIR (AVTX -> Avalo, RBOT -> Vicarious).
                # Ama red, katalogdaki yuzlerce kagidi KALICI olarak
                # erisilemez yapiyordu: olculdu 2026-08-21, `LLY` (Eli
                # Lilly) izlemeye alindi ve `prices` her kosuda "sembol
                # yok" deyip `partial` dondu — oysa veri bir cagri
                # uzaktaydi.
                #
                # TAHMINI KABUL ETMIYORUZ, DOGRULUYORUZ: Yahoo'nun
                # dondurdugu ad katalogdaki adla tutmuyorsa YAZILMAZ.
                # Kapi kapanmiyor, DOGRU yerden aciliyor.
                n, sebep = self._ad_dogrulayarak(h)
                if n:
                    toplam += n
                else:
                    basarisiz.append(f"{h['symbol']} ({sebep})")
                continue
            try:
                sebep = "bos"
                n = self._cek(yahoo, h["id"], self._aralik(h))
                if not n:
                    # SONEK HER ZAMAN BORSA DEGIL — SINIF DA OLABILIR.
                    #
                    # `_yahoo_sembolu` noktali sembolu "borsa sonekli"
                    # sayip oldugu gibi veriyor (ABN.AS, 4GLD.DE dogru
                    # calisiyor). Ama 'BRK.B' ve 'BF.B' bir BORSA degil
                    # HISSE SINIFI gosteriyor ve Yahoo bunlari tire ile
                    # yaziyor. Olculdu 2026-08-27: `BRK.B` -> 0 bar,
                    # `BF.B` -> 0 bar. Ikisi de S&P 500 uyesi.
                    #
                    # Duzeltme TAHMIN DEGIL: varyant yine ad kapisindan
                    # geciyor (`_ad_dogrulayarak`), yani Yahoo'nun
                    # dondurdugu ad katalogdakiyle tutmazsa YAZILMAZ.
                    n, sebep = self._ad_dogrulayarak(h)
                toplam += n
                if not n:
                    basarisiz.append(f"{h['symbol']} ({sebep})")
            except Exception as e:              # noqa: BLE001
                log.warning("[prices] %s alinamadi: %s", h["symbol"], e)
                basarisiz.append(h["symbol"])
        ek_toplam, ek_basarisiz = self._ek_seriler(hedefler)
        toplam += ek_toplam
        basarisiz += ek_basarisiz
        durum = "partial" if basarisiz else "ok"
        # SESSIZ KIRPMA YOK. Liste 8'de kesiliyordu ve kesildigi
        # SOYLENMIYORDU: 2026-08-23 alarminda kullanici tam 8 sembol
        # gordu ve gercekte 12 tane vardi — yani mesaj "hepsi bu"
        # gibi okundu. Kirpmak makul (mesaj Telegram'a sigmali), ama
        # kirpildigini GIZLEMEK bu projenin tekrar eden kusur sinifi.
        not_ = None
        if basarisiz:
            not_ = "alinamadi: " + ", ".join(basarisiz[:8])
            if len(basarisiz) > 8:
                not_ += f" (+{len(basarisiz) - 8} daha, toplam {len(basarisiz)})"
        return CollectorResult(self.name, durum if toplam else "error", toplam,
                               not_)

    # ------------------------------------------------------------------
    # ARALIK HEDEF BAZLI — TEK YERDEN COZULUYOR.
    #
    # Onceden tek genel deger vardi (`sources.prices.range`) ve BES
    # cagri yerine ayni degisken elden ele geciyordu. Strateji evreni
    # backtest icin derinlik istiyor (`ibkr.strateji.asgari_bar` 1500 ≈
    # 6 yil), portfoy/izleme listesi istemiyor. Iki degeri bes yere
    # dagitmak, bu deponun tekrar eden kusur sinifidir: ayni kural iki
    # kopya olur ve kopyalar AYRISIR.
    _strateji_idler: frozenset = frozenset()
    # id -> tazeleme araligi. `topla` her kosuda YENIDEN BAGLIYOR
    # (icerigi degistirmiyor); sinif duzeyindeki bos sozluk paylasilan
    # durum degil, yalnizca varsayilan.
    _strateji_araliklari: dict = {}

    def _genel_aralik(self) -> str:
        return self.s.get("sources.prices.range", "2y")

    def _derin_aralik(self) -> str:
        return self.s.get("sources.prices.range_strateji", self._genel_aralik())

    def _aralik(self, hedef) -> str:
        """Strateji evrenine derin seri, geri kalanina genel aralik."""
        if hedef["id"] not in self._strateji_idler:
            return self._genel_aralik()
        # PLANDA YOKSA DERIN. Varsayilan "derin" olmali, "kisa" degil:
        # plan yalnizca ZATEN DERIN oldugu OLCULEN seriler icin kisaltma
        # tasiyor. Bilinmeyen bir enstrumana kisa aralik vermek, geri
        # doldurulmayi hic baslamamis bir seriyi sonsuza dek sig
        # birakirdi ve `asgari_bar` onu sessizce evrenden duserdi.
        return self._strateji_araliklari.get(hedef["id"], self._derin_aralik())

    # TAZELEME KADEMELERI — (azami bosluk gunu, aralik).
    #
    # OLCULDU 2026-08-28: cekim suresi 2y'nin ALTINDA aralikla degil
    # ISTEK SAYISIYLA belirleniyor (10y 1,21 sn/sembol · 2y 0,39 ·
    # 3mo 0,38). Yani kademeler sureyi degil YAZILAN SATIRI kisiyor —
    # ve derin araliktan cikmak asil kazanci veriyor.
    #
    # Kademe SART, sabit "3mo" degil: makine iki ay kapali kalirsa
    # (OLCULDU, bkz. hafta sonu yedek boslugu) 3mo'luk tazeleme seride
    # DELIK birakirdi ve delik hicbir hata vermeden Donchian penceresini
    # bozardi. Bosluk buyudukce aralik buyuyor; hicbiri yetmezse derin.
    TAZELEME_KADEMELERI = ((20, "3mo"), (80, "6mo"), (340, "1y"), (700, "2y"))

    def _tazeleme_plani(self, strateji) -> dict:
        """
        ZATEN DERIN olan strateji serilerine kisa tazeleme araligi.

        NEDEN: `range_strateji` 10y ve `yahoo_veri` ARTIMLI DEGIL —
        `t.history(period=...)` her kosuda tum pencereyi yeniden
        indiriyor. Sahada olculdu (2026-08-27/28): `prices` 80 sn'den
        861 sn'ye cikti, gunde uc kosu ~45 dakika, ve 28 Agustos sabahi
        panel butcesi 900 sn'den 61 sn'ye dusup IKI SAHIBIN de paneli
        atlandi. Derin gecmis BIR KEZ gerekiyordu, her gun degil.

        Esik `asgari_bar` — strateji motorunun KENDI sarti, ikinci bir
        sabit degil. Sig kalan seri (yeni halka arz) derin aralikta
        kaliyor: 10y istegi zaten var olan kadarini donduruyor, ucuz.
        """
        idler = [h["id"] for h in strateji]
        if not idler:
            return {}
        asgari = int(self.s.strateji_ayari(self.db)["asgari_bar"])
        durum = self.db.seri_durumlari(idler, "yahoo")
        bugun = datetime.now(timezone.utc).date()
        plan: dict[int, str] = {}
        for iid in idler:
            d = durum.get(iid)
            if not d or d["bar"] < asgari or not d["son_ts"]:
                continue                       # seri yok ya da sig -> derin
            try:
                son = date.fromisoformat(str(d["son_ts"])[:10])
            except ValueError:
                continue                       # bozuk damga -> derin
            gun = (bugun - son).days
            if gun < 0:
                continue                       # gelecek tarihli -> derin
            for sinir, aralik in self.TAZELEME_KADEMELERI:
                if gun <= sinir:
                    plan[iid] = aralik
                    break
        return plan

    def _ad_dogrulayarak(self, hedef) -> tuple[int, str | None]:
        """
        Kimligi cozulmemis sembolu ADI DOGRULANARAK ceker.
        Doner: `(yazilan_satir, sebep)` — basarida sebep None.

        Bu, `fiyat_getir` aracinin collector tarafindaki karsiligi ve
        AYNI kapiyi kullaniyor: Yahoo'nun `shortName`'i katalogdaki adla
        ortusmuyorsa hicbir sey yazilmaz. Yanlis fiyat, eksik fiyattan
        TEHLIKELIDIR — her gosterge hesaplanir ve hepsi yanlis cikar.

        Katalogda ADI OLMAYAN kagitta da yazilmaz: dogrulayacak bir sey
        yoksa dogrulanmis sayilmaz.

        SEBEP NEDEN DONUYOR — OLCULEN ARIZA (2026-08-27, 518 sembollук
        kosumda). Uc sembol (GPC, GPN, GRMN) raporda "(sembol yok)"
        diye gecti; sonradan tek tek denendiginde UCU DE 2513 bar yazdi.
        Yani cagri GECICI olarak dusmustu ve istisna DEBUG'a
        loglaniyordu — INFO ile kosan uretimde GORUNMEZ. Kullaniciya
        giden cumle "sembol yok" idi: VERI VARKEN YOK DEMEK, bu deponun
        en kotu hata sinifi.

        Uc sebep artik AYRI: `cagri hatasi` (gecici, yeniden denenir),
        `Yahoo'da seri yok` (kalici), `ad eslesmedi` (kalici ve
        BILEREK — kapi calisiyor demektir).
        """
        ad = (hedef["name"] or "").strip()
        sembol = (hedef["symbol"] or "").upper()
        if not ad:
            return 0, "katalogda ad yok — dogrulanamaz"
        if not sembol or sembol.startswith("~"):
            return 0, "gecici anahtar (~onekli) — sembol degil"
        aralik = self._aralik(hedef)
        # SINIF SONEGI VARYANTI — ad kapisinin ARKASINDA.
        #
        # 'BRK.B' Yahoo'da 'BRK-B'. Varyanti denemek bir TAHMIN degil,
        # cunku dondurulen ad katalogdaki adla karsilastiriliyor ve
        # tutmazsa yazilmiyor — tahmini KABUL etmiyoruz, DOGRULUYORUZ.
        adaylar = [sembol]
        if "." in sembol:
            adaylar.append(sembol.replace(".", "-"))
        sebep = "Yahoo'da seri yok"
        for aday in adaylar:
            try:
                satirlar, meta = yahoo_veri(aday, aralik, ad_gerek=True)
            except Exception as e:                    # noqa: BLE001
                # WARNING, DEBUG DEGIL: yutulan bir ag hatasi kullaniciya
                # "sembol yok" diye ciktı ve UC sembol boyle kayboldu.
                log.warning("[prices] %s ad dogrulamasi basarisiz: %s: %s",
                            aday, type(e).__name__, e)
                sebep = f"cagri hatasi: {type(e).__name__}"
                continue
            if not satirlar:
                continue
            # IKI AD DA SORULUYOR, KURAL TEK. `shortName` 30 karakterde
            # kesiliyor; kesik belirtec altkume sartini yanlis yere
            # kirıyor (bkz. `yahoo_veri`, olculdu 7/14).
            adlari = [meta.get("shortName"), meta.get("longName")]
            if not any(ad_ortusuyor(ad, o) for o in adlari if o):
                log.info("[prices] %s atlandi: ad eslesmedi (bizde %r, "
                         "Yahoo short=%r long=%r)", aday, ad, *adlari)
                sebep = (f"ad eslesmedi (bizde {ad!r}, Yahoo short="
                         f"{adlari[0]!r} long={adlari[1]!r})")
                continue
            return yahoo_gunluk(self.db, aday, hedef["id"], aralik,
                                satirlar=satirlar, meta=meta), None
        return 0, sebep

    def _borsa_kotasyonlari(self, hedefler) -> int:
        """
        Pozisyonun PARA BIRIMINDEKI yerel borsa kotasyonunu IKINCI kaynak
        olarak ceker (ASML -> ASML.AS, EUR).

        NEDEN GEREKLI: `fiyat_kaynagi()` pozisyonun para birimiyle
        eslesen kaynagi seciyor ve EUR serisi Alpha Vantage'dan geliyordu.
        AV'nin ucretsiz kotasi (gunde 25) her gece tukeniyor; olculdu
        (2026-08-17): ASML'nin EUR serisi 14 Agustos'ta kalmis, yani
        portfoyun %41'inin GUNLUK HAREKETI raporda hic gorunmuyordu.
        Yahoo ayni kotasyonu kotasiz ve TAZE veriyor: ASML.AS 1.621,20 EUR
        vs AV'nin bayat 1.579,60'i — gorunmeyen hareket %2,6.

        KIMLIK KONTROLU ZORUNLU. Sonek eklemek bir TAHMINDIR ve bu
        projede tahmin edilen sembol iki kez baska sirkete denk geldi
        (AVTX -> Avalo Therapeutics, RBOT -> Vicarious Surgical).

        AD VE PARA BIRIMI KONTROLU YETMEDI — SAHADA OLCULDU. Ilk surumde
        yalnizca bu ikisi araniyordu ve `TSLA.AS` ile `MSFT.AS` her iki
        kontrolu de GECTI: Yahoo ikisini de EUR cinsinden ve "TESLA" /
        "MICROSOFT" adiyla donduruyor. Ama fiyatlar 7,22 EUR ve 8,57 EUR
        — bunlar hisse degil, Amsterdam'da islem goren SERTIFIKA/tracker
        urunleri. Sonuc: TSLA pozisyonu 144,88 EUR yerine 3,54 EUR
        degerlendi. Yanlis fiyat eksik fiyattan tehlikelidir; her sey
        hesaplanir ve hepsi yanlis cikar.

        Bu yuzden DORDUNCU kosul: FIYAT MAKULLUK KONTROLU. Aday
        kotasyonun son kapanisi, var olan referans seriye (ABD
        kotasyonu) kurla cevrilip karsilastirilir; sapma %10'u asarsa
        AYNI ENSTRUMAN DEGILDIR ve yazilmaz. Referans seri yoksa da
        yazilmaz — dogrulanamayan bir tahmini kabul etmektense o
        enstrumanda EUR serisi olmasin.
        """
        from ..storage.db import _ad_anahtari

        sonekler = self.s.get("sources.prices.borsa_sonekleri") or {"EUR": ".AS"}
        yazilan = 0
        for h in hedefler:
            poz = self.db.query(
                """SELECT currency FROM positions WHERE instrument_id = ?
                   ORDER BY snapshot_ts DESC LIMIT 1""", (h["id"],))
            ccy = (poz[0]["currency"] if poz else None) or ""
            sonek = sonekler.get(ccy.upper())
            sembol = (h["symbol"] or "").upper()
            if not sonek or "." in sembol or sembol.startswith("~"):
                continue
            # Bu para biriminde ZATEN taze bir seri varsa ikinci kez cekme.
            var = self.db.query(
                """SELECT MAX(p.ts) son FROM prices p
                   WHERE p.instrument_id = ? AND p.currency = ?""", (h["id"], ccy))
            if var and var[0]["son"] and var[0]["son"] >= _bugun_iso():
                continue
            try:
                n = self._kotasyon_yaz(f"{sembol}{sonek}", h, ccy, _ad_anahtari)
                yazilan += n
            except Exception as e:                      # noqa: BLE001
                log.debug("[prices] %s%s kotasyonu alinamadi: %s", sembol, sonek, e)
        return yazilan

    def _kotasyon_yaz(self, yahoo: str, hedef, ccy: str, ad_anahtari) -> int:
        # GENEL ARALIK, STRATEJI ARALIGI DEGIL — BILEREK.
        #
        # Bu yol AYRI bir seriye yaziyor (`kaynak="yahoo_borsa"`) ve
        # strateji motoru onu HIC okumuyor: `fiyat_serisi(tercih_ccy=USD)`
        # `yahoo` kaynagini seciyor. Yani derinlik burada gereksiz.
        #
        # Dahasi `_aralik(hedef)` cagirmak KENDI ACTIGIM bir yan etkiydi:
        # strateji evreninden bir enstrumanin (ASML) EUR kotasyonu da
        # 10y cekmeye baslamisti. Ters yonu daha tehlikeli — tazeleme
        # plani devreye girince ayni cagri 3mo'ya duserdi ve YENI eklenen
        # bir kotasyon 200 barlik SMA'ya hic ulasamazdi. Bu seriyi
        # portfoy tarafi okuyor; sessiz kapsam kaybi tam burada dogardi.
        aralik = self._genel_aralik()
        # `ad_gerek=True`: asagidaki ad eslestirmesi olmadan TSLA.AS gibi
        # bir SERTIFIKA hisse sanilir (7,22 EUR vs 339,30 USD, 40 kat).
        satirlar, meta = yahoo_veri(yahoo, aralik, ad_gerek=True)
        if not satirlar:
            return 0
        if (meta.get("currency") or "").upper() != ccy.upper():
            return 0
        bizim, onlarin = ad_anahtari(hedef["name"]), ad_anahtari(meta.get("shortName"))
        if not (bizim and onlarin and (bizim in onlarin or onlarin in bizim)):
            log.info("[prices] %s atlandi: ad eslesmedi (bizde %r, Yahoo %r)",
                     yahoo, hedef["name"], meta.get("shortName"))
            return 0
        if not self._fiyat_makul(hedef["id"], meta, ccy):
            return 0
        # AYRI KAYNAK ADI ZORUNLU — bkz. yahoo_gunluk docstring.
        # Veri zaten elde: ikinci istek atilmiyor.
        return yahoo_gunluk(self.db, yahoo, hedef["id"], aralik,
                            currency=ccy, kaynak="yahoo_borsa",
                            satirlar=satirlar, meta=meta)

    # Referanstan izin verilen en buyuk sapma. %10 secildi: iki borsanin
    # kapanis saatleri farkli (Amsterdam 17:30, New York 22:00 TRT) ve
    # aradaki gun ici hareket + kur farki birkac yuzdeyi bulabilir.
    # Sertifika/tracker urunleri ise KAT KAT farkli fiyatlanir (TSLA.AS
    # 7,22 EUR vs TSLA 339,30 USD — 40 kat), yani esik hassas olmak
    # zorunda degil, yalnizca BUYUKLUK MERTEBESINI ayirmali.
    SAPMA_ESIGI = 0.10

    def _fiyat_makul(self, instrument_id: int, meta: dict, ccy: str) -> bool:
        """Aday kotasyonun fiyati, var olan referans seriyle tutuyor mu?"""
        aday = meta.get("regularMarketPrice") or meta.get("previousClose")
        if not aday:
            return False
        ref = self.db.query(
            """SELECT close, currency FROM prices
               WHERE instrument_id = ? AND currency IS NOT NULL AND currency <> ?
               ORDER BY ts DESC LIMIT 1""", (instrument_id, ccy.upper()))
        if not ref or not ref[0]["close"]:
            log.info("[prices] %s: karsilastirilacak referans seri yok, "
                     "kotasyon KABUL EDILMEDI", meta.get("symbol"))
            return False
        kur = self.db.fx_kuru(ref[0]["currency"], ccy)
        if not kur:
            log.info("[prices] %s: %s->%s kuru yok, kotasyon KABUL EDILMEDI",
                     meta.get("symbol"), ref[0]["currency"], ccy)
            return False
        beklenen = ref[0]["close"] * kur["rate"]
        if not beklenen:
            return False
        sapma = abs(aday / beklenen - 1)
        if sapma > self.SAPMA_ESIGI:
            log.warning("[prices] %s ATLANDI: fiyat tutmuyor — aday %.4f %s, "
                        "referanstan beklenen %.4f %s (sapma %%%.1f). Bu buyuk "
                        "olasilikla hisse degil sertifika/tracker.",
                        meta.get("symbol"), aday, ccy, beklenen, ccy, sapma * 100)
            return False
        return True

    def _endeksleri_cek(self, aralik: str) -> dict:
        istenen = self.s.get("sources.prices.indices") or ["QQQ", "AEX"]
        out = {}
        for kod in istenen:
            tanim = ENDEKSLER.get(kod)
            if not tanim:
                continue
            yahoo, ad, ccy = tanim
            iid = self.db.upsert_instrument(kod, "INDEX", ad, "index", ccy)
            try:
                out[kod] = self._cek(yahoo, iid, aralik, currency=ccy)
            except Exception as e:              # noqa: BLE001
                log.warning("[prices] endeks %s alinamadi: %s", kod, e)
                out[kod] = 0
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def _yahoo_sembolu(hedef, kimlik) -> str | None:
        if hedef["asset_type"] == "cash" or hedef["symbol"] == "CASH":
            return None

        # KIMLIGI COZULEMEYEN ENSTRUMANDAN FIYAT CEKILMEZ.
        # Sembol bir TAHMINDIR ve baska sirkete ait olabilir: "Avantium"
        # icin tahmin edilen AVTX, Yahoo'da "Avalo Therapeutics" (ABD
        # biyotek). Ilk denemede tam bu oldu — 500 bar cekildi ve hepsi
        # yanlis sirketin fiyatiydi (3.59-22.87 USD; Avantium Amsterdam'da
        # ~2-4 EUR). Yanlis fiyat, eksik fiyattan cok daha tehlikeli:
        # RSI, SMA, getiri — hepsi hesaplanir ve hepsi yanlistir.
        if kimlik is not None and kimlik["status"] == "eslesmedi":
            return None

        # SEC'de dogrulanmis -> ABD kotasyonu var, sade ticker calisir.
        if kimlik is not None and kimlik["status"] in ("dogrulandi", "elle") \
                and kimlik["sec_ticker"]:
            return kimlik["sec_ticker"]

        sembol = (hedef["symbol"] or "").upper()
        # Ekran goruntusunden gelen gecici anahtarlar (~ONEKLI) kullanilamaz.
        if sembol.startswith("~"):
            return None

        # SADE SEMBOL TEK BASINA GUVENLI DEGIL — ikinci kez isbatlandi.
        # AVTX'ten sonra RBOT: kimligi dogru sekilde "fon" (iShares
        # Automation & Robotics) isaretliydi ama kod buraya dusup ham
        # sembolu Yahoo'ya verdi. Yahoo'da RBOT = Vicarious Surgical,
        # 6 SENTLIK baska bir sirket. Ekranda 19.01 EUR olan ETF icin
        # 0.06 USD'lik seri cekildi (%99.7 sapma) ve tum gostergeler
        # bu seriden hesaplandi.
        #
        # Artik yalnizca AMBIGU OLMAYAN sembol kabul ediliyor: borsa
        # sonekli olanlar (ABN.AS, ADYEN.AS) tek bir kotasyonu gosterir.
        # Soneksiz sade sembol, dogrulanmis bir SEC ticker'i yoksa
        # REDDEDILIR — eksik seri, yanlis seriden iyidir.
        if "." in sembol:
            return sembol
        return None

    def _cek(self, yahoo: str, instrument_id: int, aralik: str,
             currency: str | None = None) -> int:
        return yahoo_gunluk(self.db, yahoo, instrument_id, aralik, currency)


def yahoo_veri(yahoo: str, aralik: str, ad_gerek: bool = False) -> tuple[list[dict], dict]:
    """
    Yahoo'dan gunluk OHLCV + meta. `yfinance` ile — TARAYICISIZ.

    NEDEN TARAYICI YOKTU DA VARDI, SIMDI YINE YOK. Yahoo'nun chart ucu
    betik erisimine KAPALI: olculdu 2026-08-20, temiz bir IP'den duz
    httpx ile v8/chart, v7/quote ve v1/search'in UCU DE ILK ISTEKTE
    `429 Too Many Requests` dondu. Bu yuzden Playwright aciliyor, once
    bir "isinma sayfasi" geziliyordu (cerez icin) ve her sembol icin
    sayfa gezintisi yapiliyordu — yavas, kirilgan, ve `prices`
    collector'inin tarayici bagimliliginin TEK sebebi.

    `yfinance` ayni ucu kullaniyor ama cerez/crumb dongusunu kendisi
    yonetiyor. Olculdu ayni gun: 51 sembol (ABD + Amsterdam + LSE +
    BIST) 1,9 saniyede, 51/51 basarili. Playwright'in tek sembolde
    harcadigi sureden az.

    AYARLAMA KAPALI (`auto_adjust=False`) — BILEREK. yfinance'in
    varsayilani bolunme/temettu icin OHLC'yi duzeltiyor; eski seriler
    ise chart ucunun HAM `quote` blogundan yazildi. Ikisini karistirmak
    ayni enstrumanda iki farkli fiyat tabani demek olurdu.
    """
    import yfinance as yf

    # YFINANCE'IN KENDI GURULTUSU SUSTURULUYOR.
    # `_borsa_kotasyonlari` bilerek OLMAYAN sembolleri de deniyor
    # (NVDA.AS, PLTR.AS, SPACEX.AS — Amsterdam'da kotasyonlari yok) ve
    # yfinance bunlarin her birini ERROR seviyesinde logluyor. Bu
    # `pulse.log`'u ariza gorunumlu satirlarla dolduruyordu; oysa
    # deneme-yanilma BU TASARIMIN kendisi ve sonuc zaten yakalaniyor.
    # Gercek hatalar `yahoo_veri`nin cagiranlarinda raporlaniyor.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    t = yf.Ticker(yahoo)
    df = t.history(period=aralik, interval="1d", auto_adjust=False)
    satirlar = []
    for idx, r in df.iterrows():
        kapanis = r.get("Close")
        # Yahoo bazi barlari null dondurur (tatil, veri boslugu, son
        # gunun henuz kapanmamis olmasi). Kapanissiz bar teknik
        # gostergeyi bozar — yazma.
        if kapanis is None or kapanis != kapanis:      # NaN kontrolu
            continue

        def _s(alan):
            v = r.get(alan)
            return None if v is None or v != v else float(v)

        satirlar.append({
            "ts": idx.date().isoformat(),
            "open": _s("Open"), "high": _s("High"), "low": _s("Low"),
            "close": float(kapanis), "volume": _s("Volume"),
        })

    # `symbol` META'YA ELLE KONUYOR: eski chart ucu bunu kendisi
    # donduruyordu ve `_fiyat_makul` uyari metninde kullaniyor. Yoksa
    # sertifika reddi "None ATLANDI" diye loglanir — hangi kagidin
    # reddedildigi kaybolur, yani uyari ISE YARAMAZ hale gelir.
    meta: dict = {"symbol": yahoo}
    try:
        fi = t.fast_info
        for anahtar, alan in (("currency", "currency"),
                              ("regularMarketPrice", "last_price"),
                              ("previousClose", "previous_close")):
            try:
                meta[anahtar] = fi[alan]
            except Exception:                          # noqa: BLE001
                pass
    except Exception as e:                             # noqa: BLE001
        log.debug("[prices] %s fast_info alinamadi: %s", yahoo, e)
    if ad_gerek:
        # `get_info()` AGIR bir cagri (ayri istek) — yalnizca ad
        # eslestirmesi gereken kotasyon dogrulamasinda isteniyor.
        #
        # `longName` DE ALINIYOR VE BEDAVA: ayni yanitin icinde.
        # `shortName` 30 KARAKTERDE KESILIYOR ve kesik son belirtec ad
        # kapisini yanlis yere kapatiyor. Olculdu 2026-08-27, serisi
        # cekilemeyen 14 S&P/Nasdaq uyesinde:
        #     shortName ile eslesen : 0/14
        #     longName  ile eslesen : 7/14
        #     'International Flavors & Fragran'  <- kesik
        #     'International Flavors & Fragrances Inc.'  <- tam
        # Kural GEVSEMIYOR (ayni `ayni_sirket`, ayni altkume sarti);
        # yalnizca AYNI kaynagin daha eksiksiz alani da soruluyor.
        try:
            info = t.get_info() or {}
            meta["shortName"] = info.get("shortName")
            meta["longName"] = info.get("longName")
        except Exception as e:                         # noqa: BLE001
            log.debug("[prices] %s adi alinamadi: %s", yahoo, e)
    return satirlar, meta


def yahoo_gunluk(db, yahoo: str, instrument_id: int, aralik: str,
                 currency: str | None = None, kaynak: str = "yahoo",
                 satirlar=None, meta: dict | None = None) -> int:
    """
    Yahoo gunluk OHLCV'yi `prices`e yazar.

    MODUL SEVIYESINDE, cunku iki collector kullaniyor (`prices` ve
    `makro`). Ayni cekim mantigini iki yere kopyalamak bu projenin
    tekrar eden kusur sinifi: iki yerde beyan edilen gercek ayrisiyor
    (para birimi yazma adimi bir tarafta unutulur ve seri etiketsiz kalir).

    `kaynak` PARAMETRESI SESSIZ VERI KAYBINI ONLUYOR. `prices` birincil
    anahtari (instrument_id, ts, source) — PARA BIRIMI ANAHTARDA YOK.
    Ayni enstrumanin iki farkli kotasyonu ayni `source` adiyla
    yazilirsa ikincisi birincisini EZER. Sahada olcuLDU: ASML'nin
    Amsterdam (EUR) serisi `source='yahoo'` ile yazilinca ABD (USD)
    serisinin 502 barindan 9'u kaldi. Kotasyon basina AYRI kaynak adi
    kullanilmali.

    `satirlar`/`meta` verilirse YENIDEN CEKILMEZ: kotasyon dogrulamasi
    zaten veriyi almis oluyor, ikinci bir istek bosa gider.
    """
    if satirlar is None:
        satirlar, meta = yahoo_veri(yahoo, aralik)
    if not satirlar:
        return 0

    # PARA BIRIMI KAYNAGIN KENDI BEYANINDAN. Onceden bu deger okunuyor
    # ama seriye YAZILMIYORDU; seri etiketsiz kaldigi icin USD fiyatlar
    # EUR portfoy degerleriyle yan yana kullanildi ve 17 pozisyonun
    # 14'unde ~%15.7 (EUR/USD kuru kadar) sapma olustu.
    para = (meta or {}).get("currency") or currency
    if para:
        with db.tx() as c:
            c.execute("UPDATE instruments SET currency=COALESCE(currency,?) WHERE id=?",
                      (para, instrument_id))
    return db.upsert_prices(instrument_id, satirlar, kaynak, currency=para)
