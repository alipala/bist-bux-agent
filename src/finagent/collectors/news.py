"""RSS haber toplayici + watchlist sembol eslestirmesi."""
from __future__ import annotations

import html as html_lib
import json
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from ..research.sources import kademe
from .base import BaseCollector, CollectorResult, extract_symbols

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


def _parse_date(entry) -> str | None:
    for key in ("published", "updated", "created"):
        val = entry.get(key)
        if not val:
            continue
        try:
            dt = parsedate_to_datetime(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat(sep=" ")[:19]
        except Exception:  # noqa: BLE001
            continue
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if st:
        return datetime(*st[:6], tzinfo=timezone.utc).isoformat(sep=" ")[:19]
    return None


class NewsCollector(BaseCollector):
    name = "news"
    needs_browser = False

    def collect(self) -> CollectorResult:
        feeds = self.s.get("sources.news.feeds") or []
        if not feeds:
            return CollectorResult(self.name, "skipped", 0, "feed tanimli degil")

        limit = int(self.s.get("sources.news.max_items_per_feed", 25))
        bayatlik = int(self.s.get("sources.news.bayatlik_gun", 3))
        watch = self._sembol_evreni()
        rows, failed, bayat = [], [], []

        for feed in feeds:
            # HTML/JSON-LD kaynagi AYRI YOLDAN gecer (RSS'i olmayan
            # yayincilar icin) ama ayni satir bicimini uretir.
            if (feed.get("type") or "rss").lower() == "jsonld":
                try:
                    yeni = self._jsonld_hasat(feed, limit, watch)
                except Exception as e:               # noqa: BLE001
                    log.warning("[news] %s (jsonld) alinamadi: %s",
                                feed.get("name"), e)
                    failed.append(feed.get("name", feed["url"]))
                    continue
                rows += yeni
                yas = self._akis_yasi_gun([r["published_at"] for r in yeni])
                if yas is not None and yas > bayatlik:
                    bayat.append(f"{feed.get('name')} ({yas:.0f} gun)")
                continue

            try:
                r = httpx.get(feed["url"], headers={"User-Agent": UA},
                              timeout=20.0, follow_redirects=True)
                r.raise_for_status()
                parsed = feedparser.parse(r.content)
            except Exception as e:                   # noqa: BLE001
                log.warning("[news] %s alinamadi: %s", feed.get("name"), e)
                failed.append(feed.get("name", feed["url"]))
                continue

            # HTTP 200 "VERI TAZE" DEMEK DEGILDIR — ve bu, bu projenin
            # tekrar eden kusur sinifi. Olculdu 2026-08-20: WSJ akisi
            # 200 ve 20 girdi donuyordu, en yenisi 27 OCAK 2025'ti (19
            # AY); BloombergHT 200 ve 20 girdi, en yenisi 6 Agustos
            # (14 gun); Hurriyet 200 ve 100 girdi, en yenisi 7 Haziran.
            # Ucu de "basarili" sayiliyordu cunku olcut STATU KODUYDU.
            # Olcut artik ICERIGIN YASI: bir yayinci sessizce olurse
            # bunu aylar sonra degil ertesi gun bilelim.
            yas = self._akis_yasi_gun(
                [_parse_date(e) for e in parsed.entries[:limit]])
            if yas is not None and yas > bayatlik:
                bayat.append(f"{feed.get('name')} ({yas:.0f} gun)")
                log.warning("[news] %s BAYAT: en yeni girdi %.0f gun once",
                            feed.get("name"), yas)

            for entry in parsed.entries[:limit]:
                title = (entry.get("title") or "").strip()
                summary = (entry.get("summary") or "")[:800].strip()
                url = entry.get("link")
                if not url:
                    continue
                # YAYINCI VE KADEME BURADA BELIRLENIR.
                #
                # Onceden hicbiri yazilmiyordu: 405 haber `publisher=NULL`
                # ve `tier=0` ("bilinmeyen") ile duruyordu — yani AA,
                # BloombergHT, Dunya, WSJ gibi meşru yayincilar kanit
                # olarak KULLANILAMIYORDU. Besleme adi zaten yayincinin
                # ta kendisi; `source`a yaziliyordu ama `publisher`a
                # yazilmadigi icin kademe fonksiyonu onu hic gormedi.
                #
                # RSS girdisi kendi kaynagini bildiriyorsa (Google News
                # toplayicisinda oluyor) o oncelikli — besleme adi
                # "Google News" olur, gercek yayinci girdinin icindedir.
                yayinci = ((entry.get("source") or {}).get("title")
                           if isinstance(entry.get("source"), dict) else None)
                yayinci = yayinci or feed.get("publisher") or feed.get("name")
                rows.append({
                    "published_at": _parse_date(entry),
                    "source": feed.get("name", "rss"),
                    "publisher": yayinci,
                    "tier": kademe(yayinci),
                    "title": title,
                    "url": url,
                    "summary": summary,
                    "symbols": extract_symbols(f"{title} {summary}", watch),
                })

        n = self.db.upsert_news(rows)
        notlar = []
        if failed:
            notlar.append(f"basarisiz feed: {', '.join(failed)}")
        if bayat:
            # BAYAT AKIS SESSIZ KALMAZ. `partial` donmesi bekcinin
            # besinci olcutunu (`eksik_toplama`) tetikler ve Ali'ye
            # dusler — 19 aylik WSJ sessizligi bir daha yasanmasin.
            notlar.append("BAYAT akis: " + ", ".join(bayat))
        status = "ok" if not (failed or bayat) else (
            "error" if not rows else "partial")
        return CollectorResult(self.name, status, n, " · ".join(notlar) or None)

    @staticmethod
    def _akis_yasi_gun(tarihler) -> float | None:
        """
        Bir akisin EN YENI girdisi kac gun once? Olculemezse None.

        None ile 0 KARISTIRILMAMALI: tarihi okunamayan bir akis "taze"
        degil "olculemedi"dir ve bayat ilan edilmez — yanlis alarm,
        alarmsizliktan beter olur.
        """
        gecerli = [t for t in tarihler if t]
        if not gecerli:
            return None
        try:
            en_yeni = max(datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
                          for t in gecerli)
        except (ValueError, TypeError):
            return None
        return (datetime.now(timezone.utc) - en_yeni).total_seconds() / 86400.0

    def _jsonld_hasat(self, feed: dict, limit: int, watch) -> list[dict]:
        """
        RSS'I OLMAYAN yayinciyi schema.org JSON-LD'sinden toplar.

        NEDEN GEREKTI: BloombergHT (kademe 2) RSS'i TERK ETMIS. Olculdu
        2026-08-20: `/rss` ucu hala HTTP 200 ve 20 girdi donuyor ama en
        yenisi 6 Agustos; sitede hicbir `application/rss+xml` etiketi
        yok ve denenen bes alternatif yol (`/borsa/rss`, `/ekonomi/rss`,
        `/feed` ...) 404. Yani akis olmus, ucu unutulmus.

        TARAYICI GEREKMIYOR: sayfa duz HTML donuyor (Next/Nuxt yok) ve
        liste sayfasinda `ItemList`, makale sayfasinda `NewsArticle`
        JSON-LD'si duruyor — ikisi de schema.org standardi, yani bu
        hasatci BloombergHT'ye ozel degil.

        MAKALE BASINA BIR ISTEK: liste yalnizca URL veriyor, baslik ve
        TARIH makalenin kendisinde. Tarihi slug'dan uydurmaktansa
        istegi atmak dogru — bayatlik olcutu tarihe bagli.
        """
        with httpx.Client(timeout=20.0, follow_redirects=True,
                          headers={"User-Agent": UA}) as c:
            liste = c.get(feed["url"])
            liste.raise_for_status()
            adresler = self._itemlist_adresleri(liste.text)[:limit]
            if not adresler:
                log.warning("[news] %s: ItemList bos", feed.get("name"))
            out = []
            for adres in adresler:
                try:
                    m = self._makale(c, adres)
                except Exception as e:               # noqa: BLE001
                    log.debug("[news] %s makalesi atlandi: %s", adres, e)
                    continue
                if not m:
                    continue
                yayinci = feed.get("publisher") or feed.get("name")
                baslik, ozet = m["baslik"], m["ozet"]
                out.append({
                    "published_at": m["tarih"],
                    "source": feed.get("name", "jsonld"),
                    "publisher": yayinci,
                    "tier": kademe(yayinci),
                    "title": baslik,
                    "url": adres,
                    "summary": ozet,
                    "symbols": extract_symbols(f"{baslik} {ozet}", watch),
                })
        log.info("[news] %s (jsonld): %d makale", feed.get("name"), len(out))
        return out

    @staticmethod
    def _itemlist_adresleri(html: str) -> list[str]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        adresler: list[str] = []
        for s in soup.find_all("script", type="application/ld+json"):
            if not s.string:
                continue
            try:
                d = json.loads(s.string)
            except ValueError:
                continue
            for blok in (d if isinstance(d, list) else [d]):
                if not isinstance(blok, dict) or blok.get("@type") != "ItemList":
                    continue
                for it in blok.get("itemListElement") or []:
                    u = it.get("url") or (it.get("item") or {}).get("url")
                    if isinstance(u, str) and u.startswith("http"):
                        adresler.append(u)
        gorulen, sirali = set(), []
        for a in adresler:
            if a not in gorulen:
                gorulen.add(a)
                sirali.append(a)
        return sirali

    @staticmethod
    def _makale(client, adres: str) -> dict | None:
        from bs4 import BeautifulSoup

        r = client.get(adres)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for s in soup.find_all("script", type="application/ld+json"):
            if not s.string:
                continue
            try:
                d = json.loads(s.string)
            except ValueError:
                continue
            for blok in (d if isinstance(d, list) else [d]):
                if not isinstance(blok, dict):
                    continue
                if blok.get("@type") not in ("NewsArticle", "Article"):
                    continue
                ham = blok.get("datePublished") or blok.get("dateModified")
                tarih = None
                if ham:
                    try:
                        dt = datetime.fromisoformat(str(ham))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        tarih = (dt.astimezone(timezone.utc)
                                 .replace(microsecond=0).isoformat(sep=" ")[:19])
                    except ValueError:
                        tarih = None
                # JSON-LD govdesi HTML VARLIKLARI tasiyor ("&#039;"):
                # cozulmezse baslik ekranda ham kacis dizisiyle gorunur.
                return {
                    "baslik": html_lib.unescape(blok.get("headline") or "").strip(),
                    "ozet": html_lib.unescape(
                        blok.get("description") or "")[:800].strip(),
                    "tarih": tarih,
                }
        return None

    def _sembol_evreni(self) -> list[str]:
        """
        Haberde aranacak semboller: GERCEK portfoy + izleme + BIST takip.

        Onceden `settings.watchlist.bux` kullaniliyordu ve o liste
        [VWCE, IWDA, CSPX] — Ali'nin sahip OLMADIGI uc ETF. Yani genel
        haber akisindaki hicbir baslik portfoy isimlerine baglanmiyordu;
        "Nvidia, SB Energy'ye 1,5 milyar dolar yatirim yapacak" haberi
        `symbols` alani BOS olarak duruyordu ve rapor onu NVDA ile
        iliskilendiremiyordu. Ayarda duran bayat bir liste, veritabaninda
        duran gercegi golgeliyordu.

        KRIPTO HARIC: sembolleri gundelik kelimelerle cakisiyor (ADA,
        SOL, DOT, M, CC, GRAM) ve genel haber akisinda yanlis pozitif
        uretirler. Kripto haberi kendi collector'undan geliyor.
        """
        semboller = {s.upper() for s in self.s.bist_watchlist}
        for r in self.db.research_targets(kripto=False):
            sembol = (r["symbol"] or "").upper()
            # Borsa sonekli katalog sembolu ("ASML.AS") baslikta gecmez;
            # koku aranir.
            if sembol and not sembol.startswith("~"):
                semboller.add(sembol.split(".")[0])
        # Tek/iki harfli kalintilar her metne yapisir.
        return sorted(s for s in semboller if len(s) >= 3)
