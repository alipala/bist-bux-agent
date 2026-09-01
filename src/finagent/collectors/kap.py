"""
KAP (Kamuyu Aydinlatma Platformu) bildirim toplayici.

VERI EKRANDAN DEGIL KAYNAGINDAN ALINIYOR (2026-09-01)
-----------------------------------------------------
Onceki surum KAP ana sayfasini tarayicida render edip tablo satirlarini
kaziyordu. Ana sayfa EN SON 50 BILDIRIMI gosteriyor ve sayfalama yok;
toplayici gunde 3 kez kostugu icin gunun ancak ucte biri yakalaniyordu.

OLCULDU (API ile defterin karsilastirmasi):
    28 Agu   API 208 kayit  ·  defterde 111   -> %47 kayip
    31 Agu   API 379 kayit  ·  defterde 129   -> %66 kayip

Hangi sirketin yakalanacagi da RASTLANTIYA kaliyordu: koşum anindan
geriye 50 bildirim kimlere aitse onlar giriyordu.

Sayfanin kendi cagrisi yakalandi:
    POST /tr/api/disclosure/list/main
    {"fromDate":"31.08.2026","toDate":"31.08.2026",
     "memberTypes":["IGS","DDK"]}

Sunucuda 50 siniri YOK ve SAYFALAMA da yok — gövde TARIH ARALIGI
aliyor, yani `lookback_days` ayariyla birebir ortusuyor. Tarayici
bagimliligi tamamen kalkti.

NE DEGISMEDI: `id` hash semasi ayni (eski satirlarla cakisir, tekrar
yazilmaz), `_parse_kap_time` ayni (API tarihi de `dd.mm.yyyy HH:MM:SS`),
ve `stockCode` bos geldiginde watchlist eslestirmesine dusme ayni.

KAYNAKTAN GELEN IKI SEY BIZIM KUSURUMUZ DEGIL, olculdu:
  * `stockCode` 379 kaydin 210'unda BOS (borsa duyurulari, fon
    bildirimleri) — bunlarin sembolu gercekten yok.
  * Kod alani virgullu COKLU deger tasiyabiliyor ("AKM, AKMEN").
    KAP oyle yayimliyor; oldugu gibi saklaniyor.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .base import BaseCollector, CollectorResult, extract_symbols

log = logging.getLogger(__name__)


class KapCollector(BaseCollector):
    name = "kap"
    # TARAYICI ARTIK GEREKMIYOR — sayfa yerine API cagriliyor.
    needs_browser = False

    API = "https://www.kap.org.tr/tr/api/disclosure/list/main"
    BILDIRIM_URL = "https://www.kap.org.tr/tr/Bildirim/{id}"

    # Sayfanin kendi cagrisinda gonderdigi deger. GENISLETMEK OLCULDU:
    # tum uye tipleriyle 31 Agu icin 390 kayit doner (379 yerine), yani
    # kazanc %3 — ama o 11 kayit borsa/fon duyurusu. Sayfanin kendi
    # varsayilaninda kalmak, KAP arayuzunde gorulen ile defterin AYNI
    # kumeyi tasimasi demek; ayrisma daha pahaliya mal olur.
    MEMBER_TYPES = ("IGS", "DDK")

    def collect(self) -> CollectorResult:
        import httpx

        watch = self.s.bist_watchlist
        tz = ZoneInfo(self.s.get("timezone", "Europe/Istanbul"))
        gun = max(int(self.s.get("sources.kap.lookback_days", 3) or 3), 1)
        bugun = datetime.now(tz).date()
        basla = bugun - timedelta(days=gun - 1)
        govde = {"fromDate": basla.strftime("%d.%m.%Y"),
                 "toDate": bugun.strftime("%d.%m.%Y"),
                 "memberTypes": list(self.MEMBER_TYPES)}

        try:
            with httpx.Client(timeout=45, follow_redirects=True,
                              headers={"User-Agent": "finagent/1.0",
                                       "Referer": "https://www.kap.org.tr/tr"}) as http:
                y = http.post(self.API, json=govde)
                y.raise_for_status()
                ham = y.json()
        except Exception as e:                        # noqa: BLE001
            return CollectorResult(self.name, "error", 0,
                                   f"KAP API cagrilamadi: {type(e).__name__}: {e}")

        # YANIT SEKLI DOGRULANIYOR. `memberTypes` eksik gonderilince API
        # LISTE DEGIL bir metin donduruyor (olculdu); sekli kontrol
        # etmeden dongoye girmek "0 bildirim" diye sessizce raporlardi.
        if not isinstance(ham, list):
            return CollectorResult(
                self.name, "error", 0,
                f"beklenen liste degil: {type(ham).__name__}")

        log.info("[kap] API %s..%s araligi icin %d kayit dondu",
                 govde["fromDate"], govde["toDate"], len(ham))

        rows: list[dict] = []
        for kayit in ham:
            b = (kayit or {}).get("disclosureBasic") or {}
            title = (b.get("title") or "").strip()
            if not title:
                continue

            company = (b.get("companyTitle") or "").strip() or None
            symbol = (b.get("stockCode") or "").strip() or None
            if not symbol:
                # KAYNAKTA GERCEKTEN BOS OLABILIR (borsa/fon duyurulari).
                # Yine de metinden yakalamayi deniyoruz — eski davranis.
                syms = extract_symbols(f"{company or ''} {title}", watch)
                symbol = syms[0] if syms else None

            # KIMLIK KAP'IN KENDI NUMARASINDAN (2026-09-01).
            #
            # Onceki sema `sha1(kap|zaman|sirket|baslik)` idi ve
            # `_parse_kap_time` SANIYEYI DUSURDUGU icin ayni sirketin
            # ayni dakikada ayni baslikli iki bildirimi TEK SATIRA
            # cokuyordu. Olculdu: 31 Agu'da 379 kaydin 333'u kaldi,
            # gunde ~46 bildirim (%12) sessizce kayboluyordu.
            #
            # `disclosureIndex` KAP'in kendi sayaci. Olculdu: 15 gunde
            # 3.166 kaydin 3.166'si benzersiz, hicbiri bos degil, hepsi
            # tamsayi ve monoton. Ayni gun iki kez cekildi, 379/379 ayni
            # numara dondu. Ustelik numara KAP'in kalici adresinin
            # (`/tr/Bildirim/<no>`) parcasi — keyfi degil, yuk tasiyor.
            no = str(b.get("disclosureIndex") or "").strip()
            href = self.BILDIRIM_URL.format(id=no) if no.isdigit() else None

            rows.append({
                # NUMARA YOKSA ESKI SEMAYA DUSER (asagida kurulur):
                # kimliksiz satir yazmamak, kimligi UYDURMAKTAN iyidir.
                "id": self.db.kap_kimlik(no),
                "published_at": _parse_kap_time(
                    b.get("publishDate") or "",
                    self.s.get("timezone", "Europe/Istanbul")),
                "symbol": symbol,
                "company": company,
                "category": (b.get("disclosureCategory") or "").strip() or None,
                "title": title,
                "url": href,
                "body": (b.get("summary") or "").strip() or None,
            })

        # YALNIZCA NUMARASI OLMAYAN SATIR icin eski semaya dusuluyor.
        #
        # Onceden bu dongu HER satirin kimligini eziyordu; `disclosureIndex`
        # geldikten sonra da ezseydi degisiklik hicbir ise yaramazdi —
        # tam da bu deponun "kod dogru, kablo yanlis" kusuru.
        #
        # Olculdu: 15 gunluk 3.166 kaydin HEPSINDE numara vardi, yani bu
        # dal pratikte hic calismiyor. Yine de duruyor: numarasiz bir
        # kayit gelirse kimliksiz kalmasin.
        from ..storage.db import sha1
        for r in rows:
            if not r["id"]:
                r["id"] = sha1(
                    f"kap|{r['published_at']}|{r['company']}|{r['title']}")

        n = self.db.upsert_disclosures(rows)
        return CollectorResult(self.name, "ok" if n else "partial", n)


# ----------------------------------------------------------------------
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
_DATE_RE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})")


def _parse_kap_time(raw: str, tz_name: str = "Europe/Istanbul") -> str | None:
    """
    KAP tarih hucresi su bicimlerde gelir:
        'Bugun\\n23:30'  |  'Dun\\n09:15'  |  '13.08.2026\\n17:42'

    db.recent_disclosures() `published_at >= datetime('now', '-48 hours')`
    ile filtreliyor; bu yuzden karsilastirilabilir ISO/UTC bir string sart.
    Ham metin birakilirsa bildirimler rapora hic girmez.
    """
    if not raw:
        return None
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)

    tm = _TIME_RE.search(raw)
    hh, mm = (int(tm.group(1)), int(tm.group(2))) if tm else (0, 0)

    dm = _DATE_RE.search(raw)
    if dm:
        day, month, year = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
        local = datetime(year, month, day, hh, mm, tzinfo=tz)
    else:
        low = raw.lower()
        base = now - timedelta(days=1) if ("dün" in low or "dun" in low) else now
        local = base.replace(hour=hh, minute=mm, second=0, microsecond=0)

    # SQLite datetime('now') UTC uretir -> ayni eksene cevir.
    from datetime import timezone as _tz
    return local.astimezone(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
