"""
IBKR BULUT BAGLAYICISI SOHBETTE DOGRUDAN — 34 aracin hepsi Telegram'dan.

NEDEN VAR
---------
Ali 2026-10-07: "claude.ai'da IBKR araclarinin hepsini kullaniyorum,
Telegram'dan neden kullanamiyorum?" O aksam bot `get_watchlists`'e uzandi
ve izin kapisi reddetti (agent.log 20:44:41). Engel teknik degildi: claude.ai
her YAZMA cagrisinda kullaniciya "izin ver" sorar, botun boyle bir sorusu
yoktu. SDK'da ortasi yok: arac `allowed_tools`ta ise SORMADAN calisir (olculdu
25 Eyl, bkz. `mcp_kanal` modul basligi), degilse kapi reddeder.

Bu modul o eksik soruyu Telegram butonuyla kurar:

  * OKUMA araclari `allowed_tools`a girer — dogrudan calisir.
  * YAZMA araclari `allowed_tools`a GIRMEZ. Kapi (`can_use_tool`) cagriyi
    CALISTIRMAZ; aracin adini ve modelin gonderdigi argumani ONAY DEPOSUNA
    yazar. Telegram'a deterministik ozet + buton gider. Onayda
    `mcp_kanal.cagir` AYNI argumanla, birebir kapidan (`kapi_karari`) cagirir;
    alt oturum argumani degistirirse arac calismaz.
  * Diger claude.ai baglayicilari (Gmail, Drive...) kapidan GECEMEZ: izinli
    kume yalnizca IBKR adlarindan kurulur.

KIMIN HESABI
------------
IBKR hesabi `ibkr.sahip`in. Baska bir sahibin sohbetinde bu kip ACILMAZ ve
yurutme de sahibi yeniden denetler (onay dosyasi elle tasinsa bile).

BOTUN IZLEME LISTESI ILE IBKR'NIN LISTESI AYRI
----------------------------------------------
`watchlist` tablosu botun veri toplama kapsami (BIST, Binance dahil, sahipsiz);
IBKR listesi Ali'nin IBKR uygulamasindaki listedir. Ikisi senkron EDILMEZ ve
model bunu soylemek zorunda (`model_notu`).
"""
from __future__ import annotations

import html
import json
import logging

from .istemci import DurumBilinmiyorHatasi, IbkrHatasi
from .mcp_kanal import ONEK

log = logging.getLogger(__name__)

TIP = "ibkr_mcp"
AYAR = "ibkr.mcp_dogrudan"


def acik(settings, sahip: str | None) -> bool:
    """
    Bu sohbet turunda dogrudan kip acik mi?

    Uc sart: `ibkr.mcp_dogrudan` ACIKCA true (anahtar yoksa kapali; bool
    degilse hata — "evet" sessizce acik sayilmaz), `ibkr.acik`, ve konusan
    kisi `ibkr.sahip`.
    """
    v = settings.get(AYAR)
    if v is None:
        return False
    if not isinstance(v, bool):
        raise ValueError(f"{AYAR} bool olmali, {v!r} verilmis")
    if not v or not bool(settings.get("ibkr.acik", False)):
        return False
    hesap_sahibi = settings.get("ibkr.sahip")
    return bool(sahip) and sahip == hesap_sahibi


def araclar() -> tuple[tuple[str, ...], frozenset[str]]:
    """(okuma tam adlari, yazma tam adlari). Tek kaynak: `bulut_katalog`."""
    from .bulut_katalog import KATALOG
    okuma = tuple(ONEK + a for a, k in KATALOG.items() if not k["yazma"])
    yazma = frozenset(ONEK + a for a, k in KATALOG.items() if k["yazma"])
    return okuma, yazma


def kisa_ad(tam: str) -> str:
    return tam[len(ONEK):] if tam.startswith(ONEK) else tam


def yazma_karari(tool_name: str, onceki_sunulan: int) -> str | None:
    """
    SAF. Kapiya gelen cagri bir IBKR YAZMA araci mi?

    None    -> bu modulun isi degil (kapinin normal kurali gecerli)
    "sun"   -> onaya sun, CALISTIRMA
    "tekrar"-> bu turda zaten bir yazma onaya sunuldu: reddet. Telegram
               cevabina yalnizca SON istegin butonu eklenir; ikinci istek
               butonsuz kalir ve kullanici neyi onayladigini bilemez.
    """
    if tool_name not in araclar()[1]:
        return None
    return "tekrar" if onceki_sunulan else "sun"


def sunuldu_metni(arac: str) -> str:
    """Modele donen ret metni — calismadigini ve ne diyecegini soyler."""
    return (f"'{arac}' CALISTIRILMADI, kullanicinin ONAYINA SUNULDU. "
            "Telegram'da cevabinin altinda tam argumanlarla bir onay butonu "
            "cikacak; kullanici basarsa IBKR'de AYNEN bu argumanlarla "
            "calisacak. Cevabinda 'yaptim/olusturdum/kurdum' DEME — "
            "'onayina sundum' de ve ne yapilacagini (ad, kontratlar, deger) "
            "kisaca yaz. Ayni turda baska bir yazma araci CAGIRMA.")


