"""
KAP (Kamuyu Aydinlatma Platformu) bildirim toplayici.

KAP sayfasi JS ile render edildigi icin tarayici uzerinden okunur.
Selector'lar config/selectors.yaml'dan gelir:
    python run.py discover --site kap
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
    needs_browser = True

    def collect(self) -> CollectorResult:
        if not self.require_selectors("row", "cell_title"):
            return CollectorResult(self.name, "skipped", 0, "selector'lar tanimli degil")

        url = self.sel("url") or "https://www.kap.org.tr/tr"
        watch = self.s.bist_watchlist
        rows: list[dict] = []

        with self.browser.page(url) as pg:
            # KAP ana sayfasi arka planda surekli istek atiyor; "networkidle"
            # neredeyse hicbir zaman gerceklesmiyor. Kisa tutup asil beklemeyi
            # satir selector'ine birakiyoruz — aksi halde bos yere 20 sn gidiyor
            # ve soguk yuklemede selector timeout'una zaman kalmiyor.
            try:
                pg.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            cookie = self.sel("cookie_accept")
            if cookie:
                try:
                    pg.click(cookie, timeout=3000)
                except Exception:
                    pass

            try:
                pg.wait_for_selector(self.sel("row"), timeout=45000)
            except Exception:
                return CollectorResult(self.name, "error", 0,
                                       f"satir selector'i eslesmedi: {self.sel('row')}")

            elements = pg.query_selector_all(self.sel("row"))
            log.info("[kap] %d satir bulundu", len(elements))

            for el in elements:
                def _txt(key: str) -> str:
                    css = self.sel(key)
                    if not css:
                        return ""
                    node = el.query_selector(css)
                    return (node.inner_text().strip() if node else "")

                title = _txt("cell_title")
                if not title:
                    continue

                # BILDIRIM URL'SI.
                #
                # KAP satirlari <a> icermez; bildirim JS ile acilir. Uzun
                # sure "link cikarilamiyor" diye bos birakildi ve 72
                # bildirimin 72'si URL'siz kaldi — yani kademe 1 (resmi
                # dosyalama) kaynak BAGIMSIZ DOGRULANABILIR degildi.
                #
                # Cozum satirin icinde duruyordu: checkbox'in `id`'si
                # bildirim numarasi ve KAP'in kalici adresi
                # `.../tr/Bildirim/<id>`. Once klasik <a href> denenir
                # (sayfa degisirse diye), sonra bu kalip.
                href = ""
                link_sel = self.sel("cell_link")
                if link_sel:
                    a = el.query_selector(link_sel)
                    if a:
                        href = a.get_attribute("href") or ""
                        if not href:
                            ozellik = self.sel("link_attr") or "id"
                            kalip = self.sel("link_kalip") or ""
                            no = (a.get_attribute(ozellik) or "").strip()
                            # Yalnizca SAYI kabul et: sayfa degisip buraya
                            # baska bir id gelirse uydurma URL uretmeyelim.
                            if no.isdigit() and "{id}" in kalip:
                                href = kalip.format(id=no)
                        if href.startswith("/"):
                            href = "https://www.kap.org.tr" + href

                company = _txt("cell_company")
                # "Kod" kolonu varsa onu kullan; yoksa watchlist eslestirmesine dus.
                symbol = _txt("cell_symbol").split("\n")[0].strip() or None
                if not symbol:
                    syms = extract_symbols(f"{company} {title}", watch)
                    symbol = syms[0] if syms else None

                published = _parse_kap_time(_txt("cell_time"), self.s.get("timezone", "Europe/Istanbul"))
                rows.append({
                    # published_at + sirket + baslik -> ayni bildirim tekrar
                    # toplandiginda cakisir, farkli gunler cakismaz.
                    "id": None,
                    "published_at": published,
                    "symbol": symbol,
                    "company": company or None,
                    "category": _txt("cell_category") or None,
                    "title": title,
                    "url": href or None,
                    "body": _txt("cell_summary") or None,
                })

        # sha1 anahtari url bos oldugunda cakisir; benzersiz anahtari burada kur.
        from ..storage.db import sha1
        for r in rows:
            r["id"] = sha1(f"kap|{r['published_at']}|{r['company']}|{r['title']}")

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
