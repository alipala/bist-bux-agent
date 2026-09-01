"""
Taktik gun sonu olcumu mutasyon turu (2026-09-01).

Ufuk puanlamasi 179 taktigin 1'ini olcebilmisti (3-30 gun bekliyor).
Bu katman ayni aksam olcuyor ama FARKLI bir seyi: taktik uygulanabilir
miydi, seansi gecti mi.

EN KRITIK MUTASYON — E: taban oran ayni testi kullanmazsa sayi siser.
Ilk yazimda kullanmiyordu ve canli olcumde %76,3'e karsi %34,7 gibi
42 puanlik SAHTE bir kenar uretti; ayni teste cevrilince fark 2,6 puana
dustu.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
GS = "src/finagent/pulse/gun_sonu.py"
RU = "src/finagent/pulse/runner.py"

LAG = "test_gun_sonu_YAYIM_BARI_olcume_KATILMIYOR"
BARIYER = "test_gun_sonu_UC_BARIYER_sonuclari"
BEKLE = "test_gun_sonu_BEKLE_olculmuyor"
SEANS = "test_gun_sonu_SEANS_KAPANMADIYSA_olcmuyor"
OLCULEMEDI = "test_gun_sonu_SAATLIK_YOKSA_olculemedi_YAZILIYOR"
TABAN = "test_gun_sonu_TABAN_ORAN_ayni_testi_kullaniyor"
ESIK = "test_gun_sonu_KARNESI_ESIK_TASIMIYOR_ve_ayri_duruyor"
KABLO = "test_gun_sonu_KABLO_KACISI_yok"

M = [
    ("A) GECIKME KURALI kalkiyor — yayim bari olcume giriyor (Lag 0)",
     GS, 'return [b for b in barlar if str(b["ts"])[:16] > damga]',
     'return [b for b in barlar if str(b["ts"])[:16] >= damga]', LAG),

    ("B) `bekle` de olculuyor — tanimi olmayan sey olculur",
     GS, 'OLCULEN_TURLER = ("alim", "koruma")',
     'OLCULEN_TURLER = ("alim", "koruma", "bekle")', BEKLE),

    ("C) SEANS kontrolu kalkiyor — yarim gun tam gun gibi raporlanir",
     GS, "        if seans_kapandi_mi(borsa, tarih, simdi) is not True:",
     "        if False:", SEANS),

    ("D) SAATLIK yoksa SESSIZCE atlaniyor — taktik sonsuza dek olculmez",
     GS, "            etiket = OLCULEMEDI", "            continue", OLCULEMEDI),

    ("E) TABAN ORAN farkli test kullaniyor — sahte kenar uretir",
     GS, "        dipler = [b.get(\"low\") for b in barlar if b.get(\"low\") is not None]\n"
         "        if not dipler:\n            continue\n"
         "        toplam += 1\n        if min(dipler) > esik:",
     "        son = barlar[-1].get(\"close\")\n"
     "        if not son:\n            continue\n"
     "        toplam += 1\n        if son >= bas:", TABAN),

    ("F) GIRIS TETIKLENMEYEN de basarisiz sayiliyor — paydaya giriyor",
     GS, "                if s not in (OLCULEMEDI, GIRIS_YOK))",
     "                if s not in (OLCULEMEDI,))", ESIK),

    ("G) KUCUK ORNEKLEM uyarisi kalkiyor",
     GS, '"not": ("ORNEKLEM YETERSIZ — sonuc cikarma" if payda < 20 else None),',
     '"not": None,', ESIK),

    ("H) KABLO KESIK — `calistir` olcumu cagirmiyor",
     RU, "        self._gun_sonu_olcumu(kip)", "        pass  # cagrilmiyor",
     KABLO),

    ("I) GENIS YAKALAMA daraliyor — olcum NABZI DUSURUR",
     RU, "        except Exception as e:                        # noqa: BLE001\n"
         '            log.warning("[%s] gun sonu olcumu basarisiz: %s: %s",',
     "        except ValueError as e:\n"
     '            log.warning("[%s] gun sonu olcumu basarisiz: %s: %s",', KABLO),

    ("J) `alim`de stop yolu izlenmiyor — her giris AYAKTA sayilir",
     GS, "        if girdi and stop is not None and dusuk <= stop:\n"
         "            return STOP_YENDI",
     "        if False:\n            return STOP_YENDI", BARIYER),
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
