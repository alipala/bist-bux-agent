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


# HAFIZA ARSIVI — VERITABANINDAN AYRI VE ONDAN DAHA KIRILGAN.
#
# `~/.claude/projects/<proje>/memory` altindaki `.md` dosyalari: neyin
# neden yapildigi, hangi tuzaga kac kez dusuldugu, olculen sayilar.
# 2026-08-29'da olculdu: 58 dosya, 6.201 satir, 432 KB — ve
#
#     git             : YOK (repo disinda, kendi deposu da degil)
#     Time Machine    : "No destinations configured"
#     iCloud          : kapsam disi
#     yedek betigi    : dokunmuyor
#
# yani TEK KOPYA, TEK DISKTE. Veritabani kaybolursa yeniden cekilir;
# "bu tuzaga bes kez dustuk" bilgisi YENIDEN URETILEMEZ.
#
# TARIHLI ARSIV, duz kopya DEGIL: hafizanin git'i yok, yani surum
# gecmisi de yok. Gunluk `.tar.gz` en azindan "dun ne yaziyordu"
# sorusunu cevaplanabilir kiliyor ve 432 KB'lik bir dizinde maliyeti
# yok denecek kadar az.
HAFIZA_ONEK = "hafiza-"
HAFIZA_SONEK = ".tar.gz"


def _bugun() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _hedef(dizin: Path, gun: str) -> Path:
    return dizin / f"{ONEK}{gun}{SONEK}"


def hafiza_yedekle(settings, dizin: Path | None = None) -> dict:
    """
    Hafiza dosyalarini arsive kopyalar — VERITABANI YEDEGINDEN AYRI.

    Neden ayri: `.md` dosyalari veritabaninda DEGIL ve hicbir yedek
    mekanizmasi onlara dokunmuyordu (gerekce `HAFIZA_ONEK` basliginda).

    ARSIV DUSERSE VERITABANI YEDEGI DUSMEZ. Cagiran taraf sonucu ayri
    bir alanda tasiyor; hafiza kopyalanamadi diye gunun veritabani
    yedegini kaybetmek, kucuk bir riski buyuk bir riskle degistirmek
    olurdu.

    YAZILDIKTAN SONRA ACILARAK DOGRULANIYOR. Yazma basarili donebilir
    ama arsiv bozuk olabilir; "yedegim var" sanip kurtarma aninda
    ogrenmek bu deponun tekrar eden kusur sinifi
    (`[[veri-dayanikliligi]]`: kopmus senkron tespit edilmiyordu).
    """
    import tarfile

    ham = settings.get("yedek.hafiza_dizini")
    if not ham:
        # AYAR YOKSA SESSIZ GECILMEZ. "Kapali" ile "unutulmus" ayri
        # seyler; cagiran taraf hangisi oldugunu bilmeli.
        return {"durum": "atlandi", "sebep": "yedek.hafiza_dizini tanimsiz"}
    kaynak = Path(str(ham)).expanduser()
    if not kaynak.is_dir():
        return {"durum": "hata", "sebep": f"hafiza dizini yok: {kaynak}"}

    dosyalar = sorted(kaynak.glob("*.md"))
    if not dosyalar:
        return {"durum": "hata", "sebep": f"hafiza dizini bos: {kaynak}"}

    hedef_dizin = Path(dizin or settings.yedek_dizini)
    try:
        hedef_dizin.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"durum": "hata", "sebep": f"dizin acilamadi: {e}"}

    hedef = hedef_dizin / f"{HAFIZA_ONEK}{_bugun()}{HAFIZA_SONEK}"
    # GECICI ADA YAZ, SONRA TASI. Yarim yazilmis bir arsiv, dogru adla
    # dururken "bugunun yedegi var" diye okunurdu — veritabani
    # tarafinda ayni ders `.yaziliyor` uzantisiyla ogrenilmisti.
    gecici = hedef.with_suffix(hedef.suffix + ".yaziliyor")
    try:
        with tarfile.open(gecici, "w:gz") as t:
            for d in dosyalar:
                t.add(d, arcname=d.name)
    except (OSError, tarfile.TarError) as e:
        gecici.unlink(missing_ok=True)
        return {"durum": "hata", "sebep": f"arsiv yazilamadi: {e}"}

    # DOGRULA: acilir mi, ve BEKLENEN sayida dosya var mi.
    try:
        with tarfile.open(gecici, "r:gz") as t:
            icerik = [m.name for m in t.getmembers() if m.isfile()]
    except (OSError, tarfile.TarError) as e:
        gecici.unlink(missing_ok=True)
        return {"durum": "hata", "sebep": f"arsiv dogrulanamadi: {e}"}
    if len(icerik) != len(dosyalar):
        gecici.unlink(missing_ok=True)
        return {"durum": "hata",
                "sebep": f"arsiv eksik: {len(icerik)}/{len(dosyalar)} dosya"}

    gecici.replace(hedef)
    return {"durum": "ok", "dosya": hedef.name, "dizin": str(hedef_dizin),
            "adet": len(dosyalar),
            "boyut_kb": round(hedef.stat().st_size / 1024, 1),
            "kaynak": str(kaynak)}


