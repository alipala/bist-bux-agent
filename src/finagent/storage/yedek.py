"""
VERITABANI YEDEGI — projenin tek geri uretilemez varligi.

NEDEN VAR
---------
Her sey tek bir SQLite dosyasinda: tahmin defteri (sistemin kendi
isabetini olctugu yer), sohbet arsivi, aylarca toplanan fiyat/bilanco
gecmisi ve ekran goruntusunden okunmus portfoy anlik goruntuleri.
Bunlarin cogu YENIDEN URETILEMEZ — piyasa verisi belki tekrar cekilir,
ama "16 Agustos'ta ne tahmin etmistik" bir daha kurulamaz.

2026-08-21'e kadar repoda hicbir yedekleme mekanizmasi yoktu.

NEDEN `cp` DEGIL `VACUUM INTO` — OLCULDU, TARTISILMADI
------------------------------------------------------
Veritabani WAL kipinde. `cp` yalnizca ana dosyayi kopyalar; henuz
checkpoint edilmemis WAL icerigi KOPYAYA GIRMEZ ve o islemler sessizce
kaybolur. Olculdu (2026-08-21, izole bir veritabaninda):

    canli : [1, 2, 3]
    cp    : [1]            <- IKI COMMIT'LENMIS islem kayip
    VACUUM: [1, 2, 3]

Ayni gun bu tam da sahada yasandi: canli veritabanindan `cp` ile alinan
bir kopyada ROSE'un tez damgasi YOKTU, cunku o an WAL'da duruyordu.
`cp` bazen dogru sonuc verir — WAL o anda bossa. Yani hatasi
GORULMEYEN turden: yedek aldigini sanirsin, icerigi eksiktir ve bunu
ancak geri yuklerken ogrenirsin.

`VACUUM INTO` ise okuyucu bir islem icinde calisir ve TUTARLI bir
anlik goruntu yazar: WAL icerigi dahil, butun sayfalar sikistirilmis.
Olculdu: 128,5 MB -> 120,8 MB, 1,07 saniye.

DOGRULANMAYAN YEDEK YEDEK DEGILDIR
----------------------------------
Yazilan dosya aciliyor, `quick_check` kosuyor ve KAYNAKLA SATIR SAYISI
karsilastiriliyor. Sessizce bozuk bir yedek, hic yedek olmamasindan
kotudur: birincisinde yanlis bir guven duyarsin.

NEREYE YAZILIYOR
----------------
Varsayilan `data/yedek` — AYNI DISK. Kazaya, yanlis silmeye ve
bozulmaya karsi korur; DISK ARIZASINA KARSI KORUMAZ. Gercek felaket
kurtarma icin `yedek.dizin` bir bulut klasorunu gostermeli
(iCloud Drive, Dropbox). Bu VARSAYILAN OLARAK YAPILMADI: portfoy
verisi makineden cikar ve bu, kullanicinin bilincli karari olmali.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Dosya adi: `finagent-YYYY-AA-GG.db`. Tarih adin ICINDE cunku budama
# ve "bugun alindi mi" kontrolu dosya sisteminin mtime'ina degil
# ADA bakiyor — mtime kopyalama/senkron ile degisir, ad degismez.
ONEK = "finagent-"
SONEK = ".db"

# Satir sayisi karsilastirilacak tablolar. Hepsi degil, cunku amac
# butunluk kanitiyken tam esitlik ARANMAZ: yedek alinirken bot yazmaya
# devam ediyor olabilir ve `prices` gibi tablolar buyuyebilir. Bu
# tablolar "yedek gercekten dolu mu" sorusunu ucuza cevapliyor.
KONTROL_TABLOLARI = ("predictions", "positions", "prices", "instruments")


def _bugun() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _hedef(dizin: Path, gun: str) -> Path:
    return dizin / f"{ONEK}{gun}{SONEK}"


def _satir_sayilari(yol: Path) -> dict:
    """Tablo -> satir sayisi. Acilamayan/bozuk dosyada patlar."""
    c = sqlite3.connect(f"file:{yol}?mode=ro", uri=True)
    try:
        out = {}
        for t in KONTROL_TABLOLARI:
            try:
                out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                out[t] = None      # tablo yoksa None; kontrol bunu yakalar
        return out
    finally:
        c.close()


def dogrula(yedek: Path, kaynak_sayilar: dict | None = None) -> dict:
    """
    Yedegi ACARAK dogrular. Doner: {"tamam": bool, "sebep": str|None, ...}

    Iki asama:
      1. `quick_check` — sayfa duzeyinde bozulma. `integrity_check`
         degil: 120 MB'da tam kontrol pahali ve buradaki soru
         "dosya saglam mi", "her indeks tutarli mi" degil.
      2. SATIR SAYISI. `quick_check` bos bir veritabani icin de "ok"
         der; 0 satirli bir yedek teknik olarak saglam ama ISE YARAMAZ.
    """
    if not yedek.exists() or yedek.stat().st_size == 0:
        return {"tamam": False, "sebep": "dosya yok ya da bos"}
    try:
        c = sqlite3.connect(f"file:{yedek}?mode=ro", uri=True)
        try:
            sonuc = c.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error as e:
        return {"tamam": False, "sebep": f"acilamadi: {e}"}
    if str(sonuc).lower() != "ok":
        return {"tamam": False, "sebep": f"quick_check: {sonuc}"}

    try:
        sayilar = _satir_sayilari(yedek)
    except sqlite3.Error as e:
        return {"tamam": False, "sebep": f"tablolar okunamadi: {e}"}
    eksik = [t for t, n in sayilar.items() if n is None]
    if eksik:
        return {"tamam": False, "sebep": f"tablo yok: {', '.join(eksik)}",
                "sayilar": sayilar}
    if not any(sayilar.values()):
        return {"tamam": False, "sebep": "yedek BOS (tum tablolar 0 satir)",
                "sayilar": sayilar}

    # KAYNAKTAN AZ OLAMAZ. Fazla olabilir (yedek alindiktan sonra
    # kaynaga yazilmissa degil ama; ters yonde tolerans yok).
    if kaynak_sayilar:
        kayip = {t: (kaynak_sayilar.get(t), sayilar.get(t))
                 for t in sayilar
                 if kaynak_sayilar.get(t) is not None
                 and sayilar.get(t) is not None
                 and sayilar[t] < kaynak_sayilar[t]}
        if kayip:
            return {"tamam": False, "sayilar": sayilar,
                    "sebep": "kaynaktan AZ satir: " + ", ".join(
                        f"{t} {k}->{y}" for t, (k, y) in kayip.items())}
    return {"tamam": True, "sebep": None, "sayilar": sayilar}


def _bos_alan_gb(dizin: Path) -> float:
    try:
        return shutil.disk_usage(dizin).free / 1e9
    except OSError:
        return float("inf")        # olculemiyorsa engelleme


def budama(dizin: Path, gun: int) -> list[str]:
    """
    `gun` gunden eski yedekleri siler. Doner: silinenlerin adlari.

    SON YEDEK ASLA SILINMEZ. Makine bir hafta kapali kalirsa hepsi
    "eski" olur ve budama, elde tek yedek birakmayan bir temizlige
    donusurdu — yani koruma mekanizmasi korudugu seyi silerdi.
    """
    hepsi = sorted(dizin.glob(f"{ONEK}*{SONEK}"))
    if len(hepsi) <= 1:
        return []
    sinir = (datetime.now(timezone.utc) - timedelta(days=gun)).strftime("%Y-%m-%d")
    silinen = []
    for yol in hepsi[:-1]:                 # en yenisi her zaman kalir
        etiket = yol.stem[len(ONEK):]
        if etiket >= sinir:
            continue
        try:
            yol.unlink()
            silinen.append(yol.name)
        except OSError as e:
            log.warning("[yedek] %s silinemedi: %s", yol.name, e)
    return silinen


def yedek_al(settings, *, zorla: bool = False) -> dict:
    """
    Gunluk yedek. GUNDE BIR KEZ yeter, ama gunde dort kez DENENIR.

    Zamanlanmis kosularin her biri bunu cagiriyor: makine 17:45'te
    kapaliysa 08:00 ya da 12:30 kosusu yedegi almis olur. Bugunun
    yedegi zaten varsa ve dogrulaniyorsa is ATLANIR (ucuz kontrol).

    Doner: {"durum": "ok"|"atlandi"|"hata", ...} — cagiran taraf
    bunu kullaniciya bildirebilsin diye SOZLUK, istisna degil.
    Yedekleme basarisizligi kosuyu DUSURMEMELI ama SESSIZ de kalmamali.
    """
    ayar = settings.yedek_ayari()
    if not ayar["enabled"]:
        return {"durum": "atlandi", "sebep": "yedek.enabled: false"}

    # DIZIN `settings`ten: `YEDEK_DIZIN` ile tasinabilir olmasi ZORUNLU
    # (gerekce `Settings.yedek_dizini` docstring'inde).
    dizin = Path(settings.yedek_dizini)
    try:
        dizin.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"durum": "hata", "sebep": f"dizin acilamadi ({dizin}): {e}"}

    kaynak = Path(settings.db_path)
    if not kaynak.exists():
        return {"durum": "hata", "sebep": f"veritabani yok: {kaynak}"}

    gun = _bugun()
    hedef = _hedef(dizin, gun)

    # BUGUN ZATEN ALINDI MI? Var olan dosya DOGRULANARAK atlaniyor:
    # bozuk bir dosyanin varligi, yedek var sanmamiza yol acmamali.
    if hedef.exists() and not zorla:
        kontrol = dogrula(hedef)
        if kontrol["tamam"]:
            return {"durum": "atlandi", "sebep": "bugunun yedegi zaten var",
                    "dosya": hedef.name,
                    "boyut_mb": round(hedef.stat().st_size / 1e6, 1),
                    "budanan": budama(dizin, int(ayar["gun"]))}
        log.warning("[yedek] bugunun yedegi BOZUK (%s) — yeniden aliniyor",
                    kontrol["sebep"])

    # DISK KONTROLU YEDEKTEN ONCE. Dolu diske yazmaya calismak yarim
    # bir dosya birakir ve yarim yedek, yedek sanilir.
    gerekli_gb = kaynak.stat().st_size / 1e9
    bos = _bos_alan_gb(dizin)
    asgari = float(ayar["asgari_bos_gb"])
    if bos < gerekli_gb + asgari:
        return {"durum": "hata",
                "sebep": (f"disk yetersiz: {bos:.1f} GB bos, "
                          f"yedek {gerekli_gb:.1f} GB + {asgari:.1f} GB pay "
                          "gerekiyor")}

    # GECICI ADA YAZ, DOGRULA, SONRA TASI.
    #
    # `VACUUM INTO` var olan dosyaya yazmayi reddediyor; ustelik dogrudan
    # hedefe yazmak, yarim kalan bir yedegin GECERLI ad tasimasi demekti
    # ve bir sonraki kosu "bugunun yedegi var" deyip atlardi.
    gecici = dizin / f".{ONEK}{gun}.yaziliyor"
    gecici.unlink(missing_ok=True)
    try:
        kaynak_sayilar = _satir_sayilari(kaynak)
    except sqlite3.Error as e:
        return {"durum": "hata", "sebep": f"kaynak okunamadi: {e}"}

    import time
    t0 = time.monotonic()
    try:
        c = sqlite3.connect(f"file:{kaynak}?mode=ro", uri=True)
        try:
            # PARAMETRE OLARAK: yol tirnak kacisi gerektirmesin.
            c.execute("VACUUM INTO ?", (str(gecici),))
        finally:
            c.close()
    except sqlite3.Error as e:
        gecici.unlink(missing_ok=True)
        return {"durum": "hata", "sebep": f"VACUUM INTO basarisiz: {e}"}
    sure = time.monotonic() - t0

    kontrol = dogrula(gecici, kaynak_sayilar)
    if not kontrol["tamam"]:
        gecici.unlink(missing_ok=True)
        return {"durum": "hata",
                "sebep": f"yedek dogrulanamadi: {kontrol['sebep']}"}

    try:
        os.replace(gecici, hedef)          # atomik
    except OSError as e:
        gecici.unlink(missing_ok=True)
        return {"durum": "hata", "sebep": f"tasinamadi: {e}"}

    boyut_mb = round(hedef.stat().st_size / 1e6, 1)
    budanan = budama(dizin, int(ayar["gun"]))
    log.info("[yedek] %s · %.1f MB · %.1f sn · budanan %d",
             hedef.name, boyut_mb, sure, len(budanan))
    return {"durum": "ok", "dosya": hedef.name, "dizin": str(dizin),
            "boyut_mb": boyut_mb, "sure_sn": round(sure, 2),
            "sayilar": kontrol["sayilar"], "budanan": budanan,
            "bos_gb": round(bos, 1)}


def durum(settings) -> dict:
    """Eldeki yedeklerin ozeti — `run.py status` ve bekci icin."""
    ayar = settings.yedek_ayari()
    # DIZIN `settings`ten: `YEDEK_DIZIN` ile tasinabilir olmasi ZORUNLU
    # (gerekce `Settings.yedek_dizini` docstring'inde).
    dizin = Path(settings.yedek_dizini)
    if not dizin.exists():
        return {"adet": 0, "dizin": str(dizin), "en_yeni": None}
    hepsi = sorted(dizin.glob(f"{ONEK}*{SONEK}"))
    return {
        "adet": len(hepsi), "dizin": str(dizin),
        "en_yeni": hepsi[-1].stem[len(ONEK):] if hepsi else None,
        "toplam_mb": round(sum(y.stat().st_size for y in hepsi) / 1e6, 1),
        "bugun_var": _hedef(dizin, _bugun()).exists(),
    }
