"""Bagimliliksiz duman testleri: python -m pytest tests/ (veya dogrudan calistir)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finagent.collectors.base import extract_symbols, parse_number
from finagent.collectors.kap import _parse_kap_time
from finagent.notify.telegram import _split, md_to_tg_html
from finagent.storage.db import Database


def test_parse_number_tr_and_en():
    assert parse_number("1.234,56 TL") == 1234.56
    assert parse_number("1,234.56") == 1234.56
    assert parse_number("%-3,21") == -3.21
    assert parse_number("€ 12,50") == 12.50
    assert parse_number("450") == 450.0
    assert parse_number("—") is None
    assert parse_number(None) is None


def test_extract_symbols_word_boundary():
    wl = ["THYAO", "ASELS", "AL"]
    assert extract_symbols("THYAO bilanco acikladi", wl) == ["THYAO"]
    assert "AL" not in extract_symbols("ALARKO haberi", wl)


def test_kap_time_is_sql_comparable():
    """
    db.recent_disclosures() published_at'i datetime('now','-48 hours') ile
    karsilastiriyor. Ham 'Bugun\\n23:30' metni birakilirsa hicbir bildirim
    rapora girmez — bu yuzden ISO/UTC bicimi sozlesme sayilir.
    """
    from datetime import datetime, timedelta, timezone

    out = _parse_kap_time("13.08.2026\n17:42")
    assert out == "2026-08-13 14:42:00"          # TRT (UTC+3) -> UTC

    bugun = _parse_kap_time("Bugün\n23:30")
    assert len(bugun) == 19 and bugun[4] == "-" and bugun[13] == ":"

    dun = _parse_kap_time("Dün\n09:15")
    assert dun < bugun                            # gun kaymasi dogru yonde

    # 48 saatlik pencerenin icinde kalmali
    parsed = datetime.strptime(bugun, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    assert abs(parsed - datetime.now(timezone.utc)) < timedelta(hours=48)

    assert _parse_kap_time("") is None


def test_vision_json_extraction_survives_read_tool_noise():
    """
    Read araci ciktiya '[Image: original 1320x2868 ...]' satiri sizdiriyor ve
    model bazen ```json ile sariyor. Duz json.loads bu yuzden yetmez.
    """
    from finagent.vision.screenshot import _extract_json

    noisy = ('[Image: original 1320x2868, displayed at 921x2000.]\n'
             '{"hesap":"bux","pozisyonlar":[{"sembol":"VWCE","adet":28}]}')
    assert _extract_json(noisy)["hesap"] == "bux"

    fenced = '```json\n{"hesap":"midas","pozisyonlar":[]}\n```'
    assert _extract_json(fenced)["hesap"] == "midas"

    # ic ice suslu parantez ve string icindeki '}' bloklamamali
    tricky = 'bla {"notlar":"a } b","pozisyonlar":[{"sembol":"X"}]} son'
    assert _extract_json(tricky)["notlar"] == "a } b"

    assert _extract_json("hic json yok") is None
    assert _extract_json("") is None


def test_vision_normalise_derives_and_never_invents():
    from finagent.vision.screenshot import _normalise

    out = _normalise({
        "hesap": None, "para_birimi": None,
        "pozisyonlar": [
            # deger/kz eksik -> turetilmeli
            {"sembol": "vwce", "adet": 10, "ort_maliyet": 100, "son_fiyat": 110},
            # adet var ama fiyat yok -> KORUNUR, sayilar uydurulmaz
            {"sembol": "IWDA", "adet": 21},
            {"sembol": ""},                       # sembolsuz satir atilir
        ],
    }, account_hint="bux")

    assert out["hesap"] == "bux"
    assert out["para_birimi"] == "EUR"            # bux -> EUR varsayilani
    assert len(out["pozisyonlar"]) == 2

    a, b = out["pozisyonlar"]
    assert a["symbol"] == "VWCE"                  # buyuk harfe cevrilir
    assert a["market_value"] == 1100.0            # 10 * 110
    assert a["pnl_abs"] == 100.0                  # (110-100) * 10
    assert abs(a["pnl_pct"] - 10.0) < 1e-9

    # Kismi satir: adedi bilinir, degeri bilinmez -> ASLA hesaplanip uydurulmaz
    assert b["quantity"] == 21.0
    assert b["market_value"] is None and b["avg_cost"] is None
    assert b["pnl_abs"] is None


def test_vision_drops_cut_off_rows_and_measures_coverage():
    """
    Gercek BUX ekraninda liste altta kesiliyor: son satirin sembolu okunuyor
    ama hicbir sayisi yok. Bu satir pozisyon olarak yazilirsa hayalet kayit
    olur; ayrica ekrandaki toplam ile okunan toplam arasindaki fark
    kullaniciya bildirilmeli, yoksa portfoyun bir kismi sessizce kaybolur.
    """
    from finagent.vision.screenshot import _normalise

    out = _normalise({
        "hesap": "bux", "para_birimi": "EUR", "toplam_deger": 5929.78,
        "pozisyonlar": [
            {"sembol": "ASML", "adet": 1.534692, "deger": 2424.20},
            {"sembol": "NVDA", "adet": 3.73178, "deger": 726.23},
            {"sembol": "NOW"},                       # ekran kesik -> veri yok
        ],
    }, account_hint=None)

    assert [r["symbol"] for r in out["pozisyonlar"]] == ["ASML", "NVDA"]
    assert out["eksik_satirlar"] == ["NOW"]
    assert abs(out["okunan_toplam"] - 3150.43) < 1e-6


def test_vision_cross_check_flags_row_bleed():
    """
    Gercek hata: model iShares Automation'in +42.42'sini alt satirdaki
    Nasdaq 100'un +33.07'si ile karistirdi. Tek gecişte fark edilemezdi.
    Iki bagimsiz okuma ayrisinca alan BOS birakilmali ve celiski
    kullaniciya bildirilmeli — sessizce birini secmek en kotusu.
    """
    from finagent.vision.screenshot import _merge_passes, _yakin

    def gecis(rbot_pct):
        return {
            "hesap": "bux", "para_birimi": "EUR", "toplam_deger": 5929.78,
            "guven": "orta", "notlar": None, "eksik_satirlar": [],
            "pozisyonlar": [
                {"symbol": "RBOT", "name": "iShares Automation", "quantity": 14.986254,
                 "avg_cost": None, "last_price": None, "market_value": 284.86,
                 "pnl_abs": None, "pnl_pct": rbot_pct, "currency": "EUR",
                 "asset_type": None},
                {"symbol": "CNDX", "name": "iShares Nasdaq 100", "quantity": 2.047587,
                 "avg_cost": None, "last_price": None, "market_value": 516.61,
                 "pnl_abs": None, "pnl_pct": 33.07, "currency": "EUR",
                 "asset_type": None},
            ],
        }

    out = _merge_passes([gecis(33.07), gecis(42.42)])
    rbot = next(r for r in out["pozisyonlar"] if r["symbol"] == "RBOT")
    cndx = next(r for r in out["pozisyonlar"] if r["symbol"] == "CNDX")

    assert rbot["pnl_pct"] is None                  # celiskili -> yazilmaz
    assert rbot["market_value"] == 284.86           # uyusan alan korunur
    assert cndx["pnl_pct"] == 33.07                 # celiskisiz satir etkilenmez
    assert out["guven"] == "dusuk"
    assert any("RBOT" in c and "K/Z %" in c for c in out["celiskiler"])

    # Iki okuma uyusursa hicbir sey isaretlenmemeli
    temiz = _merge_passes([gecis(42.42), gecis(42.42)])
    assert temiz["celiskiler"] == []
    assert next(r for r in temiz["pozisyonlar"] if r["symbol"] == "RBOT")["pnl_pct"] == 42.42

    # Kuruş farki celiski sayilmaz, anlamli fark sayilir
    assert _yakin(284.86, 284.86) and _yakin(100.0, 100.004)
    assert not _yakin(33.07, 42.42)
    assert not _yakin(None, 5.0)


def test_vision_cash_becomes_a_position():
    """
    BUX portfoy toplami nakdi de icerir. Nakit pozisyon olarak eklenmezse
    agirliklar yalnizca menkul kiymetler uzerinden hesaplanir ve her hisse
    sisik gorunur; ayrica 'kapsam eksik' uyarisi hicbir zaman susmaz.
    """
    from finagent.vision.screenshot import _normalise

    out = _normalise({
        "hesap": "bux", "para_birimi": "EUR", "toplam_deger": 5929.78,
        "nakit": 503.45,
        "pozisyonlar": [{"sembol": "ASML", "adet": 1.534692, "deger": 2424.20}],
    }, account_hint=None)

    cash = [r for r in out["pozisyonlar"] if r["symbol"] == "CASH"]
    assert len(cash) == 1
    assert cash[0]["market_value"] == 503.45
    assert cash[0]["asset_type"] == "cash"
    assert cash[0]["quantity"] is None            # nakdin 'adedi' yok
    assert out["okunan_toplam"] == 2424.20 + 503.45

    # nakit yoksa CASH satiri uydurulmaz
    yok = _normalise({"hesap": "bux", "pozisyonlar": [
        {"sembol": "ASML", "deger": 10.0}]}, account_hint=None)
    assert all(r["symbol"] != "CASH" for r in yok["pozisyonlar"])


def test_bot_name_key_matches_same_company():
    """ING/INGA ayni sirket -> ayni sembolle kaydedilmeli, cift sayilmamali."""
    from finagent.bot.listener import _ad_anahtari

    assert _ad_anahtari("ING") == _ad_anahtari("ING")
    assert _ad_anahtari("Amazon.com") == _ad_anahtari("Amazon com")
    assert _ad_anahtari("iShares Nasdaq 100") == _ad_anahtari("iSHARES NASDAQ 100")
    assert _ad_anahtari("ASML") != _ad_anahtari("Adyen")
    assert _ad_anahtari(None) == "" and _ad_anahtari("") == ""


def test_identity_rejects_wrong_company_on_ticker_collision():
    """
    En tehlikeli hata: ticker tutar ama SIRKET BASKADIR. Gercek ornekler
    portfoyden cikti — bunlar sessizce gecerse yanlis sirketin bilancosu
    ve haberi rapora girer, rapor tutarli gorunur ve kimse fark etmez.
    """
    from finagent.research.identity import _ayni_sirket, fon_mu, _fon_anahtari

    # Yanlis eslesmeler REDDEDILMELI
    assert not _ayni_sirket("Avantium", "Avalo Therapeutics, Inc.")
    assert not _ayni_sirket("iShares Automation & Robotics", "Vicarious Surgical Inc.")
    # DIKKAT: bu ikisi ilk belirtec kuralina gore ESLESIR (VANGUARD == VANGUARD)
    # ama ayni tuzel kisi DEGILLER. Ad kurali tek basina yetmiyor; fonlari
    # SEC sirket aramasindan tamamen cikaran fon_mu() korumasi bu yuzden var.
    assert _ayni_sirket("Vanguard S&P 500", "Vanguard Green Investment Ltd")
    assert fon_mu("Vanguard S&P 500", None)      # -> SEC yoluna hic girmez

    # Dogru eslesmeler KABUL EDILMELI (hukuki ekler goz ardi)
    assert _ayni_sirket("NVIDIA", "NVIDIA CORP")
    assert _ayni_sirket("Amazon.com", "AMAZON COM INC")
    assert _ayni_sirket("ING", "ING GROEP NV")
    assert _ayni_sirket("Marvell Technology", "Marvell Technology, Inc.")
    assert _ayni_sirket("ServiceNow", "ServiceNow, Inc.")
    assert not _ayni_sirket(None, "NVIDIA CORP")

    # Fonlar SEC sirket kaydiyla eslestirilmemeli
    assert fon_mu("Vanguard S&P 500", None)
    assert fon_mu("iShares Nasdaq 100", None)
    assert fon_mu("Invesco EQQQ Nasdaq-100", None)
    assert not fon_mu("Palantir", None)
    assert not fon_mu("Tesla", "equity")

    # Fon adi sirasiz eslesir; pay sinifi (acc/dist) AYIRT EDICI kalir
    assert _fon_anahtari("iShares Automation & Robotics") == \
           _fon_anahtari("Automation & Robotics ETF (iShares)")
    assert _fon_anahtari("Vanguard S&P 500") == _fon_anahtari("S&P 500 Index ETF (Vanguard)")
    assert _fon_anahtari("Vanguard S&P 500") != _fon_anahtari("Vanguard S&P 500 (acc)")


def test_bot_temp_symbol_cannot_collide_with_real_ticker():
    """Ticker ekranda yoksa uretilen anahtar gercek bir ticker'a benzememeli."""
    from finagent.bot.listener import _gecici_sembol

    assert _gecici_sembol("Airbus SE").startswith("~")
    assert _gecici_sembol("Novo Nordisk") == "~NOVONORDISK"
    assert _gecici_sembol("") == "~BILINMEYEN"


def test_source_tiering_separates_evidence_from_noise():
    """
    Olculdu: enstruman bazli ham haber akisinin ~%80'i icerik ciftligi.
    Filtresiz verilirse model MarketBeat/Motley Fool metinleri uzerinden
    "analiz" uretir. Izin listesi mantigi bozulursa bu test duser.
    """
    from finagent.research.sources import kademe, guvenilir

    assert kademe("Reuters") == 2 and kademe("CNBC") == 2 and kademe("WSJ") == 2
    assert kademe("Bloomberg") == 2 and kademe("Morningstar") == 2

    # Toplayici: kendi habercilik yapmaz
    assert kademe("Yahoo Finance") == 3
    assert kademe("Investing.com") == 3

    # Promosyon/icerik ciftligi: ASLA kanit degil
    for p in ("The Motley Fool", "MarketBeat", "Zacks", "Seeking Alpha",
              "Stocktwits", "Kalkine Media", "simplywall.st"):
        assert kademe(p) == 4, p
        assert not guvenilir(p), p

    # Listede olmayan -> varsayilan REDDET (0), kanit sayilmaz
    assert kademe("Breakingthenews.net") == 4      # acikca listelendi
    assert kademe("Rastgele Blog 123") == 0
    assert not guvenilir("Rastgele Blog 123")
    assert not guvenilir(None)


def test_company_channel_only_counts_corporate_announcements():
    """
    Sirketin kendi yayini olmasi YATIRIM kanidi yapmaz. Gercek ornekler:
      aws.amazon.com/blogs/...      -> muhendislik blogu
      nvidia.com/geforce/news/...   -> oyun duyurusu ('/news' iceriyor!)
    Ikisi de kanit degil; IR/haber odasi ise kanittir.
    """
    from finagent.research.sources import sirket_kaynagi_kademe as f

    assert f("NVIDIA Newsroom", "NVIDIA",
             "https://nvidianews.nvidia.com/news/nvidia-partners-with-apollo") == 1
    assert f("Microsoft Source", "Microsoft",
             "https://news.microsoft.com/2026/08/ai-chip") == 1

    assert f("Amazon Web Services (AWS)", "Amazon.com",
             "https://aws.amazon.com/tr/blogs/security/acm") == 3
    assert f("NVIDIA", "NVIDIA",
             "https://www.nvidia.com/en-us/geforce/news/gears-of-war-beta") == 3
    assert f("ING Think", "ING", "https://think.ing.com/articles/inflation") == 3

    # Sirketin kendi kanali degilse bu kural hic uygulanmaz
    assert f("Reuters", "Tesla", "https://www.reuters.com/x") is None
    # Sirketi elestiren bagimsiz site sirketin kendisi sayilmaz
    assert f("Royal Dutch Shell Plc .com", "Shell plc", "https://x/news") is None


def test_name_key_merges_aliases_but_keeps_distinct_companies():
    """
    Ayni sirketin farkli yazimi birlesmeli, FARKLI sirketler ayrilmali.
    Ilk-kelime anahtari Siemens / Siemens Energy / Siemens Healthineers
    uclusunu tek sirkete indiriyordu — ikisinin verisi kayboluyordu.
    """
    from finagent.storage.db import _ad_anahtari as k

    assert k("ASML") == k("ASML Holding") == k("ASML Holding NV")
    assert k("ING") == k("ING Group") == k("ING GROEP NV")
    assert k("Adyen") == k("Adyen N.V.")

    assert k("Siemens") != k("Siemens Energy")
    assert k("Siemens Energy") != k("Siemens Healthineers")
    assert k("Amazon.com") != k("Amazon Web Services")
    assert k(None) == "" and k("") == ""


def test_fundamentals_never_mix_period_lengths():
    """
    XBRL tuzagi: ayni kavram ayni dosyalamada birden fazla donem tasiyor.
    NVDA 2026 Q2 10-Q'sunda "Revenues" hem 181 gunluk (6 aylik kumulatif)
    hem 90 gunluk (ceyregin kendisi) geliyor. Filtresiz sorgu ikisini
    karistirir ve "gelir %49 dustu" gibi tamamen yanlis sonuc uretir.
    """
    import tempfile
    from pathlib import Path as P

    db = Database(P(tempfile.mkdtemp()) / "t.db")
    db.init_schema()
    iid = db.upsert_instrument("NVDA", "BUX", name="Nvidia")

    # (instrument, concept, unit, start, end, days, val, form, fy, fp, frame, filed, accn)
    db.upsert_fundamentals([
        (iid, "Revenues", "USD", "2025-01-27", "2025-07-27", 181,
         90_805_000_000, "10-Q", 2026, "Q2", None, "2025-08-27", "a1"),
        (iid, "Revenues", "USD", "2025-04-28", "2025-07-27", 90,
         46_743_000_000, "10-Q", 2026, "Q2", "CY2025Q2", "2025-08-27", "a1"),
        (iid, "Revenues", "USD", "2025-01-27", "2026-01-25", 363,
         215_938_000_000, "10-K", 2026, "FY", "CY2025", "2026-02-25", "a2"),
        (iid, "Assets", "USD", None, "2026-01-25", None,
         206_803_000_000, "10-K", 2026, "FY", None, "2026-02-25", "a2"),
    ])

    ceyrek = db.finansal_seri(iid, ["Revenues"], "ceyrek")
    assert [r["days"] for r in ceyrek] == [90]          # 181 ve 363 DISARIDA
    assert ceyrek[0]["val"] == 46_743_000_000

    yillik = db.finansal_seri(iid, ["Revenues"], "yillik")
    assert [r["days"] for r in yillik] == [363]
    assert yillik[0]["val"] == 215_938_000_000

    anlik = db.finansal_seri(iid, ["Assets"], "anlik")
    assert len(anlik) == 1 and anlik[0]["days"] is None

    # Ozet de ayni ayrimi korumali: ceyreklik listede kumulatif olmamali
    ozet = db.finansal_ozet(iid)
    assert all(d["gun"] == 90 for d in ozet["ceyreklik"])
    assert all(d["gun"] == 363 for d in ozet["yillik"])
    db.close()


def test_prices_refuse_unresolved_identity():
    """
    Kimligi cozulemeyen enstrumandan fiyat cekilmemeli. Gercek olay:
    "Avantium" icin tahmin edilen AVTX sembolu Yahoo'da "Avalo Therapeutics"
    (ABD biyotek) ve 500 bar yanlis sirket fiyati cekildi. Yanlis fiyat
    eksik fiyattan tehlikelidir — RSI/SMA/getiri hepsi hesaplanir, hepsi
    yanlis cikar ve tutarli gorunur.
    """
    from finagent.collectors.prices import PriceCollector as PC

    def hedef(sym, tur="equity"):
        return {"symbol": sym, "asset_type": tur}

    class K(dict):
        def __getitem__(self, k): return self.get(k)

    # eslesmedi -> REDDET
    assert PC._yahoo_sembolu(hedef("AVTX"), K(status="eslesmedi", sec_ticker=None)) is None
    # dogrulandi -> SEC ticker kullan (ABD kotasyonu kesin)
    assert PC._yahoo_sembolu(hedef("SPACEX"), K(status="dogrulandi", sec_ticker="SPCX")) == "SPCX"
    assert PC._yahoo_sembolu(hedef("ASML"), K(status="elle", sec_ticker="ASML")) == "ASML"
    # kimlik yok ama sembol borsa sonekli -> katalog sembolu kullanilabilir
    assert PC._yahoo_sembolu(hedef("AIR.PA"), None) == "AIR.PA"
    # gecici anahtar ve nakit -> REDDET
    assert PC._yahoo_sembolu(hedef("~NOVONORDISK"), None) is None
    assert PC._yahoo_sembolu(hedef("CASH", "cash"), None) is None


def test_voice_cannot_trigger_destructive_commands():
    """
    Konusma tanima hata yapiyor ("portfoyumde" -> "port foyumde"). Yanlis
    duyulan tek kelime yuzunden /sil calisip portfoy kaydini silmemeli.
    """
    from finagent.bot.listener import sesle_calistirilmaz as f

    assert f("sil") == "sil"
    assert f("/sil") == "sil"
    assert f("  Sil  ") == "sil"
    assert f("unut") == "unut"

    # Yikici olmayanlar serbest — sesle komut kullanilabilmeli
    assert f("rapor") is None
    assert f("portfoy durumu ne") is None
    # Yikici kelime BASTA degilse tetiklenmez
    assert f("silinen bir sey var mi") is None
    assert f("bunu sil demedim") is None
    assert f("") is None and f(None) is None


def test_bot_coverage_warning_fires_only_when_incomplete():
    from finagent.bot.listener import _kapsam, _kapsam_uyarisi

    assert _kapsam(3909.40, 5929.78) < 0.98          # eksik
    assert _kapsam(5929.78, 5929.78) == 1.0
    assert _kapsam(100.0, None) is None              # ekranda toplam yoksa sessiz
    assert _kapsam(None, 500.0) is None

    eksik = _kapsam_uyarisi({"okunan_toplam": 3909.40, "toplam_deger": 5929.78,
                             "para_birimi": "EUR"})
    assert any("%34" in line for line in eksik)

    assert _kapsam_uyarisi({"okunan_toplam": 5929.78, "toplam_deger": 5929.78,
                            "para_birimi": "EUR"}) == []


def test_bot_money_and_quantity_formatting():
    """3612.0 bir tutar; '3,612' degil '3,612.00' yazilmali."""
    from finagent.bot.listener import _money, _qty

    assert _money(3612.0) == "3,612.00"
    assert _money(923) == "923.00"
    assert _money(None) == "—"
    assert _qty(28.0) == "28"                     # adet tam sayiysa sade
    assert _qty(0.4521) == "0.4521"
    assert _qty(None) == "—"


def test_telegram_markdown_conversion():
    out = md_to_tg_html("## Ozet\n- **kalin** madde\n| a | b |\n|---|---|")
    assert "<b>Ozet</b>" in out
    assert "• <b>kalin</b> madde" in out
    assert "|" not in out          # tablolar atlanir


def test_telegram_split_respects_limit():
    chunks = _split("\n".join(f"satir {i}" * 20 for i in range(200)), 3800)
    assert all(len(c) <= 3800 for c in chunks)
    assert len(chunks) > 1


def test_cli_site_list_matches_registry():
    """run.py'daki SITES listesi REGISTRY ile ayni kalmali."""
    import importlib.util
    from finagent.collectors import REGISTRY

    spec = importlib.util.spec_from_file_location(
        "runpy_mod", Path(__file__).resolve().parents[1] / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert sorted(mod.SITES) == sorted(REGISTRY.keys())


def test_cli_imports_without_heavy_deps():
    """pandas/playwright kurulu olmasa bile CLI ayaga kalkmali."""
    import importlib.util

    blocked = {"pandas", "numpy", "playwright", "feedparser"}

    class Blocker:
        def find_module(self, name, path=None):
            return None

        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in blocked:
                raise ImportError(f"simule edilmis eksik bagimlilik: {name}")
            return None

    saved = {k: v for k, v in sys.modules.items() if k.split(".")[0] in blocked}
    for k in saved:
        del sys.modules[k]
    sys.meta_path.insert(0, Blocker())
    try:
        spec = importlib.util.spec_from_file_location(
            "runpy_light", Path(__file__).resolve().parents[1] / "run.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)          # patlarsa test duser
        assert hasattr(mod, "main")
    finally:
        sys.meta_path.pop(0)
        sys.modules.update(saved)


def test_db_roundtrip(tmp_path=None):
    import tempfile
    d = Path(tempfile.mkdtemp())
    db = Database(d / "t.db")
    db.init_schema()

    iid = db.upsert_instrument("THYAO", "BIST", name="Turk Hava Yollari")
    assert db.upsert_instrument("THYAO", "BIST") == iid      # idempotent

    rows = [{"ts": "2026-08-1%d" % i, "close": 100 + i, "open": 99 + i,
             "high": 101 + i, "low": 98 + i, "volume": 1000 * i} for i in range(1, 5)]
    assert db.upsert_prices(iid, rows, "test") == 4
    assert db.upsert_prices(iid, rows, "test") == 4          # tekrar yazim cakismaz
    assert len(db.price_history("THYAO")) == 4

    n = db.insert_positions("bux", "2026-08-14T09:00:00+00:00", [
        {"symbol": "VWCE", "quantity": 10, "avg_cost": 100, "last_price": 110,
         "market_value": 1100, "pnl_abs": 100, "pnl_pct": 10, "currency": "EUR"}])
    assert n == 1
    assert db.latest_positions("bux")[0]["symbol"] == "VWCE"

    assert db.upsert_news([{"url": "https://x/1", "title": "t", "source": "s",
                            "published_at": "2026-08-14 08:00:00", "symbols": ["THYAO"]}]) == 1
    db.close()


def _seri(n=200, gunluk=0.0, sok=None, sok_idx=None, oynaklik=0.01):
    """
    Sentetik fiyat serisi: trend + DETERMINISTIK gurultu + istege bagli sok.

    Gurultu sart: sifir oynaklikli seride t-istatistigi tanimsiz kalir ve
    "anlamli mi" sorusu test edilemez. Deterministik (sinus) gurultu hem
    gercekci sapma verir hem testi tekrarlanabilir tutar.
    """
    import math as _m
    barlar, fiyat = [], 100.0
    for i in range(n):
        g = gunluk + oynaklik * _m.sin(i * 2.399963)
        if sok_idx is not None and i == sok_idx:
            g += sok
        fiyat *= (1 + g)
        barlar.append({"ts": f"2025-01-01+{i:03d}", "close": round(fiyat, 6)})
    return barlar


def test_olay_etkisi_soku_yakalar():
    """Sabit trendde tek gunluk %10 sok, CAR olarak ~%10 gorunmeli."""
    from finagent.analysis.events import olay_etkisi
    barlar = _seri(200, gunluk=0.001, sok=0.10, sok_idx=160)
    r = olay_etkisi(barlar, barlar[160]["ts"])
    assert r is not None
    # Pencere t-1..t+3; sok disindaki gunler gurultu, o yuzden genis bant.
    assert 6.0 < r["car_%"] < 14.0, r["car_%"]
    assert r["t_istatistigi"] is not None and r["t_istatistigi"] > 2
    assert r["anlamli_mi"] is True
    assert "KORELASYON" in r["uyari"]        # nedensellik iddiasi ASLA


def test_olay_etkisi_gurultuyu_anlamli_saymaz():
    """Sok yoksa CAR ~0 ve 'anlamli' isaretlenmemeli."""
    from finagent.analysis.events import olay_etkisi
    r = olay_etkisi(_seri(200, gunluk=0.002), "2025-01-01+160")
    assert r["t_istatistigi"] is not None
    assert abs(r["t_istatistigi"]) < 2, r["t_istatistigi"]
    assert r["anlamli_mi"] is False


def test_olay_etkisi_yetersiz_veride_none():
    """Kismi veriyle sayi uretmek, sayi uretmemekten kotudur."""
    from finagent.analysis.events import olay_etkisi
    assert olay_etkisi(_seri(20), "2025-01-01+010") is None      # cok az bar
    assert olay_etkisi(_seri(200), "2025-01-01+005") is None     # tahmin penceresi yok
    assert olay_etkisi(_seri(200), "2099-01-01") is None         # seri disi tarih


def test_olay_etkisi_tatil_gununu_kaydirir():
    """Olay gunu islem gunu degilse SONRAKI islem gunu kullanilir."""
    from finagent.analysis.events import olay_etkisi
    barlar = _seri(200, gunluk=0.001, sok=0.08, sok_idx=160)
    r = olay_etkisi(barlar, "2025-01-01+159z")   # 159 ile 160 arasinda, seride yok
    assert r["islem_gunu"] == "2025-01-01+160"


def test_haber_kopyalari_tek_satira_iner():
    """
    Ayni makale once google yonlendirmesi, sonra cozulmus linkle gelir.
    Iki satir olursa olay-etki ayni olayi iki kez sayar.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        ortak = {"title": "Nvidia discloses stake", "source": "rss",
                 "published_at": "2026-08-14 21:45:19", "publisher": "CNBC", "tier": 2}
        db.upsert_news([{**ortak, "url": "https://news.google.com/rss/articles/AAA",
                         "symbols": ["NVDA"]}])
        db.upsert_news([{**ortak, "url": "https://www.cnbc.com/2026/08/14/nvidia",
                         "symbols": ["SPACEX"]}])
        satir = db.query("SELECT url, symbols FROM news")
        assert len(satir) == 1, satir
        assert "news.google.com" not in satir[0]["url"]          # cozulmus link kazanir
        assert set(satir[0]["symbols"].split(",")) == {"NVDA", "SPACEX"}
        db.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
    print("\nTum duman testleri gecti.")
