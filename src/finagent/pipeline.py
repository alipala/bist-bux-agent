"""
Orkestrasyon: topla -> hesapla -> analiz et -> raporla -> bildir.
(BFF mimarisi §4 "Operational Agent Loop")
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import pandas as pd

from .analysis import Strategist, compute_indicators, portfolio_summary, technical_snapshot
from .browser import BrowserSession
from .collectors import REGISTRY
from .notify import TelegramNotifier
from .report import ReportBuilder
from .storage import Database

log = logging.getLogger(__name__)

# Kapanis panelinin SABIT sirasi. Her gun ayni satir ayni yerde olmali —
# gozun aradigini bulmasi raporun okunabilirliginin yarisi.
_GRUP_SIRA = {"endeks": 0, "emtia": 1, "kur": 2, "faiz": 3, "risk": 4}

# RSI/TREND HER VARLIK SINIFINDA AYNI SEYI SOYLEMEZ.
#
# Olculdu: USDTRY'nin RSI'i 93,4. Hisse mantigiyla okunursa "asiri alim,
# duzeltme gelir" cikar — oysa yonetilen bir deger kaybi rejiminde RSI
# aylarca 90'in uzerinde kalabilir ve hicbir sey soylemez. US10Y'de
# "asiri alim" daha da yanlis: o bir FIYAT degil GETIRI, yukselmesi
# tahvilin ucuzlamasi demek. VIX'te de trend etiketi anlamsiz.
# Sayiyi SILMIYORUZ (veri kaybi), yaninda nasil okunacagini yaziyoruz.
_MAKRO_UYARI = {
    "kur":   ("RSI/trend hisse gibi okunmaz: yonetilen kur rejiminde RSI "
              "aylarca uc degerde kalabilir. Seviye ve 20g degisim esastir."),
    "faiz":  ("Bu bir GETIRIDIR, fiyat degil — yukselmesi tahvilin "
              "ucuzlamasi demek. 'Asiri alim' okumasi tersine cevrilmelidir."),
    "risk":  ("VIX bir oynaklik beklentisidir; seviyesi (>20 tedirgin, "
              "<15 sakin) trend etiketinden daha bilgilendiricidir."),
    "emtia": ("Vadeli seri (ALTIN_VADELI, GUMUS, BRENT, WTI, BAKIR, TTF) ile "
              "spot vekili (ALTIN_ONS) AYNI SEY DEGILDIR; aralarindaki fark "
              "tasima maliyetidir, bir gorus farki degil."),
}


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


def _teknik_evren(settings, db: Database,
                  sahip: str) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Teknik gosterge hesaplanacak enstrumanlar: (TAM, OZET, MAKRO).

    ONCEDEN YALNIZCA `settings.bist_watchlist` VARDI — yani 10 BIST adi.
    Portfoyun kendisi (ASML %40,9 dahil) ve 47 sembollük kripto evreni
    hesaplanmiyordu bile; model bunlari gormeyince "fiyat serisi yok"
    diye BEYAN etti. Oysa 17 portfoy sembolunun hepsinde ve 47 kripto
    sembolunde gunluk bar VAR. Yanlis "yok" beyani, bos sonuctan kotudur:
    kullanici onu OLGU sanip sorusunu terk eder.

    UC KOVA, cunku hepsini tam goruntuyle vermek 100 satir eder ve asil
    zarar token degil DIKKAT SEYRELMESIDIR:
      TAM   : portfoy + kullanicinin KENDI sectigi izleme adaylari +
              BIST takip listesi
      OZET  : otomatik kurulan kripto evreni ve referans coin'ler —
              sembol, kapanis, 1g, 20g, RSI. "Bugun kripto tarafinda ne
              oldu" sorusu bununla cevaplanir, 70 tam snapshot gerekmez.
      MAKRO : endeks/emtia/kur/faiz paneli. AYRI, cunku bunlar bir
              pozisyon adayi degil BAGLAM; raporda da ayri bir tablo
              olarak, en ustte duruyorlar.
    """
    tam: dict[int, dict] = {}
    ozet: dict[int, dict] = {}
    makro: dict[int, dict] = {}

    def _ekle(hedef: dict, iid: int, sembol: str, venue: str, rol: str,
              grup: str | None = None) -> None:
        if iid in tam or iid in ozet or iid in makro:
            return                              # ilk rol kazanir (oncelik sirasi)
        hedef[iid] = {"id": iid, "sembol": sembol, "venue": venue or "",
                      "rol": rol, "grup": grup}

    # 1) PORTFOY — her zaman ve en once. Sahibin GUNCEL pozisyonlari.
    for r in db.enstrumanlar_by_id(db.sahip_pozisyon_idleri(sahip)):
        if r["asset_type"] == "cash" or r["symbol"] == "CASH":
            continue
        _ekle(tam, r["id"], r["symbol"], r["venue"], "portfoy")

    # 2) IZLEME LISTESI — 'aday' kullanicinin kendi sectigi isim.
    for r in db.watchlist():
        rol = "izleme" if r["kind"] == "aday" else "kripto_evreni"
        _ekle(tam if r["kind"] == "aday" else ozet,
              r["id"], r["symbol"], r["venue"], rol)

    # 3) BIST TAKIP LISTESI (settings) — portfoyde olmasa da izleniyor.
    for r in db.enstrumanlar_by_symbol(settings.bist_watchlist, venue="BIST"):
        _ekle(tam, r["id"], r["symbol"], "BIST", "bist_takip")

    # 4) MAKRO / ENDEKS PANELI — sinyal uretilecek degil, BAGLAM.
    for venue in ("MAKRO", "INDEX"):
        for r in db.query("SELECT id, symbol, name, venue, asset_type "
                          "FROM instruments WHERE venue = ? ORDER BY symbol",
                          (venue,)):
            # INDEX venue'sundeki eski kayitlarin asset_type'i 'index';
            # panelde 'endeks' ile ayni satirda durmalilar, yoksa AEX ve
            # DAX iki ayri baslik altina duser.
            grup = "endeks" if venue == "INDEX" else (r["asset_type"] or "endeks")
            _ekle(makro, r["id"], r["symbol"], venue, "makro", grup=grup)

    return list(tam.values()), list(ozet.values()), list(makro.values())


