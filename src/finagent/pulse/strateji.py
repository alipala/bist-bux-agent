"""
STRATEJI — Donchian 20/10 + 2N kuralinin GUNLUK KARARI. LLM YOK.

Bu modul HESAP YAPMAZ, KARAR VERIR: seviyeleri `pulse.seviye`den alir,
kurali uygular, karari dondurur. Gosterge hesabi tek motordadir
(`analysis.indicators`) ve ikinci bir hesap yolu acilmayacak. Donchian
pencereleri ve STOP_N `analysis.trend_takip`ten ICE AKTARILIR, burada
YENIDEN TANIMLANMAZ — bu deponun en pahali dersi: ayni kural iki kopya
olunca kopyalar ayrisir ve iki FARKLI yanlis cevap uretir.

BAGIMLILIK ICE DOGRUDUR
-----------------------
Ice aktarir : storage.db (kabuk katmaninda), pulse.seviye, config
Ice AKTARMAZ: notify.*, ibkr.*, llm, bot.*
Gerekce: mesaj bicimi, araci kurum ve model saglayicisi degisince alan
mantigi degismemeli. Test bunu AST ile sinar (metin aramasi degil —
`test_MODEL_EMIR_GONDEREMEZ` dersinde kaba metin aramasi mesru okuma
araclarina yanlis yere kirmizi olmustu).

SAF CEKIRDEK / KABUK AYRIMI
---------------------------
`kirilim_mi`, `red_sebebi`, `karar`, `secim` SAFTIR: db yok, saat yok,
rastgele global durum yok. Tum girdi parametrede. Boylece 300 satirlik
sentetik seriyle, veritabani olmadan test edilebiliyorlar.
`tara` kabuktur: db okur, saf cekirdegi cagirir.

`tara` SOZLESMESI BELGEDEN AYRILIYOR — BILEREK
----------------------------------------------
`docs/finagent-strateji-motoru.md` Adim 2'de `tara(...) -> list[dict]`
yaziyor. Burada SOZLUK donuyor: `{"gorusler", "sayaclar", "taranan"}`.
Sebep ayni belgenin Adim 3 kabul olcutu: mesajda "Taranamayan: 8 sembol
(yetersiz bar: 5, seri yok: 3)" satiri VAR. Duz bir liste o sayilari
tasiyamaz ve sayilari cagiran tarafta yeniden turetmek, kurali ikinci
kez yazmak olurdu. Sessizce dusen sayac bu deponun tekrar eden kusur
sinifi ("kirpmak makul, kirpildigini GIZLEMEK degil"), o yuzden sayac
donus degerinin PARCASI.
"""
from __future__ import annotations

import logging
import random

from ..analysis.trend_takip import CIKIS_PENCERE, GIRIS_PENCERE, STOP_N
from . import seviye as _seviye

log = logging.getLogger(__name__)

# Bu modul kuralin KENDI sabitlerini TANIMLAMAZ; yukarida ice aktarilan
# `GIRIS_PENCERE`/`CIKIS_PENCERE`/`STOP_N` tek kaynaktir. Burada
# yalnizca gorus sozlesmesinin sabit alanlari var.
AJAN = "strateji"
AJAN_SECILEN = "strateji_secilen"

# FREN — KARNE KOTUYSE TAVAN DUSER. `pulse.taktikci` ile AYNI KALIP ve
# ayni gerekce: buna karar veren sey kanaat degil, DEFTERDEKI SAYI.
#
# Esikler taktikci'den KOPYALANMIYOR, kendi degerleri var: taktik gun
# ici ve ufku kisa, strateji 14 barlik ufukla calisiyor ve isabeti
# YAPISAL OLARAK DUSUK — olculdu, %32 (belge §11). Trend takibinde on
# islemin yedisi zarar eder ve kar kuyruktan gelir; %50 esigi bu
# katmanda freni SUREKLI cekili tutardi, yani calisan bir kurali
# susturmus olurduk.
FREN_TAVANI = 1

# Frenin devreye girmesi icin gereken en az OLCUM. 20, `Defter.yeterli_mi`
# ve `taktikci.FREN_ASGARI_OLCUM` ile AYNI: altinda Wilson araligi o
# kadar genis ki "%25" ile "%45" ayirt edilemiyor.
FREN_ASGARI_OLCUM = 20

