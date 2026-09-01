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
from datetime import datetime, timezone

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


def _sonraki_barlar(db, instrument_id: int, olusma_ts: str) -> list[dict]:
    """
    `olusma_ts`ten SONRAKI saatlik barlar. GECIKME KURALI BURADA.

    Yayim barinin KENDISI dahil edilmez: sinyalin uretildigi barda
    doldurulmus saymak uygulanabilir degildir.
    """
    barlar = [dict(b) for b in db.saatlik_seri(instrument_id, limit=72)]
    damga = str(olusma_ts or "")[:16].replace("T", " ")
    return [b for b in barlar if str(b["ts"])[:16] > damga]


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

    Doner: {"olculen", "atlanan", "sonuc": {etiket: adet}}

    KAPALI OLMAYAN BORSA ATLANIR, hata degil: seans surerken olcmek
    yarim bir gunu tam gun gibi raporlamak olurdu.
    """
    from ..piyasa import borsa_coz, seans_kapandi_mi

    simdi = simdi or datetime.now(timezone.utc)
    satirlar = db.query(
        f"""SELECT p.id, p.instrument_id, p.olusma_ts, p.taktik_tur,
                   p.taktik_giris, p.taktik_stop, p.baslangic_fiyat,
                   i.venue, i.symbol
            FROM predictions p JOIN instruments i ON i.id = p.instrument_id
            WHERE p.taktik_tur IN ({','.join('?' * len(OLCULEN_TURLER))})
              AND p.gun_sonu_sonuc IS NULL
            ORDER BY p.olusma_ts""", OLCULEN_TURLER)

    sonuc: dict[str, int] = {}
    olculen = atlanan = 0
    taban_onbellek: dict[tuple, float | None] = {}

    for s in satirlar:
        r = dict(s)
        tarih = str(r["olusma_ts"] or "")[:10]
        borsa = borsa_coz(db, r["instrument_id"], r["venue"])
        if seans_kapandi_mi(borsa, tarih, simdi) is not True:
            atlanan += 1                       # seans surer ya da bilinmiyor
            continue

        barlar = _sonraki_barlar(db, r["instrument_id"], r["olusma_ts"])
        if not barlar:
            # SAATLIK YOK (olculdu: BUX'ta 20 enstrumanin 7'sinde).
            # "Olculemedi" YAZILIYOR — sessizce atlamak, o taktigi
            # sonsuza dek olculmemis birakirdi.
            etiket = OLCULEMEDI
        elif r["taktik_tur"] == "alim":
            # REFERANS = YAYIM ANINDAKI FIYAT. Giris yonu bundan
            # turetiliyor; gecirilmezse yon ilk bardan tahmin edilir ve
            # kirilim taktikleri TERS olculur.
            etiket = _alim_sonucu(barlar, r["taktik_giris"],
                                  r["taktik_stop"], r["baslangic_fiyat"])
        else:
            etiket = _koruma_sonucu(barlar, r["taktik_stop"])

        anahtar = (r["venue"], str(r["olusma_ts"])[:13])
        if anahtar not in taban_onbellek:
            taban_onbellek[anahtar] = taban_oran(db, r["venue"], r["olusma_ts"])

        with db.tx() as c:
            c.execute(
                """UPDATE predictions SET gun_sonu_sonuc=?, gun_sonu_ts=?,
                                          gun_sonu_taban=?
                   WHERE id=?""",
                (etiket, simdi.isoformat(timespec="seconds"),
                 taban_onbellek[anahtar], r["id"]))
        sonuc[etiket] = sonuc.get(etiket, 0) + 1
        olculen += 1

    if olculen:
        log.info("[gun-sonu] %d taktik olculdu: %s", olculen, sonuc)
    return {"olculen": olculen, "atlanan": atlanan, "sonuc": sonuc}


def karne(db, gun: int = 30) -> dict:
    """
    Gun sonu karnesi — UFUK KARNESINDEN AYRI.

    `olculemedi` PAYDAYA GIRMEZ: olculemeyen bir taktigi basarisiz
    saymak, veri yoklugunu beceri yoklugu gibi gostermek olurdu.

    `giris_tetiklenmedi` de paydaya girmez: taktik uygulanamazdi, yani
    ne tuttu ne tutmadi.
    """
    satirlar = db.query(
        """SELECT gun_sonu_sonuc s, COUNT(*) n, AVG(gun_sonu_taban) taban
           FROM predictions
           WHERE gun_sonu_sonuc IS NOT NULL
             AND gun_sonu_ts >= datetime('now', ?)
           GROUP BY gun_sonu_sonuc""", (f"-{int(gun)} days",))
    dagilim = {r["s"]: r["n"] for r in satirlar}
    taban = next((r["taban"] for r in satirlar if r["taban"] is not None), None)

    payda = sum(n for s, n in dagilim.items()
                if s not in (OLCULEMEDI, GIRIS_YOK))
    basarili = sum(dagilim.get(s, 0) for s in BASARILI)
    return {
        "dagilim": dagilim,
        "olcum": payda,
        "ayakta": basarili,
        "oran_%": round(basarili / payda * 100, 1) if payda else None,
        "taban_%": round(taban * 100, 1) if taban is not None else None,
        # ESIK YOK ve BILEREK YOK: gun sonu ayakta kalma oraninin dogal
        # seviyesi HENUZ BILINMIYOR. Esik uydurmak, olcmeden karar
        # vermek olurdu; taban oran birikince konacak.
        "not": ("ORNEKLEM YETERSIZ — sonuc cikarma" if payda < 20 else None),
    }
