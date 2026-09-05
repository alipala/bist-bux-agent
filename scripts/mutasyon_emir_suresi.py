"""
Emir suresi (TIF / GTC) mutasyon turu (2026-09-05).

Ali: "IBKR mobil uygulamasinda GTC presetini kurdum ama agent hala
GTC emri veremem diyor. Root cause analizi yap."

KOK SEBEP TEK SATIRDI. `emirakis._istek` icinde `sure="DAY"` SABIT
yaziliydi. GTC destegi BASTAN BERI vardi ve bir uctan obur uca
dosenmisti:

    E.SURELER           {"DAY","GTC","IOC","OPG"}   dogrulama
    EmirIstegi.sure     alan
    .govde()["tif"]     IBKR govdesi
    .parmak_izi()       onay fisi
    db.emir_yaz(sure=)  defter
    _coz(... sure=...)  onay sonrasi yeniden kurma

Yalnizca GIRISI baglayan satir sabitti — bu deponun bir numarali
ariza kalibinin (kablo kacisi) en dar hali.

MOBIL PRESET BU KANALI KAPSAMIYOR ve kapsayamaz: `govde()` `tif`
alanini ACIKCA yaziyor, yani REST ucuna ne gonderirsek o gecerli.
Preset yalnizca uygulamadan girilen emirlerin varsayilani.

EN KRITIK UC MUTASYON
---------------------
A1) `sure` yine sabit "DAY" — kusurun kendisi.
B1) Arac `sure`yi komut dizesine eklemiyor: model GTC diyemez hale
    doner, ki Ali'nin bildirdigi belirti tam olarak buydu.
C1) Onay ekranindan sure satiri kalkiyor — kullanici emrin gun emri
    mi kalici mi oldugunu GORMEDEN onaylar.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
EA = "src/finagent/bot/emirakis.py"
TL = "src/finagent/bot/tools.py"

GTC = "test_GTC_ISTEGIN_TA_ICINE_kadar_gidiyor_ve_ONAY_EKRANINDA_yaziyor"
ARAC = "test_emir_ARACI_sureyi_KOMUTA_gecirmeyi_unutmuyor"
COZUM = "test_komut_cozumu"

M = [
    # ---- A) SABIT SURE --------------------------------------------------
    ("A1) `sure` yine SABIT 'DAY' — kusurun kendisi",
     EA, '                        sure=coz.get("sure") or varsayilan_sure(s)), iid',
     '                        sure="DAY"), iid', GTC),

    ("A2) Emirdeki sure AYARA eziliyor — oncelik ters",
     EA, '                        sure=coz.get("sure") or varsayilan_sure(s)), iid',
     '                        sure=varsayilan_sure(s)), iid', GTC),

    ("A3) Bozuk ayar guvenli tarafa dusmuyor — 'SONSUZ' patlatir",
     EA, '        log.warning("[emir] ibkr.varsayilan_sure=%r gecersiz — DAY kullanildi",\n'
         '                    ham)\n        return "DAY"',
     "        return ham", GTC),

    ("A4) Ayar hic okunmuyor — 'bir kez kur, unut' calismaz",
     EA, '    ham = str((s.get("ibkr.varsayilan_sure") if s else None) or "DAY").upper()',
     '    ham = "DAY"', GTC),

    # ---- B) COZUMLEYICI -------------------------------------------------
    ("B1) Arac `sure`yi komut dizesine EKLEMIYOR — belirtinin kendisi",
     TL, '            if sure:\n                arg += f" {sure}"',
     "            pass", ARAC),

    ("B2) Sure jetonu hic taninmiyor — GTC fiyat sanilir",
     EA, "    if kalan and kalan[-1].upper() in E.SURELER:\n"
         "        sure = kalan.pop().upper()",
     "    if False:\n        sure = kalan.pop().upper()", COZUM),

    ("B3) Buyuk/kucuk harf duyarli — 'gtc' reddedilir",
     EA, "    if kalan and kalan[-1].upper() in E.SURELER:",
     "    if kalan and kalan[-1] in E.SURELER:", COZUM),

    ("B4) Artan jeton SESSIZCE dusuyor — 'GTS' yazimi DAY emri gonderir",
     EA, "    if kalan[1:]:\n"
         "        raise EmirHatasi(\n"
         '            f"Anlasilmayan {kalan[1:]!r} — sure {sorted(E.SURELER)} "\n'
         '            "icinden biri olmali.")',
     "    pass", COZUM),

    # ---- C) GORUNURLUK --------------------------------------------------
    ("C1) Onay ekranindan SURE satiri kalkiyor — sessiz alan",
     EA, '        f"Sure: <b>{istek.sure}</b>" + (',
     '        "" + (', GTC),

    ("C2) Sure yaziliyor ama ANLAMI yazilmiyor — 'DAY' tek basina anlasilmaz",
     EA, '            " <i>(seans sonunda duser)</i>" if istek.sure == "DAY"\n'
         '            else " <i>(iptal edilene kadar gecerli)</i>"\n'
         '            if istek.sure == "GTC" else ""),',
     '            ""),', GTC),

    # ---- D) SOZLESME ----------------------------------------------------
    ("D1) `govde()` `tif` gondermiyor — IBKR kendi varsayilanina duser",
     "src/finagent/ibkr/emir.py", '            "tif": self.sure,', "", GTC),

    ("D2) Parmak izi sureyi tasimiyor — onaylanan ile gonderilen ayrisir",
     "src/finagent/ibkr/emir.py", '            "sure": self.sure,\n        }, sort_keys=True',
     "        }, sort_keys=True", GTC),
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