# FREN OLCUTU ISABET DEGIL, RASTGELEYE GORE FARK.
#
# Ham isabet esigi koymak bu katmanda YANLIS OLURDU: kural zaten dusuk
# isabetle calisiyor (%32) ve dogru soru "isabet yuksek mi" degil,
# "rastgele girmekten IYI mi". `42ab2fd`in dersi: BIST'te olculen
# kenarin yarisi piyasa suruklemesiydi — kontrol grubu olmadan karne
# kendini kandirir.
#
# 0.0: rastgeleyi GECMIYORSA fren. Esigi pozitif bir sayiya koymak,
# kuraldan rastgeleden belirgin olarak iyi olmasini istemek olurdu ve
# o iddia icin ~24 ay gerekiyor (belge §11).
FREN_FARK_ESIGI = 0.0


def kirilim_mi(sv: dict | None) -> bool:
    """
    `son_kapanis > donchian_giris`. Eksik alanda False.

    SIKI `>`: esitlik kirilim DEGIL. `donchian_giris` onceki 20 kapanisin
    en yuksegi ve bugunun barini DISLIYOR (`seviye.py`), yani esitlik
    "yuksege dokundu" demek, "asti" demek degil.
    """
    if not sv:
        return False
    son, giris = sv.get("son_kapanis"), sv.get("donchian_giris")
    if son is None or giris is None:
        return False
    try:
        return float(son) > float(giris)
    except (TypeError, ValueError):
        return False


def red_sebebi(sv: dict | None, ayar: dict) -> str | None:
    """
    Kural bu seviyeyle NEDEN calisamaz? Calisabiliyorsa None.

    AYRI BIR FONKSIYON, cunku sebep SAYILACAK. `karar()` None dondurup
    sussaydi, cagiran taraf "kac sembol neden elendi" sorusunu ancak
    kurali IKINCI KEZ yazarak cevaplayabilirdi. Tek tanim, iki kullanim:
    `karar` kapi olarak, `tara` sayac olarak kullanir.

    "Kirilim yok" BURADA DEGIL: o bir red degil, sinyalin olmamasi.
    Kural konusmadigi gun susar (belge §2.6).
    """
    if not sv:
        return "yetersiz bar"

    son = sv.get("son_kapanis")
    if son is None:
        return "kapanis yok"

    # ZATEN POZISYONDA — KURALIN KENDI DURUMUNDAN.
    #
    # OLCULEN KUSUR (2026-08-28): tarama "kapanis > 20G yuksek" diyor
    # ve pozisyon durumunu BILMIYORDU. Bir hisse trende girip 20 gunluk
    # yukseginin ustunde kaldikca HER GUN yeniden sinyal veriyordu.
    # Alti gunde 318 kirilimin yalnizca 49'u (%15) kuralin TAZE
    # girisiydi; 2026-04-10'da 85 kirilimin 4'u.
    #
    # BEDELI: belge §2.2 "~247 sinyal/ay" diyor ve o rakam
    # `islemler()`ten geliyor (pozisyon farkinda). Motor ayda
    # ~750-2500 satir yaziyordu, cogu AYNI ACIK POZISYONUN tekrari —
    # yani defter kurali degil, kuralin tekrarlarini olcuyordu ve
    # karne o tekrarlari BAGIMSIZ GOZLEM sayacakti.
    #
    # DURUM `analysis.trend_takip.acik_pozisyon`dan: ikinci bir
    # giris/cikis mantigi YAZILMIYOR. `tara()` hesaplayip buraya
    # koyuyor (`devir` ile ayni kalip) ki bu fonksiyon SAF kalsin.
    if sv.get("pozisyonda"):
        return "zaten pozisyonda"

    # SERMAYE ISLEMI: seviyeler KISALTILMIS bir segmentten geliyor
    # (`son_kesintisiz`). Bolunme/temettu barinin "getirisi" fiyat
    # hareketi degildir; uzerine kurulan ATR ve Donchian anlamsizdir.
    if sv.get("sermaye_islemi"):
        return "sermaye islemi"

    stop = sv.get("stop_2n")
    if stop is None:
        return "stop_2n yok"
    try:
        if float(stop) >= float(son):
            # Stop girisin ustundeyse pozisyon acildigi anda kapanir;
            # boyle bir "islem" olculemez.
            return "stop girisin ustunde"
    except (TypeError, ValueError):
        return "stop_2n sayi degil"

    # LIKIDITE KAPISI — PARA BIRIMI BASINA. `sources.isyatirim.min_hacim_tl`
    # BURAYA MIRAS ALINMAZ: o esik TL cinsinden ve USD devire uygulanirsa
    # ~45 kat fazla siki olur (belge §9.2).
    ccy = (sv.get("para_birimi") or "").upper()
    para_birimleri = {str(p).upper() for p in (ayar.get("para_birimleri") or [])}
    if para_birimleri and ccy not in para_birimleri:
        return f"para birimi disi ({ccy or '?'})"

    esikler = ayar.get("asgari_devir") or {}
    esik = esikler.get(ccy)
    if esik is not None:
        devir = sv.get("devir")
        if devir is None:
            # OLCULEMEYEN LIKIDITE, YETERLI LIKIDITE DEGILDIR. Bos sonuc
            # yokluk kaniti degil ama burada yon TEK: bilmediginde
            # islem acmamak, bilmediginde acmaktan ucuzdur.
            return "devir olculemedi"
        try:
            if float(devir) < float(esik):
                return "devir esigin altinda"
        except (TypeError, ValueError):
            return "devir sayi degil"
    return None


