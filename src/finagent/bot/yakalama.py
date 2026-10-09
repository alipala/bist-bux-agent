"""
PORTFOY YAKALAMA — parcali ekran goruntusu -> TEK onay -> TEK kayit.

NEDEN VAR (9 Eki, Ali: "kac kere fix oldu, rock solid olmali")
-----------------------------------------------------------
Ayni kirilma logda BES kez goruldu (21 Agu, 17 Eyl, 2 Eki, 6 Eki, 9 Eki):
iki-uc ekran goruntusuyle gonderilen BUX portfoyunun bir kismi kayittan
"dustu". Her duzeltme bir koseyi kapatti, DORT kusurun etkilesimini degil:

  1. ALBUM IKIYE BOLUNUYORDU. Telegram aciklamayi yalnizca albumun ILK
     fotografina koyar; aciklamali fotograf sohbet modeline, digerleri
     goruntu okuyucuya gidiyordu -> iki ayri onay.
  2. AYNI HESAP ICIN BIRDEN FAZLA ONAY yan yana durabiliyor ve her biri
     bagimsiz uygulaniyordu.
  3. "GORUNTU = TUM PORTFOY" varsayimi: okuyucudan gelen her kayit ekranda
     gorunmeyeni SATILMIS sayiyordu — kanit aranmadan.
  4. Birlestirme penceresi kaydin ACILIS saatine bagliydi; yerinde alan
     tazelemesi pencereyi baslatmiyordu.

TASARIM — IKI KURAL
-------------------
* BIR HESAP, BIR BEKLEYEN YAKALAMA. Ayni sohbette ayni hesaba ait her okuma
  (goruntu ya da sohbet yolu) `PENCERE` icinde ayni bekleyen onaya
  BIRLESIR. Iki onay olusamaz; hangisine basildigi onemsizlesir.
* POZISYON ANCAK KANITLA DUSER. Kanit ekranda yazan TOPLAMdir: okunan
  satirlarin toplami ekran toplamini tutuyorsa (`KAPSAM_ALT..KAPSAM_UST`)
  yakalama TAMDIR ve gorunmeyen pozisyon satilmistir. Tutmuyorsa ya da
  toplam okunamadiysa EKSIKTIR: eski kayittaki gorunmeyen pozisyonlar
  KORUNUR (tasinir). Kaynagin goruntu ya da sohbet olmasi ARTIK BELIRLEYICI
  DEGIL — yalnizca kanit.

Onay mesaji planin AYNISINI gosterir (ne korunacak, ne satilmis sayilacak);
yazim da ayni fonksiyondan gecer. Gosterilen ile yapilan ayrisamaz.
"""
from __future__ import annotations

import re
from datetime import timedelta

KAPSAM_ALT = 0.98
KAPSAM_UST = 1.02
PENCERE = timedelta(minutes=20)

# Aciklamada KAYIT niyeti: "BUX guncel portfoyum. Kaydet" (9 Eki) soru
# degildir; goruntu okuyucuya gitmeli ki albumun diger kareleriyle birlessin.
_KAYIT_KOKLERI = ("kaydet", "kayded", "kayit", "kayıt", "guncel", "güncel",
                  "portfoy", "portföy", "ekle", "guncelle", "güncelle",
                  "pozisyon")
_SORU_EKLERI = frozenset({"mi", "mı", "mu", "mü", "miyim", "mıyım", "muyum",
                          "müyüm", "misin", "mısın", "musun", "müsün", "miyiz",
                          "mıyız", "midir", "mıdır", "mudur", "müdür"})
_SORU_KOKLERI = ("neden", "nasil", "nasıl", "kac", "kaç", "hangi", "niye",
                 "ne")


def kayit_niyeti(aciklama: str | None) -> bool:
    """
    SAF. Aciklama bir KAYIT istegi mi (soru degil)? Soru: '?' ya da soru
    eki ("eklemeli MIYIM") ya da soru kelimesi ile baslayan kelime.
    """
    m = (aciklama or "").lower()
    kelimeler = [k for k in re.split(r"[^\wçğıöşü]+", m) if k]
    if not kelimeler or "?" in m:
        return False
    if any(k in _SORU_EKLERI or k in _SORU_KOKLERI or
           (k.startswith(_SORU_KOKLERI[:-1])) for k in kelimeler):
        return False
    return any(k.startswith(kok) for k in kelimeler for kok in _KAYIT_KOKLERI)


def okunan(p: dict) -> float | None:
    """Okunan satirlarin deger toplami (nakit dahil); hic deger yoksa None."""
    v = [float(r["market_value"]) for r in p.get("pozisyonlar") or []
         if r.get("market_value") is not None]
    return round(sum(v), 2) if v else None


