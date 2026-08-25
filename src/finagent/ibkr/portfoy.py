"""
IBKR portfoy okuma — SALT OKUMA, veritabanina yazmaz.

SIRA ZORUNLU
------------
IBKR: "/v1/api/portfolio/accounts or /portfolio/subaccounts must be called
prior to this endpoint." Yani hesap listesi alinmadan pozisyon/ledger
cagrilari calismaz. Bu sinif sirayi KENDISI tutuyor; cagiranin hatirlamasi
gerekmiyor. `/portfolio/accounts` ayrica 5 saniyede BIR istekle sinirli,
o yuzden sonuc onbellekleniyor.

SEMBOL `description` ALANINDA, `symbol` DIYE BIR ALAN YOK
---------------------------------------------------------
Resmi semada pozisyon nesnesinin alanlari: assetClass, avgCost, avgPrice,
conid, currency, description, group, marketPrice, marketValue, position,
realizedPnl, secType, sector, timestamp, unrealizedPnl.

`symbol` YOK. Sembol `description` icinde ("Contract's local symbol").
Naif bir `r.get("symbol")` sessizce bos donerdi ve pozisyonlar isimsiz
yazilirdi — bu depoda "bir cıplak ticker kimlik degildir" dersi zaten var.

`avgCost` NULL OLABILIR
-----------------------
IBKR'nin kendi ornek yanitinda `avgCost: null` ve `avgPrice: 262.24`.
Ikisi hisse icin genelde ayni; turevlerde `avgCost` carpani icerir.
`avgCost` tercih ediliyor, yoksa `avgPrice`. IKISI DE yoksa alan NULL
kaliyor ve pnl yuzdesi HESAPLANMIYOR — uydurulmus bir maliyet, uydurulmus
bir getiri demektir.

PARA BIRIMI HER SATIRDA TASINIYOR
---------------------------------
Bu deponun en pahali hatasi para birimiydi: 17 pozisyonun 14'unde ~%15,7
sapma (EUR/USD kuru kadar). IBKR cok para birimli — ledger EUR, USD, GBP
satirlarini AYRI donduruyor ve pozisyonun para birimi hesabin taban para
biriminden FARKLI olabilir. Hicbir yerde varsayilan para birimi yok.

`BASE` BIR PARA BIRIMI DEGIL
----------------------------
Ledger yanitinda `USD`, `EUR` gibi gercek para birimlerinin yaninda bir de
`BASE` anahtari var: hesabin taban para biriminde TOPLAM. Onu bir para
birimi sanip listeye katmak, toplami iki kez saymak olurdu.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .istemci import IbkrHatasi, Istemci

log = logging.getLogger(__name__)

# Ledger yanitindaki bu anahtar bir para birimi degil, taban para
# birimindeki TOPLAM.
TOPLAM_ANAHTARI = "BASE"


def _sayi(v) -> float | None:
    """
    IBKR sayilari tutarsiz dondurüyor: portfoy uclari float, piyasa verisi
    ucu ise STRING ve binlik ayracli ("1,300"). Tek yerde tolere ediliyor.
    Cozulemeyen deger None olur — 0.0 DEGIL, cunku "bilmiyorum" ile
    "sifir" ayri seylerdir.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class Hesap:
    kimlik: str
    kagit_mi: bool | None
    para_birimi: str | None
    tip: str | None
    islem_erisimi: bool | None


@dataclass
class Nakit:
    para_birimi: str
    nakit: float | None
    netlik: float | None
    hisse_degeri: float | None
    kur: float | None


@dataclass
class Pozisyon:
    conid: str | None
    sembol: str | None
    adet: float | None
    ort_maliyet: float | None
    son_fiyat: float | None
    piyasa_degeri: float | None
    pnl_abs: float | None
    pnl_pct: float | None
    para_birimi: str | None
    varlik_sinifi: str | None
    sektor: str | None

    def db_satiri(self) -> dict:
        """`db.insert_positions()` bekledigi bicim."""
        return {
            "symbol": self.sembol,
            "quantity": self.adet,
            "avg_cost": self.ort_maliyet,
            "last_price": self.son_fiyat,
            "market_value": self.piyasa_degeri,
            "pnl_abs": self.pnl_abs,
            "pnl_pct": self.pnl_pct,
            "currency": self.para_birimi,
            "asset_type": (self.varlik_sinifi or "").lower() or None,
            "name": None,
        }


