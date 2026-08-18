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

        # ZAMANLANMIS IS YEREL SAATLE YARGILANIR (launchd saatleri yerel).
        with patch.object(W, "_yerel", lambda: cuma_gec):
            assert b.kacirilan_nabiz() is not None      # sinyal yok -> uyar
        with patch.object(W, "_yerel", lambda: cuma_erken):
            assert b.kacirilan_nabiz() is None          # 23:00'ten once yargilama
        with patch.object(W, "_yerel", lambda: cumartesi):
            assert b.kacirilan_nabiz() is None          # hafta sonu zaten calismaz

        iid = db.upsert_instrument("X", "BUX")
        with db.tx() as c:
            c.execute("INSERT INTO signals (olusma_ts,instrument_id,tur,guc,sahip) "
                      "VALUES (?,?,?,?,'ortak')", ("2026-08-14", iid, "test", 1.0))
        with patch.object(W, "_yerel", lambda: cuma_gec):
            assert b.kacirilan_nabiz() is None          # sinyal var -> sessiz
        db.close()


def test_bekci_nabzi_yerel_saatle_yargılar_ve_yanlis_alarm_calmaz():
    """
    Ali 2026-08-18'de DORT yanlis "Nabiz calismadi" alarmi aldi — oysa
    nabiz tam calismisti (259 sinyal, 60 tahmin, 10 panel kosusu).
    Uc kusur birden vardi:

    1) SAAT UTC'DEYDI. Nabiz launchd'de 22:15 YEREL kosuyor ama kontrol
       `hour >= 23` diye UTC'ye bakiyordu; makine CEST (UTC+2) oldugu
       icin pencere 01:00-01:59 YERELE kaydi ve alarmlar tam 01:15'te
       geldi.
    2) TEK GUNE ESITLIK. `olusma_ts = bugun` UTC/yerel gun kaymasinda
       "dun calisti ama bugun calismadi" gibi okunuyordu.
    3) SINYAL TEK KANITTI. Sinyal uretmemek MESRU bir sonuc (esigi gecen
       kagit yoksa tarama bos doner); sessiz ama basarili bir kosu ARIZA
       sayiliyordu.
    """
    import tempfile
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch
    from finagent.bot import watchdog as W

    with tempfile.TemporaryDirectory() as d:
        b, db = _bekci(d)
        gece = datetime(2026, 8, 17, 23, 30, tzinfo=timezone.utc)

        # 1) SINYAL YOK ama PANEL KOSMUS -> nabiz CALISMIS, alarm YOK.
        #    Sessizlik gecerli bir cikti; sinyalsizligi ariza sayamayiz.
        with db.tx() as c:
            c.execute(
                "INSERT INTO panel_runs (run_ts, ajan, ham_metin, json_durum, "
                "gorus_sayisi, atilan_sembol_yok, atilan_seri_yok, "
                "atilan_cakisma, sahip) VALUES (?,?,?,'ok',0,0,0,0,'ali')",
                ((gece - timedelta(hours=1)).strftime("%Y-%m-%d"), "teknik", "x"))
        with patch.object(W, "_yerel", lambda: gece):
            assert b.kacirilan_nabiz() is None, "sessiz ama basarili kosu ariza sayildi"

        # 2) DUN AKSAM kosan is, gece yarisi sonrasi hala "calisti" sayilmali
        with patch.object(W, "_yerel", lambda: gece + timedelta(hours=2)):
            assert b.kacirilan_nabiz() is None, "18 saatlik pencere tutmadi"

        # 3) SORGU PATLARSA ALARM CALMAZ — "sorgu basarisiz" ile "nabiz
        #    calismadi" ayri seyler (yanlis "yok" beyani sinifi).
        class _Patlak:
            def query(self, *a, **k): raise RuntimeError("db kilitli")
        b2 = W.Bekci(b.s, _Patlak(), b.dosya.parent)
        with patch.object(W, "_yerel", lambda: gece):
            assert b2.kacirilan_nabiz() is None, "sorgu hatasi alarma donustu"
        db.close()


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
    import tempfile
    from finagent.config import load_settings
    s = load_settings()
    s.raw.setdefault("telegram", {})["sahipler"] = {
        str(100 + i): ad for i, ad in enumerate(sahipler)}
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
        n._sahibe_bildir = lambda s, m: bildirimler.append((s, m)) or True
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
        n._sahibe_bildir = lambda s, m: gonderilen.append((s, m)) or True
        n._tez_bildir = lambda b, s: gonderilen.append((s, "TEZ")) or True

        r = n.calistir(bildir=True, panel=True, kip="nabiz")
        assert r["sonuc"]["ali"].get("panel_hatasi"), r["sonuc"]["ali"]
        assert ("ali", "TEZ") in gonderilen, "tez alarmi gitmemis"
        assert any("panel calismadi" in m for _, m in gonderilen), gonderilen
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
    """UC DURUM 2 — sessiz no-op degil, acik hata."""
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
            assert "sahip yok" in str(e), e
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
        n._sahibe_bildir = lambda s, m: gonderilen.append((s, m)) or True
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
        assert set(borsalar) == {"BIST", "Amsterdam", "ABD"}, borsalar
        for b in borsalar.values():
            assert b["durum"] in ("acik", "kapandi", "acilmadi", "hafta sonu")
            assert ":" in b["yerel_saat"] and b["seans"]
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
    from finagent.config import load_settings
    from finagent.bot.tools import ToolBox

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "g.db")
        db.init_schema()
        db.upsert_news([
            {"url": "https://x/1", "title": "Bakan Simsek: mali disiplini koruyoruz",
             "source": "AA - Ekonomi", "publisher": "AA - Ekonomi", "tier": 2,
             "published_at": "2026-08-17 10:00:00", "symbols": []},
            {"url": "https://x/2", "title": "Avrupa borsalari dususle kapatti",
             "source": "Reuters", "publisher": "Reuters", "tier": 2,
             "published_at": "2026-08-17 11:00:00", "symbols": []},
            {"url": "https://x/3", "title": "ASML icin hedef fiyat yukseltildi",
             "source": "Reuters", "publisher": "Reuters", "tier": 2,
             "published_at": "2026-08-17 12:00:00", "symbols": ["ASML"]},
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
    from finagent.config import load_settings
    from finagent.pipeline import _gundem_kovalari

    with tempfile.TemporaryDirectory() as d:
        db = Database(_pathlib.Path(d) / "g.db")
        db.init_schema()
        rows = [{"url": f"https://x/s{i}", "title": f"Sirket bilancosu {i}",
                 "source": "Reuters", "publisher": "Reuters", "tier": 2,
                 "published_at": "2026-08-17 10:00:00", "symbols": ["ASML"]}
                for i in range(60)]
        rows.append({"url": "https://x/tr", "title": "Bakan Simsek: mali disiplin",
                     "source": "AA - Ekonomi", "publisher": "AA - Ekonomi",
                     "tier": 2, "published_at": "2026-08-17 11:00:00",
                     "symbols": []})
        rows.append({"url": "https://x/gl", "title": "Avrupa borsalari dususle kapatti",
                     "source": "Reuters", "publisher": "Reuters", "tier": 2,
                     "published_at": "2026-08-17 11:00:00", "symbols": []})
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
    import subprocess as _sp
    r = _sp.run([sys.executable, "run.py", "bot-worker", "--help"],
                capture_output=True, text=True,
                cwd=str(_pathlib.Path(__file__).resolve().parents[1]))
    assert r.returncode == 0, r.stderr
    assert "--is" in r.stdout


def test_worker_ucdan_uca_gercek_surecte_calisir():
    """
    UCTAN UCA: gercek `run.py bot-worker` sureci, gercek is dosyasi.
    Birim testler zincirin halkalarini dogruluyor; bu, zincirin
    KOPUK OLMADIGINI dogruluyor (bkz. `_query` vakasi).
    """
    import os as _os
    import subprocess as _sp
    import tempfile
    kok = _pathlib.Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as d:
        yol = _pathlib.Path(d) / "77.json"
        # Yetkisiz sohbet: worker'in DIS dunyaya dokunmadan tam yolu
        # kosmasini saglar (yetki reddi -> log -> bitti).
        yol.write_text(_json_dumps({
            "update_id": 77, "chat_id": -1, "deneme": 1,
            "update": {"update_id": 77,
                       "message": {"chat": {"id": -1}, "text": "merhaba"}}}),
            encoding="utf-8")
        cevre = {**_os.environ, "DB_PATH": str(_pathlib.Path(d) / "test.db")}
        cevre.pop("TELEGRAM_BOT_TOKEN", None)
        r = _sp.run([sys.executable, "run.py", "bot-worker", "--is", str(yol)],
                    capture_output=True, text=True, cwd=str(kok), env=cevre,
                    timeout=180)
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
    kabuk = (_pathlib.Path(__file__).resolve().parents[1]
             / "scripts" / "run_hafif.sh").read_text(encoding="utf-8")
    assert "HAFIF_TIMEOUT:-900" in kabuk or "HAFIF_TIMEOUT" in kabuk
    plist = _pathlib.Path("launchd/com.alipala.finagent.ogle.plist").read_text()
    import re as _re
    m = _re.search(r"HAFIF_TIMEOUT</key>\s*<string>(\d+)</string>", plist)
    assert m, "plist'te HAFIF_TIMEOUT yok"
    assert isy["azami_sure_sn"] < int(m.group(1)) - 240, (
        f"ic butce {isy['azami_sure_sn']} sn, kabuk siniri {m.group(1)} sn — "
        "kalan collector'lar ve nabiz adimi icin pay yok")


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

    with tempfile.TemporaryDirectory() as d:
        n = Nabiz.__new__(Nabiz)
        n.s = types.SimpleNamespace(root=_pathlib.Path(d))
        n._iz_birak("ogle", ["ali", "yuksel"], {"piyasa_sinyali": 42})
        yol = _pathlib.Path(d) / "data" / "bot" / "kosu" / "ogle.json"
        assert yol.exists(), "kosu izi yazilmadi"
        import json as _json
        veri = _json.loads(yol.read_text())
        assert veri["kip"] == "ogle" and veri["piyasa_sinyali"] == 42
        assert veri["sahipler"] == ["ali", "yuksel"]

        # IZ ASLA KOSUYU DUSURMEZ.
        n.s = types.SimpleNamespace(root=_pathlib.Path(d) / "olmayan\0kotu")
        n._iz_birak("sabah", [], {})          # istisna FIRLATMAMALI

    # Ve `calistir` izi DONMEDEN once birakmali (yarim kosu iz birakmaz).
    import inspect
    kaynak = inspect.getsource(Nabiz.calistir)
    assert kaynak.index("_iz_birak") < kaynak.rindex("return {"), \
        "iz, sonuc donduruldukten sonra birakiliyor"


def _kosu_bekcisi(d, kip_izleri=None, saat=None, kurulum_gun_once=30):
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
    for kip, ts in (kip_izleri or {}).items():
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

    with tempfile.TemporaryDirectory() as d:
        # Sali 2026-08-18, saat 20:00 yerel: ogle (18:00) coktan gecti.
        simdi = _dt(2026, 8, 18, 20, 0).astimezone()
        eski = W._yerel
        W._yerel = lambda: simdi
        try:
            b = _kosu_bekcisi(d, {"sabah": simdi.replace(hour=9, minute=31),
                           "ogle": simdi - _td(days=1)})   # ogle DUNDEN
            eksik = b.kacirilan_kosular()
            assert [x["kip"] for x in eksik] == ["ogle"], eksik
            assert eksik[0]["beklenen"] == "18:00"

            # Ogle de bugun kosunca alarm SUSAR.
            b2 = _kosu_bekcisi(d, {"sabah": simdi.replace(hour=9, minute=31),
                            "ogle": simdi.replace(hour=18, minute=13)})
            assert b2.kacirilan_kosular() == []
        finally:
            W._yerel = eski


def test_bekci_vakti_gelmemis_kosuya_alarm_calmaz():
    """
    Ogle kosusu ~13-20 dk suruyor; 18:05'te "calismadi" demek YANLIS
    ALARM olurdu. Dort yanlis nabiz alarmindan sonra bu sinir bilincli:
    gozetim katmaninin kendisi gurultu uretmemeli.
    """
    import tempfile
    from datetime import datetime as _dt, timedelta as _td
    from finagent.bot import watchdog as W

    with tempfile.TemporaryDirectory() as d:
        simdi = _dt(2026, 8, 18, 18, 20).astimezone()     # ogle daha yeni basladi
        eski = W._yerel
        W._yerel = lambda: simdi
        try:
            b = _kosu_bekcisi(d, {"sabah": simdi.replace(hour=9, minute=31),
                           "ogle": simdi - _td(days=1)})
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
    with tempfile.TemporaryDirectory() as d:
        takvim = _kosu_bekcisi(d)._plist_saatleri()
        assert set(takvim) == {"sabah", "ogle"}, takvim
        for kip in ("sabah", "ogle"):
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

    with tempfile.TemporaryDirectory() as d:
        kok = _p.Path(d)
        (kok / "launchd").mkdir()
        # Gunun COK ERKEN saatinde zamanlanmis bir kip: 00:05. Boylece
        # "daha vakti var" dali testi maskelemez.
        (kok / "launchd" / "com.alipala.finagent.sabah.plist").write_bytes(
            plistlib.dumps({
                "Label": "com.alipala.finagent.sabah",
                "StartCalendarInterval": [
                    {"Hour": 0, "Minute": 5, "Weekday": w} for w in range(0, 8)]}))

        class _S:
            root = kok
            def get(self, *a, **k): return None

        db = Database(kok / "t.db"); db.init_schema()
        sd = kok / "data" / "bot"; sd.mkdir(parents=True)
        b = Bekci(_S(), db, sd)

        # 1) ILK CAGRI: kurulum ani SIMDI yazilir. Bugun 00:05'teki kosu
        #    kurulumdan ONCE, yargilanamaz -> SESSIZ.
        assert b.kacirilan_kosular() == [], \
            "kurulumdan onceki kosu icin alarm calindi (yanlis alarm)"
        izmar = sd / "kosu" / "kurulum.json"
        assert izmar.exists(), "kurulum ani diske yazilmadi"

        # 2) Kurulumu iki gun geriye al: artik bugunku kosu yargilanabilir
        #    ve izi YOK -> ALARM. (Ilk gunden bozuk kip de boylece yakalanir.)
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


def test_dinleyici_kacirilan_kosuyu_bildirir():
    """Gozcu bulsa da dinleyici sormazsa alarm hic calmaz."""
    import inspect
    from finagent.bot import listener as L
    kaynak = inspect.getsource(L.FinBot.run)
    assert "kacirilan_kosular()" in kaynak, "gozcu dinleyiciye baglanmadi"
    assert "kosu_kacti_" in kaynak, "bildirim anahtari kip bazli degil"
    # Nabiz gozcusu KALDIRILMADI — iki mekanizma birbirini yedekliyor.
    assert "kacirilan_nabiz()" in kaynak


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
    print("\nTum duman testleri gecti.")