def karar(sv: dict | None, ayar: dict) -> dict | None:
    """
    Kirilim varsa `journal.kaydet` sozlesmesine uygun gorus dondurur,
    yoksa None.

    SAF: db yok, saat yok, rastgele yok. Tum girdi parametrede.
    """
    if red_sebebi(sv, ayar) is not None:
        return None
    if not kirilim_mi(sv):
        return None

    giris = float(sv["son_kapanis"])
    stop = float(sv["stop_2n"])
    cikis = sv.get("donchian_cikis")
    ufuk = int(ayar.get("ufuk_gun") or 14)

    # GECERSIZLESME KOSULU — GRAMER TUZAGI.
    #
    # `pulse/tez.py` grameri alan-alan karsilastirmasini KABUL ETMIYOR:
    # "close < donchian_cikis" GECERSIZDIR ve `journal._gecerli_kosul`
    # onu reddedip SAYAR. Dogru cozum 2N stop'un SAYISAL degerini
    # yazmak — hem gramere uyar hem semantik olarak dogrudur: 2N stop
    # GIRISTE SABITLENIR ve degismez. Donchian 10 gunluk cikisi ise
    # YUVARLANAN bir seviyedir; o, gunluk kirilim tablosunda ayrica
    # gosterilir, kosul alanina yazilmaz.
    kosul = f"close < {stop:g}"

    # UFUK/KOSUL AYRIMI GEREKCEYE YAZILIYOR — ZORUNLU.
    #
    # Kuralin CIKISI ufka bagli DEGIL (10 gun dip / 2N stop). `ufuk_gun`
    # yalnizca DEFTERIN puanlama penceresidir. Bu ayrim yazilmazsa
    # `[[tahmin-defteri-ve-getiri-gercekligi]]`de gecen "kosullu talimat
    # kosulsuz puanlanamaz" hatasi tekrarlanir.
    gerekce = (
        f"Donchian {GIRIS_PENCERE} gun kirilimi: kapanis {giris:g} > "
        f"{GIRIS_PENCERE} gunluk yuksek {float(sv['donchian_giris']):g}. "
        f"Stop {STOP_N:g}N = {stop:g}. "
        f"Cikis kurali {CIKIS_PENCERE} gun dip"
        + (f" ({float(cikis):g})" if cikis is not None else "")
        + f" ya da stop. ufuk_gun={ufuk} DEFTERIN PUANLAMA PENCERESI; "
        "kuralin cikisi ufka bagli degil, olculen ortalama tutma "
        "suresinden geliyor."
    )

    return {
        "ajan": AJAN,
        "sembol": sv.get("sembol"),
        "yon": "yukari",              # ACIGA SATIS YOK — bkz. trend_takip
        "ufuk_gun": ufuk,
        "guven": 0.5,                 # KURAL guven derecelendirmiyor
        "gerekce": gerekce,
        "tez": (f"{GIRIS_PENCERE} gunluk yuksegin uzerinde kapanis; trend "
                f"takibi girisi. Cikis {CIKIS_PENCERE} gun dip ya da "
                f"{STOP_N:g}N stop."),
        "gecersizlesme_kosulu": kosul,
        "izlenecek_esik": None,
        "tur": "alim",
        "giris": giris,
        "stop": stop,
        "giris_kaynak": "donchian_giris",
        "stop_kaynak": "stop_2n",
    }


