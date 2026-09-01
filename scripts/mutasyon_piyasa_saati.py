"""
Piyasa saati onkontrolu mutasyon turu (2026-09-01).

Emir hazirlama kapali piyasayi HIC gormuyordu. Kapali borsada piyasa
emri, acilis seansinin belirleyecegi bir fiyata pesinen razi olmaktir —
modulun kendi doktrininde "MKT + gercek zamanli olmayan veri = ENGEL"
kuralinin ayni gerekcesi.

EN KRITIK IKI MUTASYON
----------------------
C) Borsa cozulemedigi halde "kapali" varsayiliyor. Olculdu: conid
   tasiyan 501 enstrumanin 445'inde borsa cozulemiyor. Bunlari kapali
   saymak mesru emirleri engellerdi — koruma degil ARIZA olurdu.

F) `db` gecilmeden cagriliyor. Parametre varsayilani None; cagiran
   gecmezse kontrol SESSIZCE olur. Bu deponun bir numarali ariza kalibi.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
OK = "src/finagent/ibkr/onkontrol.py"
EA = "src/finagent/bot/emirakis.py"

LIMIT = "test_piyasa_saati_KAPALIYKEN_limit_UYARIR_engellemez"
MKT = "test_piyasa_saati_KAPALIYKEN_piyasa_emri_ENGELLENIR"
ACIK = "test_piyasa_saati_ACIKKEN_hicbir_sey_soylemez"
COZULEMEZ = "test_piyasa_saati_BORSA_COZULEMEZSE_HUKUM_VERMEZ"
DBYOK = "test_piyasa_saati_DB_YOKSA_sessizce_atlanir_HATA_SAYILMAZ"
ARIZA = "test_piyasa_saati_KONTROLUN_ARIZASI_emri_engellemez"
HAFTA = "test_piyasa_saati_HAFTA_SONU_de_kapali_sayilir"
KABLO = "test_piyasa_saati_KABLO_KACISI_yok"
SAGLAM = "test_onkontrol_saglikli_senaryoda_GECER"

M = [
    ("A) KAPALI PIYASA hic bakilmiyor — kontrol yok",
     OK, "    seans = _seans_durumu(db, istek.conid, simdi)",
     "    seans = None", MKT),

    ("B) LIMIT de ENGELLENIYOR — acilisa kuyruk birakmak imkansiz olur",
     OK, '        if istek.tur == "MKT":\n'
         "            k.engeller.append(\n"
         '                f"piyasa KAPALI ({ne_zaman}) — piyasa emri acilis "\n'
         '                "fiyatindan doner, ne odeyecegin BILINMIYOR")',
     "        if True:\n"
     "            k.engeller.append(\n"
     '                f"piyasa KAPALI ({ne_zaman}) — piyasa emri acilis "\n'
     '                "fiyatindan doner, ne odeyecegin BILINMIYOR")', LIMIT),

    ("C) BORSA COZULEMEYINCE 'kapali' varsayiliyor — mesru emirler durur",
     OK, "    if seans is not None and not seans[\"acik\"]:",
     "    if seans is None or not seans[\"acik\"]:", COZULEMEZ),

    ("D) ACIK seansta da uyariliyor — gurultu korumayi degersizlestirir",
     OK, "    if seans is not None and not seans[\"acik\"]:",
     "    if seans is not None:", ACIK),

    ("E) PIYASA EMRI yalnizca UYARI aliyor — ne odeyecegin bilinmiyor",
     OK, '        if istek.tur == "MKT":\n'
         "            k.engeller.append(",
     '        if istek.tur == "MKT":\n'
     "            k.uyarilar.append(", MKT),

    ("F) KABLO KESIK — `emirakis` `db` gecmeden cagiriyor",
     EA, "k = OK.dogrula(istemci, istek, db=db, sahip=sahip)",
     "k = OK.dogrula(istemci, istek, sahip=sahip)", KABLO),

    ("G) KONTROLUN ARIZASI yukari sizip emri durduruyor",
     OK, "    except Exception as e:                            # noqa: BLE001\n"
         "        # KONTROLUN ARIZASI EMRI ENGELLEMEZ.",
     "    except ValueError as e:\n"
     "        # KONTROLUN ARIZASI EMRI ENGELLEMEZ.", ARIZA),

    ("H) `simdi` yok sayiliyor — test duvar saatine baglanir",
     OK, "        return next((d for d in seans_durumlari(simdi) if d[\"borsa\"] == borsa),",
     "        return next((d for d in seans_durumlari() if d[\"borsa\"] == borsa),",
     HAFTA),

    ("I) `identities.exchange`e donuluyor — 451/501'de BOS, ve piyasa.py reddediyor",
     OK, "        borsa = borsa_coz(db, satir[0][\"instrument_id\"], satir[0][\"venue\"])",
     "        borsa = satir[0][\"venue\"]", MKT),

    ("J) DB YOKKEN de hukum veriliyor — cagirilar db=None gecebiliyor",
     OK, "    if db is None or not conid:", "    if not conid:", DBYOK),

    ("K) SAGLIKLI senaryo bozuluyor mu — regresyon kapisi",
     OK, "    seans = _seans_durumu(db, istek.conid, simdi)",
     "    seans = {\"acik\": False, \"borsa\": \"X\", \"durum\": \"kapandi\",\n"
     "             \"seans\": \"?\"}", SAGLAM),
]


def _pycache_temizle() -> None:
    for dizin in ("src", "tests"):
        for k in KOK.joinpath(dizin).rglob("__pycache__"):
            shutil.rmtree(k, ignore_errors=True)


def _kos(test: str) -> int:
    return subprocess.run(
        [str(KOK / ".venv/bin/python"), "-c",
         f"import sys; sys.path.insert(0,'tests');"
         f"import test_ibkr as T; T.{test}()"],
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
