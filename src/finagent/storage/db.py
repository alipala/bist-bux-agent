"""SQLite depolama katmani. Tum yazmalar idempotent (UPSERT)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

log = logging.getLogger(__name__)


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


# Yalnizca HUKUKI/KURUMSAL ekler atilir. Ayirt edici kelimeler KALIR:
# "Siemens", "Siemens Energy" ve "Siemens Healthineers" UC AYRI sirkettir.
# Ilk kelimeye bakan bir anahtar bunlari birlestirir ve ikisinin verisi
# kaybolur — olculdu, bu yuzden tum anlamli kelimeler kullaniliyor.
_AD_EKLERI = {"nv", "sa", "ag", "plc", "inc", "corp", "corporation", "ltd",
              "limited", "se", "holding", "holdings", "group", "groep",
              "the", "company", "co", "asa", "ab", "oyj", "spa", "class"}


def _ad_anahtari(ad: str | None) -> str:
    """
    Ayni sirketin farkli yazimlarini esitler, farkli sirketleri AYIRIR.

        'ASML' ~ 'ASML Holding'          -> 'asml'
        'ING'  ~ 'ING Group'             -> 'ing'
        'Siemens' vs 'Siemens Energy'    -> 'siemens' / 'siemensenergy'  (AYRI)
    """
    if not ad:
        return ""
    parcalar = [p for p in "".join(
        ch if ch.isalnum() else " " for ch in str(ad).casefold()).split() if p]
    # Tek harfli parcalar hukuki bicim kisaltmalarindan geliyor:
    # "Adyen N.V." -> [adyen, n, v]. Atilmazsa "Adyen" ile eslesmez.
    anlamli = [p for p in parcalar
               if p not in _AD_EKLERI and not (len(p) == 1 and p.isalpha())]
    return "".join(anlamli) or "".join(parcalar)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # Collector'lar AYRI SURECTE calisabiliyor (bot sohbetten
        # `veri_topla` cagirdiginda `run.py collect` alt surec olarak
        # baslatiliyor). WAL eszamanli okumaya izin verir ama yazma
        # kilidi tektir; beklemeden hata vermek yerine 15 sn bekle.
        self._conn.execute("PRAGMA busy_timeout = 15000")
        # WAL hizli ve eszamanli okumaya izin verir; bazi ag/FUSE dosya
        # sistemlerinde desteklenmez, o durumda sessizce DELETE moduna doneriz.
        try:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("SELECT 1").fetchone()
        except sqlite3.OperationalError:
            log.warning("WAL modu desteklenmiyor -> journal_mode=DELETE")
            self._conn.execute("PRAGMA journal_mode = DELETE")

    # ------------------------------------------------------------------
    def init_schema(self) -> None:
        sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        # Temizlik SEMADAN ONCE: schema.sql artik BENZERSIZ indeks kuruyor ve
        # kopyalar dururken indeks OLUSTURULAMAZ (IntegrityError). Yani goc
        # adimi semadan sonra calisamaz — once temizle, sonra kur.
        self._on_goc()
        self._conn.executescript(sql)
        self._migrate()
        self._conn.commit()
        log.info("Sema hazir: %s", self.path)

    def _migrate(self) -> None:
        """
        CREATE TABLE IF NOT EXISTS mevcut tabloya YENI KOLON eklemez.
        Sema buyudukce eski veritabanlari sessizce eksik kalir; burada
        kolonlari tek tek kontrol edip ekliyoruz (idempotent).
        """
        eklemeler = {
            "disclosures": [("source", "TEXT NOT NULL DEFAULT 'kap'")],
            "news": [("publisher", "TEXT"), ("tier", "INTEGER NOT NULL DEFAULT 0")],
            # Kripto kimligi: hangi Binance cifti, hangi CoinGecko coin'i.
            # SEC alanlari kriptoda anlamsiz, bu ikisi onlarin karsiligi.
            "identities": [("pair", "TEXT"), ("coingecko_id", "TEXT")],
            # Fiyat serisinin PARA BIRIMI. Yoklugu sahada su hataya yol
            # acti: Yahoo'dan gelen USD seri, EUR portfoy degerleriyle yan
            # yana kullanildi ve 17 pozisyonun 14'unde ~%15,7 (EUR/USD
            # kuru kadar) sapma olustu. Model "SMA50 = 206.52" derken bunun
            # hangi para biriminde oldugu BILINMIYORDU.
            "prices": [("currency", "TEXT")],
        }
        for tablo, kolonlar in eklemeler.items():
            mevcut = {r["name"] for r in self.query(f"PRAGMA table_info({tablo})")}
            for ad, tanim in kolonlar:
                if ad not in mevcut:
                    self._conn.execute(f"ALTER TABLE {tablo} ADD COLUMN {ad} {tanim}")
                    log.info("Sema guncellendi: %s.%s eklendi", tablo, ad)
        self._haber_kopyalarini_birlestir()

    def _on_goc(self) -> None:
        """Sema kurulmadan ONCE calismasi gereken temizlikler."""
        var = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fundamentals'")
        if var:
            self._anlik_finansal_kopyalarini_temizle()

    def _anlik_finansal_kopyalarini_temizle(self) -> None:
        """
        ANLIK (days IS NULL) finansal kayitlar her collector calismasinda
        yeniden ekleniyordu — PRIMARY KEY icindeki `days` NULL oldugu icin
        cakisma hic olusmuyordu (SQLite'ta NULL != NULL).

        Degerler ayni oldugu icin hicbir sayi yanlis cikmiyordu, ama
        `finansal_seri(donem="anlik")` ayni kalemi 10 kez donduruyordu ve
        model bunu "10 ayri kayit" diye okuyabilirdi. Ayrica benzersiz
        indeks kopyalar dururken OLUSTURULAMAZ.

        En son dosyalanan (filed) satir tutulur.
        """
        var = self.query(
            """SELECT COUNT(*) c FROM (
                   SELECT 1 FROM fundamentals
                   GROUP BY instrument_id, concept, period_end,
                            COALESCE(days,-1), form, unit
                   HAVING COUNT(*) > 1)""")[0]["c"]
        if not var:
            return
        with self.tx() as c:
            c.execute(
                """DELETE FROM fundamentals WHERE rowid NOT IN (
                       SELECT MAX(rowid) FROM fundamentals
                       GROUP BY instrument_id, concept, period_end,
                                COALESCE(days,-1), form, unit)""")
            silinen = c.total_changes
        log.info("Anlik finansal kopyalari temizlendi: %d grup, ~%d satir",
                 var, silinen)

    def _haber_kopyalarini_birlestir(self) -> None:
        """
        Haber kimligi URL'den ICERIGE tasindi (bkz. _haber_anahtari). Eskiden
        yazilmis kayitlarda ayni makale iki satirda durabiliyor: biri
        news.google.com yonlendirmesi, digeri cozulmus yayinci linki.
        Birlestirmezsek olay-etki analizi ayni olayi iki kez sayar.
        """
        kopya = self.query(
            """SELECT title, substr(published_at,1,10) g, COUNT(*) n
               FROM news WHERE title IS NOT NULL AND title <> ''
               GROUP BY title, g HAVING n > 1""")
        if not kopya:
            return
        silinen = 0
        with self.tx() as c:
            for k in kopya:
                satirlar = c.execute(
                    """SELECT id, url, symbols, tier FROM news
                       WHERE title = ? AND substr(published_at,1,10) = ?
                       ORDER BY (url LIKE '%news.google.com%'), id""",
                    (k["title"], k["g"])).fetchall()
                tut = satirlar[0]                     # yonlendirme olmayan kazanir
                semboller = set()
                kademe = 0
                for s in satirlar:
                    semboller.update(x for x in (s["symbols"] or "").split(",") if x)
                    kademe = max(kademe, s["tier"] or 0)
                c.execute("UPDATE news SET symbols=?, tier=? WHERE id=?",
                          (",".join(sorted(semboller)), kademe, tut["id"]))
                for s in satirlar[1:]:
                    c.execute("DELETE FROM news WHERE id=?", (s["id"],))
                    silinen += 1
        log.info("Haber kopyalari birlestirildi: %d satir silindi", silinen)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        self._conn.close()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------------
    def upsert_instrument(
        self, symbol: str, venue: str, name: str | None = None,
        asset_type: str | None = None, currency: str | None = None,
        isin: str | None = None,
    ) -> int:
        symbol, venue = symbol.strip().upper(), venue.strip().upper()
        with self.tx() as c:
            c.execute(
                """INSERT INTO instruments (symbol, venue, name, asset_type, currency, isin)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(symbol, venue) DO UPDATE SET
                     name       = COALESCE(excluded.name, instruments.name),
                     asset_type = COALESCE(excluded.asset_type, instruments.asset_type),
                     currency   = COALESCE(excluded.currency, instruments.currency),
                     isin       = COALESCE(excluded.isin, instruments.isin)""",
                (symbol, venue, name, asset_type, currency, isin),
            )
            row = c.execute(
                "SELECT id FROM instruments WHERE symbol=? AND venue=?", (symbol, venue)
            ).fetchone()
        return int(row["id"])

    def upsert_prices(self, instrument_id: int, rows: Iterable[dict], source: str,
                      currency: str | None = None) -> int:
        """
        `currency` ZORUNLU DEGIL ama VERILMELI. Yoklugu sahada su hataya
        yol acti: Yahoo'nun USD serisi EUR portfoy degerleriyle yan yana
        kullanildi ve her seviye yanlis para biriminde cikti.
        """
        payload = [
            (instrument_id, r["ts"], r.get("open"), r.get("high"), r.get("low"),
             r.get("close"), r.get("volume"), source, r.get("currency") or currency)
            for r in rows
        ]
        if not payload:
            return 0
        with self.tx() as c:
            c.executemany(
                """INSERT INTO prices
                   (instrument_id, ts, open, high, low, close, volume, source, currency)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(instrument_id, ts, source) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume,
                     currency=COALESCE(excluded.currency, prices.currency)""",
                payload,
            )
        return len(payload)

    def fiyat_kaynagi(self, instrument_id: int) -> dict | None:
        """
        Bir enstruman icin KULLANILACAK TEK fiyat kaynagini secer.

        NEDEN SART: `prices` ayni enstruman icin birden fazla kaynak
        tutabiliyor ve bunlar FARKLI PARA BIRIMINDE olabiliyor. ASML'de
        hem Yahoo (USD 1844) hem Alpha Vantage (EUR 1579.60) serisi var.
        Kaynak filtresi olmayan bir sorgu ikisini KARISTIRIR ve SMA/RSI
        birbirine karismis iki para biriminden hesaplanir — sayi uretilir,
        hepsi yanlis cikar, hicbiri hata vermez.

        Secim kurali: pozisyonun para birimiyle ESLESEN kaynak kazanir
        (kullanicinin ekraninda gordugu para birimi odur). Eslesme yoksa
        en cok barli kaynak.
        """
        kaynaklar = self.query(
            """SELECT source, currency, COUNT(*) bar, MAX(ts) son
               FROM prices WHERE instrument_id = ?
               GROUP BY source, currency""", (instrument_id,))
        if not kaynaklar:
            return None
        if len(kaynaklar) == 1:
            return dict(kaynaklar[0])
        poz = self.query(
            """SELECT currency FROM positions WHERE instrument_id = ?
               ORDER BY snapshot_ts DESC LIMIT 1""", (instrument_id,))
        hedef = (poz[0]["currency"] if poz else None) or ""
        eslesen = [k for k in kaynaklar if (k["currency"] or "") == hedef]
        sirali = sorted(eslesen or kaynaklar, key=lambda k: -k["bar"])
        return dict(sirali[0])

    def fiyat_serisi(self, instrument_id: int, limit: int = 300) -> list:
        """
        TEK kaynaktan gunluk seri, ARTAN tarih sirali. Teknik analizin
        girdisi burasi olmali — dogrudan `prices` sorgulamak para birimi
        karistirir (bkz. fiyat_kaynagi).
        """
        k = self.fiyat_kaynagi(instrument_id)
        if not k:
            return []
        return self.query(
            """SELECT * FROM (
                   SELECT ts, open, high, low, close, volume, currency, source
                   FROM prices WHERE instrument_id = ? AND source = ?
                   ORDER BY ts DESC LIMIT ?
               ) ORDER BY ts ASC""", (instrument_id, k["source"], limit))

    def piyasa_vekili(self, instrument_id: int) -> dict | None:
        """
        Bir enstrumanin PIYASA VEKILI serisini bulur (olay calismasindaki
        alfa/beta modeli icin).

        Eslesme PARA BIRIMINE gore: piyasa modeli enstruman getirisini
        piyasa getirisine regresyon eder. Ikisi farkli para biriminde
        olursa beta kur hareketini de icine ceker ve anormal getiri kur
        gurultusuyle kirlenir.

            EUR  -> AEX          (Amsterdam kotasyonlari)
            USD  -> QQQ          (ABD kotasyonlari; portfoy tekno agirlikli)
            USDT -> BTC          (kripto beta'si standart olarak BTC'ye olcuur)
            TRY  -> yok          (XU100 serisi henuz toplanmiyor)

        Enstrumanin KENDISI vekilse None doner — kendine regresyon
        anlamsiz olurdu (beta=1, anormal getiri her zaman 0).
        """
        k = self.fiyat_kaynagi(instrument_id)
        if not k:
            return None
        kendisi = self.query(
            "SELECT symbol, venue FROM instruments WHERE id = ?", (instrument_id,))
        if kendisi and kendisi[0]["venue"] == "INDEX":
            return None
        ccy = (k["currency"] or "").upper()
        hedef = {"EUR": ("AEX", "INDEX"), "USD": ("QQQ", "INDEX"),
                 "USDT": ("BTC", "BINANCE")}.get(ccy)
        if not hedef:
            return None
        sembol, venue = hedef
        if kendisi and kendisi[0]["symbol"] == sembol:
            return None                    # BTC'nin vekili BTC olamaz
        r = self.query(
            "SELECT id FROM instruments WHERE symbol=? AND venue=? LIMIT 1",
            (sembol, venue))
        if not r:
            return None
        return {"instrument_id": r[0]["id"], "sembol": sembol,
                "para_birimi": ccy}

    def fx_kuru(self, base: str, quote: str, ts: str | None = None) -> dict | None:
        """
        1 <base> kac <quote> eder. Tarih verilirse O TARIHTEN ONCEKI en yakin
        kur (ileriye bakmak gelecek bilgisi sizdirir), yoksa en guncel.

        Ters cift de denenir: EUR/USD yoksa USD/EUR'un tersi kullanilir.
        """
        base, quote = base.upper(), quote.upper()
        if base == quote:
            return {"base": base, "quote": quote, "rate": 1.0, "ts": ts, "kaynak": "ayni"}
        kosul = "AND ts <= ?" if ts else ""
        par = (base, quote) + ((ts,) if ts else ())
        r = self.query(f"""SELECT ts, rate, source FROM fx_rates
                           WHERE base=? AND quote=? {kosul}
                           ORDER BY ts DESC LIMIT 1""", par)
        if r:
            return {"base": base, "quote": quote, "rate": r[0]["rate"],
                    "ts": r[0]["ts"], "kaynak": r[0]["source"]}
        par = (quote, base) + ((ts,) if ts else ())
        r = self.query(f"""SELECT ts, rate, source FROM fx_rates
                           WHERE base=? AND quote=? {kosul}
                           ORDER BY ts DESC LIMIT 1""", par)
        if r and r[0]["rate"]:
            return {"base": base, "quote": quote, "rate": 1.0 / r[0]["rate"],
                    "ts": r[0]["ts"], "kaynak": r[0]["source"] + " (ters cevrildi)"}
        return None

    def upsert_prices_hourly(self, instrument_id: int, rows: Iterable[dict],
                             source: str) -> int:
        """
        Saatlik barlar AYRI tabloya yazilir — `prices` ile karistirilmaz.
        Gerekcesi schema.sql'de: gunluk varsayan tum hesaplar bozulurdu.
        """
        payload = [
            (instrument_id, r["ts"], r.get("open"), r.get("high"), r.get("low"),
             r.get("close"), r.get("volume"), r.get("quote_volume"),
             r.get("trades"), source)
            for r in rows
        ]
        if not payload:
            return 0
        with self.tx() as c:
            c.executemany(
                """INSERT INTO prices_hourly
                   (instrument_id, ts, open, high, low, close, volume,
                    quote_volume, trades, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(instrument_id, ts, source) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume,
                     quote_volume=excluded.quote_volume, trades=excluded.trades""",
                payload,
            )
        return len(payload)

    def saatlik_seri(self, instrument_id: int, limit: int = 168) -> list[sqlite3.Row]:
        """Son N saatlik bar, ARTAN tarih sirali (varsayilan 7 gun)."""
        return self.query(
            """SELECT * FROM (
                   SELECT ts, open, high, low, close, volume, quote_volume, trades
                   FROM prices_hourly WHERE instrument_id = ?
                   ORDER BY ts DESC LIMIT ?
               ) ORDER BY ts ASC""", (instrument_id, limit))

    def insert_positions(self, account: str, snapshot_ts: str, rows: Iterable[dict]) -> int:
        n = 0
        with self.tx() as c:
            for r in rows:
                iid = self.upsert_instrument(
                    r["symbol"], account.upper(), r.get("name"),
                    r.get("asset_type"), r.get("currency")
                )
                c.execute(
                    """INSERT INTO positions
                       (snapshot_ts, account, instrument_id, quantity, avg_cost,
                        last_price, market_value, pnl_abs, pnl_pct, currency)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(snapshot_ts, account, instrument_id) DO UPDATE SET
                         quantity=excluded.quantity, avg_cost=excluded.avg_cost,
                         last_price=excluded.last_price, market_value=excluded.market_value,
                         pnl_abs=excluded.pnl_abs, pnl_pct=excluded.pnl_pct""",
                    (snapshot_ts, account.lower(), iid, r.get("quantity"), r.get("avg_cost"),
                     r.get("last_price"), r.get("market_value"), r.get("pnl_abs"),
                     r.get("pnl_pct"), r.get("currency")),
                )
                n += 1
        return n

    def upsert_disclosures(self, rows: Iterable[dict], source: str = "kap") -> int:
        payload = [
            (r.get("id") or sha1(r.get("url", "") + r.get("title", "")),
             r.get("published_at"), r.get("symbol"), r.get("company"),
             r.get("category"), r.get("title"), r.get("url"), r.get("body"), source)
            for r in rows
        ]
        if not payload:
            return 0
        with self.tx() as c:
            c.executemany(
                """INSERT INTO disclosures
                   (id, published_at, symbol, company, category, title, url, body, source)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     body=COALESCE(excluded.body, disclosures.body)""",
                payload,
            )
        return len(payload)

    # --- temel veri (XBRL) ----------------------------------------------
    def upsert_fundamentals(self, rows) -> int:
        rows = list(rows)
        if not rows:
            return 0
        with self.tx() as c:
            c.executemany(
                """INSERT INTO fundamentals
                   (instrument_id, concept, unit, period_start, period_end, days,
                    val, form, fy, fp, frame, filed, accn)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(instrument_id, concept, period_end, COALESCE(days,-1), form, unit)
                   DO UPDATE SET val=excluded.val, frame=COALESCE(excluded.frame, fundamentals.frame)""",
                [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9],
                  r[10], r[11], r[12]) for r in rows],
            )
        return len(rows)

    def finansal_seri(self, instrument_id: int, kavramlar: list[str],
                      donem: str = "yillik", limit: int = 6) -> list[sqlite3.Row]:
        """
        Karsilastirilabilir donem serisi dondurur.

        `donem`:
          "yillik"  -> 350-380 gunluk kayitlar (yillik)
          "ceyrek"  -> 80-100 gunluk kayitlar (ceyregin KENDISI, kumulatif degil)
          "anlik"   -> bilanco kalemleri (days IS NULL)

        Gun bandi filtresi SART: ayni kavram ayni dosyalamada hem 6 aylik hem
        3 aylik geliyor; filtresiz sorgu ikisini karistirir ve gelir/kar
        karsilastirmasi tamamen yanlis cikar.
        """
        yer = ",".join("?" * len(kavramlar))
        if donem == "anlik":
            kosul = "f.days IS NULL"
        elif donem == "ceyrek":
            kosul = "f.days BETWEEN 80 AND 100"
        else:
            kosul = "f.days BETWEEN 350 AND 380"
        return self.query(
            f"""SELECT f.concept, f.val, f.unit, f.period_start, f.period_end,
                       f.days, f.form, f.fy, f.fp, f.frame
                FROM fundamentals f
                WHERE f.instrument_id = ? AND f.concept IN ({yer}) AND {kosul}
                ORDER BY f.period_end DESC LIMIT ?""",
            (instrument_id, *kavramlar, limit),
        )

    def finansal_ozet(self, instrument_id: int) -> dict:
        """
        Bir enstrumanin en son yillik + ceyreklik + bilanco anlik goruntusu.
        Kavram adlari Turkcelestirilir; gelir icin uc alternatif etiketten
        dolu olani kullanilir.
        """
        from ..collectors.xbrl import KAVRAMLAR
        kavramlar = list(KAVRAMLAR)

        def topla(donem: str, n: int) -> list[dict]:
            gruplar: dict[str, dict] = {}
            for r in self.finansal_seri(instrument_id, kavramlar, donem, limit=400):
                anahtar = r["period_end"]
                g = gruplar.setdefault(anahtar, {
                    "donem_sonu": r["period_end"], "donem_basi": r["period_start"],
                    "gun": r["days"], "form": r["form"], "mali_yil": r["fy"],
                    "ceyrek": r["fp"], "takvim": r["frame"], "kalemler": {},
                })
                etiket = KAVRAMLAR[r["concept"]]
                # Gelir icin birden fazla kavram var; ilk doleni tut.
                g["kalemler"].setdefault(etiket, {"deger": r["val"], "birim": r["unit"]})
            return sorted(gruplar.values(), key=lambda x: x["donem_sonu"],
                          reverse=True)[:n]

        return {
            "yillik": topla("yillik", 3),
            "ceyreklik": topla("ceyrek", 4),
            "bilanco": topla("anlik", 2),
            "not": ("Degerler sirketin SEC'e dosyaladigi XBRL'den birebir alindi. "
                    "Donem uzunlugu 'gun' alaninda; farkli uzunluktaki donemler "
                    "KARSILASTIRILMAZ."),
        }

    def finansal_kapsam(self) -> list[sqlite3.Row]:
        return self.query(
            """SELECT i.symbol, i.name, COUNT(*) n, MAX(f.period_end) son
               FROM fundamentals f JOIN instruments i ON i.id = f.instrument_id
               GROUP BY i.symbol ORDER BY i.symbol""")

    # --- arastirma ------------------------------------------------------
    def research_targets(self, kripto: bool | None = False) -> list[sqlite3.Row]:
        """
        Arastirilacak enstrumanlar = guncel portfoy pozisyonlari ∪ izleme listesi.

        Nakit haric tutulur: arastirilacak bir sirketi yok.

        KRIPTO VARSAYILAN OLARAK HARIC (`kripto=False`). Sebep: bu fonksiyonu
        cagiran hisse collector'lari (edgar, xbrl, prices, stocknews) kripto
        sembolunu alirsa YANLIS VERI ceker — "Bitcoin" SEC'de aranir, BTC
        Yahoo'da baska bir enstrumana denk gelir. Kripto collector'lari
        `kripto=True` ile yalnizca kendi evrenini alir.
        `kripto=None` ikisini birden dondurur (portfoy ozeti gibi yerler icin).

        AYNI SIRKET IKI KEZ TARANMAZ. Katalog ve ekran goruntusu ayni sirketi
        farkli sembolle kaydedebiliyor (ASML / ASML.AS, ADYEN / ADYEN.AS);
        ikisi de hedef olsaydi ayni EDGAR ve haber sorgusu iki kez calisir,
        rapora da ayni gelisme iki farkli sembolle girerdi.
        """
        filtre = {False: "AND i.venue <> 'BINANCE'",
                  True: "AND i.venue = 'BINANCE'",
                  None: ""}[kripto]
        rows = self._research_targets_ham(filtre)
        gorulen: dict[str, sqlite3.Row] = {}
        for r in rows:
            anahtar = _ad_anahtari(r["name"]) or r["symbol"].upper()
            onceki = gorulen.get(anahtar)
            if onceki is None:
                gorulen[anahtar] = r
                continue
            # Sonekli katalog sembolu yerine pozisyonda kullanilani tut:
            # portfoy kayitlari ve K/Z gecmisi ona bagli.
            if "." in onceki["symbol"] and "." not in r["symbol"]:
                gorulen[anahtar] = r
        return list(gorulen.values())

    def _research_targets_ham(self, kripto_filtresi: str = "") -> list[sqlite3.Row]:
        return self.query(f"""
            SELECT DISTINCT i.id, i.symbol, i.name, i.asset_type, i.venue
            FROM instruments i
            WHERE i.asset_type IS NOT 'cash' AND i.symbol <> 'CASH'
              {kripto_filtresi} AND (
                i.id IN (
                    SELECT p.instrument_id FROM positions p
                    WHERE p.snapshot_ts = (SELECT MAX(snapshot_ts) FROM positions
                                           WHERE account = p.account)
                )
                OR i.id IN (SELECT instrument_id FROM watchlist)
            )
            ORDER BY i.symbol
        """)

    def save_identity(self, instrument_id: int, kimlik) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO identities
                   (instrument_id, cik, sec_ticker, sec_name, exchange, ir_url,
                    status, method, note, resolved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(instrument_id) DO UPDATE SET
                     cik=excluded.cik, sec_ticker=excluded.sec_ticker,
                     sec_name=excluded.sec_name, exchange=excluded.exchange,
                     ir_url=COALESCE(identities.ir_url, excluded.ir_url),
                     status=excluded.status, method=excluded.method,
                     note=excluded.note, resolved_at=excluded.resolved_at""",
                (instrument_id, kimlik.cik, kimlik.sec_ticker, kimlik.sec_name,
                 kimlik.exchange, kimlik.ir_url, kimlik.status, kimlik.method,
                 kimlik.note),
            )

    def save_crypto_identity(self, instrument_id: int, k: dict) -> bool:
        """
        Kripto kimligini yazar. ELLE atanmis kimligi EZMEZ.

        Sebep hisse tarafinda olculdu: EDGAR her calismada kimligi yeniden
        cozup kullanicinin /kimlik ile yaptigi duzeltmeyi siliyordu. Ayni
        hata burada tekrarlanmasin.
        """
        mevcut = self.query(
            "SELECT method FROM identities WHERE instrument_id = ?", (instrument_id,))
        if mevcut and mevcut[0]["method"] == "elle":
            return False
        with self.tx() as c:
            c.execute(
                """INSERT INTO identities
                   (instrument_id, sec_ticker, sec_name, exchange, status,
                    method, note, pair, coingecko_id, resolved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(instrument_id) DO UPDATE SET
                     sec_name=excluded.sec_name, exchange=excluded.exchange,
                     status=excluded.status, method=excluded.method,
                     note=excluded.note, pair=excluded.pair,
                     coingecko_id=excluded.coingecko_id,
                     resolved_at=excluded.resolved_at""",
                (instrument_id, k.get("symbol"), k.get("name"), "BINANCE",
                 k.get("status"), "kripto", k.get("note"),
                 k.get("pair"), k.get("coingecko_id")),
            )
        return True

    def identities(self, status: str | None = None) -> list[sqlite3.Row]:
        sql = """SELECT i.symbol, i.name, i.asset_type, d.*
                 FROM identities d JOIN instruments i ON i.id = d.instrument_id"""
        if status:
            return self.query(sql + " WHERE d.status = ? ORDER BY i.symbol", (status,))
        return self.query(sql + " ORDER BY i.symbol")

    def add_index_member(self, instrument_id: int, index_name: str) -> None:
        with self.tx() as c:
            c.execute("""INSERT INTO index_members (instrument_id, index_name)
                         VALUES (?, ?) ON CONFLICT DO NOTHING""",
                      (instrument_id, index_name))

    def index_summary(self) -> list[sqlite3.Row]:
        return self.query("""SELECT index_name, COUNT(*) n FROM index_members
                             GROUP BY index_name ORDER BY n DESC""")

    def search_catalog(self, term: str = "", index_name: str | None = None,
                       limit: int = 25) -> list[sqlite3.Row]:
        """Katalog aramasi: hisse + ETF, istege bagli endeks filtresi."""
        like = f"%{term.strip()}%"
        sql = """SELECT DISTINCT i.symbol, i.name, i.asset_type, i.isin,
                        (SELECT GROUP_CONCAT(m.index_name, ', ')
                         FROM index_members m WHERE m.instrument_id = i.id) AS endeksler
                 FROM instruments i
                 LEFT JOIN index_members m2 ON m2.instrument_id = i.id
                 WHERE i.venue = 'BUX'"""
        params: list = []
        if index_name:
            sql += " AND m2.index_name = ?"
            params.append(index_name)
        if term.strip():
            sql += " AND (i.name LIKE ? OR i.symbol LIKE ?)"
            params += [like, like]
        sql += " ORDER BY i.name LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    def add_watchlist(self, instrument_id: int, note: str | None = None) -> None:
        with self.tx() as c:
            c.execute("""INSERT INTO watchlist (instrument_id, kind, note)
                         VALUES (?, 'aday', ?)
                         ON CONFLICT(instrument_id) DO UPDATE SET note=excluded.note""",
                      (instrument_id, note))

    def watchlist(self) -> list[sqlite3.Row]:
        return self.query("""SELECT i.symbol, i.name, i.asset_type, w.added_at, w.note
                             FROM watchlist w JOIN instruments i ON i.id = w.instrument_id
                             ORDER BY i.name""")

    def budama(self, haber_gun: int = 90, bildirim_gun: int = 365,
               snapshot_sayisi: int = 30) -> dict:
        """
        Eski kayitlari siler. Analize giren pencerelerden COK daha genis
        esikler kullanir (haber 7 gun, bildirim 30 gun okunuyor) — amac disk
        buyumesini durdurmak, veri kaybetmek degil.

        Pozisyon anlik goruntuleri hesap basina korunur: portfoy gecmisi
        ilerideki getiri analizinin tek kaynagi, kolay silinmemeli.
        """
        ozet = {}
        with self.tx() as c:
            ozet["haber"] = c.execute(
                "DELETE FROM news WHERE published_at < datetime('now', ?)",
                (f"-{haber_gun} days",)).rowcount
            ozet["bildirim"] = c.execute(
                "DELETE FROM disclosures WHERE published_at < datetime('now', ?)",
                (f"-{bildirim_gun} days",)).rowcount

            silinen_snapshot = 0
            for hesap in ("bux", "midas"):
                tutulacak = [r["snapshot_ts"] for r in self.query(
                    """SELECT DISTINCT snapshot_ts FROM positions WHERE account=?
                       ORDER BY snapshot_ts DESC LIMIT ?""", (hesap, snapshot_sayisi))]
                if not tutulacak:
                    continue
                yer = ",".join("?" * len(tutulacak))
                silinen_snapshot += c.execute(
                    f"DELETE FROM positions WHERE account=? AND snapshot_ts NOT IN ({yer})",
                    (hesap, *tutulacak)).rowcount
            ozet["pozisyon"] = silinen_snapshot

        # VACUUM transaction icinde calismaz.
        self._conn.execute("VACUUM")
        return ozet

    def recent_disclosures_by_source(self, source: str, hours: int = 720,
                                     limit: int = 60) -> list[sqlite3.Row]:
        return self.query(
            """SELECT * FROM disclosures
               WHERE source = ? AND published_at >= datetime('now', ?)
               ORDER BY published_at DESC LIMIT ?""",
            (source, f"-{hours} hours", limit),
        )

    @staticmethod
    def _haber_anahtari(r: dict) -> str:
        """
        Haberin kimligi URL DEGIL, ICERIKTIR: baslik + yayin gunu.

        Neden: ayni makale once news.google.com yonlendirme linkiyle, sonra
        cozulmus yayinci linkiyle gelebiliyor. URL'i anahtar yapmak ayni
        makaleyi IKI KEZ kaydediyordu — olay-etki analizi ayni olayi iki
        kez sayip sahte tekrar uretiyordu.
        """
        baslik = re.sub(r"\W+", " ", (r.get("title") or "").lower()).strip()
        if not baslik:
            return sha1(r["url"])          # basliksiz haberde URL'den baskasi yok
        return sha1(f"{baslik}|{(r.get('published_at') or '')[:10]}")

    def upsert_news(self, rows: Iterable[dict]) -> int:
        payload = [
            (self._haber_anahtari(r), r.get("published_at"), r.get("source"), r.get("title"),
             r.get("url"), r.get("summary"), ",".join(r.get("symbols", []) or []),
             r.get("publisher"), int(r.get("tier") or 0))
            for r in rows if r.get("url")
        ]
        if not payload:
            return 0
        with self.tx() as c:
            c.executemany(
                """INSERT INTO news
                   (id, published_at, source, title, url, summary, symbols, publisher, tier)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     symbols = CASE
                       WHEN instr(',' || news.symbols || ',', ',' || excluded.symbols || ',') > 0
                       THEN news.symbols
                       ELSE news.symbols || ',' || excluded.symbols END,
                     publisher = COALESCE(excluded.publisher, news.publisher),
                     tier      = MAX(excluded.tier, news.tier),
                     -- Cozulmus yayinci linki, yonlendirme linkini EZER.
                     url = CASE WHEN excluded.url LIKE '%news.google.com%'
                                THEN news.url ELSE excluded.url END""",
                payload,
            )
        return len(payload)

    def log_collector_run(self, collector: str, status: str, rows: int,
                          duration_ms: int, error: str | None = None) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO collector_runs
                   (run_ts, collector, status, rows_written, duration_ms, error)
                   VALUES (?,?,?,?,?,?)""",
                (utcnow(), collector, status, rows, duration_ms, error),
            )

    def log_analysis_run(self, model: str, scope: str, input_stats: dict,
                         output_md: str, status: str, error: str | None = None) -> int:
        with self.tx() as c:
            cur = c.execute(
                """INSERT INTO analysis_runs
                   (run_ts, model, scope, input_stats, output_md, status, error)
                   VALUES (?,?,?,?,?,?,?)""",
                (utcnow(), model, scope, json.dumps(input_stats, ensure_ascii=False),
                 output_md, status, error),
            )
        return int(cur.lastrowid)

    # ------------------------------------------------------------------
    # Okuma yardimcilari
    def price_history(self, symbol: str, venue: str = "BIST", limit: int = 400) -> list[sqlite3.Row]:
        return self.query(
            """SELECT p.ts, p.open, p.high, p.low, p.close, p.volume
               FROM prices p JOIN instruments i ON i.id = p.instrument_id
               WHERE i.symbol=? AND i.venue=?
               ORDER BY p.ts DESC LIMIT ?""",
            (symbol.upper(), venue.upper(), limit),
        )

    def latest_snapshot_ts(self, account: str) -> str | None:
        row = self.query(
            "SELECT MAX(snapshot_ts) AS ts FROM positions WHERE account = ?",
            (account.lower(),),
        )
        return row[0]["ts"] if row and row[0]["ts"] else None

    def snapshot_positions(self, account: str, snapshot_ts: str) -> dict[str, float | None]:
        """sembol -> piyasa degeri (o anlik goruntudeki)."""
        rows = self.query(
            """SELECT i.symbol AS s, p.market_value AS v
               FROM positions p JOIN instruments i ON i.id = p.instrument_id
               WHERE p.account = ? AND p.snapshot_ts = ?""",
            (account.lower(), snapshot_ts),
        )
        return {r["s"]: r["v"] for r in rows}

    def snapshot_symbol_names(self, account: str, snapshot_ts: str) -> dict[str, str | None]:
        """sembol -> enstruman adi (sembol hizalamasi icin)."""
        rows = self.query(
            """SELECT i.symbol AS s, i.name AS n
               FROM positions p JOIN instruments i ON i.id = p.instrument_id
               WHERE p.account = ? AND p.snapshot_ts = ?""",
            (account.lower(), snapshot_ts),
        )
        return {r["s"]: r["n"] for r in rows}

    def snapshot_value(self, account: str, snapshot_ts: str) -> float:
        row = self.query(
            """SELECT COALESCE(SUM(market_value), 0) AS v FROM positions
               WHERE account = ? AND snapshot_ts = ?""",
            (account.lower(), snapshot_ts),
        )
        return float(row[0]["v"]) if row else 0.0

    def delete_snapshot(self, account: str, snapshot_ts: str) -> int:
        with self.tx() as c:
            cur = c.execute("DELETE FROM positions WHERE account=? AND snapshot_ts=?",
                            (account.lower(), snapshot_ts))
        return cur.rowcount

    def search_instruments(self, venue: str, term: str = "", limit: int = 25) -> list[sqlite3.Row]:
        like = f"%{term.strip()}%"
        return self.query(
            """SELECT symbol, name, asset_type, isin FROM instruments
               WHERE venue = ? AND (? = '' OR name LIKE ? OR symbol LIKE ?)
               ORDER BY name LIMIT ?""",
            (venue.upper(), term.strip(), like, like, limit),
        )

    def count_instruments(self, venue: str) -> int:
        return int(self.query("SELECT COUNT(*) c FROM instruments WHERE venue=?",
                              (venue.upper(),))[0]["c"])

    def latest_positions(self, account: str) -> list[sqlite3.Row]:
        return self.query(
            """SELECT i.symbol, i.name, i.currency, p.*
               FROM positions p JOIN instruments i ON i.id = p.instrument_id
               WHERE p.account = ?
                 AND p.snapshot_ts = (SELECT MAX(snapshot_ts) FROM positions WHERE account = ?)
               ORDER BY p.market_value DESC""",
            (account.lower(), account.lower()),
        )

    def recent_news(self, hours: int = 36, limit: int = 60) -> list[sqlite3.Row]:
        return self.query(
            """SELECT * FROM news
               WHERE published_at >= datetime('now', ?)
               ORDER BY published_at DESC LIMIT ?""",
            (f"-{hours} hours", limit),
        )

    def recent_disclosures(self, hours: int = 48, limit: int = 60) -> list[sqlite3.Row]:
        return self.query(
            """SELECT * FROM disclosures
               WHERE published_at >= datetime('now', ?)
               ORDER BY published_at DESC LIMIT ?""",
            (f"-{hours} hours", limit),
        )
