"""
EKONOMIK TAKVIM — "yarin ne var".

DURUM DURUSTCE (2026-08-17'de tek tek OLCULDU, belgeye guvenilmedi):

    Fed FOMC     ✓ duz HTTP 200, 2026+2027 toplantilari temiz ayrisiyor
    TCMB         ✓ yillik yayin takvimi: PPK karari, toplanti ozeti,
                   Enflasyon Raporu, Finansal Istikrar Raporu
    TUIK         ✗ portalin KENDI frontend'i KENDI API'sinden 403 aliyor
                   (`/api/tr/press/latest`); sayfa "Eslesen kayit
                   bulunamadi" yaziyor. Bizim kazima sorunumuz DEGIL.
    BLS          ✗ curl 403; tarayicida da 539 karakterlik engel sayfasi
    ECB          ✗ index sayfasi takvimi HTML'de TASIMIYOR

2026-09-24 EKLENDI — BLS'NIN BOSLUGU FRED'DEN KAPANDI:

    FRED takvimi ✓ `releases/calendar?rid=..&y=..` duz HTTP 200; yil
                   basina CPI 12, istihdam 12, PCE 13, GDP 13 tarih.
                   Yalnizca YAKIN yillar (y=2016 istenince 2025 donuyor).
    ALFRED       ✓ `release/downloaddates?rid=..&ff=txt` — yayinin TUM
                   gecmis gunleri (CPI 1949'dan). Anahtar gerekmez.
                   REVIZYON gunlerini de icerebilir (2015'te CPI icin 14
                   tarih); bu yuzden kaynak adi AYRI ('alfred'), ileri
                   takvimle ('fred') karismaz.

BLS artik YOKLANMIYOR: aradigimiz sey (CPI/istihdam gunleri) BLS'nin
kendisi degildi, tarihlerdi. Engelli bir kaynagi sonsuza kadar
"engelli" diye raporlamak, rapor istemini (`strategist`) kapsam boslugu
yazmaya zorluyordu — bosluk artik yok.

TCMB ONCE "ENGELLI" SANILDI VE YANLISTI — ders bu dosyanin en degerli
parcasi. Iki olcum hatasi ust uste geldi: sayfada `dd.mm.yyyy` arandi
(TCMB "22 Ocak 2026" yaziyor) ve tarayicida `inner_text` tabloyu
getirmedi. Ikisi de "erisilemiyor" sonucuna goturdu, oysa tablo duz
HTTP ile HTML'de duruyordu. **Bir kaynagi "yok" ilan etmeden once ARANAN
SEYIN BICIMINI dogrula.** Ayni hata sinifi projede daha once de cikti:
ortaklik yapisi icin "veri JS'te, alinamiyor" denmis, sonra
`Chart.getChart()` ile okunabildigi gorulmustu.

CALISMAYAN KAYNAKLAR HER KOSUDA YENIDEN DENENIR ve sonuc
`takvim_kaynak` tablosuna yazilir — boylece TUIK'in acildigi gun
kendiliginden fark edilir. Bir "su an calismiyor" bilgisi bir yorum
satirinda degil VERIDE durmali; yoksa kimse bir daha bakmaz. Yoklamanin
olcusu HTTP kodu DEGIL, sayfada ayristirilabilir TARIH olmasi (bkz.
`_tarih_sayisi`): TCMB'nin menu sayfasi 200 ve 32 KB donuyor ama tek
tarih tasimiyor.

BOLUM BOS KALIRSA RAPORDA YAZAR. "Yarin onemli bir sey yok" ile "takvim
kaynagi kirik" ayni sey degildir; ikincisi soylenmezse birincisi sanilir.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

import httpx

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

FED_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
TCMB_URL = ("https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/"
            "Main+Menu/Duyurular/Takvim")

TR_AYLAR = {"ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4,
            "mayıs": 5, "mayis": 5, "haziran": 6, "temmuz": 7,
            "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
            "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12}

AYLAR = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
         "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
         "november": 11, "december": 12}

# Denenip ENGELLI cikan kaynaklar. Her kosuda yeniden yoklanir; amac
# "acildi mi" sorusunu insana sormadan cevaplamak.
ENGELLI_KAYNAKLAR = {
    "tuik": ("https://veriportali.tuik.gov.tr/api/tr/press/latest",
             "Turkiye - TUIK haber bulteni takvimi"),
}

# ABD MAKRO YAYINLARI — FRED yayin kimligi (rid) -> (olay adi, onem).
# Hepsi ABD Dogu saatiyle 08:30'da, SEANS ONCESI yayimlaniyor: FRED
# takvimi "7:30 am" yaziyor ve bu MERKEZ saati (St. Louis). Saat bu
# yuzden tabloda degil `MAKRO_ZAMAN`da; `takvim` tablosuna kolon eklemek
# var olan bir tabloyu goc ettirmek olurdu ve ALFRED gecmisi zaten saat
# TASIMIYOR.
FRED_YAYINLARI = {
    10: ("ABD TUFE (CPI)", "yuksek"),
    50: ("ABD istihdam raporu (NFP)", "yuksek"),
    54: ("ABD kisisel gelir/harcama (PCE)", "orta"),
    53: ("ABD GSYH (GDP)", "orta"),
    46: ("ABD UFE (PPI)", "orta"),
}
# FRED/ALFRED'E USER-AGENT AYARLANMAZ — OLCULDU 2026-09-24, ayni dakikada:
#   Chrome taklidi (modulun `UA`si)         -> ReadTimeout (30 sn)
#   'finagent/1.0' ve 'finagent/1.0 (...)'  -> ReadTimeout (20 sn)
#   httpx varsayilani 'python-httpx/0.28'   -> 200, 0,1-0,3 sn
#   curl varsayilani                        -> 200, 0,5 sn
# Sunucu tanimadigi UA'lari ASILI birakiyor (hata degil, sessiz bekleme).
# Ilk saha kosusu bu yuzden iki kaynagi da `engelli` yazdi; kaynak
# engelli DEGILDI. Kutuphane varsayilani hem calisan hem de durust olan
# secenek; baska bir metin "daha kibar" gorunse de OLCUMDE KALDI.
FRED_BASLIK: dict[str, str] = {}
FRED_TAKVIM_URL = "https://fred.stlouisfed.org/releases/calendar?rid={rid}&y={yil}"
ALFRED_URL = "https://alfred.stlouisfed.org/release/downloaddates?rid={rid}&ff=txt"
FRED_YAYIN_URL = "https://fred.stlouisfed.org/release?rid={rid}"
# ALFRED gecmisi 1949'a gidiyor; backtest penceresi 2016+. Gereksiz
# binlerce satir yazmamak icin alt sinir.
ALFRED_ALT_SINIR = "2010-01-01"

# Olay kaynagi -> tepki zamani (`analysis.olay_takvimi.zaman_sinifi`
# sozlugu). TEK YER: takvim sinavi ve canli uyari buradan okur.
#   fred/alfred: 08:30 ABD Dogu -> seans ONCESI -> ayni gun
#   fed: FOMC karari 14:00 ABD Dogu -> SEANS ICI -> ayni gun VE ertesi
#        gun (kapanistan girilirse kararin ilk tam gunu ertesi gundur)
MAKRO_ZAMAN = {"fred": "once", "alfred": "once", "fed": "seans"}


def _duz(parca: str) -> str:
    """HTML hucresini duz metne cevirir."""
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", parca))).strip()


def _tr_tarih(metin: str) -> date | None:
    """'22 Ocak 2026' -> date(2026, 1, 22). Eslesmezse None."""
    m = re.search(r"(\d{1,2})\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)\s+(20\d{2})", metin or "")
    if not m:
        return None
    ay = TR_AYLAR.get(m.group(2).lower())
    if not ay:
        return None
    try:
        return date(int(m.group(3)), ay, int(m.group(1)))
    except ValueError:
        return None


def _tarih_sayisi(metin: str) -> int:
    """
    Sayfada AYRISTIRILABILIR tarih var mi?

    "HTTP 200" tek basina ERISIM DEMEK DEGIL: TCMB'nin Takvim sayfasi 200
    ve 32 KB donuyor ama icerigi yalnizca gezinme menusu — tek bir tarih
    yok. Ilk surumde esik "200 + 5000 bayt"ti ve kaynak "acildi" diye
    isaretlendi; YANLIS POZITIF. Erisim testinin olcusu, aranan verinin
    GERCEKTEN orada olmasidir.
    """
    if not metin:
        return 0
    return (len(re.findall(r"\b\d{1,2}\.\d{2}\.20\d{2}\b", metin))
            + len(re.findall(r"\b\d{1,2}\s+(?:Ocak|Şubat|Mart|Nisan|Mayıs|"
                             r"Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)"
                             r"\s+20\d{2}", metin))
            + len(re.findall(r"\b(?:January|February|March|April|May|June|July|"
                             r"August|September|October|November|December)\s+"
                             r"\d{1,2},?\s+20\d{2}", metin)))


def fred_tarihleri(html: str, yil: int) -> list[date]:
    """
    FRED yayin takvimi sayfasindaki gunler. SAF.

    Tarih satiri kalin yazili: "<span ...>Friday September 11, 2026</span>".
    Sayfanin baska yerlerinde de tarih var (guncelleme notlari vb.);
    yalnizca istenen YILA ait ve hafta gunuyle baslayan kalin satirlar
    alinir.
    """
    out: set[date] = set()
    for m in re.finditer(
            r"font-weight:\s*bold;?\s*\">\s*(?:Monday|Tuesday|Wednesday|"
            r"Thursday|Friday|Saturday|Sunday)\s+([A-Za-z]+)\s+(\d{1,2}),\s*"
            r"(\d{4})\s*<", html or ""):
        ay = AYLAR.get(m.group(1).lower())
        if not ay or int(m.group(3)) != yil:
            continue
        try:
            out.add(date(int(m.group(3)), ay, int(m.group(2))))
        except ValueError:
            continue
    return sorted(out)


def alfred_tarihleri(metin: str) -> list[str]:
    """ALFRED 'downloaddates' duz metni -> 'YYYY-MM-DD' listesi. SAF."""
    return sorted({m.group(0) for m in
                   re.finditer(r"(?m)^\d{4}-\d{2}-\d{2}$", metin or "")})


class TakvimCollector(BaseCollector):
    name = "takvim"
    needs_browser = False

    def collect(self) -> CollectorResult:
        yazilan = 0
        notlar = []

        try:
            satirlar = self._fed()
            yazilan += self._yaz(satirlar)
            self._kaynak_durumu("fed", "ok", f"{len(satirlar)} toplanti", FED_URL)
        except Exception as e:                          # noqa: BLE001
            log.warning("[takvim] Fed alinamadi: %s", e)
            self._kaynak_durumu("fed", "engelli", f"{type(e).__name__}", FED_URL)
            notlar.append(f"fed: {type(e).__name__}")

        try:
            satirlar = self._tcmb()
            yazilan += self._yaz(satirlar)
            self._kaynak_durumu("tcmb", "ok", f"{len(satirlar)} yayin", TCMB_URL)
        except Exception as e:                          # noqa: BLE001
            log.warning("[takvim] TCMB alinamadi: %s", e)
            self._kaynak_durumu("tcmb", "engelli", f"{type(e).__name__}", TCMB_URL)
            notlar.append(f"tcmb: {type(e).__name__}")

        try:
            satirlar = self._fred()
            yazilan += self._yaz(satirlar)
            self._kaynak_durumu("fred", "ok", f"{len(satirlar)} ileri yayin gunu",
                                "https://fred.stlouisfed.org/releases/calendar")
        except Exception as e:                          # noqa: BLE001
            log.warning("[takvim] FRED takvimi alinamadi: %s", e)
            self._kaynak_durumu("fred", "engelli", f"{type(e).__name__}",
                                "https://fred.stlouisfed.org/releases/calendar")
            notlar.append(f"fred: {type(e).__name__}")

        try:
            satirlar = self._alfred()
            yazilan += self._yaz(satirlar)
            self._kaynak_durumu("alfred", "ok", f"{len(satirlar)} gecmis yayin gunu",
                                "https://alfred.stlouisfed.org")
        except Exception as e:                          # noqa: BLE001
            log.warning("[takvim] ALFRED gecmisi alinamadi: %s", e)
            self._kaynak_durumu("alfred", "engelli", f"{type(e).__name__}",
                                "https://alfred.stlouisfed.org")
            notlar.append(f"alfred: {type(e).__name__}")

        # BLS YOKLAMASI KALDIRILDI (bkz. modul basligi). Eski durum satiri
        # kalirsa rapor istemi her gun "BLS takvimi cekilemiyor" yazar —
        # artik dogru olmayan bir kapsam boslugu. Satir silinir, UYDURMA
        # bir 'ok' ile ortulmez: BLS'ye hala erisemiyoruz, yalnizca ona
        # IHTIYACIMIZ kalmadi.
        with self.db.tx() as c:
            c.execute("DELETE FROM takvim_kaynak WHERE kaynak = 'bls'")

        engelli = self._engellileri_yokla()
        if engelli:
            notlar.append("hala erisilemeyen: " + ", ".join(engelli))

        durum = "ok" if yazilan else "partial"
        return CollectorResult(self.name, durum, yazilan,
                               " · ".join(notlar) if notlar else None)

    # ------------------------------------------------------------------
    def _fed(self) -> list[dict]:
        """
        FOMC toplanti takvimi.

        SAYFA YIL BASLIKLARIYLA BOLUNMUS ve ay/gun ciftleri yil bilgisi
        TASIMIYOR. Yil, ciftin sayfadaki KONUMUNA gore belirleniyor:
        her cift, kendisinden onceki son yil basligina aittir. Basliklar
        kronolojik DEGIL (2026, 2025, 2024, ..., 2027) — sirali varsayip
        saymak yanlis yila yazardi.
        """
        r = httpx.get(FED_URL, headers={"User-Agent": UA}, timeout=25.0,
                      follow_redirects=True)
        r.raise_for_status()
        s = r.text

        yillar = [(m.start(), int(m.group(1)))
                  for m in re.finditer(r">(\d{4})\s+FOMC\s+Meetings<", s)]
        if not yillar:
            raise RuntimeError("yil basligi bulunamadi (sayfa yapisi degismis)")

        out = []
        for m in re.finditer(
                r'fomc-meeting__month[^>]*>\s*(?:<strong>)?([A-Za-z]+)'
                r'(?:/[A-Za-z]+)?(?:</strong>)?\s*</div>\s*'
                r'<div class="fomc-meeting__date[^>]*>\s*(?:<strong>)?'
                r'([0-9]+)(?:\s*-\s*([0-9]+))?', s):
            onceki = [y for p, y in yillar if p < m.start()]
            if not onceki:
                continue
            yil = onceki[-1]
            ay = AYLAR.get(m.group(1).strip().lower())
            if not ay:
                continue
            # Toplanti iki gunluk; KARAR IKINCI GUN aciklanir ve piyasayi
            # o gun hareket ettirir. Takvime karar gunu yazilir.
            gun = int(m.group(3) or m.group(2))
            try:
                t = date(yil, ay, gun)
            except ValueError:
                continue
            out.append({"tarih": t.isoformat(), "kaynak": "fed", "bolge": "ABD",
                        "olay": "FOMC faiz karari", "onem": "yuksek",
                        "url": FED_URL})
        return out

    # ------------------------------------------------------------------
    def _tcmb(self) -> list[dict]:
        """
        TCMB yillik yayin takvimi: PPK karari, toplanti ozeti, Enflasyon
        Raporu, Finansal Istikrar Raporu.

        ONCE "ENGELLI" SANILDI, YANLISTI. Iki ayri olcum hatasi ust uste
        geldi: (1) sayfada `dd.mm.yyyy` arandi, oysa TCMB "22 Ocak 2026"
        yaziyor; (2) tarayicida `inner_text` tabloyu getirmedi. Ikisi de
        "kaynak erisilemiyor" sonucuna goturdu — oysa tablo duz HTTP ile
        HTML'de duruyordu. Ders: bir kaynagi "yok" ilan etmeden once
        ARANAN SEYIN BICIMINI dogrula.

        Tablo DORT SUTUN: her sutun bir olay turu, her hucre bir tarih.
        Bos hucre normal (her ayda her olay yok).
        """
        r = httpx.get(TCMB_URL, headers={"User-Agent": UA}, timeout=25.0,
                      follow_redirects=True)
        r.raise_for_status()
        tablo = None
        for t in re.findall(r"<table.*?</table>", r.text, re.S):
            if "Para Politikası Kurulu" in t:
                tablo = t
                break
        if tablo is None:
            raise RuntimeError("PPK tablosu bulunamadi (sayfa yapisi degismis)")

        satirlar_ham = re.findall(r"<tr[^>]*>(.*?)</tr>", tablo, re.S)
        basliklar: list[str] = []
        out: list[dict] = []
        for ham in satirlar_ham:
            hucreler = [_duz(h) for h in
                        re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", ham, re.S)]
            if not hucreler:
                continue
            if not basliklar and any("Para Politikası" in h for h in hucreler):
                basliklar = hucreler
                continue
            for i, h in enumerate(hucreler):
                t = _tr_tarih(h)
                if not t:
                    continue
                olay = basliklar[i] if i < len(basliklar) else "TCMB yayini"
                out.append({
                    "tarih": t.isoformat(), "kaynak": "tcmb",
                    "bolge": "Turkiye", "olay": olay,
                    # Faiz KARARI piyasayi hareket ettirir; toplanti ozeti
                    # ve raporlar aciklayicidir, surpriz tasimaz.
                    "onem": "yuksek" if "Karar" in olay else "orta",
                    "url": TCMB_URL})
        return out

    def _fred(self) -> list[dict]:
        """
        ABD makro yayinlarinin ILERI takvimi: bu yil + gelecek yil.

        Sayfa tek bir yayina (rid) suzulu; her kalin tarih satiri o
        yayinin bir gunudur. Gelecek yil henuz ilan edilmemisse sayfa
        BOS gelir — bu normal, hata degil. Ama bu yilin sayfasi bos
        gelirse sayfa yapisi degismistir: SESSIZ BOS YERINE HATA.
        """
        bugun = date.today()
        out: list[dict] = []
        for rid, (olay, onem) in FRED_YAYINLARI.items():
            for yil in (bugun.year, bugun.year + 1):
                r = httpx.get(FRED_TAKVIM_URL.format(rid=rid, yil=yil),
                              headers=FRED_BASLIK, timeout=40.0,
                              follow_redirects=True)
                r.raise_for_status()
                tarihler = fred_tarihleri(r.text, yil)
                if not tarihler and yil == bugun.year:
                    raise RuntimeError(
                        f"FRED rid={rid} {yil}: tarih ayristirilamadi "
                        "(sayfa yapisi degismis)")
                out += [{"tarih": t.isoformat(), "kaynak": "fred",
                         "bolge": "ABD", "olay": olay, "onem": onem,
                         "url": FRED_YAYIN_URL.format(rid=rid)}
                        for t in tarihler]
        return out

    def _alfred(self) -> list[dict]:
        """ABD makro yayinlarinin GECMIS gunleri (ALFRED arsivi)."""
        out: list[dict] = []
        for rid, (olay, onem) in FRED_YAYINLARI.items():
            r = httpx.get(ALFRED_URL.format(rid=rid),
                          headers=FRED_BASLIK, timeout=40.0,
                          follow_redirects=True)
            r.raise_for_status()
            tarihler = alfred_tarihleri(r.text)
            if not tarihler:
                raise RuntimeError(f"ALFRED rid={rid}: tarih ayristirilamadi")
            out += [{"tarih": t, "kaynak": "alfred", "bolge": "ABD",
                     "olay": olay, "onem": onem,
                     "url": FRED_YAYIN_URL.format(rid=rid)}
                    for t in tarihler if t >= ALFRED_ALT_SINIR]
        return out

    def _yaz(self, satirlar: list[dict]) -> int:
        if not satirlar:
            return 0
        with self.db.tx() as c:
            c.executemany(
                """INSERT INTO takvim (tarih, kaynak, bolge, olay, onem, url)
                   VALUES (:tarih, :kaynak, :bolge, :olay, :onem, :url)
                   ON CONFLICT(tarih, kaynak, olay) DO UPDATE SET
                     bolge=excluded.bolge, onem=excluded.onem,
                     url=excluded.url, guncelleme=datetime('now')""", satirlar)
        return len(satirlar)

    def _kaynak_durumu(self, kaynak: str, durum: str, ayrinti: str,
                       url: str) -> None:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO takvim_kaynak (kaynak, durum, ayrinti, url)
                   VALUES (?,?,?,?)
                   ON CONFLICT(kaynak) DO UPDATE SET
                     durum=excluded.durum, ayrinti=excluded.ayrinti,
                     url=excluded.url, son_deneme=datetime('now')""",
                (kaynak, durum, ayrinti, url))

    def _engellileri_yokla(self) -> list[str]:
        """
        Engelli kaynaklari HER KOSUDA yeniden dener.

        Ucuz (tek GET) ve degerli: TUIK portali duzeldigi gun bunu
        kimsenin elle kontrol etmesi gerekmez. "Calismiyor" bilgisi
        VERIDE durmali, bir yorum satirinda degil.
        """
        hala_kapali = []
        for kaynak, (url, aciklama) in ENGELLI_KAYNAKLAR.items():
            try:
                r = httpx.get(url, headers={"User-Agent": UA}, timeout=15.0,
                              follow_redirects=True)
                tarih = _tarih_sayisi(r.text)
                if r.status_code == 200 and tarih >= 5:
                    self._kaynak_durumu(
                        kaynak, "acildi",
                        f"HTTP 200, {tarih} tarih ayristirilabildi — "
                        f"AYRISTIRICI YAZILMALI", url)
                    log.warning("[takvim] %s ARTIK VERI DONDURUYOR (%s) — "
                                "ayristirici yazilmali: %s", kaynak, aciklama, url)
                    continue
                self._kaynak_durumu(
                    kaynak, "engelli",
                    f"HTTP {r.status_code}, {len(r.text)} bayt, {tarih} tarih", url)
            except Exception as e:                      # noqa: BLE001
                self._kaynak_durumu(kaynak, "engelli", type(e).__name__, url)
            hala_kapali.append(kaynak)
        return hala_kapali


