"""
TAHMIN DEFTERI — sistemin kendi isabetini olctugu yer.

NEDEN EN ONEMLI PARCA BU
------------------------
Olculdu: gunluk al-satta %50 isabet ayda -%4.2 getiriyor (komisyon),
%55 isabet +%5.6. Yani her sey isabet oraninin 50 mi 55 mi olduguna
bagli — ve bu VARSAYILAMAZ, olculmesi gerekir.

Kendi tahminlerini kaydetmeyen bir tavsiye sistemi, sonradan yalnizca
tutan tahminleri hatirlar. Bu bir hafiza kusuru degil, sistematik bir
yanilgidir ve tek caresi ONCEDEN yazmaktir.

PUANLAMA: HAM GETIRI DEGIL, ANORMAL GETIRI
------------------------------------------
"Yukari" dedik ve hisse %3 yukseldi — isabet mi? Piyasa ayni donemde %4
yukseldiyse HAYIR. Bu yuzden puanlama piyasa vekiline gore duzeltilmis
getiriyi kullanir (beta ile). Aksi halde boga piyasasinda her "yukari"
tahmini isabet gorunur ve sistem kendini iyi sanir.

NOTR TAHMIN
-----------
"notr" bir tahmin de puanlanir: hareket, olculen gunluk oynakligin
altinda kaldiysa isabettir. Boylece "bir sey olmayacak" demek de
sorumluluk dogurur; bedava kacamak degildir.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

VARSAYILAN_UFUK = 5          # islem gunu
NOTR_BANDI = 1.0             # kac gunluk-sigma icinde kalirsa "notr" isabet

# CAGRININ KAZANCI — KAGIDIN HAREKETI DEGIL. `anormal_pct` kagidin
# piyasaya gore hareketidir: dogru bir "asagi" cagrisi NEGATIF yazar.
# Karne bunun duz ortalamasini aliyordu. OLCULEN KUSUR (2026-09-24'te
# bulundu, 2026-10-02'de canli karnede hala oyleydi, sahip=ali, hakem):
# karne "-5,97" diyordu ve zarar gibi okunuyordu; oysa "asagi" cagrilari
# +9,9 KAZANDIRMIS, "yukari" cagrilari -3,6 KAYBETTIRMISTI. Sayi hem
# yanlis alarm veriyor hem de asil sorunu (alim tarafi) sakliyordu.
# Isaret cagrinin yonune gore cevriliyor; "notr" cagrinin yonu yok,
# NULL olur ve AVG'ye girmez. Tek tanim — iki karne de bunu okur.
YONLU_ANORMAL = ("CASE yon WHEN 'yukari' THEN anormal_pct "
                 "WHEN 'asagi' THEN -anormal_pct END")
YON_SIRASI = ("yukari", "asagi", "notr")

# AJAN BASINA KARNE SATIR KUMESI — TEK TANIM (C4, 2026-10-02). Bir ajanin
# karnesini kuran HER yer buradan okur; kendi suzgecini yazmaz. Burada
# olmayan ajan yalnizca `ajan` ile suzulur. Taktigin turleri frenin
# saydiklariyla ayni: `bekle` ve `koruma` "kullaniciya islem soyledim mi"
# sorusunu cevaplamaz.
KARNE_KUMELERI = {
    "taktik": {"taktik_turleri": ("alim", "satis")},
}


def ajan_karnesi(db, sahip: str, ajan: str, gun: int = 180) -> dict:
    """Bir ajanin karnesi, `KARNE_KUMELERI`ndeki satir kumesiyle."""
    return Defter(db).karne(sahip, gun, ajan=ajan,
                            **KARNE_KUMELERI.get(ajan, {}))


def bagimsiz_gozlem(satirlar) -> int:
    """
    Bagimsiz gozlem sayisi: ayni kagidin OLCUM PENCERELERI CAKISAN
    cagrilari TEK gozlemdir — ayni fiyat hareketini konusuyorlar.

    Pencere `olusma_ts` + ufuk; ufuk islem gunu, takvime 7/5 ile
    yukari yuvarlanarak cevriliyor (kripto 7 gun islem gordugu icin
    pencere biraz GENIS kalir — hata ihtiyatli yonde).

    OLCULEN KUSUR (2026-10-02, canli kopya): kume eskiden (kagit, gun)
    idi. AKSUE ardisik 14 gunde 25 "asagi" cagrisi aldi — tek bir taban
    serisi — ve 14 bagimsiz gozlem sayildi. Ali'nin hakem karnesi
    %53,8-66,8 diyordu; cakisma kuraliyla %49,4-70,6, yani %50'yi
    iciyor. "Asagi" satiri %53-73 ile "yazi-turadan ayrilir" diyordu,
    gercekte %48-77. Kagit basina tek gozlem (en katisi) neredeyse ayni
    sonucu veriyor; bu kural ise defter buyudukce kendiliginden gevser:
    iki ay arayla verilen iki cagri ayri gozlemdir.
    """
    pencereler: dict = {}
    for r in satirlar:
        bas = datetime.fromisoformat(str(r["olusma_ts"])[:10])
        gun = -(-int(r["ufuk_gun"] or 1) * 7 // 5)
        pencereler.setdefault(r["instrument_id"], []).append(
            (bas, bas + timedelta(days=gun)))
    kume = 0
    for ar in pencereler.values():
        ar.sort()
        son = None
        for bas, bit in ar:
            if son is None or bas > son:
                kume += 1
                son = bit
            else:
                son = max(son, bit)
    return kume


def wilson_araligi(p: float, n_etkin: int, z: float = 1.96) -> list[float]:
    """
    %95 Wilson araligi, yuzde olarak [alt, ust].

    `n_etkin` TAHMIN degil KUME sayisi olmali (bkz. `Defter.karne`).
    Tek kopya: karne ve yon kirilimi ayni hesabi okur — iki kopya
    oldugunda biri duzeltilir, digeri sessizce eski kalir.
    """
    n_etkin = max(1, n_etkin)
    payda = 1 + z * z / n_etkin
    merkez = (p + z * z / (2 * n_etkin)) / payda
    yayilim = (z * math.sqrt(p * (1 - p) / n_etkin
                             + z * z / (4 * n_etkin * n_etkin)) / payda)
    return [round(max(0, merkez - yayilim) * 100, 1),
            round(min(1, merkez + yayilim) * 100, 1)]


# KILITLI PENCERE — cagri UYGULANABILIR miydi.
#
# OLCULEN (2026-10-02, canli kopya): ali'nin hakem "asagi" defterinde
# ANELE'nin 8 cagrisi kazancin %36'sini uretiyordu ve olcum penceresinin
# %80-100'unde `high == low` idi — fiyat tek noktada kilitli. Taban
# kilidinde SATAMAZSIN (alici yok); asagi cagrisinin kazanci (aciga satis
# ya da kayiptan kacis) cebe konamazdi. Bot bunu kendisi onerdi.
#
# YONE DUYARLI, BILEREK. Ayni ANELE'ye 3 ve 15 Eyl'de verilen taktik
# "al"lari (-%31, -%36) da kilitli penceredeydi — ama taban kilidinde
# ALMAK mumkundu (satici bol; kilitli gunlerde 300-500 bin adet islem
# var). O cagrilari ayirmak gercek ve pahali bir hatayi karneden silerdi
# ve taktik freni tam o sayiya bakiyor. Kural: taban kilidi yalnizca
# "asagi"yi, tavan kilidi yalnizca "yukari"yi uygulanamaz yapar; "notr"
# hicbir islem onermiyor, ayrilmaz.
#
# ESIK %50: gozlenen oranlar iki kumeli (tek kilitli gun = %20, ANELE/
# ALKLC %60-100). Tek kilitli gun kagidi islemsiz yapmaz. Bilinen sinir:
# kilitli gunde de kuyruktan kismi dolum olabilir — olcut "normal piyasa
# yoktu" der, "hic kimse islem yapamadi" demez.
KILIT_ESIGI = 0.5


def kilit_engeli(seri: list, olusma_ts: str, ufuk_gun: int, yon: str) -> bool:
    """
    Cagrinin olcum penceresinin en az `KILIT_ESIGI` kadari, cagrinin
    gerektirdigi islemi ENGELLEYEN yonde mi kilitliydi (`high == low`)?

    `high`/`low`u bilinmeyen bar (or. CoinGecko) paydaya girmez — bilinmeyen
    kilitli SAYILMAZ. Yon degisimi bir onceki kapanisa gore.
    """
    if yon not in ("asagi", "yukari"):
        return False
    oncesi = [b for b in seri if b["ts"] <= olusma_ts][-1:]
    zincir = oncesi + [b for b in seri if b["ts"] > olusma_ts][:ufuk_gun]
    bilinen = engel = 0
    for a, b in zip(zincir, zincir[1:]):
        if b["high"] is None or b["low"] is None or not a["close"]:
            continue
        bilinen += 1
        if b["high"] == b["low"]:
            if (yon == "asagi" and b["close"] < a["close"]) or \
               (yon == "yukari" and b["close"] > a["close"]):
                engel += 1
    return bilinen > 0 and engel / bilinen >= KILIT_ESIGI


def _bugun() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yayim_damgasi() -> str:
    """
    Tahminin GERCEKTEN yazildigi an — dakika hassasiyetiyle.

    `_bugun()`in yerine gecmez. `olusma_ts` TARIH kalmak zorunda
    (catisma anahtari ona dayaniyor, asagida); bu damga gun sonu
    olcumunun gecikme kuralini uygulayabilmesi icin var. Gerekce ve
    olculmus sayilar `schema.sql::yayim_ts` icinde.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _gecerli_kosul(g: dict, rapor: dict) -> str | None:
    """Kosulu gramere gore suzer; reddi SAYAR (sessizce yutmaz)."""
    from .tez import kosul_ayristir
    ham = g.get("gecersizlesme_kosulu")
    if not ham:
        return None
    if kosul_ayristir(ham):
        return str(ham).strip()
    rapor["kosul_reddi"] = rapor.get("kosul_reddi", 0) + 1
    rapor.setdefault("reddedilen_kosullar", []).append(str(ham)[:80])
    log.warning("[defter] gramere uymayan gecersizlesme kosulu reddedildi: %r",
                ham)
    return None


