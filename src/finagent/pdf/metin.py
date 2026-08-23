"""
PDF METNI — banka/arastirma notlarini okunabilir metne cevirir.

NE YAPAR, NE YAPMAZ
-------------------
Bu modul METNI GETIRIR. Yorumu SOHBET AJANI yapiyor — `video/transkript`
ile ayni sozlesme. Araya ikinci bir "ozetleyici model" koymak, ajanin
orijinali degil bir SIKISTIRMAYI okumasi demekti.

KULLLANIM ALANI, VE NEDEN BU FARK EDIYOR
----------------------------------------
Beklenen belgeler: banka/aracı kurum arastirma notlari, bagimsiz
arastirma yazarlari (Citrini vb.), sektor raporlari. Ingilizce.
Bu, bir KAP bildirimi ya da bilanço DEGILDIR ve fark esastir:

    Bir arastirma notundaki "hedef fiyat 250$" ifadesi, SIRKET
    hakkinda bir olgu degil; ANALISTIN KANAATI hakkinda bir olgudur.

Ciktinin tamami bu ayrimi tasimak icin kurulu. Ayni disiplinin video
tarafindaki hali: bir YouTube videosu kademe 4'tur ve kanit degildir.

TARIH KRITIK — VE BEDAVA GELIYOR
--------------------------------
Bir arastirma notunun hedef fiyati BOZULUR. Aralik 2024 tarihli bir
notun hedefi bugun 20 aylik; o sayiyi guncelmis gibi sunmak, olmayan
bir olgu beyan etmektir. PDF metadata'si yazari ve tarihi 8 ms'de
veriyor (olculdu: 96 sayfalik BIS raporunda `/Title`, `/Author`,
`/CreationDate` doluydu), yani bu bilgi icin metni okumaya bile gerek
yok. `yas_gun` hesaplanip cikti ile birlikte tasiniyor.

NEDEN pypdf — OLCULDU, TERCIH EDILMEDI
--------------------------------------
Uc aday gercek finansal PDF'lerde karsilastirildi (2026-08-24):

    kutuphane    hiz (3 sayfa)   cikan olgular
    pypdf          287 ms        Expense 3 · Holdings 3 · % 28 · Inception 1
    pdfplumber     913 ms        Expense 3 · Holdings 3 · % 28 · Inception 1
    pymupdf         70 ms        Expense 3 · Holdings 3 · % 28 · Inception 1

CIKARIM KALITESI AYNI. Hiz farki bu olcekte onemsiz (bir Telegram
mesaji icin 287 ms ile 70 ms ayni sey). Geriye iki ayirt edici sey
kaliyor ve ikisi de pypdf'i secturuyor:
  * pypdf SAF PYTHON. Bu depo derlenmis tekerlek yoklugundan bir kez
    yandi (torch/onnxruntime, Py3.14/x86_64) — saf Python bagimliligin
    o ariza sinifi HIC YOKTUR.
  * Lisans: pypdf BSD-3, PyMuPDF ise AGPL/ticari. Depo su an private,
    yani AGPL hukuken sorun degil — ama gereksiz bir kisit.

Turkce karakterler ikisinde de SORUNSUZ (olculdu: 'İŞ GAYRİMENKUL
YATIRIM ORTAKLIĞI A.Ş.' tam korunuyor). Beklenen kulliyat Ingilizce
ama BIST notlari Turkce gelebilir; bu yol acik.

"BAKAMADIM" ILE "YOK" AYRI SEYLERDIR
------------------------------------
Bu modulun en kritik davranisi. Olculdu:

    taranmis PDF -> sayfa=2, cikan karakter=0, HATA YOK

Yani goruntu tabanli (taranmis) bir PDF sessizce BOS metin donduruyor.
Bunu "belgede bu konu gecmiyor" diye okumak, bu projenin en kotu hata
sinifidir. Cikti bu yuzden `metin_katmani_yok` bayragini AYRI tasiyor
ve arac katmani ajana "icerigi hakkinda HICBIR SEY soyleme" diyor.

Hata siniflari (olculdu, pypdf):
    File has not been decrypted     -> sifreli, parola gerekli
    Stream has ended unexpectedly   -> dosya bozuk/yarim
    %PDF ile baslamiyor             -> PDF degil (cogu zaman HTML hata
                                       sayfasi; olculdu: uc kaynaktan
                                       ikisi 200 dondurup HTML verdi)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# En fazla kac karakter modele gider.
#
# 120.000 OLCUMLE SECILDI. Video tarafinda 40.000 yeterliydi (20
# dakikalik video ~18.000 karakter). Arastirma notu BASKA BIR HAYVAN:
# 96 sayfalik BIS raporu 252.679 karakter, yani 40.000 sinir raporun
# %21'inde kesiyordu. Tipik bir sell-side notu 30-60 sayfa ~ 80-160 bin
# karakter; 120.000 bunun buyuk cogunlugunu TAM geciriyor.
#
# UST SINIR MASRAFLI DEGIL cunku okuma TEMBEL: sinir dolunca kalan
# sayfalar HIC acilmiyor (olculdu: tam cikarim 4,5 sn, 120k'da durunca
# ~2,2 sn, 40k'da ~0,9 sn).
AZAMI_KARAKTER = 120_000

# Patolojik dosyalara karsi arka duvar. Karakter siniri zaten once
# doluyor; bu, "5.000 sayfalik ama her sayfasi bos" gibi bir dosyanin
# CPU yemesini engelliyor.
AZAMI_SAYFA = 400

# Indirilecek en fazla bayt. Telegram bot API'si zaten 20 MB'ta kesiyor;
# bu sinir URL'den indirme icin. 30 MB, gordugumuz en buyuk arastirma
# raporunun (1,8 MB) 16 kati.
AZAMI_BAYT = 30 * 1024 * 1024

# `\s` BUNLARI YAKALAMIYOR — OLCULDU.
#
#     NO-BREAK SPACE (U+00A0)        \s eslesir: True
#     THIN SPACE (U+2009)            \s eslesir: True
#     ZERO WIDTH SPACE (U+200B)      \s eslesir: FALSE
#     ZERO WIDTH NO-BREAK (U+FEFF)   \s eslesir: FALSE
#
# Yani duz `re.sub(r"\s+", " ", ...)` sifir-genislik karakterleri
# BIRAKIYOR ve kelimeler yapisik kaliyor: 'YATIRIM​ORTAKLIĞI'.
# Sonuc sessiz: metin dogru gorunur, arama/eslestirme tutmaz.
_SIFIR_GENISLIK = str.maketrans("", "", "​‌‍﻿­")

_PDF_URL = re.compile(r"https?://\S+", re.I)


class PdfHatasi(Exception):
    """
    PDF okunamadi. `sinif` NE OLDUGUNU, `bizim_sorunumuz` KIMIN sorunu
    oldugunu soyler — `TranskriptHatasi` ile ayni sozlesme.

    Ikisi ayri cunku kullaniciya soylenecek cumle farkli: "bu PDF
    sifreli" ile "su an indiremedim" ayni sey degil ve ikincisini
    birincisi gibi soylemek olmayan bir olgu beyan etmektir.
    """

    def __init__(self, mesaj: str, sinif: str, bizim_sorunumuz: bool = False):
        super().__init__(mesaj)
        self.sinif = sinif
        self.bizim_sorunumuz = bizim_sorunumuz


def url_coz(ham: str | None) -> str | None:
    """Metinden ilk http(s) baglantisini cikarir; yoksa None."""
    if not ham:
        return None
    m = _PDF_URL.search(str(ham))
    return m.group(0).rstrip(").,;'\"") if m else None


def _guvenli_url(url: str) -> str:
    """
    Indirilebilir mi? Degilse `PdfHatasi`.

    NEDEN KAPI VAR: bu araci AJAN cagiriyor ve cagri parametresi bir
    BELGEDEN gelebilir — okudugu bir PDF "su adresi getir" yazabilir.
    Enjeksiyon siniri bu projede ONAY mimarisinde, ama ic aga ya da
    bulut metadata ucuna (169.254.169.254) yapilan bir istek onaydan
    ONCE gerceklesirdi. Kapi dar ve ucuz: sema + ozel adres reddi.
    """
    from urllib.parse import urlparse
    import ipaddress
    import socket

    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise PdfHatasi(f"Yalnizca http/https destekleniyor: {p.scheme!r}",
                        sinif="SemaDesteklenmiyor")
    if not p.hostname:
        raise PdfHatasi("Adreste sunucu adi yok", sinif="AdresGecersiz")
    try:
        bilgi = socket.getaddrinfo(p.hostname, None)
    except OSError as e:
        raise PdfHatasi(f"Adres cozulemedi: {p.hostname}", sinif="DnsHatasi",
                        bizim_sorunumuz=True) from e
    for aile, _t, _p, _c, sockaddr in bilgi:
        ip = ipaddress.ip_address(sockaddr[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            raise PdfHatasi(
                f"Ic ag/ozel adres reddedildi: {ip}", sinif="OzelAdres")
    return url


def indir(url: str, hedef_dizin, *, azami_bayt: int = AZAMI_BAYT,
          zaman_asimi: float = 60.0) -> Path:
    """
    URL'den PDF indirir. Doner: yerel dosya yolu.

    ICERIK KONTROLU BAYTTAN, BASLIKTAN DEGIL. Olculdu (2026-08-24): uc
    kaynaktan ikisi HTTP 200 dondurup govdede HTML verdi
    (`b'<!DOCTYP'`). `Content-Type` basligina guvenmek, bir hata
    sayfasini "PDF" diye ayristirmaya calismak olurdu.
    """
    import httpx

    _guvenli_url(url)
    hedef_dizin = Path(hedef_dizin)
    hedef_dizin.mkdir(parents=True, exist_ok=True)
    ad = re.sub(r"[^A-Za-z0-9._-]", "_", Path(url.split("?")[0]).name or "belge")
    if not ad.lower().endswith(".pdf"):
        ad += ".pdf"
    hedef = hedef_dizin / ad[:80]

    try:
        with httpx.stream("GET", url, timeout=zaman_asimi,
                          follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (finagent)"}) as r:
            if r.status_code != 200:
                raise PdfHatasi(f"Sunucu {r.status_code} dondu",
                                sinif=f"Http{r.status_code}",
                                bizim_sorunumuz=r.status_code >= 500)
            toplam, ilk = 0, b""
            with open(hedef, "wb") as fh:
                for blok in r.iter_bytes():
                    if not ilk:
                        ilk = blok[:5]
                        if not ilk.startswith(b"%PDF"):
                            raise PdfHatasi(
                                "Adres PDF dondurmedi (govde PDF ile "
                                f"baslamiyor: {ilk[:8]!r}). Cogu zaman bu bir "
                                "giris/hata sayfasidir.",
                                sinif="PdfDegil")
                    toplam += len(blok)
                    if toplam > azami_bayt:
                        raise PdfHatasi(
                            f"Dosya {azami_bayt // 1024 // 1024} MB sinirini "
                            "asti; indirme durduruldu.", sinif="CokBuyuk")
                    fh.write(blok)
    except PdfHatasi:
        hedef.unlink(missing_ok=True)
        raise
    except Exception as e:                              # noqa: BLE001
        hedef.unlink(missing_ok=True)
        raise PdfHatasi(f"Indirilemedi: {type(e).__name__}: {str(e)[:150]}",
                        sinif="IndirmeHatasi", bizim_sorunumuz=True) from e
    return hedef


def _temizle(ham: str) -> str:
    """Sifir-genislik karakterleri at, bosluklari tekille."""
    return re.sub(r"\s+", " ", ham.translate(_SIFIR_GENISLIK)).strip()


def _tarih_coz(ham) -> str | None:
    """PDF metadata tarihini (`D:YYYYMMDD...`) ISO gune cevirir."""
    s = str(ham or "")
    m = re.search(r"(\d{4})(\d{2})(\d{2})", s)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)),
                        int(m.group(3))).date().isoformat()
    except ValueError:
        return None


def oku(yol, *, azami_karakter: int = AZAMI_KARAKTER,
        azami_sayfa: int = AZAMI_SAYFA, kaynak: str | None = None) -> dict:
    """
    PDF'i okur. Doner: metin + KAYNAK BEYANI.

    OKUMA TEMBEL: karakter siniri dolunca kalan sayfalar HIC acilmiyor.
    96 sayfalik raporda olculdu — tam cikarim 4,5 sn, sinirla ~2,2 sn.
    Arastirma notlarinda bu ayni zamanda DOGRU davranis: tez ve ozet
    BASTA, hukuki cekince metni SONDA. Yani sinir, gurultuyu dogal
    olarak disarida birakiyor.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    yol = Path(yol)
    if not yol.exists():
        raise PdfHatasi(f"Dosya yok: {yol.name}", sinif="DosyaYok")
    try:
        okuyucu = PdfReader(str(yol))
        sifreli = bool(okuyucu.is_encrypted)
        sayfalar = okuyucu.pages
        sayfa_sayisi = len(sayfalar)
    except PdfReadError as e:
        mesaj = str(e)
        if "decrypt" in mesaj.lower():
            raise PdfHatasi(
                "PDF sifreli — parola olmadan acilamiyor.",
                sinif="Sifreli") from e
        raise PdfHatasi(f"PDF bozuk ya da yarim: {mesaj[:150]}",
                        sinif="Bozuk") from e
    except Exception as e:                              # noqa: BLE001
        raise PdfHatasi(f"{type(e).__name__}: {str(e)[:150]}",
                        sinif=type(e).__name__, bizim_sorunumuz=True) from e

    ust = (okuyucu.metadata or {})
    parcalar: list[str] = []
    uzunluk = 0
    okunan = 0
    metinsiz = 0
    for sayfa in sayfalar[:azami_sayfa]:
        okunan += 1
        try:
            ham = sayfa.extract_text() or ""
        except Exception as e:                          # noqa: BLE001
            # TEK SAYFA PATLARSA BELGE DUSMEZ — ama sessiz de kalmaz.
            log.warning("[pdf] %s sayfa %d okunamadi: %s", yol.name, okunan, e)
            ham = ""
        s = _temizle(ham)
        if len(s) < 40:
            metinsiz += 1
        if s:
            parcalar.append(s)
            uzunluk += len(s) + 1
        if uzunluk >= azami_karakter:
            break

    metin = "\n".join(parcalar)
    kesildi = len(metin) > azami_karakter or okunan < sayfa_sayisi
    if len(metin) > azami_karakter:
        metin = metin[:azami_karakter]

    # METIN KATMANI YOK MU — EN KRITIK AYRIM.
    #
    # Taranmis (goruntu) PDF sessizce BOS metin donduruyor ve bunu
    # "belgede bu konu gecmiyor" diye okumak yasak.
    #
    # OLCUT SAYFA BASINA — mutlak esik degil. Ilk surum "toplam < 40
    # karakter" diyordu ve 30 karakterlik GERCEK bir belgeyi taranmis
    # sandi: kisa bir belgeyi "okunamadi" diye reddetmek, uzun bir
    # taramayi "bos" diye okumak kadar yanlis.
    #
    # 20 karakter/sayfa esigi olculdu: taranmis sayfa 0 veriyor, gercek
    # sayfalar binlerce (SPDR factsheet 4.452/sayfa, BIS raporu
    # ~2.600/sayfa). Iki kume arasinda iki mertebe bosluk var, yani
    # esigin tam degeri kritik degil.
    #
    # KISMI durum AYRICA raporlaniyor (`metinsiz_sayfa`): 10 sayfanin
    # 9'u taranmissa belge "okunabilir" sayilir ama ajan kacirdigi
    # sayfa sayisini GORUR.
    ortalama = len(metin.strip()) / max(1, okunan)
    katman_yok = sayfa_sayisi > 0 and (len(metin.strip()) < 20
                                       or ortalama < 20)

    tarih = _tarih_coz(ust.get("/CreationDate"))
    yas_gun = None
    if tarih:
        try:
            yas_gun = (datetime.now(timezone.utc).date()
                       - datetime.fromisoformat(tarih).date()).days
        except ValueError:
            yas_gun = None

    return {
        "dosya": yol.name,
        "kaynak": kaynak or "yuklenen dosya",
        "baslik": str(ust.get("/Title") or "").strip() or None,
        "yazar": str(ust.get("/Author") or "").strip() or None,
        "tarih": tarih,
        "yas_gun": yas_gun,
        "sayfa": sayfa_sayisi,
        "okunan_sayfa": okunan,
        "metinsiz_sayfa": metinsiz,
        "sifreli": sifreli,
        "karakter": len(metin),
        "kesildi": bool(kesildi),
        "metin_katmani_yok": katman_yok,
        "metin": metin,
    }
