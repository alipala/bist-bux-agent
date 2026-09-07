"""
Tur siniri yanlis alarmi — mutasyon turu (2026-09-07).

Ali Google sifresini degistirdi, OAuth dustu, panel patladi. O kisim
DOGRUYDU. `/login` sonrasi kimlik duzeldi ama yoklama HALA "abonelik
cevap vermedi" diyordu.

KOK SEBEP: SDK "Reached maximum number of turns (N)" durumunu da ISTISNA
olarak firlatiyor ve `abonelik_saglik` bunu ARIZA sayiyordu. Oysa bu
mesaj ancak model KONUSTUKTAN sonra olusur — kimlik kabul edilmis,
cevap uretilmis, yalnizca tur butcesi dolmus. Aboneligin BOZUK
oldugunun degil, CALISTIGININ kanitidir.

OLCULDU (launchd'ye benzetilmis ortamda, ayni saniyelerde):
    max_turns=1  ->  "Reached maximum number of turns (1)"
    max_turns=2  ->  OK
    max_turns=3  ->  "Reached maximum number of turns (3)"
Sonuc tur SAYISINA degil, modelin o cagride tur harcayip harcamadigina
bagli — kararsiz ve zararsiz.

EN KRITIK IKI MUTASYON
----------------------
A1) Kapi kaldiriliyor — yanlis alarm geri gelir (kusurun kendisi).
B1) Kapi YAZILI ama CAGRILMIYOR — kablo kacisi; fonksiyon dogru,
    testi yesil, kullanici yine yanlis alarm alir.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
LL = "src/finagent/llm.py"

TUR = "test_llm_TUR_SINIRI_ariza_DEGIL_cunku_CLI_CEVAP_VERDI"
KANIT = "test_llm_yoklama_hatasi_KANITSIZ_GIRIS_SORUNU_IDDIA_ETMEZ"

M = [
    ("A1) Tur siniri kapisi YOK — yanlis alarm geri gelir",
     LL, '_CEVAP_IZLERI = ("maximum number of turns",)',
     "_CEVAP_IZLERI = ()", TUR),

    ("A2) Kapi COK GENIS — gercek arizalar da 'calisiyor' sayilir",
     LL, '_CEVAP_IZLERI = ("maximum number of turns",)',
     '_CEVAP_IZLERI = ("maximum number of turns", "error result")', TUR),

    ("A3) Kapi 'success'i de gecirir — asil opak hata gizlenir",
     LL, '_CEVAP_IZLERI = ("maximum number of turns",)',
     '_CEVAP_IZLERI = ("maximum number of turns", "success")', TUR),

    ("B1) Kapi YAZILI ama CAGRILMIYOR — kablo kacisi",
     LL, "        if _cevap_verdi(e):\n"
         '            return True, ("Claude aboneligi calisiyor "\n'
         '                          "(yoklama tur sinirinda bitti — CLI CEVAP VERDI).")',
     "        if False:\n"
     '            return True, ("Claude aboneligi calisiyor "\n'
     '                          "(yoklama tur sinirinda bitti — CLI CEVAP VERDI).")',
     TUR),

    ("B2) Kucuk/buyuk harf duyarli — 'Reached' gecemez",
     LL, "    ham = str(e).lower()\n    return any(iz in ham for iz in _CEVAP_IZLERI)",
     "    ham = str(e)\n    return any(iz in ham for iz in _CEVAP_IZLERI)", TUR),

    ("C1) Kimlik izleri bosaltildi — GERCEK sifre sorunu 'belirsiz' olur",
     LL, '_KIMLIK_IZLERI = ("unauthorized", "authentication", "not logged in",',
     '_KIMLIK_IZLERI = ("zzz_hicbir_zaman", "authentication", ', KANIT),

    ("C2) Her ariza KIMLIK sayiliyor — kanitsiz teshis geri gelir",
     LL, "    if any(iz in ham.lower() for iz in _KIMLIK_IZLERI):",
     "    if True:", KANIT),
]


def _pycache_temizle() -> None:
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
_pycache_temizle()
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
