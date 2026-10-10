"""
AJAN PANELI + HAKEM — proaktif katmanin ikinci ve ucuncu asamasi.

NEDEN PANEL, TEK AJAN DEGIL
---------------------------
Tek bir ajana "bu firsat mi" diye sormak, ona hem savciligi hem
hakimligi vermek olur; model kendi ilk cumlesini savunmaya meyleder.
Dort ajan BIRBIRINDEN HABERSIZ, farkli mercekten bakar ve hakem
CELISKIYI gorur. Celiski bastirilacak bir kusur degil, en degerli
ciktidir: teknik "al" derken temel "pahali" diyorsa, bilmen gereken sey
tam olarak budur.

Ajanlar bagimsiz calisir (paralel) ve BIRBIRININ CIKTISINI GORMEZ —
gormeseler bile ayni yone isaret ediyorlarsa bu bir bilgidir; birbirini
okusalardi ilk konusana hizalanirlardi.

HEPSI SALT-OKUNUR
-----------------
Panelde yazma araci YOK. Emir gonderme yetkisi hicbir katmanda yok ve
`can_use_tool` kapisi bunu teknik olarak garanti ediyor — `allowed_tools`
listesinin tek basina yetmedigi olculdu.

CIKTI YAPISI
------------
Her ajan sonunda tek bir JSON blogu verir. Serbest metin insan icin,
JSON defter icin: tahmin kaydedilmeden puanlanamaz.
"""
from __future__ import annotations

from ..llm import sdk_ortami

import json
import logging
import re
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

ORTAK_KURALLAR = """
MUTLAK KURALLAR
* Bir sayi soyleyeceksen once ARACLA AL. Hafizandan fiyat/oran/tarih soyleme.
* Elde yoksa "yok" de. Bakmamis olmakla verinin olmamasi AYRI seylerdir.
* Para birimini karistirma. Her seviyeyi hangi para biriminde oldugunu
  yazarak ver.
* Emir/pozisyon buyuklugu/kaldirac ONERME. Sen gozlem ve gerekce
  uretirsin; karari kullanici verir.
* Kripto: F/K, marj, ROE TANIMSIZ. Kademe 1 (resmi dosyalama) karsiligi
  yok; kanit gucun hisseden dusuk, bunu belirt.
* `alinabilir: false` olan gozlem BAGLAMDIR, ADAY DEGIL. Bu coin'ler
  (venue CRYPTO) piyasa degerinde ilk 100'de ama kullanicinin
  borsasinda LISTELENMIYOR — verilebilecek bir emir yok. Onlari
  sermayenin NEREYE dondugunu okumak icin kullan; "al/sat" onerisinin
  KONUSU YAPMA. Ayrica seri CoinGecko bilesigi, Binance defteri degil:
  fiyat USD, en dusuk/en yuksek YOK, gecmis en fazla 1 yil.

CIKTI
Once kisa TURKCE degerlendirme (en fazla 12 satir). Sonra TEK bir JSON
blogu, aynen bu semayla:
```json
{"gorusler":[{"sembol":"XXX","yon":"yukari|asagi|notr","guven":0.0-1.0,
"ufuk_gun":5,"gerekce":"tek cumle","dayanak":["hangi arac ciktisi"]}]}
```
Hakkinda konusacak veri bulamadigin sembolu JSON'a KOYMA — bos liste
gecerli bir cevaptir.
"""

# Panelde KAPALI olan finagent araclari. Ilk ucu YAZAN araclar: panel
# salt-okunur, gozlem uretir, defteri degistirmez. `ibkr_bulut_oku` yazmaz
# ama her cagrisi 15-20 sn'lik ayri bir model oturumu (olculdu 3 Eki) ve
# Ali'nin SOHBET icin istedigi bir arastirma araci; gece panelinin sure
# butcesini yemesin.
PANEL_DISI = ("pozisyon_kaydet", "izlemeye_al", "veri_topla",
              "ibkr_bulut_oku")

# Panel ajaninin ERISEMEDIGI, SDK'nin kendi araclari. Liste sabit ama
# `sinir_metni` bunu `izinli` ile SUZUYOR: biri ileride panele acilirsa
# metin kendiliginden susar.
KABUK_ARACLARI = ("WebSearch", "WebFetch", "Bash", "Read", "Write",
                  "Edit", "Glob", "Grep", "Task")


def panel_araclari() -> list[str]:
    """Panel ajaninin cagirabilecegi finagent araclari (salt-okunur)."""
    from ..bot.tools import ARAC_ADLARI
    return [a for a in ARAC_ADLARI if not a.endswith(PANEL_DISI)]


def sinir_metni(izinli) -> str:
    """
    Ajana SANDBOX'INI ONCEDEN soyleyen prompt eki.

    OLCULEN ARIZA (2026-08-25 sabah kosusu). Panel ajanlari 24 kez
    kapali arac cagirdi: WebSearch 12, Bash 5, WebFetch 4, veri_topla 3.
    Her reddedilen cagri BIR TUR yiyor ve tur payi 16 — `olay` ajani
    payinin 11'ini duvara toslayarak harcadi. `temel` 315 sn'de, HAKEM
    450 sn'de kesildi; Ali'ye "🧠 Model yorumu EKSIK kaldi" gitti.

    Ret mesaji ("panelde kapali") ancak BEDEL ODENDIKTEN SONRA geliyor.
    Ajanin sandbox'i baska turlu ogrenmesinin YOLU YOKTU: ne
    `ORTAK_KURALLAR` ne de ajan talimatlari sinirdan bahsediyordu.

    Metin `izinli`den TURETILIYOR, elle yazilmiyor: sabit bir liste izin
    kumesi degistiginde sessizce yalan soylemeye baslardi — bu projenin
    tekrar eden hata sinifi ("ayni kural iki kopya").
    """
    from ..bot.tools import ARAC_ADLARI
    kapali = [a for a in KABUK_ARACLARI if a not in izinli]
    yazma = sorted(a for a in ARAC_ADLARI if a not in izinli)
    return (
        "\n\nELINDEKI ARACLAR BU KADAR — BASKASINI DENEME.\n"
        f"KAPALI: {', '.join(kapali + yazma)}.\n"
        "Bunlari cagirirsan istek REDDEDILIR ve sayili turlerinden biri "
        "BOSA GIDER. Internete bakamazsin, kabuk calistiramazsin, dosya "
        "okuyamazsin. Bir sey elindeki araclarla ogrenilemiyorsa yapman "
        "gereken ONU ARAMAK DEGIL, 'bakamadim' demek — ve o sembolu "
        "JSON'a KOYMAMAK.")


AJANLAR = {
    "teknik": """Sen TEKNIK ANALIZ ajanisin. Yalnizca fiyat/hacim
davranisina bakarsin: trend (SMA20/50/200 dizilimi), momentum (RSI),
oynaklik, hacim teyidi, saatlik-gunluk ayrimi.
Bir hareketin buyuk olup olmadigini GUNLUK OYNAKLIGA gore soyle — %5
AEX'te olaganustu, kripto mikro-kapta siradan.
Hacim teyidi olmayan hareketi "zayif katilimli" isaretle.
Sirketin ne yaptigi seni ILGILENDIRMEZ; onu baska ajan bakiyor.""",

    "temel": """Sen TEMEL ANALIZ ajanisin. Hisselerde XBRL (gelir, marj,
bilanco, EPS, hisse sayisi), kriptoda TOKENOMIK (arz, FDV, piyasa degeri)
bakarsin.
Donem uzunluklarini KARSILASTIRMA hatasi yapma: 90 gunluk ceyrekle 363
gunluk yili yan yana koyma, hangi donemleri karsilastirdigini yaz.
Hesabini GOSTER ve adimlar sonuca CIKSIN.
Fiyatin nereye gittigi seni ilgilendirmez; deger neresi, ona bak.""",

    "olay": """Sen OLAY/HABER ajanisin. Kademeli haberlere, resmi
dosyalamalara ve olay-etki olcumune (CAR, t-istatistigi) bakarsin.
kademe 1 = sirket/duzenleyici beyani, 2 = ajans/finans basini,
3-4 = toplayici/promosyon -> KANIT DEGIL.
CAR bir KORELASYON olcumudur: "bu haber fiyati %X etkiledi" DEME.
|t|>2 kabaca anlamlilik esigi; altindakine "anlamli degil" de,
"etkisiz" DEME. Olcum GUNE aittir, tek basliga degil.""",

    "risk": """Sen RISK ajanisin. Tek tek firsatlara DEGIL, portfoyun
butunune bakarsin: pozisyon agirliklari, yogunlasma, ayni yone bakan
pozisyonlar, para birimi uyumsuzlugu, acik zararlar.
Isin bir seyi ONERMEK degil, gozden kacan MARUZIYETI gostermek.
Bir aday portfoyde zaten olan bir riski BUYUTUYORSA bunu soyle —
tek basina cazip bir fikir, portfoy baglaminda kotu olabilir.
Sayilari `portfoy` ve `fx` araclarindan al; agirligi kafadan hesaplama.""",
}

