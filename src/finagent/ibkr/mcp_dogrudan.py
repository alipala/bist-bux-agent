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

UC ASAMALI YAZMA (Ali 7 Eki: "rock solid olsun")
------------------------------------------------
IBKR'nin uc yazma araci TAM DEGISTIRME yapiyor (olculdu, arac semalari):
`edit_watchlist` gonderilmeyen kagidi listeden SILER, `update_alert`
gonderilmeyen alani (e-posta!) temizler. Ali'nin "Favorites" listesinde 17
kagit var (olculdu 7 Eki); "AMD ekle" diyen bir model yalnizca ["AMD"]
gonderirse 16'si gider. Bu yuzden her yazma:

  1. ONIZLEME (onaydan once, kapida): mevcut durum IBKR'den OKUNUR, fark
     (eklenen / CIKAN / temizlenecek alan) onay mesajina yazilir. Okunamazsa
     ve arac tam degistirme/silme ise istek onaya SUNULMAZ — kullanici neyi
     onayladigini bilemez.
  2. ON KOSUL (onayda, yazmadan hemen once): ayni durum YENIDEN okunur;
     onizlemeden beri degistiyse yazma YAPILMAZ.
  3. DOGRULAMA (yazmadan sonra): sonuc IBKR'den geri okunur ve istenenle
     karsilastirilir. Dogrulanamayan yazma "tamam" diye SUNULMAZ.

