"""
Telegram Bot API ile rapor gonderimi.

Kurulum:
  1) Telegram'da @BotFather -> /newbot -> token'i .env'e TELEGRAM_BOT_TOKEN olarak yaz
  2) Kendi botuna bir mesaj at
  3) https://api.telegram.org/bot<TOKEN>/getUpdates -> "chat":{"id": ...}
     -> .env'e TELEGRAM_CHAT_ID olarak yaz
Dogrulama:  python run.py telegram-test
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
FILE_API = "https://api.telegram.org/file/bot{token}/{path}"


class TelegramNotifier:
    def __init__(self, settings):
        self.s = settings
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.max_chars = int(settings.get("telegram.max_message_chars", 3800))

    @property
    def enabled(self) -> bool:
        if not self.s.get("telegram.enabled", True):
            return False
        if not (self.token and self.chat_id):
            log.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID yok -> bildirim atlaniyor.")
            return False
        return True

    # ------------------------------------------------------------------
    def _post(self, method: str, _timeout: float = 30.0, **kwargs) -> dict | None:
        try:
            r = httpx.post(API.format(token=self.token, method=method),
                           timeout=_timeout, **kwargs)
            data = r.json()
            if not data.get("ok"):
                log.error("Telegram %s hatasi: %s", method, data.get("description"))
                return None
            return data
        except Exception as e:                       # noqa: BLE001
            log.error("Telegram %s istegi basarisiz: %s", method, e)
            return None

    def send_message(self, text: str, reply_markup: dict | None = None,
                     chat_id: str | int | None = None) -> bool:
        if not self.enabled:
            return False
        target = chat_id if chat_id is not None else self.chat_id
        chunks = _split(text, self.max_chars)
        ok = True
        for i, chunk in enumerate(chunks):
            payload = {
                "chat_id": target,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
            # Butonlar yalnizca SON parcaya iliskir; aksi halde ayni onay
            # klavyesi her parcada tekrar cikar ve kullanici iki kez basar.
            if reply_markup and i == len(chunks) - 1:
                payload["reply_markup"] = json.dumps(reply_markup)
            res = self._post("sendMessage", data=payload)
            ok = ok and res is not None
        return ok

    def send_message_id(self, text: str,
                        chat_id: str | int | None = None) -> int | None:
        """
        TEK mesaj gonderir ve `message_id` DONDURUR.

        `send_message` bool donuyor ve kimligi atiyor; ilerleme
        gostergesi mesaji SONRADAN DUZENLEMEK zorunda oldugu icin
        kimlige ihtiyaci var. Metin BOLUNMEZ — bu yol yalnizca kisa
        durum satirlari icin; uzun cevap normal yoldan gider.
        """
        if not self.enabled:
            return None
        res = self._post("sendMessage", data={
            "chat_id": chat_id if chat_id is not None else self.chat_id,
            "text": text[:self.max_chars],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })
        try:
            return int(res["result"]["message_id"]) if res else None
        except (KeyError, TypeError, ValueError):
            return None

    def edit_message(self, message_id: int, text: str,
                     chat_id: str | int | None = None) -> bool:
        """
        Var olan mesajin metnini degistirir.

        DIKKAT — AYNI METINLE cagrilirsa Telegram 400 "message is not
        modified" doner. Cagiran taraf tekrari ELEMELI (bkz.
        `bot/ilerleme.py`); burada susturmuyoruz, cunku sessizce yutmak
        gercek bir hatayi da gizlerdi.
        """
        if not self.enabled:
            return False
        return self._post("editMessageText", data={
            "chat_id": chat_id if chat_id is not None else self.chat_id,
            "message_id": message_id,
            "text": text[:self.max_chars],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }) is not None

    def delete_message(self, message_id: int,
                       chat_id: str | int | None = None) -> bool:
        if not self.enabled:
            return False
        return self._post("deleteMessage", data={
            "chat_id": chat_id if chat_id is not None else self.chat_id,
            "message_id": message_id,
        }) is not None

    def send_photo(self, path: Path, caption: str = "",
                   chat_id: str | int | None = None) -> bool:
        """
        Gorsel gonderir. `send_document`'tan farki Telegram'in onizleme
        gostermesi — grafik/ekran goruntusu icin dogru olan bu.

        Caption 1024 karakterle sinirli (Telegram kurali); asarsa kirpilir
        ve devami ayri mesaj olarak gider, yoksa API cagriyi TAMAMEN
        reddeder ve gorsel hic gonderilmez.
        """
        path = Path(path)
        if not path.exists():
            log.warning("gonderilecek gorsel yok: %s", path)
            return False
        kalan = ""
        if len(caption) > 1024:
            kalan, caption = caption[1020:], caption[:1020] + "…"
        try:
            with path.open("rb") as f:
                ok = self._post("sendPhoto", _timeout=60,
                                data={"chat_id": chat_id or self.chat_id,
                                      "caption": caption, "parse_mode": "HTML"},
                                files={"photo": f}) is not None
        except OSError as e:                          # noqa: BLE001
            log.warning("gorsel okunamadi: %s", e)
            return False
        if ok and kalan:
            self.send_message(kalan, chat_id=chat_id)
        return ok

    def send_document(self, path: Path, caption: str = "") -> bool:
        if not self.enabled or not Path(path).exists():
            return False
        with open(path, "rb") as fh:
            res = self._post(
                "sendDocument",
                data={"chat_id": self.chat_id, "caption": caption[:1000], "parse_mode": "HTML"},
                files={"document": (Path(path).name, fh, "text/html")},
            )
        return res is not None

    # ------------------------------------------------------------------
    def send_report(self, analysis_md: str, bundle: dict, html_path: Path | None = None) -> bool:
        if not self.enabled:
            return False

        head = _headline(bundle)
        body = md_to_tg_html(analysis_md)
        ok = self.send_message(f"{head}\n\n{body}")

        if html_path and self.s.get("telegram.send_html_file", True):
            ok = self.send_document(html_path, caption="📎 Tam rapor (HTML)") and ok
        return ok

    def test(self) -> bool:
        return self.send_message("✅ <b>BIST/BUX Agent</b> — Telegram baglantisi calisiyor.")

    # --- bot (gelen yon) ------------------------------------------------
    def get_updates(self, offset: int | None = None, timeout: int = 50) -> list[dict]:
        """
        Long-polling. `timeout` saniye boyunca acik bekler; yeni guncelleme
        gelirse hemen doner. Bos liste = zaman asimi (normal durum).
        """
        data: dict = {"timeout": timeout, "limit": 20}
        if offset is not None:
            data["offset"] = offset
        # HTTP zaman asimi long-poll suresinden UZUN olmali, yoksa her
        # turda httpx.ReadTimeout firlatir ve bot surekli hata loglar.
        res = self._post("getUpdates", data=data, _timeout=timeout + 15)
        return (res or {}).get("result", []) or []

    def chat_action(self, chat_id: str | int, action: str = "typing") -> None:
        """'yaziyor…' gostergesi. LLM cevabi 30-60 sn surdugu icin kullanici
        mesajin dustugunu bilmeli; aksi halde tekrar tekrar gonderiyor."""
        self._post("sendChatAction", data={"chat_id": chat_id, "action": action},
                   _timeout=10.0)

    def answer_callback_query(self, callback_id: str, text: str = "") -> None:
        self._post("answerCallbackQuery", data={"callback_query_id": callback_id,
                                                "text": text[:200]})

    def download_file(self, file_id: str, dest_dir: Path) -> Path | None:
        """file_id -> yerel dosya. Telegram iki adim ister: getFile, sonra indirme."""
        res = self._post("getFile", data={"file_id": file_id})
        if not res:
            return None
        remote = (res.get("result") or {}).get("file_path")
        if not remote:
            return None

        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{file_id[:24]}_{Path(remote).name}"
        try:
            with httpx.stream("GET", FILE_API.format(token=self.token, path=remote),
                              timeout=90.0) as r:
                r.raise_for_status()
                with open(dest, "wb") as fh:
                    for block in r.iter_bytes():
                        fh.write(block)
        except Exception as e:                       # noqa: BLE001
            log.error("Telegram dosya indirilemedi: %s", e)
            return None
        return dest

    # ------------------------------------------------------------------
    def discover_chat_ids(self) -> list[dict]:
        """
        getUpdates'i cagirip bota yazan sohbetleri listeler.
        TELEGRAM_CHAT_ID'yi elle bulmak icin URL acmaya gerek kalmaz.
        """
        if not self.token:
            raise SystemExit("TELEGRAM_BOT_TOKEN tanimli degil (.env).")

        data = self._post("getUpdates", data={"timeout": 0, "limit": 100})
        if data is None:
            return []

        seen: dict[int, dict] = {}
        for upd in data.get("result", []):
            msg = (upd.get("message") or upd.get("channel_post")
                   or upd.get("edited_message") or {})
            chat = msg.get("chat")
            if not chat:
                continue
            title = chat.get("title") or " ".join(
                filter(None, [chat.get("first_name"), chat.get("last_name")])
            ) or chat.get("username") or "?"
            seen[chat["id"]] = {"chat_id": chat["id"], "tip": chat.get("type"), "ad": title}
        return list(seen.values())


# ----------------------------------------------------------------------
def _headline(bundle: dict) -> str:
    tot = (bundle.get("portfoy") or {}).get("toplam") or {}
    parts = [f"<b>📊 Gunluk Analiz — {bundle.get('tarih','')}</b>"]
    if tot.get("deger"):
        pnl = tot.get("kar_zarar") or 0
        emoji = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
        parts.append(f"{emoji} Portfoy: <code>{tot['deger']:,.2f}</code> "
                     f"(K/Z <code>{pnl:+,.2f}</code>)")
    parts.append(f"📰 {len(bundle.get('haber', []))} haber · "
                 f"📋 {len(bundle.get('kap', []))} KAP bildirimi")
    return "\n".join(parts)


def md_to_tg_html(md: str) -> str:
    """Markdown'i Telegram'in destekledigi kucuk HTML alt kumesine cevirir."""
    out_lines = []
    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            out_lines.append("")
            continue
        if s.startswith("> "):
            s = s[2:]
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            out_lines.append(f"<b>{html.escape(m.group(2))}</b>")
            continue
        if re.match(r"^\s*[-*]\s+", s):
            s = re.sub(r"^\s*[-*]\s+", "• ", s)
        if s.startswith("|") or set(s.strip()) <= {"-", "|", ":", " "}:
            continue  # tablolari Telegram'da atla (HTML dosyasinda var)

        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", s)
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        out_lines.append(s)

    text = "\n".join(out_lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _split(text: str, limit: int) -> list[str]:
    """Satir siniri koruyarak parcalar; tek satir bile uzunsa sert keser."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                chunks.append(cur); cur = ""
            chunks.append(line[:limit]); line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks
