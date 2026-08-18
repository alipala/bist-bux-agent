"""
Tek bir Telegram isini isleyen ALT SUREC.

`run.py bot-worker --is data/bot/kuyruk/<update_id>.json`

Ana surec (dinleyici) yalnizca is dagitir; ISIN KENDISI burada calisir ve
CEVABI DA BURASI GONDERIR. Bunun iki sonucu var, ikisi de bilerek:

  * Ana surec olse bile is tamamlanir ve kullanici cevabini alir. Planli
    restart artik ucustaki turu ne oldurur ne ciftler.
  * Bir turun cokmesi baska hicbir sohbeti etkilemez.

`FinBot.run()` CAGRILMIYOR — yalnizca `_calistir`. Yani bu surec ne
`getUpdates` ceker, ne tekil kilidi (`bot.lock`) alir, ne sinyal
yakalayicisi kurar. Isin kendisi (`_calistir`) koşu dongusunun hicbir
durumuna dokunmuyor; bolme sinirinin burada olmasinin sebebi bu.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def calistir(settings, db, is_yolu) -> int:
    is_yolu = Path(is_yolu)
    try:
        is_ = json.loads(is_yolu.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.error("worker: is dosyasi okunamadi (%s): %s", is_yolu, e)
        return 2

    update = is_.get("update")
    if not isinstance(update, dict):
        log.error("worker: is dosyasinda guncelleme yok: %s", is_yolu)
        # BITTI YAZILIYOR: dosya bozuksa yeniden denemek de bozuk okur.
        _bitti_yaz(is_yolu, "gecersiz")
        return 2

    from .kuyruk import KalpAtisi
    from .listener import FinBot

    uid = is_.get("update_id")
    chat_id = is_.get("chat_id")
    log.info("worker basladi: guncelleme %s (chat %s, deneme %s)",
             uid, chat_id, is_.get("deneme"))

    bot = FinBot(settings, db)
    # Bu surecte kuyruk YOK: `_calistir` dogrudan cagriliyor ve isin
    # kendini yeniden kuyruga atmasi imkansiz olmali.
    bot.kuyruk = None

    sonuc = "tamam"
    hb = KalpAtisi(is_yolu.with_suffix(".hb"))
    try:
        with hb:
            bot._calistir(update)
    except Exception as e:                            # noqa: BLE001
        # ISTISNA BIR SONUCTUR, YENIDEN DENEME SEBEBI DEGIL. Kullaniciya
        # soylenir ve `.bitti` yazilir; aksi halde ayni hata iki kez
        # uretilir ve kullanici iki hata mesaji alir.
        sonuc = "hata"
        log.exception("worker: is islenemedi (%s)", uid)
        try:
            bot.tg.send_message(
                f"❌ Bu mesaji islerken hata aldim.\n<code>{type(e).__name__}: "
                f"{str(e)[:200]}</code>", chat_id=chat_id)
        except Exception:                             # noqa: BLE001
            log.exception("worker: hata mesaji da gonderilemedi")
    finally:
        # SERT COKMEDE (kill/OOM) buraya HIC gelinmez ve `.bitti`
        # yazilmaz — ana surec bunu gorup isi yeniden denemeli. Ayrim
        # tam olarak budur.
        _bitti_yaz(is_yolu, sonuc)

    log.info("worker bitti: guncelleme %s (%s)", uid, sonuc)
    return 0


def _bitti_yaz(is_yolu: Path, sonuc: str) -> None:
    try:
        is_yolu.with_suffix(".bitti").write_text(sonuc, encoding="utf-8")
    except OSError as e:                              # noqa: BLE001
        # Yazilamazsa ana surec isi COKMUS sanar ve bir kez daha dener.
        # Sessiz kalmamali: iki cevap gelirse sebebi bu satirdir.
        log.error("worker: bitti isareti yazilamadi (%s): %s", is_yolu, e)
