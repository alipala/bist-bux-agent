"""
TAKTIK GUN SONU OLCUMU — seans kapandiktan sonra "tuttu mu" sorusu.

NEDEN VAR (2026-09-01, Ali istedi)
----------------------------------
Mevcut puanlama UFUK dolunca calisiyor (`Defter.puanla`, 3-30 gun).
Olculdu: 179 taktigin yalnizca 1'i puanlanmis, `taktik_tetiklendi` hepsinde
0. Bu tempoyla karne esigine (20 olcum) ulasmak aylar aliyor ve band o
sure boyunca "sicil yok" demeye devam ediyor.

Bu modul FARKLI bir soru soruyor ve cevabi AYNI AKSAM veriyor:

    ufuk puanlamasi : tez dogru muydu           -> 3-30 gun
    gun sonu olcumu : taktik UYGULANABILIR miydi,
                      SEANSI gecti mi           -> ayni aksam

IKISI AYNI KARNEYE KARISMAZ. Gun sonu kolay, ufuk zor; ayni kovada
birleslerse isabet orani yukari kayar ve hicbir sey ifade etmez.

BU MODUL BECERI OLCMEZ. Olctugu sey uygulanabilirlik ve dayaniklilik;
getiri kenari DEGIL. Bu deponun dort strateji sinavinda ogrendigi ders
burada da gecerli.

UC BARIYER (Lopez de Prado, 2018)
---------------------------------
Bir isleme uc sinir konur: ustte hedef, altta stop, sagda zaman. Sonuc,
fiyatin hangi sinira ONCE degdigiyle etiketlenir. Bizim veri modelimiz
zaten bu: `taktik_giris`, `taktik_stop`, `ufuk_gun`. Eksik olan tek sey
YOLUN degerlendirilmesiydi — ufuk sonundaki fiyata bakiliyordu, aradan
gecilen yola degil.

GECIKME KURALI — YAYIM BARI DAHIL DEGIL
---------------------------------------
Sinyalin uretildigi barda doldurulmus saymak UYGULANABILIR DEGILDIR ve
literaturde acikca oyle isaretlenir (Lag 0). Olcum `olusma_ts`ten SONRAKI
ilk bardan baslar. Bu kural gomulu ve testle bagli; aksi halde sonuclar
sistematik olarak IYIMSER cikar.

`bekle` OLCULMEZ — ALI'NIN KARARI
---------------------------------
"Bekle" bir islem degil, islem YAPMAMA onerisi; kendi seviyesi yok ve
"ayni gun tuttu mu" sorusunun karsiligi da yok. 85 taktigin (%47) olcume
girmemesi, uydurma bir tanimla olculmesinden iyidir.

TABAN ORAN ZORUNLU
------------------
Kiyassiz isabet orani tesadufu beceri gibi gosterir — analist tavsiyeleri
literaturunun ana bulgusu. "Taktiklerimin %60'i ayakta kaldi" ancak
piyasanin %80'i ayakta kaldiysa KOTU bir sonuctur. Her olcumun yaninda o
gun ayni borsadaki kagitlarin ne kadarinin ayakta kaldigi yaziliyor.

FREN BU OLCUME BAGLI DEGIL
--------------------------
Mevcut fren ufuk karnesine bakiyor (20 olcum, %50 esik). Gun sonu karnesi
cok daha hizli dolacak ama DAHA KOLAY bir seyi olcuyor: bir seansi
atlatmak, 20 gunluk tezin tutmasindan cok daha olasi. Ayni esigi
uygulamak freni hic devreye sokmaz ve SAHTE GUVEN uretir. Esik, taban
oran birkac hafta olculduktan sonra konacak.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

# YALNIZCA bu turler olculuyor. `bekle` bilerek disarida (bkz. baslik).
OLCULEN_TURLER = ("alim", "koruma")

# Sonuc etiketleri — "olculemedi" AYRI BIR SONUC, basarisizlik degil.
GIRIS_YOK = "giris_tetiklenmedi"
STOP_YENDI = "stop_yendi"
AYAKTA = "ayakta"
DAYANDI = "dayandi"
OLCULEMEDI = "olculemedi"

# Ayakta sayilan sonuclar — taban oranla kiyaslanan kume.
BASARILI = (AYAKTA, DAYANDI)

# PAYDA — OLCULEBILMIS taktikler. `olculemedi` (veri yok) ve
# `giris_tetiklenmedi` (taktik uygulanamazdi) disarida; gerekce
# `karne` icinde.
#
# SABIT OLARAK DURUYOR CUNKU IKI YERDE KULLANILIYOR: hem oran hem
# TABAN ayni satir kumesinden hesaplanmali. Ikisini ayri ayri yazmak,
# bu modulde ZATEN BIR KEZ OLCULEN kusuru davet ederdi (bkz.
# `taban_oran`: "TEST AYNI OLMAK ZORUNDA").
PAYDAYA_GIREN = BASARILI + (STOP_YENDI,)

# Farkin tesadufle aciklanip aciklanamayacaginin siniri. Istatistigin
# genel gelenegi; bu deponun sectigi bir esik DEGIL.
#
# BU BIR FREN ESIGI DEGILDIR ve olmamali: fren hala UFUK karnesine
# bagli. Buradaki sayi yalnizca "elimizdeki fark gurultuden ayirt
# edilebiliyor mu" sorusunu cevaplar.
ANLAMLILIK_P = 0.05


def _sonraki_barlar(db, instrument_id: int, olusma_ts: str,
                    yayim_ts: str | None = None) -> list[dict]:
    """
    Yayimdan SONRAKI saatlik barlar. GECIKME KURALI BURADA.

    Yayim barinin KENDISI dahil edilmez: sinyalin uretildigi barda
    doldurulmus saymak uygulanabilir degildir.

    DAMGA `yayim_ts`TEN GELIR — `olusma_ts` YETMIYORDU (olculdu
    2026-09-01). `olusma_ts` bir TARIH (1438/1438 satir 10 karakter) ve
    dizgi karsilastirmasinda gunun HER bari onu geciyordu:

        "2026-09-01 09:00" > "2026-09-01"  ->  True

    Yani taktik ogleden sonra yayimlansa bile sabahki barlar olcume
    giriyordu. Etkisi olculdu: ayakta orani %83,3 -> %82,0, ve 4 taktik
    `ayakta`dan `giris_tetiklenmedi`ye gecti (yayimdan onceki barlarla
    "girilmis" sayilanlar).

    `yayim_ts` YOKSA `olusma_ts`E DUSER. Bu, sema 29'dan onceki 179
    satirin davranisini AYNEN korur; gecmise damga uydurmak, olculmemis
    bir seyi olculmus gibi gostermek olurdu.
    """
    return _bar_durumu(db, instrument_id, olusma_ts, yayim_ts)[0]


# Bar bulunamamasinin IKI AYRI SEBEBI VAR ve ayni etiketi hak etmiyorlar.
SERI_YOK = "seri_yok"      # enstrumanin saatlik serisi HIC yok -> yapisal
ERKEN = "erken"            # seri var ama yayimdan sonrasi HENUZ gelmedi


def _ufuk_doldu(satir: dict, simdi: datetime) -> bool:
    """
    Tahminin kendi omru doldu mu? — BEKLEMENIN SINIRI.

    `ERKEN` durumundaki bir satir damgasiz birakiliyor ve her kosuda
    yeniden deneniyor. Sinirsiz beklemek, serisi olen bir enstrumanin
    satirini sonsuza dek kuyrukta tutardi.

    SINIR ICIN YENI BIR SABIT KONULMADI. Aday esikleri olcmeye
    calistim ve veri YETMEDI: yayimdan ilk bara kadar gecen sureyi
    ancak 7 satirda hesaplayabildim (saatlik seri 72 bar tutuyor, eski
    satirlarin penceresi kaymis). Yetersiz olcumden esik turetmek,
    "olculdu" gorunumunde bir tahmin olurdu.

    Bunun yerine satirin ZATEN TASIDIGI alan kullaniliyor: `ufuk_gun`.
    Ufuk dolduysa tahmin nasil olsa puanlanmis olur; "gun sonu
    uygulanabilir miydi" sorusunun cevabini beklemek anlamsiz.
    """
    try:
        ufuk = int(satir.get("ufuk_gun") or 0)
        baslangic = date.fromisoformat(str(satir.get("olusma_ts"))[:10])
    except (TypeError, ValueError):
        return True                      # tarih okunamiyorsa BEKLEME
    if ufuk <= 0:
        return True
    return (simdi.date() - baslangic).days > ufuk


def _bar_durumu(db, instrument_id: int, olusma_ts: str,
                yayim_ts: str | None = None) -> tuple[list[dict], str | None]:
    """
    (yayimdan sonraki barlar, sebep) — BOS DONUSUN SEBEBI AYRILIYOR.

    Onceden yalnizca liste donuyordu ve cagiran taraf bos listeyi tek bir
    sebebe baglayip `olculemedi` damgasi vuruyordu. Olculdu (2026-09-02,
    canli defter): ali'nin son 30 gunundeki 23 `olculemedi` satirinin

        17'si  saatlik serisi HIC OLMAYAN enstrumanlar (ASML, AVTX)
         6'si  serisi OLAN ama olcum ani COK ERKEN olanlar (AMZN, ALNY,
               REGN) — bugun bakildiginda 3'u OLCULEBILIR durumda

    Ikinci grup GERI ALINAMAZ sekilde kaybedilmisti: `olc()` yalnizca
    `gun_sonu_sonuc IS NULL` satirlari seciyor, yani bir kez damga
    vurulan satira bir daha bakilmiyor. AMZN 1 Eylul 21:04'te (ABD
    seansi 20:00'de kapandiktan SONRA) yayimlandi, olcum ertesi sabah
    06:05'te kostu — piyasa henuz acilmamisti, bar olmasi IMKANSIZDI —
    ve satir kalici olarak `olculemedi` yazildi. REGN daha da net:
    yayimdan BES SANIYE sonra olculdu.
    """
    barlar = [dict(b) for b in db.saatlik_seri(instrument_id, limit=72)]
    damga = str(yayim_ts or olusma_ts or "")[:16].replace("T", " ")
    sonraki = [b for b in barlar if str(b["ts"])[:16] > damga]
    if sonraki:
        return sonraki, None
    return [], (ERKEN if barlar else SERI_YOK)


def _alim_sonucu(barlar: list[dict], giris, stop, referans=None) -> str:
    """
    Uc bariyer, `alim` icin.

    GIRIS YONU REFERANS FIYATTAN TURETILIYOR — ILK YAZIMDA TURETILMIYORDU
    ve bu, taktiklerin yarisini TERS olcuyordu (2026-09-01, canli veride
    yakalandi):

        GOLTS  fiyat 301,25 · giris 297,887  -> LIMIT ALIS
               fiyat girisin USTUNDE, asagi cekilmesi bekleniyor.
               Tetik: dip <= giris.

        KBORU  fiyat 20,08  · giris 20,8223  -> KIRILIM
               fiyat girisin ALTINDA ("200 gunluk ortalama geri
               alinirsa tetiklenir"). Tetik: tepe >= giris.

    Tek kalip (`dip <= giris`) kullanilinca KBORU ILK BARDA girilmis
    sayiliyordu — fiyat zaten girisin altindaydi. Sonuc sistematik
    olarak KOTUMSER ve UYDURMA olurdu.

    GIRIS TETIKLENMEDIYSE BU BIR ISABET DEGIL, OLCULEMEZ BIR GUNDUR:
    taktik uygulanamazdi. Basari saymak da basarisizlik saymak da
    yanlis olurdu.
    """
    if giris is None:
        return GIRIS_YOK
    # Referans yoksa ilk barin kapanisi. Yon BILINMEDEN olcum yapilmaz.
    if referans is None:
        ilk = next((b.get("close") for b in barlar if b.get("close")), None)
        referans = ilk
    if referans is None:
        return GIRIS_YOK
    kirilim = referans < giris          # fiyat girisin ALTINDA -> yukari kirilim

    girdi = False
    for b in barlar:
        dusuk, yuksek = b.get("low"), b.get("high")
        if dusuk is None or yuksek is None:
            continue
        if not girdi:
            girdi = (yuksek >= giris) if kirilim else (dusuk <= giris)
        if girdi and stop is not None and dusuk <= stop:
            return STOP_YENDI
    return AYAKTA if girdi else GIRIS_YOK


def _koruma_sonucu(barlar: list[dict], stop) -> str:
    """`koruma`: giris yok, yalnizca stop yolu olculur."""
    for b in barlar:
        dusuk = b.get("low")
        if dusuk is not None and stop is not None and dusuk <= stop:
            return STOP_YENDI
    return DAYANDI


# Taban oranin kullandigi stop mesafesi — taktiklerin MEDYANI.
#
# Olculdu 2026-09-01: 94 taktikte medyan %7,5 (ceyrekler %5,5-%13,3).
STOP_MESAFESI = 0.075


def taban_oran(db, venue: str, olusma_ts: str, mesafe: float = STOP_MESAFESI,
               azami: int = 120) -> float | None:
    """
    O gun AYNI BORSADAKI kagitlarin ne kadari AYNI TESTI gecti.

    TEST AYNI OLMAK ZORUNDA — ILK YAZIMDA DEGILDI VE SAYIYI SISIRIYORDU.
    Ilk surumde taban "kapanis >= baslangic" diye olculuyordu, oysa
    taktigin testi "stop yenmedi". Bir kagit %3 dusup taktikte AYAKTA,
    tabanda BASARISIZ sayilabiliyordu. Canli olcumde bu, %76,3'e karsi
    %34,7 gibi etkileyici ama ANLAMSIZ bir fark uretti — farkin buyuk
    kismi tanim farkiydi, beceri degil.

    Simdi ikisi de ayni seyi soruyor: `mesafe` kadar dusmeden seansi
    gecti mi? Mesafe taktiklerin MEDYAN stop uzakligi.

    None = hesaplanamadi (yeterli kagit yok). SIFIR DEGIL.
    """
    idler = [r["id"] for r in db.query(
        """SELECT i.id FROM instruments i
           WHERE i.venue = ? AND i.id IN (SELECT instrument_id FROM prices_hourly)
           ORDER BY i.symbol LIMIT ?""", (venue, azami))]
    ayakta = toplam = 0
    for iid in idler:
        barlar = _sonraki_barlar(db, iid, olusma_ts)
        if len(barlar) < 2:
            continue
        bas = barlar[0].get("close")
        if not bas:
            continue
        esik = bas * (1 - mesafe)
        dipler = [b.get("low") for b in barlar if b.get("low") is not None]
        if not dipler:
            continue
        toplam += 1
        if min(dipler) > esik:
            ayakta += 1
    return round(ayakta / toplam, 3) if toplam >= 5 else None


def olc(db, simdi: datetime | None = None) -> dict:
    """
    Seansi kapanmis borsalardaki olculmemis taktikleri olcer.

    Doner: {"olculen", "atlanan", "bekleyen", "sonuc": {etiket: adet}}

    `bekleyen` = serisi olan ama yayimdan sonraki bari HENUZ gelmemis
    satirlar. Damga VURULMUYOR, sonraki kosuda tekrar bakiliyor.

    KAPALI OLMAYAN BORSA ATLANIR, hata degil: seans surerken olcmek
    yarim bir gunu tam gun gibi raporlamak olurdu.
    """
    from ..piyasa import borsa_coz, seans_kapandi_mi

    simdi = simdi or datetime.now(timezone.utc)
    satirlar = db.query(
        f"""SELECT p.id, p.instrument_id, p.olusma_ts, p.yayim_ts,
                   p.taktik_tur, p.taktik_giris, p.taktik_stop,
                   p.baslangic_fiyat, p.ufuk_gun, i.venue, i.symbol
            FROM predictions p JOIN instruments i ON i.id = p.instrument_id
            WHERE p.taktik_tur IN ({','.join('?' * len(OLCULEN_TURLER))})
              AND p.gun_sonu_sonuc IS NULL
            ORDER BY p.olusma_ts""", OLCULEN_TURLER)

    sonuc: dict[str, int] = {}
    olculen = atlanan = bekleyen = 0
    taban_onbellek: dict[tuple, float | None] = {}

    for s in satirlar:
        r = dict(s)
        tarih = str(r["olusma_ts"] or "")[:10]
        borsa = borsa_coz(db, r["instrument_id"], r["venue"])
        if seans_kapandi_mi(borsa, tarih, simdi) is not True:
            atlanan += 1                       # seans surer ya da bilinmiyor
            continue

        barlar, sebep = _bar_durumu(db, r["instrument_id"], r["olusma_ts"],
                                    r["yayim_ts"])
        if sebep == ERKEN and not _ufuk_doldu(r, simdi):
            # HENUZ DAMGA VURULMAZ — BU SATIR OLCULEBILIR HALE GELECEK.
            #
            # Seri VAR, yalnizca yayimdan sonraki barlar henuz gelmemis:
            # taktik seans kapandiktan SONRA yayimlanmis ve siradaki
            # seansin barlari daha olusmamis. Damga vurmak bu satiri
            # kalici olarak kaybetmek demek (`WHERE gun_sonu_sonuc IS
            # NULL`), oysa yarin ayni satir sorunsuz olculebilir.
            #
            # ESIK UYDURULMADI: vazgecme sinirini `ufuk_gun` veriyor —
            # tahminin kendi omru. O dolduysa "gun sonu uygulanabilir
            # miydi" sorusu zaten anlamsizlasmis demektir.
            #
            # AYRI SAYILIYOR, `atlanan`a KARISTIRILMIYOR: "seans surüyor"
            # gecici ve beklenen bir haldir, "bar bekliyor" ise YENI bir
            # kuyruk. Ikisi tek sayida birlesirse buyuyen bir kuyruk
            # gorunmez olur — bu deponun tekrar eden kusur sinifi.
            bekleyen += 1
            continue
        if not barlar:
            # SAATLIK SERI HIC YOK (olculdu: BUX arastirma hedeflerinin
            # 32/79'unda) ya da ufuk dolmus. Ikisinde de "olculemedi"
            # DOGRU cevap: sessizce atlamak o taktigi sonsuza dek
            # olculmemis birakirdi.
            etiket = OLCULEMEDI
        elif r["taktik_tur"] == "alim":
            # REFERANS = YAYIM ANINDAKI FIYAT. Giris yonu bundan
            # turetiliyor; gecirilmezse yon ilk bardan tahmin edilir ve
            # kirilim taktikleri TERS olculur.
            etiket = _alim_sonucu(barlar, r["taktik_giris"],
                                  r["taktik_stop"], r["baslangic_fiyat"])
        else:
            etiket = _koruma_sonucu(barlar, r["taktik_stop"])

        # TABAN AYNI PENCEREDEN OLCULUR. Taktik `yayim_ts`ten sonrasina
        # bakiyorsa taban da oyle bakmali; biri gunun basindan, digeri
        # ogleden sonra baslarsa tabanin dusme sansi DAHA UZUN bir
        # pencerede olculur ve taban SISER — yani sahte bir kenar
        # cikardi. Bu modulun ilk yaziminda tam bu sinif hata vardi.
        pencere = r["yayim_ts"] or r["olusma_ts"]
        # Onbellek anahtari SAATI de tasiyor ([:13]): sabah ve ogleden
        # sonra yayimlanan iki taktik ayni tabani PAYLASAMAZ.
        anahtar = (r["venue"], str(pencere)[:13])
        if anahtar not in taban_onbellek:
            taban_onbellek[anahtar] = taban_oran(db, r["venue"], pencere)

        with db.tx() as c:
            c.execute(
                """UPDATE predictions SET gun_sonu_sonuc=?, gun_sonu_ts=?,
                                          gun_sonu_taban=?
                   WHERE id=?""",
                (etiket, simdi.isoformat(timespec="seconds"),
                 taban_onbellek[anahtar], r["id"]))
        sonuc[etiket] = sonuc.get(etiket, 0) + 1
        olculen += 1

    if olculen or bekleyen:
        log.info("[gun-sonu] %d taktik olculdu, %d bar bekliyor, %d atlandi: %s",
                 olculen, bekleyen, atlanan, sonuc)
    return {"olculen": olculen, "atlanan": atlanan,
            "bekleyen": bekleyen, "sonuc": sonuc}


def _binom_kuyruk(n: int, k: int, p0: float) -> float:
    """
    P(X >= k | n, p0) — tek yonlu binom kuyrugu.

    Sordugu soru: "taban oran GERCEKTEN p0 olsaydi, en az k basari
    gormemiz ne kadar olasiydi?" Kucuk deger, farki tesadufle
    aciklamanin zor oldugunu soyler.

    TEK YONLU, cunku sorumuz tek yonlu: taktikler tabandan IYI mi.
    Cift yonlu kullanmak, "kotu olmasi" ihtimalini de payin icine
    katip bizim lehimize bir sayi uretirdi.
    """
    from math import comb

    if n <= 0 or not (0.0 < p0 < 1.0):
        return 1.0                        # HUKUM YOK -> kuyruk 1, anlamsiz
    n, k = int(n), max(0, min(int(k), int(n)))
    return sum(comb(n, i) * p0 ** i * (1.0 - p0) ** (n - i)
               for i in range(k, n + 1))


# `_gereken_n` taramasinin ust siniri. Bunun otesi pratikte "bu tempoyla
# gorulemez" demektir ve None donuyor — buyuk bir sayi UYDURMAKTANSA
# cevapsiz kalmak dogru.
GEREKEN_N_TAVANI = 400


def _gereken_n(oran: float, p0: float, tavan: int = GEREKEN_N_TAVANI) -> int | None:
    """
    AYNI ORAN KORUNURSA fark kac olcumde anlamli olur.

    Bu bir OLCUM DEGIL, bir izdusumdur: "bugunku oran aynen devam
    ederse". Kullanicinin "daha ne kadar bekleyecegim" sorusunun tek
    durust cevabi bu; alan adi da oyle okunmali.

    None = bu tavana kadar anlamli olmuyor ya da oran zaten tabanin
    altinda. SIFIR YA DA BUYUK BIR SAYI UYDURULMUYOR.

    ASAGI YUVARLIYOR (`int`, `round` DEGIL) — KENDI LEHIMIZE DEGIL.
    Olculdu: oran %82,7 / taban %72,1 icin `round` n=60 diyor (60'in
    %82,7'si 49,6 -> 50'ye YUKARI yuvarlaniyor, p=0,032), `int` ise
    n=80 (p=0,023). Aradaki fark beceri degil YUVARLAMA SANSI.
    Kullaniciya "daha ne kadar" diye giden bir sayinin kendi lehimize
    yuvarlanmasi, bu deponun tam da kacindigi sey.
    """
    if not (0.0 < p0 < 1.0) or oran <= p0:
        return None
    n = 10
    while n <= tavan:
        if _binom_kuyruk(n, int(n * oran), p0) < ANLAMLILIK_P:
            return n
        n += 10
    return None


def gunun_olcumu(db, sahip: str | None = None,
                 simdi: datetime | None = None) -> dict:
    """
    BUGUN olculenler — AKIS. `karne` ise STOK (son 30 gun).

    Bildirim akisi raporlar ("bugun 4 taktik olculdu"), sicil sorusu
    stogu. Ikisini tek sayiya indirmek, her aksam ayni 30 gunluk orani
    tekrar yollamak olurdu.

    `olusma_ts` DE TASINIYOR cunku olcum ile taktik AYNI GUNE ait
    olmayabilir: ABD seansi 23:00 TRT'de kapaniyor, nabiz 22:15'te
    kosuyor. ABD taktikleri ERTESI SABAH olculur ve o aksamki
    bildirimde gorunur — kendi tarihiyle.
    """
    simdi = simdi or datetime.now(timezone.utc)
    bugun = simdi.date().isoformat()
    kosul = "p.gun_sonu_ts >= ?"
    par: list = [bugun]
    if sahip:
        kosul += " AND p.sahip = ?"
        par.append(sahip)
    satirlar = db.query(
        f"""SELECT p.gun_sonu_sonuc s, p.olusma_ts, i.venue, i.symbol
            FROM predictions p JOIN instruments i ON i.id = p.instrument_id
            WHERE {kosul} AND p.gun_sonu_sonuc IS NOT NULL
            ORDER BY p.olusma_ts""", tuple(par))

    dagilim: dict[str, int] = {}
    venue: dict[str, int] = {}
    tarihler: set[str] = set()
    for r in satirlar:
        dagilim[r["s"]] = dagilim.get(r["s"], 0) + 1
        venue[r["venue"]] = venue.get(r["venue"], 0) + 1
        tarihler.add(str(r["olusma_ts"])[:10])
    return {
        "adet": len(satirlar),
        "dagilim": dagilim,
        "venue": venue,
        "taktik_tarihleri": sorted(tarihler),
        # OLCUM GUNU AYRICA TASINIYOR: "bugun olculdu" ile "bugun
        # verildi" ayni sey degil ve mesaj ikisini ayirt edebilmeli.
        "olcum_gunu": bugun,
        "sahip": sahip,
    }


def karne(db, gun: int = 30, sahip: str | None = None) -> dict:
    """
    Gun sonu karnesi — UFUK KARNESINDEN AYRI.

    `olculemedi` PAYDAYA GIRMEZ: olculemeyen bir taktigi basarisiz
    saymak, veri yoklugunu beceri yoklugu gibi gostermek olurdu.

    `giris_tetiklenmedi` de paydaya girmez: taktik uygulanamazdi, yani
    ne tuttu ne tutmadi.

    TABAN, PAYDANIN KENDI SATIRLARINDAN — OLCULEN KUSUR (2026-09-01)
    ---------------------------------------------------------------
    Ilk yazimda taban soyle aliniyordu:

        taban = next((r["taban"] for r in satirlar if ...), None)

    yani GROUP BY'in ILK grubunun ortalamasi. Canli veride bu `ayakta`
    grubuydu ve %74,6 raporlaniyordu; oysa paydanin (ayakta + dayandi +
    stop_yendi) gercek ortalama tabani %72,1. Gruplarin tabanlari
    birbirinden cok farkli (stop_yendi %61,6, olculemedi %92,3), yani
    hangi grubun once geldigi sayiyi degistiriyordu.

    Bu, bu modulde ZATEN BIR KEZ duzeltilmis kusurun ta kendisi: oran
    ile kiyas AYNI SATIR KUMESINDEN gelmeli. Simdi ikisi de
    `PAYDAYA_GIREN` uzerinden hesaplaniyor.

    ANLAMLILIK — `n >= 20` YETMIYOR
    -------------------------------
    Mevcut tek uyari `olcum < 20`'ydi. Canli veride payda 52'ye
    ulasinca uyari SUSUYOR ve karne "%82,7'ye karsi %72,1" diye temiz
    bir ustunluk gosteriyor. Oysa tek yonlu binom p = 0,057: fark
    henuz gurultuden ayirt EDILEMIYOR.

    Iki ayri soru, iki ayri alan: "yeterli olcum var mi" (`not`) ve
    "fark tesadufi olabilir mi" (`taban_farki_anlamli`).

    `sahip` SUZGECI ZORUNLU OLARAK GECILMELI — OLCULEN RISK
    -------------------------------------------------------
    Canli veride IKI sahip var (ali 42 olcum, yuksel 30). Suzgecsiz
    karne ikisini birlestirir; Ali kendi sicilini sorunca Yuksel'in
    taktikleri de sayiya girer. `[[cok-kullanicili-katman]]`: sahip
    PARAMETREDIR, varsayilani yoktur.

    None = TUM SAHIPLER ve bu bilincli bir cagri olmali; donen sozlukte
    `sahip` alani kapsami BEYAN EDIYOR ki okuyan yanlis okumasin.
    """
    ek = " AND sahip = ?" if sahip else ""
    sp: tuple = (sahip,) if sahip else ()
    satirlar = db.query(
        f"""SELECT gun_sonu_sonuc s, COUNT(*) n
            FROM predictions
            WHERE gun_sonu_sonuc IS NOT NULL
              AND gun_sonu_ts >= datetime('now', ?){ek}
            GROUP BY gun_sonu_sonuc""", (f"-{int(gun)} days", *sp))
    dagilim = {r["s"]: r["n"] for r in satirlar}

    # TABAN, ORANIN PAYDASIYLA AYNI SATIRLARDAN. Tek sorgu, tek kume.
    tb = db.query(
        f"""SELECT AVG(gun_sonu_taban) taban FROM predictions
            WHERE gun_sonu_sonuc IN ({','.join('?' * len(PAYDAYA_GIREN))})
              AND gun_sonu_ts >= datetime('now', ?){ek}""",
        (*PAYDAYA_GIREN, f"-{int(gun)} days", *sp))
    taban = tb[0]["taban"] if tb else None

    payda = sum(dagilim.get(s, 0) for s in PAYDAYA_GIREN)
    basarili = sum(dagilim.get(s, 0) for s in BASARILI)
    oran = basarili / payda if payda else None
    out = {
        # KAPSAM HER YANITTA BEYAN EDILIYOR: "kimin sicili" sorusunun
        # cevabi sayinin yaninda durmali, cagrida kalmamali.
        "sahip": sahip or "TUM SAHIPLER",
        "dagilim": dagilim,
        "olcum": payda,
        "ayakta": basarili,
        "oran_%": round(oran * 100, 1) if oran is not None else None,
        "taban_%": round(taban * 100, 1) if taban is not None else None,
        # ESIK YOK ve BILEREK YOK: gun sonu ayakta kalma oraninin dogal
        # seviyesi HENUZ BILINMIYOR. Esik uydurmak, olcmeden karar
        # vermek olurdu; taban oran birikince konacak.
        "not": ("ORNEKLEM YETERSIZ — sonuc cikarma" if payda < 20 else None),
    }

    # UC DURUM, IKI DEGIL — `seans_kapandi_mi` ile ayni disiplin:
    #   True  -> olculdu, fark gurultuden ayirt edilebiliyor
    #   False -> olculdu, AYIRT EDILEMIYOR
    #   None  -> HUKUM YOK (taban ya da olcum yok)
    # `False` ile `None`u birlestirmek, hesaplanamamis bir seyi
    # "anlamsiz cikti" diye raporlamak olurdu.
    if oran is None or taban is None:
        out["taban_farki_anlamli"] = None
        out["taban_farki_yok_sebep"] = (
            "olcum yok" if not payda else "taban orani hesaplanamadi")
        return out

    p = _binom_kuyruk(payda, basarili, taban)
    out["taban_farki_puan"] = round((oran - taban) * 100, 1)
    out["taban_farki_p"] = round(p, 3)
    out["taban_farki_anlamli"] = bool(p < ANLAMLILIK_P)
    if not out["taban_farki_anlamli"]:
        # IZDUSUM, OLCUM DEGIL — alan adi bunu soyluyor.
        out["ayni_oranla_gereken_n"] = _gereken_n(oran, taban)
    return out