def _hafiza_adimi(settings, dizin, ayar) -> dict:
    """
    Hafiza yedegi + budama, TEK cagri. Istisna DISARI CIKMAZ.

    Veritabani yedegi hafizanin arizasi yuzunden DUSMEMELI: hafiza
    kaybi kotudur ama veritabani kaybi daha kotudur ve ikisini ayni
    kaderi paylasmaya zorlamak, kucuk riski buyuk riskle degistirmek
    olurdu. Hata SESSIZ degil: sonuc sozlukte doner ve loglanir.
    """
    try:
        sonuc = hafiza_yedekle(settings, dizin)
    except Exception as e:                            # noqa: BLE001
        log.exception("[yedek] hafiza arsivi patladi: %s", e)
        return {"durum": "hata", "sebep": f"{type(e).__name__}: {e}"}
    if sonuc.get("durum") == "hata":
        log.warning("[yedek] hafiza arsivi alinamadi: %s", sonuc.get("sebep"))
    elif sonuc.get("durum") == "ok":
        try:
            sonuc["budanan"] = hafiza_buda(Path(dizin), int(ayar.get("gun", 0)))
        except Exception as e:                        # noqa: BLE001
            log.warning("[yedek] hafiza budamasi basarisiz: %s", e)
    return sonuc


def hafiza_buda(dizin: Path, gun: int) -> list[str]:
    """Hafiza arsivlerini `gun` gunden eskiyse siler — `budama` ile ayni."""
    if gun <= 0:
        return []
    sinir = (datetime.now(timezone.utc) - timedelta(days=gun)).strftime("%Y-%m-%d")
    silinen = []
    for yol in sorted(Path(dizin).glob(f"{HAFIZA_ONEK}*{HAFIZA_SONEK}")):
        etiket = yol.name[len(HAFIZA_ONEK):-len(HAFIZA_SONEK)]
        if etiket < sinir:
            try:
                yol.unlink()
                silinen.append(yol.name)
            except OSError as e:                      # noqa: PERF203
                log.warning("[yedek] hafiza arsivi silinemedi (%s): %s",
                            yol.name, e)
    return silinen


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


def _ayna_buda(dizin: Path, adet: int) -> list[str]:
    """
    Aynada YALNIZCA en yeni `adet` dosya kalir. Doner: silinenler.

    BUDAMA GUN DEGIL ADET ile — `budama()` ile bilincli olarak FARKLI.
    Arsivin sorusu "ne kadar geriye gidebilirim" (yas), aynanin sorusu
    "elimde kac tane aninda acilabilir kopya var" (sayi). `budama(.., 1)`
    kullanilsaydi sinir "dunden yeni" olurdu ve aslinda IKI dosya
    birakirdi — istenen "en son bir tane" degil.
    """
    hepsi = sorted(dizin.glob(f"{ONEK}*{SONEK}"))
    if adet <= 0:
        fazla = hepsi
    elif len(hepsi) <= adet:
        return []
    else:
        fazla = hepsi[:-adet]
    silinen = []
    for yol in fazla:
        try:
            yol.unlink()
            silinen.append(yol.name)
        except OSError as e:
            log.warning("[yedek] ayna %s silinemedi: %s", yol.name, e)
    return silinen


