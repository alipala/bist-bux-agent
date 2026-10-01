"""
HABER ZENGINLESTIRME — Jev'e NE sorulur, cevap NASIL okunur.

Soru metinleri ve esikler YALNIZCA burada. Gerekce: bir soru metnindeki
tek kelime, olculmus esigi gecersiz kilar. Metin degisirse olcum
tekrarlanir; iki yerde iki kopya olsaydi biri olculmeden kayardi
(bkz. `ayni kural iki kopya`).

OLCUM (2026-09-30, jev-1.13.0, on-kayitli, gorulmemis orneklem)
--------------------------------------------------------------
OLAY TURU — 100 ticker'li + 13 bagli haber, etiket Jev'den ONCE:
  "onemli sirket olayi" (sirket_olayi VE confidence >= 0.60):
  kesinlik 18/19, geri cagirma 18/18. 6 sinif dogruluk %86; hatalarin
  tamami `yorum_liste` karisikligi (fiyat/profil sayfalari).
  Iki kosu arasi 0/100 karar farki.
SEMBOL BAGLAMA — iki orneklemde 42 bag, 1 yanlis; ikinci orneklemde
  on-kayitli %95 esigi KACIRILDI (13/14). Bu yuzden baglama CANLIDA
  DEGIL; aday ureteci ve soru metni duzeltilip yeniden olculecek.

OLCULMEYENI SOYLEMEK
--------------------
`olay_turu` bir ON ELEME IPUCUDUR, hukum degil. Alanin yoklugu
"siniflandirilmadi" demektir (Jev'e ulasilamadi, anahtar yok, pencere
disi) — "onemsiz" DEGIL. Okuyan taraf bu ayrimi tasimak zorunda.
"""
from __future__ import annotations

import html
import math
import re
from collections import defaultdict

# ----------------------------------------------------------------------
# OLAY TURU — metinler 30 Eyl olcumundeki ile BIREBIR.
# ----------------------------------------------------------------------
OLAY_TURLERI = {
    "sirket_olayi": "Şirketin kendisinde yeni ve somut bir gelişme: finansal sonuç, beklenti, "
                    "birleşme/satın alma, sözleşme/sipariş, ürün/onay/klinik sonuç, yönetim "
                    "değişikliği, şirkete yönelik hukuki/düzenleyici işlem, sermaye işlemi "
                    "(temettü, geri alım, sermaye artırımı, halka arz, bölünme), büyük yatırım/tesis",
    "analist_gorusu": "Analist ya da aracı kurum notu, hedef fiyat, tavsiye değişikliği",
    "fiyat_hareketi": "Hissenin fiyat hareketini anlatır, yeni bir şirket gelişmesi bildirmez",
    "yorum_liste": "'Alınır mı', 'en iyi N hisse', değerleme ya da genel yorum, eğitim yazısı, "
                   "fiyat/profil sayfası",
    "fon_pozisyonu": "Kurumsal yatırımcının ya da içeriden birinin bu şirketin hisselerini alıp "
                     "sattığı bildirim",
    "ilgili_degil": "Haber bu şirket hakkında değil (yalnızca geçerken anılıyor ya da başka bir "
                    "şeyden söz ediyor)",
}
OLAY_SORUSU = "Bu haber `sirket` açısından hangi türde bir haber?"
# confidence bunun altindaysa etiket "belirsiz". 0.60 "onemli olay" icin
# olculdu; diger siniflara da ayni kapi uygulaniyor (daha az etiket,
# daha cok "belirsiz" — ihtiyatli yon).
OLAY_ESIK = 0.60
BELIRSIZ = "belirsiz"

# ----------------------------------------------------------------------
# SEMBOL BAGLAMA — v2 soru metni (grup kurali eklendi, OLCULECEK).
# ----------------------------------------------------------------------
BAG_SORUSU = "Bu haber, listedeki şirketlerden hangisi hakkında bir şey söylüyor?"
BAG_DIKKAT = ("Yalnızca adı benzeyen başka bir varlık (şehir, kamu kurumu, sıradan kelime, "
              "başka bir şirket) o şirket sayılmaz. Aynı gruptaki başka bir şirket de "
              "(holdingin iştiraki, kardeş şirket) o şirket sayılmaz: haber hangi tüzel "
              "kişiden söz ediyorsa yalnızca o.")
BAG_HICBIRI = "hicbiri"
BAG_HICBIRI_ACIKLAMA = "Haber listedeki şirketlerin hiçbiri hakkında değil"
BAG_ESIK = 0.80

OZET_AZAMI = 500


def temiz_ozet(ozet: str | None) -> str:
    """RSS ozetindeki HTML etiketleri ve varliklari ayiklanir (olcumdeki gibi)."""
    s = html.unescape(re.sub(r"<[^>]+>", " ", ozet or ""))
    return re.sub(r"\s+", " ", s).strip()[:OZET_AZAMI]


def haber_state(baslik: str, ozet: str | None) -> dict:
    return {"haber": {"baslik": baslik or "", "ozet": temiz_ozet(ozet)}}


