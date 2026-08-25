#!/usr/bin/env python
"""
IBKR Client Portal Gateway BAGLANTI RAPORU — teshis betigi, veri yazmaz.

NEDEN VAR
---------
IBKR bagliligi bu depodaki diger kaynaklardan farkli: bir collector'i
cagirdiginda ya veri gelir ya hata. IBKR'de arada UC katman var ve ucu de
ayri ayri kirilir:

    1. Gateway sureci ayakta mi?          (java, localhost:5001)
    2. Kullanici adi kimlik dogruladi mi?  (tarayicidan ELLE giris)
    3. Brokerage oturumu acik mi?          (/iserver ucları buna bagli)

Ikisi acik ucuncusu kapali olabilir — ve o durumda `/portfolio` calisir ama
`/iserver/marketdata/snapshot` bos doner. Yani "veri yok" gibi gorunur.
Bu deponun en kotu hata sinifi tam olarak bu (bkz. yanlis "yok" beyani):
veri VARKEN yok demek. Bu betik, hangi katmanin kirildigini SOYLER.

TEK OTURUM KURALI
-----------------
IBKR bir kullanici adina TUM platformlarda tek brokerage oturumu veriyor.
Telefondaki IBKR uygulamasi ya da tarayicidan Client Portal girisi, API
oturumunu DUSURUR. Bu durum `competing: true` ile gorunur ve rapor bunu
ayrica yaziyor — cunku belirtisi "her sey calisiyordu, birden durdu".

HESAP KIMLIGI MASKELI
---------------------
Hesap numarasi varsayilan olarak maskelenir (U12***67). Canli/kagit ayrimi
maskeden bagimsiz olarak gosterilir, cunku ONEMLI OLAN O: `.env`'de "paper"
yazmasi hicbir sey kanitlamaz, hesap kimliginin kendisi kanitlar.
Tam kimlik gerekiyorsa: --acik

KULLANIM
    python scripts/ibkr_baglanti.py            # okunabilir rapor
    python scripts/ibkr_baglanti.py --json     # tek satir JSON
    python scripts/ibkr_baglanti.py --acik     # hesap kimligini maskeleme

CIKIS KODU
    0  brokerage oturumu ACIK    — /iserver uclari kullanilabilir
    1  kimlik dogrulanmis DEGIL  — tarayicidan giris gerekiyor
    2  gateway'e ulasilamiyor    — java sureci calismiyor olabilir
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK / "src"))

# Gateway kendinden imzali sertifika kullaniyor. IBKR bunu "beklenen" diyor:
# baglanti yalnizca kullanici ile KENDI localhost'u arasinda dogrulanmamis;
# localhost'tan IBKR'ye giden bacak TLS ile korunuyor.
DOGRULAMA = False

# macOS'ta 5000 portunu Control Center (AirPlay Receiver) tutuyor; IBKR
# kendi SSS'inde port degistirmeyi oneriyor. Bu depo 5001 kullaniyor
# (root/conf.finagent.yaml).
VARSAYILAN_TABAN = "https://localhost:5001/v1/api"

# IBKR'nin tek istedigi baslik. Gateway diger her seyi kendisi ekliyor.
BASLIKLAR = {"User-Agent": "finagent/1.0", "Content-Type": "application/json"}

ZAMAN_ASIMI = 10.0


def _taban() -> str:
    return os.getenv("IBKR_BASE_URL") or VARSAYILAN_TABAN


def _maskele(kimlik: str) -> str:
    """U1234567 -> U12***67. Kisa kimlikler oldugu gibi birakilir."""
    if len(kimlik) <= 5:
        return kimlik
    return f"{kimlik[:3]}***{kimlik[-2:]}"


def _kagit_mi(hesap: dict) -> bool | None:
    """
    Kagit hesap mi? ORTAM DEGISKENINE DEGIL, SUNUCUDAN DONENE bakar.

    IBKR'de canli/kagit ayrimi bir bayrak degil: ayri bir KULLANICI ADI.
    Hangi hesaba bagli oldugunu tarayiciya ne yazdigin belirler. Bu yuzden
    tek mesru kanit /portfolio/accounts yanitidir:
        - kagit hesap kimlikleri "DU" ile baslar
        - resmi ornekte kagit hesap "type": "DEMO" donuyor
    Ikisi de yoksa None doner — "bilmiyorum", "canli" DEGIL.
    """
    tip = str(hesap.get("type") or "").upper()
    kimlik = str(hesap.get("accountId") or hesap.get("id") or "")
    if tip == "DEMO" or kimlik.startswith("DU"):
        return True
    if tip or kimlik.startswith("U"):
        return False
    return None


def topla() -> dict:
    taban = _taban()
    r: dict = {
        "taban": taban,
        "ulasilabilir": False,
        "kimlik_dogrulandi": False,
        "bagli": False,
        "rakip_oturum": None,
        "oturum_bitis_sn": None,
        "hesaplar": [],
        "mesaj": "",
        "hata": None,
    }

    try:
        with httpx.Client(verify=DOGRULAMA, timeout=ZAMAN_ASIMI, headers=BASLIKLAR) as c:
            # --- 1. katman: gateway ayakta mi ---
            try:
                y = c.post(f"{taban}/iserver/auth/status", json={})
            except httpx.RequestError as e:
                r["hata"] = f"gateway'e ulasilamadi: {type(e).__name__}"
                return r

            r["ulasilabilir"] = True

            # --- 2. katman: kimlik dogrulandi mi ---
            # 401 = henuz tarayicidan giris yapilmamis. Bu bir HATA DEGIL,
            # beklenen bir durum; oyle raporlanmali.
            if y.status_code == 401:
                r["mesaj"] = "giris yapilmamis"
                return r
            if y.status_code != 200:
                r["hata"] = f"auth/status HTTP {y.status_code}"
                return r

            d = y.json()
            r["kimlik_dogrulandi"] = bool(d.get("authenticated"))
            r["bagli"] = bool(d.get("connected"))
            r["rakip_oturum"] = bool(d.get("competing"))
            r["mesaj"] = str(d.get("message") or "")

            # --- 3. katman: oturum omru ---
            try:
                t = c.post(f"{taban}/tickle", json={})
                if t.status_code == 200:
                    ms = t.json().get("ssoExpires")
                    if isinstance(ms, (int, float)):
                        r["oturum_bitis_sn"] = int(ms / 1000)
            except httpx.RequestError:
                pass  # tickle teshis icin kritik degil

            # --- 4. katman: hangi hesap ---
            # /portfolio/accounts, diger /portfolio uclarindan ONCE
            # cagrilmali (IBKR sarti). Ayrica canli/kagit KANITI burada.
            try:
                h = c.get(f"{taban}/portfolio/accounts")
                if h.status_code == 200:
                    veri = h.json()
                    if isinstance(veri, list):
                        for hesap in veri:
                            kimlik = str(hesap.get("accountId") or hesap.get("id") or "")
                            r["hesaplar"].append({
                                "kimlik": kimlik,
                                "kagit_mi": _kagit_mi(hesap),
                                "para_birimi": hesap.get("currency"),
                                "tip": hesap.get("type"),
                                "islem_erisimi": hesap.get("brokerageAccess"),
                            })
            except httpx.RequestError:
                pass

    except Exception as e:  # beklenmeyen — yut ama SOYLE
        r["hata"] = f"{type(e).__name__}: {e}"

    return r


def _yaz(r: dict, acik: bool) -> None:
    def im(v: bool | None) -> str:
        return {True: "EVET", False: "HAYIR", None: "?"}[v]

    print()
    print("  IBKR BAGLANTI RAPORU")
    print("  " + "-" * 46)
    print(f"  Taban URL                 {r['taban']}")
    print(f"  Gateway'e ulasiliyor      {im(r['ulasilabilir'])}")
    print(f"  Kimlik dogrulandi         {im(r['kimlik_dogrulandi'])}")
    print(f"  IBKR arkaucuna bagli      {im(r['bagli'])}")

    if r["rakip_oturum"]:
        print("  Rakip oturum              EVET  <-- baska bir yerde giris var")
        print("                                  (telefon / Client Portal / TWS)")
    elif r["rakip_oturum"] is False:
        print("  Rakip oturum              yok")

    if r["oturum_bitis_sn"] is not None:
        dk = r["oturum_bitis_sn"] // 60
        print(f"  Oturum bitisine           ~{dk} dk")

    if r["mesaj"]:
        print(f"  Mesaj                     {r['mesaj']}")
    if r["hata"]:
        print(f"  HATA                      {r['hata']}")

    if r["hesaplar"]:
        print()
        print("  Hesaplar")
        for h in r["hesaplar"]:
            k = h["kimlik"] if acik else _maskele(h["kimlik"])
            tur = {True: "KAGIT", False: "CANLI", None: "belirsiz"}[h["kagit_mi"]]
            pb = h["para_birimi"] or "?"
            print(f"    {k:<12} {tur:<9} {pb}")

    print()
    # Ne yapmali — teshisin ise yaramasi icin bir sonraki adim SOYLENMELI.
    if not r["ulasilabilir"]:
        print("  -> Gateway calismiyor. Baslatmak icin:")
        print("     cd ~/Downloads/clientportal.gw && bin/run.sh root/conf.finagent.yaml")
    elif not r["kimlik_dogrulandi"]:
        taban_kok = r["taban"].replace("/v1/api", "")
        print(f"  -> Tarayicidan giris yap: {taban_kok}")
        print("     Sertifika uyarisi normaldir (kendinden imzali).")
    else:
        print("  -> Brokerage oturumu ACIK. /iserver uclari kullanilabilir.")
        print("     Oturumun dusmemesi icin ~60 sn'de bir /tickle gerekiyor.")
    print()


def main() -> int:
    argv = sys.argv[1:]
    r = topla()

    if "--json" in argv:
        if "--acik" not in argv:
            for h in r["hesaplar"]:
                h["kimlik"] = _maskele(h["kimlik"])
        print(json.dumps(r, ensure_ascii=False))
    else:
        _yaz(r, acik="--acik" in argv)

    if not r["ulasilabilir"]:
        return 2
    if not r["kimlik_dogrulandi"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
