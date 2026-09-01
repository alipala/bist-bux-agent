"""
Bayat portfoy verisi mutasyon turu (2026-09-01).

OLCULEN KUSUR (Ali bildirdi): sabah taramasi "IBKR · tek kalem: KO"
diyordu. KO bir gun once SATILMISTI ve elde VRT vardi. Sebep: `ibkr`
collector'u HICBIR zamanli kosumun `kaynaklar` listesinde yoktu, yani
pozisyonlar 27 Agustos'ta kalmisti.

Ustelik tarama IBKR icin "4 gun onceki EKRAN GORUNTUSU" diyordu —
IBKR'de ekran goruntusu YOK, canli API var. Cumle yalnizca yanlis
degildi, kullaniciya YANLIS IS yaptiriyordu.

Ali'nin istegi: "OZELLIKLE BAYAT BIR VERI ISTEMIYORUM. Eger bayat veri
varsa agent bana SORSUN gun bitmeden." Dipnot BILGI verir, SORU is
yaptirir.

ZINCIR:
  1. kaynak dogru siniflandirilmali (api vs ekran)   (D, I)
  2. esik SINIRINDA dogru olmali                     (G)
  3. yalnizca NABIZ kipinde sorulmali                (C)
  4. sessizken SUSMALI, patlarken NABZI DUSURMEMELI  (E, F)
  5. kablo bagli olmali, butce sayacinin ICINDE      (A, B)
  6. `ibkr` kosumlarin listesinde olmali             (H)

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
RU = "src/finagent/pulse/runner.py"
PO = "src/finagent/analysis/portfolio.py"
DB = "src/finagent/storage/db.py"
CFG = "config/settings.yaml"

KAYNAK = "test_ADET_KAYNAGI_IBKR_API_digerleri_EKRAN"
ESIK = "test_bayat_hesaplar_ESIK_SINIRINDA_dogru"
KIP = "test_bayat_veri_YALNIZCA_NABIZ_kipinde_soruluyor"
IS = "test_bayat_veri_ISTENEN_IS_kaynaga_gore_DEGISIYOR"
SESSIZ = "test_bayat_veri_SESSIZ_OLDUGUNDA_SUSAR_ve_NABZI_DUSURMEZ"
KABLO = "test_bayat_veri_KABLO_KACISI_yok_ve_IBKR_kosumlara_bagli"

M = [
    ("A) KABLO KESIK — `calistir` bayat kontrolunu cagirmiyor",
     RU, "        self._bayat_veri_uyarisi(kip, sahipler, bildir)",
     "        pass  # bayat kontrolu cagrilmiyor", KABLO),

    ("B) KONTROL BUTCE SAYACININ ONUNDE — panel kalan sureyi buyuk gorur",
     RU, "        basladi = time.monotonic()",
     "        self._bayat_veri_uyarisi(kip, sahipler, bildir)\n"
     "        basladi = time.monotonic()", KABLO),

    ("C) KIP SUZGECI kalkiyor — her kosuda soruluyor, gun ici seansi boler",
     RU, "        if kip != self.BAYAT_VERI_KIPI:\n"
         '            return {"durum": "atlandi", "sebep": f"{kip} bu kontrolun kipi degil"}',
     "        if False:\n"
     '            return {"durum": "atlandi", "sebep": "x"}', KIP),

    ("D) IBKR'ye EKRAN GORUNTUSU isteniyor — yapamayacagi is soyleniyor",
     RU, '                        ne = "IBKR oturumu acikken tazelenmeli (ekran goruntusu DEGIL)"',
     '                        ne = "guncel ekran goruntusu gonder"', IS),

    ("E) SESSIZKEN de mesaj gidiyor — gercek uyari gurultuye gomulur",
     RU, "                if not bayat:\n                    continue",
     "                if False:\n                    continue", SESSIZ),

    ("F) GENIS YAKALAMA daraliyor — db arizasi NABZI DUSURUR",
     RU, "        except Exception as e:                        # noqa: BLE001\n"
         "            # GENIS YAKALAMA BILINCLI (kural 1): bu bir BAKIM adimi,",
     "        except ValueError as e:\n"
     "            # GENIS YAKALAMA BILINCLI (kural 1): bu bir BAKIM adimi,",
     SESSIZ),

    ("G) ESIK sinirinda kayiyor — dunku goruntu de bayat sayilir",
     DB, "            if yas > esik_gun:", "            if yas >= esik_gun:", ESIK),

    ("H) `ibkr` SABAH kosumundan cikiyor — pozisyonlar yine bayatlar",
     CFG, "      kaynaklar: [prices, binance, makro, tuik, ibkr]",
     "      kaynaklar: [prices, binance, makro, tuik]", KABLO),

    ("I) IBKR 'ekran' diye siniflandiriliyor — kaynak ayrimi coker",
     PO, '    "ibkr": "api",          # Client Portal Gateway — gunluk giris gerektirir',
     '    "ibkr": "ekran",', KAYNAK),

    ("J) BILINMEYEN hesap 'api' varsayiliyor — olmayan tazelik iddiasi",
     PO, '        "adet_kaynagi": ADET_KAYNAGI.get(str(hesap).lower(), "ekran"),',
     '        "adet_kaynagi": ADET_KAYNAGI.get(str(hesap).lower(), "api"),',
     KAYNAK),
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
