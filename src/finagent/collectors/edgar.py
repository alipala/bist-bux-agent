"""
SEC EDGAR dosyalamalari — KADEME 1 (birincil kaynak).

Bu, ABD'de kote sirketler icin KAP'in karsiligidir: 8-K (ozel durum),
10-Q/10-K (finansal rapor), 6-K/20-F (yabanci ihracci), DEF 14A vb.
Sirketin DUZENLEYICIYE verdigi resmi beyandir — basin yorumu degil.

ANAHTAR: CIK. Ticker DEGIL.
    Ticker tahmini yanlis sirkete gidebiliyor ("Avantium" -> AVTX ->
    Avalo Therapeutics). Bu yuzden yalnizca research/identity.py tarafindan
    ADIYLA DOGRULANMIS kimlikler buraya girer.

API anahtari gerekmez; SEC yalnizca tanimlanabilir bir User-Agent ister.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from ..research.identity import UA
from ..storage.db import sha1
from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

# Yatirimci acisindan anlamli formlar. Tumunu cekmek gurultu yaratir
# (ownership formlari 3/4/5 gunde onlarca satir uretir).
ONEMLI_FORMLAR = {
    "8-K": "ozel durum",
    "6-K": "yabanci ihracci raporu",
    "10-Q": "ceyreklik finansal",
    "10-K": "yillik finansal",
    "20-F": "yillik finansal (yabanci)",
    "40-F": "yillik finansal (Kanada)",
    "DEF 14A": "genel kurul",
    "S-1": "halka arz izahnamesi",
    "424B4": "izahname",
    "SC 13D": "onemli pay bildirimi",
    "SC 13G": "onemli pay bildirimi",
    # EDGAR bu formlari uzun adla da donduruyor; kisa kodla eslestirmek
    # yetmiyordu ve SpaceX'in 13G'leri sessizce eleniyordu.
    "SCHEDULE 13D": "onemli pay bildirimi",
    "SCHEDULE 13G": "onemli pay bildirimi",
}


class EdgarCollector(BaseCollector):
    name = "edgar"
    needs_browser = False

    def collect(self) -> CollectorResult:
        from ..research import IdentityResolver

        hedefler = self.db.research_targets()
        if not hedefler:
            return CollectorResult(self.name, "skipped", 0,
                                   "arastirma hedefi yok (portfoy/izleme listesi bos)")

        resolver = IdentityResolver(self.s, self.db)
        gun = int(self.s.get("sources.edgar.lookback_days", 30))
        sinir = datetime.now(timezone.utc).date() - timedelta(days=gun)

        rows: list[dict] = []
        cozulemeyen: list[str] = []
        bakilan = 0

        # Elle atanan kimlikler KORUNUR. Aksi halde her calisma otomatik
        # cozumlemeyi tekrar calistirip kullanicinin duzeltmesini eziyor:
        # "SpaceX" otomatik kurala gore SEC'de eslesmiyor (SPACEX != SPACE
        # EXPLORATION...), kullanici /kimlik ile SPCX atadi, sonraki EDGAR
        # kosusu bunu silip yine "sec_disi" yapti ve dosyalamalar hic gelmedi.
        from ..research import Kimlik
        kayitli = {r["instrument_id"]: r for r in self.db.query(
            "SELECT * FROM identities WHERE method LIKE 'elle%' OR method = 'kullanici'")}

        with httpx.Client(headers={"User-Agent": UA}, timeout=30.0,
                          follow_redirects=True) as client:
            for h in hedefler:
                onceki = kayitli.get(h["id"])
                if onceki:
                    kimlik = Kimlik(
                        symbol=h["symbol"], name=h["name"], cik=onceki["cik"],
                        sec_ticker=onceki["sec_ticker"], sec_name=onceki["sec_name"],
                        exchange=onceki["exchange"], status=onceki["status"],
                        method=onceki["method"], note=onceki["note"])
                else:
                    kimlik = resolver.coz(h["symbol"], h["name"],
                                          h["asset_type"], h["venue"])
                    self.db.save_identity(h["id"], kimlik)

                if not kimlik.edgar_hazir:
                    if kimlik.status == "eslesmedi":
                        cozulemeyen.append(h["symbol"])
                    continue

                bakilan += 1
                try:
                    rows += self._sirket(client, kimlik, sinir)
                except Exception as e:              # noqa: BLE001
                    log.warning("[edgar] %s alinamadi: %s", kimlik.symbol, e)
                time.sleep(0.15)                     # SEC: <10 istek/sn

        n = self.db.upsert_disclosures(rows, source="sec")
        hata = None
        if cozulemeyen:
            hata = ("kimligi dogrulanamadi (arastirmaya alinmadi): "
                    + ", ".join(cozulemeyen))
        log.info("[edgar] %d sirket tarandi, %d dosyalama", bakilan, n)
        return CollectorResult(self.name, "partial" if cozulemeyen else "ok", n, hata)

    # ------------------------------------------------------------------
    def _sirket(self, client, kimlik, sinir) -> list[dict]:
        r = client.get(SUBMISSIONS.format(cik=kimlik.cik))
        r.raise_for_status()
        data = r.json()

        son = (data.get("filings") or {}).get("recent") or {}
        formlar = son.get("form") or []
        if not formlar:
            return []

        tarihler = son.get("filingDate") or []
        accs = son.get("accessionNumber") or []
        dokumanlar = son.get("primaryDocument") or []
        aciklamalar = son.get("primaryDocDescription") or []
        kalemler = son.get("items") or []
        rapor_tarihleri = son.get("reportDate") or []

        cik_int = str(int(kimlik.cik))
        out = []
        for i, form in enumerate(formlar):
            if form not in ONEMLI_FORMLAR:
                continue
            try:
                tarih = datetime.strptime(tarihler[i], "%Y-%m-%d").date()
            except (IndexError, ValueError):
                continue
            if tarih < sinir:
                # recent[] tarihe gore azalan sirali; ilk eski kayitta durabiliriz
                break

            acc = accs[i].replace("-", "") if i < len(accs) else ""
            doc = dokumanlar[i] if i < len(dokumanlar) else ""
            url = ARCHIVE.format(cik=cik_int, acc=acc, doc=doc) if acc and doc else \
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={kimlik.cik}"

            baslik = aciklamalar[i] if i < len(aciklamalar) and aciklamalar[i] else \
                ONEMLI_FORMLAR[form]
            govde_parcalari = [f"Form {form}"]
            if i < len(kalemler) and kalemler[i]:
                govde_parcalari.append(f"Maddeler: {kalemler[i]}")
            if i < len(rapor_tarihleri) and rapor_tarihleri[i]:
                govde_parcalari.append(f"Donem: {rapor_tarihleri[i]}")

            out.append({
                "id": sha1(f"sec|{kimlik.cik}|{accs[i] if i < len(accs) else url}"),
                "published_at": f"{tarih.isoformat()} 00:00:00",
                "symbol": kimlik.symbol,
                "company": kimlik.sec_name,
                "category": form,
                "title": f"{form} — {baslik}",
                "url": url,
                "body": " | ".join(govde_parcalari),
            })
        return out