def ayna_guncelle(settings, kaynak_yedek: Path) -> dict:
    """
    Dogrulanmis bir arsiv yedegini YEREL AYNAYA kopyalar.

    NEDEN AYNA VAR
    --------------
    Arsiv artik iCloud'da ve "Mac Depolamayi Optimize Et" acik. Atilmis
    (dataless) bir yedegi acmak onu ONCE INDIRIR; internetsiz bir anda
    ya da 135 MB'lik bir indirme beklerken geri yukleme yapamazsin.
    Ayna, en olasi kurtarma senaryosunu (bugune/dune donmek) AGDAN
    BAGIMSIZ tutuyor.

    `cp` BURADA NEDEN MESRU — DOSYANIN BASINDAKI UYARIYA RAGMEN
    -----------------------------------------------------------
    Bu modulun basi "cp kullanma, WAL'i kaybeder" diyor ve o uyari
    CANLI veritabani icin gecerli. BURADAKI kaynak canli veritabani
    DEGIL: `VACUUM INTO` ciktisi — yani zaten tutarli, checkpoint'lenmis,
    WAL yan dosyasi OLMAYAN ve kimsenin yazmadigi bir anlik goruntu.
    Onu kopyalamak yalnizca bayt kopyalamak.

    Yine de KOPYA DA DOGRULANIYOR: yarim kopyalanmis bir ayna, aynasi
    olmamasindan kotudur (ayni gerekce, ayni disiplin).
    """
    try:
        ayar = settings.yedek_ayari()["yerel_ayna"]
        dizin = settings.yedek_ayna_dizini
    except Exception as e:                     # noqa: BLE001
        return {"durum": "hata", "sebep": f"ayna ayari okunamadi: {e}"}
    if dizin is None:
        # Izole kosu (YEDEK_DIZIN verilmis) ya da ayna tanimsiz.
        return {"durum": "atlandi", "sebep": "ayna tanimli degil"}

    adet = int(ayar["adet"])
    try:
        dizin.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"durum": "hata", "sebep": f"ayna dizini acilamadi: {e}"}

    if adet <= 0:                              # ayna bilincli olarak KAPALI
        return {"durum": "atlandi", "sebep": "yerel_ayna.adet: 0",
                "budanan": _ayna_buda(dizin, 0)}

    hedef = dizin / kaynak_yedek.name
    # ZATEN VAR MI — ve DOGRU MU. Sadece varliga bakmak, bozuk bir
    # aynayi "var" sayip her gun ayni bozuk dosyayla yasamak demekti.
    #
    # SAGLAM YETMIYOR, GUNCEL DE OLMALI — OLCULDU 2026-08-27.
    # `dogrula(hedef)` KAYNAK SAYILARI OLMADAN cagriliyordu, yani
    # yalnizca `quick_check` kosuyordu: yapisal olarak saglam ama
    # ESKI bir ayna bu kapidan geciyor ve "ayna guncel" deniyordu.
    # Sahada olculdu, ayni gun ikinci kez yedek alindiginda:
    #     arsiv : prices 2.182.843 · predictions 983
    #     ayna  : prices   988.570 · predictions 888   <- "guncel"
    # Ayna tam da AGDAN BAGIMSIZ GERI YUKLEME icin var; bayat oldugunu
    # ancak geri yuklerken ogrenmek, bu deponun en kotu hata sinifi.
    # `dogrula`nin "kaynaktan AZ satir" kolu ZATEN VARDI — eksik olan
    # tek sey buraya baglanmasiydi.
    try:
        arsiv_sayilar = _satir_sayilari(kaynak_yedek)
    except sqlite3.Error:
        arsiv_sayilar = None
    if hedef.exists() and dogrula(hedef, arsiv_sayilar)["tamam"]:
        return {"durum": "atlandi", "sebep": "ayna guncel",
                "dosya": hedef.name, "dizin": str(dizin),
                "budanan": _ayna_buda(dizin, adet)}

    # DISK: ayna arsivle AYNI FIZIKSEL DISKTE olabilir (iCloud klasoru
    # de oyle). Kopya icin yer yoksa yarim dosya birakmaktansa hic
    # baslamamak gerekir.
    try:
        gerekli_gb = kaynak_yedek.stat().st_size / 1e9
    except OSError as e:
        return {"durum": "hata", "sebep": f"kaynak yedek okunamadi: {e}"}
    bos = _bos_alan_gb(dizin)
    if bos < gerekli_gb:
        return {"durum": "hata",
                "sebep": f"ayna icin disk yetersiz: {bos:.1f} GB bos, "
                         f"{gerekli_gb:.1f} GB gerekiyor"}

    # GECICI ADA KOPYALA, DOGRULA, SONRA TASI — arsivle ayni sozlesme.
    # Dogrudan hedefe kopyalamak, yarim kalan bir kopyanin GECERLI ad
    # tasimasi demekti ve bir sonraki kosu "ayna guncel" deyip atlardi.
    gecici = dizin / f".{kaynak_yedek.stem}.kopyalaniyor"
    gecici.unlink(missing_ok=True)
    try:
        shutil.copyfile(kaynak_yedek, gecici)
    except OSError as e:
        gecici.unlink(missing_ok=True)
        return {"durum": "hata", "sebep": f"ayna kopyalanamadi: {e}"}

    kontrol = dogrula(gecici)
    if not kontrol["tamam"]:
        gecici.unlink(missing_ok=True)
        return {"durum": "hata",
                "sebep": f"ayna dogrulanamadi: {kontrol['sebep']}"}
    try:
        os.replace(gecici, hedef)              # atomik
    except OSError as e:
        gecici.unlink(missing_ok=True)
        return {"durum": "hata", "sebep": f"ayna tasinamadi: {e}"}

    budanan = _ayna_buda(dizin, adet)
    log.info("[yedek] ayna %s · %.1f MB · budanan %d", hedef.name,
             hedef.stat().st_size / 1e6, len(budanan))
    return {"durum": "ok", "dosya": hedef.name, "dizin": str(dizin),
            "boyut_mb": round(hedef.stat().st_size / 1e6, 1),
            "budanan": budanan}


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
            # AYNA BU YOLDA DA GUNCELLENIYOR. Atlanirsa su ariza kalici
            # olurdu: arsiv yazildi ama ayna kopyasi o gun basarisiz
            # oldu (disk, izin); ertesi cagri "bugunun yedegi zaten var"
            # deyip cikardi ve ayna BIR DAHA hic denenmezdi.
            # HAFIZA BURADA DA YEDEKLENIYOR. Veritabani gunde bir kez
            # degisiyor, hafiza her oturumda; "bugunun db yedegi var"
            # diye cikmak, hafizayi gunde EN FAZLA bir kez ve o da
            # sansa birakirdi.
            return {"durum": "atlandi", "sebep": "bugunun yedegi zaten var",
                    "dosya": hedef.name,
                    "boyut_mb": round(hedef.stat().st_size / 1e6, 1),
                    "budanan": budama(dizin, int(ayar["gun"])),
                    "ayna": ayna_guncelle(settings, hedef),
                    "hafiza": _hafiza_adimi(settings, dizin, ayar)}
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
    # AYNA ARSIVDEN SONRA — ve arsivi DUSURMEZ. Ayna bir kolaylik
    # (agdan bagimsiz geri yukleme); arsiv ise diskin olumune karsi
    # tek koruma. Ayna kopyalanamadi diye arsiv yedegini "basarisiz"
    # ilan etmek, elimizdeki gercek korumayi da atmak olurdu. Ama
    # SESSIZ de kalmiyor: sonuc sozlukte ve `run.py` onu basiyor.
    ayna = ayna_guncelle(settings, hedef)
    # HAFIZA AYRI ALANDA — ayna ile ayni gerekce. `.md` dosyalari
    # veritabaninda DEGIL ve kaybolduklarinda yeniden URETILEMEZLER.
    hafiza = _hafiza_adimi(settings, dizin, ayar)
    log.info("[yedek] %s · %.1f MB · %.1f sn · budanan %d · ayna %s · "
             "hafiza %s", hedef.name, boyut_mb, sure, len(budanan),
             ayna["durum"], hafiza["durum"])
    return {"durum": "ok", "dosya": hedef.name, "dizin": str(dizin),
            "boyut_mb": boyut_mb, "sure_sn": round(sure, 2),
            "sayilar": kontrol["sayilar"], "budanan": budanan,
            "bos_gb": round(bos, 1), "ayna": ayna, "hafiza": hafiza}


