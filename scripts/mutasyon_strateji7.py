"""§7 mutasyon turu — LLM yorum kolu (paralel, suzgec DEGIL)."""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
SL = "src/finagent/pulse/strateji_llm.py"
RU = "src/finagent/pulse/runner.py"

M = [
    ("A) LLM'i SUZGEC yap: secimi modelden once cagir",
     RU,
     "        llm = self._strateji_llm(sonuc[\"gorusler\"], ayar)",
     "        pass",
     "test_strateji7_LLM_SINYALI_BASTIRAMAZ"),
    ("B) seviyeleri MODELDEN al (model hesap yapsin)",
     SL,
     '            "giris": g.get("giris"),\n            "stop": g.get("stop"),',
     '            "giris": y.get("giris", g.get("giris")),\n'
     '            "stop": y.get("stop", g.get("stop")),',
     "test_strateji7_SEVIYELER_KURALDAN_gelir_modelden_DEGIL"),
    ("C) modele HAM SERI de gonder",
     SL,
     '            "bar_ts": sv.get("bar_ts"),',
     '            "bar_ts": sv.get("bar_ts"),\n            "seri": sv,',
     "test_strateji7_SEVIYELER_KURALDAN_gelir_modelden_DEGIL"),
    ("D) gecersiz karari 'al' say (sessizce duzelt)",
     SL,
     "        if not g or karar not in KARARLAR:",
     "        if not g:\n            karar = 'al'\n        if False:",
     "test_strateji7_BILINMEYEN_SEMBOL_ve_KARAR_SESSIZCE_DUZELTILMEZ"),
    ("E) LLM hatasi kosumu DUSURSUN",
     RU,
     "        except Exception as e:                             # noqa: BLE001\n"
     "            log.warning(\"[strateji_llm] kol patladi: %s: %s\",\n"
     "                        type(e).__name__, e)\n"
     "            return {\"gorusler\": [], \"hata\": f\"{type(e).__name__}: {e}\"}",
     "        except Exception:\n            raise",
     "test_strateji7_LLM_HATASI_SINYALI_DUSURMEZ"),
    ("F) LLM koluna ARAC ver",
     SL, "allowed_tools=[], max_turns=1", "max_turns=1",
     "test_strateji7_PROMPT_TEK_KAYNAKTAN_ve_ARACSIZ"),
    ("G) LLM satirini deftere YAZMA",
     RU,
     "        rapor = defter.kaydet(tam + ikinci + llm, sahip)",
     "        rapor = defter.kaydet(tam + ikinci, sahip)",
     "test_strateji7_UCUNCU_SATIR_deftere_yaziliyor"),
    ("H) LLM satirina AYNI ajan adini ver (UNIQUE cakisir)",
     SL, '            "ajan": AJAN,', '            "ajan": "strateji",',
     "test_strateji7_UCUNCU_SATIR_deftere_yaziliyor"),
]

def _yesil_mi(test: str) -> bool:
    """
    MUTASYONDAN ONCE TEST YESIL MI?

    OLCULDU 2026-08-28: bir testte tirnak hatasi vardi ve test ZATEN
    KIRMIZIYDI; mutasyon turu uc bozmayi "yakalandi" diye raporladi.
    Zaten kirmizi bir teste karsi mutasyon HICBIR SEY KANITLAMAZ —
    kanit yontemi yine kendini kandirmisti.
    """
    r = subprocess.run(
        [str(KOK / ".venv/bin/python"), "-c",
         f"import sys; sys.path.insert(0,'tests');"
         f"import test_smoke as T; T.{test}()"],
        cwd=KOK, capture_output=True, text=True, timeout=900)
    return r.returncode == 0


for ad, yol, eski, yeni, test in M:
    if not _yesil_mi(test):
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
        r = subprocess.run(
            [str(KOK / ".venv/bin/python"), "-c",
             f"import sys; sys.path.insert(0,'tests');"
             f"import test_smoke as T; T.{test}()"],
            cwd=KOK, capture_output=True, text=True, timeout=900)
        print(f"  {'✗ YAKALANMADI' if r.returncode == 0 else '✓ yakalandi'}: {ad}")
    finally:
        p.write_text(yedek, encoding="utf-8")
        # Bayat .pyc mutasyon testini YALANCI yapar — olculdu 2026-08-27.
        for kok in KOK.joinpath("src").rglob("__pycache__"):
            shutil.rmtree(kok, ignore_errors=True)
print("kaynaklar geri alindi")
