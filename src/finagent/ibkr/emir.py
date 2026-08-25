"""
IBKR emir mekanigi — GERCEK PARA. Bu dosyadaki her kural bir bedelin karsiligi.

BU MODUL LLM'IN ARAC YUZEYINE GIRMEZ
------------------------------------
`bot/tools.py` bunu ice AKTARMAZ. Model emir gonderemez; yalnizca oneri
uretir. `config/settings.yaml -> risk.allow_order_execution: false` ifadesi
bu yuzden dogru kalmaya devam ediyor: agent emir iletmiyor, insan iletiyor.

GONDERME YOLU ONAY FISI OLMADAN CAGRILAMAZ
------------------------------------------
`gonder()` bir `OnayFisi` istiyor ve fisin PARMAK IZI istegin parmak iziyle
birebir tutmali. Parmak izi hesap, conid, yon, tur, adet, fiyat ve sureyi
kapsiyor. Sonuc: onaydan SONRA adedi degistiren bir kod yolu, fisi
GECERSIZ kilar — "onaylandi" bayragi tasiyip icerigi degistirmek imkansiz.

Bu bir sozlesme degil, YAPI: `gonder()` bool almiyor. Bool alsaydi
`gonder(..., onaylandi=True)` yazan tek satir korumayi delerdi.

FIS KISA OMURLU
---------------
`onay.py`nin buton omru 24 saat ve GUNLUK OKUMA icin dogru. Canli emir
icin felaket olurdu: fiyat gun icinde yuzde onlarca oynar. Varsayilan
180 saniye.

POST ASLA YENIDEN DENENMEZ
--------------------------
`istemci.post()` zaten yeniden denemiyor ve zaman asimini
`DurumBilinmiyorHatasi` olarak firlatiyor. O hata GORULDUGUNDE yapilacak
sey yeniden gondermek DEGIL, MUTABAKAT: `acik_emirler()` ile IBKR'ye
"bu emir sana ulasti mi" diye sorulur. Yeniden gondermek, gonderilmis bir
emri IKINCI KEZ gondermek olabilir.

ONAY MESAJLARI BASTIRILMIYOR, GOSTERILIYOR
------------------------------------------
IBKR emir POST'una `order_id` yerine bir "order reply message" donebiliyor
ve emir o onaylanana kadar CALISMIYOR. Bunlar cogunlukla "fat finger"
korumalari — fiyat piyasadan %3 uzak, adet olagandisi, vb.

`/iserver/questions/suppress` ile bastirilabilirler. BASTIRMIYORUZ:
bedava bir koruma katmanini, sirf akis puruzsuz olsun diye atmak olurdu.
Mesaj kullaniciya GOSTERILIP ikinci bir onay isteniyor.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field

from .istemci import DurumBilinmiyorHatasi, IbkrHatasi, Istemci

log = logging.getLogger(__name__)

# Onay fisinin varsayilan omru. `onay.py`nin 24 saatiyle KARISTIRILMASIN.
FIS_OMRU_SN = 180.0

YONLER = frozenset({"BUY", "SELL"})
# Ilk surumde YALNIZCA bu ikisi. Bracket/trailing/stop emirleri kendi
# alanlarini ve kendi dogrulamalarini istiyor; kapsam disi birakmak,
# yarim desteklemekten guvenli.
TURLER = frozenset({"LMT", "MKT"})
SURELER = frozenset({"DAY", "GTC", "IOC", "OPG"})


class EmirReddedildi(IbkrHatasi):
    """Emir GONDERILMEDI — kapida durduruldu."""


@dataclass(frozen=True)
class EmirIstegi:
    """
    Degismez emir tanimi. `frozen=True` bilincli: parmak izi alindiktan
    sonra alan degistirilememeli.
    """

    hesap: str
    conid: str
    yon: str
    tur: str
    adet: float
    fiyat: float | None = None
    sure: str = "DAY"

    def dogrula(self) -> None:
        """Kapida duruyor. Sebebi ACIKCA soyluyor; sessiz ret yok."""
        if not self.hesap:
            raise EmirReddedildi("hesap bos")
        if not self.conid:
            raise EmirReddedildi("conid bos — sembol emir kimligi DEGIL")
        if self.yon not in YONLER:
            raise EmirReddedildi(f"yon {self.yon!r} — {sorted(YONLER)} olmali")
        if self.tur not in TURLER:
            raise EmirReddedildi(f"tur {self.tur!r} — {sorted(TURLER)} olmali")
        if self.sure not in SURELER:
            raise EmirReddedildi(f"sure {self.sure!r} — {sorted(SURELER)} olmali")
        if not isinstance(self.adet, (int, float)) or self.adet <= 0:
            raise EmirReddedildi(f"adet {self.adet!r} — pozitif olmali")
        if self.tur == "LMT" and (self.fiyat is None or self.fiyat <= 0):
            # LMT'de fiyatsiz gondermek, emri PIYASA emrine cevirmez —
            # IBKR reddeder. Ama biz once reddediyoruz ki sebep NET olsun.
            raise EmirReddedildi("LMT emri fiyatsiz olamaz")
        if self.tur == "MKT" and self.fiyat is not None:
            raise EmirReddedildi("MKT emrinde fiyat olmaz")

    def parmak_izi(self) -> str:
        """
        Emri TEK BASINA belirleyen ozet. Onay fisi bunu tasiyor.

        Alan sirasi sabit ve `sort_keys=True`: ayni emir her zaman ayni
        izi uretmeli, yoksa gecerli bir fis rastgele reddedilirdi.
        """
        ham = json.dumps({
            "hesap": self.hesap, "conid": str(self.conid), "yon": self.yon,
            "tur": self.tur, "adet": float(self.adet),
            "fiyat": None if self.fiyat is None else float(self.fiyat),
            "sure": self.sure,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(ham.encode()).hexdigest()

    def govde(self) -> dict:
        """IBKR emir bileti. Anahtarlar ve degerler BUYUK/KUCUK HARFE DUYARLI."""
        g = {
            "conid": int(self.conid),
            "side": self.yon,
            "orderType": self.tur,
            "quantity": self.adet,
            "tif": self.sure,
        }
        if self.fiyat is not None:
            g["price"] = self.fiyat
        return g

    def ozet(self) -> str:
        f = "" if self.fiyat is None else f" @ {self.fiyat}"
        return f"{self.yon} {self.adet} (conid {self.conid}) {self.tur}{f} {self.sure}"


@dataclass(frozen=True)
class OnayFisi:
    """
    Insan onayinin makine-okunur kaniti.

    `gonder()` bunu istiyor; bool ISTEMIYOR. Bool olsaydi
    `gonder(..., onaylandi=True)` yazan tek satir korumayi delerdi.
    Fis, ONAYLANAN emrin parmak izini tasiyor — baska bir emri
    gondermeye yaramaz.
    """

    parmak_izi: str
    verildi: float = field(default_factory=time.monotonic)
    kim: str = ""

    def dogrula(self, istek: EmirIstegi, omur_sn: float = FIS_OMRU_SN) -> None:
        if self.parmak_izi != istek.parmak_izi():
            raise EmirReddedildi(
                "onay fisi bu emre ait DEGIL — emir onaydan sonra degistirilmis")
        yas = time.monotonic() - self.verildi
        if yas > omur_sn:
            raise EmirReddedildi(
                f"onay fisi eskidi ({yas:.0f} sn > {omur_sn:.0f} sn) — "
                "fiyat degismis olabilir, yeniden onay gerekiyor")


@dataclass
class OnayMesaji:
    """
    IBKR'nin "once sunu teyit et" mesaji. Emir HENUZ CALISMIYOR.

    `mesaj_kodlari` bastirilabilir turleri gosteriyor ama BASTIRMIYORUZ
    — bunlar bedava fat-finger korumalari.
    """

    id: str
    metinler: list[str]
    mesaj_kodlari: list[str]

    def metin(self) -> str:
        return "\n".join(self.metinler)


@dataclass
class EmirYaniti:
    """Emir IBKR'ye ULASTI ve bir kimlik aldi."""

    emir_id: str
    durum: str
    ham: dict = field(default_factory=dict)


