#!/usr/bin/env python3
"""
BIST / BUX Analiz Agent — komut satiri.

  python run.py init-db                     # veritabanini olustur
  python run.py login    --site midas       # tek seferlik elle giris
  python run.py discover --site bux         # selector kesfi
  python run.py collect  [--site kap news]  # veri topla
  python run.py analyze  [--no-llm]         # analiz uret (rapor yazmadan)
  python run.py report                      # mevcut veriden rapor uret
  python run.py daily                       # tam dongu + Telegram
  python run.py bot                         # Telegram dinleyici (ekran goruntusu -> portfoy)
  python run.py telegram-test               # bildirim testi
  python run.py status                      # veritabani durumu
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def _assert_venv_interpreter() -> None:
    """
    Proje icinde .venv varsa ama script BASKA bir Python ile calistiriliyorsa
    net bir mesaj ver.

    Aksi halde kullanici kafa karistirici bir 'ModuleNotFoundError: rich'
    gorur: sistem Python'unda tesadufen kurulu olan paketler (yaml, dotenv)
    import olur, olmayanlar (rich, pandas) patlar. Sorun eksik paket degil,
    yanlis yorumlayicidir.
    """
    venv_dir = ROOT / ".venv"
    if not venv_dir.is_dir():
        return                                   # venv kullanilmiyor, karisma
    if Path(sys.prefix).resolve() == venv_dir.resolve():
        return                                   # zaten venv icindeyiz

    bindir = "Scripts" if sys.platform == "win32" else "bin"
    venv_py = venv_dir / bindir / ("python.exe" if sys.platform == "win32" else "python")
    sys.stderr.write(
        "\n  HATA: Bu script proje sanal ortami DISINDA calistiriliyor.\n\n"
        f"    Kullanilan Python : {sys.executable}\n"
        f"    Beklenen Python   : {venv_py}\n\n"
        "  'pip install' paketleri .venv icine kurdu, ama bu yorumlayici\n"
        "  oralara bakmiyor. Cozum — sunlardan biri:\n\n"
        f"    {venv_py} {Path(sys.argv[0]).name} {' '.join(sys.argv[1:])}\n"
        "    veya\n"
        "    source .venv/bin/activate   (sonra: which python  ile dogrula)\n\n"
        "  Not: kabuk 'python' yolunu onbellege almis olabilir; "
        "aktivasyondan sonra 'hash -r' calistir.\n\n"
    )
    raise SystemExit(2)


# Sadece dogrudan calistirildiginda kontrol et; testler run.py'yi modul
# olarak yukluyor ve kendi yorumlayicisini kullaniyor.
if __name__ == "__main__":
    _assert_venv_interpreter()

# Sadece hafif modulleri en ustte import ediyoruz. pandas / playwright /
# feedparser gibi agir bagimliliklar ilgili komut CALISTIRILDIGINDA yuklenir;
# boylece `telegram-chatid`, `init-db`, `status` gibi komutlar analiz
# bagimliliklari kurulmamis olsa bile calisir.
from finagent.config import load_settings                  # noqa: E402
from finagent.logging_setup import console, setup_logging  # noqa: E402
from finagent.storage import Database                      # noqa: E402

# collectors/__init__ httpx+feedparser cektigi icin burada import etmiyoruz.
# Kaynak: finagent.collectors.REGISTRY — tests/test_smoke.py senkron tutuyor.
SITES = ["alphavantage", "isyatirim", "kap", "bist", "bistgecmis", "binance",
         "bux", "cgfiyat", "coingecko", "edgar", "indices", "kripto",
         "kriptoevren", "makro", "midas",
         "midasbilanco", "news", "prices", "stocknews", "takvim", "tiingo", "tuik", "xbrl"]


def main() -> int:
    ap = argparse.ArgumentParser(prog="run.py", description="BIST/BUX analiz agent'i")
    ap.add_argument("--log-level", default="INFO")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="SQLite semasini olustur")

    p = sub.add_parser("login", help="Tek seferlik elle giris (oturum profile kaydedilir)")
    p.add_argument("--site", required=True, choices=["bux", "midas"])

    p = sub.add_parser("discover", help="Sayfa yapisini dok, aday selector'lari listele")
    p.add_argument("--site", required=True, choices=SITES)
    p.add_argument("--url", default=None, help="Farkli bir URL denemek icin")

    p = sub.add_parser("collect", help="Veri topla")
    p.add_argument("--site", nargs="*", choices=SITES, default=None)
    p.add_argument("--headless", action="store_true")

    p = sub.add_parser("analyze", help="Analiz uret (konsola)")
    p.add_argument("--no-llm", action="store_true")

    p = sub.add_parser("backtest", help="Tarayici sinyallerini gecmiste sina (once GUC ANALIZI)")
    p.add_argument("--baslangic", default="2016-09-01")
    p.add_argument("--bitis", default="2026-07-01")
    p.add_argument("--venue", default="BIST")
    p.add_argument("--limit", type=int, default=None, help="evreni kirp (deneme icin)")

    p = sub.add_parser("trend", help="Donchian 20/10 + 2N trend takibi sinamasi")
    p.add_argument("--baslangic", default="2016-09-01")
    p.add_argument("--bitis", default="2026-06-01")
    p.add_argument("--venue", default="BIST")
    p.add_argument("--limit", type=int, default=None)

    p = sub.add_parser("report", help="Mevcut veriden rapor dosyalari uret")
    p.add_argument("--no-llm", action="store_true")

    p = sub.add_parser("daily", help="Topla + analiz + rapor + Telegram")
    p.add_argument("--skip-collect", action="store_true")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--headless", action="store_true")

    sub.add_parser("bot", help="Telegram dinleyici: ekran goruntusu -> portfoy, komutlar")
    # ELLE CAGRILMAZ: dinleyici her agir is icin bunu baslatir.
    p = sub.add_parser("bot-worker",
                       help="Tek bir Telegram isini isler (dinleyici baslatir)")
    p.add_argument("--is", dest="is_yolu", required=True,
                   help="data/bot/kuyruk/<update_id>.json")
    p = sub.add_parser("nabiz", help="Proaktif dongu: tara + ajan paneli + bildir")
    # `choices` YOK ve BILEREK yok: gecerli kip listesi
    # `config/settings.yaml -> ritim.kipler`de ve tek dogrulama noktasi
    # `Settings.ritim_kip`. Buraya ikinci bir liste yazmak, ayar
    # degistiginde sessizce ayrisirdi.
    # VARSAYILAN YOK. Onceden `default="nabiz"` yaziyordu: `--kip`
    # unutulan her cagri sessizce TAM PANEL kosturuyordu — yanlis
    # kaynaklari toplayip yanlis kisilere mesaj atmanin en kolay yolu.
    p.add_argument("--kip", default=None,
                   help="ritim.kipler altindaki kip adi (--karne disinda ZORUNLU)")
    # KIPIN ALICILARINDAN BIRI. `Nabiz.calistir(sahip=...)` bunu zaten
    # destekliyordu ("elle calistirma ve test icin") ama CLI'dan
    # ERISILEMIYORDU — yani elle bir kosu denemek KACINILMAZ olarak
    # herkese mesaj atiyordu.
    p.add_argument("--sahip", default=None,
                   help="yalnizca bu sahip icin kos (kipin alicilarindan biri)")
    p.add_argument("--no-panel", action="store_true",
                   help="yalnizca deterministik tarama (LLM yok)")
    p.add_argument("--no-notify", action="store_true", help="Telegram'a gonderme")
    p.add_argument("--karne", action="store_true", help="yalnizca isabet karnesi")

    sub.add_parser("telegram-chatid", help="Bota yazan sohbetleri listele (chat_id bul)")
    sub.add_parser("telegram-test", help="Telegram baglantisini test et")
    sub.add_parser("status", help="Veritabani ozeti")

    p = sub.add_parser("yedek", help="Veritabani yedegi (VACUUM INTO + dogrulama)")
    p.add_argument("--zorla", action="store_true",
                   help="Bugunun yedegi varsa da yeniden al")

    args = ap.parse_args()

    settings = load_settings()
    setup_logging(args.log_level, settings.root / "data" / "agent.log")
    db = Database(settings.db_path)
    db.init_schema()

    try:
        return dispatch(args, settings, db)
    finally:
        db.close()


def dispatch(args, settings, db) -> int:
    cmd = args.cmd

    if cmd == "init-db":
        console.print(f"[green]✓[/] Veritabani hazir: {settings.db_path}")

    elif cmd == "login":
        from finagent.browser import interactive_login
        interactive_login(settings, args.site)

    elif cmd == "discover":
        from finagent.browser import discover_site
        discover_site(settings, args.site, args.url)

    elif cmd == "collect":
        from finagent import pipeline
        results = pipeline.collect(settings, db, args.site,
                                   headless=True if args.headless else None)
        console.print()
        for r in results:
            color = {"ok": "green", "partial": "yellow",
                     "skipped": "dim", "error": "red"}.get(r.status, "white")
            console.print(f"  [{color}]{r.status:8}[/] {r.name:12} {r.rows:>5} satir"
                          f"  {r.duration_ms:>6} ms  {r.error or ''}")
        console.print()

    elif cmd == "trend":
        from finagent.analysis.trend_takip import kosu, ozet
        r = kosu(db, args.baslangic, args.bitis, venue=args.venue,
                 limit=args.limit)
        console.print(f"\n[bold]Kapsam[/] — {r['kapsam']['enstruman']} enstruman"
                      f" · al-tut endeks: %{r['al_tut_endeks_%']}\n")
        a = ozet(r["_islemler"])
        b = ozet(r["_islemler"], yalniz_uygulanabilir=True)
        console.print(f"  {'olcut':<24}{'HEPSI':>12}{'UYGULANABILIR':>15}")
        for k in a:
            console.print(f"  {k:<24}{str(a[k]):>12}{str(b.get(k)):>15}")
        console.print("\n  [yellow]![/] HAYATTA KALMA YANLILIGI giderilemedi: "
                      "evren BUGUN kote olan kagitlardan kuruluyor.")
        console.print("  [yellow]![/] Getiriler NOMINAL TRY. Enflasyon "
                      "arindirilmadi (TUFE verisi yok).")
        console.print()

    elif cmd == "backtest":
        from finagent.analysis.backtest import kosu
        r = kosu(db, settings, args.baslangic, args.bitis,
                 venue=args.venue, limit=args.limit)
        k = r["kapsam"]
        console.print(f"\n[bold]Kapsam[/] — {k['enstruman']} enstruman · "
                      f"{k['bar']:,} bar · {k['sinyal']:,} sinyal · "
                      f"limitte giris {k['limitte']:,} · "
                      f"sermaye islemi dusen {k['islem_dusen']:,}\n")
        for etiket, filtre in (("HEPSI", False),
                               ("UYGULANABILIR (limitte giris HARIC)", True)):
            from finagent.analysis.backtest import guc_analizi
            console.print(f"[bold]{etiket}[/]")
            console.print(f"  {'tur':<19}{'yon':<7}{'uf':>3}{'gun':>6}"
                          f"{'etki%':>8}{'MDE%':>7}{'z':>7}  yon?  Bonf  karar")
            for x in guc_analizi(r["_gozlemler"], (1, 5, 20), filtre):
                yt = "ok  " if x["yon_tutuyor"] else "TERS"
                bf = "GECTI" if x["bonferroni_gecti"] else "-    "
                console.print(f"  {x['tur']:<19}{x['yon']:<7}{x['ufuk_gun']:>3}"
                              f"{x['bagimsiz_gun']:>6}{x['gozlenen_%']:>8.3f}"
                              f"{x['mde_%']:>7.3f}{x['z']:>7.2f}  {yt}  {bf} {x['karar']}")
            console.print()
        for u in r["uyarilar"]:
            console.print(f"  [yellow]![/] {u}")
        console.print()

    elif cmd in ("analyze", "report"):
        from finagent import pipeline
        from finagent.report import ReportBuilder
        bundle, md = pipeline.analyze(settings, db, use_llm=not args.no_llm)
        if cmd == "analyze":
            console.print(md)
        else:
            files = ReportBuilder(settings).build(bundle, md)
            for k, v in files.items():
                console.print(f"  [green]✓[/] {k}: {v}")

    elif cmd == "daily":
        from finagent import pipeline
        out = pipeline.run_daily(
            settings, db,
            skip_collect=args.skip_collect,
            use_llm=not args.no_llm,
            headless=True if args.headless else None,
            notify=not args.no_notify,
        )
        for k, v in out["files"].items():
            console.print(f"  [green]✓[/] {k}: {v}")
        if out.get("notified") is not None:
            console.print("  [green]✓[/] telegram: gonderildi" if out["notified"]
                          else "  [red]✗[/] telegram: gonderilemedi "
                               "([dim].env icindeki TELEGRAM_* degerlerini kontrol et[/])")

    elif cmd == "bot":
        from finagent.bot import FinBot
        bot = FinBot(settings, db)
        console.print("\n  [bold green]Bot dinlemede[/] — Telegram'dan ekran goruntusu "
                      "gonderebilirsin.\n  Durdurmak icin [bold]Ctrl+C[/].\n")
        try:
            return bot.run()
        except KeyboardInterrupt:
            bot.stop()
            console.print("\n  [dim]Bot durduruldu.[/]\n")
            return 0

    elif cmd == "bot-worker":
        from finagent.bot.worker import calistir
        return calistir(settings, db, Path(args.is_yolu))

    elif cmd == "telegram-chatid":
        from finagent.notify import TelegramNotifier
        chats = TelegramNotifier(settings).discover_chat_ids()
        if not chats:
            console.print(
                "\n  [yellow]Sohbet bulunamadi.[/]\n"
                "  Telegram'da botunu ac, ona bir mesaj yaz (ornegin 'merhaba'),\n"
                "  sonra bu komutu tekrar calistir.\n"
            )
            return 1
        console.print("\n  [bold]Bota yazan sohbetler[/]\n")
        for c in chats:
            console.print(f"    chat_id=[green]{c['chat_id']}[/]  ({c['tip']})  {c['ad']}")
        console.print("\n  Bunu .env icine yaz:  "
                      f"[dim]TELEGRAM_CHAT_ID={chats[0]['chat_id']}[/]\n")

    elif cmd == "telegram-test":
        from finagent.notify import TelegramNotifier
        ok = TelegramNotifier(settings).test()
        console.print("[green]✓ Gonderildi[/]" if ok
                      else "[red]✗ Gonderilemedi[/] — .env icindeki TELEGRAM_* degerlerini kontrol et.")
        return 0 if ok else 1

    elif cmd == "nabiz":
        from finagent.pulse import Defter, Nabiz
        if args.karne:
            d = Defter(db)
            console.print("\n  [bold]Isabet karnesi[/]")
            sahip = (settings.sahip_listesi or ["ali"])[0]
            for k, v in d.puanla(sahip).items():
                console.print(f"    {k:28} {v}")
            aj = d.ajan_karnesi(sahip)
            if aj:
                console.print("\n  [bold]Ajan bazinda[/]")
                for x in aj:
                    console.print(f"    {x['ajan']:10} {x['olcum']:>3} olcum  "
                                  f"isabet %{x['isabet_%']}")
            return 0
        # PANEL KARARI AYARDAN, KODDAN DEGIL.
        #
        # Burada `hafif = args.kip in ("sabah", "ogle")` yaziyordu ve
        # ritim v2'nin ilk tuzagi tam olarak buydu: hangi kipin model
        # calistiracagi kodda gomuluyse, yeni bir kip eklemek kod
        # degisikligi gerektirir ve iki yer (kod + plist) ayrisir.
        # `--no-panel` KALIYOR: elle LLM'siz kosu hala gerekli.
        if not args.kip:
            console.print(
                "\n  [red]--kip zorunlu.[/] Gecerli kipler: "
                f"[bold]{', '.join(settings.ritim_kipleri)}[/]\n")
            return 2
        try:
            kip_ayar = settings.ritim_kip(args.kip)
        except ValueError as e:
            console.print(f"\n  [red]{e}[/]\n")
            return 2
        if args.sahip and args.sahip.strip().lower() not in kip_ayar["alicilar"]:
            # ALICI LISTESI BURADA DA BAGLAYICI: elle kosu, kipin
            # alicisi olmayan birine mesaj atmanin arka kapisi olmamali.
            console.print(
                f"\n  [red]'{args.sahip}' bu kipin alicisi degil.[/] "
                f"Alicilar: [bold]{', '.join(kip_ayar['alicilar'])}[/]\n")
            return 2
        sonuc = Nabiz(settings, db).calistir(
            bildir=not args.no_notify,
            panel=kip_ayar["panel"] and not args.no_panel, kip=args.kip,
            sahip=(args.sahip.strip().lower() if args.sahip else None))
        # COK SAHIPLI CIKTI: her sahip ayri satir. Tek sahipte duz
        # alanlar da doluyor, yani bugunku cikti KORUNUYOR.
        console.print(f"\n  kip: [bold]{args.kip}[/]  "
                      f"sahip: [bold]{', '.join(sonuc.get('sahipler', []))}[/]"
                      + (f"  [red]basarisiz: {', '.join(sonuc['basarisiz'])}[/]"
                         if sonuc.get("basarisiz") else ""))
        console.print(f"  piyasa sinyali (ortak): "
                      f"[bold]{sonuc.get('ortak', {}).get('piyasa_sinyali', 0)}[/]")
        for _s, _r in (sonuc.get("sonuc") or {}).items():
            if "hata" in _r:
                console.print(f"    [red]{_s:10} HATA: {_r['hata'][:70]}[/]")
                continue
            console.print(f"    {_s:10} sinyal {_r.get('sinyal', 0):>4}  "
                          f"esigi gecen {_r.get('guclu', 0):>3}  "
                          f"tahmin {_r.get('tahmin', 0):>3}  "
                          f"tez {_r.get('tez_bozuldu', 0)}"
                          + ("  [yellow]panel yok[/]" if _r.get("panel_hatasi")
                             else ""))
        if sonuc.get("ozet"):
            console.print("\n" + sonuc["ozet"])
        elif not sonuc.get("guclu"):
            # `.get` OLMAK ZORUNDA: duz alanlar YALNIZCA tek sahiplide
            # doluyor (runner.calistir sonundaki kosullu yayma). Yuksel
            # eklenince sahip sayisi 2 oldu ve bu satir her zamanlanmis
            # kosuyu `KeyError: 'guclu'` ile dusurdu — is ve bildirimler
            # tamamlaniyordu ama KARNE CIKTISI hic basilmadi ve cikis
            # kodu 1 oldu, yani disaridan her kosu "basarisiz" gorundu.
            # Olculdu: 17 Agu 22:15 nabiz ve 18 Agu 09:30 sabah.
            console.print("  [dim]Esigi gecen sinyal yok — sessiz kalindi.[/]")
        for k, v in (sonuc.get("karne") or {}).items():
            console.print(f"  karne.{k:24} {v}")

    elif cmd == "status":
        _status(db, settings)

    elif cmd == "yedek":
        from finagent.storage.yedek import yedek_al
        r = yedek_al(settings, zorla=args.zorla)
        renk = {"ok": "green", "atlandi": "dim", "hata": "red"}.get(
            r["durum"], "white")
        console.print(f"\n  [{renk}]{r['durum']}[/] {r.get('dosya') or ''}"
                      f"  {r.get('sebep') or ''}")
        if r["durum"] == "ok":
            console.print(f"    dizin   {r['dizin']}")
            console.print(f"    boyut   {r['boyut_mb']} MB "
                          f"({r['sure_sn']} sn) · bos alan {r['bos_gb']} GB")
            console.print(f"    satir   " + " · ".join(
                f"{t} {n}" for t, n in (r["sayilar"] or {}).items()))
        if r.get("budanan"):
            console.print(f"    budanan {len(r['budanan'])}: "
                          + ", ".join(r["budanan"]))
        console.print()
        # CIKIS KODU SONUCU TASIR: kabuk bunu gorup bildirebilsin.
        # Yedegin sessizce basarisiz olmasi, hic yedek olmamasindan
        # kotudur — aldigini sanirsin.
        return 1 if r["durum"] == "hata" else 0

    return 0


def _status(db, settings) -> None:
    console.print(f"\n  [bold]Veritabani:[/] {settings.db_path}\n")
    for table in ("instruments", "prices", "positions", "disclosures", "news",
                  "signals", "predictions", "analysis_runs", "collector_runs",
                  "sohbet_kaydi"):
        n = db.query(f"SELECT COUNT(*) c FROM {table}")[0]["c"]
        console.print(f"    {table:16} {n:>8}")

    runs = db.query("""SELECT collector, status, rows_written, run_ts
                       FROM collector_runs ORDER BY id DESC LIMIT 8""")
    if runs:
        console.print("\n  [bold]Son collector calismalari[/]")
        for r in runs:
            color = {"ok": "green", "partial": "yellow",
                     "skipped": "dim", "error": "red"}.get(r["status"], "white")
            console.print(f"    {r['run_ts'][:19]}  [{color}]{r['status']:8}[/] "
                          f"{r['collector']:12} {r['rows_written']:>5}")
    console.print()


if __name__ == "__main__":
    raise SystemExit(main())
