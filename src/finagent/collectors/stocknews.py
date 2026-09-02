"""
Enstruman bazli haber taramasi — KADEME 2/3 (ikincil kaynak).

Kademe 1 (SEC EDGAR, KAP) sirketin KENDI beyanidir ve edgar.py/kap.py'de
toplanir. Bu modul onun uzerine basin kapsamini ekler.

FILTRELEME
----------
Ham akis cogunlukla SEO icerik ciftligidir (olcum icin research/sources.py).
Bu yuzden her haber, ASIL YAYINCISINA gore siniflandirilip saklanir; analiz
katmani yalnizca Kademe 1-2'yi kanit sayar. Kademe 3-4 silinmez — sayilir ve
"ilgi/gurultu" gostergesi olarak kalir, ama iddiaya dayanak yapilmaz.

SORGU
-----
Anahtar TICKER DEGIL, SIRKET ADIDIR. Ticker aramasi cok gurultulu:
"NOW" (ServiceNow) veya "ING" gibi semboller gundelik kelimelerle cakisiyor.
Kimligi dogrulanmis ad + tirnak icinde tam eslesme kullanilir.
"""
from __future__ import annotations

import logging
import time
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from ..research.sources import kademe, sirket_kaynagi, sirket_kaynagi_kademe
from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

GOOGLE_NEWS = ("https://news.google.com/rss/search"
               "?q={q}+when:{gun}d&hl=en-US&gl=US&ceid=US:en")


