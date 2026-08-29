"""
"Olculmedi != yazilmadi" mutasyon turu (2026-08-29).

OLCULEN KUSUR: kullanici "motor dun ne dedi, kural para kazandiriyor
mu" diye sordu. Model deftere `gecmis_gorus` ile bakti — o arac
`ajan='hakem'`i SABIT suzuyor ve bunu SOYLEMIYORDU — yalnizca hakem
satirlarini gordu, karne de `olcum: 0` dondurunce

    "motorun sinyalleri deftere YAZILMIYOR"

dedi ve ZATEN YAPILMIS bir isi oneri diye sundu. Gercekte o gece
14 satir yazilmisti; yalnizca `ufuk_gun` 14 oldugu icin
olgunlasmamislardi.

EN KRITIK MUTASYONLAR:
  A — `yazilan` sayilmazsa karne yine "0 olcum" der ve ayrim kaybolur.
  E — suzgec beyani kalkarsa model defterin tamamini gordugunu sanir.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib, shutil, subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
ST = "src/finagent/pulse/strateji.py"
RN = "src/finagent/pulse/runner.py"
TL = "src/finagent/bot/tools.py"

SAYIM = "test_karne_YAZILAN_ile_OLCULEN_ayri_sayilir"
MESAJ = "test_karne_mesaji_OLCULMEDI_ile_YAZILMADIYI_ayirir"
DEFTER = "test_gecmis_gorus_SUZULDUGUNU_SOYLER_ve_ajan_secilebilir"
ESKI = "test_gecmis_gorus_sahibe_ait_ve_yalnizca_hakem"

M = [
    # --- karne sayimi -----------------------------------------------------
    ("A) `yazilan` puanlanmislarla ayni sayiliyor (ayrim kaybolur)",
     ST, "               WHERE olusma_ts >= ? AND sahip = ? AND ajan = ?\"\"\",",
     "               WHERE isabet IS NOT NULL AND olusma_ts >= ? "
     "AND sahip = ? AND ajan = ?\"\"\",", SAYIM),

    ("B) `olcum_bekleyen` hep 0 (bekleyen kayit gorunmez)",
     ST, '                 "olcum_bekleyen": yazilan - n,',
     '                 "olcum_bekleyen": 0,', SAYIM),

    ("C) hic yazim yokken damga UYDURULUYOR",
     ST, '                 "son_yazim": (yaz["son"] or "")[:19] or None}',
     '                 "son_yazim": (yaz["son"] or "bilinmiyor")[:19]}', SAYIM),

    # --- mesaj ------------------------------------------------------------
    ("D) mesaj yazilan sayisini SOYLEMIYOR (eski davranis)",
     RN, "        if yaz:\n            satir.append(", "        if False:\n            satir.append(",
     MESAJ),

    ("D2) hic yazim yokken de 'yaziliyor' deniyor (yanlis guven)",
     RN, "        if yaz:\n            satir.append(", "        if True:\n            satir.append(",
     MESAJ),

    # --- defter suzgeci ---------------------------------------------------
    ("E) suzgec beyani kalkiyor (model defterin tamamini gordugunu sanir)",
     TL, '            if ajan != "hepsi":\n                disarida = {}',
     '            if False:\n                disarida = {}', DEFTER),

    ("F) ajan parametresi yok sayiliyor (strateji satirlari yine gorunmez)",
     TL, '            ajan = (args.get("ajan") or "hakem").strip().lower()',
     '            ajan = "hakem"', DEFTER),

    ("G) bilinmeyen ajan SESSIZCE bos donuyor",
     TL, '                return _hata(f"\'{ajan}\' defterde yok",',
     '                return _ok({"gorusler": [], "ajan": ajan}) or _hata(f"\'{ajan}\' defterde yok",',
     DEFTER),

    ("H) bos yanit digerlerine isaret etmiyor",
     TL, '                              + (" (ama baska ajanlarin kayitlari VAR — "',
     '                              + ("" if True else " (ama baska ajanlarin kayitlari VAR — "',
     DEFTER),

    # --- ESKI KURAL BOZULMADI MI ------------------------------------------
    ("I) varsayilan artik hakem DEGIL (eski sozlesme kirilir)",
     TL, '            ajan = (args.get("ajan") or "hakem").strip().lower()',
     '            ajan = (args.get("ajan") or "hepsi").strip().lower()', ESKI),
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