def tekrar_metni(arac: str) -> str:
    return (f"'{arac}' CALISTIRILMADI: bu turda bir yazma istegi zaten onaya "
            "sunuldu ve tek turda yalnizca BIRI sunulabilir. Kullaniciya "
            "once onu onaylamasini, sonra bunu tekrar istemesini soyle.")


def _arg_metni(argumanlar: dict) -> str:
    m = json.dumps(argumanlar or {}, ensure_ascii=False, indent=1)
    return m if len(m) <= 1500 else m[:1500] + "\n…(kirpildi)"


def ozet_html(veri: dict) -> str:
    """
    Onay mesajinin DETERMINISTIK kismi. Modelin cevap metni neyi anlatirsa
    anlatsin, butona basilinca calisacak sey BURADA yazar.
    """
    from .bulut_katalog import KATALOG
    arac = veri.get("arac") or "?"
    k = KATALOG.get(arac) or {}
    satir = [f"🔐 <b>IBKR'de calisacak</b>: <code>{html.escape(arac)}</code>"]
    if k.get("ne"):
        satir.append(f"<i>{html.escape(k['ne'])}</i>")
    satir.append(f"<pre>{html.escape(_arg_metni(veri.get('argumanlar') or {}))}</pre>")
    if k.get("not_"):
        satir.append(f"⚠️ {html.escape(k['not_'])}")
    return "\n".join(satir)


def yurut(settings, veri: dict, sahip: str, _cagir=None) -> str:
    """
    Onaylanmis yazma cagrisini YAPAR. Kullaniciya gidecek metni doner.

    Hata ISTISNA OLARAK YUKSELMEZ: dinleyicinin genel hata yolu istegi
    "tekrar dene" butonuyla geri koyar. Yazmada bu CIFT ISLEM demek
    (zaman asimi = istek IBKR'ye ulasmis olabilir). Bu yuzden her sonuc
    metin olarak doner ve istek tuketilir.
    """
    from .mcp_kanal import cagir as _gercek

    arac = veri.get("arac") or ""
    arg = veri.get("argumanlar") or {}
    if ONEK + arac not in araclar()[1]:
        return f"⛔️ <code>{html.escape(arac)}</code> bir IBKR yazma araci degil; calistirilmadi."
    if sahip != settings.get("ibkr.sahip"):
        return "⛔️ Bu IBKR hesabi senin degil; calistirilmadi."
    cagir = _cagir or _gercek
    try:
        s = cagir(arac, arg, genis=True)
    except DurumBilinmiyorHatasi as e:
        log.warning("[ibkr-dogrudan] %s durum bilinmiyor: %s", arac, e)
        return (f"⚠️ <b>{html.escape(arac)}: sonuc BILINMIYOR</b>\n"
                f"<code>{html.escape(str(e)[:300])}</code>\n"
                "<i>Istek IBKR'ye ulasmis olabilir. Tekrar basmadan once "
                "IBKR uygulamasindan ya da bana sorarak kontrol et.</i>")
    except IbkrHatasi as e:
        log.warning("[ibkr-dogrudan] %s hata: %s", arac, e)
        return (f"⛔️ <b>{html.escape(arac)} calismadi</b>\n"
                f"<code>{html.escape(str(e)[:300])}</code>")
    log.info("[ibkr-dogrudan] %s ok (%.1f sn)", arac, s.sure_sn)
    ham = s.ham if len(s.ham) <= 800 else s.ham[:800] + "…"
    return (f"✅ <b>IBKR: {html.escape(arac)} calisti</b>\n"
            f"<pre>{html.escape(ham)}</pre>")


def model_notu() -> str:
    """Bu kip acikken modele giden not (sohbet baglaminin basina)."""
    okuma, yazma = araclar()
    return (
        "### IBKR BULUT ARACLARI DOGRUDAN ACIK\n"
        f"IBKR baglayicisinin {len(okuma) + len(yazma)} aracinin hepsi bu "
        "sohbette kullanilabilir (adlari `mcp__claude_ai_Interactive_Brokers_"
        "IBKR__` ile baslar; semayi ToolSearch `select:` ile yukle). OKUMA "
        "araclari dogrudan calisir. YAZMA araclari ("
        + ", ".join(sorted(kisa_ad(a) for a in yazma))
        + ") CALISMAZ, kullanicinin Telegram onayina sunulur — 'yaptim' deme. "
        "Hesap verisini (pozisyon, nakit, emir) buradan okursan cevapta "
        "'IBKR bulut baglayicisindan' de. IBKR'deki izleme listeleri botun "
        "kendi izleme listesinden (`izleme_listesi`) AYRIDIR, senkron "
        "degildir; kullaniciya hangisinden bahsettigini soyle. Kontrat "
        "kimligi gerekirse once `search_contracts`.\n\n")
