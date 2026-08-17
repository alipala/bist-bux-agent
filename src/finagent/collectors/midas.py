"""
Midas — PUBLIC BIST piyasa verisi. (Portfoy hala ekran goruntusundan.)

ONCEKI TESPIT KISMEN YANLISTI
-----------------------------
Bu dosya once "Midas public bir enstruman katalogu yayinlamiyor, cekilecek
hicbir sey yok" diyordu. Portfoy/islem arayuzu icin dogru (web arayuzu
gercekten yok, 2026-08-14'te dogrulandi), ama PIYASA VERISI icin YANLIS:
`getmidas.com/canli-borsa/` altinda giris gerektirmeyen, sunucu tarafinda
uretilmis tablolar var. 2026-08-15'te olculdu — JavaScript bile gerekmiyor,
veri dogrudan HTML'de.

NE VERIYOR
----------
  /canli-borsa/                     ~626 hisselik TAM liste (tek tablo)
  /canli-borsa/xu100-...            BIST 100 bilesenleri
  /canli-borsa/xu050-... xu030-...  BIST 50 / BIST 30
  /canli-borsa/en-cok-artan|azalan  gunun hareketlileri
  /midasin-kulaklari/               Midas'in kendi piyasa yazilari

Kolonlar: Hisse · Son · Alis · Satis · Fark · En Dusuk · En Yuksek · AOF ·
Hacim TL · Hacim Lot

ONEMLI: 15 DAKIKA GECIKMELI
---------------------------
Sayfada acikca yaziyor: "Canli Borsa (15 dakika gecikmeli)". Gunluk kapanis
icin sorun degil AMA veri "canli" diye sunulmamali.

DAHA ONEMLISI: SEANS ACIKKEN KAPANIS YAZILMAZ
---------------------------------------------
"Son" alani seans sirasinda ANLIK fiyattir. Onu gunluk kapanis olarak
kaydetmek, sonradan fark edilmesi zor bir hata sinifi olurdu: gosterge
hesaplanir, tutarli gorunur, yanlis olur. Bu yuzden `prices` tablosuna
YALNIZCA seans kapandiktan sonra yaziliyor. Seans aciksa katalog ve endeks
uyeligi yine guncelleniyor (bunlar zamandan bagimsiz), fiyat yazilmiyor
ve sebebi raporlaniyor.

HABER KADEMESI: 3
-----------------
"Midas'in Kulaklari" bir ARACI KURUMUN kendi yorumu. Ozgun icerik ama
birincil kaynak degil ve dogasi geregi promosyonel (aracinin isi islem
yaptirmak). Kademe 3 = kesif icin degerli, KANIT DEGIL. BIST'te kademe 1
karsiligi KAP'tir; buradaki bir olgu iddiasi KAP'a karsi dogrulanmali.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

KOK = "https://www.getmidas.com"
TUM = f"{KOK}/canli-borsa/"
ENDEKS_SAYFALARI = {
    "BIST 100": f"{KOK}/canli-borsa/xu100-bist-100-hisseleri",
    "BIST 50": f"{KOK}/canli-borsa/xu050-bist-50-hisseleri",
    "BIST 30": f"{KOK}/canli-borsa/xu030-bist-30-hisseleri",
}
HABER = f"{KOK}/midasin-kulaklari/"
DETAY = KOK + "/canli-borsa/{slug}-hisse/"
TEMETTU = KOK + "/canli-borsa/{slug}-hisse/temettu/"

# Detay sayfasindaki ozet metrikler. BIST tarafinda F/K, PD/DD ve net kar
# BASKA HICBIR KAYNAKTAN gelmiyordu — XBRL yalnizca SEC'e tabi sirketleri
# kapsiyor, BIST sirketleri orada yok.
#
# BILANCO SAYFASI ALINMIYOR: satir adlari HTML'de ama degerler yer tutucu
# ("0,00%"), gercek sayilar JS ile sonradan geliyor. Tarayici acmak
# gerekirdi; ozet metrikler zaten en degerli kismi veriyor.
DETAY_ALANLARI = {
    "Son İşlem Fiyatı": ("SonFiyat", "TRY"),
    "F/K": ("FK", "kat"),
    "PD/DD": ("PDDD", "kat"),
    "Piyasa Değeri": ("PiyasaDegeri", "TRY"),
    "Sermaye": ("Sermaye", "TRY"),
    # DIKKAT — BU BIR TAKVIM DONEMI DEGIL, SON 12 AY (TTM).
    # Midas detay sayfasindaki "Net Kâr" son dort ceyregin toplamidir.
    # Dogrulandi (2026-08-17): AKBNK, EREGL ve GARAN icin
    # FY2025 - H1'25 + H1'26 formulu ile %0,0 sapma.
    #
    # Kavram adi `NetKar` OLAMAZ: midasbilanco ayni adla GERCEK takvim
    # donemlerini yaziyor ve ikisi ayni seride karisirdi. Ayri ad, bunu
    # yapisal olarak imkansiz kiliyor.
    "Net Kâr": ("NetKarTTM", "TRY"),
    "Volatilite": ("Volatilite", "%"),
    "Taban": ("Taban", "TRY"),
    "Tavan": ("Tavan", "TRY"),
    "Haftalık En Yüksek": ("HaftalikYuksek", "TRY"),
    "Haftalık En Düşük": ("HaftalikDusuk", "TRY"),
    "Aylık En Yüksek": ("AylikYuksek", "TRY"),
    "Aylık En Düşük": ("AylikDusuk", "TRY"),
}

# AKIM buyuklugu olan (bir DONEM boyunca biriken) detay alanlari.
# Gerisi anlik: fiyat, taban/tavan, piyasa degeri, haftalik/aylik uclar.
# F/K ve PD/DD de anlik ORANLAR — paydalari TTM/defter degeri olsa da
# kendileri "bugun itibariyla" tek sayidir, seriye girmezler.
TTM_KAVRAMLARI = {"NetKarTTM"}

AY_TR = {"ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4,
         "mayıs": 5, "mayis": 5, "haziran": 6, "temmuz": 7, "ağustos": 8,
         "agustos": 8, "eylül": 9, "eylul": 9, "ekim": 10, "kasım": 11,
         "kasim": 11, "aralık": 12, "aralik": 12}

BASLIK = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# BIST surekli islem 10:00-18:00 (TR, UTC+3), kapanis seansi ~18:10.
# 15 dk gecikme de eklenince guvenli esik UTC 15:30.
KAPANIS_UTC = 15.5


def _sayi(m: str | None) -> float | None:
    """'1.234,56' -> 1234.56 ; '-' ve bos -> None."""
    if not m:
        return None
    # "TL", "₺" ve "%" ekleri temizlenir. Temettu tablosunda tutarlar
    # "3,36TL" bicimindeydi ve TL eki temizlenmedigi icin brut/net
    # alanlari BOS kaliyordu (olculdu).
    m = (m.strip().replace("%", "").replace("₺", "")
         .replace("TL", "").replace("\xa0", "").replace(" ", ""))
    if m in ("-", "", "—"):
        return None
    try:
        return float(m.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _tablo_oku(html: str) -> list[dict]:
    """En buyuk tabloyu hisse satirlarina cevirir."""
    from bs4 import BeautifulSoup
    s = BeautifulSoup(html, "lxml")
    en_iyi, en_cok = None, 0
    for t in s.find_all("table"):
        n = len(t.find_all("tr"))
        if n > en_cok:
            en_iyi, en_cok = t, n
    if en_iyi is None:
        return []
    out = []
    for tr in en_iyi.find_all("tr")[1:]:
        h = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(h) < 10:
            continue
        sembol = h[0].upper().strip()
        # Sembol harf+rakam, 3-6 karakter. Baslik/ozet satirlarini eler.
        if not re.fullmatch(r"[A-Z0-9]{3,6}", sembol):
            continue
        kapanis = _sayi(h[1])
        # FIYATI OLMAYAN SATIR HISSE DEGILDIR. Sembol suzgecinden gecen
        # ama fiyati olmayan ozet satirlari var: "TOPLAM" tam 6 harf,
        # duzenli ifadeye uyuyor ve butun alanlari bos bir enstruman
        # olarak kataloga girecekti (test yakaladi).
        if kapanis is None:
            continue
        out.append({
            "symbol": sembol, "close": kapanis,
            "alis": _sayi(h[2]), "satis": _sayi(h[3]),
            "fark_%": _sayi(h[4]), "low": _sayi(h[5]), "high": _sayi(h[6]),
            "aof": _sayi(h[7]), "hacim_tl": _sayi(h[8]),
            "volume": _sayi(h[9]),
        })
    return out


class MidasCollector(BaseCollector):
    name = "midas"
    needs_browser = False           # veri HTML'de; JS gerekmiyor

    def collect(self) -> CollectorResult:
        import httpx

        toplam, notlar = 0, []
        with httpx.Client(timeout=40, follow_redirects=True,
                          headers=BASLIK) as http:
            self._http = http
            try:
                n, not_ = self._piyasa()
                toplam += n
                notlar.append(f"piyasa: {n} ({not_})" if not_ else f"piyasa: {n}")
            except Exception as e:                # noqa: BLE001
                log.warning("[midas] piyasa: %s", e)
                notlar.append(f"piyasa: HATA {type(e).__name__}")
            try:
                n, not_ = self._endeks_uyeligi()
                toplam += n
                notlar.append(f"endeks: {n} ({not_})" if not_ else f"endeks: {n}")
            except Exception as e:                # noqa: BLE001
                log.warning("[midas] endeks: %s", e)
                notlar.append(f"endeks: HATA {type(e).__name__}")
            try:
                n, not_ = self._hisse_detaylari()
                toplam += n
                notlar.append(f"detay: {n}" + (f" ({not_})" if not_ else ""))
            except Exception as e:                # noqa: BLE001
                log.warning("[midas] detay: %s", e)
                notlar.append(f"detay: HATA {type(e).__name__}")
            try:
                n, not_ = self._haberler()
                toplam += n
                notlar.append(f"haber: {n} ({not_})" if not_ else f"haber: {n}")
            except Exception as e:                # noqa: BLE001
                log.warning("[midas] haber: %s", e)
                notlar.append(f"haber: HATA {type(e).__name__}")

        durum = "ok" if toplam and not any("HATA" in x for x in notlar) else (
            "partial" if toplam else "error")
        return CollectorResult(self.name, durum, toplam, " · ".join(notlar))

    # ------------------------------------------------------------------
    @staticmethod
    def _bugun_yazilir_mi() -> tuple[bool, str]:
        """
        Bugunun kapanisi yazilabilir mi?

        IKI ayri kosul var ve ikisi de gerekli:
        1. BUGUN ISLEM GUNU OLMALI. Hafta sonu sayfa yine veri gosteriyor
           ama o CUMA'nin kapanisidir. Bugunun tarihiyle yazmak islem
           gormeyen bir gune HAYALET BAR koyar; gunluk getiri serisinde
           sifir getirili sahte bir gun olusur ve oynaklik olcumu bozulur.
           Olculdu: 2026-08-15 Cumartesi, 625 bar bu sekilde yazilmisti.
           Cuma'nin tarihine geri yazmak da yapilmiyor — sayfanin hangi
           ana ait oldugunu KANITLAYAMIYORUZ (Is Yatirim ayni kapanis icin
           %0.8 farkli deger veriyor).
        2. SEANS KAPANMIS OLMALI. Seans sirasinda "Son" anlik fiyattir;
           kapanis diye kaydedilirse gosterge her calismada degisir.
        """
        n = datetime.now(timezone.utc)
        if n.weekday() >= 5:
            return False, ("hafta sonu — sayfadaki degerler Cuma'ya ait, "
                           "bugunun tarihiyle YAZILMADI")
        saat = n.hour + n.minute / 60
        if saat < KAPANIS_UTC:
            return False, (f"seans ACIK (UTC {n:%H:%M}) — 'Son' anlik fiyat, "
                           "gunluk kapanis olarak YAZILMADI")
        return True, "seans kapali"

    def _piyasa(self) -> tuple[int, str | None]:
        r = self._http.get(TUM)
        r.raise_for_status()
        satirlar = _tablo_oku(r.text)
        if not satirlar:
            return 0, "tablo bulunamadi"

        # Katalog HER ZAMAN guncellenir — zamandan bagimsiz.
        yeni = 0
        an = datetime.now(timezone.utc).date().isoformat()
        likidite = []
        for s in satirlar:
            iid = self.db.upsert_instrument(s["symbol"], "BIST", None,
                                            "equity", "TRY")
            s["_iid"] = iid
            yeni += 1
            # GUNLUK HACIM her kosuda kaydedilir — fiyat kapanisi
            # yazilmasa bile. Tarayicinin LIKIDITE SUZGECI bunu okuyor:
            # 729 BIST kagidinin cogu gunlerce zar zor islem goruyor ve
            # onlarda teknik analiz anlamsizdir. Hacim bilinmeden hangi
            # kagidin taranmaya deger oldugu da bilinemez.
            if s.get("hacim_tl") is not None:
                likidite.append((iid, "GunlukHacimTL", "TRY", None, an, None,
                                 float(s["hacim_tl"]), "midas", None, None,
                                 None, an, None))
            # GUNLUK DEGISIM de saklaniyor. "En cok artan/azalan" sayfalari
            # AYRICA CEKILMIYOR: olculdu, o sayfalar da ayni 627 satirlik
            # TAM listeyi donduruyor, yalnizca sirasi farkli. Yeni veri
            # yok, sadece siralama — onu kendimiz yapabiliriz ve boylece
            # siralama diger verimizle tutarli kalir.
            if s.get("fark_%") is not None:
                likidite.append((iid, "GunlukDegisimPct", "%", None, an, None,
                                 float(s["fark_%"]), "midas", None, None,
                                 None, an, None))
            # AOF = agirlikli ortalama fiyat. Kapanistan daha bilgilendirici:
            # gunun HACMININ hangi fiyattan gectigini soyler. Kapanis tek bir
            # islemin izidir, AOF gunun tamamini temsil eder.
            if s.get("aof") is not None:
                likidite.append((iid, "AOF", "TRY", None, an, None,
                                 float(s["aof"]), "midas", None, None,
                                 None, an, None))
            # Alis/satis makasi = likidite olcusu. Seans kapaliyken son
            # kotasyonu gosterir; gunluk analizde tek basina anlamli
            # degil ama makasin GENISLIGI islem maliyetini soyluyor.
            if s.get("alis") and s.get("satis") and s["alis"] > 0:
                makas = (s["satis"] / s["alis"] - 1) * 100
                likidite.append((iid, "AlisSatisMakasiPct", "%", None, an, None,
                                 makas, "midas", None, None, None, an, None))
        if likidite:
            self.db.upsert_fundamentals(likidite)

        kapandi, sebep = self._bugun_yazilir_mi()
        if not kapandi:
            return yeni, f"{len(satirlar)} hisse katalogda; {sebep}"

        gun = datetime.now(timezone.utc).date().isoformat()
        n = 0
        for s in satirlar:
            if s["close"] is None:
                continue
            n += self.db.upsert_prices(s["_iid"], [{
                "ts": gun, "open": None, "high": s["high"], "low": s["low"],
                "close": s["close"], "volume": s["volume"]}],
                self.name, currency="TRY")
        return yeni + n, f"{len(satirlar)} hisse · {n} kapanis ({sebep})"

    def _endeks_uyeligi(self) -> tuple[int, str | None]:
        toplam, ayrinti = 0, []
        for ad, url in ENDEKS_SAYFALARI.items():
            r = self._http.get(url)
            r.raise_for_status()
            satirlar = _tablo_oku(r.text)
            if not satirlar:
                continue
            with self.db.tx() as c:
                for s in satirlar:
                    iid = self.db.upsert_instrument(s["symbol"], "BIST", None,
                                                    "equity", "TRY")
                    c.execute(
                        "INSERT OR IGNORE INTO index_members (instrument_id, index_name)"
                        " VALUES (?,?)", (iid, ad))
            toplam += len(satirlar)
            ayrinti.append(f"{ad}:{len(satirlar)}")
        return toplam, ", ".join(ayrinti) or None

    def _hisse_detaylari(self) -> tuple[int, str | None]:
        """
        Hisse detay sayfalarindan ozet metrikler + temettu.

        Her calismada birkac sembol — 30 sembol x 2 sayfa = 60 istek eder
        ve gereksiz yavaslatir. En bayat olanlar oncelikli, donusumlu.
        """
        import re as _re
        from bs4 import BeautifulSoup

        # BIST hisseleri izleme listesinde DEGIL, endeks uyeliginde duruyor;
        # `research_targets()` endeks uyelerini kapsamiyor (olculdu: "BIST
        # arastirma hedefi yok" donuyordu). Is Yatirim collector'undaki
        # ayni yaklasim: BIST 30 ∪ portfoy.
        hedefler = [r["symbol"] for r in self.db.query(
            """SELECT DISTINCT i.symbol FROM instruments i
               WHERE i.venue = 'BIST' AND (
                   i.id IN (SELECT instrument_id FROM index_members
                            WHERE index_name = 'BIST 30')
                   OR i.id IN (SELECT instrument_id FROM positions)
                   OR i.id IN (SELECT instrument_id FROM watchlist))""")]
        if not hedefler:
            return 0, "BIST hedefi yok (once endeks uyeligi cekilmeli)"
        adet = int(self.s.get("sources.midas.detay_per_run", 6))
        secilen = self._en_bayat_detay(hedefler, adet)

        toplam, alinan = 0, []
        for sem in secilen:
            iid = self.db.upsert_instrument(sem, "BIST", None, "equity", "TRY")
            slug = sem.lower()
            try:
                r = self._http.get(DETAY.format(slug=slug))
                r.raise_for_status()
            except Exception as e:                     # noqa: BLE001
                log.debug("[midas] %s detay alinamadi: %s", sem, e)
                continue
            metin = BeautifulSoup(r.text, "lxml").get_text(" ", strip=True)
            bugun = datetime.now(timezone.utc).date()
            an = bugun.isoformat()
            # TTM'in BASLANGICI da yazilir. `days=None` birakmak iki kez
            # zarar veriyordu: (a) `finansal_seri(donem="anlik")` filtresi
            # `days IS NULL` oldugu icin bir AKIM buyuklugu BILANCO ANLIK
            # KALEMI olarak servis ediliyordu, (b) "farkli uzunluktakiler
            # karsilastirilmaz" korumasi hic ateslenemiyordu.
            ttm_bas = (bugun - timedelta(days=365)).isoformat()
            satir = []
            for etiket, (kavram, birim) in DETAY_ALANLARI.items():
                m = _re.search(_re.escape(etiket) + r"\s*₺?\s*([\d.,]+)", metin)
                deger = _sayi(m.group(1)) if m else None
                if deger is None:
                    continue
                if kavram in TTM_KAVRAMLARI:
                    satir.append((iid, kavram, birim, ttm_bas, an, 365, deger,
                                  "midas", None, "TTM", None, an, None))
                else:
                    # Gerisi GERCEKTEN anlik: fiyat, taban/tavan, piyasa
                    # degeri, haftalik/aylik uc degerler. Onlarda
                    # `days=None` DOGRU.
                    satir.append((iid, kavram, birim, None, an, None, deger,
                                  "midas", None, None, None, an, None))
            if satir:
                toplam += self.db.upsert_fundamentals(satir)
                alinan.append(sem)
            toplam += self._temettu(iid, slug)
        return toplam, ", ".join(alinan) or "veri cikarilamadi"

    def _en_bayat_detay(self, semboller, adet):
        skor = []
        for s in semboller:
            r = self.db.query(
                """SELECT MAX(f.period_end) son FROM fundamentals f
                   JOIN instruments i ON i.id = f.instrument_id
                   WHERE i.symbol = ? AND f.form = 'midas'""", (s,))
            skor.append((r[0]["son"] or "", s))
        skor.sort()
        return [s for _, s in skor[:adet]]

    def _temettu(self, instrument_id: int, slug: str) -> int:
        """
        Temettu tarihcesi. Getiri hesabinda temettu ihmal edilirse toplam
        getiri SISTEMATIK olarak dusuk cikar — ozellikle BIST'te temettu
        verimi yuksek.
        """
        import re as _re
        from bs4 import BeautifulSoup
        try:
            r = self._http.get(TEMETTU.format(slug=slug))
            r.raise_for_status()
        except Exception:                              # noqa: BLE001
            return 0
        s = BeautifulSoup(r.text, "lxml")
        tablo = next((t for t in s.find_all("table")
                      if "Temettü" in t.get_text()), None)
        if tablo is None:
            return 0
        satirlar = []
        for tr in tablo.find_all("tr")[1:]:
            h = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            if len(h) < 5:
                continue
            m = _re.match(r"(\d{1,2})\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)\s+(\d{4})", h[0])
            if not m:
                continue
            ay = AY_TR.get(m.group(2).casefold())
            if not ay:
                continue
            try:
                tarih = datetime(int(m.group(3)), ay, int(m.group(1))).date().isoformat()
            except ValueError:
                continue
            satirlar.append((instrument_id, tarih, _sayi(h[1]), _sayi(h[2]),
                             _sayi(h[3]), _sayi(h[4]), "TRY", self.name))
        if not satirlar:
            return 0
        with self.db.tx() as c:
            c.executemany(
                """INSERT INTO dividends (instrument_id, odeme_tarihi, verim_pct,
                   fiyat, brut, net, para_birimi, kaynak)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(instrument_id, odeme_tarihi, kaynak) DO UPDATE SET
                     verim_pct=excluded.verim_pct, fiyat=excluded.fiyat,
                     brut=excluded.brut, net=excluded.net""", satirlar)
        return len(satirlar)

    def _haberler(self) -> tuple[int, str | None]:
        """
        Midas'in kendi piyasa yazilari. KADEME 3 — kesif icin, kanit degil.

        Sembol eslestirmesi HEM baslikta HEM URL'de aranir: yazi
        adreslerinde sembol listesi geciyor
        (".../...-sahol-ismen-pgsus-sasa-edata-p-690663"), baslikta
        gecmese bile oradan yakalanir.
        """
        from bs4 import BeautifulSoup
        r = self._http.get(HABER)
        r.raise_for_status()
        s = BeautifulSoup(r.text, "lxml")

        bilinen = {x["symbol"] for x in self.db.query(
            "SELECT symbol FROM instruments WHERE venue='BIST'")}
        if not bilinen:
            return 0, "BIST katalogu bos — once piyasa tablosu cekilmeli"

        gorulen, rows = set(), []
        for a in s.find_all("a", href=True):
            href = a["href"]
            if "/midasin-kulaklari/" not in href or href in gorulen:
                continue
            ham = a.get_text(" ", strip=True)
            if not ham or len(ham) < 15:
                continue
            gorulen.add(href)

            # "Agustos 15, 2026 • 11 dakika okuma suresi  BASLIK"
            tarih = self._tarih(ham)
            baslik = re.sub(r"^.*?okuma s[uü]resi\s*", "", ham).strip() or ham

            # SEMBOL BUYUK HARF ARANIR, baslik BUYUK HARFE CEVRILMEZ.
            # 29 BIST sembolu gundelik Turkce kelimeyle cakisiyor
            # (HEDEF, KENT, ARENA, LIDER, BIZIM, MARKA...). Basligi
            # buyuk harfe cevirince "THYAO hedef fiyatini yukseltti"
            # HEDEF Holding'e ait sanildi — ajan paneli bunu yakaladi.
            # Ticker haberde BUYUK yazilir, gundelik kelime yazilmaz.
            #
            # URL slug'i ayri ele alinir: orada her sey kucuk harf, ama
            # slug'da sembol ancak GERCEKTEN o hisseyle ilgiliyse gecer
            # (".../...-sahol-ismen-pgsus-sasa-edata-p-690663").
            slug = (href or "").lower()
            semboller = sorted({
                x for x in bilinen
                if re.search(rf"(?<![A-Za-z0-9]){x}(?![A-Za-z0-9])", baslik)
                or re.search(rf"(?<![a-z0-9]){x.lower()}(?![a-z0-9])", slug)})
            rows.append({
                "url": href if href.startswith("http") else KOK + href,
                "title": baslik[:300], "source": "midas",
                "published_at": tarih, "summary": None,
                "symbols": semboller, "publisher": "Midas'in Kulaklari",
                "tier": 3,      # araci kurum yorumu: kesif evet, kanit hayir
            })
        if not rows:
            return 0, "yazi bulunamadi"
        n = self.db.upsert_news(rows)
        eslesen = sum(1 for r in rows if r["symbols"])
        return n, f"{eslesen}/{len(rows)} yazida sembol eslesti (kademe 3)"

    @staticmethod
    def _tarih(metin: str) -> str | None:
        AY = {"ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4,
              "mayıs": 5, "mayis": 5, "haziran": 6, "temmuz": 7,
              "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
              "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12}
        m = re.search(r"([A-Za-zÇĞİÖŞÜçğıöşü]+)\s+(\d{1,2}),\s*(\d{4})", metin)
        if not m:
            return None
        ay = AY.get(m.group(1).casefold())
        if not ay:
            return None
        try:
            return datetime(int(m.group(3)), ay, int(m.group(2))).strftime(
                "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
