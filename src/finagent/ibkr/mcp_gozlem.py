"""
FAZ 0 GECE GOZLEMI — bulut baglayicisi uretim kosullarinda calisiyor mu?

Plan olcutu 0.3: etkilesimsiz `claude -p` bu oturumda calisti, ama launchd
altinda, kilitli ekranda ve ag filtresi devredeyken OLCULMEDI. Bu modul 22:15
nabzindan SONRA, ayri bir surecte, bes gece boyunca olcer:

  1. secilen 12 arac baglayicida hala var mi (0.6, ToolSearch `select:`)
  2. bir okuma (`get_account_positions`): sure, tur, maliyet (0.4)
  3. ayni anda CPGW ile BIREBIR eslesiyor mu (0.1'in gece hali)

SESSIZ VE ZARARSIZ. Telegram'a hicbir sey gondermez; sonucu
`data/mcp_gozlem.jsonl`e bir satir olarak yazar. Hicbir kosulda istisna
disari sizmaz: gozlem cokse nabiz zaten bitmistir ve sonucu etkilenmez.
Hata da VERIDIR: turu ve mesaji satira yazilir.

Faz 0 bitince bu modul ya bekciye tasinir ya silinir; uretim yolu degil.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

VARSAYILAN_CIKTI = Path("data") / "mcp_gozlem.jsonl"


def _hata(e: Exception) -> dict:
    d = {"hata": type(e).__name__, "mesaj": str(e)[:600]}
    # BAGLAYICI YOKSA SEBEP DE KAYDA GIRER. Claude Code'un "yetki gerekiyor"
    # onbellegindeki kayit (varsa) ve zaman damgasi, kaydin kendiliginden
    # ne kadar surede dustugunu URETIM verisinden olcmeyi saglar — Faz
    # 0.5'te bu sure olculemedi.
    if hasattr(e, "onbellek_kaydi"):
        d["auth_onbellek_kaydi"] = getattr(e, "onbellek_kaydi")
        d["auth_onbellek_notu"] = getattr(e, "onbellek_notu", None)
    return d


def _cpgw_pozisyonlari(settings) -> list:
    """CPGW'den [conid, adet] listesi. Mevcut portfoy katmani — ikinci bir
    okuma yolu yazilmiyor."""
    from .istemci import Istemci
    from .portfoy import Portfoy
    istemci = Istemci(settings.get("ibkr.taban_url", None))
    try:
        return sorted([int(p.conid), float(p.adet)]
                      for p in Portfoy(istemci).pozisyonlar()
                      if p.conid and p.adet is not None)
    finally:
        istemci.kapat()


def gece_gozlemi(settings, cikti: Path | None = None, *, _sorgu=None,
                 _cpgw=None) -> dict:
    """Bir gozlem satiri uretir, dosyaya ekler ve doner. ASLA istisna atmaz."""
    import anyio

    from . import mcp_kanal as K

    kayit: dict = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # launchd isi XPC_SERVICE_NAME tasir; elle kosumu ayirt etmek icin.
        # (Kesinti izleme dersi: bekci bir ELLE kosumu ariza sanmisti.)
        "baglam": ("launchd" if os.environ.get("XPC_SERVICE_NAME", "")
                   .startswith("com.alipala") else "elle"),
    }
    try:
        kayit["varlik"] = anyio.run(lambda: K.arac_varligi_async(_sorgu=_sorgu))
    except Exception as e:                                # noqa: BLE001
        kayit["varlik"] = _hata(e)

    mcp = None
    try:
        r = K.cagir("get_account_positions", _sorgu=_sorgu)
        mcp = sorted([int(p["contract_id"]), float(p["position"])]
                     for p in r.veri.get("positions") or [])
        kayit["okuma"] = {"sure_sn": r.sure_sn, "tur": r.tur,
                          "maliyet_usd": r.maliyet_usd, "pozisyon": mcp}
    except Exception as e:                                # noqa: BLE001
        kayit["okuma"] = _hata(e)

    try:
        cpgw = (_cpgw or _cpgw_pozisyonlari)(settings)
        kayit["cpgw"] = {"pozisyon": cpgw}
        # Eslesme YALNIZCA ikisi de okunduysa hukum verir; biri dustuyse
        # None ("bilinmiyor") — "eslesmiyor" DEGIL.
        kayit["eslesme"] = (mcp == cpgw) if mcp is not None else None
    except Exception as e:                                # noqa: BLE001
        kayit["cpgw"] = _hata(e)
        kayit["eslesme"] = None

    yol = Path(cikti or VARSAYILAN_CIKTI)
    try:
        yol.parent.mkdir(parents=True, exist_ok=True)
        with yol.open("a", encoding="utf-8") as f:
            f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
    except Exception as e:                                # noqa: BLE001
        log.warning("[mcp-gozlem] satir yazilamadi: %s", e)
    log.info("[mcp-gozlem] %s", json.dumps(kayit, ensure_ascii=False)[:400])
    return kayit


def ozet(yol: Path | None = None) -> dict:
    """Gozlem dosyasinin Faz 0 kabul olcutlerine gore ozeti."""
    satirlar = []
    p = Path(yol or VARSAYILAN_CIKTI)
    if p.exists():
        for s in p.read_text(encoding="utf-8").splitlines():
            try:
                satirlar.append(json.loads(s))
            except json.JSONDecodeError:
                continue
    gece = [s for s in satirlar if s.get("baglam") == "launchd"]
    ok = [s for s in gece if "hata" not in (s.get("okuma") or {"hata": 1})]
    sureler = sorted(s["okuma"]["sure_sn"] for s in ok)
    return {
        "gece_kosusu": len(gece),
        "basarili_okuma": len(ok),
        "eslesen": sum(1 for s in gece if s.get("eslesme") is True),
        "eslesmeyen": sum(1 for s in gece if s.get("eslesme") is False),
        "eksik_arac_gorulen": sum(1 for s in gece if (s.get("varlik") or {}).get("eksik")),
        "p90_sure_sn": (sureler[min(len(sureler) - 1, int(0.9 * (len(sureler) - 1)))]
                        if sureler else None),
        "hatalar": [((s.get("okuma") or {}).get("hata")) for s in gece
                    if "hata" in (s.get("okuma") or {})],
    }
