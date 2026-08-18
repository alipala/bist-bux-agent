"""
Sohbet isleri icin DAYANIKLI KUYRUK ve ZAMANLAYICI.

NEDEN
-----
Bot tek is parcacikliydi: `_dispatch` bitene kadar `getUpdates` bir daha
CAGRILMIYORDU. Olculdu (2026-08-17, commit e970151): `isyatirim`
collector'i bir sohbet turunda 24,9 dakika surdu ve o sure boyunca
IKINCI KULLANICI da bloke kaldi. Tek kullanicida "yavas", iki
kullanicida "bot bozuk" demek.

NEDEN IS PARCACIGI (THREAD) DEGIL SUREC
---------------------------------------
1. `storage/db.py` tek bir `sqlite3` baglantisi tutuyor ve
   `check_same_thread` varsayilani True — baska bir is parcacigindan
   kullanmak `ProgrammingError` firlatir.
2. Bir is parcacigi OLDURULEMEZ. Asilan turu kesmenin tek yolu botu
   oldurmek olurdu; olculen 24,9 dakikalik arizada elle yapilan sey tam
   buydu.
3. Sert cokme (OOM/kill) tum botu goturur; ayri surecte yalnizca o turu.
Bedeli olculdu: `run.py` soguk baslangic + sema init = 0,35 sn. Zaten her
sohbet turu bir `claude` CLI alt sureci doguruyor.

SOZLESME — bes madde
--------------------
1. **IS DOSYALARININ TEK YAZARI ANA SUREC.** Worker yalnizca iki TEK
   YONLU isaret birakir: `<id>.hb` (kalp atisi) ve `<id>.bitti` (is
   tamamlandi). Boylece "iki yazar ayni dosyayi bozdu" sinifi hic
   dogmuyor.
2. **BIR SOHBETTE AYNI ANDA TEK IS.** Sohbet gecmisi
   (`data/bot/sohbet/<chat_id>.json`) ve 20 dakikalik anlik goruntu
   birlestirme penceresi buna bagli; iki tur ust uste binerse ikisi de
   ayni gecmisi okuyup birbirini ezer.
3. **CEVABI WORKER GONDERIR.** Ana surec olse bile is tamamlanir. Bu
   yuzden planli restart artik ucustaki turu ne OLDURUR ne CIFTLER —
   yeniden baslayan ana surec kalp atisini gorup sahiplenir.
4. **YENIDEN DENEME YALNIZCA SERT COKMEDE.** Istisna zaten worker icinde
   yakalanip kullaniciya bildiriliyor (`.bitti` yazilir). Zaman asimi da
   bir SONUCTUR; tekrarlamak ayni duvara ikinci kez toslamak olurdu.
5. **BITEN IS HATIRLANIR.** `bitmis.dat` son N `update_id`'yi tutar.
   Enqueue ile offset yazimi arasinda cokme olursa Telegram guncellemeyi
   YENIDEN gonderir; bu halka onun iki kez islenmesini engeller.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)


class Kuyruk:
    # Worker bu sıklıkta `<id>.hb` dosyasina dokunur.
    KALP_ARALIGI_SN = 20.0
    # Bu kadar sessiz kalan worker OLMUS sayilir. Aralikta uc atislik pay
    # var: yuklu makinede bir atis gecikirse is bosuna yeniden denenmesin.
    TERK_ESIGI_SN = 90.0
    # Toplam deneme. 2 = sert cokmede BIR kez yeniden dene. Daha fazlasi,
    # yarim kalmis yan etkileri (asili onay dosyasi, gonderilmis ara
    # mesaj) tekrar tekrar uretirdi.
    AZAMI_DENEME = 2
    # Biten islerin hatirlandigi pencere.
    BITMIS_TAVANI = 200

    def __init__(self, dizin, *, kok=None, azami_worker: int = 2,
                 zaman_asimi_sn: float = 900.0, baslat=None, bildir=None,
                 simdi=time.time):
        self.dizin = Path(dizin)
        self.dizin.mkdir(parents=True, exist_ok=True)
        self.kok = Path(kok) if kok else Path.cwd()
        self.azami_worker = max(1, int(azami_worker))
        self.zaman_asimi_sn = float(zaman_asimi_sn)
        self._baslat = baslat or self._varsayilan_baslat
        self._bildir_cb = bildir
        self._simdi = simdi
        # update_id -> Popen. YALNIZCA bu kosuda baslatilanlar burada.
        # Onceki kosudan devralinanlarin Popen'i YOKTUR ve canlilik
        # kalp atisindan okunur — ikisi bilerek ayri yollar.
        self._surecler: dict[int, subprocess.Popen] = {}

    # --- dosya yollari ---------------------------------------------------
    def _yol(self, uid) -> Path:
        return self.dizin / f"{int(uid)}.json"

    def _hb_yolu(self, uid) -> Path:
        return self.dizin / f"{int(uid)}.hb"

    def _bitti_yolu(self, uid) -> Path:
        return self.dizin / f"{int(uid)}.bitti"

    @property
    def _bitmis_yolu(self) -> Path:
        # `.dat` bilerek: `_oku_hepsi()` `*.json` topluyor ve bu dosya
        # ORAYA KARISMAMALI.
        return self.dizin / "bitmis.dat"

    # --- okuma / yazma ---------------------------------------------------
    def _oku_hepsi(self) -> list[dict]:
        """Tum isler, update_id sirasinda (= gelis sirasi)."""
        out = []
        for yol in self.dizin.glob("*.json"):
            try:
                veri = json.loads(yol.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                # BOZUK IS DOSYASI SESSIZCE DURMAZ. Islenemez ama
                # gorunmez de kalmamali; yoksa kuyruk sonsuza dek "dolu"
                # gorunur ve hicbir sey baslamaz.
                log.error("[kuyruk] bozuk is dosyasi siliniyor: %s (%s)", yol, e)
                yol.unlink(missing_ok=True)
                continue
            if isinstance(veri, dict) and "update_id" in veri:
                out.append(veri)
        return sorted(out, key=lambda i: int(i["update_id"]))

    def _yaz(self, is_: dict) -> None:
        """
        ATOMIK YAZIM. `fsync` + `os.replace`: yarim yazilmis bir is
        dosyasi, kaybolmus bir is dosyasindan DAHA KOTUDUR — ilki
        kuyrugu tikar, ikincisi Telegram'dan yeniden gelir.
        """
        yol = self._yol(is_["update_id"])
        gecici = yol.with_suffix(".tmp")
        with open(gecici, "w", encoding="utf-8") as f:
            json.dump(is_, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(gecici, yol)

    def _bitmis_oku(self) -> list[int]:
        try:
            return [int(s) for s in
                    self._bitmis_yolu.read_text(encoding="utf-8").split()]
        except (OSError, ValueError):
            return []

    def _bitmis_ekle(self, uid) -> None:
        kayit = [u for u in self._bitmis_oku() if u != int(uid)]
        kayit.append(int(uid))
        try:
            self._bitmis_yolu.write_text(
                " ".join(str(u) for u in kayit[-self.BITMIS_TAVANI:]),
                encoding="utf-8")
        except OSError as e:                          # noqa: BLE001
            log.warning("[kuyruk] bitmis kaydi yazilamadi: %s", e)

    def _temizle(self, uid) -> None:
        self._yol(uid).unlink(missing_ok=True)
        self._hb_yolu(uid).unlink(missing_ok=True)
        self._bitti_yolu(uid).unlink(missing_ok=True)
        self._surecler.pop(int(uid), None)
        self._bitmis_ekle(uid)

    def _bildir(self, chat_id, metin: str) -> None:
        """Bildirim BIR YAN ETKIDIR; kuyrugu asla durdurmamali."""
        if not self._bildir_cb or chat_id is None:
            return
        try:
            self._bildir_cb(chat_id, metin)
        except Exception as e:                        # noqa: BLE001
            log.warning("[kuyruk] bildirim gonderilemedi: %s", e)

    # --- disari acik ------------------------------------------------------
    def ekle(self, update: dict, chat_id) -> bool:
        """Isi kuyruga yazar. Zaten varsa ya da islenmisse False doner."""
        uid = int(update["update_id"])
        if self._yol(uid).exists():
            log.info("[kuyruk] %s zaten kuyrukta", uid)
            return False
        if uid in self._bitmis_oku():
            # Enqueue ile offset yazimi arasindaki cokme penceresi.
            log.info("[kuyruk] %s zaten islenmisti, tekrar islenmiyor", uid)
            return False
        self._yaz({"update_id": uid, "chat_id": chat_id, "durum": "bekliyor",
                   "deneme": 1, "olusma": self._simdi(), "update": update})
        log.info("[kuyruk] %s kuyruga alindi (chat %s)", uid, chat_id)
        return True

    def durumu(self, uid) -> str | None:
        try:
            return json.loads(
                self._yol(uid).read_text(encoding="utf-8")).get("durum")
        except (OSError, json.JSONDecodeError):
            return None

    def bekleyen_var(self) -> bool:
        """
        SIRA BEKLEYEN is var mi? Yoklama araligini bu belirliyor.

        Olcut "calisan is var mi" DEGIL: hizli yoklamanin tek sebebi
        bosalan yuvayi cabuk fark etmek. Kimse siradaysa, 5 dakikalik
        bir analiz boyunca 150 kez `getUpdates` cagirmak bedava kazanc
        degil bedava masraftir.
        """
        return any(i.get("durum") == "bekliyor" for i in self._oku_hepsi())

    def onunde(self, uid) -> int:
        """Bu isten ONCE gelmis, henuz bitmemis is sayisi."""
        return sum(1 for i in self._oku_hepsi()
                   if int(i["update_id"]) < int(uid))

    def sayim(self) -> tuple[int, int]:
        isler = self._oku_hepsi()
        calisan = sum(1 for i in isler if i["durum"] == "calisiyor")
        return len(isler) - calisan, calisan

    def tik(self) -> None:
        """Bir tur: biten/coken isleri topla, bosalan yuvalara is ver."""
        self.topla()
        self.dagit()

    # --- kurtarma ---------------------------------------------------------
    def kurtar(self) -> None:
        """
        Yeniden baslarken onceki kosunun isleri.

        UC HAL VAR ve UCU DE FARKLI:
          * `.bitti` var       -> is tamamlanmis, cevap gitmis. Temizle.
          * kalp atiyor        -> worker YASIYOR (ana surec olmus, cocuk
                                  degil). DOKUNMA; cevabini kendi
                                  gonderecek. Yeniden calistirmak
                                  kullaniciya IKI cevap gonderirdi.
          * kalp durmus        -> worker olmus, cevap hic gitmemis.
                                  Yeniden dene.
        """
        for is_ in self._oku_hepsi():
            uid = int(is_["update_id"])
            if is_.get("durum") != "calisiyor":
                continue
            if self._bitti_yolu(uid).exists():
                log.info("[kuyruk] %s onceki kosuda tamamlanmis, temizlendi", uid)
                self._temizle(uid)
            elif self._kalp_taze(is_):
                log.warning("[kuyruk] %s onceki kosudan DEVAM EDIYOR (pid %s) — "
                            "cevabini kendisi gonderecek", uid, is_.get("pid"))
            else:
                log.warning("[kuyruk] %s yarim kalmis (kalp atmiyor)", uid)
                self._cokme(is_)

    # --- zamanlama --------------------------------------------------------
    def dagit(self) -> None:
        isler = self._oku_hepsi()
        calisan = [i for i in isler if i.get("durum") == "calisiyor"]
        mesgul_sohbet = {str(i.get("chat_id")) for i in calisan}
        bos = self.azami_worker - len(calisan)
        for is_ in isler:
            if bos <= 0:
                return
            if is_.get("durum") != "bekliyor":
                continue
            # SOHBET BASINA TEK IS — sozlesme 2.
            if str(is_.get("chat_id")) in mesgul_sohbet:
                continue
            if self._basla(is_):
                mesgul_sohbet.add(str(is_.get("chat_id")))
                bos -= 1

    def _basla(self, is_: dict) -> bool:
        uid = int(is_["update_id"])
        # Onceki denemeden kalan isaretler YENI denemeyi kandirmamali.
        self._hb_yolu(uid).unlink(missing_ok=True)
        self._bitti_yolu(uid).unlink(missing_ok=True)
        try:
            proc = self._baslat(self._yol(uid))
        except Exception as e:                        # noqa: BLE001
            log.exception("[kuyruk] worker baslatilamadi: %s", uid)
            self._bildir(is_.get("chat_id"),
                         f"❌ Bu mesaji isleyemedim: alt surec baslatilamadi "
                         f"({type(e).__name__}).")
            self._temizle(uid)
            return False
        is_.update(durum="calisiyor", pid=getattr(proc, "pid", None),
                   baslama=self._simdi())
        self._yaz(is_)
        self._surecler[uid] = proc
        log.info("[kuyruk] %s BASLADI (chat %s, pid %s, deneme %s)",
                 uid, is_.get("chat_id"), is_.get("pid"), is_.get("deneme"))
        return True

    def _varsayilan_baslat(self, is_yolu: Path):
        # `start_new_session=True` KRITIK: `launchctl kill TERM` sinyali
        # SUREC GRUBUNA gider. Ayni grupta kalsalardi her planli restart
        # ucustaki turlari da oldururdu — kacinmak istedigimiz seyin ta
        # kendisi (sozlesme 3).
        return subprocess.Popen(
            [sys.executable, "run.py", "bot-worker", "--is", str(is_yolu)],
            cwd=str(self.kok), start_new_session=True)

    # --- toplama ----------------------------------------------------------
    def topla(self) -> None:
        simdi = self._simdi()
        for is_ in self._oku_hepsi():
            if is_.get("durum") != "calisiyor":
                continue
            uid = int(is_["update_id"])
            if self._bitti_yolu(uid).exists():
                log.info("[kuyruk] %s bitti", uid)
                self._temizle(uid)
                continue
            proc = self._surecler.get(uid)
            canli = (proc.poll() is None) if proc is not None \
                else self._kalp_taze(is_)
            if not canli:
                self._cokme(is_)
                continue
            gecen = simdi - float(is_.get("baslama") or simdi)
            if gecen > self.zaman_asimi_sn:
                self._zaman_asimi(is_, proc, gecen)

    def _kalp_taze(self, is_: dict) -> bool:
        """
        Devralinan is icin tek canlilik olcutu.

        PID KULLANILMIYOR: PID yeniden kullanilir ve "o pid yasiyor"
        cumlesi "benim worker'im yasiyor" demek DEGILDIR. Kalp atisi bu
        belirsizligi tasimaz.

        Henuz `.hb` yoksa BASLAMA damgasi referans alinir — worker'in
        ilk atisa ulasmasi ~0,5 sn suruyor ve o aralikta "olmus" demek
        her isi bir kez fazladan calistirirdi.
        """
        uid = int(is_["update_id"])
        hb = self._hb_yolu(uid)
        try:
            ref = hb.stat().st_mtime
        except OSError:
            ref = float(is_.get("baslama") or 0.0)
        return (self._simdi() - ref) < self.TERK_ESIGI_SN

    def _cokme(self, is_: dict) -> None:
        uid = int(is_["update_id"])
        deneme = int(is_.get("deneme", 1))
        if deneme >= self.AZAMI_DENEME:
            log.error("[kuyruk] %s %d denemede de cokti, PES EDILDI", uid, deneme)
            self._bildir(is_.get("chat_id"),
                         "❌ Bu mesaji isleyemedim — islerken surec cokti "
                         f"({deneme} deneme). Tekrar yazar misin?")
            self._temizle(uid)
            return
        log.warning("[kuyruk] %s cokti, yeniden denenecek (%d -> %d)",
                    uid, deneme, deneme + 1)
        is_.update(durum="bekliyor", deneme=deneme + 1, pid=None, baslama=None)
        self._yaz(is_)
        self._surecler.pop(uid, None)
        self._hb_yolu(uid).unlink(missing_ok=True)

    def _zaman_asimi(self, is_: dict, proc, gecen: float) -> None:
        uid = int(is_["update_id"])
        log.error("[kuyruk] %s %.0f sn'de bitmedi, DURDURULUYOR", uid, gecen)
        self._oldur(is_, proc)
        # YENIDEN DENENMEZ (sozlesme 4): ayni is ayni sureyi yine alir.
        self._bildir(is_.get("chat_id"), (
            f"⏱ Bu istek {int(self.zaman_asimi_sn // 60)} dakikada bitmedi, "
            "durdurdum.\nDaha dar bir soru sorabilir ya da veri cekmeyi "
            "gece nabzina birakabilirsin."))
        self._temizle(uid)

    def _oldur(self, is_: dict, proc) -> None:
        if proc is not None:
            try:
                proc.kill()
            except Exception as e:                    # noqa: BLE001
                log.warning("[kuyruk] surec oldurulemedi: %s", e)
            return
        # DEVRALINAN IS: elimizde yalnizca PID var ve PID yeniden
        # kullanilabilir. Yanlis sureci oldurmemek icin komut satirinda
        # BU IS DOSYASININ adini ariyoruz.
        pid = is_.get("pid")
        if not pid:
            return
        if not self._gercekten_worker_mi(int(pid), self._yol(is_["update_id"])):
            log.warning("[kuyruk] pid %s bizim worker'imiz degil, dokunulmadi", pid)
            return
        try:
            os.kill(int(pid), 9)
        except OSError as e:
            log.warning("[kuyruk] pid %s oldurulemedi: %s", pid, e)

    @staticmethod
    def _gercekten_worker_mi(pid: int, is_yolu: Path) -> bool:
        try:
            cikti = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                   capture_output=True, text=True, timeout=5)
        except Exception:                             # noqa: BLE001
            return False
        return is_yolu.name in (cikti.stdout or "")


class KalpAtisi:
    """
    Worker'in "yasiyorum" isareti — dosya mtime'i.

    Icerik yazilmiyor cunku okunmuyor: tek soru "ne zaman dokunuldu".
    Is parcacigi DAEMON; worker cikarken beklemeye gerek yok.
    """

    def __init__(self, yol, aralik: float = Kuyruk.KALP_ARALIGI_SN):
        self.yol = Path(yol)
        self.aralik = float(aralik)
        self._dur = threading.Event()
        self._is = None

    def at(self) -> None:
        try:
            self.yol.touch()
        except OSError as e:                          # noqa: BLE001
            log.debug("kalp atisi yazilamadi: %s", e)

    def _dongu(self) -> None:
        while not self._dur.wait(self.aralik):
            self.at()

    def __enter__(self):
        self.at()                                     # ilk atis HEMEN
        self._is = threading.Thread(target=self._dongu, daemon=True,
                                    name="kalp-atisi")
        self._is.start()
        return self

    def __exit__(self, *_exc):
        self._dur.set()
        return False
