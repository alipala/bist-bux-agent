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

BASLIK = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# BIST surekli islem 10:00-18:00 (TR, UTC+3), kapanis seansi ~18:10.
# 15 dk gecikme de eklenince guvenli esik UTC 15:30.
KAPANIS_UTC = 15.5


def _sayi(m: str | None) -> float | None:
    """'1.234,56' -> 1234.56 ; '-' ve bos -> None."""
    if not m:
        return None
    m = m.strip().replace("%", "").replace(" ", "")
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
        for s in satirlar:
            iid = self.db.upsert_instrument(s["symbol"], "BIST", None,
                                            "equity", "TRY")
            s["_iid"] = iid
            yeni += 1

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

            havuz = f"{baslik} {href}".upper()
            semboller = sorted({x for x in bilinen
                                if re.search(rf"(?<![A-Z0-9]){x}(?![A-Z0-9])", havuz)})
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