OLCULEN YANIT BICIMLERI (7 Eki, Claude Code'dan dogrudan okuma)
---------------------------------------------------------------
get_watchlists  {"watchlists": [{"id": "10", "name", "hash": 1790325502740}]}
get_watchlist   {"name", "hash", "instruments": [{"contract_id_ex",
                 "contract_description"}]}
get_alerts      {"alerts": [{"id", "name", "condition": {...,
                 "operator": "lte"}, "status": "ACTIVE"}]}
get_alert       {"id", "name", "status", "tif", "email", "email_note",
                 "active_hours", "condition": {"contract_id", "exchange",
                 "condition_type", "operator", "value"}}
get_order_instructions {"instructions": []}  (dolu satir OLCULMEDI)
Yazma araclarinin YANITLARI olculmedi: dogrulama yanita guvenmez, geri okur.

KIMIN HESABI
------------
IBKR hesabi `ibkr.sahip`in. Baska bir sahibin sohbetinde bu kip ACILMAZ ve
yurutme de sahibi yeniden denetler.

BOTUN IZLEME LISTESI ILE IBKR'NIN LISTESI AYRI
----------------------------------------------
`watchlist` tablosu botun veri toplama kapsami (BIST, Binance dahil, sahipsiz);
IBKR listesi Ali'nin IBKR uygulamasindaki listedir. Ikisi senkron EDILMEZ.
"""
from __future__ import annotations

import html
import json
import logging
import re
from typing import Any, Awaitable, Callable

from .istemci import DurumBilinmiyorHatasi, IbkrHatasi
from .mcp_kanal import ONEK, ONEK_DOGRUDAN, onek

log = logging.getLogger(__name__)

TIP = "ibkr_mcp"
AYAR = "ibkr.mcp_dogrudan"
ONIZLEME_SURE_SN = 90.0
LISTE_SINIRI = 40          # mesajda gosterilen en fazla kagit

Okuyucu = Callable[[str, dict], Awaitable[Any]]


class OnizlemeReddi(Exception):
    """Istek onaya SUNULMAZ (kullanici neyi onayladigini bilemezdi)."""


# ---------------------------------------------------------------------------
# kip ve arac kumesi
# ---------------------------------------------------------------------------

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
    """
    (okuma tam adlari, yazma tam adlari). Tek kaynak: `bulut_katalog`.
    ONEK TASIMAYA BAGLI (9 Eki, bulut birlesimi): Mac'te claude.ai
    baglayicisi (`mcp__claude_ai_...`), bulutta surec ici vekil
    (`mcp__ibkr__`). Ozellik ayni, araclarin geldigi kapi farkli.
    """
    from .bulut_katalog import KATALOG
    o = onek()
    okuma = tuple(o + a for a, k in KATALOG.items() if not k["yazma"])
    yazma = frozenset(o + a for a, k in KATALOG.items() if k["yazma"])
    return okuma, yazma


def kisa_ad(tam: str) -> str:
    for o in (ONEK, ONEK_DOGRUDAN):
        if tam.startswith(o):
            return tam[len(o):]
    return tam


def yazma_mi(tool_name: str) -> bool:
    return tool_name in araclar()[1]


# ---------------------------------------------------------------------------
# yardimcilar
# ---------------------------------------------------------------------------

def _conid(x) -> str | None:
    """'4815747' / '1039246@FTA' / 4815747 -> '4815747'."""
    m = re.match(r"\s*(\d+)", str(x if x is not None else ""))
    return m.group(1) if m else None


def kayitli_adlar(db, kimlikler) -> dict[str, str]:
    """Kontrat kimligi -> 'SEMBOL' (bizim kimlik kaydimizdan). Ag yok."""
    conid = {k: _conid(k) for k in kimlikler}
    degerler = sorted({c for c in conid.values() if c})
    if db is None or not degerler:
        return {}
    try:
        satir = db.query(
            "SELECT d.conid, i.symbol FROM identities d JOIN instruments i "
            "ON i.id = d.instrument_id WHERE CAST(d.conid AS TEXT) IN (%s)"
            % ",".join("?" * len(degerler)), tuple(degerler))
    except Exception as e:                                # noqa: BLE001
        log.warning("[ibkr-dogrudan] kimlik adlari okunamadi: %s", e)
        return {}
    ad = {str(r["conid"]): r["symbol"] for r in satir}
    return {k: ad[c] for k, c in conid.items() if c in ad}


def _bot_alarmlari(db, kimlikler) -> dict[str, str]:
    """Botun `/alarm` ile kurdugu alarmlar: alert_id -> ad."""
    kimlikler = [str(k) for k in kimlikler or []]
    if db is None or not kimlikler:
        return {}
    try:
        r = db.query("SELECT alert_id, ad FROM ibkr_alarm WHERE alert_id IN (%s)"
                     % ",".join("?" * len(kimlikler)), tuple(kimlikler))
    except Exception as e:                                # noqa: BLE001
        log.warning("[ibkr-dogrudan] bot alarmlari okunamadi: %s", e)
        return {}
    return {str(x["alert_id"]): x["ad"] for x in r}


def _kisalt(ogeler: list[str]) -> str:
    if len(ogeler) <= LISTE_SINIRI:
        return ", ".join(ogeler)
    return ", ".join(ogeler[:LISTE_SINIRI]) + f" … (+{len(ogeler) - LISTE_SINIRI})"


def _liste(v: Any, anahtar: str) -> list:
    """`{"watchlists": [...]}` -> liste. Bicim farkliysa HATA (yok degil)."""
    if isinstance(v, dict) and isinstance(v.get(anahtar), list):
        return v[anahtar]
    if isinstance(v, list):
        return v
    raise IbkrHatasi(f"beklenmeyen yanit bicimi ({anahtar}): {str(v)[:160]}")


def _wl_kagitlari(wl: dict) -> list[dict]:
    if not isinstance(wl, dict) or not isinstance(wl.get("instruments"), list):
        raise IbkrHatasi(f"beklenmeyen izleme listesi bicimi: {str(wl)[:160]}")
    return [x for x in wl["instruments"] if isinstance(x, dict)]


def _kosul_metni(c: dict) -> str:
    """{'condition_type','operator','value'} -> 'LAST <= 159.26'."""
    op = {"lte": "<=", "gte": ">="}.get(str(c.get("operator", "")).lower(),
                                         str(c.get("operator", "?")))
    return f"{c.get('condition_type', '?')} {op} {c.get('value', '?')}"


def _kagit_etiketi(kimlik: str, ad: str | None) -> str:
    return f"{ad} ({kimlik})" if ad else f"{kimlik} [ADI KAYITTA YOK]"


# ---------------------------------------------------------------------------
# 1+2: durum okuma (onizleme ve on kosul AYNI fonksiyonu kullanir)
# ---------------------------------------------------------------------------

async def durum_oku(arac: str, arg: dict, oku: Okuyucu) -> Any:
    """
    Yazmanin dayandigi MEVCUT durum. Onizleme bunu gosterir, on kosul
    bunu yeniden okuyup karsilastirir — ikisi ayni fonksiyondan, ki
    "neyi onayladim" ile "neye karsi kontrol ettim" ayrismasin.
    None = bu arac mevcut bir duruma dayanmiyor (olusturma, geri bildirim).
    """
    if arac in ("edit_watchlist", "delete_watchlist"):
        wl = await oku("get_watchlist", {"id": str(arg.get("id"))})
        k = _wl_kagitlari(wl)
        return {"ad": wl.get("name"), "hash": wl.get("hash"),
                "kagitlar": [[x.get("contract_id_ex"), x.get("contract_description")]
                             for x in k]}
    if arac == "update_alert":
        a = await oku("get_alert", {"id": str(arg.get("id"))})
        if not isinstance(a, dict) or not a.get("id"):
            raise IbkrHatasi(f"alarm okunamadi: {str(a)[:160]}")
        return a
    if arac in ("delete_alert", "set_alert_status"):
        tum = _liste(await oku("get_alerts", {}), "alerts")
        istenen = [str(i) for i in arg.get("ids") or []]
        return {str(a.get("id")): {"ad": a.get("name"), "durum": a.get("status")}
                for a in tum if isinstance(a, dict) and str(a.get("id")) in istenen}
    if arac == "delete_order_instruction":
        tum = _liste(await oku("get_order_instructions", {}), "instructions")
        i = str(arg.get("id"))
        return next((x for x in tum if isinstance(x, dict) and str(x.get("id")) == i),
                    None) or {}
    return None


# ---------------------------------------------------------------------------
# 1: onizleme
# ---------------------------------------------------------------------------

async def onizle(arac: str, arg: dict, oku: Okuyucu, db=None) -> dict:
    """
    Onay mesajina girecek fark. Doner:
      {"satirlar": [...], "uyarilar": [...], "durum": <durum_oku sonucu>,
       "modele": "<modele kisa ozet>"}
    Sunulamayacaksa `OnizlemeReddi`. Metinler DUZ METIN; kacis gosterimde.
    """
    arg = arg or {}
    satir: list[str] = []
    uyari: list[str] = []
    modele = ""

    def oku_ya_da_reddet():
        return durum_oku(arac, arg, oku)

    if arac == "create_watchlist":
        yeni = [str(x) for x in arg.get("instruments") or []]
        ad = kayitli_adlar(db, yeni)
        satir.append(f"Yeni liste: \"{arg.get('name', '')}\" — {len(yeni)} kagit")
        satir.append("➕ " + _kisalt([_kagit_etiketi(k, ad.get(k)) for k in yeni]))
        if not str(arg.get("name") or "").strip():
            raise OnizlemeReddi("liste adi bos")
        try:
            listeler = _liste(await oku("get_watchlists", {}), "watchlists")
            ayni = [x for x in listeler if isinstance(x, dict)
                    and str(x.get("name", "")).strip().lower()
                    == str(arg.get("name", "")).strip().lower()]
            if ayni:
                uyari.append(f"Bu adda bir liste ZATEN VAR (id {ayni[0].get('id')}). "
                             "Onaylarsan AYNI ADLI IKINCI bir liste olusur; mevcut "
                             "listeye eklemek istiyorsan Iptal et ve 'listeme ekle' de.")
                modele = (f"DIKKAT: '{arg.get('name')}' adinda liste zaten var "
                          f"(id {ayni[0].get('id')}). Kullanici eklemek istediyse "
                          "edit_watchlist kullan (get_watchlist -> tam liste).")
        except IbkrHatasi as e:
            uyari.append(f"Mevcut listeler okunamadi, ayni ad kontrolu YAPILAMADI ({e}).")
        bilinmeyen = [k for k in yeni if k not in ad]
        if bilinmeyen:
            uyari.append(f"{len(bilinmeyen)} kimligin adi bizim kayitta yok; "
                         "hangi kagit oldugu islemden sonra IBKR'den okunup gosterilecek.")
        return {"satirlar": satir, "uyarilar": uyari, "durum": None, "modele": modele}

    if arac == "edit_watchlist":
        try:
            d = await oku_ya_da_reddet()
        except IbkrHatasi as e:
            raise OnizlemeReddi(f"mevcut liste okunamadi ({e}); tam degistirme "
                                "oldugu icin neyin silinecegi gosterilemez") from e
        eski = {str(k): ac for k, ac in d["kagitlar"]}
        yeni = [str(x) for x in arg.get("instruments") or []]
        eklenen = [k for k in yeni if k not in eski]
        cikan = [k for k in eski if k not in set(yeni)]
        ad = kayitli_adlar(db, eklenen)
        satir.append(f"Liste: \"{d['ad']}\" (id {arg.get('id')}) — simdi "
                     f"{len(eski)} kagit, sonra {len(set(yeni))} kagit")
        if str(arg.get("name") or "") != str(d["ad"] or ""):
            satir.append(f"✏️ Ad degisiyor: \"{d['ad']}\" -> \"{arg.get('name')}\"")
        satir.append("➕ Eklenen: " + (_kisalt([_kagit_etiketi(k, ad.get(k))
                                                for k in eklenen]) or "yok"))
        satir.append("➖ CIKAN: " + (_kisalt([f"{eski[k] or '?'} ({k})" for k in cikan])
                                     or "yok"))
        if cikan:
            uyari.append(f"{len(cikan)} kagit listeden SILINECEK. Yalnizca eklemek "
                         "istediysen Iptal'e bas.")
        modele = (f"Bu duzenleme: {len(eklenen)} eklenen, {len(cikan)} CIKAN"
                  + (f" ({_kisalt([eski[k] or k for k in cikan])})" if cikan else "")
                  + ". edit_watchlist TAM DEGISTIRME: kullanici yalnizca eklemek "
                    "istediyse bu YANLIS — mevcut kagitlari AYNEN koruyarak "
                    "edit_watchlist'i TEKRAR cagir (yeni cagri bunun YERINE gecer)."
                  if cikan else f"Bu duzenleme: {len(eklenen)} eklenen, 0 cikan.")
        return {"satirlar": satir, "uyarilar": uyari, "durum": d, "modele": modele}

    if arac == "delete_watchlist":
        try:
            d = await oku_ya_da_reddet()
        except IbkrHatasi as e:
            raise OnizlemeReddi(f"silinecek liste okunamadi ({e})") from e
        satir.append(f"SILINECEK liste: \"{d['ad']}\" (id {arg.get('id')}), "
                     f"{len(d['kagitlar'])} kagit")
        satir.append("İçindekiler: " + (_kisalt([ac or k for k, ac in d["kagitlar"]]) or "bos"))
        uyari.append("Liste kalici olarak silinir, GERI ALINAMAZ.")
        modele = f"Silinecek liste '{d['ad']}', {len(d['kagitlar'])} kagit."
        return {"satirlar": satir, "uyarilar": uyari, "durum": d, "modele": modele}

    if arac in ("create_alert", "update_alert"):
        eski = None
        if arac == "update_alert":
            try:
                eski = await oku_ya_da_reddet()
            except IbkrHatasi as e:
                raise OnizlemeReddi(f"mevcut alarm okunamadi ({e}); tam degistirme "
                                    "oldugu icin neyin temizlenecegi gosterilemez") from e
        cid = arg.get("contract_id")
        kontrat = ("hesap geneli" if cid is None
                   else _kagit_etiketi(str(cid), kayitli_adlar(db, [str(cid)]).get(str(cid))))
        yeni_kosul = _kosul_metni(arg)
        if eski is not None:
            satir.append(f"Alarm: \"{eski.get('name')}\" (id {arg.get('id')})")
            satir.append(f"Kosul: {_kosul_metni(eski.get('condition') or {})} -> {yeni_kosul}")
            bos = [a for a in ("email", "email_note", "tif", "expiry_date",
                               "active_hours")
                   if eski.get(a) and not arg.get(a)]
            if (eski.get("condition") or {}).get("exchange") and not arg.get("exchange") \
                    and cid is not None:
                bos.append("exchange")
            if bos:
                uyari.append("Gonderilmeyen alanlar TEMIZLENECEK: " + ", ".join(bos)
                             + (" (e-posta gidince alarm yalniz IBKR Desktop'ta "
                                "bildirir)" if "email" in bos else ""))
                modele = ("update_alert TAM DEGISTIRME: su alanlar gonderilmedigi icin "
                          "TEMIZLENECEK: " + ", ".join(bos) + ". Korumak istiyorsan "
                          "get_alert'teki degerlerle TEKRAR cagir (yeni cagri bunun "
                          "YERINE gecer).")
            bot = _bot_alarmlari(db, [arg.get("id")])
            if bot:
                uyari.append("Bu alarmi bot kurdu (/alarm). Degistirirsen bot kendi "
                             "kaydindaki seviyeyi bilmeye devam eder; /alarm yenile ile "
                             "uzlastir.")
        else:
            satir.append(f"Yeni alarm: \"{arg.get('symbol', '')}\" — {kontrat}")
            satir.append(f"Kosul: {yeni_kosul}" + (f", borsa {arg['exchange']}"
                                                   if arg.get("exchange") else ""))
            satir.append(f"Sure: {arg.get('tif') or 'UNTIL_TRIGGERED (varsayilan)'}"
                         + (f" {arg['expiry_date']}" if arg.get("expiry_date") else ""))
            uyari.append("IBKR'ye gore bu araçla kurulan alarm YALNIZCA IBKR Desktop'ta "
                         "gorunur (mobil/TWS/Client Portal'da degil).")
            if not arg.get("email"):
                uyari.append("E-posta YOK: alarm tetiklenince yalniz IBKR Desktop'ta "
                             "bildirim olur (push/SMS/e-posta yok).")
                modele = ("create_alert e-postasiz: tetiklenince yalniz IBKR Desktop'ta "
                          "bildirir. Kullaniciya bunu soyle.")
        if arg.get("condition_type") in ("DAILY_PNL", "MARGIN_CUSHION") and cid is not None:
            uyari.append("Hesap geneli kosulda kontrat kullanilmaz.")
        if str(arg.get("tif") or "") == "UNTIL_DATE" and not arg.get("expiry_date"):
            raise OnizlemeReddi("tif UNTIL_DATE ama expiry_date yok")
        return {"satirlar": satir, "uyarilar": uyari, "durum": eski, "modele": modele}

    if arac in ("delete_alert", "set_alert_status"):
        try:
            d = await oku_ya_da_reddet()
        except IbkrHatasi as e:
            raise OnizlemeReddi(f"alarmlar okunamadi ({e})") from e
        istenen = [str(i) for i in arg.get("ids") or []]
        if not istenen:
            raise OnizlemeReddi("alarm kimligi yok")
        yok = [i for i in istenen if i not in d]
        if yok:
            raise OnizlemeReddi(f"IBKR'de olmayan alarm kimligi: {', '.join(yok)} "
                                "(once get_alerts)")
        if arac == "delete_alert":
            satir.append("SILINECEK alarmlar: "
                         + _kisalt([f"{d[i]['ad']} [{d[i]['durum']}]" for i in istenen]))
            uyari.append("Alarm kalici olarak silinir, GERI ALINAMAZ.")
        else:
            eylem = str(arg.get("action") or "")
            if eylem not in ("PAUSE", "RESUME"):
                raise OnizlemeReddi(f"gecersiz action: {eylem!r}")
            satir.append(("DURAKLATILACAK" if eylem == "PAUSE" else "YENIDEN BASLATILACAK")
                         + ": " + _kisalt([f"{d[i]['ad']} [{d[i]['durum']}]"
                                           for i in istenen]))
        bot = _bot_alarmlari(db, istenen)
        if bot:
            uyari.append("Bot kurdu: " + ", ".join(bot.values()) + ". "
                         + ("Bot duraklatilmis alarmi AKTIF sayar; duraklatilan alarm "
                            "seni korumaz." if arac == "set_alert_status" else
                            "Silinirse bot onu 'kayip' sayar ve kendiliginden KURMAZ."))
        return {"satirlar": satir, "uyarilar": uyari, "durum": d, "modele": modele}

    if arac == "create_order_instruction":
        kimlik = arg.get("contract_id_ex") or arg.get("contract_id")
        if not kimlik:
            raise OnizlemeReddi("kontrat kimligi yok (contract_id_ex)")
        ad = kayitli_adlar(db, [str(kimlik)]).get(str(kimlik))
        tur = arg.get("order_type") or "?"
        if tur == "LIMIT" and arg.get("limit_price") is None:
            raise OnizlemeReddi("LIMIT emirde limit_price yok")
        satir.append(f"Emir TALIMATI: {arg.get('side', '?')} {arg.get('quantity', '?')} x "
                     f"{_kagit_etiketi(str(kimlik), ad)}")
        satir.append(f"{tur}" + (f" @ {arg['limit_price']}" if arg.get("limit_price")
                                 is not None else "")
                     + f", sure {arg.get('time_in_force') or 'IBKR varsayilani'}")
        uyari.append("CANLI EMIR DEGIL: talimat IBKR uygulamasinda durur, SEN gonderince "
                     "emre donusur.")
        return {"satirlar": satir, "uyarilar": uyari, "durum": None, "modele": modele}

    if arac == "delete_order_instruction":
        try:
            d = await oku_ya_da_reddet()
        except IbkrHatasi as e:
            raise OnizlemeReddi(f"talimatlar okunamadi ({e})") from e
        if not d:
            raise OnizlemeReddi(f"'{arg.get('id')}' kimlikli talimat IBKR'de yok "
                                "(once get_order_instructions)")
        satir.append("SILINECEK talimat: " + str(d.get("description") or d)[:300])
        return {"satirlar": satir, "uyarilar": uyari, "durum": d, "modele": modele}

    if arac == "provide_customer_feedback":
        metin = str(arg.get("feedback_text") or "")
        if not metin.strip():
            raise OnizlemeReddi("geri bildirim metni bos")
        satir.append("IBKR'ye SENIN ADINA gonderilecek metin:")
        satir.append(metin[:1500])
        return {"satirlar": satir, "uyarilar": uyari, "durum": None, "modele": modele}

    raise OnizlemeReddi(f"'{arac}' icin onizleme tanimli degil")


# ---------------------------------------------------------------------------
# 3: dogrulama
# ---------------------------------------------------------------------------

async def dogrula(arac: str, arg: dict, yanit: Any, oku: Okuyucu
                  ) -> tuple[bool | None, list[str]]:
    """
    Yazmadan SONRA IBKR'den geri okur. (True, satirlar) dogrulandi,
    (False, satirlar) istenenle UYUSMUYOR, (None, satirlar) dogrulanamadi.
    """
    if arac in ("create_watchlist", "edit_watchlist"):
        wid = (str(arg.get("id")) if arac == "edit_watchlist"
               else str((yanit or {}).get("id") or "") if isinstance(yanit, dict) else "")
        if not wid:
            listeler = _liste(await oku("get_watchlists", {}), "watchlists")
            es = [x for x in listeler if isinstance(x, dict)
                  and x.get("name") == arg.get("name")]
            if len(es) != 1:
                return None, [f"Yanitta liste kimligi yok ve '{arg.get('name')}' "
                              f"adinda {len(es)} liste var — icerik dogrulanamadi."]
            wid = str(es[0].get("id"))
        wl = await oku("get_watchlist", {"id": wid})
        k = _wl_kagitlari(wl)
        simdi = {str(x.get("contract_id_ex")) for x in k}
        istenen = {str(x) for x in arg.get("instruments") or []}
        satir = [f"Liste \"{wl.get('name')}\" (id {wid}) simdi {len(k)} kagit: "
                 + (_kisalt([str(x.get("contract_description") or x.get("contract_id_ex"))
                             for x in k]) or "bos")]
        if simdi != istenen or wl.get("name") != arg.get("name"):
            eksik, fazla = istenen - simdi, simdi - istenen
            satir.append(f"UYUSMUYOR: eksik {sorted(eksik) or '-'}, fazla "
                         f"{sorted(fazla) or '-'}, ad {wl.get('name')!r}")
            return False, satir
        return True, satir

    if arac == "delete_watchlist":
        listeler = _liste(await oku("get_watchlists", {}), "watchlists")
        if any(str(x.get("id")) == str(arg.get("id")) for x in listeler
               if isinstance(x, dict)):
            return False, ["Liste IBKR'de HALA duruyor."]
        return True, [f"Liste artik yok; kalan listeler: "
                      + (", ".join(str(x.get("name")) for x in listeler
                                   if isinstance(x, dict)) or "hic")]

    if arac in ("create_alert", "update_alert"):
        aid = (str(arg.get("id")) if arac == "update_alert"
               else str((yanit or {}).get("id") or "") if isinstance(yanit, dict) else "")
        if not aid:
            return None, ["Yanitta alarm kimligi yok — dogrulanamadi."]
        a = await oku("get_alert", {"id": aid})
        c = (a or {}).get("condition") or {}
        satir = [f"Alarm \"{a.get('name')}\" (id {aid}) [{a.get('status')}]: "
                 f"{_kosul_metni(c)}"
                 + (f", e-posta {a['email']}" if a.get("email") else ", e-posta YOK")]
        tutar = (str(c.get("condition_type")) == str(arg.get("condition_type"))
                 and str(c.get("operator", "")).lower() == str(arg.get("operator", "")).lower()
                 and _sayi_esit(c.get("value"), arg.get("value")))
        if not tutar:
            satir.append(f"UYUSMUYOR: istenen {_kosul_metni(arg)}")
            return False, satir
        return True, satir

    if arac in ("delete_alert", "set_alert_status"):
        tum = {str(a.get("id")): a for a in _liste(await oku("get_alerts", {}), "alerts")
               if isinstance(a, dict)}
        istenen = [str(i) for i in arg.get("ids") or []]
        if arac == "delete_alert":
            kalan = [i for i in istenen if i in tum]
            if kalan:
                return False, [f"HALA duran alarm: {', '.join(kalan)}"]
            return True, [f"{len(istenen)} alarm silindi; kalan {len(tum)} alarm."]
        hedef = "PAUSED" if arg.get("action") == "PAUSE" else "ACTIVE"
        satir = [f"{tum[i].get('name')}: {tum[i].get('status')}" for i in istenen if i in tum]
        if any(str(tum.get(i, {}).get("status")) != hedef for i in istenen):
            return False, satir + [f"UYUSMUYOR: beklenen {hedef}"]
        return True, satir

    if arac == "delete_order_instruction":
        tum = _liste(await oku("get_order_instructions", {}), "instructions")
        if any(str(x.get("id")) == str(arg.get("id")) for x in tum if isinstance(x, dict)):
            return False, ["Talimat IBKR'de HALA duruyor."]
        return True, [f"Talimat silindi; kalan {len(tum)} talimat."]

    if arac == "create_order_instruction":
        url = yanit.get("url") if isinstance(yanit, dict) else None
        return None, ([f"IBKR'de incele ve gonder: {url}"] if url else
                      ["Yanitta baglanti yok; IBKR uygulamasinda talimatlara bak."])

    return None, []


def _sayi_esit(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return a == b


# ---------------------------------------------------------------------------
# kapi + mesajlar
# ---------------------------------------------------------------------------

def sunuldu_metni(arac: str, onizleme: dict | None = None,
                  degisti: bool = False) -> str:
    """Modele donen ret metni — calismadigini ve ne diyecegini soyler."""
    return ((f"(Bu turda onaya sunulan ONCEKI yazma istegi GERI CEKILDI; yerine "
             f"bu gecti.) " if degisti else "")
            + f"'{arac}' CALISTIRILMADI, kullanicinin ONAYINA SUNULDU. "
            "Telegram'da cevabinin altinda farki gosteren bir onay mesaji ve "
            "buton cikacak; kullanici basarsa IBKR'de AYNEN bu argumanlarla "
            "calisacak. Cevabinda 'yaptim/olusturdum/ekledim' DEME — 'onayina "
            "sundum' de. "
            + ((onizleme or {}).get("modele") or ""))


def reddedildi_metni(arac: str, sebep: str) -> str:
    return (f"'{arac}' CALISTIRILMADI ve ONAYA DA SUNULAMADI: {sebep}. "
            "Kullaniciya bunu soyle; sebep giderilirse (or. dogru kimlik) tekrar dene.")


def _arg_metni(argumanlar: dict) -> str:
    m = json.dumps(argumanlar or {}, ensure_ascii=False)
    return m if len(m) <= 1200 else m[:1200] + " …(kirpildi)"


def ozet_html(veri: dict) -> str:
    """
    Onay mesajinin DETERMINISTIK kismi. Modelin cevap metni neyi anlatirsa
    anlatsin, butona basilinca calisacak sey ve FARKI BURADA yazar.
    """
    from .bulut_katalog import KATALOG
    arac = veri.get("arac") or "?"
    k = KATALOG.get(arac) or {}
    o = veri.get("onizleme") or {}
    e = html.escape
    satir = [f"🔐 <b>IBKR'de calisacak</b>: <code>{e(arac)}</code>"]
    satir += [e(s) for s in o.get("satirlar") or []]
    satir += [f"⚠️ <b>{e(u)}</b>" for u in o.get("uyarilar") or []]
    if k.get("not_") and not o.get("uyarilar"):
        satir.append(f"⚠️ {e(k['not_'])}")
    satir.append(f"<i>Arguman:</i> <code>{e(_arg_metni(veri.get('argumanlar') or {}))}</code>")
    return "\n".join(satir)


# ---------------------------------------------------------------------------
# okuyucu ve yurutme
# ---------------------------------------------------------------------------

async def gercek_okuyucu(arac: str, arg: dict) -> Any:
    """Baglayicidan OKUMA, birebir arguman kapisiyla."""
    from .mcp_kanal import cagir_async
    return (await cagir_async(arac, arg, genis=True)).veri


async def onizle_sinirli(arac: str, arg: dict, db=None, oku: Okuyucu | None = None) -> dict:
    """Kapidan cagrilir: sure sinirli; asilirsa istek SUNULMAZ."""
    import anyio
    try:
        with anyio.fail_after(ONIZLEME_SURE_SN):
            return await onizle(arac, arg, oku or gercek_okuyucu, db)
    except TimeoutError as e:
        raise OnizlemeReddi(f"onizleme {ONIZLEME_SURE_SN:.0f} sn icinde bitmedi") from e


def yurut(settings, veri: dict, sahip: str, db=None, _cagir=None,
          _oku: Okuyucu | None = None) -> str:
    """
    Onaylanmis yazma cagrisini YAPAR: on kosul -> yazma -> dogrulama.
    Kullaniciya gidecek metni doner.

    Hata ISTISNA OLARAK YUKSELMEZ: dinleyicinin genel hata yolu istegi
    "tekrar dene" butonuyla geri koyar. Yazmada bu CIFT ISLEM demek
    (zaman asimi = istek IBKR'ye ulasmis olabilir). Bu yuzden her sonuc
    metin olarak doner ve istek tuketilir.
    """
    import anyio

    arac = veri.get("arac") or ""
    arg = veri.get("argumanlar") or {}
    e = html.escape
    if arac not in {kisa_ad(a) for a in araclar()[1]}:
        return f"⛔️ <code>{e(arac)}</code> bir IBKR yazma araci degil; calistirilmadi."
    if sahip != settings.get("ibkr.sahip"):
        return "⛔️ Bu IBKR hesabi senin degil; calistirilmadi."
    oku = _oku or gercek_okuyucu

    async def _yaz():
        if _cagir is not None:
            import inspect
            r = _cagir(arac, arg, genis=True)
            return (await r) if inspect.isawaitable(r) else r
        from .mcp_kanal import cagir_async
        return await cagir_async(arac, arg, genis=True)

    async def _akis() -> str:
        # 2: ON KOSUL — onizlemeden beri degisti mi?
        onceki = (veri.get("onizleme") or {}).get("durum")
        if onceki is not None:
            try:
                simdi = await durum_oku(arac, arg, oku)
            except IbkrHatasi as ex:
                return (f"⛔️ <b>{e(arac)} calistirilmadi</b>: mevcut durumu okuyamadim "
                        f"(<code>{e(str(ex)[:200])}</code>). Hicbir sey degismedi; "
                        "tekrar iste.")
            if _normal(simdi) != _normal(onceki):
                return (f"⛔️ <b>{e(arac)} calistirilmadi</b>: onay istendikten sonra "
                        "IBKR'de degisti (liste/alarm baskasi ya da baska bir yoldan "
                        "guncellendi). Yanlis seyi ezmemek icin durdum — tekrar iste, "
                        "guncel farki gostereyim.")
        # YAZMA
        try:
            s = await _yaz()
        except DurumBilinmiyorHatasi as ex:
            log.warning("[ibkr-dogrudan] %s durum bilinmiyor: %s", arac, ex)
            return (f"⚠️ <b>{e(arac)}: sonuc BILINMIYOR</b>\n"
                    f"<code>{e(str(ex)[:300])}</code>\n"
                    "<i>Istek IBKR'ye ulasmis olabilir. Tekrar basmadan once "
                    "IBKR uygulamasindan ya da bana sorarak kontrol et.</i>")
        except IbkrHatasi as ex:
            log.warning("[ibkr-dogrudan] %s hata: %s", arac, ex)
            return (f"⛔️ <b>{e(arac)} calismadi</b>\n"
                    f"<code>{e(str(ex)[:300])}</code>")
        log.info("[ibkr-dogrudan] %s yazildi (%.1f sn)", arac, s.sure_sn)
        # 3: DOGRULAMA
        try:
            durum, satir = await dogrula(arac, arg, s.veri, oku)
        except IbkrHatasi as ex:
            durum, satir = None, [f"geri okuma basarisiz: {ex}"]
        govde = "\n".join(e(x) for x in satir)
        if durum is True:
            return f"✅ <b>IBKR: {e(arac)} yapildi ve dogrulandi</b>\n{govde}"
        if durum is False:
            log.warning("[ibkr-dogrudan] %s DOGRULAMA UYUSMADI: %s", arac, satir)
            return (f"⚠️ <b>IBKR: {e(arac)} calisti ama sonuc istenenle UYUSMUYOR</b>\n"
                    f"{govde}\n<i>IBKR'de kontrol et.</i>")
        ham = s.ham if len(s.ham) <= 500 else s.ham[:500] + "…"
        return (f"✅ <b>IBKR: {e(arac)} calisti</b>"
                + (f"\n{govde}" if govde else "")
                + f"\n<i>IBKR yaniti:</i> <code>{e(ham)}</code>")

    return anyio.run(_akis)


def _normal(x: Any) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)


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
        + ") CALISMAZ, kullanicinin Telegram onayina sunulur — 'yaptim' deme, "
        "'onayina sundum' de. Bir turda tek yazma sunulur; yenisi oncekinin "
        "yerine gecer.\n"
        "TAM DEGISTIRME KURALLARI: listeye kagit EKLEMEK/CIKARMAK = once "
        "get_watchlists (ad -> id), sonra get_watchlist, sonra MEVCUT TUM "
        "contract_id_ex'leri AYNEN koruyup edit_watchlist (adi da get_watchlist'"
        "ten). Alarm degistirmek = once get_alert, sonra degismeyen alanlari "
        "(email, email_note, tif, active_hours, exchange) AYNEN tasiyarak "
        "update_alert. create_alert'te e-posta yoksa kullaniciya sor.\n"
        "Hesap verisini (pozisyon, nakit, emir) buradan okursan cevapta "
        "'IBKR bulut baglayicisindan' de. IBKR'deki izleme listeleri botun "
        "kendi izleme listesinden (`izleme_listesi`) AYRIDIR, senkron "
        "degildir; kullanici 'IBKR' demezse hangisini kastettigini sor. "
        "Kontrat kimligi gerekirse once `search_contracts`.\n\n")