def _referans_fiyat(gorus: dict, gunluk_kapanis):
    """
    Tahminin OLCULECEGI baslangic fiyati.

    Gorus kendi referansini beyan ederse (gun ici taktikler
    `referans_fiyat` ile SAATLIK kapanisi veriyor) o kullanilir; aksi
    halde gunluk kapanis. Gecersiz/pozitif olmayan deger SESSIZCE
    kabul edilmez, gunluk kapanisa duser.
    """
    ham = gorus.get("referans_fiyat")
    if ham is None:
        return gunluk_kapanis
    try:
        deger = float(ham)
    except (TypeError, ValueError):
        return gunluk_kapanis
    return deger if deger > 0 else gunluk_kapanis


class Defter:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------
    def kaydet(self, gorusler: list[dict], sahip: str,
               teslim: int | None = None) -> dict:
        """
        Panelin yapisal goruslerini tahmin olarak yazar — HER AJANINKINI.

        ONCEDEN NE OLUYORDU: anahtar (enstruman, ufuk) idi ve ayni sembole
        bakan ajanlardan yalnizca EN YUKSEK GUVENLI olan yaziliyordu.
        Teknik "asagi", risk "yukari" dediginde biri kalici olarak
        siliniyordu — yani projenin "celiski en degerli ciktidir" ilkesi
        deftere HIC gecmiyordu. Ustelik `ajan_karnesi()` sadece hayatta
        kalanlari saydigi icin karne, panelin degil EN IDDIALI AJANIN
        karnesiydi.

        Artik anahtara `ajan` dahil: her ajanin gorusu ayri satir. Celiski
        korunuyor ve ajan bazinda karne yansiz hale geliyor.

        Ayni ajan ayni (enstruman, ufuk) icin iki gorus verirse bu bir
        MODEL TUTARSIZLIGIDIR; yuksek guvenli tutulur ve `atilan_cakisma`
        olarak SAYILIR — sessizce yutulmaz.

        `teslim` (sema 38): 1 = mesaj GITTI, 0 = golge (bilerek
        gonderilmedi), None = cagiran bilmiyor. Cagiran bilir; burada
        TAHMIN EDILMEZ.

        Doner: {"yazilan", "atilan_sembol_yok", "atilan_seri_yok",
                "atilan_cakisma"}
        """
        # AJAN BAZLI KIRILIM. Toplamlar geriye donuk uyum icin duruyor ama
        # `panel_runs`'a yazilan sey artik kirilim: koşunun toplamini tek
        # bir ajan satirina yazmak, kacinilmak istenen seyin ta kendisiydi
        # — sorgu dort ajanin toplamini `olay`in sanirdi.
        if not sahip:
            raise ValueError("Defter.kaydet: sahip zorunlu")
        rapor = {"yazilan": 0, "atilan_sembol_yok": 0,
                 "atilan_seri_yok": 0, "atilan_cakisma": 0,
                 "ajan_bazli": {}}

        def _at(ajan: str, sebep: str) -> None:
            rapor[sebep] += 1
            rapor["ajan_bazli"].setdefault(
                ajan, {"atilan_sembol_yok": 0, "atilan_seri_yok": 0,
                       "atilan_cakisma": 0})[sebep] += 1

        if not gorusler:
            return rapor
        ts = _bugun()
        # TEK KEZ OKUNUYOR, satir basina degil: ayni yazma turundaki tum
        # gorusler AYNI ani paylasir. Satir basina okumak, tek bir panel
        # kosusunu saniyelere yayilmis gibi gosterirdi.
        yayim = _yayim_damgasi()
        en_iyi: dict[tuple, dict] = {}
        for g in gorusler:
            # `ajan` en basta okunur: dusurme sebebi ne olursa olsun
            # KIME ait oldugu bilinmeli.
            ajan = str(g.get("ajan") or "bilinmiyor").strip().lower()[:20]
            sem = str(g.get("sembol", "")).strip().upper()
            if not sem or g.get("yon") not in ("yukari", "asagi", "notr"):
                _at(ajan, "atilan_sembol_yok")
                continue
            e = self.db.query(
                "SELECT id FROM instruments WHERE UPPER(symbol)=? LIMIT 1", (sem,))
            if not e:
                _at(ajan, "atilan_sembol_yok")
                continue
            iid = e[0]["id"]
            seri = self.db.fiyat_serisi(iid, 2)
            if not seri or not seri[-1]["close"]:
                _at(ajan, "atilan_seri_yok")
                continue
            ufuk = int(g.get("ufuk_gun") or VARSAYILAN_UFUK)
            anahtar = (iid, ufuk, ajan)
            guven = float(g.get("guven") or 0.5)
            if anahtar in en_iyi:
                _at(ajan, "atilan_cakisma")
                if en_iyi[anahtar]["guven"] >= guven:
                    continue
            en_iyi[anahtar] = {
                "iid": iid, "ajan": ajan, "yon": g["yon"], "ufuk": ufuk,
                "guven": guven,
                "gerekce": f"[{ajan}] {g.get('gerekce', '')}"[:400],
                "signal_id": g.get("signal_id"),
                "tez": (g.get("tez") or None),
                # GRAMERE UYMAYAN KOSUL KAYDEDILMEZ. Kaydedilseydi kontrol
                # her gun calisir, hep False doner ve kullanici "tez hala
                # gecerli" sanirdi — uydurulmus kosul, hic kosuldan kotu.
                "gecersizlesme": _gecerli_kosul(g, rapor),
                "esik": (g.get("izlenecek_esik") or None),
                # TAKTIK ALANLARI (sema 15). Dogrulamayi GECMIS olanlar
                # gelir — `agents._taktigi_dogrula` reddettigini zaten
                # silmis olur, yani buraya uydurulmus seviye ulasmaz.
                "taktik_tur": (g.get("tur") or None),
                "taktik_giris": g.get("giris"),
                "taktik_stop": g.get("stop"),
                "taktik_giris_kaynak": g.get("giris_kaynak"),
                "taktik_stop_kaynak": g.get("stop_kaynak"),
                # REFERANS FIYAT: gorus verirse ONUNKI, yoksa gunluk
                # kapanis.
                #
                # OLCULEN KUSUR (2026-08-21, DEVA): gun ici taktik
                # 83,35'ten bakip "85,20 geri alinirsa al" diyordu ama
                # `baslangic_fiyat` DUNUN kapanisi (89,15) yaziliyordu.
                # Iki sonucu vardi: (1) getiri, taktigin GORMEDIGI bir
                # dususu de iceriyordu; (2) `_tetiklendi` yaklasma
                # yonunu 89,15'e gore hesapliyor ve "yukari toparlanma"
                # girisini "asagi geri cekilme" saniyordu — yani tetik
                # kapisi TERS calisiyordu.
                #
                # Gun ici bir cagriyi dunun kapanisiyla olcmek, cagrinin
                # bakmadigi bir hareketi ona fatura etmektir.
                "fiyat": _referans_fiyat(g, seri[-1]["close"]),
                "ccy": seri[-1]["currency"],
                # TARANAN BARIN TARIHI — gorus tasiyorsa (strateji
                # motoru `seviyeler.bar_ts`). Panel ve taktik tasimaz,
                # NULL kalir. Gerekce schema.sql `bar_ts`.
                "bar_ts": (str(g.get("bar_ts"))[:10]
                           if g.get("bar_ts") else None)}

        if not en_iyi:
            return rapor
        # GUNUN ILK PANELI KAZANIR — `DO UPDATE` DEGIL `DO NOTHING`.
        #
        # `olusma_ts` bir TARIHTIR (`_bugun()`), damga degil. Ritim v2
        # gunde DORT panel kosusu getiriyor ve `DO UPDATE` ile dordu de
        # AYNI SATIRI ezerdi: sabah yazilan tez, gerekce ve baslangic
        # fiyati aksam iz birakmadan silinirdi. "Sabah ne demistin"
        # sorusunun cevabi kalmazdi.
        #
        # USTELIK OLCULEBILIR BIR ARIZA URETIYORDU: `DO UPDATE`
        # `gecersizlesme_kosulu`'nu yeniliyor ama `tez_bozuldu_ts`'i
        # TEMIZLEMIYORDU; `tez_kontrol` ise `tez_bozuldu_ts IS NULL`
        # suzuyor (asagida). Zincir: 08:00 tezi yazar -> 12:30'da
        # bozulur, alarm gider, damga yazilir -> 17:45 AYNI SATIRA yeni
        # bir kosul yazar -> o kosul gunun geri kalaninda HIC KONTROL
        # EDILMEZ. Tek panel kosusu varken imkansizdi, dortte kacinilmaz.
        #
        # `DO NOTHING` ikisini birden cozuyor: satir hic degismiyor,
        # dolayisiyla damga ile kosul asla ayrisamiyor. Gunun sonraki
        # panellerinin TAM METNI `panel_runs.ham_metin`'de duruyor ve
        # kullanicinin OKUDUGU sey zaten hakemin o anki ciktisi — yani
        # bilgi kaybi yok, yalnizca DEFTER en erken cagriyi tutuyor.
        # "En erken tahmin en durust tahmindir": gun ilerledikce fiyat
        # zaten belli oluyor.
        with self.db.tx() as c:
            once = c.total_changes
            c.executemany(
                """INSERT INTO predictions
                   (olusma_ts, yayim_ts, instrument_id, ajan, signal_id, yon,
                    ufuk_gun, guven, gerekce, tez, gecersizlesme_kosulu,
                    izlenecek_esik, taktik_tur, taktik_giris, taktik_stop,
                    taktik_giris_kaynak, taktik_stop_kaynak,
                    baslangic_fiyat, para_birimi, sahip, bar_ts, teslim)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(olusma_ts, instrument_id, ufuk_gun, ajan, sahip)
                   DO NOTHING""",
                # `yayim_ts` CATISMA ANAHTARINDA YOK ve olmamali: anahtar
                # `olusma_ts` (tarih) uzerinden "gunun ilk paneli kazanir"
                # kuralini uyguluyor. Damgayi anahtara katmak gunde dort
                # paneli dort ayri satir yapar ve o kurali sessizce
                # kaldirirdi.
                [(ts, yayim, v["iid"], v["ajan"], v["signal_id"], v["yon"],
                  v["ufuk"], v["guven"], v["gerekce"], v["tez"],
                  v["gecersizlesme"], v["esik"], v["taktik_tur"],
                  v["taktik_giris"], v["taktik_stop"], v["taktik_giris_kaynak"],
                  v["taktik_stop_kaynak"], v["fiyat"], v["ccy"], sahip,
                  v["bar_ts"], teslim)
                 for v in en_iyi.values()])
            yazilan = c.total_changes - once
        rapor["yazilan"] = yazilan
        # SESSIZ ATLAMA YOK: gunun ikinci panelinde kac gorus deftere
        # GIRMEDIGI sayilir. Sayilmazsa "panel calisti ama defter
        # buyumedi" durumu aciklanamaz gorunur.
        rapor["gun_icinde_zaten_vardi"] = len(en_iyi) - yazilan
        if rapor["gun_icinde_zaten_vardi"]:
            log.info("[defter] %d gorus bugun zaten deftere yazilmisti "
                     "(gunun ilk paneli kazanir)",
                     rapor["gun_icinde_zaten_vardi"])
        if any(rapor[k] for k in ("atilan_sembol_yok", "atilan_seri_yok",
                                  "atilan_cakisma")):
            log.warning("[defter] gorus atildi: %s", rapor)
        return rapor

    # ------------------------------------------------------------------
    @staticmethod
    def _tetiklendi(p, barlar) -> bool | None:
        """
        Taktigin GIRIS seviyesi ufuk icinde gorulduyse True, hic
        gorulmediyse False. Taktik olmayan satirlarda None.

        YAKLASMA YONU VERIDEN TURETILIYOR, VARSAYILMIYOR:
        seviye baslangic fiyatinin USTUNDEYSE kirilim girisidir (fiyat
        YUKARI gelip degecek, `high >= giris`); ALTINDAYSA geri cekilme
        girisidir (fiyat ASAGI inip degecek, `low <= giris`). Ikisini
        tek yone sabitlemek, Donchian kirilimi ile SMA'ya geri cekilme
        girisinden birini KALICI OLARAK yanlis olcerdi.

        `high`/`low` yoksa `close` kullanilir — gun ici uc noktalar
        olmadan "deger goruldu mu" sorusunun elde kalan tek cevabi
        kapanistir ve bu, seviyeyi GORMUS bazi barlari kaciracagi icin
        MUHAFAZAKARDIR: puanlamaz, uydurmaz.
        """
        tur = (p["taktik_tur"] or "").strip().lower()
        giris, baz = p["taktik_giris"], p["baslangic_fiyat"]
        if tur not in ("alim", "satis") or giris is None or not baz:
            return None                     # taktik degil: kapi calismaz
        yukari = float(giris) > float(baz)
        for b in barlar:
            if yukari:
                uc = b["high"] if b["high"] is not None else b["close"]
                if uc is not None and uc >= giris:
                    return True
            else:
                uc = b["low"] if b["low"] is not None else b["close"]
                if uc is not None and uc <= giris:
                    return True
        return False

    @staticmethod
    def _taban_katsayisi(p, oncesi) -> tuple[float, str | None]:
        """
        Tahminin kayitli baslangici ile serinin OLUSMA GUNUNDEKI kapanisi
        arasindaki oran; ikisi bir sicrama esigi kadar ayrismissa seri
        yeniden tabanlanmis demektir ve katsayi o orandir. Aksi halde 1.

        NEDEN OLUSMA GUNU: `kaydet` baslangici o gunun kapanisindan (ya
        da gun ici referanstan) yaziyor; ayni gunun kapanisi serinin
        GUNCEL tabaninda okununca iki taban arasindaki katsayi cikar.
        Gun ici referans ile kapanis arasindaki fark yuzde birkactir,
        esigin (x1,5) cok altinda — yanlis tetiklemez.

        Serinin o gune ait bari yoksa (400 bardan eski tahmin) katsayi
        1 kalir: UYDURULMAZ, olcum eski davranisla yapilir.
        """
        from ..analysis.tutarlilik import SICRAMA_ESIGI
        try:
            baslangic = float(p["baslangic_fiyat"])
            referans = float(oncesi[-1]["close"]) if oncesi else None
        except (TypeError, ValueError, KeyError, IndexError):
            return 1.0, None
        if not referans or baslangic <= 0:
            return 1.0, None
        oran = referans / baslangic
        if 1.0 / SICRAMA_ESIGI < oran < SICRAMA_ESIGI:
            return 1.0, None
        return oran, (f"taban x{oran:.4g} yeniden olceklendi (kaynak seriyi "
                      f"yeniden tabanladi; {baslangic:g} -> {referans:g})")

    def puanla(self, sahip: str | None = None) -> dict:
        """
        Ufku dolmus tahminleri olcer.

        SAHIP VERILMEZSE TUM SAHIPLERIN tahminleri puanlanir — bilerek.
        Puanlama deterministik ve LLM'siz; fiyat serisinden hesaplaniyor
        ve kisi basina kosturmanin hicbir faydasi yok, yalnizca ayni isi
        N kere yapardi. Donen karne ise `sahip` verilmisse ona ait.

        Piyasa vekili varsa beta ile duzeltilmis ANORMAL getiri
        kullanilir; yoksa ham getiri ve bu kayitta belirtilir.
        """
        # `olcum_ts IS NULL` DE SUZULUYOR: tetiklenmemis taktikler
        # `isabet`i NULL birakiyor ama `olcum_ts` aliyor. Yalnizca
        # `isabet IS NULL` suzseydik o satirlar HER kosuda yeniden
        # incelenir ve sonsuza kadar "bekleyen" gorunurlerdi.
        from ..analysis.tutarlilik import sicramalar

        bekleyen = self.db.query(
            """SELECT * FROM predictions
               WHERE isabet IS NULL AND olcum_ts IS NULL
               ORDER BY olusma_ts""")
        olculen, kayitlar, tetiksiz, sicramali = 0, [], [], []
        for p in bekleyen:
            seri = self.db.fiyat_serisi(p["instrument_id"], 400)
            sonrasi = [r for r in seri if r["ts"] > p["olusma_ts"]]
            if len(sonrasi) < p["ufuk_gun"]:
                continue                       # ufuk dolmamis, bekle
            bitis = sonrasi[p["ufuk_gun"] - 1]
            if not bitis["close"] or not p["baslangic_fiyat"]:
                continue

            # SERI KENDI ICINDE TUTARLI MI? OLCULEN KUSUR (2026-09-08,
            # BLCYT): kaynak bolunmeden sonra gecmisi geriye donuk
            # yeniden tabanladi, 5 gunluk tazeleme penceresi yalnizca
            # son barlari yeniden yazdi; seri 25 Agu'da 21,10, 26 Agu'da
            # 2,13 oldu ve taktik "-%90" diye puanlandi — fren girdisine
            # de oyle girdi. Sicramanin USTUNDEN olcum yapilmaz: satir
            # bekler (`olcum_ts` NULL kalir), sebep `olcum_notu`ya
            # yazilir, collector sicramali seriyi TAM gecmisle yeniden
            # cekince bir sonraki turda olculur. Olcut TEK yerde:
            # `analysis/tutarlilik.py`.
            oncesi = [r for r in seri if r["ts"] <= p["olusma_ts"]]
            pencere = oncesi[-1:] + sonrasi[:p["ufuk_gun"]]
            sicrama = sicramalar(pencere)
            if sicrama:
                s = sicrama[0]
                sicramali.append((
                    f"seri sicramasi {s['ts']} x{s['oran']:g} "
                    f"({s['onceki']:g} -> {s['sonraki']:g}) — kaynak yeniden "
                    "tabanlanana kadar puanlanmadi", p["id"]))
                continue

            # TABAN UZLASTIRMA. Seri tutarli ama tahminin `baslangic_fiyat`i
            # ESKI tabanda kalmis olabilir (kaynak butun gecmisi yeniden
            # cekti, satir ise bolunmeden once yazildi). Serinin olusma
            # gunundeki kapanisi ile kayitli baslangic bir sicrama esigi
            # kadar ayrisiyorsa katsayi serinin tabanina cekilir —
            # giris ve stop seviyeleri de ayni katsayiyla, yoksa tetik
            # kapisi eski tabandaki seviyeyi yeni tabanda arardi.
            katsayi, taban_notu = self._taban_katsayisi(p, oncesi)
            p_olc = dict(p)
            p_olc["baslangic_fiyat"] = float(p["baslangic_fiyat"]) * katsayi
            for alan in ("taktik_giris", "taktik_stop"):
                if p[alan] is not None:
                    p_olc[alan] = float(p[alan]) * katsayi

            # TAKTIK KOSULLU BIR TALIMATTIR. "85,20 geri alinirsa al"
            # diyen bir satiri, fiyat 85,20'yi HIC GORMEDEN dusmusken
            # "kacirma" diye puanlamak, VERILMEMIS bir tavsiyeyi olcmek
            # olur — kullanici o pozisyonu hic acmadi. Fren bu sayiya
            # baktigi icin ayrim burada yapiliyor.
            tetik = self._tetiklendi(p_olc, sonrasi[:p["ufuk_gun"]])
            if tetik is False:
                tetiksiz.append((bitis["ts"], p["id"]))
                continue
            getiri = (bitis["close"] / p_olc["baslangic_fiyat"] - 1) * 100

            piyasa_g, anormal = None, getiri
            vekil = self.db.piyasa_vekili(p["instrument_id"])
            if vekil:
                pg, beta = self._piyasa(vekil["instrument_id"], p["olusma_ts"],
                                        bitis["ts"], p["instrument_id"])
                if pg is not None:
                    piyasa_g = pg
                    anormal = getiri - (beta or 1.0) * pg

            esik = self._notr_esigi(p["instrument_id"], p["ufuk_gun"])
            if p["yon"] == "yukari":
                isabet = 1 if anormal > 0 else 0
            elif p["yon"] == "asagi":
                isabet = 1 if anormal < 0 else 0
            else:
                isabet = 1 if abs(anormal) <= esik else 0

            kayitlar.append((bitis["ts"], bitis["close"], round(getiri, 3),
                             round(piyasa_g, 3) if piyasa_g is not None else None,
                             round(anormal, 3), isabet,
                             1 if tetik else None, taban_notu, p["id"]))
            olculen += 1

        if kayitlar:
            with self.db.tx() as c:
                c.executemany(
                    """UPDATE predictions SET olcum_ts=?, bitis_fiyat=?,
                       getiri_pct=?, piyasa_getiri_pct=?, anormal_pct=?,
                       isabet=?, taktik_tetiklendi=?, olcum_notu=?
                       WHERE id=?""", kayitlar)
        if sicramali:
            # `olcum_ts` NULL KALIR — satir bekliyor, kapanmadi. Not her
            # turda yeniden yazilir (ayni metin, ucuz); sicrama gidince
            # satir normal yoldan olculur ve not taban notuyla degisir.
            with self.db.tx() as c:
                c.executemany("UPDATE predictions SET olcum_notu=? WHERE id=?",
                              sicramali)
            log.warning("[defter] %d tahmin SERI SICRAMASI yuzunden puanlanmadi "
                        "(kaynak onarimi bekleniyor): %s", len(sicramali),
                        ", ".join(str(i) for _, i in sicramali[:12]))
        if tetiksiz:
            # `isabet` NULL KALIR — bu satir bir isabet de kacirma da
            # degil; olculmedi cunku OLCULECEK BIR ISLEM OLMADI.
            # `olcum_ts` yaziliyor ki bir daha incelenmesin.
            with self.db.tx() as c:
                c.executemany(
                    """UPDATE predictions SET olcum_ts=?, taktik_tetiklendi=0
                       WHERE id=?""", tetiksiz)
            log.info("[defter] %d taktik TETIKLENMEDI — puanlanmadi "
                     "(giris seviyesi ufuk icinde hic gorulmedi)", len(tetiksiz))
        # AD AYRIMI SART. `olculen_toplam` bu turda puanlanan TUM
        # tahminleri (dort ajan + hakem) sayar; `karne()` icindeki
        # `olcum` YALNIZCA hakem cagrilarini sayar. Ikisi ayni sozlukte
        # benzer adlarla durursa yanlis okunur — ve bu tam olarak
        # "beyan edilen sey ile gercek sey ayrisiyor" sinifidir.
        # Karne SAHIBE ait; puanlama herkes icin kostu ama rapor kisisel.
        return {"olculen_toplam": olculen,
                # BU TURDA TETIKLENMEDIGI ICIN PUANLANMAYANLAR.
                #
                # Ayri sayilir cunku "0 puanlandi" iki farkli sey
                # olabilir: ufku dolan yoktu, ya da doldu ama giris
                # seviyeleri hic gorulmedi. Ikincisi sistemin cagri
                # urettigini ama seviyelerinin tutmadigini soyler.
                #
                # Ikinci bir isi daha var: bu sayi ikinci kosuda 0
                # olmali. Olmuyorsa `bekleyen` sorgusundan `olcum_ts`
                # suzgeci dusmus demektir ve ayni satirlar her kosuda
                # yeniden isleniyordur.
                "tetiklenmeyen_toplam": len(tetiksiz),
                # SERI SICRAMASI YUZUNDEN BEKLEYENLER. Sifir olmali;
                # degilse bir kaynak yeniden tabanlanmis ve collector'in
                # kendini onarmasi (tutarlilik.sicramali_semboller)
                # henuz o sembole ulasmamis demektir.
                "sicrama_bekleyen": len(sicramali),
                **(self.karne(sahip, ajan="hakem") if sahip else {"olcum": 0,
                   "not": "sahip verilmedi — karne uretilmedi"})}

    def _piyasa(self, vekil_id, bas_ts, bitis_ts, hisse_id):
        """Vekilin ayni donemdeki getirisi ve hissenin betasi."""
        v = self.db.fiyat_serisi(vekil_id, 400)
        bas = [r for r in v if r["ts"] <= bas_ts]
        son = [r for r in v if r["ts"] <= bitis_ts]
        if not bas or not son or not bas[-1]["close"]:
            return None, None
        pg = (son[-1]["close"] / bas[-1]["close"] - 1) * 100

        h = self.db.fiyat_serisi(hisse_id, 300)
        eslesme = {r["ts"]: r["close"] for r in v}
        y, x = [], []
        for a, b in zip(h, h[1:]):
            if b["ts"] in eslesme and a["ts"] in eslesme and a["close"] and eslesme[a["ts"]]:
                y.append(b["close"] / a["close"] - 1)
                x.append(eslesme[b["ts"]] / eslesme[a["ts"]] - 1)
        if len(y) < 30:
            return pg, 1.0
        ox = sum(x) / len(x); oy = sum(y) / len(y)
        sxx = sum((v_ - ox) ** 2 for v_ in x)
        if sxx <= 0:
            return pg, 1.0
        beta = sum((a - ox) * (b - oy) for a, b in zip(x, y)) / sxx
        return pg, beta

    def _notr_esigi(self, instrument_id, ufuk) -> float:
        """Ufuk boyunca beklenen tipik hareket (1 sigma), yuzde."""
        seri = self.db.fiyat_serisi(instrument_id, 200)
        g = [b["close"] / a["close"] - 1 for a, b in zip(seri, seri[1:])
             if a["close"] and b["close"]]
        if len(g) < 30:
            return 2.0
        o = sum(g) / len(g)
        sd = math.sqrt(sum((x - o) ** 2 for x in g) / (len(g) - 1))
        return sd * math.sqrt(ufuk) * 100 * NOTR_BANDI

    # ------------------------------------------------------------------
    def karne(self, sahip: str, gun: int = 180, *, ajan: str,
              taktik_turleri: tuple[str, ...] | None = None) -> dict:
        """
        Isabet karnesi — `ajan` ZORUNLU, VARSAYILANI YOK (C4, 2026-10-02).

        OLCULEN KUSUR: varsayilan `ajan="hakem"`ti ve ayni gun iki arac
        (taktik_sicili, gecmis_gorus) taktik ya da strateji sorulunca
        HAKEM karnesini dondurdu — ali icin %60,5 yerine taktigin %23,3'u.
        `sahip` kuralinin aynisi: parametredir, varsayilani olmaz. Ajan
        basina satir kumesi `KARNE_KUMELERI` / `ajan_karnesi`.

        `ajan` PARAMETRE, cunku B6 taktikcisinin kendi karnesi var ve
        onun freni (tavani 1'e indiren kural) bu SAYIYA bakiyor. Hesap
        KOPYALANMADI: kumelenme duzeltmesi ve Wilson araligi tek yerde
        durmali — iki kopya oldugunda biri duzeltilir, digeri sessizce
        eski kalir.

        `taktik_turleri` verilirse yalnizca o turdeki taktikler sayilir.
        Taktik freni icin ('alim','satis') gecilir: fren "kullaniciya
        ISLEM soyledigimde tutturuyor muyum" sorusunu olcer; `bekle` ve
        `koruma` cagrilari o soruyu cevaplamaz.

        NEDEN TUM TAHMINLER DEGIL: `ajan` benzersizlige girdikten sonra
        ayni enstrumanin ayni gunune ait 5 tahmin olusabiliyor (dort ajan
        + hakem) ve bunlar BAGIMSIZ GOZLEM DEGIL — hepsi TEK bir fiyat
        hareketini konusuyor. Olculdu 2026-08-16: 56 tahmin, yalnizca 26
        farkli (enstruman, gun) kumesi; AMZN'de tek harekete 5 tahmin.
        Wilson araligi bagimsizlik varsayar; kumelenmeyi yok sayarsak
        aralik ~sqrt(2.15) = 1,47 kat DAR cikar ve olmayan bir kesinlik
        uretiriz.

        Eski semada tekillestirme bunu KAZARA engelliyordu (enstruman
        basina tek satir). Kisit kaldirilinca istatistigin de duzelmesi
        gerekiyordu; bu, degisikligin yan etkisiydi.

        Hakem olculmesi gereken sey: kullanicinin OKUDUGU cikti odur.
        Ajan bazinda kirilim `ajan_karnesi()`'nde.

        DUZELTME (2026-08-20): burada "hakem enstruman-gun basina TEK
        cagri verir, dolayisiyla `olcum == bagimsiz_kume`" yaziyordu ve
        CANLI VERIDE YANLISTI — hakem ayni gun ayni enstrumana farkli
        `ufuk_gun` degerleriyle gorus verebiliyor ve `ufuk_gun`
        benzersizligin parcasi (2026-08-16: iid 222/225/231, her biri
        iki satir). Kimse bakmadigi icin gorunmedi. Artik varsayilmiyor:
        kumelenme OLCULUYOR ve aralik ona gore hesaplaniyor (asagida).
        """
        sinir = (datetime.now(timezone.utc) - timedelta(days=gun)).strftime("%Y-%m-%d")
        kosul, arg = "", [sinir, ajan, sahip]
        if taktik_turleri:
            kosul = (f" AND taktik_tur IN "
                     f"({','.join('?' * len(taktik_turleri))})")
            arg += list(taktik_turleri)
        # UYGULANAMAYAN CAGRILAR ANA SAYIDAN AYRILIR, GIZLENMEZ (bkz.
        # `kilit_engeli`). Ayrim `kosul`a eklenir: asagidaki HER sorgu —
        # toplam, kume, yon kirilimi — ayni satirlari gorur.
        uygulanamaz = self._uygulanamayanlar(kosul, arg)
        if uygulanamaz["idler"]:
            kosul += (f" AND id NOT IN "
                      f"({','.join('?' * len(uygulanamaz['idler']))})")
            arg = arg + uygulanamaz["idler"]
        r = self.db.query(
            f"""SELECT COUNT(*) n, SUM(isabet) d,
                      AVG({YONLU_ANORMAL}) yonlu_ort,
                      COUNT({YONLU_ANORMAL}) yonlu_n,
                      SUM(piyasa_getiri_pct IS NULL) vekilsiz
               FROM predictions
               WHERE isabet IS NOT NULL AND olusma_ts >= ? AND ajan = ?
                 AND sahip = ?{kosul}""",
            arg)[0]
        n, dogru = r["n"] or 0, r["d"] or 0
        if not n:
            # Tahmin yoksa SESSIZ KALMA: "olcum yok" ile "henuz
            # puanlanmadi" ayri seyler ve ikincisi gecicidir.
            toplam = self.db.query(
                """SELECT COUNT(*) n FROM predictions
                   WHERE isabet IS NOT NULL AND olusma_ts >= ? AND sahip = ?""",
                (sinir, sahip))[0]["n"]
            # BEKLEYEN SAYISI DA BEYAN EDILIYOR: "0 olcum" tek basina
            # "hic cagri yok" gibi okunur, oysa cagri VAR ve ufku
            # dolmamis olabilir. Fren bu ikisini ayirt etmek zorunda.
            # `olcum_ts IS NULL` DE SART: tetiklenmemis taktiklerin
            # `isabet`i kalici olarak NULL ama ISI BITMISTIR. Yalnizca
            # `isabet IS NULL` sayan bir "bekleyen", onlari sonsuza
            # kadar "ufkunu bekliyor" diye gosterirdi — yani karne hic
            # dolmayacak bir bekleyis vaat ederdi.
            bekleyen = self.db.query(
                f"""SELECT COUNT(*) n FROM predictions
                    WHERE isabet IS NULL AND olcum_ts IS NULL
                      AND olusma_ts >= ? AND ajan = ?
                      AND sahip = ?{kosul}""", arg)[0]["n"]
            # TETIKLENMEYEN SAYISI BURADA DA GEREKLI — asil BURADA
            # gerekli: "0 olcum" ile "0 olcum, ama 12 cagri tetiklenmedi"
            # tamamen farkli iki durum. Ikincisi sistemin cagri urettigini
            # ama seviyelerin hic gorulmedigini soyler.
            tetiksiz = self.db.query(
                f"""SELECT COUNT(*) n FROM predictions
                    WHERE taktik_tetiklendi = 0 AND olusma_ts >= ?
                      AND ajan = ? AND sahip = ?{kosul}""", arg)[0]["n"]
            # KAPSAM SIFIR OLCUMDE DE BEYAN EDILIR — HATTA ASIL BURADA.
            # "0 olcum" iki farkli sey olabilir: cagri verilmedi, ya da
            # kosularin hepsi kesildi ve cagri deftere HIC girmedi.
            # Ikincisini gizlemek, olcum yoklugunu model sessizligi gibi
            # gostermek olurdu.
            kapsam = self._kosu_kapsami(sinir, sahip, ajan)
            return {"olcum": 0, "bekleyen": bekleyen, "kaynak": ajan,
                    "tetiklenmeyen": tetiksiz, "kosu_kapsami": kapsam,
                    "not": (f"henuz puanlanmis {ajan.upper()} cagrisi yok"
                            + (f"; {kapsam['kesildi']}/{kapsam['kosu']} kosu "
                               "cikti uretmeden kapandi"
                               if kapsam and kapsam["kesildi"] else "")
                            + (f"; {bekleyen} cagri ufkunu bekliyor"
                               if bekleyen else "")
                            + (f"; {tetiksiz} cagri TETIKLENMEDI "
                               "(giris seviyesi hic gorulmedi)"
                               if tetiksiz else "")
                            + (f"; {uygulanamaz['ozet']['olcum']} cagri "
                               "kilitli piyasada UYGULANAMAZDI, ayrildi"
                               if uygulanamaz["idler"] else "")
                            + (f" (toplam {toplam} tahmin puanlandi)"
                               if toplam else "")),
                    "uygulanamayan": uygulanamaz["ozet"]}
        p = dogru / n
        # ARALIK KUME SAYISIYLA HESAPLANIR, TAHMIN SAYISIYLA DEGIL.
        #
        # Wilson araligi gozlemlerin BAGIMSIZ oldugunu varsayar. Ayni
        # enstrumanin ayni gunune ait iki hakem cagrisi (or. 5 gunluk ve
        # 20 gunluk ufuk) bagimsiz DEGILDIR — ikisi de TEK bir fiyat
        # hareketini konusuyor. Canli veride olculdu (2026-08-16,
        # sahip=ali): iid 222 -> ufuk (5,20), iid 225 -> (5,20),
        # iid 231 -> (5,60). Yani `olcum != bagimsiz_kume` ve modul
        # basindaki "esit olmali" yorumu bugun YANLISTI.
        #
        # Iki cozum vardi: hakemi enstruman-gun basina tek ufka zorlamak
        # (bilgi kaybi — cok ufuklu gorus mesru) ya da aralik hesabini
        # ETKIN ORNEKLEM BUYUKLUGUNE baglamak. Ikincisi secildi: isabet
        # orani ham sayidan, ARALIK kume sayisindan. Kumelenmeyi yok
        # saymak araligi ~sqrt(olcum/kume) kat DAR gosterir, yani olmayan
        # bir kesinlik uretir — defterin varlik sebebi tam olarak bunu
        # engellemekti.
        #
        # KUME TANIMI 2026-10-02'de (kagit, gun)'den CAKISAN PENCEREYE
        # genisledi — ayni taban serisi ardisik gunlerde ayri gozlem
        # sayiliyordu (bkz. `bagimsiz_gozlem`). Satirlar bir kez okunur,
        # yon kirilimi da AYNI satirlardan kumelenir.
        satirlar = self.db.query(
            f"""SELECT instrument_id, olusma_ts, ufuk_gun, yon
                FROM predictions
                WHERE isabet IS NOT NULL AND olusma_ts >= ? AND ajan = ?
                  AND sahip = ?{kosul}""", arg)
        kume = bagimsiz_gozlem(satirlar) or n
        n_etkin = max(1, min(kume, n))
        # YONLU CAGRI YOKSA ORTALAMA ALANI HIC YOK. Eskiden `or 0` ile
        # "0,00" yaziliyordu — "kazanc sifir" gibi okunur, oysa olculecek
        # bir sey yoktu. Bos alan goren model uydurabilir, OLMAYAN alani
        # goremez; sayac ise kalir ki yokluk da beyan edilsin. Sayac
        # ORTALAMAYA GIREN satiri sayar (`COUNT(ifade)` NULL'u atlar):
        # yonlu ama `anormal_pct`i bos satir yon sayisina girseydi
        # ortalama NULL iken sayac 1 derdi.
        yonlu_n = int(r["yonlu_n"] or 0)
        yonlu = {"yonlu_olcum": yonlu_n}
        if yonlu_n:
            yonlu["yonlu_anormal_getiri_%"] = round(r["yonlu_ort"], 2)
        tabanlar = self._taban(satirlar, sinir)
        kirilim = self._yon_kirilimi(kosul, arg, satirlar, tabanlar)
        # DUZELTILMIS ORTALAMA DA TERS YONDE YANILTABILIR. Isaret
        # cevrilince 2026-10-02 canli karne (ali, hakem) -5,97 yerine
        # +6,96 dedi — ama artinin TAMAMI "asagi" cagrilarindan (+9,9),
        # "yukari" -3,6. Asagi cagrisindan kazanc acik satis ister ve
        # BUX/Midas'ta acik satis yok. Tek sayiyi okuyan "bot kazandiriyor"
        # der; not, ortalamanin YANINDA durur ki ayri okunamasin.
        if "yonlu_anormal_getiri_%" in kirilim.get("asagi", {}):
            yonlu["yonlu_kazanc_notu"] = (
                "Bu ortalama 'asagi' cagrilarini da iceriyor; onlardan kazanc "
                "acik satis ister ve BUX/Midas'ta acik satis yok. Alim "
                "kararinin olcusu `yon_kirilimi.yukari`.")
        tarihler = sorted(str(s["olusma_ts"])[:10] for s in satirlar)
        return {
            "olcum": n, "dogru": dogru, "isabet_%": round(p * 100, 1),
            "guven_araligi_%": wilson_araligi(p, n_etkin),
            # ARALIGIN DAYANDIGI SAYI. Beyan edilmezse okuyan taraf
            # araligin neye gore hesaplandigini bilemez.
            "aralik_ornegi": n_etkin,
            # KIYAS: ayni gun rastgele secim (yon karisimiyla), %50 DEGIL.
            **self._taban_alanlari(
                [t for v in tabanlar.values() for t in v], n,
                wilson_araligi(p, n_etkin)),
            # KARNENIN KENDI DONEMI. OLCULEN KUSUR (2026-10-02, canli bot):
            # `gecmis_gorus` "kapsam: son 90 gun" yaziyordu (gorus
            # listesinin `gun`u) ve model tabloyu "Son 90 gun" diye
            # basliklandirdi; karne ise 180 gunluk pencereye bakiyordu ve
            # defter 15 Agu'da basladigi icin fiilen 7 haftaydi. Pencere ve
            # FIILI ilk/son cagri tarihi karnenin icinde durur.
            "donem": {"pencere_gun": gun, "ilk_olculen_cagri": tarihler[0],
                      "son_olculen_cagri": tarihler[-1]},
            # KILITLI PIYASADA UYGULANAMAYANLAR — ana sayilarin DISINDA,
            # ama burada sayisi, isabeti ve kagitlariyla (bkz.
            # `kilit_engeli`). Ayrilinca karne DAHA kotu de cikabilir;
            # amac sayiyi iyilestirmek degil, cebe konabilecek olani olcmek.
            "uygulanamayan": uygulanamaz["ozet"],
            # CAGRININ ortalama kazanci (bkz. `YONLU_ANORMAL`): pozitif =
            # cagrilar piyasayi yenmis. `notr` cagrilar DISARIDA.
            **yonlu,
            # YON KIRILIMI. Bastaki `isabet_%` uc farkli soruyu tek sayida
            # topluyor; en kolay tutan "notr" cagrilar onu yukari cekiyor.
            # 2026-10-02 canli (ali, hakem): toplam %60,5 iken "yukari"
            # (alim) 10/25 = %40. Islem karari alim tarafina bakar ve o
            # sayi bastaki ortalamanin icinde gorunmuyordu.
            "yon_kirilimi": kirilim,
            "kaynak": ajan,
            # TETIKLENMEYENLER BEYAN EDILIYOR. Giris seviyesi hic
            # gorulmemis taktikler puanlanmiyor (dogrusu bu) ama
            # sayilari saklanirsa orneklem sessizce kuculur ve okuyan
            # taraf "bu kadar cagri verdim, bu kadari tuttu" sanir.
            "tetiklenmeyen": self.db.query(
                f"""SELECT COUNT(*) n FROM predictions
                    WHERE taktik_tetiklendi = 0 AND olusma_ts >= ?
                      AND ajan = ? AND sahip = ?{kosul}""", arg)[0]["n"],
            # KUMELENME BEYAN EDILIYOR, GIZLENMIYOR. `olcum` ham tahmin
            # sayisi, `bagimsiz_kume` farkli (enstruman, gun) sayisi.
            # Ikisi ayrildiginda aralik KUME sayisiyla hesaplanir
            # (yukaridaki `n_etkin`) — eskiden yorum "esit olmali"
            # diyordu ama canli veride esit degildi ve kimse bakmiyordu.
            "bagimsiz_kume": kume,
            # VEKILSIZ PUANLANANLAR AYRI SAYILIR. Piyasa vekili
            # bulunamayan tahmin HAM getiriyle olculur; boga piyasasinda
            # her "yukari" isabet gorunur — defterin varlik sebebi tam
            # olarak bunu engellemekti. Ayrim veride vardi
            # (`piyasa_getiri_pct IS NULL`) ama karnede YOKTU, yani
            # okuyan taraf hangi olcunun kullanildigini bilemiyordu.
            "vekilsiz_n": r["vekilsiz"] or 0,
            # VENUE KIRILIMI. `puanla()` BAR sayarak ufuk doldu mu diye
            # bakiyor; kripto haftada 7 bar uretiyor, hisse 5. Yani ayni
            # gun yazilan tahminlerde kripto ONCE olgunlasiyor ve ilk
            # karneler kripto agirlikli olacak. Kapsam beyan edilmezse
            # "sistemin isabeti" sanilan sey aslinda "kriptodaki isabeti"
            # olur.
            "venue_kirilimi": self._venue_kirilimi(kosul, arg),
            # KOSU KAPSAMI — KARNE KENDI ORNEKLEMININ NEREDEN GELDIGINI
            # BEYAN EDER.
            #
            # OLCULEN KUSUR (2026-09-08): 1-8 Eylul arasi hakem 42 kez
            # kostu, 22'si sure sinirinda KESILDI ve o kosularin
            # cagrilari deftere hic girmedi. Karne "108 olcum" diyordu
            # ve bu dogruydu — ama kayip 22 kosudan HIC SOZ ETMIYORDU.
            # Okuyan (insan ya da model) orneklem eksikligini goremezdi.
            # Kayip ORANI rastgele degil: uzun suren, yani daha cok
            # adayin oldugu kosulari vuruyor.
            "kosu_kapsami": self._kosu_kapsami(sinir, sahip, ajan),
            "yeterli_mi": n >= 20,
            "not": ("ORNEKLEM YETERSIZ — bu sayilardan sonuc cikarma"
                    if n < 20 else
                    "Komisyon sonrasi basabas ~%55 isabet gerektiriyor"),
        }

    def _uygulanamayanlar(self, kosul: str, arg: list) -> dict:
        """
        Karnenin suzgecindeki puanli YONLU cagrilardan kilitli piyasada
        uygulanamayanlar (`kilit_engeli`).

        Doner: {"idler": [...], "ozet": {...}}. `ozet` HER ZAMAN doner —
        sifir da bir olcum sonucudur; alan yoksa okuyan taraf "bakildi mi"
        bilemez.
        """
        satirlar = self.db.query(
            f"""SELECT id, instrument_id, olusma_ts, ufuk_gun, yon, isabet,
                       anormal_pct
                FROM predictions
                WHERE isabet IS NOT NULL AND olusma_ts >= ? AND ajan = ?
                  AND sahip = ?{kosul} AND yon IN ('asagi', 'yukari')""", arg)
        seri: dict = {}
        ayrilan = []
        for s in satirlar:
            if s["instrument_id"] not in seri:
                seri[s["instrument_id"]] = self.db.fiyat_serisi(
                    s["instrument_id"], 400)
            if kilit_engeli(seri[s["instrument_id"]], s["olusma_ts"],
                            s["ufuk_gun"], s["yon"]):
                ayrilan.append(s)
        if not ayrilan:
            return {"idler": [], "ozet": {"olcum": 0}}
        adlar = {r["id"]: r["symbol"] for r in self.db.query(
            f"""SELECT id, symbol FROM instruments WHERE id IN
                ({','.join('?' * len(seri))})""", list(seri))}
        kagit: dict = {}
        for s in ayrilan:
            ad = adlar.get(s["instrument_id"], "?")
            kagit[ad] = kagit.get(ad, 0) + 1
        n = len(ayrilan)
        dogru = sum(int(s["isabet"]) for s in ayrilan)
        yonlu = [(s["anormal_pct"] if s["yon"] == "yukari" else -s["anormal_pct"])
                 for s in ayrilan if s["anormal_pct"] is not None]
        ozet = {"olcum": n, "dogru": dogru,
                "isabet_%": round(dogru / n * 100, 1),
                "kagitlar": dict(sorted(kagit.items(), key=lambda x: -x[1])),
                "neden": ("olcum penceresinin en az yarisinda fiyat, cagrinin "
                          "gerektirdigi yonde kilitliydi (high == low) — "
                          "'asagi' icin taban kilidi: satilamaz; 'yukari' icin "
                          "tavan kilidi: alinamaz. Ana sayilarin DISINDA.")}
        if yonlu:
            ozet["yonlu_anormal_getiri_%"] = round(sum(yonlu) / len(yonlu), 2)
        return {"idler": [s["id"] for s in ayrilan], "ozet": ozet}

    def _taban(self, satirlar, sinir: str) -> dict:
        """
        AYNI GUN RASTGELE SECIM TABANI — yon basina (C4, 2026-10-02).

        OLCULEN KUSUR: yonlu cagri %50'ye kiyaslaniyordu. Bu donemde
        defterdeki 106 BIST kagidinin medyani -%20 (yalniz %15'i pozitif);
        ayni gun rastgele bir "asagi" BIST'te ~%68 tutuyordu. Hakemin
        %63,7 "asagi" isabeti beceri degil piyasanin yonuydu; taktigin
        %23'u de %50'ye gore "anlamli kotu", kendi tabanina (~%38) gore
        degildi. Notr cagrinin ise HIC kiyasi yoktu.

        Kontrol kumesi: AYNI venue, AYNI `olusma_ts`, AYNI `ufuk_gun` ile
        defterde puanlanmis OBUR kagitlar (kagit basina tek deger — ayni
        pencere ayni anormal getiri; cagrinin kendi kagidi disarida).
          yukari -> anormal > 0 orani · asagi -> anormal < 0 orani
          notr   -> |anormal| <= o kagidin kendi bandi (`_notr_esigi`)
        Kontrolu olmayan satir tabana girmez; sayisi `taban_olcum`.
        Bilinen sinir: evren "defterin o gun baktigi kagitlar", piyasanin
        tamami degil.
        """
        havuz: dict = {}
        for r in self.db.query(
                """SELECT p.instrument_id iid, p.olusma_ts ts, p.ufuk_gun u,
                          i.venue v, p.anormal_pct a
                   FROM predictions p JOIN instruments i ON i.id = p.instrument_id
                   WHERE p.isabet IS NOT NULL AND p.anormal_pct IS NOT NULL
                     AND p.olusma_ts >= ?""", (sinir,)):
            havuz.setdefault((r["v"], r["ts"], r["u"]), {}).setdefault(
                r["iid"], r["a"])
        iids = {x["instrument_id"] for x in satirlar}
        venue = {r["id"]: r["venue"] for r in self.db.query(
            f"SELECT id, venue FROM instruments WHERE id IN "
            f"({','.join('?' * len(iids))})", list(iids))} if iids else {}
        bant: dict = {}
        tabanlar: dict = {}
        for x in satirlar:
            kontrol = {i: a for i, a in havuz.get(
                (venue.get(x["instrument_id"]), x["olusma_ts"], x["ufuk_gun"]),
                {}).items() if i != x["instrument_id"]}
            if not kontrol:
                continue
            if x["yon"] == "yukari":
                t = sum(a > 0 for a in kontrol.values()) / len(kontrol)
            elif x["yon"] == "asagi":
                t = sum(a < 0 for a in kontrol.values()) / len(kontrol)
            else:
                ic = 0
                for i, a in kontrol.items():
                    if (i, x["ufuk_gun"]) not in bant:
                        bant[(i, x["ufuk_gun"])] = self._notr_esigi(
                            i, x["ufuk_gun"])
                    ic += abs(a) <= bant[(i, x["ufuk_gun"])]
                t = ic / len(kontrol)
            tabanlar.setdefault(x["yon"], []).append(t)
        return tabanlar

    @staticmethod
    def _taban_alanlari(tabanlar: list, n: int, aralik: list) -> dict:
        """Taban ozeti + aralikla kiyas. Az satirda taban -> hukum YOK."""
        if not tabanlar:
            return {"taban_olcum": 0}
        t = round(sum(tabanlar) / len(tabanlar) * 100, 1)
        out = {"taban_%": t, "taban_olcum": len(tabanlar)}
        if len(tabanlar) >= n / 2:
            out["tabandan_ayrilir_mi"] = bool(t < aralik[0] or t > aralik[1])
        return out

    def _yon_kirilimi(self, kosul: str, arg: list, kume_satirlari,
                      tabanlar: dict) -> dict:
        """
        `karne()` ile AYNI suzgecle, yon basina olcum / isabet / kazanc.

        `kosul` ve `arg` karnenin kendisinden geliyor — ayri bir suzgec
        yazilsaydi kirilim ile toplam farkli satirlari sayabilirdi.
        `kume_satirlari` da karnenin okudugu satirlar; kumeleme yon
        icinde yapilir (`bagimsiz_gozlem`).
        `notr` icin kazanc alani YOK: cagrinin yonu yok, ortalamasi
        `YONLU_ANORMAL`da da NULL.
        """
        satirlar = {r["yon"]: r for r in self.db.query(
            f"""SELECT yon, COUNT(*) n, SUM(isabet) d,
                       AVG({YONLU_ANORMAL}) yonlu,
                       COUNT(DISTINCT instrument_id) kagit
                FROM predictions
                WHERE isabet IS NOT NULL AND olusma_ts >= ? AND ajan = ?
                  AND sahip = ?{kosul}
                GROUP BY yon""", arg)}
        out = {}
        for yon in YON_SIRASI:
            r = satirlar.get(yon)
            if not r:
                continue
            n, dogru = int(r["n"]), int(r["d"] or 0)
            kume = bagimsiz_gozlem(x for x in kume_satirlari
                                   if x["yon"] == yon)
            n_etkin = max(1, min(kume or n, n))
            aralik = wilson_araligi(dogru / n, n_etkin)
            # ARALIK HER SATIRDA. OLCULEN KUSUR (2026-10-02, canli bot):
            # kirilimda yalnizca `yeterli_mi` (n >= 20) vardi ve model
            # onu ANLAMLILIK diye okudu — "25 olcum, yeterli, yani sans
            # eseri degil". 10/25'in araligi %23-59: hem %50'yi hem %55
            # basabasi iciyor. `yeterli_mi` "bakmaya deger" demek,
            # "kanitlandi" degil; ikisi ayri alanda durmali.
            k = {"olcum": n, "dogru": dogru,
                 "isabet_%": round(dogru / n * 100, 1),
                 "guven_araligi_%": aralik, "aralik_ornegi": n_etkin,
                 # KAC FARKLI KAGIT. 2026-10-02 canli: ali "asagi" 91
                 # cagri, 36 kagit; ilk bes kagit cagrilarin %46'si.
                 # Okuyan taraf "91 cagri" ile "91 farkli olay"i ayirsin.
                 "kagit_sayisi": int(r["kagit"] or 0),
                 "yeterli_mi": n >= 20}
            # KIYAS %50 DEGIL, AYNI GUN RASTGELE SECIM (bkz. `_taban`).
            # Eski `yazi_turadan_ayrilir_mi` kaldirildi: bu donemde %50
            # yanlis sifir hipoteziydi ve iki yonde de yanlis hukum verdi.
            k.update(self._taban_alanlari(tabanlar.get(yon, []), n, aralik))
            if r["yonlu"] is not None:
                k["yonlu_anormal_getiri_%"] = round(r["yonlu"], 2)
            out[yon] = k
        return out

    def _kosu_kapsami(self, sinir: str, sahip: str, ajan: str) -> dict | None:
        """
        Bu pencerede o ajanin kac kosusu URETTI, kaci KESILDI.

        `None` doner: ajan `panel_runs`ta izlenmiyorsa (taktik, strateji
        — onlar hakemin ciktisindan turuyor ve kendi kosu satirlari yok).
        Bos sozluk yerine `None`, cunku "izlenmiyor" ile "hepsi basarili"
        ayni sey degil.
        """
        if ajan not in ("hakem", "teknik", "temel", "olay", "risk"):
            return None
        try:
            r = self.db.query(
                """SELECT COUNT(*) toplam,
                          SUM(json_durum = 'ok') uretti,
                          SUM(json_durum IN ('kesildi', 'bos')) kesildi,
                          SUM(json_durum = 'ajan_hatasi') hata
                   FROM panel_runs
                   WHERE ajan = ? AND sahip = ? AND date(run_ts) >= date(?)""",
                (ajan, sahip, sinir))[0]
        except Exception as e:                            # noqa: BLE001
            log.warning("[defter] kosu kapsami okunamadi: %s", e)
            return None
        toplam = int(r["toplam"] or 0)
        if not toplam:
            return None
        kesildi = int(r["kesildi"] or 0)
        # DONEM AYRIMI (C4, 2026-10-02). OLCULEN: bot uc cevapta "128
        # kosunun 23'u kesildi (%18)" dedi — su anki durum gibi. Oysa
        # kesintilerin 21'i 8 Eyl duzeltmesinden ONCEYDI (21/58), sonrasi
        # 2/70. Pencere toplami kalir (orneklem gercekten kucultuldu),
        # ama yanina SON 30 GUN ve son kesinti tarihi gelir; not gecmis
        # zamanla, tarihle yazilir.
        yakin = self.db.query(
            """SELECT COUNT(*) toplam,
                      SUM(json_durum IN ('kesildi', 'bos')) kesildi,
                      MAX(CASE WHEN json_durum IN ('kesildi', 'bos')
                               THEN run_ts END) son_kesinti
               FROM panel_runs
               WHERE ajan = ? AND sahip = ?
                 AND date(run_ts) >= date('now', '-30 days')""",
            (ajan, sahip))[0]
        son_kesinti = (str(yakin["son_kesinti"] or "")[:10]) or None
        y_toplam, y_kes = int(yakin["toplam"] or 0), int(yakin["kesildi"] or 0)
        return {"kosu": toplam, "uretti": int(r["uretti"] or 0),
                "kesildi": kesildi, "ajan_hatasi": int(r["hata"] or 0),
                "kayip_%": round(100 * kesildi / toplam, 1),
                "son_30_gun": {"kosu": y_toplam, "kesildi": y_kes},
                "son_kesinti": son_kesinti,
                "not": (None if not kesildi else
                        f"pencerede {kesildi}/{toplam} kosu cikti uretmeden "
                        "kapanmisti — o kosularin cagrilari defterde yok, "
                        "orneklem o kadar kucultuldu. SU ANKI durum: son 30 "
                        f"gunde {y_kes}/{y_toplam}"
                        + (f", son kesinti {son_kesinti}" if son_kesinti
                           else "") + ".")}

    def tez_kontrol(self, sahip: str) -> list[dict]:
        """
        Acik tahminlerin GECERSIZLESME KOSULUNU deterministik kontrol eder.

        Bu bir TAHMIN DEGIL, KOSUL KONTROLU: sistemin daha once acikca
        beyan ettigi bir esigin gerceklesip gerceklesmedigini soyler.
        Isabet orani olculmeden de durustce bildirilebilir olmasinin
        sebebi bu — al/sat sinyalinden ayrildigi nokta burasi.

        UC KURAL:
          * BIR KEZ tetiklenir (`tez_bozuldu_ts`). Aksi halde esigin
            altinda kalan bir kagit her gun alarm uretir ve kullanici
            bildirimleri kapatir; alarmin degeri nadirliginden gelir.
          * TAHMIN PUANLAMASINI ETKILEMEZ. `isabet` bagimsiz kalir ve
            ufuk dolunca normal sekilde olculur — iki ayri mekanizma.
          * Gramere uymayan kosul zaten KAYDEDILMEMIS olur; buraya
            gelirse (eski kayit) sessizce atlanir, uydurulmus bir yorum
            yapilmaz.

        DAMGA BURADA ATILMAZ — `tez_damgala()` ile ve TESLIMATTAN SONRA.

        NEDEN AYRILDI (2026-08-21, canlida olculdu). Bu yontem damgayi
        kendisi atiyordu ve damga ile teslimat arasinda PANEL vardi:
        08:07:15'te ROSE'un tezi bozuldu, `tez_bozuldu_ts` YAZILDI,
        mesaj panelden sonra gidecegi icin beklemeye kaldi ve kosu
        08:25:01'de sure sinirinda OLDURULDU. Yukaridaki sorgu
        `tez_bozuldu_ts IS NULL` suzdugu icin o alarm BIR DAHA ASLA
        bildirilmeyecekti — sistemin en durust ciktisi tespit edilip
        sessizce yutuldu.

        Damgayi teslimattan sonraya almanin bedeli, teslimat ile damga
        arasinda olunursa AYNI alarmin bir kez daha gitmesi. Kalici
        kayip ile tekrar arasinda tercih yapiliyor ve tekrar seciliyor:
        gereksiz bir alarm rahatsiz eder, kaybolan bir alarm ZARAR
        ETTIRIR.
        """
        from . import tez as tezmod

        acik = self.db.query(
            """SELECT p.id, p.instrument_id, p.olusma_ts, p.ajan, p.tez,
                      p.gecersizlesme_kosulu, p.izlenecek_esik, i.symbol
               FROM predictions p JOIN instruments i ON i.id = p.instrument_id
               WHERE p.isabet IS NULL AND p.sahip = ?
                 AND p.gecersizlesme_kosulu IS NOT NULL
                 AND p.tez_bozuldu_ts IS NULL""", (sahip,))
        tetiklenen = []
        for p in acik:
            ayrisim = tezmod.kosul_ayristir(p["gecersizlesme_kosulu"])
            if not ayrisim:
                continue
            alan, op, esik = ayrisim
            deger = tezmod.alan_degeri(self.db, p["instrument_id"], alan)
            if not tezmod.tetiklendi_mi(deger, op, esik):
                continue
            tetiklenen.append({
                "id": p["id"], "sembol": p["symbol"], "ajan": p["ajan"],
                "olusma_ts": p["olusma_ts"], "tez": p["tez"],
                "kosul": p["gecersizlesme_kosulu"], "alan": alan,
                "deger": deger, "esik": esik,
                "izlenecek_esik": p["izlenecek_esik"]})
        if tetiklenen:
            log.info("[defter] tez bozuldu (HENUZ DAMGALANMADI): %s",
                     [t["sembol"] for t in tetiklenen])
        return tetiklenen

    def gun_ici_tez_kontrol(self, sahip: str) -> list[dict]:
        """
        Tez kosullarini SAATLIK barla kontrol eder. DAMGALAMAZ.

        YALNIZCA `close` KOSULLARI. Gramerdeki diger alanlar (rsi14,
        sma20/50/200, hacim_kat, car_t) GUNLUK gostergelerdir; saatlik
        bardan uretilen bir "RSI14", gunluk RSI ile ayni ad altinda
        BASKA bir sey olurdu ve iki katman birbiriyle celisirdi. O
        kosullar gunluk kosularda kontrol edilmeye devam ediyor.

        `Koruma.gun_ici_kontrol` ile ayni iki kapi: saatlik barin para
        birimi GUNLUK seriyle eslesmeli (aksi halde TRY bir esigi USD
        bir barla karsilastiririz) ve bar bayat olmamali.
        """
        from . import tez as tezmod
        from .koruma import Koruma
        from datetime import datetime as _dt

        simdi = datetime.now(timezone.utc)
        acik = self.db.query(
            """SELECT p.id, p.instrument_id, p.olusma_ts, p.ajan, p.tez,
                      p.gecersizlesme_kosulu, p.izlenecek_esik, i.symbol
               FROM predictions p JOIN instruments i ON i.id = p.instrument_id
               WHERE p.isabet IS NULL AND p.sahip = ?
                 AND p.gecersizlesme_kosulu IS NOT NULL
                 AND p.tez_bozuldu_ts IS NULL""", (sahip,))
        tetiklenen = []
        for p in acik:
            ayrisim = tezmod.kosul_ayristir(p["gecersizlesme_kosulu"])
            if not ayrisim:
                continue
            alan, op, esik = ayrisim
            if alan != "close":
                continue
            gunluk = self.db.fiyat_kaynagi(p["instrument_id"])
            barlar = self.db.saatlik_seri(p["instrument_id"], limit=2)
            if not barlar:
                continue
            son = barlar[-1]
            if (son["currency"] or None) != ((gunluk or {}).get("currency")
                                             or None):
                continue
            try:
                bar_an = _dt.strptime(str(son["ts"]), "%Y-%m-%d %H:%M").replace(
                    tzinfo=timezone.utc)
            except ValueError:
                continue
            if (simdi - bar_an).total_seconds() / 60 > \
                    Koruma.GUN_ICI_AZAMI_YAS_DK:
                continue
            deger = son["close"]
            if not tezmod.tetiklendi_mi(deger, op, esik):
                continue
            tetiklenen.append({
                "id": p["id"], "sembol": p["symbol"], "ajan": p["ajan"],
                "olusma_ts": p["olusma_ts"], "tez": p["tez"],
                "kosul": p["gecersizlesme_kosulu"], "alan": alan,
                "deger": deger, "esik": esik, "bar_ts": str(son["ts"]),
                "izlenecek_esik": p["izlenecek_esik"], "gun_ici": True})
        if tetiklenen:
            log.info("[defter] GUN ICI tez bozuldu (HENUZ DAMGALANMADI): %s",
                     [t["sembol"] for t in tetiklenen])
        return tetiklenen

    @staticmethod
    def tez_gruplari(bozulan: list[dict]) -> list[dict]:
        """
        Tetiklenen tez kayitlarini OLAY bazinda gruplar — SAF, db yok.
        `staticmethod`: db'ye dokunmadigi icin mesaj katmani onu bir
        `Defter` ornegi kurmadan cagirabiliyor.

        NEDEN VAR (Ali bildirdi, 2026-09-09): tek mesajda GDDY icin
        BIREBIR AYNI blok UC KEZ gitti. Sebep defterde degil
        teslimatta: strateji motoru her kirilimi BILEREK uc satir yazar
        (`strateji` = kuralin tamami, `strateji_secilen` = hesabin
        isleyebildigi alt kume, `strateji_llm` = modelin yorumu) cunku
        UC AYRI KARNE tutuluyor. Uc satirin tezi de, 2N stop kosulu da
        AYNI — seviyeler kuraldan geliyor, model hesap yapmiyor.
        `tez_kontrol` ise SATIR donduruyordu ve mesaj her satira bir
        blok yaziyordu.

        Ayni kusur bu depoda BIR KEZ ZATEN kapatilmisti — taktik
        katmaninda `taktikci._bugun_semboller`: "`kaydet` ikinci kaydi
        yutuyor ama yutulan sey yalnizca SATIR; mesaj yine giderdi ve
        kullanici ayni kagit icin ayni taktigi tekrar tekrar okurdu.
        Kapi burada, TESLIMATTAN once." Tez alarminda o kapi yoktu.

        DEFTER DEGISMIYOR: uc satir da kalir (yoksa uc karne coker) ve
        `tez_damgala` UCUNU DE damgalar — biri damgasiz kalirsa sonraki
        kosu ayni alarmi yeniden gonderir.

        Anahtar `(sembol, kosul)`: `deger` ayni enstrumanin ayni
        alanindan hesaplandigi icin grup icinde zaten ozdes.
        """
        gruplar: dict[tuple, dict] = {}
        for b in bozulan:
            anahtar = (str(b.get("sembol") or "").upper(),
                       str(b.get("kosul") or ""))
            g = gruplar.get(anahtar)
            if g is None:
                gruplar[anahtar] = {**b, "kaynaklar": [b.get("ajan")],
                                    "idler": [b.get("id")], "kayit": 1}
                continue
            g["kayit"] += 1
            g["idler"].append(b.get("id"))
            if b.get("ajan") not in g["kaynaklar"]:
                g["kaynaklar"].append(b.get("ajan"))
            # EN ERKEN KAYIT KAZANIR: ayni kosul iki gun yazildiysa
            # (bkz. `ayni_bar_suzgeci` oncesi REGN) olayin tarihi ilk
            # yazildigi gundur.
            if str(b.get("olusma_ts") or "") < str(g.get("olusma_ts") or ""):
                g["olusma_ts"] = b["olusma_ts"]
            if not g.get("tez") and b.get("tez"):
                g["tez"] = b["tez"]
        return list(gruplar.values())

    def tez_damgala(self, kayitlar: list[dict]) -> int:
        """
        Teslim edilmis tez alarmlarini "bir daha bildirme" diye isaretler.

        YALNIZCA TESLIMAT BASARILIYSA cagrilir. Gerekcesi
        `tez_kontrol`'un govdesinde: damga teslimattan once atilirsa,
        arada olen bir kosu alarmi KALICI olarak yutar.

        Cagiran taraf `tez_kontrol`'un dondurdugu sozlukleri geri verir;
        yalnizca `id` alani kullanilir.
        """
        idler = [(k["id"],) for k in kayitlar if k.get("id")]
        if not idler:
            return 0
        damga = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.db.tx() as c:
            c.executemany(
                "UPDATE predictions SET tez_bozuldu_ts = ? WHERE id = ? "
                # ZATEN DAMGALIYI EZME: teslimat iki kez denenirse ilk
                # damganin saati korunur, "ne zaman haber verildi"
                # sorusunun cevabi degismez.
                "AND tez_bozuldu_ts IS NULL",
                [(damga, i[0]) for i in idler])
        log.info("[defter] tez alarmi teslim edildi ve damgalandi: %s",
                 [k.get("sembol") for k in kayitlar])
        return len(idler)

    def _venue_kirilimi(self, kosul: str, arg: list) -> dict:
        """
        Karnenin SAYDIGI satirlarin venue dagilimi — karneyle AYNI suzgec.

        OLCULEN KUSUR (2026-10-02): `ajan='hakem'` koda sabitti; taktik
        karnesinde "BUX 113, BIST 107, BINANCE 18" (hakemin 238'i)
        gorunuyordu, taktigin gercek dagilimi BIST 42, BUX 1. Alt
        sorgular karnenin `kosul/arg`ini MIRAS alir, kendi suzgecini
        yazmaz.
        """
        return {r["venue"]: r["n"] for r in self.db.query(
            f"""SELECT i.venue, COUNT(*) n FROM
                  (SELECT instrument_id FROM predictions
                   WHERE isabet IS NOT NULL AND olusma_ts >= ? AND ajan = ?
                     AND sahip = ?{kosul}) p
                JOIN instruments i ON i.id = p.instrument_id
                GROUP BY i.venue ORDER BY n DESC""", arg)}

    def hakem_sapmasi(self, sahip: str, gun: int = 180) -> dict:
        """
        Hakem katmani BILGI URETIYOR MU, YOK MU EDIYOR?

        Hakem ajanlari bastirip one cikariyor. Ajanlarin cogunlugu bir yon
        soylerken hakem tersini secip YANILIYORSA, bu katman bilgi imha
        ediyor demektir — ve bu duzeltilebilir bir kusurdur. Ne `karne`
        (yalnizca hakem) ne `ajan_karnesi` (ajan basina) bunu gosterir;
        ikisi de mutlak isabet olcer, ARALARINDAKI FARKI olcmez.

        Olculen: ayni (enstruman, gun) kumesinde ajan cogunlugunun yonu ile
        hakemin yonu. Ayrildiklari durumlarda kim hakli cikmis?

        n kucukken hicbir sey iddia edilemez; `yeterli_mi` bunu tasir.
        """
        sinir = (datetime.now(timezone.utc) - timedelta(days=gun)).strftime("%Y-%m-%d")
        satirlar = self.db.query(
            """SELECT h.instrument_id, h.olusma_ts, h.yon hakem_yon,
                      h.isabet hakem_isabet
               FROM predictions h
               WHERE h.ajan = 'hakem' AND h.isabet IS NOT NULL
                 AND h.olusma_ts >= ? AND h.sahip = ?""", (sinir, sahip))
        ayrisan = hakem_hakli = panel_hakli = uyusan = 0
        for r in satirlar:
            oylar = self.db.query(
                """SELECT yon, COUNT(*) n, SUM(isabet) d FROM predictions
                   WHERE instrument_id=? AND olusma_ts=? AND ajan<>'hakem'
                     AND isabet IS NOT NULL AND sahip=?
                   GROUP BY yon ORDER BY n DESC""",
                (r["instrument_id"], r["olusma_ts"], sahip))
            if not oylar:
                continue
            cogunluk = oylar[0]
            # Berabere kalan oylama "cogunluk" saymaz: iki yon esit oy
            # aldiysa panelin bir yonu yok, hakemle karsilastirilamaz.
            if len(oylar) > 1 and oylar[1]["n"] == cogunluk["n"]:
                continue
            if cogunluk["yon"] == r["hakem_yon"]:
                uyusan += 1
                continue
            ayrisan += 1
            if r["hakem_isabet"]:
                hakem_hakli += 1
            elif cogunluk["d"]:
                panel_hakli += 1
        return {"karsilastirilan": uyusan + ayrisan, "uyusan": uyusan,
                "ayrisan": ayrisan, "ayrismada_hakem_hakli": hakem_hakli,
                "ayrismada_panel_hakli": panel_hakli,
                "yeterli_mi": ayrisan >= 20,
                "not": ("ORNEKLEM YETERSIZ — hakem katmaninin katkisi hakkinda "
                        "sonuc cikarma" if ayrisan < 20 else
                        "Panel surekli hakli cikiyorsa hakem bilgi imha ediyor")}

    def ajan_karnesi(self, sahip: str, gun: int = 180) -> list[dict]:
        """
        Hangi ajanin gorusu daha cok tutuyor — `ajan` KOLONUNDAN.

        Onceden `gerekce LIKE '[ajan]%'` ile calisiyordu ve bu iki kez
        yanliydi: (a) gerekce metni bicimini degistirirse esleme sessizce
        kesilirdi, (b) daha onemlisi, defter ayni sembolde yalnizca en
        yuksek guvenli gorusu sakladigi icin sorgu SADECE HAYATTA KALAN
        tahminleri sayiyordu. Ikinci kusur artik semada cozuldu; bu sorgu
        da kolona tasindi.

        'hakem' ayrica raporlanir: kullanicinin OKUDUGU sey odur.
        """
        sinir = (datetime.now(timezone.utc) - timedelta(days=gun)).strftime("%Y-%m-%d")
        satirlar = self.db.query(
            f"""SELECT ajan, COUNT(*) n, SUM(isabet) d,
                       AVG({YONLU_ANORMAL}) yonlu
                FROM predictions
                WHERE isabet IS NOT NULL AND olusma_ts >= ? AND sahip = ?
                GROUP BY ajan ORDER BY n DESC""", (sinir, sahip))
        # KAZANC YONLU (bkz. `YONLU_ANORMAL`): eski `ort_anormal_%` kagidin
        # hareketini ortaliyordu ve dogru "asagi" cagrilari onu eksiye
        # cekiyordu. Ajanin yalnizca `notr` cagrisi varsa deger None.
        return [{"ajan": r["ajan"], "olcum": r["n"],
                 "isabet_%": round((r["d"] or 0) / r["n"] * 100, 1),
                 "yonlu_anormal_%": (round(r["yonlu"], 2)
                                     if r["yonlu"] is not None else None),
                 # Karneden SONUC CIKARMA esigi. Defterin kendi disiplini:
                 # n<20'de yon iddiasi kurulamaz (n=20, p=0.5'te Wilson
                 # araligi kabaca ±%22).
                 "yeterli_mi": r["n"] >= 20}
                for r in satirlar]
