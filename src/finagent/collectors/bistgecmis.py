"""
BIST DERIN GECMISI — backtest'in on kosulu.

NEDEN AYRI BIR COLLECTOR
------------------------
`isyatirim` BIST'in BIRINCIL kaynagi ve oyle kalmali: fiyatin yaninda
hisse sayisi, gunluk TL hacim ve XU100 endeksini de getiriyor — yan
urunlerin hicbiri baska yerde yok. Ama DERINLIGI dar: olculdu
2026-08-20, 365 sembolde ~287 bar, yani 13,5 ay.

13,5 ay TEK bir makro rejimdir. Bir stratejiyi sinamak icin en az IKI
farkli rejim gerekir (2018 TL krizi, 2020 COVID, 2021-22 enflasyon ve
faiz dongusu, 2023-24 normallesme). Bu yuzden backtest bu projede
"acik madde" olarak duruyordu — veri yoktu.

Yahoo ayni kagitlari `.IS` sonekiyle ve COK daha derin veriyor:
DEVA 6.756 bar (2000'den), THYAO 6.756, KGYO 6.757, TRALT 4.238.
Fiyatlar isyatirim ile BIREBIR ayni — yedi sembolde de %0,00 fark
olculdu, yani bu bir "ikinci gorus" degil AYNI serinin uzunu.

`.IS` SONEKI AMBIGU DEGIL: sade "DEVA" Yahoo'da baska sirkete denk
gelebilir (AVTX ve RBOT'ta tam bu oldu) ama "DEVA.IS" tek bir
kotasyonu gosterir — dogrulandi, ad da tutuyor (DEVA HOLDING).

NEDEN GUNLUK KOSUYOR, TEK SEFERLIK DEGIL
----------------------------------------
`fiyat_kaynagi()` ayni para birimindeki kaynaklar arasinda EN TAZE
olani seciyor, esitlikte en cok barli. Tek seferlik bir dolum ertesi
gun bayatlar ve `isyatirim` (gunluk tazelenen) secilir — yani 6.756
barlik seri yazilir ama HIC KULLANILMAZ. Gunluk kosunca hem taze hem
derin olur ve secim dogal olarak buna duser.

Maliyeti kucuk: 365 sembol tek `download` cagrisinda ~26 saniye.
"""
from __future__ import annotations

import logging

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

# Yahoo'nun BIST soneki.
SONEK = ".IS"

# `prices` birincil anahtari (instrument_id, ts, source) ve para birimi
# ANAHTARDA YOK. Ayri kaynak adi sart: `isyatirim` ile ayni ada yazmak
# onun serisini EZERDI ve yan urunleri (hacim, hisse sayisi) uretmeyen
# bir seri onun yerine gecerdi.
KAYNAK = "yahoo_bist"

# Tazeleme kademeleri: (azami bosluk gunu, yfinance `period`). Bosluk
# kademeyi asarsa bir ustu; hepsini asarsa derin period. `prices`
# collector'unun kademeleriyle ayni fikir, yfinance'in kendi `period`
# sozlugunde (1mo/3mo/6mo/1y/2y).
TAZELEME_KADEMELERI = ((7, None), (25, "1mo"), (80, "3mo"), (170, "6mo"),
                       (340, "1y"), (700, "2y"))


def tazeleme_periyodu(bosluk_gun, kisa: str, derin: str) -> str:
    """
    Son bardan bu yana gecen gune gore tazeleme araligi. Saf.

    `bosluk_gun` None ise (tarih okunamadi) DERIN: bilinmeyen bosluk
    kucuk varsayilmaz. Kademe `None` ise ayardaki kisa aralik (5d).
    """
    if bosluk_gun is None or bosluk_gun < 0:
        return derin
    for sinir, aralik in TAZELEME_KADEMELERI:
        if bosluk_gun <= sinir:
            return aralik or kisa
    return derin


def _bosluk_gun(son_ts, bugun) -> int | None:
    from datetime import date as _date
    try:
        return (bugun - _date.fromisoformat(str(son_ts)[:10])).days
    except (TypeError, ValueError):
        return None


