"""
Cikis sinyali mutasyon turu (2026-08-29).

OLCULEN BOSLUK: motor yalnizca `AL` uretiyordu. 2N stop tahmin
defterinin kosuluyla tez alarmina dusuyordu ama Donchian'in ASIL cikisi
— 10 gunluk dip — giris gunundeki tabloda BIR KEZ gosterilip
unutuluyordu. Trend takibinde kenar buyuk olcude cikistadir.

EN KRITIK MUTASYON `C`: cikis sinyali SAHIP OLUNMAYAN kagit icin
uretilirse, kullanicinin hic almadigi 167 sembol icin "SAT" denir.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib, shutil, subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
TT = "src/finagent/analysis/trend_takip.py"
ST = "src/finagent/pulse/strateji.py"
RN = "src/finagent/pulse/runner.py"

KURAL = "test_cikis_karari_10G_DIP_kuralini_uygular"
KOPYA = "test_cikis_kurali_TEK_KOPYA_backtest_ile_ayni"
SAHIP = "test_cikis_SAHIP_OLUNMAYAN_kagit_icin_URETILMEZ"
NAKIT = "test_cikis_NAKIT_ve_SIFIR_ADET_atlanir"
STOP = "test_cikis_STOPU_KANITSIZ_odunc_almaz"
MESAJ = "test_cikis_mesaji_SAT_KOMUTU_ve_bilinmeyen_stopu_yazar"

M = [
    # --- kuralin kendisi ------------------------------------------------
    ("A) 10G dip testi gevsetiliyor (`<` yerine `<=`)",
     TT, 'and bar["close"] < min(onceki):',
     'and bar["close"] <= min(onceki):', KURAL),

    ("B) stop, Donchian'dan SONRA bakiliyor (iyimser cikis fiyati)",
     TT, '    if (stop is not None and bar.get("low") is not None\n'
         '            and bar["low"] <= stop):\n        return "2N stop"',
     '    if False:\n        return "2N stop"', KURAL),

    # --- EN KRITIK: kimin kagidi -----------------------------------------
    ("C) cikis GERCEK pozisyon yerine TUM evrende araniyor",
     ST, '    for p in db.latest_positions("ibkr", sahip):',
     '    for p in db.query("SELECT i.id instrument_id, i.symbol, i.asset_type,'
     ' 1.0 quantity, NULL avg_cost, i.currency FROM instruments i"):', SAHIP),

    ("D) nakit satiri elenmiyor",
     ST, '        if sembol == "CASH" or (p["asset_type"] or "") == "cash":\n'
         '            continue',
     '        if False:\n            continue', NAKIT),

    ("E) sifir adet elenmiyor",
     ST, '        if not adet or float(adet) <= 0:\n            continue',
     '        if False:\n            continue', NAKIT),

    # --- stop kaniti ------------------------------------------------------
    ("F) stop KANITSIZ odunc aliniyor (band kalkiyor)",
     ST, '        if g > 0 and abs(m / g - 1) <= ESLESME_BANDI:',
     '        if g > 0:', STOP),

    ("G) esleme bandi asiriya aciliyor (%5 -> %500)",
     ST, "ESLESME_BANDI = 0.05", "ESLESME_BANDI = 5.0", STOP),

    # --- tek kopya --------------------------------------------------------
    ("H) `_yurut` cikis kuralini YENIDEN yaziyor",
     TT, '        sebep = _cikis_sebebi(seri, kapanis, i, pozisyon["stop"])',
     '        sebep = None\n'
     '        if bar["low"] is not None and bar["low"] <= pozisyon["stop"]:\n'
     '            sebep = "2N stop"\n'
     '        else:\n'
     '            _o = [k for k in kapanis[i - CIKIS_PENCERE:i] if k]\n'
     '            if len(_o) >= CIKIS_PENCERE and bar["close"] < min(_o):\n'
     '                sebep = "10 gun dip"', KOPYA),

    # --- mesaj ------------------------------------------------------------
    ("I) SAT komutu yazilmiyor (sinyal uygulanamaz olur)",
     RN, '            L.append(f"<code>/emir {sembol} SAT {adet:g}</code>")',
     '            pass', MESAJ),

    ("J) bilinmeyen stop SESSIZ geciliyor",
     RN, '        if c.get("stop_bilinmiyor"):', '        if False:', MESAJ),
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
