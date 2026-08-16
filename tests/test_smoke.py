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


def test_kripto_kimligi_sarmalanmis_klonu_secmez():
    """
    Kriptoda ticker cakismasi NORMDUR. Ad eslestirmesi once alt-dize
    kuraliyla yazilmisti ve "Bitcoin Cash"i BTC olarak kabul ediyordu.
    """
    from finagent.research.crypto_identity import _ayni_coin
    assert _ayni_coin("Oasis Network", "Oasis Network", "ROSE")
    assert _ayni_coin("Enjin Coin", "Enjin", "ENJ")        # sus eki atilir
    assert _ayni_coin("Oasis Network", "Oasis", "ROSE")
    # Ayirt edici kelimeler sus DEGILDIR:
    assert not _ayni_coin("Bitcoin Cash", "Bitcoin", "BTC")
    assert not _ayni_coin("Ethereum Classic", "Ethereum", "ETH")
    assert not _ayni_coin("Bitcoin", "Bitcoin Gold", "BTC")
    assert not _ayni_coin("Solice", "Solana", "SOL")
    assert not _ayni_coin(None, "Solana", "SOL")           # ad yoksa dogrulama yok
    assert not _ayni_coin("Solana", None, "SOL")


def test_kripto_fiat_ve_stabil_ayrilir():
    """EUR bir coin degil; stabilcoinde fiyat analizi anlamsiz."""
    from finagent.research.crypto_identity import CryptoResolver
    r = CryptoResolver(http=None)          # ag cagrisi yapilmadan donmeli
    assert r.coz("EUR", "Euro")["status"] == "fiat"
    assert r.coz("USDT", "Tether")["status"] == "stabil"


def test_teknik_ozet_kurus_alti_fiyati_yok_etmez():
    """
    Sabit 2 haneli yuvarlama ROSE (0.0055 USD) icin kapanisi ve TUM
    hareketli ortalamalari "0.01" yapiyordu; seviye analizi imkansizdi.
    Daha kotusu trend de yuvarlanmis degerlerle belirlendigi icin zorla
    "yatay/kararsiz" cikiyordu.
    """
    import pandas as pd
    from finagent.analysis import compute_indicators, technical_snapshot
    # Belirgin DUSUS: fiyat butun ortalamalarin altina iniyor.
    n = 260
    kapanis = [0.02 - 0.00005 * i for i in range(n)]
    df = pd.DataFrame({
        "ts": [f"2025-01-{i:03d}" for i in range(n)],
        "open": kapanis, "high": kapanis, "low": kapanis,
        "close": kapanis, "volume": [1000.0] * n,
    })
    t = technical_snapshot("ROSE", compute_indicators(df, {}))
    assert t["kapanis"] != 0.01 and t["kapanis"] > 0
    # Ortalamalar birbirinden AYIRT EDILEBILIR olmali
    assert len({t["sma20"], t["sma50"], t["sma200"]}) == 3, t
    assert t["trend"].startswith("dusus"), t["trend"]

    # Yuksek fiyatli hissede davranis degismemeli
    kapanis2 = [100.0 + 0.5 * i for i in range(n)]
    df2 = df.assign(close=kapanis2, open=kapanis2, high=kapanis2, low=kapanis2)
    t2 = technical_snapshot("NVDA", compute_indicators(df2, {}))
    assert t2["trend"].startswith("yukselis")
    assert abs(t2["kapanis"] - kapanis2[-1]) < 0.01


def test_kripto_hisse_hedeflerine_sizmaz():
    """
    Kripto sembolu hisse collector'larina giderse YANLIS VERI ceker:
    "Bitcoin" SEC'de aranir, BTC Yahoo'da baska enstrumana denk gelir.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        for sym, venue, ad in [("BTC", "BINANCE", "Bitcoin"),
                               ("NVDA", "BUX", "NVIDIA")]:
            iid = db.upsert_instrument(sym, venue, ad, "crypto", "USDT")
            db.query("INSERT INTO watchlist (instrument_id, kind) VALUES (?,?)",
                     (iid, "aday"))
        db._conn.commit()
        assert [r["symbol"] for r in db.research_targets()] == ["NVDA"]
        assert [r["symbol"] for r in db.research_targets(kripto=True)] == ["BTC"]
        assert len(db.research_targets(kripto=None)) == 2
        db.close()


def test_anlik_finansal_kayit_tekrar_eklenmez():
    """
    PRIMARY KEY icindeki `days` NULL oldugunda SQLite cakisma gormuyor
    (NULL != NULL) ve bilanco kalemleri her calismada YENIDEN ekleniyordu.
    Olculdu: tek donem 10 satira cikmisti.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("ROSE", "BINANCE", "Oasis Network", "crypto")
        satir = [(iid, "PiyasaDegeri", "USD", None, "2026-08-15", None,
                  43425544.0, "coingecko", None, None, None, "2026-08-15", "oasis")]
        db.upsert_fundamentals(satir)
        db.upsert_fundamentals(satir)
        db.upsert_fundamentals(satir)
        n = db.query("SELECT COUNT(*) c FROM fundamentals")[0]["c"]
        assert n == 1, f"anlik kayit {n} kez yazildi"
        db.close()


