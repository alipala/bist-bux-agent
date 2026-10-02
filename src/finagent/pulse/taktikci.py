"""
GUN ICI TAKTIKCI (B6) — TEK LLM cagrisi.

NE YAPAR
--------
Deterministik tarayici (`gunici_tarayici`) esigi gecen adaylari bulur.
Taktikci YALNIZCA o adaylar icin, kullanicinin okuyacagi "al / koru /
sat / bekle" cumlesini kurar.

NEDEN PANEL DEGIL
-----------------
Panel dort ajan + hakem; sabah kosusunda dakikalar suruyor ve gunde bir
kez calisiyor. Gun ici katman piyasa saatlerinde ~16 kez kosuyor. Ayni
mimari burada hem maliyeti 16'ya katlar hem de her kosuda duvar saati
riskini tekrarlardi. Burada yorumlanacak sey de dar: "olculen su
seviyeler, su hareket oldu — ne yapmali". Tek cagri yeter.

DORT KAPI, LLM'DEN ONCE
-----------------------
Model, asagidakilerin DORDU birden acik degilse HIC CAGRILMAZ. Bu
sadece maliyet degil: cagrilmayan model uydurma taktik da uretemez.
  1. Aday var mi          (yoksa: sessizlik gecerli cikti)
  2. Gunluk tavan doldu mu (doluysa: bugun yeterince konustuk)
  3. Bu sembole bugun taktik verildi mi (verildiyse: `DO NOTHING`
     defterde zaten engelliyor, mesaji da engelle)
  4. Duvar saati kaldi mi  (kalmadiysa: yarim is yapma)

DUVAR SAATI ZORUNLU — VARSAYILANI YOK
-------------------------------------
`sure_siniri_sn` KEYWORD ve ZORUNLU. Panelin canli arizasi tam olarak
buydu: sure siniri "vardi" ama yalnizca sahipler ARASINDA bakiliyordu,
tek bir cagri sonsuza kadar surebiliyordu. Varsayilan bir deger
koymak, cagiran tarafin dusunmemesine izin vermek demektir.

FREN — KARNE KOTUYSE TAVAN DUSER
--------------------------------
Taktik katmaninin kendi karnesi var (`ajan='taktik'`, yalnizca
`alim`/`satis`). Olcum yoksa bu BEYAN EDILIR ("OLCULMEMIS"); olcum
varsa ve isabet esigin altindaysa gunluk tavan otomatik 1'e iner.

Fren neden gerekli: gunluk al-satta islem basina binde 1 komisyonla
aylik maliyet ~%4,2. %50 isabet aylik -%4,2, %55 isabet +%5,6. Sistem
tutturamiyorsa DAHA AZ konusmali — ve buna karar veren sey benim
kanaatim degil, defterdeki sayi olmali.
"""
from __future__ import annotations

from ..llm import sdk_ortami

import json
import logging

log = logging.getLogger(__name__)

# Gunluk tavan — sahip basina, `alim`+`satis` toplami.
#
# 3 secildi cunku kullanicinin talebi "gunde 2-3 taktik"ti ve ustune
# cikmak, mesajlari okunmaz yapmanin en hizli yolu. `koruma` ve `bekle`
# tavana GIRMEZ: ilki zaten var olan bir pozisyonun savunmasi (yeni
# risk almiyor), ikincisi "bir sey yapma" diyor.
TAVAN = 3

# Karne kotuyken inilen tavan.
FREN_TAVANI = 1

# Frenin devreye girmesi icin gereken en az OLCUM.
#
# 20, `Defter.yeterli_mi` esigiyle AYNI ve ayni gerekce: altinda Wilson
# araligi o kadar genis ki "isabet %45" ile "isabet %65" ayirt
# edilemiyor. Az veriyle fren cekmek, gurultuye tepki vermektir.
FREN_ASGARI_OLCUM = 20

# Bu isabetin ALTINDA (ve dahil) fren cekilir. %50: yazi-tura.
FREN_ISABET_ESIGI = 50.0

