"""
Tur olcumu mutasyon turu — sema 27 (2026-08-31).

OLCULEN BOSLUK: Ali "hangi modeli kullaniyoruz, baglam penceresini nasil
yonetiyoruz?" diye sordu ve cevaplanamadi — hicbir yerde token sayaci
YOKTU. `bot.log` icinde 272 'token' eslesmesi vardi, hepsi Python
traceback'lerindeki degisken adlariydi.

Oysa SDK bunu ZATEN donduruyordu (`ResultMessage.usage`,
`total_cost_usd`, `num_turns`, `duration_ms`). Akis dongusu `content`
alani olmadigi icin o mesaji `continue` ile atliyordu — veri tam
oradan gecip cope gidiyordu.

ZINCIR:
  1. sonuc mesaji ALANLARDAN taninmali (sinif adindan degil)   (B)
  2. `continue`DAN ONCE yakalanmali                            (A)
  3. olculmeyen alan NULL kalmali, 0 olmamali                  (C)
  4. kolonlar SABIT listeden gelmeli                           (D)
  5. cagri yeri sahip/chat_id gecirmeli (konumsal!)            (E)
  6. olcum CEVABI DUSURMEMELI                                  (F)

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
OL = "src/finagent/bot/olcum.py"
CH = "src/finagent/bot/chat.py"
DB = "src/finagent/storage/db.py"

TANI = "test_olcum_SONUC_MESAJINI_sinif_adiyla_DEGIL_alanlarla_taniyor"
SIFIR = "test_olcum_OLCULMEYENI_SIFIR_yazmiyor"
YAZIM = "test_tur_olcumu_YAZILIYOR_ve_bilinmeyen_alan_DUSUYOR"
KABLO = "test_olcum_KABLO_KACISI_yok_sonuc_mesaji_gercekten_yakalaniyor"
DUSMEZ = "test_olcum_CEVABI_DUSURMEZ"

M = [
    ("A) SONUC MESAJI `continue`DAN SONRA yakalaniyor — hic olculmez",
     CH, "            if _olcum.sonuc_mesaji_mi(mesaj):\n"
         "                olcum_ham = _olcum.turdan_olcum(mesaj)\n"
         "            icerik = getattr(mesaj, \"content\", None)\n"
         "            if icerik is None:\n"
         "                continue",
     "            icerik = getattr(mesaj, \"content\", None)\n"
     "            if icerik is None:\n"
     "                continue\n"
     "            if _olcum.sonuc_mesaji_mi(mesaj):\n"
     "                olcum_ham = _olcum.turdan_olcum(mesaj)", KABLO),

    ("A2) OLCUM HIC YAZILMIYOR — kablo tamamen kesik",
     CH, "            self.db.tur_olcumu_yaz(olcum_ham)",
     "            pass  # olcum yazilmiyor", KABLO),

    ("B) SONUC mesaji SINIF ADIYLA taniniyor — SDK surumu degisince susar",
     OL, "    return (hasattr(mesaj, \"usage\")\n"
         "            and hasattr(mesaj, \"duration_ms\")\n"
         "            and getattr(mesaj, \"content\", None) is None)",
     "    return type(mesaj).__name__ == \"ResultMessage\"", TANI),

    ("C) OLCULMEYEN alan SIFIR yaziliyor — ortalamalar sessizce bozulur",
     OL, "def _sayi(v: Any) -> int | None:\n"
         "    if v is None or isinstance(v, bool):\n"
         "        return None",
     "def _sayi(v: Any) -> int | None:\n"
     "    if v is None or isinstance(v, bool):\n"
     "        return 0", SIFIR),

    ("C2) LOG SATIRI olculmeyeni 0 gosteriyor",
     OL, '        return bicim.format(v) if v is not None else "?"',
     '        return bicim.format(v) if v is not None else "0"', SIFIR),

    ("D) KOLONLAR cagiranin sozlugunden uretiliyor — yazim hatasi kolon ister",
     DB, "        veri = {k: olcum.get(k) for k in self.TUR_OLCUM_ALANLARI}",
     "        veri = dict(olcum)", YAZIM),

    ("E) CAGRI YERI sahip/chat_id gecirmiyor — olcum satirlari sahipsiz",
     CH, "self.s.gorunen_ad(sahip), ilerleme, sahip, chat_id)",
     "self.s.gorunen_ad(sahip), ilerleme)", KABLO),

    ("F) OLCUM YAZIMI genis yakalama DISINDA — patlarsa TUR DUSER",
     CH, "        except Exception as e:                        # noqa: BLE001\n"
         "            # OLCUM HICBIR KOSULDA CEVABI DUSURMEZ.",
     "        except ValueError as e:\n"
     "            # OLCUM HICBIR KOSULDA CEVABI DUSURMEZ.", DUSMEZ),
]


def _pycache_temizle() -> None:
    """
    Bayat `.pyc` MUTASYONU GORUNMEZ KILAR.

    OLCULDU 2026-08-31: tek karakterlik bir mutasyon (`"?"` -> `"0"`)
    dosya BOYUTUNU degistirmiyor ve Python'un import kontrolu
    (mtime, size) bayat bayt kodu yeniden kullanabiliyor. Ayni mutasyon
    bir kosumda KACTI, sonrakinde yakalandi — yani harness'in kendisi
    guvenilmezdi ve "N/N yakalandi" raporu bunu gizliyordu.

    Temizlik artik testten ONCE de yapiliyor.
    """
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
    _pycache_temizle()
    try:
        tamam = _kos(test) == 0
        print(f"  {'✗ YAKALANMADI' if tamam else '✓ yakalandi'}: {ad}")
        yakalanan += 0 if tamam else 1
    finally:
        p.write_text(yedek, encoding="utf-8")
        _pycache_temizle()

print(f"\n{yakalanan}/{len(M)} mutasyon yakalandi · kaynaklar geri alindi")