def test_saatlik_barlar_gunluk_tablodan_ayri():
    """
    `prices`'i okuyan hicbir sorgu source filtresi kullanmiyor ve hepsi
    gunluk bar varsayiyor. Saatlik satirlar oraya karissa RSI/SMA/CAR
    sessizce yanlis hesaplanirdi.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("BTC", "BINANCE", "Bitcoin", "crypto")
        db.upsert_prices(iid, [{"ts": "2026-08-14", "close": 63043.56}], "binance")
        db.upsert_prices_hourly(iid, [
            {"ts": "2026-08-15 09:00", "close": 63100.0, "quote_volume": 1e6},
            {"ts": "2026-08-15 10:00", "close": 63200.0, "quote_volume": 2e6}],
            "binance")
        assert db.query("SELECT COUNT(*) c FROM prices")[0]["c"] == 1
        assert len(db.saatlik_seri(iid)) == 2
        assert db.saatlik_seri(iid)[0]["ts"] < db.saatlik_seri(iid)[1]["ts"]
        db.close()


def test_binance_kapanmamis_mumu_atar():
    """
    Son mum hala olusuyordur; 'close' o anki fiyattir ve her cagrida
    degisir. Kapanmis bar gibi kaydedilirse gostergeler her calismada
    baska sonuc verir.
    """
    from finagent.collectors.binance import _mumlari_coz
    ham = [
        [1786784400000, "1.0", "1.1", "0.9", "1.05", "100", 1786787999999, "105", 5],
        [1786788000000, "1.05", "1.2", "1.0", "1.15", "200", 1786791599999, "230", 9],
        [1786791600000, "1.15", "1.2", "1.1", "1.18", "50", 1786795199999, "59", 3],
    ]
    g = _mumlari_coz(ham, saatlik=False)
    s = _mumlari_coz(ham, saatlik=True)
    assert len(g) == 2 and len(s) == 2          # sonuncusu atildi
    assert g[0]["ts"] == "2026-08-15"           # gunluk: yalnizca tarih
    assert s[0]["ts"].endswith(":00")           # saatlik: saat de var
    assert s[0]["quote_volume"] == 105.0 and s[0]["trades"] == 5
    assert _mumlari_coz([], saatlik=False) == []


def test_sil_yalnizca_tek_snapshot_siler():
    """
    /sil TUM hesaplarin son anlik goruntusunu siliyordu. 2026-08-15'te
    Binance kaydi iptal edildikten sonra /sil calistirildi ve BUX'un 18
    pozisyonu da silindi (BUX'ta tek snapshot vardi -> tablo bosaldi).
    "Geri al" TEK islemi geri almalidir.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.bot.listener import FinBot
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        db.insert_positions("bux", "2026-08-14T22:04:28+00:00", [
            {"symbol": "ASML", "quantity": 5, "market_value": 2424.20,
             "currency": "EUR"}])
        db.insert_positions("binance", "2026-08-15T12:13:00+00:00", [
            {"symbol": "ROSE", "quantity": 56741.3579, "market_value": 309.24,
             "currency": "USDT"}])
        bot = FinBot.__new__(FinBot); bot.db = db

        cikti = bot._sil_son()
        assert "BINANCE" in cikti                       # en son yazilan
        assert len(db.latest_positions("binance")) == 0
        # BUX'a DOKUNULMAMALI
        bux = db.latest_positions("bux")
        assert len(bux) == 1 and bux[0]["symbol"] == "ASML", bux
        assert "TEK kaydiydi" in cikti                  # uyari verilmeli

        # Ikinci /sil artik BUX'u alir
        assert "BUX" in bot._sil_son()
        assert len(db.latest_positions("bux")) == 0
        assert bot._sil_son() == "Silinecek pozisyon kaydi yok."
        db.close()


def _toolbox(tmp):
    import pathlib as _p
    from finagent.storage.db import Database
    from finagent.bot.tools import ToolBox
    from finagent.config import load_settings
    db = Database(_p.Path(tmp) / "t.db"); db.init_schema()
    return ToolBox(load_settings(), db, _p.Path(tmp) / "pending"), db


def _cagir(arac, **kw):
    """SDK araci async; testte senkron calistir ve JSON'u coz."""
    import anyio, json as _j
    fn = getattr(arac, "handler", None) or arac
    r = anyio.run(lambda: fn(kw))
    return _j.loads(r["content"][0]["text"])


def test_arac_katmani_hatayi_veri_olarak_dondurur():
    """
    Arac bos donmemeli; NEDEN bos oldugunu soylemeli. Sessiz bosluk
    modeli uydurmaya itiyor — sahada "elimde coin verisi yok" dedi,
    17.180 bar dururken.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        araclar = {a.name: a for a in tb.araclar()}
        r = _cagir(araclar["teknik"], sembol="YOKBOYLE")
        assert "hata" in r and r.get("ipucu"), r
        db.close()


def test_veri_durumu_asla_bos_donmez():
    """Model 'yok' demeden once buna bakiyor; her zaman yapisal cevap vermeli."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        araclar = {a.name: a for a in tb.araclar()}
        r = _cagir(araclar["veri_durumu"])
        for alan in ("hesaplar", "gunluk_fiyat", "haber", "enstruman"):
            assert alan in r, (alan, r)
        db.close()


def test_pozisyon_kaydet_dogrudan_yazmaz_onaya_sunar():
    """
    Mimari §5: hicbir pozisyon onaysiz yazilmaz. Model artik islem
    yapabiliyor ama YAZMA yetkisi onay kapisindan geciyor.
    """
    import tempfile, json as _j
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        araclar = {a.name: a for a in tb.araclar()}
        r = _cagir(araclar["pozisyon_kaydet"], hesap="binance",
                   pozisyonlar=_j.dumps([{"sembol": "ROSE", "ad": "Oasis Network",
                                          "adet": 56741.3579, "deger": 309.24}]))
        assert r["durum"] == "ONAY BEKLIYOR"
        # DB'ye HICBIR SEY yazilmamis olmali
        assert db.query("SELECT COUNT(*) c FROM positions")[0]["c"] == 0
        # ama onay dosyasi diskte olmali
        dosya = tb.pending_dir / f"{r['token']}.json"
        assert dosya.exists()
        kayit = _j.loads(dosya.read_text())
        assert kayit["hesap"] == "binance"
        assert kayit["para_birimi"] == "USDT"        # TRY DEGIL
        assert kayit["pozisyonlar"][0]["quantity"] == 56741.3579
        db.close()


def test_pozisyon_kaydet_gecersiz_girdiyi_reddeder():
    import tempfile, json as _j
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        araclar = {a.name: a for a in tb.araclar()}
        assert "hata" in _cagir(araclar["pozisyon_kaydet"], hesap="yanlis",
                                pozisyonlar="[]")
        assert "hata" in _cagir(araclar["pozisyon_kaydet"], hesap="bux",
                                pozisyonlar="bu JSON degil")
        assert "hata" in _cagir(araclar["pozisyon_kaydet"], hesap="bux",
                                pozisyonlar=_j.dumps([{"ad": "sembolsuz"}]))
        assert db.query("SELECT COUNT(*) c FROM positions")[0]["c"] == 0
        db.close()


def test_veri_topla_gecersiz_kaynagi_reddeder():
    """
    Bilinmeyen kaynak sessizce yutulmamali. Tarayicili kaynaklar ise
    ARTIK REDDEDILMIYOR — alt surecte calistiriliyor (bkz.
    ToolBox._alt_surecte). Eskiden "terminalden calistir" deniyordu,
    ki bot zaten kullanicinin makinesinde ayni venv icinde calisiyor.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        araclar = {a.name: a for a in tb.araclar()}
        assert "hata" in _cagir(araclar["veri_topla"], kaynaklar="boyle_bir_sey_yok")
        assert "hata" in _cagir(araclar["veri_topla"], kaynaklar="")
        db.close()


def test_tarayicili_kaynaklar_alt_surece_ayrilir():
    """
    Playwright bot surecine SOKULMAZ: bir cokme tum botu dusururdu.
    Ayrim REGISTRY'deki needs_browser'a gore yapiliyor.
    """
    from finagent.collectors import REGISTRY
    tarayicili = {n for n, c in REGISTRY.items() if c.needs_browser}
    surecte = {n for n, c in REGISTRY.items() if not c.needs_browser}
    assert {"prices", "stocknews", "kap"} <= tarayicili
    assert {"alphavantage", "coingecko", "binance", "xbrl"} <= surecte
    assert not (tarayicili & surecte)


def test_piyasa_modeli_gurultuyu_ayiklar():
    """
    Piyasa modeli, hisse hareketinin ENDEKSTEN gelen kismini ayiklar.
    Sentetik test: hisse = 1.5 x endeks + tek gunluk sok.
    Ortalama-duzeltilmis model soku endeks gurultusune gomer;
    piyasa modeli soku YAKALAR.
    """
    import math
    from finagent.analysis.events import olay_etkisi

    n, sok_idx, sok = 200, 160, 0.06
    piyasa_g = [0.02 * math.sin(i * 1.7) for i in range(n)]
    endeks, hisse = [], []
    pe, ph = 100.0, 50.0
    for i, g in enumerate(piyasa_g):
        pe *= (1 + g)
        ph *= (1 + 1.5 * g + (sok if i == sok_idx else 0.0))
        endeks.append({"ts": f"2025-01-{i:03d}", "close": pe})
        hisse.append({"ts": f"2025-01-{i:03d}", "close": ph})

    yalin = olay_etkisi(hisse, f"2025-01-{sok_idx:03d}")
    model = olay_etkisi(hisse, f"2025-01-{sok_idx:03d}", piyasa=endeks)
    assert yalin and model
    assert model["model"].startswith("piyasa modeli")
    assert yalin["model"].startswith("ortalama")

    # Beta ~1.5 bulunmali ve endeks varyansi neredeyse tamamini aciklamali
    assert 1.4 < model["model_detay"]["beta"] < 1.6, model["model_detay"]
    assert model["model_detay"]["r_kare"] > 0.9

    # ARTIK oynaklik piyasa modelinde COK daha dusuk olmali
    assert model["artik_oynaklik_%"] < yalin["artik_oynaklik_%"] / 3
    # ve sok istatistiksel olarak GORUNUR hale gelmeli
    assert abs(model["t_istatistigi"]) > abs(yalin["t_istatistigi"]) * 2


def test_piyasa_vekili_para_birimine_gore_secilir():
    """
    Vekil para birimine gore secilir: farkli para birimindeki bir endekse
    regresyon beta'ya KUR hareketini de sokar.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        qqq = db.upsert_instrument("QQQ", "INDEX", "Nasdaq 100", "index", "USD")
        aex = db.upsert_instrument("AEX", "INDEX", "AEX", "index", "EUR")
        btc = db.upsert_instrument("BTC", "BINANCE", "Bitcoin", "crypto", "USDT")
        for iid, ccy in ((qqq, "USD"), (aex, "EUR"), (btc, "USDT")):
            db.upsert_prices(iid, [{"ts": "2026-08-14", "close": 100}],
                             "test", currency=ccy)
        nvda = db.upsert_instrument("NVDA", "BUX", "NVIDIA")
        db.upsert_prices(nvda, [{"ts": "2026-08-14", "close": 225}],
                         "yahoo", currency="USD")
        asml = db.upsert_instrument("ASML", "BUX", "ASML Holding")
        db.upsert_prices(asml, [{"ts": "2026-08-14", "close": 1579}],
                         "alphavantage", currency="EUR")
        rose = db.upsert_instrument("ROSE", "BINANCE", "Oasis Network", "crypto")
        db.upsert_prices(rose, [{"ts": "2026-08-14", "close": 0.0055}],
                         "binance", currency="USDT")

        assert db.piyasa_vekili(nvda)["sembol"] == "QQQ"
        assert db.piyasa_vekili(asml)["sembol"] == "AEX"
        assert db.piyasa_vekili(rose)["sembol"] == "BTC"
        # Endeksin ve BTC'nin kendi vekili olmaz
        assert db.piyasa_vekili(qqq) is None
        assert db.piyasa_vekili(btc) is None
        db.close()


def test_binance_ekrani_TRY_diye_etiketlenmez():
    """
    Sahada 314.82 USDT kullaniciya "314.82 TRY" diye gosterildi:
    para birimi "bux degilse TRY" diye tahmin ediliyordu.
    """
    from finagent.vision.screenshot import _normalise
    for hesap, beklenen in (("binance", "USDT"), ("bux", "EUR"), ("midas", "TRY")):
        out = _normalise({"ekran_tipi": "portfoy", "hesap": hesap,
                          "pozisyonlar": [{"symbol": "X", "quantity": 1,
                                           "market_value": 10}]}, hesap)
        assert out["para_birimi"] == beklenen, (hesap, out["para_birimi"])


def test_vision_buffer_varsayilandan_buyuk():
    """
    SDK varsayilani 1 MB ve goruntu okurken asiliyordu:
    "JSON message exceeded maximum buffer size of 1048576 bytes".
    """
    from finagent.vision.screenshot import _BUFFER_BAYT
    assert _BUFFER_BAYT > 1024 * 1024 * 8


def test_fiyat_serisi_para_birimi_karistirmaz():
    """
    Ayni enstrumanda birden fazla kaynak olabiliyor ve FARKLI para
    biriminde: ASML'de Yahoo USD 1844 ile Alpha Vantage EUR 1579.60
    yan yana duruyordu. Kaynak filtresiz sorgu ikisini karistirir ve
    SMA/RSI iki para biriminden hesaplanir.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("ASML", "BUX", "ASML Holding")
        db.upsert_prices(iid, [{"ts": f"2026-08-{i:02d}", "close": 1800 + i}
                               for i in range(1, 15)], "yahoo", currency="USD")
        db.upsert_prices(iid, [{"ts": f"2026-08-{i:02d}", "close": 1560 + i}
                               for i in range(1, 15)], "alphavantage", currency="EUR")
        db.insert_positions("bux", "2026-08-15T00:00:00+00:00", [
            {"symbol": "ASML", "quantity": 1.5, "market_value": 2400,
             "currency": "EUR"}])

        k = db.fiyat_kaynagi(iid)
        # Pozisyon EUR -> EUR serisi kazanmali
        assert k["currency"] == "EUR", k
        assert k["source"] == "alphavantage", k
        seri = db.fiyat_serisi(iid, 50)
        assert {r["currency"] for r in seri} == {"EUR"}, "para birimi karisti"
        assert len(seri) == 14
        assert seri[0]["ts"] < seri[-1]["ts"]     # artan sirali
        db.close()


def test_fx_kuru_ters_cifti_cevirir():
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        with db.tx() as c:
            c.execute("INSERT INTO fx_rates (ts,base,quote,rate,source) "
                      "VALUES ('2026-08-14','EUR','USD',1.1569,'test')")
        assert abs(db.fx_kuru("EUR", "USD")["rate"] - 1.1569) < 1e-9
        ters = db.fx_kuru("USD", "EUR")
        assert abs(ters["rate"] - 1 / 1.1569) < 1e-9
        assert "ters" in ters["kaynak"]
        assert db.fx_kuru("EUR", "EUR")["rate"] == 1.0
        assert db.fx_kuru("EUR", "JPY") is None       # uydurmaz
        db.close()


def test_fiyat_sade_sembolu_dogrulamadan_kabul_etmez():
    """
    RBOT vakasi: kimlik dogru sekilde 'fon' isaretliydi ama kod alta
    dusup ham sembolu Yahoo'ya verdi. Yahoo'da RBOT = Vicarious Surgical
    (6 sent), ekrandaki iShares ETF ise 19.01 EUR. %99.7 sapmayla 500 bar
    yanlis sirketten cekildi.
    """
    from finagent.collectors.prices import PriceCollector
    f = PriceCollector._yahoo_sembolu

    def hedef(sym, tur=None):
        return {"symbol": sym, "asset_type": tur}

    # Sade sembol + dogrulanmamis kimlik -> REDDEDILIR
    assert f(hedef("RBOT"), {"status": "fon", "sec_ticker": None}) is None
    assert f(hedef("CNDX"), None) is None
    # Borsa sonekli sembol tek kotasyonu gosterir -> kabul
    assert f(hedef("ABN.AS"), {"status": "fon", "sec_ticker": None}) == "ABN.AS"
    # Dogrulanmis SEC ticker'i -> kabul
    assert f(hedef("NVDA"), {"status": "dogrulandi", "sec_ticker": "NVDA"}) == "NVDA"
    # Eslesmeyen kimlik -> her halukarda ret
    assert f(hedef("AVTX.AS"), {"status": "eslesmedi", "sec_ticker": None}) is None
    assert f(hedef("CASH", "cash"), None) is None


def test_izin_kapisi_bilinmeyen_araci_reddeder():
    """
    `allowed_tools` GUVENLIK SINIRI DEGIL. Olculdu: bypassPermissions
    altinda listede olmayan araclar da calisti (pozisyon_kaydet dahil).
    Gercek sinir `can_use_tool`; bu test kapinin mantigini dogruluyor.

    Onemi: ileride emir gonderebilen bir ucuncu taraf MCP sunucusu
    baglanirsa, araclari izinli listede olmadikca CALISTIRILAMAZ.
    """
    import anyio
    from finagent.bot.tools import ARAC_ADLARI

    izinli = set(ARAC_ADLARI)

    async def kapi(tool_name):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        if tool_name in izinli:
            return PermissionResultAllow()
        return PermissionResultDeny(message="tanimli degil")

    def karar(ad):
        r = anyio.run(kapi, ad)
        return type(r).__name__

    assert karar("mcp__finagent__portfoy") == "PermissionResultAllow"
    assert karar("mcp__finagent__teknik") == "PermissionResultAllow"
    # Emir gonderebilecek bir ucuncu taraf araci -> RED
    assert karar("mcp__binance__place_order") == "PermissionResultDeny"
    assert karar("mcp__binance__cancel_all_orders") == "PermissionResultDeny"
    assert karar("Bash") == "PermissionResultDeny"


def test_sohbet_bypass_izin_kipini_kullanmaz():
    """
    chat.py bir daha `permission_mode="bypassPermissions"` ile
    calistirilmamali — o kip allowed_tools filtresini etkisiz kiliyor.
    """
    import pathlib as _p
    kaynak = (_p.Path(__file__).parent.parent / "src" / "finagent" / "bot"
              / "chat.py").read_text(encoding="utf-8")
    # Yorumda gecmesi serbest (neden kullanilmadigi anlatiliyor); ATANMASI
    # yasak. Bu yuzden yorum satirlari ayiklanarak bakiliyor.
    kod = "\n".join(s for s in kaynak.splitlines()
                    if not s.lstrip().startswith("#"))
    assert "bypassPermissions" not in kod, \
        "sohbet katmani bypassPermissions'a geri donmus"
    assert "can_use_tool" in kod


def test_midas_turkce_sayi_ayristirma():
    from finagent.collectors.midas import _sayi
    assert _sayi("1.234,56") == 1234.56
    assert _sayi("19,33") == 19.33
    assert _sayi("0,42%") == 0.42
    assert _sayi("-0,36%") == -0.36
    assert _sayi("2.002.676.592") == 2002676592.0
    assert _sayi("-") is None and _sayi("") is None and _sayi(None) is None


def test_midas_tablo_yalnizca_hisse_satiri_alir():
    from finagent.collectors.midas import _tablo_oku
    html = """<table>
      <tr><th>Hisse</th><th>Son</th><th>Alis</th><th>Satis</th><th>Fark</th>
          <th>Dusuk</th><th>Yuksek</th><th>AOF</th><th>HacimTL</th><th>Lot</th></tr>
      <tr><td>AEFES</td><td>19,33</td><td>19,31</td><td>19,33</td><td>0,42%</td>
          <td>19,10</td><td>19,44</td><td>19,28</td><td>2.002.676.592</td>
          <td>103.891.014</td></tr>
      <tr><td>Toplam</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td>
          <td>-</td><td>-</td><td>-</td><td>-</td></tr>
      <tr><td>kisa</td><td>1</td></tr>
    </table>"""
    r = _tablo_oku(html)
    assert len(r) == 1, r                      # "Toplam" ve kisa satir elendi
    assert r[0]["symbol"] == "AEFES"
    assert r[0]["close"] == 19.33
    assert r[0]["volume"] == 103891014.0


def test_midas_hafta_sonu_ve_seans_ici_kapanis_yazmaz():
    """
    Hafta sonu sayfa yine veri gosteriyor ama o CUMA'nin kapanisidir;
    bugunun tarihiyle yazmak islem gormeyen gune HAYALET BAR koyar.
    Olculdu: 2026-08-15 Cumartesi, 625 bar bu sekilde yazilmisti.
    Seans sirasinda ise "Son" anlik fiyattir, kapanis degildir.
    """
    from datetime import datetime, timezone
    from unittest.mock import patch
    from finagent.collectors import midas as M

    def sahte(y, ay, g, saat):
        class _D(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(y, ay, g, saat, 0, tzinfo=timezone.utc)
        return _D

    # Cumartesi -> yazilmaz
    with patch.object(M, "datetime", sahte(2026, 8, 15, 18)):
        ok, sebep = M.MidasCollector._bugun_yazilir_mi()
        assert not ok and "hafta sonu" in sebep
    # Cuma ama seans acik (UTC 12:00 = TR 15:00) -> yazilmaz
    with patch.object(M, "datetime", sahte(2026, 8, 14, 12)):
        ok, sebep = M.MidasCollector._bugun_yazilir_mi()
        assert not ok and "ACIK" in sebep
    # Cuma, kapanistan sonra -> yazilir
    with patch.object(M, "datetime", sahte(2026, 8, 14, 16)):
        ok, sebep = M.MidasCollector._bugun_yazilir_mi()
        assert ok, sebep


def test_midas_yazilari_kanit_sayilmaz():
    """
    Midas'in Kulaklari bir ARACI KURUMUN kendi yorumu: ozgun ama birincil
    kaynak degil ve promosyonel. Kademe 3 = kesif, kanit degil.
    Olay-etki analizi yalnizca kademe 1-2 kullanir, yani bu yazilar
    anormal getiri hesabina GIRMEZ.
    """
    import inspect
    from finagent.collectors.midas import MidasCollector
    kaynak = inspect.getsource(MidasCollector._haberler)
    assert '"tier": 3' in kaynak, "Midas yazilari kademe 3 olmali"
    from finagent.analysis import events
    assert "tier IN (1,2)" in inspect.getsource(events.haber_etkileri)


def test_isyatirim_endeks_uyeliginden_sembol_alir():
    """
    Tarihsel seri listesi artik elle guncellenmiyor: endeks uyeligi
    Midas'in public sayfalarindan geliyor ve buradan okunuyor.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.collectors.isyatirim import IsYatirimCollector
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        s = load_settings()
        # Endeks ADI ayardan okunur; sabit "BIST 30" yazmak, ayar BIST 100'e
        # cevrilince testi eskitiyordu (nitekim eskitti).
        endeks = (s.get("sources.isyatirim.indices") or ["BIST 100"])[0]
        for sym in ("THYAO", "SASA", "PGSUS"):
            iid = db.upsert_instrument(sym, "BIST", None, "equity", "TRY")
            db.query("INSERT INTO index_members (instrument_id, index_name) "
                     "VALUES (?,?)", (iid, endeks))
        db._conn.commit()
        c = IsYatirimCollector(s, db)
        semboller = c._semboller()
        assert {"THYAO", "SASA", "PGSUS"} <= set(semboller)
        assert len(semboller) == len(set(semboller)), "tekrar eden sembol var"
        db.close()


def test_isyatirim_tarih_iki_bicimi_de_cozer():
    from finagent.collectors.isyatirim import _tarih
    assert _tarih("03-08-2026") == "2026-08-03"      # gun-ay-yil
    assert _tarih("2026-08-03T00:00:00") == "2026-08-03"
    assert _tarih(None) is None and _tarih("") is None


def test_isyatirim_yan_urunleri_ayni_cevaptan_cikarir():
    """
    Endeks, kur ve hisse sayisi ZATEN her hisse cagrisinda geliyordu;
    31 alanin 24'u kullanilmiyordu. Ek istek YOK.

    END_DEGER'in endeks oldugunun kaniti: ayni gunde TUM hisselerde
    ozdes deger. Bu test onu da koruyor.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.collectors.isyatirim import IsYatirimCollector
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        c = IsYatirimCollector(load_settings(), db)
        iid = db.upsert_instrument("THYAO", "BIST", None, "equity", "TRY")
        ham = [
            {"HGDG_TARIH": "03-08-2026", "END_DEGER": 13410.54,
             "DD_DEGER": 47.5352, "SERMAYE": 1380000000.0,
             "PD": 437460000000.0, "HAO_PD": 219648666000.0},
            {"HGDG_TARIH": "04-08-2026", "END_DEGER": 13687.93,
             "DD_DEGER": 47.5550, "SERMAYE": 1380000000.0,
             "PD": 440000000000.0, "HAO_PD": 220000000000.0},
        ]
        assert c._yan_urunler(iid, ham) > 0

        xu = db.query("""SELECT p.ts, p.close, p.currency FROM prices p
                         JOIN instruments i ON i.id=p.instrument_id
                         WHERE i.symbol='XU100' ORDER BY p.ts""")
        assert [r["close"] for r in xu] == [13410.54, 13687.93]
        assert xu[0]["currency"] == "TRY"

        kur = db.fx_kuru("USD", "TRY")
        assert abs(kur["rate"] - 47.5550) < 1e-6      # en guncel gun

        kav = {r["concept"]: r["val"] for r in db.query(
            "SELECT concept, val FROM fundamentals WHERE instrument_id=?", (iid,))}
        assert kav["HisseSayisi"] == 1380000000.0
        assert kav["PiyasaDegeri"] == 440000000000.0  # en guncel gun
        assert c._yan_urunler(iid, []) == 0
        db.close()


def test_bist_piyasa_vekili_xu100():
    """TRY serisi XU100'e baglanmali; XU100'un kendi vekili olmamali."""
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        xu = db.upsert_instrument("XU100", "INDEX", "BIST 100", "index", "TRY")
        db.upsert_prices(xu, [{"ts": "2026-08-14", "close": 13410}],
                         "isyatirim", currency="TRY")
        thy = db.upsert_instrument("THYAO", "BIST", None, "equity", "TRY")
        db.upsert_prices(thy, [{"ts": "2026-08-14", "close": 305.25}],
                         "isyatirim", currency="TRY")
        assert db.piyasa_vekili(thy)["sembol"] == "XU100"
        assert db.piyasa_vekili(xu) is None
        db.close()


def _mini_db(d):
    """Tarayici testleri icin sentetik ama gercekci bir veritabani."""
    import math, pathlib as _p
    from finagent.storage.db import Database
    db = Database(_p.Path(d) / "t.db"); db.init_schema()
    iid = db.upsert_instrument("TEST", "BUX", "Test AS", "equity", "EUR")
    fiyat, barlar = 100.0, []
    for i in range(220):
        fiyat *= (1 + 0.01 * math.sin(i * 1.7))
        barlar.append({"ts": f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}",
                       "close": round(fiyat, 4), "high": fiyat, "low": fiyat,
                       "volume": 1000.0})
    return db, iid, barlar


def test_tarayici_tek_gosterge_motoru_kullanir():
    """
    Tarayici kendi RSI'ini hesapliyordu (duz ortalama) ve motorun
    Wilder yumusatmali degerinden SISTEMATIK YUKSEK cikiyordu:
    MSFT 84.8 vs 70.9, NVDA 75.4 vs 63.0. Ajan paneli bunu bagimsiz
    olarak yakaladi. Ayni gostergenin iki tanimi olmamali.
    """
    import tempfile
    import pandas as pd
    from finagent.analysis import compute_indicators, technical_snapshot
    from finagent.pulse.screener import Tarayici
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db, iid, barlar = _mini_db(d)
        db.upsert_prices(iid, barlar, "test", currency="EUR")
        s = load_settings()
        seri = db.fiyat_serisi(iid, 300)
        tarayici_rsi = Tarayici(s, db)._gosterge(seri, "rsi14")
        df = pd.DataFrame([dict(r) for r in seri]).sort_values("ts")
        motor_rsi = technical_snapshot("", compute_indicators(
            df, s.get("analysis.indicators", {})))["rsi14"]
        assert tarayici_rsi == motor_rsi, (tarayici_rsi, motor_rsi)
        assert not hasattr(Tarayici, "_rsi"), "ikinci RSI tanimi geri gelmis"
        db.close()


def test_tarayici_esikleri_oynakliga_gore_olcekler():
    """
    Sabit yuzde esigi kriptoyu surekli sinyal ureten bir gurultu
    kaynagina cevirirdi: %5 gunluk hareket AEX'te olaganustu, ROSE'da
    siradan (gunluk oynaklik %5.69).
    """
    import tempfile, math
    from finagent.pulse.screener import Tarayici, SIGMA_HAREKET
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db, iid, barlar = _mini_db(d)
        # Son gune 3 sigma'lik bir sicrama ekle
        db.upsert_prices(iid, barlar, "test", currency="EUR")
        seri = db.fiyat_serisi(iid, 300)
        kap = [r["close"] for r in seri]
        g = [kap[i] / kap[i - 1] - 1 for i in range(1, len(kap))]
        o = sum(g) / len(g)
        sd = math.sqrt(sum((x - o) ** 2 for x in g) / (len(g) - 1))
        son_ts = seri[-1]["ts"]
        yeni = kap[-1] * (1 + 3 * sd)
        db.upsert_prices(iid, [{"ts": "2025-12-31", "close": yeni,
                                "high": yeni, "low": yeni, "volume": 1000.0}],
                         "test", currency="EUR")
        db.query("INSERT INTO watchlist (instrument_id, kind) VALUES (?,?)",
                 (iid, "aday"))
        db._conn.commit()
        bulgular = Tarayici(load_settings(), db).tara()
        turler = {b["tur"] for b in bulgular}
        assert "olagandisi_hareket" in turler, turler
        h = next(b for b in bulgular if b["tur"] == "olagandisi_hareket")
        assert abs(h["kanit"]["sigma"]) >= SIGMA_HAREKET
        assert h["yon"] == "yukari"
        db.close()


def test_olay_pencereleri_ortusmez():
    """
    Olay penceresi t-1..t+3. Ard arda iki gunde haber varsa ikisi AYNI
    hareketi kapsar ve CAR iki kez raporlanir — bagimsiz iki kanit gibi
    gorunur. Olculdu: ADYEN'in 13 Ags +%16.4'u hem 13 hem 14 Ags
    olayinda sayilmisti.
    """
    import tempfile, math, pathlib as _p
    from finagent.storage.db import Database, sha1
    from finagent.analysis.events import haber_etkileri
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("ADYX", "BUX", "Adyen Test")
        fiyat, barlar = 100.0, []
        for i in range(200):
            fiyat *= (1 + 0.005 * math.sin(i * 2.1))
            if i == 190:
                fiyat *= 1.16                       # tek buyuk sicrama
            barlar.append({"ts": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                           "close": round(fiyat, 4)})
        db.upsert_prices(iid, barlar, "test", currency="EUR")
        # Ard arda IKI gunde haber
        for gun in (barlar[190]["ts"], barlar[191]["ts"]):
            db.upsert_news([{"url": f"https://x/{gun}", "title": f"haber {gun}",
                             "source": "t", "published_at": f"{gun} 09:00:00",
                             "symbols": ["ADYX"], "publisher": "Reuters",
                             "tier": 2}])
        etkiler = haber_etkileri(db, iid, "ADYX", limit=5, gun=99999)
        assert len(etkiler) == 1, [e["olay_tarihi"] for e in etkiler]
        db.close()


def test_defter_piyasaya_gore_puanlar():
    """
    Ham getiriyle puanlama boga piyasasinda her "yukari" tahminini
    isabet gosterir. Puanlama ANORMAL getiriye bakmali.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        qqq = db.upsert_instrument("QQQ", "INDEX", "Nasdaq 100", "index", "USD")
        hisse = db.upsert_instrument("XYZ", "BUX", "Test", "equity", "USD")
        # Piyasa +%10, hisse +%3 -> "yukari" tahmini YANLIS olmali
        gunler = [f"2026-06-{i:02d}" for i in range(1, 12)]
        db.upsert_prices(qqq, [{"ts": g, "close": 100 * (1 + 0.010 * i)}
                               for i, g in enumerate(gunler)], "t", currency="USD")
        db.upsert_prices(hisse, [{"ts": g, "close": 50 * (1 + 0.003 * i)}
                                 for i, g in enumerate(gunler)], "t", currency="USD")
        with db.tx() as c:
            c.execute("""INSERT INTO predictions (olusma_ts, instrument_id, yon,
                         ufuk_gun, guven, gerekce, baslangic_fiyat, para_birimi)
                         VALUES (?,?,?,?,?,?,?,?)""",
                      ("2026-06-01", hisse, "yukari", 5, 0.7, "[teknik] test",
                       50.0, "USD"))
        Defter(db).puanla()
        r = db.query("SELECT getiri_pct, piyasa_getiri_pct, anormal_pct, isabet "
                     "FROM predictions")[0]
        assert r["getiri_pct"] > 0, "ham getiri pozitif olmali"
        assert r["anormal_pct"] < 0, "piyasadan geri kalmis"
        assert r["isabet"] == 0, "ham getiriye gore puanlanmis"
        db.close()


def test_karne_kucuk_orneklemi_isaretler():
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("A", "BUX")
        with db.tx() as c:
            for i in range(5):
                # ajan='hakem': karne artik KULLANICININ OKUDUGU cagriyi
                # olcuyor. Tum tahminleri saymak, ayni fiyat hareketine
                # ait 5 ajan gorusunu 5 bagimsiz gozlem sayardi ve guven
                # araligini sahte biçimde daraltirdi.
                c.execute("""INSERT INTO predictions (olusma_ts, instrument_id,
                             ajan, yon, ufuk_gun, guven, baslangic_fiyat,
                             isabet, anormal_pct)
                             VALUES (?,?,'hakem',?,?,?,?,?,?)""",
                          (f"2026-08-{i+1:02d}", iid, "yukari", 5, 0.6, 10.0,
                           1 if i < 4 else 0, 1.0))
        k = Defter(db).karne()
        assert k["olcum"] == 5 and k["isabet_%"] == 80.0
        assert k["yeterli_mi"] is False
        assert "YETERSIZ" in k["not"].upper()
        # Guven araligi kucuk orneklemde GENIS olmali
        alt, ust = k["guven_araligi_%"]
        assert ust - alt > 40, (alt, ust)
        db.close()


def test_panel_yazma_araci_gormez():
    """Panel salt-okunur: emir/yazma araci hicbir ajanda YOK."""
    import inspect
    from finagent.pulse import agents
    kaynak = inspect.getsource(agents.Panel._ajan)
    assert 'pozisyon_kaydet' in kaynak and 'not a.endswith' in kaynak
    assert "can_use_tool=kapi" in kaynak


def test_bot_tek_ornek_kilidi():
    """
    Iki bot ornegi ayni Telegram kuyrugunu ceker ve her mesaj rastgele
    birine duser — sahada yasandi. Kilit fcntl.flock ile: surec olunce
    cekirdek birakir, yani launchd cokmede yeniden baslattiginda bayat
    kilit kalmaz.
    """
    import fcntl, tempfile, pathlib as _p
    from finagent.bot.listener import FinBot
    with tempfile.TemporaryDirectory() as d:
        bot = FinBot.__new__(FinBot)
        bot.state_dir = _p.Path(d)
        f = bot._tekil_kilit()
        assert (bot.state_dir / "bot.lock").read_text().strip().isdigit()

        # Ikinci ornek REDDEDILMELI
        ikinci = FinBot.__new__(FinBot)
        ikinci.state_dir = bot.state_dir
        try:
            ikinci._tekil_kilit()
            raise AssertionError("ikinci ornek kilidi alabildi")
        except SystemExit as e:
            assert "ZATEN CALISIYOR" in str(e)
            # Birincinin PID kaydi BOZULMAMALI ("w" ile acilsaydi silinirdi)
            assert (bot.state_dir / "bot.lock").read_text().strip().isdigit()

        # Kilit birakilinca yeniden alinabilmeli
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()
        ucuncu = FinBot.__new__(FinBot)
        ucuncu.state_dir = bot.state_dir
        ucuncu._tekil_kilit().close()


def test_launchd_plistleri_tutarli():
    """
    plist'ler gercek depo yoluna ve gercek venv'e isaret etmeli; yol
    kaymissa servis sessizce yanlis yeri calistirir.
    """
    import plistlib, pathlib as _p
    kok = _p.Path(__file__).parent.parent
    bot = plistlib.loads((kok / "launchd" /
                          "com.alipala.finagent.bot.plist").read_bytes())
    nabiz = plistlib.loads((kok / "launchd" /
                            "com.alipala.finagent.pulse.plist").read_bytes())

    assert bot["ProgramArguments"][0].endswith(".venv/bin/python")
    assert _p.Path(bot["WorkingDirectory"]).name == kok.name
    # Bot: acilista baslasin, cokmede geri gelsin, ama TEMIZ cikista donmesin
    assert bot["RunAtLoad"] is True
    assert bot["KeepAlive"] == {"SuccessfulExit": False}
    assert bot["ThrottleInterval"] >= 30

    # Nabiz: zamanlanmis is — yuklenince calismasin, bitince donmesin
    assert nabiz.get("RunAtLoad") is False
    assert "KeepAlive" not in nabiz
    gunler = sorted(x["Weekday"] for x in nabiz["StartCalendarInterval"])
    assert gunler == [1, 2, 3, 4, 5], gunler          # hafta sonu YOK
    assert all(x["Hour"] == 22 and x["Minute"] == 15
               for x in nabiz["StartCalendarInterval"])
    # ExitTimeOut BIR CALISMA SURESI SINIRI DEGIL. Bu test onceden
    # `>= 900` istiyordu ve YANLIS bir inanci koruyordu: bu anahtar,
    # launchd isi DURDURURKEN SIGTERM ile SIGKILL arasinda tanidigi
    # suredir; uzun suren zamanlanmis bir isi oldurmez. Buyuk bir deger
    # yalnizca sistem kapanmasini geciktirir. Gercek korumalar
    # run_pulse.sh icinde ve asagida ayrica test ediliyor.
    assert nabiz["ExitTimeOut"] <= 120


def test_nabiz_kendi_sure_sinirini_ve_kilidini_tasir():
    """
    launchd sure siniri UYGULAMADIGI icin ikisi de script'te olmali:

      * TEK ORNEK — onceki kosu surerken ikincisi baslamamali. PID
        dosyasi degil flock: surec cokerse cekirdek kilidi birakir,
        PID dosyasi oksuz kalip sonraki tum kosulari bloke ederdi.
      * DUVAR SAATI SINIRI — macOS'ta `timeout` komutu YOK (olculdu:
        command not found), o yuzden arka planda bekci surec.
    """
    import pathlib as _p
    kaynak = (_p.Path(__file__).parent.parent / "scripts" /
              "run_pulse.sh").read_text()
    assert "flock" in kaynak, "tek ornek kilidi yok"
    assert "LOCK_EX" in kaynak and "LOCK_NB" in kaynak
    assert "PULSE_TIMEOUT" in kaynak, "duvar saati siniri yok"
    assert "kill -TERM" in kaynak


def _bekci(d):
    import pathlib as _p
    from finagent.storage.db import Database
    from finagent.bot.watchdog import Bekci
    from finagent.config import load_settings
    db = Database(_p.Path(d) / "t.db"); db.init_schema()
    return Bekci(load_settings(), db, _p.Path(d)), db


def test_bekci_kesintiyi_olcer_kisa_yeniden_baslatmayi_yutar():
    """
    Coken sistem "coktum" diyemez; yapabilecegi tek durust sey GERI
    DONDUGUNDE ne kadar kapali kaldigini soylemek. Ama elle yeniden
    baslatma / deploy icin bildirim atmak gurultu olur.
    """
    import tempfile
    from datetime import timedelta
    from unittest.mock import patch
    from finagent.bot import watchdog as W
    with tempfile.TemporaryDirectory() as d:
        b, db = _bekci(d)
        assert b.kesinti() is None            # ilk calistirma: kayit yok
        b.kalp_at()
        assert b.kesinti() is None            # taze damga: kesinti yok

        simdi = W._simdi()
        # 3 dakika -> esigin ALTINDA, rapor edilmemeli
        with patch.object(W, "_simdi", lambda: simdi + timedelta(minutes=3)):
            assert b.kesinti() is None
        # 47 dakika -> rapor edilmeli
        with patch.object(W, "_simdi", lambda: simdi + timedelta(minutes=47)):
            k = b.kesinti()
            assert k and k["sure_dk"] == 47
            assert k["tur"] == "sistem kapali"
        db.close()


def test_bekci_baglanti_kopmasini_sistem_cokmesinden_ayirir():
    """
    "Bot oluydu" ile "internet yoktu" FARKLI arizalar ve kullaniciya
    farkli sey soylenmeli. Ayrim: baglanti kopukken kalp atisi ilerler,
    cevrimici damgasi ilerlemez.
    """
    import tempfile
    from datetime import timedelta
    from unittest.mock import patch
    from finagent.bot import watchdog as W
    with tempfile.TemporaryDirectory() as d:
        b, db = _bekci(d)
        t0 = W._simdi()
        with patch.object(W, "_simdi", lambda: t0):
            b.kalp_at(cevrimici=True)
        # 30 dk boyunca bot CALISTI ama Telegram'a ulasamadi
        b._son_yazim = None
        with patch.object(W, "_simdi", lambda: t0 + timedelta(minutes=30)):
            b.kalp_at(cevrimici=False)
        with patch.object(W, "_simdi", lambda: t0 + timedelta(minutes=45)):
            k = b.kesinti()
        assert k and "baglanti kopuk" in k["tur"], k
        db.close()


def test_bekci_kacirilan_nabzi_yakalar():
    """
    Sessiz basarisizlik en tehlikeli ariza: hicbir sey olmamis gibi
    gorunur. Bot ayakta oldugu surece zamanlanmis isi de gozetler.
    """
    import tempfile
    from datetime import datetime, timezone
    from unittest.mock import patch
    from finagent.bot import watchdog as W
    with tempfile.TemporaryDirectory() as d:
        b, db = _bekci(d)
        cuma_gec = datetime(2026, 8, 14, 23, 30, tzinfo=timezone.utc)
        cuma_erken = datetime(2026, 8, 14, 22, 0, tzinfo=timezone.utc)
        cumartesi = datetime(2026, 8, 15, 23, 30, tzinfo=timezone.utc)

        with patch.object(W, "_simdi", lambda: cuma_gec):
            assert b.kacirilan_nabiz() is not None      # sinyal yok -> uyar
        with patch.object(W, "_simdi", lambda: cuma_erken):
            assert b.kacirilan_nabiz() is None          # 23:00'ten once yargilama
        with patch.object(W, "_simdi", lambda: cumartesi):
            assert b.kacirilan_nabiz() is None          # hafta sonu zaten calismaz

        iid = db.upsert_instrument("X", "BUX")
        with db.tx() as c:
            c.execute("INSERT INTO signals (olusma_ts,instrument_id,tur,guc) "
                      "VALUES (?,?,?,?)", ("2026-08-14", iid, "test", 1.0))
        with patch.object(W, "_simdi", lambda: cuma_gec):
            assert b.kacirilan_nabiz() is None          # sinyal var -> sessiz
        db.close()


def test_bekci_bildirimi_susturur():
    """
    Bot yapilandirma hatasiyla surekli yeniden basliyorsa (launchd 60 sn'de
    bir dener) her kalkista mesaj atmak dakikada bir bildirim demektir.
    """
    import tempfile
    from unittest.mock import patch
    from finagent.bot import watchdog as W
    with tempfile.TemporaryDirectory() as d:
        b, db = _bekci(d)
        gonderilen = []

        class SahteTG:
            def __init__(self, *a, **k): pass
            def send_message(self, m, **k): gonderilen.append(m); return True

        with patch("finagent.notify.TelegramNotifier", SahteTG):
            assert b.bildir("kesinti", "birinci") is True
            assert b.bildir("kesinti", "ikinci") is False     # susturuldu
            assert b.bildir("baska_tur", "ucuncu") is True    # farkli anahtar
        assert gonderilen == ["birinci", "ucuncu"]
        db.close()


def test_bekci_dis_pingi_kisitlar_ve_hatada_ilerletmez():
    """
    Dongu basi ping gunde ~1.700 istek ederdi. Ayrica BASARISIZ ping
    zaman damgasini ILERLETMEMELI: gecici ag hatasi yuzunden 5 dakika
    beklemek, izleyicinin alarm esigine yaklastirir.
    """
    import tempfile, pathlib as _p
    from datetime import timedelta
    from unittest.mock import patch
    from finagent.bot import watchdog as W
    with tempfile.TemporaryDirectory() as d:
        b, db = _bekci(d)
        cagrilar = []

        class SahteHttpx:
            @staticmethod
            def get(url, timeout=None):
                cagrilar.append(url)
                return None

        with patch.dict("os.environ", {"HEARTBEAT_URL": "https://ornek/abc"}), \
             patch.dict("sys.modules", {"httpx": SahteHttpx}):
            b.disari_ping()
            assert len(cagrilar) == 1
            b.disari_ping()                       # hemen ardindan
            assert len(cagrilar) == 1, "kisitlanmadi"

            t0 = b._son_ping
            with patch.object(W, "_simdi", lambda: t0 + timedelta(minutes=3)):
                b.disari_ping()
                assert len(cagrilar) == 1, "3 dk sonra gonderdi"
            with patch.object(W, "_simdi", lambda: t0 + timedelta(minutes=6)):
                b.disari_ping()
                assert len(cagrilar) == 2, "6 dk sonra gondermedi"

        # URL yoksa HIC cagirmamali
        cagrilar.clear()
        b._son_ping = None
        with patch.dict("os.environ", {"HEARTBEAT_URL": ""}), \
             patch.dict("sys.modules", {"httpx": SahteHttpx}):
            b.disari_ping()
        assert cagrilar == []

        # Hata durumunda damga ILERLEMEMELI -> bir sonraki dongude tekrar dene
        class PatlayanHttpx:
            @staticmethod
            def get(url, timeout=None):
                raise OSError("ag yok")

        b._son_ping = None
        with patch.dict("os.environ", {"HEARTBEAT_URL": "https://ornek/abc"}), \
             patch.dict("sys.modules", {"httpx": PatlayanHttpx}):
            b.disari_ping()
        assert b._son_ping is None, "basarisiz ping damgayi ilerletti"
        db.close()


def test_likidite_suzgeci_ince_kagitlari_eler():
    """
    Katalogda 729 BIST kagidi var ama cogu gunde birkac islem goruyor.
    Hepsini taramak 200-400 sinyal uretirdi; her gun "400 sey oldu"
    demek hicbir sey dememekle ayni. Olculdu: medyan gunluk hacim 33M TL.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.screener import Tarayici
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        for sem, hacim in (("LIKIT", 200_000_000), ("INCE", 1_000_000)):
            iid = db.upsert_instrument(sem, "BIST", None, "equity", "TRY")
            db.upsert_prices(iid, [{"ts": f"2026-08-{i:02d}", "close": 10 + i}
                                   for i in range(1, 20)], "test", currency="TRY")
            db.upsert_fundamentals([(iid, "GunlukHacimTL", "TRY", None,
                                     "2026-08-16", None, float(hacim), "midas",
                                     None, None, None, "2026-08-16", None)])
        evren = {e["symbol"] for e in Tarayici(load_settings(), db).evren()}
        assert "LIKIT" in evren, "likit kagit elenmis"
        assert "INCE" not in evren, "ince kagit suzgecten gecmis"

        # Portfoyde olan kagit hacme BAKILMADAN taranir: sahibi oldugun
        # seyi izlememek, likit olmadigi icin gormezden gelmek olurdu.
        ince = db.query("SELECT id FROM instruments WHERE symbol='INCE'")[0]["id"]
        db.query("INSERT INTO watchlist (instrument_id, kind) VALUES (?,?)",
                 (ince, "aday"))
        db._conn.commit()
        evren2 = {e["symbol"] for e in Tarayici(load_settings(), db).evren()}
        assert "INCE" in evren2, "izleme listesindeki kagit elenmis"
        db.close()


def test_isyatirim_artimli_ceker():
    """
    Her gun her sembol icin 410 gunluk gecmisi yeniden cekmek olculdu:
    253 sembol = 17 dk 52 sn. Artimli cekimle 3 dk 14 sn.

    NOT: bu testin gerekcesi once "launchd ExitTimeOut'ta oldururdu"
    diye yazilmisti; bu YANLISTI (ExitTimeOut calisma suresi siniri
    degil). Gerekce yine de gecerli: nabzin duvar saati siniri
    run_pulse.sh icinde ve tek bir collector butcenin yarisini yemez.
    """
    import inspect
    from finagent.collectors.isyatirim import IsYatirimCollector
    kaynak = inspect.getsource(IsYatirimCollector.collect)
    # Mevcut barlarin son tarihi okunuyor ve baslangic ona gore secilyor
    assert "MAX(p.ts)" in kaynak
    assert "timedelta(days=5)" in kaynak, "ust uste binme payi yok"
    assert "tam_baslangic" in kaynak, "yeni sembolde tam gecmis cekilmiyor"


def test_bist_sembolu_turkce_kelimeyle_karismaz():
    """
    29 BIST sembolu gundelik Turkce kelimeyle cakisiyor: HEDEF, KENT,
    ARENA, LIDER, BIZIM, MARKA... Basligi buyuk harfe cevirip aramak
    "THYAO hedef fiyatini yukseltti" haberini HEDEF Holding'e baglamisti
    — ajan paneli yakaladi. Ticker haberde BUYUK yazilir.
    """
    import re
    bilinen = {"HEDEF", "THYAO", "PGSUS", "TAVHL", "LIDER", "KENT", "SAHOL"}

    def esle(baslik, href=""):
        slug = (href or "").lower()
        return sorted({
            x for x in bilinen
            if re.search(rf"(?<![A-Za-z0-9]){x}(?![A-Za-z0-9])", baslik)
            or re.search(rf"(?<![a-z0-9]){x.lower()}(?![a-z0-9])", slug)})

    # Kucuk harfli gundelik kelime ESLESMEZ
    assert esle("HSBC, THYAO hedef fiyatini yukseltti; PGSUS ve TAVHL") == \
        ["PGSUS", "TAVHL", "THYAO"]
    assert esle("lider konumdaki sirket buyuyor") == []
    assert esle("kent merkezinde yeni magaza") == []
    # Buyuk harfli ticker ESLESIR
    assert esle("HEDEF Holding bilanco acikladi") == ["HEDEF"]
    # URL slug'inda kucuk harf gecerli — orada sembol listesi duruyor
    assert esle("Gunun one cikanlari",
                "/midasin-kulaklari/x-sahol-pgsus-p-123") == ["PGSUS", "SAHOL"]
    # Kaynak kodda da buyuk-harfe-cevirme geri gelmemeli
    import inspect
    from finagent.collectors.midas import MidasCollector
    kaynak = inspect.getsource(MidasCollector._haberler)
    assert '.upper()' not in kaynak.split("semboller = sorted")[1][:400], \
        "baslik yine buyuk harfe cevriliyor"


def test_gunun_hareketlileri_ince_kagidi_eler():
    """
    Ince kagitta buyuk yuzde, tek bir emrin izidir. Ayrica BIST'te
    gunluk limit ±%10; +9.99 "tavan yapti" demektir, arac bunu soylemeli.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        araclar = {a.name: a for a in tb.araclar()}
        for sem, deg, hac in (("LIKIT", 9.99, 500_000_000),
                              ("INCE", 10.00, 2_000_000),
                              ("DUSEN", -9.97, 300_000_000)):
            iid = db.upsert_instrument(sem, "BIST", None, "equity", "TRY")
            db.upsert_fundamentals([
                (iid, "GunlukDegisimPct", "%", None, "2026-08-16", None,
                 deg, "midas", None, None, None, "2026-08-16", None),
                (iid, "GunlukHacimTL", "TRY", None, "2026-08-16", None,
                 float(hac), "midas", None, None, None, "2026-08-16", None)])
        r = _cagir(araclar["gunun_hareketlileri"], yon="artan", adet=10)
        semboller = [h["symbol"] for h in r["hisseler"]]
        assert "LIKIT" in semboller
        assert "INCE" not in semboller, "ince kagit elenmedi"
        assert "tavan" in r["not"].lower() and "%10" in r["not"]

        r2 = _cagir(araclar["gunun_hareketlileri"], yon="azalan", adet=10)
        assert r2["hisseler"][0]["symbol"] == "DUSEN"
        db.close()


def test_bilanco_donem_ve_birim_kaydeder():
    """
    IKI tuzak birden. (1) Gelir tablosu kalemleri KUMULATIF: 2026-06 = 6 ay,
    2025-12 = 12 ay. Ikisini karsilastirmak "kar %84 dustu" hatasi uretir.
    (2) Sayfa "Bin TRY" veriyor; 1000 ile carpilmazsa PD/DD 0,42 yerine
    416,7 cikar ve "1 milyar TL ozkaynak" makul GORUNDUGU icin hicbir sey
    alarm vermez.
    """
    from finagent.collectors.midasbilanco import _deger, _donem_gun, SATIRLAR, BIN

    # Deger ayristirma — yuzde eki YAPISIK gelebiliyor
    assert _deger("1.018.453.000") == 1_018_453_000
    # Deger ve yuzde AYRI SATIRDA gelir; bu cozulur
    assert _deger("80.346.000\n11,46%") == 80_346_000
    # YAPISIK gelirse dogru sayi BELIRSIZDIR -> tahmin etme, REDDET.
    # Regex geri izlemeyle "80.346" gibi kisa/yanlis eslesme buluyordu.
    assert _deger("80.346.00011,46%") is None
    assert _deger("-5.098.000") == -5_098_000
    assert _deger("-") is None and _deger("") is None and _deger(None) is None

    # Donem uzunlugu: kumulatif ay sayisina gore
    assert _donem_gun(3) == 90 and _donem_gun(6) == 181
    assert _donem_gun(9) == 273 and _donem_gun(12) == 365

    # BILANCO kalemi ANLIK (days=NULL), GELIR TABLOSU kalemi donemsel
    assert SATIRLAR["toplam özkaynaklar"][1] is True, "ozkaynak anlik olmali"
    assert SATIRLAR["duran varlıklar"][1] is True
    assert SATIRLAR["hasılat"][1] is False, "hasilat donemsel olmali"
    assert SATIRLAR["net dönem karı/zararı"][1] is False
    assert SATIRLAR["esas faaliyet karı/zararı"][1] is False

    assert BIN == 1000, "Bin TRY -> TRY carpani"


def test_bilanco_ayni_uzunlukta_karsilastirma_saglar():
    """
    Toplanan donemler AYNI UZUNLUKTA bir yillik karsilastirmaya izin
    vermeli: 2026-06 (181g) ile 2025-06 (181g). Varsayilan 4 sutun
    (6/3/12/9 ay) bunu SAGLAMIYOR — hepsi farkli uzunlukta.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("THYAO", "BIST", None, "equity", "TRY")
        # Collector'un yazdigi bicimde: ayni kavram, ayni gun sayisi
        db.upsert_fundamentals([
            (iid, "Hasilat", "TRY", None, "2026-06-30", 181,
             585_069_000_000.0, "midasbilanco", 2026, "M06", None, "2026-08-16", None),
            (iid, "Hasilat", "TRY", None, "2025-06-30", 181,
             408_000_000_000.0, "midasbilanco", 2025, "M06", None, "2026-08-16", None),
            (iid, "Ozkaynak", "TRY", None, "2026-06-30", None,
             1_018_453_000_000.0, "midasbilanco", 2026, "M06", None, "2026-08-16", None),
        ])
        r = {(x["concept"], x["period_end"]): (x["val"], x["days"])
             for x in db.query("SELECT concept, period_end, val, days "
                               "FROM fundamentals WHERE instrument_id=?", (iid,))}
        a = r[("Hasilat", "2026-06-30")]
        b = r[("Hasilat", "2025-06-30")]
        assert a[1] == b[1] == 181, "donem uzunluklari esit degil"
        assert abs((a[0] / b[0] - 1) * 100 - 43.4) < 0.5   # +%43 buyume
        # Bilanco kalemi ANLIK kaydedilmis olmali
        assert r[("Ozkaynak", "2026-06-30")][1] is None
        # Birim normalize: trilyon mertebesinde, milyar degil
        assert r[("Ozkaynak", "2026-06-30")][0] > 1e12
        db.close()


def test_bilanco_sektor_farkini_hata_diye_gostermez():
    """
    Sigorta/finans sirketleri FARKLI tablo yapisi kullaniyor: "Ozet
    Bilanco" tablosu yok (olculdu: ANSGR, TURSG). Bu bir HATA degil
    BILINEN BIR SINIR ve raporda oyle gorunmeli — "basarisiz" demek,
    ileride neden veri olmadigini aratirdi.
    """
    import inspect
    from finagent.collectors import midasbilanco as M
    assert issubclass(M._YapiFarkli, RuntimeError)
    kaynak = inspect.getsource(M.MidasBilancoCollector.collect)
    assert "farkli_yapi" in kaynak
    assert "SEKTOR YAPISI FARKLI" in kaynak
    # Ayri yakalaniyor, genel Exception'a karismiyor
    assert kaynak.index("except _YapiFarkli") < kaynak.index("except Exception")
    # Ozet tablosu yoksa ayirt ediliyor
    sembol_kaynak = inspect.getsource(M.MidasBilancoCollector._sembol)
    assert "ozet_var" in sembol_kaynak and "_YapiFarkli" in sembol_kaynak


def test_midas_tablosu_dokuz_kolonu_da_okur():
    """
    Listeleme tablosunda 9 kolon var; AOF ayristiriliyordu ama ATILIYORDU,
    alis/satis hic okunmuyordu. AOF kapanistan daha bilgilendirici:
    gunun HACMININ hangi fiyattan gectigini soyler.
    """
    from finagent.collectors.midas import _tablo_oku
    html = """<table>
      <tr><th>Hisse</th><th>Son</th><th>Alis</th><th>Satis</th><th>Fark</th>
          <th>Dusuk</th><th>Yuksek</th><th>AOF</th><th>HacimTL</th><th>Lot</th></tr>
      <tr><td>AEFES</td><td>19,33</td><td>19,31</td><td>19,35</td><td>0,42%</td>
          <td>19,10</td><td>19,44</td><td>19,26</td><td>2.002.676.592</td>
          <td>103.891.014</td></tr>
    </table>"""
    r = _tablo_oku(html)[0]
    assert r["close"] == 19.33
    assert r["alis"] == 19.31 and r["satis"] == 19.35
    assert r["aof"] == 19.26, "AOF okunmuyor"
    assert r["hacim_tl"] == 2_002_676_592.0
    assert r["volume"] == 103_891_014.0


def test_ortaklik_yapisi_chartjs_bellekten_okunur():
    """
    Pasta grafik Chart.js ile ciziliyor ve veri JS BELLEGINDE duruyor.
    Once "JS ile geliyor, alinamiyor" diye birakilmisti — yanlisti;
    Chart.getChart(canvas).data ile dogrudan okunuyor, piksel/OCR yok.
    """
    import inspect
    from finagent.collectors.midasbilanco import MidasBilancoCollector
    kaynak = inspect.getsource(MidasBilancoCollector._ortaklik)
    assert "Chart.getChart" in kaynak
    assert "'pie'" in kaynak
    # Sektor yapisi farkli olsa bile ortaklik ayri cagriliyor
    toplam = inspect.getsource(MidasBilancoCollector.collect)
    assert "_ortaklik" in toplam


def test_ownership_tablosu_tekil():
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("A1CAP", "BIST", None, "equity", "TRY")
        satir = [(iid, "GULER YATIRIM", 32.15, "2026-08-16", "midasbilanco")]
        for _ in range(3):
            with db.tx() as c:
                c.executemany(
                    """INSERT INTO ownership (instrument_id, ortak, pay_pct,
                       olcum_tarihi, kaynak) VALUES (?,?,?,?,?)
                       ON CONFLICT(instrument_id, ortak, olcum_tarihi, kaynak)
                       DO UPDATE SET pay_pct = excluded.pay_pct""", satir)
        assert db.query("SELECT COUNT(*) c FROM ownership")[0]["c"] == 1
        db.close()




def test_predictions_ajan_gocu_kayit_kaybetmez():
    """
    ESKI sekilli `predictions` (UNIQUE'inde `ajan` yok) yeni semaya
    tasinmali: kayit sayisi degismemeli, `ajan` gerekce onekinden geri
    kazanilmali, benzersizlik `ajan` icermeli.

    Bu goc SQLite'ta ALTER TABLE ile yapilamaz (kisit degistirilemez);
    tablo yeniden kurulup kopyalaniyor. Kayit kaybi sessiz olurdu.
    """
    import tempfile, pathlib as _p, sqlite3
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        yol = _p.Path(d) / "t.db"
        # Once ESKI semayi elle kur
        c = sqlite3.connect(yol)
        c.executescript("""
            CREATE TABLE instruments (id INTEGER PRIMARY KEY, symbol TEXT,
                venue TEXT, name TEXT, asset_type TEXT, currency TEXT, isin TEXT,
                UNIQUE(symbol, venue));
            INSERT INTO instruments (id, symbol, venue) VALUES (1,'XYZ','BUX');
            CREATE TABLE predictions (
                id INTEGER PRIMARY KEY, olusma_ts TEXT NOT NULL,
                instrument_id INTEGER NOT NULL, yon TEXT NOT NULL,
                ufuk_gun INTEGER NOT NULL, guven REAL, gerekce TEXT,
                baslangic_fiyat REAL NOT NULL, para_birimi TEXT,
                olcum_ts TEXT, bitis_fiyat REAL, getiri_pct REAL,
                piyasa_getiri_pct REAL, anormal_pct REAL, isabet INTEGER,
                UNIQUE (olusma_ts, instrument_id, ufuk_gun));
            INSERT INTO predictions (olusma_ts,instrument_id,yon,ufuk_gun,guven,
                gerekce,baslangic_fiyat) VALUES
                ('2026-08-15',1,'yukari',5,0.7,'[teknik] a',10.0),
                ('2026-08-14',1,'asagi',5,0.6,'[risk] b',11.0),
                ('2026-08-13',1,'notr',5,0.5,'onek yok',12.0);
        """)
        c.commit(); c.close()

        db = Database(yol); db.init_schema()
        kolonlar = {r["name"] for r in db.query("PRAGMA table_info(predictions)")}
        assert "ajan" in kolonlar and "signal_id" in kolonlar
        assert {"tez", "gecersizlesme_kosulu", "izlenecek_esik"} <= kolonlar

        assert db.query("SELECT COUNT(*) n FROM predictions")[0]["n"] == 3, \
            "goc kayit kaybetti"
        dagilim = {r["ajan"]: r["n"] for r in db.query(
            "SELECT ajan, COUNT(*) n FROM predictions GROUP BY ajan")}
        assert dagilim == {"teknik": 1, "risk": 1, "bilinmiyor": 1}, dagilim

        sql = db.query("SELECT sql FROM sqlite_master WHERE name='predictions'")[0]["sql"]
        assert "ajan" in sql.split("UNIQUE")[-1], "benzersizlige ajan girmemis"

        db.init_schema()      # idempotent olmali
        assert db.query("SELECT COUNT(*) n FROM predictions")[0]["n"] == 3
        db.close()


def test_defter_celiskiyi_saklar():
    """
    Iki ajan ayni sembol+ufuk icin TERS yon soylerse IKISI DE yazilmali.

    Onceden yalnizca en yuksek guvenli tutuluyordu; bu, projenin kendi
    "celiski en degerli ciktidir" ilkesini deftere hic gecirmiyordu ve
    ajan karnesini en iddiali ajanin karnesine ceviriyordu.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("XYZ", "BUX", "Test", "equity", "EUR")
        db.upsert_prices(iid, [{"ts": "2026-08-14", "close": 10.0},
                               {"ts": "2026-08-15", "close": 10.5}],
                         "t", currency="EUR")
        r = Defter(db).kaydet([
            {"sembol": "XYZ", "yon": "yukari", "guven": 0.8, "ufuk_gun": 5,
             "gerekce": "a", "ajan": "teknik"},
            {"sembol": "XYZ", "yon": "asagi", "guven": 0.6, "ufuk_gun": 5,
             "gerekce": "b", "ajan": "risk"},
        ])
        assert r["yazilan"] == 2, r
        yonler = {x["yon"] for x in db.query(
            "SELECT yon FROM predictions WHERE instrument_id=?", (iid,))}
        assert yonler == {"yukari", "asagi"}, "celiski kaybolmus"

        # Ayni ajan iki kez -> cakisma SAYILMALI, sessizce yutulmamali
        r2 = Defter(db).kaydet([
            {"sembol": "XYZ", "yon": "yukari", "guven": 0.9, "ufuk_gun": 5,
             "gerekce": "c", "ajan": "temel"},
            {"sembol": "XYZ", "yon": "notr", "guven": 0.3, "ufuk_gun": 5,
             "gerekce": "d", "ajan": "temel"},
        ])
        assert r2["atilan_cakisma"] == 1, r2

        # Olmayan sembol de SAYILMALI
        r3 = Defter(db).kaydet([{"sembol": "YOKBOYLE", "yon": "yukari",
                                 "guven": 0.5, "ufuk_gun": 5, "ajan": "olay"}])
        assert r3["atilan_sembol_yok"] == 1, r3
        db.close()


def test_hakem_ayri_puanlanir_ve_karne_kolondan_okur():
    """
    Kullanicinin OKUDUGU sey hakem ozeti. Ajanlari puanlayip hakemi
    puanlamamak, gonderilen tavsiyenin isabetini olcmemek demekti.

    Ayrica `ajan_karnesi` artik `gerekce LIKE` degil `ajan` kolonu
    kullanmali — LIKE, defterde yalnizca hayatta kalanlari sayiyordu.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("XYZ", "BUX", "Test", "equity", "EUR")
        db.upsert_prices(iid, [{"ts": "2026-08-15", "close": 10.0}],
                         "t", currency="EUR")
        Defter(db).kaydet([{"sembol": "XYZ", "yon": "yukari", "guven": 0.7,
                            "ufuk_gun": 5, "gerekce": "x", "ajan": "hakem",
                            "tez": "T", "gecersizlesme_kosulu": "close < 9"}])
        r = db.query("SELECT ajan, tez, gecersizlesme_kosulu FROM predictions")[0]
        assert r["ajan"] == "hakem" and r["tez"] == "T"
        assert r["gecersizlesme_kosulu"] == "close < 9"

        # gerekce onegini BOZ: kolon tabanli karne yine de saymali
        with db.tx() as c:
            c.execute("UPDATE predictions SET isabet=1, gerekce='onek yok'")
        k = Defter(db).ajan_karnesi()
        assert any(x["ajan"] == "hakem" and x["olcum"] == 1 for x in k), k
        assert all(x["yeterli_mi"] is False for x in k), "n<20 yeterli sayilmis"
        db.close()


def test_panel_ham_ciktiyi_saklar():
    """
    `_json_cek` bos donerse o tur olcum disi kaliyordu ve GERIYE DONUK
    sayilamiyordu — iz yoktu. Ham metin + durum saklanmali.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.agents import Panel
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        p = Panel(load_settings(), db)
        p._kosuyu_yaz({
            "teknik": ("metin + json", {"gorusler": [{"sembol": "X"}]}),
            "temel": ("json blogu bozuk", {}),
            "olay": ("(ajan calismadi: TimeoutError: x)", {}),
        })
        durum = {r["ajan"]: r["json_durum"] for r in db.query(
            "SELECT ajan, json_durum FROM panel_runs")}
        assert durum == {"teknik": "ok", "temel": "bos", "olay": "ajan_hatasi"}, durum
        ham = db.query("SELECT ham_metin FROM panel_runs WHERE ajan='temel'")[0]
        assert ham["ham_metin"] == "json blogu bozuk", "ham metin saklanmamis"
        db.close()




def test_karne_kumelenmeyi_saymaz():
    """
    `ajan` benzersizlige girdikten sonra ayni enstrumanin ayni gunune ait
    5 tahmin olusabiliyor (4 ajan + hakem). Bunlar BAGIMSIZ GOZLEM DEGIL:
    hepsi TEK bir fiyat hareketini konusuyor.

    Karne bunlari ayri sayarsa Wilson araligi oldugundan DAR cikar ve
    olmayan bir kesinlik uretir. Bu yuzden karne yalnizca HAKEM
    cagrilarini olcer — hem enstruman-gun basina tek, hem de
    kullanicinin okudugu sey.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("XYZ", "BUX", "Test", "equity", "EUR")
        with db.tx() as c:
            # Ayni enstruman + ayni gun: dort ajan + hakem
            for ajan, isabet in (("teknik", 1), ("temel", 1), ("olay", 1),
                                 ("risk", 1), ("hakem", 0)):
                c.execute(
                    """INSERT INTO predictions (olusma_ts, instrument_id, ajan,
                       yon, ufuk_gun, guven, baslangic_fiyat, isabet)
                       VALUES ('2026-07-01',?,?,'yukari',5,0.7,10.0,?)""",
                    (iid, ajan, isabet))
        k = Defter(db).karne()
        assert k["olcum"] == 1, f"kumelenme sayilmis: {k}"
        assert k["kaynak"] == "hakem"
        assert k["bagimsiz_kume"] == k["olcum"], "bagimsizlik kirilmis"
        # Ajanlarin 4/4 isabetine ragmen karne hakemi olcer: %0
        assert k["isabet_%"] == 0.0, k
        # Ajan kirilimi ayrica durmali
        aj = {x["ajan"]: x["olcum"] for x in Defter(db).ajan_karnesi()}
        assert aj == {"teknik": 1, "temel": 1, "olay": 1, "risk": 1, "hakem": 1}, aj
        db.close()


def test_karne_hakem_yoksa_sessiz_kalmaz():
    """
    Hakem tahmini puanlanmadan once "olcum yok" demek yaniltici olurdu:
    ajan tahminleri puanlanmis olabilir. Iki durum AYRI ve ikincisi
    gecici — cikti bunu SOYLEMELI.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("XYZ", "BUX", "Test", "equity", "EUR")
        with db.tx() as c:
            c.execute("""INSERT INTO predictions (olusma_ts, instrument_id, ajan,
                         yon, ufuk_gun, guven, baslangic_fiyat, isabet)
                         VALUES ('2026-07-01',?,'teknik','yukari',5,0.7,10.0,1)""",
                      (iid,))
        k = Defter(db).karne()
        assert k["olcum"] == 0
        assert "HAKEM" in k["not"] and "1" in k["not"], k["not"]
        db.close()




def _eski_semali_db(yol, kayitlar=3):
    """ESKI sekilli predictions tablosu kurar (UNIQUE'inde `ajan` yok)."""
    import sqlite3
    c = sqlite3.connect(yol)
    c.executescript("""
        CREATE TABLE instruments (id INTEGER PRIMARY KEY, symbol TEXT,
            venue TEXT, name TEXT, asset_type TEXT, currency TEXT, isin TEXT,
            UNIQUE(symbol, venue));
        INSERT INTO instruments (id, symbol, venue) VALUES (1,'XYZ','BUX');
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY, olusma_ts TEXT NOT NULL,
            instrument_id INTEGER NOT NULL, yon TEXT NOT NULL,
            ufuk_gun INTEGER NOT NULL, guven REAL, gerekce TEXT,
            baslangic_fiyat REAL NOT NULL, para_birimi TEXT,
            olcum_ts TEXT, bitis_fiyat REAL, getiri_pct REAL,
            piyasa_getiri_pct REAL, anormal_pct REAL, isabet INTEGER,
            UNIQUE (olusma_ts, instrument_id, ufuk_gun));
    """)
    for i in range(kayitlar):
        c.execute("""INSERT INTO predictions (olusma_ts,instrument_id,yon,
                     ufuk_gun,guven,gerekce,baslangic_fiyat)
                     VALUES (?,1,'yukari',5,0.7,'[teknik] a',10.0)""",
                  (f"2026-08-{i+1:02d}",))
    c.commit(); c.close()


def test_goc_patlarsa_fk_denetimi_geri_acilir():
    """
    `PRAGMA foreign_keys` BAGLANTI duzeyindedir ve bu baglanti bot
    surecinin omru boyunca acik kalir. Goc ortasinda bir istisna cikarsa
    ve pragma `finally` ile geri acilmazsa, surecin GERI KALAN TUM
    yazmalari FK denetimsiz calisir — hasar gocun cok otesine yayilir.

    Arizayi enjekte ediyoruz: tasima adimini patlatip pragma'yi olcuyoruz.
    """
    import tempfile, pathlib as _p, sqlite3
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        yol = _p.Path(d) / "t.db"
        _eski_semali_db(yol)
        db = Database(yol)
        assert db._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

        db._predictions_tablosunu_tasi = lambda *_a, **_k: (_ for _ in ()).throw(
            sqlite3.OperationalError("enjekte edilmis ariza"))
        try:
            db._predictions_ajan_gocu()
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("ariza yutulmus")

        assert db._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1, \
            "goc patladi ve FK denetimi KAPALI kaldi"
        db.close()


def test_goc_kayip_olursa_geri_sarar():
    """
    Sayim denetimi ISLEMIN ICINDE ve DROP'tan ONCE olmali. Disinda
    olsaydi `RuntimeError` atildiginda eski tablo coktan silinmis ve
    islem commit edilmis olurdu: denetim kaybi bildirir ama ENGELLEMEZ.

    Kaybi enjekte ediyoruz: tasima sirasinda bir satir siliniyor.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        yol = _p.Path(d) / "t.db"
        _eski_semali_db(yol, kayitlar=3)
        db = Database(yol)

        gercek = db._goc_islemi

        class _KayipUreten:
            """INSERT'ten sonra bir satir silerek kayip simule eder."""
            def __init__(self, c): self._c = c
            def __getattr__(self, ad): return getattr(self._c, ad)
            def execute(self, sql, *a):
                r = self._c.execute(sql, *a)
                if sql.lstrip().upper().startswith("INSERT INTO PREDICTIONS"):
                    self._c.execute("DELETE FROM predictions WHERE id="
                                    "(SELECT MIN(id) FROM predictions)")
                return r

        import contextlib

        @contextlib.contextmanager
        def sarmalayici():
            with gercek() as c:
                yield _KayipUreten(c)

        db._goc_islemi = sarmalayici
        try:
            db._predictions_ajan_gocu()
        except RuntimeError as e:
            assert "kayit kaybi" in str(e), e
        else:
            raise AssertionError("kayip fark edilmemis")
        db._goc_islemi = gercek

        # TAM GERI SARMA: DDL de dahil. sqlite3 eski kipte islemi yalnizca
        # DML icin acar, yani ALTER/CREATE otomatik commit olur ve geri
        # sarilmaz; olculdu (2026-08-16): `predictions` BOS kaliyor, veri
        # `predictions_eski`'ye dusuyordu. Goc bu yuzden ACIK BEGIN
        # kullaniyor. Beklenen: hicbir sey olmamis gibi.
        kalanlar = {r["name"] for r in db.query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "predictions" in kalanlar, "canli tablo kaybolmus"
        assert "predictions_eski" not in kalanlar, \
            "yarim goc kalmis — DDL geri sarilmamis"
        assert db.query("SELECT COUNT(*) n FROM predictions")[0]["n"] == 3, \
            "veri kaybedilmis — denetim engellemedi, sadece bildirdi"
        assert "ajan" not in {r["name"] for r in db.query(
            "PRAGMA table_info(predictions)")}, "tablo yeni sekilde kalmis"
        assert db._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        db.close()


def test_goc_yarim_kalmis_tabloyu_temizler():
    """
    Surec goc ortasinda OLDURULURSE `predictions_eski` diskte kalir.
    Temizlenmezse sonraki `ALTER TABLE ... RENAME` "already exists" ile
    patlar — yani TEK BIR COKME gocu kalici olarak bloke ederdi.
    """
    import tempfile, pathlib as _p, sqlite3
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        yol = _p.Path(d) / "t.db"
        _eski_semali_db(yol)
        c = sqlite3.connect(yol)          # cokmus kosudan kalan artik
        c.execute("CREATE TABLE predictions_eski (id INTEGER PRIMARY KEY)")
        c.commit(); c.close()

        db = Database(yol); db.init_schema()
        kalanlar = {r["name"] for r in db.query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "predictions_eski" not in kalanlar, "artik tablo temizlenmemis"
        assert db.query("SELECT COUNT(*) n FROM predictions")[0]["n"] == 3
        assert "ajan" in {r["name"] for r in db.query(
            "PRAGMA table_info(predictions)")}
        db.close()




def test_goc_semasi_ile_schema_sql_ozdes():
    """
    §1c — `predictions` tanimi IKI yerde: gocun satir ici CREATE'i ve
    schema.sql. `IF NOT EXISTS` yuzunden eski veritabanlarinda yalnizca
    goctaki kopya calisir. Biri guncellenip digeri unutulursa ESKI
    veritabanlari eksik kolonla yasar ve bu sessizdir.

    Ayni gercegin iki yerde beyan edilmesi sinifi; test tek panzehir.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        # (a) BOS db -> yalnizca schema.sql calisir
        temiz = Database(_p.Path(d) / "temiz.db"); temiz.init_schema()
        a = {r["name"]: r["type"] for r in temiz.query(
            "PRAGMA table_info(predictions)")}
        temiz.close()

        # (b) ESKI semali db -> goc calisir
        yol = _p.Path(d) / "eski.db"
        _eski_semali_db(yol)
        gocmus = Database(yol); gocmus.init_schema()
        b = {r["name"]: r["type"] for r in gocmus.query(
            "PRAGMA table_info(predictions)")}
        gocmus.close()

        assert a == b, (f"goc semasi ile schema.sql ayrismis\n"
                        f"  yalnizca schema.sql'de: {set(a) - set(b)}\n"
                        f"  yalnizca gocte        : {set(b) - set(a)}")


def test_karne_kucuk_orneklemde_araligi_genis_verir():
    """
    §5 — bu degismez eski `test_karne_kucuk_orneklemi_isaretler` icinde
    yasiyordu ve karne populasyonu degisince onunla birlikte gitme
    riski dogdu. AYRI teste alindi: iki degismez tek teste baglanirsa
    biri digerini de goturuyor.

    n=1'de aralik cok genis olmali; dar cikiyorsa Wilson yerine normal
    yaklasim kullanilmis demektir.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("A", "BUX")
        with db.tx() as c:
            c.execute("""INSERT INTO predictions (olusma_ts, instrument_id, ajan,
                         yon, ufuk_gun, guven, baslangic_fiyat, isabet)
                         VALUES ('2026-08-01',?,'hakem','yukari',5,0.6,10.0,1)""",
                      (iid,))
        alt, ust = Defter(db).karne()["guven_araligi_%"]
        assert ust - alt > 60, (alt, ust)      # n=1 -> cok genis
        db.close()


def test_panel_sessizligi_ve_json_tasmasini_isaretler():
    """
    §2 — iki olculmemis varsayim:

    1. SESSIZLIK. Hakem "kayda deger bir sey yok" derse deftere sifir
       kayit girer; sistem konustugu gunlerde olculur, sustugu gunlerde
       olculmez. Iyi susmak karneye hic yansimaz.
    2. JSON TASMASI. Yapisal cikti istemek modeli "bos vermektense bir
       sey yazayim" tarafina itebilir. Prompt yasakliyor ama bu
       olculmemisti.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.agents import Panel
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        p = Panel(load_settings(), db)
        p._kosuyu_yaz({
            "hakem": ("Bugun one cikan bir sey yok.", {"gorusler": []}),
            "teknik": ("THYAO guclu duruyor.",
                       {"gorusler": [{"sembol": "THYAO"}]}),
            # ozette GECMEYEN sembol JSON'da
            "olay": ("ASML hakkinda bir sey yok.",
                     {"gorusler": [{"sembol": "NVDA"}]}),
        })
        notlar = {r["ajan"]: r["hata"] for r in db.query(
            "SELECT ajan, hata FROM panel_runs")}
        assert "sessiz" in (notlar["hakem"] or ""), notlar
        assert notlar["teknik"] is None, notlar
        assert "ozette gecmeyen" in (notlar["olay"] or ""), notlar

        # Sessizlik SAYILABILIR olmali
        sessiz = db.query(
            """SELECT COUNT(*) n FROM panel_runs
               WHERE json_durum='ok' AND gorus_sayisi=0""")[0]["n"]
        assert sessiz == 1, sessiz
        db.close()


def test_hakem_sapmasi_bilgi_imhasini_gorur():
    """
    §3+ — hakem ajanlari bastirip one cikariyor. Cogunluk bir yon
    soylerken hakem tersini secip yaniliyorsa, bu katman BILGI IMHA
    ediyor. Ne `karne` ne `ajan_karnesi` bunu gosterir: ikisi de mutlak
    isabet olcer, ARALARINDAKI FARKI olcmez.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("A", "BUX")
        with db.tx() as c:
            # Uc ajan "yukari" ve HAKLI; hakem "asagi" ve YANILIYOR
            for ajan in ("teknik", "temel", "olay"):
                c.execute("""INSERT INTO predictions (olusma_ts,instrument_id,
                    ajan,yon,ufuk_gun,guven,baslangic_fiyat,isabet)
                    VALUES ('2026-08-01',?,?,'yukari',5,0.7,10.0,1)""",
                          (iid, ajan))
            c.execute("""INSERT INTO predictions (olusma_ts,instrument_id,
                ajan,yon,ufuk_gun,guven,baslangic_fiyat,isabet)
                VALUES ('2026-08-01',?,'hakem','asagi',5,0.7,10.0,0)""", (iid,))
        s = Defter(db).hakem_sapmasi()
        assert s["ayrisan"] == 1 and s["ayrismada_panel_hakli"] == 1, s
        assert s["ayrismada_hakem_hakli"] == 0, s
        assert s["yeterli_mi"] is False, "n=1 yeterli sayilmis"
        db.close()


def test_atilan_sayaclari_gercekten_yazilir():
    """
    §4 — sayaclar `panel_runs`'a YAZILMIYORDU; uc kolon surekli 0
    kaliyordu. "Bayrak yerine sayac" gerekcesi, sayac yazilmayinca
    kendi kendini curutuyor: kullanilmayan kolon, kacindigimiz olu
    konfigurasyonun ta kendisi.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.runner import Nabiz
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        ts = "2026-08-16T20:00:00+00:00"
        with db.tx() as c:
            for ajan in ("teknik", "hakem"):
                c.execute("""INSERT INTO panel_runs (run_ts,ajan,ham_metin,
                    json_durum,gorus_sayisi) VALUES (?,?,'m','ok',1)""",
                          (ts, ajan))
        n = Nabiz(load_settings(), db)
        n._atilanlari_isle(
            {"yazilan": 1, "atilan_sembol_yok": 3, "atilan_seri_yok": 1,
             "atilan_cakisma": 2},
            {"yazilan": 1, "atilan_sembol_yok": 0, "atilan_seri_yok": 0,
             "atilan_cakisma": 5})
        v = {r["ajan"]: (r["atilan_sembol_yok"], r["atilan_seri_yok"],
                         r["atilan_cakisma"]) for r in db.query(
            "SELECT ajan, atilan_sembol_yok, atilan_seri_yok, atilan_cakisma "
            "FROM panel_runs")}
        assert v["teknik"] == (3, 1, 2), v
        assert v["hakem"] == (0, 0, 5), v
        db.close()




def _okunan_ayar_yollari():
    """Kaynak kodda `s.get("a.b")` ile okunan tum ayar yollari."""
    import re, pathlib as _p
    kok = _p.Path(__file__).parent.parent / "src" / "finagent"
    yollar = {}
    for f in kok.rglob("*.py"):
        for m in re.finditer(r'\.get\(\s*"([a-z_][a-z0-9_.]*\.[a-z0-9_.]+)"',
                             f.read_text(encoding="utf-8")):
            yollar.setdefault(m.group(1), f.name)
    return yollar


def test_okunan_her_ayar_yaml_de_tanimli():
    """
    §12 — AYARLANABILIR GORUNEN HER DEGER GERCEKTEN AYARLANABILIR OLMALI.

    Olculdu 2026-08-16: kodun okudugu 62 yolun 22'sinin YAML'da karsiligi
    yoktu. En zararlisi `sources.binance.daily_bars`: deger YAML'da
    `watchlist.binance.daily_bars` altinda duruyordu, kod baska yere
    bakiyordu. Dis inceleme o satiri OLGU sanip "gecmis 2,7 yil" diye
    rapor etti — yani olu konfigurasyon yalnizca ayari degil, okuyani da
    yaniltiyor.

    Daha kotusu ayni koke sahipti: `source_enabled()` de `sources.*`
    okuyor, dolayisiyla `watchlist:` altinda kalan kripto collector'lari
    `run.py collect` ve `run.py daily` varsayilan zincirinde HIC
    CALISMIYORDU. Nabiz betigi acikca `--site` verdigi icin gorunmemisti.

    Degismez: `s.get()` ile okunan her yol YAML'da TANIMLI olmali.
    Degeri null olabilir (bilerek bos), ama ANAHTAR bulunmali.
    """
    import yaml, pathlib as _p
    ham = yaml.safe_load(
        (_p.Path(__file__).parent.parent / "config" / "settings.yaml")
        .read_text(encoding="utf-8"))

    def tanimli(yol):
        cur = ham
        for parca in yol.split("."):
            if not isinstance(cur, dict) or parca not in cur:
                return False
            cur = cur[parca]
        return True

    eksik = {y: f for y, f in _okunan_ayar_yollari().items() if not tanimli(y)}
    assert not eksik, (
        "kodun okudugu ama YAML'da tanimli olmayan ayar yollari:\n" +
        "\n".join(f"  {y}  <- {f}" for y, f in sorted(eksik.items())))


def test_settings_yaml_de_cift_anahtar_yok():
    """
    YAML'da ayni haritada tekrar eden anahtar SESSIZCE son yazani alir.

    Bu tam da yukaridaki testi yazarken basima geldi: `browser:` blogunda
    zaten `user_agent: null` varken ikinci bir `user_agent: ""` ekledim ve
    deger sessizce null kaldi. Hata mesaji yok, uyari yok — yalnizca
    beklenenden farkli bir davranis.
    """
    import pathlib as _p
    yol = _p.Path(__file__).parent.parent / "config" / "settings.yaml"
    gorulen, cakisan = {}, []
    for i, satir in enumerate(yol.read_text(encoding="utf-8").splitlines(), 1):
        if not satir.strip() or satir.lstrip().startswith("#"):
            continue
        if ":" not in satir:
            continue
        girinti = len(satir) - len(satir.lstrip())
        anahtar = satir.strip().split(":", 1)[0].strip()
        if anahtar.startswith("-"):
            continue
        # Daha derin girintileri unut: yeni bir harita basliyor
        for g in [g for g in gorulen if g > girinti]:
            gorulen.pop(g)
        onceki = gorulen.setdefault(girinti, {})
        if anahtar in onceki:
            cakisan.append(f"{anahtar} (satir {onceki[anahtar]} ve {i})")
        onceki[anahtar] = i
    assert not cakisan, "settings.yaml'de cift anahtar: " + ", ".join(cakisan)




def test_promptlar_var_olan_veriyi_yok_diye_beyan_etmez():
    """
    §13 — prompt'ta "YOK" diye beyan edilen her sey GERCEKTEN yok olmali.

    Uc bayat satir testlerden gecerek bugune geldi: `chat.py` "FX serisi
    veride YOK" diyordu ama `fx` araci ve `fx_rates` tablosu vardi;
    `strategist.py` yalnizca BIST ve Avrupa ETF'lerinden bahsediyordu
    ama portfoyun bir ayagi kripto; `portfolio.py` "FX donusumu v2'de"
    diyordu. Ucu de beyan ile gercegin sessizce ayrismasi.

    Bu test tam kapsamli bir dogrulayici degil — prompt metnini kod
    olgusuyla karsilastirmanin ucuz hali. Gercek cozum P2-11: yetenek
    listesinin URETILMESI.
    """
    import pathlib as _p
    kok = _p.Path(__file__).parent.parent / "src" / "finagent"
    from finagent.bot.tools import ARAC_ADLARI
    araclar = {a.rsplit("__", 1)[-1] for a in ARAC_ADLARI}

    chat = (kok / "bot" / "chat.py").read_text(encoding="utf-8")
    strat = (kok / "analysis" / "strategist.py").read_text(encoding="utf-8")
    portf = (kok / "analysis" / "portfolio.py").read_text(encoding="utf-8")

    assert "fx" in araclar, "test kendi varsayimini yitirmis"
    assert "FX serisi veride\n   YOK" not in chat and "FX serisi veride YOK" not in chat, \
        "chat.py hala 'FX yok' diyor ama `fx` araci var"
    assert "v2'de eklenecek" not in portf, \
        "portfolio.py hala 'FX v2'de' diyor ama fx_rates tablosu var"
    assert "KRIPTO" in strat or "kripto" in strat, \
        "strategist.py kriptodan hic bahsetmiyor ama portfoyun bir ayagi o"


def test_referans_coin_vekili_btc_olmali():
    """
    §3.1 — piyasa vekili PARA BIRIMINE gore secilince, CoinGecko'dan USD
    olarak gelen 21 referans coin (XMR, HYPE, OKB, KAS...) QQQ'ya
    baglaniyordu. Yani Monero'nun anormal getirisi NASDAQ-100
    regresyonuyla hesaplanip "piyasa modeli" diye beyan edilecekti.

    Varlik sinifi para biriminden ONCE gelmeli: kripto icin dogru vekil,
    kotasyon USD de olsa USDT de olsa BTC'dir.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        btc = db.upsert_instrument("BTC", "BINANCE", "Bitcoin", "crypto", "USDT")
        xmr = db.upsert_instrument("XMR", "CRYPTO", "Monero", "crypto", "USD")
        qqq = db.upsert_instrument("QQQ", "INDEX", "Nasdaq 100", "index", "USD")
        for iid, ccy in ((btc, "USDT"), (xmr, "USD"), (qqq, "USD")):
            db.upsert_prices(iid, [{"ts": "2026-08-14", "close": 10.0},
                                   {"ts": "2026-08-15", "close": 11.0}],
                             "t", currency=ccy)
        v = db.piyasa_vekili(xmr)
        assert v is not None, "referans coin vekilsiz kalmis"
        sembol = db.query("SELECT symbol FROM instruments WHERE id=?",
                          (v["instrument_id"],))[0]["symbol"]
        assert sembol == "BTC", f"USD kripto {sembol}'ya baglanmis, BTC olmali"
        assert db.piyasa_vekili(btc) is None, "BTC kendi vekili olmus"


def test_gundem_portfoye_yer_ayirir():
    """
    §3.5 — saf "en guclu N" secimi evren buyuklugunu gizli agirlik gibi
    iceri sokuyor. Olculdu: 251 BIST sembolu, 12 slotun 10'unu aliyordu;
    oysa portfoy BUX + BINANCE ve BIST'te tek pozisyon yok.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.runner import Nabiz, PANEL_ADAY
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        sahip = db.upsert_instrument("MINE", "BUX", "Sahip", "equity", "EUR")
        with db.tx() as c:
            c.execute("""INSERT INTO positions (snapshot_ts,account,instrument_id,
                         quantity,market_value,currency)
                         VALUES ('2026-08-16','bux',?,1,100.0,'EUR')""", (sahip,))
        # Portfoy sinyali ZAYIF, kalabalik evren GUCLU
        guclu = [{"instrument_id": 999 + i, "sembol": f"X{i}", "venue": "BIST",
                  "guc": 0.9} for i in range(PANEL_ADAY + 5)]
        guclu.append({"instrument_id": sahip, "sembol": "MINE",
                      "venue": "BUX", "guc": 0.56})
        g = Nabiz(load_settings(), db)._gundem(guclu)
        assert len(g) == PANEL_ADAY
        assert any(x["sembol"] == "MINE" for x in g), \
            "portfoy sinyali kalabalik evrene ezilmis"
        db.close()




def test_stablecoin_suzgeci_alinamazsa_evren_yazilmaz():
    """
    Suzgec alinamadiginda "devre disi biraksin, gorunur olur" tasarimi
    SAHADA COKTU (2026-08-16, CoinGecko hiz siniri): 13 stablecoin
    izleme listesine girdi ve orada KALDI.

    Gerekcenin kacirdigi sey yazmanin KALICI olmasi; ustelik budama
    yalnizca ilk N disina dusenleri temizliyor ve stablecoin'ler ilk
    100'un tam icinde. Eksik suzgecle yazmaktansa hic yazmamak dogru:
    evren dunden duruyor, bir gun tazelenmemek zarar vermez.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.collectors.kriptoevren import KriptoEvrenCollector
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        c = KriptoEvrenCollector(load_settings(), db)
        c._ilk_n = lambda *_a: [
            {"symbol": "usdt", "name": "Tether", "id": "tether",
             "total_volume": 9e9, "market_cap": 1e11, "market_cap_rank": 3}]
        c._stablecoinler = lambda *_a: set()      # kategori ALINAMADI
        c._binance_spot = lambda *_a: {"USDT", "BTC"}
        r = c.collect()
        assert r.status == "error", r.status
        assert "YAZILMADI" in (r.error or ""), r.error
        assert db.query("SELECT COUNT(*) n FROM watchlist")[0]["n"] == 0, \
            "suzgec yokken evren yazilmis"
        db.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
    print("\nTum duman testleri gecti.")