# Tavana sayilan turler.
TAVANA_SAYILAN = ("alim", "satis")

# Deftere yazilirken kullanilan ajan adi. Karne ve fren bu ada bakiyor.
AJAN = "taktik"


def taktik_karnesi(db, sahip: str) -> dict:
    """
    Taktik katmaninin UFUK karnesi — TEK TANIM. Fren (`Taktikci.tavan`) ve
    `taktik_sicili` araci ayni sayiyi buradan okur.

    OLCULEN KUSUR (2026-10-02): arac `Defter.karne(sahip)` cagiriyordu —
    varsayilan ajan HAKEM. "Taktiklerim tutuyor mu" sorusuna ali icin
    %60,5 (hakemin 238 cagrisi) gidiyordu; taktigin kendi karnesi %23,3
    (10/43), freni de o sayi cekiyor. Iki yer ayni karneyi kendi
    cagrisiyla kurdugu icin biri sessizce ayristi.
    """
    from .journal import Defter
    return Defter(db).karne(sahip, ajan=AJAN, taktik_turleri=TAVANA_SAYILAN)

# Gun ici taktigin en uzun ufku. Uzunu mesru bir gorustur ama GUN ICI
# taktik degildir; 250 gunluk ufuklu bir "al" cagrisi bu katmanin
# karnesini olcemez hale getirir. Sozlesme prompta da yaziliyor.
AZAMI_UFUK_GUN = 20

# Cagriya birakilan en az sure. Altinda model cagrilmaz: yarim kalan
# bir cagri hem para harcar hem taktik uretmez.
ASGARI_SURE_SN = 20


_SABLON = """Sen GUN ICI TAKTIKCISISIN. Piyasa ACIKKEN, olculen bir
hareketi kullanicinin uygulayabilecegi tek bir cumleye cevirirsin.

## SOZLESME

1. YALNIZCA JSON dondur. Once/sonra aciklama yazma.
2. Bicim: {"taktikler": [ ... ]}. Taktik yoksa {"taktikler": []}.
3. Her taktik SU alanlari tasir:
   - "sembol"      : YALNIZCA sana verilen adaylardan biri
   - "tur"         : "alim" | "koruma" | "satis" | "bekle"
   - "giris"       : sayi — OLCULEN SEVIYELER'den BIRI ("bekle"de yok)
   - "stop"        : sayi — OLCULEN SEVIYELER'den BIRI ("bekle"de yok)
   - "yon"         : "yukari" | "asagi" | "notr"
   - "guven"       : 0.0-1.0
   - "ufuk_gun"    : 1-%(azami_ufuk)d tam sayi
   - "gerekce"     : bir cumle, sade Turkce
   - "tez"         : bu taktik NEYE dayaniyor
   - "gecersizlesme_kosulu" : asagidaki GRAMER

## SEVIYELERI SEN HESAPLAMAZSIN, SECERSIN

`giris` ve `stop`, sana verilen OLCULEN SEVIYELER icindeki bir sayiya
esit olmali. Kendi hesabini yapma, yuvarlama, ortalama alma. Uydurulan
seviye REDDEDILIR ve taktik dusurulur.

## POZISYON BUYUKLUGU, LOT, KALDIRAC YAZMA

Kac adet alinacagi burada HESAPLANMAZ. Sistem `giris` ile `stop`
arasindaki mesafeden risk yuzdesini kendi uretir.

## ELDE OLMAYANI SATAMAZSIN

Adayin `pozisyonda` alani false ise "satis" ya da "koruma" YAZMA —
o kagit kullanicida yok. Bu durumda yalnizca "alim" veya "bekle".

## GECERSIZLESME GRAMERI

%(gramer)s

## "BEKLE" GECERLI BIR TAKTIKTIR

Hareket olculdu ama uygulanabilir bir sey yoksa "bekle" yaz. Zorlama.
Bos liste dondurmek de mesrudur.

## EN COK %(kalan)d TAKTIK

`alim`+`satis` toplami en cok %(kalan)d olmali. `koruma` ve `bekle`
bu sayiya girmez.
"""


