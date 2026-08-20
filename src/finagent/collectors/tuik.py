"""
TUIK SDMX — Turkiye makro gosterge katmani.

NEDEN BU YOL
------------
Ali once "TUIK'e giris yapabilirsin, kullanici adi ve sifreyi .env'e
eklerim" dedi. Sifre ALINMADI ve gerekmedi: TUIK'in RESMI, dokumante
SDMX 2.1 REST servisi var ve erisimi API ANAHTARIYLA saglaniyor.
Anahtar sifreden dort sebeple ustun — resmi ve dokumante oldugu icin
arayuz degisiminde kirilmaz, iptal edilebilir ve kapsami sinirli
(sizarsa hesap ele gecmez), kullanim sartlari belirsizligi yok, ve
Keycloak oturumu taklit etmeye calismiyoruz.

OLCULEN GERCEKLER (2026-08-18, tek tek dogrulandi)
--------------------------------------------------
  Token ucu        200; ACCESS TOKEN OMRU 300 SANIYE (cok kisa)
  SDMX servisi     200; 408 veri akisi
  TUFE / CPI       YOK — 408 akisin hicbirinde, TR ve EN adlarda sifir
                   eslesme. TUIK tuketici enflasyonunu SDMX'ten
                   YAYINLAMIYOR. Elimizdeki en yakin oncu gosterge
                   Yi-UFE.
  Yayin takvimi    YOK — `veriportali.../api/tr/press/*` bu token'la da
                   403 donuyor; portal API'si AYRI bir yetki katmani.
                   Yani anahtar "yarin ne var"i COZMUYOR.

Kisacasi anahtar takvimi degil, MAKRO VERIYI aciyor.

FILTRESIZ CEKMEK SECENEK DEGIL
------------------------------
Yi-UFE 11 boyutlu ve `all` sorgusu SON 4 GOZLEM icin 2,3 MB / 6.680
satir donduruyor. Seri anahtari boyut SIRASINA gore kuruluyor ve o
sirayi DSD veriyor — bu yuzden anahtar elle yazilmiyor, `_anahtar()`
DSD'den URETIYOR. Boyut eklenirse/sirasi degisirse config bozulmaz.
"""
from __future__ import annotations

import logging
import os
import re
import time

import httpx

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

TOKEN_URL = "https://giris.tuik.gov.tr/realms/web/protocol/openid-connect/token"
SDMX = "https://nsiws.tuik.gov.tr/rest"
CLIENT_ID = "nsi-ws-consumer"

_OBS = re.compile(r'<generic:ObsDimension[^>]*value="([^"]+)"[^>]*/>\s*'
                  r'<generic:ObsValue[^>]*value="([^"]+)"')