def olay_sorusu(sembol: str, ad: str) -> dict:
    return {"type": "choice",
            "instructions": {"sirket": {"sembol": sembol, "ad": ad}, "soru": OLAY_SORUSU},
            "criteria": OLAY_TURLERI}


def olay_cevabi(cevap: dict) -> tuple[str | None, float | None, dict]:
    """Ham Choice cevabi -> (tur, guven, olasiliklar). Bilinmeyen tur -> None."""
    tur = cevap.get("choice")
    if tur not in OLAY_TURLERI:
        return None, None, {}
    return tur, float(cevap.get("confidence") or 0.0), dict(cevap.get("probabilities") or {})


def olay_etiketi(tur: str | None, guven: float | None) -> str | None:
    """Okuyan tarafin gosterecegi etiket. None = siniflandirilmadi."""
    if tur is None or guven is None:
        return None
    return tur if guven >= OLAY_ESIK else BELIRSIZ


# ----------------------------------------------------------------------
# ADAY URETECI v2 (baglama icin; henuz canlida degil)
# ----------------------------------------------------------------------
# v1 KUSURU (olculdu, 30 Eyl): "SPK'dan Tera islemleri" haberinde dogru
# sirket TRHOL 17 adayin ICINDEYDI ama hepsi 1 puan aldi ve 15'lik ust
# sinirda KESILDI. Iki sebep: (1) "yatirimlar" 13 sirkette, "tera" 4
# sirkette gecerken ikisi ayni agirliktaydi; (2) esit puanlilar keyfi
# kesiliyordu. v2: kelime agirligi = log(N/df) (nadir kelime daha
# ayirt edici), 4 harfli kelimeler TAM eslesmeyle dahil, ve ust sinirda
# ESIT puanlilar birlikte alinir (keyfi kesim yok).
GENEL = set("""holding sanayi ticaret anonim sirketi sirket turizm yatirim ortakligi gayrimenkul
enerji uretim dagitim hizmetleri hizmet elektrik insaat gida tekstil kimya teknoloji teknolojileri
bankasi finansal kiralama faktoring sigorta group global international corporation company
limited holdings technologies systems services industries pharmaceuticals therapeutics
financial energy capital partners resources brands communications entertainment solutions
network networks trust class shares common ordinary sponsored depositary receipt american
products medical health healthcare motors airlines foods bancorp""".split())
_KATLA = str.maketrans("İIıŞşĞğÜüÖöÇçÂâ", "iiissgguuooccaa")
ADAY_UST = 30          # esit puanlilar bu sinirin otesine tasabilir
CHOICE_AZAMI = 250     # API: Choice basina en fazla 255 secenek


def katla(s: str) -> str:
    return (s or "").translate(_KATLA).lower()


class AdayDizini:
    """
    (sembol, venue, ad) listesinden kelime dizini. Bir kosuda bir kez kurulur.
    """

    def __init__(self, enstrumanlar: list[tuple[str, str, str]]):
        self.enstrumanlar = enstrumanlar
        self.kelime: dict[str, set[int]] = defaultdict(set)
        for i, (_, _, ad) in enumerate(enstrumanlar):
            for w in re.findall(r"\w+", katla(ad)):
                if len(w) >= 4 and w not in GENEL and not w.isdigit():
                    self.kelime[w].add(i)
        n = max(1, len(enstrumanlar))
        self.agirlik = {w: math.log(n / len(ix)) for w, ix in self.kelime.items()}
        self.ticker = {s: i for i, (s, _, _) in enumerate(enstrumanlar) if len(s) >= 3}

    def adaylar(self, baslik: str, ozet: str | None) -> list[tuple[str, str, str]]:
        ham = f"{baslik or ''} {temiz_ozet(ozet)}"
        kelimeler = set(re.findall(r"\w+", katla(ham)))
        puan: dict[int, float] = defaultdict(float)
        for w in kelimeler:
            for k, ix in self._eslesen(w):
                for i in ix:
                    puan[i] += self.agirlik[k]
        for s, i in self.ticker.items():
            if re.search(rf"\b{re.escape(s)}\b", ham):
                puan[i] += 10.0
        sirali = sorted(puan.items(), key=lambda kv: (-kv[1], self.enstrumanlar[kv[0]][0]))
        # Ayni sembol iki venue'de olabilir; Choice seceneklerinin anahtari
        # sembol oldugu icin ilki (en yuksek puanli) kalir.
        gorulen: set[str] = set()
        sirali = [kv for kv in sirali if not (self.enstrumanlar[kv[0]][0] in gorulen
                                              or gorulen.add(self.enstrumanlar[kv[0]][0]))]
        if len(sirali) > ADAY_UST:
            sinir = sirali[ADAY_UST - 1][1]
            sirali = [kv for kv in sirali if kv[1] >= sinir]
        return [self.enstrumanlar[i] for i, _ in sirali[:CHOICE_AZAMI]]

    def _eslesen(self, w: str):
        # 4 harfli dizin kelimesi yalnizca TAM eslesir ("kent" != "kentsel");
        # 5+ harfli olan Turkce ek toleransiyla onek olarak eslesir.
        if w in self.kelime and len(w) == 4:
            yield w, self.kelime[w]
        for k in (k for k in self._onekler(w) if len(k) >= 5):
            yield k, self.kelime[k]

    def _onekler(self, w: str):
        for L in range(5, len(w) + 1):
            k = w[:L]
            if k in self.kelime:
                yield k