def secim(adaylar: list[dict], tavan: int, tohum: int) -> list[dict]:
    """
    Tavani asan gunlerde TOHUMLU RASTGELE alt-orneklem.
    SAF: ayni girdi + ayni tohum -> ayni cikti.

    NEDEN RASTGELE, "EN IYI" DEGIL: "en iyi 2" demek, test edilmemis
    IKINCI bir kural eklemek demektir. Tohumlu rastgele secim, kuralin
    ciktisinin YANSIZ bir alt-orneklemidir — ortalama tahmini bozulmaz
    ve defterdeki tam-genislik olcumuyle karsilastirilabilir kalir.

    `random.Random(tohum)` KULLANILIYOR, `random` modulunun GLOBAL
    durumuna dokunulmuyor — `trend_takip.rastgele_kontrol` ile ayni
    disiplin. Global tohum, cagiran surecin baska yerdeki rastgeleligini
    sessizce belirlerdi.

    ADAYLAR ONCE SIRALANIYOR: ayni KUME farkli sirayla gelirse secim
    degismemeli. Tarama sirasi (evren sorgusunun ORDER BY'i) bir gun
    degisirse, tohum sabit olsa bile baska semboller secilirdi — yani
    "tekrarlanabilir" iddiasi sessizce yanlis olurdu.
    """
    if tavan is None or tavan < 0:
        raise ValueError(f"tavan negatif olamaz: {tavan!r}")
    if not adaylar or len(adaylar) <= tavan:
        return list(adaylar)
    if tavan == 0:
        return []
    sirali = sorted(adaylar, key=lambda g: str(g.get("sembol") or ""))
    return random.Random(tohum).sample(sirali, tavan)


