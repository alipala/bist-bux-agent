"""
SEC XBRL companyfacts — temel (fundamental) veri. KADEME 1.

edgar.py dosyalamalarin VARLIGINI getiriyordu ("Form 10-Q, 2026-06-30") ama
ICERIGINI degil. Bu modul sirketin dosyaladigi yapisal finansallari cekiyor:
gelir, brut kar, faaliyet kari, net kar, EPS, varlik, ozkaynak, nakit akisi.

Kaynak sirketin KENDI XBRL beyani — API anahtari yok, ucuncu taraf yok,
tahmin yok.

DONEM TUZAGI (en onemli kisim)
------------------------------
Ayni kavram ayni dosyalamada birden fazla donem icin gelir. Gercek ornek,
NVDA 2026 Q2 10-Q, "Revenues":
    90.805.000.000  start=2025-01-27  end=2025-07-27  -> 181 gun (6 aylik)
    46.743.000.000  start=2025-04-28  end=2025-07-27  ->  90 gun (ceyrek)
"Son degeri al" demek bu ikisini karistirmak demek. Bu yuzden start/end ve
gun sayisi saklaniyor; okuma tarafi donem bandina gore filtreliyor.

SEC'in `frame` alani (CY2025Q2, CY2025) takvim donemine normalize edilmis
kayitlari isaretler. Frame'i olmayan kayitlar kumulatif/ara donemdir.
"""
from __future__ import annotations

import logging
import time
from datetime import date

import httpx

from ..research.identity import UA
from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Cekilecek kavramlar. Sirketler ayni kalemi farkli etiketle raporluyor —
# gelir icin uc ayri kavram var; hangisi doluysa o kullanilir.
KAVRAMLAR = {
    # gelir tablosu (donemsel)
    "Revenues": "gelir",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "gelir",
    "SalesRevenueNet": "gelir",
    "GrossProfit": "brut_kar",
    "OperatingIncomeLoss": "faaliyet_kari",
    "NetIncomeLoss": "net_kar",
    "EarningsPerShareDiluted": "eps_seyreltilmis",
    "ResearchAndDevelopmentExpense": "ar_ge",
    "NetCashProvidedByUsedInOperatingActivities": "faaliyet_nakit_akisi",
    # bilanco (anlik)
    "Assets": "varlik",
    "Liabilities": "yukumluluk",
    "StockholdersEquity": "ozkaynak",
    "CashAndCashEquivalentsAtCarryingValue": "nakit",
    "LongTermDebtNoncurrent": "uzun_vadeli_borc",
}

# Bir sirket icin kac yil geriye gidilecek. XBRL 2009'a kadar veri tasiyor;
# hepsini yazmak DB'yi gereksiz sisirir.
GERIYE_YIL = 5


class XbrlCollector(BaseCollector):
    name = "xbrl"
    needs_browser = False

    def collect(self) -> CollectorResult:
        hedefler = self.db.research_targets()
        if not hedefler:
            return CollectorResult(self.name, "skipped", 0, "arastirma hedefi yok")

        kimlikler = {r["symbol"]: r for r in self.db.identities()}
        sinir = date.today().replace(year=date.today().year - GERIYE_YIL).isoformat()

        satirlar: list[tuple] = []
        bakilan, atlanan = 0, []

        with httpx.Client(headers={"User-Agent": UA}, timeout=60.0,
                          follow_redirects=True) as client:
            for h in hedefler:
                k = kimlikler.get(h["symbol"])
                # sqlite3.Row .get() desteklemez — anahtar erisimi kullan.
                cik = k["cik"] if k is not None else None
                if not cik or k["status"] not in ("dogrulandi", "elle"):
                    atlanan.append(h["symbol"])
                    continue

                bakilan += 1
                try:
                    satirlar += self._sirket(client, cik, h["id"], sinir)
                except Exception as e:              # noqa: BLE001
                    log.warning("[xbrl] %s alinamadi: %s", h["symbol"], e)
                time.sleep(0.15)                     # SEC: <10 istek/sn

        n = self.db.upsert_fundamentals(satirlar)
        notlar = f"{bakilan} sirket"
        if atlanan:
            notlar += f" · CIK yok, atlandi: {', '.join(atlanan[:8])}"
        log.info("[xbrl] %d sirket, %d finansal kayit", bakilan, n)
        return CollectorResult(self.name, "ok" if n else "partial", n, notlar)

    # ------------------------------------------------------------------
    def _sirket(self, client, cik: str, instrument_id: int, sinir: str) -> list[tuple]:
        r = client.get(COMPANYFACTS.format(cik=cik))
        r.raise_for_status()
        gaap = (r.json().get("facts") or {}).get("us-gaap") or {}

        out: list[tuple] = []
        for kavram in KAVRAMLAR:
            veri = gaap.get(kavram)
            if not veri:
                continue
            for birim, kayitlar in (veri.get("units") or {}).items():
                for x in kayitlar:
                    bitis = x.get("end")
                    if not bitis or bitis < sinir:
                        continue
                    baslangic = x.get("start")
                    gun = None
                    if baslangic:
                        try:
                            gun = (date.fromisoformat(bitis)
                                   - date.fromisoformat(baslangic)).days
                        except ValueError:
                            continue
                    out.append((
                        instrument_id, kavram, birim, baslangic, bitis, gun,
                        float(x["val"]), x.get("form"), x.get("fy"), x.get("fp"),
                        x.get("frame"), x.get("filed"), x.get("accn"),
                    ))
        return out
