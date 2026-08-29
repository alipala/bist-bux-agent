"""
Uc gorunurluk kusuru mutasyon turu (2026-08-29).

Hepsi 28 Agustos mesajlarinda SAHADA goruldu:
  1. LLM gerekcesi 120 karakterde SESSIZCE kesildi ("... Risk/k")
  2. Nakit uyarisi hangi hesap oldugunu yazmadi (ibkr %95,8 sabah,
     midas %63,1 aksam -> bir gunde 33 puan dusmus gibi okundu)
  3. "zaten pozisyonda: 167" gercek portfoy sanildi (gercekte IKI satir:
     CASH + KO 0,05 adet)

Ucu de SAYIYI degil OKUYUSU bozuyordu. Yesil test kanit degil;
`[[fixi-nasil-kanitlarim]]`.
"""
import pathlib, shutil, subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
RN = "src/finagent/pulse/runner.py"
ST = "src/finagent/pulse/strateji.py"

KIRPMA = "test_kirpma_SESSIZ_DEGIL_isaretli"
HESAP = "test_risk_satiri_HANGI_HESAP_oldugunu_yazar"
DEFTER = "test_strateji_POZISYON_SAYACI_kimin_defteri_oldugunu_soyler"

M = [
    # --- 1) sessiz kirpma -------------------------------------------------
    ("A) kirpma isareti kaldiriliyor (eski davranis)",
     RN, '    return s if len(s) <= n else s[:n].rstrip() + "…"',
     '    return s[:n]', KIRPMA),

    ("B) kirpilmayan metne de isaret konuyor (yanlis pozitif)",
     RN, '    return s if len(s) <= n else s[:n].rstrip() + "…"',
     '    return s[:n] + "…"', KIRPMA),

    ("C) gerekce ortak yardimciyi kullanmiyor",
     RN, "f\"{_esc(_kirp(g.get('gerekce'), 120))}\")",
     "f\"{_esc(str(g.get('gerekce'))[:120])}\")", KIRPMA),

    # --- 2) hesap adi -----------------------------------------------------
    ("D) risk satiri hesabi yine dusuruyor",
     RN, '            + (f" · <b>{_esc(hesap.upper())}</b>" if hesap else "")',
     '            + ""', HESAP),

    ("E) hesap bilinmeyince UYDURULUYOR",
     RN, '    hesap = str(k.get("hesap") or "").strip()',
     '    hesap = str(k.get("hesap") or "bilinmiyor").strip()', HESAP),

    ("F) `_ozet_bildir` risk satirini yine KENDI yaziyor",
     RN, '        for r in riskler[:self.HAFIF_AZAMI_RISK]:\n'
         '            L.append("\\n" + _risk_satiri(r))\n'
         '        if len(riskler) > self.HAFIF_AZAMI_RISK:\n'
         '            L.append(f"\\n<i>… ve {len(riskler) - self.HAFIF_AZAMI_RISK} risk "\n'
         '                     "daha.</i>")\n'
         '\n'
         '        # --- PANEL',
     '        for r in riskler[:self.HAFIF_AZAMI_RISK]:\n'
     '            _k = r.get("kanit") or {}\n'
     '            L.append(f"\\n⚠️ <b>{_esc(r[\'sembol\'])}</b> {r[\'tur\']}")\n'
     '        if len(riskler) > self.HAFIF_AZAMI_RISK:\n'
     '            L.append(f"\\n<i>… ve {len(riskler) - self.HAFIF_AZAMI_RISK} risk "\n'
     '                     "daha.</i>")\n'
     '\n'
     '        # --- PANEL', HESAP),

    # --- 3) kimin defteri --------------------------------------------------
    ("G) etiket yanlis okunan haline donuyor",
     ST, 'POZISYON_SEBEBI = "kural zaten tutuyor"',
     'POZISYON_SEBEBI = "zaten pozisyonda"', DEFTER),

    ("H) aciklama satiri hic yazilmiyor",
     RN, "        if sayaclar.get(POZISYON_SEBEBI):", "        if False:",
     DEFTER),

    ("I) aciklama sayactan BAGIMSIZ yaziliyor (ilgisiz gurultu)",
     RN, "        if sayaclar.get(POZISYON_SEBEBI):", "        if True:",
     DEFTER),
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