def _ilk(liste, n: int = 8) -> str:
    """
    Ilk n sembol — ve KIRPILDIGI SOYLENEREK.

    Sessiz kirpma bu deponun tekrar eden kusur sinifi: `prices`te liste
    8'de kesiliyor ve kesildigi soylenmiyordu, kullanici 2026-08-23
    alarminda tam 8 sembol gorup "hepsi bu" sandi — gercekte 12 vardi.
    """
    bas = ", ".join(liste[:n])
    return bas if len(liste) <= n else f"{bas} (+{len(liste) - n} daha)"


class BistGecmisCollector(BaseCollector):
    name = "bistgecmis"
    needs_browser = False

    def collect(self) -> CollectorResult:
        import warnings

        import yfinance as yf

        # yfinance'in kendi gurultusu: kapanan/degisen kodlarda ERROR
        # basiyor ve bunlar bizde zaten `atlanan` olarak raporlaniyor.
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        warnings.filterwarnings("ignore")

        semboller = self._semboller()
        if not semboller:
            return CollectorResult(self.name, "skipped", 0, "BIST evreni bos")

        period = self.s.get("sources.bistgecmis.period", "10y")
        kisa = self.s.get("sources.bistgecmis.gunluk_period", "5d")
        parca_boy = int(self.s.get("sources.bistgecmis.parca", 120))

        # DERIN DOLUM BIR KEZ, TAZELEME HER GUN.
        #
        # Ilk kosu 365 sembol icin 595.713 bar yaziyor ve 87 saniye
        # suruyor. Bunu her gece tekrarlamak 595 bin satirlik bosa
        # upsert demekti — derin gecmis DEGISMIYOR, yalnizca son bar
        # ekleniyor. Gecmisi ZATEN dolu olan sembol icin son birkac gun
        # cekilir; eksik olan icin tam period.
        #
        # Olcut bar sayisi DEGIL, ILK BARIN TARIHI: yeni kote edilmis
        # bir kagitta (MASFN 30 Tem 2026, QUICK 6 Agu 2026) bar sayisi
        # kalici olarak dusuktur ve sayiya bakan bir olcut onlari
        # SONSUZA KADAR tam cekerdi. Gecmis eksik degil, YOK.
        mevcut = {}
        for r in self.db.query(
                """SELECT i.symbol s, MIN(p.ts) ilk, MAX(p.ts) son
                   FROM prices p JOIN instruments i ON i.id = p.instrument_id
                   WHERE i.venue = 'BIST' AND p.source = ?
                   GROUP BY i.symbol""", (KAYNAK,)):
            mevcut[r["s"]] = (r["ilk"], r["son"])

        derin = [s for s in semboller if s not in mevcut]
        taze = [s for s in semboller if s in mevcut]

        # TAZELEME ARALIGI BOSLUGA GORE — SABIT "5d" DEGIL.
        #
        # OLCULEN KUSUR (2026-09-08): evrenden dusup geri gelen bir
        # sembolun (ARZUM: son bar 21 Agu) "5d" ile tazelenmesi 22 Agu-
        # 1 Eyl arasini SONSUZA KADAR bos birakiyordu; puanlayici
        # "olusmadan sonraki 5. bar"i sayarken o boslugu atlayip yanlis
        # gunu olcerdi. `prices.TAZELEME_KADEMELERI` ile ayni fikir:
        # bosluk kadar geri git, gerisini yeniden yazma.
        #
        # KENDINI ONARMA: serisi sicramali sembol TAM period ile cekilir
        # (kaynak gecmisi yeniden tabanlamis, bizde iki taban kalmis —
        # gerekce `analysis/tutarlilik.py`).
        from datetime import date as _date

        from ..analysis.tutarlilik import sicramali_semboller
        sicramali = sicramali_semboller(self.db, KAYNAK, venue="BIST")
        bugun = _date.today()
        gruplar: dict[str, list[str]] = {}
        for s in semboller:
            if s in derin or s in sicramali:
                p = period
            else:
                p = tazeleme_periyodu(_bosluk_gun(mevcut[s][1], bugun),
                                      kisa, period)
            gruplar.setdefault(p, []).append(s)
        log.info("[%s] %d sembol derin dolum, %d sembol tazeleme (%s)%s",
                 self.name, len(derin), len(taze),
                 ", ".join(f"{p}: {len(g)}" for p, g in gruplar.items()),
                 (f" · {len(sicramali)} sicramali TAM cekiliyor: "
                  + ", ".join(sorted(sicramali)[:8])) if sicramali else "")

        # UC AYRI KATEGORI — IKI DEGIL. (2026-08-28'de duzeltildi.)
        #
        # `yok`  — sembolu Yahoo TASIMIYOR. Ariza degil, KAPSAM. DMLKTG
        #          boyle: gercek bir BIST kodu (Midas'ta bari var) ama
        #          `DMLKTG.IS` Yahoo'da yok. Bunu `partial` saymak,
        #          collector'i HER KOSUDA partial yapardi ve bekcinin
        #          besinci olcutu surekli calardi — yani dort
        #          collector'da kapatilan SAHTE ALARM sinifini burada
        #          yeniden acardi.
        # `bos`  — toplu cagri sembolu GETIRMEDI ama seri ELIMIZDE var.
        #          Bu KAPSAM DEGIL, CEKIM ARIZASI.
        # `hata` — parca duzeyinde istisna (ag, API). GERCEK ariza.
        #
        # NEDEN AYRILDI — OLCULEN YANLIS BEYAN. 27 Agustos kosumu 124
        # sembol icin "Yahoo'da yok" dedi. Dokuzu orneklendi, SEKIZINDE
        # Yahoo'nun kendi verisi ZATEN veritabanindaydi:
        #     ALGYO 2.542 bar (2016'dan) · ARSAN 2.541 · ANHYT 2.538
        #     ARDYZ 1.639 · ALVES 622 · ALKLC 558 · ARMGD 412 · ATATR 126
        # Yalnizca DMLKTG gercekten bostu. Sayinin kosudan kosuya
        # ziplamasi da (97 -> 306 -> 124, AYNI evrende) yoklukla
        # aciklanamaz — yokluk gun icinde degismez.
        #
        # KURAL: ELINDE O KAYNAKTAN SERI OLAN SEMBOL "YOK" SAYILAMAZ.
        # Yahoo o kagidi tasidigini 2.542 barla zaten kanitlamis; bos
        # donen cagri kapsami degil CAGRIYI anlatir. Bu, `prices`te
        # ayni gun kapatilan hatanin ikizidir (`[[ayni-kural-iki-kopya]]`:
        # kopyalar ayrisir, biri duzeltilir digeri yalan soylemeye
        # devam eder) ve `[[yanlis-yok-beyani]]` ailesindendir.
        toplam, yok, bos, hata = 0, [], [], []
        getirilemeyen = []                    # (sembol, kod, period)
        for p, grup in gruplar.items():
            for i in range(0, len(grup), parca_boy):
                parca = grup[i:i + parca_boy]
                kodlar = [f"{s}{SONEK}" for s in parca]
                try:
                    # `threads=False` — OLCULDU (2026-09-08): threads=True
                    # her toplu cagrida ~40 dosya tanitici SIZDIRIYOR
                    # (curl oturumlari kapanmiyor). macOS'ta launchd
                    # surecinin siniri 256; 3 Eylul gecesi toplama sureci
                    # `midas`ta "Too many open files" ile oldu, `prices`
                    # ve `strateji_fiyat` hic calismadi. threads=False
                    # sabit ~21 taniticida kaliyor; 20 sembol 2-3 sn.
                    df = yf.download(kodlar, period=p, interval="1d",
                                     group_by="ticker", progress=False,
                                     auto_adjust=False, threads=False)
                except Exception as e:                # noqa: BLE001
                    # PARCANIN HATASI DIGERLERINI DUSURMEZ.
                    log.warning("[%s] parca alinamadi (%d sembol): %s",
                                self.name, len(parca), e)
                    hata += parca
                    continue
                for sembol, kod in zip(parca, kodlar):
                    try:
                        n = self._yaz(df, sembol, kod)
                    except Exception as e:            # noqa: BLE001
                        log.debug("[%s] %s yazilamadi: %s", self.name, sembol, e)
                        hata.append(sembol)
                        continue
                    if n:
                        toplam += n
                    else:
                        getirilemeyen.append((sembol, kod, p))

        # TOPLU CAGRININ GETIRMEDIGI TEK TEK DOGRULANIYOR.
        #
        # Siniflandirma TAHMINLE yapilamaz: "toplu cagri bosdu" ile
        # "sembol yok" ayni gorunuyor. Tek sembolluk cagri ikisini
        # AYIRIYOR — ve ayni zamanda veriyi KURTARIYOR.
        #
        # Kurtarma sart, sadece etiket duzeltmek yetmezdi: `fiyat_kaynagi`
        # ayni para birimindeki kaynaklardan EN TAZE olani seciyor.
        # Tazelenmeyen derin seri bayatlar ve `isyatirim`in 13,5 aylik
        # serisi secilir — yani 2.542 bar YAZILIR ama HIC KULLANILMAZ.
        # Sahada goruldu: ANHYT'in son yahoo_bist bari 21 Agustos'ta
        # kalmisti (bir hafta), oysa collector her gece kosuyordu.
        #
        # SINIRLI: yalnizca toplu cagrinin dusurdukleri, tek is parcacigi,
        # ve tavan var. Tavan asilirsa SOYLENIYOR — sessiz kirpma bu
        # deponun tekrar eden kusur sinifi.
        tavan = int(self.s.get("sources.bistgecmis.yeniden_deneme_tavani", 400))
        kirpilan = max(0, len(getirilemeyen) - tavan)
        kurtarilan = 0
        for sembol, kod, p in getirilemeyen[:tavan]:
            try:
                n = self._cerceveyi_yaz(sembol, self._tek_cek(yf, kod, p))
            except Exception as e:                    # noqa: BLE001
                log.warning("[%s] %s tek cagri hatasi: %s", self.name, sembol, e)
                hata.append(sembol)
                continue
            if n:
                toplam += n
                kurtarilan += 1
                continue
            # Tek cagri da bos. ELIMIZDE SERI VAR MI?
            if sembol in mevcut:
                # Yahoo bu kagidi tasidigini gecmiste kanitladi -> ARIZA.
                bos.append(sembol)
            else:
                yok.append(sembol)
        for sembol, _kod, _p in getirilemeyen[tavan:]:
            # Dogrulanmadi: "yok" DEMIYORUZ. Dogrulanmamis sembolu
            # yoklukla etiketlemek, kapatilan hatanin ta kendisi olurdu.
            bos.append(sembol)
        if kurtarilan:
            log.info("[%s] toplu cagrinin dusurdugu %d sembolun %d'i tek "
                     "cagriyla KURTARILDI", self.name, len(getirilemeyen),
                     kurtarilan)

        esik = float(self.s.get("sources.bistgecmis.azami_kayip_orani", 0.10))
        yapisal = len(yok) > max(1, int(len(semboller) * esik))

        # ENDEKS DE DERIN OLMALI, YOKSA PIYASA ETKISI AYIKLANAMAZ.
        # Backtest bir sinyalin getirisini PIYASA getirisinden ayirmak
        # zorunda: BIST 2022'de %200 yukseldi ve o donemde "her sinyal
        # kazandirdi" sonucu cikardi — olculen sey sinyal degil enflasyon
        # olurdu. XU100 isyatirim'den yalnizca 287 bar geliyor (13,5 ay),
        # yani hisseler 10 yillikken kiyas sig kaliyordu.
        toplam += self._endeksler(yf, period, kisa)

        notlar = [f"derin {len(derin)} · taze {len(taze)}"]
        if kurtarilan:
            notlar.append(f"tek cagriyla kurtarilan {kurtarilan}")
        if yok:
            notlar.append(f"Yahoo'da yok ({len(yok)}): " + _ilk(yok))
        if bos:
            # AYRI CUMLE, AYRI FIIL. "yok" kapsam, "cekilemedi" ariza —
            # ikisini ayni kelimeyle soylemek 124 sembolluk yanlis
            # beyani uretmisti.
            notlar.append(f"cekilemedi ({len(bos)}): " + _ilk(bos))
        if kirpilan:
            notlar.append(f"{kirpilan} sembol tavan nedeniyle DOGRULANMADI")
        if hata:
            notlar.append(f"HATA ({len(hata)}): " + _ilk(hata))
        if not toplam:
            durum = "error"
        # `bos` PARTIAL URETIYOR: elimizde serisi olan bir sembolun
        # cekilememesi gercek bir kayiptir ve sessiz kalmamali. `yok`
        # ise tek basina partial uretmiyor (kapsam), yalnizca orani
        # esigi asarsa — o zaman "birkac kod" degil yapisal bir kopus
        # demektir (Yahoo BIST'i kesti, sonek degisti).
        elif hata or bos or yapisal:
            durum = "partial"
        else:
            durum = "ok"
        return CollectorResult(self.name, durum, toplam, " · ".join(notlar))

    # ------------------------------------------------------------------
    def _endeksler(self, yf, period: str, kisa: str) -> int:
        """
        BIST endekslerinin derin serisi — `venue='INDEX'`.

        AYRI YAZILIYOR cunku hisse degiller: `venue='INDEX'` tarama
        evreninin DISINDA (bkz. `screener.evren`). Endekse "al" sinyali
        uretmek anlamsiz olurdu; bunlar BAGLAM ve kiyas enstrumani.
        """
        kodlar = self.s.get("sources.bistgecmis.endeksler") or {}
        toplam = 0
        for kod, tanim in kodlar.items():
            yahoo, ad = tanim["yahoo"], tanim["ad"]
            iid = self.db.upsert_instrument(kod, "INDEX", name=ad,
                                            asset_type="index", currency="TRY")
            var = self.db.query(
                "SELECT COUNT(*) n FROM prices WHERE instrument_id=? AND source=?",
                (iid, KAYNAK))[0]["n"]
            try:
                h = yf.Ticker(yahoo).history(period=period if not var else kisa,
                                             interval="1d", auto_adjust=False)
            except Exception as e:                    # noqa: BLE001
                log.warning("[%s] endeks %s alinamadi: %s", self.name, kod, e)
                continue
            satirlar = []
            for idx, r in h.iterrows():
                k = r.get("Close")
                if k is None or k != k:
                    continue

                def _s(alan, _r=r):
                    v = _r.get(alan)
                    return None if v is None or v != v else float(v)

                satirlar.append({"ts": idx.date().isoformat(),
                                 "open": _s("Open"), "high": _s("High"),
                                 "low": _s("Low"), "close": float(k),
                                 "volume": _s("Volume")})
            if satirlar:
                toplam += self.db.upsert_prices(iid, satirlar, KAYNAK,
                                                currency="TRY")
        return toplam

    def _semboller(self) -> list[str]:
        """
        BIST evreni — `isyatirim` ile AYNI liste.

        TEK TANIM: evreni burada yeniden kurmak, iki yerde beyan edilen
        gercegin ayrismasi demekti (likidite esigi bir tarafta
        guncellenir, digeri bayatlar). Bu projenin tekrar eden kusur
        sinifi tam olarak odur.
        """
        from .isyatirim import IsYatirimCollector

        return IsYatirimCollector(self.s, self.db)._semboller()

    def _yaz(self, df, sembol: str, kod: str) -> int:
        if kod not in df.columns.get_level_values(0):
            return 0
        return self._cerceveyi_yaz(sembol, df[kod])

    def _tek_cek(self, yf, kod: str, period: str):
        """Tek sembolluk cagri — toplu cagrinin getirmedigini dogrulamak icin."""
        return yf.Ticker(kod).history(period=period, interval="1d",
                                      auto_adjust=False)

    def _cerceveyi_yaz(self, sembol: str, alt) -> int:
        satirlar = []
        for idx, r in alt.iterrows():
            kapanis = r.get("Close")
            # Kapanissiz bar teknik gostergeyi bozar — yazma.
            if kapanis is None or kapanis != kapanis:
                continue

            def _s(alan, _r=r):
                v = _r.get(alan)
                return None if v is None or v != v else float(v)

            satirlar.append({
                "ts": idx.date().isoformat(),
                "open": _s("Open"), "high": _s("High"), "low": _s("Low"),
                "close": float(kapanis), "volume": _s("Volume"),
            })
        if not satirlar:
            return 0
        # `venue='BIST'` SART: araci kurum bir piyasa degildir ve ayni
        # kagit icin ikinci bir enstruman acmak degerlemeyi korlestirir.
        iid = self.db.upsert_instrument(sembol, "BIST", asset_type="equity",
                                        currency="TRY")
        return self.db.upsert_prices(iid, satirlar, KAYNAK, currency="TRY")
