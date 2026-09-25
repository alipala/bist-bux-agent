"""
SIRKET TEMALARI (IBKR MCP Faz 5) — kabuk.

Portfoydeki (tum sahipler, tum hesaplar) KIMLIGI DOGRULANMIS hisseler icin
IBKR `get_company_themes`. Yalnizca kaydi olmayan (portfoye yeni giren) ya
da kaydi `yenileme_gun`den eski sirketler cekilir; portfoy degismezse
baglayici HIC cagrilmaz. Gece basina en fazla `azami_sembol`; ertelenen
soylenir.

Tema bos donerse AYNI sirketin diger listelemeleri denenir
(`analysis.tema.alternatif_listeleme`); hicbiri vermezse 'bos' yazilir ve
`yenileme_gun` boyunca tekrar denenmez. 'hata' ertesi gece yeniden denenir.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)


class SirketTemaCollector(BaseCollector):
    name = "sirkettema"
    needs_browser = False

    def collect(self, _cagir=None) -> CollectorResult:
        from ..analysis.tema import BILINMEYEN_TUR, HISSE_TURLERI, alternatif_listeleme
        from ..ibkr import mcp_kanal
        from ..ibkr.istemci import IbkrHatasi

        k = self.s.get("sources.sirkettema") or {}
        azami = int(k.get("azami_sembol", 6))
        yenile = int(k.get("yenileme_gun", 90))
        cagir = _cagir or mcp_kanal.cagir

        gerekli = self._gerekli(HISSE_TURLERI + BILINMEYEN_TUR, yenile)
        if not gerekli:
            return CollectorResult(self.name, "ok", 0, "yeni sirket yok")
        notlar = []
        if len(gerekli) > azami:
            notlar.append(f"{len(gerekli) - azami} sirket sonraki geceye ertelendi: "
                          + ", ".join(s for _, s, _ in gerekli[azami:]))
        yazilan, dusen = 0, []
        for iid, sembol, conid in gerekli[:azami]:
            try:
                temalar, kullanilan = self._temalar(cagir, conid), conid
                if not temalar:
                    ara = cagir("search_contracts", {"query": sembol}).veri
                    for alt in alternatif_listeleme(ara, conid, sembol)[:2]:
                        temalar = self._temalar(cagir, alt)
                        if temalar:
                            kullanilan = alt
                            break
                durum = "tamam" if temalar else "bos"
                self._yaz(iid, durum, kullanilan, temalar, None)
                yazilan += 1
                log.info("[tema] %s: %s (%s)", sembol, durum, ", ".join(temalar[:3]))
            except IbkrHatasi as e:
                self._yaz(iid, "hata", conid, [], f"{type(e).__name__}: {str(e)[:200]}")
                dusen.append(f"{sembol}: {type(e).__name__}")
        if dusen:
            notlar.append(f"{len(dusen)} dustu: " + "; ".join(dusen[:4]))
        notlar.insert(0, f"cekilen {yazilan}/{min(len(gerekli), azami)}")
        return CollectorResult(self.name, "partial" if dusen else "ok", yazilan,
                               " · ".join(notlar))

    # ------------------------------------------------------------------
    @staticmethod
    def _temalar(cagir, conid: int) -> list[str]:
        v = cagir("get_company_themes", {"contract_id": int(conid),
                                         "max_themes": 6, "max_companies": 3}).veri
        return [str(t["name"]) for t in (v or {}).get("linked_themes") or []
                if t.get("name")]

    def _gerekli(self, turler, yenile: int) -> list[tuple[int, str, int]]:
        """(instrument_id, sembol, conid) — kaydi yok, eski ya da hatali."""
        sinir = (datetime.now(timezone.utc) - timedelta(days=yenile)
                 ).strftime("%Y-%m-%d %H:%M:%S")
        dun = (datetime.now(timezone.utc) - timedelta(hours=20)
               ).strftime("%Y-%m-%d %H:%M:%S")
        yer = ",".join("?" * len(turler))
        rows = self.db.query(
            f"""SELECT DISTINCT i.id, i.symbol, d.conid, t.durum, t.cekilis_ts
                FROM positions p
                JOIN instruments i ON i.id = p.instrument_id
                JOIN identities d ON d.instrument_id = i.id
                     AND d.conid IS NOT NULL AND d.conid <> ''
                LEFT JOIN sirket_tema t ON t.instrument_id = i.id
                WHERE LOWER(COALESCE(i.asset_type, '')) IN ({yer})
                  AND p.quantity > 0
                  AND p.snapshot_ts = (SELECT MAX(snapshot_ts) FROM positions q
                                       WHERE q.account = p.account AND q.sahip = p.sahip)
                ORDER BY i.symbol""", tuple(turler))
        out, gorulen = [], set()
        for r in rows:
            if r["id"] in gorulen:
                continue
            gorulen.add(r["id"])
            if r["durum"] is None or r["cekilis_ts"] < sinir or (
                    r["durum"] == "hata" and r["cekilis_ts"] < dun):
                try:
                    out.append((r["id"], r["symbol"], int(r["conid"])))
                except (TypeError, ValueError):
                    continue
        return out

    def _yaz(self, iid, durum, conid, temalar, not_):
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO sirket_tema (instrument_id, durum, conid, temalar, not_,
                                            cekilis_ts)
                   VALUES (?,?,?,?,?, datetime('now'))
                   ON CONFLICT(instrument_id) DO UPDATE SET durum = excluded.durum,
                     conid = excluded.conid, temalar = excluded.temalar,
                     not_ = excluded.not_, cekilis_ts = excluded.cekilis_ts""",
                (iid, durum, conid, json.dumps(temalar, ensure_ascii=False), not_))