_HAKEM_SABLON = """Sen HAKEMSIN. Dort bagimsiz ajanin (teknik, temel, olay, risk)
degerlendirmelerini aldin. Ajanlar BIRBIRINI GORMEDI.

Isin:
1. CELISKILERI ONE CIKAR. Iki ajan ayni sembolde ters yone isaret
   ediyorsa bu en degerli bilgidir — bastirma, goster.
2. Ayni yone isaret eden BAGIMSIZ ajanlari say. Uc ajan ayni yonu
   soyluyorsa bu, bir ajanin uc kez soylemesinden farklidir.
3. Riski her zaman ONE al. Risk ajani bir maruziyet gosterdiyse,
   ne kadar cazip olursa olsun firsatin onune koy.
4. Sessizlik gecerlidir. Ortada gercekten kayda deger bir sey yoksa
   "bugun one cikan bir sey yok" de. Her gun firsat uretmek ZORUNDA
   degilsin; uretmeye calisirsan gurultu uretirsin.

CIKTIN IKI KATMANLI VE HER IKI BASLIK DA ZORUNLU. Ikisini de AYNI
cevapta uret — ikinci bir model turu yok.

### SADE
3-5 satir. Kullanicinin piyasa terimi BILMEDIGI varsayilir. Terim, kisaltma,
gosterge adi kullanma; kullanman gerekiyorsa ayni cumlede bir kez ac.

EN ONEMLI KURAL — IKI YONLU: SADE katman TEKNIK katmandan ne DAHA KESIN
ne DAHA BELIRSIZ konusabilir.

(a) DAHA KESIN OLAMAZ. Teknik katmanda gecmeyen hicbir yon iddiasi,
    tahmin ya da oneri sade katmanda gorunemez. Sade katmanin isi TERIMI
    ACMAK, sonucu KESKINLESTIRMEK degil.

(b) DAHA BELIRSIZ DE OLAMAZ — ve bu, sahada (a) kadar zarar verdi.
    OLCULEN VAKA (2026-08-20): olay ajani "19 Agu tarihli SIRKET
    DUYURUSU (kademe 1) finansman paketi hazirligini beyan ediyor"
    dedi; sade katman bunu "dususun nedeni saglam kaynakla
    DOGRULANMADI — sebep belirsiz" diye ozetledi. Yani DOGRULANMIS
    bulgu atildi, DOGRULANMAMIS cekince tutuldu. Kullanici sebebi
    ogrenemedi ve sistem elindeki kademe-1 kaniti YOK saydi.

    KURAL: kademe 1 (sirketin kendi duyurusu, KAP, SEC) bir seyi
    BEYAN EDIYORSA sade katman onu SOYLEMEK ZORUNDA. "Dogrulanmadi"
    yalnizca GERCEKTEN dogrulanmamis olan icin kullanilir — ve o zaman
    NEYIN dogrulanmadigi yazilir ("55 milyon rakami kademe 4"), "sebep
    belirsiz" gibi hepsini silen bir cumle degil.

Emin olmadigin bir seyi sadelestirirken emin hale getirme; emin
oldugun bir seyi de sadelestirirken belirsizlestirme.

(c) ORAN KESIRLE YUVARLANMAZ. Veride bir oran varsa SAYIYLA yaz:
    "%72", "%40". "Ucte iki", "yarisi", "cogu" gibi kelimeler orani
    KUCULTUR ya da BUYUTUR. OLCULEN VAKA (9 Eki): veri %72 diyordu,
    sade katman "yaklasik ucte ikisi" (%67) yazdi. Ayni sekilde bakis
    SAYIMI (or. "dort bakistan ikisi") yalniz `### BU KOSUNUN SAYIMI`
    blogundan aktarilir; kendin sayma, onceki mesajdan TASIMA.

  RSI 78, hacim teyidi yok
    KOTU : "Asiri alim, duzeltme gelebilir"        <- olmayan kesinlik
    DOGRU: "Son donemde hizli yukselmis. Bu tek basina bir sey
            soylemiyor — yukselise katilan islem hacmi dusuk."
  CAR +%3,1, t=1,2
    KOTU : "Haber fiyati %3 yukari itti"           <- nedensellik iddiasi
    DOGRU: "Haber gununde fiyat yukselmis ama bu, normal dalgalanmadan
            ayirt edilemiyor."

### TEKNIK
Telegram'da okunacak KISA ozet (en fazla 25 satir):
- once RISK varsa risk
- sonra en guclu 2-3 gozlem, her biri icin: ne gorunuyor, hangi ajan
  ne diyor, celiski var mi, izlenecek esik
- guven duzeyi ve bunu YANLIS cikaracak sey

Al/sat emri, pozisyon buyuklugu, kaldirac ONERME.
BICIM: sade Markdown (**kalin**, `kod`, - madde).
BASLIK OLARAK YALNIZCA `### SADE` ve `### TEKNIK` kullan; metnin
icinde baska `##`/`###` baslik ACMA. Bu iki baslik ZORUNLU —
onlar olmadan cevap tek katman sayilir ve sade ozet gonderilemez.

OZETTEN SONRA TEK BIR JSON BLOGU VER. Sebebi: kullanicinin OKUDUGU sey
senin ozetin; olculmesi gereken de odur. Ajanlarin gorusleri ayrica
puanlaniyor ama sen onlari bastirip one cikardigin icin senin nihai
cagrin AYRI bir tahmindir.

Yalnizca gercekten one cikardigin sembolleri koy — ozette gecmeyen
sembol JSON'da OLMAMALI. Hicbir sey one cikmadiysa bos liste ver;
"bugun kayda deger bir sey yok" gecerli ve tercih edilen bir cevaptir.

```json
{"gorusler": [
  {"sembol": "THYAO", "yon": "yukari|asagi|notr", "guven": 0.0,
   "ufuk_gun": 5, "gerekce": "tek cumle",
   "tez": "bu gorusun dayandigi sey",
   "gecersizlesme_kosulu": "MAKINE-OKUNUR, asagidaki gramere UYMAK ZORUNDA",
   "izlenecek_esik": "izlenecek seviye, serbest metin",
   "tur": "alim|koruma|satis|bekle",
   "giris": 132.5, "stop": 127.8}
]}
```

GECERSIZLESME KOSULU GRAMERI — disina cikan kosul KAYDEDILMEZ:
{GRAMER}

### TAKTIK SOZLESMESI — `tur`, `giris`, `stop`

Kullanicinin sordugu soru "ne dusunuyorsun" degil "NE YAPAYIM".
Bir yon iddiasi tek basina eyleme donusmez: nereden girilecegi, nerede
yanlis oldugunun anlasilacagi ve ne kadar sure beklenecegi yazilmadan
o iddia kullanilamaz.

  alim   — su an pozisyon YOK, girmeye deger bir seviye var
  koruma — pozisyon VAR, korunacak seviye var (giris yazma)
  satis  — pozisyon VAR, cikilacak seviye var
  bekle  — bakildi, su an yapilacak bir sey yok. BU DA BIR TAKTIKTIR
           ve sessizlikten farklidir: sessizlik "bakilmadi" demektir.

SEVIYELERI SEN HESAPLAMAZSIN, SECERSIN.
Her sembol icin `### OLCULEN SEVIYELER` bloğunda sana verilen sayilar
var: `son_kapanis`, `stop_2n`, `donchian_giris`, `donchian_cikis`,
`sma20`, `sma50`, `sma200`. `giris` ve `stop` bunlardan BIRI olmak
zorunda — kendi sayini yazarsan taktik REDDEDILIR ve sayilir.

  NEDEN: olculdu 2026-08-18, model bir fiyat bari bile gormeden 335
  pencerelik bir tablo yazdi ve sayilar KALIBRELIYDI ama uydurmaydi.
  Bir seviye, nereden geldigini gosterebilmeli.

Yuvarlama serbest ama seviyeyi DEGISTIRME: 1379,22 yerine 1379,2
yazabilirsin, 1380 yazamazsin.

`alim`da stop girisin ALTINDA olmali — ustunde bir stop aninda
tetiklenir ve hicbir sey korumaz.

Emin degilsen `bekle` yaz. Yanlis bir seviye, seviyesiz bir gorusten
KOTUDUR: ilki eyleme cagirir, ikincisi cagirmaz.

POZISYON BUYUKLUGU, LOT, KALDIRAC YAZMA. Riskin ne kadari alinacagi
KODDA hesaplaniyor (stop mesafesi + portfoy degeri) ve mesaja oradan
ekleniyor; senin yazdigin bir adet sayisi o hesabi bozar.
"""


