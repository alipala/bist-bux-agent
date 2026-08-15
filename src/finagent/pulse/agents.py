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

HAKEM = """Sen HAKEMSIN. Dort bagimsiz ajanin (teknik, temel, olay, risk)
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

Kullaniciya Telegram'da okunacak KISA bir ozet yaz (en fazla 25 satir):
- once RISK varsa risk
- sonra en guclu 2-3 gozlem, her biri icin: ne gorunuyor, hangi ajan
  ne diyor, celiski var mi, izlenecek esik
- guven duzeyi ve bunu YANLIS cikaracak sey

Al/sat emri, pozisyon buyuklugu, kaldirac ONERME.
BICIM: sade Markdown (**kalin**, `kod`, - madde). ## kullanma.
"""


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
    def __init__(self, settings, db):
        self.s = settings
        self.db = db
        self.model = settings.get("analysis.llm.strategist_model", "claude-opus-5")

    # ------------------------------------------------------------------
    async def _ajan(self, ad: str, talimat: str, gundem: str) -> tuple[str, dict]:
        from claude_agent_sdk import (ClaudeAgentOptions, query,
                                      PermissionResultAllow, PermissionResultDeny)
        from ..bot.tools import ToolBox, ARAC_ADLARI

        # YAZMA ARACLARI PANELDE YOK — panel salt-okunur.
        okuma = [a for a in ARAC_ADLARI
                 if not a.endswith(("pozisyon_kaydet", "izlemeye_al", "veri_topla"))]
        tb = ToolBox(self.s, self.db, self.s.root / "data" / "bot" / "pending")
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

        gorusler = []
        for ad, (_, veri) in sonuc.items():
            for g in (veri.get("gorusler") or []):
                if isinstance(g, dict) and g.get("sembol"):
                    gorusler.append({**g, "ajan": ad})

        ozet = await self._hakem(sinyaller, sonuc, gorusler)
        return {"ozet": ozet, "ajanlar": {k: v[0] for k, v in sonuc.items()},
                "gorusler": gorusler}

    async def _hakem(self, sinyaller, sonuc, gorusler) -> str:
        from claude_agent_sdk import ClaudeAgentOptions, query

        bolumler = "\n\n".join(
            f"### {ad.upper()} AJANI\n{metin}" for ad, (metin, _) in sonuc.items())
        istem = (f"{bolumler}\n\n### YAPISAL GORUSLER\n```json\n"
                 f"{json.dumps(gorusler, ensure_ascii=False, indent=1)}\n```\n\n"
                 f"### TARAYICI SINYALLERI\n```json\n"
                 f"{json.dumps(sinyaller[:12], ensure_ascii=False, indent=1)}\n```")
        opts = ClaudeAgentOptions(system_prompt=HAKEM, model=self.model,
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
        return "\n".join(parcalar).strip()
