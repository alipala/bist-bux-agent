"""
Erken olcum, yutulan ag arizasi ve gonderim kaniti — mutasyon turu
(2026-09-02).

Ali uc maddeyi onayladi ve "varsayim yapmadan, tahmin etmeden, test
ederek" dedi. Ucu de once OLCULDU, sonra yazildi.

A) ERKEN OLCUM KAYDI YAKIYORDU. `olc()` yalnizca `gun_sonu_sonuc IS
   NULL` satirlari seciyor; bar bulunamayinca damga vuruluyor ve satira
   BIR DAHA bakilmiyordu. Olculdu: ali'nin son 30 gunundeki 23
   `olculemedi` satirinin 6'si serisi OLAN enstrumanlara ait, 3'u bugun
   bakildiginda OLCULEBILIR durumda. AMZN yayimdan 9 saat once (piyasa
   kapaliyken) olculmus; REGN yayimdan BES SANIYE sonra.

   ESIK UYDURULMADI: yayimdan ilk bara kadar gecen sureyi ancak 7
   satirda hesaplayabildim (saatlik seri 72 bar tutuyor). Yetersiz
   olcumden esik turetmek "olculdu" gorunumunde bir tahmin olurdu —
   sinir icin satirin ZATEN TASIDIGI `ufuk_gun` kullanildi.

B) AG ARIZASI YUTULUYORDU. `_post` her arizayi `None`a, `get_updates`
   de onu BOS LISTEYE ceviriyordu; yani "ag koptu" ile "mesaj yok"
   dinleyici icin ayni seydi. Olculdu (bot.log): istemci 791 ERROR,
   dinleyici 0 WARNING — ag kesintisi icin yazilmis backoff dali HIC
   CALISMAMIS, ona bagli "Baglanti geri geldi" (0 kez) ve
   `kalp_at(cevrimici=False)` (0 kez) olu kod kalmis.

C) GONDERIM KANITI YOKTU. Basarili bildirim hicbir sey yazmiyordu;
   `_sahibe_bildir` zaten bool donuyordu, eksik olan onu OKUMAKTI.

EN KRITIK UC MUTASYON
---------------------
A1) `ERKEN` dali kaldiriliyor — kusurun kendisi geri gelir.
B1) `get_updates` yine `None`i bos listeye ceviriyor: dinleyici hicbir
    sey gormez, backoff olu kod olur.
C2) Dusen gonderim WARNING'e iniyor — "olcum yapildi ama ULASMADI"
    sessizlesir.

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
GS = "src/finagent/pulse/gun_sonu.py"
TG = "src/finagent/notify/telegram.py"
RN = "src/finagent/pulse/runner.py"

ERKEN = "test_gun_sonu_ERKEN_OLCUM_kaydi_YAKMIYOR_sonra_olculuyor"
AG = "test_telegram_AG_ARIZASINI_YUTMUYOR_dinleyici_backoff_KOSUYOR"
LOG = "test_gun_sonu_bildirimi_GONDERIM_SONUCUNU_logluyor"
SERI = "test_gun_sonu_SAATLIK_YOKSA_olculemedi_YAZILIYOR"

M = [
    # ---- A) ERKEN OLCUM -------------------------------------------------
    ("A1) ERKEN dali yok — bar gelmeden damga vurulur (kusurun kendisi)",
     GS, "        if sebep == ERKEN and not _ufuk_doldu(r, simdi):",
     "        if False:", ERKEN),

    ("A2) SERI YOK da bekletiliyor — yapisal vaka sonsuza dek kuyrukta",
     GS, "    return [], (ERKEN if barlar else SERI_YOK)",
     "    return [], ERKEN", SERI),

    ("A3) Ufuk siniri yok — serisi olen satir sonsuza dek yeniden denenir",
     GS, "        if sebep == ERKEN and not _ufuk_doldu(r, simdi):",
     "        if sebep == ERKEN:", ERKEN),

    ("A4) Ufuk kontrolu TERS — her satir hemen damgalanir",
     GS, "    return (simdi.date() - baslangic).days > ufuk",
     "    return (simdi.date() - baslangic).days <= ufuk", ERKEN),

    ("A5) Tarih okunamayinca BEKLENIYOR — bozuk satir kuyrugu doldurur",
     GS, "    except (TypeError, ValueError):\n        return True",
     "    except (TypeError, ValueError):\n        return False", ERKEN),

    ("A6) Atlanan satira yine `gun_sonu_ts` yaziliyor — sessiz damga",
     GS, "            bekleyen += 1\n            continue\n        if not barlar:",
     "            pass\n        if not barlar:", ERKEN),

    # ---- B) AG ARIZASI --------------------------------------------------
    ("B1) `get_updates` yine ariza yutuyor — backoff olu koda doner",
     TG, "        res = self._post(\"getUpdates\", data=data, _timeout=timeout + 15,\n"
         "                         _sessiz=True, _yukselt=True)",
     "        res = self._post(\"getUpdates\", data=data, _timeout=timeout + 15)",
     AG),

    ("B2) Istisna yukseltilmiyor, yalnizca loglaniyor",
     TG, "            if _yukselt:\n                raise\n"
         "            seviye(\"Telegram %s istegi basarisiz: %s\", method, e)",
     "            seviye(\"Telegram %s istegi basarisiz: %s\", method, e)", AG),

    ("B3) `ok:false` ariza sayilmiyor — Bad Gateway sessiz gecer",
     TG, "            if _yukselt:\n                raise TelegramUlasilamadi(\n"
         "                    f\"{method}: {data.get('description')}\")",
     "            pass", AG),

    ("B4) Hata SEBEBI tasinmiyor — 'hata sebebini yutma' sinifi",
     TG, "                raise TelegramUlasilamadi(\n"
         "                    f\"{method}: {data.get('description')}\")",
     "                raise TelegramUlasilamadi(method)", AG),

    ("B5) TEK ATISLIK cagrilar da firlatiyor — nabzi dusurur",
     TG, "    def _post(self, method: str, _timeout: float = 30.0, _sessiz: bool = False,\n"
         "              _yukselt: bool = False, **kwargs) -> dict | None:",
     "    def _post(self, method: str, _timeout: float = 30.0, _sessiz: bool = False,\n"
     "              _yukselt: bool = True, **kwargs) -> dict | None:", AG),

    # ---- C) GONDERIM KANITI ---------------------------------------------
    ("C1) Gonderim sonucu OKUNMUYOR — 'cagirdim' yine 'gitti' sayilir",
     RN, "                if self._sahibe_bildir(sahip, metin):",
     "                if self._sahibe_bildir(sahip, metin) or True:", LOG),

    ("C2) Dusen gonderim WARNING — sessizlesir",
     RN, "                    log.error(\"[%s] gun sonu bildirimi GONDERILEMEDI (%s) — \"",
     "                    log.debug(\"[%s] gun sonu bildirimi GONDERILEMEDI (%s) — \"", LOG),

    ("C3) Basari satiri hic yazilmiyor — kanit yine yok",
     RN, "                    log.info(\"[%s] gun sonu bildirimi GONDERILDI (%s): \"",
     "                    log.debug(\"[%s] gun sonu bildirimi GONDERILDI (%s): \"", LOG),
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
