"""
Sessiz toplama bosluklari mutasyon turu (2026-09-02).

Ali uc Telegram alarmi bildirdi ("Veri toplama sessizce eksik donuyor")
ve UC AYRI KOK SEBEP cikti — ayni alarm metni, ayni "partial" etiketi,
apayri mekanizmalar:

A) SEMBOL ANAHTARI. Dokuz cagiran kimligi `{symbol: row}` ile ariyordu.
   Sembol venue'ye gore TEKRARLANIR (DASH = kripto Dash / hisse
   DoorDash) ve sozlukte kazanan TANIMSIZ. Olculdu: kripto Dash icin
   prices 0, prices_hourly 0, fundamentals 0.

B) KESME ISARETI. "Domino's" -> [DOMINO], SEC ise [DOMINOS, PIZZA]
   yaziyordu; kimlik cozumu "ayni sirket degil" diyordu. Isareti
   silmek de yetmedi — SEC 'O REILLY AUTOMOTIVE INC' diye AYRIK da
   yaziyor. Iki okuma birden.

C) BOS YANIT. `{"value": null}` (HTTP 200) ile "istek basarisiz" ayni
   kovadaydi; CITAS her kosuda `cekilemeyen` yazilip `partial`
   uretiyordu, oysa kagidin fiyati `midas` ve `yahoo_bist`ten geliyor.

EN KRITIK UC MUTASYON
---------------------
A1) Kimlik yine sembolle araniyor — kusurun kendisi.
B3) Ikinci okuma kaldiriliyor; ORLY dali yine kirilir (ilk duzeltmenin
    tam SEC haritasina karsi kosulunca verdigi regresyon).
C4) Toplu sessizlesme sinamasi SONRAYA aliniyor — butun katalog `bos`
    kovasina dusse bile collector "ok" der. Bu kusuru TESTIN KENDISI
    yakaladi, insan degil.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
DB = "src/finagent/storage/db.py"
BIN = "src/finagent/collectors/binance.py"
ID = "src/finagent/research/identity.py"
IS = "src/finagent/collectors/isyatirim.py"

CAKISMA = "test_kimlik_haritasi_SEMBOL_CAKISMASINDA_dogru_venue_yu_secer"
KESME = "test_kimlik_KESME_ISARETI_TASIYAN_sirketi_REDDETMIYOR"
BOS = "test_isyatirim_KAYNAKTA_SERI_YOK_cekilemedi_SAYILMIYOR"
BUTCE = "test_isyatirim_sure_butcesi_kosuyu_kaybetmez"
ADYOK = "test_kimlik_ADI_YOKKEN_baska_sirket_DEMEZ"

M = [
    # ---- A) SEMBOL CAKISMASI -------------------------------------------
    ("A1) Kimlik haritasi yine SEMBOLLE anahtarlaniyor — kusurun kendisi",
     DB, '        return {r["instrument_id"]: r for r in self.identities()}',
     '        return {r["symbol"]: r for r in self.identities()}', CAKISMA),

    ("A2) Collector haritayi kuruyor ama SEMBOLLE soruyor — yarim gecis",
     BIN, '                cift = self._cift(h, kimlikler.get(h["id"]))',
     '                cift = self._cift(h, kimlikler.get(h["symbol"]))', CAKISMA),

    ("A3) ATLAMA SEBEBI yanlis satirdan okunuyor — canli alarm metni",
     BIN, '                    k = kimlikler.get(h["id"])',
     '                    k = kimlikler.get(h["symbol"])', CAKISMA),

    # ---- B) KESME ISARETI ----------------------------------------------
    ("B1) Kesme isareti yine AYIRICI — DPZ yeniden reddedilir",
     ID, '    return _parcala(_kesmesiz(str(ad).upper()))',
     '    return _parcala(str(ad).upper())', KESME),

    ("B2) `_kesmesiz` isareti BOSLUGA ceviriyor — silmiyor",
     ID, '    return _KESME.sub("", ad)', '    return _KESME.sub(" ", ad)', KESME),

    ("B3) IKINCI OKUMA kaldirildi — ORLY dali kirilir (olculmus regresyon)",
     ID, "    okumalar = [ad_belirteci(ad)]\n"
         "    if _KESME.search(duz):",
     "    okumalar = [ad_belirteci(ad)]\n"
     "    if False:", KESME),

    ("B4) Eslesme YALNIZCA ilk okuma cifti ile sinaniyor",
     ID, "    if any(a <= b or b <= a for a in A for b in B):",
     "    if A[0] <= B[0] or B[0] <= A[0]:", KESME),

    ("B5) Altkume yerine KESISIM — 'Global Water' ile 'Global Payments'",
     ID, "    if any(a <= b or b <= a for a in A for b in B):",
     "    if any(a & b for a in A for b in B):", KESME),

    # ---- C) BOS YANIT --------------------------------------------------
    ("C1) Bos seri yine `cekilemeyen` sayiliyor — kalici sahte alarm",
     IS, "            if not rows:\n"
         "                (failed if barlar.get(sym, 0) >= asgari_bar "
         "else bos).append(sym)\n"
         "                continue\n",
     "", BOS),

    ("C2) REGRESYON dali yok — gecmisi olan sembol bosalinca sessiz kalir",
     IS, "                (failed if barlar.get(sym, 0) >= asgari_bar "
         "else bos).append(sym)",
     "                bos.append(sym)", BOS),

    ("C3) Bozuk SOZLESME 'seri yok' diye okunuyor — en tehlikeli sessizlik",
     IS, '        if not isinstance(data, dict) or "value" not in data:\n'
         '            log.warning("[isyatirim] beklenmeyen govde: %.120s", data)\n'
         "            return None",
     '        if not isinstance(data, dict):\n'
     "            return []", BOS),

    ("C4) Toplu sessizlesme sinamasi SONRAYA alindi — hepsi `bos` ise 'ok'",
     IS, "        if symbols and len(failed) + len(bos) == len(symbols):\n"
         '            status = "error"\n'
         "        elif failed or kesildi:\n"
         '            status = "partial"',
     "        if failed or kesildi:\n"
     '            status = ("error" if len(failed) + len(bos) == len(symbols)\n'
     '                      else "partial")\n'
     "        elif False:\n"
     '            status = "partial"', BOS),

    ("C5) Kapsam boslugu NOTA hic yazilmiyor — gorunmez olur",
     IS, '            notlar.append(f"kaynakta seri yok ({len(bos)}): "\n'
         '                          + ", ".join(bos[:8]))',
     "            pass", BOS),

    ("C6) Bos yanit icin de TARAYICI fallback'i yaniyor — butce yenir",
     IS, "        payload = self._fetch_via_httpx(url)\n"
         "        if payload is None and self.browser is not None:",
     "        payload = self._fetch_via_httpx(url)\n"
     "        if not payload and self.browser is not None:", BOS),
]


def _pycache_temizle() -> None:
    for dizin in ("src", "tests"):
        for k in KOK.joinpath(dizin).rglob("__pycache__"):
            shutil.rmtree(k, ignore_errors=True)


def _kos(test: str) -> int:
    return subprocess.run(
        [str(KOK / ".venv/bin/python"), "-c",
         f"import sys; sys.path.insert(0,'tests');"
         f"import test_smoke as T; T.{test}()"],
        cwd=KOK, capture_output=True, text=True, timeout=900).returncode


yakalanan = 0
_pycache_temizle()
for ad, yol, eski, yeni, test in M:
    if _kos(test) != 0:
        print(f"  ! TEST ZATEN KIRMIZI: {ad} [{test}]")
        continue
    p = KOK / yol
    yedek = p.read_text(encoding="utf-8")
    t2 = yedek.replace(eski, yeni)
    if t2 == yedek:
        print(f"  ! UYGULANAMADI: {ad}")
        continue
    p.write_text(t2, encoding="utf-8")
    _pycache_temizle()
    try:
        tamam = _kos(test) == 0
        print(f"  {'✗ YAKALANMADI' if tamam else '✓ yakalandi'}: {ad}")
        yakalanan += 0 if tamam else 1
    finally:
        p.write_text(yedek, encoding="utf-8")
        _pycache_temizle()

print(f"\n{yakalanan}/{len(M)} mutasyon yakalandi · kaynaklar geri alindi")