class Taktikci:
    """Gun ici taktik uretici — tek cagri, zorunlu duvar saati."""

    def __init__(self, settings, db, *, sure_siniri_sn: float):
        if not sure_siniri_sn or float(sure_siniri_sn) <= 0:
            # PANEL DERSI: sure siniri VARSAYILANI OLMAYAN bir sozlesme.
            raise ValueError(
                "Taktikci: `sure_siniri_sn` zorunlu ve pozitif olmali — "
                "duvar saatsiz LLM cagrisi gun ici kosuyu askida birakir")
        self.s = settings
        self.db = db
        self.sure_siniri_sn = float(sure_siniri_sn)
        self.model = settings.get("analysis.llm.tactical_model", "claude-fable-5")
        self._seviyeler: dict = {}

    # ------------------------------------------------------------------
    def karne(self, sahip: str) -> dict:
        """Taktik katmaninin KENDI karnesi — hakeminki degil."""
        return taktik_karnesi(self.db, sahip)

    def tavan(self, sahip: str) -> dict:
        """
        Bugunun tavani ve GEREKCESI.

        Doner: {"tavan", "fren", "olculmemis", "karne", "gerekce"}

        `olculmemis` alani mesaja tasinir: kullanicinin okudugu her
        taktik, arkasindaki isabet olcusunun VAR olup olmadigini
        soylemeli. "Henuz olculmedi" demek, olculmus gibi davranmaktan
        durusttur.
        """
        k = self.karne(sahip)
        olcum = int(k.get("olcum") or 0)
        if olcum < FREN_ASGARI_OLCUM:
            return {"tavan": TAVAN, "fren": False, "olculmemis": True,
                    "karne": k,
                    "gerekce": (f"taktik karnesi henuz yeterli degil "
                                f"({olcum}/{FREN_ASGARI_OLCUM} olcum)")}
        isabet = float(k.get("isabet_%") or 0)
        if isabet <= FREN_ISABET_ESIGI:
            return {"tavan": FREN_TAVANI, "fren": True, "olculmemis": False,
                    "karne": k,
                    "gerekce": (f"FREN: {olcum} olcumde isabet %{isabet} "
                                f"(esik %{FREN_ISABET_ESIGI}) — gunluk "
                                f"tavan {FREN_TAVANI}")}
        return {"tavan": TAVAN, "fren": False, "olculmemis": False, "karne": k,
                "gerekce": f"{olcum} olcumde isabet %{isabet}"}

    def _bugun_verilen(self, sahip: str) -> int:
        """Bugun bu sahibe verilmis `alim`+`satis` taktigi sayisi."""
        from .journal import _bugun
        return self.db.query(
            f"""SELECT COUNT(*) n FROM predictions
                WHERE olusma_ts = ? AND ajan = ? AND sahip = ?
                  AND taktik_tur IN ({','.join('?' * len(TAVANA_SAYILAN))})""",
            [_bugun(), AJAN, sahip, *TAVANA_SAYILAN])[0]["n"] or 0

    def _bugun_semboller(self, sahip: str) -> set:
        """
        Bugun bu sahibe HERHANGI bir taktik verilmis semboller.

        `Defter.kaydet` zaten `DO NOTHING` ile ikinci kaydi yutuyor —
        ama yutulan sey yalnizca SATIR. Mesaj yine giderdi ve kullanici
        ayni kagit icin gun boyunca ayni taktigi tekrar tekrar okurdu.
        Kapi burada, TESLIMATTAN once.
        """
        from .journal import _bugun
        return {r["symbol"].upper() for r in self.db.query(
            """SELECT i.symbol FROM predictions p
               JOIN instruments i ON i.id = p.instrument_id
               WHERE p.olusma_ts = ? AND p.ajan = ? AND p.sahip = ?
                 AND p.taktik_tur IS NOT NULL""",
            (_bugun(), AJAN, sahip))}

    # ------------------------------------------------------------------
    def hazirla(self, sahip: str, adaylar: list[dict]) -> dict:
        """
        LLM cagrilmali mi? Doner: {"cagir": bool, "sebep", "kalan", ...}

        AYRI METOT, cunku "model neden cagrilmadi" sorusunun cevabi
        test edilebilir olmali. Cagri icine gomulseydi yalnizca canli
        kosuda gorunurdu.
        """
        t = self.tavan(sahip)
        rapor = {"tavan": t["tavan"], "fren": t["fren"],
                 "olculmemis": t["olculmemis"], "tavan_gerekcesi": t["gerekce"],
                 "karne": t["karne"], "aday": len(adaylar)}

        # KILITLI ADAYLAR: taktik degil GOZLEM. Tavana girmez, modele
        # gitmez — kilitli kagitta uygulanabilir taktik YOKTUR.
        kilitli = [a for a in adaylar if a.get("kilitli")]
        acik = [a for a in adaylar if not a.get("kilitli")]
        rapor["kilitli"] = [a["sembol"] for a in kilitli]

        verilen = self._bugun_verilen(sahip)
        kalan = max(0, t["tavan"] - verilen)
        rapor["bugun_verilen"] = verilen
        rapor["kalan"] = kalan

        zaten = self._bugun_semboller(sahip)
        yeni = [a for a in acik if a["sembol"].upper() not in zaten]
        rapor["bugun_taktikli"] = sorted(zaten & {a["sembol"].upper()
                                                  for a in acik})
        rapor["gonderilecek"] = [a["sembol"] for a in yeni]

        if not yeni:
            rapor.update(cagir=False, sebep=(
                "aday yok" if not adaylar else
                "adaylarin hepsi kilitli" if not acik else
                "adaylarin hepsine bugun taktik verildi"))
        elif kalan <= 0:
            rapor.update(cagir=False, sebep=(
                f"gunluk tavan doldu ({verilen}/{t['tavan']})"))
        else:
            rapor.update(cagir=True, sebep=None)
        rapor["yeni_adaylar"] = yeni
        return rapor

    # ------------------------------------------------------------------
    async def uret(self, sahip: str, adaylar: list[dict], kalan: int
                   ) -> tuple[list[dict], dict]:
        """
        Tek LLM cagrisi + dogrulama. Doner: (taktikler, rapor).

        HICBIR ARIZA KOSUYU DUSURMEZ: sure asimi, JSON bozuklugu ve
        cagri hatasi ayri ayri SAYILIR ve bos liste doner. Gun ici
        kosunun koruma/tez katmani bundan bagimsiz calismaya devam eder.
        """
        import anyio

        rapor = {"cagrildi": True, "sure_siniri_sn": self.sure_siniri_sn,
                 "reddedilen": [], "gecerli": 0, "sure_asimi": False,
                 "hata": None, "ham_uzunluk": 0}
        if self.sure_siniri_sn < ASGARI_SURE_SN:
            rapor.update(cagrildi=False,
                         hata=(f"kalan sure {self.sure_siniri_sn:.0f}sn < "
                               f"{ASGARI_SURE_SN}sn — cagri YAPILMADI"))
            return [], rapor

        from .seviye import dosya as seviye_dosyasi
        try:
            self._seviyeler = seviye_dosyasi(
                self.db, sorted({a["sembol"].upper() for a in adaylar}))
        except Exception as e:                        # noqa: BLE001
            log.warning("[taktikci] seviye dosyasi derlenemedi: %s", e)
            self._seviyeler = {}
        if not self._seviyeler:
            # SEVIYESIZ CAGRI YAPILMAZ: dogrulama her taktigi reddederdi,
            # yani cagri kesin bos donerdi. Parayi harcamanin anlami yok.
            rapor.update(cagrildi=False,
                         hata="olculen seviye YOK — cagri yapilmadi")
            return [], rapor

        metin = ""
        with anyio.move_on_after(self.sure_siniri_sn) as kapsam:
            try:
                metin = await self._cagir(adaylar, kalan)
            except Exception as e:                    # noqa: BLE001
                log.exception("[taktikci] cagri patladi")
                rapor["hata"] = f"{type(e).__name__}: {e}"[:200]
        if kapsam.cancelled_caught:
            rapor["sure_asimi"] = True
            log.warning("[taktikci/%s] duvar saati doldu (%.0fsn) — taktik "
                        "URETILMEDI", sahip, self.sure_siniri_sn)
            return [], rapor
        if rapor["hata"]:
            return [], rapor

        rapor["ham_uzunluk"] = len(metin)
        return self._suz(metin, adaylar, kalan, rapor), rapor

    async def _cagir(self, adaylar: list[dict], kalan: int) -> str:
        from claude_agent_sdk import ClaudeAgentOptions, query

        from .tez import gramer_metni

        # Modele giden aday kaydi: karar icin gereken alanlar. Ic
        # alanlar (instrument_id) gonderilmiyor.
        gorunur = [{k: a[k] for k in
                    ("sembol", "ad", "venue", "para_birimi", "simdiki_fiyat",
                     "onceki_kapanis", "gun_ici_hareket_%", "sigma",
                     "gunluk_oynaklik_%", "bar_ts", "pozisyonda") if k in a}
                   for a in adaylar]
        istem = (
            "### GUN ICI ADAYLAR (esigi gecenler)\n```json\n"
            f"{json.dumps(gorunur, ensure_ascii=False, indent=1)}\n```\n\n"
            "### OLCULEN SEVIYELER — `giris`/`stop` BUNLARDAN SECILIR\n"
            "```json\n"
            f"{json.dumps(self._seviyeler, ensure_ascii=False, indent=1)}\n```")
        opts = ClaudeAgentOptions(
            **sdk_ortami(),
            system_prompt=_SABLON % {"gramer": gramer_metni(),
                                     "kalan": kalan,
                                     "azami_ufuk": AZAMI_UFUK_GUN},
            model=self.model, allowed_tools=[], max_turns=1,
            max_buffer_size=4 * 1024 * 1024)
        parcalar = []
        async for m in query(prompt=istem, options=opts):
            ic = getattr(m, "content", None)
            if not ic or isinstance(ic, str):
                continue
            for b in ic:
                if getattr(b, "text", None):
                    parcalar.append(b.text)
        return "\n".join(parcalar).strip()

    # ------------------------------------------------------------------
    def _suz(self, metin: str, adaylar: list[dict], kalan: int,
             rapor: dict) -> list[dict]:
        """
        Modelin ciktisini DOGRULAR. Reddedilen her taktik SEBEBIYLE sayilir.

        Sessiz duzeltme YOK: bir taktik ya gecer ya duser, "yaklastir"
        diye bir yol yok. Tek istisna `oturt` — o da degeri degil,
        yuvarlanmis halini OLCULENE geri cevirir.
        """
        from .agents import _json_cek
        from .seviye import dogrula, oturt

        veri = _json_cek(metin, anahtar="taktikler") or {}
        ham = veri.get("taktikler")
        if not isinstance(ham, list):
            rapor["reddedilen"].append("cikti 'taktikler' listesi degil")
            return []

        aday_bilgi = {a["sembol"].upper(): a for a in adaylar}
        gorulen: dict[str, dict] = {}
        for t in ham:
            if not isinstance(t, dict):
                rapor["reddedilen"].append("taktik nesne degil")
                continue
            sem = str(t.get("sembol") or "").strip().upper()
            aday = aday_bilgi.get(sem)
            if not aday:
                # ADAY DISI SEMBOL: model konusulmamis bir kagida taktik
                # yazmis. Seviyesi bile gonderilmedigi icin dogrulama
                # zaten reddederdi, ama sebep NET yazilsin.
                rapor["reddedilen"].append(f"{sem or '?'}(aday degil)")
                continue
            tur = str(t.get("tur") or "").strip().lower()
            if tur in ("satis", "koruma") and not aday.get("pozisyonda"):
                # ELDE OLMAYANI SATMA. Prompta yazili ama KOD da
                # uyguluyor: promptun uyulmasi umuttur, kapi degildir.
                rapor["reddedilen"].append(f"{sem}({tur} ama pozisyon yok)")
                continue
            olculen = self._seviyeler.get(sem)
            if not olculen:
                rapor["reddedilen"].append(f"{sem}(olculen seviye yok)")
                continue
            aday_taktik = {**t, "tur": tur}
            ok, sebep = dogrula(aday_taktik, olculen)
            if not ok:
                rapor["reddedilen"].append(f"{sem}({sebep})")
                continue
            if t.get("yon") not in ("yukari", "asagi", "notr"):
                # Deftere yazilamayan gorus, olculemeyen gorustur.
                rapor["reddedilen"].append(f"{sem}(yon gecersiz)")
                continue
            temiz = {**aday_taktik, **oturt(aday_taktik, olculen),
                     "ajan": AJAN, "sembol": sem,
                     "instrument_id": aday["instrument_id"],
                     "para_birimi": olculen.get("para_birimi"),
                     # MODELIN BAKTIGI FIYAT deftere referans olarak
                     # gider. Gun ici bir cagriyi DUNUN kapanisiyla
                     # olcmek, cagrinin gormedigi bir hareketi ona
                     # fatura etmek olurdu (bkz. journal._referans_fiyat).
                     "referans_fiyat": aday.get("simdiki_fiyat"),
                     "ufuk_gun": self._ufuk(t, rapor, sem),
                     "aday": {k: aday.get(k) for k in
                              ("gun_ici_hareket_%", "sigma", "bar_ts",
                               "pozisyonda")}}
            onceki = gorulen.get(sem)
            if onceki:
                # AYNI SEMBOLE IKI TAKTIK bir MODEL TUTARSIZLIGIDIR.
                # Yuksek guvenli tutulur ve SAYILIR — sessizce yutulmaz.
                rapor["reddedilen"].append(f"{sem}(ayni sembole ikinci taktik)")
                if float(onceki.get("guven") or 0) >= float(t.get("guven") or 0):
                    continue
            gorulen[sem] = temiz

        gecerli = list(gorulen.values())
        # TAVAN UYGULAMASI: `bekle`/`koruma` sayilmaz. Kesilenler BEYAN
        # edilir — sessiz kirpma "hepsi bu kadardi" gibi okunur.
        sayilan = [g for g in gecerli if g["tur"] in TAVANA_SAYILAN]
        digeri = [g for g in gecerli if g["tur"] not in TAVANA_SAYILAN]
        sayilan.sort(key=lambda g: -float(g.get("guven") or 0))
        if len(sayilan) > kalan:
            rapor["tavana_takilan"] = [g["sembol"] for g in sayilan[kalan:]]
            log.info("[taktikci] tavan: %d taktikten %d'i kesildi",
                     len(sayilan), len(sayilan) - kalan)
            sayilan = sayilan[:kalan]
        out = sayilan + digeri
        rapor["gecerli"] = len(out)
        return out

    @staticmethod
    def _ufuk(t: dict, rapor: dict, sem: str) -> int:
        """Ufku sozlesme araligina cekar ve KIRPMAYI beyan eder."""
        from .journal import VARSAYILAN_UFUK
        try:
            u = int(t.get("ufuk_gun") or VARSAYILAN_UFUK)
        except (TypeError, ValueError):
            u = VARSAYILAN_UFUK
        kirpik = max(1, min(AZAMI_UFUK_GUN, u))
        if kirpik != u:
            rapor.setdefault("ufuk_kirpilan", []).append(f"{sem}:{u}->{kirpik}")
        return kirpik
