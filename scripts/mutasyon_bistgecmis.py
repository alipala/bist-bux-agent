"""
`bistgecmis` yanlis "yok" beyani mutasyon turu (2026-08-28).

OLCULEN VAKA: 27 Agustos kosumu 124 sembol icin "Yahoo'da yok" dedi.
Dokuzu orneklendi, SEKIZINDE Yahoo'nun kendi verisi zaten
veritabanindaydi (ALGYO 2.542 bar, 2016'dan). Yalnizca DMLKTG gercekten
bostu.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib, shutil, subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
BG = "src/finagent/collectors/bistgecmis.py"

YOK = "test_bistgecmis_SERISI_OLAN_sembole_YOK_DEMEZ"
ARIZA = "test_bistgecmis_KURTARILAMAYAN_seri_ARIZA_sayilir"
TAVAN = "test_bistgecmis_TAVANI_ASAN_sembol_YOK_SAYILMAZ"

M = [
    ("A) eski davranis: getirilemeyen dogrudan `yok` sayiliyor",
     BG, "                        getirilemeyen.append((sembol, kod, p))",
     "                        yok.append(sembol)", YOK),

    ("B) tek tek dogrulama atlaniyor (siniflandirma tahmine duser)",
     BG, "        for sembol, kod, p in getirilemeyen[:tavan]:",
     "        for sembol, kod, p in []:", YOK),

    ("C) seri VARLIGI sorulmuyor — hepsi `yok`",
     BG, "            if sembol in mevcut:", "            if False:", ARIZA),

    ("D) seri VARLIGI ters okunuyor",
     BG, "            if sembol in mevcut:", "            if sembol not in mevcut:",
     ARIZA),

    ("E) kurtarilan satirlar yazilmiyor (etiket duzelir, veri bayatlar)",
     BG, "            if n:\n                toplam += n\n                kurtarilan += 1",
     "            if False:\n                toplam += n\n                kurtarilan += 1",
     YOK),

    ("F) `bos` artik partial uretmiyor (gercek kayip sessiz gecer)",
     BG, "        elif hata or bos or yapisal:", "        elif hata or yapisal:",
     ARIZA),

    ("G) tavani asanlar `yok` sayiliyor (dogrulanmamis = yok)",
     BG, "        for sembol, _kod, _p in getirilemeyen[tavan:]:\n            "
         "# Dogrulanmadi: \"yok\" DEMIYORUZ. Dogrulanmamis sembolu\n            "
         "# yoklukla etiketlemek, kapatilan hatanin ta kendisi olurdu.\n            "
         "bos.append(sembol)",
     "        for sembol, _kod, _p in getirilemeyen[tavan:]:\n            "
     "yok.append(sembol)", TAVAN),

    ("H) gercek yokluk da `bos` sayiliyor — kapi tamamen acilir",
     BG, "            else:\n                yok.append(sembol)",
     "            else:\n                bos.append(sembol)", YOK),
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