def kapsam(p: dict) -> dict:
    """
    SAF. {okunan, toplam, oran, durum}. durum:
      tam         okunan toplami ekran toplamini tutuyor -> kanit var
      eksik       okunan az -> devami gonderilmeli; dusurme YOK
      fazla       okunan COK (ust uste sayim?) -> guvenilmez; dusurme YOK
      olculemedi  ekran toplami okunamadi -> dusurme YOK
    """
    t = p.get("toplam_deger")
    o = okunan(p)
    if not t or t <= 0 or o is None:
        return {"okunan": o, "toplam": t, "oran": None, "durum": "olculemedi"}
    oran = o / float(t)
    durum = ("tam" if KAPSAM_ALT <= oran <= KAPSAM_UST
             else "eksik" if oran < KAPSAM_ALT else "fazla")
    return {"okunan": o, "toplam": float(t), "oran": round(oran, 4), "durum": durum}


def tam_mi(p: dict) -> bool:
    return kapsam(p)["durum"] == "tam"


def birlestir(hedef: dict, yeni: dict) -> int:
    """
    Yeni okumayi bekleyen yakalamaya ekler; KAC YENI satir eklendi.

    AYNI SEMBOL IKI KEZ TOPLANMAZ (kareler cakisir; BUX'ta Palantir..Tesla
    iki karede de gorunur). Cakismada ILK okuma korunur — ama ilk okumada
    adet ya da deger YOKSA (kesik satir) yenisi tamamlar.
    Ekran toplami: ilk okunan korunur; yeni bir toplam %2'den fazla
    farkliysa `celiskiler`e yazilir (kapsam o zaman sorgulanir).
    """
    satirlar = hedef.setdefault("pozisyonlar", [])
    dizin = {str(p.get("symbol") or "").upper(): p for p in satirlar}
    eklenen = 0
    for p in yeni.get("pozisyonlar") or []:
        sem = str(p.get("symbol") or "").upper()
        if not sem:
            continue
        eski = dizin.get(sem)
        if eski is None:
            satirlar.append(p)
            dizin[sem] = p
            eklenen += 1
            continue
        for alan in ("quantity", "market_value", "avg_cost", "last_price", "pnl_pct"):
            if eski.get(alan) is None and p.get(alan) is not None:
                eski[alan] = p[alan]
    t_eski, t_yeni = hedef.get("toplam_deger"), yeni.get("toplam_deger")
    if not t_eski and t_yeni:
        hedef["toplam_deger"] = t_yeni
    elif t_eski and t_yeni and abs(float(t_yeni) - float(t_eski)) > 0.02 * float(t_eski):
        hedef.setdefault("celiskiler", []).append(
            f"ekran toplamlari farkli: {t_eski} / {t_yeni}")
    # META ALANLAR: sohbet yolunun kaydinda `guven`/`ekran_tipi` YOK; goruntu
    # onun ustune birlesince onay metni `guven` ararken COKTU (9 Eki senaryo).
    for alan in ("hesap", "para_birimi", "guven", "ekran_tipi"):
        if not hedef.get(alan) and yeni.get(alan):
            hedef[alan] = yeni[alan]
    for alan in ("eslesen", "yeni_kayit", "cozulemeyen"):
        for x in yeni.get(alan) or []:
            if x not in hedef.setdefault(alan, []):
                hedef[alan].append(x)
    hedef["_gorsel"] = int(hedef.get("_gorsel") or 1) + int(yeni.get("_gorsel") or 1)
    kaynaklar = set(hedef.get("_kaynaklar") or [_kaynak(hedef)])
    kaynaklar.add(_kaynak(yeni))
    hedef["_kaynaklar"] = sorted(kaynaklar)
    hedef["okunan_toplam"] = okunan(hedef)
    return eklenen


def _kaynak(p: dict) -> str:
    return "sohbet" if str(p.get("kaynak") or "").startswith("sohbet") else "ekran"


def plan(p: dict, mevcut: dict[str, float | None]) -> dict:
    """
    SAF. Bu yakalama onaylanirsa ne olur? `mevcut`: hesabin SON kaydi,
    sembol -> adet. Doner {kapsam, tam, dusen, tasinan}:
      tam  -> eski kayitta olup burada OLMAYAN (adet>0) pozisyonlar SATILMIS
              sayilir (`dusen`).
      degil-> ayni pozisyonlar KORUNUR (`tasinan`); hicbir sey dusmez.
    """
    k = kapsam(p)
    gelen = {str(r.get("symbol") or "").upper() for r in p.get("pozisyonlar") or []}
    gorunmeyen = sorted(s for s, adet in mevcut.items()
                        if s.upper() not in gelen and (adet is None or adet > 0))
    tam = k["durum"] == "tam"
    return {"kapsam": k, "tam": tam,
            "dusen": gorunmeyen if tam else [],
            "tasinan": [] if tam else gorunmeyen}