# Sade katmanda gecerse ama teknikte yon iddiasi yoksa IHLAL sayilir.
# Sadelestirme sirasinda model belirsizlik ifadelerini de atma
# egilimindedir ve sonuc oldugundan EMIN gorunur — asil tehlike terimlerin
# atilmasi degil, kesinligin EKLENMESI.
TAHMIN_DILI = ("gelebilir", "yukselir", "duser", "beklenir", "olacak",
               "yükselir", "düşer", "artacak", "azalacak", "firsat")


def katmanlari_ayir(metin: str) -> tuple[str | None, str]:
    """
    Hakem cevabini (sade, teknik) olarak boler.

    Bolunemezse SADE None doner ve TAMAMI teknik sayilir. Gerekce:
    sessizce yarim mesaj gondermektense tam teknik mesaj gitsin —
    kullanici eksik bir ozeti tam sanmamali.
    """
    import re as _re
    m = _re.search(r"#{2,3}\s*SADE\s*\n(.*?)(?=\n#{2,3}\s*TEKNIK\b)", metin,
                   _re.S | _re.I)
    if not m:
        return None, metin
    t = _re.search(r"#{2,3}\s*TEKNIK\s*\n(.*)$", metin, _re.S | _re.I)
    sade = m.group(1).strip()
    teknik = (t.group(1).strip() if t else metin)
    return (sade or None), teknik


def sade_kesinlik_ihlali(sade: str | None, veri: dict) -> int:
    """
    Sade katmanda tahmin dili var ama teknik tarafta karsilik gelen bir
    yon iddiasi yoksa IHLAL. Ucuz, ileriye donuk ve varsayimi olcume
    ceviriyor — JSON⊆ozet kontroluyle ayni kalip.
    """
    if not sade:
        return 0
    kucuk = sade.lower()
    if not any(k in kucuk for k in TAHMIN_DILI):
        return 0
    yonlu = any((g or {}).get("yon") in ("yukari", "asagi")
                for g in (veri.get("gorusler") or []))
    return 0 if yonlu else 1


# SADE katmanin "hepsini silen" belirsizlik kaliplari. Bunlar TEK
# BASINA yasak degil — teknik katmanda kademe-1 bir beyan VARKEN
# kullanilmalari yasak.
_BELIRSIZLIK_KALIPLARI = (
    "dogrulanmadi", "doğrulanmadı", "sebep belirsiz", "neden belirsiz",
    "saglam kaynak yok", "sağlam kaynak yok", "kaynakla dogrulanmadi",
    "kaynakla doğrulanmadı", "nedeni bilinmiyor", "acikligi yok",
)
# Teknik katmanda kademe-1 bir kanit oldugunu gosteren isaretler.
_KADEME1_KALIPLARI = ("kademe 1", "kademe-1", "kademe1")


def sade_kanit_dusurdu(sade: str | None, veri: dict, db,
                       gun: int = 3) -> int:
    """
    Sade katman "dogrulanmadi / sebep belirsiz" derken VERITABANINDA o
    enstruman icin KADEME-1 haber varsa IHLAL.

    OLCULEN VAKA (2026-08-20): olay ajani "sirket duyurusu (kademe 1)
    finansman paketi hazirligini beyan ediyor" dedi; sade katman
    "dususun nedeni saglam kaynakla dogrulanmadi" diye ozetledi.
    Dogrulanmis bulgu atildi, dogrulanmamis cekince tutuldu — sistem
    elindeki kaniti YOK saydi (`yanlis-yok-beyani` sinifi).

    NEDEN TEKNIK METNI DEGIL VERITABANI OKUNUYOR: ilk surum teknik
    katmanda "kademe 1" ifadesini ariyordu ve KENDI TESTI kirdi —
    "kademe 1-2 kaydi OLMADIGI icin olcum yapilamadi" cumlesi de o
    ifadeyi iceriyor. Yani metin eslemesi OLUMSUZ bir beyani OLUMLU
    sandi. Kanitin varligi bir METIN sorusu degil, bir VERI sorusudur:
    `news.tier = 1` var mi, yok mu.

    `sade_kesinlik_ihlali`in SIMETRIGI ve ayni kalip: prompt kurali
    yeterli degil, ihlal SAYILIR ve `panel_runs.hata`ya yazilir.

    IHLALI BLOKE ETMIYOR: bildirimi durdurmak, kullaniciyi bilgisiz
    birakmanin daha kotu hali olurdu. Gorunur kilmak yeter.
    """
    if not sade or db is None:
        return 0
    s = sade.lower()
    if not any(k in s for k in _BELIRSIZLIK_KALIPLARI):
        return 0                       # belirsizlik iddiasi yok
    if any(k in s for k in _KADEME1_KALIPLARI):
        return 0                       # sade katman kademe-1'i TASIYOR
    semboller = {str(g.get("sembol") or "").strip().upper()
                 for g in (veri.get("gorusler") or [])}
    semboller.discard("")
    if not semboller:
        return 0
    sinir = (datetime.now(timezone.utc)
             - timedelta(days=max(1, gun))).strftime("%Y-%m-%d")
    for sem in sorted(semboller):
        try:
            r = db.query(
                """SELECT 1 FROM news
                   WHERE tier = 1 AND published_at >= ?
                     AND (',' || UPPER(COALESCE(symbols,'')) || ',') LIKE ?
                   LIMIT 1""", (sinir, f"%,{sem},%"))
        except Exception as e:                        # noqa: BLE001
            # SORGU PATLARSA IHLAL SAYMA. "Sorgu basarisiz" ile "kanit
            # yok" ayri seyler; ikisini karistirmak bu projenin en kotu
            # hata sinifi (bkz. bekci ders notu).
            log.warning("[panel] kanit kontrolu sorgusu basarisiz: %s", e)
            return 0
        if r:
            return 1
    return 0


def hakem_prompt() -> str:
    """
    HAKEM prompt'u URETILIYOR, elle yazilmiyor.

    Kosul grameri `pulse.tez`'de tanimli; prompt'a elle kopyalansaydi
    alan listesi degistiginde ikisi sessizce ayrisirdi — bu projenin
    tekrar eden kusur sinifi (prompt "FX yok" derken `fx` araci vardi).
    """
    from .tez import gramer_metni
    return _HAKEM_SABLON.replace("{GRAMER}", gramer_metni())


