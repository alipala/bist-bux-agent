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
    def yedek_ayna_dizini(self):
        """
        Yerel aynanin dizini — ya da ayna kapaliysa `None`.

        UC KADEME, ve ortadaki kademe bir KAZAYI onluyor:

          1. `YEDEK_AYNA_DIZIN` verilmisse o kullanilir.
          2. `YEDEK_DIZIN` verilmis ama ayna icin bir sey verilmemisse
             AYNA KAPANIR. Gerekce: `YEDEK_DIZIN` yalnizca izole
             kosular (test, e2e) icin var — arsivi izole edip aynayi
             izole ETMEMEK, o kosulari GERCEK `data/yedek` dizinine
             yazdirirdi ve oradaki taze yedek bir TEST veritabaninin
             kopyasiyla degistirilirdi. Dosya adi ayni oldugu icin de
             bu, dogru yedek varmis gibi gorunurdu. `yedek_dizini`nin
             docstring'inde anlatilan hata sinifinin ta kendisi.
          3. Ikisi de yoksa ayar okunur.
        """
        p = os.getenv("YEDEK_AYNA_DIZIN")
        if p:
            return self._resolve(p)
        if os.getenv("YEDEK_DIZIN"):
            return None
        ham = ((self.get("yedek") or {}).get("yerel_ayna") or {}).get("dizin")
        return self._resolve(str(ham)) if ham else None

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
    YEDEK_ZORUNLU = ("enabled", "dizin", "gun", "asgari_bos_gb", "yerel_ayna")

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

        # YEREL AYNA da DOGRULANIR. Ayna sessizce yazilmazsa fark
        # edilmez: arsiv bulutta durur, her sey yolunda gorunur ve
        # eksikligi ancak internetsizken geri yuklemeye calisirken
        # ogrenirsin — yani tam da aynanin var olma sebebi olan anda.
        ayna = ayar["yerel_ayna"]
        if not isinstance(ayna, dict) or not ayna:
            raise ValueError(
                "yedek: `yerel_ayna` sozluk olmali "
                "(dizin + adet). Kapatmak icin `adet: 0` degil, "
                "`yerel_ayna: {dizin: ..., adet: 0}` yazilir.")
        eksik = [k for k in ("dizin", "adet") if k not in ayna]
        if eksik:
            raise ValueError(
                f"yedek.yerel_ayna eksik alan: {', '.join(eksik)}")
        if not isinstance(ayna["dizin"], str) or not ayna["dizin"].strip():
            raise ValueError(
                f"yedek.yerel_ayna: `dizin` bos olmayan metin olmali, "
                f"{ayna['dizin']!r} verilmis")
        # ADET, GUN DEGIL — ve 0 GECERLI: aynayi kapatmanin yolu bu.
        # `gun` gibi pozitif zorunlu olsaydi, aynayi kapatmak isteyen
        # kisi dizini bos metne cevirmeye calisir ve orasi zaten
        # reddediliyor; yani kapatmanin MESRU bir yolu kalmazdi.
        if not isinstance(ayna["adet"], int) or isinstance(ayna["adet"], bool) \
                or ayna["adet"] < 0:
            raise ValueError(
                f"yedek.yerel_ayna: `adet` negatif olmayan tam sayi olmali, "
                f"{ayna['adet']!r} verilmis")
        return dict(ayar)

    # `ritim.gunici` icin zorunlu alanlar — `ritim_kip` ile ayni disiplin.
    GUNICI_ZORUNLU = ("enabled", "aralik_dk", "kabuk_butce_sn", "alicilar")

    def gunici_ayari(self) -> dict:
        """
        `ritim.gunici` — DOGRULANMIS.

        `ritim_kip` DEGIL, ayri: gun ici kosu panel calistirmiyor ve
        sabit saati yok (aralikla calisiyor). `kipler` sozlesmesine
        sokmak, `panel`/`panel_butce_sn` alanlarini anlamsizca doldurmak
        ve bekciyi plist saatine gore yanlis yargiya zorlamak olurdu.
        """
        ayar = self.get("ritim.gunici")
        if not isinstance(ayar, dict) or not ayar:
            raise ValueError(
                "gun ici kosu tanimli degil: config/settings.yaml -> "
                "ritim.gunici")
        eksik = [k for k in self.GUNICI_ZORUNLU if k not in ayar]
        if eksik:
            raise ValueError(
                f"ritim.gunici eksik alan: {', '.join(eksik)}. "
                "Varsayilan YOK — her alan acikca yazilmali.")
        if not isinstance(ayar["enabled"], bool):
            raise ValueError(
                f"ritim.gunici: `enabled` bool olmali, {ayar['enabled']!r}")
        for alan in ("aralik_dk", "kabuk_butce_sn"):
            deger = ayar[alan]
            if not isinstance(deger, (int, float)) or isinstance(deger, bool) \
                    or deger <= 0:
                raise ValueError(
                    f"ritim.gunici: `{alan}` pozitif sayi olmali, {deger!r}")
        alicilar = ayar["alicilar"]
        if not isinstance(alicilar, list) or not alicilar:
            # KOSUP KIMSEYE GONDERMEMEK, HIC KOSMAMAKTAN KOTU.
            raise ValueError("ritim.gunici: `alicilar` bos olamaz")
        bilinen = set(self.sahip_listesi)
        yabanci = [a for a in alicilar
                   if str(a).strip().lower() not in bilinen]
        if yabanci:
            raise ValueError(
                f"ritim.gunici: tanimsiz sahip {yabanci}. "
                "telegram.sahipler tek dogruluk kaynagi.")
        if "koruma" in ayar and not isinstance(ayar["koruma"], bool):
            raise ValueError(
                f"ritim.gunici: `koruma` bool olmali, {ayar['koruma']!r}")
        out = dict(ayar)
        # Seans ici koruma (Ali 8 Eki). YOKSA acik — eski davranis.
        out["koruma"] = bool(ayar.get("koruma", True))
        out.update(self._gunici_taktik(ayar, len(alicilar)))
        return out

    @staticmethod
    def _gunici_taktik(ayar: dict, alici_sayisi: int) -> dict:
        """
        `ritim.gunici.taktik` (B6) — DOGRULANMIS.

        Blok YOKSA katman KAPALI sayilir ve bu bir ariza degildir: B6
        oncesi kurulumlar gecerli kalmali. Ama blok VARSA her alani
        dogrulanir — yarim tanimli bir LLM katmani, hic tanimlanmamis
        olandan tehlikelidir.
        """
        t = ayar.get("taktik")
        if t is None:
            return {"taktik_enabled": False, "taktik_sure_sn": 0,
                    "taktik_golge_turler": ()}
        if not isinstance(t, dict):
            raise ValueError(
                f"ritim.gunici.taktik bir sozluk olmali, {type(t).__name__}")
        for alan in ("enabled", "sure_sn"):
            if alan not in t:
                raise ValueError(
                    f"ritim.gunici.taktik eksik alan: `{alan}`. "
                    "Varsayilan YOK — her alan acikca yazilmali.")
        if not isinstance(t["enabled"], bool):
            raise ValueError(
                f"ritim.gunici.taktik: `enabled` bool olmali, {t['enabled']!r}")
        sure = t["sure_sn"]
        if not isinstance(sure, (int, float)) or isinstance(sure, bool) \
                or sure <= 0:
            raise ValueError(
                f"ritim.gunici.taktik: `sure_sn` pozitif sayi olmali, {sure!r}")

        # BUTCE ILISKISI DOGRULANIYOR — CALISMA ANINDA DEGIL, BURADA.
        #
        # Cagri SAHIP BASINA yapiliyor. `sure_sn` x alici + teslimat payi
        # kabuk butcesini asiyorsa kabuk sureci teslimatin ORTASINDA
        # oldurebilir: mesaj gider, damga yazilmaz, taktik bir sonraki
        # kosuda TEKRAR gonderilir. Calisma aninda butce kisiliyor (bkz.
        # `GunIci._taktik_butcesi`) ama kisilma bir TELAFIDIR; ayarin
        # kendisi bastan tutarli olmali ki kimse sessizce kisilmis bir
        # sureyle kossun diye.
        from .pulse.gunici import TESLIMAT_PAYI_SN
        gereken = sure * max(1, alici_sayisi) + TESLIMAT_PAYI_SN
        if t["enabled"] and gereken > ayar["kabuk_butce_sn"]:
            raise ValueError(
                f"ritim.gunici: taktik.sure_sn={sure}sn x {alici_sayisi} "
                f"alici + {TESLIMAT_PAYI_SN}sn teslimat payi = {gereken:.0f}sn, "
                f"kabuk_butce_sn={ayar['kabuk_butce_sn']}sn'yi asiyor. "
                "Ya sure_sn'i dusur ya kabuk_butce_sn'i yukselt.")
        # GOLGE TURLER (2026-10-02, Ali onayi). Bu turdeki taktikler
        # URETILIR ve deftere `teslim=0` ile yazilir (olcum surer) ama
        # GONDERILMEZ. Gerekce: taktik "alim" defterde 10/43 (%23,3),
        # ayni gun rastgele BIST alimi ~%38 — kenar yok, her is gunu bir
        # alim onerisi gidiyordu. `koruma` (elde olani savunma) gitmeye
        # devam eder. Alan YOKSA bos liste: davranis degismez.
        golge = t.get("golge_turler", [])
        gecerli = {"alim", "satis", "koruma"}
        if not isinstance(golge, list) or not set(golge) <= gecerli:
            raise ValueError(
                f"ritim.gunici.taktik: `golge_turler` {sorted(gecerli)} "
                f"icinden bir liste olmali, {golge!r} verilmis")
        return {"taktik_enabled": bool(t["enabled"]),
                "taktik_sure_sn": float(sure),
                "taktik_golge_turler": tuple(golge)}

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
        # FAZ 0 GOZLEMI (IBKR bulut baglayicisi) — ISTEGE BAGLI, gecici bir
        # olcum bayragi; yoksa kapali. Varsa bool olmali: "evet" gibi bir
        # deger sessizce "acik" sayilmasin.
        if "ozet" in ayar and not isinstance(ayar["ozet"], bool):
            raise ValueError(
                f"kip {ad!r}: `ozet` bool olmali, {ayar['ozet']!r} verilmis")
        if "mcp_gozlem" in ayar and not isinstance(ayar["mcp_gozlem"], bool):
            raise ValueError(
                f"kip {ad!r}: `mcp_gozlem` bool olmali, {ayar['mcp_gozlem']!r} verilmis")

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
            # Istege bagli Faz 0 bayragi (yukarida dogrulandi); yoksa kapali.
            # BURADA DA TASINMALI: donus sozlugu bilinen alanlardan KURULUYOR,
            # buraya yazilmayan anahtar dogrulanir ama OKUNAMAZ (ilk surumde
            # tam boyle oldu: ayar dogruydu, komut onu hic goremedi).
            "mcp_gozlem": bool(ayar.get("mcp_gozlem", False)),
            # Ozet mesaji (Ali 8 Eki). YOKSA gider — eski davranis.
            "ozet": bool(ayar.get("ozet", True)),
        }

    # `ibkr.strateji` icin zorunlu alanlar — `gomme_ayari` ile ayni
    # disiplin. Varsayilan YOK: bir strateji ayarinin sessizce
    # varsayilana dusmesi, kullanicinin SANDIGINDAN baska bir kurali
    # canli paraya baglamak demektir.
    STRATEJI_ZORUNLU = ("enabled", "endeksler", "para_birimleri",
                        "asgari_devir", "asgari_bar", "ufuk_gun",
                        "gunluk_emir_tavani", "secim_tohumu",
                        "risk_payi_pct", "llm_yorumu", "kip",
                        "bilanco_filtresi")

    def strateji_ayari(self, db=None) -> dict:
        """
        `ibkr.strateji` — DOGRULANMIS.

        `db` VERILIRSE evren de dogrulanir: `endeksler`in her elemani
        `index_members.index_name` icinde BULUNMALI. Yoksa sessizce BOS
        bir evren olusur ve motor "0 kirilim" der — bu deponun en kotu
        hata sinifi ("veri varken yok demek") ile ayni goruntuyu
        uretir, ama sebebi bir yazim hatasidir.
        `db` ISTEGE BAGLI cunku bu dosya `storage`i ice aktarmiyor
        (bagimlilik ICE dogrudur) ve `--help` gibi veritabanisiz
        yollarda ayarin bicimi yine de dogrulanabilmeli.
        """
        ayar = self.get("ibkr.strateji")
        if not isinstance(ayar, dict) or not ayar:
            raise ValueError(
                "strateji tanimli degil: config/settings.yaml -> ibkr.strateji")
        eksik = [k for k in self.STRATEJI_ZORUNLU if k not in ayar]
        if eksik:
            raise ValueError(
                f"ibkr.strateji eksik alan: {', '.join(eksik)}. "
                "Varsayilan YOK — her alan acikca yazilmali.")
        for alan in ("enabled", "llm_yorumu", "bilanco_filtresi"):
            if not isinstance(ayar[alan], bool):
                raise ValueError(
                    f"ibkr.strateji: `{alan}` bool olmali, {ayar[alan]!r} verilmis")

        endeksler = ayar["endeksler"]
        if not isinstance(endeksler, list) or \
                not all(isinstance(e, str) and e.strip() for e in endeksler):
            raise ValueError(
                "ibkr.strateji: `endeksler` bos olmayan metinlerden olusan "
                f"liste olmali, {endeksler!r} verilmis")
        # KAPALIYKEN BOS LISTE MESRU: motor kosmuyorsa evren de
        # gerekmiyor. ACIKKEN bos liste, sessizce hicbir sey taramayan
        # bir motor demektir.
        if ayar["enabled"] and not endeksler:
            raise ValueError(
                "ibkr.strateji: `enabled: true` iken `endeksler` bos olamaz — "
                "bos evren, hic kosmayan bir motoru CALISIYOR gosterir")

        para = ayar["para_birimleri"]
        if not isinstance(para, list) or \
                not all(isinstance(p, str) and p.strip() for p in para):
            raise ValueError(
                "ibkr.strateji: `para_birimleri` bos olmayan metinlerden "
                f"olusan liste olmali, {para!r} verilmis")
        if ayar["enabled"] and not para:
            raise ValueError(
                "ibkr.strateji: `enabled: true` iken `para_birimleri` bos olamaz")

        devir = ayar["asgari_devir"]
        if not isinstance(devir, dict):
            raise ValueError(
                "ibkr.strateji: `asgari_devir` para birimi -> esik sozlugu "
                f"olmali, {devir!r} verilmis")
        # ANAHTARLAR ORTUSMELI. Eksik anahtar = o para biriminde LIKIDITE
        # KAPISI OLMADAN islem; fazla anahtar = hicbir zaman okunmayan
        # bir esik, yani ayarladigini sanip ayarlamamak.
        eksik_ccy = [p for p in para if p not in devir]
        if eksik_ccy:
            raise ValueError(
                f"ibkr.strateji: `asgari_devir` icinde esigi olmayan para "
                f"birimi: {', '.join(eksik_ccy)}. Esiksiz para birimi, "
                "likidite kapisi OLMADAN islem demektir.")
        fazla_ccy = [k for k in devir if k not in para]
        if fazla_ccy:
            raise ValueError(
                f"ibkr.strateji: `asgari_devir` icinde `para_birimleri`nde "
                f"olmayan anahtar: {', '.join(map(str, fazla_ccy))}. "
                "Hic okunmayan bir esik, ayarladigini sanmak demektir.")
        for k, v in devir.items():
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                raise ValueError(
                    f"ibkr.strateji: `asgari_devir.{k}` negatif olmayan sayi "
                    f"olmali, {v!r} verilmis")

        for alan in ("asgari_bar", "ufuk_gun", "risk_payi_pct"):
            deger = ayar[alan]
            if not isinstance(deger, (int, float)) or isinstance(deger, bool) \
                    or deger <= 0:
                raise ValueError(
                    f"ibkr.strateji: `{alan}` pozitif sayi olmali, "
                    f"{deger!r} verilmis")
        # TAVAN 0 GECERLI: sinyaller deftere yazilir ama hicbiri emre
        # donusmez. Olcumu surdurup emri durdurmanin mesru yolu bu.
        for alan in ("gunluk_emir_tavani", "secim_tohumu"):
            deger = ayar[alan]
            if not isinstance(deger, int) or isinstance(deger, bool) or deger < 0:
                raise ValueError(
                    f"ibkr.strateji: `{alan}` negatif olmayan tam sayi olmali, "
                    f"{deger!r} verilmis")

        # KIP `ritim.kipler` ICINDE TANIMLI OLMALI — ayni gerekce:
        # tanimsiz kip, hic kosmayan bir motor demektir.
        self.ritim_kip(ayar["kip"])

        if db is not None and endeksler:
            bilinen = {r["index_name"] for r in
                       db.query("SELECT DISTINCT index_name FROM index_members")}
            yabanci = [e for e in endeksler if e not in bilinen]
            if yabanci:
                raise ValueError(
                    f"ibkr.strateji: `index_members` icinde bulunmayan endeks: "
                    f"{', '.join(yabanci)}. Tanimli olanlar: "
                    f"{', '.join(sorted(bilinen)) or '(tablo bos)'}")

        # GOLGE MOD (2026-10-02, Ali onayi). true iken motor kosar ve
        # deftere yazar (olcum surer) ama kirilim tablosu ve `/emir`
        # butonlari GONDERILMEZ. Gerekce: defterde kural ayni gunun
        # rastgele secimine gore %20,9'a karsi %43,7 (p<0,001) — aleyhte
        # anlamli; her gun bir alim dugmesi gonderiyordu. Alan YOKSA
        # false: eski kurulumlar degismeden calisir. VARSA bool olmali.
        golge = ayar.get("golge", False)
        if not isinstance(golge, bool):
            raise ValueError(
                f"ibkr.strateji: `golge` bool olmali, {golge!r} verilmis")
        out = dict(ayar)
        out["golge"] = golge
        return out

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
