"""RSS haber toplayici + watchlist sembol eslestirmesi."""
from __future__ import annotations

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
        watch = self.s.bist_watchlist + self.s.bux_watchlist
        rows, failed = [], []

        for feed in feeds:
            try:
                r = httpx.get(feed["url"], headers={"User-Agent": UA},
                              timeout=20.0, follow_redirects=True)
                r.raise_for_status()
                parsed = feedparser.parse(r.content)
            except Exception as e:                   # noqa: BLE001
                log.warning("[news] %s alinamadi: %s", feed.get("name"), e)
                failed.append(feed.get("name", feed["url"]))
                continue

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
        status = "ok" if not failed else ("error" if not rows else "partial")
        return CollectorResult(self.name, status, n,
                               f"basarisiz feed: {', '.join(failed)}" if failed else None)
