"""
BILANCO TAKVIMI — hangi sirket bilancosunu NE ZAMAN aciklayacak.

NEDEN
-----
Donchian stop'u fiyat stop'tan GECERKEN korur, stop'un ALTINDA
ACILIRSA korumaz. Tek bir hissenin en buyuk acilis bosluklari kendi
bilanco gunlerinde olur ve bu gunler haftalar onceden ilan edilir. Yani
"ne olacak" bilinmez ama "ne zaman belirsizlik gelecek" bilinir. Bu
modul o bilgiyi toplar; ne yapilacagina `analysis/olay_takvimi.py` ve
takvim sinavi (`docs/takvim-filtresi.md`) karar verir.

KAYNAKLAR — OLCULDU 2026-09-24, belgeye guvenilmedi
---------------------------------------------------
Alpha Vantage `EARNINGS_CALENDAR`   TEK cagri, ~4.500 ABD sirketi, 3 ay
                                    ileri. Donchian evreninin 518'inin
                                    464'u (%89,6). Saat alani %97 BOS.
Yahoo `get_earnings_dates`          sembol basina bir cagri, ~25 yil
                                    geriye + ileri, SAATIYLE (ABD Dogu).
SEC 8-K Madde 2.02                  kademe 1, gerceklesen aciklama.
                                    BU OTURUMDA ALINAMADI: agdaki Zyxel
                                    guvenlik duvari `.gov` DNS'ini
                                    yonlendiriyordu. Acik madde, bkz. belge.

AV'de eksik kalan 54 sembolun bilinenleri (CRM, CRWD, DELL, AVGO...)
son haftalarda bilanco acikladi; siradaki tarihleri HENUZ ilan
edilmedi. Kapsam deligi degil, takvimin dogasi: sirket tarihi ~4-6 hafta
once duyurur.

ESLESTIRME — TICKER + AD, OLCULDU
---------------------------------
AV sembolunu katalogla eslestirmek bu deponun bilinen tuzagi (AVTX:
ticker "Avantium" yerine "Avalo Therapeutics"e gidiyordu). Canli
katalogda olculdu (2026-09-24):

    USD kotasyonu  457 ad tutuyor · 5 tutmuyor — besi de AYNI sirket
                   (AMTEK/Ametek, FLEETCOR->Corpay ad degisikligi,
                   J P MORGAN/JPMorgan, CONOCO PHILLIPS, Estee aksani)
    EUR kotasyonu    7 ad tutuyor · 1 tutmuyor — AVTX, YANLIS sirket

Kural buradan: USD kotasyonunda ABD ticker'i o an tekildir, ad
uyusmazligi LOGA yazilir ama eslesme kabul edilir; USD DISI kotasyonda
ad ESLESMESI SART. Tek kural hepsine uygulansaydi ya AVTX gecerdi ya da
JPM duserdi.

YANLIS "YOK" BEYANI YOK
-----------------------
AV kota dolunca HTTP 200 + JSON "Information" doner, CSV degil. Bunu
"takvim bos" saymak en kotu hata sinifi (`yanlis-yok-beyani`); acikca
hataya cevriliyor. Basligi degisen CSV de ayni sekilde GURULTULU reddedilir.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import time
from datetime import datetime, timezone

from ..analysis.olay_takvimi import zaman_sinifi
from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

AV_URL = "https://www.alphavantage.co/query"
AV_BASLIK = ("symbol", "name", "reportDate", "fiscalDateEnding",
             "timeOfTheDay")
# AV'nin kendi etiketi -> `olay_takvimi.zaman_sinifi` ile ayni sozluk.
AV_ZAMAN = {"pre-market": "once", "post-market": "sonra"}

# Yahoo istekleri arasi bekleme. Portfoy ~25 sembol; hiz degil nezaket.
YAHOO_ARASI_SN = 0.6


class TakvimKaynakHatasi(RuntimeError):
    """Kaynak veri yerine hata/kota mesaji dondurdu — "bos" DEGIL."""


def sembol_anahtari(s: str | None) -> str:
    """Eslestirme anahtari: buyuk harf, sinif ayraci '.' ('BRK-B' -> 'BRK.B')."""
    return (s or "").strip().upper().replace("-", ".")


def av_ayristir(metin: str) -> list[dict]:
    """
    AV EARNINGS_CALENDAR CSV'si -> kayitlar. SAF.

    Hata yollari BILEREK gurultulu: JSON govde (kota/anahtar hatasi) ve
    beklenen kolonlari tasimayan baslik `TakvimKaynakHatasi` atar. Bos
    liste YALNIZCA baslik dogru ve satir yoksa doner.
    """
    govde = (metin or "").lstrip()
    if govde.startswith("{"):
        raise TakvimKaynakHatasi(f"AV veri yerine mesaj dondurdu: {govde[:160]}")
    okuyucu = csv.DictReader(io.StringIO(govde))
    eksik = [k for k in AV_BASLIK if k not in (okuyucu.fieldnames or [])]
    if eksik:
        raise TakvimKaynakHatasi(
            f"AV CSV basligi degismis, eksik kolon: {eksik} "
            f"(gelen: {okuyucu.fieldnames})")
    out = []
    for r in okuyucu:
        tarih = (r.get("reportDate") or "").strip()[:10]
        sembol = sembol_anahtari(r.get("symbol"))
        if len(tarih) != 10 or not sembol:
            continue
        out.append({"sembol": sembol, "ad": (r.get("name") or "").strip(),
                    "tarih": tarih,
                    "donem": (r.get("fiscalDateEnding") or "").strip()[:10] or None,
                    "saat": None,
                    "zaman": AV_ZAMAN.get((r.get("timeOfTheDay") or "").strip())})
    return out


def eslestir(kayitlar: list[dict], katalog: list[dict]) -> tuple[list[dict], dict]:
    """
    Takvim kayitlarini katalog enstrumanlarina bagla. SAF.

    `katalog`: {id, symbol, name, currency} sozlukleri.
    Doner: (instrument_id eklenmis kayitlar, sayac).

    Kural ve gerekcesi modul basliginda (ESLESTIRME). Burada tekrar
    yazilmiyor ki ikisi ayrismasin.
    """
    from ..research.identity import ayni_sirket

    harita: dict[str, list[dict]] = {}
    for e in katalog:
        harita.setdefault(sembol_anahtari(e.get("symbol")), []).append(e)
    out, sayac = [], {"kayit": len(kayitlar), "eslesen": 0,
                      "ad_uyusmaz_usd": [], "reddedilen": []}
    for k in kayitlar:
        for e in harita.get(k["sembol"], []):
            usd = (e.get("currency") or "").upper() == "USD"
            ad_tutuyor = ayni_sirket(e.get("name"), k.get("ad"))
            if not ad_tutuyor and not usd:
                sayac["reddedilen"].append(
                    f"{e.get('symbol')}: katalog '{e.get('name')}' != "
                    f"takvim '{k.get('ad')}'")
                continue
            if not ad_tutuyor:
                sayac["ad_uyusmaz_usd"].append(e.get("symbol"))
            out.append({**k, "instrument_id": e["id"]})
            sayac["eslesen"] += 1
    return out, sayac


def yahoo_ayristir(zamanlar) -> list[dict]:
    """
    Yahoo `get_earnings_dates` indeksi (tz-aware, ABD Dogu) -> kayitlar. SAF.

    Saat ABD DOGU saatiyle yaziliyor; indeks baska bir saat dilimindeyse
    once oraya cevriliyor. Saat dilimi TASIMAYAN zaman damgasi REDDEDILIR:
    naif bir 16:00'in hangi saat dilimi oldugunu bilmeden seans oncesi mi
    sonrasi mi dendigini soyleyemeyiz.
    """
    from zoneinfo import ZoneInfo
    abd = ZoneInfo("America/New_York")
    out, gorulen = [], set()
    for t in zamanlar:
        if getattr(t, "tzinfo", None) is None:
            continue
        yerel = t.astimezone(abd)
        tarih = yerel.strftime("%Y-%m-%d")
        if tarih in gorulen:
            continue
        gorulen.add(tarih)
        saat = yerel.strftime("%H:%M")
        out.append({"tarih": tarih, "saat": saat,
                    "zaman": zaman_sinifi(saat), "donem": None})
    return out


def yahoo_sembolu(sembol: str) -> str:
    """
    Katalog sembolu -> Yahoo sembolu. SAF.

    YALNIZCA tek harfli SINIF soneki tireye doner ('BRK.B' -> 'BRK-B').
    Borsa soneki KORUNUR ('ASML.AS' -> 'ASML.AS'): ayrim yapmadan her noktayi
    tireye cevirmek Avrupa kotasyonlarini var olmayan bir sembole
    goturur ve Yahoo "veri yok" der — yanlis bir "bilanco yok" beyani.
    """
    s = (sembol or "").strip().upper()
    kok, nokta, sonek = s.rpartition(".")
    if nokta and len(sonek) == 1 and sonek.isalpha():
        return f"{kok}-{sonek}"
    return s


def yahoo_adayi(sembol: str, para_birimi: str | None,
                sonekler: dict | None) -> str | None:
    """
    Katalog enstrumani -> sorulacak TEK Yahoo sembolu. SAF.

    USD: yalin sembol (ABD listesi).
    USD DISI: YALNIZCA borsa sonekli sembol ('ADYEN' EUR -> 'ADYEN.AS').
    Yalin sembole ASLA dusulmez: bilanco tarihi sirket adi TASIMIYOR, yani
    yanlis sirkete gidildigi DOGRULANAMAZ. Olculmus ornek: EUR'daki AVTX
    (Avantium) yalin sorulursa Yahoo Avalo Therapeutics'in gunlerini
    dondurur. Sonekli sembolde veri yoksa enstruman "tarih bilinmiyor"
    kalir — yanlis tarihten iyidir.
    Sonek haritasi `sources.prices.borsa_sonekleri` (fiyat collector'u
    ile TEK kaynak). Sonegi zaten olan sembol ('ASML.AS') oldugu gibi.
    """
    s = (sembol or "").strip().upper()
    if not s or s.startswith("~"):
        return None
    ccy = (para_birimi or "").upper()
    if ccy in ("", "USD"):
        return yahoo_sembolu(s)
    kok, nokta, sonek = s.rpartition(".")
    if nokta and len(sonek) > 1:
        return s                                   # borsa soneki zaten var
    sonek = (sonekler or {}).get(ccy)
    return f"{s}{sonek}" if sonek else None


def yahoo_gecmis(sembol: str, limit: int = 100) -> list[dict]:
    """
    Tek Yahoo sembolunun bilanco gunleri (gecmis + ilan edilmis ileri).

    `sembol` Yahoo sembolu olarak verilir; katalogdan cevirim
    `yahoo_adayi`nin isi. Ag/kaynak hatasi YUTULMAZ, cagirana gider:
    "bu sembolde bilanco yok" ile "istek dustu" ayri seyler.
    """
    import yfinance as yf
    df = yf.Ticker(sembol).get_earnings_dates(limit=limit)
    if df is None or len(df) == 0:
        return []
    return yahoo_ayristir(list(df.index))


def yaz(db, satirlar: list[dict], kaynak: str) -> int:
    """
    UPSERT. `ilk_gorulme` ILK yazimda kalir, `son_gorulme` her gorulmede
    tazelenir — kaydirilan tarih silinmez, son_gorulme'si durur.

    Saat/zaman YALNIZCA dolu gelirse ustune yazilir: AV saati cogu kayitta
    bos veriyor ve Yahoo'dan gelmis bir saati NULL ile ezmek bilgi kaybi
    olurdu. (Ayri kaynak satirlari zaten ayri; bu, ayni kaynagin iki
    kosusu arasindaki durum.)
    """
    if not satirlar:
        return 0
    with db.tx() as c:
        c.executemany(
            """INSERT INTO bilanco_takvimi
                 (instrument_id, tarih, kaynak, saat, zaman, donem)
               VALUES (:instrument_id, :tarih, :kaynak, :saat, :zaman, :donem)
               ON CONFLICT(instrument_id, tarih, kaynak) DO UPDATE SET
                 saat  = COALESCE(excluded.saat, bilanco_takvimi.saat),
                 zaman = COALESCE(excluded.zaman, bilanco_takvimi.zaman),
                 donem = COALESCE(excluded.donem, bilanco_takvimi.donem),
                 son_gorulme = datetime('now')""",
            [{"instrument_id": s["instrument_id"], "tarih": s["tarih"],
              "kaynak": kaynak, "saat": s.get("saat"),
              "zaman": s.get("zaman"), "donem": s.get("donem")}
             for s in satirlar])
    return len(satirlar)


BILANCO_DISI_TURLER = ("etf", "cash", "crypto")


def abd_katalogu(db) -> list[dict]:
    """
    Bilanco takviminin eslesebilecegi enstrumanlar.

    `venue='BUX'` araci kurum venue'su ve ABD hisselerini de Avrupa
    kotasyonlarini da tasiyor; ayrim eslestirmedeki para birimi kapisinda.
    Fon/nakit/kripto bilanco aciklamaz, disarida.
    """
    yer = ",".join("?" * len(BILANCO_DISI_TURLER))
    return [dict(r) for r in db.query(
        f"""SELECT id, symbol, name, currency FROM instruments
            WHERE venue = 'BUX'
              AND COALESCE(asset_type, '') NOT IN ({yer})""",
        BILANCO_DISI_TURLER)]


def portfoy_enstrumanlari(db, sahipler: list[str]) -> list[dict]:
    """
    Sahiplerin guncel pozisyonlarindan bilanco aciklayabilecek olanlar.

    Pozisyon okuma `db.sahip_pozisyon_idleri`nden — "sahibin guncel
    enstrumanlari" sorusunun TEK cevabi orada; burada ikinci bir sorgu
    yazmak iki cevabin ayrismasina davetiye olurdu. Suzgec
    `abd_katalogu` ile AYNI: BIST (Midas) ve kripto (Binance) bu
    kaynaklarin kapsaminda degil. BIST bilanco takvimi KAP'tan gelmeli
    ve henuz YOK — `collect` notu bunu soyluyor.
    """
    from ..research.identity import fon_mu

    ids: set[int] = set()
    for sahip in sahipler:
        ids |= db.sahip_pozisyon_idleri(sahip)
    # FONLAR AD UZERINDEN DE ELENIR: katalogda `asset_type` cogu ETC/ETF
    # icin BOS (olculdu 2026-09-24: 4GLD.DE, CNDX, GOLD.AS, VUSA). Elenmeseler
    # "tarih bilinmiyor" diye gorunurlerdi — oysa bilanco hic aciklamazlar.
    return [{"id": r["id"], "symbol": r["symbol"], "name": r["name"],
             "currency": r["currency"]}
            for r in db.enstrumanlar_by_id(ids)
            if r["venue"] == "BUX"
            and (r["asset_type"] or "") not in BILANCO_DISI_TURLER
            and not fon_mu(r["name"], r["asset_type"])]


class BilancoTakvimCollector(BaseCollector):
    """
    Iki adim, ikisi de bagimsiz basarisiz olabilir ve AYRI raporlanir:

    1. AV takvimi — tum katalog, tek cagri (kota `alphavantage` ile ORTAK).
    2. Yahoo — yalnizca PORTFOY; saat bilgisi ve AV'nin kapsamadigi
       tarihler icin. Butun evren icin gecmis doldurma ayri betik
       (`scripts/bilanco_gecmisi.py`); gunluk kosuda 518 Yahoo cagrisi
       nabiz butcesini yerdi.
    """
    name = "bilancotakvim"
    needs_browser = False

    def collect(self) -> CollectorResult:
        notlar: list[str] = []
        yazilan = 0
        av_tamam = False

        try:
            n, not_ = self._av()
            yazilan += n
            av_tamam = True
            notlar.append(f"alphavantage: {n} ({not_})")
        except Exception as e:                            # noqa: BLE001
            log.warning("[bilancotakvim] AV takvimi alinamadi: %s", e)
            notlar.append(f"alphavantage: HATA {e}")

        try:
            n, not_ = self._portfoy_yahoo()
            yazilan += n
            notlar.append(f"yahoo(portfoy): {n}" + (f" ({not_})" if not_ else ""))
        except Exception as e:                            # noqa: BLE001
            log.warning("[bilancotakvim] Yahoo portfoy adimi dustu: %s", e)
            notlar.append(f"yahoo(portfoy): HATA {e}")

        notlar.append("kapsam: yalnizca ABD sirketleri; BIST bilanco "
                      "takvimi (KAP) YOK")
        durum = "ok" if av_tamam and yazilan else ("partial" if yazilan else "error")
        return CollectorResult(self.name, durum, yazilan, " · ".join(notlar))

    # ------------------------------------------------------------------
    def _av(self) -> tuple[int, str]:
        import httpx

        anahtar = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
        if not anahtar:
            raise TakvimKaynakHatasi("ALPHA_VANTAGE_API_KEY tanimli degil (.env)")
        gun = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        butce = int(self.s.get("sources.alphavantage.daily_budget", 20))
        kullanilan = self.db.api_kota_oku("alphavantage", gun)
        if kullanilan >= butce:
            raise TakvimKaynakHatasi(
                f"AV gunluk kota doldu ({kullanilan}/{butce}) — takvim "
                "TAZELENMEDI, eldeki son takvim gecerli")
        # Sayac ISTEKTEN ONCE artar: AV hata donen istegi de sayiyor
        # (`alphavantage._cagir` ile ayni gerekce).
        self.db.api_kota_ekle("alphavantage", gun, 1)
        ufuk = str(self.s.get("sources.bilancotakvim.ufuk", "3month"))
        r = httpx.get(AV_URL, params={"function": "EARNINGS_CALENDAR",
                                      "horizon": ufuk, "apikey": anahtar},
                      timeout=45, follow_redirects=True)
        r.raise_for_status()
        kayitlar = av_ayristir(r.text)
        eslesen, sayac = eslestir(kayitlar, abd_katalogu(self.db))
        if sayac["reddedilen"]:
            log.warning("[bilancotakvim] ad kapisinda reddedilen: %s",
                        "; ".join(sayac["reddedilen"][:10]))
        if sayac["ad_uyusmaz_usd"]:
            log.info("[bilancotakvim] USD, ticker ile kabul, ad farkli: %s",
                     ", ".join(sayac["ad_uyusmaz_usd"][:20]))
        n = yaz(self.db, eslesen, "alphavantage")
        return n, (f"takvim {sayac['kayit']} sirket, katalogla eslesen "
                   f"{sayac['eslesen']}, ad kapisinda reddedilen "
                   f"{len(sayac['reddedilen'])}")

    def _portfoy_yahoo(self) -> tuple[int, str | None]:
        sahipler = list(self.s.sahip_listesi or [])
        hedefler = portfoy_enstrumanlari(self.db, sahipler)
        sonekler = self.s.get("sources.prices.borsa_sonekleri") or {}
        toplam, dusen = 0, []
        for i, h in enumerate(hedefler):
            aday = yahoo_adayi(h["symbol"], h.get("currency"), sonekler)
            if not aday:
                continue
            if i:
                time.sleep(YAHOO_ARASI_SN)
            try:
                satirlar = yahoo_gecmis(aday, limit=12)
            except Exception as e:                        # noqa: BLE001
                dusen.append(f"{h['symbol']}: {type(e).__name__}")
                continue
            toplam += yaz(self.db, [{**s, "instrument_id": h["id"]}
                                    for s in satirlar], "yahoo")
        not_ = None
        if dusen:
            not_ = f"{len(dusen)}/{len(hedefler)} sembol dustu: " + ", ".join(dusen[:6])
        return toplam, not_


# Bir takvim satirinin "hala gecerli" sayilmasi icin en son ne zaman
# gorulmus olmasi gerektigi. Sirket tarihi kaydirinca eski satir silinmez,
# `son_gorulme`si durur; bu pencere o bayat satiri ileri takvimden eler.
# 3 gun: hafta sonu (Cuma nabzi -> Pazartesi) + bir kosu kaybi.
TAZELIK_GUN = 3


def yaklasan_bilancolar(db, sahip: str, gun: int = 30) -> dict:
    """
    Sahibin portfoyundeki ABD hisselerinin onumuzdeki `gun` gundeki
    bilancolari — panel paketi ve sohbet araci icin.

    UC AYRIM BEYAN EDILIR, cunku ucu de farkli sey soyluyor:
      * `bilancolar`   tarihi bilinen ve pencereye dusen aciklamalar
      * `tarih_bilinmiyor` pozisyonda ama gecerli ileri tarihi OLMAYAN
                        hisseler. "Bilanco yok" DEMEK DEGIL: cogu yeni
                        aciklama yapmis ve siradakini henuz ilan etmemistir.
      * `kapsam_disi`  BIST, kripto, fon/ETC, nakit — bu takvimin
                        kapsami disi (BIST bilancosu KAP'tan gelmeli ve
                        henuz yok; fon ve kripto bilanco aciklamaz).
    Kaynaklar AYRISIRSA (AV 29 Eki, Yahoo 30 Eki) iki tarih de gosterilir;
    birini secmek, digerini sessizce yanlis ilan etmek olurdu.
    """
    from datetime import date, timedelta

    from ..analysis.olay_takvimi import gun_farki

    bugun = date.today().isoformat()
    son = (date.today() + timedelta(days=gun)).isoformat()
    hedefler = portfoy_enstrumanlari(db, [sahip])
    tum_ids = db.sahip_pozisyon_idleri(sahip)
    kapsam_disi = sorted(r["symbol"] for r in db.enstrumanlar_by_id(
        tum_ids - {h["id"] for h in hedefler})
        if (r["asset_type"] or "") not in ("cash",))

    bilancolar, bilinmiyor = [], []
    for h in hedefler:
        satirlar = db.query(
            """SELECT tarih, kaynak, zaman FROM bilanco_takvimi
               WHERE instrument_id = ? AND tarih >= ?
                 AND son_gorulme >= datetime('now', ?)
               ORDER BY tarih""",
            (h["id"], bugun, f"-{TAZELIK_GUN} days"))
        if not satirlar:
            bilinmiyor.append(h["symbol"])
            continue
        ilk = satirlar[0]["tarih"]
        if ilk > son:
            continue
        # Ayni aciklamanin kaynaklara gore tarihleri: ilk tarihe 7 gun
        # icindeki satirlar AYNI aciklamadir (ceyreklik aciklamalar ~90
        # gun arayla; 7 gunluk fark bir sonraki ceyrek olamaz).
        ayni = [r for r in satirlar if gun_farki(ilk, r["tarih"]) <= 7]
        tarihler = sorted({r["tarih"] for r in ayni})
        zamanlar = sorted({r["zaman"] for r in ayni if r["zaman"]})
        from ..ibkr.beklenti import en_gec_tepki
        zaman = zamanlar[0] if len(zamanlar) == 1 else None
        kayit = {
            "sembol": h["symbol"], "ad": h["name"], "tarih": ilk,
            "kalan_gun": gun_farki(bugun, ilk),
            "zaman": zaman,
            "kaynaklar": sorted({r["kaynak"] for r in ayni}),
            "kaynaklar_ayrisiyor": tarihler if len(tarihler) > 1 else None,
            "instrument_id": h["id"],
            "en_gec_tepki": en_gec_tepki(tarihler, zaman),
        }
        # FIYATLANAN HAREKET (IBKR MCP Faz 4) — varsa son olcum. Tahmin
        # degil, opsiyon piyasasinin odedigi beklenti; YON icermez.
        b = db.query(
            """SELECT hareket_pct, vade, veri_durumu, fiyat_kaynagi, olcum_gunu
               FROM bilanco_beklentisi
               WHERE instrument_id = ? AND bilanco_tarih = ?
               ORDER BY olcum_gunu DESC LIMIT 1""", (h["id"], ilk))
        if b:
            kayit.update({"fiyatlanan_hareket_%": b[0]["hareket_pct"],
                          "opsiyon_vadesi": b[0]["vade"],
                          "opsiyon_veri_durumu": b[0]["veri_durumu"],
                          "opsiyon_fiyat_kaynagi": b[0]["fiyat_kaynagi"],
                          "olcum_gunu": b[0]["olcum_gunu"]})
        bilancolar.append(kayit)
    bilancolar.sort(key=lambda x: x["tarih"])
    return {
        "pencere_gun": gun, "bilancolar": bilancolar,
        "tarih_bilinmiyor": sorted(bilinmiyor), "kapsam_disi": kapsam_disi,
        "not": ("Tarih KESIN olgudur, sonuc DEGIL: bilanco gunu buyuk "
                "hareket beklenir ama YONU bilinmez. zaman='once' seans "
                "oncesi (tepki ayni gun), 'sonra' seans sonrasi (tepki "
                "ertesi gun acilista); stop acilis boslugunu KORUMAZ. "
                "`tarih_bilinmiyor` 'bilanco yok' DEMEK DEGILDIR. "
                "`fiyatlanan_hareket_%` opsiyon piyasasinin o bilanco icin "
                "ODEDIGI beklentidir (ATM straddle / fiyat), TAHMIN DEGIL ve "
                "YON ICERMEZ; `opsiyon_veri_durumu` DELAYED/FROZEN ise "
                "kapanis fiyatlarindan hesaplanmistir."),
    }