def bag_sorusu(adaylar: list[tuple[str, str, str]]) -> dict:
    kr = {s: ad for s, _, ad in adaylar}
    kr[BAG_HICBIRI] = BAG_HICBIRI_ACIKLAMA
    return {"type": "choice",
            "instructions": {"soru": BAG_SORUSU, "dikkat": BAG_DIKKAT},
            "criteria": kr}


# OKUYANA GOSTERILEN AD. Kod adlari (`sirket_olayi`) Telegram'da alt cizgi
# bicim isareti sayilip yutuldu ("sirketolayi", 1 Eki) ve kullaniciya giden
# metin Turkce olmali. Kod adi YALNIZCA saklamada ve Jev sorusunda kalir.
OLAY_GORUNEN = {
    "sirket_olayi": "şirket olayı",
    "analist_gorusu": "analist görüşü",
    "fiyat_hareketi": "fiyat hareketi",
    "yorum_liste": "yorum/liste",
    "fon_pozisyonu": "fon pozisyonu",
    "ilgili_degil": "ilgisiz",
    BELIRSIZ: "belirsiz",
}

OLAY_NOTU = (
    "`olay_turu` Jev'in on elemesidir (hukum degil): 'şirket olayı' = "
    "sirketin kendisinde yeni ve somut gelisme; 'analist görüşü', 'fiyat "
    "hareketi', 'yorum/liste', 'fon pozisyonu' (kurumsal/iceriden alim-satim "
    "bildirimi), 'ilgisiz'; 'belirsiz' = guven dusuk. Kullaniciya bu Turkce "
    "adlarla aynen yaz. Alan YOKSA haber SINIFLANDIRILMADI — 'onemsiz' DEME. "
    "`baglayan: jev` = sembol metinden OLASILIKSAL cikarildi (ticker gecmiyordu).")


# ----------------------------------------------------------------------
# OKUMA — okuyan taraflar (arac, haber dosyasi) bu iki fonksiyondan gecer.
# ----------------------------------------------------------------------
def olay_etiketleri(db, news_ids) -> dict[tuple[str, str], str]:
    """{(news_id, sembol): GORUNEN ad}. Siniflandirilmamis cift ANAHTAR OLARAK YOK."""
    ids = list(dict.fromkeys(news_ids))
    out: dict[tuple[str, str], str] = {}
    for bas in range(0, len(ids), 500):
        parca = ids[bas:bas + 500]
        for r in db.query(
                f"""SELECT news_id, sembol, tur, guven FROM haber_olay
                    WHERE news_id IN ({','.join('?' * len(parca))})""", tuple(parca)):
            e = olay_etiketi(r["tur"], r["guven"])
            if e:
                out[(r["news_id"], r["sembol"])] = OLAY_GORUNEN[e]
    return out


def bag_cevabi(cevap: dict) -> tuple[str | None, float]:
    """(sembol ya da None, guven). Esik alti ya da `hicbiri` -> None."""
    secim, guven = cevap.get("choice"), float(cevap.get("confidence") or 0.0)
    if not secim or secim == BAG_HICBIRI or guven < BAG_ESIK:
        return None, guven
    return secim, guven


def jev_ozeti(db) -> dict:
    """
    /durum icin: son `haberjev` kosusu + son 24 saatte yazilanlar.

    NEDEN AYRI SATIR (1 Eki): /durum yalnizca SON BES toplama isini
    listeliyor; haberjev 17:42'de kostu ve sonraki isler onu listeden
    itti — Ali "Jev calisti mi" sorusunun cevabini goremedi.
    """
    son = db.query("""SELECT status, run_ts, error FROM collector_runs
                      WHERE collector = 'haberjev' ORDER BY id DESC LIMIT 1""")
    # Adayi olmayan haber de satir birakir (aday_sayisi 0) ama Jev'e
    # SORULMADI — "sorulan" sayisina girmez.
    b = db.query("""SELECT COALESCE(SUM(aday_sayisi > 0 AND hata IS NULL), 0) sorulan,
                           COALESCE(SUM(sembol IS NOT NULL), 0) bagli,
                           COALESCE(SUM(hata IS NOT NULL), 0) hata
                    FROM haber_bag WHERE ts >= datetime('now', '-1 day')""")[0]
    o = db.query("""SELECT COUNT(*) n, COALESCE(SUM(hata IS NOT NULL), 0) hata
                    FROM haber_olay WHERE ts >= datetime('now', '-1 day')""")[0]
    return {"son": dict(son[0]) if son else None,
            "sorulan": b["sorulan"], "bagli": b["bagli"],
            "etiket": o["n"] - o["hata"], "hata": b["hata"] + o["hata"]}
