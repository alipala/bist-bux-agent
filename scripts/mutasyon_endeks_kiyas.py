"""
Gun ici endeks kiyasi mutasyon turu (2026-09-01).

NEDEN VAR: Ali sordu — "bu dususler oldu ise ilgili haberi cekip ona
dayandirmasi gerekmez mi?" Haberden ONCE sorulacak soru sudur ve
cevabi UYDURULAMAZ: hareket hisseye mi ozgu, piyasa geneli mi?
BIST 100 %2 duserken AGROT %8 dustuyse fark GERCEKTIR; "X yuzunden
dustu" cumlesi ise cogu zaman sonradan kurulmus bir hikayedir.

EN KRITIK MUTASYON — A: pencere kontrolu. Ilk yazimda YOKTU ve
fonksiyon sessizce yanlis uretti: XU100'un bugune ait bari olmayinca
31 ve 28 Agustos'u alip DUNUN endeks hareketini BUGUNUN gun ici hisse
hareketiyle karsilastirdi. Sayi makul gorunuyordu.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
GT = "src/finagent/pulse/gunici_tarayici.py"

PENCERE = "test_endeks_karsilastirma_PENCERELERI_AYNI_TUTUYOR"
VEKIL = "test_endeks_karsilastirma_KENDISI_VEKILSE_kiyas_yapmiyor"
KABLO = "test_endeks_karsilastirmasi_ADAYA_TASINIYOR_kablo_kacisi_yok"

M = [
    ("A) PENCERE KONTROLU kalkiyor — dunun endeksi bugunun hissesiyle kiyaslanir",
     GT, '        if str(g[-1]["ts"])[:10] != bugun:',
     "        if False:", PENCERE),

    ("B) REFERANS YOKKEN sessizce goreli uretiliyor",
     GT, '            return {"endeks_yok": f"{vekil[\'sembol\']} gun ici verisi yok"}',
     '            return {"goreli_%": 0.0}', PENCERE),

    ("C) KENDISI VEKILSE de kiyas yapiliyor — totoloji (goreli hep 0)",
     GT, '        return {"endeks_yok": "kendisi piyasa vekili"}',
     '        return {"endeks": "kendisi", "goreli_%": 0.0}', VEKIL),

    ("D) KABLO KESIK — aday sozluguna tasinmiyor, taktik goremez",
     GT, '            **endeks_karsilastir(db, r["id"], hareket, simdi),',
     "", KABLO),

    ("E) GORELI HAREKET ters isaretle hesaplaniyor",
     GT, '        "goreli_%": round((hareket - endeks_hareket) * 100, 2),',
     '        "goreli_%": round((endeks_hareket - hareket) * 100, 2),', PENCERE),
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
