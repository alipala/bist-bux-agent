"""
Bekci — sistemin kendi kesintilerini FARK EDIP RAPOR ETMESI.

TEMEL SINIR: COKEN SISTEM "COKTUM" DIYEMEZ
------------------------------------------
Bu modul her seyi cozmez, cozemez. Mac kapaliyken ya da internet yokken
hicbir yerel surec mesaj gonderemez. Yapabilecegi tek durust sey:
GERI DONDUGUNDE ne kadar sure kapali kaldigini SOYLEMEK.

"Su anda kapaliyim" bildirimi ancak DISARIDAN bir izleyiciyle mumkun
(dead man's switch); bunun icin `disari_ping()` var ve ayri bir servise
periyodik sinyal gonderir — sinyal kesilirse O servis haber verir.

UC KATMAN
---------
1. KALP ATISI  Bot her dongude zaman damgasi yazar. Acilista onceki damga
   ile simdi karsilastirilir; fark buyukse kesinti yasanmis demektir.
2. NABIZ GOZCUSU  Zamanlanmis nabiz calisti mi? Calismadiysa SESSIZ
   BASARISIZLIK olur — en tehlikeli arizadir, cunku hicbir sey olmamis
   gibi gorunur.
3. DIS PING  Makine tamamen kapandiginda haber alabilmenin tek yolu.

SPAM ONLEME
-----------
Bot yapilandirma hatasiyla surekli yeniden basliyorsa (launchd
ThrottleInterval 60 sn), her kalkista mesaj atmak dakikada bir bildirim
demektir. Bu yuzden bildirimler `SESSIZLIK_SURESI` boyunca susturulur ve
kisa yeniden baslatmalar (deploy gibi) esik altinda kalir.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Bu suren altindaki kesinti RAPOR EDILMEZ: elle yeniden baslatma,
# guncelleme, kisa bir cokme. Gurultu yapmanin anlami yok.
ONEMLI_KESINTI = timedelta(minutes=10)

# Ayni turden bildirim bu sure icinde TEKRARLANMAZ.
SESSIZLIK_SURESI = timedelta(hours=6)

# Kalp atisi bu araliktan seyrek yazilmaz (long-poll 50 sn surdugu icin
# dongu basi yazmak zaten ~1 dakikada bir demek).
KALP_ARALIGI = timedelta(minutes=2)

# DIS PING araligi. Dongu basi atmak gunde ~1.700 istek ederdi; gereksiz.
# Izleyicinin periyodundan BELIRGIN sik olmali ki gecici bir ag hatasi
# yanlis alarm uretmesin: 1 saatlik periyotta 5 dakikada bir ping demek,
# alarm calmadan once 12 sansimiz var demek.
PING_ARALIGI = timedelta(minutes=5)


def _simdi() -> datetime:
    """UTC. Kalp atisi ve kesinti olcumu icin — mutlak sure gerekiyor."""
    return datetime.now(timezone.utc)


def _yerel() -> datetime:
    """
    YEREL saat. ZAMANLANMIS ISLERI yargilamak icin — launchd saatleri
    yerel (nabiz 22:15, hafif kosular 09:30 ve 18:00). UTC ile bakmak
    saat farki kadar kaydiriyordu: CEST'te (UTC+2) `hour >= 23` kontrolu
    01:00-01:59 YERELE denk geliyor ve Ali alarmlari tam orada aldi.
    """
    return datetime.now().astimezone()


class Bekci:
    def __init__(self, settings, db, state_dir):
        self.s = settings
        self.db = db
        self.state_dir = state_dir
        self.dosya = state_dir / "watchdog.json"
        self._son_yazim = None
        self._son_ping = None

    # ------------------------------------------------------------------
    def _oku(self) -> dict:
        try:
            return json.loads(self.dosya.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _yaz(self, veri: dict) -> None:
        try:
            self.dosya.write_text(json.dumps(veri, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        except OSError as e:                          # noqa: BLE001
            log.warning("[bekci] durum yazilamadi: %s", e)

    # --- 1) kalp atisi ------------------------------------------------
    def kalp_at(self, cevrimici: bool = True) -> None:
        """
        Dongu basi cagrilir. Diske her seferinde yazmaz — long-poll zaten
        ~1 dk surdugu icin dakikada bir yazim gereksiz I/O olurdu.

        `cevrimici=False`: Telegram'a ulasilamiyor. Bunu AYRI kaydediyoruz
        cunku "bot calisiyordu ama internet yoktu" ile "bot oluydu" farkli
        arizalar ve kullaniciya farkli sey soylenmeli.
        """
        n = _simdi()
        if self._son_yazim and n - self._son_yazim < KALP_ARALIGI:
            return
        self._son_yazim = n
        d = self._oku()
        d["son_kalp"] = n.isoformat()
        d["pid"] = os.getpid()
        if cevrimici:
            d["son_cevrimici"] = n.isoformat()
        self._yaz(d)

    def kesinti(self) -> dict | None:
        """
        Acilista cagrilir. Onceki kalp atisiyla simdi arasindaki fark.

        Doner: {"sure_dk", "bas", "son", "tur"} veya None (kesinti yok /
        ilk calistirma / esigin altinda).
        """
        d = self._oku()
        ham = d.get("son_kalp")
        if not ham:
            return None                              # ilk kez calisiyor
        try:
            son = datetime.fromisoformat(ham)
        except ValueError:
            return None
        n = _simdi()
        fark = n - son
        if fark < ONEMLI_KESINTI:
            return None

        # Internet mi gitti, sistem mi kapandi? son_cevrimici ile ayirt
        # ediyoruz: bot calisiyor ama Telegram'a ulasamiyorsa kalp atisi
        # ilerler, son_cevrimici ilerlemez.
        tur = "sistem kapali"
        cevrimici_ham = d.get("son_cevrimici")
        if cevrimici_ham:
            try:
                if datetime.fromisoformat(cevrimici_ham) < son - timedelta(minutes=2):
                    tur = "baglanti kopuk (bot calisiyordu)"
            except ValueError:
                pass
        return {"sure_dk": int(fark.total_seconds() // 60),
                "bas": son, "son": n, "tur": tur}

    # --- 2) nabiz gozcusu ---------------------------------------------
    def kacirilan_nabiz(self) -> dict | None:
        """
        Bugun nabiz calismasi gerekiyorduysa CALISTI MI?

        Sessiz basarisizlik en tehlikeli ariza: hicbir sey olmamis gibi
        gorunur, sen de veri geldigini sanirsin. Bu yuzden bot, ayakta
        oldugu surece zamanlanmis isi de gozetliyor.

        UC DUZELTME (2026-08-18, Ali dort YANLIS alarm aldi):

        1) SAAT YEREL OLMALI. Nabiz launchd'de 22:15 YEREL calisiyor ama
           kontrol `hour >= 23` diye UTC saate bakiyordu. Makine CEST
           (UTC+2) oldugu icin pencere 01:00-01:59 YERELE kaydi — Ali'nin
           alarmlari tam 01:15'te geldi. Yerel zamanlanan bir isi UTC ile
           yargilamak, saat farki kadar kayar.

        2) ESITLIK DEGIL PENCERE. `olusma_ts = bugun` tek bir gune
           bakiyordu; UTC ile yerel gunun kaydigi saatlerde bu "dun
           calisti ama bugun calismadi" gibi okunabiliyor. Artik SON 18
           SAAT'e bakiliyor: 22:15'te kosan bir is, ertesi sabaha kadar
           her kontrolde gorunur.

        3) SINYAL TEK KANIT DEGIL. Sinyal uretmemek MESRU bir sonuc
           olabilir (esigi gecen kagit yoksa tarama bos doner —
           "sessizlik gecerli cikti" ilkesi). Nabzin CALISTIGINI
           `panel_runs` ve `collector_runs` da soyluyor. Yalnizca
           sinyale bakmak, sessiz ama basarili bir kosuyu ARIZA
           sanmak demekti.
        """
        n = _yerel()
        if n.weekday() >= 5:
            return None                              # hafta sonu zaten calismaz
        # Nabiz 22:15'te baslar ve ~9 dk surer; 23:00'ten once yargilamayiz.
        if n.hour < 23:
            return None
        bugun = n.strftime("%Y-%m-%d")
        # 18 saatlik pencere: aksam 22:15'teki kosu, ertesi gun ogleye
        # kadar "calisti" sayilir. Esitlik yerine pencere, saat dilimi
        # kaymalarina karsi da dayanikli.
        sinir = (n - timedelta(hours=18)).strftime("%Y-%m-%d")
        for sql in (
            "SELECT COUNT(*) c FROM signals WHERE olusma_ts >= ?",
            "SELECT COUNT(*) c FROM panel_runs WHERE run_ts >= ?",
            "SELECT COUNT(*) c FROM collector_runs WHERE run_ts >= ?",
        ):
            try:
                r = self.db.query(sql, (sinir,))
            except Exception as e:                    # noqa: BLE001
                # SORGU PATLARSA ALARM CALMAZ. "Sorgu basarisiz" ile
                # "nabiz calismadi" AYRI seyler; ikisini karistirmak bu
                # projenin en kotu hata sinifi (bkz. yanlis "yok" beyani).
                log.warning("[bekci] nabiz kontrolu sorgusu basarisiz: %s", e)
                return None
            if r and r[0]["c"]:
                return None
        return {"gun": bugun,
                "not": "Zamanlanmis nabiz bugun sinyal uretmedi. Ya calismadi "
                       "ya da hata aldi."}

    # --- 2b) hafif kosu gozcusu (sabah / ogle) -------------------------
    #
    # Nabiz'in gozcusu vardi, sabah ve ogle'nin YOKTU. Bedeli olculdu:
    # 2026-08-17'de ogle kosusu 18:00'de basladi, `collect` 20 dakikalik
    # kabuk butcesini doldurdu ve SUREC GRUBU olduruldu — `nabiz` adimina
    # hic ulasilamadi. O gun BIST kapanisi icin sinyal, tez alarmi ve
    # portfoy riski URETILMEDI ve bunu kimse fark etmedi.
    #
    # NEDEN AYRI BIR OLCUT: nabiz gozcusu "son 18 saatte sinyal/panel/
    # collector kaydi var mi" diye bakiyor. Bu sabah/ogle icin ISE
    # YARAMAZ — sabah kosusu kayit birakinca ogle'nin eksikligi gizlenir,
    # ustelik sohbetten tetiklenen toplamalar da ayni tabloya yaziyor.
    # Tek kesin kanit, kosunun KENDI izidir (pulse/runner.py `_iz_birak`).
    #
    # SAAT PLIST'TEN TURETILIR, ELLE YAZILMAZ. Elle yazilan bir takvim,
    # plist degistiginde sessizce yanlis olur; README testinin plist
    # saatlerini koda baglamasiyla ayni gerekce.
    IZ_KIPLERI = {"sabah": "sabah", "ogle": "ogle"}
    # Kosu bu kadar gecikirse "kacirildi" denir. Ogle kosusu ~13-20 dk
    # surer; 90 dakika, yavas bir kosuyu ariza saymayacak kadar genis.
    GECIKME_PAYI = timedelta(minutes=90)

    def _plist_saatleri(self) -> dict:
        """launchd plist'lerinden {kip: [(weekday, hour, minute), ...]}."""
        import plistlib
        from pathlib import Path
        out: dict[str, list] = {}
        dizin = Path(self.s.root) / "launchd"
        for yol in sorted(dizin.glob("*.plist")):
            try:
                veri = plistlib.loads(yol.read_bytes())
            except Exception as e:                    # noqa: BLE001
                log.warning("[bekci] plist okunamadi (%s): %s", yol.name, e)
                continue
            kip = self.IZ_KIPLERI.get(
                str(veri.get("Label", "")).rsplit(".", 1)[-1])
            if not kip:
                continue
            sc = veri.get("StartCalendarInterval") or []
            sc = [sc] if isinstance(sc, dict) else sc
            out[kip] = [(g.get("Weekday"), g.get("Hour", 0), g.get("Minute", 0))
                        for g in sc]
        return out

    def _iz_yasi(self, kip: str):
        """Kosu izinin zaman damgasi (UTC) — yoksa None."""
        yol = self.state_dir / "kosu" / f"{kip}.json"
        try:
            return datetime.fromisoformat(
                json.loads(yol.read_text(encoding="utf-8"))["ts"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def kacirilan_kosular(self) -> list[dict]:
        """
        Bugun calismasi gereken hafif kosulardan hangileri iz birakmadi?

        ILK KURULUMDA SESSIZ: hic iz dosyasi yoksa mekanizma daha yeni
        demektir ve gecmise donuk alarm calmak dogru degil. Alarm ancak
        BIR KEZ iz goruldukten sonra anlamlidir.
        """
        n = _yerel()
        if n.weekday() >= 5:
            return []                                 # hafta sonu kosmuyorlar
        takvim = self._plist_saatleri()
        if not takvim:
            return []
        # Bootstrap: hicbir kip iz birakmamissa mekanizma yeni.
        if not any(self._iz_yasi(k) for k in takvim):
            return []

        eksik = []
        for kip, aralik in sorted(takvim.items()):
            # launchd Weekday: 0/7 = Pazar, 1 = Pazartesi ... Python'da
            # Pazartesi 0. Bugune ait girisi bul.
            bugunku = [(h, m) for wd, h, m in aralik
                       if wd is None or (int(wd) % 7) == ((n.weekday() + 1) % 7)]
            if not bugunku:
                continue
            saat, dakika = min(bugunku)
            beklenen = n.replace(hour=int(saat), minute=int(dakika),
                                 second=0, microsecond=0)
            if n < beklenen + self.GECIKME_PAYI:
                continue                              # daha vakti var
            iz = self._iz_yasi(kip)
            if iz is not None and iz.astimezone(n.tzinfo) >= beklenen:
                continue                              # bugun calismis
            eksik.append({
                "kip": kip, "gun": n.strftime("%Y-%m-%d"),
                "beklenen": f"{int(saat):02d}:{int(dakika):02d}",
                "son_iz": iz.astimezone(n.tzinfo).strftime("%d.%m %H:%M")
                          if iz else "hic",
            })
        return eksik

    # --- bildirim (susturmali) ----------------------------------------
    def bildir(self, anahtar: str, mesaj: str) -> bool:
        """
        Ayni turden bildirimi SESSIZLIK_SURESI boyunca tekrarlamaz.

        Gerekce: bot yapilandirma hatasiyla surekli yeniden basliyorsa
        (launchd 60 sn'de bir dener) her kalkista mesaj atmak dakikada
        bir bildirim demektir.
        """
        d = self._oku()
        gecmis = d.setdefault("bildirimler", {})
        n = _simdi()
        onceki = gecmis.get(anahtar)
        if onceki:
            try:
                if n - datetime.fromisoformat(onceki) < SESSIZLIK_SURESI:
                    log.info("[bekci] '%s' susturuldu (yakin zamanda gonderildi)",
                             anahtar)
                    return False
            except ValueError:
                pass
        try:
            from ..notify import TelegramNotifier
            TelegramNotifier(self.s).send_message(mesaj)
        except Exception as e:                        # noqa: BLE001
            log.warning("[bekci] bildirim gonderilemedi: %s", e)
            return False
        # GONDERIM DE LOGLANIR. Onceden yalnizca SUSTURMA loglaniyordu,
        # yani bir alarm gittiginde kayitta HIC iz kalmiyordu. Ali dort
        # yanlis alarm aldiginda kaynagi bulmak icin saatler harcandi ve
        # kanitlanamadi — gozetim katmaninin kendisi gozetilemiyordu.
        log.warning("[bekci] BILDIRIM GONDERILDI '%s': %s",
                    anahtar, mesaj.split("\n")[0][:90])
        gecmis[anahtar] = n.isoformat()
        self._yaz(d)
        return True

    # --- 3) dis izleyici ----------------------------------------------
    def disari_ping(self) -> None:
        """
        Dead man's switch. Makine TAMAMEN kapandiginda haber alabilmenin
        tek yolu: disaridaki bir servise duzenli sinyal gonderilir, sinyal
        kesilince O SERVIS uyari yollar.

        `.env` -> HEARTBEAT_URL tanimliysa calisir, yoksa sessizce atlanir.
        healthchecks.io ucretsiz katmani bu ise yeterli.
        """
        url = os.environ.get("HEARTBEAT_URL", "").strip()
        if not url:
            return
        n = _simdi()
        if self._son_ping and n - self._son_ping < PING_ARALIGI:
            return
        try:
            import httpx
            httpx.get(url, timeout=10)
            self._son_ping = n
            log.debug("[bekci] dis ping gonderildi")
        except Exception as e:                        # noqa: BLE001
            # Basarisiz ping SESSIZ kalir ve _son_ping ILERLEMEZ: bir
            # sonraki dongude tekrar denenir. Gecici ag hatasi yuzunden
            # 5 dakika beklemek, izleyicinin alarm esigine yaklastirirdi.
            log.debug("[bekci] dis ping basarisiz: %s", e)