def _json_cek(metin: str, anahtar: str = "gorusler") -> dict:
    """
    Cevabin sonundaki JSON blogunu ayiklar.

    Model bazen ```json cite icinde, bazen ciplak veriyor; ikisi de
    kabul. Ayristirilamazsa BOS doner — uydurma yerine bos, cunku bu
    veri tahmin defterine girecek ve yanlis kayit puanlamayi bozar.

    `anahtar` PARAMETRE — OLCULEN KUSUR (B6 testinde yakalandi):
    ciplak-JSON yolu `"gorusler"`e SABITLENMISTI. Taktikcinin promptu
    "YALNIZCA JSON dondur" diyor, yani BEKLENEN cikti bicimi tam da
    ciplak `{"taktikler": [...]}`. Sabit anahtarla o cikti HIC
    ayristirilamiyordu: butun taktikler sessizce dusuyor ve rapor
    "cikti liste degil" diyordu. Sessiz kayip, gorunur hatadan kotudur.
    """
    # 1) METNIN TAMAMI JSON ISE dogrudan oku. "Yalnizca JSON dondur"
    #    talimatina UYAN model tam olarak bunu uretir ve onu once
    #    denememek, dogru davranan modeli cezalandirmakti.
    duz = metin.strip()
    if duz.startswith("{") and duz.endswith("}"):
        try:
            veri = json.loads(duz)
            if isinstance(veri, dict):
                return veri
        except json.JSONDecodeError:
            pass
    for kalip in (r"```json\s*(\{.*?\})\s*```", r"```\s*(\{.*?\})\s*```"):
        m = re.findall(kalip, metin, re.S)
        if m:
            try:
                return json.loads(m[-1])
            except json.JSONDecodeError:
                continue
    m = re.findall(rf'\{{[^{{}}]*"{re.escape(anahtar)}"\s*:\s*\[.*?\]\s*\}}',
                   metin, re.S)
    if m:
        try:
            return json.loads(m[-1])
        except json.JSONDecodeError:
            pass
    return {}


def sure_kancasi_yap(son_tarih: float | None, ad: str = "?",
                     mesaj: str | None = None, kayit: list | None = None):
    """
    `PreToolUse` kancasi: son tarih gectiyse YENI arac cagrisini reddeder.

    NEDEN GEREKTI — CANLI OLCUM (2026-08-21):
    `anyio.CancelScope` iptali ancak bir AWAIT noktasinda islenebilir.
    MCP araclari BIZIM SURECIMIZDE kosuyor ve govdeleri bloke edici
    (senkron HTTP/DB); bir arac calisirken olay dongusu DONUYOR ve son
    tarih HIC KONTROL EDILEMIYOR. O sabah `haberler` araci sembol basina
    tek tek cekim yapti, panel 7,5 dk'lik payina karsi 17 dk kostu ve
    kabuk kosunun TAMAMINI oldurdu — ne iz, ne tahmin, ne mesaj. Ajan
    kesme mesaji o gune kadar logda 0 kez gorunuyordu: koruma vardi ama
    ASLA calisamamisti.

    Bu kanca iptali KURTARMAZ — kosmakta olan arac biter — ama YENI
    bloke edici is baslamasini engeller, yani asim en fazla TEK arac
    cagrisi kadar olur.

    `can_use_tool` BU ISI GOREMEZ: SDK'nin kendi uyarisi
    (`CanUseToolShadowedWarning`) soyluyor — `allowed_tools`'ta tam
    adiyla gecen bir arac, geri cagri DANISILMADAN onaylaniyor. Vision
    oturumunda ayni sey olculmustu; cozum orada da `PreToolUse`'du.

    `mesaj` ve `kayit` PARAMETRE cunku ayni kanca IKI yerde kullaniliyor:
    panel ajanlarinda ve SOHBET turunda. Ikinci bir kopya yazmak, bu
    projenin tekrar eden kusur sinifi olurdu (biri duzeltilir, digeri
    sessizce eski kalir).

    AYRI FONKSIYON, `_ajan` govdesinde kapanis DEGIL: govdeye gomulu
    oldugunda testi ancak kaynak metninde "PreToolUse" arayarak
    dogrulayabiliyordum ve o dizgi kancanin KENDI ciktisinda da gectigi
    icin, kanca tamamen sokulse bile test GECIYORDU (kasitli kirmada
    yakalandi). Disari alinca davranisi dogrudan olculebiliyor.
    """
    async def _kanca(girdi, arac_kimligi, baglam):
        if son_tarih is None:
            return {}
        import anyio as _anyio
        if _anyio.current_time() < son_tarih:
            return {}
        arac = (girdi or {}).get("tool_name", "?")
        log.warning("[%s] sure doldu — '%s' cagrisi REDDEDILDI", ad, arac)
        if kayit is not None and arac not in kayit:
            # KESILDIGI SOYLENEBILSIN. Cagiran taraf hangi araclarin
            # calistirilamadigini kullaniciya yaziyor; sessiz bir
            # kesinti, eksik cevabi TAM cevap gibi gosterirdi.
            kayit.append(arac)
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (mesaj or
                "Sure siniri doldu. Yeni veri cekme; ELINDEKI bilgiyle "
                "SIMDI sonucu yaz.")}}
    return _kanca


