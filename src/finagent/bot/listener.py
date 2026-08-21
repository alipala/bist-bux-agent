"""
Telegram bot dinleyicisi — portfoy verisinin GIRIS kanali.

NEDEN
-----
BUX ve Midas mobil-only (web arayuzleri yok). Portfoy pozisyonlarini almanin
kimlik bilgisi gerektirmeyen yolu: kullanici telefonda uygulamayi acikken
ekran goruntusunu bota gonderir, bot goruntuyu okur.

Akis:
    telefon -> Telegram -> long-poll -> indir -> Claude(Fable) okur
            -> ONAY sorulur -> SQLite positions

GUVENLIK
--------
* Sadece .env'deki TELEGRAM_CHAT_ID (+ opsiyonel allowlist) kabul edilir.
  Botu bulan baskasi ne rapor alabilir ne veri yazabilir.
* Goruntu icerigi DIS VERIDIR; vision/screenshot.py prompt izolasyonu uygular.
* Hicbir pozisyon ONAY ALINMADAN veritabanina yazilmaz (mimari §5
  "Human-in-the-Loop Approval").
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# SESSIZ VARSAYILAN YOK. Sahip cozulemiyorsa portfoy islemi YAPILMAZ ve
# sebep soylenir; varsayilana dusmek yanlis kisinin portfoyune yazmak
# demektir ve bu isin tek gercek tehlikesi odur.
_SAHIPSIZ = ("⚠️ Bu sohbet bir kisiye bagli degil, portfoy islemi "
             "yapilamaz.\n\n<code>config/settings.yaml</code> -> "
             "<code>telegram.sahipler</code> icine bu sohbetin "
             "chat_id'si eklenmeli.")

# Ayni hesaba bu sure icinde gelen ekran goruntuleri TEK anlik goruntude
# birlesir. Portfoy iki ekrana sigmadiginda kullanici arka arkaya 2-3 gorsel
# atiyor; ayri snapshot'lara yazilsa latest_positions() yalnizca sonuncuyu
# gorur ve portfoyun yarisi kaybolur.
SNAPSHOT_MERGE_WINDOW = timedelta(minutes=20)

# Sesle CALISTIRILMAYACAK komutlar — geri donusu olmayan veri islemleri.
YIKICI_KOMUTLAR = {"sil", "unut"}

YARDIM = """<b>Yatirim Analistin</b>

<b>👉 Neler yapabildigimi gezmek icin: /rehber</b>

<b>Komut ezberlemene gerek yok — ne istersen yaz.</b>\n<i>Telegram'dan sohbeti temizlemek benim hafizami SILMEZ — /unut kullan.</i>
Ne sordugunu anlayip gereken veriyi kendim cekiyorum, gerekiyorsa islem
de yapiyorum. Sesli mesaj da olur (yerel olarak yaziya cevrilir).

<i>ornek:</i>
• "ROSE nasil gidiyor, ne dusunuyorsun?"
• "portfoyumun en buyuk riski ne?"
• "bu ekran goruntusundeki pozisyonlari portfoyume ekle"
• "NVDA'da bu hafta ne oldu, fiyata etkisi olcülebilir mi?"
• "kripto fiyatlarini tazele"
• "ASML ile NVDA'yi karsilastir"
• "BTC'yi izlemeye al"

<b>Neler yapabilirim</b>
Fiyat/teknik gosterge · kripto tokenomik · hisse temel veri (XBRL) ·
kademeli haber + resmi dosyalama · olay-etki (haberin fiyata etkisi) ·
portfoy agirlik/yogunlasma · veri tazeleme · <b>portfoye pozisyon yazma</b>
(her zaman onayina sunarim, onaysiz yazmam)

<b>Ekran goruntusu</b>
• <b>Aciklamaya ne istedigini yaz</b> — "portfoyume ekle", "bunlar bende
  var mi", "bu coin nasil". Goruntuyu kendim acar, ona gore is yaparim.
• <b>Aciklamasiz gonderirsen</b> okur ve onayina sunarim.
<i>Portfoy tek ekrana sigmiyorsa arka arkaya birkac gorsel at — 20 dakika
icinde gelenler tek portfoy olarak birlesir.</i>