class TuikCollector(BaseCollector):
    name = "tuik"
    needs_browser = False

    def collect(self) -> CollectorResult:
        anahtar = (os.environ.get("TUIK_API_KEY") or "").strip()
        if not anahtar:
            return CollectorResult(self.name, "skipped", 0,
                                   "TUIK_API_KEY tanimli degil (.env)")
        self._api_key = anahtar
        self._token: tuple[str, float] | None = None
        self._dsd_onbellek: dict[str, list[str]] = {}

        # SURE BUTCESI — OLCULEN BIR ARIZANIN KARSILIGI.
        #
        # 2026-08-19 22:15 nabzi: TUIK dort kez zaman asimina girdi
        # (22:38:25 / 22:43:39 / 22:48:43 / 22:53:45, her biri ~5 dk) ve
        # collector 1225,9 sn = 20,4 dk surdu. Gecelik zincirin kabuk
        # butcesi 45 dk; toplama 43,9 dk yedi, panel 33 saniye yasadi ve
        # SIGTERM ile olduruldu. O gece BILDIRIM GITMEDI.
        #
        # Sagliklinda ayni is SANIYELER suruyor: 2026-08-18'de 3,4 sn
        # (seriler tazeydi, yalnizca katalog cekildi), tek seri sorgusu
        # `son_gozlem: 240` ile 1,7 sn (bkz. settings.yaml). Yani 20
        # dakikanin tamami BEKLEME, veri degil.
        #
        # `isyatirim.azami_sure_sn` ile AYNI KALIP ve ayni gerekce: sinir
        # DISARIDAN (kabuk, SIGTERM) degil ICERIDEN uygulanir — butce
        # dolunca collector duzgunce durur, `partial` doner ve kosunun
        # geri kalani CALISIR. Disaridan oldurulen bir kosu iz birakmaz.
        butce_sn = float(self.s.get("sources.tuik.azami_sure_sn", 300))
        self._butce_bitis = (time.monotonic() + butce_sn) if butce_sn > 0 else None

        toplam, notlar, kesildi = 0, [], []
        try:
            n = self._katalog()
            toplam += n
            notlar.append(f"katalog {n} akis")
        except Exception as e:                          # noqa: BLE001
            log.warning("[tuik] katalog alinamadi: %s", e)
            notlar.append(f"katalog: {type(e).__name__}")

        # EN BAYAT ONCE — butce altinda ACLIK olmasin.
        #
        # `isyatirim` ile ayni gerekce ve ayni cozum. Sabit sirada
        # gidilseydi butce dolunca HEP AYNI seri kesilirdi: canlida
        # olculdu (2026-08-20 00:16), tek bir seri cekimi 108,9 sn
        # surdu (bir DSD istegi 90 sn'de asti, yeniden deneme tuttu),
        # yani uc seri 300 sn'lik butceyi asabiliyor. Sabit sirada
        # TR_EKONOMIK_GUVEN her kosuda kesilir ve HIC guncellenmezdi —
        # gorunmez, kalici bir kapsam boslugu. En bayat once gidince
        # kesilen kuyruk her kosuda degisir.
        istenen = sorted(self.s.get("sources.tuik.seriler") or [],
                         key=lambda t: self._son_guncelleme(str(t.get("kod"))))
        taze_saat = float(self.s.get("sources.tuik.tazelik_saat", 24))
        basarisiz, atlanan = [], []
        for tanim in istenen:
            if self._taze_mi(tanim["kod"], taze_saat):
                atlanan.append(tanim["kod"])
                continue
            if self._butce_doldu():
                # SESSIZ KESME YOK: kesilen seri ADIYLA raporlanir.
                # Aylik veri ve haftalik kosu oldugu icin bir tur
                # kacirmak kayip degil, gecikmedir — ama gorunmez
                # olursa kalici bosluga doner.
                kesildi.append(str(tanim.get("kod")))
                continue
            try:
                toplam += self._seri(tanim)
            except Exception as e:                      # noqa: BLE001
                log.warning("[tuik] %s alinamadi: %s", tanim.get("kod"), e)
                basarisiz.append(f"{tanim.get('kod')} ({type(e).__name__})")
        if atlanan:
            notlar.append(f"taze, atlandi: {', '.join(atlanan)}")
        if basarisiz:
            notlar.append("alinamadi: " + ", ".join(basarisiz))
        if kesildi:
            log.warning("[tuik] sure butcesi (%.0f sn) doldu — %d seri "
                        "siradaki kosuya birakildi", butce_sn, len(kesildi))
            notlar.append(f"sure butcesi ({butce_sn:.0f} sn) doldu, "
                          f"siradaki kosuya: {', '.join(kesildi)}")

        durum = ("ok" if not basarisiz and not kesildi
                 else ("partial" if toplam else "error"))
        return CollectorResult(self.name, durum, toplam, " · ".join(notlar) or None)

    # ------------------------------------------------------------------
    def _kalan_sn(self) -> float:
        """Butceden kalan saniye; butce kapaliysa sonsuz."""
        bitis = getattr(self, "_butce_bitis", None)
        return float("inf") if bitis is None else bitis - time.monotonic()

    def _butce_doldu(self) -> bool:
        return self._kalan_sn() <= 0

    # ------------------------------------------------------------------
    def _bearer(self) -> str:
        """
        Access token. OMRU 300 SANIYE oldugu icin onbellek ZORUNLU ama
        uzun kosularda da yenilenmeli — katalog + uc seri cekimi tek
        token'in omrunu asabiliyor. 30 sn emniyet payi birakiliyor.
        """
        simdi = time.monotonic()
        if self._token and self._token[1] - 30 > simdi:
            return self._token[0]
        # Token istegi de butceye tabi: aksi halde ust sinir her
        # yenilemede 30 sn asilirdi (bkz. `_get` gerekcesi).
        r = httpx.post(TOKEN_URL, timeout=max(1.0, min(30.0, self._kalan_sn())),
                       headers={"Content-Type": "application/x-www-form-urlencoded"},
                       data={"grant_type": "password", "client_id": CLIENT_ID,
                             "api_key": self._api_key})
        r.raise_for_status()
        d = r.json()
        self._token = (d["access_token"], simdi + float(d.get("expires_in", 300)))
        return self._token[0]

    def _get(self, yol: str, timeout: float | None = None) -> httpx.Response:
        """
        SDMX cagrisi. SUNUCU YAVAS: tam gecmisli seri sorgulari sahada
        180 sn'yi asti (ReadTimeout). Zaman asimi ayarlanabilir ve BIR
        KEZ yeniden deneniyor — kalici bir ariza ile gecici yavasligi
        ayirmanin en ucuz yolu.

        ZAMAN ASIMI KALAN BUTCEYE KISILIR. Aksi halde butce kontrolu
        ISTEKLER ARASINDA kalirdi ve tek bir asili istek butceyi
        `2 x timeout_sn` kadar asardi — 2026-08-19'da tam olarak bu oldu,
        yalnizca disaridaki sinir 45 dakikaydi. Ust sinirin anlamli
        olmasi icin bekleyen isteğin de o sinira uymasi gerekir.
        """
        sure = timeout or float(self.s.get("sources.tuik.timeout_sn", 90))
        son_hata: Exception | None = None
        for deneme in (1, 2):
            kalan = self._kalan_sn()
            if kalan <= 0:
                log.warning("[tuik] butce doldu, istek yapilmadi: %s", yol[:70])
                break
            try:
                return httpx.get(
                    f"{SDMX}{yol}",
                    headers={"Authorization": f"Bearer {self._bearer()}"},
                    timeout=min(sure, kalan))
            except httpx.TimeoutException as e:
                son_hata = e
                log.warning("[tuik] zaman asimi (deneme %d/2): %s", deneme, yol[:70])
        if son_hata is None:
            # Butce istekten ONCE doldu: bu bir zaman asimi DEGIL, bir
            # butce karari. Ayri bir tur olarak bildiriliyor ki
            # "TUIK yavas" ile "biz beklemeyi kestik" karismasin.
            raise TimeoutError(
                f"tuik: sure butcesi doldu, istek yapilmadi ({yol[:70]})")
        raise son_hata

    # ------------------------------------------------------------------
    def _katalog(self) -> int:
        """
        408 veri akisinin katalogu. Yeni seri eklemek icin gereken TEK
        sey bu tablo: hangi akis ne iceriyor, hangi DSD'ye bagli.
        """
        r = self._get("/dataflow/TR/all/latest?detail=full")
        r.raise_for_status()
        satir = []
        for blok in re.findall(r"(<structure:Dataflow id=.*?</structure:Dataflow>)",
                               r.text, re.S):
            kimlik = re.search(r'id="([^"]+)"[^>]*version="([^"]+)"', blok)
            if not kimlik:
                continue
            satir.append({
                "id": kimlik.group(1), "surum": kimlik.group(2),
                "ad_tr": _al(blok, r'<common:Name xml:lang="tr">([^<]*)<'),
                "ad_en": _al(blok, r'<common:Name xml:lang="en">([^<]*)<'),
                "aciklama": _al(blok, r'<common:Description xml:lang="tr">([^<]*)<'),
                "dsd": _al(blok, r'<Ref id="([^"]+)"[^>]*class="DataStructure"'),
            })
        if not satir:
            raise RuntimeError("dataflow ayristirilamadi (sema degismis olabilir)")
        with self.db.tx() as c:
            c.executemany(
                """INSERT INTO tuik_dataflow (id, surum, ad_tr, ad_en, aciklama, dsd)
                   VALUES (:id, :surum, :ad_tr, :ad_en, :aciklama, :dsd)
                   ON CONFLICT(id) DO UPDATE SET
                     surum=excluded.surum, ad_tr=excluded.ad_tr,
                     ad_en=excluded.ad_en, aciklama=excluded.aciklama,
                     dsd=excluded.dsd, guncelleme=datetime('now')""", satir)
        return len(satir)

    # ------------------------------------------------------------------
    def _boyutlar(self, dataflow: str) -> list[str]:
        """Dataflow -> DSD -> boyut adlari, POZISYON SIRASINDA."""
        if dataflow in self._dsd_onbellek:
            return self._dsd_onbellek[dataflow]
        r = self._get(f"/dataflow/TR/{dataflow}/latest")
        r.raise_for_status()
        ref = re.search(r'<Ref id="([^"]+)" version="([^"]+)"[^>]*'
                        r'class="DataStructure"', r.text)
        if not ref:
            raise RuntimeError(f"{dataflow}: DSD referansi yok")
        d = self._get(f"/datastructure/TR/{ref.group(1)}/{ref.group(2)}")
        d.raise_for_status()
        cift = re.findall(r'<structure:Dimension id="([^"]+)" position="(\d+)"', d.text)
        if not cift:
            raise RuntimeError(f"{dataflow}: boyut bulunamadi")
        sirali = [ad for ad, _ in sorted(cift, key=lambda x: int(x[1]))]
        self._dsd_onbellek[dataflow] = sirali
        return sirali

    @staticmethod
    def _anahtar(boyutlar: list[str], sec: dict) -> str:
        """
        SDMX seri anahtari: boyutlar POZISYON sirasinda, nokta ile ayrik;
        bos konum "hepsi" demek.

        ELLE YAZILMIYOR. Yi-UFE'de 11 boyut var ve bir noktayi eksik
        yazmak 404 uretiyor — sahada tam bu oldu. Config yalnizca
        "hangi boyut hangi deger" diyor, sira DSD'den geliyor.
        """
        bilinmeyen = set(sec) - set(boyutlar)
        if bilinmeyen:
            raise RuntimeError(f"DSD'de olmayan boyut: {sorted(bilinmeyen)}")
        return ".".join(str(sec.get(b, "")) for b in boyutlar)

    def _son_guncelleme(self, kod: str) -> str:
        """
        Serinin en son ne zaman yazildigi — hic yazilmamissa BOS METIN.

        Bos metin siralamada en basa gecer, yani HIC CEKILMEMIS seri her
        zaman once gelir. Dogru oncelik bu: bir kez bile veri gelmemis
        seri, bir gun bayatlamis seriden daha acildir.
        """
        r = self.db.query(
            "SELECT MAX(guncelleme) g FROM makro_seri WHERE kod = ?", (kod,))
        return (r[0]["g"] if r and r[0]["g"] else "") or ""

    def _taze_mi(self, kod: str, saat: float) -> bool:
        """
        Seri yakin zamanda cekildiyse ATLA.

        TUIK AYLIK yayin yapiyor ama servis ARTIMLI CEKIM DESTEKLEMIYOR:
        her sorgu tam gecmisi donduruyor (Yi-UFE'de 535 gozlem, 1982'den
        beri) ve sunucu yavas — olculdu, uc seri 10 dakikadan uzun surdu.
        Gunluk nabzin butcesi 45 dakika ve bunun 10'unu ayda bir degisen
        bir veri icin harcamak yanlis. Ayni gerekce `midasbilanco`nun
        donusumlu calismasinda da gecerliydi.
        """
        if saat <= 0:
            return False
        r = self.db.query(
            """SELECT 1 FROM makro_seri
               WHERE kod = ? AND guncelleme > datetime('now', ?) LIMIT 1""",
            (kod, f"-{saat} hours"))
        return bool(r)

    def _tam_anahtar(self, dataflow: str, sec: dict) -> str:
        """
        Seri anahtarini KESFEDEREK kurar: once son gozlemle tum seriler
        cekilir, `sec` ile eslesen serinin TUM boyut degerleri alinir.

        NEDEN KISMI ANAHTAR YETMIYOR: TUIK'in NSI uygulamasi bos
        birakilan boyutlari kabul etmiyor — olculdu, `...8.Y_GE15.S._T..`
        anahtari "NoRecordsFound" donduruyor. Eksik boyutlari config'e
        yazmak da cozum degil: `YAYIM_DONEMI` gibi alanlar her yayim
        turunda degisiyor ve elle yazilan deger sessizce bayatlar —
        bu projenin tekrar eden kusur sinifi. Kesif her kosuda guncel
        degeri buluyor.
        """
        boyutlar = self._boyutlar(dataflow)
        bilinmeyen = set(sec) - set(boyutlar)
        if bilinmeyen:
            raise RuntimeError(f"DSD'de olmayan boyut: {sorted(bilinmeyen)}")

        r = self._get(f"/data/{dataflow}/all?lastNObservations=1")
        r.raise_for_status()
        eslesen = []
        for blok in re.findall(r"(<generic:SeriesKey>.*?</generic:SeriesKey>)",
                               r.text, re.S):
            kv = dict(re.findall(r'id="([^"]+)" value="([^"]+)"', blok))
            if all(str(kv.get(b)) == str(v) for b, v in sec.items()):
                eslesen.append(kv)
        if not eslesen:
            raise RuntimeError(f"secimle eslesen seri yok: {sec}")
        # TEK SERI BEKLENIYOR. Birden fazlasi secimin eksik oldugunu
        # gosterir ve sessizce ilkini almak, farkli boyutlardan gelen
        # sayilari tek seri gibi sunmak olurdu.
        if len(eslesen) > 1:
            fark = {b for b in boyutlar
                    if len({k.get(b) for k in eslesen}) > 1}
            raise RuntimeError(f"{len(eslesen)} seri eslesti, secim eksik — "
                               f"ayrisan boyutlar: {sorted(fark)}")
        return ".".join(str(eslesen[0].get(b, "")) for b in boyutlar)

    def _seri(self, tanim: dict) -> int:
        kod = tanim["kod"]
        anahtar = self._tam_anahtar(tanim["dataflow"], tanim.get("sec") or {})
        # GOZLEM SAYISI SINIRLANIYOR. Sinirsiz gecmis sorgusu sunucuyu
        # boguyor: ekonomik guven endeksinde tam gecmis 90 sn'de bile
        # donmedi, `lastNObservations=240` ile 1,7 SANIYEDE dondu (235
        # gozlem). Gunluk bir rapor icin 20 yillik aylik seri fazlasiyla
        # yeterli; 1982'ye kadar inmek ne okunuyor ne de gerekiyor.
        n = int(tanim.get("son_gozlem")
                or self.s.get("sources.tuik.son_gozlem", 240))
        r = self._get(f"/data/{tanim['dataflow']}/{anahtar}?lastNObservations={n}")
        if r.status_code == 404:                    # NoRecordsFound
            raise RuntimeError(f"kayit yok (anahtar: {anahtar})")
        r.raise_for_status()

        gozlem = _OBS.findall(r.text)
        if not gozlem:
            raise RuntimeError("gozlem ayristirilamadi")

        satir = [{"kod": kod, "donem": d, "deger": float(v), "kaynak": "tuik"}
                 for d, v in gozlem]
        with self.db.tx() as c:
            c.executemany(
                """INSERT INTO makro_seri (kod, donem, deger, kaynak)
                   VALUES (:kod, :donem, :deger, :kaynak)
                   ON CONFLICT(kod, donem, kaynak) DO UPDATE SET
                     deger=excluded.deger, guncelleme=datetime('now')""", satir)
        # SIRALAMA DATAFLOW'A GORE DEGISIYOR (biri artan, digeri azalan
        # donduruyor), o yuzden uc degerler min/max ile aliniyor.
        donem = [x["donem"] for x in satir]
        log.info("[tuik] %s: %d gozlem (%s -> %s)", kod, len(satir),
                 min(donem), max(donem))
        return len(satir)


