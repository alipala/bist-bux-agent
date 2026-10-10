"""
DAMITICI — bir sohbet turundan KALICI bir gercek cikarir ve ONAYA sunar.

NEDEN VAR (olculdu 2026-08-25). Kalici hafiza katmani (`hatirlanan`) 9
gunde 4 kayit uretti; ayni surede arsive 342 tur yazildi. Yani her 85
turda bir kalici gercek. Katman calismiyordu.

SEBEP OKUMA DEGIL YAZMA TARAFIYDI. Kod, GERI CAGIRMAYI modele birakmanin
hafizayi olasiliksal yaptigini zaten soyluyor (`_hafiza_blogu`: "78
asistan turunun yalnizca 6'sinda cagrildi") ve okuma tarafini
duzeltmisti — kalici gercekler artik her tura otomatik giriyor. Ama
YAZMA tarafi tam olarak o eski durumdaydi: model `hatirla` aracini
cagirmaya karar vermezse hicbir sey kalicilasmiyordu.

ONAY KAPISI KALKMIYOR. Degisen tek sey ONERININ kaynagi: artik modelin
arac secme kararina degil, her turdan sonra kosan ayri bir bakisa
bagli. Yazma yine kullanicinin onayindan geciyor — yanlis bir kalici
gercek her cevabi sessizce yonlendirir ve kullanici neyin kalicilastigini
GORMELI (bkz. `tools.hatirla` yorumu).

BUTCE YALITIMI SART. Bu cagri ANA TURUN suresinden yemez: cevap zaten
gonderildikten SONRA, ayri ve kisa bir cagri olarak kosuyor. Panelin
2026-08-25 sabahi ogrettigi ders — tur payi butcenin kendisidir.
"""
from __future__ import annotations

from ..llm import sdk_ortami

import json
import logging
import re

log = logging.getLogger(__name__)

# Damiticinin duvar saati. Kisa: bu bir ANALIZ degil bir SUZGEC.
# Asilirsa oneri yok — sessizce gecilir, tur zaten tamamlanmis durumda.
SURE_SINIRI_SN = 45

# Turun modele gosterilen kismi. Damitici cevabin TAMAMINI okumaz:
# aradigi sey kullanicinin koydugu KURAL, ve o kural sorunun icinde
# olur, uc bin karakterlik analizin icinde degil.
SORU_TAVANI = 1200
CEVAP_TAVANI = 900

TALIMAT = """Sen bir SUZGECSIN. Bir sohbet turuna bakip tek soruyu
cevaplarsin: kullanici KALICI bir sey mi soyledi?

KALICI OLAN (oner):
* KURAL/TERCIH — "bundan sonra hep sunu kullan", "genel olarak sunu yap",
  "su hesabi hep boyle hesapla"
* VARLIK/OLGU — "Garanti'de altin hesabim var", "su brokerdan calisiyorum"
* KARAR — "bu stratejiden vazgectim", "artik su sektore girmiyorum"

KALICI OLMAYAN (onerme):
* Tek seferlik soru/cevap ("ASML nasil gidiyor")
* Piyasa yorumu, fiyat, gecici durum
* Senin kendi analizin ya da onerin — kullanicinin sozu degil
* Zaten hatirlananlar listesinde olan bir sey

DEGISEBILEN BIR SAYIYI ICERIGE YAZMA (fiyat, adet, maliyet): onlar
kaynaklarindan okunur. Kalici olan "bu sembol onemli" olabilir, "birim
maliyeti 713,05" DEGIL.

CIKTI — YALNIZCA JSON, baska hicbir sey:
{"oneri": null}
ya da
{"oneri": {"tur": "tercih|olgu|karar", "konu": "kisa anahtar",
           "icerik": "tam cumle, kullanicinin dilinde",
           "gerekce": "hangi cumleden cikardin"}}

EMIN DEGILSEN null DON. Yanlis bir kalici gercek, hic kayit
olmamasindan KOTUDUR: her cevabi sessizce yonlendirir."""


