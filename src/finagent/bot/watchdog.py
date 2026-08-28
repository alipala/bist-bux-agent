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
from pathlib import Path

log = logging.getLogger(__name__)

# Bu suren altindaki kesinti RAPOR EDILMEZ: elle yeniden baslatma,
# guncelleme, kisa bir cokme. Gurultu yapmanin anlami yok.
ONEMLI_KESINTI = timedelta(minutes=10)

# Ayni turden bildirim bu sure icinde TEKRARLANMAZ.
SESSIZLIK_SURESI = timedelta(hours=6)

# GECIS (edge-triggered) BILDIRIMLERI ICIN AYRI REJIM.
#
# `SESSIZLIK_SURESI` TEKRARLAYAN alarmlar icin dogru: "collector hala
# bozuk" her kosuda calarsa kullanici bildirimleri kapatir. Ama DURUM
# DEGISIMI bildirimleri (oturum dustu / geldi) kaynagında ZATEN bir kez
# tetikleniyor — uzerine 6 saatlik pencere koymak, gercek ve birbirinden
# FARKLI olaylari sessizce yutuyor.
#
# SAHADA OLDU (26 Agu): IBKR oturumu 09:46'da dustu, 09:57'de geldi.
# Ikisi de susturuldu ("yakin zamanda gonderildi" — sabah 08:16'daki
# bildirim yuzunden) ve Ali 11 dakikalik kesintiden HIC HABERI OLMADI.
# Susturucunun kendisi, gozetim katmanini kor etti.
#
# Yerine CIRPINMA (flapping) korumasi: gecis bildirimleri serbest, ama
# ayni anahtar bir saatte tavani asarsa TEK bir "cirpiniyor" mesaji
# gidip susulur. Boylece hem her gercek gecis duyulur hem de dususup
# kalkan bir oturum dakikada bir mesaj atmaz.
def _guvenli_ts(x):
    """ISO damgasi -> datetime; bozuksa None (patlamaz)."""
    try:
        return datetime.fromisoformat(str(x))
    except (TypeError, ValueError):
        return None


