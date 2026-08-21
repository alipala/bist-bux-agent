"""
BOYUTLAMA — "portfoyun yuzde kacini riske atiyorsun" satiri.

NE VERIR, NE VERMEZ
-------------------
VERIR : giris ile stop arasindaki mesafeyi YUZDE olarak ve "bir islemde
        portfoyun %R'sini riske atmak istiyorsan bu pozisyon portfoyun
        en fazla %P'si olabilir" hesabini.
VERMEZ: adet, lot, tutar, kaldirac.

NEDEN TUTAR YAZILMIYOR — BILINCLI KARAR (2026-08-21)
----------------------------------------------------
Tutar yazmak icin portfoy degerinin GUNCEL ve TEK PARA BIRIMINDE
olmasi gerekir. Ikisi de garanti degil: portfoy ekran goruntusunden
geliyor ve gunlerce bayat kalabilir (olculdu: BUX defteri 14 Agustos'ta
donmustu), ustelik BUX EUR / Midas TRY / Binance USDT yan yana duruyor
ve cevrilmeden toplanan bir "portfoy degeri" TL pozisyonlarin agirligini
52 KAT sisirmisti.

Bayat bir toplamdan uretilen "3.500 TL'lik al" cumlesi, kullanicinin
DOGRULAYAMAYACAGI bir sayidir ve yanlis oldugunda hicbir sey uyarmaz.
Yuzde ise oransaldir: portfoy degeri bayat olsa bile "sermayenin %1'i"
ifadesi anlamini korur ve son carpani kullanici kendi yapar.

FORMUL
------
    stop_mesafesi = |giris - stop| / giris
    pozisyon_payi = risk_payi / stop_mesafesi

Ornek: %1 risk, stop girisin %5 altinda -> pozisyon portfoyun %20'si.
Ayni %1 risk, stop %2 altinda -> %50. Yani DAR STOP BUYUK POZISYON
demektir ve bu, boyutlamanin en cok yanlis anlasilan tarafi: dar stop
"daha az risk" degil, "ayni risk, daha buyuk pozisyon, daha erken
tetiklenme" demek.

UST SINIR VAR
-------------
Cok dar bir stop matematiksel olarak devasa bir pozisyon payi uretir
(%0,5 stop -> portfoyun %200'u). Formul dogru ama sonuc uygulanamaz:
kaldirac gerektirir ve bu sistem kaldirac ONERMEZ. `AZAMI_PAY` bunu
kesiyor ve KESILDIGINI SOYLUYOR — sessizce kirpmak, kullaniciya
matematigin ne dedigini gizlemek olurdu.
"""
from __future__ import annotations

# Bir islemde goze alinan portfoy yuzdesi. %1 klasik ve muhafazakar
# esik: ust uste 10 yanlis islem sermayenin ~%10'unu goturur, yani
# hata serisi hayatta kalinabilir kalir.
VARSAYILAN_RISK_PAYI = 1.0

# Tek pozisyonun portfoyde alabilecegi en buyuk pay. %25 secildi cunku
# tarayicinin `yogunlasma` alarmi da orada (`screener.YOGUNLASMA_ESIGI`)
# — iki katmanin ayni esigi farkli soylemesi, sistemin kendisiyle
# celismesi olurdu.
AZAMI_PAY = 25.0


def boyut(giris: float | None, stop: float | None,
          risk_payi: float = VARSAYILAN_RISK_PAYI) -> dict | None:
    """
    Doner: {"stop_mesafesi_pct", "risk_payi_pct", "pozisyon_payi_pct",
            "kesildi", "not"} — hesaplanamiyorsa None.
    """
    try:
        g, s = float(giris), float(stop)
    except (TypeError, ValueError):
        return None
    if g <= 0 or s <= 0 or g == s:
        return None
    mesafe = abs(g - s) / g          # ORAN (0,1493), yuzde DEGIL
    if mesafe <= 0:
        return None
    # BIRIM TUZAGI — ilk yazimda burada 100 kat hata vardi.
    # `risk_payi` YUZDE (1,0 = %1), `mesafe` ORAN (0,1493). Ikisinin
    # oraninin birimi de YUZDE olur. Paydayi 100 ile carpmak sonucu
    # 100'e boluyordu: ASML'de %6,7 yerine %0,1 cikti ve satir
    # "portfoyun binde biri" gibi okunuyordu. Modul basindaki ornek
    # (%1 risk + %5 stop -> %20) tam da bunu yakalamak icin yazilmisti
    # ve testte pinlendi.
    ham = risk_payi / mesafe
    kesildi = ham > AZAMI_PAY
    return {
        "stop_mesafesi_pct": round(mesafe * 100, 2),
        "risk_payi_pct": risk_payi,
        "pozisyon_payi_pct": round(min(ham, AZAMI_PAY), 1),
        "hesaplanan_pay_pct": round(ham, 1),
        "kesildi": kesildi,
        "not": (
            f"Portfoyun %{risk_payi:g}'ini riske atmak istiyorsan bu "
            f"pozisyon portfoyun en fazla %{min(ham, AZAMI_PAY):.1f}'i "
            "olabilir. TUTAR/ADET YAZILMIYOR: portfoy degeri ekran "
            "goruntusunden geliyor ve bayat olabilir; oran bayatliktan "
            "etkilenmez."
            + (f" HESAP %{ham:.0f} veriyor ama %{AZAMI_PAY:g} ile "
               "KESILDI — bu kadar dar bir stop kaldirac gerektirir ve "
               "sistem kaldirac onermez." if kesildi else "")),
    }


def satir(giris, stop, para_birimi: str | None = None,
          risk_payi: float = VARSAYILAN_RISK_PAYI) -> str | None:
    """Telegram mesajina konacak TEK satir. Hesaplanamiyorsa None."""
    b = boyut(giris, stop, risk_payi)
    if not b:
        return None
    # SAYILAR TURKCE YAZILIR. Mesajin geri kalani "6.959,05" derken bu
    # satirin "2.54" demesi, ayni mesajda IKI ayri sayi yazimi demekti.
    from .runner import _tr
    s = (f"Girisle stop arasi %{_tr(b['stop_mesafesi_pct'])} — "
         f"%{_tr(b['risk_payi_pct'], 0)} risk icin portfoyun "
         f"<b>%{_tr(b['pozisyon_payi_pct'])}</b>'i")
    if b["kesildi"]:
        s += (f" <i>(hesap %{_tr(b['hesaplanan_pay_pct'])} cikti, "
              "tavan uygulandi)</i>")
    return s