def _snapshot(db: Database, hedef: dict, ind_cfg: dict, bugun: date,
              lookback: int) -> dict | None:
    """
    Tek enstrumanin teknik goruntusu. Seri `fiyat_serisi()` ile okunur.

    `price_history()` KULLANILMAZ: kaynak filtrelemedigi icin ayni gunun
    iki kaynaktan gelen bari seride iki kez gorunuyor ve `pct_change()`
    ozdes iki kapanisi bolup 0,00 donduruyordu (2026-08-17: isyatirim +
    midas ayni gunu yazdi, BIST tablosunun 10/10'unda "gunluk getiri
    0,00" cikti; SISE gercekte -%6,67 dusmustu).
    """
    rows = db.fiyat_serisi(hedef["id"], lookback)
    if not rows:
        return None
    df = pd.DataFrame([dict(r) for r in rows])
    snap = technical_snapshot(hedef["sembol"], compute_indicators(df, ind_cfg))
    snap["venue"] = hedef["venue"]
    snap["rol"] = hedef["rol"]
    snap["para_birimi"] = rows[-1]["currency"]
    snap["kaynak"] = rows[-1]["source"]
    # SERI YASI BEYAN EDILIR. `fiyat_serisi()` para birimi DOGRU olan
    # kaynagi seciyor ve o kaynak daha bayat olabilir (ASML'de EUR serisi
    # alphavantage'dan, USD serisi Yahoo'dan ve Yahoo daha taze). Dogru
    # para birimi tazelikten once gelir — ama bayatlik SESSIZ kalmamali.
    snap["seri_yasi_gun"] = _yas_gun(rows[-1]["ts"], bugun)
    return snap