def yaklasan(db, gun: int = 14, limit: int = 12) -> dict:
    """
    Bundle'a giden takvim: onumuzdeki `gun` gunun olaylari + KAYNAK DURUMU.

    Kaynak durumu HER ZAMAN gonderiliyor, olay listesi bos olsa bile.
    "Yarin onemli bir sey yok" ile "takvim kaynagi kirik" ayni sey
    degildir; ikincisi soylenmezse birincisi sanilir.
    """
    bugun = date.today()
    son = (bugun + timedelta(days=gun)).isoformat()
    olaylar = [dict(r) for r in db.query(
        """SELECT tarih, bolge, olay, onem, url FROM takvim
           WHERE tarih >= ? AND tarih <= ? ORDER BY tarih LIMIT ?""",
        (bugun.isoformat(), son, limit))]
    kaynaklar = {r["kaynak"]: {"durum": r["durum"], "ayrinti": r["ayrinti"],
                               "son_deneme": r["son_deneme"]}
                 for r in db.query("SELECT * FROM takvim_kaynak")}
    return {
        "olaylar": olaylar,
        "pencere_gun": gun,
        "kaynak_durumu": kaynaklar,
        "not": ("Takvim kapsami DAR: yalnizca `durum='ok'` olan kaynaklar "
                "cekiliyor. Bos bir liste 'onemli bir sey yok' DEMEK DEGILDIR; "
                "kaynak durumuna bak ve eksik kapsami raporda BELIRT."),
    }
