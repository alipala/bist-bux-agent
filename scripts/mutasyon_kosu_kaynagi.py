"""
Kosu kaynagi (sema 25) mutasyon turu (2026-08-29).

OLCULEN YANLIS ALARM: 29 Agustos CUMARTESI 15:14'te, zamanlanmis
hicbir kosu yokken, bekci "ibkrkimlik — 3 kosudur partial" gonderdi.
Sebep: conid duzeltmesi dogrulanirken collector ELLE uc kez kosuldu ve
`collector_runs` elle/zamanlanmis ayrimi tasimiyordu.

EN KRITIK MUTASYON `E`: isaret hic konmazsa her kosu "elle" olur ve
bekci HICBIR SEYI yakalamaz — koruma sessizce kapanir.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib, shutil, subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
DB = "src/finagent/storage/db.py"
WD = "src/finagent/bot/watchdog.py"
SH = "scripts/_ortak.sh"
RUN = "run.py"

COZUM = "test_kosu_kaynagi_ORTAMDAN_bilinmeyen_deger_ELLE_sayilir"
BEKCI = "test_bekci_ELLE_kosumlari_SISTEMLI_ARIZA_saymaz"
YAZAN = "test_log_collector_run_KAYNAGI_GERCEKTEN_yaziyor"
KABLO = "test_kosu_kaynagi_KABLOLARI_bagli"

M = [
    # --- kaynak cozumu ----------------------------------------------------
    ("A) bilinmeyen deger `zamanlanmis` sayiliyor (yazim hatasi olcume girer)",
     DB, '    return ham if ham in KOSU_KAYNAKLARI else "elle"',
     '    return ham or "zamanlanmis"', COZUM),

    ("B) damgasiz kosu `zamanlanmis` sayiliyor (eski davranis)",
     DB, '    return ham if ham in KOSU_KAYNAKLARI else "elle"',
     '    return ham if ham in KOSU_KAYNAKLARI else "zamanlanmis"', COZUM),

    ("C) kaynak kaydedilmiyor (kolon hep NULL kalir)",
     DB, "                (utcnow(), collector, status, rows, duration_ms, error,\n"
         "                 kosu_kaynagi()),",
     "                (utcnow(), collector, status, rows, duration_ms, error,\n"
     "                 None),", YAZAN),

    # --- bekci suzgeci ----------------------------------------------------
    ("D) bekci suzgeci kalkiyor (eski davranis: elle kosumlar sayilir)",
     WD, "                     AND (kaynak = 'zamanlanmis' OR kaynak IS NULL)\n",
     "", BEKCI),

    ("D2) NULL (gecis) satirlari eleniyor — olcut uc gun KOR kalir",
     WD, "                     AND (kaynak = 'zamanlanmis' OR kaynak IS NULL)",
     "                     AND kaynak = 'zamanlanmis'", BEKCI),

    # --- KABLOLAR — en kritik ---------------------------------------------
    ("E) zamanlanmis isareti hic konmuyor (koruma SESSIZCE kapanir)",
     SH, "export FINAGENT_KOSU_KAYNAK=zamanlanmis",
     "# isaret kaldirildi", KABLO),

    ("F) bot sureci sohbet isaretini koymuyor",
     RUN, '        _os.environ[KOSU_KAYNAK_ENV] = "sohbet"\n\n        from finagent.bot import FinBot',
     '        from finagent.bot import FinBot', KABLO),
]


def _kos(test: str) -> int:
    return subprocess.run(
        [str(KOK / ".venv/bin/python"), "-c",
         f"import sys; sys.path.insert(0,'tests');"
         f"import test_smoke as T; T.{test}()"],
        cwd=KOK, capture_output=True, text=True, timeout=900).returncode


yakalanan = 0
for ad, yol, eski, yeni, test in M:
    if _kos(test) != 0:
        print(f"  ! TEST ZATEN KIRMIZI, mutasyon anlamsiz: {ad} [{test}]")
        continue
    p = KOK / yol
    yedek = p.read_text(encoding="utf-8")
    t2 = yedek.replace(eski, yeni)
    if t2 == yedek:
        print(f"  ! UYGULANAMADI: {ad}")
        continue
    p.write_text(t2, encoding="utf-8")
    try:
        tamam = _kos(test) == 0
        print(f"  {'✗ YAKALANMADI' if tamam else '✓ yakalandi'}: {ad}")
        yakalanan += 0 if tamam else 1
    finally:
        p.write_text(yedek, encoding="utf-8")
        for dizin in ("src", "tests"):
            for kok in KOK.joinpath(dizin).rglob("__pycache__"):
                shutil.rmtree(kok, ignore_errors=True)

print(f"\n{yakalanan}/{len(M)} mutasyon yakalandi · kaynaklar geri alindi")