GECIS_TAVANI = 6
GECIS_PENCERESI = timedelta(hours=1)

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
    def __init__(self, settings, db, state_dir, bildirici=None):
        """
        `bildirici` — mesaji GONDEREN nesne (`send_message(metin)`).

        ENJEKSIYON BASTAN KONMADI, SONRADAN EKLENDI VE BEDELI OLCULDU
        (2026-08-25): `bildir()` kendi `TelegramNotifier`ini kuruyordu,
        yani cagiranin enjekte ettigi taklit istemciyi TANIMIYORDU.
        `_dongu_botu` testi `bot.tg`yi taklitle degistirmisti ama
        `bekci.bildir()` o taklidin yanindan gecip CANLI kanala mesaj
        gonderdi. `bayat_surum(surec_basi=...)` ve `Kuyruk` dersi tam
        buydu: enjeksiyonu sonradan eklemek yerine bastan koy.

        Varsayilan None: uretimde davranis DEGISMIYOR, ilk gonderimde
        `TelegramNotifier` tembel kuruluyor.
        """
        self.s = settings
        self.db = db
        self.state_dir = state_dir
        self.dosya = state_dir / "watchdog.json"
        self._son_yazim = None
        self._son_ping = None
        self._bildirici = bildirici

    def _gonderici(self):
        """Bildirim istemcisi — enjekte edilmisse O, degilse gercegi."""
        if self._bildirici is not None:
            return self._bildirici
        from ..notify import TelegramNotifier
        return TelegramNotifier(self.s)

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
    # Kaynak dosya bu kadar eskiyse "bayat" demeyiz — kurulum sirasinda
    # dosyalar surecten birkac saniye once/sonra yazilabilir.
    BAYAT_PAYI = timedelta(seconds=90)

    def bayat_surum(self, surec_basi: float | None = None) -> dict | None:
        """
        Calisan surec, diskteki KODDAN eski mi?

        NEDEN VAR (olculdu 2026-08-18): o gun bot kodunu etkileyen ALTI
        commit atildi; DORDUNDE bot yeniden baslatildi, IKISINDE ATLANDI
        (`_iz_koruyan` 37 dk, `takvim` araci 9 dk eski kodla kosdu). Zarar
        gormedi cunku o pencerede kimse yazmadi — yani SANS, surec degil.

        `launchctl list` "bot calisiyor" der; **"bot GUNCEL kodla
        calisiyor"** APAYRI bir iddiadir ve ona bakan kimse yoktu. Bu,
        projenin tekrar eden kusur sinifi: beyan edilen durumun gercek
        durumdan SESSIZCE ayrilmasi. Insanin hatirlamasina birakilan her
        adim er gec atlanir; olculebilir hale getirilmeli.

        Yeniden baslatma OTOMATIK YAPILMAZ: ucustaki bir turu kesmek
        (or. ekran goruntusu okuma) kullanicinin isini goturur. Bekci
        yalnizca GORUNUR kilar; karar sahibinin.

        `surec_basi` ENJEKTE EDILEBILIR (epoch saniye). Varsayilani KENDI
        surecimizin baslangici — canlida dogru olan bu. Ama testin kendi
        sureci HER ZAMAN dosyalardan yeni oldugu icin, enjeksiyon olmadan
        bu yontem SINANAMAZDI. `Kuyruk` dersi: enjeksiyonu sonradan
        eklemek yerine bastan koy (bkz. [[siradaki-is]] karsi ornegi).
        """
        kok = Path(self.s.root)
        en_yeni, en_yeni_ad = 0.0, None
        for desen in ("src/finagent/**/*.py", "config/settings.yaml"):
            for yol in kok.glob(desen):
                try:
                    m = yol.stat().st_mtime
                except OSError:
                    continue
                if m > en_yeni:
                    en_yeni, en_yeni_ad = m, yol.relative_to(kok)
        if not en_yeni_ad:
            return None
        if surec_basi is not None:
            basladi = float(surec_basi)
        else:
            try:
                # Surecin kendi baslangici: /proc yok (macOS), psutil yok —
                # kendi PID'imizin baslangicini ps ile al.
                cikti = os.popen(f"ps -p {os.getpid()} -o lstart=").read().strip()
                if not cikti:
                    return None
                import subprocess
                ts = subprocess.run(["date", "-j", "-f", "%a %b %d %H:%M:%S %Y",
                                     cikti, "+%s"], capture_output=True,
                                    text=True).stdout.strip()
                basladi = float(ts)
            except (OSError, ValueError):
                return None
        if en_yeni <= basladi + self.BAYAT_PAYI.total_seconds():
            return None
        return {
            "dosya": str(en_yeni_ad),
            "dosya_ts": datetime.fromtimestamp(en_yeni).astimezone().strftime("%H:%M:%S"),
            "surec_ts": datetime.fromtimestamp(basladi).astimezone().strftime("%H:%M:%S"),
            "gecikme_dk": round((en_yeni - basladi) / 60),
        }

    # --- 2b) KOSU GOZCUSU — dort kipin dordu de -----------------------
    #
    # KALDIRILAN OLCUT: `kacirilan_nabiz()`.
    #
    # O yontem "son 18 saatte `signals` / `panel_runs` / `collector_runs`
    # kaydi var mi" diye bakiyordu, yani kosunun BASLADIGINI olcuyordu —
    # BITTIGINI degil. Iki yonden de yanlis cevap verdi:
    #
    #   * YANLIS ALARM: 2026-08-18'de Ali dort kez "nabiz calismadi"
    #     mesaji aldi; kosu calismisti.
    #   * KACAN ARIZA: 2026-08-19 gecesi nabiz 22:15'te basladi,
    #     23:00'te sure sinirinda olduruldu, bildirim gitmedi ve iz
    #     yazilmadi. Ama `collector_runs`'ta o gece 32 satir VARDI, yani
    #     olcut "sorun yok" dedi. Canlida dogrulandi:
    #         kacirilan_nabiz()   -> None
    #         kacirilan_kosular() -> []          (nabiz IZ_KIPLERI'nde yoktu)
    #
    # Yerine gecen olcut asagida ve DAHA GUCLU: kosunun KENDI izine
    # bakiyor (`pulse/runner.py::_iz_birak`, isin SONUNDA yaziliyor).
    # Yarim kalan kosu iz birakmaz. Ayrica sohbetten tetiklenen
    # toplamalar `collector_runs`'a yaziyor ve eski olcutu maskeliyordu;
    # iz dosyasinda o karisma yok.
    #
    # "Kostu ama hicbir sey uretmedi" durumu ARIZA DEGILDIR ve alarm
    # uretmemeli — "sessizlik gecerli cikti" ilkesi. Iz yazilir, bekci
    # susar. Eski olcut bunu da ariza sanabiliyordu.
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
    # Kosu bu kadar gecikirse "kacirildi" denir. 90 dakika TABAN; gercek
    # pay kipin KENDI kabuk butcesinden turetiliyor (`_gecikme_payi`),
    # cunku butce buyudugunde sabit bir pay yanlis alarm uretir.
    GECIKME_PAYI = timedelta(minutes=90)

    def _iz_kipleri(self) -> set[str]:
        """
        Gozetilecek kipler — AYARDAN, elle liste TUTULMAZ.

        ONCEDEN `IZ_KIPLERI = {"sabah": "sabah", "ogle": "ogle"}` diye
        elle yaziliydi ve `nabiz` LISTEDE YOKTU. Bedeli olculdu:
        2026-08-19 gecesi nabiz 22:15'te basladi, 23:00'te sure sinirinda
        SIGTERM ile oldu, bildirim gitmedi, iz yazilmadi — ve bekci
        `kacirilan_kosular() -> []` dedi. Elle tutulan bir liste, ayar
        degistiginde SESSIZCE eksik kalir; tek kaynak `ritim.kipler`.
        """
        try:
            kipler = self.s.ritim_kipleri            # Settings
        except AttributeError:
            kipler = sorted((self.s.get("ritim.kipler") or {}))
        return {str(k) for k in kipler}

    def _gecikme_payi(self, kip: str) -> timedelta:
        """
        Kip basina gecikme payi: TABAN ile kipin kabuk butcesinin
        buyugu (+15 dk toparlanma).

        Sabit 90 dk, butce buyudugunde YANLIS ALARM uretirdi: `nabiz`
        50 dakikalik butcesini mesru sekilde doldurdugunda bekci onu
        "kacirildi" sayardi. Pay, isin kendi ust sinirindan turemeli.
        """
        try:
            butce = float(self.s.ritim_kip(kip)["kabuk_butce_sn"])
        except Exception:                             # noqa: BLE001
            return self.GECIKME_PAYI
        return max(self.GECIKME_PAYI, timedelta(seconds=butce + 900))

    def _plist_saatleri(self) -> dict:
        """launchd plist'lerinden {kip: [(weekday, hour, minute), ...]}."""
        import plistlib
        from pathlib import Path
        izlenen = self._iz_kipleri()
        out: dict[str, list] = {}
        dizin = Path(self.s.root) / "launchd"
        for yol in sorted(dizin.glob("*.plist")):
            try:
                veri = plistlib.loads(yol.read_bytes())
            except Exception as e:                    # noqa: BLE001
                log.warning("[bekci] plist okunamadi (%s): %s", yol.name, e)
                continue
            kip = str(veri.get("Label", "")).rsplit(".", 1)[-1]
            if kip not in izlenen:
                continue
            sc = veri.get("StartCalendarInterval") or []
            sc = [sc] if isinstance(sc, dict) else sc
            out[kip] = [(g.get("Weekday"), g.get("Hour", 0), g.get("Minute", 0))
                        for g in sc]
        return out

    def _kurulum_ani(self):
        """
        Bekcinin kosu-gozetimi KURULDUGU an (yerel saat) — bir kez yazilir.

        Bu, "gecmise donuk alarm calmasin" sinirinin TEK dogru olcusu.
        Iz varligina bakmak iki yonden de yaniliyordu: global bakinca
        hic kosmamis bir kipi yargiliyor (yanlis alarm), kipe ozel
        bakinca ilk gunden bozuk bir kipi hic yargilamiyor (kacan ariza).
        """
        yol = self.state_dir / "kosu" / "kurulum.json"
        try:
            return datetime.fromisoformat(
                json.loads(yol.read_text(encoding="utf-8"))["ts"]
            ).astimezone(_yerel().tzinfo)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
        try:
            yol.parent.mkdir(parents=True, exist_ok=True)
            simdi = _yerel()
            yol.write_text(json.dumps({"ts": simdi.isoformat()}),
                           encoding="utf-8")
            log.info("[bekci] kosu gozetimi kuruldu: %s", simdi.isoformat())
            return simdi
        except OSError as e:                          # noqa: BLE001
            log.warning("[bekci] kurulum ani yazilamadi: %s", e)
            return None

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

        ILK KURULUMDA SESSIZ — ama SINIR KURULUM ANI, iz varligi DEGIL.

        ILK SURUM `any(iz var mi)` diye GLOBAL bir kapi kullaniyordu ve
        BESINCI YANLIS ALARMI uretti (olculdu 2026-08-18 18:15:21, Ali'ye
        Telegram'dan gitti): sabah kosusu o gun 09:31'de GERCEKTEN kostu
        (170 piyasa sinyali, iki sahip) ama izi yoktu, cunku iz mekanizmasi
        11:54'te geldi. Ogle 18:15'te ilk izi yazinca global kapi acildi ve
        bekci sabah'i da yargilayip "kacirildi" dedi.
        Kipe ozel iz kapisi da YETMEZDI: ilk gunden beri BOZUK bir kip hic
        iz birakmaz, dolayisiyla hic alarm da almaz — mekanizmanin var olma
        sebebini ortadan kaldirir.
        DOGRU SINIR: bekci yalnizca KENDISI KURULDUKTAN SONRAYA zamanlanmis
        kosulari yargilayabilir. Kurulum ani bir kez diske yazilir; beklenen
        saati o andan ONCE olan kosu ATLANIR (yargilanamaz), sonra olan
        kosu iz birakmadiysa ALARM CALAR — bozuk kip de dahil.
        """
        n = _yerel()
        if n.weekday() >= 5:
            return []                                 # hafta sonu kosmuyorlar
        takvim = self._plist_saatleri()
        if not takvim:
            return []
        kurulum = self._kurulum_ani()

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
            if n < beklenen + self._gecikme_payi(kip):
                continue                              # daha vakti var
            # BEKCININ KURULUMUNDAN ONCEKI KOSU YARGILANAMAZ. Izinin
            # olmamasi "kosmadi" demek degil, "mekanizma yoktu" demek.
            if kurulum is not None and beklenen < kurulum:
                continue
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

    # Bir toplayicinin son bu kadar kosusunun HEPSI eksikse, sorun artik
    # gecici degil yapisaldir. 3: tek bir partial gecici sunucu
    # kisitlamasidir ve bildirilirse gurultu olur.
    EKSIK_KOSU_ESIGI = 3

    def eksik_toplama(self) -> list[dict]:
        """
        KAPSAMI SESSIZCE DUSUREN toplayicilar — BESINCI OLCUT.

        `collector_runs` uc statu yaziyor (ok/partial/error) ama bekcinin
        dort olcutunun HICBIRI `partial`a bakmiyordu. Sonuc: sistemli
        veri kaybi hic bildirilmedi. Olculdu (2026-08-20, son 3 gun):

            isyatirim  13/13 partial — her kosuda ~150/346 sembol atlandi
            prices     19/23 partial — 7 BIST sembolu her seferinde dustu
            binance    14/14 partial — 8 sembol kimliksiz (EUR dahil)
            kripto      6/6  error   — CoinGecko 400

        Bunlarin hicbiri Ali'ye dusmedi. Model ise ayni sembolu bir
        kosuda bulup digerinde bulamadigi icin "kafasi karisik"
        gorundu — kusur modelde degil, ALTINDAKI ZEMINDEYDI.

        OLCUT SUREKLILIK, tek olay DEGIL: son `EKSIK_KOSU_ESIGI` kosunun
        hepsi eksikse yapisaldir. Boylece gecip giden bir sunucu hatasi
        alarm uretmez, ama her gun sessizce yarim donen bir collector
        goze batar.
        """
        try:
            son = self.db.query(
                """SELECT collector, status, run_ts, error FROM collector_runs
                   WHERE run_ts > datetime('now','-3 days')
                   ORDER BY collector, run_ts DESC""")
        except Exception as e:                        # noqa: BLE001
            log.debug("[bekci] collector_runs okunamadi: %s", e)
            return []

        gruplar: dict[str, list] = {}
        for r in son:
            # `skipped` NE ARIZA NE SAGLIK — PENCEREDEN CIKAR.
            #
            # OLCULEN YANLIS ALARM (2026-08-28 sabahi). Mesaj
            # "ibkrkimlik — 3 kosudur partial" dedi; gercekte son uc
            # kosu soyleydi:
            #     18:51 partial
            #     18:05 skipped  "giris yapilmamis"
            #     17:30 skipped  "giris yapilmamis"
            # Yani BIR partial vardi. `skipped` "kosmadi" demek,
            # "yarim getirdi" demek DEGIL — bu olcut ise yalnizca
            # KAPSAMI SESSIZCE DUSUREN kosulari ariyor.
            #
            # Sayilmasi ucuz degil: IBKR'ye giris yapilmamis uc kosu
            # ust uste geldiginde hicbir sey bozuk degilken alarm
            # calardi. Bu, bekcinin kendi belgesindeki tuzagin ta
            # kendisi — "yanlis pozitif ureten bekci, kapatilan
            # bekcidir" — ve kapatilan bekci GERCEK arizayi da yutar.
            #
            # BILINEN SINIR: bir collector SURESIZ `skipped` donerse
            # bu olcut sessiz kalir. Dogrusu da bu; "kosu hic olmadi"
            # sorusu ayri olcutun isi (`kacirilan_kosular`).
            if r["status"] == "skipped":
                continue
            gruplar.setdefault(r["collector"], []).append(r)

        out = []
        for ad, kosular in sorted(gruplar.items()):
            if len(kosular) < self.EKSIK_KOSU_ESIGI:
                continue
            pencere = kosular[:self.EKSIK_KOSU_ESIGI]
            if any(k["status"] == "ok" for k in pencere):
                continue
            if all(self._tasarim_geregi(k) for k in pencere):
                continue
            out.append({
                "collector": ad,
                "durum": pencere[0]["status"],
                "kosu": len(pencere),
                "son": (pencere[0]["run_ts"] or "")[:16].replace("T", " "),
                "sebep": (pencere[0]["error"] or "")[:160],
            })
        return out

    # Bu ifadeyi tasiyan `partial`, ARIZA DEGIL TASARIMDIR.
    #
    # `isyatirim` 780 sn'lik IC butcesini bilerek dolduruyor: kosuyu
    # KAYBETMEKTENSE eksik cekiyor ve kalanini bir sonraki kosu aliyor
    # ("en bayat once" siralamasi kuyrugu her kosuda dondurur).
    # Olculdu 2026-08-21: 423 sembolun 422'si son 3 gunde tazelenmis
    # (%100). Yani bosluk YOK — yalnizca is iki kosuya yayilmis.
    #
    # Buna alarm calmak, DUN kapatilan "sahte partial" sinifini yeniden
    # acmak olurdu ve bu kez alarmin KENDISI uretirdi: her kosuda calan
    # bir uyari, kapatilmayi hak eder ve kapatilinca GERCEK ariza da
    # gorulmez.
    TASARIM_IFADELERI = ("sure butcesi", "kota (", "kota nedeniyle")

    @classmethod
    def _tasarim_geregi(cls, kosu) -> bool:
        s = (kosu["error"] or "").lower()
        return bool(s) and any(x in s for x in cls.TASARIM_IFADELERI)

    def eksik_toplama_bildirimi(self) -> tuple[str, list[dict]] | None:
        """
        Bildirilecek bir DEGISIKLIK var mi? (anahtar, eksikler) ya da None.

        ALARM KOTULESINCE CALAR, IYILESINCE ASLA. Bu ders 2026-08-20
        aksami PAHALIYA ogrenildi: susturma anahtarina ariza LISTESI
        konmustu ("kume degisirse yeniden calsin" diye) ve sonuc tam
        tersi oldu — collector'lar tek tek duzeltilirken liste her
        kuculdugunde anahtar degisti, susturma devre disi kaldi ve
        Ali'ye UC DAKIKADA UC, aksam boyunca ALTI bildirim gitti:

            17:38  alphavantage,binance,coingecko,isyatirim,kripto,prices
            17:39  ... kripto DUZELDI    -> yeni anahtar -> YENI ALARM
            17:42  ... binance DUZELDI   -> yeni anahtar -> YENI ALARM
            17:44  ... coingecko DUZELDI -> yeni anahtar -> YENI ALARM
            18:51  ... prices DUZELDI    -> yeni anahtar -> YENI ALARM
            20:18  ... alphavantage DUZELDI -> yeni anahtar -> YENI ALARM

        Yani kullanici, ISLER IYILESTIGI ICIN spam yedi. Bir izleme
        katmaninin yapabilecegi en kotu sey bu: gurultu, kendisinin
        kapatilmasina yol acar ve o zaman GERCEK ariza da gorulmez.

        Yeni kural: yalnizca YENI bir collector listeye girerse bildir.
        Liste kuculuyorsa sessiz kal. Tamamen temizlendiginde TEK bir
        "toparlandi" mesaji — o da bir kez.
        """
        eksikler = self.eksik_toplama()
        simdi = {e["collector"] for e in eksikler}
        d = self._oku()
        onceki = set(d.get("eksik_toplama_kume") or [])

        if not simdi:
            if not onceki:
                return None
            # TOPARLANMA BIR KEZ SOYLENIR: kullanici kapattigi bir
            # alarmin kapandigini bilmeli, ama her dongude degil.
            d["eksik_toplama_kume"] = []
            self._yaz(d)
            return ("eksik_toplama_toparlandi", [])

        yeni = simdi - onceki
        d["eksik_toplama_kume"] = sorted(simdi)
        self._yaz(d)
        if not yeni:
            return None                      # ayni ya da kuculmus: sessiz
        # ANAHTAR SABIT DEGIL AMA YALNIZCA YENI GIRENLERI TASIYOR:
        # ayni collector iki kez bozulursa `SESSIZLIK_SURESI` tutar.
        return ("eksik_toplama_" + ",".join(sorted(yeni)), eksikler)

    def gunici_sessiz(self) -> dict | None:
        """
        YEDINCI OLCUT — gun ici kosu piyasa saatinde iz birakiyor mu?

        `kacirilan_kosular` bu kosuyu YARGILAYAMAZ: o olcut plist'teki
        `StartCalendarInterval` saatlerinden turuyor, gun ici kosu ise
        `StartInterval` ile calisiyor ve plist'te saat YOK. Elle bir
        saat listesi yazmak, ayar degistiginde sessizce ayrisan ikinci
        bir dogruluk kaynagi olurdu (bkz. `_iz_kipleri` dersi).

        DOGRU OLCUT: piyasa ACIKKEN iz yasi. Kapali piyasada kosu
        hicbir sey yapmiyor ve iz de tazelenmiyor — orada SESSIZ kalmak
        dogru davranis, ariza degil.

        Pay: iki aralik + 10 dk. Tek aralik cok dar (bir kosu gecikirse
        yanlis alarm), uc aralik cok genis (bir saatlik sessizlik gozden
        kacar).
        """
        try:
            from ..pulse.gunici import acik_borsalar, en_uzun_acik_dk
            ayar = self.s.gunici_ayari()
        except Exception:                             # noqa: BLE001
            return None                               # ayar yoksa olcut yok
        if not ayar["enabled"]:
            return None
        acik = acik_borsalar()
        if not acik:
            return None                               # pencere disinda sessiz

        # BEKCININ KURULUMUNDAN ONCESI YARGILANAMAZ — `kacirilan_kosular`
        # ile ayni sinir ve ayni gerekce.
        kurulum = self._kurulum_ani()
        iz = self._iz_yasi("gunici")
        pay = timedelta(minutes=2 * float(ayar["aralik_dk"]) + 10)
        simdi = _yerel()

        # PIYASA YENI ACILDIYSA HENUZ KOSU VAKTI GELMEMISTIR.
        #
        # OLCULEN YANLIS ALARM (2026-08-25 09:00:13, Ali'ye gitti): iz
        # YALNIZCA piyasa acikken yaziliyor, dolayisiyla gece boyunca
        # zorunlu olarak bayatliyor. BIST 09:00'da acildi, bekci
        # 09:00:13'te bakti ve dunku 21:45 izini gorup "675 dk once"
        # dedi — oysa ilk kosunun vakti 09:16'ydi. Bu, her islem
        # sabahi tekrarlanan yapisal bir yanlis alarmdi.
        #
        # Olcut IZ YASI degil, ACILISTAN GECEN SURE: bir kosu ancak
        # acilistan sonra beklenebilir. `kurulum` kapisi yalnizca ilk
        # kurulumu koruyordu, ACILISI korumuyordu.
        acilali = en_uzun_acik_dk()
        if acilali is not None and timedelta(minutes=acilali) < pay:
            return None
        if iz is None:
            if kurulum is not None and simdi - kurulum < pay:
                return None                           # yeni kuruldu, bekle
            return {"sebep": "hic iz yok", "yas_dk": None, "acik": acik}
        yas = simdi - iz.astimezone(simdi.tzinfo)
        if yas <= pay:
            return None
        return {"sebep": f"son iz {yas.total_seconds() / 60:.0f} dk once",
                "yas_dk": int(yas.total_seconds() / 60), "acik": acik,
                "aralik_dk": ayar["aralik_dk"]}

    # Yedek bu kadar gunden eskiyse ariza sayilir.
    #
    # 2 secildi: yedek DORT kosunun her birinde deneniyor, yani gunde
    # dort sans var. Bir gun kacirmak "makine kapaliydi" olabilir; iki
    # gun ust uste kacirmak ARIZA demektir (dizin gitmis, disk dolmus,
    # `enabled` yanlislikla false yapilmis).
    YEDEK_BAYATLIK_GUN = 2

    def yedek_bayat(self) -> dict | None:
        """
        ALTINCI OLCUT — en yeni yedek kac gunluk?

        NEDEN AYRI BIR OLCUT GEREKIYOR: `run_kosu.sh` yedek KOSUP
        BASARISIZ olursa zaten bildiriyor. Ama yedegin sessizce
        DURMASI baska bir sey ve alarm uretmiyor:
          * `yedek.enabled` yanlislikla false yapilir -> "atlandi",
            cikis kodu 0, kimse bir sey soylemez
          * kosular hic calismaz (makine kapali) -> yedek de alinmaz
          * dizin bir bulut klasorune tasinir ve senkron kopar

        Ucunde de kullanici "yedegim var" sanir. Yedekte EN KOTU ariza
        budur: kaybi ancak GERI YUKLERKEN ogrenirsin.

        Doner: sorun varsa sozluk, yoksa None.
        """
        try:
            from ..storage.yedek import durum as yedek_durumu
            d = yedek_durumu(self.s)
        except Exception as e:                        # noqa: BLE001
            # AYAR OKUNAMIYORSA DA SORUNDUR: dogrulama patliyorsa yedek
            # de alinamiyor demektir.
            return {"sebep": f"yedek ayari okunamadi: {e}", "yas_gun": None,
                    "en_yeni": None, "dizin": "?"}
        if not d["en_yeni"]:
            return {"sebep": "hic yedek yok", "yas_gun": None,
                    "en_yeni": None, "dizin": d["dizin"]}
        try:
            en_yeni = datetime.strptime(d["en_yeni"], "%Y-%m-%d").date()
        except ValueError:
            return {"sebep": f"yedek adi okunamadi: {d['en_yeni']}",
                    "yas_gun": None, "en_yeni": d["en_yeni"],
                    "dizin": d["dizin"]}
        yas = (_yerel().date() - en_yeni).days
        if yas < self.YEDEK_BAYATLIK_GUN:
            return None
        return {"sebep": f"en yeni yedek {yas} gunluk", "yas_gun": yas,
                "en_yeni": d["en_yeni"], "dizin": d["dizin"],
                "adet": d["adet"]}

    # --- bildirim (susturmali) ----------------------------------------
    def son_bildirim_ts(self, anahtar: str) -> str | None:
        """
        Bir anahtarin EN SON gonderim damgasi (yoksa None).

        Bellekteki durum surec restart'inda silinir; DISKTEKI bu kayit
        silinmez. "En son ne bildirdim" sorusunun tek kalici cevabi bu.
        """
        return (self._oku().get("bildirimler") or {}).get(anahtar)

    def bildir(self, anahtar: str, mesaj: str, *, gecis: bool = False) -> bool:
        """
        Ayni turden bildirimi SESSIZLIK_SURESI boyunca tekrarlamaz.

        Gerekce: bot yapilandirma hatasiyla surekli yeniden basliyorsa
        (launchd 60 sn'de bir dener) her kalkista mesaj atmak dakikada
        bir bildirim demektir.

        `gecis=True` — DURUM DEGISIMI bildirimi. Zaman penceresi
        UYGULANMAZ; cunku cagiran taraf zaten yalnizca durum degistiginde
        cagiriyor ve iki farkli gecis (dustu / geldi) ayni "tekrar" degil.
        Yerine cirpinma tavani var. Ayrintili gerekce GECIS_TAVANI'nin
        yaninda.
        """
        d = self._oku()
        gecmis = d.setdefault("bildirimler", {})
        n = _simdi()
        if gecis:
            if not self._gecise_izin(d, anahtar, n):
                return False
        else:
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
            self._gonderici().send_message(mesaj)
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

    def _gecise_izin(self, d: dict, anahtar: str, n: datetime) -> bool:
        """
        Gecis bildirimi gonderilsin mi? Tek olcut CIRPINMA.

        Zaman penceresi YOK: "oturum dustu" ile "oturum geldi" birbirinin
        tekrari degil, ve ikisi de kullanicinin bilmesi gereken ayri
        olaylar. Susturulacak tek sey, ayni anahtarin saatte tavani
        asacak kadar cok kez degismesi.
        """
        kayit = d.setdefault("gecisler", {})
        damgalar = [x for x in kayit.get(anahtar, [])
                    if _guvenli_ts(x) and n - _guvenli_ts(x) < GECIS_PENCERESI]
        if len(damgalar) >= GECIS_TAVANI:
            uyarildi = d.setdefault("gecis_uyarisi", {})
            son = _guvenli_ts(uyarildi.get(anahtar))
            if son and n - son < GECIS_PENCERESI:
                log.info("[bekci] '%s' cirpiniyor — susuldu", anahtar)
                kayit[anahtar] = damgalar
                self._yaz(d)
                return False
            # TEK SEFERLIK "cirpiniyor" mesaji: susmak da bir olaydir ve
            # sessizce susmak, gozetimi yine kor eder.
            try:
                self._gonderici().send_message(
                    f"⚠️ <b>'{anahtar}' cirpiniyor</b> — son "
                    f"{GECIS_PENCERESI.seconds // 3600} saatte "
                    f"{len(damgalar)} kez degisti. Bu bildirim turu bir "
                    "sure susturuluyor; durumu <i>ibkr durum</i> ile sor.")
            except Exception as e:                        # noqa: BLE001
                log.warning("[bekci] cirpinma uyarisi gonderilemedi: %s", e)
            uyarildi[anahtar] = n.isoformat()
            kayit[anahtar] = damgalar
            self._yaz(d)
            return False
        damgalar.append(n.isoformat())
        kayit[anahtar] = damgalar
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
