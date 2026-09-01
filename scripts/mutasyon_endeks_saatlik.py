"""
Endeks saatlik referansi mutasyon turu (2026-09-01).

OLCULEN BOSLUK: `endeks_karsilastir` "bu hareket hisseye mi ozgu,
piyasa geneli mi" diye soruyordu ama cevabi verecek veri YOKTU —
XU100/QQQ/AEX icin 0 saatlik bar. Kiyas her adayda "bugune ait deger
yok" diyordu.

Simdi saatlik toplayici endeks vekillerini de hedefliyor ve kiyas
calisiyor (olculdu: KBORU -%8,79 · XU100 -%0,39 -> goreli -%8,40).

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
SA = "src/finagent/collectors/saatlik.py"
PR = "src/finagent/collectors/prices.py"
CFG = "config/settings.yaml"

HEDEF = "test_saatlik_ENDEKS_VEKILLERINI_de_hedefliyor"
GUNLUK = "test_saatlik_ENDEKS_gunluk_collectoru_DEGISTIRMIYOR"

M = [
    ("A) ENDEKS DONGUSU kalkiyor — referans yine olusmaz",
     SA, "        from .prices import ENDEKSLER\n"
         '        istenen = self.s.get("sources.saatlik.endeksler") or []',
     "        from .prices import ENDEKSLER\n        istenen = []", HEDEF),

    ("B) IKINCI ESLEME yaziliyor — kopyalar zamanla ayrisir",
     SA, "            yahoo, ad, ccy = tanim",
     '            yahoo, ad, ccy = (kod, kod, "USD")', HEDEF),

    ("C) TANIMSIZ KOD sessizce yok sayiliyor",
     SA, '                self._atlanan.append(f"{kod}(endeks eslemesi yok)")',
     "                pass", HEDEF),

    # D ILK YAZIMDA OLU HEDEFE ATILMISTI: `sources.alphavantage.indices`
    # satirini degistiriyordu, oysa gunluk endeks cekimi
    # `sources.prices.indices`i okuyor. Ayni dosyada AYNI ADLI iki
    # anahtar var ve ikisi de mesru; yanlisi secmek mutasyonu anlamsiz
    # kilmisti. Test bastan dogruydu.
    ("D) XU100 GUNLUK cekime giriyor — Is Yatirim zaten veriyor, bosuna istek",
     CFG, '    indices: ["QQQ", "AEX", "SPX"]',
     '    indices: ["QQQ", "AEX", "SPX", "XU100"]', GUNLUK),

    ("E) XU100 eslemesi siliniyor — BIST kiyasi referanssiz kalir",
     PR, '    "XU100": ("XU100.IS", "BIST 100",                   "TRY"),', "",
     GUNLUK),
]


def _pycache_temizle() -> None:
    """Bayat `.pyc` mutasyonu gorunmez kilar — bkz. mutasyon_tur_olcumu."""
    for dizin in ("src", "tests"):
        for k in KOK.joinpath(dizin).rglob("__pycache__"):
            shutil.rmtree(k, ignore_errors=True)


def _kos(test: str) -> int:
    return subprocess.run(
        [str(KOK / ".venv/bin/python"), "-c",
         f"import sys; sys.path.insert(0,'tests');"
         f"import test_smoke as T; T.{test}()"],
        cwd=KOK, capture_output=True, text=True, timeout=900).returncode


yakalanan = 0
for ad, yol, eski, yeni, test in M:
    if _kos(test) != 0:
        print(f"  ! TEST ZATEN KIRMIZI: {ad} [{test}]")
        continue
    p = KOK / yol
    yedek = p.read_text(encoding="utf-8")
    t2 = yedek.replace(eski, yeni)
    if t2 == yedek:
        print(f"  ! UYGULANAMADI: {ad}")
        continue
    p.write_text(t2, encoding="utf-8")
    _pycache_temizle()
    try:
        tamam = _kos(test) == 0
        print(f"  {'✗ YAKALANMADI' if tamam else '✓ yakalandi'}: {ad}")
        yakalanan += 0 if tamam else 1
    finally:
        p.write_text(yedek, encoding="utf-8")
        _pycache_temizle()

print(f"\n{yakalanan}/{len(M)} mutasyon yakalandi · kaynaklar geri alindi")
