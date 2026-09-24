"""
Takvim filtresi mutasyon turu (2026-09-24, sema 31).

Her satir bir korumayi TEK BASINA geri alir, ilgili testi kosar ve
KIRMIZIYA dondugunu gosterir. Yesil test kanit degil; bir test ancak
koruduğu sey bozulunca kirmiziya donerse koruma sayilir
(`[[fixi-nasil-kanitlarim]]`).

EN KRITIK UC MUTASYON
---------------------
D) Tepki gununun KENDISI de engelleniyor. Sessizce bilanco SONRASI
   kirilimlari (tam o gun olusur) disarida birakir ve sinav sonucunu
   filtrenin lehine ya da aleyhine carpitir — hangisi oldugu bilinmez.
K) USD disi kotasyonda ad kapisi kalkiyor. AVTX (Avantium -> Avalo
   Therapeutics) geri gelir: YANLIS sirketin bilanco gunu.
S) FRED'e ozel User-Agent. Olculdu: istek ASILI kalir, kaynak `engelli`
   yazilir ve CPI/istihdam takvimi sessizce BOSALIR.

Betik KENDI KONUMUNDAN kok turetir (calisma agaci ya da ana depo) ve
testleri gecici bir DB_PATH ile kosar — canli veritabanina dokunmaz.
"""
import os
import pathlib
import shutil
import subprocess
import tempfile

KOK = pathlib.Path(__file__).resolve().parents[1]
PY = pathlib.Path("/Users/alipala/github/bist-bux-agent/.venv/bin/python")
OT = "src/finagent/analysis/olay_takvimi.py"
TT = "src/finagent/analysis/trend_takip.py"
BT = "src/finagent/collectors/bilancotakvim.py"
TK = "src/finagent/collectors/takvim.py"
TL = "src/finagent/bot/tools.py"
PL = "src/finagent/pipeline.py"

ZAMAN = "test_takvim_zaman_sinifi_seans_sinirlari_ABD_DOGU"
TEPKI = "test_takvim_tepki_gunu_seans_oncesi_ayni_gun_sonrasi_ertesi_gun"
ENGEL = "test_takvim_giris_engeli_tepki_gununun_KENDISINI_engellemez"
BOSLUK = "test_takvim_bosluk_olcumu_oynakliga_gore_normalize_ve_gecmise_bakar"
MOTOR = "test_trend_motoru_giris_engeli_bos_iken_BIREBIR_ayni_dolu_iken_ERTELER"
AV = "test_bilanco_takvimi_AV_kota_mesajini_BOS_TAKVIM_saymaz"
ESLES = "test_bilanco_takvimi_eslestirme_USD_ticker_yeter_USD_DISI_ad_SART"
YSEM = "test_bilanco_takvimi_yahoo_sembolu_borsa_sonekini_BOZMAZ"
YSAAT = "test_bilanco_takvimi_yahoo_saati_ABD_DOGUYA_cevrilir_naif_reddedilir"
YAZ = "test_bilanco_takvimi_yazim_ilk_gorulmeyi_KORUR_saati_NULL_ile_EZMEZ"
YAKLAS = "test_bilanco_takvimi_yaklasan_BILINMIYOR_ile_KAPSAM_DISI_ve_BAYATI_ayirir"
FREDP = "test_takvim_FRED_ayristirici_yalnizca_istenen_yilin_kalin_satirlari"
FREDUA = "test_takvim_FRED_isteklerinde_ozel_USER_AGENT_YOK_ve_BLS_yoklanmiyor"
ARAC = "test_takvim_araci_bilanco_dali_YOK_demez_kapsami_soyler"
KABLO = "test_bilanco_takvimi_KABLOSU_paket_istem_ve_nabiz_kipine_bagli"
YADAY = "test_bilanco_takvimi_USD_DISI_hisse_Yahoo_ya_YALIN_sorulmaz"
FON = "test_bilanco_takvimi_portfoyde_FONLAR_bilinmiyor_diye_GORUNMEZ"

