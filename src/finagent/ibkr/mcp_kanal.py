"""
IBKR BULUT BAGLAYICISI KANALI — kodun IBKR MCP baglayicisini DETERMINISTIK
cagirmasi.

NEDEN BU MODUL VAR
------------------
IBKR'nin resmi Claude baglayicisi (`api.ibkr.com/v1/api/mcp`, 34 arac)
OAuth ile claude.ai hesabinda yetkili. Yerel ag gecidi (CPGW) gerektirmiyor:
gunluk giris yok, 401 dususu yok. Ama yetki claude.ai'da durdugu icin Python
kodu onu DOGRUDAN cagiramaz; yalnizca bir model oturumu cagirabilir.

Bu modul modeli yalnizca TETIKLEYICI olarak kullanir. Modelin ne yazdiginin
onemi yok; uc kural bunu garanti eder:

  1. ARGUMANI KOD BELIRLER. Istenen arac ve argumanlar sabittir; `kapi_karari`
     modelin gonderdigi `tool_input`u beklenenle BIREBIR karsilastirir.
     Farkli arac ya da farkli arguman reddedilir, arac CALISMAZ.
  2. VERI HAM SONUCTAN ALINIR. `PostToolUse` kancasi aracin ham yanitini
     yakalar; modelin metni atilir. Bu deponun "uydurma sayi" dersinin
     (model 335 pencereyi uydurmustu) dogrudan karsiligi.
  3. HATA TURU KORUNUR. Bos yanit ASLA "veri yok" sayilmaz.

OLCULEN TUZAK (2026-09-25, bu modul yazilmadan once)
----------------------------------------------------
Arac `allowed_tools` listesine konunca SDK onu OTOMATIK onayliyor ve
`can_use_tool` HIC cagrilmiyor (`CanUseToolShadowedWarning`). Ilk olcumde
kapi listesi BOS kaldi; arguman kapisi sessizce devre disiydi. Bu yuzden
arac `allowed_tools`a KONMAZ; yalnizca kapidan gecer. Ayni olcumde:
`tool_response` JSON metni (str), 3 tur, 9-12 sn (Haiku 4.5).

`ToolSearch` kapiya ugramaz (yerlesik, her zaman acik) ama yalnizca sema
yukler; hicbir IBKR aracini calistirmaz.

ARAC SECIMI TEK YERDE
---------------------
`SECILEN` plandaki 12 aracin TAM listesi (`docs` degil, burasi tek kaynak).
Joker (`..._IBKR__*`) hicbir yerde kullanilmaz; bir test bunu sinar.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .istemci import (DurumBilinmiyorHatasi, IbkrHatasi, UlasilamadiHatasi,
                      YetkiHatasi)

log = logging.getLogger(__name__)

ONEK = "mcp__claude_ai_Interactive_Brokers_IBKR__"

# TASIMA (2026-10-06, bulut): `claudeai` = yukaridaki model-tetikleyici yol
# (claude.ai baglayicisi; Mac'te bugunku davranis, VARSAYILAN). `dogrudan` =
# `mcp_dogrudan` ile `mcp-public`e kodun KENDISI baglanir: sabit argumanli
# cagri MODELSIZ gider, serbest kip ve sohbet araclari surec ici vekil
# sunucudan (`mcp__ibkr__<arac>`) sunulur. Bulutta (setup-token) claude.ai
# baglayicilari YUKLENMEDIGI icin orada tek yol bu.
# Ortamdan: kurulumun ozelligi (DB_PATH/BOT_STATE_DIR gibi), ayar degil.
ONEK_DOGRUDAN = "mcp__ibkr__"


def tasima() -> str:
    t = (os.getenv("IBKR_MCP_TASIMA") or "claudeai").strip().lower()
    if t not in ("claudeai", "dogrudan"):
        raise ValueError(f"IBKR_MCP_TASIMA={t!r} — 'claudeai' ya da 'dogrudan' olmali")
    return t


def onek() -> str:
    return ONEK_DOGRUDAN if tasima() == "dogrudan" else ONEK

# Arac -> (faz, yazma mi). Yazma araclarinda zaman asimi "istek sunucuya
# ulasmis olabilir" demektir (`DurumBilinmiyorHatasi`, yeniden deneme YASAK);
# okumada ise guvenle yeniden denenebilir (`UlasilamadiHatasi`).
SECILEN: dict[str, tuple[int, bool]] = {
    "get_account_positions": (1, False),
    "get_account_balances": (1, False),
    "get_account_summary": (1, False),
    "get_account_orders": (1, False),
    "create_alert": (2, True),
    "get_alerts": (2, False),
    "update_alert": (2, True),
    "delete_alert": (2, True),
    "get_pa_performance_all_periods": (3, False),
    "get_option_parameters": (4, False),
    "get_option_data": (4, False),
    "get_price_snapshot": (4, False),
    # Faz 5.0 olcumu (docs/tema-olcumu.md) -> Ali B secenegini secti:
    # tema yalnizca portfoye YENI sirket girince cekilir.
    "get_company_themes": (5, False),
    "search_contracts": (5, False),
    # Faz 6 (Ali 28 Eyl: "CPGW dusunce her surec buluttan"): dolum
    # mutabakatinin bulut yedegi. OKUMA.
    "get_account_trades": (6, False),
    # Faz 7 (Ali 3 Eki: "4 arti 3 okuma araci, cok dikkatli"): SOHBETTE
    # arastirma okumalari, `ibkr_bulut_oku` uzerinden. Hepsi OKUMA.
    "get_company_connections": (7, False),
    "search_investment_topics": (7, False),
    "get_theme_details": (7, False),
    "whats_new": (7, False),
    "get_price_history": (7, False),
    "get_pa_allocation": (7, False),
    "get_alert": (7, False),
}

# SOHBETIN `ibkr_bulut_oku` ile cagirabilecegi araclar. Faz 7'nin yedisi +
# IKI BAGIMLILIK (ikisi de zaten SECILEN, okuma):
#   `get_alerts`       — `get_alert` bir alarm KIMLIGI istiyor ve o kimligi
#                        sohbette veren baska bir yol yok.
#   `search_contracts` — sirket verisi ANA listelemeye bagli: ASML'in bizim
#                        kaydimizdaki NASDAQ conid'i (117902840) ile
#                        `get_company_connections` "No data is available"
#                        dondu, AEB conid'i (117589399) ile geldi (olculdu
#                        3 Eki). Ana listelemeyi bulmanin yolu bu.
# YALNIZCA OKUMA — `cagir_async(istek=...)` yazma aracini reddeder, test de
# bu kumenin SECILEN'de ve okuma oldugunu sinar.
# Hesap okumalari (pozisyon/nakit/ozet/emir) BILEREK yok: onlarin kaynagi
# CPGW, bulut yalnizca o dusunce (Faz 1); model iki kaynak arasinda secmez.
#
# Deger: modele giden KISA amac — sohbet aracinin aciklamasi buradan
# URETILIR (ikinci kopya yok). Argumanlari alt oturum gercek semadan kurar.
SOHBET_OKUMA: dict[str, str] = {
    "get_company_connections": "sirketin rakipleri, urunleri, ulke/bolge "
                               "maruziyeti (contract_id ister)",
    "search_investment_topics": "konu/sektor/trend ara ('yapay zeka', "
                                "'semiconductor equipment') -> tema anahtari",
    "get_theme_details": "temanin sirketleri (ONEM sirasi, piyasa degeri "
                         "DEGIL) ve kapsayan ETF/fonlar (tema anahtari ister)",
    "whats_new": "IBKR baglayicisindaki son degisiklikler",
    "get_price_history": "IBKR'nin gecmis OHLCV fiyat cubuklari "
                         "(contract_id ister)",
    "get_pa_allocation": "IBKR hesabinin net varlik dagilimi (varlik "
                         "sinifi, sektor, bolge...)",
    "get_alert": "tek alarmin tam detayi (alarm kimligi ister — once "
                 "get_alerts)",
    "get_alerts": "IBKR'deki tum alarmlar, kimlikleri ve durumlari",
    "search_contracts": "sembol/sirket adindan IBKR kontratlari: borsa, "
                        "ulke, contract_id (ana listelemeyi bulmak icin)",
}

# Faz 1: botun sohbet kanalina, ag gecidi kapaliyken acilacak okuma araclari.
OKUMA_ARACLARI_KISA = tuple(a for a, (faz, yazma) in SECILEN.items()
                            if faz == 1 and not yazma)
# Sohbetin gordugu TAM adlar tasimaya bagli (surec ortami acilista sabit).
OKUMA_ARACLARI = tuple(onek() + a for a in OKUMA_ARACLARI_KISA)

VARSAYILAN_MODEL = "claude-haiku-4-5-20251001"
VARSAYILAN_SURE_SN = 60.0
AZAMI_TUR = 4          # ToolSearch + arac + kapanis; olculen 3


class McpAracCagrilmadi(IbkrHatasi):
    """Model araci hic cagirmadi (ya da kapi reddetti). Yeniden denemek guvenli."""


class McpYanitBicimi(IbkrHatasi):
    """Arac yanit verdi ama beklenen bicimde degil — "veri yok" DEGIL."""


class BaglayiciYokHatasi(YetkiHatasi):
    """
    Baglayici oturumda HIC yok: ToolSearch araci bulamadi. `YetkiHatasi`nin
    alt sinifi, cunku cagiranin tepkisi ayni: kullaniciya "yetki" de.

    IKI AYRI SEBEBI VAR ve cozumleri FARKLI (olculdu 2026-09-25, Faz 0.5):

      1. claude.ai'da baglanti gercekten kesik -> claude.ai'dan yeniden bagla.
      2. Baglanti ACIK ama bu Mac'teki Claude Code, onceki bir kesinti
         sirasinda IBKR'yi `~/.claude/mcp-needs-auth-cache.json`a "yetki
         gerekiyor" diye yazmis; kayit durdukca yeni oturumlar IBKR'ye HIC
         baglanmiyor. `claude mcp list` taze kontrol edip "Connected" der,
         yani iki kaynak celisir. Yeniden baglamak bunu COZMEZ; cozum o tek
         satiri silmek. (Kaydin kendiliginden ne zaman dustugu olculmedi.)

    `onbellek_kaydi` hangisi oldugunu tasir: None -> 1 (ya da dosya
    okunamadi, `onbellek_notu`na bak), sozluk -> 2 olabilir.
    """

    def __init__(self, mesaj: str, onbellek_kaydi: dict | None = None,
                 onbellek_notu: str | None = None):
        super().__init__(mesaj)
        self.onbellek_kaydi = onbellek_kaydi
        self.onbellek_notu = onbellek_notu


# Claude Code'un "yetki gerekiyor" onbellegi ve IBKR'nin oradaki anahtari.
# Anahtar `claude mcp list` ciktisindaki sunucu adiyla ayni (olculdu).
AUTH_ONBELLEGI = Path.home() / ".claude" / "mcp-needs-auth-cache.json"
AUTH_ANAHTARI = "claude.ai Interactive Brokers (IBKR)"


def auth_onbellek_kaydi(yol: Path | None = None) -> tuple[dict | None, str | None]:
    """
    Claude Code'un auth onbelleginde IBKR kaydi var mi? YALNIZCA OKUR.

    Doner: (kayit ya da None, not). Dosya yoksa (None, None) — kayit yok
    demektir. Dosya okunamiyorsa (None, "okunamadi: ...") — bu "kayit
    yok" DEGIL, "bilmiyoruz"; mesaj iki sebebi de soylemeli.

    Dosyaya ASLA yazilmaz: Claude Code'un ic durumu. Silme karari
    kullanicinin (25 Eyl'de Ali onayiyla elle yapildi).
    """
    p = Path(yol or AUTH_ONBELLEGI)
    if not p.exists():
        return None, None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"okunamadi: {type(e).__name__}"
    if not isinstance(d, dict):
        return None, "okunamadi: beklenmeyen bicim"
    kayit = d.get(AUTH_ANAHTARI)
    return (kayit if isinstance(kayit, dict) else None), None


def baglayici_yok_mesaji(arac: str, kayit: dict | None, notu: str | None,
                         simdi_ms: float | None = None) -> str:
    """
    SAF. `BaglayiciYokHatasi` metni — sebebe gore DOGRU cozumu soyler.

    Yanlis cozum pahali: onbellek kaydi varken "yeniden baglayin" demek
    kullaniciyi ise yaramayan bir adima gonderir ve baglayici gorunmez
    kalmaya devam eder (25 Eyl'de tam boyle oldu).
    """
    bas = f"{arac}: IBKR baglayicisi bu oturumda yuklenmedi."
    baglan = ("claude.ai -> Ayarlar -> Baglayicilar -> Interactive Brokers "
              "(IBKR) sayfasindan yeniden baglayin")
    if kayit is not None:
        yas = ""
        ts = kayit.get("timestamp")
        if isinstance(ts, (int, float)):
            dk = ((simdi_ms if simdi_ms is not None else time.time() * 1000)
                  - ts) / 60000
            yas = f" ({dk:.0f} dk once)"
        return (f"{bas} Claude Code bu Mac'te IBKR'yi 'yetki gerekiyor' diye "
                f"isaretlemis{yas} ve bu kayit durdukca baglanmayi DENEMIYOR. "
                "`claude mcp list` IBKR icin 'Connected' diyorsa baglanti "
                f"acik demektir: {AUTH_ONBELLEGI} dosyasindan "
                f"'{AUTH_ANAHTARI}' satirinin silinmesi gerekir (yeniden "
                "baglamak bunu COZMEZ). 'Needs authentication' diyorsa "
                f"{baglan}.")
    if notu:
        return (f"{bas} Iki olasilik var ve hangisi oldugu bilinemedi "
                f"(Claude Code onbellegi {notu}): claude.ai'da baglanti "
                f"kesikse {baglan}; baglanti aciksa {AUTH_ONBELLEGI} "
                f"dosyasindaki '{AUTH_ANAHTARI}' satiri silinmeli.")
    return (f"{bas} claude.ai'da IBKR baglantisi kesilmis ya da yetki geri "
            f"alinmis gorunuyor: {baglan}.")


@dataclass
class McpSonuc:
    arac: str
    argumanlar: dict
    veri: Any
    ham: str
    sure_sn: float
    tur: int | None = None
    reddedilen: list = field(default_factory=list)
    # Faz 0.4: abonelik kotasi/maliyet olcumu ResultMessage'dan.
    maliyet_usd: float | None = None


def tam_ad(arac: str) -> str:
    """Kisa adi SECILEN'e karsi dogrular ve tam MCP adini dondurur."""
    kisa = arac
    for o in (ONEK, ONEK_DOGRUDAN):
        if kisa.startswith(o):
            kisa = kisa[len(o):]
    if kisa not in SECILEN:
        raise ValueError(f"'{kisa}' secilen 12 arac arasinda degil — "
                         "baglayicinin diger araclari bu kanaldan cagrilmaz")
    return ONEK + kisa


def _normal(d: dict | None) -> dict:
    """None degerli anahtarlar dusurulur: model bos alani `null` diye de
    gonderebilir; bu bir arguman DEGISIKLIGI degil."""
    return {k: v for k, v in (d or {}).items() if v is not None}


def kapi_karari(tool_name: str, tool_input: dict, beklenen_ad: str,
                beklenen_arg: dict) -> tuple[bool, str]:
    """
    SAF. Model bu araci bu argumanla mi istiyor?

    Tek izin: beklenen ad VE beklenen arguman, birebir. Sayi tipleri JSON
    karsilastirmasinda ayrilmaz (1 == 1.0) — arac tarafinda da ayni degerdir.
    """
    if tool_name == "ToolSearch":
        return True, "sema yukleme"
    if tool_name != beklenen_ad:
        return False, f"beklenmeyen arac: {tool_name}"
    if _normal(tool_input) != _normal(beklenen_arg):
        return False, (f"arguman farkli: beklenen {_normal(beklenen_arg)}, "
                       f"gelen {_normal(tool_input)}")
    return True, "beklenen arac ve arguman"


def ham_ayristir(tool_response: Any) -> Any:
    """
    SAF. `PostToolUse.tool_response` -> JSON degeri.

    Olculen bicim duz JSON metni. Icerik blogu listesi (`[{"type":"text",
    "text":...}]`) de kabul edilir: SDK surumu degisirse bicim oraya
    kayabilir ve bu "veri yok" diye okunmamali. Bos metin ya da JSON
    olmayan metin `McpYanitBicimi` — sessiz bos DEGIL.
    """
    metin = tool_response
    if isinstance(tool_response, list):
        metin = "".join(b.get("text", "") for b in tool_response
                        if isinstance(b, dict) and b.get("type") == "text")
    if isinstance(tool_response, dict):
        return tool_response
    if not isinstance(metin, str) or not metin.strip():
        raise McpYanitBicimi(f"bos ya da metin olmayan yanit: {repr(tool_response)[:160]}")
    try:
        deger = json.loads(metin)
    except json.JSONDecodeError as e:
        raise McpYanitBicimi(f"JSON degil ({e.msg}): {metin[:160]}") from e
    # ICERIK BLOGU LISTESI METIN OLARAK DA GELEBILIR (bulundu 25 Eyl, Faz 4
    # testi): liste Python nesnesi yerine JSON METNI olarak geldiginde bir
    # kez cozulunce hala `[{"type":"text",...}]` kaliyordu ve asagi akisa
    # SOZLUK yerine LISTE gidiyordu. Bloklar acilir; tek duzey — ic ice
    # sarmalama sonsuz dongu olmasin diye tekrarlanmaz.
    if isinstance(deger, list) and deger and all(
            isinstance(b, dict) and b.get("type") == "text" for b in deger):
        return ham_ayristir(deger)
    return deger


# Yetki dususunun metni HENUZ OLCULMEDI (Faz 0.5). Bu desenler ihtiyatli bir
# baslangic; 0.5 olcumunden sonra gercek metinle guncellenecek ve testi o
# metinle yazilacak.
_YETKI_DESENLERI = ("unauthorized", "401", "403", "authenticat", "authoriz",
                    "token", "sign in", "log in", "login", "reconnect")


def hata_siniflandir(metin: str, yazma: bool) -> type[IbkrHatasi]:
    """SAF. Aracin hata metni -> hata sinifi."""
    m = (metin or "").lower()
    if any(d in m for d in _YETKI_DESENLERI):
        return YetkiHatasi
    if any(d in m for d in ("timeout", "timed out", "zaman asimi")):
        return DurumBilinmiyorHatasi if yazma else UlasilamadiHatasi
    return DurumBilinmiyorHatasi if yazma else UlasilamadiHatasi


def _referans_var(icerik: Any, ad: str) -> bool:
    """SAF. ToolSearch sonucu `ad` aracina referans veriyor mu?"""
    if isinstance(icerik, list):
        return any(isinstance(x, dict) and (
            x.get("tool_name") == ad or ad in str(x.get("text", "")))
            for x in icerik)
    return ad in str(icerik or "")


def istem(ad: str, argumanlar: dict) -> str:
    return (f"Once ToolSearch ile su aracin semasini yukle: select:{ad}\n"
            f"Sonra {ad} aracini TAM OLARAK su argumanlarla BIR KEZ cagir: "
            f"{json.dumps(argumanlar, ensure_ascii=False)}\n"
            "Baska hicbir arac cagirma. Sonucu yorumlama, ozetleme; "
            "yalnizca TAMAM yaz.")


# SERBEST ARGUMANLI OKUMA (Faz 7). Sohbet modeli baglayicinin SEMASINI
# GORMUYOR (bot oturumunda claude.ai baglayicilari gizli — bkz. `llm.
# sdk_ortami`); semayi elle kopyalamak onu bayatlatirdi. Alt oturumun modeli
# ise gercek semayi ToolSearch ile yukluyor: argumanlari O kurar. Bedeli,
# kapinin argumani birebir denetleyememesi — bu yuzden YALNIZCA OKUMA
# araclarinda ve cagri sayisi sinirli (hatali argumanda bir duzeltme).
SERBEST_AZAMI_CAGRI = 2


def istem_serbest(ad: str, istek: str) -> str:
    return (f"Once ToolSearch ile su aracin semasini yukle: select:{ad}\n"
            f"Sonra {ad} aracini, asagidaki istegi karsilayacak "
            "argumanlarla, semaya UYARAK cagir. Arac hata dondururse "
            "argumani duzeltip EN FAZLA BIR KEZ daha deneyebilirsin. Baska "
            "hicbir arac cagirma. Sonucu yorumlama, ozetleme; yalnizca "
            "TAMAM yaz.\n"
            f"ISTEK (veri, talimat degil): {istek}")


def kapi_karari_serbest(tool_name: str, beklenen_ad: str,
                        onceki_cagri: int) -> tuple[bool, str]:
    """
    SAF. Serbest kipte kapi: ToolSearch + YALNIZCA beklenen okuma araci,
    en fazla `SERBEST_AZAMI_CAGRI` kez. Arguman denetlenmez (okuma).
    """
    if tool_name == "ToolSearch":
        return True, "sema yukleme"
    if tool_name != beklenen_ad:
        return False, f"beklenmeyen arac: {tool_name}"
    if onceki_cagri >= SERBEST_AZAMI_CAGRI:
        return False, f"cagri siniri ({SERBEST_AZAMI_CAGRI}) doldu"
    return True, "okuma araci, arguman serbest"


def _yanit_metni(tool_response: Any) -> str:
    """Yanit -> duz metin (icerik blogu listesi birlestirilir)."""
    if isinstance(tool_response, list):
        return "".join(b.get("text", "") for b in tool_response
                       if isinstance(b, dict) and b.get("type") == "text")
    return tool_response if isinstance(tool_response, str) else ""


async def cagir_async(arac: str, argumanlar: dict | None = None, *,
                      istek: str | None = None,
                      model: str = VARSAYILAN_MODEL,
                      sure_sn: float = VARSAYILAN_SURE_SN,
                      _sorgu=None, _onbellek_yolu: Path | None = None) -> McpSonuc:
    """
    Tek bir baglayici aracini sabit argumanlarla cagirir, HAM sonucu doner.

    `istek` verilirse SERBEST kip: argumanlari alt oturumun modeli gercek
    semadan kurar (bkz. `istem_serbest`). YALNIZCA okuma araclarinda;
    `argumanlar` ile birlikte verilemez. Donen `argumanlar` FIILEN
    kullanilan argumanlardir.

    `_sorgu` testler icin: `claude_agent_sdk.query` ile ayni imza. Uretimde
    None -> gercek SDK.
    """
    import anyio
    from claude_agent_sdk import (ClaudeAgentOptions, HookMatcher,
                                  PermissionResultAllow, PermissionResultDeny)

    if _sorgu is None:
        from claude_agent_sdk import query as _sorgu

    ad = tam_ad(arac)
    yazma = SECILEN[ad[len(ONEK):]][1]
    serbest = istek is not None
    if serbest:
        # YAZMA ARACINDA SERBEST ARGUMAN YOK: kapi argumani denetleyemez ve
        # yazmada denetlenmeyen arguman, onaylanmamis bir islem demektir.
        if yazma:
            raise ValueError(f"{arac}: serbest arguman yalnizca OKUMA "
                             "araclarinda")
        if argumanlar:
            raise ValueError(f"{arac}: `istek` ile `argumanlar` birlikte "
                             "verilemez")
        if not str(istek).strip():
            raise ValueError(f"{arac}: istek bos")
    arg = dict(argumanlar or {})
    kisa_ad = ad[len(ONEK):]
    vekil = None
    if tasima() == "dogrudan":
        if not serbest:
            return await _dogrudan_sabit(arac, kisa_ad, arg, yazma, sure_sn)
        # SERBEST KIP, DOGRUDAN: arguman yine alt oturumun modeli kurar,
        # ama arac claude.ai'dan degil surec ici vekilden gelir ve oturum
        # claude.ai baglayicilarini HIC gormez (`sdk_ortami()` varsayilani).
        from . import mcp_dogrudan as D
        vekil = await D.vekil_sunucu_async([kisa_ad])
        ad = ONEK_DOGRUDAN + kisa_ad
    yakalanan: dict = {}
    reddedilen: list = []
    arac_bulundu = {"deger": None}
    cagri = {"n": 0}

    async def _kapi(tool_name, tool_input, context):
        if serbest:
            izin, sebep = kapi_karari_serbest(tool_name, ad, cagri["n"])
            if izin and tool_name == ad:
                cagri["n"] += 1
        else:
            izin, sebep = kapi_karari(tool_name, tool_input, ad, arg)
        if izin:
            return PermissionResultAllow()
        reddedilen.append((tool_name, tool_input, sebep))
        log.warning("[mcp] kapi reddetti: %s", sebep)
        return PermissionResultDeny(message=f"reddedildi: {sebep}")

    async def _basarili(inp, tool_use_id, ctx):
        if inp.get("tool_name") == ad:
            yakalanan["yanit"] = inp.get("tool_response")
            yakalanan["arg"] = inp.get("tool_input")
        return {}

    async def _basarisiz(inp, tool_use_id, ctx):
        if inp.get("tool_name") == ad:
            yakalanan["hata"] = str(inp.get("error") or "")
        return {}

    async def _akis():
        yield {"type": "user",
               "message": {"role": "user", "content": (
                   istem_serbest(ad, str(istek)) if serbest
                   else istem(ad, arg))}}

    opts = ClaudeAgentOptions(
        model=model,
        # BOS: arac burada olsaydi SDK onu otomatik onaylar ve kapi HIC
        # cagrilmazdi (olculdu, modul basligi).
        allowed_tools=[],
        can_use_tool=_kapi,
        # Serbest kipte bir duzeltme denemesi icin BIR tur fazla.
        max_turns=AZAMI_TUR + (1 if serbest else 0),
        hooks={"PostToolUse": [HookMatcher(matcher=ad, hooks=[_basarili])],
               "PostToolUseFailure": [HookMatcher(matcher=ad, hooks=[_basarisiz])]},
        **(_vekil_secenekleri(vekil) if vekil else {}),
    )

    t0 = time.monotonic()
    tur = maliyet = None
    arama_idleri: set = set()
    try:
        with anyio.fail_after(sure_sn):
            async for m in _sorgu(prompt=_akis(), options=opts):
                icerik = getattr(m, "content", None)
                if isinstance(icerik, list):
                    for b in icerik:
                        bt = type(b).__name__
                        if bt == "ToolUseBlock" and getattr(b, "name", None) == "ToolSearch":
                            arama_idleri.add(getattr(b, "id", None))
                        # TOOLSEARCH SONUCU KIMLIGIYLE IZLENIR. Baglayici
                        # oturumda yoksa sonuc bir `tool_reference`
                        # TASIMAZ; yalnizca "baska aracin referansi geldi"
                        # durumuna bakmak bu hali "model cagirmadi" diye
                        # yanlis siniflandiriyordu (test yakaladi).
                        if bt == "ToolResultBlock" and \
                                getattr(b, "tool_use_id", None) in arama_idleri:
                            arac_bulundu["deger"] = _referans_var(
                                getattr(b, "content", None), ad)
                if type(m).__name__ == "ResultMessage":
                    tur = getattr(m, "num_turns", None)
                    maliyet = getattr(m, "total_cost_usd", None)
    except TimeoutError as e:
        sinif = DurumBilinmiyorHatasi if yazma else UlasilamadiHatasi
        raise sinif(f"{arac}: {sure_sn:.0f} sn icinde yanit yok"
                    + (" — istek IBKR'ye ULASMIS OLABILIR, yeniden deneme "
                       "yapilmadan once durum okunmali" if yazma else "")) from e
    sure = round(time.monotonic() - t0, 1)

    if vekil is not None and kisa_ad in vekil["son_hata"]:
        # Vekilin SON cagrisi hata: kanca bunu basari diye de yakalamis
        # olabilir (is_error'lu sonuc). Kaynagi vekilin kendi kaydi —
        # metinden tahmin edilmez. Basarili bir ikinci deneme kaydi siler.
        yakalanan["hata"] = vekil["son_hata"][kisa_ad]
        yakalanan.pop("yanit", None)

    # SERBEST KIPTE ikinci deneme basariliysa ilk denemenin hatasi sonucu
    # BELIRLEMEZ (argumani duzeltme hakki tam bunun icin). Sabit kipte
    # davranis DEGISMEDI: hata onceliklidir.
    if "hata" in yakalanan and not (serbest and "yanit" in yakalanan):
        sinif = hata_siniflandir(yakalanan["hata"], yazma)
        log.warning("[mcp] %s hata (%s, %.1f sn): %s", arac, sinif.__name__,
                    sure, yakalanan["hata"][:200])
        raise sinif(f"{arac}: {yakalanan['hata'][:300]}")
    if "yanit" not in yakalanan:
        if arac_bulundu["deger"] is False and vekil is None:
            kayit, notu = auth_onbellek_kaydi(_onbellek_yolu)
            raise BaglayiciYokHatasi(
                baglayici_yok_mesaji(arac, kayit, notu),
                onbellek_kaydi=kayit, onbellek_notu=notu)
        raise McpAracCagrilmadi(
            f"{arac}: model araci cagirmadi"
            + (f" (kapi {len(reddedilen)} istegi reddetti: {reddedilen[-1][2]})"
               if reddedilen else ""))

    try:
        veri = ham_ayristir(yakalanan["yanit"])
    except McpYanitBicimi:
        # Arastirma araclari JSON yerine DUZ METIN de donebilir; serbest
        # kipte dolu metin VERIDIR. Bos yanit yine hata (yokluk kaniti
        # degil — hafiza: bos-yanit-yokluk-kaniti-degil).
        metin = _yanit_metni(yakalanan["yanit"])
        if not (serbest and metin.strip()):
            raise
        veri = metin
    ham = yakalanan["yanit"] if isinstance(yakalanan["yanit"], str) \
        else json.dumps(yakalanan["yanit"], ensure_ascii=False)
    log.info("[mcp] %s ok (%.1f sn, %s tur)", arac, sure, tur)
    if serbest:
        arg = dict(yakalanan.get("arg") or {})
    return McpSonuc(arac=arac, argumanlar=arg, veri=veri, ham=ham,
                    sure_sn=sure, tur=tur, reddedilen=reddedilen,
                    maliyet_usd=maliyet)


def _vekil_secenekleri(vekil: dict) -> dict:
    from ..llm import sdk_ortami
    return {"mcp_servers": {"ibkr": vekil["sunucu"]}, **sdk_ortami()}


async def _dogrudan_sabit(arac: str, kisa: str, arg: dict, yazma: bool,
                          sure_sn: float) -> McpSonuc:
    """Sabit argumanli cagri, MODELSIZ: arguman kapisi gereksiz — argumani
    zaten kod veriyor ve arada onu degistirebilecek bir model yok."""
    from . import mcp_dogrudan as D
    t0 = time.monotonic()
    bloklar = await D.cagir_async(kisa, arg, yazma=yazma, sure_sn=sure_sn)
    sure = round(time.monotonic() - t0, 1)
    veri = ham_ayristir(bloklar)
    log.info("[mcp] %s ok (dogrudan, %.1f sn)", arac, sure)
    return McpSonuc(arac=arac, argumanlar=arg, veri=veri,
                    ham=json.dumps(bloklar, ensure_ascii=False), sure_sn=sure)


async def arac_varligi_async(*, model: str = VARSAYILAN_MODEL,
                             sure_sn: float = VARSAYILAN_SURE_SN,
                             _sorgu=None) -> dict:
    """
    Secilen 12 arac baglayicida HALA var mi? (Faz 0.6 — sema kaymasi.)

    Hicbir IBKR araci CAGRILMAZ: yalnizca `ToolSearch` `select:` ile 12 tam
    adi sorar. Sonuc kancadan HAM alinir (olculdu: `{"matches": [...],
    "total_deferred_tools": N}`), modelin metni kullanilmaz. Baglayici
    oturumda hic yoksa eslesme BOS doner — ayni kontrol baglanti sagligini
    da olcer.
    """
    if tasima() == "dogrudan" and _sorgu is None:
        # Dogrudan: sunucunun KENDI listesi (model/ToolSearch yok).
        from . import mcp_dogrudan as D
        t0 = time.monotonic()
        var = set(await D.araclar_async())
        return {"bulunan": sorted(ONEK_DOGRUDAN + a for a in SECILEN if a in var),
                "eksik": sorted(ONEK_DOGRUDAN + a for a in SECILEN if a not in var),
                "toplam_ertelenmis": len(var),
                "sure_sn": round(time.monotonic() - t0, 1)}

    import anyio
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

    if _sorgu is None:
        from claude_agent_sdk import query as _sorgu
    adlar = [ONEK + a for a in SECILEN]
    yakalanan: list = []

    async def _kanca(inp, tool_use_id, ctx):
        yakalanan.append(inp.get("tool_response"))
        return {}

    async def _akis():
        yield {"type": "user", "message": {"role": "user", "content": (
            f"ToolSearch aracini TAM OLARAK BIR KEZ su sorguyla cagir: "
            # SINIR LISTEDEN: sabit 20, SECILEN 22'ye cikinca (Faz 7) iki
            # araci her gece "eksik" gosterirdi.
            f"select:{','.join(adlar)} (max_results {len(adlar)}). Baska arac cagirma. "
            "Yalnizca TAMAM yaz.")}}

    opts = ClaudeAgentOptions(
        model=model, allowed_tools=[], max_turns=3,
        hooks={"PostToolUse": [HookMatcher(matcher="ToolSearch", hooks=[_kanca])]})
    t0 = time.monotonic()
    try:
        with anyio.fail_after(sure_sn):
            async for _ in _sorgu(prompt=_akis(), options=opts):
                pass
    except TimeoutError as e:
        raise UlasilamadiHatasi(f"arac varligi: {sure_sn:.0f} sn icinde yanit yok") from e
    if not yakalanan:
        raise McpAracCagrilmadi("arac varligi: model ToolSearch'u cagirmadi")
    ham = yakalanan[-1]
    if not isinstance(ham, dict) or "matches" not in ham:
        raise McpYanitBicimi(f"ToolSearch yaniti beklenen bicimde degil: {repr(ham)[:160]}")
    bulunan = {m if isinstance(m, str) else (m or {}).get("tool_name")
               for m in ham.get("matches") or []}
    return {"bulunan": sorted(bulunan & set(adlar)),
            "eksik": sorted(set(adlar) - bulunan),
            "toplam_ertelenmis": ham.get("total_deferred_tools"),
            "sure_sn": round(time.monotonic() - t0, 1)}


def cagir(arac: str, argumanlar: dict | None = None, **kw) -> McpSonuc:
    """Senkron sarici — zamanli toplayicilar icin. Calisan bir olay dongusu
    icindeysen `cagir_async` kullan."""
    import anyio
    return anyio.run(lambda: cagir_async(arac, argumanlar, **kw))