# Kova basina tavan. TEK BIR `LIMIT 40` NEDEN YETMIYOR: son 7 gunde 97
# sirket basligina karsi 19 makro_tr ve 12 makro_global vardi; tek listede
# ve `ORDER BY tier` ile sirket dosyalamalari tavani doldurup gundemi
# tamamen disari itiyordu. Kovalar ayri olunca sirket seli makro gundemi
# acliktan olduremiyor.
_GUNDEM_TAVANI = {
    "gundem_tr": 12,
    "gundem_global": 12,
    "emtia_enerji": 10,
    "jeopolitik": 8,
    "sirket": 25,
}
_KOVA_KONUSU = {
    "gundem_tr": "makro_tr",
    "gundem_global": "makro_global",
    "emtia_enerji": "emtia_enerji",
    "jeopolitik": "jeopolitik",
    "sirket": "sirket",
}


def _gundem_kovalari(db: Database, settings, max_news: int) -> dict[str, list]:
    """
    Kanit sayilabilir (kademe 1-2) haberi KONUYA gore kovalara ayirir.

    `alakasiz` ve `belirsiz` RAPORA GIRMEZ ama `haber_kalitesi` icinde
    SAYILIR — girmedikleri gorunur olsun diye. Sessizce dusen icerik,
    akisin calistigi izlenimini verip kanit uretmemesine yol acar.
    """
    gun = int(settings.get("analysis.llm.haber_gun", 3))
    out: dict[str, list] = {}
    for kova, konu_adi in _KOVA_KONUSU.items():
        tavan = min(_GUNDEM_TAVANI[kova], max_news)
        rows = db.query(
            """SELECT tier, publisher, source, published_at, title, symbols, url
               FROM news
               WHERE tier IN (1,2) AND konu = ?
                 AND published_at >= datetime('now', ?)
               ORDER BY tier, published_at DESC LIMIT ?""",
            (konu_adi, f"-{gun} days", tavan))
        out[kova] = [{"kademe": r["tier"],
                      "yayinci": r["publisher"] or r["source"],
                      "zaman": r["published_at"], "baslik": r["title"],
                      "semboller": r["symbols"], "url": r["url"]}
                     for r in rows]
    return out


def _yas_gun(ts: str | None, bugun: date) -> int | None:
    if not ts:
        return None
    try:
        return (bugun - date.fromisoformat(str(ts)[:10])).days
    except ValueError:
        return None


