"""
HABER ZENGINLESTIRME (Jev) — kabuk.

Iki adim, bu SIRAYLA:
  1. BAGLAMA: sembolsuz haber (olculen kaynaklar) -> aday ureteci ->
     Jev Choice "hangisi / hicbiri". Esigi gecen secim `haber_bag`a.
  2. OLAY TURU: sembole bagli her (haber, sembol) — ticker ile ya da 1.
     adimda Jev ile — icin Jev Choice (6 tur). `haber_olay`a.
Soru metinleri, esikler ve aday ureteci `research/haber_jev.py`de; burada
YALNIZCA secim, cagri ve yazim var.

NEDEN IKI AYRI ISTEK (baglama, sonra olay)
------------------------------------------
Olculen iki yapilandirma bunlar: baglama sorusu TEK BASINA (orneklem 3),
olay sorusu sembol bilinerek (orneklem 2-B). Ikisini tek istekte birlestirmek
olculmemis bir ucuncu yapilandirma olurdu. Maliyet farki yalnizca
baglanan haberler (~%15) icin bir istek.

KALEM BASINA GARANTI
--------------------
Her denenen kalem bir satir birakir — basarili ya da `hata`li. Boylece:
  * ayni haber her kosuda yeniden SORULMAZ (butce korunur);
  * 422 (bicim hatasi) sonsuza kadar denenmez;
  * ag/servis hatasinda kosu ERKEN DURUR ve o kalem YAZILMAZ — bir sonraki
    kosuda yeniden denenir. Kesinti "hicbiri" ya da "onemsiz" olarak
    kaydedilmez; okuyan taraf satir yoksa "siniflandirilmadi" der.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)


class HaberJevCollector(BaseCollector):
    name = "haberjev"
    needs_browser = False

    def collect(self, _sor=None) -> CollectorResult:
        from .. import jev
        from ..research import haber_jev as H

        k = self.s.get("sources.haberjev") or {}
        if _sor is None and not jev.anahtar():
            return CollectorResult(self.name, "skipped", 0,
                                   f"{jev.ANAHTAR_ENV} tanimli degil — haberler zenginlestirilmedi")
        sor = _sor or jev.sor
        gun = int(k.get("pencere_gun", 3))
        azami = int(k.get("azami_istek", 600))
        paralel = int(k.get("eszamanli", 4))
        notlar: list[str] = []
        durdu: str | None = None
        yazilan = 0

        # ---- 1. baglama ------------------------------------------------
        if k.get("baglama", False):
            kaynaklar = list(k.get("baglama_kaynaklari") or [])
            isler = self._baglanacaklar(gun, kaynaklar)
            if len(isler) > azami:
                notlar.append(f"baglama: {len(isler) - azami} haber sonraki kosuya")
            dizin = H.AdayDizini(self._enstrumanlar()) if isler else None
            istekler = []
            for nid, baslik, ozet in isler[:azami]:
                adaylar = dizin.adaylar(baslik, ozet)
                if not adaylar:
                    self._bag_yaz(nid, None, None, None, 0, None, None)
                    continue
                istekler.append((nid, adaylar, H.haber_state(baslik, ozet),
                                 {"sirket": H.bag_sorusu(adaylar)}))
            sonuc, durdu = self._kos(sor, istekler, paralel)
            for (nid, adaylar, _, _), (cevap, hata) in zip(istekler, sonuc):
                if cevap is None and hata is None:
                    continue                      # kosu durdu: yazma, yeniden dene
                if hata:
                    self._bag_yaz(nid, None, None, None, len(adaylar), None, hata)
                    continue
                a = cevap["answers"]["sirket"]
                sembol, guven = H.bag_cevabi(a)
                self._bag_yaz(nid, sembol, a.get("choice"), guven, len(adaylar),
                              cevap.get("model"), None)
                yazilan += 1
            azami -= len(istekler)

        # ---- 2. olay turu ----------------------------------------------
        if durdu is None and azami > 0:
            isler = self._olaylanacaklar(gun)
            if len(isler) > azami:
                notlar.append(f"olay: {len(isler) - azami} haber sonraki kosuya")
            adlar = self._adlar({s for _, _, _, ss in isler[:azami] for s in ss})
            istekler = [(nid, ss, H.haber_state(baslik, ozet),
                         {f"o{i}": H.olay_sorusu(s, adlar.get(s, s))
                          for i, s in enumerate(ss)})
                        for nid, baslik, ozet, ss in isler[:azami]]
            sonuc, durdu = self._kos(sor, istekler, paralel)
            for (nid, ss, _, _), (cevap, hata) in zip(istekler, sonuc):
                if cevap is None and hata is None:
                    continue
                for i, s in enumerate(ss):
                    if hata:
                        self._olay_yaz(nid, s, None, None, {}, None, hata)
                        continue
                    tur, guven, olas = H.olay_cevabi(cevap["answers"].get(f"o{i}") or {})
                    self._olay_yaz(nid, s, tur, guven, olas, cevap.get("model"),
                                   None if tur else "bilinmeyen tur")
                yazilan += 1

        if durdu:
            notlar.insert(0, f"Jev'e ulasilamadi, kosu erken durdu: {durdu}")
        durum = "partial" if durdu else "ok"
        return CollectorResult(self.name, durum, yazilan,
                               " · ".join(notlar) or None)

    # ------------------------------------------------------------------
    @staticmethod
    def _kos(sor, istekler, paralel):
        """
        [(cevap, hata)] — istek sirasiyla. Servis hatasinda kalanlar
        (None, None) doner ve ikinci deger hata metnidir.
        """
        from .. import jev
        out: list[tuple] = [(None, None)] * len(istekler)
        durdu = None

        def bir(i):
            _, _, state, sorular = istekler[i]
            try:
                return i, sor(state, sorular), None
            except jev.JevGecersiz as e:
                return i, None, f"gecersiz: {str(e)[:200]}"

        # Parti parti: bir partide servis hatasi gorulunce sonrakiler
        # hic gonderilmez (ayni hatayi yuzlerce kez denemek butce yakar).
        for bas in range(0, len(istekler), max(1, paralel) * 8):
            parti = range(bas, min(bas + max(1, paralel) * 8, len(istekler)))
            try:
                with ThreadPoolExecutor(max(1, paralel)) as ex:
                    for i, cevap, hata in ex.map(bir, parti):
                        out[i] = (cevap, hata)
            except jev.JevHatasi as e:
                durdu = str(e)[:200]
                break
        return out, durdu

    def _baglanacaklar(self, gun: int, kaynaklar: list[str]):
        if not kaynaklar:
            return []
        yer = ",".join("?" * len(kaynaklar))
        rows = self.db.query(
            f"""SELECT n.id, n.title, n.summary FROM news n
                LEFT JOIN haber_bag b ON b.news_id = n.id
                WHERE COALESCE(n.symbols, '') = '' AND n.source IN ({yer})
                  AND n.published_at >= datetime('now', ?)
                  AND b.news_id IS NULL
                ORDER BY n.published_at DESC""", (*kaynaklar, f"-{gun} days"))
        return [(r["id"], r["title"], r["summary"]) for r in rows]

    def _olaylanacaklar(self, gun: int):
        """(news_id, baslik, ozet, [siniflanmamis semboller]) — en yeni once."""
        rows = self.db.query(
            """SELECT n.id, n.title, n.summary, n.symbols, b.sembol AS jev_sembol
               FROM news n LEFT JOIN haber_bag b ON b.news_id = n.id
               WHERE n.published_at >= datetime('now', ?)
                 AND (COALESCE(n.symbols, '') <> '' OR b.sembol IS NOT NULL)
               ORDER BY n.published_at DESC""", (f"-{gun} days",))
        if not rows:
            return []
        var = {(r["news_id"], r["sembol"]) for r in self.db.query(
            """SELECT o.news_id, o.sembol FROM haber_olay o JOIN news n ON n.id = o.news_id
               WHERE n.published_at >= datetime('now', ?)""", (f"-{gun} days",))}
        out = []
        for r in rows:
            ss = [s.strip() for s in (r["symbols"] or "").split(",") if s.strip()]
            if not ss and r["jev_sembol"]:
                ss = [r["jev_sembol"]]
            eksik = [s for s in dict.fromkeys(ss) if (r["id"], s) not in var]
            if eksik:
                out.append((r["id"], r["title"], r["summary"], eksik))
        return out

    def _enstrumanlar(self):
        return [(r["symbol"], r["venue"], r["name"]) for r in self.db.query(
            """SELECT symbol, venue, name FROM instruments
               WHERE asset_type = 'equity' AND venue IN ('BIST', 'BUX')
                 AND name IS NOT NULL AND name <> '' ORDER BY id""")]

    def _adlar(self, semboller: set[str]) -> dict[str, str]:
        if not semboller:
            return {}
        out: dict[str, str] = {}
        yer = ",".join("?" * len(semboller))
        for r in self.db.query(
                f"""SELECT symbol, name FROM instruments
                    WHERE symbol IN ({yer}) AND name IS NOT NULL AND name <> ''
                    ORDER BY id""", tuple(semboller)):
            out.setdefault(r["symbol"], r["name"])
        return out

    def _bag_yaz(self, nid, sembol, secim, guven, aday_sayisi, model, hata):
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO haber_bag (news_id, sembol, secim, guven, aday_sayisi,
                                          model, hata, ts)
                   VALUES (?,?,?,?,?,?,?, datetime('now'))
                   ON CONFLICT(news_id) DO UPDATE SET sembol = excluded.sembol,
                     secim = excluded.secim, guven = excluded.guven,
                     aday_sayisi = excluded.aday_sayisi, model = excluded.model,
                     hata = excluded.hata, ts = excluded.ts""",
                (nid, sembol, secim, guven, aday_sayisi, model, hata))

    def _olay_yaz(self, nid, sembol, tur, guven, olas, model, hata):
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO haber_olay (news_id, sembol, tur, guven, olasiliklar,
                                           model, hata, ts)
                   VALUES (?,?,?,?,?,?,?, datetime('now'))
                   ON CONFLICT(news_id, sembol) DO UPDATE SET tur = excluded.tur,
                     guven = excluded.guven, olasiliklar = excluded.olasiliklar,
                     model = excluded.model, hata = excluded.hata, ts = excluded.ts""",
                (nid, sembol, tur, guven, json.dumps(olas), model, hata))
