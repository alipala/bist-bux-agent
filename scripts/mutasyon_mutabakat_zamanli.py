"""
Zamanli mutabakat mutasyon turu (2026-08-31).

OLCULEN DURUM: `mutabakat.kos()` gercek dolum fiyatini ve komisyonunu
KOLONLARA yaziyordu ve mekanizma calisiyordu — ama tek cagirani
sohbetteki `ibkr_mutabakat` araciydi. HICBIR zamanlanmis kosuda yoktu.
Sonuc: 4 emrin dordunde de `dolum_fiyat` NULL, SLIPPAGE SIFIR GOZLEM.

Geriye donuk alinamiyor: IBKR'nin islem penceresi KO emri icin 0 kayit
dondurdu. Kacan dolum KALICI OLARAK kayip.

EN KRITIK MUTASYONLAR:
  A — kablo kesilirse hata YENIDEN olusur (fonksiyon var, cagiran yok)
  C — genis yakalama kalkarsa IBKR arizasi NABZI DUSURUR
  F — sahip kapisi kalkarsa baskasinin hesabindaki emirler baskasina gider

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
RU = "src/finagent/pulse/runner.py"
EA = "src/finagent/bot/emirakis.py"

KABLO = "test_mutabakat_ZAMANLI_kosuya_BAGLI_kablo_kacisi_yok"
DUSMEZ = "test_mutabakat_NABZI_DUSURMEZ"
SESSIZ = "test_mutabakat_SESSIZ_OLDUGUNDA_SUSAR_haber_varsa_KONUSUR"
ONEK = "test_mutabakat_DOLUM_yazilinca_MESAJ_ONEKI_degisir"
SAHIP = "test_mutabakat_YALNIZCA_HESAP_SAHIBINE_gider"
BOS = "test_mutabakat_SAHIP_TANIMSIZSA_atlar_ve_SESSIZ_KALMAZ"
SAYAC = "test_mutabakat_ozetli_DOLUM_SAYACI_gercekten_sayiyor"
BOSOZ = "test_mutabakat_ozetli_BOS_DEFTER_sifir_ozet_doner"

M = [
    ("A) KABLO KESILIYOR — `calistir()` mutabakati cagirmiyor",
     RU, "        self._mutabakat_kosumu(kip, sahipler, bildir)",
     "        pass  # mutabakat cagrilmiyor", KABLO),

    ("B) dolum sayaci YAZIMDAN SONRA bakiyor (hep 0 doner)",
     EA, '        if k.alanlar.get("dolum_fiyat") is not None:\n'
         "            dolum_yazildi += 1",
     "        if False:\n            dolum_yazildi += 1", SAYAC),

    ("C) GENIS YAKALAMA kalkiyor — IBKR arizasi NABZI DUSURUR",
     RU, "        except Exception as e:                            # noqa: BLE001\n"
         "            # GENIS YAKALAMA BILINCLI (kural 1).",
     "        except ValueError as e:\n"
         "            # GENIS YAKALAMA BILINCLI (kural 1).", DUSMEZ),

    ("D) SESSIZLIK kalkiyor — her kosuda mesaj",
     RU, '        haber = any(ozet[k] for k in\n'
         '                    ("dolum_yazildi", "yazilan", "cozulemeyen", "defterde_yok"))',
     "        haber = True", SESSIZ),

    ("E) `cozulemeyen` haber sayilmiyor (sessiz kayip)",
     RU, '                    ("dolum_yazildi", "yazilan", "cozulemeyen", "defterde_yok"))',
     '                    ("dolum_yazildi", "yazilan", "defterde_yok"))', SESSIZ),

    ("E2) `defterde_yok` haber sayilmiyor (IBKR'de bizim olmayan emir)",
     RU, '                    ("dolum_yazildi", "yazilan", "cozulemeyen", "defterde_yok"))',
     '                    ("dolum_yazildi", "yazilan", "cozulemeyen"))', SESSIZ),

    ("F) SAHIP KAPISI kalkiyor — kipin TUM alicilarina gider",
     RU, "        if hedef not in sahipler:\n"
         '            return {"durum": "atlandi", "sebep": f"{hedef} bu kipin alicisi degil"}',
     "        if False:\n"
         '            return {"durum": "atlandi", "sebep": "x"}', SAHIP),

    ("G) sahip tanimsizken UYDURULUYOR (ilk aliciya dusuluyor)",
     RU, '        if not hedef:\n'
         '            log.info("[%s] mutabakat atlandi: `ibkr.sahip` tanimsiz", kip)\n'
         '            return {"durum": "atlandi", "sebep": "ibkr.sahip yok"}',
     '        if not hedef:\n            hedef = (sahipler or ["ali"])[0]',
     BOS),

    ("J) BOS defterde ozet EKSIK ALANLA donuyor",
     EA, '    bos_ozet = {"karar": 0, "yazilan": 0, "dolum_yazildi": 0,\n'
         '                "cozulemeyen": 0, "defterde_yok": 0}',
     '    bos_ozet = {"karar": 0, "yazilan": 0}', BOSOZ),

    ("H) DOLUM oneki kalkiyor — sirada bir satir gibi gecer",
     RU, '            onek = ("💰 <b>Dolum kaydedildi</b>\\n\\n"\n'
         '                    if ozet["dolum_yazildi"] else "")',
     '            onek = ""', ONEK),

    ("I) ozet metni AYRISTIRARAK uretiliyor (bicime bagimli)",
     EA, '    return metin, {"karar": len(kararlar), "yazilan": yazilan,',
     '    return metin, {"karar": metin.count("•"), "yazilan": yazilan,', SAYAC),
]


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
    try:
        tamam = _kos(test) == 0
        print(f"  {'✗ YAKALANMADI' if tamam else '✓ yakalandi'}: {ad}")
        yakalanan += 0 if tamam else 1
    finally:
        p.write_text(yedek, encoding="utf-8")
        for dizin in ("src", "tests"):
            for kok in KOK.joinpath(dizin).rglob("__pycache__"):
                shutil.rmtree(kok, ignore_errors=True)

print(f"\n{yakalanan}/{len(M)} mutasyon yakalandi · kaynaklar geri alindi")
