"""
BIST fiyat/tarihsel seri — Is Yatirim acik veri ucu.

Neden burasi: BIST icin login gerektirmeyen, JSON donen ve gunluk OHLC
veren en stabil kaynak. Ucun sozlesmesi degisirse `_fetch_via_browser`
fallback'i devreye girer (kalici oturumdaki gercek tarayici ustunden
ayni istegi atar; boylece UA/cerez/TLS parmak izi normal kullaniciyla ayni olur).
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import httpx

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

ENDPOINT = ("{base}/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"
            "?hisse={symbol}&startdate={start}&enddate={end}")

# JSON alan adlari -> kendi semamiz
FIELD_MAP = {
    "ts": "HGDG_TARIH",
    "close": "HGDG_KAPANIS",
    "high": "HGDG_MAX",
    "low": "HGDG_MIN",
    "volume": "HGDG_HACIM",
    "open": "HGDG_ACILIS",
}


def _tarih(ham) -> str | None:
    """
    Is Yatirim tarihi -> 'YYYY-MM-DD'.

    Iki bicim geliyor: '01-02-2025' (gun-ay-yil) ve
    '2025-02-01T00:00:00'. Once _normalize icinde gomuluydu; yan urunler
    de ayni donusume ihtiyac duyunca ortak fonksiyona cikarildi.
    """
    if not ham:
        return None
    ts = str(ham)[:10]
    if "-" in ts and len(ts.split("-")[0]) == 2:
        g, a, y = ts.split("-")
        return f"{y}-{a}-{g}"
    return ts


class IsYatirimCollector(BaseCollector):
    name = "isyatirim"
    needs_browser = False          # browser varsa fallback icin kullanilir

    def collect(self) -> CollectorResult:
        symbols = self._semboller()
        if not symbols:
            return CollectorResult(self.name, "skipped", 0, "izlenecek BIST sembolu yok")

        lookback = int(self.s.get("analysis.lookback_days", 250))
        end = date.today()
        tam_baslangic = end - timedelta(days=int(lookback * 1.6) + 10)

        # ARTIMLI CEKIM. Her gun her sembol icin 410 gunluk gecmisi
        # yeniden cekmek olculdu: 253 sembol = 17 dk 52 sn. Nabzin
        # ExitTimeOut'u 20 dk oldugu icin launchd isi OLDURURDU.
        # Elimizde bar olan sembolde yalnizca son gunler gerekiyor;
        # 5 gunluk ust uste binme, kacirilan gun ve duzeltmeler icin pay.
        mevcut = {r["symbol"]: r["son"] for r in self.db.query(
            """SELECT i.symbol, MAX(p.ts) son FROM prices p
               JOIN instruments i ON i.id = p.instrument_id
               WHERE i.venue = 'BIST' AND p.source = ?
               GROUP BY i.symbol""", (self.name,))}
        base = self.s.get("sources.isyatirim.base_url", "https://www.isyatirim.com.tr")

        # EN BAYAT ONCE. Sure butcesi dolarsa kuyrugun sonu cekilemez;
        # sabit sirada bu HER GUN AYNI sembolleri ac birakirdi. Bayatliga
        # gore siralayinca kesilen kuyruk her kosuda DEGISIR ve kapsam
        # kendi kendini dengeler. Hic bari olmayan sembol en one gecer.
        symbols = sorted(symbols, key=lambda x: mevcut.get(x) or "")

        # SURE BUTCESI — kosuyu KAYBETMEKTENSE eksik cekmek.
        #
        # Olculdu (2026-08-18): 338 sembolde medyan istek 0,61 sn ama
        # %29'u ~12 sn'ye takiliyor (sunucu kisitlamasi), toplam ~23,5
        # dakika. `run_hafif.sh` butcesi 20 dk ve asildiginda SUREC
        # GRUBUNU olduruyor — yani 17 Agustos'ta oldugu gibi `nabiz`
        # adimina HIC ULASILAMIYOR: sinyal yok, tez alarmi yok, risk
        # kontrolu yok.
        #
        # Bu yuzden sinir DISARIDAN degil ICERIDEN uygulaniyor: butce
        # dolunca collector duzgunce durur, `partial` doner ve kosunun
        # geri kalani CALISIR. Zaman asimi bir SONUCTUR, hata degil
        # (ayni ilke: bot/tools.py `_alt_surecte`).
        butce_sn = float(self.s.get("sources.isyatirim.azami_sure_sn", 780))
        baslangic = time.monotonic()
        kesildi = 0

        total, failed, tam_cekilen = 0, [], 0
        for i, sym in enumerate(symbols):
            if butce_sn > 0 and time.monotonic() - baslangic > butce_sn:
                kesildi = len(symbols) - i
                log.warning("[%s] sure butcesi (%.0f sn) doldu — %d sembol "
                            "bu kosuda atlandi (en bayat olanlar cekildi)",
                            self.name, butce_sn, kesildi)
                break
            son = mevcut.get(sym)
            if son:
                try:
                    start = date.fromisoformat(son[:10]) - timedelta(days=5)
                except ValueError:
                    start = tam_baslangic
            else:
                start = tam_baslangic
                tam_cekilen += 1
            url = ENDPOINT.format(
                base=base, symbol=sym,
                start=start.strftime("%d-%m-%Y"), end=end.strftime("%d-%m-%Y"),
            )
            if total or failed:
                time.sleep(0.2)          # 247 istegi arka arkaya atma
            rows = self._fetch(url, sym)
            if rows is None:
                failed.append(sym)
                continue
            iid = self.db.upsert_instrument(sym, "BIST", asset_type="equity", currency="TRY")
            total += self.db.upsert_prices(iid, rows, source=self.name,
                                           currency="TRY")
            # YAN URUNLER — ek istek gerektirmiyor, ayni cevabin icinde.
            try:
                total += self._yan_urunler(iid, getattr(self, "_son_ham", []) or [])
            except Exception as e:                   # noqa: BLE001
                log.warning("[%s] %s yan urunleri islenemedi: %s",
                            self.name, sym, e)

        # KESILME DE BIR SONUCTUR VE GORUNUR OLMALI. Sessizce eksik
        # donen bir collector "tam cekti" gibi okunur — bu projenin en
        # kotu hata sinifi (yanlis "yok" beyani) tam olarak boyle dogar.
        notlar = []
        if failed:
            notlar.append(f"cekilemeyen: {', '.join(failed)}")
        if kesildi:
            notlar.append(f"sure butcesi ({butce_sn:.0f} sn) doldu, "
                          f"{kesildi}/{len(symbols)} sembol atlandi")
        status = "ok" if not (failed or kesildi) else (
            "error" if len(failed) == len(symbols) else "partial")
        return CollectorResult(self.name, status, total,
                               " · ".join(notlar) or None)

    # ------------------------------------------------------------------
    def _semboller(self) -> list[str]:
        """
        Hangi BIST hisselerinin TARIHSEL serisi cekilecek.

        Once yalnizca `watchlist.bist` (10 sabit sembol) kullaniliyordu.
        Midas'in public sayfalarindan endeks uyeligi geldikten sonra
        artik BIST 30/50/100 bilesenleri VERITABANINDAN okunabiliyor —
        trend analizi icin gereken tarihsel seri boylece elle liste
        guncellemeden genisliyor.

        Kaynaklar birlestirilir: sabit liste ∪ secilen endeks(ler) ∪
        portfoydeki BIST pozisyonlari.
        """
        out = list(self.s.bist_watchlist or [])
        for endeks in (self.s.get("sources.isyatirim.indices") or []):
            out += [r["symbol"] for r in self.db.query(
                """SELECT i.symbol FROM index_members m
                   JOIN instruments i ON i.id = m.instrument_id
                   WHERE m.index_name = ? AND i.venue = 'BIST'""", (endeks,))]

        # LIKIDITE ESIGI. Katalogda 729 BIST kagidi var ama cogu gunde
        # birkac islem goruyor; onlarda teknik analiz gurultuden ibarettir
        # ve tarihsel serilerini cekmek bosa zaman.
        # Olculdu (2026-08-16, 625 kagit): medyan gunluk hacim 33M TL.
        # 50M esigi piyasanin daha likit yarisini aliyor (247 kagit).
        esik = float(self.s.get("sources.isyatirim.min_hacim_tl", 50_000_000))
        if esik > 0:
            out += [r["symbol"] for r in self.db.query(
                """SELECT i.symbol FROM fundamentals f
                   JOIN instruments i ON i.id = f.instrument_id
                   WHERE f.concept = 'GunlukHacimTL' AND i.venue = 'BIST'
                     AND f.val >= ?
                     AND f.period_end = (SELECT MAX(period_end) FROM fundamentals
                                         WHERE concept = 'GunlukHacimTL')""",
                (esik,))]
        out += [r["symbol"] for r in self.db.query(
            """SELECT DISTINCT i.symbol FROM positions p
               JOIN instruments i ON i.id = p.instrument_id
               WHERE i.venue = 'BIST'""")]
        gorulen, sirali = set(), []
        for s in out:
            s = (s or "").strip().upper()
            if s and s not in gorulen:
                gorulen.add(s)
                sirali.append(s)
        return sirali

    def _fetch(self, url: str, symbol: str) -> list[dict] | None:
        payload = self._fetch_via_httpx(url)
        if payload is None and self.browser is not None:
            log.info("[%s] %s icin tarayici fallback'i deneniyor", self.name, symbol)
            payload = self._fetch_via_browser(url)
        if payload is None:
            return None
        self._son_ham = payload      # yan urunler icin (endeks, kur, sermaye)
        return self._normalize(payload)

    def _fetch_via_httpx(self, url: str) -> list | None:
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/default.aspx",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            r = httpx.get(url, headers=headers, timeout=25.0, follow_redirects=True)
            r.raise_for_status()
            data = r.json()
        except Exception as e:                       # noqa: BLE001
            log.debug("httpx basarisiz: %s", e)
            return None
        return data.get("value") if isinstance(data, dict) else None

    def _fetch_via_browser(self, url: str) -> list | None:
        """
        Tarayici fallback'i — SURE SINIRLI.

        OLCULDU (2026-08-17 pulse.log): ucu ust uste geldiginde ogle
        kosusu 20 dakikalik butcesini doldurup SIGTERM ile olduruldu ve
        `nabiz` adimina hic ulasilamadi. Sinirsiz bir fallback, tek bir
        yavas sembolun tum kosuyu yutmasi demek.

        Playwright varsayilani gezinme ve `evaluate` icin AYRI AYRI 30
        sn'dir; ikisi ust uste binince tek sembol dakikalari yiyebiliyor.
        Ikisi de acikca sinirlaniyor: bir sembolu KACIRMAK, kosuyu
        kaybetmekten ucuzdur.
        """
        sinir_ms = int(self.s.get("sources.isyatirim.fallback_sn", 30)) * 1000
        try:
            with self.browser.page() as pg:
                pg.set_default_timeout(sinir_ms)
                pg.set_default_navigation_timeout(sinir_ms)
                pg.goto("https://www.isyatirim.com.tr/",
                        wait_until="domcontentloaded", timeout=sinir_ms)
                # SINIR JS ICINDE OLMAK ZORUNDA: `page.evaluate` bir
                # zaman asimi parametresi ALMAZ ve `set_default_timeout`
                # onu KAPSAMAZ — icerideki `fetch` asilirsa Python
                # tarafi sonsuza kadar bekler. Asil sizinti buydu.
                data = pg.evaluate(
                    """async ([u, ms]) => {
                         const c = new AbortController();
                         const t = setTimeout(() => c.abort(), ms);
                         try {
                           const r = await fetch(u, {
                             signal: c.signal,
                             headers: {'X-Requested-With':'XMLHttpRequest'}});
                           if (!r.ok) return null;
                           return await r.json();
                         } catch (e) {
                           return null;
                         } finally {
                           clearTimeout(t);
                         }
                       }""",
                    [url, sinir_ms],
                )
            return data.get("value") if isinstance(data, dict) else None
        except Exception as e:                       # noqa: BLE001
            log.debug("browser fallback basarisiz: %s", e)
            return None

    def _yan_urunler(self, instrument_id: int, ham: list) -> int:
        """
        Ayni cevaptan cikan UC ek veri. Ek istek YOK — bu alanlar zaten
        her hisse cagrisinda geliyordu, 31 alanin 24'u kullanilmiyordu.

        END_DEGER  -> XU100 endeks degeri. Ayni gunde TUM hisselerde
                      OZDES oldugu dogrulandi (endeks olmasinin kaniti).
                      BIST'te olay calismasinin piyasa modeline gecmesi
                      buna bagliydi; olculdu, BIST 30 hisselerinin gunluk
                      hareketinin ~%46'si piyasa genelinden geliyor.
        DD_DEGER   -> USD/TRY kuru. Alpha Vantage'dan da geliyor ama
                      oranin kotasi var, burasi sinirsiz.
        SERMAYE/PD -> hisse sayisi ve piyasa degeri. BIST tarafinda
                      F/K ve piyasa degeri bunlarsiz hesaplanamiyordu.
        """
        if not ham:
            return 0
        n = 0
        # --- XU100 ---
        endeks = []
        for x in ham:
            ts, deger = _tarih(x.get("HGDG_TARIH")), x.get("END_DEGER")
            if ts and deger:
                endeks.append({"ts": ts, "close": float(deger)})
        if endeks:
            xid = self.db.upsert_instrument("XU100", "INDEX", "BIST 100",
                                            "index", "TRY")
            n += self.db.upsert_prices(xid, endeks, self.name, currency="TRY")

        # --- USD/TRY ---
        kur = [(_tarih(x.get("HGDG_TARIH")), x.get("DD_DEGER")) for x in ham]
        kur = [(ts, float(v)) for ts, v in kur if ts and v]
        if kur:
            with self.db.tx() as c:
                c.executemany(
                    """INSERT INTO fx_rates (ts, base, quote, rate, source)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(ts, base, quote, source) DO UPDATE
                       SET rate=excluded.rate""",
                    [(ts, "USD", "TRY", v, self.name) for ts, v in kur])
            n += len(kur)

        # --- hisse sayisi / piyasa degeri (en guncel gun) ---
        son = max((x for x in ham if _tarih(x.get("HGDG_TARIH"))),
                  key=lambda x: _tarih(x["HGDG_TARIH"]), default=None)
        if son:
            an = _tarih(son["HGDG_TARIH"])
            satir = []
            for alan, kavram, birim in (("SERMAYE", "HisseSayisi", "adet"),
                                        ("PD", "PiyasaDegeri", "TRY"),
                                        ("HAO_PD", "HalkaAcikPiyasaDegeri", "TRY")):
                try:
                    satir.append((instrument_id, kavram, birim, None, an, None,
                                  float(son[alan]), self.name, None, None,
                                  None, an, None))
                except (KeyError, TypeError, ValueError):
                    continue
            if satir:
                n += self.db.upsert_fundamentals(satir)
        return n

    @staticmethod
    def _normalize(values: list) -> list[dict]:
        out: list[dict] = []
        for v in values or []:
            ts = _tarih(v.get(FIELD_MAP["ts"]))
            if not ts:
                continue
            close = v.get(FIELD_MAP["close"])
            if close in (None, 0):
                continue
            out.append({
                "ts": ts,
                "open": v.get(FIELD_MAP["open"]),
                "high": v.get(FIELD_MAP["high"]),
                "low": v.get(FIELD_MAP["low"]),
                "close": close,
                "volume": v.get(FIELD_MAP["volume"]),
            })
        out.sort(key=lambda r: r["ts"])
        return out
