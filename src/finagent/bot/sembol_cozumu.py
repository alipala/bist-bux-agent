"""
EKRANDAN GELEN POZISYON SATIRINI KAYITLI KAGIDA BAGLAR — sembol, ad, nakit.

NEDEN VAR (olculdu 2026-10-02/05)
---------------------------------
BUX ekraninda ticker YAZMIYOR, yalnizca ad var. Iki yol da kirikti:

1. EKRAN OKUYUCU (`vision`): istem "ticker yoksa sembolu null birak,
   cozumlemeyi programa birak" diyordu, ama `_normalise` sembolsuz satiri
   ATIYORDU ve adi sembole ceviren bir program adimi HIC YOKTU. Her BUX
   goruntusu "Pozisyon cikaramadim" ile bitiyordu — okuma dogruydu,
   eslestirme eksikti.
2. SOHBET (`pozisyon_kaydet`): model sembolu UYDURDU (`TESLA`, `PALANTIR`,
   `SERVICENOW`, `4GLD`, nakit icin `NAKIT`). `pozisyon_enstrumani` birebir
   sembolle aradigi icin bes YENI kayit acildi; kagitlarin fiyat gecmisi,
   stop seviyesi ve tahmin gecmisi eski kayitlarda kaldi.

KURAL: HEDEF, FIYAT SERISI OLAN BIR KAYITTIR
--------------------------------------------
Hicbir basamak fiyat serisi olmayan bir enstrumana BAGLAMAZ. Serisiz bir
hedef secmek 2 Ekim hatasinin ta kendisi.

UC BASAMAK, HER BIRINDE TEK ADAY SART
-------------------------------------
  1. bu hesapta bu kisinin DAHA ONCE tuttugu kagitlar (en guclu kanit)
  2. hesabin kendi katalogu (BUX -> BUX)
  3. diger hisse borsalari (kripto ve makro HARIC)
Bir basamakta birden fazla aday cikarsa SECIM YAPILMAZ ve satir
"cozulemedi" doner — yanlis kagida baglamak, baglamamaktan kotudur.

AD KARSILASTIRMASI IKINCI BIR KURAL YAZMAZ: `research.identity`'nin
`ad_okumalari` / `_fon_anahtari` / `fon_mu`su kullanilir (hafiza:
ayni-kural-iki-kopya). Sirketlerde ilk belirtec esit VE belirtecler
altkume; fonlarda katalogda ESITLIK (pay sinifi), kendi gecmisinde altkume.

SEMBOL VERILDIYSE DE AD DENETLENIR (AVTX dersi): verilen sembol kayitliysa
ama kayitli adi ekrandaki adla celisiyorsa sembol KABUL EDILMEZ.
"""
from __future__ import annotations

import re

from ..research.identity import _fon_anahtari, ad_okumalari, fon_mu
from ..storage.db import HESAP_VENUE, KRIPTO_VENUE, POZISYONSUZ_VENUE

# Ekranin ve modelin nakde verdigi adlar. Deger `CASH` + asset_type
# 'cash' olur — ekran okuyucunun zaten yazdigi bicim (tek bicim).
NAKIT_SEMBOLLERI = frozenset({"CASH", "NAKIT", "NAKİT"})
_NAKIT_AD = re.compile(r"^(nakit|cash|bakiye|kullanilabilirnakit|availablecash)"
                       r"(eur|usd|try|usdt)?$")


def _ad_norm(ad) -> str:
    return "".join(ch for ch in str(ad or "").casefold() if ch.isalnum())


def nakit_mi(sembol: str | None, ad: str | None) -> bool:
    """Satir NAKIT mi? Sembol nakit adiysa, ya da SEMBOL YOKKEN ad nakitse.

    Sembol varken ada bakilmaz: 'Cash Converters' gibi bir sirket adi
    nakde cevrilmesin.
    """
    s = (sembol or "").strip().upper()
    if s in NAKIT_SEMBOLLERI or s.startswith("CASH."):
        return True
    return not s and bool(_NAKIT_AD.match(_ad_norm(ad)))


def _taban(sembol: str | None) -> str:
    """'4GLD.DE' -> '4GLD'. Borsa eki atilir."""
    return (sembol or "").strip().upper().split(".")[0]


