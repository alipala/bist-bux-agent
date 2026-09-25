"""
YEDEK OKUMA KANALI (IBKR MCP Faz 1) — CPGW dusunce portfoy baglayicidan.

NEDEN
-----
24 Eyl 17:45-20:39 arasi yerel ag gecidi (CPGW) 8 kez HTTP 401 verdi ve
bot IBKR konusunda KORDU. IBKR'nin bulut baglayicisi CPGW gerektirmiyor
(OAuth, claude.ai hesabinda). Bu modul iki soruyu tek yerde cevaplar:

  1. Okuma icin CPGW KULLANILABILIR MI? (`cpgw_okuma_durumu`)
  2. Kullanilamiyorsa portfoyu baglayicidan OKU (`mcp_portfoy`).

KARAR KODDA, MODELDE DEGIL
--------------------------
CPGW saglamken baglayici hic kullanilmaz: iki kaynak arasinda secim yapan
bir model, ayni soruya iki ayri cevap uretebilir. Yedek yalnizca CPGW
okunamadiginda devreye girer ve bunu SOYLER (`kanal`).

OKUMA KAPISI DOGRU KATMANDA
---------------------------
IBKR oturumu iki katmanli: `/portfolio` uclari brokerage oturumu DUSMUSKEN
de calisabiliyor (olculdu 2026-08-26, bkz. `collectors/ibkrportfoy.py`).
Bu yuzden "okunabilir mi" sorusunun olcusu `/iserver/auth/status` DEGIL,
`/portfolio/accounts`un kendisi. Yanlis katmana bakmak, veri DURURKEN
yedege gecmek (ya da tersi) olurdu.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .istemci import IbkrHatasi, Istemci, UlasilamadiHatasi, YetkiHatasi

log = logging.getLogger(__name__)

# Sohbet her mesajda CPGW'yi yokluyor; askidaki bir ag gecidi sohbeti
# bekletmemeli. Yerelde saglikli bir yanit ~0,1 sn; 2 sn bol pay.
YOKLAMA_SURE_SN = 2.0


def yedek_acik(settings) -> bool:
    """
    `ibkr.mcp_yedek` — ACIKCA yazilmali. Yoksa KAPALI (yeni bir kanal
    sessizce acilmaz). Bool degilse hata: "evet" gibi bir deger sessizce
    "acik" sayilmasin.
    """
    v = settings.get("ibkr.mcp_yedek")
    if v is None:
        return False
    if not isinstance(v, bool):
        raise ValueError(f"ibkr.mcp_yedek bool olmali, {v!r} verilmis")
    return v


def cpgw_okuma_durumu(settings, sure_sn: float = YOKLAMA_SURE_SN,
                      _istemci=None) -> tuple[bool, str]:
    """
    CPGW'den portfoy OKUNABILIR MI? (okunabilir, sebep).

    Olcu `/portfolio/accounts` (bkz. modul basligi). Hesap listesi BOS
    donerse okunabilir SAYILMAZ: bos cevap "portfoy yok" kaniti degil
    (`bos-yanit-yokluk-kaniti-degil`).
    """
    from .portfoy import Portfoy

    istemci = _istemci or Istemci(settings.get("ibkr.taban_url", None),
                                  zaman_asimi=sure_sn)
    try:
        hesaplar = Portfoy(istemci).hesaplar(tazele=True)
        if not hesaplar:
            return False, "CPGW hesap listesi bos dondu"
        return True, "CPGW okunabilir"
    except YetkiHatasi:
        return False, "CPGW giris istiyor (401)"
    except UlasilamadiHatasi:
        return False, "CPGW'ye ulasilamiyor (ag gecidi calismiyor)"
    except IbkrHatasi as e:
        return False, f"CPGW hatasi: {type(e).__name__}"
    except Exception as e:                                # noqa: BLE001
        return False, f"CPGW yoklamasi dustu: {type(e).__name__}"
    finally:
        if _istemci is None:
            try:
                istemci.kapat()
            except Exception:                             # noqa: BLE001
                pass


# HIZ SINIRI: `/portfolio/accounts` IBKR'de 5 saniyede BIR istekle sinirli
# (`Portfoy.hesaplar` belgesi) ve 429 ceza kutusu riski tasiyor
# (`HizHatasi`). Sohbet HER MESAJDA yokluyor ve her mesaj AYRI bir isci
# surecte kosuyor — bellek ici onbellek surecler arasinda paylasilmaz.
# Sonuc bu dosyada 60 sn paylasilir: tum surecler toplamda dakikada en
# fazla BIR yoklama yapar (portfoy toplayicisi kendi cagrisini yapar).
ONBELLEK_SURE_SN = 60.0


def cpgw_okuma_durumu_onbellekli(settings, yol=None, sure_sn: float = ONBELLEK_SURE_SN,
                                 _yokla=None, _simdi=None) -> tuple[bool, str]:
    """
    `cpgw_okuma_durumu`, surecler arasi dosya onbellegiyle. Dosya
    okunamazsa ya da bozuksa YENIDEN yoklanir (onbellek bir hiz optimizasyonu,
    dogruluk kaynagi degil). Yazim atomik (gecici dosya + rename): iki isci
    ayni anda yazarsa yarim JSON kalmasin.
    """
    import json
    import os
    import time
    from pathlib import Path

    simdi = _simdi if _simdi is not None else time.time()
    # `bot_state_dir`: testler BOT_STATE_DIR ile izole eder; elle kurulan
    # bir yol canli bot dizinine yazabilirdi (depoda yasanmis sinif).
    p = Path(yol) if yol else (settings.bot_state_dir / "cpgw_okuma.json")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if simdi - float(d["ts"]) < sure_sn:
            return bool(d["okunur"]), str(d["sebep"]) + " (onbellek)"
    except (OSError, ValueError, KeyError, TypeError):
        pass
    okunur, sebep = (_yokla or cpgw_okuma_durumu)(settings)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        gecici = p.with_suffix(f".{os.getpid()}.tmp")
        gecici.write_text(json.dumps({"ts": simdi, "okunur": okunur,
                                      "sebep": sebep}), encoding="utf-8")
        os.replace(gecici, p)
    except OSError as e:
        log.warning("[ibkr] CPGW yoklama onbellegi yazilamadi: %s", e)
    return okunur, sebep


@dataclass
class McpPortfoy:
    pozisyonlar: list
    nakit: dict
    taban_pb: str | None
    kanal: str = "mcp"
    sureler: dict = field(default_factory=dict)


def mcp_portfoy(_cagir=None) -> McpPortfoy:
    """
    Portfoyu bulut baglayicisindan okur: pozisyon + nakit + taban para
    birimi (uc ayri cagri, her biri `mcp_kanal` uzerinden, arguman kodda).

    Hata YUTULMAZ: `mcp_kanal`in hata turleri (`YetkiHatasi`,
    `BaglayiciYokHatasi`, `UlasilamadiHatasi` ...) cagirana gider.
    """
    from . import mcp_kanal
    from .portfoy import mcp_nakit, mcp_pozisyonlari

    cagir = _cagir or mcp_kanal.cagir
    poz = cagir("get_account_positions")
    bak = cagir("get_account_balances")
    ozet = cagir("get_account_summary")
    taban = ozet.veri.get("currency") if isinstance(ozet.veri, dict) else None
    return McpPortfoy(
        pozisyonlar=mcp_pozisyonlari(poz.veri),
        nakit=mcp_nakit(bak.veri),
        taban_pb=(str(taban).upper() if taban else None),
        sureler={"pozisyon": poz.sure_sn, "bakiye": bak.sure_sn,
                 "ozet": ozet.sure_sn})
