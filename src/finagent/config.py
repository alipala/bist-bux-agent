"""Ayar yukleme: config/*.yaml + .env"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def _deep_get(d: dict, path: str, default: Any = None) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


@dataclass
class Settings:
    raw: dict = field(default_factory=dict)
    selectors: dict = field(default_factory=dict)
    root: Path = ROOT

    # --- kisayollar ---
    def get(self, path: str, default: Any = None) -> Any:
        return _deep_get(self.raw, path, default)

    def sel(self, path: str, default: Any = None) -> Any:
        return _deep_get(self.selectors, path, default)

    @property
    def db_path(self) -> Path:
        p = os.getenv("DB_PATH") or "data/finagent.db"
        return self._resolve(p)

    @property
    def bot_state_dir(self) -> Path:
        """
        Botun DURUM dizini — kosu izleri, bekleyen onaylar, kuyruk.

        `DB_PATH` gibi ortam degiskeniyle tasinabilir olmasi ZORUNLU:
        icindeki `kosu/<kip>.json` dosyalari BEKCININ KANITI. Olculdu
        (2026-08-20): bir duman testi `run.py nabiz --kip sabah --sahip
        yok_boyle_sahip` calistirdi, komut sahibi reddedip 2 ile cikti
        AMA once piyasa fazini kosup GERCEK `sabah.json` izinin ustune
        yazdi:

            {"kip": "sabah", "sahipler": ["yok_boyle_sahip"], ...}

        `DB_PATH` verilmisti, yani veritabani izoleydi — iz dizini
        degildi. Gozetim katmanini bir testin ezebilmesi, bu projede
        daha once yanilan sinifin ta kendisi: "gozetim katmaninin
        kendisi gozetilemiyordu".
        """
        p = os.getenv("BOT_STATE_DIR")
        return self._resolve(p) if p else self.root / "data" / "bot"

    @property
    def yedek_dizini(self) -> Path:
        """
        Yedeklerin yazilacagi dizin.

        `DB_PATH`/`BOT_STATE_DIR` ile AYNI GEREKCE ile ortam
        degiskeninden tasinabilir: izole bir veritabaniyla kosan bir
        test ya da e2e senaryosu, GERCEK yedek dizinine yazarsa o
        gunun yedegi bir TEST veritabaninin yedegiyle degistirilir —
        ve dosya adi ayni oldugu icin bu, dogru yedek varmis gibi
        gorunur. Bu projede ayni sinif iki kez yasandi: bir duman
        testi canli veritabanini goc ettirdi, bir digeri bekcinin
        kosu izini ezdi.
        """
        p = os.getenv("YEDEK_DIZIN")
        if p:
            return self._resolve(p)
        ham = str((self.get("yedek") or {}).get("dizin") or "data/yedek")
        return self._resolve(ham)

    @property
    def profile_dir(self) -> Path:
        p = os.getenv("BROWSER_PROFILE_DIR") or self.get("browser.profile_dir", ".browser_profile")
        return self._resolve(p)

    @property
    def report_dir(self) -> Path:
        return self._resolve(self.get("report.output_dir", "reports"))

    @property
    def discovery_dir(self) -> Path:
        return self._resolve("discovery")

    @property
    def bist_watchlist(self) -> list[str]:
        return [s.strip().upper() for s in (self.get("watchlist.bist") or [])]

    @property
    def bux_watchlist(self) -> list[str]:
        return [s.strip().upper() for s in (self.get("watchlist.bux") or [])]

    def source_enabled(self, name: str) -> bool:
        return bool(self.get(f"sources.{name}.enabled", False))


    # --- sahipler (cok kullanicili katman) ---
    @property
    def sahipler(self) -> dict[str, str]:
        """
        chat_id (metin) -> sahip adi.

        Esleme bossa .env'deki TELEGRAM_CHAT_ID tek sahip sayilir; tek
        kullanicili kurulum yapilandirma degisikligi GEREKTIRMEZ.
        """
        ham = self.get("telegram.sahipler") or {}
        esleme = {str(k).strip(): str(v).strip().lower()
                  for k, v in ham.items() if str(v).strip()}
        if esleme:
            return esleme
        tek = os.getenv("TELEGRAM_CHAT_ID")
        return {str(tek): "ali"} if tek else {}

    def sahip_bul(self, chat_id) -> str | None:
        """
        Sohbetin sahibi. Bulunamazsa None — VARSAYILANA DUSMEZ.

        Sessiz varsayilan bu isin tek gercek tehlikesi: yanlis kisinin
        portfoyune yazmak. Cagiran taraf None'i acik hataya cevirir.
        """
        return self.sahipler.get(str(chat_id))

    def gorunen_ad(self, sahip: str | None) -> str:
        """
        Sahibin KULLANICIYA GOSTERILEN adi.

        `sahip` bir veritabani anahtaridir: kucuk harf, ASCII, sabit
        ("yuksel"). Ekranda "Yuksel" yazmak kabul edilebilir ama "Yüksel"
        dogrusudur ve birinin adini her mesajda yanlis yazmak kucuk ama
        surekli bir kusurdur. Bu yuzden istege bagli bir GORUNTU esleme
        var; yoksa anahtarin bas harfi buyutulur.

        Bu ikinci bir "dogruluk kaynagi" DEGIL: yetkilendirme ve
        yonlendirme yalnizca `sahipler`den turuyor, buradaki eksik bir
        satir hicbir seyi bozmaz — sadece etiketi sadelestirir.
        """
        if not sahip:
            return "Kullanici"
        ham = self.get("telegram.gorunen_ad") or {}
        ad = {str(k).strip().lower(): str(v).strip()
              for k, v in ham.items()}.get(str(sahip).strip().lower())
        return ad or str(sahip).strip().capitalize()

    def sahip_chatleri(self, sahip: str) -> list[str]:
        """Bir sahibin sohbetleri — bildirim buradan yonlendirilir."""
        s = str(sahip).strip().lower()
        return [c for c, v in self.sahipler.items() if v == s]

    @property
    def sahip_listesi(self) -> list[str]:
        """Nabiz dongusunun uzerinde donecegi sahipler, sirali ve tekil."""
        return sorted(set(self.sahipler.values()))

    # --- ritim (gunun kosulari) ---------------------------------------
    #
    # Hangi kip ne toplar, panel calistirir mi, kime gider — UCU DE
    # AYARDA. Kodda `if kip in ("sabah", "ogle")` gibi bir demet
    # kalirsa bu katman uygulanmamis demektir (ritim v2 §5, ilk tuzak).
    RITIM_ZORUNLU: tuple[str, ...] = (
        "kaynaklar", "panel", "panel_butce_sn", "kabuk_butce_sn", "alicilar")

    @property
    def ritim_kipleri(self) -> list[str]:
        """Tanimli kip adlari, sirali. Bekci ve testler bunu okur."""
        return sorted((self.get("ritim.kipler") or {}).keys())

    # `arama.gomme` icin zorunlu alanlar. Varsayilan YOK — bir gomme
    # ayarinin sessizce varsayilana dusmesi, YANLIS BIR VEKTOR UZAYINDA
    # arama yapmak demektir ve bu bos sonuctan kotudur: makul gorunen
    # ama alakasiz turlar doner ve hicbir sey yanlis oldugunu soylemez.
    GOMME_ZORUNLU = ("enabled", "url", "model", "boyut", "timeout_sn", "batch")

    def gomme_ayari(self) -> dict:
        """
        `arama.gomme` — DOGRULANMIS.

        `ritim_kip` ile ayni disiplin ve ayni gerekce: okuyan cok
        (indeksleme, arama, olcum kosumu) ve cagiran tarafa birakilan
        dogrulama, cagiran sayisi kadar farkli davranis uretir.
        """
        ayar = self.get("arama.gomme")
        if not isinstance(ayar, dict) or not ayar:
            raise ValueError(
                "gomme tanimli degil: config/settings.yaml -> arama.gomme")
        eksik = [k for k in self.GOMME_ZORUNLU if k not in ayar]
        if eksik:
            raise ValueError(
                f"arama.gomme eksik alan: {', '.join(eksik)}. "
                "Varsayilan YOK — her alan acikca yazilmali.")
        if not isinstance(ayar["enabled"], bool):
            raise ValueError(
                f"arama.gomme: `enabled` bool olmali, {ayar['enabled']!r} verilmis")
        for alan in ("url", "model"):
            if not isinstance(ayar[alan], str) or not ayar[alan].strip():
                raise ValueError(
                    f"arama.gomme: `{alan}` bos olmayan metin olmali, "
                    f"{ayar[alan]!r} verilmis")
        for alan in ("boyut", "timeout_sn", "batch"):
            deger = ayar[alan]
            if not isinstance(deger, (int, float)) or isinstance(deger, bool) \
                    or deger <= 0:
                raise ValueError(
                    f"arama.gomme: `{alan}` pozitif sayi olmali, "
                    f"{deger!r} verilmis")
        return dict(ayar)

    # `yedek` icin zorunlu alanlar. Varsayilan YOK — bir yedekleme
    # ayarinin sessizce varsayilana dusmesi, yedegin NEREYE gittigini
    # ve KAC GUN tutuldugunu kimsenin bilmemesi demek. Yedekte en kotu
    # ariza sessiz olanidir: aldigini sanirsin, yoktur.
    YEDEK_ZORUNLU = ("enabled", "dizin", "gun", "asgari_bos_gb")

    def yedek_ayari(self) -> dict:
        """
        `yedek` — DOGRULANMIS. (`gomme_ayari` ile ayni disiplin.)
        """
        ayar = self.get("yedek")
        if not isinstance(ayar, dict) or not ayar:
            raise ValueError(
                "yedek tanimli degil: config/settings.yaml -> yedek")
        eksik = [k for k in self.YEDEK_ZORUNLU if k not in ayar]
        if eksik:
            raise ValueError(
                f"yedek eksik alan: {', '.join(eksik)}. "
                "Varsayilan YOK — her alan acikca yazilmali.")
        if not isinstance(ayar["enabled"], bool):
            raise ValueError(
                f"yedek: `enabled` bool olmali, {ayar['enabled']!r} verilmis")
        if not isinstance(ayar["dizin"], str) or not ayar["dizin"].strip():
            raise ValueError(
                f"yedek: `dizin` bos olmayan metin olmali, "
                f"{ayar['dizin']!r} verilmis")
        for alan in ("gun", "asgari_bos_gb"):
            deger = ayar[alan]
            if not isinstance(deger, (int, float)) or isinstance(deger, bool) \
                    or deger <= 0:
                raise ValueError(
                    f"yedek: `{alan}` pozitif sayi olmali, {deger!r} verilmis")
        return dict(ayar)

    def ritim_kip(self, kip: str) -> dict:
        """
        Bir kipin ayari — DOGRULANMIS.

        VARSAYILANA DUSMEZ. Bilinmeyen bir kip icin sessizce "nabiz"
        ayarini dondurmek, yanlis kaynaklari toplayip yanlis kisilere
        mesaj atmak demektir; sessiz varsayilan bu projenin tekrar eden
        kusur sinifi (bkz. `sahip_bul`).

        Dogrulama BURADA cunku okuyan cok: run.py, Nabiz, run_kosu.sh,
        bekci. Cagiran tarafa birakilan dogrulama, cagiran sayisi
        kadar farkli davranis uretir.
        """
        kipler = self.get("ritim.kipler") or {}
        if not kipler:
            raise ValueError(
                "ritim tanimli degil: config/settings.yaml -> ritim.kipler")
        ad = str(kip or "").strip()
        if ad not in kipler:
            raise ValueError(
                f"tanimsiz kip: {ad!r}. Tanimli olanlar: "
                f"{', '.join(sorted(kipler))}")
        ayar = kipler[ad]
        if not isinstance(ayar, dict):
            raise ValueError(f"kip {ad!r} bir sozluk degil: {type(ayar).__name__}")

        eksik = [k for k in self.RITIM_ZORUNLU if k not in ayar]
        if eksik:
            raise ValueError(
                f"kip {ad!r} eksik alan: {', '.join(eksik)}. "
                "Varsayilan YOK — her alan acikca yazilmali.")

        if not isinstance(ayar["panel"], bool):
            raise ValueError(
                f"kip {ad!r}: `panel` bool olmali, "
                f"{ayar['panel']!r} verilmis")
        if not isinstance(ayar["kaynaklar"], list):
            raise ValueError(f"kip {ad!r}: `kaynaklar` liste olmali")

        for alan in ("panel_butce_sn", "kabuk_butce_sn"):
            deger = ayar[alan]
            if not isinstance(deger, (int, float)) or isinstance(deger, bool) \
                    or deger <= 0:
                raise ValueError(
                    f"kip {ad!r}: `{alan}` pozitif sayi olmali, "
                    f"{deger!r} verilmis")

        # KABUK BUTCESI PANEL BUTCESINDEN BUYUK OLMALI. Kucuk olsaydi
        # kabuk, panel butcesi devreye girmeden once sureci oldururdu ve
        # iki kademeli korumanin ust kademesi hic calismazdi — "kalan
        # sahibin paneli atlandi ve KENDISINE SOYLENDI" yolu olurdu.
        if ayar["kabuk_butce_sn"] <= ayar["panel_butce_sn"]:
            raise ValueError(
                f"kip {ad!r}: kabuk_butce_sn ({ayar['kabuk_butce_sn']}) "
                f"panel_butce_sn'den ({ayar['panel_butce_sn']}) buyuk "
                "olmali; aksi halde panel butcesi hic devreye giremez")

        # IS YATIRIM KURALI — yalnizca o kaynak bu kipteyse baglar.
        # Collector kendi ic butcesinde (`azami_sure_sn`) duzgunce
        # duruyor; kabuk ondan once oldururse o duzgun durus HIC
        # gerceklesmez ve kismi veri de kaydedilmez.
        if "isyatirim" in ayar["kaynaklar"]:
            ic = float(self.get("sources.isyatirim.azami_sure_sn", 780) or 0)
            if ayar["kabuk_butce_sn"] < ic + 240:
                raise ValueError(
                    f"kip {ad!r}: kabuk_butce_sn {ayar['kabuk_butce_sn']} — "
                    f"isyatirim ic butcesi {ic:.0f} sn ve kalan adimlar icin "
                    f"en az {ic + 240:.0f} sn gerekiyor")

        alicilar = ayar["alicilar"]
        if not isinstance(alicilar, list) or not alicilar:
            # KOSUP KIMSEYE GONDERMEMEK, HIC KOSMAMAKTAN KOTU: kaynak
            # tuketir, defter yazar, ama kimse gormez. Sessiz bir "hic
            # bildirim gelmiyor" arizasi gunlerce fark edilmez.
            raise ValueError(
                f"kip {ad!r}: `alicilar` bos olamaz. Kosup kimseye "
                "gondermemek, hic kosmamaktan kotudur.")
        bilinen = set(self.sahip_listesi)
        yabanci = [a for a in alicilar if str(a).strip().lower() not in bilinen]
        if yabanci:
            raise ValueError(
                f"kip {ad!r}: telegram.sahipler icinde olmayan alici: "
                f"{', '.join(map(str, yabanci))}")

        return {
            "kip": ad,
            "kaynaklar": [str(x) for x in ayar["kaynaklar"]],
            "panel": bool(ayar["panel"]),
            "panel_butce_sn": float(ayar["panel_butce_sn"]),
            "kabuk_butce_sn": float(ayar["kabuk_butce_sn"]),
            "alicilar": [str(a).strip().lower() for a in alicilar],
        }

    def _resolve(self, p: str | Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else (self.root / p)

    # --- gizli degerler (sadece .env'den) ---
    @staticmethod
    def env(name: str, default: str | None = None) -> str | None:
        v = os.getenv(name)
        return v if v not in (None, "") else default


def _auth_modunu_uygula(raw: dict) -> str:
    """
    LLM kimlik yolunu secer: Claude ABONELIGI mi, API ANAHTARI mi?

    Claude Code CLI kimlik sirasi: ANTHROPIC_API_KEY -> ANTHROPIC_AUTH_TOKEN
    -> OAuth profili (claude.ai girisi). Yani .env'de bir API anahtari varsa
    Max/Pro ABONELIGI HIC KULLANILMAZ — anahtar onu golgeler ve her cagri
    token basina API kredisinden duser.

    Olculdu: anahtar setliyken cagri "kredi bitti" hatasi verdi; anahtar
    ortamdan silinince ayni cagri abonelik uzerinden calisti.

    DIKKAT: anahtari BOSALTMAK yetmez. Bos bir ANTHROPIC_API_KEY de kendi
    sirasini kazanir ve bos anahtarla kimlik dogrulamaya calisir — silinmeli.
    """
    mod = str(_deep_get(raw, "analysis.llm.auth", "abonelik") or "abonelik").lower()
    if mod in ("abonelik", "subscription", "oauth"):
        os.environ.pop("ANTHROPIC_API_KEY", None)
    return mod


def load_settings(root: Path | None = None) -> Settings:
    root = root or ROOT
    load_dotenv(root / ".env")

    with open(root / "config" / "settings.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    _auth_modunu_uygula(raw)

    sel_path = root / "config" / "selectors.yaml"
    selectors = {}
    if sel_path.exists():
        with open(sel_path, encoding="utf-8") as f:
            selectors = yaml.safe_load(f) or {}

    s = Settings(raw=raw, selectors=selectors, root=root)
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    s.report_dir.mkdir(parents=True, exist_ok=True)
    return s