def _yaniti_coz(y) -> OnayMesaji | EmirYaniti | None:
    """
    IBKR emir yaniti IKI SEKILDEN biri olabilir ve ikisi de 200 doner:

        [{"id": "...", "message": [...], "messageIds": [...]}]   -> teyit iste
        {"order_id": "...", "order_status": "Submitted"}         -> kabul

    Ikisini ayirt etmemek, teyit bekleyen bir emri "gonderildi" sanmak
    olurdu — kullanici emrin calistigini zanneder, emir askida kalir.
    """
    kayit = y[0] if isinstance(y, list) and y else y
    if not isinstance(kayit, dict):
        return None
    if kayit.get("id") and kayit.get("message"):
        m = kayit.get("message")
        return OnayMesaji(
            id=str(kayit["id"]),
            metinler=[str(x) for x in (m if isinstance(m, list) else [m])],
            mesaj_kodlari=[str(x) for x in (kayit.get("messageIds") or [])],
        )
    if kayit.get("order_id"):
        return EmirYaniti(str(kayit["order_id"]),
                          str(kayit.get("order_status") or ""), kayit)
    return None


# ----------------------------------------------------------------------
def gonder(istemci: Istemci, istek: EmirIstegi, fis: OnayFisi,
           fis_omru_sn: float = FIS_OMRU_SN) -> OnayMesaji | EmirYaniti:
    """
    Emri IBKR'ye gonderir. `fis` OLMADAN CAGRILAMAZ.

    Doner:
        EmirYaniti  — emir kabul edildi, kimligi var
        OnayMesaji  — IBKR teyit istiyor, emir HENUZ CALISMIYOR

    Firlatir:
        EmirReddedildi         — kapida durduruldu, emir GONDERILMEDI
        DurumBilinmiyorHatasi  — zaman asimi; emir ULASMIS OLABILIR.
                                 YENIDEN GONDERME, once mutabakat yap.
    """
    istek.dogrula()
    fis.dogrula(istek, fis_omru_sn)

    log.info("[ibkr] emir gonderiliyor: %s (onay: %s)",
             istek.ozet(), fis.kim or "?")
    # GOVDE BIR DIZI. Degistirme ucunda ise NESNE — ayni aile, farkli
    # sekil. Karistirmak sessiz 400'lere yol acar.
    y = istemci.post(f"/iserver/account/{istek.hesap}/orders", [istek.govde()])

    sonuc = _yaniti_coz(y)
    if sonuc is None:
        raise IbkrHatasi(f"emir yaniti anlasilamadi: {str(y)[:200]}")
    if isinstance(sonuc, OnayMesaji):
        log.info("[ibkr] IBKR teyit istiyor (%s): %s",
                 ",".join(sonuc.mesaj_kodlari), sonuc.metin()[:160])
    else:
        log.info("[ibkr] emir kabul edildi: %s (%s)", sonuc.emir_id, sonuc.durum)
    return sonuc


