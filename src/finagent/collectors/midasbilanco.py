"""
BIST bilancosu ve gelir tablosu — Midas hisse detay sayfasindan.

NEDEN AYRI COLLECTOR
--------------------
Tarayici gerektiriyor: satir adlari HTML'de ama DEGERLER JS ile geliyor
(ham HTML'de "0,00%" yer tutucusu var). `midas` collector'u httpx ile
hizli calisiyor ve oyle kalmali; bilanco ayri, tarayicili grupta.

NEDEN BIST'TE BU VERI KRITIK
----------------------------
XBRL yalnizca SEC'e tabi sirketleri kapsiyor; BIST sirketleri orada YOK.
Yani BIST'te elimizde sadece F/K ve PD/DD gibi ANLIK oranlar vardi.
Olculdu (THYAO): F/K 3,79 "cok ucuz" diyor, ama gelir tablosu 2026 ilk
yarisinda ESAS FAALIYET ZARARI gosteriyor (-5,1 mlr TL) ve 18,9 mlr TL
net karin tamami faaliyet DISI. Oran tek basina bunu soylemiyor.

Turkiye'de bu istisna degil kural: 2023'ten beri enflasyon muhasebesi
(TMS-29) uygulandigi icin net kar parasal kazanc/kayip iceriyor ve bu
isletme performansi degildir.

IKI TUZAK — IKISI DE KAYDEDILIYOR
---------------------------------
1. DONEM UZUNLUGU. Gelir tablosu kalemleri KUMULATIF yil-basindan-bugune:
   2026-03 = 3 ay, 2026-06 = 6 ay, 2025-12 = 12 ay. "Son degeri al,
   onceki yilla karsilastir" demek 6 ayi 12 ayla karsilastirmaktir ve
   "kar %84 dustu" gibi tamamen yanlis sonuc uretir — XBRL'de bir kez
   dustugumuz tuzagin ayni. Bu yuzden `days` HER kayitta dolduruluyor.
   BILANCO kalemleri ise ANLIK (donem sonu fotografi) -> days = NULL.
2. BIRIM. Sayfa "Bin TRY" diyor. 1.018.453.000 goruntusu aslinda
   1.018.453.000.000 TRY. Bin ile carpilmazsa PD/DD 0,42 yerine 416,7
   cikar — ve "1 milyar TL ozkaynak" makul GORUNDUGU icin hicbir sey
   alarm vermez. Degerler TRY'ye normalize ediliyor.

DONEM SECIMI
------------
Sayfa varsayilan olarak son 4 ceyregi gosteriyor (6ay, 3ay, 12ay, 9ay) —
hepsi FARKLI uzunlukta, yani karsilastirilamaz. Acilir listeler 2013'e
kadar tum donemleri sunuyor; biz sunlari seciyoruz:
    [0] en son donem            (or. 2026-06, 6 ay)
    [1] GECEN YILIN AYNI donemi (or. 2025-06, 6 ay)  <- asil olan bu
    [2] son tam yil             (or. 2025-12, 12 ay)
    [3] onceki tam yil          (or. 2024-12, 12 ay)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

BILANCO = "https://www.getmidas.com/canli-borsa/{slug}-hisse/bilanco/"

# Satir adi -> (kavram, ANLIK mi). Anlik = bilanco kalemi (donem sonu
# fotografi); anlik olmayan = gelir tablosu (donem boyunca birikmis).
SATIRLAR = {
    "toplam özkaynaklar":        ("Ozkaynak", True),
    "duran varlıklar":           ("DuranVarlik", True),
    "toplam dönen varlıklar":    ("DonenVarlik", True),
    "nakit ve nakit benzerleri": ("Nakit", True),
    "hasılat":                   ("Hasilat", False),
    "brüt kar/zarar":            ("BrutKar", False),
    "esas faaliyet karı/zararı": ("FaaliyetKari", False),
    "net dönem karı/zararı":     ("NetKar", False),
    "ana ortaklık payları":      ("AnaOrtaklikPayi", False),
}

BIN = 1000              # "Bin TRY" -> TRY

# YALNIZCA binlik ayracli TAM SAYI. Bu tablodaki degerler Bin TRY
# cinsinden tam sayidir; ondalik yoktur.
#
# Onceki kalip `-?[\d.]+(?:,\d+)?` idi ve YUZDE EKINI SAYIYA KATIYORDU:
# "80.346.00011,46%" -> 8.034.600.011,46 (100 kat sismis deger).
# Uretimde hucrede satir sonu oldugu icin patlamiyordu, ama satir sonu
# olmadigi gun sessizce yanlis sayi uretecekti — ve "nakit 8 milyar"
# makul GORUNDUGU icin hicbir sey alarm vermezdi.
SAYI = re.compile(r"-?\d{1,3}(?:\.\d{3})*")


def _deger(ham: str) -> float | None:
    """
    "1.018.453.000" -> 1018453000.0
    "80.346.00011,46%" -> 80346000.0   (yuzde eki YAPISIK gelebiliyor)
    "-" / "" -> None
    """
    if not ham:
        return None
    ham = ham.strip().split("\n")[0]
    if ham in ("-", "—", ""):
        return None
    m = SAYI.match(ham)
    if not m:
        return None

    # BELIRSIZ GIRDIYI REDDET, TAHMIN ETME.
    # Uretimde deger ve yuzde ayri satirda geliyor ("80.346.000\n11,46%")
    # ve yukaridaki split bunu cozuyor. Ama YAPISIK gelirse
    # ("80.346.00011,46%") dogru sayinin ne oldugu BELIRSIZDIR: regex
    # geri izlemeyle "80.346" gibi kisa ve yanlis bir eslesme bulur.
    # 80 bin mi 80 milyon mu — makul gorunen yanlis deger, bu projede
    # bes kez tekrarlanan hata sinifi. Eksik veri yanlis veriden iyidir.
    kalan = ham[m.end():]
    if kalan[:1].isdigit():
        log.warning("[midasbilanco] belirsiz sayi, atlandi: %r", ham[:32])
        return None
    try:
        return float(m.group(0).replace(".", ""))
    except ValueError:
        return None


class _YapiFarkli(RuntimeError):
    """
    Sayfa yapisi beklenenden farkli — sigorta/finans sirketleri.
    Ayri istisna, cunku bu bir HATA degil BILINEN BIR SINIR: raporda
    "basarisiz" degil "sektor yapisi farkli" diye gorunmeli.
    """


def _donem_gun(ay: int) -> int:
    """Kumulatif donem uzunlugu, gun. 3->90, 6->181, 9->273, 12->365."""
    return {3: 90, 6: 181, 9: 273, 12: 365}.get(ay, ay * 30)


class MidasBilancoCollector(BaseCollector):
    name = "midasbilanco"
    needs_browser = True

    def collect(self) -> CollectorResult:
        if self.browser is None:
            return CollectorResult(self.name, "skipped", 0, "tarayici yok")

        hedefler = self._hedefler()
        if not hedefler:
            return CollectorResult(self.name, "skipped", 0,
                                   "BIST hedefi yok (once endeks uyeligi cekilmeli)")
        # Ortam degiskeni ayari EZER: ilk doldurma tek seferde yapilir
        # (scripts/bilanco_doldur.sh), gunluk donusum 10'da kalir.
        import os
        adet = int(os.environ.get("FINAGENT_BILANCO_PER_RUN")
                   or self.s.get("sources.midasbilanco.per_run", 10))
        secilen = self._en_bayat(hedefler, adet)

        toplam, alinan, basarisiz, farkli_yapi = 0, [], [], []
        pg = self.browser.context.new_page()
        try:
            pg.set_viewport_size({"width": 1600, "height": 1000})
            for iid, sem in secilen:
                try:
                    n = self._sembol(pg, iid, sem)
                    n += self._ortaklik(pg, iid, sem)
                    toplam += n
                    (alinan if n else basarisiz).append(sem)
                except _YapiFarkli:
                    # SESSIZ BOSLUK BIRAKMA. Sigorta ve bazi finans
                    # sirketleri FARKLI tablo yapisi kullaniyor: "Ozet
                    # Bilanco" tablosu yok, yerine ayri Bilanco/Gelir
                    # Tablosu/Nakit Akislari tablolari ve sektore ozgu
                    # satir adlari var (olculdu: ANSGR, TURSG, DSTKF,
                    # KTLEV). Bunu "basarisiz" diye gecmek, ileride
                    # neden veri olmadigini aramaya sebep olurdu.
                    farkli_yapi.append(sem)
                except Exception as e:                # noqa: BLE001
                    log.warning("[%s] %s: %s", self.name, sem, e)
                    basarisiz.append(sem)
        finally:
            pg.close()

        notlar = f"alinan: {', '.join(alinan)}" if alinan else "veri yok"
        if farkli_yapi:
            notlar += (f" · SEKTOR YAPISI FARKLI (sigorta/finans), "
                       f"desteklenmiyor: {', '.join(farkli_yapi[:6])}")
        if basarisiz:
            notlar += f" · basarisiz: {', '.join(basarisiz[:6])}"
        kalan = len(hedefler) - len(secilen)
        if kalan > 0:
            notlar += f" · {kalan} sembol siradaki calismalarda (donusumlu)"
        return CollectorResult(self.name, "ok" if alinan else "partial",
                               toplam, notlar)

    # ------------------------------------------------------------------
    def _hedefler(self) -> list[tuple]:
        return [(r["id"], r["symbol"]) for r in self.db.query(
            """SELECT DISTINCT i.id, i.symbol FROM instruments i
               WHERE i.venue = 'BIST' AND (
                   i.id IN (SELECT instrument_id FROM index_members
                            WHERE index_name = 'BIST 100')
                   OR i.id IN (SELECT instrument_id FROM positions)
                   OR i.id IN (SELECT instrument_id FROM watchlist))
               ORDER BY i.symbol""")]

    def _en_bayat(self, hedefler, adet):
        """
        En uzun suredir guncellenmeyenler once. Bilanco CEYREKTE BIR
        degisiyor; her gun hepsini cekmek 100 sembol x ~10 sn = 17 dk
        eder ve nabzin butcesini yer. Donusumlu gidince BIST 100 ~10
        gunde bir tamamlanir — ceyreklik veri icin fazlasiyla sik.
        """
        skor = []
        for iid, sem in hedefler:
            r = self.db.query(
                """SELECT MAX(filed) son FROM fundamentals
                   WHERE instrument_id = ? AND form = 'midasbilanco'""", (iid,))
            skor.append((r[0]["son"] or "", iid, sem))
        skor.sort()
        return [(iid, sem) for _, iid, sem in skor[:adet]]

    # ------------------------------------------------------------------
    def _sembol(self, pg, instrument_id: int, sembol: str) -> int:
        pg.goto(BILANCO.format(slug=sembol.lower()),
                wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(3500)

        # Ozet tablosu var mi? Sigorta/finans sirketlerinde YOK.
        ozet_var = pg.evaluate(
            "() => [...document.querySelectorAll('table')].some("
            "t => t.innerText.toLowerCase().includes('özkaynak'))")
        if not ozet_var:
            raise _YapiFarkli(sembol)

        secililer = self._donemleri_sec(pg)
        if not secililer:
            return 0

        # YALNIZCA OZET TABLOSU okunur. Once tum tablolar taraniyordu ve
        # ayni satir adi birden fazla tabloda gectigi icin (deger tablosu
        # + yuzde degisim tablosu) son okunan digerinin uzerine yaziyordu;
        # sonuc: degerlerin cogu 0,00 cikti. Ozet tablosu "ozkaynak"
        # satirini iceren tablodur.
        satirlar = pg.evaluate("""() => {
            const tb = [...document.querySelectorAll('table')].find(
                t => t.innerText.toLowerCase().includes('özkaynak'));
            if (!tb) return [];
            const out = [];
            for (const tr of tb.querySelectorAll('tr')) {
              const h = [...tr.querySelectorAll('th,td')].map(c => c.innerText.trim());
              if (h.length > 2) out.push(h);
            }
            return out;
        }""")

        an = datetime.now(timezone.utc).date().isoformat()
        kayit = []
        gorulen = set()          # ayni kavram+donem ikinci kez yazilmasin
        for h in satirlar:
            ad = h[0].strip().casefold()
            tanim = SATIRLAR.get(ad)
            if not tanim:
                continue
            kavram, anlik = tanim
            for i, (etiket, yil, ay) in enumerate(secililer):
                if i + 1 >= len(h):
                    break
                v = _deger(h[i + 1])
                if v is None:
                    continue
                if (kavram, yil, ay) in gorulen:
                    continue
                gorulen.add((kavram, yil, ay))
                # period_end: donemin SON gunu (ay sonu yaklasik)
                son_gun = 31 if ay == 12 else 30
                bitis = f"{yil}-{ay:02d}-{son_gun:02d}"
                kayit.append((
                    instrument_id, kavram, "TRY", None, bitis,
                    None if anlik else _donem_gun(ay),   # ANLIK -> days NULL
                    v * BIN,                              # Bin TRY -> TRY
                    "midasbilanco", yil, f"M{ay:02d}", etiket, an, None))
        if not kayit:
            return 0
        return self.db.upsert_fundamentals(kayit)

    def _ortaklik(self, pg, instrument_id: int, sembol: str) -> int:
        """
        Ortaklik yapisi — hisse ANA sayfasindaki pasta grafik.

        Onceden "JS ile geliyor, alinamiyor" diye birakilmisti. Yanlisti:
        grafik Chart.js ile ciziliyor ve veri JS BELLEGINDE duruyor,
        `Chart.getChart(canvas).data` ile dogrudan okunabiliyor. Piksel
        okumaya ya da OCR'a gerek yok.

        Neden degerli: kontrolun ne kadar yogunlastigini ve gercek halka
        acikligi gosterir. Tek ortagin %50+ payi oldugu bir sirkette
        azinlik hissedarin soz hakki yoktur.
        """
        try:
            pg.goto(f"https://www.getmidas.com/canli-borsa/{sembol.lower()}-hisse/",
                    wait_until="networkidle", timeout=45000)
            pg.wait_for_timeout(3000)
            veri = pg.evaluate("""() => {
                if (typeof Chart === 'undefined') return null;
                for (const c of document.querySelectorAll('canvas')) {
                  const ch = Chart.getChart(c);
                  if (!ch || ch.config.type !== 'pie') continue;
                  const d = ch.data;
                  return (d.labels || []).map((ad, i) => ({
                    ad: String(ad),
                    pay: parseFloat(String((d.datasets[0].data || [])[i]))
                  })).filter(x => x.ad && !isNaN(x.pay));
                }
                return null;
            }""")
        except Exception as e:                        # noqa: BLE001
            log.debug("[%s] %s ortaklik alinamadi: %s", self.name, sembol, e)
            return 0
        if not veri:
            return 0
        an = datetime.now(timezone.utc).date().isoformat()
        with self.db.tx() as c:
            c.executemany(
                """INSERT INTO ownership (instrument_id, ortak, pay_pct,
                   olcum_tarihi, kaynak) VALUES (?,?,?,?,?)
                   ON CONFLICT(instrument_id, ortak, olcum_tarihi, kaynak)
                   DO UPDATE SET pay_pct = excluded.pay_pct""",
                [(instrument_id, x["ad"][:200], float(x["pay"]), an, self.name)
                 for x in veri])
        return len(veri)

    def _donemleri_sec(self, pg) -> list[tuple]:
        """
        Dort acilir listeyi ANLAMLI donemlere ayarlar ve secilenleri
        (etiket, yil, ay) olarak dondurur.

        Varsayilan 4 sutun (6ay/3ay/12ay/9ay) HEPSI FARKLI uzunlukta,
        yani birbiriyle karsilastirilamaz. Asil ihtiyac duyulan sutun
        GECEN YILIN AYNI donemi.
        """
        try:
            secim = pg.locator("select")
            if secim.count() < 4:
                return []
            secenekler = pg.evaluate(
                "() => [...document.querySelectorAll('select')[0].options]"
                ".map(o => ({v: o.value, t: o.text.trim()}))")
        except Exception:                              # noqa: BLE001
            return []
        if not secenekler:
            return []

        def coz(v):
            m = re.match(r"(\d{4})-(\d{1,2})", str(v))
            return (int(m.group(1)), int(m.group(2))) if m else None

        havuz = [(o, coz(o["v"])) for o in secenekler]
        havuz = [(o, d) for o, d in havuz if d]
        if not havuz:
            return []

        son_o, (yil, ay) = havuz[0]
        istenen = [
            (son_o, yil, ay),                          # en son donem
            (None, yil - 1, ay),                       # GECEN YIL AYNI donem
            (None, yil - 1 if ay < 12 else yil, 12),   # son tam yil
            (None, (yil - 2 if ay < 12 else yil - 1), 12),  # onceki tam yil
        ]
        out = []
        for i, (o, y, a) in enumerate(istenen):
            aday = o or next((oo for oo, d in havuz if d == (y, a)), None)
            if aday is None:
                continue
            try:
                pg.locator("select").nth(i).select_option(value=aday["v"])
                pg.wait_for_timeout(1200)
            except Exception:                          # noqa: BLE001
                continue
            out.append((aday["t"], y, a))
        pg.wait_for_timeout(1500)
        return out
