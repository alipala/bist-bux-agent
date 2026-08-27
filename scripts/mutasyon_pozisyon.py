"""Pozisyon kapisi mutasyon turu — `zaten pozisyonda` (2026-08-28 kusuru)."""
import pathlib, shutil, subprocess
KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
ST, TT = "src/finagent/pulse/strateji.py", "src/finagent/analysis/trend_takip.py"
M = [
    ("A) kapiyi TAMAMEN kaldir (eski davranis)",
     ST, '    if sv.get("pozisyonda"):\n        return "zaten pozisyonda"',
     "    if False:\n        return \"zaten pozisyonda\"",
     "test_strateji_ZATEN_POZISYONDAKI_SEMBOL_TEKRAR_SINYAL_VERMEZ"),
    ("B) giris barini da POZISYON say (kural hic sinyal vermez)",
     ST, 'return bool(a and str(a["giris_ts"])[:10] < str(sv.get("bar_ts"))[:10])',
     "return bool(a)",
     "test_strateji_GIRIS_BARI_POZISYON_SAYILMAZ"),
    ("C) acik pozisyonu HIC dondurme",
     TT, "    return _yurut(seri, borsa_limiti, taban_kilidi, asgari_devir)[1]",
     "    return None",
     "test_strateji_ZATEN_POZISYONDAKI_SEMBOL_TEKRAR_SINYAL_VERMEZ"),
    ("D) kapanmamis islemi `islemler()`e sizdir",
     TT, "    return out, pozisyon", "    return out + ([pozisyon] if pozisyon else []), pozisyon",
     "test_strateji_ZATEN_POZISYONDAKI_SEMBOL_TEKRAR_SINYAL_VERMEZ"),
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


for ad, yol, eski, yeni, test in M:
    if not _yesil_mi(test):
        print(f"  ! TEST ZATEN KIRMIZI, mutasyon anlamsiz: {ad} [{test}]")
        continue
    p = KOK / yol
    yedek = p.read_text(encoding="utf-8")
    t2 = yedek.replace(eski, yeni)
    if t2 == yedek:
        print(f"  ! UYGULANAMADI: {ad}"); continue
    p.write_text(t2, encoding="utf-8")
    try:
        r = subprocess.run(
            [str(KOK / ".venv/bin/python"), "-c",
             f"import sys; sys.path.insert(0,'tests');"
             f"import test_smoke as T; T.{test}()"],
            cwd=KOK, capture_output=True, text=True, timeout=900)
        print(f"  {'✗ YAKALANMADI' if r.returncode == 0 else '✓ yakalandi'}: {ad}")
    finally:
        p.write_text(yedek, encoding="utf-8")
        for kok in KOK.joinpath("src").rglob("__pycache__"):
            shutil.rmtree(kok, ignore_errors=True)
print("kaynaklar geri alindi")

# --- kotasyon tercihi (ASML kusuru, 2026-08-28) ---
DB = "src/finagent/storage/db.py"
M2 = [
    ("E) tercih_ccy'yi GORMEZDEN gel (eski davranis)",
     DB, "        if tercih_ccy:\n            hedefler = ([tercih_ccy] if isinstance(tercih_ccy, str)",
     "        if False:\n            hedefler = ([tercih_ccy] if isinstance(tercih_ccy, str)",
     "test_strateji_KOTASYON_TERCIHI_SEMBOLU_SESSIZCE_ELEMEZ"),
    ("F) tercih_ccy'yi HER ZAMAN uygula (portfoy tarafini boz)",
     DB, "        if tercih_ccy:\n", "        if True:\n            tercih_ccy = tercih_ccy or ['USD']\n",
     "test_strateji_KOTASYON_TERCIHI_SEMBOLU_SESSIZCE_ELEMEZ"),
    ("G) strateji tercihi GECIRMESIN",
     ST, "tercih = ayar.get(\"para_birimleri\") or None", "tercih = None",
     "test_strateji_KOTASYON_TERCIHI_SEMBOLU_SESSIZCE_ELEMEZ"),
]
for ad, yol, eski, yeni, test in M2:
    if not _yesil_mi(test):
        print(f"  ! TEST ZATEN KIRMIZI, mutasyon anlamsiz: {ad} [{test}]")
        continue
    p = KOK / yol
    yedek = p.read_text(encoding="utf-8")
    t2 = yedek.replace(eski, yeni)
    if t2 == yedek:
        print(f"  ! UYGULANAMADI: {ad}"); continue
    p.write_text(t2, encoding="utf-8")
    try:
        r = subprocess.run(
            [str(KOK / ".venv/bin/python"), "-c",
             f"import sys; sys.path.insert(0,'tests');"
             f"import test_smoke as T; T.{test}()"],
            cwd=KOK, capture_output=True, text=True, timeout=900)
        print(f"  {'✗ YAKALANMADI' if r.returncode == 0 else '✓ yakalandi'}: {ad}")
    finally:
        p.write_text(yedek, encoding="utf-8")
        for kok in KOK.joinpath("src").rglob("__pycache__"):
            shutil.rmtree(kok, ignore_errors=True)
