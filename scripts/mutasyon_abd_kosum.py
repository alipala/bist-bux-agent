"""
ABD trend koşumu mutasyon turu (2026-08-30).

NEDEN BU TUR: koşum "S&P 500'de kural rastgeleyi geciyor mu" sorusunu
cevapliyor ve cevap OTOMATIK AL-SAT kararina girecek. Yanlis bir evren,
yanlis birimde bir esik ya da yanlis bir kiyas endeksi, sayilari
BOZMADAN beyani bozar — bu deponun en pahali hata sinifi.

EN KRITIK MUTASYONLAR:
  A — endeks suzgeci kalkarsa 912 enstrumanlik ARACI KURUM evreni
      "S&P 500 backtest'i" diye raporlanir
  C — BIST disi venue sessizce TL esigi varsayarsa 50M USD devir
      uygulanir ve sonuc "likit hisselerde kural calisiyor" okunur
  E — kiyas XU100'e sabitlenirse ABD koşumu TURK ENDEKSIYLE kiyaslanir

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
BT = "src/finagent/analysis/backtest.py"
TT = "src/finagent/analysis/trend_takip.py"
RUN = "run.py"

EVREN = "test_trend_ABD_evreni_ENDEKS_UYELIGIYLE_daraliyor"
DEVIR = "test_trend_BIST_DISI_venue_DEVIR_esigini_SESSIZCE_varsaymaz"
KIYAS = "test_trend_KIYAS_ENDEKSI_kosuma_BAGLI_XU100_sabit_degil"
BEYAN = "test_trend_CLI_BEYANI_kosulan_PIYASADAN_okunuyor"

M = [
    ("A) endeks suzgeci YOK SAYILIYOR (araci kurum evreni 'S&P 500' olur)",
     BT, "    kosul, par = \"i.venue = ?\", [venue]\n    if endeksler:",
     "    kosul, par = \"i.venue = ?\", [venue]\n    if False:", EVREN),

    ("B) bilinmeyen endeks TUM evrene dusuyor (bos yerine hepsi)",
     BT, '        kosul += (" AND EXISTS (SELECT 1 FROM index_members m"',
     '        kosul += (" AND EXISTS (SELECT 1 FROM instruments m"', EVREN),

    ("C) BIST disi venue SESSIZCE TL esigi varsayiyor",
     TT, '        if venue.upper() != "BIST":\n            raise ValueError(',
     '        if False:\n            raise ValueError(', DEVIR),

    ("D) kosu() endeks suzgecini _evren'e GECIRMIYOR (kablo kacisi)",
     TT, "    evren = _evren(db, venue, endeksler=endeksler)",
     "    evren = _evren(db, venue)", DEVIR),

    ("E) kiyas endeksi XU100'e SABITLENIYOR",
     TT, "    endeks = _endeks_serisi(db, kiyas_kod)",
     "    endeks = _endeks_serisi(db)", KIYAS),

    ("F) yatay endeks 'kiyas YOK' diye raporlaniyor (0.0 falsy)",
     TT, '"al_tut_endeks_%": round(al_tut, 1) if al_tut is not None else None,',
     '"al_tut_endeks_%": round(al_tut, 1) if al_tut else None,', KIYAS),

    ("G) devir esiginin BIRIMI yine sabit TL",
     TT, '    birim = "USD" if abd else "TL"', '    birim = "TL"', BEYAN),

    ("H) para birimi beyani yine sabit TRY (dal olu)",
     TT, "    if abd:\n        s.append((\"!\", \"Getiriler NOMINAL USD.",
     "    if False:\n        s.append((\"!\", \"Getiriler NOMINAL USD.", BEYAN),

    ("H2) CLI beyani sinirlar() yerine ELDE yaziyor (ikinci kopya)",
     RUN, "        for tur, metin in sinirlar(args.piyasa, r.get(\"asgari_devir\", 0)):",
     "        for tur, metin in []:", BEYAN),

    ("H3) devir esigi GECIRILMIYOR, sabit basiliyor",
     TT, "f\"{asgari_devir:,.0f} {birim} (uretim tarayicisiyla AYNI).\")",
     "f\"50,000,000 {birim} (uretim tarayicisiyla AYNI).\")", BEYAN),

    ("I) --piyasa abd kiyasi SPX'e cevirmiyor",
     RUN, '            kiyas = "SPX"', '            kiyas = "XU100"', BEYAN),
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