def ad_uyumlu(ekran_ad: str | None, kayit_ad: str | None, *,
              siki_fon: bool) -> bool:
    """
    Iki ad AYNI kagidi mi anlatiyor? Belirsizse False.

    Sirket: herhangi iki okumada ILK belirtec esit ve biri digerinin
    altkumesi ('Tesla' ~ 'Tesla, Inc.', 'Palantir' ~ 'Palantir
    Technologies Inc.'). Fon: `_fon_anahtari`; `siki_fon` ise ESITLIK
    (katalogda pay sinifi karismasin), degilse altkume (kendi gecmisi).
    """
    if not ekran_ad or not kayit_ad:
        return False
    if fon_mu(ekran_ad) or fon_mu(kayit_ad):
        a, b = _fon_anahtari(ekran_ad), _fon_anahtari(kayit_ad)
        if not a or not b:
            return False
        if siki_fon:
            return a == b
        # GEVSEK ALTKUME EN AZ IKI BELIRTECLE: tek basina 'Gold' her altin
        # urunune altkume olur (olculdu: 'Gold' -> Xetra-Gold'a baglandi).
        # 'Xetra Gold ETC' -> {XETRA, GOLD} gecer.
        return a == b or (min(len(a), len(b)) >= 2 and (a <= b or b <= a))
    for x in ad_okumalari(ekran_ad):
        for y in ad_okumalari(kayit_ad):
            if x and y and x[0] == y[0] and (set(x) <= set(y) or set(y) <= set(x)):
                return True
    return False


_SERI_VAR = "EXISTS (SELECT 1 FROM prices pr WHERE pr.instrument_id = i.id)"


def _aday_basamaklari(db, hesap: str, sahip: str) -> list[tuple[str, list]]:
    """Uc basamak: (etiket, [satir]). Hepsi FIYAT SERISI olan kayitlar."""
    venue = HESAP_VENUE.get(hesap, hesap.upper())
    kripto = venue in KRIPTO_VENUE
    disla = tuple(sorted(POZISYONSUZ_VENUE))
    yer = ",".join("?" * len(disla))
    sinif = ("i.venue IN ({})".format(",".join("?" * len(KRIPTO_VENUE)))
             if kripto else
             "i.venue NOT IN ({})".format(",".join("?" * len(KRIPTO_VENUE))))
    sinif_arg = tuple(sorted(KRIPTO_VENUE))
    nakit_degil = ("COALESCE(i.asset_type,'') <> 'cash' AND i.symbol <> 'CASH' "
                   "AND i.symbol NOT LIKE 'CASH.%'")
    gecmis = db.query(
        f"""SELECT DISTINCT i.id, i.symbol, i.name, i.venue
            FROM positions p JOIN instruments i ON i.id = p.instrument_id
            WHERE p.account = ? AND p.sahip = ? AND {nakit_degil}
              AND {sinif} AND {_SERI_VAR}""",
        (hesap, sahip, *sinif_arg))
    kendi = db.query(
        f"""SELECT i.id, i.symbol, i.name, i.venue FROM instruments i
            WHERE i.venue = ? AND {nakit_degil} AND {_SERI_VAR}""", (venue,))
    diger = db.query(
        f"""SELECT i.id, i.symbol, i.name, i.venue FROM instruments i
            WHERE i.venue <> ? AND i.venue NOT IN ({yer}) AND {sinif}
              AND {nakit_degil} AND {_SERI_VAR}""",
        (venue, *disla, *sinif_arg))
    return [("onceki pozisyon", [dict(r) for r in gecmis]),
            (f"{venue} katalogu", [dict(r) for r in kendi]),
            ("diger borsa", [dict(r) for r in diger])]


def _tekil(adaylar: list[dict]) -> list[dict]:
    """Ayni SEMBOLE dusen adaylar tek sayilir (iki venue, ayni kod)."""
    gor, out = set(), []
    for a in adaylar:
        if a["symbol"] not in gor:
            gor.add(a["symbol"])
            out.append(a)
    return out


