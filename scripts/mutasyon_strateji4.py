"""Adim 4 mutasyon turu — deftere yazim. Bkz. `fixi-nasil-kanitlarim`."""
import pathlib, shutil, subprocess
KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
R = "src/finagent/pulse/runner.py"
M = [
    ("A) YALNIZCA secilenleri yaz (tam genislik olcumu kaybolur)",
     R, "rapor = defter.kaydet(tam + ikinci, sahip)",
     "rapor = defter.kaydet(ikinci, sahip)",
     "test_strateji4_HER_KIRILIM_YAZILIR_secilenler_IKINCI_SATIRLA"),
    ("B) ikinci satiri HIC yazma (iki karne ayrilmaz)",
     R, "rapor = defter.kaydet(tam + ikinci, sahip)",
     "rapor = defter.kaydet(tam, sahip)",
     "test_strateji4_HER_KIRILIM_YAZILIR_secilenler_IKINCI_SATIRLA"),
    ("C) secilenlere AYNI ajan adini ver (UNIQUE cakisir)",
     R, '{**g, "ajan": "strateji_secilen"}', '{**g}',
     "test_strateji4_DORT_SAYAC_SIFIR_gorus_DUSMUYOR"),
    ("D) sahip yokken VARSAYILANA dus",
     R, 'sahip = (self.s.get("ibkr.sahip") or "").strip().lower()',
     'sahip = (self.s.get("ibkr.sahip") or "ali").strip().lower()',
     "test_strateji4_SAHIP_ibkr_sahipten_VARSAYILAN_YOK"),
    ("E) taramanin ICINDE deftere yaz (her elle kosu kirletir)",
     R, "        strateji = self._strateji_taramasi(kip)\n        if strateji:",
     "        strateji = self._strateji_taramasi(kip)\n        if strateji and False:",
     "test_strateji4_ORTAK_FAZ_deftere_yazimi_GERCEKTEN_CAGIRIYOR"),
    ("F) `seviyeler` govdesini de deftere gonder",
     R, '{k: v for k, v in g.items() if k not in ("seviyeler", "conid")}',
     'dict(g)',
     "test_strateji4_SEVIYELER_GOVDESI_DEFTERE_GITMIYOR"),
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
        print(f"  ! UYGULANAMADI: {ad}")
        continue
    p.write_text(t2, encoding="utf-8")
    try:
        r = subprocess.run(
            [str(KOK / ".venv/bin/python"), "-c",
             f"import sys; sys.path.insert(0,'tests');"
             f"import test_smoke as T; T.{test}()"],
            cwd=KOK, capture_output=True, text=True, timeout=600)
        print(f"  {'✗ YAKALANMADI' if r.returncode == 0 else '✓ yakalandi'}: {ad}")
    finally:
        p.write_text(yedek, encoding="utf-8")
        # Bayat .pyc mutasyon testini YALANCI yapar — olculdu 2026-08-27.
        for kok in KOK.joinpath("src").rglob("__pycache__"):
            shutil.rmtree(kok, ignore_errors=True)
print("kaynaklar geri alindi")
