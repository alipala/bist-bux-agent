"""
Bicim onurlandirma (B) + kesilen arac suzgeci (C) mutasyon turu
(2026-08-29).

OLCULEN IKI KUSUR, ikisi de ayni turda gorulduo:
  B — kullanici alan alan JSON semasi verip "BASKA HICBIR SEY yazma"
      dedi; cevap madde isaretli duz yazi geldi. Sistem prompt'undaki
      "BICIM: sade Markdown" satiri kullanicinin istegini eziyordu.
  C — kullaniciya "BAKAMADIM: fiyat_serisi, Bash, haberler, Agent"
      yazildi. `Bash` ve `Agent` bu bota HIC verilmiyor; model onlara
      uzandi, `can_use_tool` reddetti, ama ad yine de kullaniciya gitti.

EN KRITIK MUTASYONLAR:
  C2 — sinirlar bicimin ONUNE gecmezse "sadece JSON don" talimati
       sayi uydurmaya kapi acar.
  D  — suzgec cagrilmazsa saf fonksiyon dogru olsa da hicbir sey degisir.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib, shutil, subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
CH = "src/finagent/bot/chat.py"

BICIM = "test_prompt_ISTENEN_BICIMI_onurlandirir_ama_UYDURMAYA_izin_vermez"
SUZ = "test_kesilen_listesi_SUNULMAYAN_araci_KULLANICIYA_yazmaz"
YAZI = "test_kesilen_araclar_KULLANICIYA_yaziliyor"

M = [
    # --- B: bicim ---------------------------------------------------------
    ("B1) bicim kurali kaldiriliyor (eski davranis: her zaman sade Markdown)",
     CH, "28. ISTENEN BICIM VARSA O BICIMDE YAZ \u2014 VARSAYILAN BICIM DEGIL.",
     "28. (kaldirildi)", BICIM),

    ("B2) BICIM satiri yine MUTLAK (kullanicinin istegi ezilir)",
     CH, "Bu VARSAYILANDIR — kullanici baska bir bicim istediyse (kural 28) o\ngecerlidir.",
     "Bu kural her zaman gecerlidir.", BICIM),

    ("C2) uydurma yasagi bicimden SONRA geliyor (sema sayi uydurtur)",
     CH, "    UC SINIR, UCU DE BICIMDEN ONCE GELIR:",
     "    Uc not:", BICIM),

    ("C3) sema-yalan tercihi siliniyor (catismada ne yapilacagi belirsiz)",
     CH, "Semayi bozmak, semayi\n         YALANLA doldurmaktan iyidir.",
     "Duruma gore karar ver.", BICIM),

    ("C4) kapsam sinirini kaldir (bakamadim sessizce kaybolur)",
     CH, "      b) KAPSAM SESSIZ KALMAZ.", "      b) Kapsam.", BICIM),

    # --- C: kesilen suzgeci -----------------------------------------------
    ("D) suzgec HIC cagrilmiyor (ham liste kullaniciya gider)",
     CH, "        kesilen = kesilen_suz(kesilen, araclar)", "        pass", SUZ),

    ("E) suzgec ters calisiyor (yalnizca SUNULMAYANLAR gosterilir)",
     CH, "    return [a for a in (kesilen or []) if a in kume]",
     "    return [a for a in (kesilen or []) if a not in kume]", SUZ),

    ("F) suzgec yok, hepsi geciyor",
     CH, "    return [a for a in (kesilen or []) if a in kume]",
     "    return list(kesilen or [])", SUZ),

    ("G) sira bozuluyor (kume donduruluyor)",
     CH, "    return [a for a in (kesilen or []) if a in kume]",
     "    return sorted(set(kesilen or []) & kume)", SUZ),

    ("H) dusurulen SESSIZCE atiliyor (tani izi kaybolur)",
     CH, "    if disarida:\n        log.warning(", "    if False:\n        log.warning(",
     SUZ),

    ("I) sunulan bosken hepsi geciyor (arac verilmemis turda 'bakamadim')",
     CH, "    kume = set(sunulan or [])", "    kume = set(sunulan or []) or None",
     SUZ),
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
