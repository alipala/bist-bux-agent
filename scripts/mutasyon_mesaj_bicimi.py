"""
Mesaj bicimi ve gundem blogu mutasyon turu (2026-09-02).

Ali iki ekran goruntusuyle bildirdi: "text formati hic ama hic okunur
degil" ve "o gunun kayda deger borsa haberlerinin de ozetleri olsa
harika olur".

IKI KOK SEBEP
-------------
A) YAZIM KURALI YANLIS YERE UYGULANMIS. Deponun "Turkce ASCII" kurali
   KAYNAK/COMMIT/BELGE icindi; kullaniciya giden SABLON metinlerine de
   uygulanmisti. Sonuc, tek mesajda iki alfabe: sablon "olculdu", panel
   (LLM ciktisi) "ölçüldü". Ayrica dort borsa ve hesap detayi tek
   satirda ` · ` ile diziliyor, telefonda ORTASINDAN sariyordu.

B) GUNDEM VERISI VARDI, OKUYAN YOKTU. `news.konu` doluyor ve gunluk
   RAPOR onu okuyor; NABIZ MESAJI hic okumuyordu.

EN KRITIK UC MUTASYON
---------------------
B1) `_gundem_satirlari` ozete hic baglanmiyor — kablo kacisi. Fonksiyon
    dogru, testi yesil, kullanici hicbir sey gormez.
B2) Blok her kipte gonderiliyor: pencere 24 saat oldugu icin ayni uc
    baslik gunde DORT kez tekrarlanir — Ali'nin sikayetinin ta kendisi.
B4) Asgari ortak sozcuk sarti kalkiyor: "Borsa gune dususle basladi"
    (sabah) ile "Borsa gunu dususle kapatti" (aksam) TEK habere iniyor.
    Kapsama olcutunun tek basina neden yetmedigini bu gosteriyor.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
RN = "src/finagent/pulse/runner.py"
PY = "src/finagent/piyasa.py"
PF = "src/finagent/analysis/portfolio.py"

GUNDEM = "test_gundem_blogu_AYNI_HABERI_TEKRARLAMIYOR_ve_NABZA_BAGLI"
TURKCE = "test_mesaj_metinleri_TAM_TURKCE_ASCII_kurali_KODA_ait"
SEANS = "test_seans_satiri_ACILIS_ANINI_soyluyor"
BAYAT = "test_ozet_portfoy_ADETLERIN_YASINI_beyan_ediyor"
KUR = "test_ozet_portfoy_satiri_KUR_ETKISINI_beyan_ediyor"

M = [
    # ---- B) GUNDEM BLOGU -----------------------------------------------
    ("B1) Gundem blogu URETILIYOR ama OZETE BAGLANMIYOR — kablo kacisi",
     RN, "        if kip == self.GUN_SONU_BILDIRIM_KIPI:\n"
         "            L.extend(self._gundem_satirlari())",
     "        if False:\n"
     "            L.extend(self._gundem_satirlari())", GUNDEM),

    ("B2) Her kipte gonderiliyor — ayni haber gunde DORT kez",
     RN, "        if kip == self.GUN_SONU_BILDIRIM_KIPI:\n"
         "            L.extend(self._gundem_satirlari())",
     "        if True:\n"
     "            L.extend(self._gundem_satirlari())", GUNDEM),

    ("B3) Kok kirpma yok — ayni haber uc ayri satir olur (kitap/kitabi)",
     RN, "        return frozenset(s[:Nabiz.GUNDEM_KOK_HARF]\n"
         "                         for s in sozcukler if len(s) > 2)",
     "        return frozenset(s for s in sozcukler if len(s) > 2)", GUNDEM),

    ("B4) ASGARI ORTAK SOZCUK sarti yok — AYRI olaylar birlesir",
     RN, "                if (ortak >= self.GUNDEM_ASGARI_ORTAK\n"
         "                        and ortak / kisa >= self.GUNDEM_BENZERLIK):",
     "                if ortak / kisa >= self.GUNDEM_BENZERLIK:", GUNDEM),

    ("B5) Kapsama yerine JACCARD — Bej Kitap uce bolunur",
     RN, "                kisa = min(len(anahtar), len(onceki[\"anahtar\"])) or 1",
     "                kisa = len(anahtar | onceki[\"anahtar\"]) or 1", GUNDEM),

    ("B6) Kaynak sayisi dusuruluyor — onemin tek olculebilir isareti",
     RN, '                ek = f" <i>({n} kaynak)</i>" if n > 1 else ""',
     '                ek = ""', GUNDEM),

    ("B7) Kademe 3 de kanit sayiliyor — rapordan FARKLI esik",
     RN, "                    WHERE tier IN (1, 2)", "                    WHERE tier IN (1, 2, 3)",
     GUNDEM),

    ("B8) `sirket` de gundeme giriyor — blok sirket haberiyle dolar",
     RN, "        from ..research.konular import GUNDEM_KONULARI",
     "        from ..research.konular import KONULAR as GUNDEM_KONULARI", GUNDEM),

    # ---- A) YAZIM VE BICIM ---------------------------------------------
    ("A1) Ay kisaltmalari yine ASCII — mesaj iki alfabede",
     RN, '_AY_KISA = ("Oca", "Şub", "Mar", "Nis", "May", "Haz",\n'
         '            "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")',
     '_AY_KISA = ("Oca", "Sub", "Mar", "Nis", "May", "Haz",\n'
     '            "Tem", "Agu", "Eyl", "Eki", "Kas", "Ara")', TURKCE),

    ("A2) Hafta sonu yine dort borsa dort kez yaziliyor",
     PY, '    if all(s["durum"] == "hafta sonu" for s in durumlar):\n'
         '        return "Hafta sonu — borsalar kapalı"',
     '    if False:\n'
     '        return "Hafta sonu — borsalar kapalı"', TURKCE),

    ("A3) Hepsi kapaliyken tek cumle YOK — satir yine uc satira sarar",
     PY, '    if all(s["durum"] == "kapandi" for s in durumlar):',
     "    if False:", TURKCE),

    ("A4) En son kapanan degil, EN ERKEN kapanan referans aliniyor",
     PY, '        son = min(durumlar, key=lambda s: s["kapanali_dk"])',
     '        son = max(durumlar, key=lambda s: s["kapanali_dk"])', TURKCE),

    ("A5) Portfoy notu yine ASCII — kullaniciya giden metin",
     PF, '"not": "kur etkisi hariç (fiyat hareketi)",',
     '"not": "kur etkisi haric (fiyat hareketi)",', TURKCE),

    ("A6) Bayatlik cumlesi tek satira geri doner — sarma sorunu geri gelir",
     RN, '                        alt += (f" · {yas} gün önceki ekran görüntüsü, "\n'
         '                                "arada işlem yaptıysan ağırlıklar eski")',
     '                        alt += (f" · {yas} gun onceki ekran goruntusu, "\n'
     '                                "arada islem yaptiysan agirliklar eski")', BAYAT),
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
