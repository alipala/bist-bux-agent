"""
Nabiz butcesi + butce teshisi mutasyon turu (2026-08-29).

OLCULEN VAKA: 28 Agustos gecesi nabiz 3000 sn'lik butcenin 2519'unu
toplamaya harcadi; panele 197 sn kaldi, ali'nin payi 98 sn (asgari 120)
-> paneli atlandi, yuksel'in hakemi 189 sn'de kesildi. Model yorumu o
gece HIC uretilmedi.

Butce 2026-08-20'de turetilmisti ve o gunden beri toplama buyudu.
Sessizce bayatlayan bir sayi, gorunmeyen bir arizadir.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib, shutil, subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
CFG = "config/settings.yaml"
RN = "src/finagent/pulse/runner.py"

BUTCE = "test_nabiz_BUTCESI_olculen_toplamayi_KALDIRIYOR"
TESHIS = "test_panel_ATLANDIGINDA_sebebi_SAYIYLA_soyleniyor"

M = [
    # --- butcenin kendisi ------------------------------------------------
    ("A) butce eski degerine donuyor (28 Agu gecesinin hali)",
     CFG, "      kabuk_butce_sn: 4200", "      kabuk_butce_sn: 3000", BUTCE),

    # 3800 SECILMEDI: pay 453 sn cikiyor ve esigi (450) hala geciyor —
    # yani mutasyon KUSUR DEGIL. 3600'de pay 353 sn'ye duser.
    ("B) butce hakemin calistigi surenin altina dusuyor",
     CFG, "      kabuk_butce_sn: 4200", "      kabuk_butce_sn: 3600", BUTCE),

    # --- teshis metni -----------------------------------------------------
    ("C) teshis harcanan yerine KALANI yaziyor",
     RN, "        harcanan = toplam - (bitis - time.time())",
     "        harcanan = bitis - time.time()", TESHIS),

    ("D) damga yokken UYDURMA oran yaziliyor",
     RN, '        except (TypeError, ValueError, KeyError):\n'
         '            return ""',
     '        except (TypeError, ValueError, KeyError):\n'
     '            return "\\n<i>Kosunun 0 sn butcesinin 0 sn\'si harcandi "\\\n'
     '                   "(%0). kabuk_butce_sn</i>"', TESHIS),

    ("E) hangi ayarin bakilacagi soylenmiyor",
     RN, '                f"<code>ritim.kipler.{ayar.get(\'kip\', \'\')}'
         '.kabuk_butce_sn</code> "',
     '                f"<code>ayar</code> "', TESHIS),

    ("F) teshis mesaja HIC eklenmiyor",
     RN, '                        + self._butce_teshisi(ayar))',
     '                        )', TESHIS),
]


def _kos(test: str) -> int:
    return subprocess.run(
        [str(KOK / ".venv/bin/python"), "-c",
         f"import sys; sys.path.insert(0,'tests');"
         f"import test_smoke as T; T.{test}()"],
        cwd=KOK, capture_output=True, text=True, timeout=900).returncode


yakalanan = 0
for ad, yol, eski, yeni, test in M:
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
        for dizin in ("src", "tests"):
            for kok in KOK.joinpath(dizin).rglob("__pycache__"):
                shutil.rmtree(kok, ignore_errors=True)

print(f"\n{yakalanan}/{len(M)} mutasyon yakalandi · kaynaklar geri alindi")
