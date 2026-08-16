"""
Orkestrasyon: topla -> hesapla -> analiz et -> raporla -> bildir.
(BFF mimarisi §4 "Operational Agent Loop")
"""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from .analysis import Strategist, compute_indicators, portfolio_summary, technical_snapshot
from .browser import BrowserSession
from .collectors import REGISTRY
from .notify import TelegramNotifier
from .report import ReportBuilder
from .storage import Database

log = logging.getLogger(__name__)


def collect(settings, db: Database, sites: list[str] | None = None,
            headless: bool | None = None) -> list:
    """Secilen collector'lari calistirir. Tarayici gerekenler tek oturumu paylasir."""
    names = sites or [n for n in REGISTRY if settings.source_enabled(n)]
    names = [n for n in names if n in REGISTRY]
    if not names:
        log.warning("Calistirilacak collector yok.")
        return []

    needs_browser = [n for n in names if REGISTRY[n].needs_browser]
    results = []

    if needs_browser:
        with BrowserSession(settings, headless=headless) as bs:
            for n in names:
                results.append(REGISTRY[n](settings, db, browser=bs).run())
    else:
        for n in names:
            results.append(REGISTRY[n](settings, db, browser=None).run())

    return results


def build_bundle(settings, db: Database) -> dict:
    """Veritabanindan LLM ve rapora gidecek yapisal paketi kurar."""
    ind_cfg = settings.get("analysis.indicators", {}) or {}

    technicals = []
    for sym in settings.bist_watchlist:
        rows = db.price_history(sym, "BIST", limit=int(settings.get("analysis.lookback_days", 250)))
        if not rows:
            technicals.append({"symbol": sym, "status": "fiyat verisi yok"})
            continue
        df = pd.DataFrame([dict(r) for r in rows]).sort_values("ts")
        technicals.append(technical_snapshot(sym, compute_indicators(df, ind_cfg)))

    accounts = [a for a in ("bux", "midas") if settings.source_enabled(a)]
    # RAPOR TEK SAHIBE AIT. Cok sahipli kurulumda hangi portfoy
    # raporlanacagi TAHMIN EDILMEZ; ilk sahip alinir ve bu raporda
    # BEYAN edilir (Faz B'de rapor da kisi basina uretilecek).
    sahip = (settings.sahip_listesi or ["ali"])[0]
    portfolio = portfolio_summary(db, accounts, sahip)
    portfolio["sahip"] = sahip

    max_news = int(settings.get("analysis.llm.max_news_items_in_prompt", 40))
    max_disc = int(settings.get("analysis.llm.max_disclosures_in_prompt", 30))

    # Haberler KADEME'ye gore ayrilir. Kanit (1-2) ile gurultu (3-4) ayni
    # listede gitseydi model ikisini esit agirlikta gorurdu; olcumde ham
    # akisin ~%80'i icerik ciftligi cikti.
    kanit_rows = db.query(
        """SELECT * FROM news
           WHERE tier IN (1,2) AND published_at >= datetime('now','-7 days')
           ORDER BY tier, published_at DESC LIMIT ?""", (max_news,))
    news = [{"kademe": r["tier"], "yayinci": r["publisher"] or r["source"],
             "zaman": r["published_at"], "baslik": r["title"],
             "semboller": r["symbols"], "url": r["url"]}
            for r in kanit_rows]

    zayif = db.query(
        """SELECT tier, COUNT(*) n FROM news
           WHERE tier NOT IN (1,2) AND published_at >= datetime('now','-7 days')
           GROUP BY tier""")
    haber_kalitesi = {
        "kanit_sayisi": len(news),
        "kanit_disi": {f"kademe_{r['tier']}": r["n"] for r in zayif},
        "not": ("kademe 3-4 basliklar bilerek gonderilmedi: toplayici ve "
                "promosyon icerigi kanit sayilmaz"),
    }
    # NOT: anahtar adlari haber ile AYNI olmali ("baslik"). Rapor ve
    # _fallback_note tek bir isim bekliyor; ayrisirsa listeler bos cikiyor.
    kap = [{"zaman": r["published_at"], "sirket": r["company"], "sembol": r["symbol"],
            "kategori": r["category"], "baslik": r["title"], "ozet": r["body"],
            "url": r["url"]}
           for r in db.recent_disclosures(hours=48, limit=max_disc)]

    # Resmi dosyalamalar (KADEME 1) haberden AYRI tutulur: bunlar sirketin
    # kendi beyanidir, basin yorumu degil.
    dosyalamalar = [{"zaman": r["published_at"], "sembol": r["symbol"],
                     "sirket": r["company"], "tur": r["category"],
                     "baslik": r["title"], "url": r["url"], "detay": r["body"]}
                    for r in db.recent_disclosures_by_source("sec", hours=24 * 30,
                                                             limit=max_disc)]

    # Portfoyde olmayan izleme adaylari — "firsat" bolumu bunlar uzerinden.
    portfoy_sembolleri = {
        p["sembol"] for h in (portfolio.get("hesaplar") or {}).values()
        for p in (h.get("pozisyonlar") or [])
    }
    izleme = [{"sembol": r["symbol"], "ad": r["name"], "eklendi": r["added_at"]}
              for r in db.watchlist() if r["symbol"] not in portfoy_sembolleri]

    kimlik_ozet = {r["status"]: 0 for r in db.identities()}
    for r in db.identities():
        kimlik_ozet[r["status"]] += 1

    return {
        "tarih": datetime.now().strftime("%Y-%m-%d"),
        "teknik": technicals,
        "portfoy": portfolio,
        "haber": news,
        "haber_kalitesi": haber_kalitesi,
        "kap": kap,
        "dosyalamalar": dosyalamalar,
        "izleme_listesi": izleme,
        "kimlik_ozet": kimlik_ozet,
        "kaynaklar": [n for n in REGISTRY if settings.source_enabled(n)],
    }


def analyze(settings, db: Database, bundle: dict | None = None, use_llm: bool = True) -> tuple[dict, str]:
    bundle = bundle or build_bundle(settings, db)
    strategist = Strategist(settings, db)
    if not use_llm:
        from .analysis.strategist import _fallback_note
        return bundle, _fallback_note(bundle)
    return bundle, strategist.analyze(bundle, scope="daily")


def run_daily(settings, db: Database, skip_collect: bool = False,
              use_llm: bool = True, headless: bool | None = None,
              notify: bool = True) -> dict:
    if not skip_collect:
        collect(settings, db, headless=headless)

    bundle, analysis_md = analyze(settings, db, use_llm=use_llm)
    files = ReportBuilder(settings).build(bundle, analysis_md)

    # Bildirim sonucunu yut ma: cron altinda calisirken Telegram'in sessizce
    # duserse (chat_id bos, token doldu) hicbir yerde gorunmuyordu.
    sent = None
    if notify:
        sent = TelegramNotifier(settings).send_report(analysis_md, bundle, files.get("html"))
        log.info("Telegram bildirimi: %s", "gonderildi" if sent else "GONDERILEMEDI")

    return {"bundle": bundle, "analysis": analysis_md, "files": files, "notified": sent}