def cozumle(db, hesap: str, sahip: str, satirlar: list[dict]) -> dict:
    """
    Satirlarin sembollerini kayitli kagitlara baglar. Satirlar DEGISTIRILMEZ;
    kopyalari doner.

    Doner: {"satirlar": [cozulen satirlar], "eslesen": [(eski, yeni, kaynak)],
            "yeni": [sembol], "cozulemeyen": [{"ad", "sembol", "deger",
            "sebep"}]}
      eslesen   — sembol degisti (ya da sembolsuz satira sembol bulundu)
      yeni      — verilen sembol hicbir FIYATLI kayda baglanamadi; yeni
                  kayit acilacak. Cagiran bunu KULLANICIYA SOYLER.
      cozulemeyen — sembol yok ve ad tek bir kayda baglanamadi; YAZILMAZ.
    """
    basamaklar = _aday_basamaklari(db, hesap, sahip)
    kripto = HESAP_VENUE.get(hesap, "") in KRIPTO_VENUE
    out = {"satirlar": [], "eslesen": [], "yeni": [], "cozulemeyen": []}

    for r0 in satirlar:
        r = dict(r0)
        sem = (r.get("symbol") or "").strip().upper()
        ad = r.get("name")

        if not kripto and nakit_mi(sem, ad):
            r.update({"symbol": "CASH", "name": "Nakit", "quantity": None,
                      "avg_cost": None, "last_price": None,
                      "asset_type": "cash"})
            if sem and sem != "CASH":
                out["eslesen"].append((sem, "CASH", "nakit"))
            out["satirlar"].append(r)
            continue

        # 1) VERILEN SEMBOL kayitli ve fiyatliysa — ad CELISMIYORSA kabul.
        celiski = None
        if sem:
            birebir = [a for _, liste in basamaklar for a in liste
                       if a["symbol"].upper() == sem]
            if birebir:
                uyan = [a for a in birebir
                        if not ad or not a.get("name")
                        or ad_uyumlu(ad, a["name"], siki_fon=False)]
                if uyan:
                    r["symbol"] = sem
                    out["satirlar"].append(r)
                    continue
                celiski = (f"'{sem}' kayitta '{birebir[0].get('name')}'; "
                           f"ekrandaki ad '{ad}' — sembol kabul edilmedi")

        # 2) BASAMAKLAR: ad ya da (yalniz kendi gecmisinde) borsa eki.
        bulunan = None
        for i, (etiket, liste) in enumerate(basamaklar):
            ilk = i == 0
            # BORSA EKI KURALI YALNIZCA FARKLI YAZIMDA: '4GLD' ~ '4GLD.DE'.
            # Birebir ayni sembolu burada yeniden secmek, (1)'de AD
            # CELISKISI yuzunden reddedilen kaydi arka kapidan geri
            # alirdi (olculdu: 'AVTX' + 'Avalo Therapeutics' AVTX'e
            # baglandi).
            aday = _tekil([
                a for a in liste
                if (ad and ad_uyumlu(ad, a.get("name"), siki_fon=not ilk))
                or (ilk and sem and _taban(sem)
                    and a["symbol"].upper() != sem
                    and _taban(sem) == _taban(a["symbol"]))])
            if len(aday) == 1:
                bulunan = (aday[0]["symbol"], etiket)
                break
            if len(aday) > 1:
                bulunan = ("", f"{etiket}: birden fazla aday ("
                           + ", ".join(a["symbol"] for a in aday[:4]) + ")")
                break
        if bulunan and bulunan[0]:
            yeni_sem, etiket = bulunan
            if yeni_sem != sem:
                out["eslesen"].append((sem or (ad or "?"), yeni_sem, etiket))
            r["symbol"] = yeni_sem
            out["satirlar"].append(r)
            continue

        sebep = celiski or (bulunan[1] if bulunan else
                            "adi kayitli hicbir fiyatli kagitla eslesmedi")
        if sem and not celiski:
            # Verilen sembol hicbir yere baglanmadi: YENI kayit. Yazilir
            # ama cagiran bunu kullaniciya SOYLER (onaydan once).
            r["symbol"] = sem
            out["satirlar"].append(r)
            out["yeni"].append(sem)
            continue
        out["cozulemeyen"].append({"ad": ad, "sembol": sem or None,
                                   "deger": r.get("market_value"),
                                   "sebep": sebep})
    return out