@dataclass
class Onizleme:
    """
    `/whatif` sonucu — emir GONDERILMEDEN once IBKR'nin kendi cevabi.

    `hata` doluysa IBKR bu emri KABUL ETMEZ; onay ekraninda buton
    cikarmanin anlami yok.
    """

    tutar: str = ""
    komisyon: str = ""
    toplam: str = ""
    hata: str | None = None
    uyarilar: list[str] = field(default_factory=list)
    ham: dict = field(default_factory=dict)


def onizle(istemci: Istemci, istek: EmirIstegi) -> Onizleme:
    """
    Emri GONDERMEDEN onizler ve GERCEK KOMISYONU doner.

    Neden degerli: komisyonu tahmin etmek zorunda kalmiyoruz. Olculdu
    (2026-08-26) — 0,05 lot KO icin 4,25 USD tutar, 0,04 USD komisyon.
    Web'den okudugum "emir basina asgari 1 USD" rakami bu hesap icin
    YANLISTI (o Fixed fiyatlandirma; bu hesap Tiered). Tahmin yerine
    kaynaktan sormak yine kazandi.

    GOVDE SEKLI FARKLI: `/orders` duz DIZI isterken `/whatif`
    `{"orders": [...]}` istiyor. Ayni aile, ucuncu bir sekil.

    IBKR sarti: bu uctan once ilgili enstruman icin snapshot cagrilmis
    olmali (`piyasa.Piyasa` bunu zaten yapiyor).
    """
    istek.dogrula()
    try:
        y = istemci.post(f"/iserver/account/{istek.hesap}/orders/whatif",
                         {"orders": [istek.govde()]})
    except DurumBilinmiyorHatasi:
        # `/whatif` emir GONDERMIYOR; zaman asimi burada "bilinmeyen
        # durum" degil, yalnizca onizleme yapilamadi demek.
        return Onizleme(hata="onizleme zaman asimina ugradi")
    except IbkrHatasi as e:
        return Onizleme(hata=str(e))
    if not isinstance(y, dict):
        return Onizleme(hata="onizleme yaniti anlasilamadi")
    tutar = y.get("amount") if isinstance(y.get("amount"), dict) else {}
    return Onizleme(
        tutar=str(tutar.get("amount") or ""),
        komisyon=str(tutar.get("commission") or ""),
        toplam=str(tutar.get("total") or ""),
        hata=(str(y["error"]) if y.get("error") else None),
        uyarilar=[str(u) for u in (y.get("warns") or [])],
        ham=y,
    )


