"""Onay teslimat hedefi mutasyon turu (2026-08-28 kilitlenmesi)."""
import pathlib, shutil, subprocess
KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
LI = "src/finagent/bot/listener.py"
M = [
    ("A) eski davranis: `_chat_id`i oldugu gibi kullan",
     LI, "            gruplar.setdefault(self._dusme_hedefi(o, birinci), []).append(o)",
     '            gruplar.setdefault(str(o.veri.get("_chat_id") or birinci or ""), []).append(o)',
     "test_onay_GECERSIZ_CHAT_ID_SAHIBE_yonlendiriliyor"),
    ("B) GECERLI adresi de sahibe gore EZ",
     LI, "        if chat and self.s.sahip_bul(chat):\n            return chat",
     "        if False:\n            return chat",
     "test_onay_GECERSIZ_CHAT_ID_SAHIBE_yonlendiriliyor"),
    ("C) sahip cozumunu atla",
     LI, '        sahip = str(o.veri.get("_sahip") or "").strip().lower()',
     '        sahip = ""',
     "test_onay_GECERSIZ_CHAT_ID_SAHIBE_yonlendiriliyor"),
    ("D) haber gitmese de SIL (onay.py'nin yasagi)",
     LI, "            if not self._gonder(metin, chat_id):",
     "            if False:",
     "test_onay_dusmesi_HABER_GITMEZSE_silmiyor"),
]


def _yesil_mi(test: str) -> bool:
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
        for kok in KOK.joinpath("src").rglob("__pycache__"):
            shutil.rmtree(kok, ignore_errors=True)
print("kaynaklar geri alindi")
