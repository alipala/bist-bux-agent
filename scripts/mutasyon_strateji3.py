import pathlib, shutil, subprocess
KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
R = "src/finagent/pulse/runner.py"
M = [
    ("A) tabloda 20G YUK yerine kapanis yaz (ikinci hesap yolu)",
     R, "f\"{_tablo_fiyat(sv.get('donchian_giris')):>10}\"",
     "f\"{_tablo_fiyat(g.get('giris')):>10}\"",
     "test_strateji3_TABLO_DEGERLERI_seviyeler_ciktisiyla_BIREBIR"),
    ("B) emir satirina belgedeki YANLIS 'LMT' ornegini koy",
     R, 'return f"<code>/emir {_esc(sembol)} AL &lt;adet&gt; {fiyat_metni}</code>"',
     'return f"<code>/emir {_esc(sembol)} AL &lt;adet&gt; LMT {fiyat_metni}</code>"',
     "test_strateji3_EMIR_SATIRI_GERCEKTEN_AYRISTIRILABILIYOR"),
    ("C) conid yokken yine de komut ver",
     R, 'if not gorus.get("conid"):', 'if False:',
     "test_strateji3_CONID_YOKSA_CALISMAYAN_KOMUT_VERILMEZ"),
    ("D) sifir kirilimda SESSIZ kal",
     R, '''    L = [f"📊 <b>STRATEJI — {_esc(tarih)} kirilimlari</b>" if tarih''',
     '''    if not gorusler:
        return ""
    L = [f"📊 <b>STRATEJI — {_esc(tarih)} kirilimlari</b>" if tarih''',
     "test_strateji3_SIFIR_KIRILIMDA_DA_MESAJ_GIDER"),
    ("E) kirpildigini SOYLEME",
     R, '''        if kirpilan:
            L.append(f"<i>Tabloda {len(gosterilen)} satir gosterildi, "
                     f"{kirpilan} satir kirpildi (toplam {len(gorusler)}).</i>")''',
     "        if False:\n            pass",
     "test_strateji3_KIRPMA_VARSA_SOYLENIYOR"),
    ("F) 'kirilim yok'u TARANAMAYAN sayisina kat",
     R, 'kirilimsiz = sayaclar.pop("kirilim yok", 0)',
     'kirilimsiz = sayaclar.get("kirilim yok", 0)',
     "test_strateji3_TARANAMAYANLAR_SEBEBIYLE_yaziliyor"),
    ("G) her kipte kos (kip kontrolu kalk)",
     R, 'if not ayar["enabled"] or kip != ayar["kip"]:',
     'if not ayar["enabled"]:',
     "test_strateji3_YALNIZCA_AYARDAKI_KIPTE_kosar"),
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
        for kok in KOK.joinpath("src").rglob("__pycache__"):
            shutil.rmtree(kok, ignore_errors=True)
print("kaynaklar geri alindi")
