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
SITES = ["alphavantage", "isyatirim", "kap", "bist", "binance", "bux",
         "cgfiyat", "coingecko", "edgar", "indices", "kripto", "kriptoevren", "midas",
         "midasbilanco", "news", "prices", "stocknews", "tiingo", "xbrl"]


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

    p = sub.add_parser("report", help="Mevcut veriden rapor dosyalari uret")
    p.add_argument("--no-llm", action="store_true")

    p = sub.add_parser("daily", help="Topla + analiz + rapor + Telegram")
    p.add_argument("--skip-collect", action="store_true")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--headless", action="store_true")

    sub.add_parser("bot", help="Telegram dinleyici: ekran goruntusu -> portfoy, komutlar")
    p = sub.add_parser("nabiz", help="Proaktif dongu: tara + ajan paneli + bildir")
    p.add_argument("--kip", choices=["sabah", "ogle", "nabiz"], default="nabiz",
                   help="sabah/ogle = LLM'siz hafif kosu; nabiz = tam panel")
    p.add_argument("--no-panel", action="store_true",
                   help="yalnizca deterministik tarama (LLM yok)")
    p.add_argument("--no-notify", action="store_true", help="Telegram'a gonderme")
    p.add_argument("--karne", action="store_true", help="yalnizca isabet karnesi")

    sub.add_parser("telegram-chatid", help="Bota yazan sohbetleri listele (chat_id bul)")
    sub.add_parser("telegram-test", help="Telegram baglantisini test et")
    sub.add_parser("status", help="Veritabani ozeti")

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
        # Hafif kipler (sabah/ogle) LLM CALISTIRMAZ: panel=False.
        # Bilerek CLI'da degil burada baglaniyor — kip adiyla panel
        # kararinin ayrismasi, "sabah kosusu neden pahali" turunden bir
        # soruyu dogurur.
        hafif = args.kip in ("sabah", "ogle")
        sonuc = Nabiz(settings, db).calistir(
            bildir=not args.no_notify,
            panel=not (args.no_panel or hafif), kip=args.kip)
        console.print(f"\n  kip: [bold]{args.kip}[/]  "
                      f"sinyal: [bold]{sonuc['sinyal']}[/]  "
                      f"esigi gecen: [bold]{sonuc['guclu']}[/]  "
                      f"tahmin: [bold]{sonuc['tahmin']}[/]  "
                      f"tez bozulan: [bold]{sonuc.get('tez_bozuldu', 0)}[/]")
        if sonuc.get("ozet"):
            console.print("\n" + sonuc["ozet"])
        elif not sonuc["guclu"]:
            console.print("  [dim]Esigi gecen sinyal yok — sessiz kalindi.[/]")
        for k, v in (sonuc.get("karne") or {}).items():
            console.print(f"  karne.{k:24} {v}")

    elif cmd == "status":
        _status(db, settings)

    return 0


def _status(db, settings) -> None:
    console.print(f"\n  [bold]Veritabani:[/] {settings.db_path}\n")
    for table in ("instruments", "prices", "positions", "disclosures", "news",
                  "signals", "predictions", "analysis_runs", "collector_runs"):
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
