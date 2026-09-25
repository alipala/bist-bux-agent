"""
BILANCO BEKLENTISI (IBKR MCP Faz 4) — kabuk.

Her gece iki is:
  1. GERCEKLESEN: tepki gunu gecmis satirlara gercek hareketi yaz.
  2. FIYATLANAN: portfoyun yaklasan bilancolari icin (en fazla
     `azami_sembol`, en yakin once) ATM straddle olc.

BUTCE: hisse basina 5 baglayici cagrisi (~60 sn). Bilanco sezonunda ayni
hafta 10 portfoy hissesi aciklarsa nabza 10 dk eklenirdi; bu yuzden gece
basina `azami_sembol` ve bilanco basina en fazla IKI olcum (ilk kez
`pencere_gun` icinde, bir kez de son `yenileme_gun` gunde). Ertelenen
sayisi SOYLENIR. Hesap mantigi `ibkr/beklenti.py`de.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)


class BilancoBeklentiCollector(BaseCollector):
    name = "bilancobeklenti"
    needs_browser = False

    def collect(self, _cagir=None, _bugun: str | None = None) -> CollectorResult:
        from ..ibkr import mcp_kanal
        from ..ibkr.beklenti import BeklentiHesaplanamadi, hesapla
        from ..ibkr.istemci import IbkrHatasi
        from .bilancotakvim import yaklasan_bilancolar

        k = self.s.get("sources.bilancobeklenti") or {}
        azami = int(k.get("azami_sembol", 4))
        pencere = int(k.get("pencere_gun", 7))
        yenile = int(k.get("yenileme_gun", 2))
        bugun = _bugun or date.today().isoformat()
        notlar: list[str] = []

        dolan = self._gerceklesen_doldur(bugun)
        if dolan:
            notlar.append(f"gerceklesen {dolan}")

        hedef: dict = {}
        for sahip in list(self.s.sahip_listesi or []):
            for b in yaklasan_bilancolar(self.db, sahip, gun=pencere)["bilancolar"]:
                hedef[(b["instrument_id"], b["tarih"])] = b
        gerekli = sorted((b for b in hedef.values() if self._gerekli(b, bugun, yenile)),
                         key=lambda b: (b["kalan_gun"], b["sembol"]))
        ertelenen = gerekli[azami:]
        if ertelenen:
            notlar.append(f"{len(ertelenen)} hisse sonraki geceye ertelendi (butce): "
                          + ", ".join(b["sembol"] for b in ertelenen))
        yazilan, dusen = 0, []
        for b in gerekli[:azami]:
            conid = self._conid(b["instrument_id"])
            if not conid:
                dusen.append(f"{b['sembol']}: conid yok")
                continue
            try:
                s = hesapla(conid, b["sembol"], b["en_gec_tepki"],
                            _cagir or mcp_kanal.cagir)
            except (BeklentiHesaplanamadi, IbkrHatasi) as e:
                dusen.append(f"{b['sembol']}: {type(e).__name__}: {str(e)[:160]}")
                continue
            with self.db.tx() as c:
                c.execute(
                    """INSERT INTO bilanco_beklentisi
                         (instrument_id, bilanco_tarih, olcum_gunu, conid,
                          tepki_gunu, fiyat, vade, strike, call_orta, put_orta,
                          hareket_pct, veri_durumu, fiyat_kaynagi)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(instrument_id, bilanco_tarih, olcum_gunu)
                       DO NOTHING""",
                    (b["instrument_id"], b["tarih"], bugun, s["conid"],
                     b["en_gec_tepki"], s["fiyat"], s["vade"], s["strike"],
                     s["call_orta"], s["put_orta"], s["hareket_pct"],
                     s["veri_durumu"], s["fiyat_kaynagi"]))
            yazilan += 1
            log.info("[beklenti] %s %s: piyasa ±%%%.1f fiyatliyor (vade %s, %s)",
                     b["sembol"], b["tarih"], s["hareket_pct"], s["vade"],
                     s["veri_durumu"])
        if dusen:
            notlar.append(f"{len(dusen)} dustu: " + "; ".join(dusen[:4]))
        notlar.insert(0, f"olculen {yazilan}/{min(len(gerekli), azami)}")
        durum = "partial" if dusen else "ok"
        return CollectorResult(self.name, durum, yazilan + dolan, " · ".join(notlar))

    # ------------------------------------------------------------------
    def _conid(self, iid: int) -> int | None:
        r = self.db.query("SELECT conid FROM identities WHERE instrument_id = ? "
                          "AND conid IS NOT NULL", (iid,))
        try:
            return int(r[0]["conid"]) if r else None
        except (TypeError, ValueError):
            return None

    def _gerekli(self, b: dict, bugun: str, yenile: int) -> bool:
        """Ilk olcum yoksa evet; son `yenile` gunde henuz olculmediyse evet."""
        olcumler = [r["olcum_gunu"] for r in self.db.query(
            """SELECT olcum_gunu FROM bilanco_beklentisi
               WHERE instrument_id = ? AND bilanco_tarih = ?""",
            (b["instrument_id"], b["tarih"]))]
        if not olcumler:
            return True
        # AYNI GUN TEKRAR OLCUM ayrica engellenmiyor: bugunku olcum her
        # zaman `sinir`in (bilancodan `yenile` gun once) ustunde kalir,
        # yani asagidaki kosul onu zaten kapsiyor. (Ayri bir satir vardi;
        # mutasyon turu onu kaldirmanin davranisi DEGISTIRMEDIGINI gosterdi.)
        sinir = (date.fromisoformat(b["tarih"]) - timedelta(days=yenile)).isoformat()
        return b["kalan_gun"] <= yenile and max(olcumler) < sinir

    def _gerceklesen_doldur(self, bugun: str) -> int:
        from ..ibkr.beklenti import gerceklesen
        n = 0
        for r in self.db.query(
                """SELECT DISTINCT instrument_id, bilanco_tarih, tepki_gunu
                   FROM bilanco_beklentisi
                   WHERE gerceklesen_pct IS NULL AND tepki_gunu < ?""", (bugun,)):
            kap = [(str(x["ts"])[:10], float(x["close"]))
                   for x in self.db.fiyat_serisi(r["instrument_id"], 60) if x["close"]]
            g = gerceklesen(kap, r["tepki_gunu"])
            if g is None:
                continue
            with self.db.tx() as c:
                c.execute("""UPDATE bilanco_beklentisi SET gerceklesen_pct = ?,
                               gerceklesen_ts = datetime('now')
                             WHERE instrument_id = ? AND bilanco_tarih = ?""",
                          (g, r["instrument_id"], r["bilanco_tarih"]))
            n += 1
        return n