M = [
    ("A) 16:00 seans ici sayiliyor — seans sonrasi bilancolar yanlis gune",
     OT, "    if s >= SEANS_KAPANIS:", "    if s > SEANS_KAPANIS:", ZAMAN),
    ("B) seans sonrasi aciklama AYNI gune yaziliyor — tepki bir gun erken",
     OT, "        adaylar = [ertesi]", "        adaylar = [ayni]", TEPKI),
    ("C) bilinmeyen saat tek gune indiriliyor — 'bilmiyoruz' gizleniyor",
     OT, "        adaylar = [ayni, ertesi]", "        adaylar = [ayni]", TEPKI),
    ("D) tepki gununun KENDISI de engelleniyor",
     OT, "        for k in range(max(0, j - pencere), j):",
     "        for k in range(max(0, j - pencere), j + 1):", ENGEL),
    ("E) negatif indeks SARIYOR — serinin sonundaki gunler engellenir",
     OT, "        for k in range(max(0, j - pencere), j):",
     "        for k in range(j - pencere, j):", ENGEL),
    ("F) N olay gununu de iceriyor — ileriye bakma, olay kendini kucultur",
     OT, "        N = _atr(seri, i, atr_pencere)",
     "        N = _atr(seri, min(i + 1, len(seri) - 1), atr_pencere)", BOSLUK),
    ("G) motor engeli hic okumuyor — kablo kesik, filtre etkisiz",
     TT, '                if giris_engeli and str(bar["ts"])[:10] in giris_engeli:',
     "                if False:", MOTOR),
    ("H) islem kaydi stop'u tasimiyor — birincil olcut hesaplanamaz",
     TT, '                "stop": pozisyon["stop"],', "", MOTOR),
    ("J) AV kota mesaji BOS TAKVIM sayiliyor",
     BT, '    if govde.startswith("{"):', "    if False:", AV),
    ("K) USD disi kotasyonda ad kapisi yok — AVTX geri gelir",
     BT, "            if not ad_tutuyor and not usd:", "            if False:", ESLES),
    ("L) USD'de de ad SART — JPM gibi ayni sirketler duser",
     BT, "            if not ad_tutuyor and not usd:",
     "            if not ad_tutuyor:", ESLES),
    ("M) her nokta tireye donuyor — ASML.AS -> ASML-AS, yanlis 'yok'",
     BT, "    if nokta and len(sonek) == 1 and sonek.isalpha():",
     "    if nokta:", YSEM),
    ("N) saat dilimsiz damga kabul ediliyor — makinenin saat dilimiyle okunur",
     BT, '        if getattr(t, "tzinfo", None) is None:', "        if False:", YSAAT),
    ("O) bos saat onceki saati EZIYOR",
     BT, "                 saat  = COALESCE(excluded.saat, bilanco_takvimi.saat),",
     "                 saat  = excluded.saat,", YAZ),
    ("Q) kaydirilmis (bayat) tarih ileri takvimde kaliyor",
     BT, "                 AND son_gorulme >= datetime('now', ?)",
     "                 AND (son_gorulme >= datetime('now', ?) OR 1)", YAKLAS),
    ("R) kaynak ayrismasi gizleniyor — birinin tarihi sessizce secilir",
     BT, '            "kaynaklar_ayrisiyor": tarihler if len(tarihler) > 1 else None,',
     '            "kaynaklar_ayrisiyor": None,', YAKLAS),
    ("S) FRED'e ozel UA — istek askida, CPI takvimi bosalir",
     TK, "                              headers=FRED_BASLIK, timeout=40.0,",
     '                              headers={"User-Agent": UA}, timeout=40.0,', FREDUA),
    ("T) eski BLS satiri kaliyor — istem her gun var olmayan bosluk yazar",
     TK, "            c.execute(\"DELETE FROM takvim_kaynak WHERE kaynak = 'bls'\")",
     "            pass", FREDUA),
    ("U) FRED ayristirici yil suzmuyor — gelecek yil bu yila karisir",
     TK, "        if not ay or int(m.group(3)) != yil:", "        if not ay:", FREDP),
    ("V) arac kapsam disini 'ilan edilmemis' sanıyor",
     TL, '        if adaylar and all(e["venue"] != "BUX" for e in adaylar):',
     "        if False:", ARAC),
    ("X) USD disi hisse YALIN soruluyor — AVTX -> Avalo'nun tarihleri",
     BT, "    return f\"{s}{sonek}\" if sonek else None",
     "    return f\"{s}{sonek}\" if sonek else yahoo_sembolu(s)", YADAY),
    ("X2) EUR hissede sonek eklenmiyor — yalin sembol",
     BT, '    if ccy in ("", "USD"):', "    if True:", YADAY),
    ("Y) fonlar portfoy listesinde kaliyor — 'tarih bilinmiyor' gurultusu",
     BT, "            and not fon_mu(r[\"name\"], r[\"asset_type\"])]",
     "            ]", FON),
    ("W) KABLO: paket bilanco takvimini tasimiyor",
     PL, '        bundle["bilanco_takvimi"] = yaklasan_bilancolar(db, sahip, gun=int(',
     '        _ = yaklasan_bilancolar(db, sahip, gun=int(', KABLO),
]


def _pycache_temizle() -> None:
    # Bayat .pyc: ayni boyut + ayni saniyede geri alinan mutasyon onbellekte
    # kalabiliyor (fixi-nasil-kanitlarim §4.2).
    for dizin in ("src", "tests"):
        for k in KOK.joinpath(dizin).rglob("__pycache__"):
            shutil.rmtree(k, ignore_errors=True)


def _kos(test: str) -> int:
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "DB_PATH": str(pathlib.Path(d) / "t.db"),
               "TELEGRAM_BOT_TOKEN": ""}
        return subprocess.run(
            [str(PY), "-c", f"import sys; sys.path.insert(0,'tests');"
                            f"import test_smoke as T; T.{test}()"],
            cwd=KOK, capture_output=True, text=True, timeout=900,
            env=env).returncode


yakalanan, uygulanan = 0, 0
_pycache_temizle()
for ad, yol, eski, yeni, test in M:
    if _kos(test) != 0:
        print(f"  ! TEST ZATEN KIRMIZI: {ad} [{test}]")
        continue
    p = KOK / yol
    yedek = p.read_text(encoding="utf-8")
    if yedek.count(eski) != 1:
        print(f"  ! UYGULANAMADI ({yedek.count(eski)} eslesme): {ad}")
        continue
    p.write_text(yedek.replace(eski, yeni), encoding="utf-8")
    _pycache_temizle()
    try:
        kod = _kos(test)
    finally:
        p.write_text(yedek, encoding="utf-8")
        _pycache_temizle()
    uygulanan += 1
    if kod != 0:
        yakalanan += 1
        print(f"  ✓ YAKALANDI (cikis {kod}): {ad}")
    else:
        print(f"  ✗ KACTI: {ad}  [{test}]")

# Geri alma dogrulamasi: dosyalar mutasyondan once neyse o.
for _, yol, eski, _, _ in M:
    assert eski in (KOK / yol).read_text(encoding="utf-8"), f"GERI ALINAMADI: {yol}"
print(f"\n{yakalanan}/{uygulanan} mutasyon yakalandi ({len(M)} tanimli)")
raise SystemExit(0 if yakalanan == uygulanan == len(M) else 1)
