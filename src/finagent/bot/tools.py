"""
Sohbet modelinin GERCEK ARACLARI — sureç ici MCP sunucusu.

NEDEN VAR
---------
Onceki mimaride sohbet katmani `allowed_tools=[], max_turns=1` ile
calisiyordu. Yani model:
  * yalnizca metin uretebiliyordu, HICBIR ISLEM yapamiyordu
    ("portfoye ekle" -> "yazma yetkim yok")
  * baglami kendisi secemiyordu; `baglam()` soruda gecen sembolu regex'le
    bulup sabit bir paket hazirliyordu. Sembol tespit edilemedigi anda
    ("TRY degil USDT" gibi bir cumlede) paket bos kaliyor ve model
    "elimde coin verisi yok" diyordu — 17.180 barlik veri dururken.
    KENDI VERITABANIMIZ hakkinda yanlis beyan, en kotu hata sinifi.
  * eksik kalan tek bir bilgiyi sonradan isteyemiyordu.

Cozum: model ne isteyecegine KENDISI karar versin. Okuma araclari serbest,
YAZMA araclari onay kapisindan gecer.

ONAY KAPISI
-----------
Mimari §5 (insan onayi) korunuyor ama tek dokunusa iniyor: yazma araci
veriyi DOGRUDAN yazmaz, `pending/` altina birakir ve dinleyici mesaja
Kaydet/Iptal butonlarini ekler. Model "kaydettim" diyemez, "onayina
sunuldu" der.
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import Any

log = logging.getLogger(__name__)

# Arac ciktilarinin ust siniri. Model baglamini bir arac ciktisi
# doldurmamali; kirpilirsa bunu ACIKCA soyluyoruz ki model "hepsi bu"
# sanmasin.
MAX_SATIR = 60


def _ok(veri: Any) -> dict:
    return {"content": [{"type": "text",
                         "text": json.dumps(veri, ensure_ascii=False, default=str)}]}


def _kaynak_kapsami() -> list[tuple[str, str]]:
    """
    `veri_topla` aciklamasini GERCEK collector kayitlarindan uretir.

    Elle yazilan liste 19 kaynagin 8'ini sayiyordu ve eksikler arasinda
    `isyatirim` — BIST'in tek fiyat kaynagi — vardi. Model gormedigi
    kaynagi isteyemez; gormedigi icin yanlisini istedi ve uc mesaj
    boyunca "boru hatti bozuk" dedi.
    """
    from ..collectors import KAPSAM, REGISTRY
    return [(ad, KAPSAM.get(ad, "?")) for ad in sorted(REGISTRY)]


def _hata(mesaj: str, ipucu: str | None = None) -> dict:
    """
    Arac hatasi da VERIDIR. Model neyin neden olmadigini bilmeli ki
    kullaniciya dogru seyi soylesin — sessizce bos donmek uydurmaya iter.
    """
    return _ok({"hata": mesaj, "ipucu": ipucu})


class ToolBox:
    """
    DB ve analiz katmanini araclara baglar.

    Tek ornek uzerinden calisir: her aracin `self.db`'ye ihtiyaci var ama
    SDK arac fonksiyonlarini serbest fonksiyon olarak istiyor; closure ile
    baglaniyorlar (`araclar()`).
    """

    def __init__(self, settings, db, pending_dir, sahip: str | None = None,
                 chat_id=None):
        """
        `sahip` PARAMETREDIR, ortam durumu degil.

        None olabilir (or. bir sohbet hicbir sahibe bagli degilse) ama o
        durumda PORTFOY ARACLARI CALISMAZ ve ACIK HATA doner. Bos sonuc
        donmek yanlis olurdu: model "portfoyun bos" diye okur ve bu,
        yanlis kisinin verisini gostermekten farkli ama esdeger bicimde
        yaniltici bir cevap uretir.
        """
        self.s = settings
        self.db = db
        self.sahip = sahip
        self.chat_id = str(chat_id) if chat_id is not None else None
        self.pending_dir = pending_dir
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.bekleyen_token: list[str] = []   # bu turda uretilen onay istekleri
        # Bu turda GONDERILECEK gorseller. Arac modele METIN dondurur,
        # Telegram'a DOSYA gitmesi gerekir; onay akisindaki kalibin ayni:
        # arac hazirlar, dinleyici turdan sonra gonderir.
        self.gorseller: list[dict] = []

    # ------------------------------------------------------------------
    # yardimcilar
    # ------------------------------------------------------------------
    def _sahip_gerek(self):
        """Portfoy araclarinin on kosulu; sahip yoksa ACIK hata dondur."""
        if not self.sahip:
            return _hata("bu sohbet bir kisiye bagli degil",
                         "settings.yaml -> telegram.sahipler icine bu "
                         "chat_id eklenmeli; portfoy araclari sahipsiz "
                         "calismaz")
        return None

    def _enstruman(self, sembol: str):
        """
        Sembolden enstruman satiri. Once tam eslesme, sonra ad icinde arama.

        Kripto ve hisse ayni isimde olabilecegi icin venue de doner; cagiran
        hangi evrende oldugunu bilir.
        """
        s = (sembol or "").strip().upper()
        if not s:
            return None
        r = self.db.query(
            """SELECT id, symbol, name, venue, asset_type FROM instruments
               WHERE UPPER(symbol) = ? ORDER BY
                 CASE venue WHEN 'BINANCE' THEN 0 WHEN 'BUX' THEN 1 ELSE 2 END
               LIMIT 1""", (s,))
        if r:
            return r[0]
        r = self.db.query(
            """SELECT id, symbol, name, venue, asset_type FROM instruments
               WHERE UPPER(name) LIKE ? LIMIT 1""", (f"%{s}%",))
        return r[0] if r else None

    def _kimlik(self, instrument_id: int):
        r = self.db.query(
            "SELECT * FROM identities WHERE instrument_id = ?", (instrument_id,))
        return dict(r[0]) if r else {}

    async def _alt_surecte(self, kaynaklar: list[str]) -> list[dict]:
        """
        `run.py collect --site ...` alt surec olarak. Tarayici gerektiren
        collector'lar icin. Zaman asimi ile — bir sayfa asilirsa sohbet
        sonsuza kadar beklemesin.
        """
        import asyncio
        import sys

        sure = int(self.s.get("analysis.llm.collect_timeout_sn", 300))
        komut = [sys.executable, "run.py", "collect", "--site", *kaynaklar]
        try:
            proc = await asyncio.create_subprocess_exec(
                *komut, cwd=str(self.s.root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
        except Exception as e:                        # noqa: BLE001
            return [{"kaynak": ",".join(kaynaklar), "durum": "error",
                     "not": f"alt surec baslatilamadi: {e}"}]
        try:
            cikti, _ = await asyncio.wait_for(proc.communicate(), timeout=sure)
        except asyncio.TimeoutError:
            proc.kill()
            # ZAMAN ASIMI BIR SONUCTUR, HATA DEGIL. Model bunu
            # kullaniciya DOGRU cumleyle aktarmali: "toplama basarisiz"
            # yanlis olur — is basladi, sadece sohbet turuna sigmadi ve
            # gece nabzi zaten cekecek. `isyatirim` sahada 25-32 dakika
            # surebiliyor (pulse'ta 3 dk; fark HENUZ ACIKLANMADI).
            return [{"kaynak": ",".join(kaynaklar), "durum": "zaman_asimi",
                     "sinir_sn": sure,
                     "not": (f"{sure} sn icinde bitmedi, durduruldu. Bu bir "
                             "ARIZA DEGIL: bazi kaynaklar (ozellikle "
                             "isyatirim, midasbilanco) sohbet turuna "
                             "sigmayacak kadar uzun surer ve gece 22:15 "
                             "nabzi onlari zaten cekiyor. Kullaniciya "
                             "'toplayamadim' DEME; 'bu kaynak uzun suruyor, "
                             "gece kendiliginden gelecek' de.")}]

        metin = (cikti or b"").decode("utf-8", "replace")
        # Collector sonuclari DB'ye de yaziliyor; ozeti oradan okumak
        # cikti ayristirmaktan saglam.
        out = []
        for ad in kaynaklar:
            r = self.db.query(
                """SELECT status, rows_written, error FROM collector_runs
                   WHERE collector = ? ORDER BY run_ts DESC LIMIT 1""", (ad,))
            if r:
                out.append({"kaynak": ad, "durum": r[0]["status"],
                            "satir": r[0]["rows_written"], "not": r[0]["error"]})
            else:
                out.append({"kaynak": ad, "durum": "bilinmiyor",
                            "not": metin[-300:]})
        return out

    def _stage(self, tip: str, veri: dict) -> str:
        """
        Onay bekleyen islemi diske birakir, token doner.

        SAHIP VE CHAT JSON'A YAZILIR: onay dosyasi diskte duruyor ve
        onaylayan taraf hangi kisiye yazacagini oradan okur. Sahibi
        yazmasaydik `/onayla` yanlis kisinin portfoyune yazabilirdi —
        bu isin tek gercek tehlikesi.
        """
        token = secrets.token_hex(6)
        veri = {**veri, "_tip": tip, "_token": token,
                "_sahip": self.sahip, "_chat_id": self.chat_id}
        (self.pending_dir / f"{token}.json").write_text(
            json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
        self.bekleyen_token.append(token)
        return token

    # ------------------------------------------------------------------
    # araclar
    # ------------------------------------------------------------------
    def araclar(self) -> list:
        from claude_agent_sdk import tool

        # --- OKUMA ----------------------------------------------------
        @tool("veri_durumu",
              "Veritabaninda NE OLDUGUNU ozetler: hangi hesaplar, kac "
              "enstruman, hangi sembollerde fiyat/tokenomik/haber var. "
              "Bir seyin 'yok' oldugunu soylemeden ONCE bunu cagir.",
              {})
        async def veri_durumu(_args):
            # Piyasa sayilari ORTAK; hesaplar CAGIRAN SAHIBE ait.
            hesaplar = ([dict(r) for r in self.db.query(
                """SELECT account, COUNT(DISTINCT instrument_id) pozisyon,
                          MAX(snapshot_ts) son
                   FROM positions WHERE sahip = ? GROUP BY account""",
                (self.sahip,))] if self.sahip else [])
            fiyat = [dict(r) for r in self.db.query(
                """SELECT i.venue, COUNT(DISTINCT p.instrument_id) sembol,
                          COUNT(*) bar, MAX(p.ts) son
                   FROM prices p JOIN instruments i ON i.id=p.instrument_id
                   GROUP BY i.venue""")]
            saatlik = self.db.query(
                """SELECT COUNT(DISTINCT instrument_id) s, COUNT(*) n,
                          MAX(ts) son FROM prices_hourly""")[0]
            kripto = [r["symbol"] for r in self.db.query(
                """SELECT DISTINCT i.symbol FROM prices_hourly h
                   JOIN instruments i ON i.id=h.instrument_id ORDER BY i.symbol""")]
            return _ok({
                "hesaplar": hesaplar,
                "gunluk_fiyat": fiyat,
                "saatlik_fiyat": {"sembol": saatlik["s"], "bar": saatlik["n"],
                                  "son": saatlik["son"]},
                "saatlik_verisi_olan_kripto": kripto,
                "haber": self.db.query("SELECT COUNT(*) c FROM news")[0]["c"],
                "tokenomik_kayit": self.db.query(
                    "SELECT COUNT(*) c FROM fundamentals WHERE form='coingecko'")[0]["c"],
                "xbrl_kayit": self.db.query(
                    "SELECT COUNT(*) c FROM fundamentals WHERE form<>'coingecko'")[0]["c"],
                "enstruman": self.db.query("SELECT COUNT(*) c FROM instruments")[0]["c"],
            })

        @tool("portfoy",
              "Kayitli pozisyonlar. hesap bos birakilirsa TUM hesaplar "
              "doner (bux, binance, midas). Agirliklari hesaplar.",
              {"hesap": str})
        async def portfoy(args):
            eksik = self._sahip_gerek()
            if eksik:
                return eksik
            istenen = (args.get("hesap") or "").strip().lower()
            hesaplar = [istenen] if istenen else self.db.hesaplar(self.sahip)
            out = {}
            for h in hesaplar:
                poz = self.db.latest_positions(h, self.sahip)
                if not poz:
                    continue
                toplam = sum((p["market_value"] or 0) for p in poz)
                out[h] = {
                    "anlik_goruntu": poz[0]["snapshot_ts"],
                    "toplam": round(toplam, 2),
                    "para_birimi": poz[0]["currency"],
                    "pozisyonlar": [{
                        "sembol": p["symbol"], "ad": p["name"],
                        "adet": p["quantity"], "deger": p["market_value"],
                        "kz_%": p["pnl_pct"],
                        "agirlik_%": (round((p["market_value"] or 0) / toplam * 100, 2)
                                      if toplam else None),
                    } for p in poz],
                }
            if not out:
                return _hata("kayitli pozisyon yok",
                             "kullanici ekran goruntusu gonderip onaylamali")
            # Hesaplar farkli para birimindeyse ne yapilacagini SOYLE.
            # Onceden burada kosulsuz "FX serisi veride yok" yaziyordu;
            # kur serisi geldikten sonra bu bilgi YANLIS oldu ve model
            # ayni cevapta hem "FX yok" hem de kuru kullanmis oldu.
            birimler = {v["para_birimi"] for v in out.values() if v["para_birimi"]}
            uyari = None
            if len(birimler) > 1:
                kur_var = self.db.query(
                    "SELECT COUNT(*) c FROM fx_rates")[0]["c"] > 0
                liste = ", ".join(sorted(str(b) for b in birimler))
                uyari = (f"Hesaplar FARKLI para biriminde ({liste}). "
                         + ("Toplamadan ONCE `fx` araciyla cevir ve hangi "
                            "kuru/tarihi kullandigini yaz."
                            if kur_var else
                            "Kur serisi veride YOK — tek toplama ulasma; "
                            "`veri_topla alphavantage` ile kur cekilebilir."))
            return _ok({"hesaplar": out, "uyari": uyari})

        @tool("ara",
              "Enstruman ara: sembol veya ad parcasi. Katalogda ne var, "
              "hangi sembol hangi sirket/coin — bunu ogrenmek icin.",
              {"sorgu": str})
        async def ara(args):
            q = (args.get("sorgu") or "").strip()
            if not q:
                return _hata("sorgu bos")
            rows = self.db.query(
                """SELECT i.symbol, i.name, i.venue, i.asset_type,
                          (SELECT COUNT(*) FROM prices p WHERE p.instrument_id=i.id) bar,
                          (SELECT 1 FROM watchlist w WHERE w.instrument_id=i.id) izlemede
                   FROM instruments i
                   WHERE UPPER(i.symbol) LIKE ? OR UPPER(i.name) LIKE ?
                   ORDER BY (bar > 0) DESC, izlemede DESC, i.symbol LIMIT ?""",
                (f"%{q.upper()}%", f"%{q.upper()}%", MAX_SATIR))
            return _ok({"sonuc": [dict(r) for r in rows], "sayi": len(rows)})

        @tool("teknik",
              "GUNLUK fiyat serisinden hesaplanmis teknik gostergeler: "
              "SMA20/50/200, RSI14, getiriler, oynaklik, hacim orani, trend. "
              "Hisse ve kripto icin ayni sekilde calisir.",
              {"sembol": str})
        async def teknik(args):
            e = self._enstruman(args.get("sembol", ""))
            if not e:
                return _hata(f"{args.get('sembol')} enstruman listesinde yok",
                             "once `ara` ile dogru sembolu bul")
            # TEK kaynaktan seri: ayni enstrumanda birden fazla para
            # biriminde seri olabiliyor (ASML: Yahoo USD + AV EUR) ve
            # karistirilirsa gostergeler sessizce yanlis cikar.
            rows = self.db.fiyat_serisi(e["id"], 300)
            if len(rows) < 30:
                return _hata(f"{e['symbol']} icin yeterli gunluk bar yok "
                             f"({len(rows)} bar, en az 30 gerekir)",
                             "`veri_topla` ile fiyat cekilebilir")
            import pandas as pd
            from ..analysis import compute_indicators, technical_snapshot
            df = pd.DataFrame([dict(r) for r in rows]).sort_values("ts")
            t = technical_snapshot(e["symbol"], compute_indicators(
                df, self.s.get("analysis.indicators", {})))
            t["venue"] = e["venue"]
            t["bar_sayisi"] = len(rows)
            t["para_birimi"] = rows[-1]["currency"]
            t["kaynak"] = rows[-1]["source"]
            t["not"] = (f"Tum seviyeler {rows[-1]['currency']} cinsinden. "
                        "Portfoy degeri baska para biriminde olabilir — "
                        "`fx` araciyla cevir, kafadan cevirme.")
            return _ok(t)

        @tool("saatlik",
              "SAATLIK seri (yalnizca kripto): 1s/24s/7g degisim, saatlik "
              "oynaklik, hacim. Gunluk gostergelerle KARISTIRILMAZ, ayri "
              "zaman olcegidir.",
              {"sembol": str})
        async def saatlik(args):
            e = self._enstruman(args.get("sembol", ""))
            if not e:
                return _hata(f"{args.get('sembol')} bulunamadi")
            barlar = self.db.saatlik_seri(e["id"], limit=168)
            if len(barlar) < 24:
                return _hata(f"{e['symbol']} icin saatlik seri yok "
                             f"({len(barlar)} bar)",
                             "saatlik seri yalnizca Binance kriptolarinda var")
            k = [b["close"] for b in barlar if b["close"]]
            hac = [b["quote_volume"] or 0 for b in barlar]
            son = k[-1]

            def d(saat):
                return (round((son / k[-1 - saat] - 1) * 100, 2)
                        if len(k) > saat and k[-1 - saat] else None)

            g = [k[i] / k[i - 1] - 1 for i in range(1, len(k)) if k[i - 1]]
            ort = sum(g) / len(g) if g else 0
            var = sum((x - ort) ** 2 for x in g) / (len(g) - 1) if len(g) > 1 else 0
            return _ok({
                "sembol": e["symbol"], "son_bar": barlar[-1]["ts"],
                "son_fiyat": son, "bar_sayisi": len(barlar),
                "degisim_1s_%": d(1), "degisim_24s_%": d(24),
                "degisim_7g_%": d(len(k) - 1),
                "saatlik_oynaklik_%": round(var ** 0.5 * 100, 3),
                "hacim_24s_usdt": round(sum(hac[-24:])),
                "not": "SAATLIK olcek. Gunluk SMA/RSI ile karistirma.",
            })

        @tool("tokenomik",
              "Kripto arz/degerleme verisi (CoinGecko): piyasa degeri, "
              "dolasimdaki/toplam arz, FDV, ATH/ATL, siralama. "
              "DIKKAT: bu TEMEL ANALIZ DEGILDIR — coin'in cirosu/kari yok.",
              {"sembol": str})
        async def tokenomik(args):
            e = self._enstruman(args.get("sembol", ""))
            if not e:
                return _hata(f"{args.get('sembol')} bulunamadi")
            rows = self.db.query(
                """SELECT concept, val, unit, period_end FROM fundamentals
                   WHERE instrument_id=? AND form='coingecko'""", (e["id"],))
            if not rows:
                return _hata(f"{e['symbol']} icin tokenomik yok",
                             "kripto degilse zaten olmaz; kriptoysa "
                             "`veri_topla` ile coingecko calistirilabilir")
            return _ok({"sembol": e["symbol"],
                        "olcum": {r["concept"]: {"deger": r["val"], "birim": r["unit"]}
                                  for r in rows},
                        "olcum_tarihi": rows[0]["period_end"],
                        "uyari": "F/K, marj, ROE kriptoda TANIMSIZ."})

        @tool("finansallar",
              "Hisse temel verisi. ABD: SEC XBRL (gelir, marj, bilanco, "
              "EPS). BIST: bilanco sayfasi (hasilat, brut/faaliyet/net kar, "
              "ozkaynak, donen/duran varlik). Donemler AYRI kovalarda: "
              "yillik / yariyil / ceyreklik / bilanco(anlik) — FARKLI "
              "uzunluktakiler KARSILASTIRILMAZ. `ttm` alani varsa o SON 12 "
              "AYDIR, takvim donemi degil; ayri soyle.",
              {"sembol": str})
        async def finansallar(args):
            e = self._enstruman(args.get("sembol", ""))
            if not e:
                return _hata(f"{args.get('sembol')} bulunamadi")
            ozet = self.db.finansal_ozet(e["id"])
            if not any(ozet.get(k) for k in ("yillik", "yariyil",
                                             "ceyreklik", "bilanco", "ttm")):
                return _hata(f"{e['symbol']} icin temel veri yok",
                             "kriptoda tanimsiz; hisse ise `veri_topla` ile "
                             "xbrl (ABD) ya da midasbilanco (BIST) cektir")
            return _ok({"sembol": e["symbol"], **ozet})

        @tool("haberler",
              "Bir sembolun haberleri ve resmi dosyalamalari, KADEME ile. "
              "kademe 1=resmi beyan, 2=ajans/finans basini, 3-4=toplayici "
              "(KANIT DEGIL).",
              {"sembol": str, "limit": int})
        async def haberler(args):
            e = self._enstruman(args.get("sembol", ""))
            if not e:
                return _hata(f"{args.get('sembol')} bulunamadi")
            n = min(int(args.get("limit") or 12), MAX_SATIR)
            rows = self.db.query(
                """SELECT published_at, title, url, publisher, tier FROM news
                   WHERE (',' || symbols || ',') LIKE ?
                   ORDER BY (tier IN (1,2)) DESC, published_at DESC LIMIT ?""",
                (f"%,{e['symbol']},%", n))
            dosya = self.db.query(
                """SELECT published_at, category, title, url FROM disclosures
                   WHERE symbol=? ORDER BY published_at DESC LIMIT 5""",
                (e["symbol"],))
            if not rows and not dosya:
                return _hata(f"{e['symbol']} icin haber/dosyalama yok",
                             "`veri_topla` ile stocknews calistirilabilir")
            return _ok({"sembol": e["symbol"],
                        "dosyalamalar": [dict(r) for r in dosya],
                        "haberler": [dict(r) for r in rows]})

        @tool("olay_etkisi",
              "Olay calismasi: haber gunlerinde anormal getiri (AR), "
              "kumulatif AR ve t-istatistigi. |t|>2 kabaca anlamlilik "
              "esigi. KORELASYONDUR, nedensellik degil.",
              {"sembol": str})
        async def olay_etkisi(args):
            e = self._enstruman(args.get("sembol", ""))
            if not e:
                return _hata(f"{args.get('sembol')} bulunamadi")
            from ..analysis.events import haber_etkileri
            etki = haber_etkileri(self.db, e["id"], e["symbol"], limit=6)
            if not etki:
                return _hata(f"{e['symbol']} icin olcum yapilamadi",
                             "fiyat serisi (en az 40 bar) ve kademe 1-2 "
                             "haber gerekir")
            return _ok({"sembol": e["symbol"], "olcumler": etki})

        @tool("fiyat_serisi",
              "Ham gunluk kapanis serisi. Belirli bir tarihteki fiyat veya "
              "kendi hesabini yapmak icin. gun: kac gunluk (varsayilan 30).",
              {"sembol": str, "gun": int})
        async def fiyat_serisi(args):
            e = self._enstruman(args.get("sembol", ""))
            if not e:
                return _hata(f"{args.get('sembol')} bulunamadi")
            n = min(int(args.get("gun") or 30), 400)
            rows = self.db.fiyat_serisi(e["id"], n)
            if not rows:
                return _hata(f"{e['symbol']} icin fiyat serisi yok")
            return _ok({"sembol": e["symbol"],
                        "para_birimi": rows[-1]["currency"],
                        "kaynak": rows[-1]["source"],
                        "seri": [dict(r) for r in rows]})

        @tool("fx",
              "Doviz kuru: 1 <base> kac <quote> eder. Portfoy EUR, hisse "
              "fiyatlari USD, kripto USDT — bunlari BIRLESTIRMEDEN ONCE "
              "burayi cagir. tarih verilirse o tarihten onceki en yakin kur.",
              {"base": str, "quote": str, "tarih": str})
        async def fx(args):
            b = (args.get("base") or "").strip().upper()
            q = (args.get("quote") or "").strip().upper()
            if not b or not q:
                return _hata("base ve quote gerekli", "ornek: base=EUR quote=USD")
            # USDT pratikte dolara sabitlenmis; kur tablosunda USD olarak arar.
            k = self.db.fx_kuru("USD" if b == "USDT" else b,
                                "USD" if q == "USDT" else q,
                                (args.get("tarih") or "").strip() or None)
            if not k:
                return _hata(f"{b}/{q} kuru yok",
                             "`veri_topla alphavantage` ile kur serisi cekilir")
            return _ok({**k, "aciklama": f"1 {b} = {k['rate']:.6f} {q}"})

        @tool("grafik",
              "Fiyat grafigi CIZER ve kullaniciya gonderir. tur: "
              "fiyat (tek sembol + SMA20/50/200 + hacim) | "
              "karsilastirma (birden fazla sembol, normalize) | "
              "portfoy (agirlik dagilimi). "
              "sembol(ler) virgulle ayrilir. gun: kac gunluk (varsayilan 180).",
              {"tur": str, "semboller": str, "gun": int})
        async def grafik(args):
            from .. import viz
            tur = (args.get("tur") or "fiyat").strip().lower()
            gun = min(int(args.get("gun") or 180), 400)
            ham = [x.strip().upper() for x in
                   (args.get("semboller") or "").split(",") if x.strip()]
            dizin = self.s.root / "data" / "bot" / "gorseller"

            if tur == "portfoy":
                eksik = self._sahip_gerek()
                if eksik:
                    return eksik
                hesap = (ham[0].lower() if ham else "bux")
                r = viz.portfoy_grafigi(self.db, hesap, self.sahip, dizin)
                if not r:
                    return _hata(f"{hesap} hesabinda pozisyon yok")
                self.gorseller.append({"yol": r["yol"],
                                       "aciklama": f"{hesap.upper()} portfoy dagilimi"})
                return _ok({**r, "durum": "gorsel HAZIRLANDI; gonderimi dinleyici yapar"})

            if not ham:
                return _hata("sembol verilmedi", "ornek: semboller=ASML,NVDA")
            bulunan = []
            for s in ham[:6]:
                e = self._enstruman(s)
                if e:
                    bulunan.append((e["id"], e["symbol"]))
            if not bulunan:
                return _hata(f"bulunamadi: {', '.join(ham)}")

            if tur == "karsilastirma" or len(bulunan) > 1:
                r = viz.karsilastirma_grafigi(self.db, bulunan, gun, dizin)
                if not r:
                    return _hata("karsilastirma icin yeterli seri yok")
                self.gorseller.append({
                    "yol": r["yol"],
                    "aciklama": "Normalize karsilastirma (baslangic=100)"})
                return _ok({**r, "durum": "gorsel HAZIRLANDI; gonderimi dinleyici yapar"})

            iid, sem = bulunan[0]
            r = viz.fiyat_grafigi(self.db, iid, sem, gun, dizin)
            if not r:
                return _hata(f"{sem} icin yeterli fiyat serisi yok")
            self.gorseller.append({
                "yol": r["yol"],
                "aciklama": f"{sem} · {r['para_birimi']} · kaynak {r['kaynak']}"})
            return _ok({**r, "durum": "gorsel HAZIRLANDI; gonderimi dinleyici yapar"})

        @tool("kaynak_goruntusu",
              "Enstrumanin KAYNAK SAYFASINDAN canli ekran goruntusu alir ve "
              "gonderir (BIST->Midas, kripto->Binance, hisse->Yahoo). "
              "Bizim verimizle kaynagi CAPRAZ KONTROL etmek icin. "
              "Tarayici acar, 15-40 sn surer.",
              {"sembol": str})
        async def kaynak_goruntusu(args):
            import asyncio
            import json as _j
            import sys as _sys

            e = self._enstruman(args.get("sembol", ""))
            if not e:
                return _hata(f"{args.get('sembol')} bulunamadi")
            hedef = (self.s.root / "data" / "bot" / "gorseller"
                     / f"kaynak_{e['symbol']}.png")
            komut = [_sys.executable, "scripts/kaynak_goruntusu.py",
                     e["symbol"], e["venue"], str(hedef)]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *komut, cwd=str(self.s.root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
            except asyncio.TimeoutError:
                return _hata("90 sn icinde bitmedi (sayfa yavas ya da engelli)")
            except Exception as ex:                   # noqa: BLE001
                return _hata(f"alt surec hatasi: {ex}")

            try:
                sonuc = _j.loads((out or b"{}").decode().strip().splitlines()[-1])
            except Exception:                          # noqa: BLE001
                return _hata("alt surec cikti veremedi")
            if not sonuc.get("ok"):
                return _hata(sonuc.get("hata", "goruntu alinamadi"),
                             "kaynak sayfasi degismis olabilir")

            # CAPRAZ KONTROL icin bizim son degerimizi de veriyoruz ki
            # model ikisini karsilastirsin.
            seri = self.db.fiyat_serisi(e["id"], 1)
            bizim = ({"kapanis": seri[-1]["close"], "tarih": seri[-1]["ts"],
                      "para_birimi": seri[-1]["currency"],
                      "kaynak": seri[-1]["source"]} if seri else None)
            self.gorseller.append({
                "yol": sonuc["yol"],
                "aciklama": f"{e['symbol']} · KAYNAK: {sonuc['url']}"})
            return _ok({**sonuc, "bizim_verimiz": bizim,
                        "not": "Kaynak CANLI/gecikmeli, bizim veri gunluk "
                               "KAPANIS. Fark normal olabilir; buyuk fark "
                               "veri hatasina isaret eder."})

        @tool("gecmis_gorus",
              "DAHA ONCE NE DEDIGIN ve tuttu mu. Hakem cagrilari + isabet "
              "karnesi. sembol bos birakilirsa tum semboller. gun: kac "
              "gunluk gecmis (varsayilan 30, en fazla 365). "
              "'gecen hafta ne demistim', 'tuttu mu' sorularinin cevabi.",
              {"sembol": str, "gun": int})
        async def gecmis_gorus(args):
            eksik = self._sahip_gerek()
            if eksik:
                return eksik
            # `or 30` DEGIL: 0 falsy oldugu icin sessizce varsayilana
            # duserdi ve sinir kontrolu hic calismazdi. "Verilmedi" ile
            # "0 verildi" AYRI seyler.
            ham_gun = args.get("gun")
            gun = 30 if ham_gun in (None, "") else int(ham_gun)
            if not 1 <= gun <= 365:
                return _hata(f"gun {gun} sinir disinda", "1-365 arasi olmali")

            kosul, par = ["p.ajan = 'hakem'", "p.sahip = ?",
                          "p.olusma_ts >= date('now', ?)"], [self.sahip,
                                                             f"-{gun} days"]
            sem = (args.get("sembol") or "").strip()
            if sem:
                e = self._enstruman(sem)
                if not e:
                    return _hata(f"{sem} enstruman listesinde yok",
                                 "once `ara` ile dogru sembolu bul")
                kosul.append("p.instrument_id = ?")
                par.append(e["id"])

            satirlar = self.db.query(
                f"""SELECT p.olusma_ts, i.symbol, p.yon, p.guven, p.ufuk_gun,
                           p.gerekce, p.tez, p.gecersizlesme_kosulu,
                           p.tez_bozuldu_ts, p.isabet, p.getiri_pct,
                           p.anormal_pct, p.para_birimi
                    FROM predictions p JOIN instruments i ON i.id=p.instrument_id
                    WHERE {' AND '.join(kosul)}
                    ORDER BY p.olusma_ts DESC LIMIT ?""",
                (*par, MAX_SATIR + 1))

            gorusler = []
            for r in satirlar[:MAX_SATIR]:
                g = {"tarih": r["olusma_ts"], "sembol": r["symbol"],
                     "yon": r["yon"], "guven": r["guven"],
                     "ufuk_gun": r["ufuk_gun"], "gerekce": r["gerekce"],
                     "tez": r["tez"],
                     "gecersizlesme_kosulu": r["gecersizlesme_kosulu"],
                     "tez_bozuldu_ts": r["tez_bozuldu_ts"]}
                # PUANLANMAMIS TAHMINDE SONUC ALANLARI HIC YOK.
                # `null` birakmak yetmez: bos alan goren model uydurabilir,
                # OLMAYAN alani goremez. Ufuk dolmadan "tuttu/tutmadi"
                # denemez ve bunu semayla garanti ediyoruz.
                if r["isabet"] is None:
                    g["durum"] = "acik"
                    g["not"] = ("ufuk dolmadi — tutup tutmadigi BILINMIYOR, "
                                "sonucu hakkinda iddiada bulunma")
                else:
                    g["durum"] = "puanlandi"
                    g["isabet"] = bool(r["isabet"])
                    g["getiri_pct"] = r["getiri_pct"]
                    g["anormal_pct"] = r["anormal_pct"]
                gorusler.append(g)

            from ..pulse.journal import Defter
            out = {"gorusler": gorusler,
                   # KARNE OLDUGU GIBI: `yeterli_mi`, `not`, guven araligi
                   # ve `vekilsiz_n` orneklem uyarisini tasiyor. Kirpilirsa
                   # model n=3'ten "%67 isabet" diye alintilar.
                   "karne": Defter(self.db).karne(self.sahip),
                   "kapsam": f"son {gun} gun" + (f", {sem.upper()}" if sem else "")}
            if not gorusler:
                out["not"] = ("bu donemde" + (f" {sem.upper()} hakkinda" if sem
                                              else "") + " hakem gorusu yok")
            if len(satirlar) > MAX_SATIR:
                out["kirpildi"] = (f"{MAX_SATIR} kayit gosterildi, daha fazlasi "
                                   "var — `gun` daralt ya da sembol ver")
            return _ok(out)

        @tool("saat",
              "SU ANKI ZAMAN ve piyasa seanslari: hangi borsa acik, ne "
              "zaman kapaniyor. 'piyasa acik mi', 'kapandi mi', 'saat kac', "
              "'bugun hangi gun' sorularinda cagir. Zamani HAFIZANDAN "
              "soyleme — bilemezsin.",
              {})
        async def saat(args):
            from datetime import datetime, time as _time
            from zoneinfo import ZoneInfo

            simdi = datetime.now(ZoneInfo("UTC"))
            # Seans saatleri YEREL borsa saatiyle; tatil takvimi YOK ve
            # bu ACIKCA soyleniyor — "acik" demek "bugun tatil degil"
            # demek DEGIL.
            borsalar = [
                ("BIST",      "Europe/Istanbul",  _time(10, 0), _time(18, 0), "TRY"),
                ("Amsterdam", "Europe/Amsterdam", _time(9, 0),  _time(17, 40), "EUR"),
                ("ABD",       "America/New_York", _time(9, 30), _time(16, 0), "USD"),
            ]
            out = []
            for ad, tz, ac, kapa, ccy in borsalar:
                yerel = simdi.astimezone(ZoneInfo(tz))
                hafta_ici = yerel.weekday() < 5
                acik = hafta_ici and ac <= yerel.time() < kapa
                out.append({
                    "borsa": ad, "para_birimi": ccy,
                    "yerel_saat": yerel.strftime("%H:%M"),
                    "gun": yerel.strftime("%A"),
                    "durum": "acik" if acik else
                             ("hafta sonu" if not hafta_ici else
                              ("acilmadi" if yerel.time() < ac else "kapandi")),
                    "seans": f"{ac.strftime('%H:%M')}-{kapa.strftime('%H:%M')} "
                             f"({tz.split('/')[-1]} saati)"})
            return _ok({
                "utc": simdi.strftime("%Y-%m-%d %H:%M"),
                "borsalar": out,
                "uyari": ("TATIL TAKVIMI YOK. 'acik' yalnizca hafta ici ve "
                          "seans saatleri icinde demektir; resmi tatilde de "
                          "'acik' gorunur. Kesinlik gerekiyorsa bunu belirt.")})

        @tool("endeks_uyeleri",
              "Bir ENDEKSIN UYE HISSELERI: BIST 100, BIST 50, BIST 30, "
              "S&P 500, Nasdaq 100, DAX, CAC 40, AEX, BEL 20, IBEX 35. "
              "endeks bos birakilirsa hangi endeksler var ve kaci uye. "
              "'BIST100 icinden', 'S&P500'de olanlar' turu her istekte "
              "ONCE BUNU CAGIR — uyelik bilgisi HAFIZANDAN degil buradan.",
              {"endeks": str, "sirala": str})
        async def endeks_uyeleri(args):
            ad = (args.get("endeks") or "").strip()
            if not ad:
                return _ok({"endeksler": [
                    {"endeks": r["index_name"], "uye": r["n"]}
                    for r in self.db.query(
                        "SELECT index_name, COUNT(*) n FROM index_members "
                        "GROUP BY index_name ORDER BY n DESC")]})

            # Esnek eslesme: "BIST100", "bist 100", "BIST-100" hepsi olsun.
            sade = "".join(c for c in ad.upper() if c.isalnum())
            eslesen = [r["index_name"] for r in self.db.query(
                "SELECT DISTINCT index_name FROM index_members")
                if "".join(c for c in r["index_name"].upper()
                           if c.isalnum()) == sade]
            if not eslesen:
                mevcut = [r["index_name"] for r in self.db.query(
                    "SELECT DISTINCT index_name FROM index_members")]
                return _hata(f"'{ad}' diye bir endeks kaydi yok",
                             "elimdekiler: " + ", ".join(sorted(mevcut)))

            satirlar = self.db.query(
                """SELECT i.symbol, i.name, i.venue FROM index_members m
                   JOIN instruments i ON i.id = m.instrument_id
                   WHERE m.index_name = ? ORDER BY i.symbol""", (eslesen[0],))
            # Fiyat verisi OLAN uyeler ayrilir: model "hepsini tarayabilirim"
            # sanmamali. Uyelik bilgisi ile FIYAT verisi ayri seyler.
            fiyatli = {r["symbol"] for r in self.db.query(
                """SELECT DISTINCT i.symbol FROM index_members m
                   JOIN instruments i ON i.id = m.instrument_id
                   JOIN prices p ON p.instrument_id = i.id
                   WHERE m.index_name = ?""", (eslesen[0],))}
            uyeler = [{"sembol": r["symbol"], "ad": r["name"],
                       "fiyat_verisi": r["symbol"] in fiyatli}
                      for r in satirlar]
            return _ok({
                "endeks": eslesen[0], "uye_sayisi": len(uyeler),
                "fiyat_verisi_olan": len(fiyatli),
                "uyeler": uyeler,
                "not": ("Uyelik listesi TAM. Fiyat verisi olmayan uyeler "
                        "icin teknik hesap YAPILAMAZ — onlari eleme, "
                        "'verisi yok' diye ayir.")
                if len(fiyatli) < len(uyeler) else None})

        @tool("bekleyen_okumalar",
              "ONAY BEKLEYEN ekran goruntusu okumalari: kac tane, ne "
              "kadar eski, hangi hesap. 'bekleyen bir sey var mi', "
              "'onayladim mi', 'o resmi kaydettin mi' sorularinin cevabi.",
              {})
        async def bekleyen_okumalar(args):
            import time
            eksik = self._sahip_gerek()
            if eksik:
                return eksik
            bekleyen = []
            for yol in sorted(self.pending_dir.glob("*.json")):
                try:
                    v = json.loads(yol.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                # SAHIBE GORE SUZ: dosyalar tek dizinde duruyor.
                if (v.get("_sahip") or "").strip().lower() != self.sahip:
                    continue
                # OKUMA olmayan bekleyenler (rapor, silme) bu aracin
                # konusu degil — soru "o resmi kaydettin mi".
                if v.get("_tip", "pozisyon") != "pozisyon":
                    continue
                yas_sa = (time.time() - yol.stat().st_mtime) / 3600.0
                bekleyen.append({
                    "tur": v.get("_tip"), "hesap": v.get("hesap"),
                    "satir": len(v.get("pozisyonlar") or []),
                    "sembol": v.get("sembol"),
                    "yas_saat": round(yas_sa, 1)})
            out = {"bekleyen": bekleyen, "adet": len(bekleyen)}
            if not bekleyen:
                out["not"] = "onay bekleyen okuma yok"
            elif max(b["yas_saat"] for b in bekleyen) > 6:
                out["uyari"] = ("6 saatten eski bekleyen var — kullanici "
                                "hatirlamiyor olabilir, NE OLDUGUNU ozetle")
            return _ok(out)

        @tool("izleme_listesi",
              "Arastirma/izleme kapsamindaki semboller ve kimlik durumlari. "
              "'neleri takip ediyorsun', 'kapsaminda ne var' sorulari.",
              {})
        async def izleme_listesi(args):
            hedefler = self.db.research_targets()
            kimlikler = {r["symbol"]: r for r in self.db.identities()}
            liste = [{"sembol": h["symbol"], "ad": h["name"],
                      "kimlik": (kimlikler.get(h["symbol"]) or {}).get(
                          "status", "cozulmedi")}
                     for h in hedefler[:MAX_SATIR]]
            out = {"semboller": liste, "adet": len(hedefler)}
            if not hedefler:
                out["not"] = ("izleme listesi bos — portfoy ekran goruntusu "
                              "ya da `izlemeye_al` ile doldurulur")
            if len(hedefler) > MAX_SATIR:
                out["kirpildi"] = f"{MAX_SATIR}/{len(hedefler)} gosterildi"
            return _ok(out)

        @tool("rapor_uret",
              "Tam gunluk raporu URETMEYI ONAYA SUNAR. topla=true ise once "
              "veri toplar (~3 dk), false ise mevcut veriden ozet cikarir. "
              "Dogrudan calistirmaz — kullaniciya Baslat butonu gosterilir, "
              "cunku islem uzun surer ve kullanici beklemeyi SECMELI.",
              {"topla": bool})
        async def rapor_uret(args):
            eksik = self._sahip_gerek()
            if eksik:
                return eksik
            topla = bool(args.get("topla"))
            token = self._stage("rapor", {"topla": topla})
            return _ok({"durum": "onaya sunuldu", "token": token,
                        "sure": "~3 dk" if topla else "~1 dk",
                        "not": "Kullaniciya butonla soruldu. 'Uretiyorum' "
                               "DEME; 'onayina sundum' de."})

        @tool("son_kaydi_sil",
              "SON portfoy anlik goruntusunu geri almayi ONAYA SUNAR. "
              "'yanlis kaydettin', 'son kaydi geri al', 'onu sil' "
              "istekleri icin. Dogrudan silmez — onay butonu gosterilir.",
              {})
        async def son_kaydi_sil(args):
            eksik = self._sahip_gerek()
            if eksik:
                return eksik
            son = self.db.son_snapshot(self.sahip)
            if not son:
                return _hata("silinecek portfoy kaydi yok",
                             "once bir ekran goruntusu kaydedilmis olmali")
            token = self._stage("sil_son", {})
            return _ok({"durum": "onaya sunuldu", "token": token,
                        "silinecek": {"hesap": son["account"],
                                      "tarih": son["snapshot_ts"],
                                      "pozisyon": son["n"]},
                        "not": "GERI ALINAMAZ. Kullaniciya butonla soruldu; "
                               "'sildim' DEME, 'onayina sundum' de."})

        @tool("neler_yapabilirim",
              "KENDI YETENEKLERIN. Kullanici 'ne yapabilirsin', 'bunu "
              "yapabilir misin', 'nasil yaparim', 'bu nasil calisiyor' "
              "diye sordugunda ya da yeni bir kullaniciysa cagir. "
              "konu bos birakilirsa TUM basliklar; konu verilirse o "
              "alanin detayi. konu: portfoy|analiz|veri|kripto|gecmis|"
              "komutlar",
              {"konu": str})
        async def neler_yapabilirim(args):
            from . import yetenekler
            konu = (args.get("konu") or "").strip().lower()
            if konu and konu not in yetenekler.KONULAR:
                return _hata(f"'{konu}' diye bir konu yok",
                             "gecerli konular: "
                             + ", ".join(yetenekler.KONULAR))
            return _ok(yetenekler.ozet(self, konu or None))

        @tool("ipucu",
              "BIR OZELLIGI ILK KEZ ogretmek icin. Kullanici bir seyi zor "
              "yoldan yaptiysa ya da yapamadigin bir sey istediyse, "
              "CEVABINI VERDIKTEN SONRA bunu cagir ve donen metni cevabin "
              "en altina EKLE. Ayni ipucu bir kisiye BIR KEZ gider; "
              "'ver' false donerse HICBIR SEY EKLEME. "
              "kod: ekran_goruntusu|gorsel_aciklama|grafik|veri_tazele|"
              "gecmis|sesli|izleme|kaynak|rehber",
              {"kod": str})
        async def ipucu(args):
            from . import yetenekler
            eksik = self._sahip_gerek()
            if eksik:
                return eksik
            kod = (args.get("kod") or "").strip().lower()
            if kod not in yetenekler.IPUCLARI:
                return _hata(f"'{kod}' diye bir ipucu kodu yok",
                             "gecerli kodlar: "
                             + ", ".join(yetenekler.IPUCLARI))
            # KONTROL VE ISARETLEME TEK CAGRIDA — ayrilirsa ayni ipucu
            # her turda tekrar eder.
            if not self.db.ipucu_ilk_mi(self.sahip, kod):
                return _ok({"ver": False,
                            "not": "bu ipucu daha once verildi, EKLEME"})
            return _ok({"ver": True, "metin": yetenekler.IPUCLARI[kod]})

        @tool("sohbet_arsivi",
              "GECMIS SOHBETLER — kullanicinin sana yazdiklari ve senin "
              "cevaplarin. sorgu: metinde aranacak kelime (bos birakilirsa "
              "en son turlar). gun: kac gunluk (varsayilan 30, en fazla "
              "3650). 'gecen hafta ne konusmustuk', 'sana bunu sormus "
              "muydum', 'bana ne demistin' sorularinin cevabi. "
              "DIKKAT: burada yazan sey KONUSULMUS olandir, DOGRU olan "
              "degil — sayilari araclarla YENIDEN dogrula.",
              {"sorgu": str, "gun": int})
        async def sohbet_arsivi(args):
            eksik = self._sahip_gerek()
            if eksik:
                return eksik
            ham_gun = args.get("gun")
            gun = 30 if ham_gun in (None, "") else int(ham_gun)
            if not 1 <= gun <= 3650:
                return _hata(f"gun {gun} sinir disinda", "1-3650 arasi olmali")
            sorgu = (args.get("sorgu") or "").strip()

            satirlar = self.db.sohbet_ara(self.sahip, gun=gun, sorgu=sorgu,
                                          limit=MAX_SATIR + 1)
            turlar = []
            for r in satirlar[:MAX_SATIR]:
                # Arsiv TAM metni tutar ama baglama tamami sigmaz; kirpip
                # SOYLE. Sessiz kirpma, modelin yarim cumleyi tam sanip
                # uzerine yorum kurmasina yol acar.
                govde = r["metin"] or ""
                t = {"tarih": r["ts"],
                     "kim": "sen" if r["rol"] == "user" else "ben",
                     "metin": govde[:900]}
                if len(govde) > 900:
                    t["kirpildi"] = True
                if r["gorsel"]:
                    t["ekran_goruntusu_vardi"] = True
                if r["araclar"]:
                    t["kullandigim_araclar"] = r["araclar"]
                turlar.append(t)

            out = {"turlar": turlar,
                   "kapsam": f"son {gun} gun" + (f", '{sorgu}' gecenler"
                                                 if sorgu else ""),
                   "uyari": "BU KONUSULMUS OLANDIR, DOGRULANMIS DEGIL. "
                            "Buradaki bir sayiyi tekrar kullanacaksan once "
                            "ilgili araci cagirip guncel degeri al."}
            if not turlar:
                out["not"] = ("bu donemde" + (f" '{sorgu}' gecen" if sorgu
                                              else "") + " sohbet kaydi yok")
            if len(satirlar) > MAX_SATIR:
                out["kirpildi"] = (f"{MAX_SATIR} tur gosterildi, daha fazlasi "
                                   "var — `gun` daralt ya da `sorgu` ver")
            return _ok(out)

        @tool("gecmis_ozet",
              "DAHA ONCE GONDERILEN ozetler ve raporlar (nabiz + gunluk "
              "rapor). gun: kac gunluk (varsayilan 7, en fazla 90). "
              "tarih verilirse o gune en yakin kayit. "
              "'dun ne yazmistin' sorularinin cevabi.",
              {"gun": int, "tarih": str})
        async def gecmis_ozet(args):
            eksik = self._sahip_gerek()
            if eksik:
                return eksik
            ham_gun = args.get("gun")
            gun = 7 if ham_gun in (None, "") else int(ham_gun)
            if not 1 <= gun <= 90:
                return _hata(f"gun {gun} sinir disinda", "1-90 arasi olmali")
            tarih = (args.get("tarih") or "").strip()

            from ..pulse.agents import katmanlari_ayir

            def _metin(ham: str | None) -> tuple[str, bool]:
                """SADE katman; yoksa teknik. JSON blogu ASLA donmez."""
                if not ham:
                    return "", False
                sade, teknik = katmanlari_ayir(ham)
                govde = (sade or teknik).split("```json")[0].strip()
                # 7 gunluk ham metin baglami doldurur; kirpip SOYLE.
                return (govde[:1200], len(govde) > 1200)

            kayitlar = []
            if tarih:
                p_sql = ("SELECT run_ts, ham_metin FROM panel_runs WHERE "
                         "ajan='hakem' AND sahip=? "
                         "ORDER BY ABS(julianday(run_ts)-julianday(?)) LIMIT 3")
                p_par = (self.sahip, tarih)
                a_sql = ("SELECT run_ts, output_md FROM analysis_runs WHERE "
                         "sahip=? ORDER BY ABS(julianday(run_ts)-julianday(?)) "
                         "LIMIT 3")
                a_par = (self.sahip, tarih)
            else:
                p_sql = ("SELECT run_ts, ham_metin FROM panel_runs WHERE "
                         "ajan='hakem' AND sahip=? AND run_ts >= "
                         "datetime('now', ?) ORDER BY run_ts DESC LIMIT ?")
                p_par = (self.sahip, f"-{gun} days", MAX_SATIR)
                a_sql = ("SELECT run_ts, output_md FROM analysis_runs WHERE "
                         "sahip=? AND run_ts >= datetime('now', ?) "
                         "ORDER BY run_ts DESC LIMIT ?")
                a_par = (self.sahip, f"-{gun} days", MAX_SATIR)

            for r in self.db.query(p_sql, p_par):
                govde, kirpik = _metin(r["ham_metin"])
                if govde:
                    kayitlar.append({"tarih": r["run_ts"], "tur": "nabiz",
                                     "metin": govde,
                                     **({"kirpildi": True} if kirpik else {})})
            for r in self.db.query(a_sql, a_par):
                govde, kirpik = _metin(r["output_md"])
                if govde:
                    kayitlar.append({"tarih": r["run_ts"], "tur": "rapor",
                                     "metin": govde,
                                     **({"kirpildi": True} if kirpik else {})})
            kayitlar.sort(key=lambda x: x["tarih"], reverse=True)

            out = {"kayitlar": kayitlar[:MAX_SATIR],
                   "kapsam": tarih or f"son {gun} gun"}
            if not kayitlar:
                out["not"] = "bu donemde gonderilmis ozet/rapor yok"
            return _ok(out)

        @tool("gunun_hareketlileri",
              "BIST'te gunun EN COK ARTAN / EN COK AZALAN hisseleri. "
              "yon: artan|azalan (varsayilan artan). adet: kac tane (10). "
              "min_hacim_tl: ince kagitlari elemek icin esik (varsayilan "
              "50 milyon). Hacim de doner — ince kagitta buyuk yuzde, "
              "tek bir emrin izidir.",
              {"yon": str, "adet": int, "min_hacim_tl": float})
        async def gunun_hareketlileri(args):
            yon = (args.get("yon") or "artan").strip().lower()
            adet = min(int(args.get("adet") or 10), 40)
            esik = float(args.get("min_hacim_tl") or 50_000_000)
            sira = "ASC" if yon.startswith("azal") else "DESC"

            rows = self.db.query(f"""
                SELECT i.symbol, i.name, f.val AS degisim, f.period_end,
                       (SELECT h.val FROM fundamentals h
                        WHERE h.instrument_id = f.instrument_id
                          AND h.concept = 'GunlukHacimTL'
                          AND h.period_end = f.period_end) AS hacim
                FROM fundamentals f
                JOIN instruments i ON i.id = f.instrument_id
                WHERE f.concept = 'GunlukDegisimPct'
                  AND f.period_end = (SELECT MAX(period_end) FROM fundamentals
                                      WHERE concept = 'GunlukDegisimPct')
                ORDER BY f.val {sira} LIMIT 200""")
            if not rows:
                return _hata("gunluk degisim verisi yok",
                             "`veri_topla midas` ile cekilir")

            suzulen = [dict(r) for r in rows if (r["hacim"] or 0) >= esik][:adet]
            elenen = len([r for r in rows[:adet * 3] if (r["hacim"] or 0) < esik])
            return _ok({
                "yon": "azalan" if sira == "ASC" else "artan",
                "olcum_tarihi": rows[0]["period_end"],
                "min_hacim_tl": esik,
                "hisseler": suzulen,
                "likidite_suzgeciyle_elenen": elenen,
                "not": "BIST'te gunluk fiyat limiti ±%10'dur. ±%9.9 civari "
                       "bir deger 'tavan/taban yapti' demektir: emir "
                       "karsilanmadan seans kapanmis olabilir. Bunu 'cok "
                       "yukseldi' diye degil, 'karsilanmamis talep/arz var' "
                       "diye oku. Ayrica bu bir GUNLUK degisimdir; trend "
                       "icin `teknik` ile bak.",
            })

        @tool("kimlik",
              "Bir sembolun kimlik durumu: hangi sirket/coin oldugu nasil "
              "dogrulandi, Binance cifti, CoinGecko id'si, SEC CIK'i. "
              "'dogrulandi' degilse o enstrumandan VERI CEKILMEZ.",
              {"sembol": str})
        async def kimlik(args):
            e = self._enstruman(args.get("sembol", ""))
            if not e:
                return _hata(f"{args.get('sembol')} bulunamadi")
            k = self._kimlik(e["id"])
            return _ok({"sembol": e["symbol"], "ad": e["name"],
                        "venue": e["venue"], "kimlik": k or "cozulmemis"})

        # --- YAZMA / ISLEM (onay kapisindan gecer) ---------------------
        @tool("pozisyon_kaydet",
              "Portfoye pozisyon yazmayi ONAYA SUNAR. Dogrudan yazmaz — "
              "kullaniciya Kaydet/Iptal butonu gosterilir. hesap: "
              "bux|binance|midas. pozisyonlar: JSON dizi, her biri "
              "{sembol, ad, adet, deger, kz_yuzde} (deger/kz istege bagli). "
              "toplam_deger: ekranda yazan TOPLAM — kapsam kontrolu icin, "
              "eksik pozisyon varsa kullaniciya soylenir.",
              {"hesap": str, "pozisyonlar": str, "para_birimi": str,
               "toplam_deger": float})
        async def pozisyon_kaydet(args):
            eksik = self._sahip_gerek()
            if eksik:
                return eksik
            hesap = (args.get("hesap") or "").strip().lower()
            if hesap not in ("bux", "binance", "midas"):
                return _hata(f"gecersiz hesap: {hesap!r}",
                             "bux, binance veya midas")
            try:
                poz = json.loads(args.get("pozisyonlar") or "[]")
            except json.JSONDecodeError as ex:
                return _hata(f"pozisyonlar gecerli JSON degil: {ex}")
            if not isinstance(poz, list) or not poz:
                return _hata("pozisyon listesi bos")

            temiz = []
            for p in poz:
                if not isinstance(p, dict) or not p.get("sembol"):
                    return _hata(f"gecersiz pozisyon kaydi: {p!r}",
                                 "her kayitta en az 'sembol' olmali")
                temiz.append({
                    "symbol": str(p["sembol"]).strip().upper(),
                    "name": p.get("ad"),
                    "quantity": p.get("adet"),
                    "market_value": p.get("deger"),
                    "pnl_pct": p.get("kz_yuzde"),
                    "currency": args.get("para_birimi") or (
                        "USDT" if hesap == "binance" else "EUR"),
                    "asset_type": "crypto" if hesap == "binance" else None,
                })
            token = self._stage("pozisyon", {
                "hesap": hesap, "pozisyonlar": temiz,
                # Onay ozeti bunu gosteriyor; yoksa "313.08" diye birimsiz
                # bir sayi cikiyor ve hangi para biriminde oldugu kayboluyor.
                "para_birimi": temiz[0]["currency"],
                "toplam_deger": args.get("toplam_deger") or None,
                "kaynak": "sohbet (model tarafindan hazirlandi)",
            })
            return _ok({"durum": "ONAY BEKLIYOR", "token": token,
                        "hesap": hesap, "adet": len(temiz),
                        "not": "Kullaniciya Kaydet/Iptal butonu gosterildi. "
                               "'kaydettim' DEME; 'onayina sundum' de."})

        @tool("izlemeye_al",
              "Bir sembolu KAPSAMA ALIR. Kapsama giren sembol icin haber, "
              "kimlik, BIST bilancosu ve kripto tokenomigi toplanmaya "
              "baslar. BU, 'o sembolde temel veri yok' durumunun "
              "COZUMUDUR: once izlemeye_al, sonra `veri_topla` (BIST icin "
              "midasbilanco stocknews, ABD icin xbrl edgar stocknews). "
              "Geri alinabilir oldugu icin onay gerektirmez.",
              {"sembol": str, "venue": str})
        async def izlemeye_al(args):
            sem = (args.get("sembol") or "").strip().upper()
            if not sem:
                return _hata("sembol bos")
            e = self._enstruman(sem)
            if e is None:
                venue = (args.get("venue") or "").strip().upper()
                if venue not in ("BUX", "BIST", "BINANCE"):
                    return _hata(f"{sem} katalogda yok",
                                 "yeni enstruman icin venue gerekli: "
                                 "BUX, BIST veya BINANCE")
                iid = self.db.upsert_instrument(
                    sem, venue, None, "crypto" if venue == "BINANCE" else None,
                    "USDT" if venue == "BINANCE" else None)
            else:
                iid = e["id"]
            self.db.query(
                "INSERT OR IGNORE INTO watchlist (instrument_id, kind, note) "
                "VALUES (?,?,?)", (iid, "aday", "sohbet uzerinden eklendi"))
            self.db._conn.commit()
            # HANGI KAYNAKLARIN cekilecegini SOYLE. "veri_topla calistir"
            # demek yetmiyordu: 19 kaynak var ve yanlis olani secmek
            # sessizce bos sonuc uretiyor (BIST'i `prices` sanip uc mesaj
            # boyunca "boru hatti bozuk" denmesi tam boyle oldu).
            venue = (e["venue"] if e is not None else
                     (args.get("venue") or "").strip().upper())
            oneri = {"BIST": "midasbilanco stocknews kap",
                     "BINANCE": "kripto binance coingecko",
                     "CRYPTO": "kripto cgfiyat coingecko"}.get(
                         venue, "xbrl edgar stocknews")
            return _ok({"durum": "kapsama alindi", "sembol": sem,
                        "venue": venue,
                        "sirada": f"veri_topla('{oneri}')",
                        "not": ("Fiyat serisi zaten vardi (BIST'te tum "
                                "kotasyon cekiliyor); eklenen sey HABER ve "
                                "TEMEL VERI kapsami. Toplama birkac dakika "
                                "surebilir.")})

        @tool("veri_topla",
              "Collector calistirir ve VERIYI TAZELER. kaynaklar: bosluklu "
              "liste. DOGRU KAYNAGI SEC — hangisi neyi tazeliyor:\n"
              + "\n".join(f"  {a} = {k}" for a, k in _kaynak_kapsami())
              + "\nKripto icin sira: kripto binance coingecko. "
                "Uzun surebilir (10-60 sn).",
              {"kaynaklar": str})
        async def veri_topla(args):
            from ..collectors import REGISTRY
            istenen = [x for x in (args.get("kaynaklar") or "").split() if x]
            gecersiz = [x for x in istenen if x not in REGISTRY]
            if gecersiz:
                return _hata(f"bilinmeyen kaynak: {', '.join(gecersiz)}",
                             f"gecerli: {', '.join(sorted(REGISTRY))}")
            if not istenen:
                return _hata("kaynak belirtilmedi")
            # TARAYICI GEREKTIRENLER ALT SURECTE.
            #
            # Onceden bunlar reddediliyordu ("terminalden calistir") ama
            # bu gereksiz bir sinirdi: bot zaten kullanicinin makinesinde,
            # ayni venv icinde calisiyor. Tek gercek sorun Playwright'i
            # bot surecine sokmakti — bir cokme tum botu dusururdu.
            # Alt surec bunu cozer: izolasyon var, cokme bota bulasmaz,
            # zaman asimi uygulanabiliyor. SQLite tarafinda WAL +
            # busy_timeout eszamanli yazmayi karsiliyor.
            # HEPSI ALT SURECTE, ZAMAN ASIMIYLA.
            #
            # Onceden yalnizca TARAYICILI collector'lar alt surece
            # gidiyordu; digerleri bot is parcaciginda SENKRON ve
            # ZAMAN ASIMISIZ kosuyordu. Olculdu (2026-08-17 17:23):
            # model `isyatirim` cagirdi, collector 24,9 DAKIKA surdu ve
            # bu sure boyunca bot HICBIR mesaji isleyemedi — kullanicinin
            # sonraki sorusu Telegram kuyrugunda bekledi, ikinci
            # kullanici da bloke oldu. Sohbet turu icinde 25 dakikalik
            # is YAPILMAMALI.
            #
            # Alt surec uc seyi birden veriyor: zaman asimi, cokme
            # izolasyonu ve bot dongusunun serbest kalmasi. Bedeli surec
            # baslatma (~1-2 sn) ve bu, tikanmanin yaninda hicbir sey.
            sonuc = await self._alt_surecte(istenen)
            return _ok({"calistirilan": sonuc})

        return [veri_durumu, portfoy, ara, teknik, saatlik, tokenomik,
                finansallar, haberler, olay_etkisi, fiyat_serisi, fx,
                grafik, kaynak_goruntusu, gunun_hareketlileri, kimlik,
                pozisyon_kaydet, izlemeye_al, veri_topla,
                gecmis_gorus, gecmis_ozet, sohbet_arsivi,
                neler_yapabilirim, ipucu, bekleyen_okumalar,
                izleme_listesi, rapor_uret, son_kaydi_sil, endeks_uyeleri,
                saat]

    # ------------------------------------------------------------------
    def sunucu(self):
        from claude_agent_sdk import create_sdk_mcp_server
        return create_sdk_mcp_server(
            name="finagent", version="1.0.0", tools=self.araclar())


# MCP araclari "mcp__<sunucu>__<arac>" adiyla gorunur.
ARAC_ADLARI = [
    "mcp__finagent__" + a for a in (
        "veri_durumu", "portfoy", "ara", "teknik", "saatlik", "tokenomik",
        "finansallar", "haberler", "olay_etkisi", "fiyat_serisi", "fx",
        "grafik", "kaynak_goruntusu", "gunun_hareketlileri", "kimlik",
        "pozisyon_kaydet", "izlemeye_al", "veri_topla",
        "gecmis_gorus", "gecmis_ozet", "sohbet_arsivi",
        "neler_yapabilirim", "ipucu", "bekleyen_okumalar",
        "izleme_listesi", "rapor_uret", "son_kaydi_sil",
        "endeks_uyeleri", "saat",
    )
]
