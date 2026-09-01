"""
KAP: kaynaktan cekme + kimlik semasi mutasyon turu (2026-09-01).

IKI KUSUR BIRDEN KAPANDI:
  1. Ana sayfanin 50 satirlik DOM'u kaziniyordu; API gunun tamamini
     veriyor. Olculdu: 31 Agu'da API 379, defterde 129 (%66 kayip).
  2. Kimlik `sha1(kap|zaman|sirket|baslik)` idi ve `_parse_kap_time`
     SANIYEYI dusurdugu icin ayni sirketin ayni dakikada ayni baslikli
     iki bildirimi TEK SATIRA cokuyordu (~%12).

EN KRITIK MUTASYON — B: sha1 dongusu HER satiri ezerse degisiklik
hicbir ise yaramaz. Kod dogru, kablo yanlis — bu deponun imzasi.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
KP = "src/finagent/collectors/kap.py"
DB = "src/finagent/storage/db.py"

KIMLIK = "test_kap_kimlik_KAP_NUMARASINDAN_uretiliyor"
DAKIKA = "test_kap_AYNI_DAKIKADAKI_iki_bildirim_ARTIK_cokmuyor"
TARAYICI = "test_kap_TARAYICI_KULLANMIYOR_ve_TARIH_ARALIGI_gonderiyor"
LISTE = "test_kap_LISTE_DONMEZSE_sessizce_SIFIR_demiyor"
NUMARASIZ = "test_kap_NUMARASIZ_kayit_ESKI_SEMAYA_dusuyor"
GOC = "test_kap_kimlik_gocu_YERINDE_yaziyor_ve_TEKRARLANABILIR"
BAGLI = "test_kap_kimlik_gocu_SEMA_KURULUMUNA_bagli"

M = [
    ("A) KIMLIK numaradan uretilmiyor — eski cokme geri gelir",
     KP, '                "id": self.db.kap_kimlik(no),', '                "id": None,',
     DAKIKA),

    ("B) sha1 dongusu HER satiri eziyor — degisiklik ise yaramaz",
     KP, "            if not r[\"id\"]:\n                r[\"id\"] = sha1(",
     "            if True:\n                r[\"id\"] = sha1(", DAKIKA),

    ("C) YANIT SEKLI dogrulanmiyor — hata SESSIZCE 0 bildirim olur",
     KP, "        if not isinstance(ham, list):", "        if False:", LISTE),

    ("D) GOC sema kurulumundan cikiyor — hic kosmaz",
     DB, "        self._kap_kimlik_gocu()", "        pass  # goc cagrilmiyor",
     BAGLI),

    ("E) GOC kaynak suzgecini kaybediyor — SEC satirlarina da dokunur",
     DB, "                   WHERE source = 'kap'\n                     AND id NOT LIKE 'kap:%'",
     "                   WHERE id NOT LIKE 'kap:%'", GOC),

    ("F) GOC zaten cevrilmisleri de yeniden yaziyor — tekrarlanabilir degil",
     DB, "                     AND id NOT LIKE 'kap:%'\n", "\n", GOC),

    ("G) `kap_kimlik` sayi olmayani da kabul ediyor — uydurma kimlik",
     DB, '        return f"kap:{int(s)}" if s.isdigit() else None',
     '        return f"kap:{s}"', KIMLIK),

    ("H) TARAYICI yeniden isteniyor — kosum yavaslar, kapsam degismez",
     KP, "    needs_browser = False", "    needs_browser = True", TARAYICI),

    ("I) NUMARASIZ kayitta URL UYDURULUYOR",
     KP, "            href = self.BILDIRIM_URL.format(id=no) if no.isdigit() else None",
     "            href = self.BILDIRIM_URL.format(id=no)", NUMARASIZ),
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
