"""
Gun sonu SICILININ BILDIRILMESI mutasyon turu (2026-09-01).

Olcum sema 28'den beri calisiyordu ve `karne` yazilmisti — ama HICBIR
OKUYUCUSU YOKTU. Ne mesajda ne aracta. Bu tur, kablonun bagli KALDIGINI
ve bagliyken DOGRU SEYI soyledigini kanitlar.

EN KRITIK IKI MUTASYON
----------------------
D) Taban oran paydadan FARKLI satirlardan aliniyor. Sahada boyleydi:
   `GROUP BY`in ilk grubu okunuyordu ve raporlanan taban %74,6 iken
   paydanin gercek tabani %72,1'di. Gruplarin tabanlari cok farkli
   (stop_yendi %61,6, olculemedi %92,3), yani sayi HANGI GRUBUN ONCE
   GELDIGINE bagliydi.

H) Oran, taban olmadan mesaja giriyor. Kiyassiz isabet orani tesadufu
   beceri gibi gosterir; canli veride %84,6 tek basina bir kenar gibi
   okunur, oysa ayni gun piyasada oran %78,2 ve fark p>0,05.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
GS = "src/finagent/pulse/gun_sonu.py"
RU = "src/finagent/pulse/runner.py"
TO = "src/finagent/bot/tools.py"
JO = "src/finagent/pulse/journal.py"

ANLAM = "test_gun_sonu_KARNE_taban_farkinin_ANLAMLILIGINI_beyan_ediyor"
UCDURUM = "test_gun_sonu_KARNE_anlamlilik_UC_DURUMLU_False_ile_None_ayri"
TABANSATIR = "test_gun_sonu_KARNE_TABANI_PAYDANIN_SATIRLARINDAN_geliyor"
YUVARLA = "test_gun_sonu_GEREKEN_N_kendi_lehimize_yuvarlamiyor"
BINOM = "test_gun_sonu_BINOM_kuyrugu_bilinen_degerleri_veriyor"
SAHIP = "test_gun_sonu_KARNESI_SAHIBE_gore_suzuluyor"
ARACSAHIP = "test_gun_sonu_ARACI_karneyi_SAHIBE_baglayarak_cagiriyor"
GIZLE = "test_taktik_sicili_ANLAMSIZ_farkta_orani_GONDERMIYOR"
AYRI = "test_taktik_sicili_IKI_KARNEYI_ayri_anahtarda_donduruyor"
KIP = "test_gun_sonu_BILDIRIMI_yalnizca_NABIZDA_ve_olcum_yoksa_SESSIZ"
TABANSIZ = "test_gun_sonu_BILDIRIMI_ORANI_TABANSIZ_yazmiyor"
ACIKCA = "test_gun_sonu_BILDIRIMI_ANLAMSIZ_farki_ACIKCA_soyluyor"
BASKA = "test_gun_sonu_BILDIRIMI_baskasinin_sicilini_GONDERMIYOR"
DUSURME = "test_gun_sonu_BILDIRIMI_NABZI_DUSURMUYOR"
KABLO = "test_gun_sonu_BILDIRIM_KABLO_KACISI_yok"
GECIKME = "test_gun_sonu_GECIKME_KURALI_TARIHLE_calismiyordu_yayim_ts_ile_caliyor"
PENCERE = "test_gun_sonu_TABAN_ORANI_taktikle_AYNI_PENCEREDEN_olculuyor"
CATISMA = "test_gun_sonu_YAYIM_TS_catisma_anahtarina_GIRMIYOR"
YAZMA = "test_gun_sonu_YAYIM_TS_yazma_yolunda_DOLDURULUYOR"

M = [
    # --- 1A: anlamlilik ------------------------------------------------
    ("A) ANLAMLILIK hic hesaplanmiyor — `n>=20` yeter saniliyor",
     GS, '    out["taban_farki_anlamli"] = bool(p < ANLAMLILIK_P)',
     '    out["taban_farki_anlamli"] = True', ANLAM),

    ("B) ESIK gevsiyor (0,05 -> 0,20) — gurultu kenar sayilir",
     GS, "ANLAMLILIK_P = 0.05", "ANLAMLILIK_P = 0.20", ANLAM),

    ("C) `None` ile `False` birlesiyor — hesaplanamayan 'kenar yok' olur",
     GS, '        out["taban_farki_anlamli"] = None',
     '        out["taban_farki_anlamli"] = False', UCDURUM),

    ("D) TABAN paydadan FARKLI satirlardan — sahada olculen kusur",
     GS, "            WHERE gun_sonu_sonuc IN ({','.join('?' * len(PAYDAYA_GIREN))})",
     "            WHERE gun_sonu_sonuc IS NOT NULL AND ? IS NOT NULL",
     TABANSATIR),

    ("E) GEREKEN_N kendi lehimize yuvarliyor (int -> round)",
     GS, "        if _binom_kuyruk(n, int(n * oran), p0) < ANLAMLILIK_P:",
     "        if _binom_kuyruk(n, round(n * oran), p0) < ANLAMLILIK_P:",
     YUVARLA),

    ("F) BINOM kuyrugu cift yonlu gibi hesaplaniyor — p siser",
     GS, "    return sum(comb(n, i) * p0 ** i * (1.0 - p0) ** (n - i)\n"
         "               for i in range(k, n + 1))",
     "    return min(1.0, 2 * sum(comb(n, i) * p0 ** i * (1.0 - p0) ** (n - i)\n"
     "               for i in range(k, n + 1)))", BINOM),

    # --- sahip suzgeci -------------------------------------------------
    ("G) SAHIP suzgeci karnede yok — baskasinin sicili karisir",
     GS, '    ek = " AND sahip = ?" if sahip else ""', '    ek = ""', SAHIP),

    ("H) ARAC karneyi SAHIPSIZ cagiriyor — Ali, Yuksel'in sicilini gorur",
     TO, "            k = dict(karne(self.db, gun, sahip=self.sahip))",
     "            k = dict(karne(self.db, gun))", ARACSAHIP),

    # --- 1C: arac ------------------------------------------------------
    ("I) ANLAMSIZ oran ARACTA gizlenmiyor — model 'sicilim %83' der",
     TO, '            if k.get("taban_farki_anlamli") is not True:',
     "            if False:", GIZLE),

    ("J) IKI KARNE tek duzeye cokuyor — kolay ile zor karisir",
     TO, '                "gun_sonu": k,\n'
         "                # UFUK KARNESI OLDUGU GIBI: kendi orneklem uyarilarini\n"
         "                # ve guven araligini tasiyor.\n"
         '                "ufuk": Defter(self.db).karne(self.sahip),',
     "                **k,\n"
     '                "ufuk": Defter(self.db).karne(self.sahip),', AYRI),

    # --- 1D: bildirim --------------------------------------------------
    ("K) BILDIRIM her kipte gidiyor — gunde dort mesaj, gurultu",
     RU, "        if kip != self.GUN_SONU_BILDIRIM_KIPI:", "        if False:",
     KIP),

    ("L) OLCUM YOKKEN de mesaj gidiyor — 'bugun 0 taktik olculdu'",
     RU, '                if not gun["adet"]:\n                    continue',
     '                if False:\n                    continue', KIP),

    ("M) ORAN TABANSIZ yaziliyor — kiyassiz oran tesadufu beceri gosterir",
     RU, '        L[-1] += f" — ayni gun piyasada bu oran <b>%{_tr(taban, 1)}</b>."',
     '        L[-1] += "."', TABANSIZ),

    ("N) ANLAMSIZ fark SESSIZ geciliyor — mesaj eksiltiyle yalan soyler",
     RU, '                f"<i>Fark {fark} puan; n={olcum}\'de tesadufden AYIRT "\n'
         '                "EDILEMIYOR"',
     '                f"<i>Fark {fark} puan."\n'
     '                ""', ACIKCA),

    ("O) BILDIRIM sahibe gore suzulmuyor — baskasinin sicili gider",
     RU, "                gun = gun_sonu.gunun_olcumu(self.db, sahip)",
     "                gun = gun_sonu.gunun_olcumu(self.db)", BASKA),

    ("P) KABLO KESIK — `calistir` bildirimi cagirmiyor",
     RU, "        self._gun_sonu_bildirimi(kip, sahipler, bildir)",
     "        pass  # cagrilmiyor", KABLO),

    ("R) GENIS YAKALAMA daraliyor — bildirim NABZI DUSURUR",
     RU, "        except Exception as e:                        # noqa: BLE001\n"
         "            # KURAL 1: genis yakalama bilincli.\n"
         '            log.warning("[%s] gun sonu bildirimi basarisiz: %s: %s",',
     "        except ValueError as e:\n"
     "            # KURAL 1: genis yakalama bilincli.\n"
     '            log.warning("[%s] gun sonu bildirimi basarisiz: %s: %s",',
     DUSURME),

    # --- 1B: yayim damgasi ---------------------------------------------
    ("S) GECIKME KURALI yine tarihe duser — yayimdan onceki barlar girer",
     GS, '    damga = str(yayim_ts or olusma_ts or "")[:16].replace("T", " ")',
     '    damga = str(olusma_ts or "")[:16].replace("T", " ")', GECIKME),

    ("T) TABAN farkli pencereden olculuyor — taban siser, sahte kenar",
     GS, '        pencere = r["yayim_ts"] or r["olusma_ts"]',
     '        pencere = r["olusma_ts"]', PENCERE),

    ("U) YAYIM DAMGASI catisma anahtarina giriyor — gunde dort satir",
     JO, "                   ON CONFLICT(olusma_ts, instrument_id, ufuk_gun, ajan, sahip)",
     "                   ON CONFLICT(olusma_ts, yayim_ts, instrument_id, ufuk_gun, ajan, sahip)",
     CATISMA),

    ("V) DAMGA yazilmiyor — kolon acilir, dolduran olmaz (gun_sonu_endeks kalibi)",
     JO, "        yayim = _yayim_damgasi()", "        yayim = None", YAZMA),
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
