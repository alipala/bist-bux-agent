"""
IBKR anlik kotasyon — ON-UCUS, HAT MUHASEBESI, VE HER ZAMAN BIRAKMA.

UC OLCULMUS DAVRANIS (2026-08-26, canli gateway)
------------------------------------------------

1. ON-UCUS: ilk istek FIYAT VERMEZ, yalnizca conid'i doner.

       1. istek -> [{"conidEx": "8894", "conid": 8894}]
       2. istek -> [{"31": "91.66", "87_raw": 11900000.0, "6509": "DPB", ...}]

   IBKR bunu "pre-flight request" diye adlandiriyor: ilk cagri IServer'a
   "bu enstrumanin akisini tuketmeye basla" demek. Bunu bilmeyen kod ilk
   yanita bakip "fiyat yok" der — bu deponun en kotu hata sinifi.

2. HAT BIRAKILABILIYOR, VE BIRAKILDIGI GORULEBILIYOR:

       GET  /iserver/marketdata/unsubscribeall     -> {"unsubscribed": true}
       POST /iserver/marketdata/unsubscribe {conid}-> {"success": true}

   Birakma sonrasi ilk istek YINE on-ucus donuyor (fiyatsiz) — yani akis
   gercekten kapaniyor. Kanit bu: davranis geri sariliyor.

   NEDEN ONEMLI: IBKR her hesaba "100 concurrent lines of real-time
   market data (which can be displayed in TWS or via the API)" veriyor.
   Her on-ucus bir hat tuketiyor ve KAPATILMAZSA acik kaliyor. 40 aday
   tarayan bir kosu 40 hat acar; birkac kosu sonra 100'e dayanir ve yeni
   semboller SESSIZCE bos doner. Yani veri VARKEN "yok" denir.

   Bu yuzden birakma `finally` icinde: sizinti YAPISAL OLARAK imkansiz,
   "unutmamaya" dayanmiyor.

3. HACIM IKI BICIMDE GELIYOR — biri sayi DEGIL:

       "87":     "11.9M"          <-- insan icin, K/M/B sonekli
       "87_raw": 11900000.0       <-- makine icin

   `float("11.9M")` patlar; daha kotusu, tolere eden bir cozumleyici
   11.9 okuyup HACMI BIR MILYON KAT KUCUK gosterebilirdi. Belgede
   `_raw` sonekinden hic bahsedilmiyor, olcumle bulundu. Kural: once
   `<alan>_raw`, sonra `<alan>`.

FIYATLAR STRING GELIYOR ("91.66") ve binlik ayrac icerebiliyor.
`portfoy._sayi` bunu zaten tolere ediyor; ayni cozumleyici kullaniliyor.

`_updated` TAZELIK OLCUSU DEGIL — OLCULDU
-----------------------------------------
`_updated` alani cazip bir tazelik olcusu gibi duruyor ama DEGIL. Olculdu
(2026-08-26 22:33Z, veri kipi `D` = 15-20 dk gecikmeli):

    AMZN  _updated=22:33:13Z   "yasi" 0,0 dk
    KO    _updated=22:33:13Z   "yasi" 0,0 dk
    NVDA  _updated=22:33:13Z   "yasi" 0,0 dk

Veri gecikmeliyken bile `_updated` SU AN'i gosteriyor. Cunku o alan
"islem ne zaman oldu"yu degil, "IBKR bu degeri bize ne zaman iletti"yi
soyluyor — yani `received_at`, `source_timestamp` DEGIL.

`_updated`i tazelik sanmak, 15 dakika eski bir fiyata "0 dakika taze"
damgasi vurmak olurdu. Gercek zamanli mi degil mi sorusunun TEK yetkili
cevabi `6509`.

VERI KIPI GIZLENMEZ
-------------------
`6509` alaninin ilk karakteri verinin ne oldugunu soyluyor:
R gercek zamanli, D 15-20 dk gecikmeli, Z/Y donmus, N abone degil,
O yillik "Market Data API Agreement" imzalanmamis.

Bu alan cagirana AYNEN tasiniyor ve `gercek_zamanli` ayri bir bayrak.
Gecikmeli fiyati gercek zamanliymis gibi sunmak, yanlis fiyattan daha
kotu: yanlis oldugu BILINMEZ.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .istemci import IbkrHatasi, Istemci
from .portfoy import _sayi

log = logging.getLogger(__name__)

# IBKR: "All clients initially receive 100 concurrent lines of real-time
# market data". Pay birakiliyor — TWS acikken o da ayni havuzdan yiyor.
AZAMI_HAT = 80

# On-ucus sonrasi veri icin bekleme. Olculdu: 2 saniye yetiyor.
# Bosuna uzun beklemek hiz sinirini degil KULLANICIYI yorar.
BEKLEME_SN = 0.7
AZAMI_DENEME = 4

# Tek istekte sorulacak conid sayisi. Snapshot ucu 10 istek/sn ile
# sinirli; yiginlamak istek sayisini dusuruyor.
YIGIN = 25

# Alan etiketleri (IBKR "Market Data Fields" tablosundan).
SON = "31"
ALIS = "84"
SATIS = "86"
HACIM = "87"
ACILIS = "7295"
KAPANIS = "7296"
ONCEKI_KAPANIS = "7741"
ERISIM = "6509"

ALANLAR = ",".join([SON, ALIS, SATIS, HACIM, ACILIS, KAPANIS,
                    ONCEKI_KAPANIS, ERISIM])

# `6509` ilk karakteri -> insan okunur kip.
KIPLER = {
    "R": "gercek_zamanli",
    "D": "gecikmeli",
    "Z": "donmus",
    "Y": "gecikmeli_donmus",
    "N": "abone_degil",
    "O": "anlasma_imzalanmamis",
}


@dataclass
class Kotasyon:
    conid: str
    son: float | None = None
    alis: float | None = None
    satis: float | None = None
    hacim: float | None = None
    acilis: float | None = None
    onceki_kapanis: float | None = None
    erisim: str = ""                       # ham 6509
    para_birimi: str | None = None
    borsa: str | None = None
    guncelleme_ms: int | None = None
    alanlar: dict = field(default_factory=dict)

    @property
    def kip(self) -> str:
        return KIPLER.get(self.erisim[:1].upper() if self.erisim else "",
                          "bilinmiyor")

    @property
    def gercek_zamanli(self) -> bool:
        return self.kip == "gercek_zamanli"

    @property
    def kullanilabilir(self) -> bool:
        """
        HERHANGI bir fiyat geldi mi?

        Yalnizca `son`a bakmak yanlisti — olculdu: piyasa kapaliyken
        Avrupa kagitlari (`ZB` = donmus) `son` VERMIYOR ama alis/satis
        veriyor:

            ABN.AS   son=None  alis=40.70  satis=41.11   ZB
            AMZN     son=260.99 alis=None  satis=None    DPB

        `son is None` demek "fiyat yok" demek degil; oyle saymak elimizde
        alis/satis dururken "veri yok" beyani olurdu.
        """
        return any(v is not None for v in (self.son, self.alis, self.satis))

    @property
    def orta(self) -> float | None:
        """
        Alis/satis ORTASI. Emir oncesi fiyatlama icin `son`dan daha
        dogru olabilir (son islem eski olabilir, spread guncel).
        Ikisi de yoksa None — uydurulmuyor.
        """
        if self.alis is None or self.satis is None:
            return None
        return (self.alis + self.satis) / 2.0


def _alan(ham: dict, etiket: str) -> float | None:
    """
    Once `<etiket>_raw`, sonra `<etiket>`.

    Hacim "11.9M" gibi geliyor ve `_raw` surumu gercek sayiyi tasiyor.
    Sirayi ters cevirmek hacmi bir milyon kat kucuk gosterebilirdi.
    """
    v = _sayi(ham.get(f"{etiket}_raw"))
    return v if v is not None else _sayi(ham.get(etiket))


class Piyasa:
    """
    Kotasyon okuyucu. Hat muhasebesini KENDISI tutuyor.

    Kullanim tek yol: `with Piyasa(istemci) as p: p.kotasyon([...])`
    Cikista acik kalan her hat birakilir.
    """

    def __init__(self, istemci: Istemci, azami_hat: int = AZAMI_HAT):
        self.istemci = istemci
        self.azami_hat = azami_hat
        self._acik: set[str] = set()
        self._hazir = False
        # conid -> (para_birimi, borsa). Kotasyon ucu para birimini
        # DONDURMUYOR ve bu depoda para birimsiz sayi kabul edilemez.
        self._sozlesme: dict[str, tuple[str | None, str | None]] = {}

    # ------------------------------------------------------------------
    def __enter__(self) -> "Piyasa":
        return self

    def __exit__(self, *a) -> None:
        self.hepsini_birak()

    @property
    def acik_hat(self) -> int:
        return len(self._acik)

    # ------------------------------------------------------------------
    def _on_kosul(self) -> None:
        """
        IBKR: "The endpoint /iserver/accounts must be called prior to
        /iserver/marketdata/snapshot." Cagiranin hatirlamasi gerekmiyor.
        """
        if self._hazir:
            return
        self.istemci.get("/iserver/accounts")
        self._hazir = True

    def _sozlesme_bilgisi(self, conid: str) -> tuple[str | None, str | None]:
        """
        `/iserver/contract/{conid}/info` -> (para_birimi, borsa).

        NEDEN AYRI CAGRI: kotasyon uctan para birimi GELMIYOR. Sayiyi
        para birimsiz dondurmek bu depoda kabul edilemez — en pahali
        hata (17 pozisyonun 14'unde ~%15,7 sapma) tam olarak boyle
        dogmustu: USD seri, EUR portfoy degerleriyle yan yana kondu.

        Sonuc ONBELLEKLENIYOR: sozlesme bilgisi gun icinde degismez.
        """
        if conid in self._sozlesme:
            return self._sozlesme[conid]
        pb = borsa = None
        try:
            y = self.istemci.get(f"/iserver/contract/{conid}/info")
            if isinstance(y, dict):
                pb = y.get("currency")
                borsa = y.get("listing_exchange") or y.get("exchange")
        except IbkrHatasi as e:
            log.debug("[ibkr] %s sozlesme bilgisi alinamadi: %s", conid, e)
        self._sozlesme[conid] = (pb, borsa)
        return pb, borsa

    def _iste(self, conidler: list[str], alanlarla: bool) -> list[dict]:
        p = {"conids": ",".join(conidler)}
        if alanlarla:
            p["fields"] = ALANLAR
        y = self.istemci.get("/iserver/marketdata/snapshot", p)
        return [r for r in (y or []) if isinstance(r, dict)]

    # ------------------------------------------------------------------
    def kotasyon(self, conidler) -> dict[str, Kotasyon]:
        """
        conid listesi -> kotasyonlar. Hatlar HER ZAMAN birakilir.

        Doner sozlukte YALNIZCA veri gelenler bulunur; gelmeyen conid
        sessizce atlanmaz, `bos` kaydiyla doner ki cagiran "sorduk ama
        gelmedi" ile "hic sormadik"i ayirt edebilsin.
        """
        istenen = [str(c) for c in conidler if str(c).strip()]
        if not istenen:
            return {}
        self._on_kosul()

        cikti: dict[str, Kotasyon] = {}
        for i in range(0, len(istenen), YIGIN):
            parca = istenen[i:i + YIGIN]
            # HAT TAVANI. Asmaktansa EKSIK donmek dogru: 100'u asan
            # istekler sessizce bos donuyor ve bu "veri yok" gibi
            # gorunuyor.
            yer = self.azami_hat - self.acik_hat
            if len(parca) > yer:
                # TAVANA TAKILAN CONID'LER SESSIZCE DUSMEZ. Ilk surumde
                # dusuyordu ve testi yakaladi: cagiran 5 sembol sorup 3
                # yanit alsaydi, eksik ikisini "hic sorulmamis" sanirdi.
                # Bu deponun tekrar eden kusur sinifi — kirpmak makul,
                # kirpildigini GIZLEMEK degil.
                kalan = istenen[i + max(yer, 0):]
                log.warning(
                    "[ibkr] hat tavani (%s): %s conid bu kosuda okunamadi",
                    self.azami_hat, len(kalan))
                for c in kalan:
                    cikti.setdefault(c, Kotasyon(c))
                parca = parca[:yer] if yer > 0 else []
                if parca:
                    cikti.update(self._parcayi_oku(parca))
                break

            cikti.update(self._parcayi_oku(parca))
        return cikti

    def _parcayi_oku(self, parca: list[str]) -> dict[str, Kotasyon]:
        # ON-UCUS: ilk cagri akisi acar, veri VERMEZ.
        try:
            self._iste(parca, alanlarla=True)
        except IbkrHatasi as e:
            log.info("[ibkr] on-ucus basarisiz: %s", e)
            return {c: Kotasyon(c) for c in parca}
        self._acik.update(parca)

        ham: dict[str, dict] = {}
        for _ in range(AZAMI_DENEME):
            time.sleep(BEKLEME_SN)
            try:
                satirlar = self._iste(parca, alanlarla=False)
            except IbkrHatasi as e:
                log.info("[ibkr] kotasyon okunamadi: %s", e)
                break
            for r in satirlar:
                c = str(r.get("conid") or "")
                # Fiyat alani GELDIYSE kaydet. On-ucus yaniti yalnizca
                # conid tasiyor ve onu "veri" saymak bos kotasyon
                # yazmak olurdu.
                if c and SON in r:
                    ham[c] = r
            if len(ham) == len(parca):
                break

        cikti = {}
        for c in parca:
            q = self._kotasyon(c, ham[c]) if c in ham else Kotasyon(c)
            if q.kullanilabilir:
                q.para_birimi, q.borsa = self._sozlesme_bilgisi(c)
            cikti[c] = q
        return cikti

    @staticmethod
    def _kotasyon(conid: str, r: dict) -> Kotasyon:
        return Kotasyon(
            conid=conid,
            son=_alan(r, SON),
            alis=_alan(r, ALIS),
            satis=_alan(r, SATIS),
            hacim=_alan(r, HACIM),
            acilis=_alan(r, ACILIS),
            onceki_kapanis=_alan(r, ONCEKI_KAPANIS),
            erisim=str(r.get(ERISIM) or ""),
            guncelleme_ms=(int(r["_updated"])
                           if isinstance(r.get("_updated"), (int, float))
                           else None),
            alanlar=r,
        )

    # ------------------------------------------------------------------
    def birak(self, conid: str) -> None:
        try:
            self.istemci.post("/iserver/marketdata/unsubscribe",
                              {"conid": int(conid)})
        except (IbkrHatasi, ValueError) as e:
            log.debug("[ibkr] %s birakilamadi: %s", conid, e)
        self._acik.discard(str(conid))

    def hepsini_birak(self) -> None:
        """
        `unsubscribeall` — tek cagriyla hepsi.

        Hata SESSIZ ama LOGLU: cikis yolunda patlamak, asil isin
        sonucunu golgeler. Yine de gorunmez olmamali; sizan hat bir
        sonraki kosuda "veri yok" olarak geri gelir.
        """
        if not self._acik:
            return
        try:
            self.istemci.get("/iserver/marketdata/unsubscribeall")
            log.info("[ibkr] %s hat birakildi", len(self._acik))
            self._acik.clear()
        except IbkrHatasi as e:
            log.warning("[ibkr] hatlar birakilamadi (%s acik): %s",
                        len(self._acik), e)
