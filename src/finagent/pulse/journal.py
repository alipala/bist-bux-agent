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


def _bugun() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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


class Defter:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------
    def kaydet(self, gorusler: list[dict], sahip: str) -> dict:
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
                "fiyat": seri[-1]["close"], "ccy": seri[-1]["currency"]}

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
                   (olusma_ts, instrument_id, ajan, signal_id, yon, ufuk_gun,
                    guven, gerekce, tez, gecersizlesme_kosulu, izlenecek_esik,
                    taktik_tur, taktik_giris, taktik_stop,
                    taktik_giris_kaynak, taktik_stop_kaynak,
                    baslangic_fiyat, para_birimi, sahip)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(olusma_ts, instrument_id, ufuk_gun, ajan, sahip)
                   DO NOTHING""",
                [(ts, v["iid"], v["ajan"], v["signal_id"], v["yon"], v["ufuk"],
                  v["guven"], v["gerekce"], v["tez"], v["gecersizlesme"],
                  v["esik"], v["taktik_tur"], v["taktik_giris"],
                  v["taktik_stop"], v["taktik_giris_kaynak"],
                  v["taktik_stop_kaynak"], v["fiyat"], v["ccy"], sahip)
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
        bekleyen = self.db.query(
            """SELECT * FROM predictions WHERE isabet IS NULL
               ORDER BY olusma_ts""")
        olculen, kayitlar = 0, []
        for p in bekleyen:
            seri = self.db.fiyat_serisi(p["instrument_id"], 400)
            sonrasi = [r for r in seri if r["ts"] > p["olusma_ts"]]
            if len(sonrasi) < p["ufuk_gun"]:
                continue                       # ufuk dolmamis, bekle
            bitis = sonrasi[p["ufuk_gun"] - 1]
            if not bitis["close"] or not p["baslangic_fiyat"]:
                continue
            getiri = (bitis["close"] / p["baslangic_fiyat"] - 1) * 100

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
                             round(anormal, 3), isabet, p["id"]))
            olculen += 1

        if kayitlar:
            with self.db.tx() as c:
                c.executemany(
                    """UPDATE predictions SET olcum_ts=?, bitis_fiyat=?,
                       getiri_pct=?, piyasa_getiri_pct=?, anormal_pct=?, isabet=?
                       WHERE id=?""", kayitlar)
        # AD AYRIMI SART. `olculen_toplam` bu turda puanlanan TUM
        # tahminleri (dort ajan + hakem) sayar; `karne()` icindeki
        # `olcum` YALNIZCA hakem cagrilarini sayar. Ikisi ayni sozlukte
        # benzer adlarla durursa yanlis okunur — ve bu tam olarak
        # "beyan edilen sey ile gercek sey ayrisiyor" sinifidir.
        # Karne SAHIBE ait; puanlama herkes icin kostu ama rapor kisisel.
        return {"olculen_toplam": olculen,
                **(self.karne(sahip) if sahip else {"olcum": 0,
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
    def karne(self, sahip: str, gun: int = 180) -> dict:
        """
        Isabet karnesi — YALNIZCA HAKEMIN cagrilari uzerinden.

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
        r = self.db.query(
            """SELECT COUNT(*) n, SUM(isabet) d, AVG(anormal_pct) ort,
                      COUNT(DISTINCT instrument_id || olusma_ts) kume,
                      SUM(piyasa_getiri_pct IS NULL) vekilsiz
               FROM predictions
               WHERE isabet IS NOT NULL AND olusma_ts >= ? AND ajan = 'hakem'
                 AND sahip = ?""",
            (sinir, sahip))[0]
        n, dogru = r["n"] or 0, r["d"] or 0
        if not n:
            # Hakem tahmini yoksa SESSIZ KALMA: "olcum yok" ile "hakem
            # henuz puanlanmadi" ayri seyler ve ikincisi gecicidir.
            toplam = self.db.query(
                """SELECT COUNT(*) n FROM predictions
                   WHERE isabet IS NOT NULL AND olusma_ts >= ? AND sahip = ?""",
                (sinir, sahip))[0]["n"]
            return {"olcum": 0,
                    "not": ("henuz puanlanmis HAKEM cagrisi yok"
                            + (f" (ajan tahmini {toplam} puanlandi; karne "
                               "kullanicinin okudugu ozeti olcer)"
                               if toplam else ""))}
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
        kume = int(r["kume"] or n)
        n_etkin = max(1, min(kume, n))
        z = 1.96
        payda = 1 + z * z / n_etkin
        merkez = (p + z * z / (2 * n_etkin)) / payda
        yayilim = (z * math.sqrt(p * (1 - p) / n_etkin
                                 + z * z / (4 * n_etkin * n_etkin)) / payda)
        return {
            "olcum": n, "dogru": dogru, "isabet_%": round(p * 100, 1),
            "guven_araligi_%": [round(max(0, merkez - yayilim) * 100, 1),
                                round(min(1, merkez + yayilim) * 100, 1)],
            # ARALIGIN DAYANDIGI SAYI. Beyan edilmezse okuyan taraf
            # araligin neye gore hesaplandigini bilemez.
            "aralik_ornegi": n_etkin,
            "ortalama_anormal_getiri_%": round(r["ort"] or 0, 2),
            "kaynak": "hakem",
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
            "venue_kirilimi": self._venue_kirilimi(sinir, sahip),
            "yeterli_mi": n >= 20,
            "not": ("ORNEKLEM YETERSIZ — bu sayilardan sonuc cikarma"
                    if n < 20 else
                    "Komisyon sonrasi basabas ~%55 isabet gerektiriyor"),
        }

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

    def _venue_kirilimi(self, sinir: str, sahip: str) -> dict:
        """Puanlanmis hakem cagrilarinin venue dagilimi."""
        return {r["venue"]: r["n"] for r in self.db.query(
            """SELECT i.venue, COUNT(*) n FROM predictions p
               JOIN instruments i ON i.id = p.instrument_id
               WHERE p.isabet IS NOT NULL AND p.olusma_ts >= ?
                 AND p.ajan = 'hakem' AND p.sahip = ?
               GROUP BY i.venue ORDER BY n DESC""", (sinir, sahip))}

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
            """SELECT ajan, COUNT(*) n, SUM(isabet) d,
                      AVG(anormal_pct) ort_anormal
               FROM predictions
               WHERE isabet IS NOT NULL AND olusma_ts >= ? AND sahip = ?
               GROUP BY ajan ORDER BY n DESC""", (sinir, sahip))
        return [{"ajan": r["ajan"], "olcum": r["n"],
                 "isabet_%": round((r["d"] or 0) / r["n"] * 100, 1),
                 "ort_anormal_%": (round(r["ort_anormal"], 2)
                                   if r["ort_anormal"] is not None else None),
                 # Karneden SONUC CIKARMA esigi. Defterin kendi disiplini:
                 # n<20'de yon iddiasi kurulamaz (n=20, p=0.5'te Wilson
                 # araligi kabaca ±%22).
                 "yeterli_mi": r["n"] >= 20}
                for r in satirlar]
