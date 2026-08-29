"""
Hafiza yedegi mutasyon turu (2026-08-29).

OLCULEN DURUM: hafiza dosyalarinin (`~/.claude/.../memory`, 58 dosya,
6.201 satir) HICBIR yedegi yoktu — git YOK (repo disinda), Time
Machine "no destinations", iCloud kapsam disi, yedek betigi
dokunmuyor. Tek kopya, tek diskte.

Veritabani kaybolursa piyasa verisi yeniden cekilir; "bu tuzaga bes
kez dustuk" bilgisi YENIDEN URETILEMEZ.

EN KRITIK MUTASYONLAR:
  C — dogrulama kalkarsa BOZUK arsiv "yedek" sayilir (yanlis guven)
  F — `atlandi` yolunda cagrilmazsa hafiza gunde en fazla bir kez ve
      o da SANSA yedeklenir (db gunde bir, hafiza her oturumda degisir)

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib, shutil, subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
YD = "src/finagent/storage/yedek.py"
CFG = "config/settings.yaml"

ARSIV = "test_hafiza_yedegi_ARSIVLENIR_ve_ACILARAK_dogrulanir"
EKSIK = "test_hafiza_yedegi_EKSIK_ARSIVI_kabul_etmez"
AYIRIM = "test_hafiza_yedegi_TANIMSIZ_ve_YOK_durumlarini_AYIRIR"
IZOLE = "test_hafiza_ARIZASI_VERITABANI_yedegini_DUSURMEZ"

M = [
    ("A) `.md` disindaki dosyalar da arsivleniyor (kapsam kayar)",
     YD, '    dosyalar = sorted(kaynak.glob("*.md"))',
     '    dosyalar = sorted(p for p in kaynak.iterdir() if p.is_file())', ARSIV),

    ("B) gecici ada yazilmiyor — yarim arsiv DOGRU adla durur",
     YD, "    gecici = hedef.with_suffix(hedef.suffix + \".yaziliyor\")",
     "    gecici = hedef", ARSIV),

    ("C) DOGRULAMA kalkiyor (bozuk arsiv 'yedek' sayilir)",
     YD, "    if len(icerik) != len(dosyalar):",
     "    if False:", EKSIK),

    ("D) eksik arsiv diskte BIRAKILIYOR",
     YD, '        gecici.unlink(missing_ok=True)\n        return {"durum": "hata",\n'
         '                "sebep": f"arsiv eksik: {len(icerik)}/{len(dosyalar)} dosya"}',
     '        return {"durum": "hata",\n'
     '                "sebep": f"arsiv eksik: {len(icerik)}/{len(dosyalar)} dosya"}',
     EKSIK),

    ("E) ayar yokken SESSIZ 'ok' donuyor",
     YD, '        return {"durum": "atlandi", "sebep": "yedek.hafiza_dizini tanimsiz"}',
     '        return {"durum": "ok", "adet": 0}', AYIRIM),

    ("E2) olmayan dizin SESSIZCE atlaniyor (hata degil)",
     YD, '        return {"durum": "hata", "sebep": f"hafiza dizini yok: {kaynak}"}',
     '        return {"durum": "atlandi", "sebep": "yok"}', AYIRIM),

    ("F) `atlandi` yolunda hafiza HIC denenmiyor",
     YD, '                    "hafiza": _hafiza_adimi(settings, dizin, ayar)}',
     '                    }', IZOLE),

    ("G) hafiza arizasi VERITABANI yedegini dusuruyor",
     YD, '    hafiza = _hafiza_adimi(settings, dizin, ayar)',
     '    hafiza = hafiza_yedekle(settings, dizin)\n'
     '    if hafiza.get("durum") == "hata":\n'
     '        return {"durum": "hata", "sebep": hafiza["sebep"]}', IZOLE),

    ("I) arsiv icerigi BOZULUYOR (geri yukleme sinanmasaydi kacardi)",
     YD, "                t.add(d, arcname=d.name)",
     "                t.add(d, arcname=d.name)\n"
     "                break", ARSIV),

    ("H) ayar dosyasindan hafiza_dizini kaldiriliyor",
     CFG, "  hafiza_dizini: ", "  _kaldirildi_hafiza_dizini: ",
     "test_okunan_her_ayar_yaml_de_tanimli"),
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
