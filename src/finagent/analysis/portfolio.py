"""Portfoy metrikleri — anlik goruntu + BUGUNKU fiyatla canli degerleme."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _turetilmis_pnl(market_value: float | None, pnl_pct: float | None) -> float | None:
    """
    Mutlak K/Z'yi deger ve yuzdeden geri hesaplar.

    NEDEN GEREKLI: ekran goruntusu okuyucusu `pnl_abs` alanini cogu zaman
    getiremiyor (BUX ekraninda mutlak K/Z yazmiyor, yuzde yaziyor). Onceden
    `pnl or 0.0` yaziliyordu ve rapor "Toplam K/Z 0.00" diye BEYAN ediyordu —
    bilinmeyen bir degeri sifir diye sunmak, en kotu turden sessiz yalan.

    Aritmetik: deger = maliyet x (1 + pct/100)  ->  maliyet = deger / (1+pct/100)
               K/Z   = deger - maliyet = deger x pct / (100 + pct)
    """
    if market_value is None or pnl_pct is None:
        return None
    payda = 100.0 + pnl_pct
    if abs(payda) < 1e-9:                     # -%100: maliyetin tamami silinmis
        return None
    return market_value * pnl_pct / payda


def _canli_fiyat(db, instrument_id: int) -> dict | None:
    """
    Enstrumanin son kapanisi + bir onceki kapanis (gunluk degisim icin).

    `fiyat_serisi()` uzerinden okunur: `prices` ayni enstruman icin farkli
    KAYNAK ve farkli PARA BIRIMINDE seri tutabiliyor ve dogrudan sorgu
    ikisini karistirir.
    """
    seri = db.fiyat_serisi(instrument_id, 2)
    if not seri:
        return None
    son = seri[-1]
    onceki = seri[-2] if len(seri) > 1 else None
    kapanis = son["close"]
    if kapanis is None:
        return None
    degisim = None
    if onceki is not None and onceki["close"]:
        degisim = (kapanis / onceki["close"] - 1) * 100
    return {
        "kapanis": float(kapanis),
        # ONCEKI KAPANIS DA DONUYOR: gunluk degisimi AGIRLIKLI
        # hesaplayabilmek icin yuzde yetmez, iki fiyat da gerekir.
        # Yuzdeden geri turetmek (kapanis / (1 + d/100)) yuvarlama
        # hatasini agirliga tasirdi.
        "onceki_kapanis": (float(onceki["close"])
                           if onceki is not None and onceki["close"] else None),
        "tarih": son["ts"],
        "onceki_tarih": onceki["ts"] if onceki is not None else None,
        "para_birimi": son["currency"],
        "kaynak": son["source"],
        "gun_degisim_%": None if degisim is None else round(degisim, 2),
    }


# GUNLUK DEGISIM ICIN ASGARI KAPSAM.
#
# Portfoyun %80'ini fiyatlayamiyorsak "portfoy +%0,4" demek YANLIS
# BEYANDIR: sayi dogru hesaplanmis olsa bile TEMSIL ETTIGI sey portfoy
# degil, portfoyun olculebilen parcasidir. Esigin altinda satir HIC
# gorunmez — eksik oldugu soylenmeyen bir sayi, TAM sanilir.
KAPSAM_ESIGI = 0.80

# ADET HANGI YOLDAN GELIYOR — bayatligin SEBEBI hesaba gore farkli ve
# kullaniciya soylenecek CUMLE de farkli.
#
# OLCULEN KUSUR (2026-09-01, Ali bildirdi): sabah taramasi IBKR icin
# "4 gun onceki ekran goruntusu, arada islem yaptiysan agirliklar eski"
# diyordu. IBKR'de EKRAN GORUNTUSU YOK — canli API var ve calisiyor.
# Ustelik ayni tarama "tek kalem: KO" diyordu; KO bir gun once
# SATILMISTI ve elde VRT vardi.
#
# Yani cumle yalnizca yanlis degildi, kullaniciya YANLIS IS yaptiriyordu:
# "ekran goruntusu gonder" deniyordu, oysa gereken tek sey collector'un
# kosmasiydi (`ibkr` hicbir zamanli kosumun `kaynaklar` listesinde yoktu).
#
# BURADA LISTELENMEYEN HESAP "ekran" SAYILIR. Varsayilan bilincli:
# yeni bir araci kurum eklendiginde API'si oldugunu VARSAYMAK, olmayan
# bir tazelik iddiasi olurdu.
ADET_KAYNAGI = {
    "ibkr": "api",          # Client Portal Gateway — gunluk giris gerektirir
    "bux": "ekran",         # mobil-only
    "midas": "ekran",       # mobil-only
    "binance": "ekran",     # ekran goruntusundan; fiyat API'den
}


def gunluk_degisim(db, hesap: str, sahip: str) -> dict | None:
    """
    Bir hesabin GUNLUK degisimi — mevcut pozisyonlar, iki kapanis.

    NEDEN "SON IKI SNAPSHOT FARKI" DEGIL
      `positions` anlik goruntuleri PARCALI: ekran goruntusu ne
      gosteriyorsa o kadar satir yaziliyor. Olculdu (2026-08-20):
      ali'nin dort goruntusu sirasiyla 18 / 2 / 1 / 4 satir. Iki
      goruntuyu birbirinden cikarmak 4 pozisyonu 1 pozisyonla
      karsilastirmak olurdu — sayi cikar ama hicbir seyi olcmez.
      Dogru olcum: BUGUNKU pozisyonlar, fiyat serisinden IKI KAPANIS.

    KUR ETKISI DISARIDA. Her enstruman icin AYNI (guncel) kur iki gune
    de uygulaniyor, yani cikan sayi yalnizca FIYAT hareketini olcer.
    Kur etkisini de katmak tarihsel kur serisi ister; `fx_rates` gunluk
    dolu degil ve eksik kuru "bugunkuyle ayni" saymak sessiz bir
    varsayim olurdu. Ne olculdugu ciktida BEYAN EDILIYOR.

    ADETLERIN YASI DA BEYAN EDILIYOR. Fiyat gunluk tazeleniyor ama ADET
    yalnizca yeni bir ekran goruntusu geldiginde degisiyor; ikisi AYNI
    SATIRDA gorunup ayni tazelikte SANILIYOR. Olculdu 2026-08-20:
    bux fiyatlari 19 Agustos, adetleri 14 Agustos — alti gun. Arada
    islem yapildiysa agirliklar yanlis ve bunu VERIDEN bilemeyiz;
    bilemedigimiz seyi soylemek yerine TARIHI soyluyoruz.

    Doner: None (olculemedi) ya da
      {hesap, para_birimi, degisim_%, kapsam, en_cok, en_az, tarih,
       adet_tarihi, adet_yas_gun}
    """
    rows = db.latest_positions(hesap, sahip)
    if not rows:
        return None
    adet_ts = max((r["snapshot_ts"] for r in rows if r["snapshot_ts"]),
                  default=None)

    ccy_sayac: dict[str, float] = {}
    for r in rows:
        c = (r["currency"] or "").upper()
        if c:
            ccy_sayac[c] = ccy_sayac.get(c, 0.0) + (r["market_value"] or 0.0)
    if not ccy_sayac:
        return None
    hesap_ccy = max(ccy_sayac, key=lambda k: ccy_sayac[k])

    bugun = onceki = 0.0
    kapsanan = toplam_deger = 0.0
    hareketler: list[tuple[str, float]] = []
    tarih = None

    for r in rows:
        mv = r["market_value"] or 0.0
        toplam_deger += mv
        nakit = (r["asset_type"] == "cash" or r["symbol"] == "CASH")
        if nakit:
            # NAKIT HAREKET ETMEZ ama portfoyun PARCASIDIR: paydaya
            # girer, yoksa yuzde oldugundan buyuk cikar.
            bugun += mv
            onceki += mv
            kapsanan += mv
            continue
        canli = _canli_fiyat(db, r["instrument_id"])
        if not canli or not canli["onceki_kapanis"] or not r["quantity"]:
            continue                                  # kapsam disi, sayilir
        kur = 1.0
        seri_ccy = (canli["para_birimi"] or "").upper()
        if seri_ccy and seri_ccy != hesap_ccy:
            k = db.fx_kuru(seri_ccy, hesap_ccy)
            if not k:
                continue                              # cevrilemedi -> kapsam disi
            kur = k["rate"]
        bugun += r["quantity"] * canli["kapanis"] * kur
        onceki += r["quantity"] * canli["onceki_kapanis"] * kur
        kapsanan += mv
        tarih = tarih or canli["tarih"]
        if canli["gun_degisim_%"] is not None:
            hareketler.append((r["symbol"], canli["gun_degisim_%"]))

    if not onceki or not toplam_deger:
        return None
    kapsam = kapsanan / toplam_deger
    if kapsam < KAPSAM_ESIGI:
        # SESSIZ DEGIL: cagiran taraf neden satir olmadigini bilsin.
        return {"hesap": hesap, "kapsam": round(kapsam, 3),
                "yetersiz_kapsam": True}
    hareketler.sort(key=lambda x: -x[1])
    return {
        "hesap": hesap,
        "para_birimi": hesap_ccy,
        "degisim_%": round((bugun / onceki - 1) * 100, 2),
        "kapsam": round(kapsam, 3),
        "tarih": tarih,
        # ADETIN TARIHI, fiyatinkinden AYRI alan. Ayni alanda birlestirmek
        # tam da gizlemek istedigimiz seyi gizlerdi.
        "adet_tarihi": (str(adet_ts)[:10] if adet_ts else None),
        "adet_yas_gun": _gun_farki(adet_ts, tarih),
        # ADET NEREDEN GELIYOR — bayatligin SEBEBI hesaba gore farkli.
        #
        # BUX/Midas/Binance mobil-only: adet ancak yeni bir EKRAN
        # GORUNTUSU geldiginde degisir, yani bayatlik VERI KAYNAGI
        # SINIRIDIR ve cozumu kullanicidadir.
        #
        # IBKR'de canli API var: bayatlik bir SINIR degil, TAZELENMEMIS
        # olmasidir. Ikisine ayni cumleyi kurmak (2026-09-01'e kadar
        # oyleydi) kullaniciya YANLIS IS yaptirir — Ali'ye "ekran
        # goruntusu gonder" deniyordu, oysa gereken tek sey collector'un
        # kosmasiydi.
        "adet_kaynagi": ADET_KAYNAGI.get(str(hesap).lower(), "ekran"),
        "en_cok": hareketler[0] if hareketler else None,
        "en_az": hareketler[-1] if len(hareketler) > 1 else None,
        "not": "kur etkisi haric (fiyat hareketi)",
    }


def _gun_farki(adet_ts, fiyat_ts) -> int | None:
    """
    Adet anlik goruntusu, kullanilan FIYAT BARINDAN kac gun eski?

    Bugune gore DEGIL bara gore: hafta sonu ya da bayat bir seride
    "bugun"e gore olcmek olmayan bir bayatlik uydururdu — `screener.
    _gun_farki` ve `piyasa` katmanindaki ayni disiplin.

    Ayristirilamayan tarih None doner; UYDURMA SAYI YOK.
    """
    from datetime import date
    try:
        a = date.fromisoformat(str(adet_ts)[:10])
        f = date.fromisoformat(str(fiyat_ts)[:10])
    except (TypeError, ValueError):
        return None
    return max(0, (f - a).days)


def portfolio_summary(db, accounts: list[str], sahip: str) -> dict:
    result: dict = {"hesaplar": {}, "toplam": {}}
    grand_value = 0.0
    grand_value_bugun = 0.0
    grand_pnl = 0.0
    pnl_var = False
    pnl_eksik = 0
    bugun_eksik: list[str] = []

    for acct in accounts:
        rows = db.latest_positions(acct, sahip)
        if not rows:
            result["hesaplar"][acct] = {"durum": "pozisyon verisi yok"}
            continue

        positions = []
        total_val = 0.0
        total_val_bugun = 0.0
        total_pnl = 0.0
        hesap_pnl_var = False
        for r in rows:
            mv = r["market_value"] or 0.0
            total_val += mv

            # --- BUGUNKU fiyatla canli deger -----------------------------
            # Anlik goruntu gunlerce eski olabiliyor (ekran goruntusu ne
            # zaman gonderildiyse o). Adet elimizde, bugunku kapanis da —
            # "veri eski" demek yerine YENIDEN DEGERLEMEK dogrusu.
            canli = _canli_fiyat(db, r["instrument_id"])
            deger_bugun = None
            fiyat_notu = None
            if r["asset_type"] == "cash" or r["symbol"] == "CASH":
                deger_bugun = round(mv, 2)    # nakit hareket etmez
                total_val_bugun += deger_bugun
            elif canli and r["quantity"]:
                birim = round(r["quantity"] * canli["kapanis"], 2)
                seri_ccy = (canli["para_birimi"] or "").upper()
                poz_ccy = (r["currency"] or "").upper()
                if seri_ccy and poz_ccy and seri_ccy != poz_ccy:
                    # PARA BIRIMI ESLESMIYORSA HAM CARPMA YAPILMAZ; kurla
                    # CEVRILIR ve KULLANILAN KUR YAZILIR. 17 pozisyonun
                    # 14'unde yanlis fiyat tam bu adim atlandigi icin
                    # olusmustu. Cevirmemek de secenek degil: portfoyun
                    # yarisi Yahoo'da USD kote ve cevrilmezse "canli
                    # deger" pozisyonlarin yalnizca yarisini kapsar.
                    kur = db.fx_kuru(seri_ccy, poz_ccy)
                    if kur:
                        deger_bugun = round(birim * kur["rate"], 2)
                        total_val_bugun += deger_bugun
                        fiyat_notu = (f"{seri_ccy}->{poz_ccy} @ {kur['rate']:.4f} "
                                      f"({kur['ts']}, {kur['kaynak']})")
                    else:
                        fiyat_notu = (f"seri {seri_ccy}, pozisyon {poz_ccy} — "
                                      f"kur bulunamadi, cevrilmedi")
                else:
                    deger_bugun = birim
                    total_val_bugun += deger_bugun
            if deger_bugun is None:
                bugun_eksik.append(r["symbol"])
                total_val_bugun += mv        # elde ne varsa o; toplam bozulmasin

            # --- K/Z: UC KAYNAK, SIRASI ONEMLI ---------------------------
            #
            # NULL ile 0 AYRI SEYLER — bu kural degismedi. Degisen, hangi
            # kaynagin ONDE geldigi.
            #
            # 1) MALIYET: adet ve ort. maliyet YALNIZCA kullanicinin
            #    bildigi seylerdir ve turetilemezler. Ikisi elimizdeyse
            #    K/Z BUGUNKU fiyattan hesaplanir ve fiyatla birlikte
            #    HAREKET EDER.
            # 2/3) `pnl_abs` ve `pnl_pct` ekran goruntusunden gelir ve
            #    o anda DONAR. Olculdu (2026-08-20): BUX'ta ASML
            #    "+%121,52" gosteriyordu — 14 Agustos ekranindan kalma
            #    bir sayi. Alti gun boyunca fiyat oynadi, o yuzde hic
            #    kipirdamadi ve "guncel" gibi duruyordu.
            #
            # Yani ekran sayilari YANLIS degil, ESKI. Elde daha iyisi
            # varken eskisini kullanmak icin sebep yok; yoksa hala
            # bos birakmaktan iyidir.
            pnl = pnl_kaynak = None
            pnl_yuzde = r["pnl_pct"]
            maliyet_toplam = None
            if r["avg_cost"] is not None and r["quantity"] and deger_bugun is not None:
                maliyet_toplam = r["quantity"] * r["avg_cost"]
                if maliyet_toplam:
                    pnl = deger_bugun - maliyet_toplam
                    pnl_yuzde = pnl / maliyet_toplam * 100
                    pnl_kaynak = "maliyet"
            if pnl is None:
                pnl = r["pnl_abs"]
                pnl_kaynak = "ekran" if pnl is not None else None
            if pnl is None:
                pnl = _turetilmis_pnl(r["market_value"], r["pnl_pct"])
                pnl_kaynak = "turetilmis" if pnl is not None else None
            if pnl is not None:
                total_pnl += pnl
                hesap_pnl_var = True
            else:
                pnl_eksik += 1

            positions.append({
                "sembol": r["symbol"],
                "adet": r["quantity"],
                "ort_maliyet": r["avg_cost"],
                "son_fiyat": r["last_price"],
                "deger": round(mv, 2),
                "deger_bugun": deger_bugun,
                "maliyet_toplam": (None if maliyet_toplam is None
                                   else round(maliyet_toplam, 2)),
                "kar_zarar": None if pnl is None else round(pnl, 2),
                "kar_zarar_kaynagi": pnl_kaynak,
                "kar_zarar_%": round(pnl_yuzde, 2) if pnl_yuzde is not None else None,
                "para_birimi": r["currency"],
                "son_kapanis": canli["kapanis"] if canli else None,
                "son_kapanis_tarih": canli["tarih"] if canli else None,
                "gun_degisim_%": canli["gun_degisim_%"] if canli else None,
                "fiyat_notu": fiyat_notu,
            })

        for p in positions:
            p["agirlik_%"] = round(p["deger"] / total_val * 100, 2) if total_val else None

        top = max(positions, key=lambda p: p["deger"], default=None)
        result["hesaplar"][acct] = {
            "snapshot": rows[0]["snapshot_ts"],
            "pozisyon_sayisi": len(positions),
            "toplam_deger": round(total_val, 2),
            "toplam_deger_bugunku_fiyatla": round(total_val_bugun, 2),
            "toplam_kar_zarar": round(total_pnl, 2) if hesap_pnl_var else None,
            "toplam_kar_zarar_%": round(total_pnl / (total_val - total_pnl) * 100, 2)
                                   if (hesap_pnl_var and (total_val - total_pnl)) else None,
            "en_buyuk_pozisyon": top["sembol"] if top else None,
            "yogunlasma_%": top["agirlik_%"] if top else None,
            "pozisyonlar": positions,
        }
        grand_value += total_val
        grand_value_bugun += total_val_bugun
        grand_pnl += total_pnl
        pnl_var = pnl_var or hesap_pnl_var

    result["toplam"] = {
        "deger": round(grand_value, 2),
        "deger_bugunku_fiyatla": round(grand_value_bugun, 2),
        # BILINMIYORSA None. `0.00` yazmak bilinmeyeni sifir diye sunmaktir.
        "kar_zarar": round(grand_pnl, 2) if pnl_var else None,
        "kar_zarar_durumu": (
            "hicbir pozisyonda mutlak K/Z yok (ekran goruntusu yalnizca yuzde veriyor)"
            if not pnl_var else
            (f"{pnl_eksik} pozisyonda mutlak K/Z hesaplanamadi"
             if pnl_eksik else "tam")),
        "not": ("HESAP ICINDE fiyatlar pozisyonun para birimine cevrildi (kullanilan "
                "kur her satirin `fiyat_notu` alaninda). HESAPLAR ARASI toplam "
                "CEVRILMEDI: BUX EUR, Binance USDT ve ikisi burada toplanmis "
                "durumda. Tek para biriminde bir toplam isteyen taraf `fx` "
                "araciyla cevirmeli."),
    }
    if bugun_eksik:
        result["toplam"]["bugunku_fiyat_bulunamayan"] = sorted(set(bugun_eksik))
    return result