<b>Kisayol komutlar</b> <i>(istege bagli, hepsi sohbetle de yapilabilir)</i>
<i>Komut icin <b>/</b> gerekir. Cizgisiz yazdigin her sey bana gelir —
"sil sunu" ya da "rapor ne zaman hazir" artik komut calistirmaz.</i>
/rehber — neler yapabildigimi gez
/portfoy /rapor /ozet /takip /evren /aday /haber /etki /durum /bekleyen
/onayla — bekleyen okumalari kaydet
/kimlik ISIM = TICKER — kimligi elle ata
/sil — SON kaydi geri al (tek anlik goruntu)
/temizle [gun] — indirilen medyayi ve eski kayitlari sil
/unut — sohbet gecmisini temizle (kalici arsiv kalir)
/unut arsiv — kalici arsivi de sil (geri donusu yok)
/hatirladiklarin — kalici olarak neleri bildigimi goster
/hatirladiklarin unut 12 — 12 numarali kaydi gecersizlestir"""


class FinBot:
    def __init__(self, settings, db):
        self.s = settings
        self.db = db

        from ..notify import TelegramNotifier
        self.tg = TelegramNotifier(settings)

        self.state_dir = settings.root / "data" / "bot"
        self.pending_dir = self.state_dir / "pending"
        self.media_dir = self.state_dir / "media"
        for d in (self.state_dir, self.pending_dir, self.media_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.offset_file = self.state_dir / "offset.txt"
        # Islenmekte olan guncelleme (sert cokme sonrasi deneme sayaci).
        self.ucus_file = self.state_dir / "ucus.json"

        self.allowed = self._load_allowlist()
        self._running = True
        # Sohbet ici gorsel hafizasi: kullanici bir tur goruntu atip
        # SONRAKI turda "resimde gordugun kadar..." diyebiliyor. Eskiden
        # goruntu akisi sohbetten kopuktu ve model "gorsel bana ulasmadi"
        # diyordu — dogru ama kullanici icin anlamsiz bir sinirdi.
        #
        # DISKTE, RAM'DE DEGIL: isler artik ayri SURECLERDE calisiyor
        # (bkz. bot/kuyruk.py) ve surec ici bir sozluk turlar arasinda
        # yasamaz. RAM'de birakilsaydi "az once attigim resim" zinciri
        # SESSIZCE kopardi — kullanici gorseli gonderdigini bilir, model
        # gormez.
        self.gorsel_dir = self.state_dir / "gorsel"
        self.gorsel_dir.mkdir(parents=True, exist_ok=True)
        # Is kuyrugu YALNIZCA dinleyici surecinde kurulur (`run()`).
        # Worker'da None kalir; is kendini yeniden kuyruga atamamali.
        self.kuyruk = None

    # ------------------------------------------------------------------
    def _load_allowlist(self) -> set[int]:
        """
        Yetkili sohbetler = SAHIP ESLEMESINDEKI sohbetler.

        `extra_chat_ids` KALDIRILDI: yetkili olmak artik "bir sahibe
        bagli olmak" demek. Ikinci bir liste, yetkilendirme ile
        yonlendirmenin ayrismasina yol acardi — yetkili ama sahipsiz bir
        sohbet portfoy komutlarinda ne yapacagini bilemezdi.
        """
        ids: set[int] = set()
        for chat in self.s.sahipler:
            try:
                ids.add(int(chat))
            except (TypeError, ValueError):
                log.warning("telegram.sahipler icinde sayi olmayan chat_id: %r",
                            chat)
        return ids

    def _authorised(self, chat_id) -> bool:
        try:
            return int(chat_id) in self.allowed
        except (TypeError, ValueError):
            return False

    # --- offset kaliciligi ---------------------------------------------
    def _read_offset(self) -> int | None:
        try:
            return int(self.offset_file.read_text().strip())
        except (OSError, ValueError):
            return None

    def _write_offset(self, value: int) -> None:
        try:
            self.offset_file.write_text(str(value))
        except OSError as e:                          # noqa: BLE001
            log.warning("offset yazilamadi: %s", e)

    # --- ucustaki is ----------------------------------------------------
    #
    # Telegram'in getUpdates'i ONAYLI bir kuyruktur: daha yuksek bir
    # offset ile cagirmak, oncekileri ONAYLADIGIN anlamina gelir ve
    # Telegram onlari SILER.
    #
    # Offset ONCEDEN yaziliyordu — yani is yapilmadan "yaptim" deniyordu.
    # Surec arada olurse guncelleme KALICI OLARAK kaybolur. Sahada
    # yasandi (2026-08-17 16:26): kullanici ekran goruntusu gonderdi,
    # gorsel indirildi, okuma basladi ve 16:29'daki planli restart onu
    # kesti. Kullanici "okuyorum…" mesajini aldi, devami hic gelmedi.
    #
    # Simdi offset dispatch'ten SONRA yaziliyor. Onceki tavizin sebebi
    # ZEHIRLI MESAJ dongusuydu (hep coken bir guncelleme sonsuza dek
    # yeniden islenir); bu, deneme sayaciyla karsilaniyor. Istisnalar
    # zaten `_dispatch` cevresinde yakalaniyor, yani dongu riski
    # yalnizca SERT cokmelerde (kill, OOM) var.
    UCUS_AZAMI_DENEME = 3

    def _ucus_oku(self) -> dict:
        try:
            return json.loads(self.ucus_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _ucus_yaz(self, update_id: int, deneme: int) -> None:
        try:
            self.ucus_file.write_text(
                json.dumps({"update_id": update_id, "deneme": deneme}),
                encoding="utf-8")
        except OSError as e:                          # noqa: BLE001
            log.warning("ucus kaydi yazilamadi: %s", e)

    def _ucus_temizle(self) -> None:
        self.ucus_file.unlink(missing_ok=True)

    def _deneme_sayisi(self, update_id: int) -> int:
        """Bu guncelleme daha once kac kez denendi? (sert cokme sayaci)"""
        k = self._ucus_oku()
        return int(k.get("deneme", 0)) if k.get("update_id") == update_id else 0

    # ------------------------------------------------------------------
    def _tekil_kilit(self):
        """
        AYNI ANDA TEK BOT. Iki ornek ayni Telegram kuyrugunu ceker ve her
        mesaj rastgele birine duser — sahada yasandi.

        fcntl.flock kullaniliyor: kilit surec olunce CEKIRDEK tarafindan
        birakilir, yani cokme sonrasi bayat kilit dosyasi kalmaz. PID
        dosyasiyla yapilan cozumler bu sorunu yasar; launchd cokmede
        yeniden baslatacagi icin burada bayat kilit olumcul olurdu.
        """
        import fcntl
        yol = self.state_dir / "bot.lock"
        # "a+" ile aciliyor, "w" ile DEGIL: "w" dosyayi ACAR ACMAZ kirpar,
        # yani kilidi alamayan ikinci ornek birincinin PID kaydini silerdi
        # (test ederken goruldu: hata mesajinda pid "?" cikti).
        f = open(yol, "a+")                      # noqa: SIM115 (surec boyu acik)
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            mevcut = ""
            try:
                mevcut = yol.read_text().strip()
            except OSError:
                pass
            raise SystemExit(
                f"Bot ZATEN CALISIYOR (pid {mevcut or '?'}).\n"
                f"  Kilit: {yol}\n"
                "  Ikinci ornek ayni Telegram kuyrugunu ceker ve mesajlar "
                "rastgele birine duser.\n"
                "  Durdurmak icin: launchctl kill TERM "
                "gui/$UID/com.alipala.finagent.bot") from None
        # Kilit ALINDIKTAN sonra kirp ve kendi PID'ini yaz.
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
        return f                                 # kapanmamali: kilit acik kalsin

    def _kapatma_sinyalini_yakala(self) -> None:
        """
        SIGTERM/SIGINT gelince ucustaki isi BITIR, sonra cik.

        `launchctl kickstart -k` once SIGTERM yollar, `ExitTimeOut`
        kadar bekler, sonra SIGKILL eder. Varsayilan 20 sn'dir ve gorsel
        okuma 30-90 sn surer — yani planli her restart, o anda islenen
        mesaji OLDURUYORDU. plist'e uzun bir `ExitTimeOut` konuldu;
        burasi ise sinyali "yeni is ALMA, eldekini bitir"e ceviriyor.

        Varsayilan davranis (aninda olum) DEGISTIRILDI ama KAYBEDILMEDI:
        ikinci bir sinyal gelirse Python'un varsayilanina donuyoruz, yani
        iki kez Ctrl+C hala aninda durduruyor.
        """
        import signal

        def _dur(signum, _frame):
            if not self._running:              # ikinci sinyal -> aninda
                signal.signal(signum, signal.SIG_DFL)
                return
            self._running = False
            log.warning("sinyal %s alindi — ucustaki is bitirilip cikilacak",
                        signum)

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _dur)
            except (ValueError, OSError):       # ana is parcacigi degilse
                log.debug("sinyal %s yakalanamadi", sig)

    def run(self) -> int:
        self._kilit = self._tekil_kilit()
        if not self.tg.token:
            raise SystemExit("TELEGRAM_BOT_TOKEN tanimli degil (.env).")
        if not self.allowed:
            raise SystemExit(
                "TELEGRAM_CHAT_ID tanimli degil (.env).\n"
                "  Once: python run.py telegram-chatid")

        self._kapatma_sinyalini_yakala()

        offset = self._read_offset()
        # YARIM KALMIS IS VAR MI? Varsa bunu SOYLE — sessiz kalirsa
        # "mesajim kayboldu" ile "mesajim iki kez islendi" ayirt edilemez.
        ucus = self._ucus_oku()
        if ucus.get("update_id"):
            log.warning("onceki kosu %s numarali guncellemeyi yarim birakti "
                        "(%d. deneme) — Telegram yeniden gonderecek",
                        ucus["update_id"], ucus.get("deneme", 1))

        log.info("Bot dinlemede (yetkili sohbet: %s). Durdurmak icin Ctrl+C.",
                 ", ".join(str(i) for i in sorted(self.allowed)))

        # KESINTI RAPORU — coken sistem "coktum" diyemez, ama GERI
        # DONDUGUNDE ne kadar kapali kaldigini soyleyebilir. Tek durust
        # yaklasim bu.
        from .watchdog import Bekci
        self.bekci = Bekci(self.s, self.db, self.state_dir)
        kesinti = self.bekci.kesinti()
        if kesinti:
            self.bekci.bildir("kesinti", (
                f"🔴 <b>Kesinti</b> — {kesinti['sure_dk']} dakika\n"
                f"<code>{kesinti['bas']:%d.%m %H:%M} → "
                f"{kesinti['son']:%d.%m %H:%M} UTC</code>\n"
                f"Tur: <i>{_esc(kesinti['tur'])}</i>\n\n"
                "Bu surede gelen mesajlari goremedim. Telegram guncellemeleri "
                "~24 saat tuttugu icin cogu yine de islenecek."))
            log.warning("kesinti tespit edildi: %s dk (%s)",
                        kesinti["sure_dk"], kesinti["tur"])
        else:
            self.tg.send_message(
                "🟢 <b>Agent dinlemede.</b>\nPortfoy ekran goruntusu gonderebilirsin.\n"
                "/yardim ile komutlar.")

        # Kesinti KONTROL EDILDIKTEN hemen sonra damgala. Kalp atisini ilk
        # basarili long-poll'a birakmak yanlisti: long-poll 50 sn surebiliyor
        # ve bot o sure icinde tekrar coktugunde damga hic guncellenmiyor,
        # her yeniden baslatma AYNI eski kesintiyi yeniden tespit ediyordu.
        self.bekci.kalp_at()

        # IS KUYRUGU. Buradan sonra agir isler (sohbet, gorsel, ses,
        # onay, rapor) AYRI SURECLERDE kosuyor; bu dongu yalnizca is
        # dagitiyor. Sebep: tek is parcaciginda bir tur 25 dakika
        # surunce IKINCI KULLANICI da bloke kaliyordu (olculdu).
        from .kuyruk import Kuyruk
        self.kuyruk = Kuyruk(
            self.state_dir / "kuyruk", kok=self.s.root,
            azami_worker=int(self.s.get("telegram.worker_sayisi", 2)),
            zaman_asimi_sn=60.0 * float(
                self.s.get("telegram.is_zaman_asimi_dk", 15)),
            bildir=lambda cid, metin: self.tg.send_message(metin, chat_id=cid))
        # Onceki kosudan devrolan isler: yasayan devam eder, olen
        # yeniden denenir. Bu, restart'in mesaj yemesini onleyen ikinci
        # katman (birincisi offset muhasebesi).
        self.kuyruk.kurtar()

        backoff = 1
        kopma_ani = None
        while self._running:
            # Once toparla ve dagit: bosalan yuva bir sonraki uzun
            # yoklamayi BEKLEMEMELI.
            #
            # KUYRUK ARIZASI DINLEYICIYI OLDURMEZ. Disk dolu ya da izin
            # hatasi butun botu dusurseydi, es zamanliligi acmak
            # DAYANIKLILIGI DUSURMUS olurdu — kabul edilemez bir takas.
            # Sessiz de kalmiyor: her turda tam iz yaziliyor.
            try:
                self.kuyruk.tik()
            except Exception:                         # noqa: BLE001
                log.exception("[kuyruk] tik basarisiz — dinleyici devam ediyor")
            # SIRA VARKEN KISA YOKLAMA. 20 sn'lik pencere bosta dogru
            # (API maliyeti), ama sirada is beklerken bir worker'in
            # bitisini 20 sn gec fark etmek siradakini bosuna bekletirdi.
            bekleme = 2 if self.kuyruk.bekleyen_var() else 20
            try:
                # UZUN YOKLAMA PENCERESI = KAPANMA GECIKMESI.
                # Sinyal `_running`'i dusuruyor ama dongu bu cagrinin
                # icinde bekliyor; olculdu: 50 sn'lik pencerede SIGTERM
                # sonrasi cikis 32 sn surdu. 20'ye indirildi — API
                # maliyeti onemsiz (dakikada 3 istek yerine 1,2) ama
                # planli restart belirgin sekilde hizlaniyor.
                updates = self.tg.get_updates(offset=offset, timeout=bekleme)
                if backoff > 1:
                    # Baglanti GERI GELDI. Kopukluk suresini raporla —
                    # kullanici "bot suskundu" diye merak etmesin.
                    kopuk = int(time.time() - kopma_ani) if kopma_ani else 0
                    if kopuk >= 600:
                        self.bekci.bildir("baglanti", (
                            f"🌐 <b>Baglanti geri geldi</b>\n"
                            f"{kopuk // 60} dakika Telegram'a ulasilamadi. "
                            "Bot calisiyordu, mesajlar simdi islenecek."))
                    kopma_ani = None
                backoff = 1
            except KeyboardInterrupt:
                break
            except Exception as e:                    # noqa: BLE001
                # Ag kesintisi botu oldurmemeli; artan bekleme ile yeniden dene.
                if backoff == 1:
                    kopma_ani = time.time()
                log.warning("getUpdates hatasi (%s), %ss sonra tekrar", e, backoff)
                # Kalp atisi ILERLER ama cevrimici damgasi ILERLEMEZ:
                # boylece "bot oluydu" ile "internet yoktu" ayirt edilir.
                self.bekci.kalp_at(cevrimici=False)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue

            self.bekci.kalp_at()
            self.bekci.disari_ping()

            # ZAMANLANMIS IS GOZETIMI — DORT KIPIN DORDU DE.
            #
            # Tek olcut var ve o kosunun KENDI izine bakiyor. Eskiden
            # nabiz icin AYRI ve daha zayif bir olcut vardi
            # (`kacirilan_nabiz`, `collector_runs`'a bakiyordu); dort
            # yanlis alarm uretti ve 2026-08-19'daki GERCEK arizayi
            # kacirdi — o gece kosu SIGTERM ile oldu ama collector
            # kayitlari doluydu, olcut "sorun yok" dedi.
            #
            # Kip listesi `settings.ritim.kipler`'den turuyor: elle
            # yazilan liste, yeni bir kip eklendiginde SESSIZCE eksik
            # kalir — nabiz tam olarak boyle gozetimsiz kalmisti.
            for eksik in self.bekci.kacirilan_kosular():
                # ANAHTAR GUNE BAGLI. Onceden yalnizca kipe bagliydi ve
                # `SESSIZLIK_SURESI` 6 saat: ayni kacirilmis kosu icin
                # gunde DORT kez alarm gidiyordu. Kullanici 2026-08-21'de
                # ayni sabah alarmini ikinci kez alinca bildirdi.
                #
                # Kacirilmis bir kosu bir OLAYDIR, DURUM degil: bir kez
                # soylenir. Ertesi gun yine kacirilirsa anahtar degisir
                # ve yeniden calar — yani KOTULESME hala duyuluyor.
                self.bekci.bildir(
                    f"kosu_kacti_{eksik['kip']}_{eksik['gun']}", (
                    f"⚠️ <b>{eksik['kip'].capitalize()} kosusu calismadi</b>"
                    f" — {eksik['gun']}\n"
                    f"Beklenen saat <code>{eksik['beklenen']}</code>, "
                    f"son iz: <i>{_esc(eksik['son_iz'])}</i>\n\n"
                    "Kontrol: <code>tail -80 data/pulse.log</code>\n"
                    "Elle calistir: <code>launchctl kickstart -p "
                    f"gui/$UID/com.alipala.finagent.{eksik['kip']}</code>"))

            # SURUM BAYATLIGI. `launchctl list` "bot calisiyor" der;
            # "bot GUNCEL kodla calisiyor" APAYRI bir iddiadir ve 18
            # Agustos'ta ona bakan kimse yoktu: alti commit'in ikisinde
            # yeniden baslatma ATLANDI (`takvim` araci 9 dk, `_iz_koruyan`
            # 37 dk eski kodla kosdu). Zarar gormedi cunku o pencerede
            # kimse yazmadi — SANS, surec degil.
            # Otomatik yeniden baslatmiyoruz: ucustaki bir turu kesmek
            # (ekran goruntusu okuma gibi) kullanicinin isini goturur.
            bayat = self.bekci.bayat_surum()
            if bayat:
                self.bekci.bildir("bayat_surum", (
                    "🔄 <b>Kod guncellendi, bot hala eski surumde</b>\n"
                    f"<code>{_esc(bayat['dosya'])}</code> "
                    f"{bayat['dosya_ts']}'te degisti, "
                    f"surec {bayat['surec_ts']}'ten beri kosuyor "
                    f"({bayat['gecikme_dk']} dk geride).\n\n"
                    "Yeniden baslat: <code>launchctl kickstart -k "
                    "gui/$UID/com.alipala.finagent.bot</code>"))

            # SESSIZ VERI KAYBI. Yukaridaki dort olcut "kosu calisti mi"
            # diye soruyor; bu besincisi "kostu da NE KADARINI getirdi"
            # diye soruyor. Ikisi ayri iddia: 20 Agustos'ta dort kosunun
            # dordu de calisiyordu ve ayni gun isyatirim her kosuda
            # ~150/346 sembol dusuruyordu, kimsenin haberi olmadan.
            # BILDIRIM KARARI BEKCININ ICINDE. Burada `eksik_toplama()`
            # cagirip anahtari elle kurmak, 20 Agustos aksami ALTI
            # bildirimlik spam'i uretti: anahtara ariza listesi
            # konunca liste her KUCULDUGUNDE yeni alarm calmisti.
            karar = self.bekci.eksik_toplama_bildirimi()
            if karar:
                anahtar, eksikler = karar
                if not eksikler:
                    self.bekci.bildir(
                        anahtar,
                        "✅ <b>Veri toplama toparlandi</b>\n"
                        "Eksik donen collector kalmadi.")
                else:
                    satir = "\n".join(
                        f"• <code>{_esc(e['collector'])}</code> — "
                        f"{e['kosu']} kosudur <b>{e['durum']}</b>"
                        + (f"\n   <i>{_esc(e['sebep'])}</i>" if e["sebep"] else "")
                        for e in eksikler[:6])
                    self.bekci.bildir(
                        anahtar,
                        "🕳 <b>Veri toplama sessizce eksik donuyor</b>\n"
                        f"{satir}\n\n"
                        "<i>Bu semboller sorulunca 'veri yok' cevabi cikar — "
                        "oysa sebep kapsam degil, TOPLAMA.</i>\n"
                        "Kontrol: <code>tail -60 data/pulse.log</code>")

            # ALTINCI OLCUT: YEDEK BAYAT MI.
            #
            # `run_kosu.sh` yedek kosup BASARISIZ olursa zaten bildiriyor;
            # bu olcut yedegin sessizce DURMASINI yakaliyor (ayar
            # kapatilmis, dizin gitmis, kosular hic calismamis). Ucunde
            # de kullanici "yedegim var" sanir ve kaybi ancak geri
            # yuklerken ogrenir.
            #
            # ANAHTAR SABIT (`yedek_bayat`), canli veriden kurulmuyor —
            # 20 Agustos'ta alti bildirimlik spam uretmis olan kalibin
            # tekrarlanmamasi icin (bkz. `eksik_toplama_bildirimi`).
            bayat_yedek = self.bekci.yedek_bayat()
            if bayat_yedek:
                self.bekci.bildir("yedek_bayat", (
                    "🗄 <b>Veritabani yedegi bayat</b>\n"
                    f"{_esc(bayat_yedek['sebep'])}"
                    + (f" (son: {_esc(bayat_yedek['en_yeni'])})"
                       if bayat_yedek.get("en_yeni") else "")
                    + f"\nDizin: <code>{_esc(bayat_yedek['dizin'])}</code>\n\n"
                    "<i>Tahmin defteri, sohbet arsivi ve portfoy gecmisi "
                    "yeniden URETILEMEZ.</i>\n"
                    "Elle al: <code>.venv/bin/python run.py yedek</code>"))

            # YEDINCI OLCUT: GUN ICI KOSU PIYASA SAATINDE SESSIZ MI.
            #
            # `kacirilan_kosular` bunu yargilayamiyor — o olcut plist
            # saatlerinden turuyor, gun ici kosu ise aralikla calisiyor
            # ve plist'te saat yok. Pencere DISINDA sessiz kaliyor:
            # kapali piyasada iz tazelenmemesi ariza degil.
            sessiz = self.bekci.gunici_sessiz()
            if sessiz:
                self.bekci.bildir("gunici_sessiz", (
                    "⏱ <b>Gun ici kontrol calismiyor</b>\n"
                    f"{_esc(sessiz['sebep'])} — acik borsa: "
                    f"{_esc(', '.join(sessiz['acik']))}\n\n"
                    "<i>Koruma seviyeleri ve tez kosullari SEANS ICINDE "
                    "kontrol edilmiyor; kirilim ancak aksam kosusunda "
                    "gorulur.</i>\n"
                    "Kontrol: <code>tail -40 data/gunici.log</code>\n"
                    "Elle: <code>launchctl kickstart -p "
                    "gui/$UID/com.alipala.finagent.gunici</code>"))

            self._suresi_dolan_onaylari_dusur()

            for upd in updates:
                uid = upd["update_id"]
                deneme = self._deneme_sayisi(uid) + 1

                # ZEHIRLI MESAJ KAPISI. Bir guncelleme SERT cokme
                # uretiyorsa (kill/OOM — istisna degil) offset hic
                # ilerlemez ve bot sonsuza dek ayni mesaji dener.
                # Ucuncu denemeden sonra ATLA ama SESSIZCE degil.
                if deneme > self.UCUS_AZAMI_DENEME:
                    log.error("guncelleme %s %d kez denendi, ATLANIYOR",
                              uid, deneme - 1)
                    self._ucus_temizle()
                    offset = uid + 1
                    self._write_offset(offset)
                    continue

                self._ucus_yaz(uid, deneme)
                try:
                    self._dispatch(upd)
                except Exception:                     # noqa: BLE001
                    log.exception("guncelleme islenemedi: %s", uid)
                # OFFSET ISTEN SONRA. Istisna yakalandiysa da ilerler —
                # kaybi onlemek istedigimiz sey COKME, hata degil.
                offset = uid + 1
                self._write_offset(offset)
                self._ucus_temizle()

                # KAPATMA ISTENDIYSE burada cik: ucustaki is bitti,
                # sonraki guncellemeye BASLAMA. Planli restart'in
                # mesaj yemesini onleyen sey budur.
                if not self._running:
                    log.info("kapatma istendi — kalan %d guncelleme sonraki "
                             "kosuya birakildi", len(updates) - updates.index(upd) - 1)
                    break

        # CALISAN ISLER OLDURULMEZ. Ayri oturumda (`start_new_session`)
        # kosuyorlar, yani grup sinyali onlara gitmiyor; bitirip
        # cevaplarini KENDILERI gonderecekler. Yeniden baslayan dinleyici
        # kalp atislarini gorup sahiplenir (`Kuyruk.kurtar`).
        if self.kuyruk is not None:
            bekleyen, calisan = self.kuyruk.sayim()
            if bekleyen or calisan:
                log.info("kapaniyor — %d is calismaya devam ediyor, %d is "
                         "sonraki kosuda baslayacak", calisan, bekleyen)
        log.info("Bot durduruldu.")
        return 0

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # HIZLI SERIT — yerinde (ana surecte) calisan isler.
    #
    # Olcut TEK: SALT OKUNUR ve LLM'SIZ olmali. Bunlar yerel veritabani
    # okumasi + tek Telegram gonderimi, yani milisaniyeler. Kuyruga
    # atilsalardi, mesgul bir sohbette menu gezinmek dakikalarca
    # donardi — sikayetin ta kendisi.
    #
    # VARSAYILAN "AGIR"DIR. Yeni bir komut eklenip buraya yazilmazsa
    # bedeli GECIKME olur, BOZULMA degil. Yon bilerek guvenli tarafa
    # bakiyor. `/durum` bilerek DISARIDA: `api_saglik()` ile ag cagrisi
    # yapiyor.
    HIZLI_KOMUTLAR = frozenset({"start", "yardim", "help", "rehber", "bekleyen"})
    HIZLI_CALLBACK = frozenset({"reh", "det"})

    @staticmethod
    def _chat_id(upd: dict):
        cb = upd.get("callback_query")
        if cb:
            return ((cb.get("message") or {}).get("chat") or {}).get("id")
        msg = upd.get("message") or upd.get("edited_message") or {}
        return (msg.get("chat") or {}).get("id")

    def _hizli_mi(self, upd: dict) -> bool:
        cb = upd.get("callback_query")
        if cb:
            return (cb.get("data") or "").partition(":")[0] in self.HIZLI_CALLBACK
        msg = upd.get("message") or upd.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return False                     # sohbet -> her zaman agir
        return text[1:].partition(" ")[0].lower().split("@")[0] \
            in self.HIZLI_KOMUTLAR

    def _dispatch(self, upd: dict) -> None:
        """
        GIRIS: isi ya yerinde yapar ya da kuyruga atar.

        Bu metot HIZLI olmak zorunda — koşu dongusu bunun donmesini
        bekliyor ve o sirada `getUpdates` cagrilmiyor.

        Yetkisiz ya da chat_id'si cozulemeyen guncelleme KUYRUGA
        GIRMEZ; eski yola duser ve reddini oradan alir (davranis
        birebir korunuyor).
        """
        self._basisi_onayla(upd)
        if self.kuyruk is not None and not self._hizli_mi(upd):
            chat_id = self._chat_id(upd)
            if chat_id is not None and self._authorised(chat_id):
                return self._kuyruga_al(upd, chat_id)
        return self._calistir(upd)

    # Basisi ANINDA gorunur kilinan callback'ler — yani ISE DONUSEN
    # butonlar. `reh`/`det` disarida: onlar zaten aninda cevaplaniyor
    # ve menude buton KALMALI (kullanici konular arasinda geziniyor).
    ONAY_CALLBACK = frozenset({"ok", "no", "wl"})

    def _basisi_onayla(self, upd: dict) -> None:
        """
        Butona basildigini ANINDA ve GORUNUR sekilde teyit eder.

        OLCULEN SIKAYET: "Kaydet basilinca geri bildirim almiyorum."
        Sebep tek bir sey degildi, ust uste binen ucu:

          1. Tek isaret `answerCallbackQuery` balonuydu ve is KUYRUGA
             giriyordu; worker balonu dakikalar sonra cagirdiginda
             Telegram "query is too old" donuyordu. Logda duruyor
             (`data/bot.log`), ustelik `_sessiz=True` oldugu icin
             ERROR bile yazmiyordu.
          2. Butonlar basildiktan sonra OLDUGU YERDE kaliyordu —
             mesaj hic degismiyor, yani ekranda hicbir sey olmuyor.
          3. Sonuc mesaji da gonderilebilir ya da gonderilemezdi;
             kimse bakmiyordu.

        Bu metot 1 ve 2'yi kapatiyor ve DINLEYICI SURECINDE calisiyor:
        callback kimligi burada birkac YUZ MILISANIYE yasinda, yani
        balon her zaman tutar. Butonlarin kalkmasi da ayni anda olur.

        BUTONLAR NEDEN IS BITMEDEN KALKIYOR? Cunku sorulan soru "is
        bitti mi" degil, "basisin duyuldu mu". Ikisini ayni isarete
        baglamak, uzun suren iste kullaniciyi yeniden basmaya iter ve
        cift yazim riski dogurur. Is basarisiz olursa TEKRAR DENE butonu
        gonderiliyor (bkz. `_onay_isle`) — istek diskte duruyor, yani
        buton kalksa da geri donus yolu KAPANMIYOR.
        """
        cb = upd.get("callback_query")
        if not cb:
            return
        if (cb.get("data") or "").partition(":")[0] not in self.ONAY_CALLBACK:
            return
        chat_id = self._chat_id(upd)
        if chat_id is None or not self._authorised(chat_id):
            return          # yetki kapisi asagida; burada hicbir sey sizdirma

        if cb.get("id"):
            self.tg.answer_callback_query(cb["id"], "alindi…")
        # ISARETLE: worker ayni balonu bir daha cagirmasin. Guncelleme
        # kuyruga JSON olarak yaziliyor, bayrak onunla birlikte gidiyor.
        cb["_basis_onaylandi"] = True

        mesaj_id = (cb.get("message") or {}).get("message_id")
        if mesaj_id:
            self.tg.edit_message_reply_markup(mesaj_id, None, chat_id=chat_id)

    def _kuyruga_al(self, upd: dict, chat_id) -> None:
        if not self.kuyruk.ekle(upd, chat_id):
            return
        # HEMEN baslatmayi dene: sohbet bossa kuyruk gorunmez olmali.
        self.kuyruk.tik()
        if self.kuyruk.durumu(upd["update_id"]) != "bekliyor":
            return
        # Sirada kaldi -> SOYLE. Sessizlik, "mesajim dusmedi" hissinin
        # ta kendisi; ilerleme gostergesi ancak is BASLAYINCA kuruluyor.
        onunde = self.kuyruk.onunde(upd["update_id"])
        cb = upd.get("callback_query")
        if cb:
            self.tg.answer_callback_query(cb["id"], "sirada")
        self.tg.send_message(
            "⏳ <b>Siraya alindi.</b>\n"
            + (f"Onumde {onunde} is var, " if onunde else "")
            + "sirasi gelince baslayip haber verecegim.", chat_id=chat_id)

    def _calistir(self, upd: dict) -> None:
        """ISIN KENDISI. Worker sureci dogrudan burayi cagirir."""
        if "callback_query" in upd:
            return self._on_callback(upd["callback_query"])

        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            return

        chat_id = (msg.get("chat") or {}).get("id")
        if not self._authorised(chat_id):
            log.warning("Yetkisiz sohbet reddedildi: %s", chat_id)
            return

        if msg.get("photo") or self._image_document(msg):
            return self._on_image(msg, chat_id)

        ses = msg.get("voice") or msg.get("audio") or self._audio_document(msg)
        if ses:
            return self._on_voice(ses, chat_id)

        text = (msg.get("text") or "").strip()
        if text:
            return self._on_text(text, chat_id)

        # Buraya dusen mesaj tipi desteklenmiyor. SESSIZ KALMA: kullanici
        # sesli mesaj attiginda hicbir cevap gelmiyordu ve bunun neden
        # oldugu anlasilmiyordu.
        log.info("Desteklenmeyen mesaj tipi: %s", sorted(msg.keys()))
        self.tg.send_message(
            "🤷 Bu mesaj turunu okuyamiyorum.\n"
            "Desteklenenler: <b>metin</b>, <b>sesli mesaj</b>, <b>ekran goruntusu</b>.",
            chat_id=chat_id)

    @staticmethod
    def _image_document(msg: dict) -> dict | None:
        """'Dosya olarak gonder' ile atilan gorsel — sikistirilmamis, daha net."""
        doc = msg.get("document")
        if doc and str(doc.get("mime_type", "")).startswith("image/"):
            return doc
        return None

    @staticmethod
    def _audio_document(msg: dict) -> dict | None:
        """Ses 'dosya olarak' gonderilmis olabilir."""
        doc = msg.get("document")
        if doc and str(doc.get("mime_type", "")).startswith("audio/"):
            return doc
        return None

    # --- sesli mesaj ------------------------------------------------------
    def _on_voice(self, ses: dict, chat_id) -> None:
        """
        Sesli mesaj -> metin -> normal metin akisi.

        Transkript kullaniciya GERI GOSTERILIR. Konusma tanima hata yapabilir
        ("portfoyumde" -> "port foyumde"); ne anlasildigi gorunmezse yanlis
        soruya dogru cevap gelir ve kullanici bunu fark etmez.
        """
        from ..voice import VoiceError, VoiceTranscriber

        motor = VoiceTranscriber(self.s)
        ok, sebep = motor.hazir()
        if not ok:
            self.tg.send_message(
                f"🎤 Sesli mesaji okuyamiyorum.\n\n<i>{_esc(sebep)}</i>",
                chat_id=chat_id)
            return

        saniye = ses.get("duration")
        self.tg.send_message(
            "🎤 Sesi yaziya cevriyorum"
            + (f" (~{saniye} sn)…" if saniye else "…"), chat_id=chat_id)
        self.tg.chat_action(chat_id, "typing")

        yol = self.tg.download_file(ses["file_id"], self.media_dir)
        if not yol:
            self.tg.send_message("❌ Ses indirilemedi.", chat_id=chat_id)
            return

        try:
            metin = motor.cevir(yol)
        except VoiceError as e:
            self.tg.send_message(f"❌ {_esc(str(e))}", chat_id=chat_id)
            return
        except Exception as e:                        # noqa: BLE001
            log.exception("sesli mesaj cevrilemedi")
            self.tg.send_message(f"❌ {type(e).__name__}: {_esc(str(e)[:150])}",
                                 chat_id=chat_id)
            return

        self.tg.send_message(f"📝 <i>«{_esc(metin)}»</i>", chat_id=chat_id)

        # YIKICI KOMUT KAPISI BURADA KALIYOR — artik iki kat koruma.
        # `_on_text` egik cizgi olmadan komut CALISTIRMIYOR ve transkript
        # cizgi uretmez, yani teorik olarak bu kapi gereksiz. Duruyor
        # cunku: (a) korumanin tek dayanagi baska bir fonksiyonun
        # davranisi olmamali, (b) transkript "/sil" uretebilir ("bolu
        # sil" gibi bir ifade tanimada egik cizgiye donusebiliyor).
        engellenen = sesle_calistirilmaz(metin)
        if engellenen:
            self.tg.send_message(
                f"⚠️ <code>/{_esc(engellenen)}</code> veri siler ve "
                "sesle calistirilmaz.\n"
                "<i>Konusma tanima yanlis duyabiliyor. Gercekten istiyorsan "
                "komutu yazarak gonder.</i>", chat_id=chat_id)
            return

        # Metin akisina devret. Transkriptte egik cizgi olmadigi icin
        # bu pratikte HER ZAMAN sohbete duser — istenen de bu.
        self._on_text(metin, chat_id)

    # --- metin komutlari ------------------------------------------------
    def _on_text(self, text: str, chat_id) -> None:
        """
        Komut mu sohbet mi?

        EGIK CIZGI ZORUNLU. Onceden `cmd.lstrip("/")` vardi, yani cizgi
        istege bagliydi ve bir komut adiyla BASLAYAN her dogal cumle
        komuta kaciyordu. Olculdu, hepsi gercek:
          "sil sunu"                 -> SON PORTFOY KAYDINI SILDI
          "unut gitsin"              -> sohbet hafizasini temizledi
          "haber var mi ASELSAN icin"-> "VAR MI ASELSAN ICIN" diye
                                        hisse aradi
          "temizle biraz yer ac"     -> COKTU
        Ikisi VERI SILIYORDU, hicbir onay sormadan. Sesli komutlarda
        `YIKICI_KOMUTLAR` ile korunan sey, yazili metinde acikti.

        Artik varsayilan SOHBET: cizgisiz her sey modele gider.
        Komutlar duruyor ama yalnizca `/` ile cagriliyor.
        """
        cmd = ""
        if text.startswith("/"):
            ilk, _, arg_ham = text.partition(" ")
            cmd = ilk[1:].lower().split("@")[0]
            arg = arg_ham.strip()
        else:
            arg = ""

        if not cmd:
            # ONAY NIYETI once bakilir: bekleyen bir istek varken "kaydet"
            # demek, butona basmakla ayni sey olmali. Bekleyen yoksa
            # dokunmadan modele gecer.
            if self._dogal_onay(text, chat_id):
                return
            # Komut degilse SOHBET. Son gonderilen gorsel de tasinir ki
            # "az once attigim resimdeki..." turu istekler calissin.
            self._sohbet(text, chat_id, gorsel=self._gorsel_al(chat_id))
            return

        if cmd in ("start", "yardim", "help"):
            self.tg.send_message(YARDIM, chat_id=chat_id)
        elif cmd == "rehber":
            self._rehber(chat_id, arg)
        elif cmd == "durum":
            self.tg.send_message(self._durum_text(self.s.sahip_bul(chat_id)),
                                 chat_id=chat_id)
        elif cmd == "portfoy":
            sahip = self.s.sahip_bul(chat_id)
            self.tg.send_message(
                self._portfoy_text(sahip) if sahip else _SAHIPSIZ,
                chat_id=chat_id)
        elif cmd == "evren":
            self.tg.send_message(self._evren_text(arg), chat_id=chat_id)
        elif cmd in ("onayla", "hepsi"):
            self._hepsini_onayla(chat_id)
        elif cmd == "bekleyen":
            self.tg.send_message(self._bekleyen_text(chat_id), chat_id=chat_id)
        elif cmd == "aday":
            self.tg.send_message(self._aday_ekle(arg), chat_id=chat_id)
        elif cmd == "haber":
            self._haber_tara(chat_id, arg)
        elif cmd == "etki":
            self.tg.send_message(self._etki_text(arg), chat_id=chat_id)
        elif cmd == "temizle":
            self.tg.send_message(self._temizle(arg), chat_id=chat_id)
        elif cmd == "takip":
            self.tg.send_message(self._takip_text(), chat_id=chat_id)
        elif cmd == "kimlik":
            self.tg.send_message(self._kimlik_text(arg), chat_id=chat_id)
        elif cmd == "sil":
            sahip = self.s.sahip_bul(chat_id)
            self.tg.send_message(
                self._sil_son(sahip) if sahip else _SAHIPSIZ, chat_id=chat_id)
        elif cmd in ("rapor", "ozet"):
            self._calistir_rapor(chat_id, topla=(cmd == "rapor"))
        elif cmd == "unut":
            self.tg.send_message(self._unut(chat_id, arg), chat_id=chat_id)
        elif cmd == "hatirladiklarin":
            self._gonder(self._hatirladiklarin_metni(chat_id, arg), chat_id)
        else:
            # BILINMEYEN KOMUT SOHBETE DUSER, hata mesajina degil.
            # Kullanici komut ezberlemek zorunda degil; "/ASELSAN nasil"
            # yazana "bilinmeyen komut" demek, cevabi bilmemek degil
            # SORMAMAK olurdu. Cizgiyi atip modele veriyoruz.
            log.info("bilinmeyen komut sohbete dusuruldu: /%s", cmd)
            self._sohbet(text.lstrip("/"), chat_id,
                         gorsel=self._gorsel_al(chat_id))

    # --- sohbet ----------------------------------------------------------
    def _chat(self):
        if getattr(self, "_chat_engine", None) is None:
            from .chat import ChatEngine
            self._chat_engine = ChatEngine(self.s, self.db)
        return self._chat_engine

    # --- sohbet ici gorsel hafizasi ---------------------------------------
    #
    # Gorsel TASINIYOR ki "az once attigim resimdeki..." calissin. Ama
    # SURESIZ tasiniyordu ve iki zarari olculdu (2026-08-17):
    #   1. Gorsel varken `Read` araci aciliyor (modelin goruntuye ulasmasi
    #      icin). Gorsel hic silinmedigi icin Read SONSUZA KADAR acik
    #      kaliyordu; model uc tur sonra onunla KAYNAK KODU okudu.
    #   2. Alakasiz bir sonraki soruya 40 dakika onceki ekran goruntusu
    #      ekleniyordu.
    # Cozum: dar bir pencere. Ekran goruntusu bir SORUNUN ekidir, sohbetin
    # kalici parcasi degil.
    GORSEL_OMRU_SN = 15 * 60

    def _gorsel_kaydi(self, chat_id) -> Path:
        # chat_id DOSYA ADINA giriyor: Telegram tam sayi gonderiyor ama
        # dis veriden gelen bir degeri dogrudan yola yazmak, ileride bir
        # cagiran degistiginde sessiz bir yol kacisi kanali olurdu.
        ad = "".join(ch for ch in str(chat_id) if ch.isdigit() or ch == "-")
        return self.gorsel_dir / f"{ad or 'bilinmeyen'}.json"

    def _gorsel_koy(self, chat_id, yol) -> None:
        # DISKE: gorseli alan tur ile onu kullanan tur AYRI SURECLER
        # olabiliyor (bkz. bot/kuyruk.py). Surec ici bir sozluk bu
        # zinciri sessizce koparirdi.
        try:
            self._gorsel_kaydi(chat_id).write_text(
                json.dumps({"yol": str(yol), "ts": time.time()}),
                encoding="utf-8")
        except OSError as e:                          # noqa: BLE001
            log.warning("gorsel kaydi yazilamadi (chat %s): %s", chat_id, e)

    def _gorsel_al(self, chat_id) -> str | None:
        kayit_yolu = self._gorsel_kaydi(chat_id)
        try:
            kayit = json.loads(kayit_yolu.read_text(encoding="utf-8"))
            yol, ts = str(kayit["yol"]), float(kayit["ts"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        if time.time() - ts > self.GORSEL_OMRU_SN:
            kayit_yolu.unlink(missing_ok=True)
            log.info("[sohbet] %s: gorsel suresi doldu, Read kapandi", chat_id)
            return None
        return yol

    # --- rehber -----------------------------------------------------------
    def _rehber(self, chat_id, arg: str) -> None:
        """
        Gezinilebilir yetenek rehberi.

        Butonlu MENU, tek uzun metin degil: `/yardim` uzadikca okunmaz
        oluyor ve zaten eksikti. Menu KESIFEDILEBILIR — ne soracagini
        bilmeyen kullanici goz gezdirir.
        """
        from . import yetenekler
        konu = (arg or "").strip().lower()

        if konu == "sifirla":
            sahip = self.s.sahip_bul(chat_id)
            n = self.db.ogretilenleri_sifirla(sahip) if sahip else 0
            self.tg.send_message(
                f"🔄 {n} ipucu sifirlandi; bastan anlatabilirim.",
                chat_id=chat_id)
            return

        if konu in yetenekler.KONULAR:
            self.tg.send_message(yetenekler.konu_metni(konu),
                                 reply_markup=yetenekler.menu_markup(),
                                 chat_id=chat_id)
            return

        self.tg.send_message(yetenekler.menu_metni(),
                             reply_markup=yetenekler.menu_markup(),
                             chat_id=chat_id)

    def _unut(self, chat_id, arg: str) -> str:
        """
        `/unut` calisma hafizasini siler, ARSIVI SILMEZ.

        Ikisi ayri seyler ve fark KULLANICIYA SOYLENMELI: "sohbet gecmisi
        silindi" deyip arsivi tutmak, kullanicinin sildigini sandigi bir
        kaydi saklamak olurdu. Arsivi de silmek icin `/unut arsiv` — ACIK
        istek gerekiyor cunku geri donusu yok.
        """
        self._chat().unut(chat_id)
        # BEKLEYEN ONAYLAR DA IPTAL. Telegram'dan sohbeti temizleyen
        # kullanicinin butonlu mesaji kaybolur ama `pending/` dosyasi
        # kalirdi; sonraki `/onayla` GORULMEYEN bir ekran goruntusunu
        # portfoye yazardi.
        # HEPSI iptal: `tipler=None`. Sohbeti unutan kullanicinin
        # ekraninda hicbir buton kalmiyor; diskte duran bir rapor ya da
        # silme istegi de artik sahipsiz.
        # `.isleniyor` ve `.hata` izleri de silinir (`depo.sil`): yalnizca
        # `.json` silinseydi hata almis bir istek diskte kalir ve
        # `/bekleyen` "yarim kalmis is" diye onu sonsuza kadar gosterirdi.
        depo = self._depo()
        n = 0
        for o in self._bekleyen_onaylar(chat_id, tipler=None):
            depo.sil(o.token)
            n += 1
        for o in depo.asili_isler(timedelta(0)):
            if str(o.veri.get("_chat_id") or chat_id) == str(chat_id):
                depo.sil(o.token)

        satir = ["🧹 Modelin gordugu sohbet gecmisi silindi."]
        if n:
            satir.append(f"<b>{n}</b> bekleyen onay da iptal edildi.")

        if (arg or "").strip().lower() in ("arsiv", "arşiv", "hepsi"):
            sahip = self.s.sahip_bul(chat_id)
            if not sahip:
                satir.append("Arsiv SILINMEDI: " + _SAHIPSIZ)
            else:
                silinen = self.db.sohbet_sil(sahip)
                satir.append(f"🗑 Arsivden <b>{silinen}</b> tur da silindi.")
        else:
            kalan = self.db.sohbet_sayisi(self.s.sahip_bul(chat_id))
            satir.append(
                f"Kalici arsiv DURUYOR ({kalan} tur) — <code>/unut arsiv</code> "
                "onu da siler.")
        return "\n".join(satir)

    # ------------------------------------------------------------------
    # ONAY DEPOSU + GARANTILI GONDERIM
    # ------------------------------------------------------------------
    def _depo(self):
        """
        `pending/` kapisi. HER CAGRIDA yeniden kuruluyor, bilerek.

        `pending_dir` kurulumdan SONRA degistirilebiliyor (testler
        dogrudan atiyor, worker ayri surecte kuruyor). Depoyu `__init__`de
        bir kez baglasaydik, degistirilen dizin sessizce ONEMSENMEZDI ve
        onaylar iki ayri yere yazilirdi. Nesne durum tutmuyor; maliyeti
        bir `Path` sarmalamak.
        """
        from .onay import OnayDeposu
        return OnayDeposu(self.pending_dir)

    def _suresi_dolan_onaylari_dusur(self) -> int:
        """
        Onaylanmamis eski istekleri DUSURUR — once haber vererek.

        NEDEN (olculdu 2026-08-20): `pending/` altinda 1-2 gunluk SEKIZ
        kayit birikmisti, ikisi 17 Agustos'tan. Dusme diye bir kavram
        yoktu. Birikmis onay iki turlu zarar verir: `/bekleyen` listesi
        okunamaz hale gelir, ve gunler once sunulmus bir YAZMA butonu
        hala canlidir.

        SIRA SOZLESMEDIR: haber -> silme. `_gonder` gonderimi DOGRULUYOR
        ve basarisizsa False donuyor; o durumda dosya YERINDE KALIR ve
        bir sonraki turda yeniden denenir. Ters sirada bir Telegram
        kesintisi, kullanicinin hic haberi olmadan isteklerini silerdi
        (`onay.py` modul basligi: "sessiz silme, sessiz yazmanin
        ikizidir").

        SOHBET BASINA TEK MESAJ. Bugunku sekiz kayit icin sekiz bildirim
        atmak, uyariyi gurultuye cevirirdi — ve okunmayan uyari
        gonderilmemis uyaridir.
        """
        depo = self._depo()
        dusenler = depo.suresi_dolanlar()
        if not dusenler:
            return 0

        from .onay import SURE_ASIMI, yas_metni

        # Sahipsiz eski dosyalar BIRINCI sahibe raporlanir: kime ait
        # oldugu BILINMIYOR, ama sessizce silmek tam da gorunur kilmak
        # istedigimiz anomaliyi gizlerdi. Kaynak `settings.sahipler` —
        # bildirici nesnesinin ic alani degil; o alan `.env`den geliyor
        # ve cok kullanicida "varsayilan sohbet" diye bir kavram yok.
        birinci = next(iter(self.s.sahipler), "")

        gruplar: dict[str, list] = {}
        for o in dusenler:
            hedef = str(o.veri.get("_chat_id") or birinci or "")
            gruplar.setdefault(hedef, []).append(o)

        dusen = 0
        saat = int(SURE_ASIMI.total_seconds() // 3600)
        for chat_id, liste in gruplar.items():
            if not chat_id:
                log.warning("[onay] %d suresi dolmus istek raporlanamiyor: "
                            "hedef sohbet yok", len(liste))
                continue
            en_eski = max(liste, key=lambda o: o.yas_sn)
            tipler = ", ".join(sorted({o.tip for o in liste}))
            metin = (f"⏳ <b>{len(liste)} bekleyen kayit dustu</b> — "
                     f"{saat} saati gecti, onaylanmadi.\n"
                     f"<i>{_esc(tipler)}</i> · en eskisi "
                     f"{yas_metni(en_eski.yas_sn)}. Gerekiyorsa yeniden iste.")
            if not self._gonder(metin, chat_id):
                log.warning("[onay] dusme haberi gonderilemedi (%s) — "
                            "%d istek YERINDE BIRAKILDI", chat_id, len(liste))
                continue
            for o in liste:
                depo.sil(o.token)
                dusen += 1
        if dusen:
            log.info("[onay] %d suresi dolmus istek dusuruldu", dusen)
        return dusen

    def _gonder(self, metin: str, chat_id, reply_markup: dict | None = None,
                *, kritik: bool = False) -> bool:
        """
        Mesaji gonderir VE gittigini dogrular. Basarisizsa sadeleserek yeniden dener.

        NEDEN VAR — SESSIZ BASARISIZLIK BURADAN GIRIYORDU. `send_message`
        bool donuyor ama cagiranlarin HICBIRI bakmiyordu:

            self.tg.send_message(self._pozisyon_kaydet(parsed, sahip), ...)

        Bu satirda veritabani yazimi ZATEN olmustur. Telegram mesaji
        reddederse (en sik sebep: `parse_mode=HTML` ile kacilmamis bir
        `<` ya da `&`) kullanici hicbir sey gormez ve portfoyunun
        degistigini BILMEZ. "Kaydedildi" diyen cumle, kimseye
        ulasmadigi icin yok hukmunde olur — ama kayit gercektir.

        UC KADEME, her biri bir oncekinden daha az sey varsayar:
          1. Oldugu gibi (HTML)
          2. Etiketler sokulup kacilmis hali — bicim bozulur, BILGI KALIR
          3. Kritikse: tek satirlik duz uyari

        Ucu de dusersse metin LOG'A yazilir; kullaniciya ulasamadik ama
        kayit KAYBOLMADI. Bu satiri gormek, "neden haberim olmadi"
        sorusunun cevabidir.
        """
        if self.tg.send_message(metin, reply_markup=reply_markup,
                                chat_id=chat_id):
            return True

        log.warning("mesaj gonderilemedi (chat %s), sadelestirip yeniden "
                    "deneniyor", chat_id)
        sade = _esc(re.sub(r"<[^>]+>", "", metin))
        if sade.strip() and self.tg.send_message(sade, reply_markup=reply_markup,
                                                 chat_id=chat_id):
            return True

        if kritik and self.tg.send_message(
                "Bir islem tamamlandi ama sonucunu gonderemedim. "
                "Durumu gormek icin /portfoy ya da /bekleyen yaz.",
                chat_id=chat_id):
            log.error("SONUC METNI ULASMADI (chat %s), yalin uyari gitti. "
                      "Ulasmayan metin: %s", chat_id, metin[:2000])
            return True

        log.error("MESAJ HIC ULASMADI (chat %s). Metin: %s",
                  chat_id, metin[:2000])
        return False

    # Onay butonunun yazisi ISLEME GORE degisir. Yikici bir islemde
    # "✅ Kaydet" yazan bir buton, kullaniciya ne onayladigini YANLIS
    # soyler — butonun metni tek basina anlasilir olmali.
    _ONAY_ETIKET = {"rapor": "▶️ Baslat", "sil_son": "🗑 Evet, geri al",
                    "watchlist": "✅ Ekle", "hatirla": "🧠 Hatirla"}

    def _onay_etiketi(self, token: str) -> str:
        veri = self._depo().oku(token)
        return self._ONAY_ETIKET.get((veri or {}).get("_tip"), "✅ Kaydet")

    def _onay_markup(self, token: str) -> dict:
        """Kaydet/Iptal klavyesi. Tek yerde kuruluyor: ilk gosterim,
        yeniden gosterim ve HATA SONRASI TEKRAR DENEME ayni butonu
        kullanmali, yoksa uc ayri yerde uc ayri `callback_data` olur."""
        return {"inline_keyboard": [[
            {"text": self._onay_etiketi(token), "callback_data": f"ok:{token}"},
            {"text": "❌ Iptal", "callback_data": f"no:{token}"}]]}

    # KULLANICININ BIR ISLEM BILDIRDIGINI GOSTEREN KOKLER.
    #
    # Turkce cekim ekleri yuzunden kok bazli: "aldim/aldık/almıştım",
    # "sattim/satmıştım". Sorulari DISLAMIYOR bilerek — bu liste bir
    # KARAR vermiyor, yalnizca OLCUYOR (bkz. `_islem_bildirimi_kacti`).
    ISLEM_KOKLERI = ("aldim", "aldım", "aldik", "aldık", "almistim",
                     "almıştım", "sattim", "sattım", "satmistim",
                     "satmıştım", "girdim", "ciktim", "çıktım")

    @classmethod
    def _islem_bildirimi_kacti(cls, soru: str, araclar) -> bool:
        """
        Kullanici bir ISLEM bildirdi ama `pozisyon_kaydet` cagrilmadi mi?

        OLCULEN ZARAR (2026-08-19 14:51): kullanici ekran goruntusuyle
        "Bu kadar aldim ... kaca vereyim?" dedi. Model pozisyonu okudu,
        kur makasini hesapladi, seviye tablosu verdi — ama deftere
        gecmedi. Moderna portfoye HIC girmedi ve bunu ancak ertesi gun
        kullanici fark etti.

        BU KONTROL KARAR VERMIYOR, OLCUYOR. Kullanicinin niyetini
        ("aldim" mi, "alsam ne olur" mu) kelime listesiyle ayirmak
        guvenilmez; o is modelin ve prompt kurali 6b onu soyluyor.
        Buradaki sayac, kural TUTMADIGINDA boslugun SESSIZ kalmamasi
        icin — `sade_kanit_dusurdu` ile ayni kalip: olc, bloke etme.
        """
        if not soru:
            return False
        kucuk = soru.lower()
        if not any(k in kucuk for k in cls.ISLEM_KOKLERI):
            return False
        return "pozisyon_kaydet" not in {str(a) for a in (araclar or [])}

    def _arsivle(self, chat_id, sahip, soru: str, cevap: str,
                 gorsel: str | None, araclar) -> None:
        """
        Sohbeti kalici arsive yazar. ASLA cevabi engellemez.

        Arsivleme bir YAN ETKI; veritabani kilitli ya da disk dolu diye
        kullanicinin cevabi kaybolmamali. Bu yuzden genis except ve
        yalnizca log — ama SESSIZ degil, cunku fark edilmeyen bir arsiv
        arsiv degildir.
        """
        if self._islem_bildirimi_kacti(soru, araclar):
            log.warning(
                "[sohbet] ISLEM BILDIRIMI KACMIS OLABILIR (chat %s): "
                "kullanici bir islem bildirdi ama `pozisyon_kaydet` "
                "cagrilmadi. Soru: %r", chat_id, (soru or "")[:120])
        try:
            self.db.sohbet_kaydet(chat_id, "user", soru,
                                  sahip=sahip, gorsel=bool(gorsel))
            self.db.sohbet_kaydet(chat_id, "assistant", cevap,
                                  sahip=sahip, araclar=araclar)
        except Exception as e:                        # noqa: BLE001
            log.warning("sohbet arsivine yazilamadi (chat %s): %s", chat_id, e)

    def _sohbet(self, soru: str, chat_id, gorsel: str | None = None) -> None:
        """
        Serbest sohbet. Model araclariyla calisir ve ISLEM de yapabilir.

        Model bir yazma islemi hazirladiysa (`pozisyon_kaydet`) mesaja
        Kaydet/Iptal butonu eklenir — mimari §5 insan onayi korunuyor ama
        tek dokunusa iniyor.
        """
        motor = self._chat()
        # SAHIP TEK SINIRDA cozulur ve asagi PARAMETRE olarak tasinir.
        sahip = self.s.sahip_bul(chat_id)

        # ILERLEME GOSTERGESI. Cevap 30-60 sn suruyor ve onceden burada
        # yalnizca TEK bir `chat_action` vardi — Telegram'in "yaziyor…"
        # gostergesi ~5 SANIYEDE soner, yani kullanici kalan 25-55
        # saniyeyi sessizlikte geciriyordu ve "mesajim dusmedi galiba"
        # diyordu. Artik kalici bir durum mesaji var, model her arac
        # cagirdiginda GERCEK ilerlemeyi yaziyor ve cevaptan hemen once
        # siliniyor. Bkz. bot/ilerleme.py.
        from .ilerleme import Ilerleme
        with Ilerleme(self.tg, chat_id) as gosterge:
            sonuc = motor.cevapla(chat_id, soru, gorsel=gorsel, sahip=sahip,
                                  ilerleme=gosterge.arac_gordu)
        cevap = sonuc["metin"]

        # IKI AYRI KAYIT, IKI AYRI AMAC — karistirilmamali:
        #   gecmis_yaz -> modelin GORDUGU pencere. Dar ve budanir
        #                 (son 8 tur, 6 saat), cevap 1500 karakterde kesilir.
        #   _arsivle   -> SAKLANAN kayit. Tam metin, budama yok.
        # Onceden yalnizca birincisi vardi ve arsiv gorevini de o
        # gorunuyordu; "gecen hafta ne konusmustuk" sorusunun cevabi
        # sessizce silinmis oluyordu.
        self._arsivle(chat_id, sahip, soru, cevap, gorsel, sonuc.get("araclar"))

        gecmis = motor.gecmis_oku(chat_id)
        gecmis += [{"rol": "user", "metin": soru},
                   {"rol": "assistant", "metin": cevap[:1500]}]
        motor.gecmis_yaz(chat_id, gecmis)

        from ..notify.telegram import md_to_tg_html
        tokenlar = sonuc["tokenlar"]
        markup = None
        if tokenlar:
            t = tokenlar[-1]        # birden fazlaysa sonuncusu gecerli
            markup = self._onay_markup(t)
        # KRITIK: modelin cevabi Telegram tarafindan reddedilirse (bicim
        # hatasi) kullanici 40 saniye bekleyip HICBIR SEY almiyordu ve
        # tur kaybolmus gorunuyordu. Arsivde duruyor ama kimse bakmiyor.
        self._gonder(md_to_tg_html(cevap), chat_id, reply_markup=markup,
                     kritik=True)

        # GORSELLER cevaptan SONRA gider. Once metin gonderiliyor cunku
        # gorsel yuklemesi birkac saniye surebiliyor ve kullanicinin
        # cevabi beklemesi gereksiz olurdu.
        basarisiz: list[str] = []
        for g in sonuc["gorseller"]:
            neden = "Telegram kabul etmedi"
            try:
                ok = self.tg.send_photo(Path(g["yol"]), _esc(g.get("aciklama", "")),
                                        chat_id=chat_id)
            except Exception as e:                    # noqa: BLE001
                ok, neden = False, str(e)
                log.warning("gorsel gonderilemedi (%s): %s", g.get("yol"), e)
            if not ok:
                basarisiz.append(f"{Path(g['yol']).name} — {neden}")

        # SESSIZ BASARISIZLIK YASAK. Model cevabinda "ekran goruntusu
        # alindi" yazmis olabilir; gorsel gitmezse kullanici olmayan bir
        # seyi arar. Olculdu 2026-08-16: bot eski kodla calisiyordu,
        # gorsel HIC gonderilmedi ve HICBIR yerde iz birakmadi.
        # send_photo istisna ATMAZ, False DONER — bu yuzden donus degeri
        # kontrol ediliyor, yalnizca try/except yetmiyor.
        if basarisiz:
            self.tg.send_message(
                "⚠️ <b>Gorsel gonderilemedi</b>\n"
                + "\n".join("• " + _esc(b) for b in basarisiz)
                + "\n\nDosya diskte duruyor, metindeki okuma gecerli.",
                chat_id=chat_id)

    # --- goruntu akisi --------------------------------------------------
    def _on_image(self, msg: dict, chat_id) -> None:
        doc = self._image_document(msg)
        if doc:
            file_id, kaynak = doc["file_id"], "dosya"
        else:
            # photo[] artan boyutta gelir; en buyuk surum en okunabilir olan.
            file_id, kaynak = msg["photo"][-1]["file_id"], "sikistirilmis foto"

        # Aciklama (caption) modu belirler:
        #   yok / sadece "bux"|"midas"|"binance" -> KAYDET akisi (siniflandir + onay)
        #   baska bir metin             -> SORU: ekrani oku, cevapla, kaydetme
        # Boylece "her hisseyi ekran goruntusuyle atmak" gerekmiyor; tek tek
        # sorup katalog durumunu ogrenmek mumkun oluyor.
        caption = (msg.get("caption") or "").strip()
        kelimeler = [k for k in re.split(r"[^\wçğıöşüÇĞİÖŞÜ]+", caption.lower()) if k]
        hint = next((a for a in ("bux", "midas", "binance") if a in kelimeler), None)
        sadece_ipucu = bool(kelimeler) and all(k in ("bux", "midas", "binance") for k in kelimeler)
        soru = caption if (caption and not sadece_ipucu) else None

        if soru:
            return self._gorsel_soru(file_id, soru, chat_id)

        self.tg.send_message(f"🔍 Goruntu alindi ({kaynak}), okuyorum…", chat_id=chat_id)

        path = self.tg.download_file(file_id, self.media_dir)
        if not path:
            self.tg.send_message("❌ Goruntu indirilemedi.", chat_id=chat_id)
            return
        self._gorsel_koy(chat_id, path)

        from ..vision import ScreenshotReader, VisionError
        try:
            parsed = ScreenshotReader(self.s).read_positions(path, account_hint=hint)
        except VisionError as e:
            self.tg.send_message(f"❌ Okuyamadim: {e}", chat_id=chat_id)
            return
        except Exception as e:                        # noqa: BLE001
            log.exception("goruntu okuma hatasi")
            self.tg.send_message(f"❌ Beklenmeyen hata: {type(e).__name__}: {e}",
                                 chat_id=chat_id)
            return

        if parsed.get("ekran_tipi") == "liste":
            return self._liste_onayi(parsed, chat_id)

        if not parsed["pozisyonlar"]:
            # NE YAPILDIGI SOYLENIYOR. Onceden tek cumleyle "pozisyon
            # goremedim" deniyordu ve bu, ekran GERCEKTEN portfoy
            # ekraniysa yanlis beyandi. Artik uc gecis yapiliyor
            # (`vision.read_positions`) ve modelin EKRANI NE OLARAK
            # TANIDIGI da yaziliyor — kullanici neyi duzeltecegini bilsin.
            not_ = parsed.get("notlar") or "ekranda pozisyon satiri bulunamadi"
            kac = 3 if parsed.get("ikinci_bakis") else 2
            self.tg.send_message(
                f"⚠️ <b>Pozisyon cikaramadim.</b> Goruntuyu {kac} kez "
                "bagimsiz okudum.\n"
                f"<i>Gordugum: {_esc(not_)}</i>\n\n"
                "Portfoy/holdings ekranini tam gorunur halde tekrar "
                "gonderirsen okurum. Birden fazla ekran gerekiyorsa "
                "HEPSINI TEK SEFERDE (albüm olarak) gonderebilirsin.",
                chat_id=chat_id)
            return

        if not parsed["hesap"]:
            self.tg.send_message(
                "⚠️ Hangi hesap oldugunu ayirt edemedim. Gorseli tekrar gonderirken "
                "aciklamasina <code>bux</code> veya <code>midas</code> yaz.",
                chat_id=chat_id)
            return

        # ONAY DOSYASI SAHIBI VE SOHBETI TASIR (A4). Onaylayan taraf
        # kime yazacagini buradan okur; `/onayla` ve `/bekleyen` de
        # yalnizca kendi sohbetinin dosyalarini gorur.
        sahip = self.s.sahip_bul(chat_id)

        # COKLU GORUNTU — TEK ONAY.
        #
        # Kullanici bazen portfoyu TEK EKRANA sigdiramiyor ve birden
        # fazla goruntu gonderiyor (2026-08-21 istegi). Telegram bunlari
        # ALBUM olarak yollar: ayri ayri mesajlar ama AYNI
        # `media_group_id`. Her birini ayri onaya cevirmek iki sorun
        # uretirdi:
        #   * kullanici uc ayri onay ekrani gorurdu;
        #   * her onay KENDI basina "portfoyun tamami" sayilir ve
        #     `pozisyon_kaydet`in kume kiyasi digerlerini SATILMIS
        #     sanardi (bkz. `pozisyon-yazma-semantigi`).
        # Bu yuzden ayni albumun pozisyonlari TEK onayda birlesiyor.
        grup = str(msg.get("media_group_id") or "") or None
        onceki = self._albumu_bul(grup, chat_id) if grup else None
        if onceki:
            token, birlesik = onceki
            eklenen = self._pozisyon_birlestir(birlesik, parsed)
            self._depo().yaz(token, birlesik)
            self._gonder(
                self._onay_metni(birlesik, sahip)
                + f"\n\n<i>Bu onay {birlesik.get('_gorsel', 1)} goruntuden "
                  f"birlestirildi (+{eklenen} yeni satir).</i>",
                chat_id, reply_markup=self._onay_markup(token))
            return

        token = secrets.token_hex(6)
        kayit = json.loads(json.dumps(
            {**parsed, "_sahip": sahip, "_chat_id": str(chat_id),
             "_grup": grup, "_gorsel": 1},
            ensure_ascii=False, default=str))
        self._depo().yaz(token, kayit)

        self._gonder(self._onay_metni(parsed, sahip), chat_id,
                     reply_markup=self._onay_markup(token))

    def _albumu_bul(self, grup: str, chat_id):
        """
        Bu albume ait BEKLEYEN onay varsa (token, kayit) dondurur.

        Yalnizca AYNI SOHBETIN bekleyen kayitlarina bakiyor: albüm
        kimligi Telegram genelinde benzersiz ama sahip ayrimi yine de
        kodda durmali — `sahip` bu projede PARAMETRE, ortam degil.
        """
        try:
            for o in self._depo().bekleyenler(chat_id=chat_id):
                kayit = o.veri if hasattr(o, "veri") else None
                if kayit and kayit.get("_grup") == grup:
                    return o.token, kayit
        except Exception as e:                        # noqa: BLE001
            # ALBUM BULUNAMAZSA AKIS DUSMEZ: en kotu ihtimalle ikinci
            # goruntu AYRI bir onay acar — eski davranis.
            log.warning("[gorsel] album aranamadi: %s", e)
        return None

    @staticmethod
    def _pozisyon_birlestir(hedef: dict, yeni: dict) -> int:
        """
        Yeni goruntunun pozisyonlarini hedefe ekler; KAC YENI eklendi.

        AYNI SEMBOL IKI KEZ TOPLANMAZ: albumdeki ekranlar cakisabilir
        (kullanici kaydirirken ayni satir iki karede gorunur) ve adetleri
        toplamak portfoyu IKIYE KATLARDI. Cakisan sembolde ILK okuma
        korunuyor — sonraki kare genellikle kismen gorunen satiri
        tasiyor.
        """
        var = {str(p.get("symbol") or "").upper()
               for p in (hedef.get("pozisyonlar") or [])}
        eklenen = 0
        for p in (yeni.get("pozisyonlar") or []):
            sem = str(p.get("symbol") or "").upper()
            if not sem or sem in var:
                continue
            hedef.setdefault("pozisyonlar", []).append(p)
            var.add(sem)
            eklenen += 1
        hedef["_gorsel"] = int(hedef.get("_gorsel") or 1) + 1
        # HESAP: ilk goruntude cozulememisse sonraki cozebilir.
        if not hedef.get("hesap") and yeni.get("hesap"):
            hedef["hesap"] = yeni["hesap"]
        return eklenen

    def _gorsel_soru(self, file_id: str, soru: str, chat_id) -> None:
        """
        Ekran goruntusu + soru -> AJAN turu.

        Eskiden iki adimliydi: once vision goruntuyu metne cevirir, sonra
        o metin sohbete verilirdi. Iki sorunu vardi:
          * Model goruntuyu KENDISI goremiyordu; ara ozet neyi atlarsa
            o bilgi kayboluyordu.
          * Sohbet turunun araci yoktu, dolayisiyla "resimde gordugun
            kadar ROSE'u portfoyume ekle" gibi bir istek IMKANSIZDI.
        Artik goruntu dogrudan ajana veriliyor: Read ile kendisi aciyor,
        gerekirse pozisyon_kaydet ile onaya sunuyor.
        """
        self.tg.send_message("🔍 Ekrani okuyup cevapliyorum…", chat_id=chat_id)
        yol = self.tg.download_file(file_id, self.media_dir)
        if not yol:
            self.tg.send_message("❌ Goruntu indirilemedi.", chat_id=chat_id)
            return
        self._gorsel_koy(chat_id, yol)
        self._sohbet(soru, chat_id, gorsel=str(yol))

    def _liste_onayi(self, p: dict, chat_id) -> None:
        """Alinabilir enstruman listesi -> izleme listesine aday olarak eklenir."""
        satirlar = p.get("liste") or []
        if not satirlar:
            self.tg.send_message(
                "⚠️ Bu goruntude enstruman listesi goremedim.\n"
                f"<i>{_esc(p.get('notlar') or '')}</i>", chat_id=chat_id)
            return

        # SOHBET KIMLIGI DOSYAYA GIRIYOR. Onceden girmiyordu ve cok
        # sahipli kurulumda `_bekleyenler` bu dosyalari "kime ait
        # bilinmiyor" diye ATLIYORDU: `/onayla` onlari hic gormuyordu.
        token = secrets.token_hex(6)
        self._depo().yaz(token, json.loads(json.dumps(
            {**p, "_sahip": self.s.sahip_bul(chat_id), "_chat_id": str(chat_id)},
            ensure_ascii=False, default=str)))

        L = [f"📋 <b>Izleme listesi adayi</b> — {len(satirlar)} enstruman okundu", ""]
        for r in satirlar[:25]:
            satir = f"• {_esc(r['name'])}"
            if r.get("last_price") is not None:
                satir += f"  {_money(r['last_price'])}"
            if r.get("change_pct") is not None:
                satir += f"  ({r['change_pct']:+.2f}%)"
            L.append(satir)
        if len(satirlar) > 25:
            L.append(f"<i>… ve {len(satirlar) - 25} tane daha</i>")

        if p.get("celiskiler"):
            L += ["", "🔶 <b>Iki okuma ayrisan satirlar</b> (kaydedilmeyecek):"]
            L += [f"  • {_esc(c)}" for c in p["celiskiler"][:6]]

        L += ["", "<i>Kaydedersem bu enstrumanlarin kimligini cozup SEC/IR "
              "birincil kaynaklarini ve haberlerini taramaya baslarim.</i>",
              "", "Izleme listesine eklensin mi?"]

        self.tg.send_message("\n".join(L), reply_markup={"inline_keyboard": [[
            {"text": "✅ Ekle", "callback_data": f"wl:{token}"},
            {"text": "❌ Iptal", "callback_data": f"no:{token}"},
        ]]}, chat_id=chat_id)

    def _watchlist_kaydet(self, p: dict, chat_id) -> None:
        from ..research import IdentityResolver
        resolver = IdentityResolver(self.s, self.db)

        eklendi, cozulemeyen = [], []
        for r in p.get("liste") or []:
            # Sembol ekranda yoksa gecici anahtar olarak adi kullan; kimlik
            # cozumlemesi dogru ticker'i zaten kendisi bulacak.
            sembol = r.get("symbol") or _gecici_sembol(r["name"])
            iid = self.db.upsert_instrument(sembol, "BUX", name=r["name"],
                                            asset_type=None, currency=r.get("currency"))
            self.db.add_watchlist(iid, note="ekran goruntusu")
            kimlik = resolver.coz(sembol, r["name"], None)
            self.db.save_identity(iid, kimlik)
            (eklendi if kimlik.arastirilabilir else cozulemeyen).append(
                (r["name"], kimlik))

        L = [f"✅ <b>{len(eklendi) + len(cozulemeyen)} enstruman</b> izleme listesine eklendi.", ""]
        if eklendi:
            L.append(f"<b>Kimligi dogrulandi ({len(eklendi)})</b> — kaynak taramasina girecek:")
            for ad, k in eklendi[:20]:
                ref = f"CIK {k.cik}" if k.cik else (k.isin or "")
                L.append(f"  ✅ {_esc(ad)} <code>{_esc(k.sec_ticker or k.isin or '')}</code>")
        if cozulemeyen:
            L += ["", f"<b>Cozulemedi ({len(cozulemeyen)})</b> — taranmayacak:"]
            for ad, k in cozulemeyen[:10]:
                L.append(f"  ❓ {_esc(ad)}\n     <i>{_esc((k.note or '')[:90])}</i>")
            L.append("\n<i>Bunlar icin ticker'i elle verebilirsin: "
                     "<code>/kimlik ISIM = TICKER</code></i>")
        L.append("\nTaramayi baslatmak icin /rapor")
        self.tg.send_message("\n".join(L), chat_id=chat_id)

    def _onay_metni(self, p: dict, sahip: str | None = None) -> str:
        guven_ikon = {"yuksek": "🟢", "orta": "🟡", "dusuk": "🔴"}.get(p["guven"], "🟡")
        L = [f"<b>{p['hesap'].upper()}</b> — {len(p['pozisyonlar'])} pozisyon okundu "
             f"{guven_ikon} <i>guven: {p['guven']}</i>", ""]
        for r in p["pozisyonlar"]:
            parts = [f"<b>{_esc(r['symbol'])}</b>"]
            if r["quantity"] is not None:
                parts.append(f"{_qty(r['quantity'])} adet")
            if r["avg_cost"] is not None:
                parts.append(f"@ {_money(r['avg_cost'])}")
            if r["market_value"] is not None:
                parts.append(f"= {_money(r['market_value'])}")
            if r["pnl_pct"] is not None:
                parts.append(f"({r['pnl_pct']:+.2f}%)")
            L.append("• " + "  ".join(parts))

        ccy = p.get("para_birimi") or ""
        projeksiyon = self._projeksiyon(p, sahip)
        if p.get("okunan_toplam") is not None:
            L += ["", f"Bu gorselde okunan: <b>{_money(p['okunan_toplam'])}</b> {ccy}"]
        if projeksiyon is not None and projeksiyon != p.get("okunan_toplam"):
            L.append(f"Kaydedilince portfoy: <b>{_money(projeksiyon)}</b> {ccy}")
        if p.get("toplam_deger") is not None:
            L.append(f"Ekrandaki toplam: <b>{_money(p['toplam_deger'])}</b> {ccy}")

        L += _kapsam_uyarisi({**p, "okunan_toplam": projeksiyon})

        if p.get("eksik_satirlar"):
            L += ["", "⚠️ Sayilari okunamayan satir (ekran kesik): "
                  + ", ".join(f"<code>{_esc(s)}</code>" for s in p["eksik_satirlar"])
                  + "\n<i>Bunlar kaydedilmeyecek.</i>"]

        if p.get("celiskiler"):
            L += ["", f"🔶 <b>Iki okuma su alanlarda celisti</b> "
                  f"({p.get('gecis_sayisi', 2)} bagimsiz okuma):"]
            L += [f"  • {_esc(c)}" for c in p["celiskiler"][:10]]
            if len(p["celiskiler"]) > 10:
                L.append(f"  <i>… ve {len(p['celiskiler']) - 10} tane daha</i>")
            L.append("<i>Celisen alanlar bos kaydedilecek — dogru degeri "
                     "gormek icin o kismi yakinlastirip tekrar gonderebilirsin.</i>")
        if p.get("notlar"):
            L += ["", f"<i>Not: {_esc(p['notlar'])}</i>"]
        if p["guven"] == "dusuk":
            L += ["", "⚠️ <b>Okuma guveni dusuk</b> — rakamlari kontrol et."]
        L += ["", "Dogru mu?"]
        return "\n".join(L)

    # --- onay/iptal -----------------------------------------------------
    def _teknik_detay(self, cb: dict, chat_id, anahtar: str) -> None:
        """
        Saklanan hakem ciktisinin TEKNIK katmanini gonderir.

        SAHIP DOGRULANIR. Once sorgu yalnizca `run_ts` ile eslesiyordu ve
        iki sahibin damgasi ayni saniyeye duserse B, A'nin teknik
        detayini gorurdu — kullaniciya GORUNEN bir yuzeyde capraz
        sizinti. Artik buton SATIR ID'si tasiyor ve satirin sahibi,
        butona basan sohbetin sahibiyle karsilastiriliyor.

        Eski (damga tasiyan) butonlar da calisir: sayi degilse damga
        kabul edilir ama yine SAHIBE gore suzulur.

        Sayiyi gormek isteyen gorebilmeli — teknik katmani tamamen
        gizlemek guveni kaybettirir. Buton her zaman duruyor.
        """
        from ..notify.telegram import md_to_tg_html
        from ..pulse.agents import katmanlari_ayir

        sahip = self.s.sahip_bul(chat_id)
        if not sahip:
            # Bos sonuc DEGIL acik ret: sohbet bir kisiye bagli degilse
            # kimin detayinin gosterilecegi TANIMSIZ.
            self.tg.answer_callback_query(cb["id"], "bu sohbet bir kisiye bagli degil")
            return

        if str(anahtar).isdigit():
            r = self.db.query(
                "SELECT ham_metin FROM panel_runs WHERE id = ? AND sahip = ?",
                (int(anahtar), sahip))
        else:                                   # eski bicim: run_ts
            r = self.db.query(
                "SELECT ham_metin FROM panel_runs WHERE run_ts = ? "
                "AND ajan = 'hakem' AND sahip = ?", (anahtar, sahip))
        if not r or not r[0]["ham_metin"]:
            self.tg.answer_callback_query(cb["id"], "detay bulunamadi")
            return
        _, teknik = katmanlari_ayir(r[0]["ham_metin"])
        # JSON blogu kullaniciya gitmez — defter icin, insan icin degil.
        teknik = teknik.split("```json")[0].strip()
        self.tg.answer_callback_query(cb["id"], "gonderiliyor")
        self.tg.send_message("🔍 <b>Teknik detay</b>\n\n" + md_to_tg_html(teknik),
                             chat_id=chat_id)

    def _on_callback(self, cb: dict) -> None:
        chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
        if not self._authorised(chat_id):
            self.tg.answer_callback_query(cb["id"], "yetkisiz")
            return

        action, _, token = (cb.get("data") or "").partition(":")

        # TEKNIK DETAY: `pending/` dosyasi yok, saklanan HAM METIN var.
        # Yeniden URETILMIYOR — ikinci bir model cagrisi, gonderilen sey
        # ile olculen sey arasinda suruklenme kanali acardi.
        if action == "det":
            self._teknik_detay(cb, chat_id, token)
            return

        # REHBER: `pending/` dosyasi yok, statik konu metni. Onay
        # akisindaki token kontrolunden ONCE donmeli.
        if action == "reh":
            from . import yetenekler
            self.tg.answer_callback_query(cb["id"])
            if token not in yetenekler.KONULAR:
                self.tg.send_message(yetenekler.menu_metni(),
                                     reply_markup=yetenekler.menu_markup(),
                                     chat_id=chat_id)
                return
            self.tg.send_message(yetenekler.konu_metni(token),
                                 reply_markup=yetenekler.menu_markup(),
                                 chat_id=chat_id)
            return

        # Basis dinleyicide zaten teyit edildiyse balonu TEKRAR cagirma:
        # ikinci cagri "query is too old" doner ve hicbir sey eklemez.
        onaylandi = bool(cb.get("_basis_onaylandi"))

        def _balon(metin: str) -> None:
            if not onaylandi and cb.get("id"):
                self.tg.answer_callback_query(cb["id"], metin)

        if not token:
            _balon("gecersiz istek")
            return

        depo = self._depo()

        if action == "no":
            silinen = depo.sil(token)
            _balon("iptal edildi")
            self._gonder(
                "🗑 Iptal edildi, hicbir sey kaydedilmedi." if silinen
                else "🗑 Bu istek zaten kapanmisti — hicbir sey kaydedilmedi.",
                chat_id)
            return

        if action not in ("ok", "wl"):
            _balon("bilinmeyen islem")
            return

        # SAHIPLEN, SONRA CALIS. Once silip sonra calismak, is ortasinda
        # olen surecte istegi de goturuyordu (bkz. bot/onay.py).
        onay = depo.sahiplen(token)
        if onay is None:
            _balon("islenemedi")
            self._gonder(self._sahiplenilemedi_metni(depo.durum(token)), chat_id)
            return

        _balon("isleniyor…")
        self._onay_isle(onay, chat_id, action)

    _SAHIPLENME_METNI = {
        "isleniyor": "⏳ Bu istek SU AN isleniyor — bitince sonucu yazacagim. "
                     "Tekrar basmana gerek yok.",
        "hata": "⚠️ Bu istek daha once hata almisti; tekrar islenmedi.\n"
                "<i>Ekran goruntusunu yeniden gonderirsen temiz bir istek "
                "acarim.</i>",
        "yok": "ℹ️ Bu istek artik bekleyenler arasinda degil — islenmis, "
               "iptal edilmis ya da /unut ile temizlenmis olabilir.\n"
               "<i>Portfoyun guncel halini gormek icin /portfoy yaz.</i>",
    }

    def _sahiplenilemedi_metni(self, durum: str) -> str:
        """
        SESSIZ KALMA. Eski davranis yalnizca "bu istek artik gecerli degil"
        yazan bir balondu — ustelik gecikmis callback'te o balon da
        dusuyordu. Kullanici acisindan sonuc: bastim, hicbir sey olmadi.

        Uc durum uc ayri cumle: "isleniyor" beklemeyi, "hata" yeniden
        gondermeyi, "yok" ise durumu kontrol etmeyi soyluyor.
        """
        return self._SAHIPLENME_METNI.get(durum, self._SAHIPLENME_METNI["yok"])

    def _onay_isle(self, onay, chat_id, action: str = "ok") -> None:
        """
        Sahiplenilmis bir istegi yurutur ve SONUCU MUTLAKA bildirir.

        Buton yolu ile dogal dil ("kaydet") yolu BURADA birlesiyor: iki
        tetikleyici, tek davranis. Ayri yazilsalardi biri duzeltilip
        digeri unutulurdu — bu dosyada tam olarak bu oldu (`/onayla`
        yolu, buton yolundaki sahip kontrolunu iki surum boyunca
        tasimadi).

        HATA YOLU DA BIR SONUCTUR: istek `.hata` olarak diskte kalir,
        kullaniciya sebep + TEKRAR DENE butonu gider. Boylece butonlarin
        basista kaldirilmis olmasi cikmaz sokak yaratmaz.
        """
        depo = self._depo()
        try:
            metin = self._onay_yurut(onay, chat_id, action)
        except Exception as e:                        # noqa: BLE001
            log.exception("onay islenemedi (token %s)", onay.token)
            depo.hataya_dus(onay, f"{type(e).__name__}: {e}")
            self._gonder(
                "❌ Bu istegi islerken hata aldim, <b>kayit tamamlanmadi</b>.\n"
                f"<code>{_esc(type(e).__name__)}: {_esc(str(e)[:200])}</code>\n\n"
                "<i>Istek duruyor — asagidaki butonla yeniden deneyebilirsin. "
                "Portfoyun su anki halini gormek icin /portfoy.</i>",
                chat_id, reply_markup=self._onay_markup(onay.token), kritik=True)
            return

        depo.tamamla(onay)
        if metin:
            self._gonder(metin, chat_id, kritik=True)

    def _onay_yurut(self, onay, chat_id, action: str) -> str | None:
        """
        Isin KENDISI. Kullaniciya gidecek metni doner.

        `None` = is kendi mesajlarini kendisi gonderdi (rapor, watchlist).
        Tek bir gonderim noktasi olmasinin sebebi garantili gonderimi
        (`_gonder`) TEK yerde tutmak; her dal kendi `send`ini cagirsaydi
        biri yine kontrolsuz kalirdi.
        """
        veri = onay.veri
        if action == "wl":
            self._watchlist_kaydet(veri, chat_id)
            return None

        # SAHIP ONAY DOSYASINDAN okunur, cagiran chat'ten degil: onayi
        # kim baslattiysa portfoy onundur.
        sahip = veri.get("_sahip") or self.s.sahip_bul(chat_id)
        if not sahip:
            return _SAHIPSIZ

        # ISLEM TIPINE GORE. Onay kapisi ORTAK; arkasindaki is farkli.
        tip = onay.tip
        if tip == "rapor":
            self._calistir_rapor(chat_id, topla=bool(veri.get("topla")))
            return None
        if tip == "sil_son":
            return self._sil_son(sahip)
        if tip == "hatirla":
            return self._hatirla_kaydet(veri, sahip)
        if veri.get("ekran_tipi") == "liste":
            self._watchlist_kaydet(veri, chat_id)
            return None

        # YAZILACAK BIR SEY VAR MI? Eksik alanla `_pozisyon_kaydet`e
        # girmek KeyError uretirdi; bu bir ariza degil, okunamamis bir
        # ekran — hata gibi degil, DURUM gibi anlatilmali.
        if not veri.get("pozisyonlar") or not veri.get("hesap"):
            return ("⚠️ Bu istekte kaydedilecek pozisyon yok "
                    "(hesap ya da satirlar okunamamis). Hicbir sey yazilmadi.\n"
                    "<i>Ekrani tekrar gonderirsen yeniden okurum.</i>")
        return self._pozisyon_kaydet(veri, sahip)

    # Kalici kayitlarin kullaniciya gorunen yuzu. Bir hafiza katmani,
    # icinde NE OLDUGU gorulemiyorsa denetlenemez — ve denetlenemeyen
    # hafiza, sessizce yanlis yonlendiren hafizadir.
    _HATIRLANAN_IKON = {"tercih": "⚙️", "olgu": "📌", "karar": "🎯"}

    def _hatirladiklarin_metni(self, chat_id, arg: str | None) -> str:
        sahip = self.s.sahip_bul(chat_id)
        if not sahip:
            return _SAHIPSIZ
        arg = (arg or "").strip()
        if arg:
            # `/hatirladiklarin unut 12`
            parca = arg.split()
            if parca[0].lower() not in ("unut", "sil"):
                return ("Kullanim: <code>/hatirladiklarin</code> ya da "
                        "<code>/hatirladiklarin unut &lt;no&gt;</code>")
            if len(parca) < 2 or not parca[1].isdigit():
                return ("Hangi kaydi unutayim? "
                        "<code>/hatirladiklarin unut 12</code>")
            no = int(parca[1])
            if self.db.unut_hatirlanan(sahip, no):
                return (f"🧠 Kayit <b>#{no}</b> gecersizlestirildi — artik "
                        "cevaplarima girmiyor.\n<i>Silinmedi; gecmis "
                        "denetlenebilir kalsin diye kayit duruyor.</i>")
            return (f"⚠️ <b>#{no}</b> diye gecerli bir kaydin yok. "
                    "<code>/hatirladiklarin</code> ile listeye bak.")

        kayitlar = self.db.hatirlananlar(sahip)
        if not kayitlar:
            return ("🧠 <b>Kalici olarak hatirladigim bir sey yok.</b>\n\n"
                    "<i>Kalici bir kural koyarsan ('bundan sonra hep su "
                    "fiyati kullan') ya da elindeki bir varligi "
                    "bildirirsen, onayina sunup hatirlarim.</i>")
        L = [f"🧠 <b>Kalici olarak hatirladiklarim</b> ({len(kayitlar)})", ""]
        for r in kayitlar:
            ikon = self._HATIRLANAN_IKON.get(r["tur"], "•")
            ne_zaman = (r["kaynak_ts"] or r["olusma_ts"] or "")[:10]
            L.append(f"{ikon} <b>#{r['id']}</b> {_esc(r['konu'])}")
            L.append(f"   {_esc(r['icerik'])}")
            # KAYNAK TARIHI HER SATIRDA: model bunu alintilarken tarih
            # verebilsin, kullanici da nereden geldigini gorebilsin.
            L.append(f"   <i>{ne_zaman}</i>")
        L.append("")
        L.append("<i>Unutturmak icin: /hatirladiklarin unut &lt;no&gt;</i>")
        return "\n".join(L)

    def _hatirla_kaydet(self, veri: dict, sahip: str) -> str:
        """
        Kalici gercegi yazar ve NE OLDUGUNU soyler.

        EZILEN KAYIT SESSIZ GECMEZ: ayni konuya yeni bir kural
        yazildiginda eskisi dusuyor ve kullanici bunu GORMELI — aksi
        halde "neden artik boyle davraniyor" sorusunun cevabi kaybolur.
        """
        try:
            sonuc = self.db.hatirla(
                sahip, veri.get("tur", ""), veri.get("konu", ""),
                veri.get("icerik", ""), kaynak_ts=veri.get("kaynak_ts"))
        except ValueError as e:
            # Gecersiz istek bir ARIZA degil, okunamamis bir niyet.
            return (f"⚠️ Hatirlanacak kayit gecersiz: {_esc(str(e))}\n"
                    "<i>Hicbir sey yazilmadi.</i>")
        L = [f"🧠 <b>Hatirladim</b> — {_esc(veri.get('konu', ''))}",
             f"<i>{_esc(veri.get('tur', ''))}</i>: {_esc(veri.get('icerik', ''))}",
             "", "<i>Bu, sohbet penceresi kapansa da kalir ve her "
             "cevabimda goz onunde olur.</i>"]
        if sonuc["gecersizlesen"]:
            L.insert(2, f"↩️ Ayni konudaki {len(sonuc['gecersizlesen'])} eski "
                        "kayit gecersizlestirildi (silinmedi).")
        L.append(f"<i>Kayit no {sonuc['id']} — /hatirladiklarin ile "
                 "gorebilir, oradan unutturabilirsin.</i>")
        return "\n".join(L)

    # ------------------------------------------------------------------
    @staticmethod
    def _ayni_miktarlar(a: dict, b: dict) -> bool:
        """Iki {sembol: adet} haritasi ayni portfoyu mu anlatiyor?"""
        if set(a) != set(b):
            return False
        for k, x in a.items():
            y = b[k]
            if x is None and y is None:
                continue                              # or. nakit satiri
            if x is None or y is None:
                return False
            if abs(float(x) - float(y)) > 1e-9:
                return False
        return True

    def _degisiklik_var_mi(self, account: str, snapshot: str,
                           rows: list[dict], sahip: str) -> bool:
        """
        Bu yazim depolanan durumu DEGISTIRIR mi?

        Ali arka arkaya ekran gonderebiliyor ve 20 dakikalik birlestirme
        penceresi disinda her gonderim, hicbir sey degismemis olsa bile
        YENI bir snapshot aciyordu — ayni portfoyun onlarca kopyasi.
        Soru "yeni satir mi geldi" degil, "SONUC farkli mi": hedef
        snapshot'a yazildiktan sonraki hal, bugunku halle ayni mi.

        KIYAS ADET UZERINDEN (bkz. `snapshot_quantities`). Fiyat oynadi
        diye yeni snapshot acmak, ekran goruntusune piyasa verisinin isini
        yaptirmak olurdu.

        SATIS YAKALANIR: kiyas KUME esitligi, alt kume degil. Bir kagit
        satilip ekran yeniden gonderildiginde semboller kumesi kuculur,
        esitlik bozulur ve yazim NORMAL yolundan gecer. Alt kume kabul
        edilseydi satilan kagit portfoyde sonsuza kadar asili kalirdi —
        ve ayni gevseklik, kaydirarak gonderilen ikinci ekrani da
        "degisiklik yok" sayardi.
        """
        son = self.db.latest_snapshot_ts(account, sahip)
        if not son:
            return True                               # ilk kayit
        mevcut = self.db.snapshot_quantities(account, son, sahip)
        yeni = {r["symbol"]: r.get("quantity") for r in rows}
        # Birlestirme penceresi icindeysek yazim MEVCUDUN USTUNE biner;
        # disindaysak yeni snapshot YALNIZCA gonderilenleri icerir.
        sonuc = {**mevcut, **yeni} if snapshot == son else yeni
        return not self._ayni_miktarlar(sonuc, mevcut)

    # `pozisyon_kaydet` araci her kaydi bu damgayla sunuyor. Ekran
    # goruntusu yolu (vision) BASKA bir sekil uretiyor (`ekran_tipi`),
    # yani iki kaynak ayirt edilebilir — ayrimin tasidigi anlam asagida.
    MODEL_KAYNAGI = "sohbet"

    def _eksiltmeyi_engelle(self, account: str, snapshot: str,
                            rows: list[dict], sahip: str,
                            parsed: dict) -> tuple[list[dict], list[str]]:
        """
        MODELIN DERLEDIGI yazim mevcut pozisyonlari DUSUREMEZ.

        OLCULEN VAKA (2026-08-20 14:03). Ali "Moderna hakkinda ne
        demistin, o fiyatlar hala gecerli mi?" diye sordu — saf bir
        OKUMA sorusu. Model cevabin sonunda `pozisyon_kaydet` cagirdi
        ve `bux` hesabi icin TEK satirlik (yalnizca MRNA) bir kayit
        onaya sundu. Onay kapisi tuttu, yazilmadi. Ama kopya
        veritabaninda denendi:

            onaydan ONCE   bux: 18 pozisyon · 5.929,81 EUR
            onaydan SONRA  bux:  1 pozisyon ·   148,90 EUR

        Cunku `portfoy` her hesabin EN SON anlik goruntusunu okuyor ve
        birlestirme penceresi (20 dk) disindaki yazim YENI bir goruntu
        aciyor — icinde yalnizca gonderilenler oluyor. BUX'un son
        goruntusu 14 Agustos'tan, yani bugun yapilan HER kismi yazim
        gorunumu degistirir.

        TEHLIKE MESRU YOLDA DA VAR: "Moderna aldim, ekle" demek de ayni
        sonucu verirdi. Yani sorun modelin fazla hevesli olmasi DEGIL,
        kismi bir listenin TAM GORUNUM sanilmasi.

        AYRIM KANITTA: ekran goruntusu hesabin tamamini gosterir, orada
        bir pozisyonun YOKLUGU kanittir (satis) — nitekim ROSE tam
        boyle kapatildi. Modelin derledigi liste ise hicbir seyin
        kaniti degildir; model yalnizca o an KONUSTUGU kagidi yazar.
        Bu yuzden model kaynakli yazim EKLER ve GUNCELLER, ama
        DUSURMEZ; dusurmek icin ekran goruntusu gerekir.

        Tarih BOZULMAZ: eski anlik goruntuye dokunulmuyor, tasinan
        satirlar YENI goruntuye yaziliyor.
        """
        if not str(parsed.get("kaynak") or "").startswith(self.MODEL_KAYNAGI):
            return rows, []                  # ekran goruntusu: TAM gorunum
        son = self.db.latest_snapshot_ts(account, sahip)
        if not son or son == snapshot:
            # Ilk kayit, ya da zaten mevcut goruntuye ekleniyoruz
            # (birlestirme penceresi) — ikisinde de dusen bir sey yok.
            return rows, []
        gelen = {r["symbol"] for r in rows}
        tasinan = [r for r in self.db.snapshot_satirlari(account, son, sahip)
                   if r["symbol"] not in gelen]
        if tasinan:
            log.info("[bot] %s/%s: model kaynakli yazim — %d mevcut pozisyon "
                     "tasindi (dusurulmedi)", account, sahip, len(tasinan))
        return tasinan + rows, [r["symbol"] for r in tasinan]

    # Adet DISI alanlar. Bunlarin degismesi YENI bir anlik goruntu ACMAZ —
    # fiyat oynadi diye tarih acmak, ekran goruntusune piyasa verisinin
    # isini yaptirmak olurdu (bkz. `snapshot_quantities`). Ama yazilmalari
    # gerekir; hangisinin neden onemli oldugu `_alanlari_tazele`de.
    _TAZELENEN_ALANLAR = ("avg_cost", "market_value",
                          "pnl_abs", "pnl_pct", "last_price")

    @staticmethod
    def _alan_farkli(yeni, eski) -> bool:
        """
        BOS GELEN ALAN SILME DEGILDIR. Ekranin tasimadigi bir alan
        (or. "Today" filtresindeki ekranda maliyet yoktur) `None`
        gelir; bunu fark saymak, bilinen bir maliyeti bir sonraki
        ekran goruntusuyle SIFIRLARDI.
        """
        if yeni is None:
            return False
        if eski is None:
            return True
        try:
            return abs(float(yeni) - float(eski)) > 1e-9
        except (TypeError, ValueError):
            return yeni != eski

    def _alanlari_tazele(self, account: str, snapshot: str,
                         rows: list[dict], sahip: str) -> str:
        """
        ADETLER AYNI — ama MALIYET/DEGER yeni gelmis olabilir.

        OLCULEN VAKA (2026-08-20, 16:21 -> 17:36). Ali BUX ekranini
        "All time + EUR" filtresine alip bes kez gonderdi; amaci ADET
        degil MALIYET yazdirmakti. Adetler her seferinde ayni oldugu
        icin `_degisiklik_var_mi` hepsini "degisiklik yok" sayip
        dusurdu — ARKA ARKAYA YEDI ONAYLANMIS YAZIM cope gitti:

            defterde kalan   19 satir, 18'inde `avg_cost` BOS
            ASML degeri      2.424,20 EUR  (14 Agustos rakami)
            ekranda yazan    2.317,08 EUR

        Kullanici butona basmisti; sistem sessizce reddediyordu ve
        ustelik "deger ve K/Z zaten piyasa verisinden guncelleniyor"
        diyordu. O beyan YANLISTI: `portfoy` degeri dogrudan bu
        tablodan okuyor, hicbir yerde fiyattan yeniden hesaplamiyor.

        AYRIM ADETTE: adet degistiyse portfoyun BILESIMI degismistir,
        yeni bir anlik goruntu acilir ve gecmis korunur. Adet ayniysa
        ayni portfoyun DAHA IYI okunmus halidir — yeni tarih acmak
        gecmisi ayni gunun kopyalariyla doldururdu; yerine mevcut
        goruntu guncellenir.

        ALAN BAZINDA BIRLESTIRME: yalnizca DOLU gelen alan yazilir
        (bkz. `_alan_farkli`). Maliyeti gosteren ekran maliyeti
        tazeler, gostermeyen ekran ona dokunmaz.
        """
        son = self.db.latest_snapshot_ts(account, sahip) or snapshot
        if son != snapshot:
            # Hedef degisti: hizalamayi da HEDEFE gore yeniden yap, yoksa
            # "ING/INGA" gibi ayrisan sembol mevcut satirla eslesmez ve
            # tazeleme sessizce bos doner.
            rows, _ = self._hizala_semboller(account, son, rows, sahip)
        mevcut = {r["symbol"]: r
                  for r in self.db.snapshot_satirlari(account, son, sahip)}

        yazilacak, degisen = [], []
        for r in rows:
            eski = mevcut.get(r["symbol"])
            if eski is None:
                continue                      # adet kiyasi bunu zaten eledi
            farkli = [a for a in self._TAZELENEN_ALANLAR
                      if self._alan_farkli(r.get(a), eski.get(a))]
            if not farkli:
                continue
            birlesik = dict(eski)
            for a in farkli:
                birlesik[a] = r[a]
            yazilacak.append(birlesik)
            degisen.append((r["symbol"], farkli))

        if not yazilacak:
            log.info("[bot] %s/%s: adet ve alanlar ayni, yazilmadi (%d satir)",
                     account, sahip, len(rows))
            return "\n".join([
                f"ℹ️ <b>{account.upper()}</b> — degisiklik yok, "
                "yeni kayit acilmadi.",
                f"Ekrandaki adetler VE maliyet/deger alanlari en son "
                f"kayitla birebir ayni ({len(rows)} pozisyon).",
            ])

        self.db.insert_positions(account, son, yazilacak, sahip)
        maliyetli = [s for s, f in degisen if "avg_cost" in f]
        log.info("[bot] %s/%s: adet ayni, %d satirin alanlari tazelendi "
                 "(%s) — snapshot %s", account, sahip, len(yazilacak),
                 f"{len(maliyetli)} maliyet", son)

        L = [f"🔄 <b>{account.upper()}</b> — adetler ayni, "
             f"<b>{len(yazilacak)}</b> pozisyonun bilgileri guncellendi.",
             f"<code>{son[:19]}</code> (yeni kayit acilmadi)"]
        if maliyetli:
            L.append(f"\n💰 Maliyet yazildi: <b>{len(maliyetli)}</b> kagitta — "
                     + ", ".join(f"<code>{_esc(s)}</code>"
                                 for s in maliyetli[:8])
                     + (" …" if len(maliyetli) > 8 else ""))
            L.append("<i>Artik K/Z donmus bir yuzdeden degil GERCEK "
                     "girisinden hesaplaniyor.</i>")
        kayitli = self.db.snapshot_value(account, son, sahip)
        if kayitli:
            L.append(f"\nPortfoyde toplam: <b>{_money(kayitli)}</b> "
                     f"{rows[0].get('currency') or ''}")
        return "\n".join(L)

    def _pozisyon_kaydet(self, parsed: dict, sahip: str) -> str:
        account = parsed["hesap"]
        snapshot = self._snapshot_ts(account, sahip)
        rows, duzeltmeler = self._hizala_semboller(account, snapshot,
                                                   parsed["pozisyonlar"], sahip)
        rows, tasinan = self._eksiltmeyi_engelle(account, snapshot, rows,
                                                 sahip, parsed)

        # SESSIZ ATLAMA YOK: kullanici "kaydet" dedi, ne olduğunu gormeli.
        # Adet degismediyse is BITMEZ: maliyet/deger yeni gelmis olabilir
        # ve o YERINDE yazilir (bkz. `_alanlari_tazele`).
        if not self._degisiklik_var_mi(account, snapshot, rows, sahip):
            return self._alanlari_tazele(account, snapshot, rows, sahip)

        n = self.db.insert_positions(account, snapshot, rows, sahip)

        L = [f"✅ <b>{account.upper()}</b> — {n} pozisyon kaydedildi.",
             f"<code>{snapshot[:19]}</code>"]
        if duzeltmeler:
            L.append("\n🔗 Ayni sirket olarak eslestirildi: "
                     + ", ".join(f"<code>{_esc(d)}</code>" for d in duzeltmeler))
        if tasinan:
            # SESSIZ TASIMA OLMAZ: kullanici neyi onayladigini gormeli.
            # "1 pozisyon kaydedildi" yazip sessizce 17 satir tasimak,
            # dogru sonucu yanlis bir beyanla vermek olurdu.
            L.append(f"\n📌 Mevcut <b>{len(tasinan)}</b> pozisyon korundu "
                     f"(model kaydi EKLER, dusurmez): "
                     + ", ".join(f"<code>{_esc(s)}</code>" for s in tasinan[:8])
                     + (" …" if len(tasinan) > 8 else ""))
            L.append("<i>Bir kagidi portfoyden CIKARMAK icin ekran "
                     "goruntusu gonder — silme ancak kanitla olur.</i>")

        # Kapsami TUM snapshot uzerinden yeniden olc: kullanici ikinci/ucuncu
        # gorseli gonderdikce eksik oran dusmeli, uyari kendiliginden susmali.
        kayitli = self.db.snapshot_value(account, snapshot, sahip)
        beklenen = parsed.get("toplam_deger")
        ccy = parsed.get("para_birimi") or ""
        if kayitli:
            L.append(f"\nPortfoyde toplam: <b>{_money(kayitli)}</b> {ccy}")
        L += self._kapsam_satirlari(kayitli, beklenen, ccy)
        return "\n".join(L)

    @staticmethod
    def _kapsam_satirlari(kayitli, beklenen, ccy: str) -> list[str]:
        """
        Kapsamin UC durumu, ucu de SOYLENIYOR.

        Once yalnizca ikisi vardi: "eksik var" ve sessizlik. Ucuncu
        durum — ekrandaki TOPLAM okunamamis, yani kapsam OLCULEMIYOR —
        sessiz dala dusuyordu ve kullanici "eksik uyarisi gelmedi,
        demek ki tam" diye okuyordu. Diskteki bekleyen okumalarin
        ucunde `toplam_deger` bos: nadir bir kose degil.

        "Olcemedim" ile "tam" ayni cumleyle anlatilamaz — bu, projenin
        tekrar eden kusur sinifinin ta kendisi (bkz. yanlis "yok"
        beyani).
        """
        oran = _kapsam(kayitli, beklenen)
        if oran is None:
            return ["\n⚠️ <b>Kapsam dogrulanamadi</b> — ekranda yazan toplam "
                    "degeri okuyamadim, dolayisiyla eksik pozisyon olup "
                    "olmadigini SOYLEYEMEM.",
                    "<i>Toplamin gorundugu ekrani da gonderirsen dogrularim. "
                    "Yanlissa /sil ile geri alabilirsin.</i>"]
        if oran < KAPSAM_ESIGI:
            return [f"\n🔻 Ekranda yazan toplam {_money(beklenen)} {ccy} — "
                    f"hala <b>{_money(beklenen - kayitli)}</b> {ccy} eksik.",
                    "<i>Kaydirip devamini gonder.</i>"]
        return ["\n✓ Kapsam tam (ekrandaki toplamla ortusuyor).",
                "Yanlissa /sil ile geri alabilirsin. Analiz icin /rapor."]

    # ------------------------------------------------------------------
    # DOGAL DILDE ONAY — "kaydet"
    # ------------------------------------------------------------------
    # ACIK BIR KAYIT/ONAY FIILI SART. "evet", "tamam", "olur" BILEREK
    # DISARIDA: model bir tur once "Moderna'yi da inceleyeyim mi?" diye
    # sormus olabilir ve oraya gelen "evet" PORTFOYE YAZMAK anlamina
    # gelmez. Onay kelimesi, neyi onayladigini KENDI BASINA tasimali.
    _ONAY_KOKLERI = ("kaydet", "kayded", "kaydi", "kayit et", "kayit yap",
                     "onayla", "onayli", "onaylad")
    _ONAY_AZAMI_KELIME = 5

    # TURKCE SORU EKI AYRI KELIMEDIR ve soru isareti olmadan da yazilir.
    # "altin hesabimi portfoyume kaydeder misin" bes kelime, `?` yok ve
    # "kayded" kokunu tasiyor — yani diger uc kapiyi da geciyordu. Ama bu
    # bir ISTEK, onay degil: bekleyen bir seyi onaylamiyor, YENI bir is
    # istiyor. Testte yakalandi.
    _SORU_EKLERI = frozenset({
        "mi", "mu", "misin", "musun", "misiniz", "musunuz",
        "miyim", "muyum", "miyiz", "muyuz", "midir", "mudur"})

    @classmethod
    def _onay_niyeti(cls, text: str) -> bool:
        """
        Metin, bekleyen bir istegin ONAYI mi?

        Uc kapi birden gecilmeli:
          * SORU DEGIL — "kaydettin mi?" bir onay degil, bir soru.
          * KISA — uzun cumlede "kaydet" bir kosula bagli olabilir
            ("once fiyata bak, sonra kaydet"); orayi model cozmeli.
          * ACIK FIIL — kok listesi yukarida, sebebiyle birlikte.
        """
        ham = (text or "").strip()
        if not ham or "?" in ham:
            return False
        sade = ham.casefold()
        for a, b in (("ı", "i"), ("ğ", "g"), ("ü", "u"), ("ş", "s"),
                     ("ö", "o"), ("ç", "c"), ("İ".casefold(), "i")):
            sade = sade.replace(a, b)
        kelimeler = [k for k in re.split(r"[^\w]+", sade) if k]
        if not kelimeler or len(kelimeler) > cls._ONAY_AZAMI_KELIME:
            return False
        if any(k in cls._SORU_EKLERI for k in kelimeler):
            return False
        return any(k.startswith(kok.replace(" ", ""))
                   for k in kelimeler for kok in cls._ONAY_KOKLERI)

    def _onay_ozeti(self, onay) -> str:
        """Tek satirlik 'ne yazilacak' ozeti — YAS DAHIL."""
        v = onay.veri
        yas = self._yas_metni(onay.yas_sn)
        if v.get("ekran_tipi") == "liste":
            return (f"📋 Izleme listesi — {len(v.get('liste') or [])} enstruman "
                    f"<i>({yas})</i>")
        if onay.tip == "rapor":
            return f"▶️ Rapor calistirma <i>({yas})</i>"
        if onay.tip == "sil_son":
            return f"🗑 Son kaydi geri alma <i>({yas})</i>"
        hesap = (v.get("hesap") or "?").upper()
        poz = v.get("pozisyonlar") or []
        parca = f"💼 <b>{_esc(hesap)}</b> — {len(poz)} pozisyon"
        if v.get("toplam_deger"):
            parca += f", {_money(v['toplam_deger'])} {_esc(v.get('para_birimi') or '')}"
        return f"{parca} <i>({yas})</i>"

    def _dogal_onay(self, text: str, chat_id) -> bool:
        """
        "kaydet" -> bekleyen istegi isler. Isledi/cevapladi ise True.

        UC SART, ucu de bilerek:

        1. TOKEN'E BAGLANIR, KELIMEYE DEGIL. Yazi bir NIYET; islenen sey
           diskteki somut istektir. Belirsizse hicbir sey yazilmaz.

        2. YAZMADAN ONCE NE YAZILACAGI EKRANDA OLUR. Tek ve TAZE (15 dk)
           bir istek varsa "kaydet" ona baglanir ve ozeti sonuc mesajinda
           yazilir. Aday birden fazlaysa ya da biri bayatsa yazim
           YAPILMAZ; adaylar ozetleriyle ve butonlariyla YENIDEN
           GOSTERILIR. Kullanicinin kafasindaki "kaydet" ile sistemin
           yazacagi sey ayrisabiliyorsa, karar kullanicinindir.

        3. KAPSAM KONTROLU ATLANMAZ. Yazim `_onay_isle` ->
           `_pozisyon_kaydet` yolundan gecer; buton yoluyla AYNI kod.
           Ikinci bir "hizli yol" acilsaydi kapsam uyarisi orada
           unutulurdu.

        Bekleyen hicbir istek yoksa False doner ve cumle MODELE gider —
        "kaydet" o baglamda baska bir sey isteyebilir.
        """
        if not self._onay_niyeti(text):
            return False

        from .onay import OMUR

        # YIKICI TIPLER DISARIDA. `sil_son` ve `rapor` kendi butonlarinda
        # kendi etiketleriyle duruyor ("🗑 Evet, geri al", "▶️ Baslat");
        # "kaydet" kelimesiyle bir SILME islemini onaylatmak, `/onayla`
        # icin daha once kapatilan delignin dogal dildeki ikizi olurdu.
        onaylar = self._bekleyen_onaylar(chat_id, azami_yas=OMUR)
        eskiler = len(self._bekleyen_onaylar(chat_id)) - len(onaylar)
        if not onaylar:
            if eskiler:
                self._gonder(
                    f"⏰ Bekleyen <b>{eskiler}</b> istek var ama hepsi 24 "
                    "saatten eski; tek kelimeyle islemiyorum.\n"
                    "<code>/bekleyen</code> ile bak, hatirlamiyorsan "
                    "<code>/unut</code> ile temizle.", chat_id)
                return True
            return False              # bekleyen yok -> modele dussun

        if any(not o.taze_mi for o in onaylar):
            return self._onay_adaylarini_goster(onaylar, chat_id, eskiler)

        # TEK YA DA COK, HEPSI TAZE. Arka arkaya gonderilen 3 ekranin
        # ardindan gelen "kaydet" ucunu birden kasteder; hepsi son 15
        # dakikada geldiyse bunda belirsizlik yok.
        L = [f"✍️ <b>{len(onaylar)}</b> bekleyen istek kaydediliyor:"]
        L += [f"  {self._onay_ozeti(o)}" for o in onaylar]
        self._gonder("\n".join(L), chat_id)
        for o in onaylar:
            sahiplenilen = self._depo().sahiplen(o.token)
            if sahiplenilen is None:
                continue                  # arada butonuna basilmis
            self._onay_isle(sahiplenilen, chat_id)
        return True

    def _onay_adaylarini_goster(self, onaylar, chat_id, eskiler: int) -> bool:
        """
        BELIRSIZSE YAZMA, GOSTER.

        Bayat bir istek varken "kaydet" hangisini kastediyor bilinmiyor.
        Tahmin etmenin bedeli, hatirlanmayan bir ekranin portfoye
        yazilmasi — ve bu sessiz olur, cunku kullanici zaten "kaydet"
        dedigi icin gelen onayi BEKLIYOR.
        """
        self._gonder(
            f"🤔 <b>{len(onaylar)}</b> bekleyen istek var ve en az biri "
            "bayat — hangisini kastettigini varsaymiyorum.\n"
            "<i>Asagidakilerden birine bas:</i>"
            + (f"\n⏰ Ayrica {eskiler} istek 24 saatten eski, listeye "
               "alinmadi." if eskiler else ""), chat_id)
        for o in onaylar[:5]:
            self._gonder(self._onay_ozeti(o), chat_id,
                         reply_markup=self._onay_markup(o.token))
        if len(onaylar) > 5:
            self._gonder(f"<i>… ve {len(onaylar) - 5} istek daha "
                         "(<code>/bekleyen</code>).</i>", chat_id)
        return True

    # `pending/` artik TEK TIP tasimiyor: pozisyon okumalari, watchlist
    # eklemeleri, rapor baslatma ve "son kaydi geri al" ayni dizinde.
    # Toplu onay YALNIZCA pozisyon okumalarini almali — `/onayla` bir
    # silme islemini de onaylasaydi, kullanici "okumalarimi kaydet"
    # derken KAYIT SILERDI.
    OKUMA_TIPLERI = ("pozisyon",)

    def _bekleyen_onaylar(self, chat_id, tipler=OKUMA_TIPLERI,
                          azami_yas=None) -> list:
        """
        YALNIZCA bu sohbete ait bekleyen onaylar (`Onay` nesneleri).

        `tipler=None` verilirse TUMU (or. `/unut` hepsini iptal eder).
        `azami_yas` verilirse daha eskiler DISARIDA kalir — otomatik
        yollarin (toplu onay, dogal dil) suresi dolmus bir istegi
        islememesi icin.

        Dosyalar tek dizinde duruyor; suzmezsek A'nin bekleyen okumasi
        B'nin `/onayla` komutuyla A'nin portfoyune yazilirdi. Butonlu
        akis zaten guvenli (buton A'nin mesajinda), tehlike toplu
        komutta.
        """
        return self._depo().bekleyenler(
            chat_id=chat_id, tipler=tipler, azami_yas=azami_yas,
            tek_sahipli=len(self.s.sahip_listesi) <= 1)

    def _bekleyenler(self, chat_id, tipler=OKUMA_TIPLERI) -> list:
        """Geriye donuk yol: dosya yollari. Yeni kod `_bekleyen_onaylar`i kullanir."""
        return [o.yol for o in self._bekleyen_onaylar(chat_id, tipler)]

    @staticmethod
    def _yas_metni(saniye: float) -> str:
        from .onay import yas_metni
        return yas_metni(saniye)

    def _bekleyen_text(self, chat_id) -> str:
        """
        Bekleyen onaylar + EN ESKISININ YASI.

        Yas gosterilmesinin sebebi Telegram'in "Clear Messages"i: ekran
        temizlense de `pending/` dosyalari kalir. Kullanici HATIRLAMADIGI
        bir ekran goruntusunu onaylamadan once ne kadar beklemis
        oldugunu gormeli.
        """
        onaylar = self._bekleyen_onaylar(chat_id)
        if not onaylar:
            return "Bekleyen okuma yok."

        L = [f"Bekleyen okuma: <b>{len(onaylar)}</b>"]
        L.append(f"En eskisi: <i>{self._yas_metni(onaylar[0].yas_sn)}</i>")

        # SURESI DOLANLARI AYRI SAY. Bunlar `/onayla` ve "kaydet" ile
        # ISLENMEZ; sayilari gosterilmezse kullanici "5 bekliyor" okuyup
        # `/onayla` yazar ve 2'sinin islenmedigini fark etmez.
        dolan = [o for o in onaylar if o.suresi_doldu_mu]
        if dolan:
            L.append(f"\n⏰ <b>{len(dolan)}</b> tanesi 24 saatten eski — toplu "
                     "onaya GIRMEZ.\nHatirlamiyorsan <code>/unut</code> ile "
                     "temizle; hatirliyorsan o mesajdaki butona bas.")
        elif onaylar[0].yas_sn > 6 * 3600:
            L.append("⚠️ Eski bir okuma — hatirlamiyorsan /unut ile iptal et.")

        asili = self._depo().asili_isler()
        if asili:
            # `.isleniyor` dosyasi: sahiplenilmis ama bitmemis is. Uzun
            # sureli asili kalmasi surecin oldugu anlamina gelir ve
            # SOYLENMELI — yoksa "kaydettim mi acaba" belirsizligi kalir.
            L.append(f"\n🔧 <b>{len(asili)}</b> istek yarim kalmis gorunuyor "
                     "(surec kesilmis olabilir). Portfoyun guncel halini "
                     "/portfoy ile dogrula.")

        if len(onaylar) > len(dolan):
            L.append(f"\n/onayla ile {len(onaylar) - len(dolan)} tanesini kaydet.")
        return "\n".join(L)

    def _hepsini_onayla(self, chat_id) -> None:
        """
        Bekleyen TUM okumalari tek komutla kaydeder.

        Arka arkaya 10 ekran goruntusu gonderirken her biri icin ayri butona
        basmak gereksiz surtunme yaratiyor. Onay yine de aliniyor — sadece
        toplu.

        SURESI DOLANLAR DISARIDA (24 sa). Toplu onay, kullanicinin
        BAKMADIGI bir listeyi tek kelimeyle isleyen yol; oraya iki gun
        onceki bir okumanin girmesi, hatirlanmayan bir ekrani portfoye
        yazmak demek. Butonu duruyor — o mesaja bakan biri hala
        onaylayabilir.
        """
        from .onay import OMUR

        onaylar = self._bekleyen_onaylar(chat_id, azami_yas=OMUR)
        atlanan = len(self._bekleyen_onaylar(chat_id)) - len(onaylar)
        if not onaylar:
            self._gonder(
                "Bekleyen okuma yok." if not atlanan else
                f"Islenebilir bekleyen okuma yok.\n⏰ <b>{atlanan}</b> tanesi "
                "24 saatten eski, toplu onaya girmiyor — <code>/bekleyen</code> "
                "ile bak, <code>/unut</code> ile temizle.", chat_id)
            return

        yas = f" (en eskisi {self._yas_metni(onaylar[0].yas_sn)})"
        atlandi = (f"\n⏰ {atlanan} eski okuma ATLANDI (24 sa+)."
                   if atlanan else "")
        self._gonder(
            f"⏳ {len(onaylar)} bekleyen okuma kaydediliyor{yas}…{atlandi}",
            chat_id)

        liste_toplami: list[dict] = []
        for o in onaylar:
            # HER BIRI AYRI SAHIPLENILIR: aradan biri hata alsa da
            # kalanlar islenir ve hata alan `.hata` olarak diskte kalir.
            sahiplenilen = self._depo().sahiplen(o.token)
            if sahiplenilen is None:
                continue                      # arada butonuna basilmis
            p = sahiplenilen.veri
            if p.get("ekran_tipi") == "liste":
                liste_toplami += p.get("liste") or []
                self._depo().tamamla(sahiplenilen)
            else:
                self._onay_isle(sahiplenilen, chat_id)

        if liste_toplami:
            # Ayni enstruman birden fazla ekranda gorunebilir — ada gore tekille.
            gorulen, tekil = set(), []
            for r in liste_toplami:
                anahtar = _ad_anahtari(r.get("name"))
                if anahtar and anahtar not in gorulen:
                    gorulen.add(anahtar)
                    tekil.append(r)
            self._watchlist_kaydet({"liste": tekil}, chat_id)

    def _merge_target(self, account: str, sahip: str) -> str | None:
        """Birlestirme penceresi icindeki mevcut snapshot; yoksa None."""
        last = self.db.latest_snapshot_ts(account, sahip)
        if not last:
            return None
        try:
            prev = datetime.fromisoformat(last)
        except ValueError:
            return None
        if prev.tzinfo is None:
            prev = prev.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return last if now - prev < SNAPSHOT_MERGE_WINDOW else None

    def _snapshot_ts(self, account: str, sahip: str) -> str:
        """
        Yakin zamanli bir anlik goruntu varsa ONA ekle, yoksa yenisini ac.
        Cok ekranli portfoyun tek snapshot'ta toplanmasini saglar.
        """
        hedef = self._merge_target(account, sahip)
        if hedef:
            log.info("[bot] %s: mevcut snapshot'a ekleniyor (%s)", account, hedef)
            return hedef
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _hizala_semboller(self, account: str, snapshot: str,
                          rows: list[dict],
                          sahip: str) -> tuple[list[dict], list[str]]:
        """
        Ayni sirketi ayni sembolle kaydet.

        Ticker'lar ekranda yazmadigi icin model isimden turetiyor ve bu
        turetme okumalar arasinda oynayabiliyor: ayni ING satiri bir
        goruntude "INGA", digerinde "ING" cikti. Ikisi de yazilirsa ayni
        pozisyon iki kez sayilir ve portfoy toplami sisiyor.
        Mevcut snapshot'ta AYNI ISIMDE bir kayit varsa onun sembolu esas alinir.
        """
        mevcut = self.db.snapshot_symbol_names(account, snapshot, sahip)
        if not mevcut:
            return rows, []

        ad2sym = {_ad_anahtari(ad): sym for sym, ad in mevcut.items() if _ad_anahtari(ad)}
        duzeltmeler = []
        for r in rows:
            if r["symbol"] in mevcut:
                continue
            anahtar = _ad_anahtari(r.get("name"))
            hedef = ad2sym.get(anahtar)
            if hedef and hedef != r["symbol"]:
                duzeltmeler.append(f"{r['symbol']} → {hedef}")
                log.info("[bot] sembol hizalandi: %s -> %s (%s)",
                         r["symbol"], hedef, r.get("name"))
                r["symbol"] = hedef
        return rows, duzeltmeler

    def _projeksiyon(self, p: dict, sahip: str | None) -> float | None:
        """
        Bu gorsel kaydedilirse portfoy toplami ne olur?

        Ikinci/ucuncu ekran goruntusunde yalnizca o gorseldeki tutari
        ekrandaki TOPLAM ile karsilastirmak yaniltici: "%60 eksik" der,
        oysa ilk gorselin verisi zaten kayitli. Onceki kayitlarin ustune
        bu gorseli bindirip gercek kapsami olcuyoruz.
        """
        hesap = p.get("hesap")
        if not hesap:
            return p.get("okunan_toplam")

        # Sahip yoksa PROJEKSIYON YAPILMAZ: baskasinin kayitli
        # pozisyonlarini bu goruntunun uzerine bindirmek yanlis bir
        # kapsam orani uretir.
        if not sahip:
            return p.get("okunan_toplam")
        hedef = self._merge_target(hesap, sahip)
        mevcut = self.db.snapshot_positions(hesap, hedef, sahip) if hedef else {}

        yeni = {r["symbol"]: r["market_value"] for r in p["pozisyonlar"]}
        birlesik = {**mevcut, **yeni}       # ayni sembol -> yeni okuma kazanir
        toplam = sum(v for v in birlesik.values() if v is not None)
        return toplam or None

    # --- rapor ----------------------------------------------------------
    def _calistir_rapor(self, chat_id, topla: bool) -> None:
        self.tg.send_message(
            "⏳ " + ("Veri topluyorum, analiz ediyorum… (~3 dk)" if topla
                    else "Mevcut veriden ozet cikariyorum…"),
            chat_id=chat_id)
        try:
            from .. import pipeline
            pipeline.run_daily(self.s, self.db, skip_collect=not topla,
                               use_llm=True, notify=True)
        except Exception as e:                        # noqa: BLE001
            log.exception("rapor uretilemedi")
            self.tg.send_message(f"❌ Rapor uretilemedi: {type(e).__name__}: {e}",
                                 chat_id=chat_id)

    # --- bilgi komutlari -------------------------------------------------
    def _durum_text(self, sahip: str | None = None) -> str:
        L = ["<b>Veritabani</b>", ""]
        # PIYASA SAYILARI ORTAK, POZISYON SAYISI KISISEL.
        for table, etiket in (("instruments", "enstruman"), ("prices", "fiyat"),
                              ("disclosures", "KAP"), ("news", "haber")):
            n = self.db.query(f"SELECT COUNT(*) c FROM {table}")[0]["c"]
            L.append(f"  {etiket:12} <code>{n:>7}</code>")
        if sahip:
            n = self.db.query("SELECT COUNT(*) c FROM positions WHERE sahip=?",
                              (sahip,))[0]["c"]
            L.append(f"  {'pozisyon':12} <code>{n:>7}</code>  <i>({sahip})</i>")

        from ..llm import api_saglik
        saglikli, aciklama = api_saglik(self.s)
        L += ["", ("✅ <b>LLM erisimi</b> calisiyor" if saglikli
                   else f"❌ <b>LLM erisimi YOK</b>\n<i>{_esc(aciklama)}</i>")]

        runs = self.db.query("""SELECT collector, status, rows_written, run_ts
                                FROM collector_runs ORDER BY id DESC LIMIT 5""")
        if runs:
            L += ["", "<b>Son toplamalar</b>", ""]
            ikon = {"ok": "✅", "partial": "🟡", "skipped": "⬜", "error": "❌"}
            for r in runs:
                L.append(f"  {ikon.get(r['status'], '?')} {r['collector']:10} "
                         f"{r['rows_written']:>5}  <i>{r['run_ts'][:16]}</i>")
        return "\n".join(L)

    def _portfoy_text(self, sahip: str) -> str:
        L = []
        for acct in ("bux", "midas", "binance"):
            rows = self.db.latest_positions(acct, sahip)
            if not rows:
                continue
            ts = rows[0]["snapshot_ts"][:16]
            L += [f"<b>{acct.upper()}</b> <i>({ts})</i>", ""]
            toplam = 0.0
            for r in rows:
                line = f"• <b>{_esc(r['symbol'])}</b>"
                if r["quantity"] is not None:
                    line += f"  {_qty(r['quantity'])} adet"
                if r["market_value"] is not None:
                    line += f"  = {_money(r['market_value'])}"
                    toplam += r["market_value"]
                if r["pnl_pct"] is not None:
                    line += f"  ({r['pnl_pct']:+.2f}%)"
                L.append(line)
            L += ["", f"<b>Toplam:</b> {_money(toplam)} {rows[0]['currency'] or ''}", ""]

        if not L:
            return ("Henuz kayitli pozisyon yok.\n\n"
                    "BUX veya Midas uygulamasindan portfoy ekraninin goruntusunu gonder.")
        return "\n".join(L)

    def _evren_text(self, arg: str) -> str:
        toplam = self.db.count_instruments("BUX")
        if not toplam:
            return ("BUX katalogu henuz cekilmedi.\n"
                    "Terminalde: <code>python run.py collect --site bux indices</code>")

        endeksler = {r["index_name"].casefold(): r["index_name"]
                     for r in self.db.index_summary()}

        # "/evren AEX bank" -> once endeks adini ayikla
        endeks, terim = None, arg.strip()
        for uzunluk in (3, 2, 1):
            parcalar = terim.split()
            if len(parcalar) >= uzunluk:
                aday = " ".join(parcalar[:uzunluk]).casefold()
                if aday in endeksler:
                    endeks = endeksler[aday]
                    terim = " ".join(parcalar[uzunluk:])
                    break

        if not arg:
            L = [f"<b>BUX katalogu</b> — {toplam} enstruman", ""]
            for r in self.db.index_summary():
                L.append(f"  • <b>{_esc(r['index_name'])}</b> {r['n']}")
            etf = self.db.query(
                "SELECT COUNT(*) n FROM instruments WHERE venue='BUX' AND asset_type='etf'")
            L.append(f"  • <b>ETF</b> {etf[0]['n']}")
            L += ["", "<i>Arama:</i>",
                  "  <code>/evren AEX</code> — endeksin tamami",
                  "  <code>/evren dividend</code> — isimde ara",
                  "  <code>/evren CAC 40 bank</code> — endeks + kelime"]
            return "\n".join(L)

        rows = self.db.search_catalog(terim, endeks, limit=30)
        if not rows:
            return (f"Sonuc yok. <i>{_esc(endeks or '')} {_esc(terim)}</i>\n"
                    f"Katalogda {toplam} enstruman var — /evren ile endeksleri gor.")

        baslik = "<b>" + " · ".join(x for x in (endeks, f"'{terim}'" if terim else None) if x) + "</b>"
        L = [baslik or "<b>Katalog</b>", ""]
        for r in rows:
            tur = "📊" if r["asset_type"] == "etf" else "•"
            L.append(f"{tur} {_esc(r['name'] or r['symbol'])}  <code>{_esc(r['symbol'])}</code>")
        if len(rows) == 30:
            L += ["", "<i>ilk 30 gosteriliyor — aramayi daraltabilirsin</i>"]
        L += ["", "<i>Arastirmaya almak icin: <code>/aday SEMBOL</code></i>"]
        return "\n".join(L)

    def _aday_ekle(self, arg: str) -> str:
        """Katalogdaki bir enstrumani arastirma hedefine tasir."""
        if not arg:
            return ("Kullanim: <code>/aday ASML.AS</code>\n"
                    "Once <code>/evren</code> ile katalogda bul.")

        from ..research import IdentityResolver
        eklendi, bulunamadi = [], []
        for sembol in arg.replace(",", " ").split():
            r = self.db.query(
                "SELECT id, symbol, name, asset_type FROM instruments "
                "WHERE venue='BUX' AND UPPER(symbol)=? LIMIT 1", (sembol.upper(),))
            if not r:
                bulunamadi.append(sembol)
                continue
            self.db.add_watchlist(r[0]["id"], note="elle secildi")
            kimlik = IdentityResolver(self.s, self.db).coz(
                r[0]["symbol"], r[0]["name"], r[0]["asset_type"], "BUX")
            self.db.save_identity(r[0]["id"], kimlik)
            eklendi.append((r[0]["name"] or r[0]["symbol"], kimlik))

        L = []
        if eklendi:
            L.append(f"✅ <b>{len(eklendi)} enstruman</b> arastirmaya alindi:")
            for ad, k in eklendi:
                ikon = {"dogrulandi": "✅", "fon": "📊", "sec_disi": "⬜"}.get(k.status, "❓")
                ek = f" <code>{k.sec_ticker}</code>" if k.sec_ticker else ""
                L.append(f"  {ikon} {_esc(ad)}{ek}")
        if bulunamadi:
            L.append(f"\n❌ Katalogda yok: {', '.join(_esc(b) for b in bulunamadi)}")
        L.append("\nTarama icin /haber, rapor icin /rapor")
        return "\n".join(L)

    def _haber_tara(self, chat_id, arg: str) -> None:
        """/haber [sembol] — birincil + basin taramasini elle tetikler."""
        from ..collectors import REGISTRY

        if arg:
            return self.tg.send_message(self._haber_goster(arg), chat_id=chat_id)

        self.tg.send_message("🔎 Kaynak taramasi basliyor "
                             "(SEC dosyalamalari + basin)…", chat_id=chat_id)
        ozet = []
        for ad in ("edgar", "stocknews"):
            try:
                r = REGISTRY[ad](self.s, self.db, browser=None).run()
                ozet.append(f"  {ad}: <b>{r.rows}</b> kayit  <i>{r.error or ''}</i>")
            except Exception as e:                    # noqa: BLE001
                log.exception("%s taramasi basarisiz", ad)
                ozet.append(f"  {ad}: ❌ {type(e).__name__}")

        dagilim = self.db.query(
            """SELECT tier, COUNT(*) n FROM news
               WHERE published_at >= datetime('now','-7 days') GROUP BY tier""")
        etiket = {1: "birincil (sirketin kendisi)", 2: "ajans/finans basini",
                  3: "toplayici", 4: "promosyon — kanit degil", 0: "bilinmeyen"}
        L = ["✅ <b>Tarama bitti</b>", ""] + ozet + ["", "<b>Son 7 gun kaynak dagilimi</b>"]
        for r in sorted(dagilim, key=lambda x: x["tier"]):
            L.append(f"  K{r['tier']} <b>{r['n']:>3}</b>  {etiket.get(r['tier'], '?')}")
        L += ["", "<i>Analizde yalnizca K1-K2 kanit sayilir.</i>",
              "Detay: /haber NVDA   ·   Tam rapor: /rapor"]
        self.tg.send_message("\n".join(L), chat_id=chat_id)

    def _etki_text(self, arg: str) -> str:
        """/etki SEMBOL — haber gunlerinde anormal getiri (olay calismasi)."""
        s = (arg or "").strip().upper()
        if not s:
            return ("Kullanim: <code>/etki NVDA</code>\n\n"
                    "<i>Haber gunlerinde fiyatin normalden ne kadar saptigini "
                    "olcer. Fiyat serisi ve kademe 1-2 kaynak gerekir.</i>")

        row = self.db.query(
            "SELECT id, name FROM instruments WHERE symbol = ? LIMIT 1", (s,))
        if not row:
            return f"<b>{_esc(s)}</b> enstruman listesinde yok."

        from ..analysis.events import haber_etkileri
        etkiler = haber_etkileri(self.db, row[0]["id"], s, limit=6)
        if not etkiler:
            n = self.db.query(
                "SELECT COUNT(*) c FROM prices WHERE instrument_id = ?",
                (row[0]["id"],))[0]["c"]
            eksik = ("fiyat gecmisi yok (once /haber ile toplama calistir)"
                     if n < 40 else "olculecek kademe 1-2 haber yok")
            return f"<b>{_esc(s)}</b> icin olcum yapilamadi — {eksik}."

        L = [f"<b>{_esc(s)}</b> — olay-etki olcumu", ""]
        for e in etkiler:
            t = e["t_istatistigi"]
            isaret = "⚡" if e["anlamli_mi"] else "·"
            L.append(f"{isaret} <b>{e['olay_tarihi']}</b>  CAR "
                     f"<b>{e['car_%']:+.2f}%</b>"
                     + (f"  t={t:.2f}" if t is not None else "")
                     + ("  <i>anlamli</i>" if e["anlamli_mi"] else ""))
            for o in e["olaylar"][:3]:
                L.append(f"   <a href=\"{_esc(o['url'])}\">"
                         f"{_esc((o['baslik'] or '')[:62])}</a> "
                         f"<i>[{o['kademe']}]</i>")
            L.append("")

        ilk = etkiler[0]
        L += [f"<i>Gunluk oynaklik {ilk['gunluk_oynaklik_%']:.2f}% · "
              f"pencere {ilk['pencere']} · model: ortalama-duzeltilmis</i>",
              "",
              "<b>⚠️ Bu korelasyondur, nedensellik degil.</b> Ayni pencerede "
              "piyasa geneli ve baska haberler de var; tek bir haberin etkisi "
              "bu veriyle ayristirilamaz. |t| &gt; 2 kabaca anlamlilik esigi."]
        return "\n".join(L)

    def _haber_goster(self, sembol: str) -> str:
        s = sembol.strip().upper()
        rows = self.db.query(
            """SELECT published_at, title, url, publisher, tier FROM news
               WHERE (',' || symbols || ',') LIKE ?
               ORDER BY (tier IN (1,2)) DESC, published_at DESC LIMIT 20""",
            (f"%,{s},%",))
        if not rows:
            return (f"<b>{_esc(s)}</b> icin haber yok.\n"
                    "/haber ile taramayi calistir veya sembolu /takip ile kontrol et.")

        bildirimler = self.db.query(
            """SELECT published_at, category, title, url FROM disclosures
               WHERE symbol = ? ORDER BY published_at DESC LIMIT 5""", (s,))

        L = [f"<b>{_esc(s)}</b>", ""]
        if bildirimler:
            L += ["<b>📄 Birincil kaynak (dosyalama)</b>", ""]
            for r in bildirimler:
                L.append(f"• <a href=\"{_esc(r['url'])}\">{_esc(r['category'] or '')}</a> "
                         f"<i>{r['published_at'][:10]}</i>")
            L.append("")

        kanit = [r for r in rows if r["tier"] in (1, 2)]
        digerleri = [r for r in rows if r["tier"] not in (1, 2)]
        if kanit:
            L += ["<b>✅ Kanit sayilabilir</b>", ""]
            for r in kanit[:10]:
                L.append(f"• <a href=\"{_esc(r['url'])}\">{_esc(r['title'][:78])}</a>\n"
                         f"  <i>{_esc(r['publisher'] or '')} · {(r['published_at'] or '')[:10]}</i>")
            L.append("")
        if digerleri:
            L.append(f"<i>Ayrica {len(digerleri)} toplayici/promosyon icerikli baslik "
                     f"var — kanit sayilmiyor.</i>")
        return "\n".join(L)

    def _temizle(self, arg: str) -> str:
        """
        /temizle [gun]  — indirilen medyayi ve eski DB kayitlarini siler.

        Ekran goruntuleri ve ses dosyalari okunduktan sonra ISE YARAMAZ:
        pozisyonlar zaten SQLite'a yazildi, transkript sohbet gecmisinde.
        Ama diskte birikiyorlar ve icinde portfoy ekranlarin var — sadece
        yer degil, gizlilik meselesi.
        """
        gun = 7
        if arg.strip().isdigit():
            gun = max(0, int(arg.strip()))

        sinir = time.time() - gun * 86400
        silinen, bayt = 0, 0
        for f in self.media_dir.iterdir():
            if not f.is_file() or f.stat().st_mtime >= sinir:
                continue
            bayt += f.stat().st_size
            try:
                f.unlink()
                silinen += 1
            except OSError as e:                      # noqa: BLE001
                log.warning("silinemedi %s: %s", f.name, e)

        # Onaylanmamis bekleyen okumalar da birikir; 7 gunden eskisi olulmustur.
        bekleyen = 0
        for f in self.pending_dir.glob("*.json"):
            if f.stat().st_mtime < time.time() - 7 * 86400:
                f.unlink(missing_ok=True)
                bekleyen += 1

        db_once = self.s.db_path.stat().st_size
        budama = self.db.budama()
        db_sonra = self.s.db_path.stat().st_size

        kalan = sum(f.stat().st_size for f in self.media_dir.iterdir() if f.is_file())
        L = [f"🧹 <b>Temizlik bitti</b> ({gun} gunden eski medya)", "",
             f"  medya dosyasi   <b>{silinen}</b> silindi ({_boyut(bayt)})",
             f"  bekleyen okuma  <b>{bekleyen}</b> silindi",
             f"  eski haber      <b>{budama['haber']}</b> (90 gun+)",
             f"  eski bildirim   <b>{budama['bildirim']}</b> (1 yil+)",
             f"  eski pozisyon   <b>{budama['pozisyon']}</b> (son 30 snapshot tutuldu)",
             "",
             f"  veritabani  {_boyut(db_once)} → <b>{_boyut(db_sonra)}</b>",
             f"  medya kalan <b>{_boyut(kalan)}</b>"]
        if gun == 0:
            L.append("\n<i>0 gun verildi: tum medya silindi.</i>")
        else:
            L.append(f"\n<i>Hepsini silmek icin: /temizle 0</i>")
        return "\n".join(L)

    def _takip_text(self) -> str:
        hedefler = self.db.research_targets()
        if not hedefler:
            return "Arastirma hedefi yok. Portfoy veya liste ekran goruntusu gonder."

        kimlikler = {r["symbol"]: r for r in self.db.identities()}
        gruplar: dict[str, list[str]] = {}
        for h in hedefler:
            k = kimlikler.get(h["symbol"])
            durum = k["status"] if k else "cozulmedi"
            etiket = (f"{h['name'] or h['symbol']}"
                      + (f" <code>{k['sec_ticker']}</code>" if k and k["sec_ticker"] else ""))
            gruplar.setdefault(durum, []).append(etiket)

        basliklar = {
            "dogrulandi": "✅ <b>SEC dogrulandi</b> — birincil kaynak taraniyor",
            "kap": "🇹🇷 <b>BIST</b> — birincil kaynak KAP",
            "fon": "📊 <b>Fon</b> — ISIN uzerinden izleniyor",
            "sec_disi": "⬜ <b>SEC disi</b> — yalnizca basin/IR",
            "eslesmedi": "❓ <b>Kimlik cozulemedi</b> — taranmiyor",
            "cozulmedi": "⏳ <b>Henuz cozulmedi</b>",
        }
        L = [f"<b>Arastirma hedefleri</b> ({len(hedefler)})", ""]
        for durum in ("dogrulandi", "kap", "fon", "sec_disi", "eslesmedi", "cozulmedi"):
            if durum not in gruplar:
                continue
            L.append(basliklar[durum])
            L += [f"  • {a}" for a in sorted(gruplar[durum])]
            L.append("")
        if "eslesmedi" in gruplar:
            L.append("<i>Duzeltmek icin: /kimlik Avantium = AVTX.AS</i>")
        return "\n".join(L)

    def _kimlik_text(self, arg: str) -> str:
        """/kimlik <isim veya sembol> = <ticker>  — elle kimlik atama."""
        if "=" not in arg:
            cozulemeyen = self.db.identities("eslesmedi")
            if not cozulemeyen:
                return ("Kullanim: <code>/kimlik Avantium = AVTX.AS</code>\n\n"
                        "Su an cozulemeyen enstruman yok.")
            L = ["<b>Kimligi cozulemeyenler</b>", ""]
            for r in cozulemeyen:
                L.append(f"• {_esc(r['name'] or r['symbol'])}\n  <i>{_esc((r['note'] or '')[:90])}</i>")
            L += ["", "Duzeltmek icin: <code>/kimlik ISIM = TICKER</code>"]
            return "\n".join(L)

        sol, _, ticker = arg.partition("=")
        sol, ticker = sol.strip(), ticker.strip().upper()
        if not sol or not ticker:
            return "Kullanim: <code>/kimlik Avantium = AVTX.AS</code>"

        anahtar = _ad_anahtari(sol)
        hedef = None
        for h in self.db.research_targets():
            if _ad_anahtari(h["name"]) == anahtar or h["symbol"].upper() == sol.upper():
                hedef = h
                break
        if not hedef:
            return f"'{_esc(sol)}' izleme listesinde bulunamadi. /takip ile bak."

        from ..research import IdentityResolver
        kimlik = IdentityResolver(self.s, self.db).elle_coz(
            hedef["symbol"], hedef["name"], ticker)
        self.db.save_identity(hedef["id"], kimlik)

        L = [f"✅ <b>{_esc(hedef['name'] or hedef['symbol'])}</b> → "
             f"<code>{_esc(ticker)}</code>"]
        if kimlik.edgar_hazir:
            L += [f"\n🔎 SEC'de dogrulandi: <b>{_esc(kimlik.sec_name)}</b>",
                  f"CIK <code>{kimlik.cik}</code> · {_esc(kimlik.exchange or '')}",
                  "\nArtik EDGAR dosyalamalari da taranacak. /haber ile calistir."]
        else:
            L.append(f"\n<i>{_esc(kimlik.note or '')}</i>")
        return "\n".join(L)

    def _sil_son(self, sahip: str) -> str:
        """
        /sil — SON kaydi geri alir. TEK anlik goruntu, tek hesap.

        Onceki hali TUM hesaplarin son anlik goruntusunu siliyordu. Sonuc:
        2026-08-15'te Binance kaydi iptal edildikten sonra /sil calistirildi
        ve BUX'un 18 pozisyonu (5.929,81 EUR) da silindi — BUX'ta tek
        snapshot vardi, tablo tamamen bosaldi. Yedekten geri yuklendi.

        "Geri al" TEK islemi geri almalidir. En son yazilan anlik goruntu
        hangi hesaba aitse yalnizca o silinir.
        """
        # SAHIP SUZGECI SECIMDE DE OLMALI. Yoksa en son yazan KIM olursa
        # olsun onun kaydi secilir; A'nin "geri al"i B'nin hesabini
        # hedefler ve silme (dogru sekilde) hicbir sey silmez — kullanici
        # "geri alindi" yazisini gorur ama kaydi DURUYORDUR.
        en_son = self.db.son_snapshot(sahip)
        if not en_son:
            return "Silinecek pozisyon kaydi yok."
        r = en_son
        kalan = self.db.query(
            "SELECT COUNT(DISTINCT snapshot_ts) c FROM positions "
            "WHERE account=? AND sahip=?", (r["account"], sahip))[0]["c"]
        n = self.db.delete_snapshot(r["account"], r["snapshot_ts"], sahip)
        mesaj = [f"🗑 Geri alindi: <b>{r['account'].upper()}</b> "
                 f"{n} pozisyon <i>({r['snapshot_ts'][:16]})</i>"]
        if kalan <= 1:
            mesaj.append(f"\n⚠️ Bu <b>{r['account'].upper()}</b> hesabinin "
                         f"TEK kaydiydi — artik portfoy verisi yok. "
                         f"Yeniden ekran goruntusu gonderman gerekir.")
        else:
            mesaj.append(f"\n<i>{r['account'].upper()} icin {kalan - 1} "
                         f"onceki kayit duruyor.</i>")
        return "\n".join(mesaj)


# ----------------------------------------------------------------------
# Ekranin altinda kalan pozisyonlari yakalar. Portfoy ekrani kaydirilmadan
# gonderildiginde okunan toplam, ekranda yazan toplamdan kucuk kalir; bu
# fark sessizce gecerse agirlik/yogunlasma analizi eksik veri uzerinde
# calisir ve kullanici bunu fark etmez.
KAPSAM_ESIGI = 0.98


def _kapsam(okunan: float | None, toplam: float | None) -> float | None:
    if not toplam or okunan is None or toplam <= 0:
        return None
    return okunan / toplam


def _kapsam_uyarisi(p: dict) -> list[str]:
    oran = _kapsam(p.get("okunan_toplam"), p.get("toplam_deger"))
    if oran is None or oran >= KAPSAM_ESIGI:
        return []
    fark = p["toplam_deger"] - p["okunan_toplam"]
    return ["", f"🔻 <b>Portfoyun ~%{(1 - oran) * 100:.0f}'i eksik</b> "
            f"({_money(fark)} {p.get('para_birimi') or ''} gorunmuyor).",
            "<i>Listeyi asagi kaydirip devamini da gonder — 20 dk icinde "
            "gelenleri ayni portfoye eklerim.</i>"]


def _gecici_sembol(ad: str) -> str:
    """
    Ticker ekranda yoksa addan GECICI anahtar uret.

    Bilerek ticker'a benzemiyor (~ onekli): boylece yanlislikla gercek bir
    ticker'la cakisip baska sirketin verisine baglanamaz. Kimlik cozumlemesi
    dogru sembolu bulunca bu kayit guncellenir.
    """
    sade = "".join(ch for ch in ad.upper() if ch.isalnum())[:18]
    return f"~{sade}" if sade else "~BILINMEYEN"


def sesle_calistirilmaz(metin: str) -> str | None:
    """
    Sesli mesajdan YIKICI bir komut cikiyorsa adini dondurur.

    Konusma tanima hata yapiyor ("portfoyumde" -> "port foyumde"); yanlis
    duyulan tek bir kelime yuzunden portfoy kaydinin silinmesi kabul edilemez.
    """
    parcalar = [p for p in re.split(r"[^\wçğıöşüÇĞİÖŞÜ]+", (metin or "").strip().lower()) if p]
    if not parcalar:
        return None
    ilk = parcalar[0].lstrip("/")
    return ilk if ilk in YIKICI_KOMUTLAR else None


def _ad_anahtari(ad) -> str:
    """Isim karsilastirmasi icin sadelestir: 'Amazon.com' ~ 'Amazon com'."""
    if not ad:
        return ""
    return "".join(ch for ch in str(ad).casefold() if ch.isalnum())


def _esc(s) -> str:
    import html
    return html.escape(str(s))


def _boyut(bayt: float) -> str:
    for birim in ("B", "KB", "MB", "GB"):
        if bayt < 1024 or birim == "GB":
            return f"{bayt:.0f} {birim}" if birim == "B" else f"{bayt:.1f} {birim}"
        bayt /= 1024
    return f"{bayt:.1f} GB"


def _money(v) -> str:
    """Para her zaman 2 ondalikli. '3,612' bir tutar icin yanlis okunur."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "—"
    return f"{v:,.2f}"


def _qty(v) -> str:
    """Adet tam sayiysa ondalik gosterme: '28', '0.4521' degil '28.00'."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "—"
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.4f}".rstrip("0").rstrip(".")