def teyit_et(istemci: Istemci, mesaj_id: str) -> OnayMesaji | EmirYaniti:
    """
    `/iserver/reply/{id}` ile teyit. ZINCIRLENEBILIR: teyit yanitinda
    BASKA bir onay mesaji gelebilir; cagiran donguyu kurmali.

    NOT — bu bir YENIDEN DENEME DEGIL, devam adimi. `gonder()` zaman
    asimina ugradiysa buraya gelinmez; oraya mutabakat gelir.
    """
    y = istemci.post(f"/iserver/reply/{mesaj_id}", {"confirmed": True})
    sonuc = _yaniti_coz(y)
    if sonuc is None:
        raise IbkrHatasi(f"teyit yaniti anlasilamadi: {str(y)[:200]}")
    return sonuc


def acik_emirler(istemci: Istemci, hesap: str | None = None) -> list[dict]:
    """
    `/iserver/account/orders` — 5 SANIYEDE BIR ISTEK sinirli (istemci
    bunu kendisi uyguluyor).

    MUTABAKATIN ARACI BUDUR: `DurumBilinmiyorHatasi` sonrasi "emrim
    ulasmis mi" sorusunun cevabi burada.
    """
    p = {"accountId": hesap} if hesap else None
    y = istemci.get("/iserver/account/orders", p)
    if isinstance(y, dict):
        return [r for r in (y.get("orders") or []) if isinstance(r, dict)]
    return []


def mutabakat(istemci: Istemci, istek: EmirIstegi) -> list[dict]:
    """
    Zaman asimindan sonra: bu emir IBKR'ye ULASMIS MI?

    Ayni conid ve yondeki acik/yeni emirleri doner. BOS DONMESI "emir
    gitmedi" ANLAMINA GELMEZ — emir henuz gorunmuyor da olabilir.
    Karar insana birakiliyor; kod kendi basina yeniden gondermiyor.
    """
    try:
        emirler = acik_emirler(istemci, istek.hesap)
    except IbkrHatasi as e:
        log.warning("[ibkr] mutabakat yapilamadi: %s", e)
        return []
    return [e for e in emirler
            if str(e.get("conid") or "") == str(istek.conid)
            and str(e.get("side") or "").upper() == istek.yon]


def durum(istemci: Istemci, hesap: str, emir_id: str) -> dict:
    y = istemci.get(f"/iserver/account/{hesap}/order/status/{emir_id}")
    return y if isinstance(y, dict) else {}


def iptal(istemci: Istemci, hesap: str, emir_id: str) -> dict:
    """
    IPTAL TALEBI — iptalin KENDISI degil.

    IBKR'nin kendi uyarisi: yanit "istegin alindigini" gosteriyor, emrin
    iptal edildigini DEGIL. Borsadaki bir emir (or. acilis muzayedesi
    yuzunden) iptal edilemeyebilir. Durum ayrica sorulmali.
    """
    y = istemci.delete(f"/iserver/account/{hesap}/order/{emir_id}")
    return y if isinstance(y, dict) else {}


__all__ = [
    "DurumBilinmiyorHatasi", "EmirIstegi", "EmirReddedildi", "EmirYaniti",
    "OnayFisi", "OnayMesaji", "Onizleme", "acik_emirler", "durum", "gonder",
    "iptal", "mutabakat", "onizle", "teyit_et",
]