class Portfoy:
    def __init__(self, istemci: Istemci):
        self.istemci = istemci
        self._hesaplar: list[Hesap] | None = None

    # ------------------------------------------------------------------
    def hesaplar(self, tazele: bool = False) -> list[Hesap]:
        """
        `/portfolio/accounts` — diger butun /portfolio cagrilarindan ONCE.

        Onbellekleniyor cunku uc 5 saniyede BIR istekle sinirli ve hesap
        listesi gun icinde degismiyor.
        """
        if self._hesaplar is not None and not tazele:
            return self._hesaplar
        veri = self.istemci.get("/portfolio/accounts")
        cikti: list[Hesap] = []
        if isinstance(veri, list):
            for h in veri:
                if not isinstance(h, dict):
                    continue
                kimlik = str(h.get("accountId") or h.get("id") or "").strip()
                if not kimlik:
                    continue
                cikti.append(Hesap(
                    kimlik=kimlik,
                    kagit_mi=self.kagit_mi(h),
                    para_birimi=h.get("currency"),
                    tip=h.get("type"),
                    islem_erisimi=h.get("brokerageAccess"),
                ))
        self._hesaplar = cikti
        return cikti

    @staticmethod
    def kagit_mi(hesap: dict) -> bool | None:
        """
        Kagit hesap mi? ORTAM DEGISKENINE DEGIL, SUNUCUDAN DONENE bakar.

        IBKR'de canli/kagit bir bayrak degil, ayri bir KULLANICI ADI.
        `.env`'de "paper" yazmasi hicbir sey kanitlamaz. Tek mesru kanit
        bu yanit: kagit hesap kimlikleri "DU" ile baslar ve `type`
        "DEMO" doner.

        Emin olamadigimizda None doner — "canli" DEGIL. Bilinmeyeni
        canli saymak, kagit sandigi hesaba gercek emir gondermenin yolu.
        """
        tip = str(hesap.get("type") or "").upper()
        kimlik = str(hesap.get("accountId") or hesap.get("id") or "")
        if tip == "DEMO" or kimlik.startswith("DU"):
            return True
        if tip or kimlik.startswith("U"):
            return False
        return None

    def _hesap_kimligi(self, hesap: str | None) -> str:
        """Sira zorunlulugunu BURADA uyguluyoruz; cagiran unutabilir."""
        hepsi = self.hesaplar()
        if not hepsi:
            raise IbkrHatasi("hesap listesi bos — /portfolio/accounts veri dondurmedi")
        if hesap:
            for h in hepsi:
                if h.kimlik == hesap:
                    return h.kimlik
            raise IbkrHatasi(f"hesap bulunamadi: {hesap}")
        if len(hepsi) > 1:
            # Birden fazla hesapta SESSIZCE ilkini secmek, yanlis
            # portfoye bakmanin en kolay yolu.
            raise IbkrHatasi(
                f"{len(hepsi)} hesap var, hangisi belirtilmeli: "
                + ", ".join(h.kimlik for h in hepsi))
        return hepsi[0].kimlik

    # ------------------------------------------------------------------
    def nakit(self, hesap: str | None = None) -> dict[str, Nakit]:
        """
        `/portfolio/{id}/ledger` — para birimi basina nakit.

        `BASE` anahtari DISARIDA birakiliyor: o bir para birimi degil,
        taban para biriminde toplam. Toplami ayrica `toplam_netlik()`
        veriyor.
        """
        kimlik = self._hesap_kimligi(hesap)
        veri = self.istemci.get(f"/portfolio/{kimlik}/ledger")
        cikti: dict[str, Nakit] = {}
        if isinstance(veri, dict):
            for pb, v in veri.items():
                if pb == TOPLAM_ANAHTARI or not isinstance(v, dict):
                    continue
                cikti[pb] = Nakit(
                    para_birimi=pb,
                    nakit=_sayi(v.get("cashbalance")),
                    netlik=_sayi(v.get("netliquidationvalue")),
                    hisse_degeri=_sayi(v.get("stockmarketvalue")),
                    kur=_sayi(v.get("exchangerate")),
                )
        return cikti

    def toplam_netlik(self, hesap: str | None = None) -> tuple[float | None, str | None]:
        """Taban para biriminde net likidite. (deger, para_birimi)."""
        kimlik = self._hesap_kimligi(hesap)
        veri = self.istemci.get(f"/portfolio/{kimlik}/ledger")
        if not isinstance(veri, dict):
            return None, None
        taban = veri.get(TOPLAM_ANAHTARI)
        if not isinstance(taban, dict):
            return None, None
        pb = next((h.para_birimi for h in self.hesaplar() if h.kimlik == kimlik), None)
        return _sayi(taban.get("netliquidationvalue")), pb

    # ------------------------------------------------------------------
    def ozet(self, hesap: str | None = None) -> dict[str, tuple[float | None, str | None]]:
        """
        `/portfolio/{id}/summary` — 40+ alan doner; ilgilendiklerimizi
        aliyoruz. Her deger `{amount, currency, ...}` sarmalinda geliyor,
        yani PARA BIRIMI ALANIN KENDISINDE — cikartip atmak yok.
        """
        kimlik = self._hesap_kimligi(hesap)
        veri = self.istemci.get(f"/portfolio/{kimlik}/summary")
        istenen = ("netliquidation", "totalcashvalue", "availablefunds",
                   "buyingpower", "excessliquidity", "equitywithloanvalue")
        cikti: dict[str, tuple[float | None, str | None]] = {}
        if isinstance(veri, dict):
            for k in istenen:
                v = veri.get(k)
                if isinstance(v, dict):
                    cikti[k] = (_sayi(v.get("amount")), v.get("currency"))
        return cikti

    # ------------------------------------------------------------------
    def pozisyonlar(self, hesap: str | None = None) -> list[Pozisyon]:
        """
        `/portfolio2/{id}/positions` — onbelleksiz surum.

        Eski `/portfolio/{id}/positions/{pageId}` onbellekli; IBKR yeni
        ucu "near-real time updates and removes caching" diye tanimliyor.
        Portfoy degerlemesi icin bayat veri, yanlis veriden ayirt
        edilemez.
        """
        kimlik = self._hesap_kimligi(hesap)
        veri = self.istemci.get(f"/portfolio2/{kimlik}/positions")
        cikti: list[Pozisyon] = []
        if not isinstance(veri, list):
            return cikti
        for r in veri:
            if not isinstance(r, dict):
                continue
            adet = _sayi(r.get("position"))
            # KAPANMIS pozisyon satirda kalabiliyor (adet 0). Portfoye
            # sifir adetli bir satir yazmak, "elimde var" gibi gorunur.
            if adet is None or adet == 0:
                continue
            # `avgCost` IBKR'nin kendi orneginde bile null olabiliyor.
            maliyet = _sayi(r.get("avgCost"))
            if maliyet is None:
                maliyet = _sayi(r.get("avgPrice"))
            pnl = _sayi(r.get("unrealizedPnl"))
            # Yuzde YALNIZCA maliyet biliniyorsa. Bilinmiyorken
            # piyasa degerinden geri hesaplamak, uydurulmus bir
            # maliyetten uydurulmus bir getiri uretirdi.
            pnl_pct = None
            if pnl is not None and maliyet not in (None, 0) and adet:
                temel = abs(maliyet * adet)
                if temel:
                    pnl_pct = pnl / temel * 100.0
            conid = r.get("conid")
            cikti.append(Pozisyon(
                conid=str(conid) if conid not in (None, "") else None,
                # `symbol` DIYE BIR ALAN YOK — sembol `description`'da.
                sembol=(str(r.get("description") or "").strip().upper() or None),
                adet=adet,
                ort_maliyet=maliyet,
                son_fiyat=_sayi(r.get("marketPrice")),
                piyasa_degeri=_sayi(r.get("marketValue")),
                pnl_abs=pnl,
                pnl_pct=pnl_pct,
                para_birimi=r.get("currency"),
                varlik_sinifi=r.get("assetClass") or r.get("secType"),
                sektor=r.get("sector"),
            ))
        return cikti