def _al(metin: str, kalip: str) -> str | None:
    m = re.search(kalip, metin, re.S)
    return m.group(1).strip() if m else None


def son_gostergeler(db, settings, n: int = 13) -> list[dict]:
    """
    Rapora giden makro gostergeler: son deger + bir onceki + yil onceki.

    Seviye TEK BASINA az sey soyler; "issizlik %7,6" cumlesi ancak
    "gecen ay %7,8, gecen yil %8,4" ile birlikte bir sey ifade eder.
    """
    out = []
    for tanim in (settings.get("sources.tuik.seriler") or []):
        satir = db.query(
            """SELECT donem, deger FROM makro_seri
               WHERE kod = ? ORDER BY donem DESC LIMIT ?""", (tanim["kod"], n))
        if not satir:
            continue
        son = satir[0]
        onceki = satir[1] if len(satir) > 1 else None
        yil_once = satir[12] if len(satir) > 12 else None
        out.append({
            "kod": tanim["kod"], "ad": tanim.get("ad", tanim["kod"]),
            "birim": tanim.get("birim"),
            "donem": son["donem"], "deger": round(son["deger"], 2),
            "onceki_donem_deger": round(onceki["deger"], 2) if onceki else None,
            "bir_yil_once": round(yil_once["deger"], 2) if yil_once else None,
            "not": tanim.get("not"),
        })
    return out