def tara(db, settings, evren: list, bitis: str | None = None) -> dict:
    """
    Evrendeki her enstrumana `seviyeler()` + `karar()`. Kabuk.

    Doner: {"gorusler": [...], "sayaclar": {sebep: adet}, "taranan": int}
    Sozlesmenin belgeden ayrildigi yer ve gerekcesi modul basinda.

    SEVIYE TEK KAPIDAN: `pulse.seviye.seviyeler()`. Ikinci bir seviye
    hesabi acilmayacak — tarayicinin kendi RSI'ini hesaplamasi MSFT'de
    84,8 vs 70,9 farki uretmisti.

    `bitis` — LOOK-AHEAD KAPISI. Gecmis bir gunun kirilim kumesini
    "o gun uretilmis gibi" verir; §8 dis sinavinin on kosulu.
    KESME BURADA YAPILMIYOR, DEVREDILIYOR: `seviyeler(bitis=)` ve
    `_devir(bitis=)` uzerinden `db.fiyat_serisi(bitis=)`e gidiyor.
    Burada kendi tarih suzgecimizi yazmak ikinci bir kesme yolu acardi
    ve iki yol AYRISIRDI — o zaman "sinav tarihe citlendi" beyani ile
    gercek ayrisir.
    """
    ayar = settings.strateji_ayari(db)
    # KOTASYON TERCIHI AYARDAN: strateji EMIR icin okuyor ve emir
    # IBKR'nin ABD listesine gidiyor. Pozisyonun para birimi portfoy
    # raporu icin dogru cevap, burada DEGIL — ASML'nin 2513 barlik USD
    # serisi varken EUR seciliyordu ve sembol sessizce eleniyordu.
    tercih = ayar.get("para_birimleri") or None
    gorusler: list[dict] = []
    sayaclar: dict[str, int] = {}
    taranan = 0

    for e in evren:
        taranan += 1
        try:
            sv = _seviye.seviyeler(db, e["id"], bitis=bitis,
                                   tercih_ccy=tercih)
        except Exception as ex:                        # noqa: BLE001
            # ARIZA SESSIZ KALMAZ, ama tek sembol tum taramayi
            # dusurmez. Sebep sayaca YAZILIYOR: "hata" ile "sinyal yok"
            # ayni satirda gorunemez.
            log.warning("[strateji] %s seviyeleri okunamadi: %s",
                        e["symbol"], ex)
            sayaclar["seviye hatasi"] = sayaclar.get("seviye hatasi", 0) + 1
            continue

        if sv is not None and "devir" not in sv:
            sv = {**sv, "devir": _devir(db, e["id"], bitis=bitis,
                                        tercih_ccy=tercih)}
        if sv is not None and "pozisyonda" not in sv:
            sv = {**sv, "pozisyonda": _pozisyonda(db, e, sv, ayar,
                                                  bitis, tercih)}

        sebep = red_sebebi(sv, ayar)
        if sebep is not None:
            sayaclar[sebep] = sayaclar.get(sebep, 0) + 1
            continue
        g = karar(sv, ayar)
        if g is None:
            # KIRILIM YOK — bu bir RED DEGIL. Ayri sayilir ki
            # "taranamayan" ile "sinyal vermeyen" karismasin.
            sayaclar["kirilim yok"] = sayaclar.get("kirilim yok", 0) + 1
            continue
        gorusler.append({**g, "seviyeler": sv})

    return {"gorusler": gorusler, "sayaclar": sayaclar, "taranan": taranan}


def _pozisyonda(db, e, sv: dict, ayar: dict, bitis: str | None,
                tercih_ccy=None) -> bool:
    """
    Kural BU BARDAN ONCE zaten pozisyona girmis mi?

    GIRIS BARININ KENDISI POZISYON SAYILMAZ: bugunun kirilimi da bir
    "acik pozisyon" uretir ve onu elemek, aradigimiz sinyali elemek
    olurdu. Bu yuzden karsilastirma `giris_ts < bar_ts`.

    Kuralin durumu `trend_takip.acik_pozisyon`dan okunuyor — giris ve
    cikis mantigi TEK yerde. Buraya ikinci bir dongu yazmak, bu deponun
    en pahali dersini tekrarlamak olurdu.
    """
    from ..analysis.karsilastirma import borsa_limiti
    from ..analysis.trend_takip import acik_pozisyon

    seri = [dict(r) for r in db.fiyat_serisi(
        e["id"], 100000, bitis=bitis, tercih_ccy=tercih_ccy)]
    if not seri:
        return False
    limit = borsa_limiti(e["venue"] if "venue" in e.keys() else None)
    a = acik_pozisyon(seri, limit, taban_kilidi=limit,
                      asgari_devir=(ayar.get("asgari_devir") or {}).get(
                          (sv.get("para_birimi") or "").upper()))
    return bool(a and str(a["giris_ts"])[:10] < str(sv.get("bar_ts"))[:10])


