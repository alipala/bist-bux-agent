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

import json
import logging
import re
from datetime import datetime, timezone

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

EN ONEMLI KURAL: SADE katman TEKNIK katmandan DAHA KESIN konusamaz.
Teknik katmanda gecmeyen hicbir yon iddiasi, tahmin ya da oneri sade
katmanda gorunemez. Sade katmanin isi TERIMI ACMAK, sonucu
KESKINLESTIRMEK degil. Emin olmadigin bir seyi sadelestirirken emin
hale getirme.

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
   "izlenecek_esik": "izlenecek seviye, serbest metin"}
]}
```

GECERSIZLESME KOSULU GRAMERI — disina cikan kosul KAYDEDILMEZ:
{GRAMER}
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


def hakem_prompt() -> str:
    """
    HAKEM prompt'u URETILIYOR, elle yazilmiyor.

    Kosul grameri `pulse.tez`'de tanimli; prompt'a elle kopyalansaydi
    alan listesi degistiginde ikisi sessizce ayrisirdi — bu projenin
    tekrar eden kusur sinifi (prompt "FX yok" derken `fx` araci vardi).
    """
    from .tez import gramer_metni
    return _HAKEM_SABLON.replace("{GRAMER}", gramer_metni())


def _json_cek(metin: str) -> dict:
    """
    Cevabin sonundaki JSON blogunu ayiklar.

    Model bazen ```json cite icinde, bazen ciplak veriyor; ikisi de
    kabul. Ayristirilamazsa BOS doner — uydurma yerine bos, cunku bu
    veri tahmin defterine girecek ve yanlis kayit puanlamayi bozar.
    """
    for kalip in (r"```json\s*(\{.*?\})\s*```", r"```\s*(\{.*?\})\s*```"):
        m = re.findall(kalip, metin, re.S)
        if m:
            try:
                return json.loads(m[-1])
            except json.JSONDecodeError:
                continue
    m = re.findall(r'\{[^{}]*"gorusler"\s*:\s*\[.*?\]\s*\}', metin, re.S)
    if m:
        try:
            return json.loads(m[-1])
        except json.JSONDecodeError:
            pass
    return {}


class Panel:
    def __init__(self, settings, db, sahip: str | None = None):
        self.s = settings
        self.db = db
        self.sahip = sahip
        self.model = settings.get("analysis.llm.strategist_model", "claude-opus-5")

    # ------------------------------------------------------------------
    async def _ajan(self, ad: str, talimat: str, gundem: str) -> tuple[str, dict]:
        from claude_agent_sdk import (ClaudeAgentOptions, query,
                                      PermissionResultAllow, PermissionResultDeny)
        from ..bot.tools import ToolBox, ARAC_ADLARI

        # YAZMA ARACLARI PANELDE YOK — panel salt-okunur.
        okuma = [a for a in ARAC_ADLARI
                 if not a.endswith(("pozisyon_kaydet", "izlemeye_al", "veri_topla"))]
        tb = ToolBox(self.s, self.db, self.s.root / "data" / "bot" / "pending",
                     sahip=self.sahip)
        izinli = set(okuma)

        async def kapi(tool_name, tool_input, context):
            if tool_name in izinli:
                return PermissionResultAllow()
            log.warning("[panel:%s] arac reddedildi: %s", ad, tool_name)
            return PermissionResultDeny(message=f"'{tool_name}' panelde kapali")

        async def akis():
            yield {"type": "user", "message": {"role": "user", "content": gundem}}

        opts = ClaudeAgentOptions(
            system_prompt=talimat + ORTAK_KURALLAR,
            model=self.model, mcp_servers={"finagent": tb.sunucu()},
            allowed_tools=okuma, can_use_tool=kapi,
            max_turns=int(self.s.get("analysis.llm.panel_max_turns", 16)),
            max_buffer_size=64 * 1024 * 1024,
        )
        parcalar = []
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
    async def calistir(self, sinyaller: list[dict]) -> dict:
        """Dort ajani PARALEL calistirir, sonra hakemi."""
        import anyio

        if not sinyaller:
            return {"ozet": None, "ajanlar": {}, "gorusler": []}

        gundem = ("Tarayici bugun su gozlemleri uretti (deterministik, LLM yok). "
                  "Kendi mercegin uzerinden degerlendir; gerekli veriyi "
                  "ARACLARLA cek.\n\n```json\n"
                  + json.dumps(sinyaller[:12], ensure_ascii=False, indent=1)
                  + "\n```")

        sonuc: dict[str, tuple[str, dict]] = {}

        async def kos(ad, talimat):
            try:
                sonuc[ad] = await self._ajan(ad, talimat, gundem)
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

        self._kosuyu_yaz(sonuc)

        ozet, hakem_veri = await self._hakem(sinyaller, sonuc, gorusler)
        hakem_gorusler = []
        for g in (hakem_veri.get("gorusler") or []):
            if isinstance(g, dict) and g.get("sembol"):
                sid = sinyal_id.get(str(g["sembol"]).upper())
                hakem_gorusler.append({**g, "ajan": "hakem",
                                       "signal_id": sid[0] if sid else None})
        self._kosuyu_yaz({"hakem": (ozet, hakem_veri)})

        sade, teknik = katmanlari_ayir(ozet)
        return {"ozet": teknik, "sade": sade,
                "ajanlar": {k: v[0] for k, v in sonuc.items()},
                "gorusler": gorusler, "hakem_gorusler": hakem_gorusler}

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

    def _kosuyu_yaz(self, sonuc: dict) -> None:
        """
        Her ajanin HAM cevabini `panel_runs`'a yazar.

        Sebebi olculdu: "`_json_cek` simdiye kadar kac turda bos dondu"
        sorusu GERIYE DONUK cevaplanamadi, cunku hicbir iz yoktu. Sayac
        ileriye donuk cozerdi; ham metin saklamak, bugun sormadigimiz
        sorulari da cozer. Gozlemlenebilirlik yoksa hata sinifi gorunmez.
        """
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with self.db.tx() as c:
                c.executemany(
                    """INSERT INTO panel_runs
                       (run_ts, ajan, ham_metin, json_durum, gorus_sayisi,
                        hata, sahip)
                       VALUES (?,?,?,?,?,?,?)""",
                    [(ts, ad, metin,
                      "ajan_hatasi" if metin.startswith("(ajan calismadi")
                      else ("ok" if veri else "bos"),
                      len(veri.get("gorusler") or []),
                      self._not(metin, veri), self.sahip or "ali")
                     for ad, (metin, veri) in sonuc.items()])
        except Exception as e:                        # noqa: BLE001
            # Kayit tutamamak kosuyu DUSURMEMELI: nabzin isi analiz,
            # panel_runs gozlem icin.
            log.warning("[panel] ham cikti yazilamadi: %s", e)

    async def _hakem(self, sinyaller, sonuc, gorusler) -> tuple[str, dict]:
        from claude_agent_sdk import ClaudeAgentOptions, query

        bolumler = "\n\n".join(
            f"### {ad.upper()} AJANI\n{metin}" for ad, (metin, _) in sonuc.items())
        istem = (f"{bolumler}\n\n### YAPISAL GORUSLER\n```json\n"
                 f"{json.dumps(gorusler, ensure_ascii=False, indent=1)}\n```\n\n"
                 f"### TARAYICI SINYALLERI\n```json\n"
                 f"{json.dumps(sinyaller[:12], ensure_ascii=False, indent=1)}\n```")
        opts = ClaudeAgentOptions(system_prompt=hakem_prompt(), model=self.model,
                                  allowed_tools=[], max_turns=1,
                                  max_buffer_size=16 * 1024 * 1024)
        parcalar = []
        async for m in query(prompt=istem, options=opts):
            ic = getattr(m, "content", None)
            if not ic or isinstance(ic, str):
                continue
            for b in ic:
                if getattr(b, "text", None):
                    parcalar.append(b.text)
        metin = "\n".join(parcalar).strip()
        return metin, _json_cek(metin)
