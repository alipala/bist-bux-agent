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

# Ayni hesaba bu sure icinde gelen ekran goruntuleri TEK anlik goruntude
# birlesir. Portfoy iki ekrana sigmadiginda kullanici arka arkaya 2-3 gorsel
# atiyor; ayri snapshot'lara yazilsa latest_positions() yalnizca sonuncuyu
# gorur ve portfoyun yarisi kaybolur.
SNAPSHOT_MERGE_WINDOW = timedelta(minutes=20)

# Sesle CALISTIRILMAYACAK komutlar — geri donusu olmayan veri islemleri.
YIKICI_KOMUTLAR = {"sil", "unut"}

YARDIM = """<b>Yatirim Analistin</b>

<b>Komut ezberlemene gerek yok — ne istersen yaz.</b>
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
/portfoy /rapor /ozet /takip /evren /aday /haber /etki /durum /bekleyen
/onayla — bekleyen okumalari kaydet
/kimlik ISIM = TICKER — kimligi elle ata
/sil — SON kaydi geri al (tek anlik goruntu)
/temizle [gun] — indirilen medyayi ve eski kayitlari sil
/unut — sohbet gecmisini temizle"""


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

        self.allowed = self._load_allowlist()
        self._running = True
        # Sohbet ici gorsel hafizasi: kullanici bir tur goruntu atip
        # SONRAKI turda "resimde gordugun kadar..." diyebiliyor. Eskiden
        # goruntu akisi sohbetten kopuktu ve model "gorsel bana ulasmadi"
        # diyordu — dogru ama kullanici icin anlamsiz bir sinirdi.
        self._son_gorsel: dict[int, str] = {}

    # ------------------------------------------------------------------
    def _load_allowlist(self) -> set[int]:
        ids: set[int] = set()
        if self.tg.chat_id:
            try:
                ids.add(int(self.tg.chat_id))
            except ValueError:
                log.warning("TELEGRAM_CHAT_ID sayi degil: %r", self.tg.chat_id)
        for extra in (self.s.get("telegram.extra_chat_ids") or []):
            try:
                ids.add(int(extra))
            except (TypeError, ValueError):
                continue
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

    def run(self) -> int:
        self._kilit = self._tekil_kilit()
        if not self.tg.token:
            raise SystemExit("TELEGRAM_BOT_TOKEN tanimli degil (.env).")
        if not self.allowed:
            raise SystemExit(
                "TELEGRAM_CHAT_ID tanimli degil (.env).\n"
                "  Once: python run.py telegram-chatid")

        offset = self._read_offset()
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

        backoff = 1
        kopma_ani = None
        while self._running:
            try:
                updates = self.tg.get_updates(offset=offset, timeout=50)
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

            # ZAMANLANMIS IS GOZETIMI: nabiz sessizce calismamis olabilir.
            # Sessiz basarisizlik en tehlikeli ariza — hicbir sey olmamis
            # gibi gorunur.
            kacan = self.bekci.kacirilan_nabiz()
            if kacan:
                self.bekci.bildir("nabiz_kacti", (
                    f"⚠️ <b>Nabiz calismadi</b> — {kacan['gun']}\n"
                    f"{_esc(kacan['not'])}\n\n"
                    "Kontrol: <code>tail -50 data/pulse.log</code>\n"
                    "Elle calistir: <code>launchctl kickstart -p "
                    "gui/$UID/com.alipala.finagent.pulse</code>"))

            for upd in updates:
                offset = upd["update_id"] + 1
                self._write_offset(offset)
                try:
                    self._dispatch(upd)
                except Exception:                     # noqa: BLE001
                    log.exception("guncelleme islenemedi: %s", upd.get("update_id"))

        log.info("Bot durduruldu.")
        return 0

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    def _dispatch(self, upd: dict) -> None:
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

        engellenen = sesle_calistirilmaz(metin)
        if engellenen:
            self.tg.send_message(
                f"⚠️ <code>/{_esc(engellenen)}</code> veri siler ve "
                "sesle calistirilmaz.\n"
                "<i>Konusma tanima yanlis duyabiliyor. Gercekten istiyorsan "
                "komutu yazarak gonder.</i>", chat_id=chat_id)
            return

        # Metin akisina devret: komutlar da sesle calisir ("rapor", "portfoy").
        self._on_text(metin, chat_id)

    # --- metin komutlari ------------------------------------------------
    def _on_text(self, text: str, chat_id) -> None:
        cmd, _, arg = text.partition(" ")
        cmd = cmd.lower().lstrip("/").split("@")[0]
        arg = arg.strip()

        if cmd in ("start", "yardim", "help"):
            self.tg.send_message(YARDIM, chat_id=chat_id)
        elif cmd == "durum":
            self.tg.send_message(self._durum_text(), chat_id=chat_id)
        elif cmd == "portfoy":
            self.tg.send_message(self._portfoy_text(), chat_id=chat_id)
        elif cmd == "evren":
            self.tg.send_message(self._evren_text(arg), chat_id=chat_id)
        elif cmd in ("onayla", "hepsi"):
            self._hepsini_onayla(chat_id)
        elif cmd == "bekleyen":
            n = len(list(self.pending_dir.glob("*.json")))
            self.tg.send_message(f"Bekleyen okuma: <b>{n}</b>"
                                 + ("\n/onayla ile hepsini kaydet." if n else ""),
                                 chat_id=chat_id)
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
            self.tg.send_message(self._sil_son(), chat_id=chat_id)
        elif cmd in ("rapor", "ozet"):
            self._calistir_rapor(chat_id, topla=(cmd == "rapor"))
        elif cmd == "unut":
            self._chat().unut(chat_id)
            self.tg.send_message("🧹 Sohbet gecmisi temizlendi.", chat_id=chat_id)
        elif text.startswith("/"):
            self.tg.send_message(
                f"Bilinmeyen komut: <code>{_esc(cmd)}</code>\n/yardim ile listeye bak.",
                chat_id=chat_id)
        else:
            # Komut degilse SOHBET. Son gonderilen gorsel de tasinir ki
            # "az once attigim resimdeki..." turu istekler calissin.
            self._sohbet(text, chat_id, gorsel=self._son_gorsel.get(chat_id))

    # --- sohbet ----------------------------------------------------------
    def _chat(self):
        if getattr(self, "_chat_engine", None) is None:
            from .chat import ChatEngine
            self._chat_engine = ChatEngine(self.s, self.db)
        return self._chat_engine

    def _sohbet(self, soru: str, chat_id, gorsel: str | None = None) -> None:
        """
        Serbest sohbet. Model araclariyla calisir ve ISLEM de yapabilir.

        Model bir yazma islemi hazirladiysa (`pozisyon_kaydet`) mesaja
        Kaydet/Iptal butonu eklenir — mimari §5 insan onayi korunuyor ama
        tek dokunusa iniyor.
        """
        motor = self._chat()
        # Cevap ~30-60 sn suruyor; kullanici bota mesajin dustugunu gormeli.
        self.tg.chat_action(chat_id, "typing")
        cevap = motor.cevapla(chat_id, soru, gorsel=gorsel)

        gecmis = motor.gecmis_oku(chat_id)
        gecmis += [{"rol": "user", "metin": soru},
                   {"rol": "assistant", "metin": cevap[:1500]}]
        motor.gecmis_yaz(chat_id, gecmis)

        from ..notify.telegram import md_to_tg_html
        tokenlar = getattr(motor, "bekleyen_tokenlar", []) or []
        markup = None
        if tokenlar:
            t = tokenlar[-1]        # birden fazlaysa sonuncusu gecerli
            markup = {"inline_keyboard": [[
                {"text": "✅ Kaydet", "callback_data": f"ok:{t}"},
                {"text": "❌ Iptal", "callback_data": f"no:{t}"}]]}
        self.tg.send_message(md_to_tg_html(cevap), chat_id=chat_id,
                             reply_markup=markup)

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
        self._son_gorsel[chat_id] = str(path)

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
            not_ = parsed.get("notlar") or "pozisyon bulunamadi"
            self.tg.send_message(
                f"⚠️ Bu goruntude pozisyon goremedim.\n<i>{_esc(not_)}</i>\n\n"
                "Portfoy/holdings ekranini tam gorunur halde tekrar dener misin?",
                chat_id=chat_id)
            return

        if not parsed["hesap"]:
            self.tg.send_message(
                "⚠️ Hangi hesap oldugunu ayirt edemedim. Gorseli tekrar gonderirken "
                "aciklamasina <code>bux</code> veya <code>midas</code> yaz.",
                chat_id=chat_id)
            return

        token = secrets.token_hex(6)
        (self.pending_dir / f"{token}.json").write_text(
            json.dumps(parsed, ensure_ascii=False, default=str), encoding="utf-8")

        self.tg.send_message(
            self._onay_metni(parsed),
            reply_markup={"inline_keyboard": [[
                {"text": "✅ Kaydet", "callback_data": f"ok:{token}"},
                {"text": "❌ Iptal", "callback_data": f"no:{token}"},
            ]]},
            chat_id=chat_id,
        )

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
        self._son_gorsel[chat_id] = str(yol)
        self._sohbet(soru, chat_id, gorsel=str(yol))

    def _liste_onayi(self, p: dict, chat_id) -> None:
        """Alinabilir enstruman listesi -> izleme listesine aday olarak eklenir."""
        satirlar = p.get("liste") or []
        if not satirlar:
            self.tg.send_message(
                "⚠️ Bu goruntude enstruman listesi goremedim.\n"
                f"<i>{_esc(p.get('notlar') or '')}</i>", chat_id=chat_id)
            return

        token = secrets.token_hex(6)
        (self.pending_dir / f"{token}.json").write_text(
            json.dumps(p, ensure_ascii=False, default=str), encoding="utf-8")

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

    def _onay_metni(self, p: dict) -> str:
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
        projeksiyon = self._projeksiyon(p)
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
    def _on_callback(self, cb: dict) -> None:
        chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
        if not self._authorised(chat_id):
            self.tg.answer_callback_query(cb["id"], "yetkisiz")
            return

        action, _, token = (cb.get("data") or "").partition(":")
        pending = self.pending_dir / f"{token}.json"
        if not token or not pending.exists():
            self.tg.answer_callback_query(cb["id"], "bu istek artik gecerli degil")
            return

        if action == "no":
            pending.unlink(missing_ok=True)
            self.tg.answer_callback_query(cb["id"], "iptal edildi")
            self.tg.send_message("🗑 Iptal edildi, hicbir sey kaydedilmedi.",
                                 chat_id=chat_id)
            return

        if action not in ("ok", "wl"):
            self.tg.answer_callback_query(cb["id"], "bilinmeyen islem")
            return

        parsed = json.loads(pending.read_text(encoding="utf-8"))
        pending.unlink(missing_ok=True)

        if action == "wl":
            self.tg.answer_callback_query(cb["id"], "ekleniyor…")
            self._watchlist_kaydet(parsed, chat_id)
            return

        self.tg.answer_callback_query(cb["id"], "kaydediliyor…")
        self.tg.send_message(self._pozisyon_kaydet(parsed), chat_id=chat_id)

    # ------------------------------------------------------------------
    def _pozisyon_kaydet(self, parsed: dict) -> str:
        account = parsed["hesap"]
        snapshot = self._snapshot_ts(account)
        rows, duzeltmeler = self._hizala_semboller(account, snapshot, parsed["pozisyonlar"])
        n = self.db.insert_positions(account, snapshot, rows)

        L = [f"✅ <b>{account.upper()}</b> — {n} pozisyon kaydedildi.",
             f"<code>{snapshot[:19]}</code>"]
        if duzeltmeler:
            L.append("\n🔗 Ayni sirket olarak eslestirildi: "
                     + ", ".join(f"<code>{_esc(d)}</code>" for d in duzeltmeler))

        # Kapsami TUM snapshot uzerinden yeniden olc: kullanici ikinci/ucuncu
        # gorseli gonderdikce eksik oran dusmeli, uyari kendiliginden susmali.
        kayitli = self.db.snapshot_value(account, snapshot)
        beklenen = parsed.get("toplam_deger")
        ccy = parsed.get("para_birimi") or ""
        if kayitli:
            L.append(f"\nPortfoyde toplam: <b>{_money(kayitli)}</b> {ccy}")
        oran = _kapsam(kayitli, beklenen)
        if oran is not None and oran < KAPSAM_ESIGI:
            L += [f"\n🔻 Ekranda yazan toplam {_money(beklenen)} {ccy} — "
                  f"hala <b>{_money(beklenen - kayitli)}</b> {ccy} eksik.",
                  "<i>Kaydirip devamini gonder.</i>"]
        else:
            L.append("\nYanlissa /sil ile geri alabilirsin. Analiz icin /rapor.")
        return "\n".join(L)

    def _hepsini_onayla(self, chat_id) -> None:
        """
        Bekleyen TUM okumalari tek komutla kaydeder.

        Arka arkaya 10 ekran goruntusu gonderirken her biri icin ayri butona
        basmak gereksiz surtunme yaratiyor. Onay yine de aliniyor — sadece
        toplu.
        """
        bekleyenler = sorted(self.pending_dir.glob("*.json"))
        if not bekleyenler:
            self.tg.send_message("Bekleyen okuma yok.", chat_id=chat_id)
            return

        self.tg.send_message(f"⏳ {len(bekleyenler)} bekleyen okuma kaydediliyor…",
                             chat_id=chat_id)
        liste_toplami: list[dict] = []
        for yol in bekleyenler:
            try:
                p = json.loads(yol.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            yol.unlink(missing_ok=True)

            if p.get("ekran_tipi") == "liste":
                liste_toplami += p.get("liste") or []
            elif p.get("pozisyonlar") and p.get("hesap"):
                self.tg.send_message(self._pozisyon_kaydet(p), chat_id=chat_id)

        if liste_toplami:
            # Ayni enstruman birden fazla ekranda gorunebilir — ada gore tekille.
            gorulen, tekil = set(), []
            for r in liste_toplami:
                anahtar = _ad_anahtari(r.get("name"))
                if anahtar and anahtar not in gorulen:
                    gorulen.add(anahtar)
                    tekil.append(r)
            self._watchlist_kaydet({"liste": tekil}, chat_id)

    def _merge_target(self, account: str) -> str | None:
        """Birlestirme penceresi icindeki mevcut snapshot; yoksa None."""
        last = self.db.latest_snapshot_ts(account)
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

    def _snapshot_ts(self, account: str) -> str:
        """
        Yakin zamanli bir anlik goruntu varsa ONA ekle, yoksa yenisini ac.
        Cok ekranli portfoyun tek snapshot'ta toplanmasini saglar.
        """
        hedef = self._merge_target(account)
        if hedef:
            log.info("[bot] %s: mevcut snapshot'a ekleniyor (%s)", account, hedef)
            return hedef
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _hizala_semboller(self, account: str, snapshot: str,
                          rows: list[dict]) -> tuple[list[dict], list[str]]:
        """
        Ayni sirketi ayni sembolle kaydet.

        Ticker'lar ekranda yazmadigi icin model isimden turetiyor ve bu
        turetme okumalar arasinda oynayabiliyor: ayni ING satiri bir
        goruntude "INGA", digerinde "ING" cikti. Ikisi de yazilirsa ayni
        pozisyon iki kez sayilir ve portfoy toplami sisiyor.
        Mevcut snapshot'ta AYNI ISIMDE bir kayit varsa onun sembolu esas alinir.
        """
        mevcut = self.db.snapshot_symbol_names(account, snapshot)
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

    def _projeksiyon(self, p: dict) -> float | None:
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

        hedef = self._merge_target(hesap)
        mevcut = self.db.snapshot_positions(hesap, hedef) if hedef else {}

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
    def _durum_text(self) -> str:
        L = ["<b>Veritabani</b>", ""]
        for table, etiket in (("instruments", "enstruman"), ("prices", "fiyat"),
                              ("positions", "pozisyon"), ("disclosures", "KAP"),
                              ("news", "haber")):
            n = self.db.query(f"SELECT COUNT(*) c FROM {table}")[0]["c"]
            L.append(f"  {etiket:12} <code>{n:>7}</code>")

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

    def _portfoy_text(self) -> str:
        L = []
        for acct in ("bux", "midas", "binance"):
            rows = self.db.latest_positions(acct)
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

    def _sil_son(self) -> str:
        """
        /sil — SON kaydi geri alir. TEK anlik goruntu, tek hesap.

        Onceki hali TUM hesaplarin son anlik goruntusunu siliyordu. Sonuc:
        2026-08-15'te Binance kaydi iptal edildikten sonra /sil calistirildi
        ve BUX'un 18 pozisyonu (5.929,81 EUR) da silindi — BUX'ta tek
        snapshot vardi, tablo tamamen bosaldi. Yedekten geri yuklendi.

        "Geri al" TEK islemi geri almalidir. En son yazilan anlik goruntu
        hangi hesaba aitse yalnizca o silinir.
        """
        en_son = self.db.query(
            """SELECT account, snapshot_ts, COUNT(*) n FROM positions
               GROUP BY account, snapshot_ts
               ORDER BY snapshot_ts DESC LIMIT 1""")
        if not en_son:
            return "Silinecek pozisyon kaydi yok."
        r = en_son[0]
        kalan = self.db.query(
            "SELECT COUNT(DISTINCT snapshot_ts) c FROM positions WHERE account=?",
            (r["account"],))[0]["c"]
        n = self.db.delete_snapshot(r["account"], r["snapshot_ts"])
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