def build_bundle(settings, db: Database) -> dict:
    """Veritabanindan LLM ve rapora gidecek yapisal paketi kurar."""
    ind_cfg = settings.get("analysis.indicators", {}) or {}
    lookback = int(settings.get("analysis.lookback_days", 250))
    bugun = date.today()

    # RAPOR TEK SAHIBE AIT. Cok sahipli kurulumda hangi portfoy
    # raporlanacagi TAHMIN EDILMEZ; ilk sahip alinir ve bu raporda
    # BEYAN edilir (Faz B'de rapor da kisi basina uretilecek).
    sahip = (settings.sahip_listesi or ["ali"])[0]

    tam_hedef, ozet_hedef, makro_hedef = _teknik_evren(settings, db, sahip)
    technicals, fiyatsiz = [], []
    for h in tam_hedef:
        snap = _snapshot(db, h, ind_cfg, bugun, lookback)
        if snap is None:
            fiyatsiz.append(f"{h['sembol']} ({h['venue']})")
            continue
        technicals.append(snap)

    # KAPANIS PANELI — raporun en ustundeki tablo. Deterministik,
    # LLM'siz. Gruplu ve sabit sirali: her gun ayni yerde ayni satir.
    kapanis_paneli = []
    for h in sorted(makro_hedef, key=lambda x: (_GRUP_SIRA.get(x.get("grup"), 9),
                                                x["sembol"])):
        snap = _snapshot(db, h, ind_cfg, bugun, lookback)
        if snap is None:
            fiyatsiz.append(f"{h['sembol']} (MAKRO)")
            continue
        ad = db.query("SELECT name FROM instruments WHERE id=?", (h["id"],))
        kapanis_paneli.append({
            "kod": snap["symbol"],
            "ad": ad[0]["name"] if ad else snap["symbol"],
            "grup": h.get("grup") or "endeks",
            "kapanis": snap["kapanis"],
            "para_birimi": snap["para_birimi"],
            "getiri_1g_%": snap["getiri_1g_%"],
            "getiri_5g_%": snap["getiri_5g_%"],
            "getiri_20g_%": snap["getiri_20g_%"],
            "rsi14": snap["rsi14"],
            "trend": snap["trend"],
            "seri_yasi_gun": snap["seri_yasi_gun"],
            "kaynak": snap["kaynak"],
            "yorum_notu": _MAKRO_UYARI.get(h.get("grup") or "endeks"),
        })

    kripto_ozet = []
    for h in ozet_hedef:
        snap = _snapshot(db, h, ind_cfg, bugun, lookback)
        if snap is None:
            continue
        kripto_ozet.append({k: snap.get(k) for k in
                            ("symbol", "kapanis", "getiri_1g_%", "getiri_20g_%",
                             "rsi14", "trend", "para_birimi", "seri_yasi_gun")})

    # HESAP LISTESI ELLE YAZILMAZ. Onceki hali ("bux","midas") sabitti ve
    # BINANCE hesabini rapordan tamamen disariya atiyordu: kripto
    # pozisyonlari veritabaninda dururken rapor "kripto tarafinda bugun
    # soylenebilecek hicbir sey yok" diyordu.
    #
    # `source_enabled` ile SUZULMUYOR, bilerek: o bayrak VERI TOPLAMAYI
    # yonetir, kullanicinin parasinin var olup olmadigini degil. Bir
    # collector kapatildi diye o hesabin pozisyonlari rapordan dusmemeli.
    accounts = db.hesaplar(sahip)
    portfolio = portfolio_summary(db, accounts, sahip)
    portfolio["sahip"] = sahip

    max_news = int(settings.get("analysis.llm.max_news_items_in_prompt", 40))
    max_disc = int(settings.get("analysis.llm.max_disclosures_in_prompt", 30))

    # Haberler KADEME'ye gore ayrilir. Kanit (1-2) ile gurultu (3-4) ayni
    # listede gitseydi model ikisini esit agirlikta gorurdu; olcumde ham
    # akisin ~%80'i icerik ciftligi cikti.
    #
    # KONUYA GORE AYRI KOVA, AYRI TAVAN. Tek `LIMIT 40` sirket haberi
    # seliyle makro gundemi acliktan olduruyordu: son 7 gunde 97 sirket
    # basligina karsi 19 makro_tr, 12 makro_global vardi ve `ORDER BY
    # tier` sirketleri one aliyordu. Sonuc: raporda dunya ve Turkiye
    # gundemi HIC gorunmedi — veri toplanmis olmasina ragmen.
    news = _gundem_kovalari(db, settings, max_news)
    kanit_sayisi = sum(len(v) for v in news.values())

    zayif = db.query(
        """SELECT tier, COUNT(*) n FROM news
           WHERE tier NOT IN (1,2) AND published_at >= datetime('now','-7 days')
           GROUP BY tier""")
    from .research.konular import konu_dagilimi, siniflanmamis_basliklar
    haber_kalitesi = {
        "kanit_sayisi": kanit_sayisi,
        "kanit_disi": {f"kademe_{r['tier']}": r["n"] for r in zayif},
        "konu_dagilimi": konu_dagilimi(db, gun=7),
        # SINIFLANAMAYAN KUYRUK GORUNUR KALIR. Sessizce dusen basliklar,
        # akisin "calisiyor" gibi gorunup gundem uretmemesine yol acar —
        # `bilinmeyen_yayincilar()` ile ayni gerekce.
        "siniflanmamis_ornek": siniflanmamis_basliklar(db, gun=2, limit=8),
        "not": ("kademe 3-4 basliklar bilerek gonderilmedi: toplayici ve "
                "promosyon icerigi kanit sayilmaz. KONU ekseni kademeden "
                "AYRIDIR: kademe guvenilirligi olcer, konu alakayi — "
                "'alakasiz' etiketli basliklar (spor, magazin, asayis) "
                "mesru yayincilardan gelir ama rapora girmez."),
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

    bundle = {
        "tarih": datetime.now().strftime("%Y-%m-%d"),
        "kapanis_paneli": kapanis_paneli,
        "teknik": technicals,
        "kripto_evreni_ozet": kripto_ozet,
        "portfoy": portfolio,
        "haber": news,
        "haber_kalitesi": haber_kalitesi,
        "kap": kap,
        "dosyalamalar": dosyalamalar,
        "izleme_listesi": izleme,
        "kimlik_ozet": kimlik_ozet,
        "kaynaklar": [n for n in REGISTRY if settings.source_enabled(n)],
    }
    # TAKVIM: kaynak durumu HER ZAMAN gonderiliyor, olay listesi bos olsa
    # bile. "Yarin onemli bir sey yok" ile "takvim kaynagi kirik" ayni sey
    # degildir; ikincisi soylenmezse birincisi sanilir.
    from .collectors.takvim import yaklasan
    try:
        bundle["takvim"] = yaklasan(db, gun=int(
            settings.get("sources.takvim.pencere_gun", 14)))
    except Exception as e:                              # noqa: BLE001
        log.warning("takvim okunamadi: %s", e)

    bundle["kapsam"] = _kapsam(bundle, fiyatsiz)
    return bundle


def _kapsam(bundle: dict, fiyatsiz: list[str]) -> dict:
    """
    MODELE VERILEN KATMANLARIN ACIK BEYANI.

    Bunun olmamasi, projenin en kotu hata sinifini rapor katmaninda
    yeniden uretiyordu: model bir katmani GORMEYINCE "veri yok" diye
    yaziyor, kullanici da bunu OLGU saniyor. "Bana verilmedi" ile
    "veritabaninda yok" arasindaki farki model kendi basina bilemez —
    ancak BEYAN EDILIRSE bilir.

    Buradaki sayilar bundle'in KENDISINDEN uretiliyor, elle yazilmiyor:
    elle yazilan bir envanter bir katman eklendiginde eksik, bir katman
    kaldirildiginda YALAN olur.
    """
    teknik = bundle.get("teknik") or []
    roller: dict[str, int] = {}
    for t in teknik:
        roller[t.get("rol", "?")] = roller.get(t.get("rol", "?"), 0) + 1
    bayat = [f"{t['symbol']} ({t['seri_yasi_gun']}g)" for t in teknik
             if (t.get("seri_yasi_gun") or 0) >= 3]
    return {
        "kapanis_paneli_satiri": len(bundle.get("kapanis_paneli") or []),
        "takvim_olayi": len((bundle.get("takvim") or {}).get("olaylar") or []),
        "teknik_gosterge_hesaplanan": len(teknik),
        "teknik_rol_dagilimi": roller,
        "kripto_ozet_satiri": len(bundle.get("kripto_evreni_ozet") or []),
        "kap_bildirimi": len(bundle.get("kap") or []),
        "sec_dosyalamasi": len(bundle.get("dosyalamalar") or []),
        "gundem_kovalari": {k: len(v) for k, v in
                            (bundle.get("haber") or {}).items()},
        "fiyat_serisi_bulunamayan": fiyatsiz,
        "serisi_3_gunden_bayat": bayat,
        "not": ("Bu blok sana VERILEN katmanlari sayar. Burada sayisi >0 olan "
                "bir katman icin 'veri yok' YAZMA. Bir sembol "
                "`fiyat_serisi_bulunamayan` icinde degilse teknik verisi VARDIR."),
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