class StockNewsCollector(BaseCollector):
    name = "stocknews"
    needs_browser = True   # yonlendirme linklerini cozmek icin

    def collect(self) -> CollectorResult:
        hedefler = self.db.research_targets()
        if not hedefler:
            return CollectorResult(self.name, "skipped", 0, "arastirma hedefi yok")

        kimlikler = self.db.kimlik_haritasi()
        gun = int(self.s.get("sources.stocknews.lookback_days", 7))
        basina = int(self.s.get("sources.stocknews.max_per_instrument", 25))

        rows: list[dict] = []
        sayac = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
        atlanan: list[str] = []

        with httpx.Client(headers={"User-Agent": UA}, timeout=25.0,
                          follow_redirects=True) as client:
            for h in hedefler:
                kimlik = kimlikler.get(h["id"])
                # Kimligi cozulmemis enstruman TARANMAZ: yanlis sirketin
                # haberini dogru sirkete baglamaktansa hic haber olmasi iyidir.
                if not kimlik or kimlik["status"] == "eslesmedi":
                    atlanan.append(h["symbol"])
                    continue

                sorgu = self._sorgu_adi(h, kimlik)
                if not sorgu:
                    atlanan.append(h["symbol"])
                    continue

                try:
                    yeni = self._ara(client, sorgu, h["symbol"], gun, basina,
                                     sirket_adi=sorgu)
                except Exception as e:              # noqa: BLE001
                    log.warning("[stocknews] %s alinamadi: %s", h["symbol"], e)
                    continue

                for r in yeni:
                    sayac[r["tier"]] = sayac.get(r["tier"], 0) + 1
                rows += yeni
                time.sleep(0.4)                      # nazik ol

        # KANIT linklerini gercek yayinci URL'sine cevir. Yalnizca kademe
        # 1-2: rapor zaten sadece onlari kaynak gosteriyor, kalanini cozmek
        # bosa tarayici zamani olurdu.
        if self.browser is not None:
            from ..research.resolve_links import google_link_mi, resolve_batch
            hedef = [r["url"] for r in rows
                     if r["tier"] in (1, 2) and google_link_mi(r["url"])]
            harita = resolve_batch(self.browser, sorted(set(hedef)))
            for r in rows:
                yeni = harita.get(r["url"])
                if yeni:
                    r["url"] = yeni
                # Gercek URL elde edilince sirket kanalinin TURU belli olur:
                # kurumsal duyuru mu, muhendislik blogu mu?
                if r["tier"] == 1:
                    k = sirket_kaynagi_kademe(r["publisher"], r.get("_sirket"), r["url"])
                    if k is not None:
                        r["tier"] = k
        else:
            log.warning("[stocknews] tarayici yok -> kaynak linkleri "
                        "news.google.com yonlendirmesi olarak kalacak")

        n = self.db.upsert_news(rows)
        guvenilir = sayac.get(1, 0) + sayac.get(2, 0)
        log.info("[stocknews] %d haber (kanit sayilabilir: %d) — kademe dagilimi %s",
                 n, guvenilir, sayac)

        notlar = f"kanit: {guvenilir}/{n}"
        if atlanan:
            notlar += f" · kimlik yok, atlandi: {', '.join(atlanan[:6])}"
        return CollectorResult(self.name, "ok" if n else "partial", n, notlar)

    # ------------------------------------------------------------------
    def tek_sembol(self, symbol: str) -> tuple[int, str | None]:
        """
        TEK sembol icin haber ceker. (yazilan, engel) doner.

        NEDEN `collect()`TEN AYRI: `collect()` TUM arastirma hedeflerini
        tariyor — 20 Agustos'ta 27 sembol, aralarinda 0,4 sn nezaket
        beklemesiyle ~40 saniye. O sure sohbet is parcaciginda kabul
        edilemez; olculmus 50 dakikalik kilitlenme tam olarak agir bir
        collector'un sohbette senkron kosmasindan cikmisti. Burasi tek
        bir RSS istegi: saniyeler.

        TARAYICI KULLANILMAZ. Yani kademe 1-2 linkleri google
        yonlendirmesi olarak kalir — `collect()` bunu tarayiciyla
        cozuyor. Sohbette dogru cevabi GECIKTIRMEKTENSE linki ham
        birakmak yeglenir; zaten bir sonraki zamanlanmis kosu cozecek.
        """
        e = self.db.query(
            "SELECT id, symbol, name FROM instruments WHERE symbol = ? LIMIT 1",
            (symbol,))
        if not e:
            return 0, f"{symbol} katalogda yok"
        hedef = e[0]
        kimlik = self.db.query(
            "SELECT * FROM identities WHERE instrument_id = ?", (hedef["id"],))
        kimlik = kimlik[0] if kimlik else None
        if kimlik is not None and kimlik["status"] == "eslesmedi":
            # Yanlis sirketin haberini dogru sirkete baglamaktansa hic
            # haber olmasi iyidir — `collect()` ile ayni kural.
            return 0, f"{symbol} kimligi cozulmemis (eslesmedi)"
        sorgu = self._sorgu_adi(hedef, kimlik)
        if not sorgu:
            return 0, f"{symbol} icin aranacak sirket adi yok"

        gun = int(self.s.get("sources.stocknews.lookback_days", 7))
        basina = int(self.s.get("sources.stocknews.max_per_instrument", 25))
        try:
            with httpx.Client(headers={"User-Agent": UA}, timeout=15.0,
                              follow_redirects=True) as client:
                rows = self._ara(client, sorgu, hedef["symbol"], gun, basina,
                                 sirket_adi=sorgu)
        except Exception as e:                        # noqa: BLE001
            log.warning("[stocknews] %s tek cekim basarisiz: %s", symbol, e)
            return 0, f"haber servisi cevap vermedi: {e}"
        n = self.db.upsert_news(rows)
        log.info("[stocknews] %s tek cekim: %d haber", symbol, n)
        return n, None

    @staticmethod
    def _sorgu_adi(hedef, kimlik) -> str | None:
        """
        Arama anahtari: en spesifik DOGRULANMIS ad.

        SEC adi ('NVIDIA CORP') resmi ama arama icin fazla kuru; enstruman adi
        ('NVIDIA') daha iyi kapsiyor. Ikisinden uzun olani degil, ENSTRUMAN
        adini tercih ediyoruz cunku ekranda gorunen ad odur.
        """
        ad = (hedef["name"] or "").strip()
        if not ad or ad.upper() in ("NAKIT", "CASH"):
            return None
        # Fonlarda sirket haberi aranmaz; fonun kendisi haber uretmez.
        if (kimlik["status"] if kimlik else "") == "fon":
            return None
        return ad

    def _ara(self, client, sorgu: str, symbol: str, gun: int, limit: int,
             sirket_adi: str | None = None) -> list[dict]:
        url = GOOGLE_NEWS.format(q=urllib.parse.quote(f'"{sorgu}"'), gun=gun)
        r = client.get(url)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)

        out = []
        for e in parsed.entries[:limit]:
            link = e.get("link")
            baslik = (e.get("title") or "").strip()
            if not link or not baslik:
                continue

            yayinci = (e.get("source") or {}).get("title") or ""
            # Sirketin kendi haber odasi/blogu birincil kaynaktir; genel
            # yayinci listesinde yer almadigi icin once bunu kontrol et.
            k = 1 if sirket_kaynagi(yayinci, sirket_adi) else kademe(yayinci)

            # Google News basliga " - Yayinci" ekliyor; tekrari temizle.
            if yayinci and baslik.endswith(f" - {yayinci}"):
                baslik = baslik[: -len(f" - {yayinci}")].strip()

            out.append({
                "url": link,
                "title": baslik,
                "source": yayinci or "bilinmeyen",
                "publisher": yayinci or None,
                "tier": k,
                "published_at": _tarih(e),
                "summary": "",
                "symbols": [symbol],
                "_sirket": sirket_adi,
            })
        return out


def _tarih(entry) -> str | None:
    for anahtar in ("published", "updated"):
        v = entry.get(anahtar)
        if not v:
            continue
        try:
            dt = parsedate_to_datetime(v)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:                            # noqa: BLE001
            continue
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if st:
        return datetime(*st[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return None
