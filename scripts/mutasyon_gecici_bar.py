"""
Seans ici bar (gecici) mutasyon turu — sema 26 (2026-08-31).

OLCULEN KUSUR: `prices` bir GUNLUK BAR tablosu ve her satirin anlami
"D gununde su oldu". Ama toplayicilar seans ACIKKEN de o gunun satirini
yaziyordu ve tabloda bunu ayirt edecek HICBIR ALAN yoktu — yerlesmis
kapanis ile yarim enstantane birebir ayni goruniyordu.

ASELS 31 Agu: gercek kapanis 386,25 (hacim 34,0M) iken `yahoo_borsa`
seans ici 396,75 yazmisti (hacim 11,7M, dip 396,00 — dususu hic
gormemis). Alis 12:16'da 396,75'tendi; enstantane o civarda dondugu
icin K/Z TAM 0,00 cikti ve bot "basabastasin" dedi. Gercek -73,50 TL.

KAYNAK ONCELIGI COZMEZ ve denenmedi: `midas` de seans icinde BIST
kapanisi yaziyor. Tek gecerli ayrim SEANSIN KAPANIP KAPANMADIGI.

ZINCIR — her halkasi ayri kirilabilir:
  1. seans bilgisi dogru olmali            (E, F)
  2. bayrak TEK YAZMA KAPISINDA konmali    (A)
  3. bilinmiyorsa TAHMIN EDILMEMELI        (D)
  4. seri gecici bari VARSAYILAN dismali   (B)
  5. KAYNAK SECIMI de bozulmamali          (C)
  6. `gecici_dahil` sozunu tutmali         (G)

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
PI = "src/finagent/piyasa.py"
DB = "src/finagent/storage/db.py"

SEANS = "test_seans_kapandi_mi_HUKUM_VEREMEDIGINDE_None_donuyor"
BAYRAK = "test_GECICI_BAYRAK_tek_yazma_kapisinda_hesaplaniyor"
SERI = "test_GECICI_BAR_seriden_VARSAYILAN_olarak_dusuyor"
KAYNAK = "test_GECICI_BAR_kaynak_secimini_BOZMUYOR"
BILINMEYEN = "test_BORSASI_BILINMEYEN_enstruman_gecici_SAYILMIYOR"
ASELS = "test_ASELS_VAKASI_ayni_gun_IKI_KAYNAK_dogru_olani_seciyor"

M = [
    ("A) BAYRAK HIC KONMUYOR — yalan tabloya geri doner",
     DB, "        return 1 if kapandi is False else 0",
     "        return 0", BAYRAK),

    ("B) SERI gecici bari VARSAYILAN olarak ICERIYOR",
     DB, '        suzgec = "" if gecici_dahil else " AND COALESCE(gecici, 0) = 0"',
     '        suzgec = ""', SERI),

    ("C) KAYNAK SECIMI gecici bari sayiyor — yahoo haksiz yere en taze",
     DB, '        gs = "" if gecici_dahil else " AND COALESCE(gecici, 0) = 0"',
     '        gs = ""', KAYNAK),

    ("D) BILINMEYEN borsa GECICI sayiliyor — yerlesmis barlar seriden duser",
     DB, "        return 1 if kapandi is False else 0",
     "        return 0 if kapandi is True else 1", BILINMEYEN),

    ("E) BUGUNUN seansi SAATE bakmadan kapali sayiliyor",
     PI, "        return yerel.time() >= kapanis      # bugun — saate bagli",
     "        return True", SEANS),

    ("F) 7/24 piyasada BUGUNUN bari yerlesmis sayiliyor",
     PI, "        return gun < simdi.astimezone(timezone.utc).date()",
     "        return True", SEANS),

    ("G) `gecici_dahil` kaynak secimine TASINMIYOR — parametre sozunu tutmaz",
     DB, "        k = self.fiyat_kaynagi(instrument_id, tercih_ccy=tercih_ccy,\n"
         "                               gecici_dahil=gecici_dahil)",
     "        k = self.fiyat_kaynagi(instrument_id, tercih_ccy=tercih_ccy)",
     ASELS),

    ("H) GECMIS gun de gecici sayiliyor (tarih karsilastirmasi ters)",
     PI, "        if gun < yerel.date():\n"
         "            return True                     # gecmis gun — yerlesmis",
     "        if gun < yerel.date():\n            return False", SEANS),
]


def _pycache_temizle() -> None:
    """
    Bayat `.pyc` MUTASYONU GORUNMEZ KILAR.

    OLCULDU 2026-08-31: tek karakterlik bir mutasyon (`"?"` -> `"0"`)
    dosya BOYUTUNU degistirmiyor ve Python'un import kontrolu
    (mtime, size) bayat bayt kodu yeniden kullanabiliyor. Ayni mutasyon
    bir kosumda KACTI, sonrakinde yakalandi — yani harness'in kendisi
    guvenilmezdi ve "N/N yakalandi" raporu bunu gizliyordu.

    Temizlik artik testten ONCE de yapiliyor.
    """
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
        print(f"  ! TEST ZATEN KIRMIZI, mutasyon anlamsiz: {ad} [{test}]")
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
