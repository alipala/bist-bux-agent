import importlib, pathlib, shutil, subprocess, sys, tempfile

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
MUTASYONLAR = [
    ("2) donchian_giris bugunun barini ICERSIN",
     "src/finagent/pulse/seviye.py",
     'max(kapanis[-GIRIS_PENCERE - 1:-1])',
     'max(kapanis[-GIRIS_PENCERE:])',
     "test_strateji_1_KIRILIM_gorus_uretir"),
    ("3) STOP_N 2.0 -> 1.0",
     "src/finagent/analysis/trend_takip.py",
     "STOP_N = 2.0",
     "STOP_N = 1.0",
     "test_strateji_1_KIRILIM_gorus_uretir"),
    ('5) kosula "close < donchian_cikis" yaz',
     "src/finagent/pulse/strateji.py",
     'kosul = f"close < {stop:g}"',
     'kosul = "close < donchian_cikis"',
     "test_strateji_9_KOSUL_GRAMERE_UYUYOR"),
]

def _yesil_mi(test: str) -> bool:
    """
    MUTASYONDAN ONCE TEST YESIL MI?

    OLCULDU 2026-08-28: bir testte tirnak hatasi vardi ve test ZATEN
    KIRMIZIYDI; mutasyon turu uc bozmayi "yakalandi" diye raporladi.
    Zaten kirmizi bir teste karsi mutasyon HICBIR SEY KANITLAMAZ —
    kanit yontemi yine kendini kandirmisti.
    """
    r = subprocess.run(
        [str(KOK / ".venv/bin/python"), "-c",
         f"import sys; sys.path.insert(0,'tests');"
         f"import test_smoke as T; T.{test}()"],
        cwd=KOK, capture_output=True, text=True, timeout=900)
    return r.returncode == 0


for ad, yol, eski, yeni, test in MUTASYONLAR:
    if not _yesil_mi(test):
        print(f"  ! TEST ZATEN KIRMIZI, mutasyon anlamsiz: {ad} [{test}]")
        continue
    p = KOK / yol
    yedek = p.read_text(encoding="utf-8")
    t2 = yedek.replace(eski, yeni)
    if t2 == yedek:
        print(f"  ! UYGULANAMADI: {ad}  ({eski[:40]})")
        continue
    p.write_text(t2, encoding="utf-8")
    try:
        r = subprocess.run(
            [str(KOK / ".venv/bin/python"), "-c",
             f"import sys; sys.path.insert(0,'tests');"
             f"import test_smoke as T; T.{test}()"],
            cwd=KOK, capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            print(f"  ✗ YAKALANMADI: {ad}   [{test}]")
        else:
            son = [x for x in r.stderr.strip().splitlines() if x.strip()][-1]
            print(f"  ✓ [{test}] yakaladi: {ad}")
            print(f"      -> {son[:110]}")
    finally:
        p.write_text(yedek, encoding="utf-8")
        # BAYAT BYTECODE, MUTASYON TESTINI YALANCI YAPAR — OLCULDU.
        # Python onbellegi (mtime, boyut) ciftiyle dogruluyor. Mutasyon
        # ayni boyutta ve ayni saniye icinde geri alininca `.pyc`
        # GECERLI sayiliyor: kaynak `STOP_N = 2.0` derken calisan modul
        # 1.0 kaliyor. Sonraki her kosum, DUZELTILMIS kodu bozukmus
        # gibi gosterir — kanit yontemi kanitin kendisini bozar.
        for kok in KOK.joinpath("src").rglob("__pycache__"):
            shutil.rmtree(kok, ignore_errors=True)
print("kaynaklar geri alindi")
