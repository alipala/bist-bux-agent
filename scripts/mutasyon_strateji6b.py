"""Adim 6b mutasyon turu — emir butonu ve adet hesabi."""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
LI = "src/finagent/bot/listener.py"
RU = "src/finagent/pulse/runner.py"

M = [
    ("H) buton onay yolunu ATLAYIP dogrudan gondersin",
     LI,
     'self._emir_komutu(f"{sembol} AL {adet} {fiyat}", chat_id)',
     'from .emirakis import yurut\n            yurut(self.s, self.db, {}, "ali")',
     "test_strateji6_BUTON_EMIR_GONDERMEZ_onay_yolundan_gecer"),
    ("I) adetsiz sinyale de buton koy",
     RU,
     'if not (adet and sembol and g.get("conid") and g.get("giris")):',
     'if not (sembol and g.get("giris")):',
     "test_strateji6_ADETSIZ_SINYALE_BUTON_KONMAZ"),
    ("J) adet yokken sebebi yut, 0 yaz",
     RU,
     '    sebep = gorus.get("adet_sebep")',
     '    return f"<code>/emir {_esc(sembol)} AL 0 {fiyat_metni}</code>"\n'
     '    sebep = gorus.get("adet_sebep")',
     "test_strateji6_ADET_UYDURULMAZ_sebebi_SOYLENIR"),
    ("K) kur yoksa 1 varsay (EUR hesapta USD emri ~%15 yanlis)",
     RU,
     '                    g["adet_sebep"] = f"{pb}->{hedef_pb} kuru yok"\n'
     '                    continue',
     '                    kur = 1.0',
     "test_strateji6_KUR_YOKSA_ADET_YAZILMAZ"),
    ("L) callback_data 64 bayt sinirini gormezden gel",
     RU,
     "        if len(veri.encode()) > 64:          # Telegram siniri\n"
     "            continue",
     "        if False:\n            continue",
     "test_strateji6_ADETSIZ_SINYALE_BUTON_KONMAZ"),
]

for ad, yol, eski, yeni, test in M:
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