# Devir penceresi `analysis.trend_takip._devir` ile AYNI: 20 gun, medyan,
# ve GIRIS BARI DAHIL DEGIL. Backtest ile uretimin ayni esigi farkli
# olcmesi, olculen kenarin uygulanamaz olmasi demekti.
def _devir(db, instrument_id: int, pencere: int = 20,
           bitis: str | None = None, tercih_ccy=None) -> float | None:
    """
    Gunluk devir medyani (kapanis x hacim), kotasyonun para biriminde.

    MEDYAN, ORTALAMA DEGIL: tek bir blok islem ortalamayi kata cikarir
    ve illikit bir kagidi likit gosterir.

    Fiyat serisine TEK KAPI: `db.fiyat_serisi()`. Dogrudan `prices`
    sorgulamak para birimi karistirir (TSLA serisinde 4,07 EUR ile
    489,88 USD yan yanaydi).
    """
    seri = db.fiyat_serisi(instrument_id, pencere + 1, bitis=bitis,
                           tercih_ccy=tercih_ccy)
    if not seri:
        return None
    # SON BAR DISLANIYOR: karari verirken o barin kendi hacmi henuz
    # olusmamis sayilir — `trend_takip._devir` ile ayni kural.
    d = [(b["close"] or 0) * (b["volume"] or 0)
         for b in seri[:-1] if b["close"] and b["volume"]]
    if len(d) < pencere // 2:
        return None
    d.sort()
    return d[len(d) // 2]


# ======================================================================
# KARNE VE FREN (Adim 6)
# ======================================================================

def karne(db, sahip: str, gun: int = 180, tohum: int = 20260821) -> dict:
    """
    Kuralin karnesi + KONTROL GRUBU. Kontrolsuz karne YAYINLANMAZ.

    `42ab2fd`in dersi: BIST'te olculen kenarin yarisi PIYASA
    SURUKLEMESIYDI (beklenti %5,846, rastgele giris %2,970, kurala
    kalan %2,876). Kontrol grubu olmadan bir karne kendini kandirir.

    IKI FARKLI TABAN VAR VE KARISTIRILMIYOR — bu ayrim yazilmazsa
    kullaniciya elmayla armut karsilastirmasi gosterilir:

      `isabet_%`      DEFTERIN olcusu: PIYASAYA GORE DUZELTILMIS
                      (`anormal_pct > 0`, yani beta ile piyasa getirisi
                      cikarilmis). "Yukari dedik, hisse %3 cikti ama
                      piyasa %4 ciktiysa" bu ISABET DEGILDIR.
      `ham_isabet_%`  Duz getiri pozitif mi (`getiri_pct > 0`).
                      `rastgele_kontrol` de ham getiri olcuyor; fark
                      YALNIZCA bu taban uzerinden aliniyor.

    Doner: {"strateji": {...}, "secilen": {...}, "rastgele": {...},
            "fark_%": float|None, "yeterli_mi": bool}
    """
    from datetime import datetime, timedelta, timezone
    from ..analysis.trend_takip import rastgele_kontrol

    sinir = (datetime.now(timezone.utc)
             - timedelta(days=gun)).strftime("%Y-%m-%d")

    def _olc(ajan: str) -> dict:
        r = db.query(
            """SELECT COUNT(*) n, SUM(isabet) d,
                      SUM(CASE WHEN getiri_pct > 0 THEN 1 ELSE 0 END) ham,
                      AVG(getiri_pct) ort_getiri, AVG(anormal_pct) ort_anormal
               FROM predictions
               WHERE isabet IS NOT NULL AND olusma_ts >= ?
                 AND sahip = ? AND ajan = ?""", (sinir, sahip, ajan))[0]
        n = int(r["n"] or 0)
        if not n:
            return {"ajan": ajan, "olcum": 0, "isabet_%": None,
                    "ham_isabet_%": None, "ort_getiri_%": None,
                    "yeterli_mi": False}
        return {
            "ajan": ajan, "olcum": n,
            "isabet_%": round((r["d"] or 0) / n * 100, 1),
            "ham_isabet_%": round((r["ham"] or 0) / n * 100, 1),
            "ort_getiri_%": (round(r["ort_getiri"], 3)
                             if r["ort_getiri"] is not None else None),
            "ort_anormal_%": (round(r["ort_anormal"], 3)
                              if r["ort_anormal"] is not None else None),
            "yeterli_mi": n >= FREN_ASGARI_OLCUM,
        }

    tam, sec = _olc(AJAN), _olc(AJAN_SECILEN)

    # KONTROL GRUBU: AYNI SEMBOLLERDE, AYNI SAYIDA, AYNI SUREDE
    # rastgele giris. Sembol basina paylastiriliyor — tum islemleri tek
    # seride taklit etmek, o serinin kendi trendini kontrol grubuna
    # tasirdi (`trend_takip.kosu` ile ayni disiplin).
    dagilim = db.query(
        """SELECT p.instrument_id iid, COUNT(*) n, AVG(p.ufuk_gun) ufuk
           FROM predictions p
           WHERE p.isabet IS NOT NULL AND p.olusma_ts >= ?
             AND p.sahip = ? AND p.ajan = ?
           GROUP BY p.instrument_id""", (sinir, sahip, AJAN))
    toplam, agirlik = [], 0
    for r in dagilim:
        seri = [dict(x) for x in db.fiyat_serisi(r["iid"], 100000)]
        k = rastgele_kontrol(seri, int(r["n"]), float(r["ufuk"] or 14),
                             tohum=tohum)
        if k.get("isabet_%") is not None:
            toplam.append((int(r["n"]), k["isabet_%"], k["ortalama_%"]))
            agirlik += int(r["n"])
    rastgele: dict = {"sembol": len(toplam), "isabet_%": None}
    if agirlik:
        # ISLEM SAYISIYLA AGIRLIKLI: cok islem uretmis bir sembolun
        # kontrolu de o kadar agirlik tasimali.
        rastgele = {
            "sembol": len(toplam), "agirlik": agirlik,
            "isabet_%": round(sum(a * i for a, i, _ in toplam) / agirlik, 1),
            "ortalama_%": round(sum(a * o for a, _, o in toplam) / agirlik, 3),
        }

    fark = None
    if tam["ham_isabet_%"] is not None and rastgele["isabet_%"] is not None:
        fark = round(tam["ham_isabet_%"] - rastgele["isabet_%"], 1)

    return {"strateji": tam, "secilen": sec, "rastgele": rastgele,
            "fark_%": fark, "yeterli_mi": tam["yeterli_mi"],
            "pencere_gun": gun}


def tavan(db, settings, sahip: str) -> dict:
    """
    Bugunun gunluk emir tavani ve GEREKCESI.

    Doner: {"tavan", "fren", "olculmemis", "karne", "gerekce"}

    `olculmemis` MESAJA TASINIR: kullanicinin okudugu her sinyal,
    arkasindaki olcunun VAR olup olmadigini soylemeli. "Henuz
    olculmedi" demek, olculmus gibi davranmaktan durusttur — ve
    iyimser varsayilmaz (`taktikci.tavan` ile ayni sozlesme).
    """
    ayar = settings.strateji_ayari(db)
    varsayilan = int(ayar["gunluk_emir_tavani"])
    k = karne(db, sahip)
    olcum = int(k["strateji"]["olcum"])

    if olcum < FREN_ASGARI_OLCUM:
        return {"tavan": varsayilan, "fren": False, "olculmemis": True,
                "karne": k,
                "gerekce": (f"strateji karnesi henuz yeterli degil "
                            f"({olcum}/{FREN_ASGARI_OLCUM} olcum) — "
                            "OLCULMEMIS")}
    if k["fark_%"] is None:
        # OLCUM VAR AMA KONTROL YOK: kontrolsuz karne yayinlanmaz, ve
        # kontrolsuzken fren de cekilmez — ikisi de UYDURMA olurdu.
        return {"tavan": varsayilan, "fren": False, "olculmemis": True,
                "karne": k,
                "gerekce": (f"{olcum} olcum var ama RASTGELE KONTROL "
                            "hesaplanamadi — kontrolsuz karne yayinlanmaz")}
    if k["fark_%"] <= FREN_FARK_ESIGI:
        return {"tavan": FREN_TAVANI, "fren": True, "olculmemis": False,
                "karne": k,
                "gerekce": (f"FREN: {olcum} olcumde ham isabet "
                            f"%{k['strateji']['ham_isabet_%']}, rastgele "
                            f"%{k['rastgele']['isabet_%']} — fark "
                            f"%{k['fark_%']} (esik %{FREN_FARK_ESIGI}); "
                            f"gunluk tavan {FREN_TAVANI}")}
    return {"tavan": varsayilan, "fren": False, "olculmemis": False, "karne": k,
            "gerekce": (f"{olcum} olcumde ham isabet "
                        f"%{k['strateji']['ham_isabet_%']}, rastgele "
                        f"%{k['rastgele']['isabet_%']} — fark %{k['fark_%']}")}
