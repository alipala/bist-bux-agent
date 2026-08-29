"""
Es-ad tablosu + `asgari_bar` beyani mutasyon turu (2026-08-29).

OLCULEN KAPSAM KAYBI: yedi S&P 500 uyesi (BEN, BNY, DECK, IBM, SLB,
SMCI, WAB) SIFIR barla duruyordu. Katalog adi endeks kaynagindan
(gunluk konusma adi), Yahoo'nunki hukuki unvan; ortak belirtec yok.

EN KRITIK MUTASYON `C`: tablo TAM KUME yerine ALTKUME ile eslerse,
kapatmak icin var oldugu hatayi kendisi acar.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib, shutil, subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
ID = "src/finagent/research/identity.py"
ST = "src/finagent/pulse/strateji.py"

ESAD = "test_es_ad_tablosu_YEDI_SEMBOLU_acar_kapiyi_ACMAZ"
BEYAN = "test_asgari_bar_VERI_DERINLIGI_esigi_TARAMA_KAPISI_DEGIL"
TEKKURAL = "test_ad_karsilastirmasi_TEK_KURAL"

M = [
    ("A) es-ad dali hic calismiyor (eski davranis: 7 sembol gorunmez)",
     ID, "    ga = es_ad_grubu(ad_a)\n"
         "    return ga is not None and ga == es_ad_grubu(ad_b)",
     "    return False", ESAD),

    ("B) tablo bosaltiliyor",
     ID, '_ES_ADLAR = (\n    ("Franklin Resources", "Franklin Templeton"),',
     '_ES_ADLAR = (\n    ("__yok__", "__yok2__"),\n    ("x Franklin Resources", "Franklin Templeton"),',
     ESAD),

    ("C) TAM KUME yerine ALTKUME ile esleniyor (grup sizar)",
     ID, "    b = frozenset(ad_belirteci(ad))\n"
         "    return _ES_ANAHTAR.get(b) if b else None",
     "    b = frozenset(ad_belirteci(ad))\n"
     "    if not b:\n        return None\n"
     "    for k, gid in _ES_ANAHTAR.items():\n"
     "        if k <= b:\n            return gid\n"
     "    return None", ESAD),

    ("D) grup kimligi yok sayiliyor — farkli gruplar eslesir",
     ID, "    return ga is not None and ga == es_ad_grubu(ad_b)",
     "    return ga is not None or es_ad_grubu(ad_b) is not None", ESAD),

    ("E) adsiz kayit gruba dusuyor",
     ID, "    return _ES_ANAHTAR.get(b) if b else None",
     "    return _ES_ANAHTAR.get(b, 0)", ESAD),

    # --- beyan ------------------------------------------------------------
    ("F) `asgari_bar` TARAMA KAPISI haline geliyor (belge guncellenmeden)",
     ST, '    if sv.get("pozisyonda"):',
     '    if (ayar.get("asgari_bar") or 0) and sv.get("bar_sayisi", 10**9) < \\\n'
     '            int(ayar["asgari_bar"]):\n'
     '        return "yetersiz derinlik"\n'
     '    if sv.get("pozisyonda"):', BEYAN),
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
