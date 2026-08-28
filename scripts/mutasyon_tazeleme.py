"""
Tazeleme plani + bekci `skipped` mutasyon turu (2026-08-28).

Iki duzeltmeyi de sinar:
  1. `prices` derin araligi her kosuda tekrarlamasin (panel butcesi
     900 sn -> 61 sn dusmustu, iki sahibin de paneli atlanmisti)
  2. Bekci `skipped` kosuyu ariza saymasin ("ibkrkimlik 3 kosudur
     partial" derken gercekte 1 partial + 2 skipped vardi)

Her mutasyon, testin GERCEKTEN o kusuru bagladigini gosterir. Yesil
test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib, shutil, subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
PR = "src/finagent/collectors/prices.py"
DB = "src/finagent/storage/db.py"
WD = "src/finagent/bot/watchdog.py"
SF = "src/finagent/collectors/strateji_fiyat.py"

DERIN = "test_prices_DERIN_ARALIK_HER_KOSUDA_TEKRARLANMAZ"
KADEME = "test_prices_TAZELEME_KADEMESI_BOSLUGU_KAPATIR"
KOTASYON = "test_prices_KOTASYON_YOLU_STRATEJI_ARALIGINDAN_ETKILENMEZ"
SERI = "test_seri_durumlari_KAYNAK_BAZLI_sayar"
BEKCI = "test_bekci_SKIPPED_kosuyu_ARIZA_SAYMAZ"
KABLO = "test_strateji_fiyat_TAZELEME_PLANI_COLLECT_ICINDE_KURULUYOR"
AYRIM = "test_prices_STRATEJI_EVRENINE_ARTIK_DOKUNMUYOR"

M = [
    # --- 1) tazeleme plani ---------------------------------------------
    # A ILK TURDA KACTI: dort testin dordu de plani ELLE kuruyordu, yani
    # `collect()` icindeki kabloyu kimse sinamiyordu. Kablo testi eklendi.
    ("A) plan hic kurulmuyor (eski davranis: her kosu derin)",
     SF, "        self._strateji_araliklari = self._tazeleme_plani(evren)",
     "        self._strateji_araliklari = {}", KABLO),

    ("B) planda olmayana KISA aralik (sig seri asla derinlesmez)",
     PR, "        return self._strateji_araliklari.get(hedef[\"id\"], self._derin_aralik())",
     "        return self._strateji_araliklari.get(hedef[\"id\"], self._genel_aralik())",
     DERIN),

    ("C) `asgari_bar` esigi kalkiyor (sig seri kisa araliga duser)",
     PR, "            if not d or d[\"bar\"] < asgari or not d[\"son_ts\"]:",
     "            if not d or not d[\"son_ts\"]:", DERIN),

    ("D) bosluk yok sayiliyor — herkese en kisa kademe",
     PR, "                if gun <= sinir:", "                if True:", KADEME),

    ("E) kademe siniri gevsetiliyor (20 -> 400 gun)",
     PR, "    TAZELEME_KADEMELERI = ((20, \"3mo\"),",
     "    TAZELEME_KADEMELERI = ((400, \"3mo\"),", KADEME),

    ("F) gelecek tarihli damga kabul ediliyor",
     PR, "            if gun < 0:\n                continue",
     "            if False:\n                continue", KADEME),

    # --- 2) kotasyon yolu (yan etki kapisi) -----------------------------
    ("G) kotasyon yolu strateji araligina geri donuyor",
     PR, "        aralik = self._genel_aralik()\n        # `ad_gerek=True`",
     "        aralik = self._aralik(hedef)\n        # `ad_gerek=True`", KOTASYON),

    # --- 3) kaynak bazli sayim ------------------------------------------
    ("H) `seri_durumlari` kaynak suzmuyor",
     DB, "                    WHERE source = ? AND instrument_id IN",
     "                    WHERE (? IS NOT NULL) AND instrument_id IN", SERI),

    # --- 4) bekci -------------------------------------------------------
    ("I) `skipped` yine ariza sayiliyor (eski davranis)",
     WD, "            if r[\"status\"] == \"skipped\":\n                continue",
     "            if False:\n                continue", BEKCI),

    ("J) `partial` de eleniyor — gercek ariza kacar",
     WD, "            if r[\"status\"] == \"skipped\":",
     "            if r[\"status\"] in (\"skipped\", \"partial\"):", BEKCI),

    # "Plan KURULDU" ile "plan KULLANILDI" ayri iddialar.
    ("K) plan kuruluyor ama `_aralik` onu hic okumuyor",
     PR, "        return self._strateji_araliklari.get(hedef[\"id\"], self._derin_aralik())",
     "        return self._derin_aralik()", KABLO),

    # --- 5) evren ayrimi (Plan A) ---------------------------------------
    ("L) `prices` evreni geri aliyor (ayirma bosa gider)",
     PR, "        return list(self.db.research_targets())",
     "        return list(self.db.research_targets()) + list(self.strateji_evreni())",
     AYRIM),

    ("M) `strateji_fiyat` evren yerine arastirma hedeflerini cekiyor",
     SF, "        evren = self.strateji_evreni()",
     "        evren = list(self.db.research_targets())", KABLO),

    ("N) `strateji_fiyat` endeks/kotasyon isini de ustleniyor",
     SF, "        return 0, []", "        return super()._ek_seriler(hedefler)",
     KABLO),
]


def _kos(test: str) -> int:
    return subprocess.run(
        [str(KOK / ".venv/bin/python"), "-c",
         f"import sys; sys.path.insert(0,'tests');"
         f"import test_smoke as T; T.{test}()"],
        cwd=KOK, capture_output=True, text=True, timeout=900).returncode


yakalanan = 0
for ad, yol, eski, yeni, test in M:
    # ON KOSUL: test zaten kirmizi ise mutasyon "yakalandi" der ve YALAN
    # soyler. Bu tuzak 27 Agustos'ta uc mutasyonda yasandi.
    if _kos(test) != 0:
        print(f"  ! TEST ZATEN KIRMIZI, mutasyon anlamsiz: {ad} [{test}]")
        continue
    p = KOK / yol
    yedek = p.read_text(encoding="utf-8")
    t2 = yedek.replace(eski, yeni)
    if t2 == yedek:
        print(f"  ! UYGULANAMADI: {ad}")
        continue
    p.write_text(t2, encoding="utf-8")
    try:
        tamam = _kos(test) == 0
        print(f"  {'✗ YAKALANMADI' if tamam else '✓ yakalandi'}: {ad}")
        yakalanan += 0 if tamam else 1
    finally:
        p.write_text(yedek, encoding="utf-8")
        # `tests` DE TEMIZLENIYOR — src yetmiyor. Bu turda bir mutasyon
        # (B) once "kacti" diye raporlandi, elle denendiginde YAKALANDI:
        # alt surec bayat bir `test_smoke.pyc` iceri aliyordu. Yalan
        # soyleyen mutasyon turu, hic kosmamis turdan kotudur.
        for dizin in ("src", "tests"):
            for kok in KOK.joinpath(dizin).rglob("__pycache__"):
                shutil.rmtree(kok, ignore_errors=True)

print(f"\n{yakalanan}/{len(M)} mutasyon yakalandi · kaynaklar geri alindi")