class Panel:
    """
    Dort ajan + hakem. HER PANELIN BIR DUVAR SAATI VARDIR.

    `sure_siniri_sn` ZORUNLU ve VARSAYILANI YOK — bilerek.

    2026-08-21 sabah kosusu bu yuzden oldu: panelin hicbir sure siniri
    yoktu. `max_turns` TUR sayisini sinirliyor, SURE'yi degil; bir tur
    dakikalarca surebilir. `runner`'daki `panel_butce_sn` kontrolu ise
    yalnizca SAHIPLER ARASINDA bakiyordu, yani birinci sahibin paneli
    her zaman basliyor ve istedigi kadar surebiliyordu. Olculen: ali'nin
    paneli 1055 sn kostu ve BITMEDI; kabuk 1500 sn'de tum surec grubunu
    oldurdu. Sonuc: 0 panel_runs, 0 tahmin, iz yok, mesaj yok — 25
    dakika hesap, iki kullanici, sifir cikti.

    Varsayilan konsaydi cagiranlar onu sessizce miras alirdi ve "bu
    panelin siniri ne" sorusunun cevabi yine tek bir yerde gizlenirdi.
    Eksik parametre GURULTULU patlamali (`sahip` ile ayni gerekce).
    """

    # Sure butcesinin ajanlara ayrilan orani; kalani hakeme.
    #
    # Hakem ajanlarin ciktisini OKUR, arac cagirmaz ve tek tur uretir —
    # yani ucuz. Ama SIFIR birakilamaz: hakem kosamazsa kullaniciya
    # gidecek ozet metni HIC uretilmez ve panel bosa harcanmis olur.
    AJAN_PAYI = 0.70

    # HAKEMIN TABANI — ORAN DEGIL, REZERVASYON. (2026-09-08'de olculdu.)
    #
    # `AJAN_PAYI` bir ORANDI ve butce kuculdukce hakemin payi da
    # kuculuyordu. Panel butcesi iki sahibe bolununce (900/2 = 450) ve
    # ajanlar oranlarini (0,7 x 450 = 315) sonuna kadar kullanabildigi
    # icin hakeme 135 sn kaliyordu. OLCULEN IHTIYAC: hakem tamamlanan
    # 29 kosuda medyan 185 sn, azami 257 sn surdu. Yani hakem
    # YAPISAL OLARAK kesiliyordu — 1-8 Eylul arasi 42 kosunun 22'si.
    #
    # Ajan fazi ayni pencerede medyan 234 sn, p90 289 sn, azami 319 sn.
    #
    # ONCELIK NEDEN HAKEMDE: bir ajanin dusmesi bes girdiden birini
    # kaybettirir, hakem yine sentez yapar. Hakemin dusmesi ise
    # kullaniciya giden TUM metni ve deftere giren TUM satirlari
    # goturur — yani karne o kosuda SESSIZCE kuculur.
    HAKEM_ASGARI_SN = 260.0

    # Tabanin kendisi de sinirli: cok kucuk bir butcede hakeme her seyi
    # verip ajanlari 0'a indirmek, hakemi OKUYACAK bir sey birakmaz.
    HAKEM_AZAMI_PAY = 0.60

    def __init__(self, settings, db, sahip: str | None = None, *,
                 sure_siniri_sn: float):
        self.s = settings
        self.db = db
        self.sahip = sahip
        self.model = settings.get("analysis.llm.strategist_model", "claude-opus-5-5")
        if not isinstance(sure_siniri_sn, (int, float)) \
                or isinstance(sure_siniri_sn, bool) or sure_siniri_sn <= 0:
            raise ValueError(
                "Panel: `sure_siniri_sn` pozitif sayi olmali, "
                f"{sure_siniri_sn!r} verilmis. Sinirsiz panel, kabugun "
                "tum kosuyu oldurmesi demektir (2026-08-21).")
        self.sure_siniri_sn = float(sure_siniri_sn)

    def hakem_payi_sn(self) -> float:
        """
        Hakeme AYRILAN (ajanlardan artan degil) sure. Gerekce ve olculen
        sayilar `HAKEM_ASGARI_SN`de.

        Buyuk butcede eski davranis korunuyor: 1800 sn'de oran (0,3)
        zaten tabandan buyuk ve 540 sn cikiyor. Kucuk butcede taban
        devreye giriyor ve ajanlar kisiliyor.
        """
        return min(max(self.HAKEM_ASGARI_SN,
                       self.sure_siniri_sn * (1.0 - self.AJAN_PAYI)),
                   self.sure_siniri_sn * self.HAKEM_AZAMI_PAY)

    # ------------------------------------------------------------------
    async def _ajan(self, ad: str, talimat: str, gundem: str,
                    son_tarih: float | None = None,
                    tampon: list | None = None) -> tuple[str, dict]:
        from claude_agent_sdk import (ClaudeAgentOptions, HookMatcher, query,
                                      PermissionResultAllow, PermissionResultDeny)
        from ..bot.tools import ToolBox

        # YAZMA ARACLARI PANELDE YOK — panel salt-okunur.
        okuma = panel_araclari()
        izinli = set(okuma)
        # ARACLAR DA BU SINIRI BILMELI. `haberler` bos donunce modele
        # "SIMDI WebSearch ile ara" diyordu ve panelde WebSearch KAPALI:
        # ajan emri uygulamaya calisip reddediliyordu. Elle `False`
        # YAZILMIYOR, izin listesinden HESAPLANIYOR — yoksa kural iki
        # kopya olur ve ayrisir (bu projenin tekrar eden hata sinifi).
        tb = ToolBox(self.s, self.db, self.s.root / "data" / "bot" / "pending",
                     sahip=self.sahip, web_arama="WebSearch" in izinli)

        async def kapi(tool_name, tool_input, context):
            if tool_name in izinli:
                return PermissionResultAllow()
            log.warning("[panel:%s] arac reddedildi: %s", ad, tool_name)
            return PermissionResultDeny(message=f"'{tool_name}' panelde kapali")

        async def akis():
            yield {"type": "user", "message": {"role": "user", "content": gundem}}

        # SON TARIH GECTIYSE YENI ARAC CAGRISI YOK.
        #
        # OLCULEN KUSUR (2026-08-21): `CancelScope` iptali ancak bir
        # AWAIT noktasinda islenebilir. MCP araclari BIZIM SURECIMIZDE
        # calisiyor ve govdeleri bloke edici (senkron HTTP/DB); bir arac
        # kosarken olay dongusu DONUYOR ve son tarih HIC KONTROL
        # EDILEMIYOR. O sabah `haberler` araci sembol basina tek tek
        # cekim yapti, panel 7,5 dk'lik payina karsi 17 dk kostu ve
        # kabuk kosunun tamamini oldurdu. Kesme mesaji logda 0 kez
        # gorunuyordu: koruma vardi ama ASLA calisamamisti.
        #
        # Bu kanca iptali kurtarmaz — KOSMAKTA OLAN aracin bitmesini
        # bekler — ama YENI bloke edici is baslamasini engeller, yani
        # asim en fazla TEK arac cagrisi kadar olur.
        #
        # `can_use_tool` BU ISI GOREMEZ: SDK'nin kendi uyarisi
        # (`CanUseToolShadowedWarning`) soyluyor — `allowed_tools`'ta
        # tam adiyla gecen bir arac, geri cagri DANISILMADAN onaylaniyor.
        # Vision oturumunda ayni sey olculmustu; cozum orada da
        # `PreToolUse` kancasiydi.
        _sure_kancasi = sure_kancasi_yap(
            son_tarih, f"panel:{ad}",
            "Panel sure siniri doldu. Yeni veri cekme; ELINDEKI "
            "bilgiyle SIMDI sonucu yaz.")

        opts = ClaudeAgentOptions(
            **sdk_ortami(),
            system_prompt=talimat + ORTAK_KURALLAR + sinir_metni(izinli),
            model=self.model, mcp_servers={"finagent": tb.sunucu()},
            allowed_tools=okuma, can_use_tool=kapi,
            hooks={"PreToolUse": [HookMatcher(hooks=[_sure_kancasi])]},
            max_turns=int(self.s.get("analysis.llm.panel_max_turns", 16)),
            max_buffer_size=64 * 1024 * 1024,
        )
        # TAMPON DISARIDAN GELIR — KESILME ANINDA KURTARMA ICIN.
        #
        # OLCULEN KUSUR (2026-09-08): kesilen kosuda `ham_metin` SIFIR
        # uzunluktaydi (22 kosunun 22'sinde). Sebep buydu: parcalar bu
        # coroutine'in YEREL degiskeniydi ve `CancelScope` iptali
        # coroutine'i oldurunce o ana kadar akan metin de olurdu.
        # Semanin kendi gerekcesi "gorus kaybolmaz, ham metin duruyor"
        # diyordu; kesilme durumunda DOGRU DEGILDI.
        #
        # Liste cagirana ait oldugu icin iptalden SAG CIKIYOR.
        parcalar = tampon if tampon is not None else []
        async for m in query(prompt=akis(), options=opts):
            ic = getattr(m, "content", None)
            if not ic or isinstance(ic, str):
                continue
            for b in ic:
                if getattr(b, "text", None):
                    parcalar.append(b.text)
        metin = "\n".join(parcalar).strip()
        return metin, _json_cek(metin)

    # ------------------------------------------------------------------
    async def calistir(self, sinyaller: list[dict],
                       haber: dict | None = None) -> dict:
        """Dort ajani PARALEL calistirir, sonra hakemi."""
        import anyio

        # HABER TEK BASINA DA GUNDEM KURAR.
        #
        # Once kosul `if not sinyaller: return` idi: fiyat esigi
        # gecilmediginde panel HIC calismiyordu. Ama fiyat sinyali
        # nadir (esikler oynakliga gore ve dogru olarak siki), haber
        # ise her gun var. Sonuc: sessiz gunlerde kullaniciya hicbir
        # sey gitmiyordu — oysa o gun kademe 1-2 haberi olan bir
        # kagidi olabilir.
        #
        # AYRICA: 2026-08-20 backtest'i fiyat sinyallerinin 24
        # hucresinin 22'sinde sifirdan ayirt edilemedigini gosterdi.
        # Paneli YALNIZCA o sinyallere baglamak, olculmus zayif bir
        # girdiye bagli kalmak demekti.
        haberli = bool(haber and (haber.get("bagli_haberler")
                                  or haber.get("bagsiz_haberler")))
        if not sinyaller and not haberli:
            return {"ozet": None, "ajanlar": {}, "gorusler": []}

        parcalar = []
        if sinyaller:
            parcalar.append(
                "Tarayici bugun su gozlemleri uretti (deterministik, LLM yok). "
                "Kendi mercegin uzerinden degerlendir; gerekli veriyi "
                "ARACLARLA cek.\n\n```json\n"
                + json.dumps(sinyaller[:12], ensure_ascii=False, indent=1)
                + "\n```")
        if haberli:
            parcalar.append(
                "Ayrica son gunlerin KADEME 1-2 haberleri asagida — "
                "SIRALANMAMIS ham dosya. Sirayi sen kur; kurallar dosyanin "
                "`ZORUNLU` alaninda.\n\n```json\n"
                + json.dumps(haber, ensure_ascii=False, indent=1)[:14000]
                + "\n```")
        gundem = "\n\n".join(parcalar)

        sonuc: dict[str, tuple[str, dict]] = {}

        # --- DUVAR SAATI ------------------------------------------------
        # Iki AYRI son tarih, cunku iki ayri sey korunuyor:
        #   `ajan_bitis`  bir ajanin digerlerini ve hakemi ac birakmasini
        #   `panel_bitis` panelin kosunun tamamini goturmesini
        # Mutlak zaman kullaniliyor (sure degil): asama asama "kalan"
        # hesaplamak, her asamada butcenin YENIDEN baslamasi demekti.
        #
        # AJAN SON TARIHI HAKEMDEN GERIYE HESAPLANIR, oranla ILERIYE
        # degil: hakemin payi rezervasyon (bkz. `hakem_payi_sn`).
        panel_bitis = anyio.current_time() + self.sure_siniri_sn
        hakem_payi = self.hakem_payi_sn()
        ajan_bitis = panel_bitis - hakem_payi
        kesilen: list[str] = []
        tamponlar: dict[str, list] = {}

        async def kos(ad, talimat):
            tampon = tamponlar.setdefault(ad, [])
            try:
                # HER AJAN KENDI KAPSAMINDA kesilir, ortak bir grup
                # kapsaminda degil: grup kapsami dolunca HEPSI birden
                # iptal olurdu ve bitmek uzere olan ajanin ciktisi da
                # giderdi. Boylece yalnizca gec kalan kesilir.
                with anyio.CancelScope(deadline=ajan_bitis) as kapsam:
                    # SON TARIH AJANA DA VERILIYOR: kapsam iptali
                    # bloke edici bir arac kosarken ISLENEMIYOR, o
                    # yuzden ajanin kendi kancasi yeni arac cagrilarini
                    # kesiyor (bkz. `_ajan`).
                    sonuc[ad] = await self._ajan(ad, talimat, gundem,
                                                 son_tarih=ajan_bitis,
                                                 tampon=tampon)
                if kapsam.cancelled_caught:
                    # SESSIZ KESINTI YOK: hangi ajanin kesildigi hem loga
                    # hem panel_runs'a yaziliyor, yoksa "panel neden zayif
                    # cikti" sorusu veriden cevaplanamaz.
                    #
                    # VE KISMI CIKTI ATILMAZ: tampon cagirana ait, iptalden
                    # sag cikiyor. JSON blogu tamamlanmissa gorusler de
                    # KURTARILIYOR (`_json_cek` yarim blogu zaten reddeder,
                    # yani uydurma riski yok).
                    kesilen.append(ad)
                    kismi = "\n".join(tampon).strip()
                    veri = _json_cek(kismi) if kismi else {}
                    log.error("[panel] %s ajani SURE SINIRINDA kesildi "
                              "(%.0f sn) — kismi cikti %d karakter, "
                              "kurtarilan gorus %d", ad,
                              ajan_bitis - (panel_bitis - self.sure_siniri_sn),
                              len(kismi), len(veri.get("gorusler") or []))
                    sonuc[ad] = (
                        kismi or (f"(ajan sure sinirinda kesildi, kismi cikti "
                                  f"da yok)"), veri)
            except Exception as e:                    # noqa: BLE001
                log.exception("[panel] %s ajani basarisiz", ad)
                sonuc[ad] = (f"(ajan calismadi: {type(e).__name__}: {e})", {})

        async with anyio.create_task_group() as tg:
            for ad, talimat in AJANLAR.items():
                tg.start_soon(kos, ad, talimat)

        # SINYAL BAGI: gorus bir sembole ait, sinyal de oyle. Tahmini
        # doguran sinyali baglamazsak backtest (signal_stats) ile defter
        # iki ayri ada kalir — "bu tip tarihsel olarak ne yapti" ile
        # "bizim bu tipteki isabetimiz ne" birbirine baglanamaz. Ayni
        # sembolde birden fazla sinyal varsa EN GUCLUSU baglanir.
        sinyal_id = {}
        for s in sinyaller:
            sem = str(s.get("sembol", "")).upper()
            if s.get("id") and (sem not in sinyal_id
                                or s.get("guc", 0) > sinyal_id[sem][1]):
                sinyal_id[sem] = (s["id"], s.get("guc", 0))

        gorusler = []
        for ad, (_, veri) in sonuc.items():
            for g in (veri.get("gorusler") or []):
                if isinstance(g, dict) and g.get("sembol"):
                    sid = sinyal_id.get(str(g["sembol"]).upper())
                    gorusler.append({**g, "ajan": ad,
                                     "signal_id": sid[0] if sid else None})

        panel_idleri = self._kosuyu_yaz(sonuc, kesilen)

        # HAKEM DE SINIRLI, ve KENDINE AYRILAN sureyi alir (artani
        # degil — bkz. `hakem_payi_sn`). Kesilirse kismi cikti
        # KURTARILIR; cagiran taraf durumu `panel_notu` ile kullaniciya
        # soyluyor.
        ozet, hakem_veri = "", {}
        hakem_tamponu: list = []
        with anyio.CancelScope(deadline=panel_bitis) as hakem_kapsami:
            ozet, hakem_veri = await self._hakem(sinyaller, sonuc, gorusler,
                                                 tampon=hakem_tamponu)
        if hakem_kapsami.cancelled_caught:
            # KISMI CIKTI ATILMIYOR. Onceden burada `ozet, hakem_veri =
            # "", {}` yaziyordu ve modelin o ana kadar urettigi HER SEY
            # — hem metin hem tamamlanmis JSON blogu — cope gidiyordu.
            # 1-8 Eylul: 42 hakem kosusunun 22'si boyle bitti, 22'sinde
            # de `ham_metin` sifir uzunluktaydi. Karne bu kosularda
            # SESSIZCE kuculuyordu; kayip gorunur bir bosluk birakmiyordu.
            kesilen.append("hakem")
            ozet = "\n".join(hakem_tamponu).strip()
            hakem_veri = _json_cek(ozet) if ozet else {}
            log.error("[panel] HAKEM sure sinirinda kesildi (hakem payi "
                      "%.0f sn / panel butcesi %.0f sn) — kismi cikti %d "
                      "karakter, kurtarilan gorus %d", hakem_payi,
                      self.sure_siniri_sn, len(ozet),
                      len(hakem_veri.get("gorusler") or []))
        hakem_gorusler, taktik_rapor = [], {"gecerli": 0, "reddedilen": []}
        for g in (hakem_veri.get("gorusler") or []):
            if isinstance(g, dict) and g.get("sembol"):
                sid = sinyal_id.get(str(g["sembol"]).upper())
                g = self._taktigi_dogrula(g, taktik_rapor)
                hakem_gorusler.append({**g, "ajan": "hakem",
                                       "signal_id": sid[0] if sid else None})
        if taktik_rapor["reddedilen"]:
            log.warning("[panel] taktik REDDEDILDI: %s",
                        taktik_rapor["reddedilen"])
        panel_idleri.update(self._kosuyu_yaz({"hakem": (ozet, hakem_veri)},
                                             kesilen))

        sade, teknik = katmanlari_ayir(ozet)
        return {"ozet": teknik, "sade": sade,
                "ajanlar": {k: v[0] for k, v in sonuc.items()},
                "gorusler": gorusler, "hakem_gorusler": hakem_gorusler,
                # KESILENLER CIKTIYA TASINIR. Cagiran taraf bunu
                # kullaniciya "panel eksik kostu" diye soyluyor; tasinmazsa
                # yarim bir panel TAM panel gibi okunur.
                "kesilen": kesilen,
                # TAKTIKLER AYRICA TASINIYOR: mesaj katmani onlari
                # ozetin ICINDEN ayiklamak zorunda kalmasin. `bekle`
                # disarida — "su an bir sey yapma" mesaja satir acmaz,
                # ama deftere YAZILIR (bakildi ve karar verildi kaydi).
                "taktikler": [g for g in hakem_gorusler
                              if g.get("tur") and g["tur"] != "bekle"],
                "taktik_reddedilen": taktik_rapor["reddedilen"],
                # Sayaclarin YAZILACAGI satirlar — zaman damgasi degil.
                "panel_idleri": panel_idleri}

    def _not(self, metin: str, veri: dict) -> str | None:
        """`panel_runs.hata` alanina yazilacak tanisal not."""
        if not veri:
            return "JSON blogu ayristirilamadi"
        tasan = self._json_ozetin_disina_tasti_mi(metin, veri)
        if tasan:
            # Ihlal, yapisal ciktinin duzyaziyi ezdigine isaret eder.
            return f"JSON'da ozette gecmeyen {tasan} sembol"
        sade, _ = katmanlari_ayir(metin)
        if sade_kesinlik_ihlali(sade, veri):
            return "sade_kesinlik_ihlali"
        # SIMETRIK IHLAL: sade katman kademe-1 kaniti dusurup yerine
        # "dogrulanmadi" koydu. Kesinlik ihlaliyle AYNI AGIRLIKTA ve
        # sahada once o gorundu (2026-08-20, AVTX).
        if sade_kanit_dusurdu(sade, veri, self.db):
            return "sade_kanit_dusurdu (kademe-1 beyan sade katmanda yok)"
        # SADE KATMANIN URETILMEMESI KENDINI GIZLIYORDU.
        #
        # `sade_kesinlik_ihlali(None, ...)` tanim geregi 0 doner — ihlal
        # aranacak bir metin yok — ve kontrol sessizce geciyordu. Ilk
        # kosuda katmanlarin hic uretilmedigini INSAN GOZU yakaladi
        # (prompt'taki "## kullanma" kurali `### SADE` basligini
        # yasakliyordu); ikinci kez olsa yakalayacak hicbir sey yoktu.
        #
        # Sirasi onemli: JSON ayristirma ve sembol tasmasi kontrollerinden
        # SONRA, cunku onlar daha temel arizalar; sessizlik kontrolunden
        # ONCE, cunku katman yoksa "sessiz kaldi" teshisi yaniltici olur.
        if sade is None:
            return "sade_katman_yok"
        if not (veri.get("gorusler") or []):
            # SESSIZLIK BIR SECIMDIR ve olculmelidir. Hakem "bugun kayda
            # deger bir sey yok" derse deftere sifir kayit girer; yani
            # sistem KONUSTUGU gunlerde olculur, SUSTUGU gunlerde
            # olculmez. Iyi susmak karneye hic yansimaz. Bu, `_json_cek`
            # yanliliginin bir kat yukarisi: olcum populasyonu modelin
            # kendi davranisina gore seciliyor.
            return "sessiz kaldi (gorus yok)"
        return None

    @staticmethod
    def _json_ozetin_disina_tasti_mi(metin: str, veri: dict) -> int:
        """
        JSON'daki semboller ozette GECIYOR MU — kacini gecmiyor?

        Yapisal cikti istemek modeli "bos liste vermektense bir sey
        yazayim" tarafina itebilir. Prompt bunu yasakliyor ("ozette
        gecmeyen sembol JSON'da OLMAMALI") ama bu OLCULMEMIS bir
        varsayimdi. Ihlal sayilirsa, yapisal ciktinin duzyazi karari
        ezip ezmedigi gorunur hale gelir.

        Buyuk harf duyarli arama: kripto/BIST sembolleri buyuk harf ve
        29 BIST sembolu gundelik Turkce kelimeyle cakisiyor (HEDEF,
        KENT, LIDER...) — kucuk harfe indirsek "hedef fiyat" gecen bir
        cumle HEDEF sembolunu gecmis sayardi.
        """
        govde = metin.split("```")[0]
        tasan = 0
        for g in (veri.get("gorusler") or []):
            sem = str((g or {}).get("sembol", "")).strip()
            if sem and sem not in govde:
                tasan += 1
        return tasan

    def _kosuyu_yaz(self, sonuc: dict, kesilen=()) -> dict:
        """
        Her ajanin HAM cevabini `panel_runs`'a yazar; ajan -> SATIR ID doner.

        `kesilen` AYRI BIR DURUM URETIR (`json_durum='kesildi'`), cunku
        "model konusmadi" ile "model konusuyordu, biz kestik" ayni sey
        DEGIL ve ikincisi BIZIM arizamiz. Ikisi `bos` kovasinda
        birlestiginde 22 kesilme "model sustu" gibi okunuyordu.
        Kesilen kosuda kismi metin ve — JSON tamamlandiysa — kurtarilan
        gorus sayisi da yaziliyor.

        Sebebi olculdu: "`_json_cek` simdiye kadar kac turda bos dondu"
        sorusu GERIYE DONUK cevaplanamadi, cunku hicbir iz yoktu. Sayac
        ileriye donuk cozerdi; ham metin saklamak, bugun sormadigimiz
        sorulari da cozer. Gozlemlenebilirlik yoksa hata sinifi gorunmez.

        ID DONDURULUYOR, ZAMAN DAMGASI DEGIL. Atilan sayaclari once
        "o kosunun EN SON run_ts'i" ile bulunuyordu ve bu, panellerin
        SIRAYLA kosmasi sayesinde dogruydu — tasarimdan degil TESADUFTEN.
        Iki sahibin damgasi ayni saniyeye duserse sayaclar yanlis satira
        yazilirdi. `executemany` yerine dongu, cunku `lastrowid` satir
        basina gerekiyor; kosu basina 5 satir, maliyeti yok.
        """
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        idler = {}
        try:
            with self.db.tx() as c:
                for ad, (metin, veri) in sonuc.items():
                    cur = c.execute(
                        """INSERT INTO panel_runs
                           (run_ts, ajan, ham_metin, json_durum, gorus_sayisi,
                            hata, sahip)
                           VALUES (?,?,?,?,?,?,?)""",
                        (ts, ad, metin,
                         "ajan_hatasi" if metin.startswith("(ajan calismadi")
                         else ("kesildi" if ad in kesilen
                               else ("ok" if veri else "bos")),
                         len(veri.get("gorusler") or []),
                         self._not(metin, veri), self.sahip or "ali"))
                    idler[ad] = cur.lastrowid
        except Exception as e:                        # noqa: BLE001
            # Kayit tutamamak kosuyu DUSURMEMELI: nabzin isi analiz,
            # panel_runs gozlem icin.
            log.warning("[panel] ham cikti yazilamadi: %s", e)
        return idler

    # GUNUN ONCEKI KOSULARI — yalnizca HAKEME, ajanlara DEGIL.
    #
    # Ajanlar birbirini gormedigi gibi gecmisi de gormemeli:
    # bagimsizlik AJAN katmaninda, sentez HAKEMDE. Bir ajana "bu sabah
    # sunu demistin" demek, onu kendi onceki cikarimina demirler ve
    # panelin uretmesi gereken CELISKIYI bastirir.
    GUNUN_AZAMI_KOSUSU = 3
    SADE_KIRPMA = 600

    # GONDERILEN, URETILEN DEGIL (9 Eki, olgu denetimi). Blok eskiden
    # `panel_runs`tan okuyordu: bugun KOSAN her hakem ciktisi. 8 Eki'den beri
    # sabah/ogle ozetleri GONDERILMIYOR (ritim.kipler.*.ozet) ama panel kosuyor;
    # istem "bunlar kullaniciya gonderildi" diyordu. Olculen sonuc: 9 Eki gece
    # nabzi "Sabah soylenenlerde degisen yok" diye acildi (Ali sabah ozeti
    # almamisti) ve 10:39 panelinin "INGA 4/4 asagi" sayimini tasidi — o gece
    # ajanlarin yalniz 2'si asagi diyordu. Kaynak artik `sohbet_kaydi`:
    # kullaniciya GERCEKTEN giden ozetlerin panel bolumu.
    OZET_KAYNAKLARI = ("sabah", "ogle", "kapanis", "nabiz")

    def _bugun_gonderilenler(self) -> list[tuple[str, str]]:
        if not self.db:
            return []
        try:
            satir = self.db.query(
                f"""SELECT ts, metin FROM sohbet_kaydi
                    WHERE sahip = ? AND rol = 'assistant'
                      AND date(ts) = date('now')
                      AND kaynak IN ({','.join('?' * len(self.OZET_KAYNAKLARI))})
                      AND metin LIKE '%🧠%'
                    ORDER BY id DESC LIMIT ?""",
                (self.sahip or "ali", *self.OZET_KAYNAKLARI,
                 self.GUNUN_AZAMI_KOSUSU))
        except Exception as e:                            # noqa: BLE001
            log.warning("[panel] bugun gonderilen ozetler okunamadi: %s", e)
            return []
        out = []
        for r in reversed(satir):
            metin = r["metin"] or ""
            panel = metin[metin.index("🧠"):]
            if "🎯" in panel:
                panel = panel[:panel.index("🎯")]
            kirpik = panel.strip()[:self.SADE_KIRPMA]
            if len(panel.strip()) > self.SADE_KIRPMA:
                kirpik += " […kisaltildi]"
            out.append((str(r["ts"])[:16], kirpik))
        return out

    def _gecmis_bolumu(self) -> str:
        """Hakem istemine eklenecek "bugun daha once" blogu."""
        gonderilen = self._bugun_gonderilenler()
        if not gonderilen:
            return ""
        govde = "\n\n".join(f"[{ts} UTC]\n{m}" for ts, m in gonderilen)
        return (
            "\n\n### BUGUN BU KULLANICIYA GONDERILENLER\n"
            f"{govde}\n\n"
            "Bunlar bugun bu kullaniciya GERCEKTEN gonderildi. Ayni seyi "
            "tekrarlama; NE DEGISTI onu soyle. Degisen bir sey yoksa bunu "
            "bir cumlede soyle. Bu metinlerdeki SAYIMLARI (or. 'dort bakistan "
            "dordu') ve yuzdeleri TASIMA: bunlar o kosuya aitti; bu kosunun "
            "sayimi `### BU KOSUNUN SAYIMI` blogunda.")

    @staticmethod
    def _sayim_bolumu(sonuc: dict, gorusler: list) -> str:
        """
        BU KOSUNUN DETERMINISTIK SAYIMI — sembol basina hangi ajan ne dedi.

        Hakem "dort bakisin dordu asagi" gibi sayimlari kendisi yapiyordu ve
        9 Eki'de onceki kosunun sayimini tasidi (INGA: yazilan 4/4, gercek
        2 asagi + 1 notr + 1 gorus yok). Sayim koddan gelir; hakem aktarir.
        """
        ajanlar = list(sonuc)
        if not ajanlar:
            return ""
        tablo: dict = {}
        for g in gorusler:
            sym = str(g.get("sembol") or "").upper()
            if sym and g.get("ajan") in ajanlar:
                tablo.setdefault(sym, {})[g["ajan"]] = g.get("yon") or "?"
        if not tablo:
            return ""
        satirlar = []
        for sym in sorted(tablo):
            yonler = [tablo[sym].get(a, "gorus yok") for a in ajanlar]
            say = {y: yonler.count(y) for y in dict.fromkeys(yonler)}
            satirlar.append(f"{sym}: " + ", ".join(
                f"{y} {n}/{len(ajanlar)}" for y, n in say.items())
                + " — " + ", ".join(f"{a}={y}" for a, y in zip(ajanlar, yonler)))
        return ("\n\n### BU KOSUNUN SAYIMI (bakis sayimini YALNIZ buradan "
                "aktar; kendin sayma)\n" + "\n".join(satirlar))

    # Hakeme verilen olculen seviyeler — `_taktigi_dogrula` okuyor.
    _seviyeler: dict = {}

    def _taktigi_dogrula(self, g: dict, rapor: dict) -> dict:
        """
        Taktik alanlarini (`tur`/`giris`/`stop`) DOGRULAR.

        REDDEDILEN TAKTIK GORUSU DUSURMEZ: yon/guven/tez kismi hala
        degerli. Yalnizca taktik alanlari SILINIR ve red SAYILIR —
        uydurulmus bir seviye, seviyesiz bir gorusten KOTUDUR cunku
        ilki eyleme cagirir.
        """
        from .seviye import dogrula

        if not g.get("tur"):
            return g                       # taktik teklif edilmemis
        olculen = self._seviyeler.get(str(g.get("sembol", "")).upper())
        if not olculen:
            rapor["reddedilen"].append(
                f"{g.get('sembol')}(olculen seviye yok)")
            return {k: v for k, v in g.items()
                    if k not in ("tur", "giris", "stop")}
        ok, sebep = dogrula(g, olculen)
        if not ok:
            rapor["reddedilen"].append(f"{g.get('sembol')}({sebep})")
            return {k: v for k, v in g.items()
                    if k not in ("tur", "giris", "stop")}
        rapor["gecerli"] += 1
        # OLCULEN DEGERE OTURT: model yuvarlamis olabilir (1379,22 ->
        # 1379,2). Defterde ve mesajda OLCULEN sayi durmali ki "bu
        # seviye nereden geldi" sorusunun cevabi tek olsun.
        return {**g, **self._oturt(g, olculen)}

    @staticmethod
    def _oturt(g: dict, olculen: dict) -> dict:
        """Seviyeyi olculen degere oturtur — bkz. `seviye.oturt`."""
        from .seviye import oturt
        out = oturt(g, olculen)
        return out

    async def _hakem(self, sinyaller, sonuc, gorusler,
                     tampon: list | None = None) -> tuple[str, dict]:
        from claude_agent_sdk import ClaudeAgentOptions, query

        bolumler = "\n\n".join(
            f"### {ad.upper()} AJANI\n{metin}" for ad, (metin, _) in sonuc.items())

        # OLCULEN SEVIYELER — hakem bunlardan SECER, hesaplamaz.
        #
        # Kapsam: ajanlarin ve tarayicinin one cikardigi semboller.
        # Katalogun tamamini gondermek hem promptu sisirir hem hakemi
        # hic konusulmamis bir sembole taktik yazmaya davet eder.
        from .seviye import dosya as seviye_dosyasi
        adaylar = {str(g.get("sembol", "")).upper() for g in gorusler
                   if g.get("sembol")}
        adaylar |= {str(s.get("sembol", "")).upper() for s in sinyaller[:12]
                    if s.get("sembol")}
        try:
            seviyeler = seviye_dosyasi(self.db, sorted(a for a in adaylar if a))
        except Exception as e:                        # noqa: BLE001
            # SEVIYE DOSYASI DUSERSE HAKEM YINE KOSAR: taktik uretemez
            # (dogrulama reddeder) ama yorum katmani kaybolmaz.
            log.warning("[panel] seviye dosyasi derlenemedi: %s", e)
            seviyeler = {}
        self._seviyeler = seviyeler

        istem = (f"{bolumler}\n\n### YAPISAL GORUSLER\n```json\n"
                 f"{json.dumps(gorusler, ensure_ascii=False, indent=1)}\n```\n\n"
                 f"### TARAYICI SINYALLERI\n```json\n"
                 f"{json.dumps(sinyaller[:12], ensure_ascii=False, indent=1)}\n```\n\n"
                 f"### OLCULEN SEVIYELER — `giris`/`stop` BUNLARDAN SECILIR\n"
                 f"```json\n"
                 f"{json.dumps(seviyeler, ensure_ascii=False, indent=1)}\n```"
                 f"{self._sayim_bolumu(sonuc, gorusler)}"
                 f"{self._gecmis_bolumu()}")
        opts = ClaudeAgentOptions(**sdk_ortami(), system_prompt=hakem_prompt(), model=self.model,
                                  allowed_tools=[], max_turns=1,
                                  max_buffer_size=16 * 1024 * 1024)
        # TAMPON CAGIRANA AIT — kesilme aninda metin kurtarilsin diye
        # (gerekce ve olculen sayilar `_ajan` icinde).
        parcalar = tampon if tampon is not None else []
        async for m in query(prompt=istem, options=opts):
            ic = getattr(m, "content", None)
            if not ic or isinstance(ic, str):
                continue
            for b in ic:
                if getattr(b, "text", None):
                    parcalar.append(b.text)
        metin = "\n".join(parcalar).strip()
        return metin, _json_cek(metin)