def _ayna_durumu(settings) -> dict:
    """
    Yerel aynanin ozeti. ARSIVDEN AYRI RAPORLANIR: ikisi tek sayiya
    indirgenirse "yedek var" beyani, hangisinin var oldugunu gizler —
    ve bu projede yanlis "var/yok" beyani en pahali hata sinifi.
    """
    try:
        dizin = settings.yedek_ayna_dizini
    except Exception:                          # noqa: BLE001
        return {"acik": False, "sebep": "ayar okunamadi"}
    if dizin is None:
        return {"acik": False, "sebep": "tanimli degil"}
    dizin = Path(dizin)
    if not dizin.exists():
        return {"acik": True, "adet": 0, "dizin": str(dizin), "en_yeni": None}
    hepsi = sorted(dizin.glob(f"{ONEK}*{SONEK}"))
    return {
        "acik": True, "adet": len(hepsi), "dizin": str(dizin),
        "en_yeni": hepsi[-1].stem[len(ONEK):] if hepsi else None,
        "toplam_mb": round(sum(y.stat().st_size for y in hepsi) / 1e6, 1),
    }


def durum(settings) -> dict:
    """Eldeki yedeklerin ozeti — `run.py status` ve bekci icin."""
    ayar = settings.yedek_ayari()
    # DIZIN `settings`ten: `YEDEK_DIZIN` ile tasinabilir olmasi ZORUNLU
    # (gerekce `Settings.yedek_dizini` docstring'inde).
    dizin = Path(settings.yedek_dizini)
    # BEKCININ YARGILADIGI SEY ARSIV — ayna DEGIL. Sebep: ayna her
    # zaman yerel diskte ve neredeyse hep yazilir; bekcinin yakalamasi
    # gereken ariza ise tam tersi yonde — "dizin bir bulut klasorune
    # tasindi ve senkron koptu" (bkz. `watchdog.yedek_bayat`). Ayna
    # bayatligi ARSIVI maskelerse o olcut korudugu seyi kaybeder.
    if not dizin.exists():
        return {"adet": 0, "dizin": str(dizin), "en_yeni": None,
                "ayna": _ayna_durumu(settings)}
    hepsi = sorted(dizin.glob(f"{ONEK}*{SONEK}"))
    return {
        "adet": len(hepsi), "dizin": str(dizin),
        "en_yeni": hepsi[-1].stem[len(ONEK):] if hepsi else None,
        "toplam_mb": round(sum(y.stat().st_size for y in hepsi) / 1e6, 1),
        "bugun_var": _hedef(dizin, _bugun()).exists(),
        "ayna": _ayna_durumu(settings),
    }
