"""Adim 6 mutasyon turu — karne, kontrol grubu ve fren."""
import pathlib, shutil, subprocess
KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
ST, TT, RU = ("src/finagent/pulse/strateji.py",
              "src/finagent/analysis/trend_takip.py",
              "src/finagent/pulse/runner.py")
M = [
    ("A) fark DUZELTILMIS tabandan alinsin (elma-armut)",
     ST, 'fark = round(tam["ham_isabet_%"] - rastgele["isabet_%"], 1)',
     'fark = round(tam["isabet_%"] - rastgele["isabet_%"], 1)',
     "test_strateji6_IKI_TABAN_KARISTIRILMIYOR"),
    ("B) kontrol grubu HIC hesaplanmasin",
     ST, "        if k.get(\"isabet_%\") is not None:", "        if False:",
     "test_strateji6_KARNE_DORT_SAYI_ve_KONTROL_GRUBU"),
    ("C) kontrolsuzken de FREN cek",
     ST, '    if k["fark_%"] is None:', '    if False:',
     "test_strateji6_KONTROLSUZ_KARNE_YAYINLANMAZ_ve_FREN_CEKILMEZ"),
    ("D) az olcumde de fren cek (gurultuye tepki)",
     ST, "    if olcum < FREN_ASGARI_OLCUM:", "    if False:",
     "test_strateji6_OLCUM_YOKSA_OLCULMEMIS_DIYE_BEYAN_EDILIR"),
    ("E) fren esigi asilsa da tavani DUSURME",
     ST, '        return {"tavan": FREN_TAVANI, "fren": True,',
     '        return {"tavan": varsayilan, "fren": True,',
     "test_strateji6_FREN_KARNE_KOTUYSE_TAVAN_DUSER"),
    ("F) isabeti TUR ORTALAMASINDAN turet",
     TT, "                ornek += 1\n                pozitif += 1 if net > 0 else 0",
     "                ornek += 1",
     "test_strateji6_RASTGELE_KONTROL_ISABET_TUR_ORTALAMASINDAN_TURETILMEZ"),
    ("G) freni tavana BAGLAMA (ayardaki degeri kullan)",
     RU, "        secilen = ST.secim(sonuc[\"gorusler\"], etkin_tavan,",
     "        secilen = ST.secim(sonuc[\"gorusler\"], int(ayar[\"gunluk_emir_tavani\"]),",
     "test_strateji6_FREN_TAVANA_GERCEKTEN_BAGLI"),
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
            cwd=KOK, capture_output=True, text=True, timeout=900)
        print(f"  {'✗ YAKALANMADI' if r.returncode == 0 else '✓ yakalandi'}: {ad}")
    finally:
        p.write_text(yedek, encoding="utf-8")
        for kok in KOK.joinpath("src").rglob("__pycache__"):
            shutil.rmtree(kok, ignore_errors=True)
print("kaynaklar geri alindi")
