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

    def __init__(self, settings, db, pending_dir):
        self.s = settings
        self.db = db
        self.pending_dir = pending_dir
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.bekleyen_token: list[str] = []   # bu turda uretilen onay istekleri

    # ------------------------------------------------------------------
    # yardimcilar
    # ------------------------------------------------------------------
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

    def _stage(self, tip: str, veri: dict) -> str:
        """Onay bekleyen islemi diske birakir, token doner."""
        token = secrets.token_hex(6)
        veri = {**veri, "_tip": tip, "_token": token}
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
            hesaplar = [dict(r) for r in self.db.query(
                """SELECT account, COUNT(DISTINCT instrument_id) pozisyon,
                          MAX(snapshot_ts) son
                   FROM positions GROUP BY account""")]
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
            istenen = (args.get("hesap") or "").strip().lower()
            hesaplar = [istenen] if istenen else [
                r["account"] for r in self.db.query(
                    "SELECT DISTINCT account FROM positions")]
            out = {}
            for h in hesaplar:
                poz = self.db.latest_positions(h)
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
            # FX uyarisi: hesaplar farkli para birimindeyse toplanamaz.
            birimler = {v["para_birimi"] for v in out.values()}
            return _ok({"hesaplar": out,
                        "uyari": ("Hesaplar FARKLI para biriminde "
                                  f"({', '.join(sorted(str(b) for b in birimler))}); "
                                  "FX serisi veride yok, tek toplama ULASILAMAZ."
                                  if len(birimler) > 1 else None)})

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
            rows = self.db.query(
                """SELECT ts, open, high, low, close, volume FROM prices
                   WHERE instrument_id=? ORDER BY ts DESC LIMIT 300""", (e["id"],))
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
              "Hisse temel verisi (SEC XBRL): gelir, marj, bilanco, EPS. "
              "Donem uzunlugu 'gun' alaninda; FARKLI uzunluktakiler "
              "karsilastirilmaz.",
              {"sembol": str})
        async def finansallar(args):
            e = self._enstruman(args.get("sembol", ""))
            if not e:
                return _hata(f"{args.get('sembol')} bulunamadi")
            ozet = self.db.finansal_ozet(e["id"])
            if not any(ozet.get(k) for k in ("yillik", "ceyreklik", "bilanco")):
                return _hata(f"{e['symbol']} icin XBRL verisi yok",
                             "SEC'e tabi olmayan sirketlerde ve kriptoda olmaz")
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
            rows = self.db.query(
                """SELECT ts, open, high, low, close, volume FROM prices
                   WHERE instrument_id=? ORDER BY ts DESC LIMIT ?""", (e["id"], n))
            if not rows:
                return _hata(f"{e['symbol']} icin fiyat serisi yok")
            return _ok({"sembol": e["symbol"],
                        "seri": [dict(r) for r in reversed(rows)]})

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
              "Bir sembolu arastirma/izleme listesine ekler. Boylece fiyat, "
              "haber ve tokenomik toplanmaya baslar. Bu islem geri "
              "alinabilir oldugu icin onay gerektirmez.",
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
            return _ok({"durum": "eklendi", "sembol": sem,
                        "not": "Veri gelmesi icin `veri_topla` calistirilmali."})

        @tool("veri_topla",
              "Collector calistirir ve VERIYI TAZELER. kaynaklar: bosluklu "
              "liste — kripto, binance, coingecko, prices, xbrl, edgar, "
              "stocknews, kap. Kripto icin sira: kripto binance coingecko. "
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
            tarayici_gerek = [x for x in istenen if REGISTRY[x].needs_browser]
            if tarayici_gerek:
                return _hata(
                    f"bu kaynaklar tarayici gerektiriyor: {', '.join(tarayici_gerek)}",
                    "sohbetten tarayicili collector calistirilamaz; "
                    "kullanici terminalden `run.py collect` calistirmali")
            sonuc = []
            for ad in istenen:
                r = REGISTRY[ad](self.s, self.db, browser=None).run()
                sonuc.append({"kaynak": ad, "durum": r.status,
                              "satir": r.rows, "not": r.error})
            return _ok({"calistirilan": sonuc})

        return [veri_durumu, portfoy, ara, teknik, saatlik, tokenomik,
                finansallar, haberler, olay_etkisi, fiyat_serisi, kimlik,
                pozisyon_kaydet, izlemeye_al, veri_topla]

    # ------------------------------------------------------------------
    def sunucu(self):
        from claude_agent_sdk import create_sdk_mcp_server
        return create_sdk_mcp_server(
            name="finagent", version="1.0.0", tools=self.araclar())


# MCP araclari "mcp__<sunucu>__<arac>" adiyla gorunur.
ARAC_ADLARI = [
    "mcp__finagent__" + a for a in (
        "veri_durumu", "portfoy", "ara", "teknik", "saatlik", "tokenomik",
        "finansallar", "haberler", "olay_etkisi", "fiyat_serisi", "kimlik",
        "pozisyon_kaydet", "izlemeye_al", "veri_topla",
    )
]
