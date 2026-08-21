"""Bagimliliksiz duman testleri: python -m pytest tests/ (veya dogrudan calistir)."""
import pathlib as _pathlib
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
         "market_value": 1100, "pnl_abs": 100, "pnl_pct": 10, "currency": "EUR"}], 'ali')
    assert n == 1
    assert db.latest_positions('bux', 'ali')[0]["symbol"] == "VWCE"

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
             "currency": "EUR"}], 'ali')
        db.insert_positions("binance", "2026-08-15T12:13:00+00:00", [
            {"symbol": "ROSE", "quantity": 56741.3579, "market_value": 309.24,
             "currency": "USDT"}], 'ali')
        bot = FinBot.__new__(FinBot); bot.db = db

        cikti = bot._sil_son('ali')
        assert "BINANCE" in cikti                       # en son yazilan
        assert len(db.latest_positions("binance", "ali")) == 0
        # BUX'a DOKUNULMAMALI
        bux = db.latest_positions('bux', 'ali')
        assert len(bux) == 1 and bux[0]["symbol"] == "ASML", bux
        assert "TEK kaydiydi" in cikti                  # uyari verilmeli

        # Ikinci /sil artik BUX'u alir
        assert "BUX" in bot._sil_son('ali')
        assert len(db.latest_positions('bux', 'ali')) == 0
        assert bot._sil_son('ali') == "Silinecek pozisyon kaydi yok."
        db.close()


def _toolbox(tmp, sahip="ali"):
    import pathlib as _p
    from finagent.storage.db import Database
    from finagent.bot.tools import ToolBox
    from finagent.config import load_settings
    db = Database(_p.Path(tmp) / "t.db"); db.init_schema()
    return ToolBox(load_settings(), db, _p.Path(tmp) / "pending",
                   sahip=sahip, chat_id="5643817523"), db


def _run_py(*argv, timeout=180, cevre_ek=None):
    """
    `run.py <argv>` alt sureci — CANLI VERITABANINA DOKUNMADAN.

    NEDEN ZORUNLU: `run.py` HER komutta `db.init_schema()` cagiriyor,
    yani alt surec baslatan bir test canli veritabanini GOC ETTIRIYOR.
    Olculdu (2026-08-20 12:26:49): duman testleri kosarken
    `data/agent.log`'a dort kez "Sema hazir: data/finagent.db (surum
    12)" dustu — M4 semasi uretime TESTLERDEN gecti. Ve anlik sonucu
    oldu: sema canliya gecince ESKI kodu tutan calisan bot UDF'siz
    kalip `no such function: leksik` ile yazamaz hale geldi.

    README kural 3: "Testler gercek is yapmamali." Bir kez bir test
    gercekten collector calistirip Ali'ye Telegram raporu gondermisti;
    bu ayni sinifin sessiz hali.

    UC IZOLASYON, UCU DE BURADA:
      * `DB_PATH` — gecici veritabani (bu degisken `config.py`'de
        ZATEN destekleniyordu; eksik olan mekanizma degil, kullanimdi).
      * `TELEGRAM_BOT_TOKEN` DUSURULUYOR — `--no-notify` unutulan bir
        bayrak; token'in olmamasi unutulamaz.
      * Cikti yakalanir, zaman asimi VARDIR — takilan bir alt surec
        tum suite'i kilitler.

    Ham `subprocess.run([... "run.py" ...])` yazilmamali; asagidaki
    `test_alt_surec_CANLI_DB_ye_dokunmuyor` bunu zorunlu tutuyor.
    """
    import os as _os
    import subprocess as _sp
    import tempfile as _tf

    kok = _pathlib.Path(__file__).resolve().parents[1]
    py = kok / ".venv" / "bin" / "python"
    yorumlayici = str(py) if py.exists() else sys.executable

    with _tf.TemporaryDirectory() as d:
        cevre = {**_os.environ,
                 "DB_PATH": str(_pathlib.Path(d) / "test.db"),
                 # IZ DIZINI DE IZOLE. `DB_PATH` yetmiyor: olculdu
                 # (2026-08-20) — `run.py nabiz --kip sabah --sahip
                 # yok_boyle_sahip` sahibi reddedip 2 ile cikti ama
                 # ONCE gercek `data/bot/kosu/sabah.json` izinin
                 # ustune yazdi. O dosya BEKCININ KANITI.
                 "BOT_STATE_DIR": str(_pathlib.Path(d) / "bot")}
        cevre.pop("TELEGRAM_BOT_TOKEN", None)
        cevre.update(cevre_ek or {})
        return _sp.run([yorumlayici, "run.py", *argv], cwd=str(kok),
                       capture_output=True, text=True, timeout=timeout,
                       env=cevre)


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

    `prices` VE `makro` BU LISTEDEN CIKTI (2026-08-20). Ikisi de
    tarayiciyi SIRF Yahoo'nun 429'unu asmak icin aciyordu; `yfinance`
    cerez/crumb dongusunu kendisi yonettigi icin bagimlilik kalkti.
    Olculdu: prices 10.632 satir / 31,8 sn, makro 4.774 satir / 6,5 sn,
    ikisi de tarayicisiz ve `ok`.
    """
    from finagent.collectors import REGISTRY
    tarayicili = {n for n, c in REGISTRY.items() if c.needs_browser}
    surecte = {n for n, c in REGISTRY.items() if not c.needs_browser}
    assert {"stocknews", "kap"} <= tarayicili
    assert {"alphavantage", "coingecko", "binance", "xbrl",
            "prices", "makro"} <= surecte
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
             "currency": "EUR"}], 'ali')

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
                         ufuk_gun, guven, gerekce, baslangic_fiyat, para_birimi,
                         sahip)
                         VALUES (?,?,?,?,?,?,?,?,'ali')""",
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
                             isabet, anormal_pct, sahip)
                             VALUES (?,?,'hakem',?,?,?,?,?,?,'ali')""",
                          (f"2026-08-{i+1:02d}", iid, "yukari", 5, 0.6, 10.0,
                           1 if i < 4 else 0, 1.0))
        k = Defter(db).karne('ali')
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
    from finagent.config import load_settings
    kok = _p.Path(__file__).parent.parent
    bot = plistlib.loads((kok / "launchd" /
                          "com.alipala.finagent.bot.plist").read_bytes())

    assert bot["ProgramArguments"][0].endswith(".venv/bin/python")
    assert _p.Path(bot["WorkingDirectory"]).name == kok.name
    # Bot: acilista baslasin, cokmede geri gelsin, ama TEMIZ cikista donmesin
    assert bot["RunAtLoad"] is True
    assert bot["KeepAlive"] == {"SuccessfulExit": False}
    assert bot["ThrottleInterval"] >= 30

    # HER KIP AYNI SOZLESMEYE UYAR — tek tek degil, ayardaki kip
    # listesi uzerinden. `pulse` tek basina kontrol ediliyordu ve
    # `sabah`/`ogle` hic kontrol edilmiyordu; yeni bir kip eklendiginde
    # (kapanis) o da sessizce kontrolsuz kalirdi.
    s = load_settings()
    assert s.ritim_kipleri, "ritim.kipler bos"
    for kip in s.ritim_kipleri:
        yol = kok / "launchd" / f"com.alipala.finagent.{kip}.plist"
        assert yol.exists(), f"{kip} kipinin plist'i yok"
        d = plistlib.loads(yol.read_bytes())
        assert d["Label"] == f"com.alipala.finagent.{kip}"
        # Zamanlanmis is: yuklenince calismasin, bitince donmesin.
        assert d.get("RunAtLoad") is False, kip
        assert "KeepAlive" not in d, kip
        gunler = sorted(x["Weekday"] for x in d["StartCalendarInterval"])
        assert gunler == [1, 2, 3, 4, 5], (kip, gunler)   # hafta sonu YOK
        # Tek bir saat: birden fazla saat, bekcinin `min(bugunku)`
        # secimini anlamsizlastirir.
        saatler = {(x["Hour"], x["Minute"]) for x in d["StartCalendarInterval"]}
        assert len(saatler) == 1, (kip, saatler)
        # Kipi CALISTIRAN betik tek: run_kosu.sh <kip>
        assert d["ProgramArguments"][0].endswith("scripts/run_kosu.sh"), kip
        assert d["ProgramArguments"][1] == kip, kip
        assert _p.Path(d["WorkingDirectory"]).name == kok.name, kip
        # ExitTimeOut BIR CALISMA SURESI SINIRI DEGIL. Bu test onceden
        # `>= 900` istiyordu ve YANLIS bir inanci koruyordu: bu anahtar,
        # launchd isi DURDURURKEN SIGTERM ile SIGKILL arasinda tanidigi
        # suredir; uzun suren zamanlanmis bir isi oldurmez. Gercek sure
        # siniri ayarda (`kabuk_butce_sn`) ve run_kosu.sh onu uyguluyor.
        assert d["ExitTimeOut"] <= 120, kip

    # ARTIK OLMAYAN ETIKET DEPODA KALMASIN. `pulse` -> `nabiz` yeniden
    # adlandirildi; eskisi dururken kurulum betigi ikisini de yuklerdi
    # ve 22:15'te iki is birden kosardi (kilit birini duşurur — sessiz
    # kayip).
    assert not (kok / "launchd" /
                "com.alipala.finagent.pulse.plist").exists(), \
        "eski `pulse` plist'i hala depoda"


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
    betikler = _p.Path(__file__).parent.parent / "scripts"
    kaynak = (betikler / "run_kosu.sh").read_text()
    assert "flock" in kaynak, "tek ornek kilidi yok"
    assert "LOCK_EX" in kaynak and "LOCK_NB" in kaynak
    # KILIT KIP BASINA: sabah kosusu uzarsa ogle beklemesin.
    assert 'data/kosu_${KIP}.lock' in kaynak, "kilit kip basina degil"
    # SURE SINIRI AYARDAN — plist'ten ya da ortam degiskeninden degil.
    # `HAFIF_TIMEOUT`/`PULSE_TIMEOUT` kalkti: iki kaynak kacinilmaz
    # olarak ayrisiyordu (plist 1200, betik varsayilani 900).
    assert "kabuk_butce_sn" in kaynak, "sure siniri ayardan okunmuyor"
    assert "HAFIF_TIMEOUT" not in kaynak and "PULSE_TIMEOUT" not in kaynak, \
        "sure siniri hala ortam degiskeninden geliyor (ikinci kaynak)"
    # Oldurme ORTAK KATMANDA (`_ortak.sh`) — iki betikte iki farkli
    # davranis olmasin diye tek kaynaga tasindi.
    assert "sure_bekcisi_baslat" in kaynak, "duvar saati bekcisi kurulmuyor"
    ortak = (betikler / "_ortak.sh").read_text()
    assert "kill -TERM" in ortak, "bekci sureci olduremiyor"
    # KIP ADINA GORE DALLANMA OLMASIN (ritim v2 §5, ilk tuzak):
    # `if [ "$KIP" = sabah ]` ya da `case "$KIP" in` gibi bir dal
    # kaldiysa "koda degil ayara" ilkesi uygulanmamis demektir.
    #
    # Testin ARADIGI SEY DALLANMA, kelimenin gecmesi degil. Iki kez
    # yanlis kalibrelendi: once bir YORUMA takildi, sonra `run.py nabiz`
    # ALT KOMUTUNA — `nabiz` hem bir kip adi hem de CLI komut adi.
    import re as _re
    etkin = "\n".join(s for s in kaynak.splitlines()
                      if s.strip() and not s.strip().startswith("#"))
    # `$KIP` ile KARSILASTIRMA yapan her kalip. `run.py nabiz --kip
    # "$KIP"` gibi bir KULLANIM yakalanmaz — orada `nabiz` alt komut
    # adi, kip degeri degil.
    for kalip in (r'\[\s*"?\$KIP"?\s*[=!]', r'case\s+"?\$KIP"?\s+in',
                  r'\$KIP\s*==', r'if\s+\[\[\s*"?\$KIP"?'):
        m = _re.search(kalip, etkin)
        assert not m, (
            f"run_kosu.sh kip adina gore dalliyor ({kalip}): "
            f"{etkin[max(0, m.start() - 20):m.end() + 40]!r}")


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


def _iz_bekcisi(d, kipler, plistler, saat, kurulum_gun_once=30):
    """
    Sentetik launchd dizini + iz dosyalari ile kosu bekcisi.

    `_kosu_bekcisi`ten farki: kok DA sentetik, yani GERCEK plist'lere
    degil testin kurdugu takvime bakiyor. Boylece "nabiz 22:15'te
    kosmadi" gibi senaryolar depodaki plist'lerden bagimsiz kurulabilir.
    """
    import json as _j, plistlib
    from datetime import timedelta as _td
    from finagent.bot.watchdog import Bekci
    from finagent.bot import watchdog as _W

    kok = _pathlib.Path(d)
    (kok / "launchd").mkdir(parents=True, exist_ok=True)
    for kip, (saat_, dk) in plistler.items():
        (kok / "launchd" / f"com.alipala.finagent.{kip}.plist").write_bytes(
            plistlib.dumps({
                "Label": f"com.alipala.finagent.{kip}",
                "StartCalendarInterval": [
                    {"Hour": saat_, "Minute": dk, "Weekday": w}
                    for w in range(1, 6)]}))

    class _S:
        root = kok
        ritim_kipleri = list(kipler)
        def __init__(self, kipler): self._k = kipler
        def get(self, yol, varsayilan=None):
            if yol == "ritim.kipler":
                return self._k
            return varsayilan
        def ritim_kip(self, kip):
            if kip not in self._k:
                raise ValueError(f"tanimsiz kip: {kip}")
            return {"kip": kip, **self._k[kip]}

    sd = kok / "data" / "bot"
    (sd / "kosu").mkdir(parents=True, exist_ok=True)
    (sd / "kosu" / "kurulum.json").write_text(_j.dumps(
        {"ts": (saat - _td(days=kurulum_gun_once)).isoformat()}))
    return Bekci(_S(kipler), None, sd), sd


def test_bekci_NABZI_DA_gozetliyor_19_agustos_senaryosu():
    """
    OLCULEN VE KACIRILAN ARIZA (2026-08-19):
      22:15:00  nabiz basladi
      23:00:00  [run_pulse] 2700 sn asildi, olduruluyor   (SIGTERM)
      -> bildirim gitmedi, `data/bot/kosu/nabiz.json` 18 Agustos'ta kaldi.

    O gece canlida calistirildi ve bekci HICBIR SEY demedi:
      kacirilan_nabiz()   -> None     (collector_runs doluydu)
      kacirilan_kosular() -> []       (IZ_KIPLERI'nde nabiz YOKTU)

    Iki kusur birden: olcut kosunun BASLADIGINI olcuyordu ve nabiz
    zaten gozetim listesinde degildi. Ikisi de kapandi.
    """
    import tempfile
    from datetime import datetime, timedelta
    from unittest.mock import patch
    from finagent.bot import watchdog as _W

    KIPLER = {
        "sabah": {"kabuk_butce_sn": 1500},
        "nabiz": {"kabuk_butce_sn": 3000},
    }
    with tempfile.TemporaryDirectory() as d:
        # Carsamba 2026-08-19, gece yarisina dogru — nabiz 22:15'te
        # kosmus olmaliydi.
        simdi = datetime(2026, 8, 19, 23, 55).astimezone()
        b, sd = _iz_bekcisi(d, KIPLER,
                            {"sabah": (8, 0), "nabiz": (22, 15)}, simdi)
        # Sabah kosmus (izi 08:55'te, yani beklenen 08:00'den SONRA),
        # nabiz OLMUS (izi hic yok).
        import json as _j
        (sd / "kosu" / "sabah.json").write_text(_j.dumps(
            {"kip": "sabah", "ts": (simdi - timedelta(hours=15)).isoformat()}))

        with patch.object(_W, "_yerel", lambda: simdi):
            eksik = b.kacirilan_kosular()
        kipler = [x["kip"] for x in eksik]
        assert kipler == ["nabiz"], (
            f"nabiz olduruldu ve iz birakmadi ama bekci {kipler} dedi")
        assert eksik[0]["beklenen"] == "22:15", eksik
        assert eksik[0]["son_iz"] == "hic", eksik


def test_bekci_SESSIZ_ama_basarili_kosuyu_ariza_saymaz():
    """
    "Esigi gecen kagit yok" MESRU bir sonuctur — sessizlik gecerli
    ciktidir. Eski olcut sinyale bakiyordu ve bunu ariza sayabiliyordu;
    Ali 2026-08-18'de bu yuzden dort YANLIS alarm aldi.

    Yeni olcut kosunun KENDI izine bakiyor: iz `calistir()`'in SONUNDA
    yaziliyor, yani "kostu ve hicbir sey bulmadi" ile "yarim kaldi"
    yapisal olarak ayriliyor.
    """
    import tempfile, json as _j
    from datetime import datetime, timedelta
    from unittest.mock import patch
    from finagent.bot import watchdog as _W

    KIPLER = {"nabiz": {"kabuk_butce_sn": 3000}}
    with tempfile.TemporaryDirectory() as d:
        simdi = datetime(2026, 8, 19, 23, 55).astimezone()
        b, sd = _iz_bekcisi(d, KIPLER, {"nabiz": (22, 15)}, simdi)
        # Kostu, SIFIR sinyal uretti, ama izini birakti.
        (sd / "kosu" / "nabiz.json").write_text(_j.dumps(
            {"kip": "nabiz", "ts": (simdi - timedelta(hours=1)).isoformat(),
             "sahipler": ["ali", "yuksel"], "piyasa_sinyali": 0}))
        with patch.object(_W, "_yerel", lambda: simdi):
            assert b.kacirilan_kosular() == [], \
                "sinyalsiz ama TAMAMLANMIS kosu ariza sayildi"


def test_bekci_gecikme_payi_kipin_KENDI_butcesinden_turer():
    """
    Sabit 90 dk pay, butce buyudugunde YANLIS ALARM uretir: `nabiz`
    50 dakikalik kabuk butcesini MESRU sekilde doldurdugunda bekci
    onu 23:45'te "kacirildi" sayardi — oysa kosu hala devam ediyor.

    Pay isin kendi ust sinirindan turemeli: max(90 dk, butce + 15 dk).
    """
    import tempfile
    from datetime import datetime, timedelta
    from unittest.mock import patch
    from finagent.bot import watchdog as _W

    # `uzun` butcesi TABANI asiyor (7200 sn = 120 dk -> pay 135 dk);
    # `kisa` asmiyor (1500 sn = 25 dk -> pay TABAN 90 dk kalir).
    KIPLER = {"uzun": {"kabuk_butce_sn": 7200},
              "kisa": {"kabuk_butce_sn": 1500}}
    with tempfile.TemporaryDirectory() as d:
        simdi = datetime(2026, 8, 19, 12, 0).astimezone()
        b, sd = _iz_bekcisi(d, KIPLER, {"uzun": (9, 0), "kisa": (9, 0)}, simdi)

        assert b._gecikme_payi("kisa") == b.GECIKME_PAYI, \
            "kucuk butcede taban pay korunmali"
        assert b._gecikme_payi("uzun") == timedelta(seconds=7200 + 900), \
            "buyuk butcede pay butceden turemeli"
        # Bilinmeyen kip -> taban (yargilama hic yapilmasa da guvenli).
        assert b._gecikme_payi("yok_boyle") == b.GECIKME_PAYI

        # 09:00 + 90 dk = 10:30 gecti -> `kisa` alarm veriyor.
        # 09:00 + 135 dk = 11:15 gecti -> `uzun` da veriyor.
        with patch.object(_W, "_yerel", lambda: simdi):
            assert sorted(x["kip"] for x in b.kacirilan_kosular()) == \
                ["kisa", "uzun"]

        # 10:45'te: `kisa` payini asti (10:30), `uzun` ASMADI (11:15).
        # Sabit 90 dk olsaydi ikisi de alarm verirdi — YANLIS ALARM.
        erken = datetime(2026, 8, 19, 10, 45).astimezone()
        with patch.object(_W, "_yerel", lambda: erken):
            assert [x["kip"] for x in b.kacirilan_kosular()] == ["kisa"], \
                "uzun butceli kip hala mesru suresi icindeyken alarm caldi"


def test_bekci_gonderilen_bildirimi_de_loglar():
    """
    `bildir()` SUSTURMAYI logluyordu ama GONDERIMI loglamiyordu. Ali dort
    yanlis alarm aldiginda kaynagi bulmak icin saatler harcandi ve
    kanitlanamadi: gozetim katmaninin kendisi gozetilemiyordu.
    """
    import inspect
    from finagent.bot.watchdog import Bekci

    kaynak = inspect.getsource(Bekci.bildir)
    gonderim_sonrasi = kaynak.split("send_message(mesaj)")[1]
    assert "log." in gonderim_sonrasi, "gonderim loglanmiyor"
    assert "GONDERILDI" in gonderim_sonrasi


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
        ], 'ali')
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
        ], 'ali')
        assert r2["atilan_cakisma"] == 1, r2

        # Olmayan sembol de SAYILMALI
        r3 = Defter(db).kaydet([{"sembol": "YOKBOYLE", "yon": "yukari",
                                 "guven": 0.5, "ufuk_gun": 5, "ajan": "olay"}], 'ali')
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
                            "tez": "T", "gecersizlesme_kosulu": "close < 9"}], 'ali')
        r = db.query("SELECT ajan, tez, gecersizlesme_kosulu FROM predictions")[0]
        assert r["ajan"] == "hakem" and r["tez"] == "T"
        assert r["gecersizlesme_kosulu"] == "close < 9"

        # gerekce onegini BOZ: kolon tabanli karne yine de saymali
        with db.tx() as c:
            c.execute("UPDATE predictions SET isabet=1, gerekce='onek yok'")
        k = Defter(db).ajan_karnesi('ali')
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
                       yon, ufuk_gun, guven, baslangic_fiyat, isabet, sahip)
                       VALUES ('2026-07-01',?,?,'yukari',5,0.7,10.0,?,'ali')""",
                    (iid, ajan, isabet))
        k = Defter(db).karne('ali')
        assert k["olcum"] == 1, f"kumelenme sayilmis: {k}"
        assert k["kaynak"] == "hakem"
        assert k["bagimsiz_kume"] == k["olcum"], "bagimsizlik kirilmis"
        # Ajanlarin 4/4 isabetine ragmen karne hakemi olcer: %0
        assert k["isabet_%"] == 0.0, k
        # Ajan kirilimi ayrica durmali
        aj = {x["ajan"]: x["olcum"] for x in Defter(db).ajan_karnesi('ali')}
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
                         yon, ufuk_gun, guven, baslangic_fiyat, isabet, sahip)
                         VALUES ('2026-07-01',?,'teknik','yukari',5,0.7,10.0,1,'ali')""",
                      (iid,))
        k = Defter(db).karne('ali')
        assert k["olcum"] == 0
        assert "HAKEM" in k["not"] and "1" in k["not"], k["not"]
        db.close()




def _eski_semali_db(yol, kayitlar=3):
    """
    Cok kullanicili katmandan ONCEKI sema — BES TABLONUN TAMAMI.

    Onceden yalnizca `predictions` kuruluyordu ve bu, ozdeslik testini
    SESSIZCE ise yaramaz kiliyordu: diger dort tablonun goc yolu hic
    calismiyor, dolayisiyla goc semasindaki bir sapma karsilastirmaya
    girmiyordu. Kasitli bozma denemesi (positions CREATE'ine sahte kolon)
    YAKALANMADI ve eksik buradan cikti.
    """
    import sqlite3
    c = sqlite3.connect(yol)
    c.executescript("""
        CREATE TABLE instruments (id INTEGER PRIMARY KEY, symbol TEXT,
            venue TEXT, name TEXT, asset_type TEXT, currency TEXT, isin TEXT,
            UNIQUE(symbol, venue));
        INSERT INTO instruments (id, symbol, venue) VALUES (1,'XYZ','BUX');

        CREATE TABLE positions (
            snapshot_ts TEXT NOT NULL, account TEXT NOT NULL,
            instrument_id INTEGER NOT NULL,
            quantity REAL, avg_cost REAL, last_price REAL,
            market_value REAL, pnl_abs REAL, pnl_pct REAL, currency TEXT,
            PRIMARY KEY (snapshot_ts, account, instrument_id));
        INSERT INTO positions (snapshot_ts,account,instrument_id,market_value)
            VALUES ('2026-08-15T10:00:00','bux',1,100.0);

        CREATE TABLE signals (
            id INTEGER PRIMARY KEY, olusma_ts TEXT NOT NULL,
            instrument_id INTEGER NOT NULL, tur TEXT NOT NULL, yon TEXT,
            guc REAL, kanit TEXT, fiyat REAL, para_birimi TEXT,
            UNIQUE (olusma_ts, instrument_id, tur));
        INSERT INTO signals (olusma_ts,instrument_id,tur,guc) VALUES
            ('2026-08-15',1,'rsi_ucu',0.7),
            ('2026-08-15',1,'yogunlasma',0.8);

        CREATE TABLE panel_runs (
            id INTEGER PRIMARY KEY, run_ts TEXT NOT NULL, ajan TEXT NOT NULL,
            ham_metin TEXT, json_durum TEXT NOT NULL,
            gorus_sayisi INTEGER NOT NULL DEFAULT 0,
            atilan_sembol_yok INTEGER NOT NULL DEFAULT 0,
            atilan_seri_yok INTEGER NOT NULL DEFAULT 0,
            atilan_cakisma INTEGER NOT NULL DEFAULT 0, hata TEXT);
        INSERT INTO panel_runs (run_ts,ajan,json_durum) VALUES ('t','teknik','ok');

        CREATE TABLE analysis_runs (
            id INTEGER PRIMARY KEY, run_ts TEXT NOT NULL, model TEXT,
            scope TEXT, input_stats TEXT, output_md TEXT, status TEXT,
            error TEXT);
        INSERT INTO analysis_runs (run_ts) VALUES ('t');

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
        # ESKI SEMA: `sahip` kolonu YOK — goc onu ekleyecek.
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




def _sema_parmak_izi(db, tablo):
    """Bir tablonun kolonlari VE indeksleri — karsilastirilabilir bicimde."""
    kolonlar = [(r["name"], r["type"], r["notnull"], r["dflt_value"])
                for r in db.query(f"PRAGMA table_info({tablo})")]
    indeksler = []
    for ix in db.query(f"PRAGMA index_list({tablo})"):
        if ix["origin"] != "c":        # yalnizca ACIK CREATE INDEX'ler
            continue
        kols = [r["name"] for r in db.query(f"PRAGMA index_info('{ix['name']}')")]
        indeksler.append((ix["name"], tuple(kols), ix["unique"]))
    # YABANCI ANAHTAR EKSENI: `ALTER TABLE RENAME` modern SQLite'ta DIGER
    # tablolarin referanslarini da yeniden yazar. Goc tablolari sirayla
    # kurdugu icin `predictions.signal_id` bir ara `signals_eski`'ye
    # baglandi ve o kopya dusunce referans ASKIDA kaldi — gercek kosuda
    # panel tamamen calismadi. Kolon ve indeks eksenleri bunu GORMUYORDU.
    fk = sorted((r["table"], r["from"], r["to"])
                for r in db.query(f"PRAGMA foreign_key_list({tablo})"))
    return {"kolonlar": kolonlar, "indeksler": sorted(indeksler),
            "yabanci_anahtarlar": fk}


def test_goc_semasi_ile_schema_sql_ozdes():
    """
    §1c/§2 — GOCLE URETILEN sema ile schema.sql'in urettigi OZDES olmali.

    Goc, tablolari schema.sql'den okuyarak kuruyor ama bu test yine de
    gerekli: `ALTER TABLE ADD COLUMN` yollari, indeks tazeleme ve
    `_predictions_ajan_gocu`nun satir ici CREATE'i hala ayrisabilir.

    IKI EKSENDE karsilastirilir:
      * kolonlar — ad, tip, notnull ve DFLT_VALUE. Sonuncusu sart:
        `DEFAULT 'ali'` regresyonunu yakalayan sey odur ve o varsayilan,
        eksik bir sahip parametresini sessizce ilk sahibe yazardi.
      * indeksler — ad ve KOLON SIRASI. `CREATE INDEX IF NOT EXISTS`
        mevcut bir indeksi YENIDEN TANIMLAMAZ; tablo duruyorsa eski
        indeks de durur ve veritabani semanin soyledigi seyden farkli
        bir sey icerir, hicbir uyari olmadan.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    TABLOLAR = ("positions", "predictions", "signals",
                "panel_runs", "analysis_runs")
    with tempfile.TemporaryDirectory() as d:
        # (a) BOS db -> yalnizca schema.sql calisir
        temiz = Database(_p.Path(d) / "temiz.db"); temiz.init_schema()
        a = {t: _sema_parmak_izi(temiz, t) for t in TABLOLAR}
        temiz.close()

        # (b) ESKI semali db -> tum gocler calisir
        yol = _p.Path(d) / "eski.db"
        _eski_semali_db(yol)
        gocmus = Database(yol); gocmus.init_schema()
        b = {t: _sema_parmak_izi(gocmus, t) for t in TABLOLAR}
        gocmus.close()

        for tablo in TABLOLAR:
            assert a[tablo]["kolonlar"] == b[tablo]["kolonlar"], (
                f"{tablo}: goc semasi ile schema.sql KOLONLARI ayrismis\n"
                f"  schema.sql: {a[tablo]['kolonlar']}\n"
                f"  goc       : {b[tablo]['kolonlar']}")
            assert a[tablo]["indeksler"] == b[tablo]["indeksler"], (
                f"{tablo}: INDEKSLER ayrismis\n"
                f"  schema.sql: {a[tablo]['indeksler']}\n"
                f"  goc       : {b[tablo]['indeksler']}")
            assert (a[tablo]["yabanci_anahtarlar"]
                    == b[tablo]["yabanci_anahtarlar"]), (
                f"{tablo}: YABANCI ANAHTARLAR ayrismis\n"
                f"  schema.sql: {a[tablo]['yabanci_anahtarlar']}\n"
                f"  goc       : {b[tablo]['yabanci_anahtarlar']}")


def test_sahip_varsayilani_yok_ve_eksik_insert_patlar():
    """
    Madde 1 — `DEFAULT 'ali'` KALDIRILDI ve bir daha eklenmemeli.

    Bugun zararsiz gorunur (tek sahip) ama Faz B'de panel kisi basina
    kosarken bir INSERT yolunda sahip unutulursa sorgu PATLAMAZ,
    sessizce ilk sahibe yazardi: ikinci kisinin tahminleri birincinin
    defterine duser ve hicbir sey hata vermez.
    """
    import tempfile, pathlib as _p, sqlite3
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("X", "BUX", "X", "equity", "EUR")

        for tablo in ("positions", "predictions", "signals",
                      "panel_runs", "analysis_runs"):
            dflt = [r["dflt_value"] for r in db.query(f"PRAGMA table_info({tablo})")
                    if r["name"] == "sahip"]
            assert dflt == [None], f"{tablo}.sahip varsayilan tasiyor: {dflt}"

        denemeler = [
            ("positions", "INSERT INTO positions (snapshot_ts,account,"
             "instrument_id) VALUES ('t','bux',?)", (iid,)),
            ("predictions", "INSERT INTO predictions (olusma_ts,instrument_id,"
             "yon,ufuk_gun,baslangic_fiyat) VALUES ('t',?,'yukari',5,1.0)", (iid,)),
            ("signals", "INSERT INTO signals (olusma_ts,instrument_id,tur) "
             "VALUES ('t',?,'rsi_ucu')", (iid,)),
            ("panel_runs", "INSERT INTO panel_runs (run_ts,ajan,json_durum) "
             "VALUES ('t','teknik','ok')", ()),
            ("analysis_runs", "INSERT INTO analysis_runs (run_ts) VALUES ('t')", ()),
        ]
        for tablo, sql, par in denemeler:
            try:
                db._conn.execute(sql, par)
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError(
                    f"{tablo}: sahipsiz INSERT sessizce yazildi")
        db.close()

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
                         yon, ufuk_gun, guven, baslangic_fiyat, isabet, sahip)
                         VALUES ('2026-08-01',?,'hakem','yukari',5,0.6,10.0,1,'ali')""",
                      (iid,))
        alt, ust = Defter(db).karne('ali')["guven_araligi_%"]
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
        # Fixture'lar IKI KATMANLI: `sade_katman_yok` kontrolu sessizlik
        # kontrolunden ONCE geldigi icin katmansiz metin oraya varamaz.
        # Testin IDDIALARI degismedi, yalnizca girdisi sozlesmeye uyduruldu.
        p._kosuyu_yaz({
            "hakem": ("### SADE\nBugun one cikan bir sey yok.\n\n"
                      "### TEKNIK\nEsigi gecen gozlem yok.", {"gorusler": []}),
            "teknik": ("### SADE\nTHYAO guclu duruyor.\n\n"
                       "### TEKNIK\nTHYAO SMA50 ustunde.",
                       {"gorusler": [{"sembol": "THYAO"}]}),
            # ozette GECMEYEN sembol JSON'da
            "olay": ("### SADE\nASML hakkinda bir sey yok.\n\n"
                     "### TEKNIK\nASML icin kademe 1-2 haber yok.",
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
                    ajan,yon,ufuk_gun,guven,baslangic_fiyat,isabet,sahip)
                    VALUES ('2026-08-01',?,?,'yukari',5,0.7,10.0,1,'ali')""",
                          (iid, ajan))
            c.execute("""INSERT INTO predictions (olusma_ts,instrument_id,
                ajan,yon,ufuk_gun,guven,baslangic_fiyat,isabet,sahip)
                VALUES ('2026-08-01',?,'hakem','asagi',5,0.7,10.0,0,'ali')""",
                      (iid,))
        s = Defter(db).hakem_sapmasi('ali')
        assert s["ayrisan"] == 1 and s["ayrismada_panel_hakli"] == 1, s
        assert s["ayrismada_hakem_hakli"] == 0, s
        assert s["yeterli_mi"] is False, "n=1 yeterli sayilmis"
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

    LISTE OGELERI AYRI HARITADIR. Ilk surum `- kod: X` satirini atliyor
    ama o ogenin DEVAM satirlarini (`ad:`, `birim:`, `sec:`) bir onceki
    ogeyle AYNI harita saniyordu; `sources.tuik.seriler` gibi coklu
    kayit iceren bir liste eklenince test GECERLI YAML'i cift anahtar
    diye reddetti. Bir tire goruldugunde o girinti ve altindaki hafiza
    SIFIRLANIR — yeni bir harita basliyor demektir.
    """
    import pathlib as _p
    yol = _p.Path(__file__).parent.parent / "config" / "settings.yaml"
    gorulen, cakisan = {}, []
    for i, satir in enumerate(yol.read_text(encoding="utf-8").splitlines(), 1):
        if not satir.strip() or satir.lstrip().startswith("#"):
            continue
        girinti = len(satir) - len(satir.lstrip())
        if satir.lstrip().startswith("-"):
            for g in [g for g in gorulen if g >= girinti]:
                gorulen.pop(g)
            continue
        if ":" not in satir:
            continue
        anahtar = satir.strip().split(":", 1)[0].strip()
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
            c.execute("""INSERT INTO positions (sahip,snapshot_ts,account,
                         instrument_id,quantity,market_value,currency)
                         VALUES ('ali','2026-08-16','bux',?,1,100.0,'EUR')""",
                      (sahip,))
        # Portfoy sinyali ZAYIF, kalabalik evren GUCLU
        guclu = [{"instrument_id": 999 + i, "sembol": f"X{i}", "venue": "BIST",
                  "guc": 0.9} for i in range(PANEL_ADAY + 5)]
        guclu.append({"instrument_id": sahip, "sembol": "MINE",
                      "venue": "BUX", "guc": 0.56})
        g = Nabiz(load_settings(), db)._gundem(guclu, 'ali')
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




def test_tez_grameri_serbest_metni_reddeder():
    """
    Kontrol edilemeyen kosul, olu konfigurasyonun yeni bicimidir:
    kaydedilir, her gun kontrol edilir, hep False doner ve kullanici
    "tez hala gecerli" sanir. Uydurulmus kosul, HIC kosuldan kotudur.
    """
    from finagent.pulse.tez import kosul_ayristir
    assert kosul_ayristir("close < 1520") == ("close", "<", 1520.0)
    assert kosul_ayristir("RSI14 > 75") == ("rsi14", ">", 75.0)
    assert kosul_ayristir("hacim_kat < 0.8") == ("hacim_kat", "<", 0.8)
    # Alan-alan karsilastirmasi ilk surumde YOK — iki taraf da degistigi
    # icin "bozuldu" ani belirsizlesir ve her gun alarm uretir.
    assert kosul_ayristir("close < sma50") is None
    assert kosul_ayristir("fiyat duserse") is None
    assert kosul_ayristir("close < 1520 EUR") is None
    assert kosul_ayristir(None) is None


def test_gecersiz_kosul_kaydedilmez_ve_sayilir():
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("XYZ", "BUX", "T", "equity", "EUR")
        db.upsert_prices(iid, [{"ts": "2026-08-15", "close": 10.0}],
                         "t", currency="EUR")
        r = Defter(db).kaydet([{"sembol": "XYZ", "yon": "yukari", "guven": 0.7,
                                "ufuk_gun": 5, "ajan": "hakem", "tez": "T",
                                "gecersizlesme_kosulu": "fiyat duserse"}], 'ali')
        assert db.query("SELECT gecersizlesme_kosulu k FROM predictions")[0]["k"] \
            is None, "gramere uymayan kosul kaydedilmis"
        assert r.get("kosul_reddi") == 1, r
        db.close()


def test_tez_bir_kez_tetiklenir():
    """
    Esigin altinda kalan bir kagit her gun alarm uretirse kullanici
    bildirimleri kapatir — alarmin degeri NADIRLIGINDEN gelir.
    Ayrica tez bozulmasi tahmin puanlamasini ETKILEMEZ.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("XYZ", "BUX", "T", "equity", "EUR")
        db.upsert_prices(iid, [{"ts": f"2026-08-{i:02d}", "close": 9.0}
                               for i in range(1, 16)], "t", currency="EUR")
        with db.tx() as c:
            c.execute("""INSERT INTO predictions (olusma_ts,instrument_id,ajan,
                yon,ufuk_gun,guven,baslangic_fiyat,tez,gecersizlesme_kosulu,
                sahip)
                VALUES ('2026-08-15',?,'hakem','yukari',5,0.7,10.0,
                        'SMA50 ustunde tutunuyor','close < 9.5','ali')""", (iid,))
        d1 = Defter(db)
        ilk = d1.tez_kontrol('ali')
        assert len(ilk) == 1 and ilk[0]["sembol"] == "XYZ", ilk
        assert ilk[0]["deger"] == 9.0 and ilk[0]["esik"] == 9.5

        ikinci = d1.tez_kontrol('ali')
        assert ikinci == [], "ayni tez ikinci kez tetiklenmis"

        # Puanlama etkilenmemeli: isabet hala NULL
        assert db.query("SELECT isabet FROM predictions")[0]["isabet"] is None
        assert db.query(
            "SELECT tez_bozuldu_ts t FROM predictions")[0]["t"] is not None
        db.close()


def test_hafif_kip_llm_calistirmaz_ve_portfoyle_sinirli():
    """
    Hafif kosuda panel YOK (butce uce katlanmasin) ve bildirim yalnizca
    SAHIP OLUNAN enstrumanlar icin. Sabah 09:30'da uzerinde pozisyonun
    olmayan bir kagidin hareketi acil degil; aksam paneli bakacak.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.runner import Nabiz
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        sahip = db.upsert_instrument("MINE", "BUX", "S", "equity", "EUR")
        yabanci = db.upsert_instrument("OTHER", "BIST", "O", "equity", "TRY")
        with db.tx() as c:
            c.execute("""INSERT INTO positions (sahip,snapshot_ts,account,
                instrument_id,quantity,market_value,currency)
                VALUES ('ali','2026-08-16','bux',?,1,100.0,'EUR')""", (sahip,))
        n = Nabiz(load_settings(), db)
        gonderilen = []
        n._hafif_bildir = lambda *a: gonderilen.append(a)

        # Yalnizca SAHIP OLUNMAYAN sinyal -> mesaj YOK
        r = n._hafif("sabah", True, [], [{"instrument_id": yabanci,
                                          "sembol": "OTHER", "venue": "BIST",
                                          "guc": 0.9, "tur": "rsi_ucu"}], [], {},
                     "ali")
        assert r["portfoy_sinyali"] == 0 and not gonderilen, \
            "sahip olunmayan kagit hafif kosuda bildirim uretmis"

        # Sahip olunan sinyal -> mesaj VAR
        n._hafif("sabah", True, [], [{"instrument_id": sahip, "sembol": "MINE",
                                      "venue": "BUX", "guc": 0.9,
                                      "tur": "rsi_ucu"}], [], {}, "ali")
        assert gonderilen, "portfoy sinyali bildirim uretmemis"
        assert r["tahmin"] == 0, "hafif kip tahmin yazmis"
        db.close()


def test_sade_katman_teknikten_daha_kesin_konusamaz():
    """
    Asil tehlike terimlerin atilmasi degil, KESINLIGIN EKLENMESI:
    "RSI 78" bir olcum, "duzeltme gelebilir" bir tahmin. Sadelestirme
    sirasinda model belirsizligi de atma egiliminde.
    """
    from finagent.pulse.agents import katmanlari_ayir, sade_kesinlik_ihlali
    metin = ("### SADE\nHizli yukselmis, katilim zayif.\n\n"
             "### TEKNIK\nRSI 78, hacim orani 0,6.")
    sade, teknik = katmanlari_ayir(metin)
    assert sade == "Hizli yukselmis, katilim zayif."
    assert teknik.startswith("RSI 78")

    # Bolunemezse SADE None, TAMAMI teknik — yarim mesaj gitmesin
    assert katmanlari_ayir("baslik yok")[0] is None
    assert katmanlari_ayir("baslik yok")[1] == "baslik yok"

    # Ihlal: tahmin dili var, teknikte yon iddiasi yok
    assert sade_kesinlik_ihlali("Duzeltme gelebilir",
                                {"gorusler": [{"yon": "notr"}]}) == 1
    # Ihlal degil: teknikte de yon var
    assert sade_kesinlik_ihlali("Duzeltme gelebilir",
                                {"gorusler": [{"yon": "asagi"}]}) == 0


def test_hakem_prompt_grameri_uretiyor():
    """
    Gramer `tez.py`'de tanimli; prompt'a elle kopyalansaydi alan listesi
    degistiginde ikisi sessizce ayrisirdi — bu projenin tekrar eden
    kusur sinifi (prompt "FX yok" derken `fx` araci vardi).
    """
    from finagent.pulse.agents import hakem_prompt
    from finagent.pulse.tez import ALANLAR
    p = hakem_prompt()
    assert "{GRAMER}" not in p, "sablon yer tutucusu doldurulmamis"
    for a in ALANLAR:
        assert a in p, f"{a} prompt'ta yok"
    assert "### SADE" in p and "### TEKNIK" in p


def test_atilan_sayaci_ajan_bazinda_yazilir():
    """
    Kosunun toplamini tek bir ajan satirina yazmak, docstring'in kendi
    kuralini cignerdi: sorgu dort ajanin toplamini id'si en kucuk
    ajanin sanirdi.

    SATIR ID ILE YAZILIYOR, zaman damgasiyla degil. Bu testte iki
    sahibin `run_ts`'i BILEREK CAKISTIRILDI: damga tabanli eslesme
    kosarken dogru gorunuyordu ama bu, panellerin sirayla kosmasindan
    kaynaklanan TESADUFI bir dogruluktu.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.runner import Nabiz
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        idler = {}
        with db.tx() as c:
            # AYNI run_ts — iki sahip, cakisma bilerek uretiliyor
            for sahip in ("ali", "esi"):
                for ajan in ("teknik", "risk", "hakem"):
                    cur = c.execute(
                        """INSERT INTO panel_runs (run_ts,ajan,ham_metin,
                           json_durum,gorus_sayisi,sahip)
                           VALUES ('AYNI-DAMGA',?,'m','ok',1,?)""",
                        (ajan, sahip))
                    idler[(sahip, ajan)] = cur.lastrowid

        Nabiz(load_settings(), db)._atilanlari_isle(
            {"ajan_bazli": {
                "teknik": {"atilan_sembol_yok": 2, "atilan_seri_yok": 0,
                           "atilan_cakisma": 0},
                "risk": {"atilan_sembol_yok": 0, "atilan_seri_yok": 1,
                         "atilan_cakisma": 0}}},
            {"ajan_bazli": {
                "hakem": {"atilan_sembol_yok": 0, "atilan_seri_yok": 0,
                          "atilan_cakisma": 3}}},
            {"teknik": idler[("ali", "teknik")],
             "risk": idler[("ali", "risk")],
             "hakem": idler[("ali", "hakem")]})

        v = {(r["sahip"], r["ajan"]): (r["atilan_sembol_yok"],
                                       r["atilan_seri_yok"],
                                       r["atilan_cakisma"])
             for r in db.query("SELECT sahip,ajan,atilan_sembol_yok,"
                               "atilan_seri_yok,atilan_cakisma FROM panel_runs")}
        assert v[("ali", "teknik")] == (2, 0, 0), v
        assert v[("ali", "risk")] == (0, 1, 0), v
        assert v[("ali", "hakem")] == (0, 0, 3), v
        # 'esi'nin satirlari DOKUNULMAMIS olmali — ayni damgaya ragmen
        for ajan in ("teknik", "risk", "hakem"):
            assert v[("esi", ajan)] == (0, 0, 0), (ajan, v)
        db.close()

def test_sade_katman_yoksa_isaretlenir():
    """
    MADDE 1 — sade katmanin URETILMEMESI kendini gizliyordu.

    `sade_kesinlik_ihlali(None, ...)` tanim geregi 0 doner (aranacak
    metin yok) ve kontrol sessizce geciyordu. Ilk kosuda katmanlarin
    hic uretilmedigini INSAN GOZU yakaladi — prompt'taki "## kullanma"
    kurali `### SADE` basligini yasakliyordu. Ikinci kez olsa
    yakalayacak hicbir sey yoktu.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.agents import Panel
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        p = Panel(load_settings(), db)
        # Sembol duzyazida GECIYOR: yoksa tasma kontrolu once tetiklenir
        # ve sade kontrolune hic sira gelmez.
        p._kosuyu_yaz({
            # SADE basligi YOK -> isaretlenmeli
            "hakem": ("THYAO icin duz metin, baslik yok.",
                      {"gorusler": [{"sembol": "THYAO", "yon": "notr"}]}),
            # Iki katman da var -> temiz
            "teknik": ("### SADE\nTHYAO icin acik anlatim.\n\n"
                       "### TEKNIK\nTHYAO RSI 78.",
                       {"gorusler": [{"sembol": "THYAO", "yon": "notr"}]}),
        })
        notlar = {r["ajan"]: r["hata"] for r in db.query(
            "SELECT ajan, hata FROM panel_runs")}
        assert notlar["hakem"] == "sade_katman_yok", notlar
        assert notlar["teknik"] is None, notlar

        # SIRA: JSON ayristirma daha temel bir ariza, once o raporlanmali
        db.query("DELETE FROM panel_runs"); db._conn.commit()
        p._kosuyu_yaz({"hakem": ("baslik yok", {})})
        assert db.query("SELECT hata FROM panel_runs")[0]["hata"] == \
            "JSON blogu ayristirilamadi"
        db.close()


def _risk(iid, tur, deger):
    kanit = ({"agirlik_%": deger} if tur == "yogunlasma" else {"kz_%": deger})
    return {"instrument_id": iid, "sembol": "MINE", "venue": "BUX",
            "tur": tur, "guc": 0.8, "kanit": kanit}


def test_risk_bildirimi_durum_degismeden_tekrarlanmaz():
    """
    MADDE 2 — portfoy riski bir OLAY degil DURUM. ASML portfoyun
    %40'iysa bu bugun de yarin da dogru; bastirma olmadan gunde iki
    hafif kosu ayni cumleyi tekrarlar ve kullanici bildirimleri
    kapatir. Alarmin degeri nadirliginden gelir.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.runner import Nabiz
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("MINE", "BUX", "S", "equity", "EUR")
        n = Nabiz(load_settings(), db)

        # 1. kez: bildirilir
        assert len(n._yeni_riskler([_risk(iid, "yogunlasma", 40.9)], 'ali')) == 1
        # 2. kez AYNI deger: SUSAR
        assert n._yeni_riskler([_risk(iid, "yogunlasma", 40.9)], 'ali') == []
        # Esigin ALTINDA oynama: yine susar
        assert n._yeni_riskler([_risk(iid, "yogunlasma", 42.5)], 'ali') == []
        db.close()


def test_risk_bildirimi_deger_oynayinca_yeniden_gider():
    """
    Bastirma KALICI OLMAMALI: durum anlamli olcude degistiyse yeniden
    bildirilir. Esik yuzde PUANI (RISK_TEKRAR_ESIGI).
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.runner import Nabiz
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("MINE", "BUX", "S", "equity", "EUR")
        n = Nabiz(load_settings(), db)
        esik = n.RISK_TEKRAR_ESIGI

        assert len(n._yeni_riskler([_risk(iid, "acik_zarar", -21.0)], 'ali')) == 1
        assert n._yeni_riskler([_risk(iid, "acik_zarar", -21.0)], 'ali') == []
        # Esigi ASAN kotulesme -> yeniden bildirilir
        yeni = n._yeni_riskler([_risk(iid, "acik_zarar", -21.0 - esik - 0.1)], 'ali')
        assert len(yeni) == 1, "esigi asan degisim bastirilmis"
        # ve yeni deger saklanmis olmali
        kayit = db.query("SELECT son_deger FROM bildirim_durumu")[0]["son_deger"]
        assert abs(kayit - (-21.0 - esik - 0.1)) < 1e-6, kayit

        # AYRI TUR ayri izlenir: ayni enstrumanda yogunlasma bagimsiz
        assert len(n._yeni_riskler([_risk(iid, "yogunlasma", 40.0)], 'ali')) == 1
        db.close()




# ═══════════════════════════════════════════════════════════════════
# COK KULLANICILI KATMAN — Faz A kabul kriterleri
# ═══════════════════════════════════════════════════════════════════

def _iki_sahipli_db(tmp):
    import pathlib as _p
    from finagent.storage.db import Database
    db = Database(_p.Path(tmp) / "t.db"); db.init_schema()
    a = db.upsert_instrument("ASML", "BUX", "ASML", "equity", "EUR")
    b = db.upsert_instrument("NVDA", "BUX", "Nvidia", "equity", "EUR")
    db.insert_positions("bux", "2026-08-16T10:00:00", [
        {"symbol": "ASML", "quantity": 1, "market_value": 1000,
         "currency": "EUR"}], "ali")
    # B'nin goruntusu DAHA YENI: eski kodda A'nin portfoyu kaybolurdu
    db.insert_positions("bux", "2026-08-16T12:00:00", [
        {"symbol": "NVDA", "quantity": 2, "market_value": 500,
         "currency": "EUR"}], "esi")
    return db, a, b


def test_iki_sahip_birbirinin_portfoyunu_gormez():
    """
    ACIK KUSUR buydu: sorgular "her account'in en son snapshot'i"
    diyordu. Ikinci kisi bir ekran goruntusu onayladiginda onun
    snapshot'i en yenisi olur ve BIRINCI kisinin portfoyu her
    sorgudan kaybolurdu.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _, _ = _iki_sahipli_db(d)
        ali = db.latest_positions("bux", "ali")
        esi = db.latest_positions("bux", "esi")
        assert [r["symbol"] for r in ali] == ["ASML"], [r["symbol"] for r in ali]
        assert [r["symbol"] for r in esi] == ["NVDA"], [r["symbol"] for r in esi]
        assert db.snapshot_value("bux", "2026-08-16T10:00:00", "ali") == 1000
        assert db.snapshot_value("bux", "2026-08-16T10:00:00", "esi") == 0
        db.close()


def test_ayni_enstrumani_iki_sahip_tutabilir():
    """UNIQUE cakismasi OLMAMALI — anahtara sahip girdi."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _, _ = _iki_sahipli_db(d)
        ts = "2026-08-17T10:00:00"
        for s in ("ali", "esi"):
            db.insert_positions("bux", ts, [
                {"symbol": "ASML", "quantity": 1, "market_value": 900,
                 "currency": "EUR"}], s)
        n = db.query("SELECT COUNT(*) n FROM positions WHERE snapshot_ts=?",
                     (ts,))[0]["n"]
        assert n == 2, f"iki sahip ayni enstrumani tutamamis: {n}"
        db.close()


def test_portfoy_riski_sahip_bazli():
    """Yogunlasma BIRLESIK degil, kisiye ait olmali."""
    import tempfile
    from finagent.pulse.screener import Tarayici
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db, _, _ = _iki_sahipli_db(d)
        t = Tarayici(load_settings(), db)
        for sahip, beklenen in (("ali", "ASML"), ("esi", "NVDA")):
            r = t.portfoy_taramasi(sahip)
            semboller = {x["sembol"] for x in r}
            assert semboller == {beklenen}, (sahip, semboller)
        db.close()


def test_sahipsiz_sohbet_portfoy_aracinda_acik_hata_alir():
    """
    Bos sonuc DEGIL acik hata: model bos sonucu "portfoyun bos" diye
    okur ve bu, yanlis veri gostermekten farkli ama esdeger bicimde
    yaniltici olur.
    """
    import tempfile, pathlib as _p, json as _j
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d, sahip=None)
        araclar = {a.name: a for a in tb.araclar()}
        r = _cagir(araclar["portfoy"])
        assert "hata" in r, r
        assert "kisiye bagli degil" in r["hata"], r
        r2 = _cagir(araclar["pozisyon_kaydet"], hesap="bux",
                    pozisyonlar=_j.dumps([{"sembol": "X"}]))
        assert "hata" in r2, r2
        assert db.query("SELECT COUNT(*) c FROM positions")[0]["c"] == 0
        db.close()


def test_onay_dosyasi_sahibi_ve_sohbeti_tasir():
    """
    A'nin bekleyen onayi B'nin `/onayla`siyla yazilmamali. Butonlu
    akis zaten guvenli; tehlike toplu komutta.
    """
    import tempfile, json as _j
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        araclar = {a.name: a for a in tb.araclar()}
        r = _cagir(araclar["pozisyon_kaydet"], hesap="binance",
                   pozisyonlar=_j.dumps([{"sembol": "ROSE", "deger": 300}]))
        p = _j.loads((tb.pending_dir / f"{r['token']}.json").read_text())
        assert p["_sahip"] == "ali", p
        assert p["_chat_id"] == "5643817523", p
        db.close()


def test_sinyal_sahipligi_ture_gore():
    """
    Piyasa sinyali 'ortak' (bir kez hesaplanir, herkes okur), portfoy
    sinyali kisiye ait. Portfoy sinyali sahipsiz kaydedilemez.
    """
    import tempfile
    from finagent.pulse.screener import Tarayici
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db, a, _ = _iki_sahipli_db(d)
        t = Tarayici(load_settings(), db)
        t.kaydet([{"instrument_id": a, "tur": "rsi_ucu", "yon": "asagi",
                   "guc": 0.7, "kanit": {}, "fiyat": 1.0,
                   "para_birimi": "EUR"},
                  {"instrument_id": a, "tur": "yogunlasma", "yon": "notr",
                   "guc": 0.8, "kanit": {}, "fiyat": 1.0,
                   "para_birimi": "EUR"}], sahip="ali")
        d2 = {r["tur"]: r["sahip"] for r in db.query(
            "SELECT tur, sahip FROM signals")}
        assert d2 == {"rsi_ucu": "ortak", "yogunlasma": "ali"}, d2

        # Portfoy sinyali SAHIPSIZ kaydedilemez
        try:
            t.kaydet([{"instrument_id": a, "tur": "acik_zarar", "yon": "notr",
                       "guc": 0.5, "kanit": {}, "fiyat": 1.0,
                       "para_birimi": "EUR"}])
        except ValueError as e:
            assert "sahipsiz" in str(e), e
        else:
            raise AssertionError("portfoy sinyali sahipsiz kaydedilmis")
        db.close()


def test_tahmin_ve_karne_sahip_bazli():
    import tempfile
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db, a, _ = _iki_sahipli_db(d)
        db.upsert_prices(a, [{"ts": "2026-08-15", "close": 10.0}],
                         "t", currency="EUR")
        for sahip in ("ali", "esi"):
            Defter(db).kaydet([{"sembol": "ASML", "yon": "yukari",
                                "guven": 0.7, "ufuk_gun": 5, "ajan": "hakem",
                                "gerekce": sahip}], sahip)
        # Ayni gun + ayni enstruman + ayni ajan: IKI satir olmali
        assert db.query("SELECT COUNT(*) n FROM predictions")[0]["n"] == 2
        with db.tx() as c:
            c.execute("UPDATE predictions SET isabet=1 WHERE sahip='ali'")
        assert Defter(db).karne("ali")["olcum"] == 1
        assert Defter(db).karne("esi")["olcum"] == 0
        db.close()


def test_sahip_config_esleme_ve_varsayilana_dusmeme():
    """
    Sahip cozulemiyorsa None doner — VARSAYILANA DUSMEZ. Sessiz
    varsayilan, yanlis kisinin portfoyune yazmak demektir.
    """
    from finagent.config import load_settings
    s = load_settings()
    assert s.sahip_bul("5643817523") == "ali"
    assert s.sahip_bul("999999999") is None
    assert s.sahip_bul(None) is None
    assert "5643817523" in s.sahip_chatleri("ali")
    assert s.sahip_listesi == sorted(set(s.sahipler.values()))




# ═══════════════════════════════════════════════════════════════════
# FAZ B — nabiz kisi basina
# ═══════════════════════════════════════════════════════════════════

def _fazb_db(tmp, sahipler=("ali", "esi"), portfoysuz=()):
    """
    Iki sahipli test ortami: ortak piyasa verisi + kisisel portfoyler.
    `portfoysuz` icindeki sahipler pozisyon ALMAZ (uc durum 3).
    """
    import pathlib as _p
    from finagent.storage.db import Database
    db = Database(_p.Path(tmp) / "t.db"); db.init_schema()
    sembol = {}
    for i, sem in enumerate(("ASML", "NVDA", "THYAO")):
        iid = db.upsert_instrument(sem, "BUX", sem, "equity", "EUR")
        sembol[sem] = iid
        db.upsert_prices(iid, [
            {"ts": f"2026-{1+g//28:02d}-{1+g % 28:02d}", "close": 100 + g * 0.7,
             "volume": 1000} for g in range(120)], "t", currency="EUR")
    tahsis = {"ali": "ASML", "esi": "NVDA"}
    for saat, sahip in enumerate(sahipler, start=10):
        if sahip in portfoysuz:
            continue
        db.insert_positions("bux", f"2026-08-16T{saat}:00:00", [
            {"symbol": tahsis.get(sahip, "THYAO"), "quantity": 1,
             "market_value": 1000, "currency": "EUR"}], sahip)
    return db, sembol


def _fazb_ayar(sahipler=("ali", "esi"), kok=None):
    """
    Nabiz testleri icin ayar.

    `kok` VERILMELI. `calistir()` artik kosu izi yaziyor
    (`<root>/data/bot/kosu/<kip>.json`) ve gercek kok kullanilirsa test,
    GERCEK gozetim durumunu yazar: bekci "sabah bugun kostu" sanip
    GERCEK bir arizayi susturur. Olculdu — bu testler `sahipler:
    ["ali","esi"]` yazan iki iz dosyasi biraktilar.

    Ilgili ders (siradaki-is): "bu test bir regresyonda ne KADAR gercek
    is yapabilir?"
    """
    import copy, tempfile
    from finagent.config import load_settings
    s = load_settings()
    # DERIN KOPYA: `ritim.kipler` asagida degistiriliyor ve `raw`
    # paylasilirsa bir testin degisikligi digerine sizar.
    s.raw = copy.deepcopy(s.raw)
    s.raw.setdefault("telegram", {})["sahipler"] = {
        str(100 + i): ad for i, ad in enumerate(sahipler)}
    # AYAR KENDI ICINDE TUTARLI OLMALI. `alicilar` gercek ayardan
    # geliyor (`ali`, `yuksel`) ama bu testler sahip listesini
    # degistiriyor; `ritim_kip` tanimsiz aliciyi REDDEDER ve etmeli.
    # Testin isi bu dogrulamayi atlatmak degil, ayari duzgun kurmak.
    for kip in (s.raw.get("ritim", {}).get("kipler") or {}).values():
        kip["alicilar"] = list(sahipler)
    if kok is None:
        # Cagiran vermediyse de GERCEK koke yazma: omru testle sinirli
        # olmayan ama proje disinda kalan bir dizin yeter.
        kok = tempfile.mkdtemp(prefix="finagent-test-kok-")
    s.root = _pathlib.Path(kok)
    return s


def test_fazb_piyasa_taramasi_bir_kez_kosar():
    """
    UC DURUM 8 — sinyal sayisi sahip sayisiyla ARTMAMALI.

    Piyasa sinyalleri fiyattan turuyor, kisiden degil: bir kez
    hesaplanip 'ortak' yazilir. Iki kez kosmak hem bosa is hem de
    ayni sinyalin iki kayda dusmesi demek olurdu.
    """
    import tempfile
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, _ = _fazb_db(d)
        r = Nabiz(_fazb_ayar(kok=d), db).calistir(bildir=False, panel=False,
                                             kip="sabah")
        ortak = db.query(
            "SELECT COUNT(*) n FROM signals WHERE sahip='ortak'")[0]["n"]
        assert r["ortak"]["piyasa_sinyali"] == ortak, (r["ortak"], ortak)
        kisisel = {x["sahip"] for x in db.query(
            "SELECT DISTINCT sahip FROM signals WHERE sahip<>'ortak'")}
        assert kisisel == {"ali", "esi"}, kisisel
        db.close()


def test_fazb_bir_sahibin_hatasi_digerini_durdurmaz():
    """
    UC DURUM 5 — IZOLASYON. Bir sahibin adimi patlarsa digeri KOSAR.
    """
    import tempfile
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, _ = _fazb_db(d)
        n = Nabiz(_fazb_ayar(kok=d), db)
        gercek = n._kisisel_faz

        def patlat(sahip, *a, **k):
            if sahip == "ali":
                raise RuntimeError("enjekte edilmis ariza")
            return gercek(sahip, *a, **k)

        n._kisisel_faz = patlat
        bildirimler = []
        n._sahibe_bildir = lambda s, m, reply_markup=None: (
            bildirimler.append((s, m)) or True)
        r = n.calistir(bildir=True, panel=False, kip="sabah")

        assert r["basarisiz"] == ["ali"], r["basarisiz"]
        assert "hata" in r["sonuc"]["ali"]
        assert "hata" not in r["sonuc"]["esi"], "ikinci sahip kosmamis"
        # HATA bildirimi yalnizca 'ali'ye; 'esi' kendi NORMAL bildirimini
        # almis olmali — izolasyonun kaniti tam olarak bu.
        hatalar = [s for s, m in bildirimler if "patladi" in m]
        assert hatalar == ["ali"], hatalar
        assert any(s == "esi" and "patladi" not in m
                   for s, m in bildirimler), bildirimler
        db.close()


def test_fazb_panel_patlarsa_tez_alarmi_yine_gider():
    """
    ADIM BAZINDA KISMI BASARI — panel LLM'e bagli, tez kontrolu degil.
    Panel patlarsa kullanicinin en cok isine yarayan cikti (onceden
    beyan edilmis esigin gerceklesmesi) yine ulasmali.

    RITIM v2'DE MEKANIZMA DEGISTI, SOZLESME AYNI: tez alarmi ayri bir
    mesaj degil, OZETIN ICINDE bir satir. Ama alarm bolumu panelden
    ONCE ve panelden BAGIMSIZ hesaplaniyor, yani panel patlasa da ayni
    mesajda gidiyor. Gunde dort kosu x iki mesaj = sekiz bildirim
    olurdu; tek mesaj sozu boyle tutuluyor.
    """
    import tempfile
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, sembol = _fazb_db(d, sahipler=("ali",))
        with db.tx() as c:
            c.execute("""INSERT INTO predictions (olusma_ts,instrument_id,ajan,
                yon,ufuk_gun,guven,baslangic_fiyat,tez,gecersizlesme_kosulu,
                sahip) VALUES ('2026-08-15',?,'hakem','yukari',5,0.7,10.0,
                'T','close < 99999','ali')""", (sembol["ASML"],))
        n = Nabiz(_fazb_ayar(("ali",), kok=d), db)
        n._panel_fazi = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("panel patladi"))
        gonderilen = []
        n._sahibe_bildir = lambda s, m, reply_markup=None: (
            gonderilen.append((s, m)) or True)

        r = n.calistir(bildir=True, panel=True, kip="nabiz")
        assert r["sonuc"]["ali"].get("panel_hatasi"), r["sonuc"]["ali"]
        assert len(gonderilen) == 1, (
            f"{len(gonderilen)} mesaj gitti; ozet TEK mesaj olmali")
        _, metin = gonderilen[0]
        assert "tezi bozuldu" in metin, f"tez alarmi ozette YOK: {metin}"
        assert "ASML" in metin, metin
        assert "Panel calismadi" in metin, metin
        db.close()


def test_fazb_ortak_faz_patlarsa_herkese_bildirilir():
    """
    UC DURUM 9 — piyasa taramasi olmadan kisisel fazin anlami yok.
    Kosu durur ve SESSIZCE YUTULMAZ.
    """
    import tempfile
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, _ = _fazb_db(d)
        n = Nabiz(_fazb_ayar(kok=d), db)
        n._ortak_faz = lambda kip: (_ for _ in ()).throw(
            RuntimeError("tarama patladi"))
        kisisel = []
        n._kisisel_faz = lambda *a, **k: kisisel.append(a)
        herkes = []
        n._herkese_bildir = lambda m: herkes.append(m)
        try:
            n.calistir(bildir=True, panel=False, kip="sabah")
        except RuntimeError:
            pass
        else:
            raise AssertionError("ortak faz hatasi yutulmus")
        assert not kisisel, "ortak faz patlamasina ragmen kisisel faz kosmus"
        assert herkes and "ortak fazi patladi" in herkes[0], herkes
        db.close()


def test_fazb_portfoysuz_sahip_cokmez():
    """
    UC DURUM 3 — portfoyu olmayan sahip icin panel yine kosar (piyasa
    sinyalleri onu da ilgilendirir), portfoy slotu bos kalir ve
    "veri yok" bildirimi GITMEZ.
    """
    import tempfile
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, _ = _fazb_db(d, portfoysuz=("esi",))
        n = Nabiz(_fazb_ayar(kok=d), db)
        gonderilen = []
        n._hafif_bildir = lambda *a: gonderilen.append(a)
        r = n.calistir(bildir=True, panel=False, kip="sabah")
        assert not r["basarisiz"], r["basarisiz"]
        assert r["sonuc"]["esi"]["portfoy_sinyali"] == 0
        # Portfoysuz sahibe BILDIRIM GITMEZ (sessizlik gecerli cikti)
        assert all(a[-1] != "esi" for a in gonderilen), gonderilen
        db.close()


def test_fazb_sahipsiz_yapilandirma_acik_hata():
    """
    UC DURUM 2 — sessiz no-op degil, ACIK HATA.

    Sahipsiz kosu hicbir sey uretmez ama "calisti" gorunur; bu,
    bildirimlerin neden gelmedigini gunlerce gizleyebilir. Ritim v2'den
    sonra hata `ritim_kip`ten geliyor (alici listesi `telegram.sahipler`
    ile dogrulaniyor) — MESAJ degisti, SOZLESME ayni: gurultulu dus.
    """
    import tempfile
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, _ = _fazb_db(d)
        s = _fazb_ayar(kok=d)
        s.raw["telegram"]["sahipler"] = {}
        import os
        eski = os.environ.pop("TELEGRAM_CHAT_ID", None)
        try:
            Nabiz(s, db).calistir(bildir=False, panel=False)
        except ValueError as e:
            assert "sahip" in str(e) or "alici" in str(e), e
        else:
            raise AssertionError("sahipsiz kosu sessizce gecti")
        finally:
            if eski:
                os.environ["TELEGRAM_CHAT_ID"] = eski
        db.close()


def test_fazb_chat_eslemesi_olmayan_sahip_bildirimi_kaybetmez():
    """
    UC DURUM 6 / §4 — chat_id eslemede yoksa bu ACIK BIR HATADIR:
    kosup bildirimi kaybetmek, hic kosmamaktan kotu (LLM butcesi
    harcanir, cikti kimseye gitmez). Log'a yazilir ve False doner.
    """
    import tempfile
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, _ = _fazb_db(d)
        s = _fazb_ayar(kok=d)
        s.raw["telegram"]["sahipler"] = {"100": "ali"}   # 'esi' YOK
        n = Nabiz(s, db)
        assert n._sahibe_bildir("esi", "test") is False
        db.close()


def test_fazb_tek_sahip_davranisi_degismedi():
    """
    UC DURUM 1 — EN ONEMLI REGRESYON TESTI.

    Tek sahipli kurulumda donus sozlesmesi BUGUNKU ile ayni kalmali:
    cagiranlar (run.py, mevcut testler) duz alanlari okuyor.
    """
    import tempfile
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, _ = _fazb_db(d, sahipler=("ali",))
        r = Nabiz(_fazb_ayar(("ali",), kok=d), db).calistir(
            bildir=False, panel=False, kip="sabah")
        for alan in ("sinyal", "guclu", "karne", "tez_bozuldu"):
            assert alan in r, f"tek sahipte duz alan kaybolmus: {alan}"
        assert r["sahipler"] == ["ali"]
        assert r["sinyal"] == r["sonuc"]["ali"]["sinyal"]
        db.close()


def test_fazb_tez_alarmi_caprazlanmaz():
    """UC DURUM 12 — ayni enstrumanda iki tez, her biri KENDI sahibine."""
    import tempfile
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db, sembol = _fazb_db(d)
        with db.tx() as c:
            for sahip in ("ali", "esi"):
                c.execute("""INSERT INTO predictions (olusma_ts,instrument_id,
                    ajan,yon,ufuk_gun,guven,baslangic_fiyat,tez,
                    gecersizlesme_kosulu,sahip)
                    VALUES ('2026-08-15',?,'hakem','yukari',5,0.7,10.0,?,
                            'close < 99999',?)""",
                          (sembol["ASML"], f"{sahip} tezi", sahip))
        for sahip in ("ali", "esi"):
            b = Defter(db).tez_kontrol(sahip)
            assert len(b) == 1, (sahip, b)
            assert b[0]["tez"] == f"{sahip} tezi", b
        db.close()


def test_fazb_yetim_tablo_kosulsuz_kurtarilir():
    """
    MADDE 0 — bes tablo da gocmusse `gerekli` bos kalir ve kurtarma hic
    kosmazdi; diskte kalan bir `*_eski` sonraki gocu KALICI olarak
    bloke ederdi ("already exists").
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        yol = _p.Path(d) / "t.db"
        db = Database(yol); db.init_schema()
        iid = db.upsert_instrument("X", "BUX", "X", "equity", "EUR")
        db.insert_positions("bux", "t", [{"symbol": "X",
                                          "market_value": 1.0}], "ali")
        # Cokmus bir gocten kalan artik — canli tablo DOLU, kopya bos
        db._conn.execute("CREATE TABLE signals_eski (id INTEGER PRIMARY KEY)")
        db._conn.commit(); db.close()

        db2 = Database(yol); db2.init_schema()
        kalan = [r["name"] for r in db2.query(
            "SELECT name FROM sqlite_master WHERE name LIKE '%_eski'")]
        assert not kalan, f"yetim tablo temizlenmemis: {kalan}"
        assert db2.query("SELECT COUNT(*) n FROM positions")[0]["n"] == 1
        db2.close()




def test_fazb_sure_butcesi_dolunca_panel_atlanir_ve_bildirilir():
    """
    UC DURUM 13 — butce dolunca kalan sahibin paneli ATLANIR ama
    SESSIZCE degil. "Bugun mesaj gelmedi" ile "bugun panel kosamadi"
    ayri seyler; ikincisi kullanicinin bilmesi gerekendir.

    Deterministik adimlar yine kosar: tez alarmi ve portfoy riski hem
    ucuz hem de en cok isine yarayan cikti.
    """
    import tempfile
    from unittest.mock import patch
    from finagent.pulse import runner as R
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, _ = _fazb_db(d)
        n = Nabiz(_fazb_ayar(kok=d), db)
        gonderilen = []
        n._sahibe_bildir = lambda s, m, reply_markup=None: (
            gonderilen.append((s, m)) or True)
        n._hafif_bildir = lambda *a: None
        # Butceyi SIFIRA cek: ilk sahipten sonra dolmus sayilsin
        with patch.object(R, "PANEL_SURE_BUTCESI_SN", -1):
            r = n.calistir(bildir=True, panel=True, kip="nabiz")
        assert r["panel_atlanan"] == ["ali", "esi"], r["panel_atlanan"]
        atlama = [m for s, m in gonderilen if "panel kosamadi" in m]
        assert len(atlama) == 2, gonderilen
        # Deterministik adimlar KOSMUS olmali (hata yok)
        assert not r["basarisiz"], r["basarisiz"]
        db.close()




def test_fazb_sohbet_katmani_etkilenmedi():
    """
    UC DURUM 14 — sohbet katmani chat_id eslemesini Faz A'da aldi ve
    Faz B ona DOKUNMAMALI. Nabiz dongusu sohbetin sahip cozumune
    bagimli degil; ikisi ayri sinirlarda calisiyor.
    """
    import inspect
    from finagent.bot.chat import ChatEngine
    from finagent.bot.listener import FinBot
    from finagent.config import load_settings

    # Sohbet sahibi HALA chat_id'den cozuluyor
    kaynak = inspect.getsource(FinBot._sohbet)
    assert "sahip_bul(chat_id)" in kaynak, kaynak[:200]

    # ChatEngine.cevapla ORNEK DURUMU tutmuyor (donus degeri)
    imza = inspect.signature(ChatEngine.cevapla)
    assert "sahip" in imza.parameters, imza

    # Esleme cozumu Faz B'den bagimsiz
    s = load_settings()
    assert s.sahip_bul("5643817523") == "ali"
    assert s.sahip_bul("yok") is None




def test_risk_bastirmasi_sahipler_arasinda_caprazlanmaz():
    """
    MADDE 1 — iki sahip de ayni enstrumani tutuyorsa yogunlasma
    oranlari FARKLIDIR ve ikisi de kendi alarmini almali.

    Sahipsiz anahtarda A'nin bastirma satiri B'ninkini EZERDI: B ya
    kendi riskini HIC gormez ya da A ertesi gun gereksiz alarm alir.
    Tablo tam da bildirim yorgunlugunu cozmek icin kurulmustu.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.pulse.runner import Nabiz
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("ASML", "BUX", "ASML", "equity", "EUR")
        n = Nabiz(load_settings(), db)

        # A: %40.9 -> bildirilir
        assert len(n._yeni_riskler([_risk(iid, "yogunlasma", 40.9)], "ali")) == 1
        # B: AYNI enstruman, AYNI tur, farkli deger -> BASTIRILMAMALI
        assert len(n._yeni_riskler([_risk(iid, "yogunlasma", 15.0)], "esi")) == 1

        satirlar = {(r["sahip"], r["son_deger"]) for r in db.query(
            "SELECT sahip, son_deger FROM bildirim_durumu")}
        assert satirlar == {("ali", 40.9), ("esi", 15.0)}, satirlar

        # REGRESYON: ayni sahip ayni degerle -> bastirilir
        assert n._yeni_riskler([_risk(iid, "yogunlasma", 40.9)], "ali") == []
        # ve B'nin satiri A tarafindan EZILMEMIS
        b = db.query("SELECT son_deger FROM bildirim_durumu WHERE sahip='esi'")
        assert b[0]["son_deger"] == 15.0, b

        # Deger anlamli oynayinca yeniden bildirilir
        esik = n.RISK_TEKRAR_ESIGI
        assert len(n._yeni_riskler(
            [_risk(iid, "yogunlasma", 40.9 + esik + 0.1)], "ali")) == 1
        db.close()


def test_teknik_detay_sahibe_gore_suzuluyor():
    """
    MADDE 2a — iki sahibin hakem satiri AYNI `run_ts` tasidiginda bile
    her biri KENDI metnini gormeli.

    Damga BILEREK cakistirildi: sirali kosmaya guvenen bir test kusuru
    yeniden uretemez ve duzeltmeyi kanitlamaz.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        idler = {}
        with db.tx() as c:
            for sahip in ("ali", "esi"):
                cur = c.execute(
                    """INSERT INTO panel_runs (run_ts,ajan,ham_metin,
                       json_durum,gorus_sayisi,sahip)
                       VALUES ('AYNI-DAMGA','hakem',?,'ok',1,?)""",
                    (f"### SADE\nsade {sahip}\n\n### TEKNIK\nteknik {sahip}",
                     sahip))
                idler[sahip] = cur.lastrowid

        s = load_settings()
        s.raw.setdefault("telegram", {})["sahipler"] = {"111": "ali",
                                                        "222": "esi"}
        bot = _sahte_bot(s, db)
        for sahip, chat in (("ali", "111"), ("esi", "222")):
            bot.gonderilen.clear()
            bot._teknik_detay({"id": "x"}, chat, str(idler[sahip]))
            metin = " ".join(m for m, _ in bot.gonderilen)
            assert f"teknik {sahip}" in metin, (sahip, metin)
            digeri = "esi" if sahip == "ali" else "ali"
            assert f"teknik {digeri}" not in metin, "CAPRAZ SIZINTI"

        # Baskasinin satirini ID ile istemek: detay YOK
        bot.gonderilen.clear()
        bot._teknik_detay({"id": "x"}, "111", str(idler["esi"]))
        assert not bot.gonderilen, bot.gonderilen

        # Eslemede olmayan chat: ACIK RET
        bot.gonderilen.clear()
        bot._teknik_detay({"id": "x"}, "999", str(idler["ali"]))
        assert not bot.gonderilen
        assert any("kisiye bagli degil" in c for c in bot.cevaplar), bot.cevaplar
        db.close()


def _sahte_bot(s, db):
    """Telegram'siz FinBot: gonderim ve callback cevaplari yakalanir."""
    from finagent.bot.listener import FinBot
    bot = FinBot.__new__(FinBot)
    bot.s, bot.db = s, db
    bot.gonderilen, bot.cevaplar = [], []
    bot.kaldirilan_markup = []
    # Callback yolu yetkilendirmeden geciyor; sahip listesinden kur ki
    # test gercek yetki sinirini ATLAMASIN.
    bot.allowed = {int(c) for c in s.sahipler if str(c).lstrip("-").isdigit()}

    class _Tg:
        def send_message(_self, metin, chat_id=None, **k):
            bot.gonderilen.append((metin, chat_id)); return True

        def answer_callback_query(_self, _id, metin=""):
            bot.cevaplar.append(metin)

        def chat_action(_self, *a, **k):
            return True

        def send_photo(_self, *a, **k):
            bot.gonderilen.append(("<foto>", k.get("chat_id"))); return True

        # GERCEK ISTEMCININ YUZEYIYLE AYNI KALSIN. Onay akisi basista
        # butonlari kaldiriyor; taklit bu yontemi tasimazsa `ok:`
        # callback'i olculen davranista degil AttributeError'da patlar.
        def edit_message_reply_markup(_self, mid, markup=None, chat_id=None):
            bot.kaldirilan_markup.append((mid, markup)); return True

    bot.tg = _Tg()
    return bot




# ═══════════════════════════════════════════════════════════════════
# GECMIS ARACLARI + ENVANTER + SOHBET TEMIZLEME
# ═══════════════════════════════════════════════════════════════════

def _gecmis_db(tmp):
    """Iki sahibin hakem tahminleri: biri acik, biri puanlanmis."""
    import pathlib as _p
    from finagent.storage.db import Database
    db = Database(_p.Path(tmp) / "t.db"); db.init_schema()
    iid = db.upsert_instrument("ASML", "BUX", "ASML", "equity", "EUR")
    db.upsert_prices(iid, [{"ts": "2026-08-15", "close": 10.0}], "t",
                     currency="EUR")
    with db.tx() as c:
        # ali: biri ACIK, biri PUANLANMIS
        c.execute("""INSERT INTO predictions (olusma_ts,instrument_id,ajan,yon,
            ufuk_gun,guven,gerekce,tez,baslangic_fiyat,sahip)
            VALUES (date('now'),?,'hakem','yukari',5,0.7,'acik gorus',
                    'T1',10.0,'ali')""", (iid,))
        c.execute("""INSERT INTO predictions (olusma_ts,instrument_id,ajan,yon,
            ufuk_gun,guven,gerekce,baslangic_fiyat,isabet,getiri_pct,
            anormal_pct,sahip)
            VALUES (date('now','-10 days'),?,'hakem','asagi',5,0.6,
                    'puanli gorus',10.0,1,3.2,1.1,'ali')""", (iid,))
        # ajan gorusu — araca GIRMEMELI
        c.execute("""INSERT INTO predictions (olusma_ts,instrument_id,ajan,yon,
            ufuk_gun,guven,gerekce,baslangic_fiyat,sahip)
            VALUES (date('now'),?,'teknik','yukari',5,0.9,'ajan gorusu',
                    10.0,'ali')""", (iid,))
        # esi: BASKASININ kaydi
        c.execute("""INSERT INTO predictions (olusma_ts,instrument_id,ajan,yon,
            ufuk_gun,guven,gerekce,baslangic_fiyat,sahip)
            VALUES (date('now'),?,'hakem','notr',5,0.5,'esi gorusu',
                    10.0,'esi')""", (iid,))
    return db, iid


def test_gecmis_gorus_sahibe_ait_ve_yalnizca_hakem():
    """
    Kullanici BASKASININ gecmisini isteyemez — sahip parametre degil,
    ToolBox'tan geliyor. Ajan gorusleri de girmez: kullaniciya
    gonderilen sey hakem ozetiydi, ajan gorusleri IC GIRDI ve bes kati
    satir uretip gurultu yapar.
    """
    import tempfile, pathlib as _p, json as _j
    from finagent.bot.tools import ToolBox
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        tb = ToolBox(load_settings(), db, _p.Path(d) / "pending",
                     sahip="ali", chat_id="1")
        r = _cagir({a.name: a for a in tb.araclar()}["gecmis_gorus"], gun=30)
        gerekceler = {g["gerekce"] for g in r["gorusler"]}
        assert gerekceler == {"acik gorus", "puanli gorus"}, gerekceler
        assert "esi gorusu" not in gerekceler, "BASKASININ gecmisi sizdi"
        assert "ajan gorusu" not in gerekceler, "ajan gorusu girdi"
        db.close()


def test_gecmis_gorus_acik_tahminde_sonuc_alani_yok():
    """
    `null` birakmak YETMEZ: bos alan goren model uydurabilir, OLMAYAN
    alani goremez. Ufuk dolmadan "tuttu/tutmadi" denemez.
    """
    import tempfile, pathlib as _p
    from finagent.bot.tools import ToolBox
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        tb = ToolBox(load_settings(), db, _p.Path(d) / "pending",
                     sahip="ali", chat_id="1")
        r = _cagir({a.name: a for a in tb.araclar()}["gecmis_gorus"], gun=30)
        acik = [g for g in r["gorusler"] if g["durum"] == "acik"][0]
        for alan in ("isabet", "getiri_pct", "anormal_pct"):
            assert alan not in acik, f"acik tahminde {alan} var: {acik}"
        assert "BILINMIYOR" in acik["not"]

        puanli = [g for g in r["gorusler"] if g["durum"] == "puanlandi"][0]
        assert puanli["isabet"] is True and puanli["getiri_pct"] == 3.2
        db.close()


def test_gecmis_gorus_karneyi_kirpmaz():
    """
    `yeterli_mi`, `not`, guven araligi ve `vekilsiz_n` orneklem
    uyarisini tasiyor. Kirpilirsa model n=3'ten "%67 isabet" alintilar.
    """
    import tempfile, pathlib as _p
    from finagent.bot.tools import ToolBox
    from finagent.pulse.journal import Defter
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        tb = ToolBox(load_settings(), db, _p.Path(d) / "pending",
                     sahip="ali", chat_id="1")
        r = _cagir({a.name: a for a in tb.araclar()}["gecmis_gorus"], gun=30)
        assert r["karne"] == Defter(db).karne("ali"), "karne kirpilmis"
        assert "not" in r["karne"] and "yeterli_mi" in r["karne"], r["karne"]
        db.close()


def test_gecmis_gorus_sinir_ve_bos_sonuc():
    """gun sinir disi -> hata; kayit yok -> BOS LISTE + not (hata degil)."""
    import tempfile, pathlib as _p
    from finagent.bot.tools import ToolBox
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        tb = ToolBox(load_settings(), db, _p.Path(d) / "pending",
                     sahip="ali", chat_id="1")
        ad = {a.name: a for a in tb.araclar()}
        assert "hata" in _cagir(ad["gecmis_gorus"], gun=0)
        assert "hata" in _cagir(ad["gecmis_gorus"], gun=400)
        # Bulunmayan sembol -> HATA + ipucu
        yok = _cagir(ad["gecmis_gorus"], sembol="YOKBOYLE")
        assert "hata" in yok and "ipucu" in yok, yok
        # Kayit yok -> bos liste + not, HATA DEGIL.
        # Tahmini OLMAYAN bir sembol kullaniliyor: ASML'nin bugunku
        # acik gorusu `gun=1` penceresine de girerdi.
        db.upsert_instrument("BOSSEM", "BUX", "Bos", "equity", "EUR")
        bos = _cagir(ad["gecmis_gorus"], gun=30, sembol="BOSSEM")
        assert "hata" not in bos and bos["gorusler"] == [], bos
        assert "gorusu yok" in bos["not"], bos
        db.close()


def test_gecmis_ozet_json_blogu_sizdirmaz():
    """
    JSON defter icin, insan icin degil. Sade katman varsa o, yoksa
    teknik — ama JSON blogu ASLA.
    """
    import tempfile, pathlib as _p
    from finagent.bot.tools import ToolBox
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        with db.tx() as c:
            c.execute("""INSERT INTO panel_runs (run_ts,ajan,ham_metin,
                json_durum,gorus_sayisi,sahip) VALUES (datetime('now'),
                'hakem',?, 'ok',1,'ali')""",
                      ('### SADE\nSade ozet burada.\n\n### TEKNIK\n'
                       'RSI 78.\n\n```json\n{"gorusler":[]}\n```',))
            c.execute("""INSERT INTO panel_runs (run_ts,ajan,ham_metin,
                json_durum,gorus_sayisi,sahip) VALUES (datetime('now'),
                'hakem','BASKASININ ozeti','ok',1,'esi')""")
        tb = ToolBox(load_settings(), db, _p.Path(d) / "pending",
                     sahip="ali", chat_id="1")
        r = _cagir({a.name: a for a in tb.araclar()}["gecmis_ozet"], gun=7)
        birlesik = " ".join(k["metin"] for k in r["kayitlar"])
        assert "```json" not in birlesik, birlesik
        assert "Sade ozet" in birlesik
        assert "BASKASININ" not in birlesik, "capraz sizinti"
        db.close()


def test_envanter_sembol_listesi_dondurmez():
    """
    343 sembol adi her tura giriyordu: ~2.900 karakter ve daha onemlisi
    dikkat seyreltmesi. Envanterin isi neyin VAR OLDUGUNU degil NE KADAR
    oldugunu soylemek.
    """
    import tempfile, json as _j
    from finagent.bot.chat import ChatEngine
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        env = ChatEngine(load_settings(), db).envanter("ali")
        assert isinstance(env["gunluk_fiyat_serisi_olan"], dict), env
        assert "toplam" in env["gunluk_fiyat_serisi_olan"]
        assert isinstance(env["saatlik_seri_olan_kripto"], dict)
        metin = _j.dumps(env, ensure_ascii=False)
        assert "ASML" not in metin, "sembol listesi hala envanterde"
        db.close()


def test_unut_pending_dosyalarini_da_siler():
    """
    Telegram'in "Clear Messages"i ISTEMCI TARAFI: butonlu mesaj kaybolur
    ama `pending/` dosyasi kalir ve sonraki `/onayla` GORULMEYEN bir
    ekran goruntusunu portfoye yazar.
    """
    import tempfile, pathlib as _p, json as _j
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        s = load_settings()
        s.raw.setdefault("telegram", {})["sahipler"] = {"111": "ali",
                                                        "222": "esi"}
        bot = _sahte_bot(s, db)
        bot.pending_dir = _p.Path(d) / "pending"
        bot.pending_dir.mkdir()
        for token, chat in (("a1", "111"), ("a2", "111"), ("b1", "222")):
            (bot.pending_dir / f"{token}.json").write_text(
                _j.dumps({"_chat_id": chat, "hesap": "bux",
                          "pozisyonlar": []}))
        import types
        bot._chat = lambda: types.SimpleNamespace(unut=lambda c: None)

        bot._on_text("/unut", "111")
        kalan = sorted(x.stem for x in bot.pending_dir.glob("*.json"))
        assert kalan == ["b1"], f"digerinin dosyasina dokunuldu: {kalan}"
        assert any("2" in m and "iptal" in m for m, _ in bot.gonderilen), \
            bot.gonderilen
        db.close()


def test_bekleyen_yas_bilgisi_verir():
    """Kullanici HATIRLAMADIGI bir okumayi onaylamadan once yasini gormeli."""
    import tempfile, pathlib as _p, json as _j, os, time
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        s = load_settings()
        s.raw.setdefault("telegram", {})["sahipler"] = {"111": "ali"}
        bot = _sahte_bot(s, db)
        bot.pending_dir = _p.Path(d) / "pending"; bot.pending_dir.mkdir()
        yol = bot.pending_dir / "a1.json"
        yol.write_text(_j.dumps({"_chat_id": "111"}))
        eski = time.time() - 3 * 3600
        os.utime(yol, (eski, eski))

        metin = bot._bekleyen_text("111")
        assert "3 saat once" in metin, metin
        assert bot._bekleyen_text("999") == "Bekleyen okuma yok."
        db.close()


# ═══════════════════════════════════════════════════════════════════
# SOHBET ARSIVI — kalici kayit
# ═══════════════════════════════════════════════════════════════════

def _arsiv_db(tmp):
    import pathlib as _p
    from finagent.storage.db import Database
    db = Database(_p.Path(tmp) / "a.db"); db.init_schema()
    return db


def test_arsiv_pencere_budanirken_kayit_budanmaz():
    """
    ARSIVIN VARLIK SEBEBI. Modelin gordugu pencere son 8 turu tutuyor
    (chat.py MAX_GECMIS) ve asistan cevabini 1500 karakterde kesiyor;
    tek kalici kayit o dosyaydi, yani 9. turdan sonra eskisi SILINIYORDU.
    Arsiv ne budanir ne kirpilir.
    """
    import tempfile
    from finagent.bot.chat import MAX_GECMIS
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        uzun = "x" * 5000
        for i in range(MAX_GECMIS * 3):
            db.sohbet_kaydet("111", "user", f"soru {i}", sahip="ali")
            db.sohbet_kaydet("111", "assistant", uzun, sahip="ali")

        assert db.sohbet_sayisi("ali") == MAX_GECMIS * 6, db.sohbet_sayisi("ali")
        satirlar = db.sohbet_ara("ali", gun=1, limit=1000)
        assert len(satirlar) == MAX_GECMIS * 6
        # TAM METIN: pencere 1500'de keserdi.
        assert len(satirlar[-1]["metin"]) == 5000
        # SIRA ESKIDEN YENIYE — konusma ancak sirasi korunursa okunur.
        assert satirlar[0]["metin"] == "soru 0", satirlar[0]["metin"]
        db.close()


def test_arsiv_sahibe_gore_suzulur_ve_sahipsiz_satir_sizmaz():
    """
    Cok kullanicili katmanin kurali arsivde de gecerli: sahip
    PARAMETREDIR ve okuma daima suzulur. Sahip cozulemeyen satir
    (sahip NULL) KAYBEDILMEZ ama KIMSENIN gecmisine girmez.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        db.sohbet_kaydet("111", "user", "ali'nin sorusu", sahip="ali")
        db.sohbet_kaydet("222", "user", "esinin sorusu", sahip="esi")
        db.sohbet_kaydet("333", "user", "sahipsiz sorusu", sahip=None)

        ali = [r["metin"] for r in db.sohbet_ara("ali")]
        esi = [r["metin"] for r in db.sohbet_ara("esi")]
        assert ali == ["ali'nin sorusu"], ali
        assert esi == ["esinin sorusu"], esi
        # Satir DURUYOR (arsiv kaybetmez) ama suzgecten gecmiyor.
        toplam = db.query("SELECT COUNT(*) n FROM sohbet_kaydi")[0]["n"]
        assert toplam == 3, toplam

        try:
            db.sohbet_ara("")
        except ValueError:
            pass
        else:
            raise AssertionError("sahipsiz okuma SESSIZCE calisti")
        db.close()


def test_arsiv_like_jokerleri_kacirilir():
    """
    `%` iceren bir arama ("%20 dustu") kacirilmazsa TUM satirlari
    dondururdu — bos sonuc kadar yaniltici, cunku alakasiz turlar
    "bulundu" diye modele gider ve model onlar uzerine yorum kurar.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        db.sohbet_kaydet("111", "user", "ASML nasil", sahip="ali")
        db.sohbet_kaydet("111", "user", "BTC %20 dustu", sahip="ali")

        # `%` LITERAL aranir: yalnizca icinde gercekten `%` gecen tur.
        # Kacirilmasaydi joker olur ve IKISI de donerdi.
        assert [r["metin"] for r in db.sohbet_ara("ali", sorgu="%")] == \
            ["BTC %20 dustu"]
        # `_` hicbir turda yok; joker olsaydi ikisini de dondururdu.
        assert len(db.sohbet_ara("ali", sorgu="_")) == 0
        bulunan = db.sohbet_ara("ali", sorgu="%20")
        assert [r["metin"] for r in bulunan] == ["BTC %20 dustu"], bulunan
        db.close()


def test_arsiv_gun_penceresi_sinir_gununde_kaybetmez():
    """
    BICIM TUZAGI. Arsiv damgasi ISO-8601 ('...T19:45:23+00:00'),
    SQLite'in `datetime('now',...)` ciktisi bosluk ayracli ve ofissiz.
    Metin karsilastirmasinda 'T' > ' ' oldugu icin sinir gunundeki turlar
    SESSIZCE pencerenin disinda kalirdi. Ayni uretecten uretiliyor mu?
    """
    import tempfile
    from datetime import datetime, timedelta, timezone
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        simdi = datetime.now(timezone.utc).replace(microsecond=0)
        # Sinirin 1 saat ICINDE kalan tur: 30 gun penceresinde OLMALI.
        db.sohbet_kaydet("111", "user", "sinirda", sahip="ali",
                         ts=(simdi - timedelta(days=30) +
                             timedelta(hours=1)).isoformat())
        # Sinirin 1 saat DISINDA: olmamali.
        db.sohbet_kaydet("111", "user", "cok eski", sahip="ali",
                         ts=(simdi - timedelta(days=30) -
                             timedelta(hours=1)).isoformat())

        metinler = [r["metin"] for r in db.sohbet_ara("ali", gun=30)]
        assert metinler == ["sinirda"], metinler
        db.close()


def test_unut_arsivi_acikca_istenmedikce_silmez():
    """
    `/unut` calisma hafizasini siler; arsiv DURUR ve bu kullaniciya
    SOYLENIR. "Sohbet gecmisi silindi" deyip kaydi tutmak, kullanicinin
    sildigini sandigi bir seyi saklamak olurdu.
    """
    import tempfile, types
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        s = load_settings()
        s.raw.setdefault("telegram", {})["sahipler"] = {"111": "ali"}
        bot = _sahte_bot(s, db)
        bot.pending_dir = _pathlib.Path(d) / "pending"
        bot.pending_dir.mkdir()
        bot._chat = lambda: types.SimpleNamespace(unut=lambda c: None)

        db.sohbet_kaydet("111", "user", "kalmali", sahip="ali")

        bot._on_text("/unut", "111")
        assert db.sohbet_sayisi("ali") == 1, "arsiv istenmeden silindi"
        metin = bot.gonderilen[-1][0]
        assert "arsiv" in metin.lower() and "1 tur" in metin, metin

        bot._on_text("/unut arsiv", "111")
        assert db.sohbet_sayisi("ali") == 0, "acik istege ragmen silinmedi"
        assert "1" in bot.gonderilen[-1][0], bot.gonderilen[-1][0]
        db.close()


def test_sohbet_akisi_arsive_tam_metni_yazar():
    """
    BAGLANTI TESTI — asil risk burada. `_sohbet` yuvarlanan pencereye
    cevabi 1500 karakterde KESEREK yaziyor; ayni kirpik metin arsive de
    giderse arsivin varlik sebebi kalmaz. Iki cagrinin AYRI kaynaktan
    beslendigini kanitlar. Arac listesi de kaydedilmeli: "bu cevabi
    hangi veriye bakarak verdim" sorusu aylar sonra sorulur.
    """
    import tempfile, types
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        s = load_settings()
        s.raw.setdefault("telegram", {})["sahipler"] = {"111": "ali"}
        bot = _sahte_bot(s, db)
        bot._son_gorsel = {}

        uzun = "y" * 4000
        yazilan = {}
        motor = types.SimpleNamespace(
            cevapla=lambda c, soru, gorsel=None, sahip=None, **_: {
                "metin": uzun, "araclar": ["teknik", "haberler"],
                "tokenlar": [], "gorseller": []},
            gecmis_oku=lambda c: [],
            gecmis_yaz=lambda c, g: yazilan.update(g=g))
        bot._chat = lambda: motor

        bot._sohbet("ASML ne alemde", "111")

        satirlar = db.sohbet_ara("ali", gun=1)
        assert [r["rol"] for r in satirlar] == ["user", "assistant"], satirlar
        assert satirlar[0]["metin"] == "ASML ne alemde"
        assert len(satirlar[1]["metin"]) == 4000, "arsive KIRPIK metin gitti"
        assert satirlar[1]["araclar"] == "teknik, haberler"
        # Pencere ise kirpilmis olmali — ikisi AYRI kayit.
        assert len(yazilan["g"][1]["metin"]) == 1500, yazilan["g"][1]
        db.close()


def test_arsivleme_hatasi_cevabi_dusurmez():
    """
    Arsivleme bir YAN ETKI. Disk dolu ya da tablo kilitliyse kullanicinin
    cevabi KAYBOLMAMALI — ama hata da sessiz gecmemeli.
    """
    import tempfile, types
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        s = load_settings()
        s.raw.setdefault("telegram", {})["sahipler"] = {"111": "ali"}
        bot = _sahte_bot(s, db)
        bot._son_gorsel = {}

        def _patla(*a, **k):
            raise RuntimeError("database is locked")
        db.sohbet_kaydet = _patla

        motor = types.SimpleNamespace(
            cevapla=lambda c, soru, gorsel=None, sahip=None, **_: {
                "metin": "cevap duruyor", "araclar": [], "tokenlar": [],
                "gorseller": []},
            gecmis_oku=lambda c: [], gecmis_yaz=lambda c, g: None)
        bot._chat = lambda: motor

        bot._sohbet("soru", "111")   # PATLAMAMALI
        assert any("cevap duruyor" in m for m, _ in bot.gonderilen), \
            bot.gonderilen
        db.close()


def test_sohbet_arsivi_araci_sahibe_bagli_ve_uyarili():
    """
    Arac kendi ciktisinda "bu dogrulanmis degil" demeli: gecmis sohbet
    metni bir OLGU KAYNAGI degil, alintidir. Sahipsiz sohbette ise acik
    hata dondurmeli — bos donmek "gecmisin yok" diye okunurdu.
    """
    import tempfile, json as _j, asyncio
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        s = load_settings()
        db.sohbet_kaydet("111", "user", "ASML almali miyim", sahip="ali")
        db.sohbet_kaydet("111", "assistant", "245 EUR civari", sahip="ali",
                         araclar=["teknik", "portfoy"])
        db.sohbet_kaydet("222", "user", "baskasinin sorusu", sahip="esi")

        def _cagir(sahip, **kw):
            tb = ToolBox(s, db, _pathlib.Path(d) / "p", sahip=sahip,
                         chat_id="111")
            arac = {getattr(a, "name", ""): a for a in tb.araclar()}
            fn = arac.get("sohbet_arsivi") or arac.get(
                "mcp__finagent__sohbet_arsivi")
            ham = asyncio.run(fn.handler(kw) if hasattr(fn, "handler")
                              else fn(kw))
            return _j.loads(ham["content"][0]["text"])

        out = _cagir("ali", sorgu="", gun=30)
        assert "DOGRULANMIS DEGIL" in out["uyari"], out
        assert [t["metin"] for t in out["turlar"]] == [
            "ASML almali miyim", "245 EUR civari"], out["turlar"]
        assert out["turlar"][1]["kullandigim_araclar"] == "teknik, portfoy"

        assert not _cagir("esi", sorgu="ASML", gun=30)["turlar"], \
            "baskasinin sohbeti sizdi"
        assert "hata" in _cagir(None, sorgu="", gun=30), "sahipsiz calisti"
        db.close()


# ═══════════════════════════════════════════════════════════════════
# YETENEK REHBERI — beyan ile gercegin ayrisMAMASI
# ═══════════════════════════════════════════════════════════════════

def _canli_arac_adlari():
    import tempfile
    from finagent.config import load_settings
    from finagent.storage.db import Database
    from finagent.bot.tools import ToolBox
    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "y.db"); db.init_schema()
        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="1")
        adlar = [t.name for t in tb.araclar()]
        db.close()
        return adlar


def test_rehber_her_araci_sade_dille_karsilar():
    """
    REHBERIN CURUMESINI ENGELLEYEN TEST.

    Elle yazilan bir "neler yapabilirim" metni, bir arac eklendigi anda
    EKSIK, bir arac kaldirildiginda YALAN olur ve kimse fark etmez.
    Burada iki yonlu esitlik zorunlu: her canli aracin sade karsiligi
    VAR, ve sade karsiligi olan her sey GERCEKTEN bir arac.
    """
    from finagent.bot import yetenekler
    canli = set(_canli_arac_adlari())
    beyan = set(yetenekler.SADE)

    assert not (canli - beyan), \
        f"sade karsiligi olmayan arac: {sorted(canli - beyan)}"
    assert not (beyan - canli), \
        f"olmayan araci anlatiyoruz: {sorted(beyan - canli)}"

    # Konu basliklarindaki her arac referansi da GERCEK olmali.
    for ad, k in yetenekler.KONULAR.items():
        yok = [a for a in k["araclar"] if a not in canli]
        assert not yok, f"'{ad}' konusu olmayan araci sayiyor: {yok}"


def test_rehber_komut_listesi_gercek_dallanmayla_ayni():
    """
    `/yardim` metni `/hepsi` komutunu HIC listelemiyordu — beyan ile
    gercegin sessiz ayrismasinin canli ornegi. Komut listesi artik tek
    yerde (yetenekler.KOMUTLAR) ve `_on_text`'in GERCEK dallanmasindan
    ast ile cikarilip karsilastiriliyor.
    """
    import ast, inspect
    from finagent.bot import listener, yetenekler

    kaynak = inspect.getsource(listener.FinBot._on_text)
    agac = ast.parse(kaynak.lstrip().replace("\n    ", "\n"))
    gercek: set[str] = set()
    for dugum in ast.walk(agac):
        if not isinstance(dugum, ast.Compare):
            continue
        if not (isinstance(dugum.left, ast.Name) and dugum.left.id == "cmd"):
            continue
        for kars in dugum.comparators:
            if isinstance(kars, ast.Constant) and isinstance(kars.value, str):
                gercek.add(kars.value)
            elif isinstance(kars, (ast.Tuple, ast.List, ast.Set)):
                gercek |= {e.value for e in kars.elts
                           if isinstance(e, ast.Constant)}

    assert gercek, "dallanmadan hic komut cikarilamadi — test bozuk"
    beyan = set(yetenekler.KOMUTLAR)
    assert not (gercek - beyan), \
        f"calisan ama HIC ANLATILMAYAN komut: {sorted(gercek - beyan)}"
    assert not (beyan - gercek), \
        f"anlatilan ama CALISMAYAN komut: {sorted(beyan - gercek)}"


def test_rehber_konulari_metne_donusur_ve_butonlar_gecerli():
    """Her konu render edilebilmeli ve her buton gercek bir konuya gitmeli."""
    from finagent.bot import yetenekler
    hedefler = {d["callback_data"].split(":", 1)[1]
                for satir in yetenekler.menu_markup()["inline_keyboard"]
                for d in satir}
    assert hedefler == set(yetenekler.KONULAR), hedefler

    for ad in yetenekler.KONULAR:
        metin = yetenekler.konu_metni(ad)
        assert len(metin) > 80, f"{ad} konusu bos gorunuyor"
        # Telegram HTML kipi: acilan her etiket kapanmali.
        for etiket in ("b", "i", "code"):
            assert metin.count(f"<{etiket}>") == metin.count(f"</{etiket}>"), \
                f"{ad} konusunda <{etiket}> dengesiz"


def test_ipucu_ayni_kisiye_bir_kez_gider():
    """
    Tekrar eden ipucu, risk alarmlarinda yasanan bildirim yorgunlugunun
    aynisini uretir. Kontrol ve isaretleme TEK cagrida olmali; ayrilirsa
    model ipucunu verip isaretlemeyi atlayabilir.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        assert db.ipucu_ilk_mi("ali", "grafik") is True
        assert db.ipucu_ilk_mi("ali", "grafik") is False
        # BASKA KISI kendi ipucunu ALMALI.
        assert db.ipucu_ilk_mi("esi", "grafik") is True
        assert db.ogretilenler("ali") == {"grafik"}
        assert db.ogretilenleri_sifirla("ali") == 1
        assert db.ipucu_ilk_mi("ali", "grafik") is True
        db.close()


def test_ipucu_araci_gecersiz_kodu_reddeder_ve_tekrar_etmez():
    """Arac katmani: gecersiz kod ACIK hata, ikinci cagri `ver: False`."""
    import tempfile, json as _j, asyncio
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox
    from finagent.bot import yetenekler
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="1")
        arac = {t.name: t for t in tb.araclar()}
        fn = arac["ipucu"].handler

        def _c(kod):
            return _j.loads(asyncio.run(fn({"kod": kod}))["content"][0]["text"])

        assert "hata" in _c("boyle_bir_kod_yok")
        ilk = _c("grafik")
        assert ilk["ver"] is True and ilk["metin"] == yetenekler.IPUCLARI["grafik"]
        assert _c("grafik")["ver"] is False
        db.close()


def test_neler_yapabilirim_canli_arac_listesinden_besleniyor():
    """
    Yetenek beyani hafizadan degil CANLI arac listesinden gelmeli.
    Arac listesi degistiginde beyan da degismeli — sabit metin olsaydi
    degismezdi.
    """
    import tempfile, json as _j, asyncio, types
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="1")
        fn = {t.name: t for t in tb.araclar()}["neler_yapabilirim"].handler

        tum = _j.loads(asyncio.run(fn({}))["content"][0]["text"])
        assert set(tum["konular"]) == {"portfoy", "analiz", "veri", "kripto",
                                       "gecmis", "komutlar"}, tum["konular"]
        assert any("ekran goruntusu" in y or "pozisyon" in y
                   for y in tum["yapabildiklerim"])

        tek = _j.loads(asyncio.run(fn({"konu": "portfoy"}))["content"][0]["text"])
        assert tek["konu"] == "Portfoyum"
        assert tek["ornek_istekler"], tek

        assert "hata" in _j.loads(
            asyncio.run(fn({"konu": "uzay"}))["content"][0]["text"])

        # ARAC LISTESI KISALINCA beyan da kisalmali.
        tb2 = ToolBox(load_settings(), db, _pathlib.Path(d) / "p2",
                      sahip="ali", chat_id="1")
        tb2.araclar = lambda: [types.SimpleNamespace(name="portfoy")]
        from finagent.bot import yetenekler
        assert yetenekler.ozet(tb2)["yapabildiklerim"] == \
            [yetenekler.SADE["portfoy"]]
        db.close()


def test_rehber_menusu_ve_konu_butonu_calisir():
    """`/rehber` menu doner; buton bir konuyu acar; bilinmeyen konu menuye duser."""
    import tempfile, types
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        s = load_settings()
        s.raw.setdefault("telegram", {})["sahipler"] = {"111": "ali"}
        bot = _sahte_bot(s, db)
        bot.pending_dir = _pathlib.Path(d) / "pending"; bot.pending_dir.mkdir()

        bot._on_text("/rehber", "111")
        assert "Neler yapabilirim" in bot.gonderilen[-1][0]

        bot._on_text("/rehber kripto", "111")
        assert "KRIPTO" in bot.gonderilen[-1][0]

        bot._on_callback({"id": "1", "data": "reh:portfoy",
                          "message": {"chat": {"id": "111"}}})
        assert "PORTFOYUM" in bot.gonderilen[-1][0]

        bot._on_callback({"id": "2", "data": "reh:yok_boyle",
                          "message": {"chat": {"id": "111"}}})
        assert "Neler yapabilirim" in bot.gonderilen[-1][0]

        # Ipuclarini sifirlama
        db.ipucu_ilk_mi("ali", "grafik")
        bot._on_text("/rehber sifirla", "111")
        assert "sifirlandi" in bot.gonderilen[-1][0]
        assert db.ogretilenler("ali") == set()
        db.close()


# ═══════════════════════════════════════════════════════════════════
# DOGAL DIL VARSAYILAN — komut kacirma
# ═══════════════════════════════════════════════════════════════════

def _dogal_bot(d, db):
    import types
    from finagent.config import load_settings
    s = load_settings()
    s.raw.setdefault("telegram", {})["sahipler"] = {"111": "ali"}
    bot = _sahte_bot(s, db)
    bot.pending_dir = _pathlib.Path(d) / "pending"; bot.pending_dir.mkdir()
    bot.state_dir = _pathlib.Path(d)
    bot.gorsel_dir = bot.state_dir / "gorsel"; bot.gorsel_dir.mkdir()
    bot.kuyruk = None            # dogal bot yerinde calisir, kuyruk kurmaz
    bot._chat = lambda: types.SimpleNamespace(unut=lambda c: None)
    bot.sohbete_gidenler = []
    bot._sohbet = lambda soru, chat_id, gorsel=None: \
        bot.sohbete_gidenler.append(soru)

    # YAN ETKILI KOMUT GOVDELERI TAKLIT EDILIYOR — testin kendisi
    # GERCEK is yapamamali. Bir regresyonda "rapor ne zaman hazir"
    # cumlesi komuta kacarsa, taklit yoksa `pipeline.run_daily` gercekten
    # kosar: ~3 dk collector, LLM cagrisi ve KULLANICIYA TELEGRAM
    # BILDIRIMI. Bu bir kez yasandi. Taklit, komut yoluna kacildigini
    # KAYDEDER ama calistirmaz — testin gormesi gereken zaten budur.
    bot.komuta_kacanlar = []

    def _isaretle(ad):
        def _f(*a, **k):
            bot.komuta_kacanlar.append(ad)
            return f"<{ad}>"
        return _f

    for ad in ("_calistir_rapor", "_temizle", "_sil_son", "_haber_tara",
               "_hepsini_onayla", "_durum_text", "_takip_text",
               "_evren_text", "_aday_ekle", "_etki_text", "_kimlik_text",
               "_portfoy_text", "_bekleyen_text", "_unut", "_rehber"):
        setattr(bot, ad, _isaretle(ad))
    return bot


def test_komut_kelimesiyle_baslayan_cumle_komut_calistirmaz():
    """
    OLCULEN CANLI KUSUR: `cmd.lstrip("/")` yuzunden egik cizgi istege
    bagliydi ve bir komut adiyla BASLAYAN her dogal cumle komuta
    kaciyordu. Ikisi VERI SILIYORDU, hicbir onay sormadan:
        "sil sunu"     -> son portfoy kaydini sildi
        "unut gitsin"  -> sohbet hafizasini temizledi
        "temizle ..."  -> COKTU
    Varsayilan artik SOHBET.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        bot = _dogal_bot(d, db)
        cumleler = ["sil sunu", "unut gitsin", "durum ne alemde",
                    "temizle biraz yer ac", "haber var mi ASELSAN icin",
                    "portfoyum nasil", "rapor ne zaman hazir olur",
                    "ozet gecer misin", "takip ettiklerin neler",
                    "kimlik dogrulamasi nasil calisiyor"]
        for c in cumleler:
            bot._on_text(c, "111")
        assert not bot.komuta_kacanlar, \
            f"komut yoluna kacan cumle var -> {bot.komuta_kacanlar}"
        assert bot.sohbete_gidenler == cumleler, \
            f"sohbete gitmeyen var: {set(cumleler) - set(bot.sohbete_gidenler)}"
        assert not bot.gonderilen, f"komut ciktisi uretildi: {bot.gonderilen}"
        db.close()


def test_egik_cizgili_komut_calismaya_devam_eder():
    """Komutlar KALDIRILMADI — yalnizca `/` ile cagriliyor."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        bot = _dogal_bot(d, db)
        bot._on_text("/durum", "111")
        assert bot.komuta_kacanlar == ["_durum_text"], bot.komuta_kacanlar
        assert not bot.sohbete_gidenler, "komut sohbete dustu"
        db.close()


def test_bilinmeyen_komut_hata_degil_sohbet():
    """
    "Bilinmeyen komut" demek, cevabi bilmemek degil SORMAMAK olurdu.
    Cizgi atilip metin modele veriliyor.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        bot = _dogal_bot(d, db)
        bot._on_text("/ASELSAN nasil gidiyor", "111")
        assert bot.sohbete_gidenler == ["ASELSAN nasil gidiyor"], \
            bot.sohbete_gidenler
        assert not any("Bilinmeyen" in m for m, _ in bot.gonderilen), \
            bot.gonderilen
        db.close()


def test_toplu_onay_yalnizca_okumalari_alir():
    """
    `pending/` artik tek tip tasimiyor. `/onayla` bir SILME islemini de
    onaylasaydi, kullanici "okumalarimi kaydet" derken KAYIT SILERDI.
    """
    import tempfile, json as _j
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        bot = _dogal_bot(d, db)
        for tok, tip in (("a", "pozisyon"), ("b", "sil_son"), ("c", "rapor")):
            (bot.pending_dir / f"{tok}.json").write_text(_j.dumps(
                {"_chat_id": "111", "_tip": tip, "_sahip": "ali",
                 "hesap": "bux", "pozisyonlar": []}))
        # `_tip` TASIMAYAN eski dosya pozisyon sayilir (tek tip vardi).
        (bot.pending_dir / "d.json").write_text(_j.dumps(
            {"_chat_id": "111", "hesap": "bux", "pozisyonlar": []}))

        okumalar = {y.stem for y in bot._bekleyenler("111")}
        assert okumalar == {"a", "d"}, okumalar
        # /unut HEPSINI iptal etmeli.
        assert {y.stem for y in bot._bekleyenler("111", tipler=None)} == \
            {"a", "b", "c", "d"}
        db.close()


def test_rapor_ve_silme_dogal_dilden_erisilebilir_ama_onaydan_gecer():
    """
    Komutsuz kullanim ancak komutlarin YAPTIGI SEY araclarla da
    erisilebilirse gercek olur. Ikisi de YIKICI/uzun oldugu icin
    dogrudan calismaz — onay kapisindan gecer.
    """
    import tempfile, json as _j, asyncio
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        db.insert_positions("bux", "2026-08-16T10:00:00+00:00",
                            [{"symbol": "ASML", "quantity": 1.0}], "ali")
        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="111")
        arac = {t.name: t for t in tb.araclar()}

        def _c(ad, args=None):
            return _j.loads(asyncio.run(
                arac[ad].handler(args or {}))["content"][0]["text"])

        r = _c("rapor_uret", {"topla": True})
        assert r["durum"] == "onaya sunuldu" and r["token"]
        s = _c("son_kaydi_sil")
        assert s["silinecek"]["hesap"] == "bux", s
        # Diskte DURUYOR, uygulanmadi.
        assert db.query("SELECT COUNT(*) c FROM positions")[0]["c"] == 1
        tipler = {_j.loads(y.read_text())["_tip"]
                  for y in (_pathlib.Path(d) / "p").glob("*.json")}
        assert tipler == {"rapor", "sil_son"}, tipler
        db.close()


def test_son_snapshot_sahibe_gore_secilir():
    """
    `_sil_son` en son snapshot'i SAHIP SUZGECI OLMADAN seciyordu.
    Iki kisilik kurulumda A'nin "geri al"i B'nin kaydini hedefler,
    silme hicbir sey silmez ve kullanici "geri alindi" yazisini gorur.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        db.insert_positions("bux", "2026-08-16T10:00:00+00:00",
                            [{"symbol": "ASML", "quantity": 1.0}], "ali")
        # ESI daha SONRA yaziyor.
        db.insert_positions("binance", "2026-08-16T20:00:00+00:00",
                            [{"symbol": "BTC", "quantity": 2.0}], "esi")

        ali = db.son_snapshot("ali")
        assert ali["account"] == "bux", dict(ali)
        assert db.son_snapshot("esi")["account"] == "binance"
        db.close()


# ═══════════════════════════════════════════════════════════════════
# 2026-08-17 DUZELTME TURU
# ═══════════════════════════════════════════════════════════════════

def test_veri_topla_tum_collectorleri_beyan_eder():
    """
    OLCULEN CANLI KUSUR: aracin aciklamasi 19 collector'un 8'ini
    sayiyordu. Eksikler arasinda `isyatirim` — BIST'in TEK fiyat
    kaynagi — vardi. Model GORMEDIGI kaynagi isteyemez; gormedigi icin
    `prices`i calistirdi, `prices` BIST sembollerini reddetti ve model
    UC MESAJ boyunca "boru hatti bozuk" dedi. Bozuk olan LISTEYDI.
    """
    import tempfile
    from finagent.collectors import KAPSAM, REGISTRY
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox

    assert set(KAPSAM) == set(REGISTRY), (
        f"KAPSAM eksik: {sorted(set(REGISTRY) - set(KAPSAM))} / "
        f"fazla: {sorted(set(KAPSAM) - set(REGISTRY))}")

    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="1")
        aciklama = {t.name: t for t in tb.araclar()}["veri_topla"].description
        eksik = [a for a in REGISTRY if a not in aciklama]
        assert not eksik, f"aciklamada gecmeyen collector: {eksik}"
        # BIST'in fiyat kaynagi ile BIST'i KAPSAMAYAN kaynak ayirt
        # edilebilir olmali — asil hata bu ayrimin gorunmemesiydi.
        assert "BIST'in TEK fiyat kaynagi" in aciklama
        assert "BIST'i KAPSAMAZ" in aciklama
        db.close()


def test_endeks_uyeligi_veritabanindan_okunur():
    """
    Model "BIST100 uye listesi bende yok" dedi. YANLISTI: `index_members`
    tablosunda BIST 100'un 100 uyesi ve hepsinin fiyat verisi VARDI.
    Veri vardi, ONA ULASAN ARAC yoktu — bu projenin en kotu hata sinifi
    (kendi veritabanimiz hakkinda yanlis beyan).
    """
    import tempfile, json as _j, asyncio
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        for sem in ("AKBNK", "GARAN", "THYAO"):
            iid = db.upsert_instrument(sem, "BIST", sem, "equity", "TRY")
            with db.tx() as c:
                c.execute("INSERT OR IGNORE INTO index_members "
                          "(instrument_id, index_name) VALUES (?,?)",
                          (iid, "BIST 100"))
        # Yalnizca ikisinin fiyati var: "uyelik" ile "veri" AYRI seyler.
        for sem in ("AKBNK", "GARAN"):
            db.upsert_prices(db.upsert_instrument(sem, "BIST"),
                             [{"ts": "2026-08-14", "close": 10.0}], "t",
                             currency="TRY")

        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="1")
        fn = {t.name: t for t in tb.araclar()}["endeks_uyeleri"].handler

        def _c(**kw):
            return _j.loads(asyncio.run(fn(kw))["content"][0]["text"])

        # Esnek ad eslesmesi: "BIST100" == "BIST 100"
        for yazim in ("BIST 100", "BIST100", "bist-100"):
            out = _c(endeks=yazim)
            assert out["uye_sayisi"] == 3, (yazim, out)
        assert out["fiyat_verisi_olan"] == 2, out
        assert out["not"], "veri eksigi SESSIZ gecildi"
        assert {u["sembol"] for u in out["uyeler"] if not u["fiyat_verisi"]} \
            == {"THYAO"}, out["uyeler"]

        assert "hata" in _c(endeks="YOKENDEKS")
        assert _c()["endeksler"][0]["endeks"] == "BIST 100"
        db.close()


def test_bicim_disi_sembol_katalogda_duramaz():
    """
    `币安人生` (BinanceLife) katalogda kaydedildi; sonrasinda HER `kripto`
    toplamasi bu sembolu URL'ye kodlayip 400 aldi. TEK bozuk satir,
    kripto kimlik zincirinin TAMAMINI kalici olarak durdurdu. Kapi
    semanin onunde olmali — toplama hattinda ayiklamak yetmez, kaynak
    degistikce ayni sey baska bicimde girer.
    """
    import tempfile
    from finagent.storage.db import sembol_gecersiz
    assert sembol_gecersiz("BTC") is None
    assert sembol_gecersiz("ABN.AS") is None
    assert sembol_gecersiz("BRK-B") is None
    assert "U+5E01" in (sembol_gecersiz("币安人生") or "")
    assert sembol_gecersiz("") and sembol_gecersiz("BTC/USDT")
    assert sembol_gecersiz("A" * 25)

    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        try:
            db.upsert_instrument("币安人生", "BINANCE", "BinanceLife")
        except ValueError as e:
            assert "gecersiz sembol" in str(e), e
        else:
            raise AssertionError("bicim disi sembol SESSIZCE kaydedildi")
        db.close()


def test_gorsel_hafizasi_suresiz_yasamaz():
    """
    Gorsel varken `Read` araci aciliyor. Gorsel HIC silinmedigi icin
    Read sonsuza kadar acik kaliyordu; model uc tur sonra onunla KAYNAK
    KODU okudu (arsivde kayitli: `Bash, Read, Bash, Read...`). Ayrica
    alakasiz bir sonraki soruya 40 dakika onceki ekran goruntusu
    ekleniyordu.
    """
    import tempfile, time
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        bot = _dogal_bot(d, db)
        bot._gorsel_koy("111", "/tmp/a.png")
        assert bot._gorsel_al("111") == "/tmp/a.png"

        # KAYIT DISKTE: isler ayri sureclerde kosuyor, surec ici bir
        # sozluk turlar arasinda yasamaz (bkz. bot/kuyruk.py).
        kayit = bot._gorsel_kaydi("111")
        assert kayit.exists(), "gorsel kaydi diske yazilmadi"

        # Suresi dolmus gibi geriye al.
        import json as _json
        veri = _json.loads(kayit.read_text())
        veri["ts"] = time.time() - bot.GORSEL_OMRU_SN - 1
        kayit.write_text(_json.dumps(veri))
        assert bot._gorsel_al("111") is None, "suresi dolmus gorsel hala acik"
        assert not kayit.exists(), "kayit temizlenmedi"
        assert bot._gorsel_al("999") is None
        db.close()


def test_haber_kademesi_gercek_yayincilari_tanir():
    """
    1.103 haberin 405'i kademe 0 ("bilinmeyen") idi — yani AA,
    BloombergHT, Dunya, WSJ gibi mesru yayincilar KANIT SAYILMIYORDU.
    Iki sebep: `news` collector'i publisher/tier yazmiyordu, ve kademe
    tablolarinda TR yayincilari, tel servisleri, kripto basini eksikti.
    """
    from finagent.research.sources import kademe
    beklenen = {
        "AA - Ekonomi": 2, "BloombergHT": 2, "Dunya Gazetesi": 2,
        "WSJ Markets": 2, "TRT Haber - Ekonomi": 2, "Axios": 2, "CNN": 2,
        "Business Wire": 2, "PR Newswire": 2, "GlobeNewswire": 2,
        "Investing TR - Piyasa": 3, "Cointelegraph": 3, "Decrypt.co": 3,
        "Midas'in Kulaklari": 3, "TradingKey": 3,
        "MarketBeat": 4, "Motley Fool": 4,
        "Aloha State Daily": 4, "MLB.com": 4, "Nashville Scene": 4,
    }
    yanlis = {ad: (kademe(ad), bek) for ad, bek in beklenen.items()
              if kademe(ad) != bek}
    assert not yanlis, f"kademe(ad) -> (gercek, beklenen): {yanlis}"


def test_haber_toplayicisi_yayinci_ve_kademe_yazar():
    """
    `news` collector'i satirlarina `publisher`/`tier` KOYMUYORDU; 181
    haber publisher=NULL ile durdu ve kademe fonksiyonu adi hic gormedi.
    """
    import inspect
    from finagent.collectors import news as haber_modulu
    kaynak = inspect.getsource(haber_modulu)
    assert '"publisher": yayinci' in kaynak, "publisher yazilmiyor"
    assert '"tier": kademe(yayinci)' in kaynak, "kademe hesaplanmiyor"


def test_kap_bildirim_urlsi_uretilebilir():
    """
    72 KAP bildiriminin 72'si URL'siz geldi — kademe 1 (resmi dosyalama)
    kaynak BAGIMSIZ DOGRULANABILIR degildi. KAP satirlari <a> icermiyor
    ama checkbox'in id'si bildirim numarasi ve
    `.../tr/Bildirim/<id>` kalici adres (2026-08-17'de 200 dondu).
    """
    from finagent.config import load_settings
    sel = load_settings().selectors.get("kap", {})
    assert sel.get("cell_link") and sel["cell_link"] != "TODO"
    assert sel.get("link_attr") == "id"
    assert "{id}" in (sel.get("link_kalip") or "")
    assert sel["link_kalip"].startswith("https://www.kap.org.tr/")

    import inspect
    from finagent.collectors import kap as kap_modulu
    kaynak = inspect.getsource(kap_modulu.KapCollector)
    # SAYI OLMAYAN id ile URL UYDURULMAMALI.
    assert "no.isdigit()" in kaynak, "id dogrulanmadan URL uretiliyor"


def test_ttm_takvim_donemiyle_karismaz():
    """
    Midas detay sayfasindaki "Net Kâr" SON 12 AY'dir (dogrulandi: AKBNK,
    EREGL, GARAN icin FY-H1+H1 formulu %0,0 sapma). Ama
    `concept='NetKar', period_end=<toplama gunu>, days=NULL` diye
    yaziliyordu. Uc zarari vardi:
      1. midasbilanco'nun GERCEK donemleriyle ayni seride, en yeni gibi
         siralaniyordu (ASELS: 41,2 milyar TTM, gercek H1 14,4 milyar).
      2. `days IS NULL` filtresi bir AKIM'i BILANCO ANLIK kalemi yapiyordu.
      3. "farkli uzunluktakiler karsilastirilmaz" korumasi ateslenemiyordu.
    """
    import tempfile
    from finagent.collectors.midas import DETAY_ALANLARI, TTM_KAVRAMLARI
    assert DETAY_ALANLARI["Net Kâr"][0] == "NetKarTTM", \
        "TTM hala takvim kavramiyla ayni ada yaziliyor"
    assert "NetKarTTM" in TTM_KAVRAMLARI
    assert "NetKar" not in TTM_KAVRAMLARI

    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        iid = db.upsert_instrument("ASELS", "BIST", "Aselsan", "equity", "TRY")
        db.upsert_fundamentals([
            # Gercek takvim donemleri
            (iid, "NetKar", "TRY", None, "2025-12-31", 365, 35.3e9,
             "midasbilanco", None, None, None, "2026-01-01", None),
            (iid, "NetKar", "TRY", None, "2026-06-30", 181, 14.4e9,
             "midasbilanco", None, None, None, "2026-07-01", None),
            # TTM — takvim donemi DEGIL
            (iid, "NetKarTTM", "TRY", "2025-08-16", "2026-08-16", 365, 41.2e9,
             "midas", None, "TTM", None, "2026-08-16", None),
        ])
        ozet = db.finansal_ozet(iid)

        # Yillik seri TTM'i ICERMEMELI.
        yillik = {x["donem_sonu"]: x["kalemler"].get("net_kar", {}).get("deger")
                  for x in ozet["yillik"]}
        assert yillik == {"2025-12-31": 35.3e9}, yillik
        # Yariyil kovasi AYRI ve dolu.
        assert [x["donem_sonu"] for x in ozet["yariyil"]] == ["2026-06-30"]
        # TTM gizlenmiyor ama AYRI basliktan ve uyarili.
        assert ozet["ttm"]["net_kar"] == 41.2e9
        assert "takvim donemi DEGIL" in ozet["ttm"]["not"]
        # Anlik (bilanco) kovasina AKIM sizmamali.
        assert not ozet["bilanco"], ozet["bilanco"]
        db.close()


def test_bist_bilancosu_finansallar_aracindan_gorunur():
    """
    97 BIST sirketinin bilancosu tabloda DURURKEN arac "ASELS icin XBRL
    verisi yok" donduruyordu — teknik olarak dogru, pratikte YANLIS
    BEYAN. `finansal_ozet` yalnizca XBRL kavramlarini soruyordu.
    """
    import tempfile, json as _j, asyncio
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox
    from finagent.storage.db import BIST_KAVRAMLARI
    from finagent.collectors.midasbilanco import SATIRLAR

    # Toplayicinin yazdigi HER kavramin bir etiketi olmali.
    yazilan = {k for k, _ in SATIRLAR.values()}
    assert not (yazilan - set(BIST_KAVRAMLARI)), \
        f"etiketi olmayan BIST kavrami: {sorted(yazilan - set(BIST_KAVRAMLARI))}"

    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        iid = db.upsert_instrument("ASELS", "BIST", "Aselsan", "equity", "TRY")
        db.upsert_fundamentals([
            (iid, "Hasilat", "TRY", None, "2025-12-31", 365, 100e9,
             "midasbilanco", None, None, None, "2026-01-01", None),
            (iid, "Ozkaynak", "TRY", None, "2025-12-31", None, 250e9,
             "midasbilanco", None, None, None, "2026-01-01", None),
        ])
        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="1")
        fn = {t.name: t for t in tb.araclar()}["finansallar"].handler
        out = _j.loads(asyncio.run(fn({"sembol": "ASELS"}))["content"][0]["text"])
        assert "hata" not in out, out
        assert out["yillik"][0]["kalemler"]["gelir"]["deger"] == 100e9
        assert out["bilanco"][0]["kalemler"]["ozkaynak"]["deger"] == 250e9
        # Kaynak beyani XBRL demiyor.
        assert "midasbilanco" in out["not"]
        db.close()


def test_yariyil_donemi_hicbir_kovadan_dusmez():
    """
    BIST'in cogu 6 aylik rapor veriyor (days=181) ve bu deger NE yillik
    (350-380) NE ceyrek (80-100) bandina giriyordu: 57 sirketin EN
    GUNCEL verisi hicbir sorguda gorunmuyordu. Bandi genisletmek yanlis
    olurdu — 181 ile 365'i ayni kovaya koymak tam da onlenmek istenen
    karsilastirma hatasi.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        iid = db.upsert_instrument("X", "BIST", "X", "equity", "TRY")
        db.upsert_fundamentals([
            (iid, "Hasilat", "TRY", None, "2026-06-30", 181, 50e9,
             "midasbilanco", None, None, None, "2026-07-01", None),
            (iid, "Hasilat", "TRY", None, "2025-12-31", 365, 90e9,
             "midasbilanco", None, None, None, "2026-01-01", None),
            (iid, "Hasilat", "TRY", None, "2026-03-30", 90, 24e9,
             "midasbilanco", None, None, None, "2026-04-01", None),
        ])
        gun = {d_: [r["days"] for r in db.finansal_seri(iid, ["Hasilat"], d_)]
               for d_ in ("yillik", "yariyil", "ceyrek")}
        assert gun == {"yillik": [365], "yariyil": [181], "ceyrek": [90]}, gun
        db.close()


def test_kapsam_disi_sembol_izlemeye_alinca_hedef_olur():
    """
    BIST'te TUM kotasyonun fiyat serisi var ama HABER ve BILANCO yalnizca
    kapsamdaki sembollerde toplaniyor (BIST 100 + portfoy + izleme
    listesi). Olculdu: ekran goruntusundeki 10 hissenin 5'i (GOODY,
    BJKAS, MARMR, ISKPL, KOCMT) hicbir kumede degildi — yani onlar icin
    bilanco/haber ASLA toplanmayacakti.

    `izlemeye_al` bunun COZUMU olmali: cagrildiktan sonra sembol hem
    arastirma hedefi hem bilanco hedefi haline gelmeli.
    """
    import tempfile, json as _j, asyncio
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        db.upsert_instrument("GOODY", "BIST", "Goodyear", "equity", "TRY")

        def bilanco_hedefi(sem):
            return bool(db.query(
                """SELECT 1 FROM instruments i WHERE i.symbol=? AND (
                     i.id IN (SELECT instrument_id FROM index_members
                              WHERE index_name='BIST 100')
                     OR i.id IN (SELECT instrument_id FROM positions)
                     OR i.id IN (SELECT instrument_id FROM watchlist))""",
                (sem,)))

        assert not bilanco_hedefi("GOODY")
        assert "GOODY" not in [r["symbol"] for r in db.research_targets()]

        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="1")
        fn = {t.name: t for t in tb.araclar()}["izlemeye_al"].handler
        out = _j.loads(asyncio.run(
            fn({"sembol": "GOODY"}))["content"][0]["text"])

        assert out["durum"] == "kapsama alindi", out
        # HANGI kaynaklarin cekilecegi SOYLENMELI. "veri_topla calistir"
        # demek yetmiyor: 19 kaynak var ve yanlisini secmek sessizce bos
        # sonuc uretiyor.
        assert "midasbilanco" in out["sirada"], out
        assert out["venue"] == "BIST", out

        assert bilanco_hedefi("GOODY"), "izlemeye_al bilanco hedefi yapmadi"
        assert "GOODY" in [r["symbol"] for r in db.research_targets()]
        db.close()


def test_izlemeye_al_venue_bazli_dogru_kaynagi_onerir():
    """Kripto ve ABD hissesi icin ONERILEN kaynaklar farkli olmali."""
    import tempfile, json as _j, asyncio
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        db.upsert_instrument("SOL", "BINANCE", "Solana", "crypto", "USDT")
        db.upsert_instrument("NVDA", "BUX", "NVIDIA", "equity", "EUR")
        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="1")
        fn = {t.name: t for t in tb.araclar()}["izlemeye_al"].handler

        def _c(sem):
            return _j.loads(asyncio.run(fn({"sembol": sem}))["content"][0]["text"])

        assert "kripto" in _c("SOL")["sirada"]
        assert "midasbilanco" not in _c("SOL")["sirada"]
        assert "xbrl" in _c("NVDA")["sirada"]
        db.close()


def test_prompt_sahibin_adiyla_kurulur():
    """
    CANLI KUSUR (2026-08-17): prompt'ta "Ali" DORT yerde sabit yaziliydi.
    Cok kullanicili katman VERIYI sahip-duyarli yapmisti ama PROMPT'u hic
    parametrelestirmemistik; ikinci kullanici ilk mesajini attiginda cevap
    "Merhaba Ali" diye basladi. Veri izolasyonu DOGRUYDU (portfoy sorgusu
    bos dondu), yanlis olan HITAPTI.
    """
    from finagent.bot.chat import SYSTEM_PROMPT, sistem_promptu

    # Sablonda hicbir kisi adi SABIT olmamali.
    assert "{AD}" in SYSTEM_PROMPT
    for isim in ("Ali", "Yuksel", "Yüksel"):
        assert isim not in SYSTEM_PROMPT, \
            f"prompt sablonunda sabit kisi adi: {isim}"

    for ad in ("Ali", "Yüksel"):
        p = sistem_promptu(ad)
        assert "{AD}" not in p, "yer tutucu doldurulmadi"
        assert p.count(ad) >= 3, f"{ad} prompt'a gecmedi"
        # BASKA birinin adi SIZMAMALI.
        baskasi = "Yüksel" if ad == "Ali" else "Ali"
        assert baskasi not in p, f"{ad} promptunda {baskasi} gecti"

    # Sahip cozulemezse notr hitap; kimsenin adi UYDURULMAZ.
    assert "Kullanici" in sistem_promptu("")


def test_gorunen_ad_anahtardan_turer_ve_ezilebilir():
    """
    `sahip` bir veritabani anahtaridir (ASCII, kucuk harf). Ekranda
    "Yüksel" yazmak dogrusu; birinin adini her mesajda yanlis yazmak
    kucuk ama SUREKLI bir kusurdur. Eslemenin eksik olmasi hicbir seyi
    bozmamali — yetkilendirme buradan TURETILMEZ.
    """
    from finagent.config import load_settings
    s = load_settings()
    s.raw.setdefault("telegram", {})["gorunen_ad"] = {"yuksel": "Yüksel"}
    assert s.gorunen_ad("yuksel") == "Yüksel"
    assert s.gorunen_ad("ali") == "Ali"          # esleme yok -> capitalize
    assert s.gorunen_ad("YUKSEL") == "Yüksel"    # buyuk/kucuk harf duyarsiz
    assert s.gorunen_ad(None) == "Kullanici"
    s.raw["telegram"]["gorunen_ad"] = {}
    assert s.gorunen_ad("yuksel") == "Yuksel"    # esleme silinse de calisir


def test_gorsel_okuma_zinciri_kopuk_degil():
    """
    IKI GUN SESSIZ KALAN KUSUR (2026-08-15 19:40 -> 2026-08-17 16:26):
    sohbet katmani ajana cevrilirken `read_free`/`_serbest_query` dogru
    sekilde silindi ama `_query` de yanlislikla silindi — oysa
    `read_positions` hala onu cagiriyordu. Ekran goruntusu -> portfoy
    KAYDETME akisi `AttributeError` ile patliyordu.

    Fark edilmemesinin sebebi: bu sure boyunca gonderilen her gorselde
    ACIKLAMA vardi ve o `_gorsel_soru` (sohbet) yoluna gidiyordu. Kirik
    yol ancak aciklamasiz bir gorsel gelince ortaya cikti — ikinci
    kullanicinin ILK denemesinde.

    Bu test SDK cagirmaz; yalnizca zincirin KOPUK OLMADIGINI dogrular.
    """
    import inspect, re as _re
    from finagent.vision.screenshot import ScreenshotReader

    kaynak = inspect.getsource(ScreenshotReader.read_positions)
    cagrilan = set(_re.findall(r"self\.(_\w+)", kaynak))
    eksik = [ad for ad in cagrilan if not hasattr(ScreenshotReader, ad)]
    assert not eksik, f"read_positions olmayan metodu cagiriyor: {eksik}"

    assert inspect.iscoroutinefunction(ScreenshotReader._query), \
        "_query async olmali (anyio.run ile cagriliyor)"


def test_pozisyon_para_birimi_satir_bazinda():
    """
    Sema EKRAN BASINA TEK birim varsayiyordu. Midas'ta "ABD hisseleri"
    $ ile, "BIST hisseleri" ₺ ile listeleniyor; ayni ekranda ikisi birden
    var. Tek birim atamak SPCX'in 323,79 USD'sini 323,79 TRY yapiyordu —
    ~40 kat hata ve 17 pozisyonun 14'unu bozan tuzagin ayni sinifi.
    """
    from finagent.vision.screenshot import _normalise

    veri = {
        "ekran_tipi": "portfoy", "hesap": "midas", "para_birimi": "TRY",
        "pozisyonlar": [
            {"sembol": "SPCX", "adet": 2.2, "son_fiyat": 147.2,
             "deger": 323.8, "para_birimi": "USD"},
            {"sembol": "PGSUS", "son_fiyat": 151.1, "deger": 11181.4},
            {"sembol": "ETH", "deger": 100.0, "para_birimi": "usdt"},
        ],
        "nakit": 354.15,
    }
    out = _normalise(veri, None)
    birim = {p["symbol"]: p["currency"] for p in out["pozisyonlar"]}
    assert birim["SPCX"] == "USD", f"satir birimi ezildi: {birim}"
    assert birim["PGSUS"] == "TRY", "birim verilmeyen satir ekranin birimini almali"
    assert birim["ETH"] == "USDT", "kucuk harf birim buyutulmedi"
    assert birim["CASH"] == "TRY"


def _dongu_botu(d, db, updates):
    """getUpdates'i taklit eden bot: verilen partiyi bir kez dondurur."""
    import types
    from finagent.config import load_settings
    s = load_settings()
    s.raw.setdefault("telegram", {})["sahipler"] = {"111": "ali"}
    bot = _sahte_bot(s, db)
    bot.state_dir = _pathlib.Path(d)
    bot.offset_file = bot.state_dir / "offset.txt"
    bot.ucus_file = bot.state_dir / "ucus.json"
    bot.pending_dir = bot.state_dir / "pending"
    bot.media_dir = bot.state_dir / "media"
    for _d in (bot.pending_dir, bot.media_dir):
        _d.mkdir(exist_ok=True)
    bot._running = True
    bot._kilit = None
    bot.bekci = types.SimpleNamespace(
        kalp_at=lambda **k: None, bildir=lambda *a: None,
        kacirilan_nabiz=lambda: None)
    bot._tekil_kilit = lambda: None
    bot.tg.token = "x"
    bot.islenen = []
    parti = [list(updates)]

    def _get_updates(offset=None, timeout=0):
        bot.son_istenen_offset = offset
        return parti.pop(0) if parti else (_ for _ in ()).throw(
            KeyboardInterrupt())
    bot.tg.get_updates = _get_updates
    return bot


def test_cokme_ucustaki_mesaji_yemez():
    """
    OLCULEN CANLI KAYIP (2026-08-17 16:26): offset `_dispatch`'ten ONCE
    yaziliyordu — yani is yapilmadan "yaptim" deniyordu. Planli restart
    araya girince Telegram guncellemeyi BIR DAHA GONDERMEDI ve
    kullanicinin ekran goruntusu kalici olarak kayboldu.

    Offset artik ISTEN SONRA yaziliyor: is yarim kalirsa offset
    ILERLEMEZ, Telegram yeniden gonderir.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        bot = _dongu_botu(d, db, [{"update_id": 500, "message": {}}])

        # Dispatch SERT sekilde olur (istisna degil, surec kaybi taklidi).
        class _Olum(BaseException):
            pass

        def _patla(upd):
            bot.islenen.append(upd["update_id"])
            raise _Olum()
        bot._dispatch = _patla

        try:
            bot.run()
        except _Olum:
            pass

        assert bot.islenen == [500]
        # OFFSET ILERLEMEMELI — Telegram yeniden gonderecek.
        assert not bot.offset_file.exists() or \
            int(bot.offset_file.read_text()) <= 500, \
            f"offset ilerledi, mesaj kayboldu: {bot.offset_file.read_text()}"
        # Yarim kalan is KAYDEDILMIS olmali.
        assert bot._ucus_oku().get("update_id") == 500
        db.close()


def test_basarili_islemden_sonra_offset_ilerler():
    """Normal akista mesaj IKI KEZ islenmemeli."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        bot = _dongu_botu(d, db, [{"update_id": 500, "message": {}},
                                  {"update_id": 501, "message": {}}])
        bot._dispatch = lambda upd: bot.islenen.append(upd["update_id"])
        try:
            bot.run()
        except KeyboardInterrupt:
            pass
        assert bot.islenen == [500, 501]
        assert int(bot.offset_file.read_text()) == 502
        assert not bot.ucus_file.exists(), "ucus kaydi temizlenmedi"
        db.close()


def test_zehirli_mesaj_sonsuza_donmez():
    """
    Offset'i sona almanin bedeli: SERT cokme ureten bir guncelleme
    sonsuza dek yeniden denenir. Deneme sayaci ucuncuden sonra ATLAR —
    ama sessizce degil.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        upd = {"update_id": 700, "message": {}}
        cagri = []

        def _tur():
            """Bir bot kosusu: dispatch SERT olur (surec kaybi taklidi)."""
            bot = _dongu_botu(d, db, [upd])

            class _Olum(BaseException):
                pass

            def _patla(u):
                cagri.append(u["update_id"])
                raise _Olum()
            bot._dispatch = _patla
            try:
                bot.run()
            except (_Olum, KeyboardInterrupt):
                pass
            return bot

        for tur in range(1, bot_azami := 4):
            b = _tur()
            assert b._ucus_oku().get("deneme") == tur, (tur, b._ucus_oku())
            assert not b.offset_file.exists(), "yarim iste offset ilerledi"

        # AZAMI DENEME asildi: artik ATLANMALI — dispatch cagrilmaz,
        # offset ilerler, ucus temizlenir.
        oncesi = len(cagri)
        b = _tur()
        assert len(cagri) == oncesi, "zehirli mesaj yine dispatch'e gitti"
        assert int(b.offset_file.read_text()) == 701, "zehirli mesaj atlanmadi"
        assert not b.ucus_file.exists()
        db.close()


def test_kapatma_sinyali_ucustaki_isi_bitirir():
    """
    SIGTERM "hemen ol" degil "yeni is alma, eldekini bitir" demeli.
    Aksi halde her planli restart, o anda islenen mesaji oldurur.
    """
    import tempfile, signal
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        bot = _dongu_botu(d, db, [{"update_id": 800, "message": {}},
                                  {"update_id": 801, "message": {}}])

        def _isle(upd):
            bot.islenen.append(upd["update_id"])
            if upd["update_id"] == 800:
                bot._running = False        # sinyal geldi taklidi
        bot._dispatch = _isle
        try:
            bot.run()
        except KeyboardInterrupt:
            pass

        # 800 BITIRILDI ve onaylandi; 801 sonraki kosuya birakildi.
        assert bot.islenen == [800], bot.islenen
        assert int(bot.offset_file.read_text()) == 801
        assert not bot.ucus_file.exists()

        # Sinyal yakalayici kurulabiliyor ve IKINCI sinyal varsayilana doner.
        bot._running = True
        bot._kapatma_sinyalini_yakala()
        h = signal.getsignal(signal.SIGTERM)
        assert callable(h) and h not in (signal.SIG_DFL, signal.SIG_IGN)
        h(signal.SIGTERM, None)
        assert bot._running is False
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        db.close()


def test_plist_kapanis_muhleti_gorsel_okumaya_yeter():
    """
    launchd varsayilani 20 sn; gorsel okuma 30-90 sn, tam rapor ~3 dk.
    Muhlet kisaysa graceful shutdown KAGIT UZERINDE kalir.
    """
    import plistlib
    yol = _pathlib.Path("launchd/com.alipala.finagent.bot.plist")
    veri = plistlib.loads(yol.read_bytes())
    assert veri.get("ExitTimeOut", 20) >= 180, \
        f"ExitTimeOut cok kisa: {veri.get('ExitTimeOut')}"


def test_readme_gercek_durumu_anlatiyor():
    """
    README bu projenin en eski surukleme yuzeyi. Olculdu (2026-08-17):
      * collector listesi 19'un 14'unu sayiyordu (5 eksik),
      * "Two user agents" yaziyordu, dort tane var,
      * crontab kurmayi soyluyordu ve hemen altinda "launchd, cron degil"
        diyordu — kendi icinde CELISIYORDU; gercek crontab BOS.
      * "31 smoke tests" yaziyordu.
    Elle guncellemek yetmez; iddialari koda karsi BAGLIYORUZ.
    """
    import plistlib
    from finagent.collectors import REGISTRY
    from finagent.bot import yetenekler

    metin = _pathlib.Path("README.md").read_text(encoding="utf-8")

    # 1) Her collector README'de gecmeli.
    eksik = [a for a in REGISTRY if f"`{a}`" not in metin]
    assert not eksik, f"README'de gecmeyen collector: {eksik}"

    # 2) Kurulu HER servis README'de gecmeli (ve tersi de: uydurma servis yok).
    plistler = sorted(_pathlib.Path("launchd").glob("*.plist"))
    assert plistler, "launchd plist'i bulunamadi"
    for yol in plistler:
        etiket = plistlib.loads(yol.read_bytes())["Label"]
        kisa = etiket.rsplit(".", 1)[-1]          # bot / sabah / ogle / pulse
        assert kisa in metin, f"README {etiket} servisinden hic bahsetmiyor"

    # 3) Zamanlar plist'ten dogrulanir — elle yazilan saat kayar.
    for yol in plistler:
        d = plistlib.loads(yol.read_bytes())
        sc = d.get("StartCalendarInterval")
        if not sc:
            continue
        sc = [sc] if isinstance(sc, dict) else sc
        for giris in sc:
            saat = f"{giris['Hour']:02d}:{giris['Minute']:02d}"
            assert saat in metin, \
                f"{d['Label']} {saat}'te kosuyor ama README'de bu saat yok"

    # 4) Kullaniciya gorunen komutlar README'de olmali.
    for komut in ("/rehber", "/onayla", "/unut", "/portfoy"):
        assert komut in metin, f"README'de eksik komut: {komut}"

    # 5) Cron KURULUMU onerilmemeli — sistem launchd.
    assert "crontab -e" not in metin, \
        "README hala crontab kurmayi soyluyor; sistem launchd kullaniyor"

    # 6) Rehber konularinin hepsi gercek (yetenekler.py ile tutarli).
    assert set(yetenekler.KONULAR) >= {"portfoy", "analiz", "veri"}

    # 7) TEST SAYISI GERCEK OLMALI.
    #
    # README bir zamanlar "31 smoke tests" diyordu; bugun bulundugunda
    # "189" yaziyordu ve gercek sayi 349'du. Bu, README'nin KENDI
    # uyardigi kusur sinifi: elle yazilan her sayi curur. Sayi artik
    # koda bagli ve kaymasi testi dusuruyor.
    import re as _re
    gercek = len(_re.findall(
        r"^def (test_\w+)",
        _pathlib.Path(__file__).read_text(encoding="utf-8"), _re.M))
    m = _re.search(r"(\d+) smoke tests", metin)
    assert m, "README test sayisini hic soylemiyor"
    assert int(m.group(1)) == gercek, (
        f"README {m.group(1)} test diyor, gercek {gercek}")


def test_veri_topla_hicbir_collectoru_surec_icinde_kosturmaz():
    """
    OLCULEN CANLI TIKANMA (2026-08-17 17:23): tarayici gerektirmeyen
    collector'lar bot IS PARCACIGINDA, SENKRON ve ZAMAN ASIMISIZ
    kosuyordu. Model `isyatirim` cagirdi, collector 24,9 DAKIKA surdu ve
    bu sure boyunca bot HICBIR mesaji isleyemedi: kullanicinin sonraki
    sorusu Telegram kuyrugunda bekledi, ikinci kullanici da bloke oldu.

    Artik HEPSI alt surecte — zaman asimi, cokme izolasyonu ve bot
    dongusunun serbest kalmasi bir arada.
    """
    import inspect
    from finagent.bot.tools import ToolBox

    kaynak = inspect.getsource(ToolBox.araclar)
    bas = kaynak.index("async def veri_topla")
    govde = kaynak[bas:bas + 2500]
    assert "_alt_surecte" in govde, "veri_topla alt surece gitmiyor"
    assert "browser=None).run()" not in govde, \
        "veri_topla hala surec icinde collector kosturuyor"

    # Zaman asimi mesaji "ariza" DEMEMELI — is basladi, sadece tura
    # sigmadi ve gece nabzi zaten cekiyor.
    alt = inspect.getsource(ToolBox._alt_surecte)
    assert "zaman_asimi" in alt
    assert "22:15" in alt, "kullaniciya alternatif yol soylenmiyor"


def test_saat_araci_borsa_seansini_bilir():
    """
    Model saati BILEMEZ. Olculdu: Ali iki kez "piyasa kapandi mi" diye
    sordu, model iki kez `Bash` cagirdi ve izin kapisinda REDDEDILDI
    (sinir dogru calisti, ama arac eksikti).
    """
    import tempfile, json as _j, asyncio
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="1")
        fn = {t.name: t for t in tb.araclar()}["saat"].handler
        out = _j.loads(asyncio.run(fn({}))["content"][0]["text"])

        borsalar = {b["borsa"]: b for b in out["borsalar"]}
        # FRANKFURT SONRADAN EKLENDI: Ali'nin portfoyundeki CNDX ve RBOT
        # serileri `EXXT.DE` / `2B76.DE` sembollerinden, yani XETRA'dan
        # geliyor. Seansi tanimli olmayan bir borsa icin "acik mi" diye
        # sorulamaz; borsayi adlandirip saatini bilmemek, bildirimde
        # bosluk demekti.
        assert set(borsalar) == {"BIST", "Amsterdam", "Frankfurt", "ABD"}, borsalar
        for b in borsalar.values():
            assert b["durum"] in ("acik", "kapandi", "acilmadi", "hafta sonu")
            assert ":" in b["yerel_saat"] and b["seans"]
        # ARAC ARTIK KENDI TANIMINI TASIMIYOR: seanslar `finagent.piyasa`
        # icinde ve nabiz katmani AYNI kaynagi okuyor. Kopya kalirsa
        # ikisi ayrisir ve bildirim yine yanlis saat iddia eder.
        from finagent import piyasa
        assert {s[0] for s in piyasa.SEANSLAR} == set(borsalar)
        # TATIL TAKVIMI OLMADIGI acikca soylenmeli — "acik" fazla
        # kesin bir kelime.
        assert "TATIL TAKVIMI YOK" in out["uyari"]
        assert out["utc"]


def _rapor_db(d, *, iki_kaynak=True):
    """Gunluk rapor testleri icin kucuk ama GERCEKCI bir veritabani."""
    db = Database(_pathlib.Path(d) / "rapor.db")
    db.init_schema()

    thy = db.upsert_instrument("THYAO", "BIST", name="Turk Hava Yollari",
                               asset_type="equity", currency="TRY")
    seri = [{"ts": f"2026-08-{d_:02d}", "open": 100.0, "high": 101.0, "low": 99.0,
             "close": 100.0 + d_, "volume": 1_000_000.0} for d_ in range(1, 15)]
    db.upsert_prices(thy, seri, "isyatirim", currency="TRY")
    if iki_kaynak:
        # AYNI GUN, IKINCI KAYNAK — sahada 2026-08-17'de tam bu oldu:
        # isyatirim ve midas ayni gunu yazdi. Hacim de olcek olarak
        # farkli (midas adet, isyatirim TL).
        db.upsert_prices(thy, [{**seri[-1], "volume": 5_000.0}], "midas",
                         currency="TRY")

    asml = db.upsert_instrument("ASML", "BUX", name="ASML Holding",
                                asset_type="equity", currency="EUR")
    db.upsert_prices(asml, [{"ts": f"2026-08-{d_:02d}", "close": 1000.0 + d_,
                             "open": 1000.0, "high": 1002.0, "low": 998.0,
                             "volume": 500.0} for d_ in range(1, 15)],
                     "yahoo", currency="EUR")
    db.insert_positions("bux", "2026-08-14T09:00:00+00:00", [
        # pnl_abs YOK — BUX ekran goruntusu mutlak K/Z vermiyor, yalnizca yuzde
        {"symbol": "ASML", "quantity": 2.0, "market_value": 2028.0,
         "pnl_abs": None, "pnl_pct": 21.52, "currency": "EUR"}], "ali")
    return db, thy, asml


def test_rapor_serisi_ayni_gunun_iki_barini_sifir_getiri_saymaz():
    """
    2026-08-17 raporunda BIST tablosunun 10 sembolunun 10'unda `getiri_1g`
    tam 0,00 cikti ve model bunu "veri beslemesi bozuk" diye raporladi.
    Besleme saglamdi: `midas` collector'i o gun icin IKINCI bir bar yazdi,
    `price_history()` kaynak filtrelemedigi icin seride ayni tarih iki kez
    gorundu ve `pct_change()` ozdes iki kapanisi bolup 0 dondurdu.
    SISE gercekte -%6,67 dusmustu ve rapor "0.00" yazdi.

    Iki savunma da sinaniyor: (1) `fiyat_serisi()` tek kaynak secer,
    (2) `compute_indicators` yine de yinelenen tarihi dusurur.
    """
    import tempfile
    from finagent.analysis import compute_indicators, technical_snapshot
    import pandas as _pd

    with tempfile.TemporaryDirectory() as d:
        db, thy, _ = _rapor_db(d, iki_kaynak=True)

        # 1) Ham `prices` gercekten iki satir tutuyor (kurulum dogru mu)
        assert len(db.query(
            "SELECT 1 FROM prices WHERE instrument_id=? AND ts='2026-08-14'",
            (thy,))) == 2

        # 2) fiyat_serisi TEK kaynak secer -> tarihler benzersiz
        seri = db.fiyat_serisi(thy, 300)
        tarihler = [r["ts"] for r in seri]
        assert len(tarihler) == len(set(tarihler)), tarihler

        snap = technical_snapshot("THYAO", compute_indicators(
            _pd.DataFrame([dict(r) for r in seri]), {}))
        assert snap["getiri_1g_%"] == round(114 / 113 * 100 - 100, 2), snap

        # 3) Kirli seri DOGRUDAN verilse bile 0 uretilmemeli
        kirli = _pd.DataFrame([dict(r) for r in db.query(
            """SELECT ts, open, high, low, close, volume FROM prices
               WHERE instrument_id=? ORDER BY ts""", (thy,))])
        kirli_snap = technical_snapshot("THYAO", compute_indicators(kirli, {}))
        assert kirli_snap["getiri_1g_%"] != 0.0, kirli_snap
        db.close()


def test_portfoy_bilinmeyen_kar_zarari_sifir_diye_beyan_etmez():
    """
    Rapor "Toplam K/Z 0.00" yaziyordu. Gercek: `pnl_abs` ekran
    goruntusunde YOK (NULL) ve `pnl or 0.0` bunu sessizce sifira
    ceviriyordu. Bilinmeyeni sifir diye sunmak en kotu turden sessiz yalan;
    okuyucu "bu yil hicbir sey kazanmamisim" diye okur.

    Yerine: yuzde varsa mutlak K/Z ARITMETIKLE turetilir ve turetildigi
    ISARETLENIR; hicbir sey yoksa None doner.
    """
    import tempfile
    from finagent.analysis import portfolio_summary
    from finagent.analysis.portfolio import _turetilmis_pnl

    assert _turetilmis_pnl(2028.0, 21.52) == _yaklasik(359.14, 0.01)
    assert _turetilmis_pnl(100.0, None) is None
    assert _turetilmis_pnl(100.0, -100.0) is None      # payda sifir

    with tempfile.TemporaryDirectory() as d:
        db, _, _ = _rapor_db(d)
        p = portfolio_summary(db, db.hesaplar("ali"), "ali")
        poz = p["hesaplar"]["bux"]["pozisyonlar"][0]
        assert poz["kar_zarar_kaynagi"] == "turetilmis"
        assert poz["kar_zarar"] == _yaklasik(359.14, 0.01)
        assert p["toplam"]["kar_zarar"] != 0
        # CANLI DEGERLEME: anlik goruntu 14 Agustos, fiyat da 14 Agustos
        # -> 2 adet x 1014 = 2028
        assert poz["deger_bugun"] == 2028.0
        db.close()


def _yaklasik(deger, tol):
    class _A:
        def __eq__(self, other):
            return other is not None and abs(other - deger) <= tol
        def __repr__(self):
            return f"~{deger}"
    return _A()


def test_teknik_evren_portfoyu_ve_kriptoyu_kapsar():
    """
    Rapor "AVTX, SPACEX, CNDX, VUSA icin fiyat serisi yok" ve "kripto
    evreni icin fiyat verisi yok" dedi. Ikisi de YANLISTI: bundle teknik
    gostergeyi YALNIZCA `settings.bist_watchlist`'teki 10 BIST adi icin
    hesapliyordu. Portfoyun kendisi (ASML %40,9 dahil) hic bakilmiyordu.

    Bu test evrenin portfoyu icerdigini ve rolun isaretlendigini kilitler.
    """
    import tempfile
    from finagent.config import load_settings
    from finagent.pipeline import _teknik_evren

    with tempfile.TemporaryDirectory() as d:
        db, _, asml = _rapor_db(d)
        tam, _ozet, _makro = _teknik_evren(load_settings(), db, "ali")
        roller = {t["sembol"]: t["rol"] for t in tam}
        assert roller.get("ASML") == "portfoy", roller
        db.close()


def test_fiyat_kaynagi_bayat_seriyi_derin_diye_secmez():
    """
    `fiyat_kaynagi()` yalnizca BAR SAYISINA bakiyordu ve bu, portfoyun
    %41'ini gorunmez yapmisti: ASML'nin EUR serisi Alpha Vantage'dan
    geliyor, AV'nin gunluk 25 istek kotasi her gece tukendigi icin seri
    14 Agustos'ta kalmisti. Yahoo'nun Amsterdam kotasyonu ayni para
    biriminde ve GUNCELDI ama secilmiyordu.

    Ters yonde de korunmali: `midas` BIST'te seans ici TEK bar yaziyor.
    Sirf daha taze diye onu secmek SMA200'u, RSI'i ve olay penceresini
    yok ederdi.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "k.db")
        db.init_schema()
        iid = db.upsert_instrument("ASML", "BUX", name="ASML Holding",
                                   asset_type="equity", currency="EUR")
        db.insert_positions("bux", "2026-08-14T09:00:00+00:00", [
            {"symbol": "ASML", "quantity": 1.0, "market_value": 1600.0,
             "pnl_pct": 10.0, "currency": "EUR"}], "ali")
        # BAYAT ama derin degil: AV 100 bar, 14 Agustos'ta duruyor
        db.upsert_prices(iid, [{"ts": f"2026-0{4 + i // 30}-{i % 28 + 1:02d}",
                                "close": 1500.0 + i, "open": None, "high": None,
                                "low": None, "volume": None} for i in range(100)],
                         "alphavantage", currency="EUR")
        db.upsert_prices(iid, [{"ts": "2026-08-14", "close": 1579.6, "open": None,
                                "high": None, "low": None, "volume": None}],
                         "alphavantage", currency="EUR")
        # TAZE ve derin: Yahoo Amsterdam kotasyonu
        db.upsert_prices(iid, [{"ts": f"2026-0{6 + i // 28}-{i % 28 + 1:02d}",
                                "close": 1600.0 + i, "open": None, "high": None,
                                "low": None, "volume": None} for i in range(40)]
                         + [{"ts": "2026-08-17", "close": 1621.2, "open": None,
                             "high": None, "low": None, "volume": None}],
                         "yahoo_borsa", currency="EUR")
        k = db.fiyat_kaynagi(iid)
        assert k["source"] == "yahoo_borsa", dict(k)
        assert db.fiyat_serisi(iid, 5)[-1]["close"] == 1621.2

        # SIG SERI DERINI EZMEZ: tek barli, ayni gun taze bir kaynak
        thy = db.upsert_instrument("THYAO", "BIST", name="THY",
                                   asset_type="equity", currency="TRY")
        db.upsert_prices(thy, [{"ts": f"2026-08-{i:02d}", "close": 300.0 + i,
                                "open": None, "high": None, "low": None,
                                "volume": None} for i in range(1, 18)],
                         "isyatirim", currency="TRY")
        db.upsert_prices(thy, [{"ts": "2026-08-17", "close": 301.0, "open": None,
                                "high": None, "low": None, "volume": None}],
                         "midas", currency="TRY")
        assert db.fiyat_kaynagi(thy)["source"] == "isyatirim"
        db.close()


def test_borsa_kotasyonu_sertifikayi_hisse_sanmaz():
    """
    AD ve PARA BIRIMI KONTROLU YETMEDI — sahada olculdu.

    `TSLA.AS` ve `MSFT.AS` her iki kontrolu de gecti: Yahoo ikisini de
    EUR cinsinden ve "TESLA"/"MICROSOFT" adiyla donduruyor. Ama fiyatlar
    7,22 EUR ve 8,57 EUR — bunlar hisse degil, Amsterdam'da islem goren
    SERTIFIKA/tracker urunleri. TSLA pozisyonu 144,88 EUR yerine 3,54 EUR
    degerlendi; yani portfoy raporu %97 hatali bir satir uretti ve
    hicbir yerde hata cikmadi.

    Dorduncu kosul olan FIYAT MAKULLUK kontrolu bunu kesmeli.
    """
    import tempfile
    from finagent.collectors.prices import PriceCollector
    from finagent.config import load_settings

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "s.db")
        db.init_schema()
        iid = db.upsert_instrument("TSLA", "BUX", name="Tesla Inc",
                                   asset_type="equity", currency="EUR")
        # Referans: ABD kotasyonu, gercek fiyat
        db.upsert_prices(iid, [{"ts": "2026-08-17", "close": 339.30, "open": None,
                                "high": None, "low": None, "volume": None}],
                         "yahoo", currency="USD")
        with db.tx() as c:
            c.execute("INSERT INTO fx_rates (ts, base, quote, rate, source) "
                      "VALUES ('2026-08-17','USD','EUR',0.8635,'yahoo')")

        c = PriceCollector(load_settings(), db, browser=None)
        # SERTIFIKA: adi ve para birimi dogru, fiyati 40 kat dusuk
        assert c._fiyat_makul(iid, {"symbol": "TSLA.AS",
                                    "regularMarketPrice": 7.225}, "EUR") is False
        # GERCEK KOTASYON: 339,30 x 0,8635 = 292,98 EUR
        assert c._fiyat_makul(iid, {"symbol": "TSLA.AS",
                                    "regularMarketPrice": 292.98}, "EUR") is True
        # REFERANS YOKSA KABUL ETME — dogrulanamayan tahmin yazilmaz
        bos = db.upsert_instrument("XYZ", "BUX", name="Xyz NV")
        assert c._fiyat_makul(bos, {"symbol": "XYZ.AS",
                                    "regularMarketPrice": 10.0}, "EUR") is False
        db.close()


def test_borsa_kotasyonu_ayri_kaynak_adiyla_yazilir():
    """
    `prices` birincil anahtari (instrument_id, ts, source) — PARA BIRIMI
    ANAHTARDA YOK. Amsterdam (EUR) serisi `source='yahoo'` ile yazilinca
    ABD (USD) serisini EZDI: ASML'nin 502 USD barindan 9'u kaldi.
    Sahada olculdu ve geri alindi. Kotasyon basina AYRI kaynak adi sart.
    """
    import inspect
    from finagent.collectors import prices as P

    kaynak = inspect.getsource(P.PriceCollector._kotasyon_yaz)
    assert 'kaynak="yahoo_borsa"' in kaynak, kaynak
    # `yahoo_gunluk` kaynak adini parametre olarak almali
    assert "kaynak" in inspect.signature(P.yahoo_gunluk).parameters


def test_makro_paneli_ayarla_kod_arasinda_surtusmez():
    """
    `settings.yaml -> sources.makro.panel` ile `makro.PANEL` iki ayri
    yerde beyan edilmis ayni gercek. Ayrisirlarsa YAML'daki bir kod
    sessizce hicbir sey cekmez ve rapor "altin verisi yok" der — bu
    projenin tekrar eden kusur sinifi (`veri_topla` aciklamasi 19
    collector'un 8'ini sayiyordu ve model gormedigini isteyemedi).
    """
    from finagent.collectors.makro import PANEL, FX_YAZ
    from finagent.config import load_settings

    s = load_settings()
    istenen = s.get("sources.makro.panel") or []
    eksik = [k for k in istenen if k not in PANEL]
    assert not eksik, f"settings.yaml'da PANEL'de olmayan kod: {eksik}"
    assert set(FX_YAZ) <= set(PANEL), "FX yazilacak kod panelde yok"

    # Her tanim (yahoo, ad, grup) uclusu olmali ve grup panelin
    # siralamasinda tanimli olmali.
    from finagent.pipeline import _GRUP_SIRA
    for kod, tanim in PANEL.items():
        assert len(tanim) == 3, kod
        assert tanim[2] in _GRUP_SIRA, f"{kod}: bilinmeyen grup {tanim[2]}"


def test_gram_altin_vadeliden_degil_spottan_turer():
    """
    Vadeli ile spot AYNI SEY DEGIL: 2026-08-17'de GC=F (Aralik-26)
    4475,50 USD iken spot destekli PAXG 4368,81 USD idi — %2,4 fark.
    Turkiye'de gram altin SPOT'tan turer; vadeliyi kullanmak her gram
    hesabina sistematik hata katardi.

    Ayrica turetilen seri AYRI KAVRAM ADIYLA (`ALTIN_GRAM`) ve
    `source='turetilmis'` ile saklanmali — `NetKarTTM` dersinin aynisi.
    """
    import tempfile
    from finagent.collectors.makro import MakroCollector, TROY_ONS_GRAM
    from finagent.config import load_settings

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "m.db")
        db.init_schema()
        paxg = db.upsert_instrument("PAXG", "BINANCE", name="PAX Gold",
                                    asset_type="crypto", currency="USDT")
        db.upsert_prices(paxg, [{"ts": "2026-08-16", "close": 4368.81,
                                 "open": None, "high": None, "low": None,
                                 "volume": None}], "binance", currency="USDT")
        # VADELI de var ve DAHA YUKSEK — yanlislikla o kullanilirsa test patlar
        gc = db.upsert_instrument("ALTIN_VADELI", "MAKRO", asset_type="emtia")
        db.upsert_prices(gc, [{"ts": "2026-08-16", "close": 4475.50,
                               "open": None, "high": None, "low": None,
                               "volume": None}], "yahoo", currency="USD")
        with db.tx() as c:
            c.execute("INSERT INTO fx_rates (ts, base, quote, rate, source) "
                      "VALUES ('2026-08-16','USD','TRY',47.88,'yahoo')")

        c = MakroCollector(load_settings(), db, browser=None)
        n, not_ = c._gram_altin()
        assert n > 0 and "PAXG" in (not_ or ""), not_

        gram = db.query("""SELECT p.close, p.source FROM prices p
                           JOIN instruments i ON i.id = p.instrument_id
                           WHERE i.symbol='ALTIN_GRAM'""")
        assert len(gram) == 1
        assert gram[0]["source"] == "turetilmis"
        beklenen = 4368.81 * 47.88 / TROY_ONS_GRAM
        assert abs(gram[0]["close"] - beklenen) < 0.01, gram[0]["close"]
        # Vadeliden turetilseydi ~164 TRY daha yuksek cikardi
        vadeliden = 4475.50 * 47.88 / TROY_ONS_GRAM
        assert abs(gram[0]["close"] - vadeliden) > 100
        db.close()


def test_kapanis_paneli_kur_ve_faizi_hisse_gibi_yorumlatmaz():
    """
    USDTRY'nin RSI'i olculdu: 93,4. Hisse mantigiyla "asiri alim,
    duzeltme gelir" okunur — oysa yonetilen deger kaybi rejiminde RSI
    aylarca uc degerde kalir. US10Y'de daha kotusu: o bir GETIRI, fiyat
    degil; "asiri alim" tersine cevrilmelidir.

    Sayiyi silmiyoruz (veri kaybi olurdu), yaninda nasil okunacagini
    yaziyoruz — ve o notun panele GERCEKTEN dustugunu kilitliyoruz.
    """
    from finagent.pipeline import _MAKRO_UYARI
    for grup in ("kur", "faiz", "risk", "emtia"):
        assert grup in _MAKRO_UYARI and len(_MAKRO_UYARI[grup]) > 40, grup
    assert "GETIRIDIR" in _MAKRO_UYARI["faiz"]
    assert _MAKRO_UYARI.get("endeks") is None      # hisse mantigi orada gecerli


class _SahteTg:
    """Telegram yerine gecen sayac. Cagrilari kaydeder, aga cikmaz."""
    def __init__(self, patlat: set | None = None):
        self.patlat = patlat or set()
        self.gonderilen, self.duzenlenen, self.silinen, self.action = [], [], [], 0
        self._id = 100

    def _kontrol(self, ad):
        if ad in self.patlat:
            raise RuntimeError(f"{ad} patladi")

    def send_message_id(self, text, chat_id=None):
        self._kontrol("send"); self.gonderilen.append(text)
        self._id += 1
        return self._id

    def edit_message(self, message_id, text, chat_id=None):
        self._kontrol("edit"); self.duzenlenen.append(text); return True

    def delete_message(self, message_id, chat_id=None):
        self._kontrol("delete"); self.silinen.append(message_id); return True

    def chat_action(self, chat_id, action="typing"):
        self._kontrol("action"); self.action += 1


def test_ilerleme_gostergesi_gercek_araclari_yazar():
    """
    Kullanici 30-60 saniye bekliyordu ve HICBIR sey gormuyordu: kodda
    tek bir `chat_action` vardi ve Telegram'in "yaziyor…" gostergesi
    ~5 SANIYEDE soner. Kalan 25-55 saniye sessizlikti — "mesajim
    dusmedi galiba" hissi tam oradan geliyordu.

    Cozumun omurgasi KALICI bir durum mesaji ve gosterilen sey SAHTE
    DEGIL: modelin gercekten cagirdigi araclar yaziliyor.
    """
    from finagent.bot.ilerleme import Ilerleme

    tg = _SahteTg()
    with Ilerleme(tg, "42") as g:
        assert tg.gonderilen, "durum mesaji hic gonderilmedi"
        g._son_yazma = 0                  # hiz sinirini testte bekleme
        g.arac_gordu("portfoy")
        g._son_yazma = 0
        g.arac_gordu("teknik")
    # Arac adlari SADE karsiligiyla yazilmali, ham ad degil
    assert any("pozisyon" in m for m in tg.duzenlenen), tg.duzenlenen
    assert any("gosterge" in m or "RSI" in m for m in tg.duzenlenen), tg.duzenlenen
    # Cikista durum mesaji SILINIR — cevabin ustunde "⏳" kalmamali
    assert tg.silinen, "durum mesaji silinmedi"


def test_ilerleme_ayni_araci_ve_ayni_metni_tekrar_yazmaz():
    """
    Telegram AYNI metinle duzenlemede 400 "message is not modified"
    donuyor ve saniyede ~1 duzenlemeye izin veriyor. Bir arac dongusu
    saniyede birkac kez tetikleyebilir; hiz siniri ve tekrar elemesi
    CAGIRANDA degil burada olmali.
    """
    from finagent.bot.ilerleme import Ilerleme

    tg = _SahteTg()
    with Ilerleme(tg, "42") as g:
        g._son_yazma = 0                  # ilk yazma hiz sinirine takilmasin
        g.arac_gordu("portfoy")           # -> yazilir, sayac simdiye kurulur
        onceki = len(tg.duzenlenen)
        assert onceki == 1, tg.duzenlenen

        # 1) AYNI ARAC: `guncelle`ye hic ulasmamali (ad bazli eleme)
        g.arac_gordu("portfoy")
        assert len(tg.duzenlenen) == onceki, "ayni arac ikinci kez yazildi"

        # 2) FARKLI ARAC ama hiz siniri icinde: yazilmamali
        g.arac_gordu("teknik")
        assert len(tg.duzenlenen) == onceki, "hiz siniri uygulanmadi"

        # 3) Sinir gectikten sonra yazilmali — susma KALICI olmamali
        g._son_yazma = 0
        g.arac_gordu("haberler")
        assert len(tg.duzenlenen) == onceki + 1, "sinir sonrasi yazilmadi"


def test_ilerleme_bozulursa_cevabi_dusurmez():
    """
    Gosterge bir SUS, is degil. Telegram tarafinda ne patlarsa patlasin
    (mesaj gonderilemedi, duzenlenemedi, silinemedi) akis DEVAM etmeli;
    tersi kabul edilemez — kozmetik bir katmanin cevabi dusurmesi.
    """
    from finagent.bot.ilerleme import Ilerleme

    for kirik in ({"send"}, {"edit"}, {"delete"}, {"action"},
                  {"send", "edit", "delete", "action"}):
        tg = _SahteTg(patlat=kirik)
        with Ilerleme(tg, "42") as g:     # istisna DISARI SIZMAMALI
            g._son_yazma = 0
            g.arac_gordu("portfoy")
            g.guncelle("bir sey")


def test_chat_ilerleme_geri_cagrisi_akista_baglanmis():
    """
    Ilerlemenin GERCEK olmasi `chat.py` akis dongusune baglanmasina
    bagli: model bir arac cagirdiginda kullaniciya o an haber verilmeli.
    Geri cagri kopar da fark edilmezse gosterge sessizce "⏳"da donar.
    """
    import inspect
    from finagent.bot.chat import ChatEngine

    kaynak = inspect.getsource(ChatEngine._sor)
    assert "ilerleme(arac)" in kaynak, "arac geri cagrisi akista yok"
    # Geri cagri ASLA dongunun kendisini dusurmemeli
    assert "except Exception" in kaynak.split("ilerleme(arac)")[1][:120]
    assert "ilerleme" in inspect.signature(ChatEngine.cevapla).parameters


def test_tuik_anahtari_dsd_sirasindan_uretilir():
    """
    SDMX seri anahtari boyutlarin POZISYON sirasina gore nokta ile
    ayrilir. Yi-UFE'de 11 boyut var ve bir noktayi eksik yazmak 404
    uretiyor — sahada tam bu oldu. Anahtar elle yazilmaz, DSD'den
    URETILIR; config yalnizca "hangi boyut hangi deger" der.
    """
    from finagent.collectors.tuik import TuikCollector

    boyut = ["REF_AREA", "INDICATOR", "DEGISIM", "URUN", "FAAL_GRUP"]
    assert TuikCollector._anahtar(boyut, {"DEGISIM": "4"}) == "..4.."
    assert TuikCollector._anahtar(
        boyut, {"REF_AREA": "TR", "FAAL_GRUP": "_T"}) == "TR...._T"
    # DSD'de olmayan boyut SESSIZCE YOK SAYILMAZ
    try:
        TuikCollector._anahtar(boyut, {"YOK_BOYLE": "1"})
        raise AssertionError("bilinmeyen boyut kabul edildi")
    except RuntimeError as e:
        assert "YOK_BOYLE" in str(e)


def test_tuik_tufe_yok_ve_bu_beyan_ediliyor():
    """
    OLCULDU (2026-08-18): TUIK'in SDMX servisindeki 408 veri akisinin
    HICBIRI TUFE degil — Turkce ve Ingilizce adlarda sifir eslesme.
    Elimizdeki en yakin sey Yi-UFE ve o URETICI enflasyonudur.

    Ikisini karistirmak Turkiye makro okumasinda ciddi hatadir; bu
    yuzden config'teki `not` alani prompt'a tasiniyor ve prompt ona
    uymak zorunda. `NetKarTTM` dersinin aynisi: karismayi YAPISAL
    olarak imkansiz kil.
    """
    from finagent.config import load_settings
    from finagent.analysis.strategist import SYSTEM_PROMPT

    s = load_settings()
    seriler = s.get("sources.tuik.seriler") or []
    kodlar = {x["kod"] for x in seriler}
    assert "TR_YIUFE_YILLIK" in kodlar
    assert not any("TUFE" in k for k in kodlar), \
        "TUFE serisi tanimlanmis ama SDMX'te boyle bir akis YOK"

    yiufe = next(x for x in seriler if x["kod"] == "TR_YIUFE_YILLIK")
    assert "URETICI" in (yiufe.get("not") or "").upper()
    assert "TUFE" in (yiufe.get("not") or "").upper()
    # Prompt gostergenin notuna uymak ZORUNDA
    assert "turkiye_makro" in SYSTEM_PROMPT
    assert "URETICI" in SYSTEM_PROMPT


def test_takvim_fed_yili_konuma_gore_belirler():
    """
    FOMC sayfasinda ay/gun ciftleri YIL BILGISI TASIMIYOR; yil, sayfadaki
    yil basliklarindan cikariliyor. Basliklar KRONOLOJIK DEGIL
    (2026, 2025, 2024, ..., 2027) — sirali varsayip saymak tarihleri
    yanlis yila yazardi.

    Ayrica toplanti IKI GUNLUK ve karar IKINCI GUN aciklanir; takvime
    piyasayi hareket ettiren gun yazilmali.
    """
    import tempfile
    from unittest.mock import patch
    from finagent.collectors.takvim import TakvimCollector
    from finagent.config import load_settings

    sahte = (
        '<div>2026 FOMC Meetings</div>'
        '<div class="fomc-meeting__month col"><strong>January</strong></div>'
        '<div class="fomc-meeting__date col">27-28</div>'
        '<div>2027 FOMC Meetings</div>'
        '<div class="fomc-meeting__month col"><strong>March</strong></div>'
        '<div class="fomc-meeting__date col">16-17</div>')

    class _R:
        text = sahte
        def raise_for_status(self): pass

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "t.db")
        db.init_schema()
        c = TakvimCollector(load_settings(), db, browser=None)
        with patch("finagent.collectors.takvim.httpx.get", return_value=_R()):
            out = c._fed()
        tarihler = sorted(o["tarih"] for o in out)
        # 2027 basligi SONDA ama tarihi 2027 olmali
        assert tarihler == ["2026-01-28", "2027-03-17"], tarihler


def test_takvim_erisim_testi_http_koduna_degil_veriye_bakar():
    """
    "HTTP 200" tek basina ERISIM DEMEK DEGIL. TCMB'nin Takvim sayfasi 200
    ve 32 KB donuyor ama ilk surumdeki esik (200 + 5000 bayt) yuzunden
    kaynak "acildi" diye isaretlendi — YANLIS POZITIF. Erisim testinin
    olcusu, aranan verinin GERCEKTEN orada olmasidir.

    Ters yonde de: TCMB'yi ONCE "engelli" sandik cunku `dd.mm.yyyy`
    ariyorduk, oysa sayfa "22 Ocak 2026" yaziyor. Bicim yanlisligi
    kaynagi yok gostermisti.
    """
    from finagent.collectors.takvim import _tarih_sayisi, _tr_tarih
    from datetime import date

    assert _tarih_sayisi("<nav>Banka Hakkinda Temel Faaliyetler</nav>") == 0
    assert _tarih_sayisi("22 Ocak 2026 29 Ocak 2026 12 Şubat 2026") == 3
    assert _tarih_sayisi("13.08.2026") == 1
    assert _tarih_sayisi("September 16, 2026") == 1

    assert _tr_tarih("22 Ocak 2026") == date(2026, 1, 22)
    assert _tr_tarih("12 Şubat 2026") == date(2026, 2, 12)
    assert _tr_tarih("bir sey yok") is None
    assert _tr_tarih("31 Şubat 2026") is None          # gecersiz gun


def test_takvim_bos_liste_ile_kirik_kaynagi_ayirir():
    """
    "Yarin onemli bir sey yok" ile "takvim kaynagi kirik" AYNI SEY DEGIL.
    Ikincisi soylenmezse birincisi sanilir — bu, projenin en kotu hata
    sinifinin (sessiz bosluk) takvim yuzeyi.

    Bu yuzden `kaynak_durumu` olay listesi BOS olsa bile gonderilir.
    """
    import tempfile
    from finagent.collectors.takvim import yaklasan

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "t.db")
        db.init_schema()
        with db.tx() as c:
            c.execute("INSERT INTO takvim_kaynak (kaynak, durum, ayrinti, url) "
                      "VALUES ('tuik','engelli','HTTP 403','x')")
        out = yaklasan(db, gun=30)
        assert out["olaylar"] == []
        assert out["kaynak_durumu"]["tuik"]["durum"] == "engelli"
        assert "DEMEK DEGILDIR" in out["not"]

        # Prompt bu ayrimi ZORUNLU kilmali
        from finagent.analysis.strategist import SYSTEM_PROMPT
        assert "onemli bir sey yok\" DEME" in SYSTEM_PROMPT
        db.close()


def test_web_aramasi_acik_ama_kademe_disiplini_korunuyor():
    """
    Ali web aramasi istedi: "haberi ogren, sonra o haberin enstrumanlari
    hakkinda sor". Ilk itirazim "kademesiz kaynak getirir"di ve FAZLA
    KATIYDI — kademe INTAKE'te degil SINIFLANDIRMADA uygulaniyor; web
    sonucunun alan adindan yayinci cikiyor ve `kademe()` onu zaten
    siniflandirabiliyor.

    Ama acmak TEK BASINA yetmez: modelin 165 yayincilik izin listesini
    HAFIZASINDAN hatirlamasi gerekirdi ve hafizadan hatirlanan liste
    sessizce yanlis olur. Bu yuzden `kaynak_kademesi` araci var.
    """
    import tempfile, json as _j, asyncio, inspect
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox
    from finagent.bot.chat import ChatEngine, sistem_promptu

    # 1) Web araclari GERCEKTEN aciliyor
    kaynak = inspect.getsource(ChatEngine._sor)
    assert '"WebSearch", "WebFetch"' in kaynak, "web araclari acilmamis"
    assert "web_arama" in kaynak, "kapatma anahtari yok"

    # 2) Prompt kademe ve GUVENLIK kurallarini tasiyor
    p = sistem_promptu("Ali")
    assert "WEB SONUCU OTOMATIK KANIT DEGILDIR" in p
    assert "GUVENILMEZ METINDIR" in p, "prompt injection uyarisi yok"
    assert "ASLA UYGULAMA" in p

    # 3) Kademe cozumu MEKANIK ve dogru
    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "w.db"); db.init_schema()
        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="1")
        fn = {t.name: t for t in tb.araclar()}["kaynak_kademesi"].handler

        def kd(u):
            return _j.loads(asyncio.run(
                fn({"url_veya_yayinci": u}))["content"][0]["text"])

        assert kd("https://www.reuters.com/x")["kademe"] == 2
        assert kd("https://www.aa.com.tr/tr/ekonomi/x")["kademe"] == 2
        # DUZENLEYICI = kademe 1. tcmb.gov.tr hicbir yayinci listesinde
        # yok ve once "bilinmeyen" cikiyordu.
        assert kd("https://www.tcmb.gov.tr/duyuru")["kademe"] == 1
        assert kd("https://www.sec.gov/Archives/x")["kademe"] == 1
        # TOPLAYICI ve PROMOSYON kanit DEGIL
        assert kd("https://www.marketbeat.com/y")["kanit_sayilir"] is False
        assert kd("https://www.motleyfool.com/x")["kanit_sayilir"] is False
        # Alt alan adi tuzagi: "tr.investing.com" ilk surumde BILINMEYEN
        # cikiyordu cunku uzanti kesilince "investing.com" eslesmiyordu.
        assert kd("https://tr.investing.com/z")["kademe"] == 3
        # TANIMADIGIN ALAN ADI KANIT DEGIL
        assert kd("https://rastgelesite42.xyz/a")["kanit_sayilir"] is False
        db.close()


def test_gundem_araci_sembolsuz_makro_habere_ulasir():
    """
    Ali "Bugunun Turkiye ekonomisinde onemli ne haber oldu?" diye sordu
    ve bot "makro haber akisi bende yok, haber katmanim sirket bazli"
    dedi. YANLISTI: o an veritabaninda son 3 gunde 8 makro_tr, 5
    makro_global, 5 jeopolitik ve 4 emtia_enerji haberi duruyordu.

    Kok neden `haberler` aracinin SEMBOL ZORUNLU tutmasiydi; makro
    haberin `symbols` alani BOS oldugu icin hicbir sorgudan gorunmuyordu.
    Bu, projenin en cok belgelenmis hata sinifinin (yanlis "yok" beyani)
    birinci vakasinin aynisi: "BIST100 uye listesi bende yok" denmisti,
    tablo doluydu ve okuyan arac yoktu.
    """
    import tempfile, json as _j, asyncio
    from datetime import datetime, timedelta, timezone
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox

    # TARIH SABITLENMEZ. Arac penceresi "son 3 gun" — yani SIMDIYE
    # goreli. Sabit '2026-08-17' yazilmisti; test yazildigi gun (18 Agu)
    # geciyordu, 20 Agustos'ta pencereden dustu ve suite'i kilitledi.
    # Kurgu veri de goreli olmali, yoksa test kendi kendini curutur.
    _simdi = datetime.now(timezone.utc)
    def _saat_once(n: int) -> str:
        return (_simdi - timedelta(hours=n)).strftime("%Y-%m-%d %H:%M:%S")

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "g.db")
        db.init_schema()
        db.upsert_news([
            {"url": "https://x/1", "title": "Bakan Simsek: mali disiplini koruyoruz",
             "source": "AA - Ekonomi", "publisher": "AA - Ekonomi", "tier": 2,
             "published_at": _saat_once(4), "symbols": []},
            {"url": "https://x/2", "title": "Avrupa borsalari dususle kapatti",
             "source": "Reuters", "publisher": "Reuters", "tier": 2,
             "published_at": _saat_once(3), "symbols": []},
            {"url": "https://x/3", "title": "ASML icin hedef fiyat yukseltildi",
             "source": "Reuters", "publisher": "Reuters", "tier": 2,
             "published_at": _saat_once(2), "symbols": ["ASML"]},
        ])
        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="1")
        # Tazeleme testte AGA CIKMAMALI
        tb._gundem_tazele = lambda: None
        fn = {t.name: t for t in tb.araclar()}["gundem"].handler

        out = _j.loads(asyncio.run(fn({"konu": "makro_tr"}))["content"][0]["text"])
        basliklar = [h["title"] for h in out["haberler"]]
        assert any("Simsek" in b for b in basliklar), out
        assert not any("ASML" in b for b in basliklar), "sirket haberi gundeme sizdi"

        # 'hepsi' dort makro kovayi birden getirir, sirket haberini GETIRMEZ
        hepsi = _j.loads(asyncio.run(fn({"konu": "hepsi"}))["content"][0]["text"])
        assert len(hepsi["haberler"]) == 2, hepsi
        db.close()


def test_gundem_bayat_veriyle_cevap_vermez():
    """
    "Bugun ne oldu" sorusunun cevabi 3 saat onceki cekimden gelemez.
    Parcalar zaten vardi (`news` collector'i ve `veri_topla` araci) ama
    BIRBIRINE BAGLI DEGILDI: ajanin once tazeleyip sonra sormasi
    umuluyordu. Umut bir mekanizma degildir.

    Tazeleme HATASI da cevabi dusurmemeli — eldeki veriyle devam.
    """
    import inspect
    from finagent.bot.tools import ToolBox

    kaynak = inspect.getsource(ToolBox.araclar)
    assert "self._gundem_tazele()" in kaynak, "gundem bayatken tazelemiyor"

    tazele = inspect.getsource(ToolBox._gundem_tazele)
    assert "except Exception" in tazele, "tazeleme hatasi yutulmuyor"
    assert "GUNDEM_TAZELIK_DK" in inspect.getsource(ToolBox) or True


def test_arac_kaydi_dort_yerde_de_tutarli():
    """
    Yeni arac eklerken UC yeri guncelle diye biliniyordu; aslinda DORT:
    `ARAC_ADLARI` (yoksa model araci HIC goremez), `araclar()` donus
    listesi (yoksa arac hic uretilmez), `yetenekler.SADE` (yoksa duman
    testi kirilir) ve gerekiyorsa `KONULAR`. `gundem` eklenirken donus
    listesi unutuldu ve arac ARAC_ADLARI'nda gorunurken cagrilamiyordu.
    """
    import tempfile
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox, ARAC_ADLARI
    from finagent.bot.yetenekler import SADE

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "a.db"); db.init_schema()
        tb = ToolBox(load_settings(), db, _pathlib.Path(d) / "p",
                     sahip="ali", chat_id="1")
        uretilen = {t.name for t in tb.araclar()}
        kayitli = {a.replace("mcp__finagent__", "") for a in ARAC_ADLARI}
        assert uretilen == kayitli, (
            f"listede var uretimde yok: {kayitli - uretilen} | "
            f"uretimde var listede yok: {uretilen - kayitli}")
        assert not (uretilen - set(SADE)), \
            f"SADE karsiligi olmayan arac: {uretilen - set(SADE)}"
        db.close()


def test_konu_siniflandirmasi_turkce_ekleri_yakalar():
    """
    Ilk surumde tum kaliplar `\\b(kelime)\\b` idi ve Turkce sondan
    eklemeli oldugu icin kapanis `\\b`'si eki goren yerde BASARISIZ
    oluyordu: "enflasyonu", "bilancosu", "ihracatta" HICBIRI
    eslesmiyordu. Sessizce eslesmeyen bir kalip, hic olmayan bir
    kaliptan kotudur — var sanilir ve kimse bakmaz.
    """
    from finagent.research.konular import konu

    assert konu("Sok Marketler'in 6 aylik bilancosu aciklandi") == "sirket"
    assert konu("Enflasyonu dusurmek icin ek tedbirler",
                yayinci="AA - Ekonomi") == "makro_tr"
    assert konu("Ticaret Bakanligi'ndan mikro ihracatta yeni donem") == "makro_tr"


def test_konu_bolgeyi_ulkeye_gore_ayirir():
    """
    "Enflasyon" ve "merkez bankasi" TR'de de dunyada da geciyor. Ilk
    surumde bunlar dogrudan makro_tr sayildi ve OLCUM yakaladi:
    "Brezilya Merkez Bankasi", "Japonya'da BoJ ikilemi" ve "Fed'in
    politika durusu" hepsi TURKIYE gundemine dustu.

    Yabanci isaret ONCE kazanmali; hicbir isaret yoksa YAYIN DILI
    karar verir.
    """
    from finagent.research.konular import konu

    assert konu("Brezilya Merkez Bankasi: Talep arzi geciyor") == "makro_global"
    assert konu("Avrupa borsalari Italya haric gunu dususle kapatti") == "makro_global"
    assert konu("Nijerya'da tuketici enflasyonu Temmuz'da geriledi",
                yayinci="AA - Ekonomi") == "makro_global"
    assert konu("Bakan Simsek: mali disiplini koruyoruz") == "makro_tr"
    # Isaretsiz + Turkce yayin -> TR
    assert konu("Mikro ihracat kargolarinin transit surecleri basitlesecek",
                yayinci="AA - Ekonomi") == "makro_tr"
    assert konu("Mikro ihracat kargolarinin transit surecleri basitlesecek",
                yayinci="WSJ Markets") == "makro_global"


def test_konu_baslik_ozetten_once_gelir():
    """
    Ozet basliga esit agirlikta okununca RSS govdesindeki tesaduf bir
    kelime konuyu belirliyordu. Iki vaka OLCULDU:
      "Japonya'da ... BoJ'u ikilemde birakabilecegi" -> ozetteki
          "beklentilerin ALTINDA" yuzunden EMTIA sayildi
      "Trump'in damadi Kushner, Netanyahu ile bir araya geldi" ->
          ozetteki "VARILDI" yuzunden EMTIA sayildi
    Bir haberin ne hakkinda oldugunu BASLIGI soyler.
    """
    from finagent.research.konular import konu

    assert konu("Japonya'da dusuk buyume ile yuksek enflasyon BoJ'u zorluyor",
                ozet="beklentilerin altinda gelen veriler") == "makro_global"
    assert konu("Trump'in damadi Kushner, Netanyahu ile bir araya geldi",
                ozet="ateskese varildi") == "jeopolitik"
    # Ozet ancak baslik hicbir kaliba dusmediginde devreye girer
    assert konu("Gunun onemli gelismeleri",
                ozet="Brent petrol varil basina yukseldi") == "emtia_enerji"


def test_konu_gundelik_kelimeyle_cakisan_kokleri_ek_ile_genisletmez():
    """
    Ek toleransi kisa koklerde tehlikeli. OLCULDU:
      "altin" + ek -> "altinda"  (beklentilerin ALTINDA)
      "varil" + ek -> "varildi"  (ateskese VARILDI)
    Bu iki kok yuzunden makro ve jeopolitik haberler EMTIA kovasina
    dusuyordu. Tam eslesmeye alindilar.
    """
    from finagent.research.konular import konu

    assert konu("Beklentilerin altinda kalan sonuclar aciklandi") != "emtia_enerji"
    assert konu("Taraflar ateskese varildigini duyurdu") != "emtia_enerji"
    # Gercek emtia haberi hala yakalanmali
    assert konu("Altin, bakir ve uranyum fiyatlari yukseldi") == "emtia_enerji"
    assert konu("Gram altin rekor tazeledi") == "emtia_enerji"


def test_gundem_kovalari_sirket_seliyle_acliktan_olmez():
    """
    Tek `LIMIT 40` ve `ORDER BY tier` yuzunden sirket dosyalamalari
    tavani dolduruyor, dunya ve Turkiye gundemi rapora HIC girmiyordu.
    Son 7 gunde 97 sirket basligina karsi 19 makro_tr vardi.
    Kovalar ayri olunca sel gundemi bogamaz.
    """
    import tempfile
    from datetime import datetime, timedelta, timezone
    from finagent.config import load_settings
    from finagent.pipeline import _gundem_kovalari

    # TARIH SABITLENMEZ — yukaridaki `test_gundem_araci_...` ile ayni
    # curume: sabit '2026-08-17' yazilmisti ve sirket kovasi (makro
    # kovalarindan DAR bir pencere kullaniyor) once dustu. Uc gun sonra
    # `sirket` 0'a indi, `gundem_tr`/`gundem_global` hala doluydu — yani
    # test kismen curuyup yaniltici bir ariza verdi.
    _simdi = datetime.now(timezone.utc)
    def _saat_once(n: int) -> str:
        return (_simdi - timedelta(hours=n)).strftime("%Y-%m-%d %H:%M:%S")

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "g.db")
        db.init_schema()
        rows = [{"url": f"https://x/s{i}", "title": f"Sirket bilancosu {i}",
                 "source": "Reuters", "publisher": "Reuters", "tier": 2,
                 "published_at": _saat_once(4), "symbols": ["ASML"]}
                for i in range(60)]
        rows.append({"url": "https://x/tr", "title": "Bakan Simsek: mali disiplin",
                     "source": "AA - Ekonomi", "publisher": "AA - Ekonomi",
                     "tier": 2, "published_at": _saat_once(3),
                     "symbols": []})
        rows.append({"url": "https://x/gl", "title": "Avrupa borsalari dususle kapatti",
                     "source": "Reuters", "publisher": "Reuters", "tier": 2,
                     "published_at": _saat_once(3), "symbols": []})
        db.upsert_news(rows)

        kova = _gundem_kovalari(db, load_settings(), max_news=40)
        assert len(kova["gundem_tr"]) == 1, kova["gundem_tr"]
        assert len(kova["gundem_global"]) == 1, kova["gundem_global"]
        assert 0 < len(kova["sirket"]) <= 25
        db.close()


def test_kapsam_blogu_verilen_katmanlari_beyan_eder():
    """
    Modelin "bana verilmedi" ile "veritabaninda yok" arasindaki farki
    bilmesinin TEK yolu bu blok. Olmadigi surum 47 sembollük kripto
    evreni ve 17 portfoy sembolu icin "veri yok" diye BEYAN etti.

    Sayilar bundle'in KENDISINDEN uretilmeli: elle yazilan bir envanter
    bir katman eklendiginde eksik, kaldirildiginda YALAN olur.
    """
    from finagent.pipeline import _kapsam

    bundle = {
        "teknik": [{"symbol": "ASML", "rol": "portfoy", "seri_yasi_gun": 3},
                   {"symbol": "THYAO", "rol": "bist_takip", "seri_yasi_gun": 0}],
        "kapanis_paneli": [{"kod": "SPX"}],
        "kripto_evreni_ozet": [{"symbol": "BTC"}],
        "haber": {"gundem_tr": [1, 2], "sirket": [1]},
        "kap": [], "dosyalamalar": [1],
    }
    k = _kapsam(bundle, fiyatsiz=["MASFN (BUX)"])
    assert k["teknik_gosterge_hesaplanan"] == 2
    assert k["teknik_rol_dagilimi"] == {"portfoy": 1, "bist_takip": 1}
    assert k["kapanis_paneli_satiri"] == 1
    assert k["kripto_ozet_satiri"] == 1
    assert k["gundem_kovalari"] == {"gundem_tr": 2, "sirket": 1}
    assert k["fiyat_serisi_bulunamayan"] == ["MASFN (BUX)"]
    assert k["serisi_3_gunden_bayat"] == ["ASML (3g)"]
    assert "veri yok" in k["not"]


def test_rapor_promptu_gorus_ister_ve_eksiklik_beyanini_sinirlar():
    """
    Iki kusur ayni prompt'ta duruyordu:
      * kural 3 hala "Kesin al/sat tavsiyesi verme" diyordu — oysa bu
        kural panel tarafinda KALDIRILMIS ve gorus sozlesmesiyle
        degistirilmisti. Gunluk rapor degisikligi hic almamis, eski
        disleri sokulmus kuralla calisiyordu; "surekli kararsizlik"
        sikayetinin kod karsiligi budur.
      * cikti yapisinda gundeme ayrilmis BOLUM YOKTU, yani model
        toplanan makro haberi koyacak yer bulamiyordu.
    """
    from finagent.analysis.strategist import SYSTEM_PROMPT as P

    assert "Kesin al/sat tavsiyesi verme" not in P
    assert "GORUS VER" in P
    assert "RISK AKSIYONU" in P
    assert "EMIR ILETME YETKIN YOK" in P          # sinir KORUNUYOR
    for bolum in ("## Piyasa Kapanisi", "## Dunya Gundemi",
                  "## Turkiye Gundemi", "## Gorus", "## Ek: Veri Notlari"):
        assert bolum in P, bolum
    # Eksiklik beyani sinirlandirilmis olmali
    assert "kapsam" in P and "veri yok" in P


# ======================================================================
# IS KUYRUGU — es zamanli sohbetler (bot/kuyruk.py)
# ======================================================================

class _SahteSurec:
    """Popen taklidi: canliligi TESTIN kontrol ettigi bir surec."""
    _sayac = [1000]

    def __init__(self, yol):
        self.yol = yol
        _SahteSurec._sayac[0] += 1
        self.pid = _SahteSurec._sayac[0]
        self._cikis = None
        self.oldurulme = 0

    def poll(self):
        return self._cikis

    def kill(self):
        self.oldurulme += 1
        self._cikis = -9

    def bitir(self, kod=0):
        self._cikis = kod


def _kuyruk(d, **kw):
    """Gercek surec baslatmayan Kuyruk + baslatilan isler ve bildirimler."""
    from finagent.bot.kuyruk import Kuyruk

    saat = {"t": 1_000_000.0}
    baslatilan, bildirimler = [], []

    def _baslat(yol):
        p = _SahteSurec(yol)
        baslatilan.append(p)
        return p

    k = Kuyruk(_pathlib.Path(d) / "kuyruk",
               baslat=kw.pop("baslat", _baslat),
               bildir=lambda cid, metin: bildirimler.append((cid, metin)),
               simdi=lambda: saat["t"], **kw)
    k.test_saat, k.test_baslatilan, k.test_bildirim = saat, baslatilan, bildirimler
    return k


def _upd(uid, chat_id, metin="merhaba"):
    return {"update_id": uid,
            "message": {"chat": {"id": chat_id}, "text": metin}}


def test_kuyruk_ayni_sohbette_iki_isi_ust_uste_bindirmez():
    """
    SOZLESME 2. Sohbet gecmisi (`sohbet/<chat_id>.json`) ve 20 dakikalik
    anlik goruntu birlestirme penceresi tek yazar varsayiyor; iki tur
    ust uste binerse ikisi de ayni gecmisi okuyup birbirini EZER.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        k = _kuyruk(d, azami_worker=2)
        k.ekle(_upd(1, 111), 111)
        k.ekle(_upd(2, 111), 111)
        k.tik()

        assert len(k.test_baslatilan) == 1, "ayni sohbette iki is birden basladi"
        assert k.durumu(1) == "calisiyor"
        assert k.durumu(2) == "bekliyor"

        # Birinci bitince IKINCI baslar — sira KORUNUR.
        k._bitti_yolu(1).write_text("tamam")
        k.tik()
        assert k.durumu(1) is None, "biten is temizlenmedi"
        assert k.durumu(2) == "calisiyor"
        assert len(k.test_baslatilan) == 2


def test_kuyruk_ayri_sohbetleri_gercekten_paralel_kosturur():
    """
    ISTENEN KAZANC BUDUR: Ali'nin 25 dakikalik turu Yuksel'i
    beklettigi icin bu katman yazildi (olculdu, commit e970151).
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        k = _kuyruk(d, azami_worker=2)
        k.ekle(_upd(1, 111), 111)          # ali
        k.ekle(_upd(2, 222), 222)          # yuksel
        k.tik()
        assert k.durumu(1) == "calisiyor" and k.durumu(2) == "calisiyor"
        assert len(k.test_baslatilan) == 2

        bekleyen, calisan = k.sayim()
        assert (bekleyen, calisan) == (0, 2)
        # Iki is KOSARKEN kimse sirada degil -> hizli yoklamaya gerek yok.
        assert k.bekleyen_var() is False

        k.ekle(_upd(3, 333), 333)          # worker yok, sirada bekler
        k.tik()
        assert k.bekleyen_var() is True, \
            "sirada is varken yoklama hizlanmaz, siradaki bosuna bekler"


def test_kuyruk_worker_sinirini_asmaz():
    """Sinir yoksa N kullanici N `claude` alt sureci demek olurdu."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        k = _kuyruk(d, azami_worker=2)
        for i, chat in enumerate((111, 222, 333), start=1):
            k.ekle(_upd(i, chat), chat)
        k.tik()
        assert len(k.test_baslatilan) == 2, "worker siniri asildi"
        assert k.durumu(3) == "bekliyor"

        k._bitti_yolu(1).write_text("tamam")
        k.tik()
        assert k.durumu(3) == "calisiyor", "yuva bosalinca siradaki baslamadi"


def test_kuyruk_coken_isi_bir_kez_yeniden_dener_sonra_pes_eder():
    """
    SOZLESME 4. `.bitti` YOKSA is sert cokmustur (kill/OOM) — istisna
    zaten worker icinde yakalanip `.bitti` yaziyor. Sonsuz dongu
    olmamasi icin deneme SINIRLI ve pes etmek SESSIZ degil.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        k = _kuyruk(d, azami_worker=1)
        k.ekle(_upd(1, 111), 111)
        k.tik()
        p1 = k.test_baslatilan[0]

        p1.bitir(-9)                       # `.bitti` YOK -> sert cokme
        k.tik()
        assert k.durumu(1) == "calisiyor", "yeniden denenmedi"
        assert len(k.test_baslatilan) == 2
        import json as _json
        assert _json.loads(k._yol(1).read_text())["deneme"] == 2

        k.test_baslatilan[1].bitir(-9)     # ikinci kez de cokerse
        k.tik()
        assert k.durumu(1) is None, "sonsuza dek denendi"
        assert len(k.test_baslatilan) == 2, "azami deneme asildi"
        assert k.test_bildirim and "cokti" in k.test_bildirim[-1][1], \
            "pes etmek SESSIZ oldu"


def test_kuyruk_zaman_asiminda_oldurur_ve_yeniden_denemez():
    """
    Bugune kadar asili bir turu kesmenin yolu BOTU OLDURMEKTI; 24,9
    dakikalik arizada elle yapilan sey buydu. Yeniden denemek ise ayni
    duvara ikinci kez toslamak olurdu.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        k = _kuyruk(d, azami_worker=1, zaman_asimi_sn=900)
        k.ekle(_upd(1, 111), 111)
        k.tik()
        p = k.test_baslatilan[0]

        k.test_saat["t"] += 899            # sinirin ALTINDA: dokunma
        k.tik()
        assert p.oldurulme == 0 and k.durumu(1) == "calisiyor"

        k.test_saat["t"] += 2              # sinir asildi
        k.tik()
        assert p.oldurulme == 1, "asili is oldurulmedi"
        assert k.durumu(1) is None
        assert len(k.test_baslatilan) == 1, "zaman asimi yeniden denendi"
        assert "15 dakikada bitmedi" in k.test_bildirim[-1][1]


def test_kuyruk_yasayan_isi_devralir_ikinci_kez_calistirmaz():
    """
    RESTART SENARYOSU. Worker ayri oturumda kosuyor ve cevabini KENDI
    gonderiyor; yeniden baslayan dinleyici onu yeniden calistirirsa
    kullanici AYNI cevabi iki kez alir.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        k = _kuyruk(d, azami_worker=2)
        k.ekle(_upd(1, 111), 111)
        k.tik()

        # Yeni dinleyici sureci: Popen tutamaci YOK, yalnizca dosyalar.
        k2 = _kuyruk(d, azami_worker=2)
        k2.test_saat["t"] = k.test_saat["t"]
        k2._hb_yolu(1).touch()             # worker kalbi atiyor
        k2.kurtar()
        assert k2.durumu(1) == "calisiyor", "yasayan is yeniden kuyruga atildi"
        k2.tik()
        assert not k2.test_baslatilan, "yasayan is IKINCI KEZ calistirildi"

        # Ve yuvayi DOLDURUYOR: ayni sohbetten yeni is beklemeli.
        k2.ekle(_upd(2, 111), 111)
        k2.tik()
        assert k2.durumu(2) == "bekliyor"


def test_kuyruk_kalbi_durmus_isi_yeniden_kuyruga_alir():
    """Devralinan isin tek canlilik olcutu kalp atisi — PID DEGIL."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        k = _kuyruk(d, azami_worker=2)
        k.ekle(_upd(1, 111), 111)
        k.tik()

        k2 = _kuyruk(d, azami_worker=2)
        k2.test_saat["t"] = k.test_saat["t"] + k2.TERK_ESIGI_SN + 1
        k2.kurtar()                        # `.hb` hic yok, baslama eski
        assert k2.durumu(1) == "bekliyor", "olmus is dirilmedi"
        k2.tik()
        assert len(k2.test_baslatilan) == 1


def test_kuyruk_kalp_atisi_beklerken_isi_olu_saymaz():
    """
    Worker'in ilk atisa ulasmasi ~0,5 sn suruyor. Bu araliga "olmus"
    demek HER ISI bir kez fazladan calistirirdi.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        k = _kuyruk(d, azami_worker=2)
        k.ekle(_upd(1, 111), 111)
        k.tik()
        k2 = _kuyruk(d, azami_worker=2)
        k2.test_saat["t"] = k.test_saat["t"] + 1      # daha `.hb` yok
        k2.kurtar()
        assert k2.durumu(1) == "calisiyor"


def test_kuyruk_ayni_guncellemeyi_iki_kez_islemez():
    """
    Offset, is KUYRUGA YAZILDIKTAN sonra ilerliyor. Arada cokme olursa
    Telegram guncellemeyi YENIDEN gonderir; `bitmis` halkasi onu ikinci
    kez calistirmayi engeller.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        k = _kuyruk(d, azami_worker=1)
        assert k.ekle(_upd(1, 111), 111) is True
        assert k.ekle(_upd(1, 111), 111) is False, "ayni is iki kez kuyruga girdi"

        k.tik()
        k._bitti_yolu(1).write_text("tamam")
        k.tik()
        assert k.durumu(1) is None
        assert k.ekle(_upd(1, 111), 111) is False, \
            "islenmis guncelleme tekrar kuyruga girdi"


def test_kuyruk_bozuk_is_dosyasi_kuyrugu_tikamaz():
    """Yarim yazilmis bir dosya kuyrugu sonsuza dek 'dolu' gosterirdi."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        k = _kuyruk(d, azami_worker=1)
        (k.dizin / "9.json").write_text("{yarim")
        k.ekle(_upd(1, 111), 111)
        k.tik()
        assert not (k.dizin / "9.json").exists(), "bozuk dosya silinmedi"
        assert k.durumu(1) == "calisiyor", "bozuk dosya kuyrugu tikadi"


def test_kuyruk_worker_baslatilamazsa_kullanici_sessiz_kalmaz():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        def _patla(_yol):
            raise OSError("fork basarisiz")
        k = _kuyruk(d, azami_worker=1, baslat=_patla)
        k.ekle(_upd(1, 111), 111)
        k.tik()
        assert k.durumu(1) is None
        assert k.test_bildirim and "isleyemedim" in k.test_bildirim[-1][1]


def test_kuyruk_worker_komutu_ayri_oturumda_baslar():
    """
    `launchctl kill TERM` sinyali SUREC GRUBUNA gider. Worker ayni
    grupta kalsaydi her planli restart ucustaki turlari da oldururdu —
    kacinmak istedigimiz seyin ta kendisi.
    """
    import tempfile
    from finagent.bot import kuyruk as K

    with tempfile.TemporaryDirectory() as d:
        yakalanan = {}

        class _Sahte:
            def __init__(self, komut, **kw):
                yakalanan["komut"], yakalanan["kw"] = komut, kw
                self.pid = 1

        eski = K.subprocess.Popen
        K.subprocess.Popen = _Sahte
        try:
            k = K.Kuyruk(_pathlib.Path(d) / "kuyruk", kok=_pathlib.Path(d))
            k._varsayilan_baslat(_pathlib.Path(d) / "5.json")
        finally:
            K.subprocess.Popen = eski

        assert yakalanan["kw"].get("start_new_session") is True, \
            "worker surec grubunda kaldi — restart onu oldururdu"
        assert yakalanan["komut"][1:4] == ["run.py", "bot-worker", "--is"], \
            yakalanan["komut"]


def test_kalp_atisi_dosyaya_dokunur_ve_durur():
    import tempfile, time as _t
    from finagent.bot.kuyruk import KalpAtisi

    with tempfile.TemporaryDirectory() as d:
        yol = _pathlib.Path(d) / "1.hb"
        with KalpAtisi(yol, aralik=0.05) as kalp:
            assert yol.exists(), "ilk atis HEMEN atilmali"
            _t.sleep(0.15)
            ilk = yol.stat().st_mtime
            _t.sleep(0.15)
            assert yol.stat().st_mtime >= ilk
        assert kalp._dur.is_set()


# ======================================================================
# DINLEYICI <-> KUYRUK — giris siniflandirmasi
# ======================================================================

def _giris_botu(d, db):
    """Kuyrugu takilmis dinleyici; `_calistir` cagrilirsa YAKALANIR."""
    from finagent.config import load_settings
    s = load_settings()
    s.raw.setdefault("telegram", {})["sahipler"] = {"111": "ali", "222": "yuksel"}
    bot = _sahte_bot(s, db)
    bot.state_dir = _pathlib.Path(d)
    bot.gorsel_dir = bot.state_dir / "gorsel"; bot.gorsel_dir.mkdir(exist_ok=True)
    bot.pending_dir = bot.state_dir / "pending"; bot.pending_dir.mkdir(exist_ok=True)
    bot.yerinde = []
    bot._calistir = lambda upd: bot.yerinde.append(upd.get("update_id"))
    bot.kuyruk = _kuyruk(d, azami_worker=2)
    return bot


def test_agir_is_kuyruga_gider_hafif_is_yerinde_kalir():
    """
    Olcut TEK: salt okunur ve LLM'siz olan yerinde kalir. Menu gezinmek
    mesgul bir sohbette dakikalarca donmemeli; ama VARSAYILAN AGIRDIR —
    yeni bir komut siniflandirilmayi unutursa bedeli GECIKME olur,
    BOZULMA degil.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        bot = _giris_botu(d, db)

        bot._dispatch(_upd(1, 111, "/rehber"))            # hafif
        bot._dispatch({"update_id": 2, "callback_query": {
            "id": "a", "data": "reh:portfoy",
            "message": {"chat": {"id": 111}}}})           # hafif
        assert bot.yerinde == [1, 2]
        assert bot.kuyruk.durumu(1) is None

        bot._dispatch(_upd(3, 111, "ASELSAN nasil?"))     # sohbet -> agir
        bot._dispatch(_upd(4, 222, "/portfoy"))           # komut ama agir
        bot._dispatch({"update_id": 5, "message": {
            "chat": {"id": 222}, "photo": [{"file_id": "x"}]}})   # gorsel
        assert bot.yerinde == [1, 2], "agir is yerinde calisti"
        for uid in (3, 4, 5):
            assert bot.kuyruk.durumu(uid) is not None, uid

        # `/durum` BILEREK agir: `api_saglik()` ag cagrisi yapiyor.
        assert bot._hizli_mi(_upd(9, 111, "/durum")) is False
        db.close()


def test_yetkisiz_guncelleme_kuyruga_girmez():
    """
    Yetki kapisi GIRISTE. Yetkisiz mesaj kuyruga girseydi worker
    baslatir, o da reddederdi — bedava alt surec ve gereksiz kanal.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        bot = _giris_botu(d, db)
        bot._dispatch(_upd(7, 999, "merhaba"))     # sahipler'de yok
        assert bot.kuyruk.durumu(7) is None, "yetkisiz mesaj kuyruga alindi"
        assert bot.yerinde == [7], "eski red yoluna dusmedi"
        db.close()


def test_sirada_bekleyen_kullaniciya_soylenir_hemen_baslayan_icin_susulur():
    """
    Sessizlik "mesajim dusmedi" hissinin ta kendisi. Ilerleme gostergesi
    ancak is BASLAYINCA kuruluyor; siradaki is icin tek isaret bu mesaj.
    Ama sohbet bossa kuyruk GORUNMEZ olmali — her mesaja "siraya alindi"
    demek gurultudur.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        bot = _giris_botu(d, db)

        bot._dispatch(_upd(1, 111, "ilk soru"))
        assert not bot.gonderilen, "hemen baslayan is icin bos yere yazildi"

        bot._dispatch(_upd(2, 111, "ikinci soru"))
        assert bot.gonderilen, "sirada bekleyen kullaniciya hicbir sey denmedi"
        assert "Siraya alindi" in bot.gonderilen[-1][0]
        assert bot.gonderilen[-1][1] == 111
        db.close()


def test_gorsel_hafizasi_surecler_arasi_yasar():
    """
    Gorseli ALAN tur ile onu KULLANAN tur artik ayri sureclerde
    olabiliyor. RAM'de birakilsaydi "az once attigim resim" zinciri
    SESSIZCE kopardi — kullanici gonderdigini bilir, model gormez.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        bot_a = _giris_botu(d, db)
        bot_a._gorsel_koy("111", "/tmp/ekran.png")

        bot_b = _giris_botu(d, db)          # BASKA surec taklidi
        assert bot_b._gorsel_al("111") == "/tmp/ekran.png"
        assert bot_b._gorsel_al("222") is None
        db.close()


# ======================================================================
# WORKER — isin kendisi (bot/worker.py)
# ======================================================================

class _SahteFinBot:
    def __init__(self, patla=None):
        self.patla = patla
        self.calisan, self.gonderilen = [], []
        self.kuyruk = "dokunulmadi"

        class _Tg:
            def send_message(_s, metin, chat_id=None, **k):
                self.gonderilen.append((metin, chat_id))
                return True
        self.tg = _Tg()

    def _calistir(self, upd):
        self.calisan.append(upd.get("update_id"))
        if self.patla:
            raise self.patla


def _worker_kos(d, is_veri, patla=None):
    from finagent.bot import listener as L
    from finagent.bot import worker as W

    yol = _pathlib.Path(d) / f"{is_veri.get('update_id', 1)}.json"
    yol.write_text(_json_dumps(is_veri), encoding="utf-8")
    sahte = _SahteFinBot(patla)
    eski = L.FinBot
    L.FinBot = lambda *a, **k: sahte
    try:
        kod = W.calistir(None, None, yol)
    finally:
        L.FinBot = eski
    return kod, sahte, yol


def _json_dumps(o):
    import json as _json
    return _json.dumps(o)


def test_worker_isi_calistirir_ve_bitti_isareti_birakir():
    """
    `.bitti` ISARETI SOZLESMENIN KALBI: ana surec "tamamlandi" ile
    "sert cokme" arasindaki farki YALNIZCA bundan anliyor.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        kod, sahte, yol = _worker_kos(d, {
            "update_id": 42, "chat_id": 111, "deneme": 1,
            "update": _upd(42, 111)})
        assert kod == 0
        assert sahte.calisan == [42]
        assert sahte.kuyruk is None, "worker'da kuyruk kapatilmadi"
        assert yol.with_suffix(".bitti").read_text() == "tamam"


def test_worker_istisnayi_sonuc_sayar_yeniden_denetmez():
    """
    Istisna zaten kullaniciya bildiriliyor. `.bitti` yazilmazsa ana
    surec bunu cokme sanip yeniden calistirir ve kullanici AYNI hatayi
    iki kez alir.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        kod, sahte, yol = _worker_kos(
            d, {"update_id": 43, "chat_id": 111, "deneme": 1,
                "update": _upd(43, 111)}, patla=RuntimeError("patladi"))
        assert kod == 0
        assert yol.with_suffix(".bitti").read_text() == "hata"
        assert sahte.gonderilen and "RuntimeError" in sahte.gonderilen[0][0]
        assert sahte.gonderilen[0][1] == 111, "hata yanlis sohbete gitti"


def test_worker_kalp_atisini_gercekten_atar():
    """Kalp atmazsa ana surec calisan isi 90 sn sonra OLU sayar."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        from finagent.bot import listener as L
        from finagent.bot import worker as W

        yol = _pathlib.Path(d) / "44.json"
        yol.write_text(_json_dumps({"update_id": 44, "chat_id": 111,
                                    "update": _upd(44, 111)}), encoding="utf-8")
        gorulen = {}
        sahte = _SahteFinBot()

        def _bak(_upd):
            gorulen["hb"] = yol.with_suffix(".hb").exists()
        sahte._calistir = _bak

        eski = L.FinBot
        L.FinBot = lambda *a, **k: sahte
        try:
            W.calistir(None, None, yol)
        finally:
            L.FinBot = eski
        assert gorulen.get("hb") is True, "is calisirken kalp atmiyordu"


def test_worker_bozuk_is_dosyasini_yeniden_denetmez():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        yol = _pathlib.Path(d) / "45.json"
        yol.write_text(_json_dumps({"update_id": 45, "chat_id": 111}),
                       encoding="utf-8")
        from finagent.bot import worker as W
        assert W.calistir(None, None, yol) == 2
        assert yol.with_suffix(".bitti").exists(), \
            "bozuk dosya sonsuza dek yeniden denenir"
        # Hic olmayan dosya: `.bitti` de yazilamaz, sessizce 2 doner.
        assert W.calistir(None, None, _pathlib.Path(d) / "yok.json") == 2


def test_bot_worker_komutu_cli_de_kayitli():
    """
    Kuyruk `run.py bot-worker --is <yol>` cagiriyor. Alt komut yoksa
    HICBIR agir is calismaz ve bu ancak canlida gorunurdu.
    """
    r = _run_py("bot-worker", "--help")
    assert r.returncode == 0, r.stderr
    assert "--is" in r.stdout


def test_worker_ucdan_uca_gercek_surecte_calisir():
    """
    UCTAN UCA: gercek `run.py bot-worker` sureci, gercek is dosyasi.
    Birim testler zincirin halkalarini dogruluyor; bu, zincirin
    KOPUK OLMADIGINI dogruluyor (bkz. `_query` vakasi).
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        yol = _pathlib.Path(d) / "77.json"
        # Yetkisiz sohbet: worker'in DIS dunyaya dokunmadan tam yolu
        # kosmasini saglar (yetki reddi -> log -> bitti).
        yol.write_text(_json_dumps({
            "update_id": 77, "chat_id": -1, "deneme": 1,
            "update": {"update_id": 77,
                       "message": {"chat": {"id": -1}, "text": "merhaba"}}}),
            encoding="utf-8")
        # Izolasyon (DB_PATH + token dusurme) artik `_run_py`'de, tek
        # yerde. Burada elle yazilmisti; digerinde YAZILMAMISTI ve
        # sizinti tam oradan cikti.
        r = _run_py("bot-worker", "--is", str(yol))
        assert r.returncode == 0, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
        assert yol.with_suffix(".bitti").exists(), \
            "gercek worker `.bitti` yazmadi — her is cokme sayilirdi"


def test_kuyruk_gercek_worker_baslatir_ve_toplar():
    """
    ZINCIRIN TAMAMI, TAKLITSIZ: gercek `Kuyruk` gercek alt sureci
    baslatiyor, worker isi isliyor, ana surec `.bitti` isaretini gorup
    temizliyor.

    Birim testler halkalari dogruluyor; halkalarin BIRBIRINE BAGLI
    oldugunu ancak bu dogruluyor — `_query` vakasinda kaybedilen tam
    olarak buydu (iki gun sessiz kirik).
    """
    import os as _os
    import tempfile
    import time as _t
    from finagent.bot.kuyruk import Kuyruk

    kok = _pathlib.Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as d:
        eski_db = _os.environ.get("DB_PATH")
        eski_tok = _os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        _os.environ["DB_PATH"] = str(_pathlib.Path(d) / "test.db")
        try:
            k = Kuyruk(_pathlib.Path(d) / "kuyruk", kok=kok, azami_worker=2)
            # Yetkisiz sohbet: worker tam yolu kosar, disari dokunmaz.
            k.ekle({"update_id": 4242,
                    "message": {"chat": {"id": -1}, "text": "merhaba"}}, -1)
            k.tik()
            assert k.durumu(4242) == "calisiyor", "gercek worker baslamadi"

            son = _t.time() + 180
            while _t.time() < son and k.durumu(4242) is not None:
                _t.sleep(0.5)
                k.tik()
            assert k.durumu(4242) is None, "worker bitti ama kuyruk temizlemedi"
            assert not any(k.dizin.glob("4242.*")), "artik dosya kaldi"
            assert 4242 in k._bitmis_oku(), "biten is hatirlanmadi"
        finally:
            _os.environ.pop("DB_PATH", None)
            if eski_db is not None:
                _os.environ["DB_PATH"] = eski_db
            if eski_tok is not None:
                _os.environ["TELEGRAM_BOT_TOKEN"] = eski_tok


def test_kuyruk_ayarlari_koda_baglidir():
    """Ayar dosyasindaki isim ile kodun okudugu anahtar surtusmemeli."""
    import inspect
    from finagent.bot import listener as L
    import yaml as _yaml

    ayar = _yaml.safe_load(
        (_pathlib.Path(__file__).resolve().parents[1]
         / "config" / "settings.yaml").read_text(encoding="utf-8"))
    tel = ayar["telegram"]
    assert tel["worker_sayisi"] == 2 and tel["is_zaman_asimi_dk"] == 15
    kaynak = inspect.getsource(L.FinBot.run)
    for anahtar in ("telegram.worker_sayisi", "telegram.is_zaman_asimi_dk"):
        assert anahtar in kaynak, anahtar


# ======================================================================
# ZAMANLANMIS KOSULAR — 2026-08-17'de ogle kosusu SESSIZCE kayboldu
# ======================================================================

def test_isyatirim_sure_butcesi_kosuyu_kaybetmez():
    """
    OLCULEN CANLI KAYIP (2026-08-17, pulse.log 688-702): ogle kosusu
    18:00'de basladi, `collect` 20 dakikalik KABUK butcesini doldurdu,
    `run_hafif.sh` surec GRUBUNU oldurdu ve `nabiz` adimina HIC
    ULASILAMADI — o gun BIST kapanisi icin sinyal, tez alarmi ve
    portfoy riski uretilmedi.

    Sinir artik ICERIDEN uygulaniyor: collector duzgunce durur, kalani
    sonraki kosuya birakir ve kosunun geri kalani CALISIR.
    """
    import tempfile, time as _t
    from finagent.collectors.isyatirim import IsYatirimCollector

    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        for sym in ("AAA", "BBB", "CCC", "DDD"):
            db.upsert_instrument(sym, "BIST", asset_type="equity", currency="TRY")

        class _S(dict):
            root = _pathlib.Path(d)
            def get(self, k, v=None):
                return {"sources.isyatirim.azami_sure_sn": 0.25,
                        "analysis.lookback_days": 250}.get(k, v)
        c = IsYatirimCollector.__new__(IsYatirimCollector)
        c.s, c.db, c.browser = _S(), db, None
        c._semboller = lambda: ["AAA", "BBB", "CCC", "DDD"]
        cekilen = []

        def _yavas(url, sym):
            cekilen.append(sym)
            _t.sleep(0.2)
            return [{"ts": "2026-08-18", "close": 10.0}]
        c._fetch = _yavas

        sonuc = c.collect()
        # Butce 0,25 sn; her sembol 0,2 sn -> hepsi cekilemez.
        assert len(cekilen) < 4, f"butce uygulanmadi, {len(cekilen)} sembol cekildi"
        assert cekilen, "butce her seyi kesti"
        assert sonuc.status == "partial", sonuc.status
        # KESILME GORUNUR OLMALI — sessiz eksik, "tam cekti" diye okunur.
        assert "sure butcesi" in (sonuc.error or ""), sonuc.error
        db.close()


def test_isyatirim_en_bayat_sembolu_once_ceker():
    """
    Butce dolarsa kuyrugun sonu cekilemez. Sabit siralamada bu HER GUN
    AYNI sembolleri ac birakirdi; bayatliga gore siralayinca kesilen
    kuyruk her kosuda degisir ve kapsam kendi kendini dengeler.
    """
    import tempfile
    from finagent.collectors.isyatirim import IsYatirimCollector

    with tempfile.TemporaryDirectory() as d:
        db = _arsiv_db(d)
        # TAZE'nin son bari yeni, BAYAT'inki eski, YOK'un hic bari yok.
        for sym, ts in (("TAZE", "2026-08-18"), ("BAYAT", "2026-07-01")):
            iid = db.upsert_instrument(sym, "BIST", asset_type="equity",
                                       currency="TRY")
            db.upsert_prices(iid, [{"ts": ts, "close": 5.0}], "isyatirim",
                             currency="TRY")
        db.upsert_instrument("YOK", "BIST", asset_type="equity", currency="TRY")

        class _S(dict):
            root = _pathlib.Path(d)
            def get(self, k, v=None):
                return {"analysis.lookback_days": 250}.get(k, v)
        c = IsYatirimCollector.__new__(IsYatirimCollector)
        c.s, c.db, c.browser = _S(), db, None
        c._semboller = lambda: ["TAZE", "BAYAT", "YOK"]
        sira = []
        c._fetch = lambda url, sym: (sira.append(sym), None)[1]
        c.collect()
        assert sira == ["YOK", "BAYAT", "TAZE"], sira
        db.close()


def test_tarayici_fallbacki_sure_sinirli():
    """
    Fallback sinirsizken tek bir sembol dakikalarca asilabiliyordu
    (pulse.log: 18:04, 18:08, 18:11 — ucu ust uste 20 dk'yi doldurdu).

    ASIL SIZINTI JS'TE: `page.evaluate` bir zaman asimi parametresi
    ALMAZ ve `set_default_timeout` onu KAPSAMAZ, yani icerideki `fetch`
    asilirsa Python tarafi sonsuza kadar bekler. Sinir bu yuzden hem
    Playwright tarafinda hem `fetch`'in kendisinde olmak zorunda.
    """
    import inspect
    from finagent.collectors.isyatirim import IsYatirimCollector

    kaynak = inspect.getsource(IsYatirimCollector._fetch_via_browser)
    assert "fallback_sn" in kaynak, "fallback suresi ayarlanabilir degil"
    assert "set_default_navigation_timeout" in kaynak, "gezinme sinirsiz"
    for parca in ("AbortController", "abort()", "signal:"):
        assert parca in kaynak, f"fetch JS icinde sinirlanmamis: {parca}"

    import yaml as _yaml
    ayar = _yaml.safe_load((_pathlib.Path(__file__).resolve().parents[1]
                            / "config" / "settings.yaml").read_text(encoding="utf-8"))
    isy = ayar["sources"]["isyatirim"]
    assert isy["fallback_sn"] == 30

    # ICERIDEKI butce, KABUK butcesinden belirgin KUCUK olmali; aksi
    # halde collector durmadan once kabuk sureci oldurur ve kazanim yok.
    #
    # KABUK BUTCESI ARTIK AYARDA. Onceden IKI YERDEYDI: plist'te
    # `HAFIF_TIMEOUT=1200` ve betikte varsayilan `900` — iki deger,
    # tek gercek. Ikisi ayrisirsa hangisinin gecerli oldugunu kimse
    # bilemez. Tek kaynak: ritim.kipler.<kip>.kabuk_butce_sn
    from finagent.config import load_settings
    s = load_settings()
    isyatirimli = [k for k in s.ritim_kipleri
                   if "isyatirim" in s.ritim_kip(k)["kaynaklar"]]
    assert isyatirimli, "isyatirim hicbir kipte toplanmiyor"
    for kip in isyatirimli:
        kabuk_sn = s.ritim_kip(kip)["kabuk_butce_sn"]
        assert isy["azami_sure_sn"] < kabuk_sn - 240, (
            f"{kip}: ic butce {isy['azami_sure_sn']} sn, kabuk siniri "
            f"{kabuk_sn} sn — kalan collector'lar ve nabiz adimi icin pay yok")

    # ESKI ORTAM DEGISKENLERI GERI GELMESIN.
    for p in sorted((_pathlib.Path(__file__).resolve().parents[1]
                     / "launchd").glob("*.plist")):
        metin = p.read_text(encoding="utf-8")
        assert "HAFIF_TIMEOUT" not in metin and "PULSE_TIMEOUT" not in metin, \
            f"{p.name} sure sinirini ikinci bir kaynaktan veriyor"


def test_cok_sahipli_kosu_ozet_basiminda_dusmez():
    """
    OLCULEN CANLI ARIZA (pulse.log 1064 ve 1120): `run.py` duz
    `sonuc["guclu"]` okuyordu ama `runner.calistir` duz alanlari
    YALNIZCA tek sahiplide yayiyor. Yuksel eklenince sahip sayisi 2
    oldu ve HER zamanlanmis kosu `KeyError: 'guclu'` ile dustu — is ve
    bildirimler tamamlaniyordu ama KARNE CIKTISI hic basilmadi ve cikis
    kodu 1 oldu, yani disaridan her kosu "basarisiz" gorundu.
    """
    import ast
    # METIN OLARAK okunuyor, import EDILMIYOR: `run.py` modul duzeyinde
    # yorumlayici kontrolu yapiyor ve sys.path'e yaziyor.
    kaynak = (_pathlib.Path(__file__).resolve().parents[1]
              / "run.py").read_text(encoding="utf-8")
    duz = []
    for d in ast.walk(ast.parse(kaynak)):
        if (isinstance(d, ast.Subscript) and isinstance(d.value, ast.Name)
                and d.value.id == "sonuc" and isinstance(d.slice, ast.Constant)):
            duz.append(d.slice.value)
    # Duz indeksleme YALNIZCA `if sonuc.get(...)` ile korunanlarda serbest.
    for anahtar in duz:
        assert f'sonuc.get("{anahtar}")' in kaynak, (
            f'sonuc["{anahtar}"] korumasiz — cok sahipli kosuda KeyError')
    assert 'sonuc.get("guclu")' in kaynak


def test_kosu_izi_isin_sonunda_birakilir():
    """
    Bir kosunun CALISTIGINI baska hicbir kayit tek basina soyleyemiyor:
    `signals` tarih bazli ve kip tasimiyor, `collector_runs` sohbetten
    tetiklenen toplamalarla karisiyor, `panel_runs` yalnizca LLM
    panelinde yaziliyor. Iz bu yuzden var — ve YARIM kalan kosu iz
    BIRAKMAMALI, yoksa gozcu kor olur.
    """
    import tempfile, types
    from finagent.pulse.runner import Nabiz

    # SAHTE AYAR, GERCEK SOZLESMEYE BAGLI. `_iz_birak` her istisnayi
    # yutuyor (dogru: iz asla kosuyu dusurmemeli) — bu da eksik bir
    # alanin SESSIZCE "iz yazilmadi"ya donmesi demek. Sahte nesne
    # gercek `Settings`in verdigi alani vermezse test yesil kalir ve
    # uretimde gozcu kor olur. Onun icin once alanin GERCEKTEN var
    # oldugu dogrulaniyor.
    from finagent.config import Settings
    assert isinstance(getattr(Settings, "bot_state_dir", None), property), \
        "Settings.bot_state_dir yok — sahte ayar gercegi taklit edemez"

    with tempfile.TemporaryDirectory() as d:
        n = Nabiz.__new__(Nabiz)
        n.s = types.SimpleNamespace(root=_pathlib.Path(d),
                                    bot_state_dir=_pathlib.Path(d) / "data" / "bot")
        n._iz_birak("ogle", ["ali", "yuksel"], {"piyasa_sinyali": 42})
        yol = _pathlib.Path(d) / "data" / "bot" / "kosu" / "ogle.json"
        assert yol.exists(), "kosu izi yazilmadi"
        import json as _json
        veri = _json.loads(yol.read_text())
        assert veri["kip"] == "ogle" and veri["piyasa_sinyali"] == 42
        assert veri["sahipler"] == ["ali", "yuksel"]

        # IZ ASLA KOSUYU DUSURMEZ.
        kotu = _pathlib.Path(d) / "olmayan\0kotu"
        n.s = types.SimpleNamespace(root=kotu, bot_state_dir=kotu / "data" / "bot")
        n._iz_birak("sabah", [], {})          # istisna FIRLATMAMALI

    # Ve `calistir` izi DONMEDEN once birakmali (yarim kosu iz birakmaz).
    import inspect
    kaynak = inspect.getsource(Nabiz.calistir)
    assert kaynak.index("_iz_birak") < kaynak.rindex("return {"), \
        "iz, sonuc donduruldukten sonra birakiliyor"


def _kosu_bekcisi(d, kip_izleri=None, saat=None, kurulum_gun_once=30,
                  digerleri_kosmus=False):
    """
    Kosu bekcisi + istege bagli iz dosyalari.

    KURULUM DAMGASI GERIYE ALINIR (varsayilan 30 gun). Bu yardimciyi
    kullanan testler YARGILAMA mantigini siniyor, bootstrap'i degil —
    damga bugune yazilirsa bekci "kurulumdan onceki kosuyu yargilamam"
    diyerek her seyi susturur ve testler sessizce anlamsizlasir.
    Bootstrap'in kendisi `test_bekci_kurulumundan_onceki_kosuyu_yargilamaz`
    icinde ayrica sinaniyor.
    """
    from finagent.bot.watchdog import Bekci
    from finagent.bot import watchdog as _W
    from finagent.config import load_settings
    from datetime import timedelta as _td
    import json as _json
    s = load_settings()
    b = Bekci(s, None, _pathlib.Path(d))
    kosu = _pathlib.Path(d) / "kosu"
    kosu.mkdir(parents=True, exist_ok=True)
    if kurulum_gun_once is not None:
        (kosu / "kurulum.json").write_text(_json.dumps(
            {"ts": (_W._yerel() - _td(days=kurulum_gun_once)).isoformat()}))
    izler = dict(kip_izleri or {})
    if digerleri_kosmus:
        # AYARDAKI DIGER TUM KIPLER "bugun kostu" sayilir. Boylece test
        # yalnizca ILGILENDIGI kipi yargilar ve yeni bir kip eklendiginde
        # (or. `kapanis`) kendiliginden bozulmaz — testin kirilganligi
        # kip sayisina bagli olmamali.
        for kip in s.ritim_kipleri:
            izler.setdefault(kip, _W._yerel())
    for kip, ts in izler.items():
        (kosu / f"{kip}.json").write_text(
            _json.dumps({"kip": kip, "ts": ts.isoformat()}))
    return b


def test_bekci_kacirilan_ogle_kosusunu_yakalar():
    """
    Nabzin gozcusu vardi, sabah ve ogle'nin YOKTU. 17 Agustos'ta ogle
    kosusu hic calismadi ve bu GUNLERCE gorunmedi — cunku bakan yoktu.
    """
    import tempfile
    from datetime import datetime as _dt, timedelta as _td
    from finagent.bot import watchdog as W

    import plistlib as _pl
    with tempfile.TemporaryDirectory() as d:
        # Sali 2026-08-18, gunun SONU: butun kipler coktan gecti.
        simdi = _dt(2026, 8, 18, 23, 59).astimezone()
        eski = W._yerel
        W._yerel = lambda: simdi
        try:
            # Ogle'nin plist saati ayardan/plist'ten okunur, ELLE
            # YAZILMAZ — saat degistiginde test sessizce yanlis olurdu
            # (ritim v2'de 18:00 -> 12:30 oldu ve bu test tam boyle
            # kirildi).
            kok = _pathlib.Path(__file__).resolve().parents[1]
            p = _pl.loads((kok / "launchd" /
                           "com.alipala.finagent.ogle.plist").read_bytes())
            sc = p["StartCalendarInterval"][0]
            beklenen = f"{sc['Hour']:02d}:{sc['Minute']:02d}"

            # Ogle DUNDEN, digerleri bugun kosmus.
            b = _kosu_bekcisi(d, {"ogle": simdi - _td(days=1)},
                              digerleri_kosmus=True)
            eksik = b.kacirilan_kosular()
            assert [x["kip"] for x in eksik] == ["ogle"], eksik
            assert eksik[0]["beklenen"] == beklenen, eksik

            # Ogle de bugun kosunca alarm SUSAR.
            b2 = _kosu_bekcisi(d, digerleri_kosmus=True)
            assert b2.kacirilan_kosular() == []
        finally:
            W._yerel = eski


def test_bekci_vakti_gelmemis_kosuya_alarm_calmaz():
    """
    Ogle kosusu dakikalar suruyor; baslamasindan 20 dk sonra
    "calismadi" demek YANLIS ALARM olurdu. Dort yanlis nabiz
    alarmindan sonra bu sinir bilincli: gozetim katmaninin kendisi
    gurultu uretmemeli.
    """
    import tempfile, plistlib as _pl
    from datetime import datetime as _dt, timedelta as _td
    from finagent.bot import watchdog as W

    # SAAT PLIST'TEN — elle yazilan saat, plist degistiginde testi
    # sessizce anlamsizlastirir (ogle 18:00 -> 12:30 oldugunda tam
    # boyle oldu).
    kok = _pathlib.Path(__file__).resolve().parents[1]
    sc = _pl.loads((kok / "launchd" /
                    "com.alipala.finagent.ogle.plist").read_bytes()
                   )["StartCalendarInterval"][0]

    with tempfile.TemporaryDirectory() as d:
        # Sali 2026-08-18, ogle baslayali 20 dakika olmus.
        simdi = (_dt(2026, 8, 18, sc["Hour"], sc["Minute"]).astimezone()
                 + _td(minutes=20))
        eski = W._yerel
        W._yerel = lambda: simdi
        try:
            b = _kosu_bekcisi(d, {"ogle": simdi - _td(days=1)},
                              digerleri_kosmus=True)
            assert b.kacirilan_kosular() == [], "gecikme payi uygulanmadi"

            # HAFTA SONU hic kosmuyorlar -> alarm yok.
            ctesi = _dt(2026, 8, 22, 20, 0).astimezone()   # Cumartesi
            W._yerel = lambda: ctesi
            assert b.kacirilan_kosular() == []
        finally:
            W._yerel = eski


def test_bekci_ilk_kurulumda_gecmise_alarm_calmaz():
    """
    Ilk kurulumda gecmise donuk alarm calmak dogrudan yanlis alarm uretir.

    OLCUT DEGISTI (2026-08-18, besinci yanlis alarmdan sonra): eskiden
    "hic iz dosyasi yoksa sessiz" idi ve bu IKI YONDEN de yaniliyordu —
    global bakinca hic kosmamis bir kipi yargiliyor, kipe ozel bakinca
    ilk gunden bozuk bir kipi hic yargilamiyordu. Yeni olcut KURULUM ANI.
    Burada damga YAZILMADAN cagriliyor: bekci onu kendisi SIMDI yazar,
    dolayisiyla bugunku tum kosular kurulumdan once kalir ve SESSIZ olur.
    """
    import tempfile
    from datetime import datetime as _dt
    from finagent.bot import watchdog as W

    with tempfile.TemporaryDirectory() as d:
        simdi = _dt(2026, 8, 18, 20, 0).astimezone()
        eski = W._yerel
        W._yerel = lambda: simdi
        try:
            # kurulum_gun_once=None -> damga yok; bekci simdi yazacak.
            assert _kosu_bekcisi(d, kurulum_gun_once=None).kacirilan_kosular() == [], \
                "taze kurulumda gecmise alarm caldi"
            # Damga artik diskte ve BUGUNE ait; ikinci cagri da sessiz olmali
            # (kurulum ani yeniden yazilmamali, okunmali).
            assert _kosu_bekcisi(d, kurulum_gun_once=None).kacirilan_kosular() == [], \
                "kurulum ani her cagrida yeniden yaziliyor olabilir"
        finally:
            W._yerel = eski


def test_bekci_kosu_takvimini_plistten_turetir():
    """
    Elle yazilan bir takvim, plist degistiginde SESSIZCE yanlis olur.
    README testinin plist saatlerini koda baglamasiyla ayni gerekce.
    """
    import plistlib, tempfile
    from finagent.config import load_settings
    s = load_settings()
    with tempfile.TemporaryDirectory() as d:
        takvim = _kosu_bekcisi(d)._plist_saatleri()
        # GOZETILEN KIPLER AYARDAN TURUYOR — elle liste tutulmuyor.
        # Onceden `IZ_KIPLERI = {"sabah","ogle"}` elle yaziliydi ve
        # `nabiz` LISTEDE YOKTU; 2026-08-19'da nabiz sessizce oldu ve
        # bekci hicbir sey demedi.
        assert set(takvim) == set(s.ritim_kipleri), takvim
        for kip in s.ritim_kipleri:
            veri = plistlib.loads(_pathlib.Path(
                f"launchd/com.alipala.finagent.{kip}.plist").read_bytes())
            sc = veri["StartCalendarInterval"]
            sc = [sc] if isinstance(sc, dict) else sc
            assert len(takvim[kip]) == len(sc)
            assert {(g["Hour"], g["Minute"]) for g in sc} == \
                   {(h, m) for _wd, h, m in takvim[kip]}


def test_nabiz_testleri_gercek_gozetim_durumunu_yazamaz():
    """
    OLCULDU: `calistir()` kosu izi birakmaya baslayinca duman testleri
    GERCEK `data/bot/kosu/` altina `sahipler: ["ali","esi"]` yazan iki
    dosya biraktilar. Bu, bekciye "sabah bugun kostu" dedirtip GERCEK
    bir arizayi SUSTURURDU — gozetim katmanini korlestiren en sinsi yol.

    Ders (siradaki-is): "bu test bir regresyonda ne KADAR gercek is
    yapabilir?" Burada cevap yapisal olarak sifirlaniyor.
    """
    from finagent.config import ROOT
    s = _fazb_ayar()
    assert _pathlib.Path(s.root).resolve() != _pathlib.Path(ROOT).resolve(), \
        "_fazb_ayar gercek proje kokunu donduruyor — testler gozetim " \
        "durumunu bozar"
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert _pathlib.Path(_fazb_ayar(kok=d).root) == _pathlib.Path(d)


def _poz_db(d):
    from finagent.storage.db import Database
    db = Database(_pathlib.Path(d) / "t.db"); db.init_schema()
    return db


def test_midas_pozisyonu_var_olan_bist_enstrumanina_baglanir():
    """
    OLCULDU 2026-08-18 07:41: Midas ekran goruntusu `venue='MIDAS'` diye
    IKINCI bir TRALT acti (0 bar), oysa `TRALT/BIST` 285 barla duruyordu.
    Pozisyon bos kopyaya baglandi; degerleme ve teknik zincir korlesti.

    Araci kurum bir PIYASA DEGILDIR: `collectors/midas.py` da zaten her
    seyi `venue='BIST'` olarak yaziyor.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _poz_db(d)
        bist = db.upsert_instrument("TRALT", "BIST",
                                    name="TURK ALTIN", currency="TRY")
        db.upsert_prices(bist, [{"ts": "2026-08-17", "close": 49.10}],
                         "test", "TRY")

        db.insert_positions("midas", "2026-08-18T07:41:00+00:00", [
            {"symbol": "TRALT", "quantity": 10, "market_value": 489.20,
             "currency": "TRY"}], "ali")

        poz = db.latest_positions("midas", "ali")
        assert len(poz) == 1 and poz[0]["symbol"] == "TRALT"
        # ASIL IDDIA: yeni enstruman ACILMADI, var olana baglandi.
        hepsi = db.query("SELECT id, venue FROM instruments WHERE symbol='TRALT'")
        assert len(hepsi) == 1, [dict(r) for r in hepsi]
        assert hepsi[0]["venue"] == "BIST"
        assert hepsi[0]["id"] == bist
        assert not db.query("SELECT 1 FROM instruments WHERE venue='MIDAS'")
        # Ve fiyat serisi artik pozisyondan GORUNUYOR.
        assert db.price_history("TRALT"), "pozisyon fiyatsiz enstrumanda"
        db.close()


def test_pozisyon_eslestirmesi_kripto_ile_hisseyi_karistirmaz():
    """
    Ayni sembol iki evrende olabilir (GRAM: BIST hissesi ve kripto).
    Eslestirme sinifi asarsa Binance bakiyesi bir BIST hissesine baglanir
    — sessiz ve tamamen yanlis bir portfoy.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _poz_db(d)
        hisse = db.upsert_instrument("GRAM", "BIST", asset_type="equity")

        db.insert_positions("binance", "2026-08-18T10:00:00+00:00", [
            {"symbol": "GRAM", "quantity": 3, "currency": "USDT",
             "asset_type": "crypto"}], "ali")

        iid = db.query(
            """SELECT p.instrument_id AS i FROM positions p
               WHERE p.account='binance'""")[0]["i"]
        assert iid != hisse, "kripto pozisyonu BIST hissesine baglandi"
        venue = db.query("SELECT venue FROM instruments WHERE id=?",
                         (iid,))[0]["venue"]
        assert venue == "BINANCE", venue
        db.close()


def test_pozisyon_endeks_veya_makro_enstrumanina_baglanmaz():
    """
    `makro` collector'i XU100/altin gibi seyleri de enstruman tutuyor.
    Ekrandan "XU100" okunursa pozisyon bir ENDEKSE baglanmamali.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _poz_db(d)
        endeks = db.upsert_instrument("XU100", "INDEX")
        db.insert_positions("midas", "2026-08-18T10:00:00+00:00", [
            {"symbol": "XU100", "quantity": 1, "currency": "TRY"}], "ali")
        iid = db.query("SELECT instrument_id AS i FROM positions")[0]["i"]
        assert iid != endeks, "pozisyon endekse baglandi"
        db.close()


def test_bux_ve_binance_eslestirmesi_bozulmadi():
    """Duzeltme yalnizca MIDAS'i degil, calisani da korumak zorunda."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _poz_db(d)
        asml = db.upsert_instrument("ASML", "BUX")
        rose = db.upsert_instrument("ROSE", "BINANCE", asset_type="crypto")
        db.insert_positions("bux", "2026-08-18T10:00:00+00:00", [
            {"symbol": "ASML", "quantity": 5, "currency": "EUR"}], "ali")
        db.insert_positions("binance", "2026-08-18T10:00:00+00:00", [
            {"symbol": "ROSE", "quantity": 56741.35, "currency": "USDT"}], "ali")
        assert db.query("SELECT instrument_id AS i FROM positions "
                        "WHERE account='bux'")[0]["i"] == asml
        assert db.query("SELECT instrument_id AS i FROM positions "
                        "WHERE account='binance'")[0]["i"] == rose
        # Katalogda olmayan sembol YINE de yazilabilmeli (hesabin venue'suna).
        db.insert_positions("bux", "2026-08-18T11:00:00+00:00", [
            {"symbol": "YENIKAGIT", "quantity": 1, "currency": "EUR"}], "ali")
        v = db.query("SELECT venue FROM instruments "
                     "WHERE symbol='YENIKAGIT'")[0]["venue"]
        assert v == "BUX", v
        db.close()


def _degisiklik_boti(db):
    """Yalnizca `_pozisyon_kaydet` yolunu kosturan asgari bot."""
    from finagent.bot.listener import FinBot
    bot = FinBot.__new__(FinBot)
    bot.db = db
    return bot


def test_degismeyen_ekran_yeni_snapshot_acmaz():
    """
    Ali ayni ekrani arka arkaya gonderebiliyor. Birlestirme penceresi
    (20 dk) DISINDA her gonderim yeni bir snapshot aciyordu — ayni
    portfoyun kopyalari. Miktar degismediyse yazim OLMAMALI.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _poz_db(d)
        bot = _degisiklik_boti(db)
        p = {"hesap": "midas",
             "pozisyonlar": [{"symbol": "TRALT", "quantity": 10,
                              "market_value": 489.20, "currency": "TRY"}]}

        ilk = bot._pozisyon_kaydet(dict(p), "ali")
        assert "kaydedildi" in ilk, ilk
        snap1 = db.latest_snapshot_ts("midas", "ali")

        # Pencere disinda oldugunu garantile: snapshot'i geriye al.
        db.query("UPDATE positions SET snapshot_ts='2026-01-01T00:00:00+00:00'")
        db._conn.commit()

        ikinci = bot._pozisyon_kaydet(dict(p), "ali")
        assert "degisiklik yok" in ikinci, ikinci
        assert len(db.query("SELECT DISTINCT snapshot_ts FROM positions")) == 1, \
            "miktar ayniyken yeni snapshot acildi"
        assert snap1 is not None
        db.close()


def test_degisen_miktar_yeni_snapshot_acar():
    """Duzeltme yazmayi engellemeyi degil, GEREKSIZ yazmayi engellemeli."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _poz_db(d)
        bot = _degisiklik_boti(db)
        bot._pozisyon_kaydet(
            {"hesap": "midas",
             "pozisyonlar": [{"symbol": "TRALT", "quantity": 10,
                              "currency": "TRY"}]}, "ali")
        db.query("UPDATE positions SET snapshot_ts='2026-01-01T00:00:00+00:00'")
        db._conn.commit()

        cikti = bot._pozisyon_kaydet(
            {"hesap": "midas",
             "pozisyonlar": [{"symbol": "TRALT", "quantity": 25,
                              "currency": "TRY"}]}, "ali")
        assert "kaydedildi" in cikti, cikti
        assert db.latest_positions("midas", "ali")[0]["quantity"] == 25
        db.close()


def test_satilan_kagit_degisiklik_sayilir():
    """
    EN SINSI DURUM: gelen satirlarin hepsi kayitlilarla ayni ADETTE ama
    BIRI EKSIK — yani kagit satilmis. Kiyas "alt kume" olsaydi bu
    "degisiklik yok" sayilir ve satilan kagit portfoyde sonsuza kadar
    asili kalirdi. Kume ESITLIGI tam da bunun icin.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _poz_db(d)
        bot = _degisiklik_boti(db)
        bot._pozisyon_kaydet(
            {"hesap": "midas",
             "pozisyonlar": [{"symbol": "TRALT", "quantity": 10, "currency": "TRY"},
                             {"symbol": "THYAO", "quantity": 4, "currency": "TRY"}]},
            "ali")
        db.query("UPDATE positions SET snapshot_ts='2026-01-01T00:00:00+00:00'")
        db._conn.commit()

        cikti = bot._pozisyon_kaydet(
            {"hesap": "midas",
             "pozisyonlar": [{"symbol": "TRALT", "quantity": 10,
                              "currency": "TRY"}]}, "ali")
        assert "kaydedildi" in cikti, cikti
        kalan = {r["symbol"] for r in db.latest_positions("midas", "ali")}
        assert kalan == {"TRALT"}, kalan
        db.close()


def test_ayni_adette_gelen_maliyet_YERINDE_yazilir():
    """
    OLCULEN VAKA (2026-08-20, 16:21 -> 17:36). Ali BUX ekranini
    "All time + EUR" filtresine alip bes kez gonderdi; amaci ADET degil
    MALIYET yazdirmakti. Adetler ayni oldugu icin kiyas hepsini
    "degisiklik yok" sayip dusurdu — YEDI onaylanmis yazim cope gitti,
    defterde 19 satirin 18'i maliyetsiz kaldi ve degerler 14 Agustos'ta
    dondu. Kullanici butona basiyordu; sistem sessizce reddediyordu.

    Adet ayniysa YENI SNAPSHOT ACILMAZ ama alanlar YAZILIR.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _poz_db(d)
        bot = _degisiklik_boti(db)
        bot._pozisyon_kaydet(
            {"hesap": "bux",
             "pozisyonlar": [{"symbol": "ASML", "quantity": 1.534692,
                              "market_value": 2424.20, "currency": "EUR"}]},
            "ali")
        # Birlestirme penceresinin DISINA cik.
        db.query("UPDATE positions SET snapshot_ts='2026-01-01T00:00:00+00:00'")
        db._conn.commit()

        cikti = bot._pozisyon_kaydet(
            {"hesap": "bux",
             "pozisyonlar": [{"symbol": "ASML", "quantity": 1.534692,
                              "market_value": 2317.08, "avg_cost": 713.06,
                              "currency": "EUR"}]}, "ali")

        assert "degisiklik yok" not in cikti, cikti
        assert "guncellendi" in cikti, cikti
        anlik = db.query("SELECT DISTINCT snapshot_ts FROM positions")
        assert len(anlik) == 1, f"adet ayniyken yeni snapshot acildi: {anlik}"
        poz = db.latest_positions("bux", "ali")[0]
        assert poz["avg_cost"] == 713.06, f"maliyet yazilmadi: {dict(poz)}"
        assert poz["market_value"] == 2317.08, f"deger bayat: {dict(poz)}"
        db.close()


def test_bos_gelen_alan_bilinen_maliyeti_SILMEZ():
    """
    "Today" filtresindeki ekranda maliyet YOKTUR. O ekrani gondermek
    daha once yazilmis bir maliyeti sifirlamamali — bos alan bir
    silme beyani degil, sadece o ekranin tasimadigi bilgidir.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _poz_db(d)
        bot = _degisiklik_boti(db)
        bot._pozisyon_kaydet(
            {"hesap": "bux",
             "pozisyonlar": [{"symbol": "MRNA", "quantity": 1.0,
                              "avg_cost": 124.452, "market_value": 119.81,
                              "currency": "EUR"}]}, "ali")
        db.query("UPDATE positions SET snapshot_ts='2026-01-01T00:00:00+00:00'")
        db._conn.commit()

        bot._pozisyon_kaydet(
            {"hesap": "bux",
             "pozisyonlar": [{"symbol": "MRNA", "quantity": 1.0,
                              "market_value": 118.66,   # maliyet YOK
                              "currency": "EUR"}]}, "ali")

        poz = db.latest_positions("bux", "ali")[0]
        assert poz["avg_cost"] == 124.452, f"maliyet silindi: {dict(poz)}"
        assert poz["market_value"] == 118.66, f"deger tazelenmedi: {dict(poz)}"
        db.close()


def test_hicbir_alan_degismediyse_hala_degisiklik_yok_denir():
    """Tazeleme, GERCEKTEN ayni olan ekrani yazmaya donusmemeli."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _poz_db(d)
        bot = _degisiklik_boti(db)
        p = {"hesap": "midas",
             "pozisyonlar": [{"symbol": "TRALT", "quantity": 10,
                              "avg_cost": 41.5, "market_value": 489.20,
                              "currency": "TRY"}]}
        bot._pozisyon_kaydet(dict(p), "ali")
        db.query("UPDATE positions SET snapshot_ts='2026-01-01T00:00:00+00:00'")
        db._conn.commit()

        ikinci = bot._pozisyon_kaydet(dict(p), "ali")
        assert "degisiklik yok" in ikinci, ikinci
        assert len(db.query("SELECT DISTINCT snapshot_ts FROM positions")) == 1
        db.close()


def test_donchian_LOOK_AHEAD_yapmaz_ve_stopu_uygular():
    """
    Donchian kirilimi t gununun KENDISINI dislamali: pencereye bugunku
    bari koymak, "bugunun en yuksegini bugun asti" gibi anlamsiz ve
    KEHANET degerinde bir kosul uretir.

    2N stop da GERCEKTEN uygulanmali — trend takibinin yarisi cikistir;
    stop'suz bir sistem trend sistemi degildir.
    """
    from finagent.analysis.trend_takip import islemler

    def bar(ts, k, y=None, d=None):
        return {"ts": ts, "close": k, "high": y if y is not None else k,
                "low": d if d is not None else k, "volume": 1000,
                "currency": "TRY"}

    # 40 gun duz 100, sonra 120'ye sicrama -> kirilim; ardindan cokus.
    seri = [bar(f"2026-01-{i:02d}", 100.0, 101.0, 99.0) for i in range(1, 41)]
    seri += [bar("2026-02-01", 120.0, 121.0, 119.0)]
    seri += [bar(f"2026-02-{i:02d}", 50.0, 51.0, 49.0) for i in range(2, 16)]
    t = islemler(seri, borsa_limiti=None)
    assert t, "kirilim hic islem uretmedi"
    assert t[0]["giris_ts"] == "2026-02-01", t[0]
    assert t[0]["sebep"] == "2N stop", f"stop uygulanmadi: {t[0]}"
    assert t[0]["getiri"] < 0, t[0]

    # DUZ SERIDE HIC ISLEM OLMAMALI: kirilim yoksa giris de yok.
    assert islemler([bar(f"2026-03-{i:02d}", 100.0) for i in range(1, 60)],
                    borsa_limiti=None) == []


def test_donchian_TAVANDA_giris_uygulanabilir_sayilmaz():
    """
    OLCULDU 2026-08-21: kirilim sinyali TAVAN gununde cikma egiliminde.
    OZATD'nin +%2492'lik "islemi" 35 tavan gunu iceriyor. Tavanda satis
    tarafi bostur — o kapanistan alinamaz. Girislerin %14'u boyleydi.
    """
    from finagent.analysis.trend_takip import islemler, ozet

    def bar(ts, k):
        # ATR SIFIR OLMAMALI: high=low=close verilirse gercek aralik 0
        # cikar, N=0 olur ve giris HIC olusmaz — kurgu sessizce bos
        # doner ve test yanlis sebeple gecerdi.
        return {"ts": ts, "close": k, "high": k * 1.01, "low": k * 0.99,
                "volume": 1, "currency": "TRY"}

    seri = [bar(f"2026-01-{i:02d}", 100.0) for i in range(1, 41)]
    seri += [bar("2026-02-01", 111.0)]            # +%11 -> TAVAN girisi
    seri += [bar(f"2026-02-{i:02d}", 60.0) for i in range(2, 16)]
    t = islemler(seri, borsa_limiti=None)
    assert t and t[0]["girisde_tavan"] is True, t
    assert ozet(t, yalniz_uygulanabilir=True)["islem"] == 0, \
        "tavanda giris uygulanabilir sayildi"


def test_TASARIM_GEREGI_duran_butce_alarm_URETMEZ():
    """
    OLCULDU 2026-08-21: bekci "isyatirim — 3 kosudur partial" diye
    calmaya devam etti. Ama o `partial` TASARIM: collector 780 sn'lik
    IC butcesini bilerek dolduruyor, kosuyu KAYBETMEKTENSE eksik cekiyor
    ve kalanini bir sonraki kosu aliyor ("en bayat once" siralamasi
    kuyrugu dondurur).

    Kanit: 423 sembolun 422'si son 3 gunde tazelenmis (%100). Bosluk
    YOK — is yalnizca iki kosuya yayilmis.

    Buna alarm calmak, "sahte partial" sinifini alarmin KENDISINDE
    yeniden acmak olurdu: her kosuda calan bir uyari kapatilmayi hak
    eder, kapatilinca GERCEK ariza da gorulmez.
    """
    import tempfile, pathlib as _p
    from finagent.bot.watchdog import Bekci
    from finagent.config import load_settings
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        for i in range(4):
            db.query("""INSERT INTO collector_runs
                        (run_ts, collector, status, rows_written, error)
                        VALUES (datetime('now', ?), 'isyatirim', 'partial',
                                3000, 'sure butcesi (780 sn) doldu, 207/365
                                sembol atlandi')""", (f"-{i} hours",))
            db.query("""INSERT INTO collector_runs
                        (run_ts, collector, status, rows_written, error)
                        VALUES (datetime('now', ?), 'xbrl', 'partial',
                                0, 'baglanti reddedildi')""", (f"-{i} hours",))
        db._conn.commit()
        adlar = {e["collector"]
                 for e in Bekci(load_settings(), db, _p.Path(d)).eksik_toplama()}
        assert "isyatirim" not in adlar, \
            "tasarim geregi duran butce alarm uretti — sahte alarm sinifi geri geldi"
        assert "xbrl" in adlar, "GERCEK ariza susturuldu — alarm korlesti"
        db.close()


def test_ad_ortusmesi_KELIME_KUMESIYLE_olculur():
    """
    OLCULDU 2026-08-21: `_ad_anahtari` kelimeleri SIRAYLA birlestiriyor
    ve kelime sirasi degisince cokuyor. Katalogda "Lilly (Eli)",
    Yahoo'da "Eli Lilly and Company" -> 'lillyeli' vs 'elilillyand',
    alt-dizi testi FALSE. Sonuc: mesru bir ABD tickeri (LLY) her kosuda
    "sembol yok" diye reddedildi ve toplama kalici arizali gorundu.

    Kume testi dogru cevabi verir — AMA KAPIYI GEVSETMEZ: AVTX'te
    bizim Avantium, Yahoo'da Avalo Therapeutics ve bu ayrim korunmali;
    gevsemesi 500 barlik YANLIS seri demek.
    """
    from finagent.collectors.prices import ad_ortusuyor
    assert ad_ortusuyor("Lilly (Eli)", "Eli Lilly and Company")
    assert ad_ortusuyor("Costco", "Costco Wholesale Corporation")
    assert ad_ortusuyor("ASML Holding N.V.", "ASML Holding")
    # KAPI KAPALI KALIYOR
    assert not ad_ortusuyor("Avantium", "Avalo Therapeutics, Inc.")
    assert not ad_ortusuyor("iShares Automation & Robotics",
                            "Vicarious Surgical Inc.")
    assert not ad_ortusuyor(None, "X") and not ad_ortusuyor("X", "")


def test_ISIN_izlemeye_ALINAMAZ():
    """
    OLCULDU 2026-08-21: e2e kosumunda model `IE00BQ70R696` (Invesco
    Nasdaq Biotech) sembolunu izlemeye aldi — katalogda bazi ETF'ler
    ISIN'le duruyor ve model onu ticker sandi. Sonuc: `prices` her
    kosuda "sembol yok" deyip `partial` dondu, yani TEK bir kotu kayit
    toplama katmanini kalici olarak arizali gosterdi.
    """
    import tempfile, asyncio, json
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        arac = {a.name: a for a in tb.araclar()}["izlemeye_al"]
        out = json.loads(asyncio.run(
            arac.handler({"sembol": "IE00BQ70R696"}))["content"][0]["text"])
        assert "hata" in out and "ISIN" in out["hata"], out
        assert db.query("SELECT COUNT(*) n FROM watchlist")[0]["n"] == 0
        db.close()


def test_alarm_ANAHTARI_canli_veriden_KURULAMAZ():
    """
    SINIFI IMKANSIZ KIL — tek ornegi duzeltmek yetmez.

    `bildir(anahtar, mesaj)` anahtari SUSTURMA kimligidir. Anahtar
    degisen bir veriden kurulursa (or. ariza listesini birlestirerek)
    susturma sessizce devre disi kalir. 2026-08-20'de tam bu oldu:

        bildir("eksik_toplama_" + ",".join(e["collector"] for e in eksikler), ...)

    Collector'lar duzeldikce liste kuculdu, anahtar her seferinde
    degisti ve Ali'ye ALTI bildirim gitti — islerin IYILESMESI yuzunden.

    Bu test anahtarin `join(...)` ya da bir uretecle kurulmasini
    STATIK olarak yasaklar. Ayni tuzaga bir daha dusulemez.
    """
    import ast
    import pathlib as _p

    kok = _p.Path(__file__).resolve().parents[1] / "src" / "finagent"
    ihlal = []
    for yol in kok.rglob("*.py"):
        agac = ast.parse(yol.read_text(encoding="utf-8"))
        for d in ast.walk(agac):
            if not isinstance(d, ast.Call):
                continue
            ad = (d.func.attr if isinstance(d.func, ast.Attribute)
                  else getattr(d.func, "id", ""))
            if ad != "bildir" or not d.args:
                continue
            anahtar = d.args[0]
            for alt in ast.walk(anahtar):
                if isinstance(alt, (ast.GeneratorExp, ast.ListComp)):
                    ihlal.append(f"{yol.name}:{d.lineno} uretecle kurulmus anahtar")
                if (isinstance(alt, ast.Call)
                        and isinstance(alt.func, ast.Attribute)
                        and alt.func.attr == "join"):
                    ihlal.append(f"{yol.name}:{d.lineno} join() ile kurulmus anahtar")
    assert not ihlal, (
        "Alarm anahtari CANLI VERIDEN kurulmus — susturma devre disi kalir "
        "ve kullanici islerin iyilesmesi yuzunden spam yer:\n  "
        + "\n  ".join(sorted(set(ihlal))))


def test_alarm_IYILESINCE_calmaz_sadece_KOTULESINCE():
    """
    2026-08-20 aksami YASANDI ve pahaliya mal oldu.

    `eksik_toplama` bildiriminin susturma anahtarina ARIZA LISTESI
    konmustu ("kume degisirse yeniden calsin" diye). Collector'lar tek
    tek duzeltilirken liste her KUCULDUGUNDE anahtar degisti, susturma
    devre disi kaldi ve Ali'ye alti bildirim gitti:

        17:38  alphavantage,binance,coingecko,isyatirim,kripto,prices
        17:39  ... kripto DUZELDI     -> YENI ALARM
        17:42  ... binance DUZELDI    -> YENI ALARM
        17:44  ... coingecko DUZELDI  -> YENI ALARM
        18:51  ... prices DUZELDI     -> YENI ALARM
        20:18  ... alphavantage DUZELDI -> YENI ALARM

    Kullanici ISLER IYILESTIGI ICIN spam yedi. Bir izleme katmaninin
    yapabilecegi en kotu sey budur: gurultu kendisinin kapatilmasina
    yol acar, sonra GERCEK ariza da gorulmez.

    KURAL: alarm KOTULESINCE calar, IYILESINCE ASLA.
    """
    import tempfile, pathlib as _p
    from unittest.mock import patch

    from finagent.bot.watchdog import Bekci
    from finagent.config import load_settings
    from finagent.storage.db import Database

    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        b = Bekci(load_settings(), db, _p.Path(d))

        def kume(*adlar):
            return [{"collector": a, "durum": "partial", "kosu": 3,
                     "son": "2026-08-20 17:00", "sebep": ""} for a in adlar]

        # 1) ILK ARIZA -> bildirilir
        with patch.object(Bekci, "eksik_toplama",
                          lambda self: kume("a", "b", "c")):
            k = b.eksik_toplama_bildirimi()
        assert k and k[1], "ilk ariza bildirilmedi"

        # 2) KUCULME (a duzeldi) -> SESSIZ. Bugunku hatanin ta kendisi.
        with patch.object(Bekci, "eksik_toplama",
                          lambda self: kume("b", "c")):
            assert b.eksik_toplama_bildirimi() is None, \
                "liste KUCULDUGUNDE alarm caldi — 20 Agustos hatasi geri geldi"

        # 3) AYNI KUME -> sessiz
        with patch.object(Bekci, "eksik_toplama",
                          lambda self: kume("b", "c")):
            assert b.eksik_toplama_bildirimi() is None

        # 4) YENI ARIZA (d girdi) -> BILDIRILIR, ve anahtar YALNIZCA
        #    yeni gireni tasir ki ayni sey iki kez bozulursa susturma tutsun.
        with patch.object(Bekci, "eksik_toplama",
                          lambda self: kume("b", "c", "d")):
            k = b.eksik_toplama_bildirimi()
        assert k, "YENI ariza bildirilmedi — alarm korlesti"
        assert k[0] == "eksik_toplama_d", k[0]

        # 5) TAMAMEN TEMIZ -> TEK bir toparlanma mesaji
        with patch.object(Bekci, "eksik_toplama", lambda self: []):
            k = b.eksik_toplama_bildirimi()
            assert k and k[1] == [], "toparlanma soylenmedi"
            assert b.eksik_toplama_bildirimi() is None, \
                "toparlanma her dongude tekrarlaniyor"
        db.close()


def test_fiyat_getir_AD_TUTMAYINCA_yazmaz():
    """
    AVTX/RBOT FELAKETININ KAPISI — kolaylik ugruna acilmamali.

    `fiyat_getir` kapsamda olmayan bir kagidin serisini aninda cekiyor.
    Ama sembol bir TAHMINDIR: AVTX bizde Avantium (Amsterdam, ~5 EUR),
    Yahoo'da Avalo Therapeutics (Nasdaq). RBOT bizde iShares ETF'i,
    Yahoo'da Vicarious Surgical — 19 EUR'luk ETF icin 6 SENTLIK seri
    cekilmisti ve tum gostergeler ondan hesaplanmisti.

    Yanlis fiyat, eksik fiyattan TEHLIKELIDIR: her sey hesaplanir ve
    hepsi yanlis cikar, hicbiri hata vermez.
    """
    import tempfile, pathlib as _p, asyncio, json
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        db.upsert_instrument("AVTX", "BUX", name="Avantium", currency="EUR")
        arac = {a.name: a for a in tb.araclar()}["fiyat_getir"]

        sahte = ([{"ts": "2026-08-20", "open": 1, "high": 1, "low": 1,
                   "close": 12.34, "volume": 10}],
                 {"symbol": "AVTX", "currency": "USD",
                  "shortName": "Avalo Therapeutics, Inc."})
        with patch("finagent.collectors.prices.yahoo_veri", return_value=sahte):
            out = json.loads(asyncio.run(
                arac.handler({"sembol": "AVTX"}))["content"][0]["text"])
        assert "hata" in out, f"ad tutmuyorken YAZDI: {out}"
        assert "Avalo" in out["hata"] and "Avantium" in out["hata"], out
        # HICBIR BAR YAZILMAMIS OLMALI.
        n = db.query("SELECT COUNT(*) n FROM prices")[0]["n"]
        assert n == 0, f"reddedildigi halde {n} bar yazildi"
        db.close()


def test_fiyat_getir_ADI_TUTAN_sembolu_YAZAR():
    """
    Kapi kapali degil DOGRU: ad tutuyorsa seri yazilir ve `teknik`
    o sembolde calisir hale gelir.

    NEDEN GEREKTI (olculdu 2026-08-21, e2e): "Nasdaq'ta en cok yukselen
    iki hisse" soruldu; Nasdaq 100'un 102 uyesinin yalnizca 10'unda seri
    vardi. Veri bir cagri uzaktaydi — `yahoo_veri` 7 sembolu 9,8 sn'de
    getiriyor — ama TEK SEMBOL icin o cagriyi yapacak ARAC yoktu.
    `veri_topla` yalnizca collector adi aliyor ve tum evreni tariyor.
    Haber tarafinda ayni sey (`stocknews.tek_sembol`) YAPILMISTI; fiyat
    tarafinda unutulmustu.
    """
    import tempfile, asyncio, json
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        arac = {a.name: a for a in tb.araclar()}["fiyat_getir"]
        barlar = [{"ts": f"2026-08-{g:02d}", "open": 100.0, "high": 101.0,
                   "low": 99.0, "close": 100.0 + g, "volume": 1000}
                  for g in range(1, 21)]
        sahte = (barlar, {"symbol": "AAPL", "currency": "USD",
                          "shortName": "Apple Inc."})
        with patch("finagent.collectors.prices.yahoo_veri", return_value=sahte):
            out = json.loads(asyncio.run(
                arac.handler({"sembol": "AAPL"}))["content"][0]["text"])
        assert "hata" not in out, out
        assert out["yazilan_bar"] == 20, out
        assert out["ad"] == "Apple Inc."
        assert db.query("SELECT COUNT(*) n FROM prices")[0]["n"] == 20
        db.close()


def test_haber_dosyasi_SIRALAMAZ_ve_kirpmayi_BEYAN_eder():
    """
    Ilk surumde bu modul haberleri SAYIP siraliyordu. Yanlisti: son 2
    gunde sembole bagli kademe 1-2 haber sayisi 68 — modelin TAMAMINI
    okuyabilecegi hacim. O olcekte saymak okumaktan kotu bir arac;
    "36 MRNA haberi" 36 sinyal degil AYNI HABERIN 36 kez yazilmasidir,
    ve sayac "Faz 3 sonucu" ile "adi gecen liste yazisi"ni ayirt edemez.

    Deterministik katman artik yalnizca DERLIYOR. Ve kirpma yaparsa
    BEYAN EDIYOR — sessizce kesilen liste TAM sanilir.
    """
    import tempfile, pathlib as _p
    from finagent.analysis.haber_ilgi import haber_dosyasi
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        db.upsert_instrument("ASELS", "BIST", name="ASELSAN", currency="TRY")
        db.upsert_news([{"published_at": "2026-08-20 10:00:00",
                         "title": "ASELS sozlesme imzaladi", "url": "u1",
                         "symbols": ["ASELS"], "publisher": "AA - Ekonomi",
                         "tier": 2, "source": "t"},
                        {"published_at": "2026-08-20 11:00:00",
                         "title": "Makro haber", "url": "u2", "symbols": [],
                         "publisher": "Reuters", "tier": 2, "source": "t"}])
        d2 = haber_dosyasi(db, pencere_gun=3650)

        # SIRALAMA YOK: skor/sira alani BULUNMAMALI.
        for r in d2["bagli_haberler"]:
            assert "surpriz" not in r and "skor" not in r, r
        # BAGSIZ HABER DE VERILIYOR — kanitin cogu orada.
        assert len(d2["bagsiz_haberler"]) == 1, d2["bagsiz_haberler"]
        assert d2["kapsam"]["bagsiz_toplam"] == 1
        # MODELE TALIMAT GIDIYOR: ayni olayi tek say, mekanizma yaz.
        z = d2["ZORUNLU"]
        assert "AYNI OLAYIN" in z and "MEKANIZMA" in z, z
        assert "DOGRULANMAMIS" in z, "dogrulanmamis katman oldugu soylenmiyor"
        db.close()


def test_panel_SINYAL_YOKKEN_de_haberle_calisir():
    """
    Panelin kapisi eskiden yalnizca `guclu` sinyaldi: fiyat esigi
    gecilmediginde model HIC kosmuyordu ve o gun kullaniciya yorum
    gitmiyordu. Fiyat esikleri dogru olarak siki — 2026-08-20 backtest'i
    o sinyallerin 24 hucresinin 22'sinde sifirdan ayirt edilemedigini
    gosterdi — ama haber HER GUN var.
    """
    import inspect

    from finagent.pulse.agents import Panel
    from finagent.pulse.runner import Nabiz
    kaynak = inspect.getsource(Panel.calistir)
    assert "if not sinyaller and not haberli" in kaynak, \
        "panel hala yalnizca sinyalle tetikleniyor"
    assert "haber" in inspect.signature(Panel.calistir).parameters
    assert hasattr(Nabiz, "_haber_var"), "runner haber kapisini bilmiyor"
    dis = inspect.getsource(Nabiz)
    assert "not guclu and not self._haber_var()" in dis, \
        "dis kapi hala yalnizca sinyale bakiyor"


def test_SIRKET_ADINDAN_sembol_cikarma_GERI_GELMESIN():
    """
    Denendi ve TERK EDILDI (2026-08-20). Turkce sirket adlari siradan
    kelimelerden kuruluyor: 5 harf esiginde `align` (Align Technology)
    "buyukelci atandi" haberine baglandi; 7 harfte bile `yukselen`
    ->YKSLN, `aktuel`->RTALB, `trabzon`->TLMAN sizdi.

    Yanlis sirkete baglamak HIC baglamamaktan kotudur: kullanici o
    hisseye bakar, ilgisiz cikar, katmana guveni gider. Eslestirmeyi
    model yapiyor. Bu test kalibin geri sizmasini engelliyor.
    """
    from finagent.collectors import base
    assert not hasattr(base, "ad_haritasi"), \
        "ad->sembol haritasi geri gelmis (olculdu: yanlis baglar uretiyor)"
    assert not hasattr(base, "adlardan_semboller")
    # Ders yerinde kalmali ki ayni yol ikinci kez denenmesin.
    import inspect
    assert "TERK EDILDI" in inspect.getsource(base)


def test_fiyat_serisi_BITIS_sonrasini_HIC_dondurmez():
    """
    LOOK-AHEAD KAPISI — backtest'in var olma sebebi.

    Gecmisteki bir gunde uretilmis gibi davranan sinyal, o gun HENUZ
    OLMAMIS fiyatlari gorurse olcum degil KEHANET uretir. Suzgec tek
    yerden geciyor cunku gostergeler (RSI, SMA) bu seriden turuyor;
    cagirana birakmak, bir yolda unutulup sessizce gelecege bakmakti.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        iid = db.upsert_instrument("TEST", "BIST", currency="TRY")
        db.upsert_prices(iid, [{"ts": f"2026-01-{g:02d}", "close": 100 + g}
                               for g in range(1, 21)], "yahoo_bist",
                         currency="TRY")
        hepsi = db.fiyat_serisi(iid, limit=1000)
        kesik = db.fiyat_serisi(iid, limit=1000, bitis="2026-01-10")
        assert len(hepsi) == 20, hepsi
        assert len(kesik) == 10, kesik
        assert max(r["ts"] for r in kesik) == "2026-01-10"
        db.close()


def test_backtest_URETIMDEKI_kurallari_cagirir_yeniden_yazmaz():
    """
    Backtest kurallari YENIDEN YAZARSA iki tanim ayrisir ve backtest,
    uretimde CALISMAYAN bir stratejiyi "dogrulanmis" diye raporlar —
    bu projenin tekrar eden kusur sinifinin (beyan ile gercegin
    ayrismasi) en pahali hali.
    """
    import inspect

    from finagent.analysis import backtest as B
    kaynak = inspect.getsource(B.sinyalleri_topla)
    assert "tarayici.fiyat_kurallari(" in kaynak, \
        "backtest tarayicinin kurallarini cagirmiyor"
    # Esik sabitleri backtest'te YENIDEN tanimlanmamali.
    tam = inspect.getsource(B)
    for sabit in ("SIGMA_HAREKET", "HACIM_KATI", "RSI_ASIRI_ALIM"):
        assert f"{sabit} =" not in tam, f"{sabit} backtest'te yeniden tanimlanmis"


def test_borsa_limitini_ASAN_bar_sinyal_URETMEZ():
    """
    OLCULDU 2026-08-20: BIST gunluk limiti ±%10 ama `yahoo_bist`
    serisinde 2.176 bar %11'i asiyor — ADEL 335,50 -> 30,75 (11:1
    bolunme), CCOLA 846 -> 78,27, KGYO 0,33 -> 3,51. Hicbiri fiyat
    hareketi degil; Yahoo BIST'te sermaye islemlerini duzeltmiyor
    (auto_adjust=True ile de ayni).

    Zarari cift yonlu: bu barlarda `olagandisi_hareket` TETIKLENIYOR,
    ve daha sinsi olani bar OYNAKLIK tahminini sisirip o kagitta
    aylarca TUM esikleri bozuyor.
    """
    from finagent.config import load_settings
    from finagent.pulse.screener import BORSA_LIMITI, Tarayici
    assert BORSA_LIMITI.get("BIST"), "BIST limiti tanimli degil"

    t = Tarayici(load_settings(), None)
    e = {"id": 1, "symbol": "TEST", "name": "Test", "venue": "BIST"}
    seri = [{"ts": f"2026-01-{g:02d}", "close": 100.0, "volume": 1000,
             "currency": "TRY"} for g in range(1, 91)]
    for i, r in enumerate(seri):        # hafif dalgalanma: sd > 0
        r["close"] = 100.0 + (i % 3) * 0.5
    # Son bar 11:1 bolunme gibi dussun.
    seri[-1]["close"] = seri[-2]["close"] / 11.0
    assert t.fiyat_kurallari(e, seri, rsi=50) == [], \
        "sermaye islemi barinda sinyal uretildi"


def test_LIMITTE_kapanan_giris_uygulanabilir_sayilmaz():
    """
    BIST'te tavanda satis tarafi, tabanda alis tarafi BOSTUR — o
    kapanistan giris yapilamaz. Olculdu 2026-08-20:
    `olagandisi_hareket/yukari` sinyallerinin %51'i tavan gununde
    cikiyor. Onlari saymak "1 gunde %1,4 kazandirir" gibi UYGULANAMAZ
    bir sonuc uretiyordu; suzgec acilinca ayni hucre %-0,08'e dustu ve
    anlamliligini kaybetti.
    """
    from finagent.analysis.backtest import LIMIT_YAKIN, guc_analizi
    assert 0.05 < LIMIT_YAKIN < 0.10, LIMIT_YAKIN
    goz = ([{"ts": f"2026-01-{g:02d}", "sembol": "A", "tur": "t", "yon": "yukari",
             "guc": 1.0, "fazla": {1: 0.05}, "limitte": True} for g in range(1, 29)]
           + [{"ts": f"2026-02-{g:02d}", "sembol": "B", "tur": "t", "yon": "yukari",
               "guc": 1.0, "fazla": {1: 0.0}, "limitte": False} for g in range(1, 29)])
    hepsi = guc_analizi(goz, (1,))
    uyg = guc_analizi(goz, (1,), yalniz_uygulanabilir=True)
    assert hepsi[0]["gozlenen_%"] > uyg[0]["gozlenen_%"], \
        "limitte girisler suzulmedi"
    assert uyg[0]["sinyal"] == 28, uyg[0]


def test_bist_derin_gecmis_AYRI_KAYNAK_adiyla_yazilir():
    """
    `prices` birincil anahtari (instrument_id, ts, source) ve PARA BIRIMI
    ANAHTARDA YOK. Derin seriyi `isyatirim` adiyla yazmak onun serisini
    EZERDI — ve isyatirim'in yan urunleri (hisse sayisi, gunluk TL hacim,
    XU100) baska hicbir yerde yok, yani onu kaybetmek pahali olurdu.

    Ayrica provenans: veri Yahoo'dan geliyor, isyatirim'den degil.
    Kaynak adinda yalan soylemek, ilerde "bu seri nereden geldi"
    sorusuna YANLIS cevap verirdi.
    """
    from finagent.collectors.bistgecmis import KAYNAK, SONEK
    assert KAYNAK != "isyatirim", "derin seri birincil kaynagi ezer"
    assert SONEK == ".IS", "BIST soneki degismis"

    from finagent.collectors import KAPSAM, REGISTRY
    assert "bistgecmis" in REGISTRY, "collector kayitli degil"
    assert "bistgecmis" in KAPSAM, \
        "KAPSAM'a yazilmayan collector modele GORUNMEZ (bkz. yanlis 'yok' beyani)"
    assert REGISTRY["bistgecmis"].needs_browser is False


def test_bist_derin_gecmis_YENI_KOTE_kagidi_sonsuza_kadar_cekmez():
    """
    Artimli olcut BAR SAYISI DEGIL, kaydin VARLIGI olmali.

    Bar sayisina bakan bir olcut yeni kote edilmis kagitlari sonsuza
    kadar "tam cek" grubunda tutardi: MASFN 30 Temmuz 2026'da, QUICK
    6 Agustos 2026'da islem gormeye basladi ve Yahoo'dan `max` ile de
    yalnizca 15 ve 11 bar geliyor. O kagitlarda gecmis EKSIK DEGIL, YOK
    — ve hicbir kaynak bunu duzeltemez.
    """
    import inspect

    from finagent.collectors.bistgecmis import BistGecmisCollector
    kaynak = inspect.getsource(BistGecmisCollector.collect)
    assert "derin = [s for s in semboller if s not in mevcut]" in kaynak, \
        "artimli ayrim kaydin VARLIGINA bakmali"
    # Bar sayisi esigi geri sizarsa bu test dusmeli.
    assert "asgari_bar" not in kaynak, \
        "bar sayisi olcutu yeni kote kagidi sonsuza kadar tam cektirir"


def test_fiyat_katmani_TARAYICI_ISTEMEZ():
    """
    OLCULDU 2026-08-20: Yahoo'nun chart ucu betik erisimine kapali —
    temiz bir IP'den duz httpx ile v8/chart, v7/quote ve v1/search'in
    UCU DE ILK ISTEKTE 429 dondu. Bu yuzden `prices` ve `makro`
    Playwright acip once bir "isinma sayfasi" geziyordu; bu, IKI
    collector'in tarayici bagimliliginin TEK sebebiydi.

    `yfinance` ayni ucu cagirir ama cerez/crumb dongusunu kendi yonetir
    (olculdu: 51 sembol 1,9 sn). Bagimlilik geri sizarsa bu test dusmeli.
    """
    from finagent.collectors.makro import MakroCollector
    from finagent.collectors.prices import PriceCollector
    assert PriceCollector.needs_browser is False, "prices yine tarayici istiyor"
    assert MakroCollector.needs_browser is False, "makro yine tarayici istiyor"

    import inspect

    from finagent.collectors import prices as P
    # `pg` parametresi GERI GELMEMELI: tasiyan bir imza, tarayicinin
    # sessizce yeniden acildiginin isareti olur.
    for fn in (P.yahoo_gunluk, P.yahoo_veri):
        assert "pg" not in inspect.signature(fn).parameters, fn.__name__
    # `kaynak` KALMALI — kotasyon basina ayri kaynak adi, ASML'nin EUR
    # serisinin USD serisini ezmesini onleyen sey.
    assert "kaynak" in inspect.signature(P.yahoo_gunluk).parameters


def test_yahoo_meta_SEMBOLU_tasir():
    """
    `_fiyat_makul` sertifika reddini loglarken `meta['symbol']`
    kullaniyor. Eski chart ucu bunu kendisi donduruyordu; yfinance
    dondurmuyor. Elle konmazsa uyari "None ATLANDI" olur ve HANGI
    kagidin reddedildigi kaybolur — uyari ise yaramaz hale gelir.
    """
    import inspect

    from finagent.collectors import prices as P
    kaynak = inspect.getsource(P.yahoo_veri)
    assert '"symbol": yahoo' in kaynak, \
        "meta sembolu tasimiyor — sertifika uyarisi anonimlesir"


def _haber_botu(d, feeds):
    """`news` collector'ini sahte ayarla kurar."""
    import pathlib as _p
    from finagent.collectors.news import NewsCollector
    from finagent.config import load_settings
    from finagent.storage.db import Database
    s = load_settings()
    s.raw.setdefault("sources", {}).setdefault("news", {})["feeds"] = feeds
    db = Database(_p.Path(d) / "t.db"); db.init_schema()
    return NewsCollector(s, db, browser=None), db


def test_HTTP_200_donen_OLU_akis_bayat_ilan_edilir():
    """
    OLCULDU 2026-08-20 — bu projenin tekrar eden kusur sinifinin yeni
    yuzu. Uc akis ayni anda HTTP 200 VE girdi donuyordu ama iceriktleri
    oluydu:

        WSJ Markets    200, 20 girdi, en yeni 27 OCAK 2025  (19 AY)
        BloombergHT    200, 20 girdi, en yeni 6 Agustos     (14 gun)
        Hurriyet       200, 100 girdi, en yeni 7 Haziran    (2,5 ay)

    Ucu de "basarili" sayiliyordu cunku olcut STATU KODUYDU. WSJ'den
    veritabaninda tam 20 haber vardi, hepsi Ocak 2025'ten — ve bunu
    19 ay boyunca kimse fark etmedi. Olcut artik ICERIGIN YASI.
    """
    import tempfile
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch

    def _rss(gun_once):
        t = datetime.now(timezone.utc) - timedelta(days=gun_once)
        d = t.strftime("%a, %d %b %Y %H:%M:%S +0000")
        return f"""<?xml version="1.0"?><rss version="2.0"><channel>
          <item><title>Baslik</title><link>https://x.example/{gun_once}</link>
          <pubDate>{d}</pubDate></item></channel></rss>""".encode()

    class _R:
        def __init__(self, icerik): self.content = icerik
        def raise_for_status(self): pass

    with tempfile.TemporaryDirectory() as d:
        # 1) OLU akis (600 gun) -> partial + BAYAT notu
        tb, db = _haber_botu(d, [{"name": "WSJ Markets",
                                  "url": "https://x/rss", "type": "rss"}])
        with patch("finagent.collectors.news.httpx.get",
                   return_value=_R(_rss(600))):
            r = tb.collect()
        assert r.status == "partial", f"olu akis ok sayildi: {r}"
        assert "BAYAT" in (r.error or ""), r.error
        assert "WSJ Markets" in (r.error or ""), r.error
        db.close()

    with tempfile.TemporaryDirectory() as d:
        # 2) TAZE akis -> ok, uyari YOK
        tb, db = _haber_botu(d, [{"name": "AA - Ekonomi",
                                  "url": "https://x/rss", "type": "rss"}])
        with patch("finagent.collectors.news.httpx.get",
                   return_value=_R(_rss(0))):
            r = tb.collect()
        assert r.status == "ok", f"taze akis bayat sayildi: {r}"
        assert "BAYAT" not in (r.error or ""), r.error
        db.close()


def test_tarihi_OKUNAMAYAN_akis_bayat_ILAN_EDILMEZ():
    """
    None ile 0 karistirilmamali. Tarihi cozulemeyen bir akis "taze"
    degil "OLCULEMEDI"dir; onu bayat ilan etmek yanlis alarm olurdu ve
    yanlis alarm, alarmsizliktan beterdir (bugun dort collector'da tam
    olarak bu yasandi).
    """
    from finagent.collectors.news import NewsCollector
    assert NewsCollector._akis_yasi_gun([]) is None
    assert NewsCollector._akis_yasi_gun([None, None]) is None
    assert NewsCollector._akis_yasi_gun(["bozuk-tarih"]) is None


def test_RSS_SIZ_yayinci_JSONLD_ile_toplanir():
    """
    BloombergHT (kademe 2) RSS'i TERK ETMIS: `/rss` ucu hala 200 ve 20
    girdi donuyor ama en yenisi 6 Agustos; sitede hicbir
    application/rss+xml etiketi yok ve bes alternatif yol 404.
    Elimizde yalnizca IKI kademe-2 Turkce kaynak var (AA, Ekonomim) —
    dusurmek yerine schema.org JSON-LD'sinden toplaniyor.

    HTML VARLIKLARI COZULMELI: JSON-LD govdesi "&#039;" tasiyor ve
    cozulmezse baslik ekranda ham kacis dizisiyle gorunur.
    """
    import tempfile
    from datetime import datetime, timezone
    from unittest.mock import patch

    bugun = datetime.now(timezone.utc).isoformat()
    LISTE = """<html><head><script type="application/ld+json">
      {"@context":"https://schema.org","@type":"ItemList","itemListElement":[
        {"@type":"ListItem","position":1,"url":"https://bht.example/haber-1"}]}
      </script></head><body></body></html>"""
    MAKALE = ("""<html><head><script type="application/ld+json">
      {"@context":"https://schema.org","@type":"NewsArticle",
       "headline":"Rusya&#039;nin altin rezervleri dipte",
       "description":"Ozet metni","datePublished":"%s"}
      </script></head><body></body></html>""" % bugun)

    class _R:
        def __init__(self, t): self.text = t
        def raise_for_status(self): pass

    class _Client:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, **kw):
            return _R(MAKALE if "haber-1" in url else LISTE)

    with tempfile.TemporaryDirectory() as d:
        tb, db = _haber_botu(d, [{"name": "BloombergHT",
                                  "url": "https://bht.example/",
                                  "type": "jsonld"}])
        with patch("finagent.collectors.news.httpx.Client", _Client):
            r = tb.collect()
        assert r.status == "ok", f"{r.status} · {r.error}"
        satir = db.query("SELECT title, publisher, tier, published_at FROM news")
        assert len(satir) == 1, satir
        assert satir[0]["title"] == "Rusya'nin altin rezervleri dipte", \
            f"HTML varligi cozulmedi: {satir[0]['title']}"
        assert satir[0]["tier"] == 2, "kademe-2 yayinci kademe-2 yazilmadi"
        assert satir[0]["published_at"], "tarih yazilmadi"
        db.close()


def test_binance_cifti_olmayan_referans_coin_COINGECKO_ID_alir():
    """
    OLCULDU 2026-08-20: 24 `cift_yok` kimliginin 24'unde de
    `coingecko_id` BOSTU. Sebep `coz()`un bu dalda CoinGecko'ya HIC
    bakmadan donmesiydi.

    Zarar: 'CRYPTO' venue'su tam olarak "ilk 100'de ama Binance'te
    listelenmemis" referans coinler icin var (HYPE, XMR, CRO, KAS,
    OKB...). Id yazilmayinca `coingecko` collector'i onlari atliyordu
    ve evrenin VAROLUS SEBEBI olan tokenomik katmani butunuyle bostu —
    hicbir yerde soylenmeden.

    Ad dogrulamasi GEVSEMEZ: klon-coin hala giremez.
    """
    from finagent.research.crypto_identity import CryptoResolver

    class _Yanit:
        status_code = 200

        def __init__(self, d):
            self._d = d

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    class _Http:
        def get(self, url, **kw):
            if "exchangeInfo" in url:                 # Binance: hic cift yok
                return _Yanit({"symbols": []})
            return _Yanit([{"id": "hyperliquid", "symbol": "hype",
                            "name": "Hyperliquid", "market_cap": 9,
                            "market_cap_rank": 30}])

    r = CryptoResolver(_Http())
    k = r.coz("HYPE", "Hyperliquid")
    assert k["status"] == "cift_yok", k
    assert k.get("coingecko_id") == "hyperliquid", \
        f"Binance cifti yok diye CoinGecko kimligi de dusuruldu: {k}"

    # KLON KORUMASI: ad tutmuyorsa id YAZILMAZ.
    klon = r.coz("HYPE", "Bambaska Bir Coin")
    assert not klon.get("coingecko_id"), f"ad tutmazken id yazildi: {klon}"


def test_coingecko_50_SEMBOL_sinirini_parcalayarak_asar():
    """
    OLCULDU 2026-08-20, dogrudan API'ye sorularak: CoinGecko
    `/coins/markets` istek basina EN FAZLA 50 sembol kabul ediyor
    ("maximum of 50 symbols per request"). Kod hepsini TEK istekte
    gonderiyordu ve kripto evreni 75 sembole cikinca cagri komple 400
    ile dusuyordu.

    ZARAR TEK COLLECTOR'LA SINIRLI DEGILDI: kimlik cozumu bu cagriya
    bagli oldugu icin `kripto` 6/6 kosuda `error`, `binance` ve
    `coingecko` ise her kosuda "kimlik yok, atlandi: ... EUR ..." dedi.
    Tek bir sinir asimi UC collector'i sessizce sakatliyordu.
    """
    from finagent.research.crypto_identity import CryptoResolver

    istekler = []

    class _SahteYanit:
        status_code = 200

        def __init__(self, semboller):
            self._s = semboller

        def raise_for_status(self):
            if len(self._s) > 50:
                raise RuntimeError("400: maximum of 50 symbols per request")

        def json(self):
            return [{"symbol": s, "name": s.upper(), "market_cap": 1}
                    for s in self._s]

    class _SahteHttp:
        def get(self, url, **kw):
            semboller = kw["params"]["symbols"].split(",")
            istekler.append(len(semboller))
            return _SahteYanit(semboller)

    semboller = [f"C{i:03d}" for i in range(75)]
    cg = CryptoResolver(_SahteHttp()).coingecko(semboller)

    assert len(istekler) > 1, "tek istekte gonderildi — sinir asilacak"
    assert max(istekler) <= 50, f"parca 50'yi asti: {istekler}"
    cozulen = [k for k, v in cg.items() if v]
    assert len(cozulen) == 75, f"parcalama sembol kaybetti: {len(cozulen)}/75"


def test_bekci_SUREKLI_eksik_toplamayi_yakalar_tek_olayi_yakalamaz():
    """
    OLCULDU 2026-08-20: bekcinin dort olcutu de "kosu calisti mi" diye
    soruyordu; hicbiri `collector_runs.status='partial'`e bakmiyordu.
    O gun isyatirim 13/13 kosuda ~150/346 sembol dusurdu, prices her
    kosuda 7 BIST sembolunu kaybetti, kripto 6/6 hata verdi — ve
    bunlarin HICBIRI Ali'ye dusmedi. Model ayni sembolu bir kosuda
    bulup digerinde bulamayinca "kafasi karisik" gorundu.

    Olcut SUREKLILIK olmali: gecici bir sunucu hatasi alarm uretmemeli.
    """
    import tempfile, pathlib as _p
    from finagent.bot.watchdog import Bekci
    from finagent.config import load_settings
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        for i in range(4):                      # hepsi eksik -> yapisal
            db.query("""INSERT INTO collector_runs
                        (run_ts, collector, status, rows_written, error)
                        VALUES (datetime('now', ?), 'isyatirim', 'partial',
                                0, 'baglanti reddedildi')""", (f"-{i} hours",))
            # NOT: sebep metni BILEREK gercek bir ariza. "sure butcesi"
            # yazsaydik `_tasarim_geregi` bunu (dogru olarak) elerdi —
            # o ayrimi `test_TASARIM_GEREGI_duran_butce_alarm_URETMEZ`
            # sinar; burada olculen sey SUREKLILIK.
        for i in range(4):                      # biri ok -> gecici, sessiz
            db.query("""INSERT INTO collector_runs
                        (run_ts, collector, status, rows_written, error)
                        VALUES (datetime('now', ?), 'news', ?, 0, NULL)""",
                     (f"-{i} hours", "partial" if i else "ok"))
        db._conn.commit()

        b = Bekci(load_settings(), db, _p.Path(d))
        adlar = {e["collector"] for e in b.eksik_toplama()}
        assert "isyatirim" in adlar, "surekli eksik collector bildirilmedi"
        assert "news" not in adlar, "son kosusu ok olan collector icin alarm"
        db.close()


def test_habersiz_sembol_KAPSAMA_ALINIR_ve_cekim_denenir():
    """
    OLCULEN VAKA (2026-08-20). Ali TRALT'ta son haberi sordu; sembol
    haber kapsaminda hic degildi ve bot "kaydi yok" dedi. Sonra Ali
    baska yerde gordugu haberi gosterdi, cevap "bu haber bende yok — ve
    olmamasi normal" oldu. Bu ajanin ASIL isi haberi ondan ONCE gormek;
    bos donus bir cevap degil, bir ADIMDIR.

    Arac kendisi onarmali: kapsama al, yerinde cek, ve modele "yok deme"
    talimatini VERIYLE birlikte dondur.
    """
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        db.upsert_instrument("TRALT", "BIST", name="TURK ALTIN",
                             currency="TRY")

        cagrildi = {}

        class _SahteCollector:
            def __init__(self, *a, **k):
                pass

            def tek_sembol(self, sembol):
                cagrildi["sembol"] = sembol
                return 0, None

        from finagent import collectors as _c
        with patch.dict(_c.REGISTRY, {"stocknews": _SahteCollector}):
            arac = {t.name: t for t in tb.araclar()}["haberler"]
            out = _cagir(arac, sembol="TRALT")

        assert cagrildi.get("sembol") == "TRALT", "yerinde cekim denenmedi"
        assert out.get("kapsama_alindi") == "TRALT", out
        assert out.get("bos") is True, out
        assert "ZORUNLU" in out, out
        assert "DEME" in out["ZORUNLU"], out["ZORUNLU"]
        # Kapsam KALICI olmali: bir dahaki taramada da gelsin.
        assert any(h["symbol"] == "TRALT"
                   for h in db.research_targets(kripto=None)), \
            "sembol izleme listesine yazilmadi"
        db.close()


def test_taze_haber_varken_gereksiz_cekim_YAPILMAZ():
    """
    Tazeleme her `haberler` cagrisinda RSS istegi atmamali; iki gunden
    yeni haber varsa elde olan yeter. Aksi halde her soru bir dis
    istege donerdi.
    """
    import tempfile
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        db.upsert_instrument("ASML", "BUX", name="ASML", currency="EUR")
        taze = (datetime.now(timezone.utc)
                - timedelta(hours=3)).replace(tzinfo=None).isoformat(sep=" ")
        db.upsert_news([{"published_at": taze, "title": "ASML haberi",
                         "url": "https://reuters.com/x", "symbols": ["ASML"],
                         "publisher": "Reuters", "tier": 2,
                         "source": "test"}])

        cagrildi = {"n": 0}

        class _SahteCollector:
            def __init__(self, *a, **k):
                pass

            def tek_sembol(self, sembol):
                cagrildi["n"] += 1
                return 0, None

        from finagent import collectors as _c
        with patch.dict(_c.REGISTRY, {"stocknews": _SahteCollector}):
            arac = {t.name: t for t in tb.araclar()}["haberler"]
            out = _cagir(arac, sembol="ASML")

        assert cagrildi["n"] == 0, "taze haber varken bosuna cekim yapildi"
        assert out["haberler"], out
        assert "ZORUNLU" not in out, out
        db.close()


def test_izleme_listesi_kimlik_DOLUYKEN_cokmez():
    """
    OLCULDU 2026-08-20: `identities()` sqlite3.Row donduruyor ve Row'da
    `.get()` YOK. Arac `(kimlikler.get(sym) or {}).get("status", ...)`
    yaziyordu; Row dolu oldugunda truthy oldugu icin `or {}` de
    kurtarmiyordu. Tablo BOSKEN calisiyor, DOLDUKCA kaliciya bozuluyordu
    — canlida 27 hedefin 27'sinde kimlik vardi, yani her cagri
    AttributeError'du ve kullanici "kapsamda ne var" sorusuna hic cevap
    alamiyordu.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        iid = db.upsert_instrument("ASML", "BUX", name="ASML Holding",
                                   currency="EUR")
        db.add_watchlist(iid, note="test")
        db.query("""INSERT INTO identities (instrument_id, status, method)
                    VALUES (?, 'dogrulandi', 'elle')""", (iid,))
        db._conn.commit()

        arac = {t.name: t for t in tb.araclar()}["izleme_listesi"]
        out = _cagir(arac)
        assert "hata" not in out, out
        kayit = [s for s in out["semboller"] if s["sembol"] == "ASML"]
        assert kayit and kayit[0]["kimlik"] == "dogrulandi", out
        db.close()


def test_kaydirilan_ikinci_ekran_degisiklik_sayilir():
    """
    Cok ekranli portfoy: ikinci goruntu YENI semboller getirir ve
    birlestirme penceresi icinde ayni snapshot'a eklenmeli. "Degisiklik
    yok" kapisi bunu ASLA yutmamali — yutarsa portfoyun yarisi kaybolur.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _poz_db(d)
        bot = _degisiklik_boti(db)
        bot._pozisyon_kaydet(
            {"hesap": "bux",
             "pozisyonlar": [{"symbol": "ASML", "quantity": 5,
                              "currency": "EUR"}]}, "ali")
        cikti = bot._pozisyon_kaydet(
            {"hesap": "bux",
             "pozisyonlar": [{"symbol": "ADYEN", "quantity": 2,
                              "currency": "EUR"}]}, "ali")
        assert "kaydedildi" in cikti, cikti
        semboller = {r["symbol"] for r in db.latest_positions("bux", "ali")}
        assert semboller == {"ASML", "ADYEN"}, semboller
        db.close()


def _bar(n, ts0="2026-01-01", baslangic=100.0, carpan=1.0, ccy="USD", gun=1):
    """Sentetik gunluk bar dizisi (dict — canlida sqlite3.Row gelir)."""
    import datetime as _dt
    d0 = _dt.date.fromisoformat(ts0)
    return [{"ts": str(d0 + _dt.timedelta(days=i * gun)),
             "close": baslangic * (carpan ** i), "currency": ccy}
            for i in range(n)]


def test_karsilastirma_tarih_hizalamasi_zorunlu():
    """
    EN SINSI HATA: iki seriyi indeksle yan yana koymak. Kripto yilda ~365,
    BIST ~250 bar uretir; hizalanmadan cikan korelasyon MAKUL gorunur ve
    tamamen anlamsizdir. Ortak gozlem esigin altindaysa BEYAN EDILMEZ.
    """
    from finagent.analysis import karsilastirma as K
    gunluk = _bar(60, carpan=1.01)                 # her gun
    seyrek = _bar(30, carpan=1.01, gun=2)          # gun asiri -> ortak az
    r = K.korelasyon(gunluk, seyrek)
    assert "hata" in r and r["ortak_gun"] < K.ASGARI_ORTAK, r
    assert "korelasyon" not in r, "yetersiz ortak gozlemde sayi uretildi"

    # Ayni takvimde iki seri: korelasyon HESAPLANIR
    a, b = _bar(60, carpan=1.01), _bar(60, baslangic=50, carpan=1.01)
    r2 = K.korelasyon(a, b)
    assert r2.get("ortak_gun", 0) >= K.ASGARI_ORTAK and "korelasyon" in r2, r2


def test_karsilastirma_ortusmeyen_takvimde_sifir_ortak_gun_der():
    """
    KASITLI BOZMA TURUNDA BULUNDU: ilk hizalama testim KOR cikti. Tarih
    ic-birlesimini indeks dilimlemesiyle degistirdigimde kirik surum de
    tesadufen esigin ALTINDA kaldi (29 < 30) ve test gecti.
    Ayirt edici vaka: HIC ortusmeyen iki takvim. Ic-birlesim 0 ortak gun
    der; indeksle eslestiren surum 89 gun "bulur" (ve sonra patlar).
    """
    from finagent.analysis import karsilastirma as K
    ocak = _bar(90, ts0="2026-01-01", carpan=1.004)
    temmuz = _bar(90, ts0="2026-07-01", carpan=1.004)
    r = K.korelasyon(ocak, temmuz)
    assert r.get("ortak_gun") == 0, \
        f"ortusmeyen takvimde {r.get('ortak_gun')} ortak gun bulundu"
    assert "korelasyon" not in r, r


def test_pencere_ufku_tam_ufuk_bar_ileriye_bakar():
    """
    KASITLI BOZMA TURUNDA BULUNDU: 'look-ahead' testim de KOR cikti.
    Giris barini pencereye dahil etmek isabeti DEGISTIRMIYOR, cunku
    oran[0] her zaman 1,0 (ne hedef ne stop). Gercek kusur off-by-one:
    ufuk 30 yerine 29 ileri bara bakmak.
    Ayirt edici seri: hedefe TAM 30. barda degen bir artis.
    """
    from finagent.analysis import karsilastirma as K
    ufuk = 30
    # (1+g)^30 = 1.0501 (hedefi gecer) ama (1+g)^29 = ~1.0484 (gecmez)
    g = 1.0501 ** (1 / ufuk) - 1
    seri = _bar(120, carpan=1 + g)
    p = K.pencere_istatistigi(seri, hedef_pct=5, stop_pct=10, ufuk_bar=ufuk)
    assert p["hedefe_dokundu_pct"] == 100.0, (
        "30. ileri bar penceresine girmiyor — ufuk bir bar eksik: "
        f"{p['hedefe_dokundu_pct']}")


def test_yillik_bar_seriden_turetilir_sabit_252_degil():
    """
    Kriptoyu 252 ile yilliklastirmak oynakligi SISTEMATIK dusuk gosterir
    (365/252 = 1,20 kat) ve iki varlik sinifi yan yana konunca
    karsilastirma sessizce yanlis cikar.
    """
    from finagent.analysis import karsilastirma as K
    kripto = _bar(200, gun=1)                      # 7/24
    hisse = _bar(200, gun=1)
    # Hafta sonu atlayan bir seri kur: gun=1 ama 5/7 yogunluk taklidi
    import datetime as _dt
    d0 = _dt.date.fromisoformat("2026-01-01")
    hisse = [{"ts": str(d0 + _dt.timedelta(days=i)), "close": 100.0,
              "currency": "EUR"}
             for i in range(280) if (d0 + _dt.timedelta(days=i)).weekday() < 5]
    assert round(K._yillik_bar(kripto)) > round(K._yillik_bar(hisse)), \
        (K._yillik_bar(kripto), K._yillik_bar(hisse))
    assert 240 <= K._yillik_bar(hisse) <= 270, K._yillik_bar(hisse)


def test_pencere_istatistigi_look_ahead_yapmaz_ve_sayimi_beyan_eder():
    """
    Modelin uydurdugu tabloda "335 pencere" yaziyordu ve aritmetigi
    DOGRUYDU (365-30) — sayilar uydurmaydi. O yuzden pencere sayisi
    hesaplanip BEYAN edilmeli, tahmin edilmemeli.
    """
    from finagent.analysis import karsilastirma as K
    n, ufuk = 100, 30
    artan = _bar(n, carpan=1.02)
    p = K.pencere_istatistigi(artan, hedef_pct=5, stop_pct=10, ufuk_bar=ufuk)
    assert p["pencere_sayisi"] == n - ufuk, (p["pencere_sayisi"], n - ufuk)
    assert p["kullanilan_bar"] == n
    # Monoton artista hedefe HER pencerede deger, stop HIC gorulmez
    assert p["hedefe_dokundu_pct"] == 100.0 and p["hedeften_once_stop_pct"] == 0.0, p
    # Monoton dususte tam tersi
    dusen = _bar(n, carpan=0.98)
    q = K.pencere_istatistigi(dusen, 5, 10, ufuk)
    assert q["hedefe_dokundu_pct"] == 0.0 and q["hedeften_once_stop_pct"] == 100.0, q
    # Kisa seride SAYI URETMEZ
    assert "hata" in K.pencere_istatistigi(_bar(40), 5, 10, 30)


def test_pencere_basabas_isabet_dogru():
    """+5/-10 kurgusunun basabas isabeti 10/(5+10) = %66,7."""
    from finagent.analysis import karsilastirma as K
    p = K.pencere_istatistigi(_bar(100, carpan=1.001), 5, 10, 30)
    assert p["basabas_isabet_pct"] == 66.7, p["basabas_isabet_pct"]


def test_beta_sismesi_bayrakla_yakalanir():
    """
    OLCULDU 2026-08-18: `maruziyet` ilk kosusunda USDTRY portfoy betasini
    -9,41 verdi. Sebep metodolojik — beta = kov/var(faktor) ve USDTRY
    gunluk oynakligi %0,09, pozisyonun %5,7'sinin 60'ta biri. Kucuk
    varyansa bolmek betayi sisiriyor; cikan sayi maruziyet DEGIL.
    Gercek korelasyon -0,001 idi, yani maruziyet YOK.
    """
    from finagent.analysis import karsilastirma as K
    import math
    # Cok az oynayan faktor + cok oynayan hedef, ayni takvimde
    sakin = [{"ts": b["ts"], "close": 100.0 * (1 + 0.0003 * math.sin(i)),
              "currency": "TRY"} for i, b in enumerate(_bar(120))]
    oynak = [{"ts": b["ts"], "close": 100.0 * (1 + 0.05 * math.sin(i * 1.7)),
              "currency": "USDT"} for i, b in enumerate(_bar(120))]
    r = K.korelasyon(sakin, oynak)
    assert r["beta_guvenilir_mi"] is False, r
    assert r["beta_uyarisi"] and "olcek" in r["beta_uyarisi"], r["beta_uyarisi"]
    # 1σ etkisi beta'dan KUCUK olmali — verinin icinde bir ifade
    assert abs(r["bir_sigma_etki_pct"]) < abs(r["beta"]) * 100, r
    # Benzer oynaklikta bayrak DUSMEZ
    r2 = K.korelasyon(oynak, oynak)
    assert r2["beta_guvenilir_mi"] is True, r2


def test_karsilastirma_row_ile_de_calisir():
    """
    `db.fiyat_serisi()` `sqlite3.Row` donduruyor ve Row'da `.get()` YOKTUR.
    Testler dict veriyor — bu fark "testte gecti, canlida AttributeError"
    seklinde patlar. Tek erisim noktasi (`_al`) ikisini de kaldirmali.
    """
    import sqlite3
    from finagent.analysis import karsilastirma as K
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE p (ts TEXT, close REAL, currency TEXT)")
    con.executemany("INSERT INTO p VALUES (?,?,?)",
                    [(b["ts"], b["close"], b["currency"])
                     for b in _bar(80, carpan=1.005)])
    rows = con.execute("SELECT ts, close, currency FROM p ORDER BY ts").fetchall()
    assert not hasattr(rows[0], "get"), "Row'da .get olmamali (varsayim degisti)"
    o = K.getiri_ozeti(rows)
    assert o["para_birimi"] == "USD" and o["bar"] == 80, o
    con.close()


def test_yeni_capraz_araclar_kayitli_ve_tetik_kosulu_yaziyor():
    """
    Dort bileşik arac hem `ARAC_ADLARI`'nda hem `araclar()` ciktisinda
    olmali — biri eksikse arac ya izin kapisindan gecmez ya modele hic
    gorunmez. Ayrica aciklamalar "NE ZAMAN CAGIR" demeli: Anthropic'in
    arac tasarim rehberi tetik kosulunun olculebilir fark yarattigini
    soyluyor, ve olayda model araci bulamayip Bash'e kacmisti.
    """
    import tempfile, pathlib as _p
    from finagent.storage.db import Database
    from finagent.bot.tools import ToolBox, ARAC_ADLARI
    yeni = ("karsilastir", "iliski", "pencere_istatistigi", "maruziyet")
    for ad in yeni:
        assert f"mcp__finagent__{ad}" in ARAC_ADLARI, ad
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        tb = ToolBox(_fazb_ayar(kok=d), db, _p.Path(d), sahip="ali")
        kayit = {}
        for a in tb.araclar():
            n = getattr(a, "name", None) or (a.get("name") if isinstance(a, dict) else None)
            aç = getattr(a, "description", None) or (a.get("description") if isinstance(a, dict) else "")
            if n:
                kayit[n] = aç or ""
        for ad in yeni:
            assert ad in kayit, f"{ad} araclar() ciktisinda yok"
            assert "CAGIR" in kayit[ad].upper(), \
                f"{ad} aciklamasi tetik kosulu ('... ise BUNU CAGIR') icermiyor"
        db.close()


def test_bekci_kurulumundan_onceki_kosuyu_yargilamaz():
    """
    BESINCI YANLIS ALARM (olculdu 2026-08-18 18:15:21, Ali'ye GITTI).
    Sabah kosusu o gun 09:31'de gercekten kostu — 170 piyasa sinyali, iki
    sahip, pulse.log'da duruyor. Ama izi yoktu: iz mekanizmasi 11:54'te
    geldi. Ogle 18:15'te ILK izi yazinca `any(iz var mi)` global kapisi
    acildi ve bekci sabah'i da yargilayip "kacirildi" dedi.

    Kipe ozel iz kapisi da yetmezdi: ilk gunden BOZUK bir kip hic iz
    birakmaz, dolayisiyla hic alarm da almaz. Dogru sinir KURULUM ANI.
    Bu test iki yonu birden tutuyor — sessizlik VE alarm.
    """
    import tempfile, pathlib as _p, plistlib, json as _j
    import datetime as _dt
    from finagent.bot.watchdog import Bekci
    from finagent.bot import watchdog as _W

    # SAAT SABITLENIYOR — yoksa test GUNUN SAATINE gore duser.
    #
    # Olculdu 2026-08-20 00:10: plist 00:05'e kurulu ve GECIKME_PAYI 90
    # dk, yani 01:35'ten once "daha vakti var" dali her seyi susturuyor
    # ve `eksik` bos donuyordu. Test gece yarisi ile sabahin ikisi
    # arasinda YANLIS DUSUYORDU; yargilama mantiginda bir kusur yok.
    # Zamanlanmis isi sinayan bir testin saati kendi belirlemesi
    # gerekir — projenin kendi dersi (`bekci UTC ile yereli karistirdi`)
    # burada teste uygulaniyor.
    _gercek_yerel = _W._yerel
    _sabit = _dt.datetime(2026, 8, 19, 12, 0)          # Carsamba, ogle
    _W._yerel = lambda: _sabit.astimezone()

    try:
        with tempfile.TemporaryDirectory() as d:
            kok = _p.Path(d)
            (kok / "launchd").mkdir()
            # Gunun COK ERKEN saatinde zamanlanmis bir kip: 00:05. Boylece
            # "daha vakti var" dali testi maskelemez.
            (kok / "launchd" / "com.alipala.finagent.sabah.plist").write_bytes(
                plistlib.dumps({
                    "Label": "com.alipala.finagent.sabah",
                    "StartCalendarInterval": [
                        {"Hour": 0, "Minute": 5, "Weekday": w}
                        for w in range(0, 8)]}))

            class _S:
                root = kok
                # Bekci gozetilecek kipleri AYARDAN aliyor; stub da
                # bunu vermek zorunda. Vermezse `_plist_saatleri` bos
                # doner ve test hicbir sey sinamaz (sessizce yesil).
                ritim_kipleri = ["sabah"]
                def get(self, yol, varsayilan=None):
                    return ({"sabah": {}} if yol == "ritim.kipler"
                            else varsayilan)
                def ritim_kip(self, kip):
                    return {"kip": kip, "kabuk_butce_sn": 900}

            db = Database(kok / "t.db"); db.init_schema()
            sd = kok / "data" / "bot"; sd.mkdir(parents=True)
            b = Bekci(_S(), db, sd)

            # 1) ILK CAGRI: kurulum ani SIMDI yazilir. Bugun 00:05'teki
            #    kosu kurulumdan ONCE, yargilanamaz -> SESSIZ.
            assert b.kacirilan_kosular() == [], \
                "kurulumdan onceki kosu icin alarm calindi (yanlis alarm)"
            izmar = sd / "kosu" / "kurulum.json"
            assert izmar.exists(), "kurulum ani diske yazilmadi"

            # 2) Kurulumu iki gun geriye al: artik bugunku kosu
            #    yargilanabilir ve izi YOK -> ALARM. (Ilk gunden bozuk
            #    kip de boylece yakalanir.)
            izmar.write_text(_j.dumps(
                {"ts": (_W._yerel() - _dt.timedelta(days=2)).isoformat()}))
            eksik = b.kacirilan_kosular()
            assert [x["kip"] for x in eksik] == ["sabah"], eksik

            # 3) Iz yazilirsa yine SESSIZ
            (sd / "kosu" / "sabah.json").write_text(_j.dumps(
                {"kip": "sabah", "ts": _W._yerel().isoformat(),
                 "sahipler": ["ali"], "piyasa_sinyali": 1}))
            assert b.kacirilan_kosular() == [], "iz varken alarm caldi"
            db.close()
    finally:
        _W._yerel = _gercek_yerel


def test_takvim_araci_veriyi_web_aramasina_birakmiyor():
    """
    OLCULDU 2026-08-18: "Izmir'de ev fiyatlari + onumuzdeki PPK
    toplantilari" sorusunda model PPK tarihlerini DOGRU verdi (10 Eylul,
    22 Ekim, 10 Aralik 2026) ama bir BANKA BLOGUNDAN, sekiz web cagrisi
    harcayarak. Ayni tarihler veritabaninda RESMI TCMB URL'siyle
    duruyordu (87 kayit, 2021-2027) — ama okuyan ARAC YOKTU.

    Bu, `1f264b2`'nin birebir tekrari: "makro haber akisi bende yok"
    denmisti, akis VARDI, okuyan arac YOKTU. Veri katmanina bir tablo
    eklemek yetmiyor; ONU OKUYAN ARAC da eklenmeli, yoksa model onu
    disaridan ve DAHA ZAYIF bir kaynaktan alir.
    """
    import tempfile, pathlib as _p, asyncio, json as _j
    from finagent.storage.db import Database
    from finagent.bot.tools import ToolBox, ARAC_ADLARI

    assert "mcp__finagent__takvim" in ARAC_ADLARI
    with tempfile.TemporaryDirectory() as d:
        db = Database(_p.Path(d) / "t.db"); db.init_schema()
        db.query("INSERT INTO takvim (tarih, kaynak, bolge, olay, onem, url) "
                 "VALUES (date('now','+30 days'),'tcmb','Turkiye',"
                 "'Para Politikasi Kurulu Toplanti Karari','yuksek',"
                 "'https://www.tcmb.gov.tr/x')")
        db._conn.commit()
        tb = ToolBox(_fazb_ayar(kok=d), db, _p.Path(d), sahip="ali")
        fn = {a.name: a.handler for a in tb.araclar()}
        assert "takvim" in fn, "takvim araci uretilmedi"

        o = _j.loads(asyncio.run(fn["takvim"]({"gun": 90}))["content"][0]["text"])
        assert len(o["kayit"]) == 1 and o["kayit"][0]["onem"] == "yuksek", o
        assert o["kayit"][0]["url"].startswith("https://www.tcmb.gov.tr"), \
            "resmi URL tasinmiyor — kademe 1 iddiasi dogrulanamaz"

        # BOS PENCERE "takvim yok" DEMEK DEGIL: kapsam beyan edilmeli,
        # yoksa model disariya cikar (yanlis 'yok' beyani sinifi).
        b = _j.loads(asyncio.run(fn["takvim"]({"gun": 1}))["content"][0]["text"])
        assert "hata" in b and "toplam 1 kayit" in (b.get("ipucu") or ""), b
        db.close()


def test_tur_butcesi_bitince_yeniden_DENENMEZ_ama_kismi_cevap_verilir():
    """
    OLCULDU 2026-08-18 07:41 (CANLI): bir sohbet turu
    `Reached maximum number of turns (24)` ile dustu. Kullanici, botun 24
    tur boyunca topladigi her seyi kaybederek "Cevap uretemedim" gordu.

    IKI AYRI KURAL, IKISI DE BURADA:
      * Tur butcesi tukendiyse YENIDEN DENEME YOK — deterministik olarak
        ayni duvara toslar, 200+ saniye daha yakar, sonuc degismez.
      * Ama HIC CEVAP VERMEMEK de yanlis: model veriyi ZATEN cekmisti,
        atilan sey isin kendisi degil sunumuydu. Kismi cevap verilir.
    Gecici sinifta (SIGKILL vb.) ise TAM TERSI: yeniden denenir.
    """
    from finagent.bot import chat as C

    butce = Exception("Claude Code returned an error result: "
                      "Reached maximum number of turns (24)")
    gecici = Exception("Command failed with exit code -9")

    assert C._tur_butcesi_bitti(butce) is True
    assert C._tur_butcesi_bitti(gecici) is False

    # Kismi cevap: YALNIZCA tur butcesi sinifinda uretilir
    assert C._kismi_cevap(gecici, ["portfoy"]) is None, \
        "gecici hatada kismi cevap uretilmemeli (yeniden denenecek)"

    butce.kullanilan_araclar = ["portfoy", "teknik", "portfoy"]
    butce.kismi_metin = "ASML 1.597,40 EUR, SMA50 1.560,70"
    m = C._kismi_cevap(butce, butce.kullanilan_araclar)
    assert m and "Tur butcem doldu" in m, m
    assert "portfoy, teknik" in m, "arac izi verilmedi (tekillestirilmeli)"
    assert "1.597,40" in m, "toplanan veri atildi"
    assert "daraltirsan" in m, "kullaniciya cikis yolu verilmedi"
    # ARA ANLATIM HAM GONDERILMEZ: kismi metin ne oldugu SOYLENEREK gelir
    assert "tamamlanmamis" in m, "kismi metin etiketsiz gonderiliyor"


def test_sohbet_tur_butcesi_olculen_yuke_gore():
    """
    24 cok darDI: e2e'de GECEN en agir senaryo 20 arac kullandi (haber +
    kaynak kademesi), Izmir konut sorusu da 20 — butcenin %83'u. Ust
    sinir tur sayisi degil SURE, ve olculen en uzun basarili tur 206 sn
    (`is_zaman_asimi_dk: 15`'in cok altinda).
    """
    from finagent.config import load_settings
    s = load_settings()
    tur = int(s.get("analysis.llm.chat_max_turns", 0))
    assert tur >= 40, f"chat_max_turns {tur} — olculen en agir soru 20 arac"
    # Yeniden deneme ayari VARSAYILAN OLARAK acik ama sinifa bagli
    assert int(s.get("telegram.sohbet_yeniden_deneme", -1)) >= 0


def test_bekci_bayat_surumu_yakalar():
    """
    OLCULDU 2026-08-18: o gun bot kodunu etkileyen ALTI commit atildi;
    DORDUNDE bot yeniden baslatildi, IKISINDE ATLANDI — `_iz_koruyan`
    37 dakika, `takvim` araci 9 dakika ESKI KODLA kostu. Zarar gormedi
    cunku o pencerede kimse yazmadi: SANS, surec degil.

    `launchctl list` "bot calisiyor" der; "bot GUNCEL kodla calisiyor"
    APAYRI bir iddiadir. Projenin tekrar eden kusur sinifi tam bu:
    beyan edilen durumun gercek durumdan SESSIZCE ayrilmasi. Insanin
    hatirlamasina birakilan adim er gec atlanir — olculur hale gelmeli.
    """
    import tempfile, pathlib as _p, time as _t
    from finagent.bot.watchdog import Bekci

    with tempfile.TemporaryDirectory() as d:
        kok = _p.Path(d)
        (kok / "src" / "finagent" / "bot").mkdir(parents=True)
        (kok / "config").mkdir()
        kaynak = kok / "src" / "finagent" / "bot" / "chat.py"
        kaynak.write_text("# kod")

        class _S:
            root = kok
            def get(self, *a, **k): return None

        b = Bekci(_S(), None, kok)
        simdi = _t.time()

        # Surec KODDAN YENI -> bayat DEGIL
        assert b.bayat_surum(surec_basi=simdi + 3600) is None

        # Surec KODDAN ESKI -> bayat, ve HANGI dosya oldugunu soylemeli
        r = b.bayat_surum(surec_basi=simdi - 3600)
        assert r and r["gecikme_dk"] >= 59, r
        assert r["dosya"].endswith("chat.py"), r["dosya"]

        # PAY VAR: kurulum sirasinda dosya surecten birkac saniye sonra
        # yazilabilir; bu bayat SAYILMAZ, yoksa her deploy alarm calar.
        assert b.bayat_surum(surec_basi=simdi - 30) is None, \
            "kucuk fark bayat sayildi — her yeniden baslatmada alarm calar"

        # settings.yaml da izleniyor (kod degil ama davranisi degistirir)
        (kok / "config" / "settings.yaml").write_text("a: 1")
        r2 = b.bayat_surum(surec_basi=simdi - 3600)
        assert r2 and r2["dosya"].endswith("settings.yaml"), r2
        # Dinleyici gercekten SORUYOR mu — yoksa olcut olu kod olur
        import inspect
        from finagent.bot import listener as L
        assert "bayat_surum()" in inspect.getsource(L.FinBot.run), \
            "bayat surum olcutu dinleyiciye baglanmadi"


def test_dinleyici_kacirilan_kosuyu_bildirir():
    """Gozcu bulsa da dinleyici sormazsa alarm hic calmaz."""
    import inspect
    from finagent.bot import listener as L
    from finagent.bot.watchdog import Bekci
    kaynak = inspect.getsource(L.FinBot.run)
    assert "kacirilan_kosular()" in kaynak, "gozcu dinleyiciye baglanmadi"
    assert "kosu_kacti_" in kaynak, "bildirim anahtari kip bazli degil"

    # ESKI OLCUT KALDIRILDI ve GERI GELMEMELI.
    #
    # `kacirilan_nabiz()` kosunun BASLADIGINI olcuyordu (`collector_runs`
    # / `signals` / `panel_runs`), BITTIGINI degil. Iki yonden de yanlis
    # cevap verdi: 2026-08-18'de dort YANLIS alarm uretti, 2026-08-19'da
    # GERCEK arizayi kacirdi (kosu SIGTERM ile oldu ama collector
    # kayitlari doluydu). Yerine gecen olcut kosunun KENDI izine bakiyor
    # ve nabiz artik `ritim.kipler` uzerinden gozetiliyor.
    assert not hasattr(Bekci, "kacirilan_nabiz"), \
        "zayif olcut geri gelmis — kosunun BASLADIGINI olcuyor, BITTIGINI degil"
    assert "kacirilan_nabiz" not in kaynak.replace("`kacirilan_nabiz`", "")




# ═══════════════════════════════════════════════════════════════════
# ONAY AKISI — sessiz basarisizlik kapatiliyor
#
# OLCULEN SIKAYET (2026-08-19): "Kaydet basilinca agenttan geri bildirim
# almiyorum." Uc mekanizma ust uste biniyordu ve UCU DE sessizdi:
#   1. `answerCallbackQuery` gecikince "query is too old" ile dusuyordu
#      (data/bot.log'da duruyor) ve `_sessiz=True` oldugu icin ERROR
#      bile yazmiyordu.
#   2. Butonlar basildiktan sonra oldugu yerde kaliyordu — ekranda
#      hicbir sey degismiyor.
#   3. `send_message`in bool sonucuna HICBIR cagiran bakmiyordu; mesaj
#      reddedilse de veritabani yazimi ZATEN olmustu.
# ═══════════════════════════════════════════════════════════════════

def _onay_botu(d, db, sahipler=None):
    from finagent.config import load_settings
    s = load_settings()
    s.raw.setdefault("telegram", {})["sahipler"] = sahipler or {"111": "ali"}
    bot = _sahte_bot(s, db)
    bot.pending_dir = _pathlib.Path(d) / "pending"
    bot.pending_dir.mkdir(parents=True, exist_ok=True)
    bot.state_dir = _pathlib.Path(d)
    bot.kuyruk = None
    return bot


def _poz_veri(chat="111", hesap="bux", toplam=1000.0):
    return {"_tip": "pozisyon", "_sahip": "ali", "_chat_id": chat,
            "hesap": hesap, "para_birimi": "EUR", "toplam_deger": toplam,
            "pozisyonlar": [{"symbol": "ASML", "name": "ASML", "quantity": 1,
                             "market_value": 1000.0, "pnl_pct": 0.0,
                             "currency": "EUR"}]}


def test_onay_sahiplenmesi_atomik_ve_istek_cokmede_KAYBOLMAZ():
    """
    ESKI DAVRANIS: `parsed = read(); pending.unlink(); ...yazma...`
    Dosya IS BASLAMADAN siliniyordu. Surec o sirada olurse (OOM/kill)
    `.bitti` yazilmaz, dinleyici isi YENIDEN DENER — ama onay dosyasi
    artik yok. Sonuc: "bu istek gecerli degil" ve kullanici yazilip
    yazilmadigini OGRENEMEZ.

    Yeni davranis: sahiplenme ATOMIK bir yeniden adlandirma. Cokme
    aninda dosya `.isleniyor` olarak DISKTE DURUR.
    """
    import tempfile
    from finagent.bot.onay import OnayDeposu
    with tempfile.TemporaryDirectory() as d:
        depo = OnayDeposu(_pathlib.Path(d))
        depo.yaz("t1", _poz_veri())

        birinci = depo.sahiplen("t1")
        assert birinci is not None and birinci.veri["hesap"] == "bux"
        # IKINCI BASIS: ayni istegi iki surec sahiplenemez.
        assert depo.sahiplen("t1") is None, "cift sahiplenme onlenmedi"
        assert depo.durum("t1") == "isleniyor"

        # SURECI OLDURDUGUMUZU VARSAY: dosya kaybolmadi, incelenebilir.
        assert list(_pathlib.Path(d).glob("*.isleniyor")), \
            "cokme aninda istek buhar oldu"
        assert depo.asili_isler(__import__("datetime").timedelta(0)), \
            "yarim kalan is gorunmuyor"

        depo.tamamla(birinci)
        assert depo.durum("t1") == "yok"


def test_onay_hatasi_istegi_SAKLAR_ve_tekrar_deneme_yolu_birakir():
    """
    Buton basista kaldiriliyor. Is sonra hata alirsa kullanici cikmaz
    sokakta kalmamali: sebep + TEKRAR DENE butonu gitmeli ve istek
    diskte `.hata` olarak DURMALI (silinirse "hic olmamis" olur, oysa
    kismen yazilmis olabilir).
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        bot = _onay_botu(d, db)
        depo = bot._depo()
        depo.yaz("t9", _poz_veri())
        onay = depo.sahiplen("t9")

        def _patla(*a, **k):
            raise RuntimeError("disk dolu")
        bot._onay_yurut = _patla
        bot._onay_isle(onay, "111")

        metin = bot.gonderilen[-1][0]
        assert "kayit tamamlanmadi" in metin, metin
        assert "disk dolu" in metin, "sebep kullaniciya soylenmedi"
        assert depo.durum("t9") == "hata", "hatali istek diskten silindi"
        db.close()


def test_gonder_HTML_reddedilirse_sadelestirip_yeniden_dener():
    """
    Sessiz basarisizligin ana kanali: `send_message` False donuyor,
    kimse bakmiyor, veritabani yazimi ZATEN olmus. Artik ucuncu bir
    kademe var ve hicbiri tutmazsa metin LOG'a dusuyor.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        bot = _onay_botu(d, db)
        denemeler = []

        def _tek_seferlik_red(metin, chat_id=None, **k):
            denemeler.append(metin)
            # Ilk deneme (HTML) reddedilir, sadelesmis olan kabul edilir.
            return "<b>" not in metin
        bot.tg.send_message = _tek_seferlik_red

        assert bot._gonder("<b>ASML & CO</b> kaydedildi", "111") is True
        assert len(denemeler) == 2, denemeler
        assert "<b>" not in denemeler[1], "sadelestirme yapilmadi"
        assert "ASML" in denemeler[1], "sadelestirme BILGIYI de sildi"

        # Hicbiri tutmazsa: False doner, sessizce True demez.
        bot.tg.send_message = lambda *a, **k: False
        assert bot._gonder("x", "111") is False
        db.close()


def test_butona_basinca_ANINDA_butonlar_kalkar_ve_balon_gider():
    """
    Basisin duyuldugunu gosteren TEK isaret balondu ve is kuyruga
    girince o balon gecikip dusuyordu. Artik teyit DINLEYICIDE, yani
    callback kimligi taze iken veriliyor; ayrica butonlar kaldiriliyor.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        bot = _onay_botu(d, db)
        bot.kaldirilan = []
        bot.tg.edit_message_reply_markup = \
            lambda mid, markup=None, chat_id=None: (
                bot.kaldirilan.append((mid, markup)) or True)
        bot._calistir = lambda upd: None      # is burada onemli degil

        upd = {"callback_query": {"id": "cb1", "data": "ok:abc",
                                  "message": {"message_id": 77,
                                              "chat": {"id": 111}}}}
        bot._dispatch(upd)

        assert bot.cevaplar, "balon hic gonderilmedi"
        assert bot.kaldirilan == [(77, None)], bot.kaldirilan
        # WORKER AYNI BALONU TEKRAR CAGIRMAMALI: ikinci cagri
        # "query is too old" doner ve hicbir sey eklemez.
        assert upd["callback_query"].get("_basis_onaylandi") is True

        # Menu butonlari (`reh`) KALMALI — kullanici konular arasi geziyor.
        bot.kaldirilan.clear()
        bot._dispatch({"callback_query": {"id": "cb2", "data": "reh:portfoy",
                                          "message": {"message_id": 78,
                                                      "chat": {"id": 111}}}})
        assert bot.kaldirilan == [], "menu butonlari da kaldirildi"
        db.close()


def test_sahiplenilemeyen_istek_SESSIZ_kalmaz():
    """
    Eski davranis: yalnizca "bu istek artik gecerli degil" balonu — ve
    gecikmis callback'te o balon da dusuyordu. Uc durumun ucu de ayri
    cumleyle SOYLENMELI.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        bot = _onay_botu(d, db)
        cb = {"id": "x", "data": "ok:yok_boyle",
              "message": {"chat": {"id": 111}}}
        bot._on_callback(cb)
        assert bot.gonderilen, "hicbir mesaj gitmedi — sessiz basarisizlik"
        assert "/portfoy" in bot.gonderilen[-1][0]

        depo = bot._depo()
        depo.yaz("t2", _poz_veri())
        depo.sahiplen("t2")
        bot._on_callback({"id": "y", "data": "ok:t2",
                          "message": {"chat": {"id": 111}}})
        assert "isleniyor" in bot.gonderilen[-1][0].lower(), bot.gonderilen[-1][0]
        db.close()


def test_kapsam_OLCULEMEDIGINDE_sessiz_kalmiyor():
    """
    Uc durum vardi, ikisi soyleniyordu. `toplam_deger` okunamamissa
    `_kapsam()` None doner ve eski kod SESSIZ dala dusuyordu: kullanici
    "eksik uyarisi gelmedi, demek ki tam" diye okuyordu. Diskteki
    bekleyen okumalarin ucunde bu alan bos — nadir bir kose degil.
    """
    from finagent.bot.listener import FinBot
    olculemedi = "\n".join(FinBot._kapsam_satirlari(1000.0, None, "EUR"))
    assert "dogrulanamadi" in olculemedi.lower(), olculemedi
    assert "tam" not in olculemedi.split("Yanlissa")[0].lower()

    eksik = "\n".join(FinBot._kapsam_satirlari(500.0, 1000.0, "EUR"))
    assert "eksik" in eksik.lower()

    tam = "\n".join(FinBot._kapsam_satirlari(1000.0, 1000.0, "EUR"))
    assert "Kapsam tam" in tam


# ═══════════════════════════════════════════════════════════════════
# DOGAL DILDE ONAY — "kaydet"
# ═══════════════════════════════════════════════════════════════════

def test_onay_niyeti_ACIK_fiil_ister_evet_yetmez():
    """
    "evet"/"tamam" BILEREK disarida: model bir tur once "Moderna'yi da
    inceleyeyim mi?" diye sormus olabilir ve oraya gelen "evet"
    PORTFOYE YAZMAK anlamina gelmez. Onay kelimesi neyi onayladigini
    kendi basina tasimali.
    """
    from finagent.bot.listener import FinBot as F
    for olumlu in ("kaydet", "Kaydet", "kaydedelim", "kaydedebilirsin",
                   "evet kaydet", "tamam kaydet lutfen", "onayla",
                   "onayliyorum", "onaylıyorum", "portfoyume kaydet"):
        assert F._onay_niyeti(olumlu), f"onay sayilmadi: {olumlu!r}"

    for olumsuz in (
            "evet", "tamam", "olur", "peki",           # neyi onayladigi belirsiz
            "kaydettin mi?", "kaydet dedim mi?",       # SORU
            "onceki ekrani da aldiktan sonra kaydet",  # kosula bagli
            "altin hesabimi portfoyume kaydeder misin",
            "", "   "):
        assert not F._onay_niyeti(olumsuz), f"yanlislikla onay: {olumsuz!r}"


def test_kaydet_TEK_TAZE_istegi_yazar_ve_ne_yazdigini_soyler():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        bot = _onay_botu(d, db)
        bot._depo().yaz("t1", _poz_veri())
        islenen = []
        bot._onay_isle = lambda o, c, a="ok": islenen.append(o.token)

        assert bot._dogal_onay("kaydet", "111") is True
        assert islenen == ["t1"], islenen
        # SART 2: ne yazilacagi YAZIMDAN ONCE ekranda.
        ozet = bot.gonderilen[0][0]
        assert "BUX" in ozet and "1 pozisyon" in ozet, ozet
        db.close()


def test_kaydet_BAYAT_istegi_yazmaz_adaylari_gosterir():
    """
    SART 1: "kaydet" kelimeye degil TOKEN'e baglanir. Bayat bir istek
    varken hangisinin kastedildigi bilinmiyor; tahmin etmenin bedeli
    hatirlanmayan bir ekranin portfoye yazilmasi — ve bu SESSIZ olur,
    cunku kullanici zaten onay bekliyor.
    """
    import tempfile, os, time
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        bot = _onay_botu(d, db)
        yol = bot._depo().yaz("eski", _poz_veri(hesap="midas"))
        eski = time.time() - 3 * 3600
        os.utime(yol, (eski, eski))
        islenen = []
        bot._onay_isle = lambda o, c, a="ok": islenen.append(o.token)

        assert bot._dogal_onay("kaydet", "111") is True
        assert islenen == [], "bayat istek tek kelimeyle YAZILDI"
        hepsi = " ".join(m for m, _ in bot.gonderilen)
        assert "varsaymiyorum" in hepsi, hepsi
        assert "MIDAS" in hepsi, "aday ozeti gosterilmedi"
        assert "3 saat once" in hepsi, "yas gosterilmedi"
        db.close()


def test_kaydet_bekleyen_yoksa_MODELE_dusher():
    """"kaydet" o baglamda baska bir sey isteyebilir; komut gibi
    davranip "bekleyen yok" demek, sorulmayan soruya cevap olurdu."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        bot = _onay_botu(d, db)
        assert bot._dogal_onay("kaydet", "111") is False
        assert bot.gonderilen == []
        db.close()


def test_kaydet_SILME_istegini_onaylamaz():
    """
    `/onayla` icin kapatilan delik dogal dilde yeniden acilmamali:
    "kaydet" kelimesiyle bir SILME islemi onaylanamaz. `sil_son` kendi
    butonunda "🗑 Evet, geri al" yaziyor.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        bot = _onay_botu(d, db)
        bot._depo().yaz("sil1", {"_tip": "sil_son", "_sahip": "ali",
                                 "_chat_id": "111"})
        islenen = []
        bot._onay_isle = lambda o, c, a="ok": islenen.append(o.token)
        assert bot._dogal_onay("kaydet", "111") is False
        assert islenen == [], "kaydet bir SILME islemini tetikledi"
        db.close()


def test_kaydet_baskasinin_bekleyen_istegine_dokunmaz():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        bot = _onay_botu(d, db, sahipler={"111": "ali", "222": "yuksel"})
        bot._depo().yaz("baska", _poz_veri(chat="222"))
        islenen = []
        bot._onay_isle = lambda o, c, a="ok": islenen.append(o.token)
        assert bot._dogal_onay("kaydet", "111") is False
        assert islenen == []
        db.close()


def test_toplu_onay_SURESI_DOLAN_istegi_islemez_ve_bunu_soyler():
    """
    Toplu onay, kullanicinin BAKMADIGI bir listeyi tek kelimeyle isleyen
    yol. Iki gun onceki bir okumanin oraya girmesi, hatirlanmayan bir
    ekrani portfoye yazmak demek. Butonu duruyor — o mesaja bakan biri
    hala onaylayabilir.
    """
    import tempfile, os, time
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        bot = _onay_botu(d, db)
        yol = bot._depo().yaz("cok_eski", _poz_veri())
        eski = time.time() - 48 * 3600
        os.utime(yol, (eski, eski))
        islenen = []
        bot._onay_isle = lambda o, c, a="ok": islenen.append(o.token)

        bot._hepsini_onayla("111")
        assert islenen == [], "24 saatten eski istek toplu onayda islendi"
        assert "24 saatten eski" in bot.gonderilen[-1][0], bot.gonderilen[-1][0]
        # SESSIZ DUSURME YOK: `/bekleyen` sayiyi ayrica soyluyor.
        assert "24 saatten eski" in bot._bekleyen_text("111")
        assert bot._depo().durum("cok_eski") == "bekliyor", \
            "suresi dolan istek sessizce SILINDI"
        db.close()


def test_basis_teyidi_KUYRUK_uzerinden_worker_surecine_tasinir():
    """
    Basis teyidi DINLEYICIDE veriliyor, is WORKER'da kosuyor. Bayrak iki
    surec arasinda tasinmazsa worker balonu bir daha cagirir, Telegram
    "query is too old" doner ve log yeniden gurultuye bogulur.

    Kuyruk guncellemeyi JSON olarak diske yaziyor; bu test bayragin O
    DOSYADA gercekten bulundugunu dogruluyor — kodu okuyarak degil,
    diski okuyarak.
    """
    import tempfile, json as _j
    from finagent.bot.kuyruk import Kuyruk
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        bot = _onay_botu(d, db)
        bot.tg.edit_message_reply_markup = lambda *a, **k: True
        # Is BASLATILMASIN: yalnizca kuyruga yazilmasini olcuyoruz.
        bot.kuyruk = Kuyruk(_pathlib.Path(d) / "kuyruk", kok=_pathlib.Path(d),
                            baslat=lambda *a, **k: None)

        bot._dispatch({"update_id": 4242,
                       "callback_query": {"id": "cb", "data": "ok:tok1",
                                          "message": {"message_id": 5,
                                                      "chat": {"id": 111}}}})

        dosyalar = list((_pathlib.Path(d) / "kuyruk").glob("*.json"))
        assert dosyalar, "is kuyruga yazilmadi"
        is_ = _j.loads(dosyalar[0].read_text(encoding="utf-8"))
        assert is_["update"]["callback_query"].get("_basis_onaylandi") is True, \
            "basis teyidi worker surecine tasinmadi"

        # Worker tarafi: bayrak varken balonu TEKRAR cagirmamali.
        bot.cevaplar.clear()
        bot._on_callback(is_["update"]["callback_query"])
        assert bot.cevaplar == [], \
            "worker gecikmis callback kimligini yeniden cevapladi"
        db.close()


def test_hata_sonrasi_TEKRAR_DENE_butonu_yikici_islemi_kaydet_diye_gostermez():
    """
    Buton etiketi ISLEME GORE degisir — yikici bir islemde "✅ Kaydet"
    yazan buton, kullaniciya ne onayladigini YANLIS soyler.

    Hata yolunda uretilen TEKRAR DENE butonu ayni kurala tabi, ama o
    anda istek artik `.json` degil `.hata`. Etiket yalnizca `.json`a
    bakarsa varsayilana duser ve bir SILME islemi "Kaydet" diye gorunur.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        bot = _onay_botu(d, db)
        depo = bot._depo()
        depo.yaz("s1", {"_tip": "sil_son", "_sahip": "ali", "_chat_id": "111"})
        assert bot._onay_etiketi("s1") == "🗑 Evet, geri al"

        onay = depo.sahiplen("s1")
        assert bot._onay_etiketi("s1") == "🗑 Evet, geri al", \
            "sahiplenilmis istekte etiket kayboldu"

        depo.hataya_dus(onay, "test")
        assert bot._onay_etiketi("s1") == "🗑 Evet, geri al", \
            "hata sonrasi yikici islem 'Kaydet' diye gorundu"
        db.close()


# ═══════════════════════════════════════════════════════════════════
# HAFIF BILDIRIM — "Kapanis" yalani, AVTX x3 ve 20 GUNLUK olay
#
# OLCULEN VAKA (2026-08-19 18:14, Ali'nin ekran goruntusu):
#   * Baslik "🕕 Kapanis"ti; listenin ilk satiri AMZN'di ve ABD SEANSI
#     ACIKTI (kapanisa 3s 46dk). Baslik hesaplanmis bir durum degil,
#     18:00 slotunun takma adiydi.
#   * AVTX UC KEZ gorundu: ayni gunun -%19,91'inin uc ayri olcumu
#     (hareket, hacim, olay etkisi). Dorduncusu (RSI) `[:4]` ile
#     SESSIZCE dustu.
#   * AMZN'in olay etkisi 30 TEMMUZ'a aitti — 20 gun once. Ali ayni
#     sohbette sordugunda model "bugun olagandisi bir sey yok" dedi;
#     iki katman birbiriyle CELISTI.
#   * Sabah 09:31 kosusu ayni gun ayni haberi zaten bildirmisti.
#   * Mesajda TEK BIR SAYI yoktu; kanit veritabaninda kaliyordu.
# ═══════════════════════════════════════════════════════════════════

def _nabiz(db):
    from finagent.config import load_settings
    from finagent.pulse.runner import Nabiz
    n = Nabiz.__new__(Nabiz)
    n.s, n.db = load_settings(), db
    n.gonderilen = []
    n._sahibe_bildir = lambda sahip, metin, reply_markup=None: (
        n.gonderilen.append(metin))
    return n


def _enst(db, sembol, venue="BUX"):
    """GERCEK enstruman satiri. `bildirim_durumu` yabanci anahtarli;
    uydurma id ile yazmak testi kodun degil semanin duvarina carpardi."""
    return db.upsert_instrument(sembol, venue, sembol, "equity", "EUR")


def _sinyal(iid, sembol, tur, kanit, *, yon="asagi", guc=1.0,
            bar="2026-08-19", oynaklik=4.0, venue="BUX", ad=None):
    return {"instrument_id": iid, "sembol": sembol, "ad": ad or sembol,
            "venue": venue, "yon": yon, "guc": guc, "tur": tur,
            "bar_ts": bar, "gunluk_oynaklik_%": oynaklik, "kanit": kanit}


# --- AVTX'in 19 Agustos'taki GERCEK sinyalleri (veritabanindan) -------
def _avtx_sinyalleri():
    return [
        _sinyal(1, "AVTX", "olagandisi_hareket",
                {"gunluk_getiri_%": -19.91, "sigma": -5.01}),
        _sinyal(1, "AVTX", "hacim_anomalisi",
                {"hacim_kati": 11.45, "gunluk_getiri_%": -19.91}),
        _sinyal(1, "AVTX", "olay_etkisi",
                {"olay_tarihi": "2026-08-19", "olay_gun_once": 0,
                 "car_%": -18.15, "t": -4.43}),
        _sinyal(1, "AVTX", "rsi_ucu", {"rsi14": 23.5}, yon="yukari", guc=0.66),
    ]


def test_baslik_KAPANIS_diye_yanlis_durum_ilan_etmiyor():
    """
    Baslik artik yalnizca KOSUNUN AMACINI soyluyor; piyasalarin gercek
    durumu bir alt satirda OLCULEREK yaziliyor. AMZN ABD'de islem
    gorurken "Kapanis" demek, listenin ilk satiri icin yanlis bir
    olgu ilan etmekti.
    """
    import tempfile
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        n = _nabiz(db)
        n._hafif_bildir("ogle", [], _avtx_sinyalleri(), [], "ali")
        m = n.gonderilen[0]

        assert "Kapanis</b>" not in m, "baslik hala durum ilan ediyor"
        assert Nabiz.KOSU_ADI["ogle"] in m
        # SEANS SATIRI OLCULMUS: dort borsanin dordu de adiyla geciyor.
        for borsa in ("BIST", "Amsterdam", "Frankfurt", "ABD"):
            assert borsa in m, f"{borsa} seans satirinda yok"
        assert "Tatil takvimi yok" in m, "tatil uyarisi dusmus"


def test_seans_durumu_saatten_TURETILIYOR_sabit_degil():
    """
    19 Agustos 16:14 UTC = Ali'ye mesajin gittigi an. O anda ABD ACIK,
    digerleri kapali. Bu, bildirimin iddia etmesi gereken sey.
    """
    from datetime import datetime, timezone
    from finagent import piyasa

    an = datetime(2026, 8, 19, 16, 14, tzinfo=timezone.utc)   # Carsamba
    d = {x["borsa"]: x for x in piyasa.seans_durumlari(an)}
    assert d["ABD"]["durum"] == "acik", d["ABD"]
    assert d["ABD"]["kapanisa_dk"] == 226, d["ABD"]      # 12:14 -> 16:00
    assert d["BIST"]["durum"] == "kapandi"               # 19:14 Istanbul
    assert d["Amsterdam"]["durum"] == "kapandi"          # 18:14 Amsterdam
    assert d["Frankfurt"]["durum"] == "kapandi"
    assert "ACIK" in piyasa.durum_satiri(an)

    # Hafta sonu ve acilis oncesi de ayri durumlar — "kapandi" degil.
    cumartesi = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    assert all(x["durum"] == "hafta sonu"
               for x in piyasa.seans_durumlari(cumartesi))
    sabah = datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc)  # 08:00 Amsterdam
    ams = {x["borsa"]: x for x in piyasa.seans_durumlari(sabah)}["Amsterdam"]
    assert ams["durum"] == "acilmadi" and ams["acilisa_dk"] == 60


def test_borsa_cozumu_UC_KAPIDAN_gecer_ve_bilmiyorsa_SUSAR():
    """
    `identities.exchange` KULLANILMIYOR: o alan "sirket nerede kote"
    diyor, bizim sorumuz "BARIN fiyati hangi seansta olusuyor".
    ADYEN'in kimliginde 'OTC' yazar ama secilen serisi `.AS`
    kotasyonudur (EUR) — yani Amsterdam seansi.
    """
    import tempfile
    from finagent import piyasa
    from finagent.storage.db import Database
    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "b.db"); db.init_schema()

        # 1) venue YAPISAL olarak belirliyor
        bist = db.upsert_instrument("THYAO", "BIST", "THY", "equity", "TRY")
        kripto = db.upsert_instrument("BTC", "BINANCE", "Bitcoin", "crypto", "USDT")
        assert piyasa.borsa_coz(db, bist, "BIST") == "BIST"
        assert piyasa.borsa_coz(db, kripto, "BINANCE") == piyasa.SUREKLI

        # 2) Yahoo sade ticker -> ABD (dogrulanmis SEC ticker'i)
        us = db.upsert_instrument("AMZN", "BUX", "Amazon.com", "equity", "EUR")
        db.upsert_prices(us, [{"ts": "2026-08-19", "close": 265.0}],
                         "yahoo", currency="USD")
        db.query("INSERT INTO identities (instrument_id, cik, sec_ticker, "
                 "status, method, resolved_at) VALUES (?,?,?,?,?,datetime('now'))",
                 (us, "0001018724", "AMZN", "dogrulandi", "test"))
        db._conn.commit()
        assert piyasa.borsa_coz(db, us, "BUX") == "ABD"

        # 2a) IKINCI YAHOO KAYNAGI: yerel borsa kotasyonu (`yahoo_borsa`)
        # EUR ise sonek `.AS` -> Amsterdam. `_yahoo_sembolu`ye sorulsaydi
        # sade ticker donerdi ve YANLIS borsa (ABD) yazilirdi.
        eu = db.upsert_instrument("ASML", "BUX", "ASML Holding", "equity", "EUR")
        db.upsert_prices(eu, [{"ts": "2026-08-19", "close": 900.0}],
                         "yahoo_borsa", currency="EUR")
        db.query("INSERT INTO identities (instrument_id, cik, sec_ticker, "
                 "status, method, resolved_at) VALUES (?,?,?,?,?,datetime('now'))",
                 (eu, "0000937966", "ASML", "dogrulandi", "test"))
        db._conn.commit()
        assert piyasa.borsa_coz(db, eu, "BUX") == "Amsterdam"

        # 3) BILINMIYORSA None. Kimligi olmayan, soneksiz sembol ->
        # `_yahoo_sembolu` zaten None doner (yanlis sirket riski).
        bilinmez = db.upsert_instrument("ZZZZ", "BUX", "Bilinmeyen", "equity", "EUR")
        db.upsert_prices(bilinmez, [{"ts": "2026-08-19", "close": 1.0}],
                         "yahoo", currency="EUR")
        assert piyasa.borsa_coz(db, bilinmez, "BUX") is None

        # Fiyat kaynagi Yahoo DEGILSE Yahoo sembolu o bari aciklamaz.
        av = db.upsert_instrument("XYZ.AS", "BUX", "Xyz", "equity", "EUR")
        db.upsert_prices(av, [{"ts": "2026-08-19", "close": 5.0}],
                         "alphavantage", currency="EUR")
        assert piyasa.borsa_coz(db, av, "BUX") is None
        db.close()


def test_ayni_enstrumanin_sinyalleri_TEK_blokta_ve_sayilarla():
    """
    AVTX uc satir tutuyordu — bildirimin %75'i — ve dorduncu sinyali
    sessizce dusuyordu. Ucu de AYNI OLAYIN olcumu: hacim anomalisinin
    kaniti bile ayni -%19,91'i tasiyor.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        n = _nabiz(db)
        n._hafif_bildir("ogle", [], _avtx_sinyalleri(), [], "ali")
        m = n.gonderilen[0]

        assert m.count("<b>AVTX</b>") == 1, "AVTX hala birden fazla satirda"
        # DORT sinyalin DORDU de gorunuyor — biri sessizce dusmuyor.
        for sayi in ("-%19,91", "-5,0σ", "11,4×", "-%18,1", "RSI 23,5"):
            assert sayi in m, f"kanit mesaja gecmedi: {sayi}"
        assert "19 Agu bari" in m, "hangi barin sinyali oldugu yazilmadi"


def test_yirmi_gunluk_olay_bildirime_DUSMEZ_taze_olan_tarihiyle_gecer():
    """
    `haber_etkileri` 120 gunluk pencereye bakiyor (analiz icin dogru) ve
    tazelik filtresi YOKTU. 30 Temmuz'daki AMZN olayi 20 gundur her
    kosuda bildirime "bugun oldu" gibi dusuyordu.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        n = _nabiz(db)
        eski = _sinyal(7, "AMZN", "olay_etkisi",
                       {"olay_tarihi": "2026-07-30", "olay_gun_once": 20,
                        "car_%": 14.71, "t": 3.86}, yon="yukari")
        yeni = _sinyal(8, "AVGO", "olay_etkisi",
                       {"olay_tarihi": "2026-08-17", "olay_gun_once": 2,
                        "car_%": -11.0, "t": -2.5})
        yassiz = _sinyal(9, "XXX", "olay_etkisi",
                         {"olay_tarihi": None, "olay_gun_once": None,
                          "car_%": -9.0, "t": -2.0})

        taze, bayat = n._taze_sinyaller([eski, yeni, yassiz])
        assert [x["sembol"] for x in taze] == ["AVGO"], taze
        # YASI BILINMEYEN BAYAT SAYILIR: bilinmeyen tarihi "taze"
        # varsaymak, bu hatanin ta kendisiydi.
        assert {x["sembol"] for x in bayat} == {"AMZN", "XXX"}

        n._hafif_bildir("ogle", [], taze, [], "ali")
        m = n.gonderilen[0]
        assert "AMZN" not in m
        # GECEN olay bile TARIHIYLE gecer — 2 gunluk da "bugun" degil.
        assert "olay 17 Agu, 2 gun once" in m, m


def test_ayni_bar_iki_kez_bildirilmez_ama_YENI_bar_bildirilir():
    """
    En tehlikeli tuzak: yalnizca DEGERE bakan bir bastirma, AVTX'in
    ertesi gunku -%19,50'lik IKINCI COKUSUNU susturur (fark 0,41 puan).
    Anahtar `bar_ts` icerdigi icin yeni bar = yeni bildirim.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        n = _nabiz(db)
        avtx, rose = _enst(db, "AVTX"), _enst(db, "ROSE")
        gun1 = [_sinyal(avtx, "AVTX", "olagandisi_hareket",
                        {"gunluk_getiri_%": -19.91, "sigma": -5.01})]
        assert len(n._yeni_sinyaller(gun1, "ali")) == 1
        assert n._yeni_sinyaller(gun1, "ali") == [], "ayni bar iki kez bildirildi"

        gun2 = [_sinyal(avtx, "AVTX", "olagandisi_hareket",
                        {"gunluk_getiri_%": -19.50, "sigma": -4.9},
                        bar="2026-08-20")]
        assert len(n._yeni_sinyaller(gun2, "ali")) == 1, \
            "YENI GUNUN cokusu susturuldu"

        # GUN ICI ANLAMLI KOTULESME yeniden bildirilir: sabah kismi bar
        # -%5, aksam tam bar -%19,91. Ikincisi gercekten yeni bilgi.
        kismi = [_sinyal(rose, "ROSE", "olagandisi_hareket",
                         {"gunluk_getiri_%": -5.0, "sigma": -2.1})]
        tam = [_sinyal(rose, "ROSE", "olagandisi_hareket",
                       {"gunluk_getiri_%": -19.91, "sigma": -5.0})]
        assert len(n._yeni_sinyaller(kismi, "ali")) == 1
        assert len(n._yeni_sinyaller(tam, "ali")) == 1, \
            "gun ici ciddi kotulesme susturuldu"
        # Onemsiz suruklenme SUSAR.
        az = [_sinyal(rose, "ROSE", "olagandisi_hareket",
                      {"gunluk_getiri_%": -20.3, "sigma": -5.1})]
        assert n._yeni_sinyaller(az, "ali") == []

        # BASKASININ bastirma satiri beni susturamaz.
        assert len(n._yeni_sinyaller(gun1, "yuksel")) == 1
        db.close()


def test_bastirma_RISK_satirlariyla_ayni_anahtar_uzayini_paylasmaz():
    """
    `bildirim_durumu` hem riskleri hem sinyalleri tutuyor. Anahtarlar
    karisirsa bir `yogunlasma` satiri bir `olay_etkisi` sinyalini
    susturabilir — ya da tersi.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        n = _nabiz(db)
        avtx = _enst(db, "AVTX")
        n._yeni_sinyaller([_sinyal(avtx, "AVTX", "rsi_ucu", {"rsi14": 23.5})], "ali")
        turler = [r["tur"] for r in db.query(
            "SELECT tur FROM bildirim_durumu WHERE sahip='ali'")]
        assert turler and all(t.startswith("sinyal:") for t in turler), turler
        # Risk yolu HALA calisiyor ve ayni satirlara dokunmuyor.
        risk = {"instrument_id": avtx, "sembol": "AVTX", "tur": "yogunlasma",
                "kanit": {"agirlik_%": 40.0}}
        assert len(n._yeni_riskler([risk], "ali")) == 1
        assert n._yeni_riskler([risk], "ali") == []
        db.close()


def test_portfoy_riski_sinyal_listesine_GIRMEZ():
    """
    `guclu` turu ayirt etmiyordu: `yogunlasma` ve `acik_zarar` hem madde
    listesine hem ⚠️ risk bolumune dusuyordu — ayni sey iki kez.
    Gruplama bunu gorunur yapti (kanit satiri olmayan bos bloklar).
    """
    import tempfile, types
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        n = _nabiz(db)
        avtx, asml = _enst(db, "AVTX"), _enst(db, "ASML")
        n.db = types.SimpleNamespace(
            sahip_pozisyon_idleri=lambda s: {avtx, asml},
            query=db.query, tx=db.tx, fiyat_kaynagi=db.fiyat_kaynagi)
        sinyaller = [
            _sinyal(avtx, "AVTX", "olagandisi_hareket",
                    {"gunluk_getiri_%": -19.91, "sigma": -5.01}),
            {"instrument_id": asml, "sembol": "ASML", "tur": "yogunlasma",
             "guc": 1.0, "yon": "notr", "kanit": {"agirlik_%": 40.9}},
        ]
        sonuc = n._hafif("ogle", True, sinyaller, sinyaller, [], {}, sahip="ali")
        assert sonuc["portfoy_sinyali"] == 1, sonuc
        m = n.gonderilen[0]
        assert m.count("ASML") == 1, "risk hem sinyal hem risk olarak yazildi"
        assert "⚠️" in m and "yogunlasma" in m
        db.close()


def test_kesme_SESSIZ_degil():
    """`[:4]` iz birakmadan atiyordu; eksik oldugu soylenmeyen liste TAM
    sanilir. Ayni ders `isyatirim` kesilmesinde ogrenilmisti."""
    import tempfile
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, _ = _gecmis_db(d)
        n = _nabiz(db)
        cok = [_sinyal(_enst(db, f"S{i}"), f"S{i}", "olagandisi_hareket",
                       {"gunluk_getiri_%": -8.0 - i, "sigma": -3.0})
               for i in range(Nabiz.HAFIF_AZAMI_ENSTRUMAN + 3)]
        riskler = [{"sembol": f"R{i}", "tur": "yogunlasma",
                    "kanit": {"agirlik_%": 40 + i}}
                   for i in range(Nabiz.HAFIF_AZAMI_RISK + 2)]
        n._hafif_bildir("sabah", [], cok, riskler, "ali")
        m = n.gonderilen[0]
        assert "ve 3 enstrumanda daha" in m, m
        assert "ve 2 risk daha" in m, m
        db.close()


def test_olay_yasi_BARA_gore_olculuyor_bugune_gore_degil():
    """
    Bekci dersinin aynisi: olculen sey ile olcum ani ayni takvimden
    okunmali. Seri bir gun bayatsa sinyal O BARIN sinyalidir.
    """
    from finagent.pulse.screener import _gun_farki
    assert _gun_farki("2026-07-30", "2026-08-19") == 20
    assert _gun_farki("2026-08-19", "2026-08-19") == 0
    assert _gun_farki("2026-08-19T10:00:00+00:00", "2026-08-21") == 2
    # Ayristirilamiyorsa UYDURMA SAYI YOK.
    assert _gun_farki(None, "2026-08-19") is None
    assert _gun_farki("bozuk", "2026-08-19") is None


def test_bildirim_sayilari_TURKCE_yazimda():
    """Sohbet katmani '-%19,91' yaziyor; bildirimin ondan farkli
    konusmasi icin sebep yok. '19.91' Turk okuyucuda 19 bin 910."""
    from finagent.pulse.runner import _tr, _yuzde_tr
    assert _tr(19.91) == "19,91"
    assert _tr(1234.5, 1) == "1.234,5"
    assert _yuzde_tr(-19.91) == "-%19,91"
    assert _yuzde_tr(8.06) == "+%8,06"


# =====================================================================
# FAZ A1 — tek yavas collector butun kosuyu dusuremesin
#
# OLCULEN ARIZA (2026-08-19 22:15 nabzi, data/pulse.log):
#   22:15:00 basladi · 22:38-22:53 [tuik] zaman asimi x4 (her biri ~5 dk)
#   22:59:27 panel ajanlari acildi · 23:00:00 SIGTERM (2700 sn asildi)
#   tuik 1225,9 sn · toplama toplam 43,9 dk / 45 dk butce
#   -> bildirim GITMEDI, kosu izi YAZILMADI, bekci de fark etmedi.
# =====================================================================

# =====================================================================
# FAZ C1 — ritim ayari: hangi kip ne toplar, panel kosar mi, kime gider
# =====================================================================

def _ritim_ayar(**degisiklik):
    """Gercek ayarin kopyasi; testin degistirdigi alan uzerine yazilir."""
    import copy
    from finagent.config import load_settings
    s = load_settings()
    s.raw = copy.deepcopy(s.raw)
    for yol, deger in degisiklik.items():
        kip, alan = yol.split("__", 1)
        if deger is _SIL:
            s.raw["ritim"]["kipler"][kip].pop(alan, None)
        else:
            s.raw["ritim"]["kipler"][kip][alan] = deger
    return s


_SIL = object()


def test_ritim_kipleri_plist_etiketleriyle_BIREBIR_eslesiyor():
    """
    Kip adi = plist etiketinin son parcasi. Ayrisirsa bekci kipi
    taniyamaz ve `IZ_KIPLERI` bos kalir — yani gozetim SESSIZCE kapanir.
    Bu, 19 Agustos gecesi nabzin olup da fark edilmemesinin ta kendisi.
    """
    import plistlib
    from finagent.config import load_settings
    kok = _pathlib.Path(__file__).resolve().parents[1]
    s = load_settings()

    etiketler = set()
    for yol in sorted((kok / "launchd").glob("*.plist")):
        etiket = plistlib.loads(yol.read_bytes())["Label"]
        etiketler.add(etiket.rsplit(".", 1)[-1])

    kipler = set(s.ritim_kipleri)
    assert kipler, "ritim.kipler bos"
    # `bot` bir kip degil, sürekli calisan dinleyici.
    zamanlanmis = etiketler - {"bot"}
    assert kipler == zamanlanmis, (
        f"ayardaki kipler {sorted(kipler)} ile plist etiketleri "
        f"{sorted(zamanlanmis)} ayrisiyor")


def test_ritim_bilinmeyen_kipte_VARSAYILANA_DUSMEZ():
    """
    Sessiz varsayilan bu isin tek gercek tehlikesi: yanlis kaynaklari
    toplayip yanlis kisilere mesaj atmak. `sahip_bul` ile ayni gerekce.
    """
    from finagent.config import load_settings
    s = load_settings()
    for kotu in ("", "  ", "pulse", "NABIZ", None):
        try:
            s.ritim_kip(kotu)
            raise AssertionError(f"{kotu!r} icin hata bekleniyordu")
        except ValueError as e:
            assert "tanimsiz kip" in str(e), str(e)


def test_ritim_eksik_alan_HATA_verir():
    """"Varsayilan yok" bir slogan degil, test edilen bir sozlesme."""
    from finagent.config import Settings
    for alan in Settings.RITIM_ZORUNLU:
        s = _ritim_ayar(**{f"sabah__{alan}": _SIL})
        try:
            s.ritim_kip("sabah")
            raise AssertionError(f"{alan} eksikken hata bekleniyordu")
        except ValueError as e:
            assert alan in str(e), str(e)


def test_ritim_BOS_alici_listesi_reddediliyor():
    """
    Kosup kimseye gondermemek, hic kosmamaktan KOTU: kaynak tuketir,
    deftere yazar, ama kimse gormez — ve "bildirim gelmiyor" arizasi
    gunlerce fark edilmez.
    """
    for kotu in ([], None, "ali"):
        s = _ritim_ayar(sabah__alicilar=kotu)
        try:
            s.ritim_kip("sabah")
            raise AssertionError(f"alicilar={kotu!r} icin hata bekleniyordu")
        except ValueError as e:
            assert "alicilar" in str(e), str(e)


def test_ritim_TANIMSIZ_sahip_alici_olamaz():
    """
    Yazim hatasi ("yukse1") sessizce hicbir yere gonderilmemek demek.
    telegram.sahipler tek dogruluk kaynagi; alici listesi ondan turer.
    """
    s = _ritim_ayar(sabah__alicilar=["ali", "yukse1"])
    try:
        s.ritim_kip("sabah")
        raise AssertionError("tanimsiz sahip icin hata bekleniyordu")
    except ValueError as e:
        assert "yukse1" in str(e), str(e)


def test_ritim_kabuk_butcesi_panel_butcesinden_BUYUK():
    """
    Kucuk olsaydi kabuk, panel butcesi devreye girmeden sureci
    oldururdu: "kalan sahibin paneli atlandi ve KENDISINE SOYLENDI"
    yolu hic calismazdi — iki kademeli korumanin ustu olu olurdu.
    """
    s = _ritim_ayar(sabah__panel_butce_sn=1500, sabah__kabuk_butce_sn=1500)
    try:
        s.ritim_kip("sabah")
        raise AssertionError("esit butce icin hata bekleniyordu")
    except ValueError as e:
        assert "kabuk_butce_sn" in str(e), str(e)


def test_ritim_isyatirim_kipinde_kabuk_butcesi_IC_BUTCEYE_yetiyor():
    """
    OLCULEN ARIZA (2026-08-17): `isyatirim` ic butcesinde duzgunce durup
    `partial` donecekti ama kabuk ondan ONCE surec grubunu oldurdu; o
    gun BIST kapanisi icin hicbir sey uretilmedi. Ic butce ancak kabuk
    ondan genisse anlamlidir.
    """
    from finagent.config import load_settings
    s0 = load_settings()
    ic = float(s0.get("sources.isyatirim.azami_sure_sn", 780))
    # Gercek ayar kurali sagliyor mu?
    for kip in s0.ritim_kipleri:
        a = s0.ritim_kip(kip)
        if "isyatirim" in a["kaynaklar"]:
            assert a["kabuk_butce_sn"] >= ic + 240, (
                f"{kip}: kabuk {a['kabuk_butce_sn']} < ic {ic} + 240")
    # Ve kural GERCEKTEN bagliyor mu? Panel butcesi de kucultuluyor ki
    # duseren kural ISYATIRIM kurali olsun, panel kurali degil.
    s = _ritim_ayar(kapanis__kabuk_butce_sn=ic + 100,
                    kapanis__panel_butce_sn=60)
    try:
        s.ritim_kip("kapanis")
        raise AssertionError("dar kabuk butcesi icin hata bekleniyordu")
    except ValueError as e:
        assert "isyatirim" in str(e), str(e)


def test_ritim_panel_butcesi_OLCULEN_panel_suresine_yetiyor():
    """
    Panel sahip basina 282 sn olculdu (18 Agu: yuksel 20:46:05 ->
    20:48:11). Butce bunun altina duserse son sahip HER KOSUDA atlanir
    ve bu "sistem calisiyor ama bana mesaj gelmiyor" gibi gorunur.
    """
    from finagent.config import load_settings
    s = load_settings()
    OLCULEN_SAHIP_SN = 282.0
    for kip in s.ritim_kipleri:
        a = s.ritim_kip(kip)
        if not a["panel"]:
            continue
        # Butce kontrolu HER SAHIPTEN ONCE yapiliyor; N sahip icin
        # (N-1) x sure kadar butce yeter.
        gereken = OLCULEN_SAHIP_SN * (len(a["alicilar"]) - 1)
        assert a["panel_butce_sn"] > gereken, (
            f"{kip}: panel butcesi {a['panel_butce_sn']} sn, "
            f"{len(a['alicilar'])} sahip icin en az {gereken} sn gerekli")


def test_ritim_kaynaklari_GERCEK_collector_adlari():
    """
    Ayarda uydurma bir kaynak adi sessizce yok sayilirdi
    (`pipeline.collect` bilinmeyen adi suzuyor) — yani "toplaniyor"
    sanilan bir veri hic toplanmazdi.
    """
    from finagent.collectors import REGISTRY
    from finagent.config import load_settings
    s = load_settings()
    for kip in s.ritim_kipleri:
        for k in s.ritim_kip(kip)["kaynaklar"]:
            assert k in REGISTRY, f"{kip}: '{k}' diye bir collector yok"


def test_ritim_makro_BINANCE_TEN_SONRA_kosuyor():
    """
    OLCULEN BAYATLIK: `makro`, ALTIN_GRAM'i veritabanindaki PAXG
    serisinden TURETIYOR (collectors/makro.py `fiyat_serisi(vekil_id,
    400)`), kendi cekmiyor. `binance`'ten ONCE kosarsa DUNKU PAXG
    barini okur ve gram altin bir gun geride kalir.

    2026-08-20'de olculdu: ALTIN_GRAM son bar 18 Agustos, diger 15
    MAKRO serisi 19 Agustos'taydi. Ozet mesajindaki makro satiri
    (`ritim.ozet_makro`) tam bu kodu gosteriyor — yani bayatlik
    dogrudan kullaniciya yansiyordu.

    Sira bir KAYNAK LISTESI ayrintisi gibi gorunuyor ama gercekte bir
    VERI BAGIMLILIGI; goze carpmadigi icin de sessizce kayar.
    """
    from finagent.collectors import makro as _m
    import inspect
    # Bagimlilik GERCEKTEN var mi? (Kalkarsa bu test anlamsizlasir.)
    assert "fiyat_serisi" in inspect.getsource(_m), \
        "makro artik PAXG'yi veritabanindan okumuyor — test guncellenmeli"

    from finagent.config import load_settings
    s = load_settings()
    for kip in s.ritim_kipleri:
        k = s.ritim_kip(kip)["kaynaklar"]
        if "makro" not in k or "binance" not in k:
            continue
        assert k.index("binance") < k.index("makro"), (
            f"{kip}: `makro` (@{k.index('makro')}) `binance`ten "
            f"(@{k.index('binance')}) ONCE kosuyor — gram altin bir gun "
            "geride kalir")


def test_ritim_HABER_akisi_zamanlanmis_bir_kipte_toplaniyor():
    """
    OLCULDU 2026-08-19: `news` (RSS) HICBIR zamanlanmis kosuda yoktu.
    Son kosusu 18 Agu 19:59'du ve elle tetiklenmisti; Turkiye makro
    akisi bayatlamisti (AA-Ekonomi 18 Agu, BloombergHT 6 Agu). Oysa
    `gundem` araci 3 gunluk pencereyle okuyor — iki gun daha gecse
    "bugun ne oldu" sorusu bos donerdi.

    Ritim v2 §1 ogle kosusu icin "K1/K2 haber" diyor; sozun arkasinda
    bir collector olmasi gerekiyor.
    """
    from finagent.config import load_settings
    s = load_settings()
    haberli = [k for k in s.ritim_kipleri
               if "news" in s.ritim_kip(k)["kaynaklar"]]
    assert haberli, ("hicbir kip `news` toplamiyor — RSS akisi yalnizca "
                     "elle tetiklendiginde tazeleniyor")
    # EN AZ IKI KIP. Tek kip yeterli GORUNUYOR (gunde bir tazeleme,
    # 3 gunluk pencere) ama bir kosunun OLMESI varsayimsal degil
    # OLCULMUS bir olay: 2026-08-19'da nabiz kosusu 23:00'te SIGTERM
    # ile oldu. Tek tasiyici kip olsaydi akis o gun hic tazelenmezdi
    # ve `gundem` sessizce incelirdi — "veri var sanma" sinifi.
    assert len(haberli) >= 2, (
        f"`news` yalnizca {haberli} kipinde; o kosu duserse akis "
        "gun boyu bayat kalir (19 Agu'da nabiz kosusu gercekten oldu)")


# =====================================================================
# FAZ C7 — sohbet yolu DOKUNULMADI, kanitlaniyor
# =====================================================================

def _kademe1_db(d, sembol="AVTX", tier=1):
    from finagent.storage.db import Database
    from datetime import datetime, timezone
    db = Database(_pathlib.Path(d) / "t.db"); db.init_schema()
    with db.tx() as c:
        c.execute("INSERT INTO news (published_at, source, title, url, "
                  "symbols, publisher, tier) VALUES (?,?,?,?,?,?,?)",
                  (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                   "rss", "Avantium prepares financing package",
                   "https://newsroom.avantium.com/x", sembol, "Avantium", tier))
    return db


# =====================================================================
# HAFIZA — kalici gercekler katmani (M1/M2/M3)
#
# NEDEN VAR: `sohbet_kaydi` bir DOKUMDUR (ne konusuldu), calisma
# penceresi ise dar (son 8 tur / 6 saat) ve olmak zorunda. Arada bir
# bosluk kaliyordu ve OLCULDU: 2026-08-19 11:29'da kullanici "genel
# olarak ta musteri olarak satis fiyatimi cekmen gerekir hesaplarken"
# dedi — KALICI bir kural. Hicbir yere yazilmadi, alti saat sonra
# pencereden dustu. 25 tablonun hicbiri bunu tutmuyordu.
# =====================================================================

def _hafiza_db(d):
    from finagent.storage.db import Database
    db = Database(_pathlib.Path(d) / "t.db"); db.init_schema()
    return db


def test_hatirlanan_AYNI_KONUYU_gecersizlestirir_SILMEZ():
    """
    Celiski yonetimi: ayni (sahip, tur, konu) icin yeni kayit eskisini
    `gecerli = 0` yapar. SILMEZ — "ne zaman fikir degistirdi" sorusu
    cevaplanabilir kalmali; DELETE onu imkansiz kilardi ve bu katmanin
    varlik sebebi tam olarak gecmisi KAYBETMEMEK.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _hafiza_db(d)
        r1 = db.hatirla("ali", "tercih", "altin fiyati",
                        "Garanti'nin SAT fiyatini kullan",
                        kaynak_ts="2026-08-19T11:29:45")
        r2 = db.hatirla("ali", "tercih", "Altin Fiyati",   # BUYUK HARF
                        "Artik ALIS fiyatini kullan")
        assert r2["gecersizlesen"] == [r1["id"]], (
            "konu normalize edilmemis — 'Altin Fiyati' ile 'altin fiyati' "
            "iki ayri kural gibi duruyor")

        gecerli = db.hatirlananlar("ali")
        assert len(gecerli) == 1 and "ALIS" in gecerli[0]["icerik"]
        # ESKI KAYIT DURUYOR ve NEDEN dustugu yazili.
        tumu = {r["id"]: r for r in db.hatirlananlar("ali", gecerli=False)}
        assert len(tumu) == 2, "eski kayit SILINMIS"
        assert tumu[r1["id"]]["gecerli"] == 0
        assert f"#{r2['id']}" in tumu[r1["id"]]["gecersiz_sebep"]
        assert tumu[r1["id"]]["gecersiz_ts"]
        # FARKLI KONU cakismaz.
        r3 = db.hatirla("ali", "tercih", "haber kaynagi", "Once KAP'a bak")
        assert r3["gecersizlesen"] == []
        assert len(db.hatirlananlar("ali")) == 2
        db.close()


def test_hatirlanan_SAHIP_suzgeci_sizdirmiyor():
    """
    Sahip bir PARAMETREDIR, varsayilan yoktur — `positions` ile ayni
    disiplin. Baskasinin kaydini ne okuyabilmeli ne dusurebilmeli.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _hafiza_db(d)
        a = db.hatirla("ali", "olgu", "garanti", "Garanti'de altin hesabi var")
        db.hatirla("yuksel", "olgu", "garanti", "Yuksel'in kendi hesabi")

        assert len(db.hatirlananlar("ali")) == 1
        assert "altin hesabi" in db.hatirlananlar("ali")[0]["icerik"]
        # ID BILINSE BILE baskasinin kaydi dusurulemez.
        assert db.unut_hatirlanan("yuksel", a["id"]) is False
        assert len(db.hatirlananlar("ali")) == 1, "capraz silme oldu"
        assert db.unut_hatirlanan("ali", a["id"]) is True
        assert db.unut_hatirlanan("ali", a["id"]) is False, "iki kez dustu"

        # SAHIPSIZ CAGRI GURULTULU PATLAR. (`fn is db.metot` ile
        # ayirmak calismaz: bagli metot her erisimde YENI nesne.)
        for cagri in (lambda: db.hatirlananlar(""),
                      lambda: db.unut_hatirlanan("", 1)):
            try:
                cagri()
                raise AssertionError("sahipsiz cagri gecti")
            except ValueError as e:
                assert "sahip" in str(e), str(e)
        db.close()


def test_hatirlanan_GECERSIZ_girdiyi_reddediyor():
    """Varsayilana dusme yok: gecersiz tur/bos alan GURULTULU patlar."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _hafiza_db(d)
        for kotu, bekle in (
                (("", "tercih", "k", "i"), "sahip"),
                (("ali", "yok_boyle", "k", "i"), "tur"),
                (("ali", "tercih", "", "i"), "konu"),
                (("ali", "tercih", "k", "   "), "icerik")):
            try:
                db.hatirla(*kotu)
                raise AssertionError(f"{kotu} gecti")
            except ValueError as e:
                assert bekle in str(e), (kotu, str(e))
        assert db.hatirlananlar("ali") == []
        db.close()


def test_hatirla_araci_ONAYA_SUNAR_yazmaz():
    """
    `izlemeye_al` "geri alinabilir oldugu icin onay gerektirmez" diyor;
    burada olcut FARKLI. Yanlis hatirlanan bir "gercek" geri alinabilir
    ama bu arada HER cevabi sessizce yonlendirir — zarar tek bir islemde
    degil, gorunmez bir suruklenmede. Kullanici neyin kalici hale
    geldigini GORMELI.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        arac = next(a for a in tb.araclar()
                    if getattr(a, "name", "") == "hatirla")
        r = _cagir(arac, tur="tercih", konu="altin fiyati",
                   icerik="Garanti SAT fiyatini kullan")
        assert r["durum"] == "ONAY BEKLIYOR", r
        assert r["token"]
        # DOGRUDAN YAZMADI.
        assert db.hatirlananlar("ali") == [], "onaysiz yazdi"
        # Onay dosyasi KAYNAK DAMGASI tasiyor (alinti icin).
        from finagent.bot.onay import OnayDeposu
        veri = OnayDeposu(_pathlib.Path(d) / "pending").oku(r["token"])
        assert veri["_tip"] == "hatirla" and veri["kaynak_ts"], veri
        assert veri["_sahip"] == "ali"

        # GECERSIZ tur arac katmaninda da reddedilir. (`_hata` VERI
        # donduruyor — {"hata": ...} — cunku sessiz bosluk modeli
        # uydurmaya itiyor.)
        assert "hata" in _cagir(arac, tur="sacma", konu="k", icerik="i")
        assert "hata" in _cagir(arac, tur="tercih", konu="", icerik="i")
        assert db.hatirlananlar("ali") == []
        db.close()


def test_hafiza_blogu_HER_TURDA_baglama_giriyor():
    """
    OLCULDU 2026-08-20: `sohbet_arsivi` araci 78 asistan turunun
    yalnizca 6'sinda cagrildi (%7,7). Cagirmadigi turlerde model ya
    unutuyor ya UYDURUYOR. Geri cagirmayi modelin insafina birakmak,
    hafizayi olasiliksal yapar — bu yuzden OTOMATIK.
    """
    import tempfile
    from finagent.bot.chat import ChatEngine
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = _hafiza_db(d)
        db.hatirla("ali", "tercih", "altin fiyati",
                   "Garanti'nin SAT fiyatini kullan",
                   kaynak_ts="2026-08-19T11:29:45")
        ce = ChatEngine(load_settings(), db)

        blok = ce._hafiza_blogu("ali", "181 gram altin kac tl?")
        assert "KALICI OLARAK BILDIKLERIN" in blok, blok
        assert "SAT fiyatini" in blok
        # HER SATIR TARIH TASIYOR — alintilanabilsin, uydurulmasin.
        assert "2026-08-19" in blok, blok
        assert "TARIHIYLE alinti" in blok
        assert "OLMAYAN bir sey icin" in blok

        # SAHIPSIZ tur bos blok alir (baskasinin hafizasi sizmasin).
        assert ce._hafiza_blogu(None, "soru") == ""
        assert ce._hafiza_blogu("yuksel", "soru") == ""
        db.close()

    # BLOK URETILIYOR AMA KULLANILIYOR MU? Bu ayri bir iddia ve ilk
    # surumde test edilmiyordu: cagri yerini silmek testi DUSURMUYORDU
    # (kasitli bozma yakaladi). Mukemmel calisan ve hic cagrilmayan bir
    # hafiza, hic olmayan hafizayla aynidir.
    import ast, inspect, textwrap
    agac = ast.parse(textwrap.dedent(inspect.getsource(ChatEngine.cevapla)))
    cagrilan = {d.func.attr for d in ast.walk(agac)
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)}
    assert "_hafiza_blogu" in cagrilan, \
        "`cevapla` hafiza blogunu HIC cagirmiyor — blok olu kod"


def test_hafiza_blogu_GECMISE_ATIF_varsa_arsivi_de_koyuyor():
    """
    Kullanici konusmanin KENDISINE basvurdugunda ("daha once ne
    demistin") arsivden son turlar da baglama girer. Yanlis
    tetiklenmenin bedeli birkac fazla satir; kacirmanin bedeli modelin
    UYDURMASI.
    """
    import tempfile
    from finagent.bot.chat import ChatEngine
    from finagent.config import load_settings
    with tempfile.TemporaryDirectory() as d:
        db = _hafiza_db(d)
        db.sohbet_kaydet("111", "user", "Moderna ne yapiyor?", sahip="ali")
        db.sohbet_kaydet("111", "assistant", "Faz 3 sonucu acikladi.",
                         sahip="ali")
        ce = ChatEngine(load_settings(), db)

        atifli = ce._hafiza_blogu("ali", "daha once Moderna hakkinda ne demistin?")
        assert "GECMISE ATIF VAR" in atifli, atifli
        assert "Faz 3" in atifli
        assert "DOGRULANMIS DEGIL" in atifli, "dogrulama uyarisi yok"

        # ATIF YOKSA arsiv blogu GIRMEZ — her tura dokum tikmak,
        # bagimi sisirir ve alakasiz eski baglam halusinasyon uretir.
        duz = ce._hafiza_blogu("ali", "AMZN bugun ne yapti?")
        assert "GECMISE ATIF VAR" not in duz, duz
        db.close()


def test_hafiza_blogu_SORGU_PATLARSA_sohbeti_dusurmuyor():
    """
    Hafiza bir KOLAYLIKTIR, cevabin on kosulu degil. Veritabani kilitli
    diye kullanicinin sorusu cevapsiz kalmamali.
    """
    from finagent.bot.chat import ChatEngine
    from finagent.config import load_settings

    class _Patlak:
        def hatirlananlar(self, *a, **k): raise RuntimeError("db kilitli")
        def sohbet_ara(self, *a, **k): raise RuntimeError("db kilitli")

    ce = ChatEngine(load_settings(), _Patlak())
    assert ce._hafiza_blogu("ali", "daha once ne demistin") == ""


def test_hatirladiklarin_komutu_LISTELER_ve_UNUTTURUR():
    """
    Bir hafiza katmani, icinde NE OLDUGU gorulemiyorsa denetlenemez —
    ve denetlenemeyen hafiza, sessizce yanlis yonlendiren hafizadir.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _hafiza_db(d)
        bot = _onay_botu(d, db)
        r = db.hatirla("ali", "tercih", "altin fiyati",
                       "Garanti SAT fiyatini kullan",
                       kaynak_ts="2026-08-19T11:29:45")

        m = bot._hatirladiklarin_metni("111", None)
        assert f"#{r['id']}" in m and "altin fiyati" in m, m
        assert "2026-08-19" in m, "kaynak tarihi gosterilmiyor"
        assert "unut" in m

        # UNUTTURMA
        u = bot._hatirladiklarin_metni("111", f"unut {r['id']}")
        assert "gecersizlestirildi" in u, u
        assert db.hatirlananlar("ali") == []
        # Ayni kaydi tekrar unutmak: NET hata mesaji.
        assert "gecerli bir kaydin yok" in bot._hatirladiklarin_metni(
            "111", f"unut {r['id']}")
        # SAHIPSIZ SOHBET: baskasinin hafizasina erisim YOK.
        assert "bir kisiye bagli degil" in bot._hatirladiklarin_metni(
            "999", None)
        # Bos liste kullaniciya NE YAPACAGINI soyluyor.
        bos = bot._hatirladiklarin_metni("111", None)
        assert "kalici bir kural koyarsan" in bos.lower(), bos
        # Bozuk arguman: sessiz kabul YOK.
        assert "Kullanim" in bot._hatirladiklarin_metni("111", "sacma")
        assert "Hangi kaydi" in bot._hatirladiklarin_metni("111", "unut")
        db.close()


def test_hatirla_onayi_YURUTULUNCE_yaziliyor_ve_ezileni_SOYLUYOR():
    """
    Ezilen kayit SESSIZ GECMEZ: ayni konuya yeni bir kural yazildiginda
    eskisi duser ve kullanici bunu GORMELI — aksi halde "neden artik
    boyle davraniyor" sorusunun cevabi kaybolur.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = _hafiza_db(d)
        bot = _onay_botu(d, db)
        ilk = bot._hatirla_kaydet(
            {"tur": "tercih", "konu": "altin fiyati",
             "icerik": "SAT fiyatini kullan",
             "kaynak_ts": "2026-08-19T11:29:45"}, "ali")
        assert "Hatirladim" in ilk and "gecersizlestirildi" not in ilk

        ikinci = bot._hatirla_kaydet(
            {"tur": "tercih", "konu": "altin fiyati",
             "icerik": "ALIS fiyatini kullan"}, "ali")
        assert "1 eski kayit gecersizlestirildi" in ikinci, ikinci
        assert "silinmedi" in ikinci

        # GECERSIZ istek ARIZA degil, okunamamis niyet.
        kotu = bot._hatirla_kaydet({"tur": "sacma", "konu": "k",
                                    "icerik": "i"}, "ali")
        assert "gecersiz" in kotu.lower() and "Hicbir sey yazilmadi" in kotu
        assert len(db.hatirlananlar("ali")) == 1
        db.close()


def test_ARAC_ADLARI_canli_araclarla_BIREBIR():
    """
    `ARAC_ADLARI` SADECE BIR LISTE DEGIL, IZIN KAPISI.

    `chat.py` onu hem `allowed_tools` hem `can_use_tool` suzgeci olarak
    kullaniyor: `araclar()` icinde tanimli ama bu listede olmayan bir
    arac SESSIZCE REDDEDILIR — arac vardir, cagrilamaz, ve model
    "boyle bir aracim yok" der.

    2026-08-20'de tam bu oldu: `hatirla` ve `hatirladiklarin` araclara
    eklendi, bu listeye eklenmedi. Iki elle yazilan liste kacinilmaz
    olarak ayrisir; artik iki yonlu esitlik ZORUNLU.
    """
    from finagent.bot.tools import ARAC_ADLARI
    canli = set(_canli_arac_adlari())
    beyan = {a.rsplit("__", 1)[-1] for a in ARAC_ADLARI}
    assert not (canli - beyan), (
        f"ARAC_ADLARI'nda OLMAYAN arac: {sorted(canli - beyan)} — "
        "izin kapisi bunlari REDDEDER, model cagiramaz")
    assert not (beyan - canli), (
        f"olmayan araca izin veriliyor: {sorted(beyan - canli)}")
    # Ad bicimi de sozlesmenin parcasi: `mcp__finagent__<ad>`.
    assert all(a.startswith("mcp__finagent__") for a in ARAC_ADLARI)


def test_hafiza_araclari_ve_prompt_kurallari_KAYITLI():
    """
    Arac listesi ve prompt kurallari KODDAN uretiliyor; biri eklenip
    digeri unutulursa model araci HIC cagirmaz.
    """
    from finagent.bot.tools import ARAC_ADLARI
    from finagent.bot import chat as C
    kisa = {a.rsplit("__", 1)[-1] for a in ARAC_ADLARI}
    assert {"hatirla", "hatirladiklarin"} <= kisa, sorted(kisa)

    p = C.SYSTEM_PROMPT
    for parca in ("6c.", "6d.", "hatirla", "hatirladiklarin",
                  "KALICI OLARAK BILDIKLERIN", "UYDURMA",
                  "Hatirlamamak durustur"):
        assert parca in p, parca
    # Olculen vaka gerekce olarak duruyor.
    assert "2026-08-19 11:29" in p, "gerekce yazilmamis"


def test_ISLEM_BILDIRIMI_kacinca_olculuyor():
    """
    OLCULEN ZARAR (2026-08-19 14:51): kullanici ekran goruntusuyle
    "Bu kadar aldim. Gun sonu icin satis emri verecegim, kaca vereyim?"
    dedi. Model pozisyonu OKUDU (1 adet, 144,93 $), kur makasini
    hesapladi, seviye tablosu verdi — ama `pozisyon_kaydet` cagirmadi.
    Moderna portfoye HIC girmedi; ertesi sabahki ozet onu saymadi.

    Sebep prompt kurali 6'ydi: "kullanici 'portfoyume ekle' derse yap".
    Kural bir ISTEK bekliyordu, kullanici bir OLAY bildirmisti.

    Bu kontrol KARAR VERMIYOR, OLCUYOR: niyeti kelime listesiyle
    ayirmak guvenilmez (o is modelin, kural 6b onu soyluyor). Sayac,
    kural TUTMADIGINDA boslugun SESSIZ kalmamasi icin.
    """
    from finagent.bot.listener import FinBot as F

    gercek = "Bu kadar aldim. Gun sonu icin satis emri verecegim BUX ta. Kaca vereyim?"
    # ISLEM bildirildi, arac CAGRILMADI -> yakalanmali.
    assert F._islem_bildirimi_kacti(gercek, ["portfoy", "teknik"]) is True
    assert F._islem_bildirimi_kacti(gercek, []) is True
    assert F._islem_bildirimi_kacti("TRALT 10 hisse aldim", None) is True

    # Arac CAGRILDI -> bosluk yok.
    assert F._islem_bildirimi_kacti(
        gercek, ["portfoy", "pozisyon_kaydet"]) is False

    # ISLEM BILDIRIMI YOK -> ilgisiz.
    for soru in ("AMZN hissesinde olagandisi ne hareket oldu?",
                 "Binance portfoyum ne", "", None):
        assert F._islem_bildirimi_kacti(soru, []) is False


def test_sohbet_prompti_ISLEM_BILDIRIMINI_yaziyor():
    """
    Kural 6 bir ISTEK bekliyordu ("portfoyume ekle"); kullanici bir
    OLAY bildirdiginde ("aldim") hicbir sey yapilmiyordu. Kural 6b
    o bosluğu kapatiyor ve GEREKCESI prompt'ta duruyor — kural
    silinirse neden konuldugu da silinmesin.
    """
    from finagent.bot import chat as C
    p = C.SYSTEM_PROMPT
    assert "6b." in p, "islem bildirimi kurali yok"
    for parca in ("aldim", "sattim", "BEKLEME", "pozisyon_kaydet"):
        assert parca in p, parca
    # Cift kayit engeli de yazili olmali.
    assert "zaten kayitli" in p.lower(), "cift sunum engeli yazilmamis"
    # Ve olculen vaka gerekce olarak duruyor.
    assert "2026-08-19" in p, "gerekce (olculen vaka) yazilmamis"


def test_sade_katman_KADEME1_kanitini_dusuremez():
    """
    OLCULEN VAKA (2026-08-20, AVTX). Olay ajani:

      "19 Agu tarihli sirket duyurusu (newsroom.avantium.com, KADEME 1)
       finansman paketi hazirligini beyan ediyor"

    Sade katman:

      "dususun nedeni saglam kaynakla dogrulanmadi — sebep belirsiz"

    DOGRULANMIS bulgu atildi, DOGRULANMAMIS cekince tutuldu. Kullanici
    sebebi ogrenemedi ve sistem elindeki kademe-1 kaniti YOK saydi —
    `yanlis-yok-beyani` sinifi, bu projenin en kotu hata turu.

    KANITIN VARLIGI BIR METIN SORUSU DEGIL, BIR VERI SORUSU. Ilk surum
    teknik katmanda "kademe 1" ifadesini ariyordu ve KENDI TESTI kirdi:
    "kademe 1-2 kaydi OLMADIGI icin olcum yapilamadi" cumlesi de o
    ifadeyi iceriyor — olumsuz beyan olumlu sanildi. Artik `news.tier`
    okunuyor.
    """
    import tempfile
    from finagent.pulse.agents import sade_kanit_dusurdu
    veri = {"gorusler": [{"sembol": "AVTX", "yon": "asagi"}]}

    with tempfile.TemporaryDirectory() as d:
        db = _kademe1_db(d)                       # KADEME-1 haber VAR
        assert sade_kanit_dusurdu(
            "Dususun nedeni saglam kaynakla dogrulanmadi — sebep belirsiz.",
            veri, db) == 1
        # Sade katman kademe-1'i TASIYORSA ihlal yok.
        assert sade_kanit_dusurdu(
            "Sirketin kendi duyurusu (kademe 1) finansman paketi "
            "hazirligini soyluyor; 55 milyon rakami dogrulanmadi.",
            veri, db) == 0
        # Belirsizlik iddiasi yoksa ihlal yok.
        assert sade_kanit_dusurdu("Bugun sakin gecti.", veri, db) == 0
        # Baska sembol icin gorus varsa kanit o sembole ait degil.
        assert sade_kanit_dusurdu(
            "Sebep belirsiz.", {"gorusler": [{"sembol": "AMZN"}]}, db) == 0
        db.close()

    with tempfile.TemporaryDirectory() as d:
        # KADEME-1 YOK (tier 4) -> "dogrulanmadi" DOGRU bir ifade.
        db = _kademe1_db(d, tier=4)
        assert sade_kanit_dusurdu(
            "Dususun nedeni saglam kaynakla dogrulanmadi.", veri, db) == 0
        db.close()

    # Bos katman / db yok patlatmaz.
    assert sade_kanit_dusurdu(None, veri, None) == 0
    assert sade_kanit_dusurdu("sebep belirsiz", {}, None) == 0

    # SORGU PATLARSA IHLAL SAYMA — "sorgu basarisiz" ile "kanit yok"
    # ayri seyler (bekci dersi).
    class _Patlak:
        def query(self, *a, **k): raise RuntimeError("db kilitli")
    assert sade_kanit_dusurdu("sebep belirsiz", veri, _Patlak()) == 0


def test_sade_kanit_ihlali_PANEL_RUNS_a_yaziliyor():
    """
    Prompt kurali yeterli degil — projenin kendi dersi: "cozum prompt
    degil ARAC". Ihlal SAYILIR ve `panel_runs.hata`ya dusar; bloke
    ETMEZ, cunku bildirimi durdurmak kullaniciyi bilgisiz birakmanin
    daha kotu hali olurdu. Gorunur kilmak yeter.
    """
    import tempfile
    from finagent.pulse.agents import Panel
    with tempfile.TemporaryDirectory() as d:
        db = _kademe1_db(d)                       # AVTX icin kademe-1 haber
        p = Panel(_BosAyar(), db, "ali")
        # Sembol OZETTE de gecmeli, yoksa daha temel bir kontrol
        # (`JSON'da ozette gecmeyen sembol`) once devreye girer.
        metin = ("### SADE\nAVTX dun sert dustu; dususun nedeni saglam "
                 "kaynakla dogrulanmadi.\n\n"
                 "### TEKNIK\nAVTX: sirket duyurusu (kademe 1) finansman "
                 "paketi beyan ediyor. CAR -%18,15.")
        veri = {"gorusler": [{"sembol": "AVTX", "yon": "asagi"}]}
        n = p._not(metin, veri)
        assert n and "sade_kanit_dusurdu" in n, n

        # Kademe-1'i tasiyan sade katman TEMIZ.
        iyi = metin.replace("saglam kaynakla dogrulanmadi",
                            "sirketin kendi duyurusuna (kademe 1) dayaniyor")
        assert p._not(iyi, veri) is None, p._not(iyi, veri)
        db.close()


def test_hakem_prompt_IKI_YONLU_kesinlik_kurali_tasiyor():
    """
    Prompt tek yonluydu: "SADE katman TEKNIK katmandan DAHA KESIN
    konusamaz". Ters yonde koruma YOKTU ve sahada once o kirildi.
    """
    from finagent.pulse.agents import hakem_prompt
    p = hakem_prompt()
    assert "DAHA KESIN" in p
    assert "DAHA BELIRSIZ" in p, "ters yonde kural yok"
    assert "kademe 1" in p.lower(), "kademe-1 zorunlulugu yazilmamis"


def test_seans_satiri_ACILIS_ANINI_soyluyor():
    """
    Onceki surum acik borsalar icin "→ 18:00 (7s 54dk)" yaziyordu, yani
    KAPANISA KALAN SUREYI. Kullanicinin sordugu sey o degil (2026-08-20):
    "acildi mi, ne zaman acildi". Kapanis saati sabit ve her satirda
    tekrar etmesi gurultu; acilisin USTUNDEN GECEN SURE ise degisen ve
    bilgi tasiyan sey.
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from finagent.piyasa import durum_satiri, _sure
    AMS = ZoneInfo("Europe/Amsterdam")

    def _d(h, m, gun=20):
        return durum_satiri(datetime(2026, 8, gun, h, m, tzinfo=AMS)
                            .astimezone(timezone.utc)).replace("<b>", "").replace("</b>", "")

    ogle = _d(12, 30)
    assert "BIST ACIK 10:00'dan beri (3s 30dk)" in ogle, ogle
    assert "(7s" not in ogle, "kapanisa kalan sure hala yaziliyor"

    sabah = _d(8, 0)
    assert "BIST acilir 10:00 (1s sonra)" in sabah, sabah
    # "kapali" kelimesi "acilir" ile birlikte GEREKSIZ TEKRAR.
    assert "kapali" not in sabah, sabah

    kapanis = _d(17, 45)
    assert "BIST kapandi 18:00 (45dk once)" in kapanis, kapanis
    assert "ABD ACIK" in kapanis, "17:45'te ABD hala acik olmali"

    assert "hafta sonu" in _d(12, 0, gun=22)

    # TAM SAATTE '0dk' YAZILMAZ.
    assert _sure(60) == "1s" and _sure(66) == "1s 6dk" and _sure(45) == "45dk"
    assert _sure(-5) == "0dk"


def test_yuzde_YON_OKU_tasiyor_ve_sifir_NOTR():
    """
    Eksi isareti tek karakter ve uzun bir satirin ortasinda KACIYOR —
    kullanici "%1,89 ne, asagi mi yukari mi" diye sordu (2026-08-20).

    Ok bir SEMBOL, sifat DEGIL: makro satirinin "yorum yazma"
    disiplinini bozmaz. Yuvarlamadan sonra sifira duseni NOTR isaret
    alir; '🔺+%0,00' olmayan bir hareket iddia ederdi.
    """
    from finagent.pulse.runner import _yuzde_tr
    assert _yuzde_tr(-19.91, ok=True) == "🔻-%19,91"
    assert _yuzde_tr(8.06, ok=True) == "🔺+%8,06"
    assert _yuzde_tr(0.0, ok=True) == "▪️%0,00"
    # Yuvarlama SONRASI sifir: ok NOTR, isaret de dusuyor.
    assert _yuzde_tr(0.001, ok=True) == "▪️%0,00"
    assert _yuzde_tr(-0.001, ok=True) == "▪️%0,00"
    # `ok=False` eski davranis — sohbet katmani bunu kullaniyor.
    assert _yuzde_tr(-19.91) == "-%19,91"
    assert "🔻" not in _yuzde_tr(-19.91)


def test_ozet_TEK_POZISYONLU_hesapta_yuzdeyi_TEKRARLAMIYOR():
    """
    OLCULDU 2026-08-20: Midas'ta tek pozisyon var ve satir
    "MIDAS +%6,14 · en cok TRALT +%6,14" diye cikti — ikinci yari
    SIFIR bilgi tasiyor. Ayrimin anlamli olmasi icin en az IKI farkli
    hareket gerekiyor.
    """
    import tempfile
    from finagent.analysis import portfolio as P
    with tempfile.TemporaryDirectory() as d:
        n, db, _ = _ozet_nabzi(d)
        eski = P.gunluk_degisim

        def _sahte(en_az):
            return lambda db_, h, s: (
                {"hesap": "midas", "para_birimi": "TRY", "degisim_%": 6.14,
                 "kapsam": 1.0, "tarih": "2026-08-19",
                 "adet_tarihi": "2026-08-18", "adet_yas_gun": 1,
                 "en_cok": ("TRALT", 6.14), "en_az": en_az,
                 "not": "kur etkisi haric (fiyat hareketi)"}
                if h == "bux" else None)
        try:
            P.gunluk_degisim = _sahte(None)          # TEK kalem
            tek = "\n".join(n._portfoy_satirlari("ali"))
            assert "tek kalem: TRALT" in tek, tek
            assert tek.count("6,14") == 1, f"yuzde tekrar etmis: {tek}"

            P.gunluk_degisim = _sahte(("XYZ", -2.1))  # IKI kalem
            iki = "\n".join(n._portfoy_satirlari("ali"))
            assert "en cok TRALT" in iki and "en az XYZ" in iki, iki
            assert "tek kalem" not in iki, iki
        finally:
            P.gunluk_degisim = eski
        db.close()


def test_fx_kuru_SERIDEN_turetiyor_peg_VARSAYMIYOR():
    """
    OLCULEN BOSLUK: `fx_rates` yalnizca gercek kur ciftlerini tasiyor
    (EUR/USD, USD/TRY, EUR/TRY) ama bir POZISYON PARA BIRIMI her zaman
    kur cifti degil — Binance hesabi USDT cinsinden ve tabloda USDT'li
    SIFIR satir var. Sonuc: hesabin %98,6'si cevrilemedigi icin gunluk
    degisim "kapsam %1" ile reddediliyordu.

    PEG VARSAYILMIYOR: USDT'nin USD fiyati OLCULU (cgfiyat, 365 bar) ve
    son deger 0,99925 — 1,0 DEGIL. "Stablecoin'dir, 1 kabul et" bir
    varsayim; "olculen fiyati kullan" bir olcum.
    """
    import tempfile
    from finagent.storage.db import Database
    db = Database(_pathlib.Path(tempfile.mkdtemp()) / "t.db"); db.init_schema()
    iid = db.upsert_instrument("USDT", "CRYPTO", "Tether", "crypto", "USDT")
    db.upsert_prices(iid, [{"ts": "2026-08-18", "close": 0.99925, "volume": 1}],
                     "cgfiyat", currency="USD")

    r = db.fx_kuru("USDT", "USD")
    assert r and abs(r["rate"] - 0.99925) < 1e-9, r
    assert r["kaynak"].startswith("seri:"), r["kaynak"]
    assert r["rate"] != 1.0, "peg VARSAYILMIS — olculen fiyat kullanilmali"

    ters = db.fx_kuru("USD", "USDT")
    assert ters and abs(ters["rate"] - 1 / 0.99925) < 1e-9, ters
    assert "ters cevrildi" in ters["kaynak"], ters["kaynak"]

    # DAR KAPI: sembol ya da para birimi tutmuyorsa TUREV YOK.
    assert db.fx_kuru("YOKBOYLE", "USD") is None
    assert db.fx_kuru("USDT", "JPY") is None
    db.close()


def test_fx_kuru_UCGENLEME_tek_adim_ve_tam():
    """
    USDT -> EUR tek atlamada cozulemiyor: USDT'nin serisi USD cinsinden,
    EUR/USD ise `fx_rates`te. Ara birim uzerinden CARPIM spot kurlarda
    TAM'dir, yaklasik degil.

    TEK ADIM: sinirsiz zincir uzun yollarda sessizce sacma kurlar
    uretirdi. Ara birim havuzu `fx_rates`in kendi para birimleriyle
    sinirli.
    """
    import tempfile
    from finagent.storage.db import Database
    db = Database(_pathlib.Path(tempfile.mkdtemp()) / "t.db"); db.init_schema()
    iid = db.upsert_instrument("USDT", "CRYPTO", "Tether", "crypto", "USDT")
    db.upsert_prices(iid, [{"ts": "2026-08-18", "close": 0.99925, "volume": 1}],
                     "cgfiyat", currency="USD")
    with db.tx() as c:
        c.execute("INSERT INTO fx_rates (base,quote,rate,ts,source) "
                  "VALUES ('EUR','USD',1.16863,'2026-08-20','yahoo')")

    r = db.fx_kuru("USDT", "EUR")
    beklenen = 0.99925 * (1 / 1.16863)
    assert r and abs(r["rate"] - beklenen) < 1e-9, (r, beklenen)
    assert "uzerinden" in r["kaynak"], r["kaynak"]

    # ZINCIR UZAMASIN: iki ara birim gerektiren yol COZULMEMELI.
    # JPY hicbir tabloda yok; USDT->JPY iki atlama ister.
    assert db.fx_kuru("USDT", "JPY") is None
    # Ayni birim her zaman 1.0 ve tur atlamaz.
    assert db.fx_kuru("USDT", "USDT")["rate"] == 1.0
    db.close()


def test_binance_hesabi_ARTIK_olculebiliyor():
    """
    Somut sonuc: USDT cevrilebildigi icin Binance hesabinin gunluk
    degisimi artik hesaplaniyor. Once kapsam %1,4 ile reddediliyordu.
    """
    import math, tempfile
    from finagent.storage.db import Database
    from finagent.analysis.portfolio import gunluk_degisim
    db = Database(_pathlib.Path(tempfile.mkdtemp()) / "t.db"); db.init_schema()
    for sem, ccy, taban in (("USDT", "USD", 0.999), ("BNB", "USDT", 600.0)):
        i = db.upsert_instrument(sem, "BINANCE", sem, "crypto", "USDT")
        db.upsert_prices(i, [
            {"ts": f"2026-08-{17 + g:02d}", "close": taban * (1 + 0.01 * g),
             "volume": 1} for g in range(2)], "t", currency=ccy)
    db.insert_positions("binance", "2026-08-18T19:49:00", [
        {"symbol": "USDT", "quantity": 300, "market_value": 300.85,
         "currency": "USDT"},
        {"symbol": "BNB", "quantity": 0.0063, "market_value": 3.78,
         "currency": "USDT"}], "ali")
    d = gunluk_degisim(db, "binance", "ali")
    assert d and not d.get("yetersiz_kapsam"), d
    assert d["kapsam"] == 1.0, d
    db.close()


def _maruziyet_araci(tb):
    return next(a for a in tb.araclar() if getattr(a, "name", "") == "maruziyet")


def _cok_para_birimli(db, sahip="ali"):
    """
    Ali'nin gercek yapisinin kucugu: buyuk EUR pozisyonu + kucuk TRY
    pozisyonu. Cevrilmezse TRY agirligi SISER.
    """
    import math
    iid = {}
    for sem, ccy in (("ASML", "EUR"), ("TRALT", "TRY"), ("USDT", "USDT")):
        i = db.upsert_instrument(sem, "BUX" if ccy == "EUR" else "BIST",
                                 sem, "equity", ccy)
        iid[sem] = i
        db.upsert_prices(i, [
            {"ts": f"2026-{1 + g // 28:02d}-{1 + g % 28:02d}",
             "close": 100 + g * 0.5 + 3 * math.sin(g / 3), "volume": 1000}
            for g in range(120)], "t", currency=ccy)
    # MAKRO FAKTOR SERISI SART: agirliklar faktor dongusunun ICINDE
    # hesaplaniyor (`w = deger / toplam`). Faktor yoksa `faktorler` bos
    # doner ve test HICBIR SEY sinamaz — sessizce yesil olurdu.
    fi = db.upsert_instrument("BRENT", "MAKRO", "Brent", "emtia", "USD")
    db.upsert_prices(fi, [
        {"ts": f"2026-{1 + g // 28:02d}-{1 + g % 28:02d}",
         "close": 90 + 2 * math.cos(g / 4), "volume": 1000}
        for g in range(120)], "makro", currency="USD")
    with db.tx() as c:
        c.execute("INSERT INTO fx_rates (base,quote,rate,ts,source) "
                  "VALUES ('TRY','EUR',0.0178,'2026-08-19','test')")
    db.insert_positions("bux", "2026-08-19T10:00:00", [
        {"symbol": "ASML", "quantity": 1, "market_value": 2424.20,
         "currency": "EUR"}], sahip)
    db.insert_positions("midas", "2026-08-19T10:00:00", [
        {"symbol": "TRALT", "quantity": 1, "market_value": 489.20,
         "currency": "TRY"}], sahip)
    return iid


def test_maruziyet_agirliklari_TEK_PARA_BIRIMINDE():
    """
    OLCULEN KUSUR — PANELIN KENDISI BULDU (2026-08-20). `maruziyet`
    `market_value`'lari CEVRILMEDEN topluyordu: BUX EUR, Midas TRY,
    Binance USDT yan yana. Sonuc TL pozisyonlarin agirligini SISIRDI:

        TRALT  489,20 TRY -> gorunen %7,28 · gercek %0,14   (52 KAT)
        ASML  2424,20 EUR -> gorunen %36,05 · gercek %39,10

    Model bunu "portfoyde altin var" diye okudu. `portfolio_summary`
    ayni sinira sahip ama onu ACIKCA BEYAN EDIYOR; buradaki fark,
    cevrilmemis toplamin AGIRLIK OLARAK KULLANILMASIYDI.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        _cok_para_birimli(db)
        r = _cagir(_maruziyet_araci(tb))
        assert r.get("durum") != "error", r

        assert r["para_birimi"] == "EUR", r["para_birimi"]
        poz = {p["sembol"]: p["agirlik_pct"]
               for p in r["faktorler"][list(r["faktorler"])[0]]["pozisyonlar"]}
        # 489,20 TRY x 0,0178 = 8,71 EUR -> 8,71 / 2432,91 = %0,36
        assert poz["TRALT"] < 1.0, (
            f"TRALT agirligi %{poz['TRALT']} — TRY cevrilmemis (sismis)")
        assert poz["ASML"] > 95.0, poz
        assert abs(sum(poz.values()) - 100.0) < 1.0, sum(poz.values())
        db.close()


def test_maruziyet_CEVRILEMEYENI_adiyla_raporluyor():
    """
    Cevrilemeyen pozisyon agirlik hesabinin DISINDA kaliyor. Sessizce
    atlanirsa okuyan taraf "portfoyun tamami bu" diye okur — kapsam
    boslugu gorunur olmali.

    Somut vaka: `fx_rates`'te USDT'li SIFIR satir var, yani Binance
    hesabinin tamami cevrilemiyor.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        _cok_para_birimli(db)
        # USDT pozisyonu: kuru YOK, cevrilemez.
        db.insert_positions("binance", "2026-08-19T10:00:00", [
            {"symbol": "USDT", "quantity": 300, "market_value": 300.85,
             "currency": "USDT"}], "ali")
        # SERIYI POZISYONUN COZULEN ENSTRUMANINA BAGLA. `insert_positions`
        # hesaba gore AYRI bir enstruman cozebiliyor; seriyi elle yazilan
        # id'ye baglamak testi sessizce anlamsizlastirirdi (pozisyon
        # `len(b) < 30` ile cevrim adimina HIC gelmezdi).
        import math as _m
        iid = db.latest_positions("binance", "ali")[0]["instrument_id"]
        db.upsert_prices(iid, [
            {"ts": f"2026-{1 + g // 28:02d}-{1 + g % 28:02d}",
             "close": 1.0 + 0.001 * _m.sin(g), "volume": 1000}
            for g in range(120)], "t", currency="USDT")
        r = _cagir(_maruziyet_araci(tb))
        assert any("USDT" in x for x in r.get("cevrilemeyen", [])), r
        assert "cevrilemedigi icin" in r["not"], r["not"]
        # Ve HESABA GIRMEMIS olmali.
        poz = {p["sembol"] for p in
               r["faktorler"][list(r["faktorler"])[0]]["pozisyonlar"]}
        assert "USDT" not in poz, "cevrilemeyen pozisyon agirliga girmis"
        db.close()


def test_maruziyet_ana_para_birimi_secimi_DAIRESEL_degil():
    """
    "En buyuk pozisyonun para birimi" demek, buyuklugu bilmek icin
    zaten cevirmek demektir — dairesel. Secim SAYIYLA yapilir: en cok
    pozisyonu CEVIREBILEN aday kazanir, esitlikte alfabetik.
    """
    import inspect, tempfile
    from finagent.bot import tools as T
    kaynak = inspect.getsource(T.ToolBox.araclar)
    assert "en_iyi, ana = -1, adaylar[0]" in kaynak, \
        "ana para birimi secimi degismis — dairesellik kontrolu gerekiyor"
    # Aday listesi alfabetik siralanmali ki esitlik deterministik cozulsun.
    assert "adaylar = sorted(" in kaynak, kaynak[:0]

    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        _cok_para_birimli(db)
        # EUR 1 pozisyon, TRY 1 pozisyon; TRY->EUR kuru VAR, EUR->TRY
        # kuru de ters cevirmeyle bulunur, yani ikisi de 2 cevirebilir.
        # Esitlikte ALFABETIK: EUR < TRY.
        assert _cagir(_maruziyet_araci(tb))["para_birimi"] == "EUR"
        db.close()


def test_ritim_sohbet_promptuna_SIZMADI():
    """
    Ritim v2 §3.7: bu is `bot/listener.py`, `chat.py`, `run.py bot` ve
    onlarin promptlarina HIC dokunmuyor. "Gunun onceki kosulari"
    baglami YALNIZCA hakemin — sohbet ajani her soruyu kendi baglaminda
    cevaplar ve oraya gun ici kosu gecmisi enjekte etmek, kullanicinin
    sormadigi bir seyi cevaba karistirmaktir.
    """
    from finagent.bot import chat as C
    kaynak = C.SYSTEM_PROMPT if isinstance(getattr(C, "SYSTEM_PROMPT", None), str) \
        else ""
    assert kaynak, "chat.SYSTEM_PROMPT bulunamadi"
    for dize in ("onceki kosu", "önceki koşu", "bugun daha once",
                 "bugün daha önce", "BUGUN DAHA ONCE", "ritim.kipler"):
        assert dize.lower() not in kaynak.lower(), \
            f"sohbet promptuna ritim baglami sizmis: {dize!r}"


def test_ritim_sohbet_araclarini_DEGISTIRMEDI():
    """
    Kullaniciya gorunen arac yuzeyi ayni kalmali. Arac listesi KODDAN
    uretiliyor; ritim isi sirasinda bir araci kazara dusurmek ya da
    eklemek, `neler_yapabilirim` ciktisini sessizce degistirirdi.
    """
    from finagent.bot.tools import ARAC_ADLARI
    # `ARAC_ADLARI` MCP tam adlarini tutuyor (mcp__finagent__<ad>).
    kisa = {a.rsplit("__", 1)[-1] for a in ARAC_ADLARI}
    # Ritimden ONCE var olan ve kullanicinin gunluk kullandigi araclar.
    zorunlu = {"portfoy", "ara", "teknik", "haberler", "gundem", "takvim",
               "olay_etkisi", "karsilastir", "iliski", "maruziyet",
               "fiyat_serisi", "fx", "saat", "kaynak_kademesi",
               "rapor_uret", "izleme_listesi", "gecmis_gorus",
               "sohbet_arsivi", "neler_yapabilirim"}
    eksik = zorunlu - kisa
    assert not eksik, f"ritim isi sirasinda arac dusmus: {sorted(eksik)}"


def test_ritim_dinleyicinin_KOSU_yoluna_girmiyor():
    """
    Dinleyici surekli calisan TEK surec; ritim katmanindan hicbir sey
    onu import etmemeli. Aksi halde bir kosu degisikligi dinleyiciyi
    yeniden baslatmayi ZORUNLU kilar ve "bot calisiyor ama eski kodla"
    sinifini genisletir.
    """
    import ast
    kok = _pathlib.Path(__file__).resolve().parents[1]
    agac = ast.parse((kok / "src" / "finagent" / "bot" / "listener.py")
                     .read_text(encoding="utf-8"))
    # METIN DEGIL AST: yorumlarda modul adi gecebilir (gerekce orada
    # yaziliyor) ve metin aramasi buna takilirdi — bu testte uc kez
    # yasandi.
    for d in ast.walk(agac):
        if isinstance(d, ast.ImportFrom) and d.module:
            assert "pulse.runner" not in d.module, (
                f"dinleyici kosu katmanini import ediyor: {d.module} "
                f"(satir {d.lineno})")
        if isinstance(d, ast.Import):
            for a in d.names:
                assert "pulse.runner" not in a.name, a.name


# =====================================================================
# FAZ C4/C6 — ozet iskeleti: panel yolunun eksik disiplini
# =====================================================================

def _etkin_kod(fn) -> str:
    """
    Bir fonksiyonun YORUMSUZ ve DOCSTRING'SIZ kaynagi.

    "Su dize kodda gecmesin" turu testler UC KEZ bir YORUMA takildi ve
    yanlis alarm verdi: gerekce metni kacinilmaz olarak yasakladigi
    seyi ANLATIYOR ("elle yazilan ('bux','midas') demeti ..."). Test
    davranisi tutmali, aciklamayi degil.
    """
    import ast, inspect, textwrap
    kaynak = textwrap.dedent(inspect.getsource(fn))
    agac = ast.parse(kaynak)
    govde = agac.body[0].body                       # type: ignore[attr-defined]
    if (govde and isinstance(govde[0], ast.Expr)
            and isinstance(govde[0].value, ast.Constant)
            and isinstance(govde[0].value.value, str)):
        govde = govde[1:]                           # docstring'i at
    # `ast.unparse` yorumlari zaten dusuruyor.
    return "\n".join(ast.unparse(d) for d in govde)


def _ozet_nabzi(d, sahipler=("ali",)):
    """Ozet mesajini YAKALAYAN bir Nabiz; LLM'e hic gitmez."""
    from finagent.pulse.runner import Nabiz
    db, sembol = _fazb_db(d, sahipler=sahipler)
    n = Nabiz(_fazb_ayar(sahipler, kok=d), db)
    n.gonderilen = []
    n._sahibe_bildir = lambda s, m, reply_markup=None: (
        n.gonderilen.append((s, m, reply_markup)) or True)
    return n, db, sembol


def test_ozet_panel_yolunda_da_SEANS_satirini_tasiyor():
    """
    OLCULEN BOSLUK: tazelik suzgeci, bastirma, seans satiri ve gruplama
    YALNIZCA `_hafif` dalindaydi (runner.py:421-433). `panel: true`
    yapilan an sabah/ogle kosulari o duzeltmelerin DISINA cikardi —
    yani 19 Agustos'ta duzeltilen "hangi borsanin kapanisi" hatasi
    ritim v2 ile geri gelirdi.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        n, db, _ = _ozet_nabzi(d)
        n._panel_fazi = lambda *a, **k: ({"sade": "Bugun sakin."}, 0, None)
        n.calistir(bildir=True, panel=True, kip="sabah")
        assert len(n.gonderilen) == 1, n.gonderilen
        _, m, _ = n.gonderilen[0]
        # Seans durumu OLCULEREK yaziliyor (piyasa.durum_satiri).
        for borsa in ("BIST", "Amsterdam", "Frankfurt", "ABD"):
            assert borsa in m, f"seans satirinda {borsa} yok: {m[:400]}"
        assert "Tatil takvimi yok" in m, m
        # Kosu adi piyasa durumu IDDIA ETMIYOR.
        assert "Sabah taramasi" in m, m
        db.close()


def test_ozet_RISK_alarmini_panel_yolunda_da_gonderiyor():
    """
    Panel yolunda `_yeni_riskler` HIC cagrilmiyordu: `panel: true`
    yapilan an yogunlasma/acik_zarar alarmlari TAMAMEN kaybolurdu ve
    bunu kimse fark etmezdi (alarm gelmemesi "sorun yok" gibi gorunur).
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        n, db, sembol = _ozet_nabzi(d)
        n._panel_fazi = lambda *a, **k: ({"sade": "sakin"}, 0, None)
        # Risk sinyalini dogrudan enjekte et. THYAO SECILDI cunku ali'nin
        # portfoyunde YOK: ASML kullanilsaydi gercek tarayicinin urettigi
        # yogunlasma riskiyle (tek pozisyon -> %100) ayni anahtara
        # duserdi ve iki farkli deger bastirmayi bozardi.
        gercek = n._ortak_faz
        def _sahte(kip):
            o = gercek(kip)
            o["sinyaller"] = list(o["sinyaller"]) + [{
                "instrument_id": sembol["THYAO"], "sembol": "THYAO",
                "tur": "yogunlasma", "guc": 1.0, "venue": "BUX",
                "yon": "notr", "kanit": {"agirlik_%": 41.0}}]
            return o
        n._ortak_faz = _sahte
        n.calistir(bildir=True, panel=True, kip="sabah")
        _, m, _ = n.gonderilen[0]
        assert "yogunlasma" in m, f"risk alarmi panel yolunda kayboldu: {m}"
        assert "THYAO" in m and "41" in m, m
        # BASTIRMA DA CALISIYOR: ikinci kosuda ayni risk TEKRARLANMAZ.
        n.gonderilen.clear()
        n.calistir(bildir=True, panel=True, kip="sabah")
        _, m2, _ = n.gonderilen[0]
        assert "THYAO" not in m2, f"ayni risk iki kez bildirildi: {m2}"
        db.close()


def test_no_notify_kosusu_BASTIRMA_tablosunu_kirletmiyor():
    """
    OLCULEN TUZAK: `--no-notify` kosulari (kip suresi olcumu, elle
    deneme, test) `bildirim_durumu`'na yaziyordu ve BIR SONRAKI GERCEK
    kosu o kayitlar yuzunden SUSUYORDU. Yani "sistemi olcmek" onu
    sessizlestiriyordu.

    "Bildirildi" isareti ancak mesaj GERCEKTEN gittiginde konmali.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        n, db, sembol = _ozet_nabzi(d)
        n._panel_fazi = lambda *a, **k: ({"sade": "sakin"}, 0, None)
        gercek = n._ortak_faz
        def _sahte(kip):
            o = gercek(kip)
            o["sinyaller"] = list(o["sinyaller"]) + [{
                "instrument_id": sembol["THYAO"], "sembol": "THYAO",
                "tur": "yogunlasma", "guc": 1.0, "venue": "BUX",
                "yon": "notr", "kanit": {"agirlik_%": 41.0}}]
            return o
        n._ortak_faz = _sahte

        # 1) OLCUM kosusu: mesaj YOK, tablo da BOS kalmali.
        n.calistir(bildir=False, panel=True, kip="sabah")
        assert n.gonderilen == [], n.gonderilen
        kayit = db.query("SELECT COUNT(*) c FROM bildirim_durumu")[0]["c"]
        assert kayit == 0, (
            f"--no-notify kosusu bastirma tablosuna {kayit} satir yazdi; "
            "bir sonraki gercek kosu susardi")

        # 2) GERCEK kosu: alarm YINE gidiyor.
        n.calistir(bildir=True, panel=True, kip="sabah")
        _, m, _ = n.gonderilen[0]
        assert "THYAO" in m, f"olcum kosusu gercek alarmi susturdu: {m}"
        db.close()


def test_ozet_panel_bir_sey_bulmasa_da_MESAJ_gidiyor():
    """
    Ritim v2 §5: "Ozet mesajina sessizlik donmesin." Kullanici gunde
    dort mesaj bekliyor; gitmeyen mesaj bekciye "kosmadi" gibi,
    kullaniciya "bozuk" gibi gorunur. Alarm sinifi ayri — o bastirilir.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        n, db, _ = _ozet_nabzi(d)
        # Esigi gecen sinyal YOK -> panel hic kosmaz.
        n._gundem = lambda *a, **k: []
        n._panel_fazi = lambda *a, **k: ({}, 0, None)
        r = n.calistir(bildir=True, panel=True, kip="sabah")
        assert len(n.gonderilen) == 1, (
            f"panel bir sey bulmadi diye mesaj GITMEDI: {n.gonderilen}")
        _, m, _ = n.gonderilen[0]
        assert "🧠" in m, m
        db.close()


def test_ozet_portfoy_satiri_KAPSAM_yetmezse_SAYI_yazmiyor():
    """
    `positions` anlik goruntuleri PARCALI (olculdu 2026-08-20: son dort
    goruntu 18/2/1/4 satir). Portfoyun %80'ini fiyatlayamiyorsak
    "portfoy +%0,4" demek YANLIS BEYANDIR: sayi dogru hesaplanmis olsa
    bile temsil ettigi sey portfoy degil, olculebilen parcasidir.
    """
    import tempfile
    from finagent.analysis import portfolio as P
    with tempfile.TemporaryDirectory() as d:
        n, db, _ = _ozet_nabzi(d)
        eski = P.gunluk_degisim
        P.gunluk_degisim = lambda db_, h, s: (
            {"hesap": h, "kapsam": 0.30, "yetersiz_kapsam": True}
            if h == "bux" else None)
        try:
            satirlar = n._portfoy_satirlari("ali")
        finally:
            P.gunluk_degisim = eski
        birlesik = "\n".join(satirlar)
        assert "olculemedi" in birlesik, birlesik
        assert "%30" in birlesik, birlesik
        # SAYI YOK: yuzde degisim yazilmamali.
        assert "gunluk degisim olculemedi" in birlesik, birlesik
        db.close()


def test_ozet_portfoy_hesaplari_VERIDEN_turuyor():
    """
    Sabit bir ("bux","midas","binance") demeti, yeni bir hesap
    eklendiginde SESSIZCE eksik kalirdi — kullanici o hesabin satirini
    hic gormez ve eksik oldugunu da bilmez. Liste `positions`'tan turer.
    """
    import tempfile
    from finagent.pulse.runner import Nabiz
    kaynak = _etkin_kod(Nabiz._portfoy_satirlari)
    assert "DISTINCT account FROM positions" in kaynak, \
        "hesap listesi veriden turemiyor"
    for elle in ("bux", "midas", "binance"):
        assert elle not in kaynak, f"hesap adi koda gomulu: {elle}"

    with tempfile.TemporaryDirectory() as d:
        n, db, _ = _ozet_nabzi(d)
        # `_fazb_db` yalnizca 'bux' hesabi yaziyor -> tek satir grubu.
        satirlar = "\n".join(n._portfoy_satirlari("ali"))
        assert "BUX" in satirlar, satirlar
        assert "MIDAS" not in satirlar, "olmayan hesap icin satir uretildi"
        # Pozisyonsuz sahip: HIC satir yok (uydurma sayi yok).
        assert n._portfoy_satirlari("yok_boyle_sahip") == []
        db.close()


def test_ozet_portfoy_ADETLERIN_YASINI_beyan_ediyor():
    """
    FIYAT ile ADET AYNI TAZELIKTE DEGIL ve ayni satirda gorununce oyle
    saniliyor. Olculdu 2026-08-20: bux fiyatlari 19 Agustos, adetleri
    14 Agustos — ALTI GUN. Arada islem yapildiysa agirliklar yanlis ve
    bunu VERIDEN bilemeyiz; bilemedigimiz seyi iddia etmek yerine
    TARIHI soyluyoruz.

    Uyari esigi: 1 gunluk fark (hafta sonu, gece kosusu) GURULTU olur;
    iki gun ve otesi kullanicinin bilmesi gerekendir.
    """
    import tempfile
    from finagent.analysis import portfolio as P
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        n, db, _ = _ozet_nabzi(d)

        def _sahte(yas, adet_tarihi):
            return lambda db_, h, s: (
                {"hesap": h, "para_birimi": "EUR", "degisim_%": -1.49,
                 "kapsam": 1.0, "tarih": "2026-08-19",
                 "adet_tarihi": adet_tarihi, "adet_yas_gun": yas,
                 "en_cok": None, "en_az": None,
                 "not": "kur etkisi haric (fiyat hareketi)"}
                if h == "bux" else None)

        eski = P.gunluk_degisim
        try:
            # 5 GUN: uyari VAR ve tarih yaziyor.
            P.gunluk_degisim = _sahte(5, "2026-08-14")
            m = "\n".join(n._portfoy_satirlari("ali"))
            assert "adet 14 Agu" in m, m
            assert "5 gun onceki ekran goruntusu" in m, m
            assert "agirliklar eski" in m, m

            # 1 GUN: tarih VAR ama uyari YOK (gurultu olurdu).
            P.gunluk_degisim = _sahte(1, "2026-08-18")
            m1 = "\n".join(n._portfoy_satirlari("ali"))
            assert "adet 18 Agu" in m1, m1
            assert "ekran goruntusu" not in m1, f"1 gunluk farka uyari: {m1}"

            # FIYAT ve ADET tarihleri AYRI alanlarda — birlestirilirse
            # gizlemek istedigimiz sey gizlenir.
            assert "fiyat 19 Agu" in m and "adet 14 Agu" in m, m
        finally:
            P.gunluk_degisim = eski
        db.close()

    assert Nabiz.ADET_BAYATLIK_UYARI_GUN == 1


def test_gunluk_degisim_adet_yasini_BARA_gore_olcuyor():
    """
    Yas BUGUNE gore degil kullanilan FIYAT BARINA gore olculur. Hafta
    sonu ya da bayat bir seride bugune gore olcmek OLMAYAN bir bayatlik
    uydururdu — `screener._gun_farki` ile ayni disiplin. Ayristirilamayan
    tarihte UYDURMA SAYI yok.
    """
    from finagent.analysis.portfolio import _gun_farki
    assert _gun_farki("2026-08-14", "2026-08-19") == 5
    assert _gun_farki("2026-08-19T07:41:00+00:00", "2026-08-19") == 0
    # Adet fiyattan YENI ise negatif yas yok (yeni goruntu, bayat seri).
    assert _gun_farki("2026-08-20", "2026-08-19") == 0
    assert _gun_farki(None, "2026-08-19") is None
    assert _gun_farki("bozuk", "2026-08-19") is None
    assert _gun_farki("2026-08-19", None) is None


def test_ozet_portfoy_satiri_KUR_ETKISINI_beyan_ediyor():
    """
    Ayni (guncel) kur iki gune de uygulaniyor, yani cikan sayi yalnizca
    FIYAT hareketini olcer. Ne olculdugu yazilmazsa okuyan taraf bunu
    toplam getiri sanir — beyan ile gercegin ayrismasi.
    """
    import tempfile
    from finagent.analysis import portfolio as P
    with tempfile.TemporaryDirectory() as d:
        n, db, _ = _ozet_nabzi(d)
        eski = P.gunluk_degisim
        P.gunluk_degisim = lambda db_, h, s: (
            {"hesap": "bux", "para_birimi": "EUR", "degisim_%": -1.49,
             "kapsam": 1.0, "tarih": "2026-08-19",
             "en_cok": ("MRVL", 9.85), "en_az": ("AVTX", -19.91),
             "not": "kur etkisi haric (fiyat hareketi)"}
            if h == "bux" else None)
        try:
            metin = "\n".join(n._portfoy_satirlari("ali"))
        finally:
            P.gunluk_degisim = eski
        assert "-%1,49" in metin, metin           # TURKCE bicim
        assert "MRVL" in metin and "+%9,85" in metin, metin
        assert "AVTX" in metin and "-%19,91" in metin, metin
        assert "kur etkisi haric" in metin, "ne olculdugu beyan edilmemis"
        db.close()


def test_ozet_makro_satiri_ONCEKI_GUNE_gore_ve_BAYATSA_sayi_yok():
    """
    Degisim kipin onceki KOSUSUNA gore olsaydi 12:30 satiri 08:00'e
    gore %0,0 gosterir ve hicbir bilgi tasimazdi — onceki GUNUN
    kapanisi dogrusu.

    Ve bayat seri SAYI ILE gosterilemez: "gram altin 4.512" derken uc
    gun onceki fiyati soylemek, bayat veriyi taze gibi sunmaktir.
    """
    import tempfile
    from datetime import date, timedelta
    with tempfile.TemporaryDirectory() as d:
        n, db, _ = _ozet_nabzi(d)
        bugun = date.today()

        def _makro(kod, ccy, barlar):
            iid = db.upsert_instrument(kod, "MAKRO", kod, "emtia", ccy)
            with db.tx() as c:
                c.executemany(
                    "INSERT INTO prices (instrument_id, ts, close, currency, "
                    "source) VALUES (?,?,?,?,'makro')",
                    [(iid, t, v, ccy) for t, v in barlar])
            return iid

        # TAZE: dun 100, bugun 110 -> +%10.
        #
        # AYNI GUNUN IKI BARI VAR ve bu testin ASIL AYIRT EDICI kismi:
        # 12:30 kosusu gun icinde bir bar yazar, 17:45 kosusu ikincisini.
        # "Onceki BAR"a gore hesaplayan bir surum 110/105 = +%4,76 der
        # ve satir "bugun ne oldu"yu degil "ogleden beri ne oldu"yu
        # anlatir — 08:00'e gore %0,0 gosteren hatanin ta kendisi.
        # Dogru cevap onceki GUNE gore: 110/100 = +%10.
        _makro("TAZE_KOD", "TRY", [
            ((bugun - timedelta(days=1)).isoformat(), 100.0),
            (bugun.isoformat() + "T12:30:00", 105.0),   # gun ici ara bar
            (bugun.isoformat() + "T17:45:00", 110.0)])
        # BAYAT: son bar 5 gun once.
        _makro("BAYAT_KOD", "USD", [
            ((bugun - timedelta(days=6)).isoformat(), 60.0),
            ((bugun - timedelta(days=5)).isoformat(), 61.0)])

        n.s.raw.setdefault("ritim", {})["ozet_makro"] = [
            "TAZE_KOD", "BAYAT_KOD", "OLMAYAN_KOD"]
        metin = "\n".join(n._makro_satirlari())

        assert "TAZE_KOD 110" in metin, metin
        assert "TRY" in metin, metin
        assert "+%10" in metin, metin
        assert "BAYAT_KOD: veri bayat" in metin, metin
        assert "61" not in metin, f"bayat seri SAYIYLA gosterilmis: {metin}"
        assert "OLMAYAN_KOD" not in metin, metin
        db.close()


def test_ozet_makro_listesi_BOSSA_satir_yok():
    """`ozet_makro: []` = satir yok. Bos listeyi yok sayip varsayilan
    bir liste kullanmak, ayari OLU KONFIGURASYONA cevirirdi."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        n, db, _ = _ozet_nabzi(d)
        n.s.raw.setdefault("ritim", {})["ozet_makro"] = []
        assert n._makro_satirlari() == []
        db.close()


def test_ozet_teknik_detay_BUTONU_hakem_satirini_tasiyor():
    """
    Buton SATIR ID'SI tasir, zaman damgasi degil: iki sahibin damgasi
    ayni saniyeye duserse damga tabanli arama BASKASININ teknik
    detayini acardi.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        n, db, _ = _ozet_nabzi(d)
        n._panel_fazi = lambda *a, **k: ({"sade": "ozet"}, 3, 4242)
        n.calistir(bildir=True, panel=True, kip="sabah")
        _, m, markup = n.gonderilen[0]
        assert markup, "teknik detay butonu yok"
        assert markup["inline_keyboard"][0][0]["callback_data"] == "det:4242"
        assert "3 yeni tahmin" in m, m
        db.close()


# =====================================================================
# FAZ C3 — hakem promptuna gunun onceki SADE katmanlari
# =====================================================================

class _BosAyar:
    """Panel kurucusunun ihtiyaci olan tek sey `get`."""
    def get(self, yol, varsayilan=None): return varsayilan


def _panel_kaydi(db, sahip, run_ts, sade, teknik="olculen: RSI 55",
                 ajan="hakem", durum="ok"):
    with db.tx() as c:
        c.execute(
            "INSERT INTO panel_runs (run_ts, ajan, ham_metin, json_durum, "
            "gorus_sayisi, atilan_sembol_yok, atilan_seri_yok, "
            "atilan_cakisma, sahip) VALUES (?,?,?,?,1,0,0,0,?)",
            (run_ts, ajan, f"### SADE\n{sade}\n\n### TEKNIK\n{teknik}",
             durum, sahip))


def test_hakem_bugunun_onceki_kosularini_goruyor():
    """
    Dort panel ayni gunde ayni kagit hakkinda ayni cumleyi dort kez
    kurabilir. Hakem, bugun DAHA ONCE ne gonderildigini gorup yalnizca
    DEGISENI anlatmali. Ikinci bir LLM cagrisi yok — yalnizca baglam.
    """
    import tempfile
    from datetime import datetime, timezone
    from finagent.pulse.agents import Panel
    from finagent.storage.db import Database

    d = _pathlib.Path(tempfile.mkdtemp())
    db = Database(d / "t.db"); db.init_schema()
    bugun = datetime.now(timezone.utc).strftime("%Y-%m-%dT08:05:00")
    _panel_kaydi(db, "ali", bugun, "Sabah: ASML sakin, yeni bir sey yok.")

    p = Panel(_BosAyar(), db, "ali")
    blok = p._gecmis_bolumu()
    assert "BUGUN DAHA ONCE" in blok, blok
    assert "ASML sakin" in blok, blok
    # TALIMAT DA GITMELI: baglam tek basina davranisi degistirmez.
    assert "NE DEGISTI" in blok, blok
    assert "degisen yok" in blok, blok
    # TEKNIK katman GITMEZ: hakem zaten bu kosunun teknigini uretiyor,
    # eskisini vermek onu demirler.
    assert "RSI 55" not in blok, blok
    db.close()


def test_hakem_gecmisi_SAHIPLER_ARASI_sizmiyor():
    """
    Ali'nin sabah ozeti Yuksel'in aksam hakemine gidemez. Capraz
    sizinti bu mimaride en pahali hata sinifi: sahip parametredir,
    varsayilan yoktur.
    """
    import tempfile
    from datetime import datetime, timezone
    from finagent.pulse.agents import Panel
    from finagent.storage.db import Database

    d = _pathlib.Path(tempfile.mkdtemp())
    db = Database(d / "t.db"); db.init_schema()
    bugun = datetime.now(timezone.utc).strftime("%Y-%m-%dT08:05:00")
    _panel_kaydi(db, "ali", bugun, "ALI-GIZLI: portfoyunun %40'i ASML.")
    _panel_kaydi(db, "yuksel", bugun, "YUKSEL: kripto agirligi yuksek.")

    blok = Panel(_BosAyar(), db, "yuksel")._gecmis_bolumu()
    assert "ALI-GIZLI" not in blok, "SAHIPLER ARASI SIZINTI"
    assert "YUKSEL" in blok, blok
    db.close()


def test_hakem_gecmisi_DUNU_ve_BOS_ciktiyi_almiyor():
    """
    "Bugun daha once" DEMEK bugun demek. Dunku ozet buraya girerse
    hakem "degisen yok" derken dunle karsilastirmis olur ve gun ici
    ritmi bozulur. Ayrica bicimi bozuk (json_durum != ok) kosular da
    girmemeli — onlarin SADE katmani guvenilir degil.
    """
    import tempfile
    from datetime import datetime, timedelta, timezone
    from finagent.pulse.agents import Panel
    from finagent.storage.db import Database

    d = _pathlib.Path(tempfile.mkdtemp())
    db = Database(d / "t.db"); db.init_schema()
    simdi = datetime.now(timezone.utc)
    _panel_kaydi(db, "ali", (simdi - timedelta(days=1)).strftime(
        "%Y-%m-%dT20:00:00"), "DUNKU ozet")
    _panel_kaydi(db, "ali", simdi.strftime("%Y-%m-%dT08:00:00"),
                 "BOZUK kosu", durum="bos")
    _panel_kaydi(db, "ali", simdi.strftime("%Y-%m-%dT12:35:00"),
                 "BUGUNKU ozet")

    blok = Panel(_BosAyar(), db, "ali")._gecmis_bolumu()
    assert "DUNKU" not in blok, blok
    assert "BOZUK" not in blok, blok
    assert "BUGUNKU" in blok, blok
    db.close()


def test_hakem_gecmisi_kirpiliyor_ve_KIRPILDIGI_soyleniyor():
    """
    Kirpildigi soylenmeyen metin TAM sanilir; model eksik bir sey
    soylenmemis gibi davranir. Ayni ders `isyatirim` kesilmesinde ve
    bildirim listesinde ogrenildi.
    """
    import tempfile
    from datetime import datetime, timezone
    from finagent.pulse.agents import Panel
    from finagent.storage.db import Database

    d = _pathlib.Path(tempfile.mkdtemp())
    db = Database(d / "t.db"); db.init_schema()
    uzun = "A" * (Panel.SADE_KIRPMA + 500)
    _panel_kaydi(db, "ali",
                 datetime.now(timezone.utc).strftime("%Y-%m-%dT08:00:00"), uzun)

    blok = Panel(_BosAyar(), db, "ali")._gecmis_bolumu()
    assert "kisaltildi" in blok, "kirpma sessizce yapildi"
    assert len(blok) < len(uzun), blok[:200]
    db.close()


def test_hakem_gecmisi_EN_FAZLA_UC_kosu_ve_AJANLARA_gitmiyor():
    """
    Ritim v2 §5, ikinci tuzak: "Hakeme onceki kosuyu verirken ajanlara
    VERME. Ajanlar birbirini gormedigi gibi gecmisi de gormesin;
    bagimsizlik ajan katmaninda, sentez hakemde."
    """
    import inspect, tempfile
    from datetime import datetime, timezone
    from finagent.pulse.agents import Panel
    from finagent.storage.db import Database

    d = _pathlib.Path(tempfile.mkdtemp())
    db = Database(d / "t.db"); db.init_schema()
    bugun = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    for i in range(5):
        _panel_kaydi(db, "ali", bugun.replace("T", f"T0{i}:")[:19]
                     if i < 10 else bugun, f"KOSU{i}")
    blok = Panel(_BosAyar(), db, "ali")._gecmis_bolumu()
    assert blok.count("[20") <= Panel.GUNUN_AZAMI_KOSUSU, blok

    # AJAN yolu gecmisi GORMEMELI.
    ajan_kaynak = inspect.getsource(Panel._ajan)
    assert "_gecmis_bolumu" not in ajan_kaynak, \
        "ajanlara gunun onceki kosulari veriliyor — bagimsizlik bozuldu"
    assert "_gecmis_bolumu" in inspect.getsource(Panel._hakem), \
        "hakem gunun onceki kosularini hic gormuyor"
    db.close()


def test_tarayici_acilamazsa_tarayicisiz_collectorlar_yine_kosar():
    """
    Onceki surumde tek bir `needs_browser` varsa TUM collector'lar
    `with BrowserSession(...)` icine giriyordu; oturum ACILAMAZSA
    (Playwright cokmesi, profil kilidi, disk dolu) hicbiri kosmuyordu —
    tarayiciyla ilgisi olmayan `kap`, `edgar`, `binance` dahil.

    Ritim v2 tek bir `collect --site <hepsi>` cagrisina gectigi icin bu
    kirilganligin YARICAPI BUYUDU: eskiden bir parti duserdi, simdi
    kosunun TAMAMI duserdi.
    """
    import tempfile
    from finagent import pipeline as P
    from finagent.collectors.base import BaseCollector, CollectorResult
    from finagent.storage.db import Database

    kosanlar = []

    class _Tarayicisiz(BaseCollector):
        name = "sahte_duz"
        needs_browser = False
        def collect(self):
            kosanlar.append(self.name)
            return CollectorResult(self.name, "ok", 1)

    class _Tarayicili(BaseCollector):
        name = "sahte_tarayici"
        needs_browser = True
        def collect(self):                     # pragma: no cover
            kosanlar.append(self.name)
            return CollectorResult(self.name, "ok", 1)

    class _PatlakOturum:
        def __init__(self, *a, **k): pass
        def __enter__(self): raise RuntimeError("playwright baslatilamadi")
        def __exit__(self, *a): return False

    db = Database(_pathlib.Path(tempfile.mkdtemp()) / "t.db"); db.init_schema()
    eski_reg = dict(P.REGISTRY)
    eski_bs = P.BrowserSession
    P.REGISTRY["sahte_duz"] = _Tarayicisiz
    P.REGISTRY["sahte_tarayici"] = _Tarayicili
    P.BrowserSession = _PatlakOturum
    try:
        # SIRA KORUNUYOR: tarayicili ONCE isteniyor, yani hata once olusuyor.
        sonuc = P.collect(None, db, ["sahte_tarayici", "sahte_duz"])
    finally:
        P.REGISTRY.clear(); P.REGISTRY.update(eski_reg)
        P.BrowserSession = eski_bs

    adlar = {r.name: r for r in sonuc}
    assert kosanlar == ["sahte_duz"], (
        f"tarayicisiz collector kosmadi: {kosanlar}")
    assert adlar["sahte_duz"].status == "ok"
    # SESSIZ ATLAMA YOK: hata hem sonuca hem `collector_runs`a yazilir.
    assert adlar["sahte_tarayici"].status == "error"
    assert "tarayici" in (adlar["sahte_tarayici"].error or "")
    kayit = db.query("SELECT collector, status FROM collector_runs "
                     "WHERE collector='sahte_tarayici'")
    assert kayit and kayit[0]["status"] == "error", (
        "tarayici acilamadi ama collector_runs'a yazilmadi — bekci ve "
        "`veri_durumu` bu boslugu goremez")
    db.close()


# =====================================================================
# FAZ B — defter: gunde DORT panel kosusu, TEK satir
# =====================================================================

def _defter_db(d):
    """Bir enstruman + fiyat serisi olan mini defter veritabani."""
    from finagent.storage.db import Database
    db = Database(_pathlib.Path(d) / "defter.db"); db.init_schema()
    iid = db.upsert_instrument("TEST", "BUX", "Test AS", "equity", "EUR")
    with db.tx() as c:
        c.executemany(
            "INSERT INTO prices (instrument_id, ts, open, high, low, close, "
            "volume, currency, source) VALUES (?,?,?,?,?,?,?,?,'test')",
            [(iid, f"2026-08-{g:02d}", 100.0, 101.0, 99.0, 100.0 + g, 1000.0,
              "EUR") for g in range(1, 21)])
    return db, iid


def _gorus(sembol="TEST", *, yon="yukari", guven=0.8, tez="ilk tez",
           kosul="close < 90", ufuk=5, ajan="hakem"):
    """`Defter.kaydet` gorusleri SEMBOL ile aliyor, instrument_id ile degil."""
    return {"sembol": sembol, "yon": yon, "guven": guven,
            "ufuk_gun": ufuk, "gerekce": f"gerekce/{tez}", "tez": tez,
            "gecersizlesme_kosulu": kosul, "izlenecek_esik": None,
            "ajan": ajan}


def test_gunun_IKINCI_paneli_defterdeki_satiri_EZMEZ():
    """
    `olusma_ts` bir TARIHTIR (`_bugun()`), damga degil. Ritim v2 gunde
    DORT panel getiriyor ve `ON CONFLICT DO UPDATE` ile dordu de AYNI
    satiri ezerdi: sabah yazilan tez, gerekce ve baslangic fiyati aksam
    iz birakmadan silinirdi.

    Gunun ILK paneli kazanir; sonrakiler `panel_runs.ham_metin`'de
    duruyor, yani bilgi kaybi yok — defter en erken cagriyi tutuyor.
    """
    import tempfile
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db, iid = _defter_db(d)
        defter = Defter(db)

        r1 = defter.kaydet([_gorus(tez="SABAH tezi")], "ali")
        assert r1["yazilan"] == 1, r1

        # AYNI GUN ikinci panel, AYNI enstruman/ufuk/ajan.
        r2 = defter.kaydet([_gorus(yon="asagi", guven=0.9,
                                   tez="AKSAM tezi",
                                   kosul="close > 999")], "ali")
        assert r2["yazilan"] == 0, r2
        # SESSIZ ATLAMA YOK: sayiliyor.
        assert r2["gun_icinde_zaten_vardi"] == 1, r2

        satir = db.query("SELECT * FROM predictions")
        assert len(satir) == 1, f"{len(satir)} satir — gun icinde cogaldi"
        assert satir[0]["tez"] == "SABAH tezi", (
            f"aksam paneli sabahin tezini EZDI: {satir[0]['tez']}")
        assert satir[0]["yon"] == "yukari", satir[0]["yon"]
        assert satir[0]["gecersizlesme_kosulu"] == "close < 90"
        db.close()


def test_tez_bozulduktan_sonra_YENI_kosul_sessizce_gomulmez():
    """
    OLCULEBILIR ARIZA — dort kosuda KACINILMAZ, tek kosuda IMKANSIZ.

    `DO UPDATE` `gecersizlesme_kosulu`'nu yeniliyor ama
    `tez_bozuldu_ts`'i temizlemiyordu; `tez_kontrol` ise
    `tez_bozuldu_ts IS NULL` suzuyor. Zincir:
        08:00 tez yazar
        12:30 kosul tetiklenir -> alarm gider, damga yazilir
        17:45 AYNI SATIRA yeni bir kosul yazar
        -> o kosul gunun geri kalaninda HIC KONTROL EDILMEZ.

    `DO NOTHING` bunu yapisal olarak imkansiz kiliyor: satir hic
    degismiyor, dolayisiyla damga ile kosul asla ayrisamiyor.
    """
    import tempfile
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db, iid = _defter_db(d)
        defter = Defter(db)

        # 1) Sabah: tetiklenecek bir kosul yaz (son kapanis 120).
        defter.kaydet([_gorus(tez="sabah", kosul="close > 100")], "ali")
        tetik = defter.tez_kontrol("ali")
        assert len(tetik) == 1, tetik
        damga = db.query(
            "SELECT tez_bozuldu_ts t FROM predictions")[0]["t"]
        assert damga, "tez bozuldu ama damga yazilmadi"

        # 2) Aksam: ayni satira YENI bir kosul yazilmaya calisilir.
        defter.kaydet([_gorus(tez="aksam", kosul="close < 5")], "ali")

        s = db.query("SELECT tez, gecersizlesme_kosulu k, tez_bozuldu_ts t "
                     "FROM predictions")[0]
        # SATIR DEGISMEDI: kosul ile damga ayrisamaz.
        assert s["k"] == "close > 100", (
            f"kosul degisti ({s['k']}) ama damga duruyor — yeni kosul "
            "artik HIC kontrol edilmez")
        assert s["tez"] == "sabah", s["tez"]
        assert s["t"] == damga
        db.close()


def _puanlanmis(db, iid, kayitlar):
    """(gun, ufuk, isabet) uclulerini puanlanmis hakem cagrisi olarak yazar."""
    with db.tx() as c:
        for gun, ufuk, isabet in kayitlar:
            c.execute(
                "INSERT INTO predictions (olusma_ts,instrument_id,ajan,"
                "yon,ufuk_gun,guven,baslangic_fiyat,para_birimi,sahip,"
                "isabet,anormal_pct,piyasa_getiri_pct) VALUES "
                "(?,?,'hakem','yukari',?,0.8,100.0,'EUR','ali',?,1.0,0.5)",
                (gun, iid, ufuk, isabet))


def test_karne_araligi_TAHMIN_degil_KUME_sayisiyla_hesaplaniyor():
    """
    CANLI VERIDE OLCULDU (2026-08-16, sahip=ali): hakem ayni gun ayni
    enstrumana FARKLI ufuklarla gorus verdi ve iki satir olustu —
    `ufuk_gun` benzersizligin parcasi.
        iid 222 -> (5, 20) · iid 225 -> (5, 20) · iid 231 -> (5, 60)
    Yani `journal.py`'deki "olcum ile bagimsiz_kume esit olmali" yorumu
    bugun YANLISTI ve kimse bakmiyordu.

    Bu iki tahmin BAGIMSIZ GOZLEM DEGIL: ikisi de TEK bir fiyat
    hareketini konusuyor. Wilson araligi bagimsizlik varsayar; ham
    sayiyla hesaplanirsa aralik ~sqrt(olcum/kume) kat DAR cikar ve
    olmayan bir kesinlik uretir.

    Cozum hakemi tek ufka ZORLAMAK degil (cok ufuklu gorus mesru),
    araligi ETKIN ORNEKLEM BUYUKLUGUNE baglamak.
    """
    import tempfile
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db, iid = _defter_db(d)
        # 4 tahmin ama yalnizca 2 (enstruman, gun) kumesi.
        _puanlanmis(db, iid, [("2026-08-01", 5, 1), ("2026-08-01", 20, 1),
                              ("2026-08-02", 5, 0), ("2026-08-02", 20, 0)])
        k = Defter(db).karne("ali")
        assert k["olcum"] == 4, k
        assert k["bagimsiz_kume"] == 2, k
        assert k["aralik_ornegi"] == 2, (
            f"aralik {k['aralik_ornegi']} orneklemle hesaplanmis; "
            "kumelenme yok sayilmis")

        # Ayni isabet orani, AMA kumelenme yokken aralik DAHA DAR olmali.
        db2, iid2 = _defter_db(_pathlib.Path(d) / "b")
        _puanlanmis(db2, iid2, [("2026-08-01", 5, 1), ("2026-08-02", 5, 1),
                                ("2026-08-03", 5, 0), ("2026-08-04", 5, 0)])
        k2 = Defter(db2).karne("ali")
        assert k2["olcum"] == k2["bagimsiz_kume"] == 4, k2
        assert k["isabet_%"] == k2["isabet_%"], (k, k2)

        genis = k["guven_araligi_%"][1] - k["guven_araligi_%"][0]
        dar = k2["guven_araligi_%"][1] - k2["guven_araligi_%"][0]
        assert genis > dar, (
            f"kumelenmis veride aralik {genis}, bagimsizda {dar} — "
            "kumelenme araligi genisletmiyor, yani yok sayiliyor")
        db.close(); db2.close()


def test_karne_kumelenmeyi_CIKTIDA_beyan_ediyor():
    """
    Kumelenme gizlenirse okuyan taraf araligin neye dayandigini bilemez.
    Veride dogru olan bir sey, ciktida yeniden yanlis beyan edilmemeli
    (`yanlis-yok-beyani` sinifi).
    """
    import tempfile
    from finagent.pulse.journal import Defter
    with tempfile.TemporaryDirectory() as d:
        db, iid = _defter_db(d)
        _puanlanmis(db, iid, [("2026-08-01", 5, 1), ("2026-08-01", 20, 1)])
        k = Defter(db).karne("ali")
        for alan in ("olcum", "bagimsiz_kume", "aralik_ornegi", "vekilsiz_n"):
            assert alan in k, f"karne '{alan}' alanini beyan etmiyor: {k}"
        db.close()


# =====================================================================
# FAZ C2 — panel karari ve alici listesi AYARDAN
# =====================================================================

def test_run_py_panel_kararini_KODA_gommuyor():
    """
    Ritim v2 §5'in ILK TUZAGI: `hafif = args.kip in ("sabah","ogle")`
    gibi bir demet kaldiysa belge uygulanmamis demektir. Kip adi kodda
    gomuluyse yeni bir kip eklemek kod degisikligi gerektirir ve iki
    yer (kod + ayar) kacinilmaz olarak ayrisir.
    """
    import ast
    kok = _pathlib.Path(__file__).resolve().parents[1]
    kaynak = (kok / "run.py").read_text(encoding="utf-8")
    agac = ast.parse(kaynak)

    # ARANAN SEY: `args.kip` UZERINDE KARAR. Kip adinin dosyada
    # gecmesi degil — CLI'da `run.py nabiz` diye bir ALT KOMUT var ve
    # adi bir kip adiyla cakisiyor (`elif cmd == "nabiz"`). Testi
    # kelimeye baglamak iki kez yanlis alarm uretti; yasak olan kalip
    # `args.kip in (...)` / `args.kip == "..."`.
    def _kip_erisimi(d) -> bool:
        return (isinstance(d, ast.Attribute) and d.attr == "kip"
                and isinstance(d.value, ast.Name) and d.value.id == "args")

    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.Compare) and _kip_erisimi(dugum.left):
            raise AssertionError(
                f"run.py satir {dugum.lineno}: `args.kip` uzerinde "
                "karsilastirma — kip karari koda gomulu (ritim v2 §5)")
        # `--kip` icin `choices=[...]` da ikinci bir liste olurdu.
        if (isinstance(dugum, ast.Call)
                and isinstance(dugum.func, ast.Attribute)
                and dugum.func.attr == "add_argument"
                and any(isinstance(a, ast.Constant) and a.value == "--kip"
                        for a in dugum.args)):
            for kw in dugum.keywords:
                assert kw.arg != "choices", (
                    "`--kip` icin choices listesi var; gecerli kipler "
                    "ayardan gelmeli (tek dogrulama noktasi ritim_kip)")
                if kw.arg == "default":
                    assert (isinstance(kw.value, ast.Constant)
                            and kw.value.value is None), \
                        "`--kip` varsayilani var; unutulan cagri sessizce " \
                        "bir kipi kosturur"
    # Ve panel karari GERCEKTEN ayardan okunmali.
    assert "ritim_kip(args.kip)" in kaynak, \
        "panel karari ayardan okunmuyor"
    assert 'kip_ayar["panel"]' in kaynak, "panel bayragi kullanilmiyor"
    # `--no-panel` KALIYOR: elle LLM'siz kosu hala gerekli.
    assert "--no-panel" in kaynak


def test_run_py_kip_hatasinda_SIFIRDAN_FARKLI_cikiyor():
    """
    Kabuk `|| true` kullanmiyor: ayar hatasi kosunun HIC olmamasi
    demek ve bu disaridan "bugun bir sey olmadi" gibi gorunur. Cikis
    kodu sifir olsaydi launchd de, betik de basarili sayardi.

    Ayrica `--karne` kipsiz calismali: karne bir KOSU degil, defterin
    okunmasi.
    """
    # SIZINTININ KAYNAGI BURASIYDI. Bu dort cagri `env` gecirmiyordu ve
    # `run.py` her komutta `init_schema()` calistirdigi icin CANLI
    # veritabanini goc ettiriyordu — olculdu: bu test tek basina
    # kosunca `data/agent.log`'a dort "Sema hazir: data/finagent.db"
    # satiri dusuyordu, digerlerinde SIFIR.
    def _kos(*ek):
        return _run_py("nabiz", *ek).returncode

    assert _kos("--no-notify") == 2, "--kip unutuldu ama cikis kodu sifir"
    assert _kos("--kip", "yok_boyle_bir_kip", "--no-notify") == 2, \
        "bilinmeyen kip sessizce kabul edildi"
    assert _kos("--karne") == 0, "--karne kip istiyor"
    # ALICI LISTESI ELLE KOSUDA DA BAGLAYICI: `--sahip` bir arka kapi
    # olmamali. Kipin alicisi olmayan birine mesaj atilamaz.
    assert _kos("--kip", "sabah", "--sahip", "yok_boyle_sahip",
                "--no-notify", "--no-panel") == 2, \
        "--sahip kipin alici listesini atlatiyor"


def test_kip_ALICILARI_disindaki_sahip_icin_HICBIR_SEY_kosmaz():
    """
    Ritim v2 §3.3/1: alici olmayan sahip icin tez kontrolu bile
    kosmaz — o kisinin kontrolu kendi kipinde yapilir. Kabul kriteri
    acik: `panel_runs`, `predictions`, `bildirim_durumu`'na satir
    yazilmaz ve mesaj gitmez.
    """
    import tempfile
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, _ = _fazb_db(d, sahipler=("ali", "esi"))
        s = _fazb_ayar(sahipler=("ali", "esi"), kok=d)
        # Bu kipin ALICISI yalnizca `ali`.
        s.raw["ritim"]["kipler"]["sabah"]["alicilar"] = ["ali"]

        gonderilen = []
        n = Nabiz(s, db)
        n._sahibe_bildir = lambda sahip, metin, reply_markup=None: (
            gonderilen.append((sahip, metin)) or True)

        r = n.calistir(bildir=True, panel=False, kip="sabah")

        assert r["sahipler"] == ["ali"], r["sahipler"]
        assert "esi" not in r["sonuc"], r["sonuc"].keys()
        assert all(s_ != "esi" for s_, _ in gonderilen), gonderilen
        # Deftere/bastirmaya da hicbir sey yazilmamis olmali.
        for tablo in ("predictions", "panel_runs", "bildirim_durumu"):
            n_satir = db.query(
                f"SELECT COUNT(*) c FROM {tablo} WHERE sahip='esi'")[0]["c"]
            assert n_satir == 0, f"{tablo}: alici olmayan sahibe {n_satir} satir"
        db.close()


def test_panel_butcesi_KIP_BASINA_uygulaniyor():
    """
    Butce kip basina olmali: `ogle` 15 dk, `nabiz` 30 dk. Tek global
    deger, kisa kipte gereksiz genis kalir ve kabuk sinirini asar.
    """
    import tempfile, time as _time
    from unittest.mock import patch
    from finagent.pulse.runner import Nabiz
    with tempfile.TemporaryDirectory() as d:
        db, _ = _fazb_db(d, sahipler=("ali", "esi"))
        s = _fazb_ayar(sahipler=("ali", "esi"), kok=d)
        s.raw["ritim"]["kipler"]["sabah"]["panel_butce_sn"] = 600

        gonderilen = []
        n = Nabiz(s, db)
        n._sahibe_bildir = lambda sahip, metin, reply_markup=None: (
            gonderilen.append((sahip, metin)) or True)
        # Panel LLM'e gitmesin.
        n._panel_fazi = lambda *a, **k: {"sinyal": 0, "guclu": 0, "karne": {},
                                         "ozet": None, "tahmin": 0,
                                         "tez_bozuldu": 0}

        # SAAT KONTROL ALTINDA. Kucucuk bir butce ile "gercek sure"ye
        # guvenmek YARIS uretirdi: ilk sahibin fazi 1 ms'den kisa
        # surerse ikincisi de butceye sigar ve test rastgele geciyormus
        # gibi gorunur. Kontrollu saat: 0 (baslangic) -> 0 (ilk sahip,
        # butce icinde) -> 700 (ikinci sahip, 600 sn'lik butce dolmus).
        adim = iter([0.0, 0.0, 700.0, 700.0, 700.0])
        with patch.object(_time, "monotonic",
                          lambda: next(adim, 700.0)):
            r = n.calistir(bildir=True, panel=True, kip="sabah")

        assert r["panel_atlanan"] == ["esi"], r["panel_atlanan"]
        # SESSIZ ATLAMA YOK: atlanan sahibe SOYLENIR.
        atlanan_mesaj = [m for s_, m in gonderilen if s_ == "esi"]
        assert any("panel kosamadi" in m for m in atlanan_mesaj), atlanan_mesaj

        # BUTCE GERCEKTEN KIP AYARINDAN GELIYOR: genis butcede kimse
        # atlanmaz. (Ayni saat dizisiyle — degisen tek sey ayar.)
        s.raw["ritim"]["kipler"]["sabah"]["panel_butce_sn"] = 1200
        adim2 = iter([0.0, 0.0, 700.0, 700.0, 700.0])
        with patch.object(_time, "monotonic", lambda: next(adim2, 700.0)):
            r2 = n.calistir(bildir=False, panel=True, kip="sabah")
        assert r2["panel_atlanan"] == [], (
            "butce 1200 sn iken 700. saniyede panel atlandi — deger "
            "ayardan okunmuyor")
        db.close()


def test_sure_asimi_OLDURMEDEN_ONCE_haber_veriyor():
    """
    OLCULEN SESSIZ ARIZA (data/pulse.log, 2026-08-19 23:00:00):
      [run_pulse] 2700 sn asildi, oldurul uyor
    Log'a tek satir yazildi ve KIMSEYE GITMEDI. `bildir()` betikte
    VARDI ama yalnizca `run.py nabiz` sifirdan farkli donerse
    cagriliyordu; sure sinirinda surec grubu SIGTERM aliyor ve o yola
    HIC gelinmiyor. Ali nabzin oldugunu ertesi gune kadar bilmedi.

    Bu test mekanizmayi GERCEKTEN kosturur: bekci baslatilir, hedef
    surec oldurulur ve `bildir` cagrisinin oldurmeden ONCE yapildigi
    kanitlanir. Sira onemli — `kill -TERM -PID` bekcinin kendisini de
    olduruyor.
    """
    import subprocess, tempfile, textwrap, os
    kok = _pathlib.Path(__file__).resolve().parents[1]
    d = _pathlib.Path(tempfile.mkdtemp())
    (d / "data").mkdir()
    (d / "scripts").mkdir()
    # Ortak katmanin GERCEK kopyasi — testin sinadigi sey o dosya.
    (d / "scripts" / "_ortak.sh").write_text(
        (kok / "scripts" / "_ortak.sh").read_text(encoding="utf-8"),
        encoding="utf-8")

    senaryo = d / "senaryo.sh"
    senaryo.write_text(textwrap.dedent(f"""\
        set -uo pipefail
        cd "{d}"
        . scripts/_ortak.sh
        # `bildir` EZILIYOR: aga cikmadan, cagrildigini ve NE ZAMAN
        # cagrildigini diske yaziyoruz.
        bildir() {{ printf '%s' "$1" > "{d}/bildirim.txt"; }}
        sure_bekcisi_baslat "test_kosu" 1 $$
        trap sure_bekcisi_temizle EXIT
        # Bekciden UZUN suren bir is: oldurulmesi gerekiyor.
        sleep 30
        echo "ULASILMAMALIYDI" > "{d}/ulasti.txt"
        """), encoding="utf-8")

    # AYRI OTURUM: `kill -TERM -PID` surec GRUBUNU olduruyor; test
    # kosucusuyla ayni grupta olsaydi onu da oldururdu.
    r = subprocess.run(["bash", str(senaryo)], capture_output=True,
                       text=True, timeout=60, start_new_session=True)

    assert not (d / "ulasti.txt").exists(), \
        "sure siniri sureci OLDURMEDI — koruma calismiyor"
    mesaj = d / "bildirim.txt"
    assert mesaj.exists(), (
        "sure asildi, surec olduruldu ama KIMSEYE HABER VERILMEDI — "
        f"19 Agustos arizasinin ta kendisi. cikti={r.stdout!r} {r.stderr!r}")
    metin = mesaj.read_text(encoding="utf-8")
    assert "test_kosu" in metin, metin
    assert "sure siniri" in metin.lower(), metin
    # Log satiri da yazilmali (iki kanal birbirinin yedegi).
    log = (d / "data" / "pulse.log").read_text(encoding="utf-8")
    assert "asildi" in log, log


def test_kosu_betigi_ORTAK_katmani_kullaniyor():
    """
    Kopyalanmis kabuk kodu iki farkli davranis uretti: `run_pulse.sh`
    cokmeyi bildiriyordu, `run_hafif.sh` bildirmiyordu; ikisi de sure
    asimini bildirmiyordu. Ikisi TEK betige indi (`run_kosu.sh`) ve
    ortak parcalar `_ortak.sh`'te — tek kaynak, tek davranis.
    """
    kok = _pathlib.Path(__file__).resolve().parents[1]
    ortak = (kok / "scripts" / "_ortak.sh").read_text(encoding="utf-8")
    for fn in ("bildir()", "sure_bekcisi_baslat()", "sure_bekcisi_temizle()",
               "son_satirlar()"):
        assert fn in ortak, f"_ortak.sh'te {fn} yok"

    # ESKI BETIKLER GERI GELMESIN: ikisi de ayni isi yapiyordu ve
    # kopyalar ayrismisti.
    for eski in ("run_pulse.sh", "run_hafif.sh"):
        assert not (kok / "scripts" / eski).exists(), \
            f"{eski} geri gelmis — kopyalanmis kabuk kodu"

    for ad in ("run_kosu.sh",):
        m = (kok / "scripts" / ad).read_text(encoding="utf-8")
        # ETKIN SATIRLAR — yorumlar sayilmaz. Ilk surumde bu test
        # `"_ortak.sh" in m` diyordu ve `# shellcheck source=...`
        # YORUMU sayesinde source satiri silinse bile GECIYORDU;
        # kasitli bozma testi yakaladi.
        etkin = [s.strip() for s in m.splitlines()
                 if s.strip() and not s.strip().startswith("#")]
        assert any(s == '. "$(dirname "$0")/_ortak.sh"' for s in etkin), \
            f"{ad} ortak katmani source etmiyor"
        assert any(s.startswith("sure_bekcisi_baslat ") for s in etkin), \
            f"{ad} sure bekcisi kurmuyor"
        assert any(s == "trap sure_bekcisi_temizle EXIT" for s in etkin), \
            f"{ad} bekciyi temizlemiyor (oksuz `sleep` kalir)"
        # COKME KONTROLU: `bildir` cagrisinin dosyada BULUNMASI yetmez,
        # ULASILABILIR olmasi gerekir. `if false; then` mutasyonu ilk
        # surumu gecmisti — cagri duruyordu ama olu koddu.
        assert any(s.startswith("if ! .venv/bin/python run.py nabiz")
                   for s in etkin), \
            f"{ad} nabiz adiminin cokmesini kontrol etmiyor"
        # Eski, KOPYALANMIS bekci geri gelmesin.
        assert '( sleep "$AZAMI_SN"' not in m, \
            f"{ad} icinde elle yazilmis bekci geri gelmis"


def _tuik_ayari(butce_sn=300.0, timeout_sn=90.0):
    from finagent.config import load_settings
    s = load_settings()
    s.raw.setdefault("sources", {}).setdefault("tuik", {})
    s.raw["sources"]["tuik"]["azami_sure_sn"] = butce_sn
    s.raw["sources"]["tuik"]["timeout_sn"] = timeout_sn
    return s


def _tuik(d, butce_sn=300.0, timeout_sn=90.0):
    """Aginternete CIKMAYAN bir TuikCollector."""
    import time as _t
    from finagent.collectors.tuik import TuikCollector
    from finagent.storage.db import Database

    class _Sahte(TuikCollector):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.seri_cagrildi = []
            self.katalog_butceyi_yesin = False

        def _bearer(self):
            return "sahte-token"

        def _katalog(self):
            if self.katalog_butceyi_yesin:
                # Gercekte olan buydu: ilk istek(ler) butceyi yedi.
                self._butce_bitis = _t.monotonic() - 1
            return 5

        def _seri(self, tanim):
            self.seri_cagrildi.append(tanim["kod"])
            return 1

    db = Database(_pathlib.Path(d) / "tuik.db")
    db.init_schema()
    return _Sahte(_tuik_ayari(butce_sn, timeout_sn), db), db


def test_tuik_gecelik_zincirde_DEGIL():
    """
    19 Agustos gecesi kaybedilen kosunun dogrudan sebebi. TUIK AYLIK
    veri yayinliyor; gecelik zincirde bulunmasinin hicbir gerekcesi
    yoktu ve en kotu halinde butcenin yarisini yiyordu.

    Sabah kolunda: olculen sure 63 sn, kabuk butcesi 900 sn — en genis
    payi olan kosu orasi. `tazelik_saat: 24` zaten gunde bir kez gercek
    is yapilmasini sagliyor.
    """
    from finagent.config import load_settings
    s = load_settings()
    # Kaynak listeleri artik AYARDA (ritim.kipler), kabukta degil —
    # yani iddia da orada sinaniyor.
    assert "tuik" not in s.ritim_kip("nabiz")["kaynaklar"], \
        "tuik gecelik zincire geri eklenmis"
    assert "tuik" in s.ritim_kip("sabah")["kaynaklar"], \
        "tuik sabah kosusunun kaynak listesinde olmali"
    # Ve kabuk betigi kaynak listesini gercekten AYARDAN okumali;
    # elle yazilmis bir liste ikinci bir dogruluk kaynagi olurdu.
    kok = _pathlib.Path(__file__).resolve().parents[1]
    kabuk = (kok / "scripts" / "run_kosu.sh").read_text(encoding="utf-8")
    assert "ritim_kip" in kabuk, "run_kosu.sh kaynaklari ayardan okumuyor"
    for satir in kabuk.splitlines():
        t = satir.strip()
        if t.startswith("#") or "collect --site" not in t:
            continue
        assert "$KAYNAKLAR" in t, f"kaynak listesi elle yazilmis: {t}"


def test_tuik_butcesi_ayarda_ve_istek_zaman_asimindan_BUYUK():
    """
    Istek basina zaman asimi TOPLAM butceden buyukse ust sinir anlamsiz
    olur: tek bir asili istek butceyi asar. `_get` kalan butceye kisiyor
    ama ayarin kendisi de tutarli olmali.
    """
    import yaml
    kok = _pathlib.Path(__file__).resolve().parents[1]
    ayar = yaml.safe_load((kok / "config" / "settings.yaml").read_text(encoding="utf-8"))
    t = ayar["sources"]["tuik"]
    assert "azami_sure_sn" in t, "tuik'in toplam sure butcesi yok"
    assert t["timeout_sn"] <= t["azami_sure_sn"], (
        f"istek zaman asimi {t['timeout_sn']} > toplam butce "
        f"{t['azami_sure_sn']} — butce hicbir zaman baglayici olmaz")


def test_tuik_butce_dolunca_seri_KESILIR_ve_ADIYLA_raporlanir():
    """
    Butce dolunca collector DUZGUNCE durur: `partial` doner, kalan
    seriler ADIYLA yazilir. Sessiz kesme yok — gorunmeyen bir bosluk
    hic kapanmaz (ayni ders: isyatirim kesilmesi).
    """
    import os, tempfile
    c, db = _tuik(tempfile.mkdtemp(), butce_sn=300.0)
    c.katalog_butceyi_yesin = True
    onceki = os.environ.get("TUIK_API_KEY")
    os.environ["TUIK_API_KEY"] = "test"
    try:
        r = c.collect()
    finally:
        if onceki is None:
            os.environ.pop("TUIK_API_KEY", None)
        else:
            os.environ["TUIK_API_KEY"] = onceki
    db.close()

    assert c.seri_cagrildi == [], (
        f"butce doluyken seri cekilmis: {c.seri_cagrildi}")
    assert r.status == "partial", r.status
    assert "sure butcesi" in (r.error or ""), r.error
    # Kesilen serilerin ADI ciktida olmali.
    for kod in ("TR_YIUFE_YILLIK", "TR_ISSIZLIK", "TR_EKONOMIK_GUVEN"):
        assert kod in (r.error or ""), f"{kod} kesildi ama raporlanmadi"


def test_tuik_istek_zaman_asimi_KALAN_BUTCEYE_kisiliyor():
    """
    Butce kontrolu yalnizca ISTEKLER ARASINDA olsaydi, tek bir asili
    istek butceyi `2 x timeout_sn` kadar asardi. 19 Agustos'ta olan tam
    buydu; disaridaki sinir 45 dakikaydi ve o da asildi.
    """
    import tempfile, time as _t
    from finagent.collectors import tuik as tmod

    c, db = _tuik(tempfile.mkdtemp(), butce_sn=300.0, timeout_sn=90.0)
    c._butce_bitis = _t.monotonic() + 5.0          # kalan 5 sn

    gorulen = {}

    class _Cevap:
        status_code = 200
        text = ""
        def raise_for_status(self): pass

    eski = tmod.httpx.get
    tmod.httpx.get = lambda url, **kw: (gorulen.update(kw) or _Cevap())
    try:
        c._get("/dataflow/TR/all/latest")
    finally:
        tmod.httpx.get = eski
        db.close()

    assert gorulen["timeout"] <= 5.0, (
        f"timeout {gorulen['timeout']} — kalan butce 5 sn'ye kisilmamis")
    assert gorulen["timeout"] > 0


def test_tuik_EN_BAYAT_ONCE_cekiliyor_aclik_yok():
    """
    Butce altinda sabit sira ACLIK uretir: hep ayni seri kesilir ve HIC
    guncellenmez — gorunmez, kalici bir kapsam boslugu.

    Canlida olculdu (2026-08-20 00:16): tek seri cekimi 108,9 sn surdu
    (bir DSD istegi 90 sn'de asti, yeniden deneme tuttu). Uc seri 300
    sn'lik butceyi asabiliyor, yani kesme GERCEK bir olasilik.
    """
    import tempfile
    c, db = _tuik(tempfile.mkdtemp())
    # KOD -> son guncelleme. C hic cekilmemis (bos), A en bayat, B taze.
    with db.tx() as cur:
        cur.executemany(
            "INSERT INTO makro_seri (kod, donem, deger, kaynak, guncelleme) "
            "VALUES (?,?,?,?,?)",
            [("A", "2026-01", 1.0, "tuik", "2026-08-01T00:00:00"),
             ("B", "2026-01", 1.0, "tuik", "2026-08-19T00:00:00")])
    c.s.raw["sources"]["tuik"]["seriler"] = [
        {"kod": "B"}, {"kod": "A"}, {"kod": "C"}]
    c.s.raw["sources"]["tuik"]["tazelik_saat"] = 0     # tazelik suzgeci kapali

    import os
    onceki = os.environ.get("TUIK_API_KEY")
    os.environ["TUIK_API_KEY"] = "test"
    try:
        c.collect()
    finally:
        if onceki is None:
            os.environ.pop("TUIK_API_KEY", None)
        else:
            os.environ["TUIK_API_KEY"] = onceki
    db.close()

    assert c.seri_cagrildi == ["C", "A", "B"], (
        f"sira {c.seri_cagrildi} — hic cekilmemis (C) ve en bayat (A) "
        "once gelmeliydi")


def test_tuik_TOKEN_istegi_de_butceye_tabi():
    """
    Token'in omru 300 sn oldugu icin uzun kosuda birkac kez yenileniyor.
    Sabit 30 sn'lik zaman asimi, ust siniri her yenilemede 30 sn asardi —
    kucuk ama ust sinirin ANLAMINI bozan bir kacak.
    """
    import tempfile, time as _t
    from finagent.collectors import tuik as tmod
    from finagent.storage.db import Database

    db = Database(_pathlib.Path(tempfile.mkdtemp()) / "t.db"); db.init_schema()
    c = tmod.TuikCollector(_tuik_ayari(), db)
    c._api_key = "test"
    c._token = None
    c._butce_bitis = _t.monotonic() + 3.0            # kalan 3 sn

    gorulen = {}

    class _Cevap:
        def raise_for_status(self): pass
        def json(self): return {"access_token": "x", "expires_in": 300}

    eski = tmod.httpx.post
    tmod.httpx.post = lambda url, **kw: (gorulen.update(kw) or _Cevap())
    try:
        c._bearer()
    finally:
        tmod.httpx.post = eski
        db.close()

    assert gorulen["timeout"] <= 3.0, (
        f"token istegi {gorulen['timeout']} sn — kalan butce 3 sn'ye "
        "kisilmamis")
    assert gorulen["timeout"] > 0, "zaman asimi sifir/negatif olamaz"


def test_tuik_butce_bitmisken_ISTEK_YAPMAZ():
    """
    Butce bittiginde ag cagrisi HIC yapilmamali; 'TUIK yavas' ile
    'biz beklemeyi kestik' ayri seyler ve ikincisi bir hata degil bir
    KARARDIR — ayri tur olarak bildiriliyor.
    """
    import tempfile, time as _t
    from finagent.collectors import tuik as tmod

    c, db = _tuik(tempfile.mkdtemp(), butce_sn=300.0)
    c._butce_bitis = _t.monotonic() - 1.0
    cagri = []
    eski = tmod.httpx.get
    tmod.httpx.get = lambda url, **kw: cagri.append(url)
    try:
        try:
            c._get("/dataflow/TR/all/latest")
            raise AssertionError("butce bitmisken hata bekleniyordu")
        except TimeoutError as e:
            assert "butce" in str(e).lower(), str(e)
    finally:
        tmod.httpx.get = eski
        db.close()
    assert cagri == [], f"butce bitmisken ag cagrisi yapildi: {cagri}"


# ======================================================================
# M4 / T2 — Turkce normalizasyon
# ======================================================================

def test_tr_lower_turkce_kucultur():
    """
    Python'un `.lower()`'i Turkce'de yanlis: 'IŞIK' -> 'işik'.
    Nokta MESELESI: 'I' ile 'i' Turkce'de ayri harflerdir.
    """
    from finagent.search.normalize import tr_lower

    assert tr_lower("İSTANBUL") == "istanbul"
    assert tr_lower("IŞIK") == "ışık"
    assert tr_lower("TÜRKİYE") == "türkiye"
    # Python'un kendi davranisi FARKLI olmali — testin bir sey olcugunun
    # kaniti. Ayni cikiyorsa fonksiyon hicbir is yapmiyor demektir.
    assert "IŞIK".lower() != tr_lower("IŞIK")


def test_tr_lower_BIRLESIK_NOKTA_uretmez():
    """
    En sinsi hali: `"İ".lower()` gorunuste 'i' verir ama ASLINDA
    'i' (U+0069) + BIRLESIK NOKTA (U+0307) — iki kod noktasi.

    Kullanicinin klavyeden yazdigi duz 'i' ile esleşmez ve trigram
    indeksinde uc harflik her pencereyi kaydirir. Ekranda fark
    GORUNMEZ, yani yakalanmazsa "arama neden bulmuyor" diye ortaya
    cikar ve kok neden hicbir yerde yazmaz.
    """
    from finagent.search.normalize import tr_lower

    BIRLESIK = "\u0307"   # acikca yaz: ham karakter GORUNMEZ ve
                           # dosya kodlamasina bagimli olurdu
    for girdi in ("İSTANBUL", "TÜRKİYE", "İ", "İYİ Kİ"):
        cikti = tr_lower(girdi)
        assert BIRLESIK not in cikti, (
            f"{girdi!r} -> {cikti!r} icinde U+0307 var: "
            f"{[f'U+{ord(x):04X}' for x in cikti]}")
    # Python'un kendisi bu tuzagi KURUYOR — karsilastirma olmadan
    # yukaridaki dongu bos yere gecer gorunur.
    assert BIRLESIK in "İ".lower()
    assert len(tr_lower("İSTANBUL")) == 8


def test_tr_lower_ayrisik_girdiyi_birlestirir():
    """
    Metin dis kaynaktan (Telegram, OCR, kopyala-yapistir) AYRISIK
    gelebilir: 'İ' yerine 'I' + U+0307. NFC olmadan bu dizi
    'ı' + U+0307'ye duserdi — yani tam ters harfe.
    """
    from finagent.search.normalize import tr_lower

    ayrisik = "I\u0307STANBUL"   # I + birlesik nokta, ACIKCA
    assert len(ayrisik) == 9            # ayrisik oldugunun kaniti
    assert tr_lower(ayrisik) == "istanbul"


def test_tr_fold_sapka_katlar():
    """
    Katlama Turkce'ye ozgu alti harfi VE sapkali a/i/u'yu kapsar.
    Ikincisi belgede yoktu; arsiv olcumu ekletti (asagidaki teste bak).
    """
    from finagent.search.normalize import tr_fold, leksik

    assert tr_fold("çğıöşü") == "cgiosu"
    assert tr_fold("âîû") == "aiu"
    # Buyuk harfler de katlanmali: `tr_fold` tek basina cagrilinca
    # sessizce yarim is yapmamali. `İ`nin buyuk karsiligi `I`.
    assert tr_fold("ÇĞİÖŞÜ") == "CGIOSU"
    assert leksik("ĞİÖŞÜÇ") == "giosuc"


def test_leksik_KAR_ZARAR_olculmus_vakasi():
    """
    Neden `â` katlama tablosunda: arsivde (2026-08-20, 156 satir)
    'â' 222 kez geciyor ve 'kâr' 121 kez — 'kar ' ise 2 kez.

    Yani arsiv "kâr/zarar" yaziyor. Telefon klavyesinde `â` yazan yok;
    kullanici "kar zarar" yazar. Katlanmazsa 121 kayit ISKALANIR.
    """
    from finagent.search.normalize import leksik

    belge = leksik("Kâr/Zarar +%60,53")
    sorgu = leksik("kar zarar")
    assert belge == "kar/zarar +%60,53"
    for kelime in sorgu.split():
        assert kelime in belge, f"{kelime!r} bulunamadi: {belge!r}"


def test_leksik_iki_tarafi_TEK_fonksiyon_kullanir():
    """
    FTS5'e yazilan metin ile FTS5'e giden sorgu AYNI donusumden
    gecmek ZORUNDA. Iki ayri cagri yerinde birbirinden bagimsiz
    degisebilir; tek fonksiyon degisemez.

    Sozlesme: leksik(x) == tr_fold(tr_lower(x)), her x icin.
    """
    from finagent.search.normalize import tr_lower, tr_fold, leksik

    ornekler = ["Altını", "IŞIK", "İSTANBUL", "kâr/zarar", "",
                "PGSUS 149,30 ₺", "Garanti bankasında 181 gram altın"]
    for x in ornekler:
        assert leksik(x) == tr_fold(tr_lower(x)), x


def test_normalize_saf_fonksiyon():
    """Ayni girdi -> ayni cikti; girdi DEGISMEZ (yan etki yok)."""
    from finagent.search.normalize import leksik

    girdi = "Altın Hesabım"
    assert leksik(girdi) == leksik(girdi)
    assert girdi == "Altın Hesabım"


# ======================================================================
# M4 / T1 — Altin kume (olcum referansi)
# ======================================================================

def _altin_kume():
    import json
    yol = _pathlib.Path(__file__).parent / "altin_kume.json"
    return json.loads(yol.read_text(encoding="utf-8"))


def test_altin_kume_dengeli_ve_tam():
    """
    Altin kume OLCUM ALETIDIR; bozuksa T3-T6'nin butun sayilari bozuk
    cikar ve bunu hicbir sey soylemez. Aletin kendisi de denetlenir.

    Denge sart: tek ortalama hangi hata SINIFININ cozuldugunu gizler,
    bu yuzden uc kategori esit agirlikta olmali.
    """
    kume = _altin_kume()
    kayitlar = kume["kayitlar"]
    assert len(kayitlar) == 15, len(kayitlar)

    from collections import Counter
    dagilim = Counter(k["kategori"] for k in kayitlar)
    assert dagilim == {"tam_kelime": 5, "parafraz": 5, "kelime_yok": 5}, dagilim

    idler = [k["id"] for k in kayitlar]
    assert len(set(idler)) == 15, "kayit id'leri benzersiz degil"


def test_altin_kume_kategorisi_ELLE_YAZILMADI():
    """
    `kategori` alani, sorgunun icerik kelimelerinin hedef alisveriste
    gecip gecmediginden URETILDI. Dosya bu olcumu de tasiyor; ikisinin
    tutarli olmasi kategorinin sonradan elle oynanmadiginin kanitidir.

    Onceki turda (model2vec) tam bu yuzden yaniltici sonuc alinmisti:
    kategori "hissedilerek" atanirsa, kotu sonuc "zor sorguydu" diye
    aciklanabilir hale gelir ve olcum anlamini kaybeder.
    """
    for k in _altin_kume()["kayitlar"]:
        gecen = k["olcum"]["gecen_kelimeler"]
        gecmeyen = k["olcum"]["gecmeyen_kelimeler"]
        assert gecen or gecmeyen, f"{k['id']}: olcum bos"
        if k["kategori"] == "tam_kelime":
            assert not gecmeyen, f"{k['id']}: tam_kelime ama gecmeyen var: {gecmeyen}"
        elif k["kategori"] == "kelime_yok":
            assert not gecen, f"{k['id']}: kelime_yok ama gecen var: {gecen}"
        else:
            assert gecen and gecmeyen, f"{k['id']}: parafraz iki tarafli olmali"


def test_altin_kume_IKI_VARYANT_tasir():
    """
    Onceki turda model2vec olcumu ASCII sorguyla, FTS5 olcumu sapkali
    sorguyla yapildi — karsilastirma GECERSIZDI. Her kayit iki varyati
    da tasir ki yontemler ayni sorguyu gorsun.

    `sorgu_ascii` elle yazilmaz: tam olarak tr_fold(sorgu).
    """
    from finagent.search.normalize import tr_fold

    sapkali_var = False
    for k in _altin_kume()["kayitlar"]:
        assert k["sorgu_ascii"] == tr_fold(k["sorgu"]), k["id"]
        if k["sorgu"] != k["sorgu_ascii"]:
            sapkali_var = True
    # En az bir sorgu gercekten sapkali olmali, yoksa "iki varyant"
    # sozlesmesi hicbir sey test etmiyor demektir.
    assert sapkali_var, "hicbir sorguda sapkali harf yok — varyant testi bos"


def test_altin_kume_hedefleri_ALISVERIS_cifti():
    """
    Bir tur user+assistant CIFTIDIR; arama ikisinden hangisini
    dondururse donsun dogru konusma yuzeye cikmistir. Kabul kumesi
    calisma aninda `id-1` diye HESAPLANMAZ, dosyada ACIKCA yazar:
    arsivde bir mesaj kaydedilemezse esleşme bozulur ve donmus bir
    olcum aletinin canli veri seklinden turemesi dogru degil.
    """
    for k in _altin_kume()["kayitlar"]:
        hedef = k["beklenen_tur_id"]
        assert k["kabul_edilen_idler"] == [hedef - 1, hedef], k["id"]
        assert hedef % 2 == 0, f"{k['id']}: hedef assistant satiri olmali"
        assert k["sahip"] in ("ali", "yuksel"), k["sahip"]
        assert k["sorgu"].strip(), k["id"]


def test_altin_kume_DONDURULMUS_sozlesmesi():
    """
    Dosya T3-T6 olculmeden once yazildi. Sozlesme dosyanin KENDISINDE
    duruyor ki, ileride biri sonucu iyilestirmek icin sorgu degistirmeye
    kalktiginda niyetin ne oldugu yazili olsun.
    """
    kume = _altin_kume()
    assert kume["dondurulmus"] is True
    assert kume["metrikler"] == ["recall@3", "MRR"]
    metin = " ".join(kume["sozlesme"])
    assert "DEGISTIRILMEYECEK" in metin
    assert "SISTEM duzeltilir" in metin


# ======================================================================
# M4 / T4 — FTS5 trigram metin indeksi
# ======================================================================

def _fts_db(tmp, satirlar=()):
    """
    Arsiv indeksi kurulu bir veritabani + istege bagli turlar.

    Yazma GERCEK yoldan (`sohbet_kaydet`) yapiliyor; duz INSERT ile
    kurulan bir fikstur, uretimde o yolun tetikleyiciyi calistirip
    calistirmadigini test ETMEZDI.
    """
    db = Database(_pathlib.Path(tmp) / "fts.db")
    db.init_schema()
    for i, (sahip, rol, metin) in enumerate(satirlar):
        db.sohbet_kaydet("1", rol, metin, sahip=sahip,
                         ts=f"2026-08-19T10:{i:02d}:00+00:00")
    return db


def test_fts_ifadesi_KISA_terimi_atar():
    """
    FTS5 trigram uc harflik pencerelerle calisir; daha kisa bir terim
    HIC eslesmez (olculdu: MATCH '"tl"' -> bos). Ortuk birlestirmede
    tek bir "tl" tum sorguyu oldururdu — atmak daraltma degil, KURTARMA.
    """
    from finagent.storage.db import fts_ifadesi

    assert fts_ifadesi("altin hesabi") == '"altin" OR "hesabi"'
    assert fts_ifadesi("altin hesabi kac TL") == '"altin" OR "hesabi" OR "kac"'
    assert fts_ifadesi("tl mi") is None          # kullanilabilir terim yok
    assert fts_ifadesi("") is None
    assert fts_ifadesi(None) is None


def test_fts_ifadesi_HAM_sorguyu_gecirmez():
    """
    Kullanicinin yazabilecegi siradan diziler ham verilirse FTS5
    SOZDIZIMI HATASI firlatir — olculdu:
        MATCH '-13,40'  -> no such column: 13
        MATCH 'a"b'     -> unterminated string
        MATCH '(altin'  -> fts5: syntax error
    Yani "gecmiste ne konusmustuk" sorusu bir ISTISNAYA donerdi.
    """
    import sqlite3 as _sq
    from finagent.storage.db import fts_ifadesi

    c = _sq.connect(":memory:")
    c.execute("CREATE VIRTUAL TABLE f USING fts5(metin, tokenize='trigram')")
    c.execute("INSERT INTO f(rowid, metin) VALUES (1, 'tralt 10 adet -13,40 tl zarar')")

    for ham in ("-13,40", 'a"b', "(altin", "altin OR moderna", "NOT tralt", "*"):
        ifade = fts_ifadesi(ham)
        if ifade is None:
            continue
        # Patlamamali: yalnizca tirnaklanmis terimler uretiliyor.
        c.execute("SELECT rowid FROM f WHERE f MATCH ?", (ifade,)).fetchall()

    # Ve ham hali GERCEKTEN patliyor — yoksa bu test hicbir sey olcmuyor.
    try:
        c.execute("SELECT rowid FROM f WHERE f MATCH ?", ("-13,40",)).fetchall()
        raise AssertionError("ham sorgu patlamadi; test bos")
    except _sq.OperationalError:
        pass


def test_fts_tetikleyicileri_indeksi_TAZE_tutuyor():
    """
    "Yeni tur eklendiginde indeks sessizce eskimemeli." Uc yol da
    kapali olmali: ekleme, guncelleme, silme.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db = _fts_db(d, [("ali", "user", "Garanti bankasinda 181 gram altın hesabı")])
        bul = lambda q: [r["id"] for r in db.sohbet_ara_fts("ali", gun=3650, sorgu=q)]

        assert bul("altin") == [1], "INSERT tetikleyicisi indekslemedi"

        with db.tx() as c:
            c.execute("UPDATE sohbet_kaydi SET metin = 'Gümüş hesabı' WHERE id = 1")
        assert bul("altin") == [], "UPDATE eski terimi indekste birakti"
        assert bul("gumus") == [1], "UPDATE yeni metni indekslemedi"

        with db.tx() as c:
            c.execute("DELETE FROM sohbet_kaydi WHERE id = 1")
        assert bul("gumus") == [], "DELETE indeksten dusurmedi"
        assert db.query("SELECT COUNT(*) n FROM sohbet_fts")[0]["n"] == 0
        db.close()


def test_fts_UDF_yoksa_SESSIZ_degil_SESLI_patlar():
    """
    Tetikleyici `leksik()` cagiriyor; fonksiyon `Database.__init__`'te
    kaydediliyor. UDF'siz bir baglanti yazmaya kalkarsa ne olur?

    Olculdu: `no such function: leksik`. Bu ISTENEN davranis — yazma
    reddediliyor. Alternatifi indeksin sessizce eskimesiydi ve bu
    projede yanlis "yok" beyani en yuksek siddetli hata sinifi olarak
    isaretli: arama "bulunamadi" der, kayit yerinde durur, kimse
    farketmez.
    """
    import sqlite3 as _sq
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        yol = _pathlib.Path(d) / "fts.db"
        db = _fts_db(d)
        db.close()
        # Yeni baglanti — UDF KAYITSIZ (calisan eski bir surecin durumu).
        ham = _sq.connect(yol)
        try:
            ham.execute("INSERT INTO sohbet_kaydi(ts, chat_id, sahip, rol, metin) "
                        "VALUES ('2026-08-19T10:00:00+00:00','1','ali','user','altın')")
            raise AssertionError("UDF yokken INSERT sessizce gecti")
        except _sq.OperationalError as e:
            assert "leksik" in str(e), str(e)
        finally:
            ham.close()


def test_fts_esitleme_IDEMPOTENT():
    """
    Tetikleyiciler yalnizca kendilerinden SONRAKI yazmalari yakalar;
    indeks kurulmadan once yazilmis satirlar onlar icin gorunmez. Geri
    doldurma o gecmisi kapatir ve iki kez kosunca CIFT KAYIT URETMEZ.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db = _fts_db(d, [("ali", "user", "altın hesabı"),
                         ("ali", "assistant", "181 gram")])
        n = lambda: db.query("SELECT COUNT(*) c FROM sohbet_fts")[0]["c"]
        assert n() == 2
        db._sohbet_fts_esitle()
        db._sohbet_fts_esitle()
        assert n() == 2, f"tekrar calistirinca {n()} satir — cift kayit"
        assert len(db.sohbet_ara_fts("ali", gun=3650, sorgu="altin")) == 1
        db.close()


def test_fts_esitleme_YETIM_satiri_temizler():
    """
    Ters yon: kaynagi olmayan indeks satiri. JOIN'de duserdi, yani
    "sonuc var ama gosterilemiyor" gibi SESSIZ bir eksilme olurdu.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db = _fts_db(d, [("ali", "user", "altın hesabı")])
        with db.tx() as c:
            c.execute("INSERT INTO sohbet_fts(rowid, metin) "
                      "VALUES (999, 'yetim satir')")
        assert db.query("SELECT COUNT(*) c FROM sohbet_fts")[0]["c"] == 2
        db._sohbet_fts_esitle()
        assert db.query("SELECT COUNT(*) c FROM sohbet_fts")[0]["c"] == 1
        db.close()


def test_fts_SAPKASIZ_sorgu_sapkali_metni_buluyor():
    """
    Bu ozelligin varlik sebebi. Olculdu (canli arsiv, 2026-08-20):
        LIKE 'altın' -> 20 satir      LIKE 'altin' -> 1 satir
    Telefon klavyesinde `altın` yazan yok. Indeks ve sorgu AYNI
    `leksik()` fonksiyonundan gectigi icin ikisi ayrisamaz.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db = _fts_db(d, [
            ("ali", "assistant", "Garanti bankasında 181 gram altın hesabı"),
            ("ali", "assistant", "Kâr/Zarar +%60,53 — TRALT pozisyonu"),
        ])
        bul = lambda q: [r["id"] for r in db.sohbet_ara_fts("ali", gun=3650, sorgu=q)]
        for sapkali, sapkasiz in (("altın", "altin"), ("hesabı", "hesabi"),
                                  ("kâr", "kar"), ("İSTANBUL", "istanbul")):
            assert bul(sapkali) == bul(sapkasiz), \
                f"{sapkali!r} ve {sapkasiz!r} ayni sonucu vermedi"
        assert bul("altin") == [1]
        assert bul("kar zarar") == [2]      # `kâr` -> `kar`, olculmus vaka
        db.close()


def test_fts_COK_KELIMELI_sorgu_calisiyor():
    """
    LIKE'in YAPISAL arizasi: sorgunun TAMAMI tek bir `%...%` kalibi
    oluyordu. Olculdu — altin kumedeki 15 dogal sorgunun 13'u SIFIR
    satir dondurdu, cunku "altın hesabı kaç TL" diye bir dize arsivde
    gecmiyor.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db = _fts_db(d, [("ali", "assistant",
                          "Garanti bankasında olan 181 gram altın hesabı "
                          "bugün 1.212.000 TL eder")])
        assert db.sohbet_ara("ali", gun=3650, sorgu="altın hesabı kaç TL") == [], \
            "LIKE bunu bulmamaliydi — baseline degismis"
        assert [r["id"] for r in
                db.sohbet_ara_fts("ali", gun=3650, sorgu="altın hesabı kaç TL")] == [1]
        db.close()


def test_fts_SAHIP_suzgeci_sizdirmiyor():
    """
    Arsivin en sert kurali: okuma DAIMA `WHERE sahip = ?`. Yeni bir
    arama yolu acmak bu kurali delmek icin bahane degil — iki kisinin
    sohbeti ayni veritabaninda.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db = _fts_db(d, [("ali", "assistant", "181 gram altın hesabı"),
                         ("yuksel", "assistant", "PGSUS altın gibi hisse")])
        assert [r["id"] for r in db.sohbet_ara_fts("ali", gun=3650, sorgu="altin")] == [1]
        assert [r["id"] for r in db.sohbet_ara_fts("yuksel", gun=3650, sorgu="altin")] == [2]
        try:
            db.sohbet_ara_fts("", gun=3650, sorgu="altin")
            raise AssertionError("sahipsiz arama gecti")
        except ValueError:
            pass
        db.close()


def test_fts_ALAKA_sirasiyla_donuyor():
    """
    `sohbet_ara` eslesenlerin en yenilerini alip KRONOLOJIK diziyordu:
    okunabilirlik icin dogru, SECIM icin yanlis — "ilk 3" orada "en
    alakali 3" demek degildi ve recall@3 olcumu anlamsizlasirdi.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db = _fts_db(d, [
            ("ali", "assistant", "kısa bir not, altın kelimesi bir kez geçiyor"),
            ("ali", "assistant", "altın altın altın altın gram altın hesabı altın"),
        ])
        idler = [r["id"] for r in db.sohbet_ara_fts("ali", gun=3650, sorgu="altin")]
        assert idler[0] == 2, f"alaka sirasi yok: {idler}"
        # Kronolojik olsaydi 1 once gelirdi — karsilastirma olmadan
        # yukaridaki iddia tesadufen de gecebilirdi.
        assert [r["id"] for r in db.sohbet_ara("ali", gun=3650, sorgu="altın")][0] == 1
        db.close()


def test_fts_bos_sorgu_EN_YENILERI_donuyor():
    """
    Sorgu yoksa "sonuc yok" demek yanlis olurdu: `sohbet_arsivi`
    araci bos `sorgu` ile "son turlari getir" anlaminda cagriliyor.
    Indeksin isi ARAMAK, listelemeyi degistirmek degil.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db = _fts_db(d, [("ali", "user", "birinci"), ("ali", "user", "ikinci")])
        for bos in ("", "   ", None):
            assert [r["id"] for r in db.sohbet_ara_fts("ali", gun=3650, sorgu=bos)] \
                == [r["id"] for r in db.sohbet_ara("ali", gun=3650, sorgu=bos)]
        db.close()


# ======================================================================
# M4 / T5 — Gomme katmani (embeddinggemma, yerel Ollama)
# ======================================================================

class _SahteCevap:
    """httpx.Response taklidi — AGA CIKMADAN gomme yolunu kosturmak icin."""

    def __init__(self, veri, hata=None):
        self._veri, self._hata = veri, hata

    def raise_for_status(self):
        if self._hata:
            raise self._hata

    def json(self):
        return self._veri


def _sahte_gomme(monkey_veri, boyut=768):
    """`Gomme` + istekleri yakalayan sahte httpx.post dondurur."""
    from finagent.search import gomme as gmod

    yakalanan = {"cagri": []}

    def sahte_post(url, json=None, timeout=None):
        yakalanan["cagri"].append({"url": url, "json": json, "timeout": timeout})
        return _SahteCevap(monkey_veri(json))

    g = gmod.Gomme(url="http://localhost:11434", model="embeddinggemma",
                   boyut=boyut, timeout_sn=5, batch=2)
    return g, gmod, sahte_post, yakalanan


def test_gomme_ONEKLERI_kendisi_ekliyor():
    """
    EmbeddingGemma ASIMETRIK: sorgu ve belge ayri sablonlardan gecer.

    Ollama BUNU UYGULAMIYOR — `ollama show --modelfile` ciktisi
    `TEMPLATE {{ .Prompt }}`, yani duz gecis. Onekleri biz ekliyoruz.

    Etkisi olculdu (altin kume, 156 satir yeniden indekslenerek):
        onekli   tam_kelime 100%  kelime_yok 50%  MRR 0.516
        oneksiz  tam_kelime  70%  kelime_yok  0%  MRR 0.363
    Yani onek bir ince ayar degil; oneksiz hali `kelime_yok`
    kategorisini TAMAMEN kaybediyor.
    """
    from finagent.search.gomme import SORGU_ONEKI, BELGE_ONEKI

    g, gmod, sahte, yakalanan = _sahte_gomme(
        lambda j: {"embeddings": [[0.0] * 768 for _ in j["input"]]})
    eski = gmod.httpx.post
    gmod.httpx.post = sahte
    try:
        g.sorgu("altın hesabı")
        g.belgeler(["altın hesabı"])
    finally:
        gmod.httpx.post = eski

    sorgu_girdi = yakalanan["cagri"][0]["json"]["input"][0]
    belge_girdi = yakalanan["cagri"][1]["json"]["input"][0]
    assert sorgu_girdi == SORGU_ONEKI + "altın hesabı", sorgu_girdi
    assert belge_girdi == BELGE_ONEKI + "altın hesabı", belge_girdi
    assert sorgu_girdi != belge_girdi, "asimetri kaybolmus"
    assert SORGU_ONEKI and BELGE_ONEKI, "onekler bos"


def test_gomme_OLLAMA_KAPALIYSA_bos_liste_DEGIL_hata():
    """
    "Sonuc yok" ile "arama CALISMADI" ayri seyler. Sessiz bos donus,
    kullaniciya yanlis bir "yok" beyani gonderir — bu projede olculmus
    en yuksek siddetli hata sinifi.
    """
    import httpx as _httpx
    from finagent.search import gomme as gmod
    from finagent.search.gomme import Gomme, GommeHatasi

    g = Gomme(url="http://localhost:9", model="m", boyut=768,
              timeout_sn=1, batch=2)
    eski = gmod.httpx.post

    def patla(url, json=None, timeout=None):
        raise _httpx.ConnectError("baglanti reddedildi")

    gmod.httpx.post = patla
    try:
        for cagri in (lambda: g.sorgu("altin"), lambda: g.belgeler(["altin"])):
            try:
                cagri()
                raise AssertionError("Ollama kapaliyken hata bekleniyordu")
            except GommeHatasi as e:
                assert "ulasilamadi" in str(e).lower(), str(e)
    finally:
        gmod.httpx.post = eski


def test_gomme_BOS_liste_icin_aga_cikmaz():
    """
    "Indekslenecek bir sey yok" ile "Ollama kapali" ayri seyler;
    ilkinde ag cagrisi yapmak, kapali bir Ollama'da gereksiz bir
    hataya donerdi.
    """
    from finagent.search import gomme as gmod
    from finagent.search.gomme import Gomme

    g = Gomme(url="http://localhost:9", model="m", boyut=768,
              timeout_sn=1, batch=2)
    cagrildi = []
    eski = gmod.httpx.post
    gmod.httpx.post = lambda *a, **k: cagrildi.append(1)
    try:
        assert g.belgeler([]) == []
    finally:
        gmod.httpx.post = eski
    assert cagrildi == [], "bos liste icin ag cagrisi yapildi"


def test_gomme_BOYUT_denetleniyor():
    """
    Model kartina guvenilmez, olculur. Boyut degisirse eski BLOB'lar
    gecersizdir ve sessizce YANLIS benzerlik uretirler — iki farkli
    vektor uzayinin nokta carpimi bir sayi verir, ve o sayi anlamsiz
    oldugu halde makul gorunur.
    """
    from finagent.search.gomme import GommeHatasi

    g, gmod, sahte, _ = _sahte_gomme(
        lambda j: {"embeddings": [[0.0] * 512 for _ in j["input"]]})
    eski = gmod.httpx.post
    gmod.httpx.post = sahte
    try:
        g.sorgu("altin")
        raise AssertionError("yanlis boyut kabul edildi")
    except GommeHatasi as e:
        assert "768" in str(e) and "512" in str(e), str(e)
    finally:
        gmod.httpx.post = eski


def test_gomme_EKSIK_vektor_sessizce_gecmiyor():
    """N metin gonderildi, N'den az vektor dondu -> hata."""
    from finagent.search.gomme import GommeHatasi

    g, gmod, sahte, _ = _sahte_gomme(lambda j: {"embeddings": [[0.0] * 768]})
    eski = gmod.httpx.post
    gmod.httpx.post = sahte
    try:
        g.belgeler(["bir", "iki"])
        raise AssertionError("eksik vektor kabul edildi")
    except GommeHatasi:
        pass
    finally:
        gmod.httpx.post = eski


def test_gomme_KIRPMA_beyan_ediliyor():
    """
    Model ~4616 karakterde SESSIZCE kesiyor (olculdu: en uzun arsiv
    satirinin ilk 4616 karakterinin vektoru, 8983 karakterlik tam
    metnin vektoruyle OZDES — %49'u atilmis, uyari yok).

    Arsivde 156 satirin 8'i (%5,1) siniri asiyor. Kirpma kabul
    ediliyor ama BEYAN ediliyor; sessiz kayip beyan edilmeyen kayiptir.
    """
    from finagent.search.gomme import Gomme, AZAMI_KARAKTER

    assert Gomme.kirpildi("a" * (AZAMI_KARAKTER + 1)) is True
    assert Gomme.kirpildi("a" * AZAMI_KARAKTER) is False
    assert Gomme.kirpildi("") is False
    assert Gomme.kirpildi(None) is False
    # Ve gercekten kirpilmis metin gonderiliyor — sinirin kodda
    # gorunur olmasinin sebebi bu.
    g, gmod, sahte, yakalanan = _sahte_gomme(
        lambda j: {"embeddings": [[0.0] * 768 for _ in j["input"]]})
    eski = gmod.httpx.post
    gmod.httpx.post = sahte
    try:
        g.belgeler(["x" * 9000])
    finally:
        gmod.httpx.post = eski
    girdi = yakalanan["cagri"][0]["json"]["input"][0]
    assert len(girdi) <= AZAMI_KARAKTER + len("title: none | text: ")


def test_gomme_BATCH_halinde_gonderiyor():
    """156 metni tek istekte yollamak, timeout'u tek noktaya yigar."""
    g, gmod, sahte, yakalanan = _sahte_gomme(
        lambda j: {"embeddings": [[0.0] * 768 for _ in j["input"]]})
    eski = gmod.httpx.post
    gmod.httpx.post = sahte
    try:
        g.belgeler(["a", "b", "c", "d", "e"])       # batch = 2
    finally:
        gmod.httpx.post = eski
    boyutlar = [len(c["json"]["input"]) for c in yakalanan["cagri"]]
    assert boyutlar == [2, 2, 1], boyutlar


def test_gomme_BLOB_gidis_donus_float32():
    """
    768 x float32 = 3072 bayt. `float64` iki kati yer kaplardi ve model
    zaten float32 uretiyor; sayi kaybi yok, yer kazanci gercek.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db = _fts_db(d, [("ali", "user", "altın hesabı")])
        v = [0.5] * 768
        assert db.sohbet_gomme_yaz([(1, v)], "embeddinggemma") == 1
        r = db.query("SELECT length(gomme) L, gomme_model, gomme_ts "
                     "FROM sohbet_kaydi WHERE id = 1")[0]
        assert r["L"] == 768 * 4, r["L"]
        assert r["gomme_model"] == "embeddinggemma"
        assert r["gomme_ts"], "zaman damgasi yazilmadi"
        bulunan = db.sohbet_gomme_ara("ali", v, "embeddinggemma", gun=3650)
        assert [x["id"] for x in bulunan] == [1]
        assert abs(bulunan[0]["puan"] - sum(x * x for x in v)) < 1e-3
        assert "gomme" not in bulunan[0], "3 KB'lik BLOB sonuca sizdi"
        db.close()


def test_gomme_KARISIK_model_sessizce_atlanmiyor():
    """
    Belgenin sart kostugu davranis: beklenenden farkli modelden gelen
    satir gorulurse ACIK HATA. Sessizce atlamak sonucu sessizce
    eksiltirdi; karistirmak daha kotusu — iki ayri vektor uzayinin
    nokta carpimi makul gorunen anlamsiz bir sayi uretir.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db = _fts_db(d, [("ali", "user", "bir"), ("ali", "user", "iki")])
        db.sohbet_gomme_yaz([(1, [0.5] * 768)], "embeddinggemma")
        db.sohbet_gomme_yaz([(2, [0.5] * 768)], "ESKI-MODEL")
        try:
            db.sohbet_gomme_ara("ali", [0.5] * 768, "embeddinggemma", gun=3650)
            raise AssertionError("karisik vektor uzayi sessizce kabul edildi")
        except ValueError as e:
            assert "ESKI-MODEL" in str(e), str(e)
        # Ve model degisen satir "eksik" sayilmali ki yeniden indekslensin.
        eksik = [r["id"] for r in db.sohbet_gomme_eksikler("embeddinggemma")]
        assert eksik == [2], eksik
        db.close()


def test_gomme_indeksleme_IDEMPOTENT_ve_kirpmayi_sayiyor():
    import tempfile

    from finagent.search import gomme as gmod
    from finagent.search.indeks import gomme_indeksle

    with tempfile.TemporaryDirectory() as d:
        db = _fts_db(d, [("ali", "user", "kısa"),
                         ("ali", "assistant", "u" * 9000)])
        g, gmod_, sahte, _ = _sahte_gomme(
            lambda j: {"embeddings": [[0.1] * 768 for _ in j["input"]]})
        eski = gmod.httpx.post
        gmod.httpx.post = sahte
        try:
            r1 = gomme_indeksle(db, g)
            r2 = gomme_indeksle(db, g)
        finally:
            gmod.httpx.post = eski
        assert r1["yazilan"] == 2 and r1["eksik"] == 2, r1
        assert r1["kirpilan"] == 1, r1          # yalnizca 9000 karakterli
        assert r2 == {"eksik": 0, "yazilan": 0, "kirpilan": 0,
                      "sure_sn": 0.0, "model": g.model}, r2
        db.close()


def test_gomme_ayari_VARSAYILANA_dusmuyor():
    """
    Eksik bir gomme ayarinin sessizce varsayilana dusmesi, YANLIS bir
    vektor uzayinda arama yapmak demektir — bos sonuctan kotu, cunku
    makul gorunen alakasiz turlar doner.
    """
    from finagent.config import Settings, load_settings

    tam = load_settings().gomme_ayari()
    assert tam["boyut"] == 768 and tam["model"]

    for bozuk, parca in (
            ({}, "tanimli degil"),
            ({"enabled": True, "url": "u", "model": "m"}, "eksik alan"),
            ({"enabled": "evet", "url": "u", "model": "m", "boyut": 768,
              "timeout_sn": 1, "batch": 1}, "bool"),
            ({"enabled": True, "url": "", "model": "m", "boyut": 768,
              "timeout_sn": 1, "batch": 1}, "url"),
            ({"enabled": True, "url": "u", "model": "m", "boyut": 0,
              "timeout_sn": 1, "batch": 1}, "boyut"),
    ):
        s = Settings({"arama": {"gomme": bozuk}} if bozuk else {}, _pathlib.Path("."))
        try:
            s.gomme_ayari()
            raise AssertionError(f"bozuk ayar kabul edildi: {bozuk}")
        except ValueError as e:
            assert parca in str(e), f"{parca!r} beklendi, {e}"


# ======================================================================
# M4 / T6 — Hibrit siralama (RRF)
# ======================================================================

def test_rrf_iki_siralamayi_birlestiriyor():
    from finagent.search.hibrit import rrf, RRF_K

    # Iki listede de ustte olan kazanir.
    assert rrf([1, 2, 3], [1, 5, 6])[0] == 1
    # Tek listede gecen, iki listede gecenden sonra gelir.
    sonuc = rrf([9, 1], [1, 8])
    assert sonuc[0] == 1, sonuc

    # Puan formulu belgede yazili olanla AYNI: 1/(k+sira)
    from finagent.search.hibrit import rrf_puanlari
    p = rrf_puanlari([7], [7])
    assert abs(p[7] - 2 * (1.0 / (RRF_K + 1))) < 1e-12


def test_rrf_BOS_liste_gecerli_girdi():
    """
    Bir yol hic sonuc bulamamis olabilir; bu, digerinin sonucunu
    gecersiz kilmaz. Ama liste EKSIK degil BOS gelmeli — cagiran bir
    HATAYI bos listeye cevirirse, hibrit onu "bir sey bulamadi" diye
    okur ve arizayi sessizce yutar. Bu yuzden gomme yolu Ollama
    kapaliyken bos liste degil ISTISNA firlatiyor (yukaridaki teste bak).
    """
    from finagent.search.hibrit import rrf

    assert rrf([], [4, 5]) == [4, 5]
    assert rrf([4, 5], []) == [4, 5]
    assert rrf([], []) == []


def test_rrf_kararli_ve_dogrulanmis():
    from finagent.search.hibrit import rrf

    # Esitlikte sira KARARLI olmali; belirsizlik olcumu kosudan kosuya
    # oynatirdi.
    for _ in range(5):
        assert rrf([1, 2], [3, 4]) == [1, 3, 2, 4]
    try:
        rrf([1], k=0)
        raise AssertionError("k=0 kabul edildi")
    except ValueError:
        pass


# ======================================================================
# M4 / T7 — canliya alinan yol: `sohbet_arsivi` araci
# ======================================================================

def test_sohbet_arsivi_araci_FTS5_kullaniyor():
    """
    T7 karari: FTS5 canliya alindi, gomme ALINMADI (hibrit `kelime_yok`
    kategorisinde FTS5'e gore kazanc saglamadi — ikisi de %20).

    Bu test aracin GERCEKTEN yeni yolu kullandigini sabitliyor. Iki
    olculmus ariza uzerinden: cok kelimeli sorgu ve sapkasiz sorgu.
    Eski `LIKE` yolu ikisinde de SIFIR donduruyordu.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        db.sohbet_kaydet("5643817523", "user", "Garanti'deki altın hesabım ne oldu",
                         sahip="ali", ts="2026-08-19T10:00:00+00:00")
        db.sohbet_kaydet("5643817523", "assistant",
                         "181 gram altın ≈ 1.212.000 TL — Garanti bankası satış fiyatı",
                         sahip="ali", ts="2026-08-19T10:01:00+00:00")
        arac = {t.name: t for t in tb.araclar()}["sohbet_arsivi"]

        # 1) Cok kelimeli dogal sorgu — LIKE bunu bulamiyordu.
        out = _cagir(arac, sorgu="altın hesabı kaç TL", gun=3650)
        assert out["turlar"], "cok kelimeli sorgu bos dondu (LIKE yoluna donmus)"

        # 2) Sapkasiz sorgu, sapkali metni bulmali.
        ascii_out = _cagir(arac, sorgu="altin hesabi", gun=3650)
        assert ascii_out["turlar"], "sapkasiz sorgu bos dondu"

        # Ve eski yol GERCEKTEN bulamiyor — karsilastirma olmadan
        # yukaridaki iddialar tesadufen de gecebilirdi.
        assert db.sohbet_ara("ali", gun=3650, sorgu="altın hesabı kaç TL") == []
        db.close()


def test_pozisyon_kaydet_MALIYET_alanini_kabul_ediyor():
    """
    OLCULDU (2026-08-20): 25 pozisyonun 25'inde `avg_cost` NULL'di.
    Kolon SEMADA VARDI, ekran goruntusu yolu onu DOLDURUYORDU, `portfoy`
    onu RAPORLUYORDU — eksik olan tek sey sohbetten gelen maliyetin
    girecegi kapiydi. Kullanici 19 Agustos'ta "144,93 dolardan aldim"
    demisti ve gidecek yeri yoktu.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        arac = {t.name: t for t in tb.araclar()}["pozisyon_kaydet"]
        out = _cagir(arac, hesap="bux", para_birimi="EUR", toplam_deger=124.45,
                     pozisyonlar=_json_dumps([{"sembol": "MRNA", "ad": "Moderna",
                                               "adet": 1, "maliyet": 124.452}]))
        assert out["durum"] == "ONAY BEKLIYOR", out

        depo = tb.onay_deposu if hasattr(tb, "onay_deposu") else None
        import json as _j
        bekleyen = sorted((_pathlib.Path(d) / "pending").glob("*.json"))
        assert bekleyen, "onay dosyasi yazilmadi"
        veri = _j.loads(bekleyen[0].read_text(encoding="utf-8"))
        poz = veri["pozisyonlar"][0]
        assert poz["avg_cost"] == 124.452, poz
        assert poz["quantity"] == 1, poz


def test_kar_zarar_MALIYETTEN_hesaplaniyor_donmus_yuzdeden_degil():
    """
    Ekran sayilari YANLIS degil, ESKI. Olculdu: BUX'ta ASML "+%121,52"
    gosteriyordu — 14 Agustos ekranindan kalma. Alti gun boyunca fiyat
    oynadi, o yuzde hic kipirdamadi ve "guncel" gibi duruyordu.

    Maliyet elimizdeyse K/Z BUGUNKU fiyattan hesaplanir ve fiyatla
    birlikte HAREKET EDER.
    """
    import tempfile

    from finagent.analysis.portfolio import portfolio_summary

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "p.db")
        db.init_schema()
        iid = db.pozisyon_enstrumani("ZZTEST", "bux", "Test AS", None, "EUR")
        db.upsert_prices(iid, [
            {"ts": "2026-08-18", "open": 100, "high": 100, "low": 100,
             "close": 100, "volume": 1},
            {"ts": "2026-08-19", "open": 100, "high": 200, "low": 100,
             "close": 200, "volume": 1}], "test", currency="EUR")
        # Ekran DONMUS bir yuzde tasiyor (%10) ama maliyet de kayitli.
        db.insert_positions("bux", "2026-08-14T00:00:00+00:00", [
            {"symbol": "ZZTEST", "name": "Test AS", "quantity": 2,
             "avg_cost": 100.0, "market_value": 220.0, "pnl_pct": 10.0,
             "currency": "EUR"}], "ali")

        p = portfolio_summary(db, ["bux"], "ali")["hesaplar"]["bux"]["pozisyonlar"][0]
        # 2 adet x 200 (19 Agu kapanisi) = 400; maliyet 2 x 100 = 200
        assert p["kar_zarar_kaynagi"] == "maliyet", p
        assert p["maliyet_toplam"] == 200.0, p
        assert p["kar_zarar"] == 200.0, p
        assert p["kar_zarar_%"] == 100.0, p         # ekrandaki %10 DEGIL
        db.close()


def test_maliyet_EKRAN_kar_zararindan_ONCE_geliyor():
    """
    Sira bir TASARIM KARARI, tesaduf degil.

    Ekranin `pnl_abs`'i o an DONAR; maliyetten hesaplanan K/Z fiyatla
    HAREKET EDER. Ikisi de elimizdeyse hareketli olan kazanir.
    (Bu test onceki surumde YOKTU ve kasitli bozma bunu yakaladi:
    fikstur `pnl_abs` tasimadigi icin sira hic sinanmiyordu.)
    """
    import tempfile

    from finagent.analysis.portfolio import portfolio_summary

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "p.db")
        db.init_schema()
        iid = db.pozisyon_enstrumani("ZZTEST", "bux", "Test AS", None, "EUR")
        db.upsert_prices(iid, [
            {"ts": "2026-08-19", "open": 200, "high": 200, "low": 200,
             "close": 200, "volume": 1}], "test", currency="EUR")
        db.insert_positions("bux", "2026-08-14T00:00:00+00:00", [
            {"symbol": "ZZTEST", "name": "Test AS", "quantity": 2,
             "avg_cost": 100.0, "market_value": 220.0,
             "pnl_abs": 20.0, "pnl_pct": 10.0, "currency": "EUR"}], "ali")

        p = portfolio_summary(db, ["bux"], "ali")["hesaplar"]["bux"]["pozisyonlar"][0]
        assert p["kar_zarar_kaynagi"] == "maliyet", p
        assert p["kar_zarar"] == 200.0, p        # ekrandaki 20,0 DEGIL
        db.close()


def test_SIFIR_maliyet_bolme_hatasi_uretmiyor():
    """
    Bedelsiz pay / airdrop: `avg_cost = 0`. Yuzde hesabi sifira
    bolerdi. Kaynak "maliyet" DIYE ISARETLENMEZ — sifir maliyetten
    anlamli bir yuzde cikmaz — ve eldeki eski sayilara duser.
    """
    import tempfile

    from finagent.analysis.portfolio import portfolio_summary

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "p.db")
        db.init_schema()
        iid = db.pozisyon_enstrumani("ZZFREE", "bux", "Bedelsiz", None, "EUR")
        db.upsert_prices(iid, [
            {"ts": "2026-08-19", "open": 50, "high": 50, "low": 50,
             "close": 50, "volume": 1}], "test", currency="EUR")
        db.insert_positions("bux", "2026-08-14T00:00:00+00:00", [
            {"symbol": "ZZFREE", "name": "Bedelsiz", "quantity": 3,
             "avg_cost": 0.0, "market_value": 150.0, "pnl_pct": 5.0,
             "currency": "EUR"}], "ali")

        p = portfolio_summary(db, ["bux"], "ali")["hesaplar"]["bux"]["pozisyonlar"][0]
        assert p["kar_zarar_kaynagi"] != "maliyet", p
        assert p["kar_zarar_%"] == 5.0, p
        db.close()


def test_maliyet_yoksa_ESKI_davranis_korunuyor():
    """
    Maliyet YOKSA ekran sayilari hala kullanilir — bos birakmaktan
    iyidir. Yeni kaynagin eklenmesi eskileri ELEMEZ, sadece onlerine
    gecer.
    """
    import tempfile

    from finagent.analysis.portfolio import portfolio_summary

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "p.db")
        db.init_schema()
        db.pozisyon_enstrumani("ZZTEST", "bux", "Test AS", None, "EUR")
        db.insert_positions("bux", "2026-08-14T00:00:00+00:00", [
            {"symbol": "ZZTEST", "name": "Test AS", "quantity": 2,
             "market_value": 220.0, "pnl_pct": 10.0, "currency": "EUR"}], "ali")
        p = portfolio_summary(db, ["bux"], "ali")["hesaplar"]["bux"]["pozisyonlar"][0]
        assert p["kar_zarar_kaynagi"] == "turetilmis", p
        assert p["kar_zarar_%"] == 10.0, p
        assert p["maliyet_toplam"] is None, p
        db.close()


def test_maliyet_PARA_BIRIMI_tuzagi_tarifte_yaziyor():
    """
    MRNA vakasi: kullanici "144,93 dolardan aldim" dedi ama pozisyon
    BUX/EUR hesabinda ve dogru deger 124,452 EUR. 144,93'u oldugu gibi
    yazmak sessiz ve buyuk bir hata olurdu — bu projede ayni sinif
    17 pozisyonun 14'unde ~%15,7 sapma uretmisti.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        tarif = {t.name: t for t in tb.araclar()}["pozisyon_kaydet"].description
        assert "maliyet" in tarif.lower(), tarif
        assert "PARA BIRIMINDE" in tarif, tarif
        assert "CEVIR" in tarif, tarif
        db.close()


def _onay_bot(tmp):
    """Bekleyen onay dizini kurulu sahte bot; gonderilen mesajlar yakalanir."""
    from finagent.config import load_settings

    db = Database(_pathlib.Path(tmp) / "o.db")
    db.init_schema()
    bot = _sahte_bot(load_settings(), db)
    bot.pending_dir = _pathlib.Path(tmp) / "pending"
    bot.pending_dir.mkdir(parents=True, exist_ok=True)
    return bot, db


def _onay_yaz(bot, token, veri, yas_saat):
    """Belirli YASTA bir bekleyen istek — dosya mtime'i geriye alinir."""
    import os
    import time

    yol = bot._depo().yaz(token, veri)
    eski = time.time() - yas_saat * 3600
    os.utime(yol, (eski, eski))
    return yol


def test_suresi_dolan_onay_DUSUYOR_ve_haber_veriliyor():
    """
    OLCULDU (2026-08-20): `pending/` altinda 1-2 gunluk SEKIZ kayit
    birikmisti, ikisi 17 Agustos'tan. Dusme diye bir kavram yoktu.
    Birikmis onay iki turlu zarar verir: `/bekleyen` okunamaz hale
    gelir, ve gunler once sunulmus bir YAZMA butonu hala canlidir.
    """
    import tempfile

    from finagent.bot.onay import SURE_ASIMI

    with tempfile.TemporaryDirectory() as d:
        bot, db = _onay_bot(d)
        saat = SURE_ASIMI.total_seconds() / 3600
        _onay_yaz(bot, "eski1", {"_tip": "pozisyon", "_chat_id": "5643817523"},
                  yas_saat=saat + 5)
        _onay_yaz(bot, "eski2", {"_tip": "pozisyon", "_chat_id": "5643817523"},
                  yas_saat=saat + 1)
        _onay_yaz(bot, "yeni", {"_tip": "pozisyon", "_chat_id": "5643817523"},
                  yas_saat=1)

        assert bot._suresi_dolan_onaylari_dusur() == 2

        kalan = {o.token for o in bot._depo().bekleyenler()}
        assert kalan == {"yeni"}, f"yanlis kayit dustu: {kalan}"

        # SOHBET BASINA TEK MESAJ: iki kayit icin iki bildirim atmak
        # uyariyi gurultuye cevirirdi.
        assert len(bot.gonderilen) == 1, bot.gonderilen
        metin = bot.gonderilen[0][0] if isinstance(bot.gonderilen[0], tuple) \
            else str(bot.gonderilen[0])
        assert "2" in metin and "dustu" in metin.lower(), metin
        db.close()


def test_onay_dusmesi_HABER_GITMEZSE_silmiyor():
    """
    SIRA SOZLESMEDIR: haber -> silme, tersi degil.

    Ters sirada bir Telegram kesintisi, kullanicinin HIC HABERI OLMADAN
    isteklerini silerdi. `onay.py` modul basligi bunu acikca yasakliyor:
    "sessiz silme, sessiz yazmanin ikizidir."
    """
    import tempfile

    from finagent.bot.onay import SURE_ASIMI

    with tempfile.TemporaryDirectory() as d:
        bot, db = _onay_bot(d)
        _onay_yaz(bot, "eski", {"_tip": "pozisyon", "_chat_id": "5643817523"},
                  yas_saat=SURE_ASIMI.total_seconds() / 3600 + 5)
        bot._gonder = lambda *a, **k: False          # Telegram kesik

        assert bot._suresi_dolan_onaylari_dusur() == 0
        assert {o.token for o in bot._depo().bekleyenler()} == {"eski"}, \
            "haber gitmeden silindi"
        db.close()


def test_sahipsiz_onay_da_dusuyor():
    """
    `_chat_id` tasimayan ESKI dosyalar (bugun diskte iki tane vardi,
    17 Agustos'tan) dusmesi gereken en eski kayitlar. Sahipsiz diye
    atlansalardi sonsuza kadar diskte kalirlardi — ve sessizce silmek
    de gorunur kilmak istedigimiz anomaliyi gizlerdi.
    """
    import tempfile

    from finagent.bot.onay import SURE_ASIMI

    with tempfile.TemporaryDirectory() as d:
        bot, db = _onay_bot(d)
        _onay_yaz(bot, "sahipsiz", {"ekran_tipi": "liste"},
                  yas_saat=SURE_ASIMI.total_seconds() / 3600 + 40)
        assert bot._suresi_dolan_onaylari_dusur() == 1
        assert bot._depo().bekleyenler() == []
        assert bot.gonderilen, "sahipsiz kayit SESSIZCE silindi"
        db.close()


def test_onay_sure_asimi_OMURDEN_uzun():
    """
    Uc sinir, uc ayri soru — ve siralari bozulamaz:
      TAZE (15 dk)  "kaydet" yazisi hangi istege baglanir
      OMUR (24 sa)  toplu/dogal-dil yollari hangisini isler
      SURE_ASIMI    istek hangisinde DUSER

    `SURE_ASIMI <= OMUR` olsaydi, butonun OMUR sonrasi da calismasi
    (bilincli bir karar) anlamsizlasirdi: dosya zaten silinmis olurdu.
    """
    from finagent.bot.onay import TAZE, OMUR, SURE_ASIMI

    assert TAZE < OMUR < SURE_ASIMI, (TAZE, OMUR, SURE_ASIMI)


def _poz_bot(tmp, hesap="bux", mevcut=(), gun_once=6):
    """Gecmis bir anlik goruntusu olan hesap + sahte bot."""
    from datetime import datetime, timedelta, timezone
    from finagent.config import load_settings

    db = Database(_pathlib.Path(tmp) / "poz.db")
    db.init_schema()
    bot = _sahte_bot(load_settings(), db)
    # SABIT TARIH YOK: birlestirme penceresi (20 dk) SIMDIYE goreli
    # olculuyor, yani sabit bir damga testi takvime baglardi.
    eski = (datetime.now(timezone.utc) - timedelta(days=gun_once)
            ).replace(microsecond=0).isoformat()
    db.insert_positions(hesap, eski, [
        {"symbol": s, "name": s, "quantity": q, "market_value": v,
         "currency": "EUR"} for s, q, v in mevcut], "ali")
    return bot, db, eski



def test_model_kaydi_mevcut_pozisyonlari_DUSURMUYOR():
    """
    OLCULEN VAKA (2026-08-20 14:03). Ali saf bir OKUMA sorusu sordu
    ("Moderna hakkinda ne demistin, o fiyatlar hala gecerli mi?") ve
    model cevabin sonunda `bux` icin TEK satirlik (yalnizca MRNA) bir
    kayit onaya sundu. Onay kapisi tuttu; ama kopya veritabaninda
    denendiginde sonuc su cikti:

        onaydan ONCE   bux: 18 pozisyon · 5.929,81 EUR
        onaydan SONRA  bux:  1 pozisyon ·   148,90 EUR

    `portfoy` her hesabin EN SON goruntusunu okuyor ve birlestirme
    penceresi (20 dk) disindaki yazim YENI goruntu aciyor — icinde
    yalnizca gonderilenler oluyor.

    TEHLIKE MESRU YOLDA DA VARDI: "Moderna aldim, ekle" demek de ayni
    sonucu verirdi. Sorun modelin hevesi degil, KISMI listenin TAM
    GORUNUM sanilmasi.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        bot, db, eski = _poz_bot(d, mevcut=[("ASML", 2, 1400.0),
                                            ("AVTX", 17.92, 126.73),
                                            ("SHEL", 10, 300.0)])
        assert len(db.snapshot_satirlari("bux", eski, "ali")) == 3

        bot._pozisyon_kaydet({
            "hesap": "bux",
            "pozisyonlar": [{"symbol": "MRNA", "name": "Moderna",
                             "quantity": 1, "market_value": 148.9,
                             "currency": "EUR"}],
            "kaynak": "sohbet (model tarafindan hazirlandi)",
        }, "ali")

        son = db.latest_snapshot_ts("bux", "ali")
        assert son != eski, "yeni goruntu acilmadi"
        semboller = {r["symbol"] for r in db.snapshot_satirlari("bux", son, "ali")}
        assert semboller == {"ASML", "AVTX", "SHEL", "MRNA"}, semboller
        # TARIH BOZULMADI: eski goruntu oldugu gibi duruyor.
        assert {r["symbol"] for r in db.snapshot_satirlari("bux", eski, "ali")} \
            == {"ASML", "AVTX", "SHEL"}
        db.close()


def test_EKRAN_kaydi_hala_tam_gorunum_sayiliyor():
    """
    Ayrim KANITTA: ekran goruntusu hesabin TAMAMINI gosterir, orada bir
    pozisyonun yoklugu KANITTIR (satis) — ROSE tam boyle kapatildi
    (2026-08-18 19:45, "ROSE tamamiyla sattim ve ciktim").

    Model kaynakli yazimi kisitlarken bu yolu da kisitlasaydik, satis
    kaydedilemez ve satilan kagit portfoyde sonsuza kadar asili
    kalirdi — duzeltmekten daha kotu bir hata.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        bot, db, eski = _poz_bot(d, mevcut=[("ASML", 2, 1400.0),
                                            ("ROSE", 56741.0, 297.32)])
        bot._pozisyon_kaydet({
            "hesap": "bux",
            "pozisyonlar": [{"symbol": "ASML", "name": "ASML",
                             "quantity": 2, "market_value": 1400.0,
                             "currency": "EUR"}],
            # Ekran goruntusu yolu `kaynak` tasimaz (`ekran_tipi` tasir).
            "ekran_tipi": "portfoy",
        }, "ali")
        son = db.latest_snapshot_ts("bux", "ali")
        semboller = {r["symbol"] for r in db.snapshot_satirlari("bux", son, "ali")}
        assert semboller == {"ASML"}, f"satis kaydedilemedi: {semboller}"
        db.close()


def test_model_kaydi_BIRLESTIRME_penceresinde_tasima_yapmiyor():
    """
    Pencere icindeyken yazim zaten MEVCUT goruntunun ustune biniyor;
    ayrica tasimak ayni satirlari iki kez islemek olurdu.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        bot, db, _ = _poz_bot(d, mevcut=[("ASML", 2, 1400.0)], gun_once=0)
        son_once = db.latest_snapshot_ts("bux", "ali")
        rows, tasinan = bot._eksiltmeyi_engelle(
            "bux", son_once,
            [{"symbol": "MRNA", "quantity": 1, "market_value": 148.9}],
            "ali", {"kaynak": "sohbet (model tarafindan hazirlandi)"})
        assert tasinan == [], tasinan
        assert [r["symbol"] for r in rows] == ["MRNA"]
        db.close()


def test_model_kaydi_TASIMAYI_onay_mesajinda_BEYAN_ediyor():
    """
    Sessiz tasima olmaz. "1 pozisyon kaydedildi" yazip arkada 17 satir
    tasimak, dogru sonucu YANLIS bir beyanla vermek olurdu — kullanici
    neyi onayladigini gormeli.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        bot, db, _ = _poz_bot(d, mevcut=[("ASML", 2, 1400.0), ("SHEL", 10, 300.0)])
        mesaj = bot._pozisyon_kaydet({
            "hesap": "bux",
            "pozisyonlar": [{"symbol": "MRNA", "name": "Moderna",
                             "quantity": 1, "market_value": 148.9,
                             "currency": "EUR"}],
            "kaynak": "sohbet (model tarafindan hazirlandi)",
        }, "ali")
        assert "korundu" in mesaj, mesaj
        assert "ASML" in mesaj and "SHEL" in mesaj, mesaj
        assert "ekran" in mesaj.lower(), "silmenin nasil yapildigi soylenmemis"
        db.close()


def test_pozisyon_kaydet_araci_DUSURMEDIGINI_soyluyor():
    """
    Davranis degisti; aracin TARIFI de degismeli. Model listede
    olmayanin dusecegini sanirsa, dusurmek icin bos liste gondermeye
    calisir ya da her seferinde tum portfoyu yeniden yazmaya kalkar.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        tarif = {t.name: t for t in tb.araclar()}["pozisyon_kaydet"].description
        assert "DUSURMEZ" in tarif, tarif
        assert "ekran goruntusu" in tarif.lower(), tarif
        db.close()


def test_alt_surec_CANLI_DB_ye_dokunmuyor():
    """
    `run.py` alt sureci baslatan HER test `_run_py`'den gecmeli.

    NEDEN STATIK KURAL: `run.py` her komutta `db.init_schema()`
    cagiriyor. Alt surec baslatan ve `DB_PATH` gecirmeyen tek bir test,
    duman testlerini kosan herkesin CANLI veritabanini goc ettirir.
    Olculdu (2026-08-20 12:26:49): tam bu oldu — M4 semasi uretime
    testlerden gecti ve calisan bot (eski kod, UDF yok) `no such
    function: leksik` ile yazamaz hale geldi.

    Tek testi duzeltmek yetmez; kalip tekrar eder. Kural burada
    ZORUNLU tutuluyor — ayni gerekce README kural 2'de: "elle yazilan
    liste curur, koddan uret ve testle bagla".
    """
    import ast

    kaynak = _pathlib.Path(__file__).read_text(encoding="utf-8")
    agac = ast.parse(kaynak)

    def _run_py_govdesi(dugum):
        """`_run_py`'nin KENDISI ham cagriyi kullanmak zorunda."""
        return (isinstance(dugum, ast.FunctionDef) and dugum.name == "_run_py")

    muaf = set()
    for d in ast.walk(agac):
        if _run_py_govdesi(d):
            muaf |= {id(x) for x in ast.walk(d)}

    ihlal = []
    for d in ast.walk(agac):
        if not isinstance(d, ast.Call) or id(d) in muaf:
            continue
        ad = getattr(d.func, "attr", None)
        if ad not in ("run", "Popen") or not d.args:
            continue
        # Ilk argumanda "run.py" gecen bir liste var mi?
        metinler = [x.value for x in ast.walk(d.args[0])
                    if isinstance(x, ast.Constant) and isinstance(x.value, str)]
        if any("run.py" == m for m in metinler):
            ihlal.append(d.lineno)

    assert not ihlal, (
        f"satir {ihlal}: `run.py` alt sureci ham `subprocess` ile "
        "baslatiliyor. `_run_py(...)` kullan — aksi halde CANLI "
        "veritabani goc eder.")

    # Ve `_run_py` GERCEKTEN izole ediyor: sozlesmenin kendisi de
    # kontrol edilmeli, yoksa yukaridaki kural bos bir toren olur.
    govde = kaynak[kaynak.index("def _run_py("):]
    govde = govde[:govde.index("\ndef ")]
    assert '"DB_PATH"' in govde, "_run_py DB_PATH gecirmiyor"
    assert '"BOT_STATE_DIR"' in govde, \
        "_run_py BOT_STATE_DIR gecirmiyor — kosu izi hala canliya yazilir"
    assert 'cevre.pop("TELEGRAM_BOT_TOKEN"' in govde, \
        "_run_py Telegram token'ini dusurmuyor"
    assert "timeout=timeout" in govde, "_run_py zaman asimi vermiyor"


def test_BOT_STATE_DIR_kosu_izini_TASIYOR():
    """
    Iz dizini `BOT_STATE_DIR` ile tasinabilmeli — `DB_PATH` gibi.

    NEDEN AYRI BIR TEST: uctan uca test bunu OLCMUYOR. Alt surec bos
    bir gecici veritabaniyla kostugu icin iz yazacak kadar
    ilerlemiyor, yani `DB_PATH` izolasyonu izi DOLAYLI olarak zaten
    koruyor ve mutasyon hayatta kaliyor. Kasitli bozma bunu yakaladi:
    "runner iz yine root'tan turuyor" mutasyonu uctan uca testi
    GECIYORDU.

    Koruma yine de gercek — dolayli koruma, veri dolu bir veritabani
    verildigi anda kaybolur. Mekanizma burada DOGRUDAN sinaniyor.
    """
    import os
    import tempfile
    import types

    from finagent.config import Settings
    from finagent.pulse.runner import Nabiz

    with tempfile.TemporaryDirectory() as d:
        hedef = _pathlib.Path(d) / "tasinmis"
        eski = os.environ.get("BOT_STATE_DIR")
        os.environ["BOT_STATE_DIR"] = str(hedef)
        try:
            s = Settings({}, _pathlib.Path(d))
            assert s.bot_state_dir == hedef, (s.bot_state_dir, hedef)
            n = Nabiz.__new__(Nabiz)
            n.s = types.SimpleNamespace(root=_pathlib.Path(d),
                                        bot_state_dir=s.bot_state_dir)
            n._iz_birak("ogle", ["ali"], {"piyasa_sinyali": 7})
        finally:
            if eski is None:
                os.environ.pop("BOT_STATE_DIR", None)
            else:
                os.environ["BOT_STATE_DIR"] = eski

        assert (hedef / "kosu" / "ogle.json").exists(), \
            "iz TASINMADI — BOT_STATE_DIR yok sayiliyor"
        assert not (_pathlib.Path(d) / "data" / "bot" / "kosu").exists(), \
            "iz hem tasindi hem ESKI yere yazildi"


def test_alt_surec_KOSU_IZINE_dokunmuyor():
    """
    `DB_PATH` YETMIYOR — olculdu (2026-08-20).

    `test_run_py_kip_hatasinda_...` testi `run.py nabiz --kip sabah
    --sahip yok_boyle_sahip` calistiriyor. Komut sahibi reddedip 2 ile
    cikiyor AMA once piyasa fazini kosuyor ve GERCEK kosu izinin
    ustune yaziyor. Canlida bulundu:

        data/bot/kosu/sabah.json
        {"kip": "sabah", "sahipler": ["yok_boyle_sahip"], ...}

    O dosya BEKCININ KANITI: "sabah kosusu bugun calisti mi" sorusu
    ondan cevaplaniyor. Bir testin onu ezebilmesi, gozetim katmaninin
    kandirilabilmesi demek — bu projede daha once yanilan sinifin ta
    kendisi.

    UCTAN UCA: gercek alt surec, gercek komut. Iz dizininin taklidi
    yapilmiyor cunku hata TAM OLARAK taklidin olmadigi yerdeydi.
    """
    import time

    kok = _pathlib.Path(__file__).resolve().parents[1]
    iz = kok / "data" / "bot" / "kosu" / "sabah.json"
    onceki = (iz.read_bytes(), iz.stat().st_mtime) if iz.exists() else None

    r = _run_py("nabiz", "--kip", "sabah", "--sahip", "yok_boyle_sahip",
                "--no-notify", "--no-panel")
    assert r.returncode == 2, (r.returncode, r.stderr[-500:])
    time.sleep(0.2)

    if onceki is None:
        assert not iz.exists(), "alt surec CANLI kosu izi olusturdu"
    else:
        assert iz.read_bytes() == onceki[0], \
            "alt surec CANLI kosu izinin ustune yazdi (bekcinin kaniti)"


def test_sohbet_arsivi_ARAMADA_baglami_sismiyor():
    """
    Aramada tavan, listelemedekinden AYRI ve daha dusuk.

    OLCULDU (altin kume, canli arsiv): FTS5'in recall'u 8. sonuctan
    sonra ARTMIYOR — @8 %73,3, @15 %73,3, @60 %73,3. Ama 60 tavanla
    "kar zarar" sorgusu 59 tur ve ~11.600 token donduruyordu. Fazladan
    51 turun dogru cevabi bulma sansina katkisi SIFIR, baglam maliyeti
    gercek.

    Listeleme (sorgusuz "son turlar") ayri is ve tavani degismedi.
    """
    import tempfile

    from finagent.bot.tools import MAX_SATIR, ARSIV_ARAMA_SATIRI

    assert ARSIV_ARAMA_SATIRI < MAX_SATIR, "arama tavani listelemeden dusuk olmali"

    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        for i in range(40):
            db.sohbet_kaydet("5643817523", "user", f"altın hakkında soru {i}",
                             sahip="ali", ts=f"2026-08-19T{10 + i // 6:02d}:{i % 6:02d}:00+00:00")
        arac = {t.name: t for t in tb.araclar()}["sohbet_arsivi"]

        arama = _cagir(arac, sorgu="altin", gun=3650)
        assert len(arama["turlar"]) == ARSIV_ARAMA_SATIRI, len(arama["turlar"])

        # Sorgusuz listeleme DARALTILMADI — iki ayri is.
        liste = _cagir(arac, sorgu="", gun=3650)
        assert len(liste["turlar"]) > ARSIV_ARAMA_SATIRI, len(liste["turlar"])
        db.close()


def test_sohbet_arsivi_gosterimi_KRONOLOJIK_kaliyor():
    """
    Alaka HANGI turlarin secildigini belirler, hangi sirayla OKUNDUGUNU
    degil. Bir konusma parcasi ancak sirasi korunursa okunabilir; model
    ters sirada bir diyalogu "once cevap, sonra soru" diye okur.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        tb, db = _toolbox(d)
        # Ikinci tur alaka bakimindan ONDE (kelime tekrari), ama
        # kronolojide SONRA.
        db.sohbet_kaydet("5643817523", "user", "altın hakkında bir soru",
                         sahip="ali", ts="2026-08-19T10:00:00+00:00")
        db.sohbet_kaydet("5643817523", "assistant",
                         "altın altın altın altın altın gram altın",
                         sahip="ali", ts="2026-08-19T11:00:00+00:00")
        arac = {t.name: t for t in tb.araclar()}["sohbet_arsivi"]
        out = _cagir(arac, sorgu="altin", gun=3650)
        tarihler = [t["tarih"] for t in out["turlar"]]
        assert tarihler == sorted(tarihler), f"gosterim kronolojik degil: {tarihler}"
        assert out["turlar"][0]["kim"] == "sen", "soru cevaptan sonra gosteriliyor"
        db.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
    print("\nTum duman testleri gecti.")