def _json_coz(metin: str) -> dict | None:
    """Modelin cevabindan JSON blogunu cikarir."""
    if not metin:
        return None
    ham = metin.strip()
    if "```" in ham:
        parcalar = re.findall(r"```(?:json)?\s*(.*?)```", ham, re.S)
        if parcalar:
            ham = parcalar[0].strip()
    bas, son = ham.find("{"), ham.rfind("}")
    if bas < 0 or son <= bas:
        return None
    try:
        return json.loads(ham[bas:son + 1])
    except (TypeError, ValueError):
        return None


async def _sor(settings, istem: str) -> str:
    """Tek atislik model cagrisi — ARAC YOK, TEK TUR."""
    from claude_agent_sdk import ClaudeAgentOptions, query

    opts = ClaudeAgentOptions(
        **sdk_ortami(),
        system_prompt=TALIMAT,
        model=settings.get("analysis.llm.strategist_model", "claude-opus-5-5"),
        # ARAC YOK: damitici veri CEKMEZ, elindeki metne bakar. Arac
        # acilsaydi tek atislik bir suzgec, dakikalarca surebilen bir
        # ajana donusurdu.
        allowed_tools=[], max_turns=1,
    )
    parcalar: list[str] = []
    async for m in query(prompt=istem, options=opts):
        for blok in getattr(m, "content", None) or []:
            metin = getattr(blok, "text", None)
            if metin:
                parcalar.append(metin)
    return "".join(parcalar)


def damit(settings, db, sahip: str, soru: str, cevap: str) -> dict | None:
    """
    Turdan kalici bir gercek cikarir. Doner: oneri sozlugu ya da None.

    HATA YUTULUYOR VE SESSIZ DEGIL. Damitici bir KOLAYLIK; dusmesi
    sohbeti etkilememeli (cevap zaten gonderildi) ama sessizce
    kaybolmasi da "hafiza neden hic oneri getirmiyor" sorusunu
    cevapsiz birakirdi.
    """
    if not sahip or not (soru or "").strip():
        return None
    try:
        mevcut = db.hatirlananlar(sahip)
    except Exception as e:                            # noqa: BLE001
        log.warning("[damitici] mevcut kayitlar okunamadi: %s", e)
        mevcut = []

    # ZATEN HATIRLANANLAR VERILIYOR: yoksa damitici her turda ayni
    # kurali yeniden onerir ve kullanici onay yorgunlugundan butonlari
    # okumayi birakir — bu katmanin en gercek riski.
    liste = "\n".join(f"- ({r['tur']}) {r['konu']}: {(r['icerik'] or '')[:120]}"
                      for r in mevcut) or "(henuz yok)"
    istem = (f"ZATEN HATIRLANANLAR (bunlari TEKRAR onerme):\n{liste}\n\n"
             f"KULLANICI:\n{(soru or '')[:SORU_TAVANI]}\n\n"
             f"SEN:\n{(cevap or '')[:CEVAP_TAVANI]}")

    try:
        import anyio
        ham = anyio.run(
            lambda: _zaman_asimiyla(anyio, _sor, settings, istem))
    except Exception as e:                            # noqa: BLE001
        log.warning("[damitici] cagri dustu (%s): %s",
                    type(e).__name__, str(e)[:160])
        return None

    veri = _json_coz(ham)
    if not veri:
        log.info("[damitici] JSON cozulemedi, oneri yok")
        return None
    oneri = veri.get("oneri")
    if not isinstance(oneri, dict):
        return None

    tur = str(oneri.get("tur") or "").strip().lower()
    konu = str(oneri.get("konu") or "").strip()
    icerik = str(oneri.get("icerik") or "").strip()
    if tur not in db.HATIRLANAN_TURLERI or not konu or not icerik:
        log.info("[damitici] eksik/gecersiz oneri atlandi: %r", oneri)
        return None
    return {"tur": tur, "konu": konu, "icerik": icerik,
            "gerekce": str(oneri.get("gerekce") or "").strip()}


async def _zaman_asimiyla(anyio, fn, *a):
    """Duvar saati — asilirsa istisna, cagiran yutuyor."""
    with anyio.fail_after(SURE_SINIRI_SN):
        return await fn(*a)
