# BIST / BUX Analysis Agent

A personal investment-analysis agent that runs entirely on a local machine.
It tracks a **BUX (ABN AMRO, EUR)** portfolio and **Binance crypto**, is wired
for **Midas (BIST, TRY)**, collects public market data, computes technical,
fundamental and event-study metrics deterministically, and uses Claude only to
*interpret* what was measured.

Data goes in through a **Telegram bot**, and so do the questions. The chat
model has real tools — it decides what to fetch, runs collectors, and can
stage portfolio writes for one-tap approval. No command vocabulary to learn.

It also runs **without being asked**: a scheduled pulse screens the universe,
puts the survivors past four independent agents, and writes every call to a
journal so its own hit rate can be measured rather than assumed.

```
phone screenshot ─┐
voice message  ───┼─→ Telegram bot ─→ SQLite ─→ Claude (interprets) ─→ answer
typed question ───┘         ↑
                    public collectors
        (SEC EDGAR · XBRL · KAP · Yahoo · Binance · CoinGecko · news)
```

---

# Türkçe özet — sistem nasıl çalışıyor

> Bu bölüm günlük kullanım için. Aşağıdaki İngilizce gövde ayrıntılı
> referans; ikisi aynı şeyi anlatıyor, biri kısa biri uzun.

## Üç parça

**1. Toplayıcılar** internetten veri çeker, SQLite'a yazar.
**2. Hesaplama** göstergeleri (RSI, ortalamalar, olay etkisi) koddan üretir.
**3. Claude** yalnızca **ölçülmüş olanı yorumlar.**

İlk ikisinde yapay zekâ **yok** — tamamen deterministik. Claude sayı
uydurmaz; her rakamı bir araçla veritabanından çeker. Elde yoksa "yok" der.

Veri içeri **Telegram'dan** girer: ekran görüntüsü, sesli mesaj ya da yazı.
Broker'ların web arayüzü olmadığı için portföy ekran görüntüsünden okunur;
hiçbir yerde broker şifresi durmaz.

## Bilgisayar açılınca ne çalışıyor

Açılışta **tek bir şey** başlar — bot. Diğer dördü saatinde tetiklenir.

| Servis | Ne zaman | Piyasa anı | Log |
|---|---|---|---|
| `com.alipala.finagent.bot` | **Açılışta, sürekli** | Telegram'ı dinler | `data/bot.log` |
| `com.alipala.finagent.sabah` | Hafta içi **08:00** | ABD/Asya gecesi kapandı, Avrupa açılmadı, kripto günlük barı kapandı | `data/pulse.log` |
| `com.alipala.finagent.ogle` | Hafta içi **12:30** | Avrupa + BIST seans ortası, ABD pre-market | `data/pulse.log` |
| `com.alipala.finagent.kapanis` | Hafta içi **17:45** | Euronext 17:30 ve BIST 17:00'de kapandı, ABD açık | `data/pulse.log` |
| `com.alipala.finagent.nabiz` | Hafta içi **22:15** | ABD kapandı (22:00) — günün en yoğun bilgi anı | `data/pulse.log` |

Saatler **Europe/Amsterdam** — launchd makinenin yerel saatini kullanıyor ve
makine Amsterdam'da (ölçüldü 2026-08-19: `date +%Z` → CEST).

Dördü de tek betikten çalışır: `scripts/run_kosu.sh <kip>`. **Hangi kip ne
toplar, ne kadar sürebilir, panel çalıştırır mı, kime gider** — hepsi
`config/settings.yaml → ritim.kipler`. Betikte kip adı geçmez; plist yalnızca
**saatin** tek doğruluk kaynağıdır ve bekçi onu okur.

Hepsi `launchd` altında (`~/Library/LaunchAgents/`). Bilgisayar kapalıysa o
koşu **atlanır**, sonra telafi edilmez — ama kaçırılan koşu Telegram'dan
bildirilir.

Bot çöktüğünde launchd onu geri başlatır. **Temiz durdurmada başlatmaz** —
`kill -TERM` atarsan kapalı kalır, bilinçli durdurma sayılır.

**Dördü de model çağırır** (panel: 4 ajan + hakem) ve tahminleri deftere yazar.
Ölçülen maliyet sahip başına ~4,7 dk; iki sahiple koşu başına ~9,4 dk, günde
~38 dk. Bir kipi LLM'siz koşturmak için `ritim.kipler.<kip>.panel: false`.

### Koşu ölü kalırsa ne olur

Üç kademe var ve üçü de **konuşur**:

1. **Collector'ın kendi bütçesi** — `isyatirim` 780 sn, `tuik` 300 sn. Dolunca
   düzgünce durur, `partial` döner, kesileni **adıyla** raporlar.
2. **Kabuk duvar saati** (`kabuk_butce_sn`) — aşılırsa süreç grubu öldürülür
   **ama önce Telegram'a haber verilir**. (2026-08-19'da bu sessizdi ve o gece
   nabız kayboldu; kimse fark etmedi.)
3. **Bekçi** — koşu kendi izini bırakmadıysa (`data/bot/kosu/<kip>.json`) bot
   bunu Telegram'dan söyler. Gözetilen kip listesi `ritim.kipler`'den türer.

> `scripts/run_daily.sh` ve `run_hourly_crypto.sh` **zamanlanmış değil** —
> nabızdan önceki dönemden kalma, elle çalıştırmalık.

## Ne zaman terminale ihtiyacın var

Normal kullanımda **hiç**. Telegram yeter. Şunlar için gerekir:

```bash
cd ~/github/bist-bux-agent

# Kod değiştiyse botu yenile (~45 sn — launchd 60 sn'den sık başlatmıyor)
launchctl kickstart -k gui/$UID/com.alipala.finagent.bot

# Ne oluyor?
tail -f data/bot.log            # bot
tail -f data/pulse.log          # zamanlı koşular
.venv/bin/python run.py status  # veritabanı özeti

# Bir koşuyu şimdi çalıştır (saatini bekleme)
launchctl kickstart -p gui/$UID/com.alipala.finagent.nabiz
#   sabah | ogle | kapanis | nabiz

# Testler (~2 dk)
.venv/bin/python tests/test_smoke.py
```

**Kod değiştirdiysen botu mutlaka yeniden başlat.** Çalışan süreç dosyayı
bir kez okur; yeniden başlatmadan değişiklik geçmez. Bu bir kez sahada
sorun çıkardı.

## Kimler kullanıyor

Sistem **çok kullanıcılı**. Her kişinin kendi portföyü, kendi nabzı, kendi
sohbet arşivi var; piyasa verisi ortak toplanır. Kişi eklemek:
`config/settings.yaml` → `telegram.sahipler` altına `"<chat_id>": ad`, sonra
restart. Göç gerekmez.

`chat_id`'yi bulmak için kişi bota yazsın, sonra:

```bash
grep "Yetkisiz sohbet reddedildi" data/bot.log | tail
```

> `run.py telegram-chatid` bu durumda **işe yaramaz** — çalışan bot mesajı
> çoktan tüketmiş olur.

---

# Yeni bir Claude sohbetinde ne demeli

Bu klasörde yeni bir Claude Code sohbeti açtığında **proje hafızası
otomatik yükleniyor** (`~/.claude/projects/-Users-alipala-github-bist-bux-agent/memory/`).
Yani her şeyi baştan anlatman gerekmiyor. Şunu söylemen yeter:

> Bu `~/github/bist-bux-agent` projesi. MEMORY.md'yi ve `siradaki-is`
> hafızasını oku, sonra durumu bana özetle. Kod değiştirirsen
> `tests/test_smoke.py` koştur ve botu `launchctl kickstart -k
> gui/$UID/com.alipala.finagent.bot` ile yeniden başlat.

Ajanın bilmesi gereken ve **kolayca yanlış yapacağı** dört şey:

1. **Sayı uydurmak yasak.** Bir şeyin "yok" olduğunu söylemeden önce
   veritabanına bakılır. Bu projenin en kötü hata sınıfı, veri varken
   "yok" demek — üç kez yaşandı, `yanlis-yok-beyani` hafızasında.
2. **Beyan ile gerçek ayrışır.** Elle yazılan liste (araç listesi, komut
   listesi, kaynak listesi) mutlaka çürür. Yeni bir liste yazmak yerine
   **koddan üret** ve testle zorunlu tut.
3. **Testler gerçek iş yapmamalı.** Bir kez bir test gerçekten collector
   çalıştırdı ve kullanıcıya Telegram raporu gönderdi. İkinci vaka daha
   sessizdi: `run.py` alt süreci başlatan bir test `DB_PATH` geçirmediği
   için **canlı veritabanını göç ettirdi** (20 Ağu 12:26, dört kez) —
   çalışan bot eski kodla kalıp yazamaz hâle geldi. `run.py` alt süreci
   artık yalnızca `_run_py()` üzerinden başlar ve bu statik testle
   zorunlu (`test_alt_surec_CANLI_DB_ye_dokunmuyor`).
4. **`.env` asla commit edilmez, içeriği asla paylaşılmaz.**

Ayrıntılı gerekçeler `docs/` altında ve hafızada.

---

## Table of contents

1. [Why it is built this way](#1-why-it-is-built-this-way)
2. [Setup from scratch](#2-setup-from-scratch)
3. [Running and restarting the backend](#3-running-and-restarting-the-backend)
4. [Telegram bot commands](#4-telegram-bot-commands)
5. [CLI commands](#5-cli-commands)
6. [Data layers](#6-data-layers)
7. [How the analysis works](#7-how-the-analysis-works)
   - [The proactive loop](#the-proactive-loop) · [The prediction journal](#the-prediction-journal)
8. [Architecture](#8-architecture)
9. [Testing](#9-testing)
10. [Troubleshooting](#10-troubleshooting)
11. [Known limits](#11-known-limits)

---

## 1. Why it is built this way

### The constraint that shaped everything

**BUX and Midas have no web interface.** Verified 2026-08-15: `app.getbux.com`
does not resolve in DNS, `getmidas.com/giris` returns 404, and every call to
action points at the App Store. Both are mobile-only.

The original design — "log in with a browser and scrape the portfolio" — could
never have worked. So portfolio data arrives as a **screenshot sent to the
Telegram bot**, read by Claude vision. No broker password is stored anywhere,
in any form.

Browser automation still exists, but only for **public, login-free** sources:
KAP, the BUX ETF catalog, index membership, and resolving news redirect links.

### The agent loop

The chat layer used to be `allowed_tools=[], max_turns=1` — a single shot
over a context package chosen in advance by regex-matching instrument names
in the question. Three failures followed directly from that shape, all of
them observed in use:

- **It could not act.** "Add this to my portfolio" got "I have no write
  permission." Correct, and useless.
- **It stated falsehoods about our own database.** A message with no
  detectable ticker ("USDT not TRY") produced an empty context, and the
  model concluded "I have no coin data to look at" while 17,180 bars sat in
  the table. Silence from a missing retrieval is indistinguishable, to the
  model, from silence from missing data.
- **It could not follow up.** One missing number ended the turn.

Now the model calls tools and decides for itself what it needs. Reads are
free; **writes are staged, never direct** — `pozisyon_kaydet` drops a file in
`pending/` and the bot attaches Save/Cancel buttons, so the human-approval
guarantee holds while costing one tap. The prompt forbids saying "saved";
it must say "submitted for your approval".

| Tool | Purpose |
|---|---|
| `veri_durumu` | What exists in the DB — required before claiming anything is missing |
| `portfoy` | Positions, weights, per-account currency |
| `ara` · `kimlik` | Find an instrument; check how its identity was verified |
| `teknik` · `saatlik` · `fiyat_serisi` | Daily indicators, hourly series, raw closes |
| `tokenomik` · `finansallar` | Crypto supply/valuation; equity XBRL |
| `haberler` · `olay_etkisi` | Tiered sources; event study |
| `pozisyon_kaydet` | **Stages** a portfolio write for approval |
| `izlemeye_al` · `veri_topla` | Track a symbol; run collectors |

`veri_topla` runs light collectors in-process and browser-based ones
(`prices`, `stocknews`, `kap`, `bux`, `bist`) as a **subprocess**. Playwright
is deliberately kept out of the bot process — a crash there would take the
listener down with it — and the subprocess also gets a timeout, so a hung
page cannot stall the conversation. SQLite runs in WAL with a 30-second busy
timeout, which is what makes concurrent writes from several processes safe.

### One listener, N workers

The listener used to do the work itself, so a single turn blocked everyone:
measured on 2026-08-17, one `isyatirim` call inside a chat turn took **24.9
minutes** and the second user was blocked for all of it. Now the listener
only polls, authorises and **hands out work**; each heavy job (chat, image,
voice, approval, report) runs as its own `run.py bot-worker` process
(`src/finagent/bot/kuyruk.py`).

Processes, not threads, for three measured reasons: the SQLite connection is
`check_same_thread`-bound, a thread cannot be killed (so a hung turn stays
hung), and a hard crash in a thread takes the whole bot with it. Cold start
of a worker is 0.35 s — every turn already spawns a `claude` CLI subprocess.

Four rules make it safe:

- **One job at a time per chat.** Chat history and the 20-minute screenshot
  merge window assume a single writer; different chats run in parallel
  (`telegram.worker_sayisi`, default 2).
- **The worker sends its own reply.** So a planned restart neither kills nor
  duplicates in-flight work — the restarted listener sees the heartbeat file
  and adopts the job instead of re-running it.
- **Retry only on hard crash.** Exceptions are already reported to the user
  and count as finished (`.bitti`); a timeout is a result too, so it is
  reported, not repeated (`telegram.is_zaman_asimi_dk`, default 15).
- **Read-only, LLM-free commands stay inline** (`/yardim`, `/rehber`,
  `/bekleyen` and guide buttons), so menu navigation never waits behind an
  analysis. Everything else defaults to the queue — misclassifying a new
  command costs latency, never correctness.

Tools return errors *as data* (`{"hata": ..., "ipucu": ...}`) rather than
returning nothing. An empty result and an unasked question look identical
from inside the model, and the second one invites invention.

### Design decisions

| Decision | Reason |
|---|---|
| **Deterministic layer separate from the LLM layer** | RSI/SMA/CAR/ratios are computed in pandas and plain Python. Claude never calculates them — it reads the numbers. If the LLM is unavailable, reports are still produced. |
| **Screenshots read twice, independently** | A single vision pass silently mis-read a row once (`+42.42%` reported as the next row's `+33.07%`). Two independent passes are cross-checked and conflicts are surfaced instead of averaged. |
| **Nothing is written without approval** | Both paths — parsed screenshots and model-initiated writes — stage to `pending/` and wait for an explicit tap. The model can prepare a write; it cannot commit one. |
| **Images go straight to the agent** | The old flow converted a screenshot to text, then fed the text to a separate chat call, so the model never saw the image and could not act on it. Now it opens the file itself with `Read` and can chain straight into a tool call. |
| **Source tiering (allowlist, not blocklist)** | Roughly 80% of raw instrument news is content-farm noise. Tier 1 = the company's or regulator's own statement (SEC, KAP, company newsroom). Tier 2 = wire and financial press (Reuters, Bloomberg, CNBC, WSJ). Tier 3–4 = aggregators/promotional — **never** used as evidence for a factual claim. |
| **Identity must match on name, not just ticker** | A guessed ticker once pulled 500 days of *Avalo Therapeutics* prices for *Avantium*. Wrong data is more dangerous than missing data: every indicator computes cleanly and every one of them is wrong. If the company name does not match, the instrument is marked `eslesmedi` and **no** collector touches it. |
| **`<untrusted_data>` isolation** | Text scraped from the web is passed inside a tagged block, and the system prompt forbids following instructions found inside it. |
| **The vision session is locked to one directory** | A screenshot is outside data too — it can carry text aimed at the model. That session used to run under `permission_mode="bypassPermissions"`, and it was measured on 2026-08-21 that the model really could run **Bash** there (`echo` wrote a file) while `can_use_tool` was never consulted. The mode is gone. The gate that actually works is a `PreToolUse` hook: `Read` is confined to the directory of the image being read, every other tool is refused, and each refusal is logged — a poisoned screenshot leaves a trace. Verified live: attempts to read outside the directory and to run Bash were both denied while the intended image still parsed. |
| **Voice transcribed locally** | whisper.cpp runs on-device; audio never leaves the machine. `/sil` and `/unut` cannot be triggered by voice — one misheard word should not delete data. |
| **No order execution, ever** | The agent never places a trade. `config/settings.yaml → risk.allow_order_execution: false`. |
| **Claude Max subscription, not API key** | `analysis.llm.auth: abonelik` removes `ANTHROPIC_API_KEY` from the process environment, because the key takes precedence in the Claude Code credential chain and would silently bill per token. |

---

## 2. Setup from scratch

Assumes macOS with Homebrew. Python 3.12–3.14 is supported (3.14 in use).

### 2.1 Get the code and create the virtual environment

```bash
git clone https://github.com/alipala/bist-bux-agent.git
cd bist-bux-agent

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

> Always call `.venv/bin/python` explicitly rather than activating.
> A shell alias such as `alias python=/usr/bin/python3` takes precedence over
> `PATH`, so `source .venv/bin/activate` does not always win, and you get
> `ModuleNotFoundError` for packages that `pip list` clearly shows.

### 2.2 Create the Telegram bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Send your new bot any message (it will not reply yet).
3. Find your chat ID:

```bash
.venv/bin/python run.py telegram-chatid
```

> If this returns nothing, send the bot a **new** message and run it again —
> Telegram only retains updates for about 24 hours.

### 2.3 Configure `.env`

```bash
cp .env.example .env
```

Fill in **only** these:

```ini
TELEGRAM_BOT_TOKEN=123456:AA...
TELEGRAM_CHAT_ID=5643817523
```

Leave `ANTHROPIC_API_KEY` **empty**. The default auth mode
(`config/settings.yaml → analysis.llm.auth: abonelik`) uses the Claude Max
subscription through the Claude Code CLI. If the key is set, it shadows the
subscription and every call is billed per token.

**No broker password goes in this file, or anywhere else.**

### 2.4 Voice messages (optional but recommended)

```bash
brew install whisper-cpp
mkdir -p data/models
curl -L -o data/models/ggml-small.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin
```

Roughly 480 MB. Turn it off with `voice.enabled: false` in
`config/settings.yaml` if you do not want it.

### 2.5 Initialise the database and verify

```bash
.venv/bin/python run.py init-db
.venv/bin/python run.py telegram-test     # expect a message in Telegram
.venv/bin/python run.py status
```

### 2.6 First data collection

```bash
.venv/bin/python run.py collect --site indices bist news
```

This populates the instrument catalog (~1,600 instruments) with no login and
no API key. Price, XBRL, EDGAR and per-instrument news collection are driven
by the watchlist, which fills up once you send your first portfolio screenshot.

---

## 3. Running and restarting the backend

The backend is the Telegram listener. **Directory:** `~/github/bist-bux-agent`,
**script:** `run.py`, **subcommand:** `bot`.

### Foreground (recommended while working — logs are live, `Ctrl+C` stops it)

```bash
cd ~/github/bist-bux-agent
.venv/bin/python run.py bot
```

### Background

```bash
cd ~/github/bist-bux-agent
nohup .venv/bin/python run.py bot > data/bot.log 2>&1 &
```

### Managing the process

```bash
pgrep -fl "run.py bot"      # is it running?
tail -f data/bot.log        # follow the log
pkill -f "run.py bot"       # stop it
```

### Under launchd (how it actually runs)

Once `scripts/launchd_install.sh` has run, the bot is a managed service and
starts itself on login and after a crash. Manage it with:

```bash
launchctl print gui/$UID/com.alipala.finagent.bot | head -20   # status
launchctl kickstart -k gui/$UID/com.alipala.finagent.bot       # restart
launchctl bootout   gui/$UID/com.alipala.finagent.bot          # stop
launchctl kickstart -p gui/$UID/com.alipala.finagent.nabiz     # run tonight's pulse now
```

> Starting a second copy by hand is safe: the lock refuses it and tells you
> which PID holds it. Stop the service first if you want to run in the
> foreground.

When it starts, the bot sends `🟢 Agent dinlemede.` to your chat. That message
is the confirmation that it is up — if it does not arrive, check `data/bot.log`.

---

## 4. Telegram bot commands

Anything that is not a command is treated as a question and answered from the
database. Voice messages work the same way and are transcribed locally; the
transcript is echoed back so you can see what was heard.

### Sending a screenshot — two modes

| What you send | What happens |
|---|---|
| Screenshot with **no caption** | **SAVE.** The screen is classified (portfolio vs. list), read twice, and held for approval. Nothing is written until `/onayla`. |
| Screenshot **with a question in the caption** | **ASK.** The screen is read and answered against the database. Nothing is saved. |

Screenshots sent to the same account within **20 minutes** merge into a single
snapshot — that is how a portfolio spanning several screens is captured. Send
them back to back, then `/onayla` once.

> Choose "send as file" in Telegram to skip compression; numbers read more
> reliably.

### Commands

| Command | What it does |
|---|---|
| `/rehber` | **Browsable capability guide** — buttons per topic; the tool list is generated from code, not hand-written |
| `/onayla` · `/hepsi` | Save all pending screenshot readings |
| `/bekleyen` | How many readings await approval |
| `/rapor` | Collect data + full Claude analysis + report (~3 min) |
| `/ozet` | Summary from existing data, no collection (fast) |
| `/portfoy` | Saved positions |
| `/takip` | Research targets and identity-resolution status |
| `/kimlik NAME = TICKER` | Manually assign an unresolved identity (verified against SEC; manual assignments are sticky and survive re-runs) |
| `/evren [index\|keyword]` | Search the BUX catalog (661 stocks + 210 ETFs) |
| `/aday SYMBOL` | Promote a catalog instrument into research scope |
| `/haber [SYMBOL]` | Run a source scan, or show one instrument's sources |
| `/etki SYMBOL` | Event study: abnormal return on news days |
| `/durum` | Database status |
| `/sil` | Undo the last saved portfolio snapshot |
| `/temizle [days]` | Delete downloaded media and old DB rows (media contains portfolio screenshots) |
| `/unut` | Clear the model's working memory — the permanent archive is kept |
| `/unut arsiv` | Also purge the permanent chat archive (irreversible) |
| `/yardim` | Help |

**Two chat records, on purpose.** The *working window* the model sees is
deliberately narrow — last 8 turns, nothing older than 6 hours — because
Telegram's "Clear Messages" is client-side only and the bot is never told;
replaying old turns would make the model answer as if continuing a
conversation the user can no longer see. That window used to be the only
persistent record, so anything past 8 turns was silently discarded. The
`sohbet_kaydi` table is now the durable one: append-only, full text, never
pruned. The model reaches it explicitly through the `sohbet_arsivi` tool,
which labels its output as *what was said*, not *what is true*.

**Archive search is an FTS5 trigram index**, not `LIKE`. The old path made
the *entire* query one `LIKE '%…%'` pattern, so 13 of 15 natural questions
returned zero rows — no such string as "altın hesabı kaç TL" exists in the
archive — and diacritics decided everything (`altın` → 20 rows, `altin` →
1). Trigram tokenisation handles Turkish suffixes (`altın`/`altını`), and
index and query pass through the *same* `leksik()` function, registered as
a SQLite UDF so the triggers and the search cannot drift apart. Measured on
a frozen 15-query golden set (`tests/altin_kume.json`), recall@3 went from
10% to 66.7%, and accented and unaccented spellings now score identically.

A local embedding layer (`embeddinggemma` over Ollama) was built, measured
and **left switched off** — see `arama.gomme` in `config/settings.yaml` for
the numbers and the reason. Re-measure with
`.venv/bin/python scripts/arama_olc.py like fts5 gomme hibrit`.

---

## 5. CLI commands

All are run as `.venv/bin/python run.py <command>`.

| Command | What it does |
|---|---|
| `init-db` | Create/migrate the SQLite schema |
| `bot` | **Start the Telegram listener (the backend)** |
| `nabiz` | **The proactive loop**: screen → agent panel → arbiter → Telegram |
| `nabiz --karne` | Hit-rate scorecard for past predictions |
| `nabiz --no-panel` | Deterministic screen only, no LLM |
| `nabiz --kip {sabah,ogle,nabiz}` | `sabah`/`ogle` = light LLM-free run; `nabiz` = full panel |
| `nabiz --no-notify` | Do not send to Telegram |
| `status` | Database summary + recent collector runs |
| `yedek [--zorla]` | Back up the database (`VACUUM INTO` + verification). Runs automatically at the start of every scheduled run; does the work once a day. |
| `collect [--site ...] [--headless]` | Run collectors |
| `analyze [--no-llm]` | Print analysis to the console |
| `report [--no-llm]` | Write `.md` + `.html` into `reports/` |
| `daily [--skip-collect] [--no-llm] [--no-notify]` | Full cycle + Telegram delivery |
| `telegram-chatid` | List chats that messaged the bot (find `TELEGRAM_CHAT_ID`) |
| `telegram-test` | Send a test notification |
| `discover --site X [--url ...]` | Dump DOM and propose selectors |
| `login --site {bux,midas}` | Legacy manual-login flow — **not usable**, both brokers are mobile-only |

Collector names for `--site` (23; the authoritative list is
`finagent.collectors.REGISTRY`, and `finagent.collectors.KAPSAM` says what
each one refreshes — a smoke test keeps both in sync):

`alphavantage`, `binance`, `bist`, `bistgecmis`, `bux`, `cgfiyat`,
`coingecko`, `edgar`, `indices`, `isyatirim`, `kap`, `kripto`, `kriptoevren`,
`makro`, `midas`, `midasbilanco`, `news`, `prices`, `saatlik`, `stocknews`,
`takvim`, `tiingo`, `tuik`, `xbrl`.

**Pick the right one.** `prices` pulls Yahoo and **does not cover BIST**;
BIST closes come from `isyatirim` alone. `bistgecmis` is the *depth*
companion, not a replacement: it pulls the same BIST names from Yahoo with
the `.IS` suffix under a separate source name (`yahoo_bist`), because
`isyatirim` only reaches ~13.5 months — a single macro regime, too short to
backtest. Measured 2026-08-20: 595,713 bars for 365 symbols on the first
run, and the two sources agree to **0.0000%** on overlapping days.
`isyatirim` stays primary; its side-products (share count, daily TRY volume,
XU100) exist nowhere else. `makro` is the macro/closing panel
(indices, gold/silver/oil, FX, DXY, US10Y, VIX) — none of that existed
before and its absence, not the prompt, is why daily notes had no world or
Turkey macro picture. Getting this wrong once cost three
messages of confidently wrong diagnosis — which is why the chat tool now
generates its source list from the registry instead of carrying a copy.

**Crypto ordering matters:** `kripto` (identity) must run before `binance`
and `coingecko`; both silently skip any symbol whose identity is not
`dogrulandi`.

### Scheduled runs

Everything scheduled runs under **launchd**; the crontab is empty and is not
used. Install/remove with:

```bash
scripts/launchd_install.sh      # idempotent
scripts/launchd_uninstall.sh    # removes services, leaves data alone
```

Four user agents land in `~/Library/LaunchAgents`. Collection is **not** a
separate job — each scheduled run collects what it needs first, then analyses:

| Service | Trigger | Market moment | Then |
|---|---|---|---|
| `…bot` | `RunAtLoad`, always on | — | Listens to Telegram |
| `…sabah` | weekdays 08:00 | US/Asia closed, Europe not open, crypto daily bar closed | `run_kosu.sh sabah` |
| `…ogle` | weekdays 12:30 | Europe + BIST mid-session, US pre-market | `run_kosu.sh ogle` |
| `…kapanis` | weekdays 17:45 | Euronext 17:30 and BIST 17:00 closed, US open | `run_kosu.sh kapanis` |
| `…nabiz` | weekdays 22:15 | US closed (22:00) — densest information moment | `run_kosu.sh nabiz` |

Times are **Europe/Amsterdam** — launchd uses the machine's local time and the
machine is in Amsterdam (measured 2026-08-19: `date +%Z` → CEST).

**What each run collects, how long it may take, whether it runs the panel and
who receives it all live in `config/settings.yaml → ritim.kipler`.** The shell
script contains no mode names; the plist is the single source of truth for the
**time** only, and the watchdog reads it. Adding a fifth run is a settings
change plus a plist — no code change.

All four call the model (four agents + an arbiter) and write predictions to the
journal. Measured cost: ~4.7 min per owner, ~9.4 min per run with two owners.
Set `ritim.kipler.<mode>.panel: false` to run one deterministically.

> `scripts/run_daily.sh` and `scripts/run_hourly_crypto.sh` are **not
> scheduled**. They predate the pulse and remain for manual use; the README
> used to tell you to add crontab entries for them, which contradicted the
> launchd setup directly below. Run them by hand or ignore them.

launchd rather than cron plus `nohup`, for three concrete reasons:

- **A `nohup` process does not survive a reboot.** After an update or a panic
  the bot is simply gone — the pulse still messages you, but you cannot reply.
- **cron skips a job if the machine was asleep at that minute.** launchd runs
  the missed job on wake.
- **cron does not restart anything that crashed.** `KeepAlive` does. Verified:
  `kill -9` on the bot brought a new process back in about 40 seconds.

They must be **user agents, not daemons**. The Claude Max subscription lives in
the user's `~/.claude` profile; a daemon runs as root, cannot see it, and the
agent layer would fail silently.

`KeepAlive` is `{SuccessfulExit: false}` rather than plain `true`, so a clean
exit from a configuration error does not spin forever, and `ThrottleInterval`
is 60s so a broken `.env` cannot flood the log. The pulse sets no `KeepAlive`
and no `RunAtLoad` — a scheduled job should run when scheduled and then stop.

**Every budget in `ritim.kipler` is measured, not estimated.** That
distinction is not pedantic: on 2026-08-19 the 22:15 run was killed at its
2700-second limit because the budget had been inherited from a three-day-old
measurement while the collection chain had grown to 43.9 minutes — TUIK alone
ate 20.4 of them in timeouts. No notification went out, no run trace was
written, and the watchdog reported nothing wrong.

Three independent guards now exist, and **all three speak**:

1. **Per-collector budget** — `isyatirim` 780 s, `tuik` 300 s. When it fills,
   the collector stops cleanly, returns `partial` and names what it dropped.
   The request timeout is clamped to the remaining budget, so one hung call
   cannot overshoot. Series are fetched **stalest-first** so the truncated
   tail rotates instead of starving the same series every run.
2. **Shell wall clock** (`kabuk_butce_sn`) — on overrun the process group is
   killed, but **Telegram is notified first**. Ordering matters: the killer is
   inside the same process group.
3. **Watchdog** — if a run leaves no trace in `data/bot/kosu/<mode>.json`, the
   bot says so. The watched mode list is derived from `ritim.kipler`, and the
   grace period from each mode's own `kabuk_butce_sn`.

`ExitTimeOut` stays small: it is *not* a run-time limit (a common
misreading) — it is what launchd allows between SIGTERM and SIGKILL when
*stopping* a job. Collection is tolerated with `|| true`, and if the browser
session itself cannot start, the browser-free collectors still run.

Weekends are excluded on purpose: the markets are shut, so there is no new
close to screen. Crypto trades through the weekend, but its hourly collection
runs separately.

> Verify a scheduled job in a bare environment before trusting it. `env -i
> HOME=$HOME PATH=/usr/bin:/bin` reproduces roughly what the scheduler gives
> you — enough to catch a missing `.env` or an unavailable credential on the
> day you install it rather than on the first scheduled run.

### The backup

Everything the system knows sits in one SQLite file, and most of it
cannot be rebuilt: the prediction journal (what we thought *before* the
outcome), the chat archive, months of collected history, and portfolio
snapshots read off screenshots. Until 2026-08-21 there was no backup at
all.

**`VACUUM INTO`, never `cp` — that is a measurement, not a preference.**
The database runs in WAL mode. `cp` copies only the main file, so
transactions still sitting in the write-ahead log are silently missing
from the copy. Measured in isolation:

```
live  : [1, 2, 3]
cp    : [1]          <- two committed transactions gone
VACUUM: [1, 2, 3]
```

It happened for real the same day: a `cp` snapshot of the live database
was missing a thesis-break stamp because the write was still in the WAL.
`cp` is right *sometimes* — whenever the log happens to be empty — which
is the worst kind of wrong: you think you have a backup and only find out
when you try to restore it.

So the backup is written with `VACUUM INTO`, then **opened and checked**
(`quick_check` plus a row-count comparison against the source) before it
is allowed to take its final name. A backup nobody verified is not a
backup. Measured: 120.8 MB, ~1 second.

Every scheduled run backs up **before** collecting, so a crashed
collector or a filled budget still leaves today's data safe. The work
happens once a day — later runs see today's file and skip in
milliseconds — which means four chances daily to catch a machine that
was asleep at 17:45.

Two things guard the silence. The shell reports a failed backup to
Telegram, and the watchdog carries a sixth check: if the newest backup is
two days old it says so. That covers the failure the whole feature exists
for — a backup that quietly *stopped* (setting flipped, directory moved,
runs never fired) leaves you believing you are covered.

Retention is `yedek.gun` days and **the newest file is never pruned**: if
the machine is off for a week every backup becomes "old", and a naive
sweep would delete the last copy standing.

By default backups land in `data/yedek` — the **same disk**. That
protects against accidental deletion and corruption, not against drive
failure. Point `yedek.dizin` at a cloud folder (iCloud Drive, Dropbox)
for real disaster recovery. That is deliberately not the default:
portfolio data would leave the machine, and that has to be the owner's
decision.

### Knowing when it was down

A system that has crashed cannot report that it crashed. That limit is real
and the design admits it rather than pretending otherwise:

| Situation | Told immediately? |
|---|---|
| Bot crashed, machine up, network fine | Yes — `KeepAlive` restarts it and it reports the gap on the way back |
| Pulse failed or produced nothing | Yes — the bot is a separate process and still running |
| Network dropped | No — but the outage is reported when it returns |
| Machine off or asleep | No — reported on next boot |
| Machine off, want to know *now* | Only via an external watcher |

The bot writes a heartbeat each poll cycle. On startup it compares that mark
against the clock: a gap under ten minutes is a restart or a deploy and stays
quiet, anything longer is reported with its duration. It distinguishes *the
bot was dead* from *the network was gone* — during a network outage the
heartbeat advances but the online mark does not, so the two look different on
disk and the user gets the accurate one.

The heartbeat is written **immediately at startup**, not on the first
successful poll. Long-polling blocks for up to 50 seconds, so a bot crashing
inside that window would never refresh the mark and every restart would
re-report the original outage. That was found by testing, not by reasoning.

Notifications are throttled per kind for six hours. With `ThrottleInterval` at
60s, a bot failing on a bad `.env` would otherwise send a message every minute.

The bot also watches the scheduled jobs it does not control — **all four of
them**, via each run's own trace file. **Silent failure of a scheduled job is
the failure mode that matters**, because nothing looks wrong.

The earlier check asked whether rows had appeared today in `signals` /
`panel_runs` / `collector_runs`. That measured whether a run had *started*,
not whether it *finished*, and it was wrong in both directions: it produced
four false alarms on 2026-08-18, and on 2026-08-19 it missed a real one — the
run died mid-panel but its collector rows were already in the table, so the
check said everything was fine. A run's own trace is written at the *end* of
`Nabiz.calistir`, so a half-finished run leaves none.

For the one case none of this covers — the machine being off — set
`HEARTBEAT_URL` to a dead man's switch endpoint (healthchecks.io's free tier
is enough). The bot pings it each cycle; when the pings stop, that service
alerts you. It is the only way to learn about an outage while it is happening.

**Only one bot may run at a time.** Two instances poll the same Telegram queue
and each message reaches a random one. The listener takes an exclusive
`flock` on `data/bot/bot.lock` and a second instance refuses to start, naming
the PID that holds it. `flock` rather than a PID file because the kernel
releases it when the process dies — no stale lock after the crash that
`KeepAlive` is there to recover from.

---

## 6. Data layers

Analysis quality is set by **data**, not by prompt wording. Current coverage:

| Layer | Source | Coverage (2026-08-15) | Collector |
|---|---|---|---|
| Crypto identity | Binance + CoinGecko | 10 verified of 12 | `kripto` |
| Crypto daily OHLCV | Binance klines | 999 bars each, 2.7 years | `binance` |
| Crypto hourly OHLCV | Binance klines | 719 bars each, 30 days | `binance` |
| **Stock hourly OHLCV** | Yahoo 60m | BIST (`.IS`, TRY) + US (USD), position ∪ watchlist; 38 symbols, 6,635 bars, 3 s | `saatlik` |
| Tokenomics | CoinGecko | market cap, supply, FDV, ATH | `coingecko` |
| Price OHLCV | Yahoo chart via browser | 10,413 bars, 2 years | `prices` |
| Fundamentals (XBRL) | SEC `companyfacts` | 5,221 facts, 11 companies, 5 years | `xbrl` |
| Regulatory filings | SEC EDGAR | form + date + URL (no body) | `edgar` |
| Turkish disclosures | KAP | tier 1 for BIST | `kap` |
| Press | Google News, tiered | 745 items, ~20% usable as evidence | `stocknews` |
| News topic axis | rule-based classifier | macro-TR / macro-global / commodity / geopolitics / company | `research/konular.py` |
| Catalog | indices + KAP + BUX | 1,600 instruments | `indices`, `bist`, `bux` |
| BIST prices | İş Yatırım | 2,840 bars | `isyatirim` |
| FX rates | Alpha Vantage (Tiingo fallback) | EUR/USD, USD/TRY daily | `alphavantage`, `tiingo` |
| European quotes | Yahoo `.AS` (AV fallback) | ASML/ADYEN/INGA in EUR, **fresh** | `prices` |
| Macro / closing panel | Yahoo chart via browser | 7 indices, 8 commodities, 4 FX, US10Y, VIX | `makro` |
| Economic calendar | Fed + TCMB (plain HTTP) | FOMC + PPK/inflation-report dates; TÜİK & BLS blocked, **re-probed every run** | `takvim` |
| Turkish macro | TÜİK SDMX 2.1 (API key) | Yİ-PPI 1982→, unemployment 2005→, economic confidence · **catalog of 408 dataflows** | `tuik` |
| Gram gold parity (TRY) | derived: spot proxy × USD/TRY | labelled derived; **excludes domestic premium** | `makro` |
| Shares outstanding | Alpha Vantage `OVERVIEW` | rotating, US listings | `alphavantage` |
| Crypto news | Alpha Vantage `NEWS_SENTIMENT` | rotating, majors only | `alphavantage` |
| Event impact | prices + tier 1–2 news | AR / CAR / t-stat | `analysis/events.py` |

All of these are keyless except two: Alpha Vantage (free tier) and TÜİK
SDMX. **TÜİK takes an API key, not a password** — generated in the Data
Portal after SMS verification, revocable, and the officially documented
access path. Measured 2026-08-18: **CPI (TÜFE) is not published over
SDMX at all** — zero matches across all 408 dataflows in Turkish and
English names — so Yİ-PPI is the closest available leading indicator and
the config says so in a note the prompt must honour.

### Turkish financial statements

XBRL covers SEC filers only, so Turkish stocks had no fundamentals at all —
just point-in-time ratios from the Midas detail page. `midasbilanco` adds
the balance sheet and income statement. It needs a browser: the row labels
are in the HTML but the values arrive by JavaScript.

Two traps are recorded rather than assumed away.

**Period length.** Income-statement rows are cumulative year-to-date, so
2026-03 is three months, 2026-06 is six and 2025-12 is twelve. Taking "the
latest" and comparing it to "last year" compares six months against twelve
and reports an 84% collapse in profit — the same trap XBRL sprang once
before. Balance-sheet rows are instants and carry no period at all. Both are
stored: `days` is filled for income-statement rows and null for the balance
sheet. The page's default four columns are 6/3/12/9 months, all different
lengths and therefore mutually incomparable, so the collector drives the
period dropdowns to pick the latest period, **the same period a year
earlier**, and the last two full years.

**Unit.** The page reports in thousands of lira. Stored raw, equity of
1.02 trillion becomes 1.02 billion, and P/B computes as 416 instead of 0.42
— while "a billion lira of equity" looks entirely reasonable and nothing
raises an alarm. Values are normalised to lira.

What this buys, on THYAO: P/E of 3.79 looks like a bargain. Compared like
for like across 181 days, revenue grew 43% while operating profit went from
+24.5bn to **-5.1bn** — costs outrunning revenue — and the reported net
profit is entirely non-operating. In Turkey that is not an edge case:
inflation accounting has applied since 2023, so monetary gains and losses
sit inside net profit and are not operating performance. The ratio alone
cannot show this.

Coverage rotates ten symbols per run, so BIST 100 stays current in about ten
days. Balance sheets change quarterly; fetching all hundred daily would cost
17 minutes of the pulse budget to re-read numbers that had not moved. The
rotation keeps data fresh — it is the wrong tool for filling from empty, so
`scripts/bilanco_doldur.sh` does the initial backfill in one 20-minute pass.

Four of the hundred are not covered: insurance and some finance companies
publish a different statement structure with no summary table and
sector-specific line items. That is reported as "sector structure differs"
rather than folded in with failures, because a silent gap invites hunting
for a bug that is not there.

### Currency is part of the data, not an afterthought

A price series without a currency label caused the worst bug found so far.
Yahoo returns US-dollar quotes; the BUX portfolio is denominated in euro; the
two were used side by side with no conversion. Comparing each position's
screen value ÷ quantity against the stored series exposed it: **14 of 17
positions were off, most by ~15.7%, which is exactly the EUR/USD rate.**

For ASML and Adyen it was worse than a unit mismatch — those trade on
Euronext in euro, so the US series was the wrong instrument's price
altogether. Alpha Vantage's `ASML.AMS` returns 1579.60 EUR for 2026-08-14,
and the broker screen implies 2424.20 ÷ 1.534692 = **1579.60**. Exact.

Three changes followed:

- `prices` carries a `currency` column, and `upsert_prices` takes one.
- `db.fiyat_kaynagi()` picks **one** source per instrument — preferring the
  one whose currency matches the position — and `db.fiyat_serisi()` is the
  only sanctioned way to read a series. Querying `prices` directly can mix
  EUR and USD rows for the same symbol and produce indicators computed
  across two currencies.
- The `teknik` tool states the currency in every response, and the prompt
  forbids combining currencies without calling the `fx` tool.

### A bare ticker is not an identifier

RBOT was the second instance of this failure, after AVTX. Its identity was
correctly marked `fon` (iShares Automation & Robotics), but the price
collector fell through to using the raw catalog symbol, and on Yahoo `RBOT`
is **Vicarious Surgical** — a different company trading at $0.06 while the
ETF on the screen was €19.01. 500 bars of the wrong company were stored and
every indicator computed cleanly from them.

The rule is now: a symbol is accepted only if it carries an exchange suffix
(`ABN.AS` names exactly one listing) or resolves to a verified SEC ticker.
A bare, unverified symbol is refused. Missing series beat wrong series.

### Two data-integrity rules worth knowing

**XBRL periods.** The same concept appears with several period lengths in one
filing. A 90-day quarter and a 363-day year sit side by side. Comparing across
lengths produces nonsense, so `finansal_seri()` filters by day-band
(`yillik` 350–380d, `ceyrek` 80–100d, `anlik` for instants), and the system
prompt requires the model to state which periods it compared.

**News identity is content, not URL.** The same article arrives once with a
`news.google.com` redirect and again with the resolved publisher link. Keying
on URL created two rows, and the event study counted the same event twice.
The key is now `sha1(normalised title + publication day)`; the resolved link
overwrites the redirect. The migration collapsed 850 rows to 745.

---

## 7. How the analysis works

### Technical

SMA/EMA/RSI/returns/volatility/volume ratio are computed in pandas from the
stored series and handed to the model as **finished numbers**. The prompt
forbids recomputing them and requires naming the indicator behind any level
that is quoted.

### Fundamental

Ratios (gross/operating/net margin, ROE, debt/equity, P/E) are derived from
XBRL. Two rules matter:

- **Shown arithmetic must reconcile.** In testing, the model computed NVDA's
  TTM EPS correctly (6.53) but wrote steps beside it that did not produce that
  number. A correct figure with unverifiable working is worse than no working
  at all. The prompt now requires the steps to add up, and gives the explicit
  TTM formula: *full year + new quarter − same quarter last year*.
- **Refusals are successes.** "Share count not in the data, so no market cap"
  and "no FX series, so this is USD/USD" are the intended behaviour.

### Crypto — different rules, deliberately

Crypto is **not** analysed like equity, and the system prompt says so
explicitly. A coin has no revenue, earnings or equity, so P/E, margins, ROE
and debt/equity are **undefined**. Asking for them gets "undefined for
crypto", not a fabricated number.

What replaces fundamentals is **tokenomics**, kept in a separate context field
from `finansallar` so the model cannot confuse the two:

- circulating / total supply → how much locked supply is still to unlock
- FDV / market cap → the size of that future supply pressure
- volume / market cap → liquidity; thin books move on small trades

Two structural rules:

- **Hourly and daily bars live in different tables.** No query against
  `prices` filters on `source`, and every one of them assumes daily bars.
  Mixing hourly rows in would corrupt RSI, SMA and the event study silently.
  The prompt also forbids deriving daily indicators from the hourly series.
- **Symbol collisions are the norm, not the exception.** `/coins/list` was
  tried first and measurably picked wrapped clones — `ADA → binance-peg-cardano`,
  `ETH → bridged-binance-peg-ethereum-opbnb`, `BNB → anubis-bridged-bnb`.
  Their market caps are a fraction of the real coin's, so every derived figure
  would have been wrong but plausible. The authority is now `/coins/markets`,
  which returns the canonical coin per symbol, and the name check still runs
  on top of it.

Price precision is scaled to the asset. A fixed 2 decimals collapsed ROSE
(0.0055 USD) so that close and all three moving averages read `0.01`, and
because the trend comparison used those rounded values it was forced to
"sideways". Both are fixed: ~6 significant digits for display, raw values for
comparison.

### The proactive loop

`run.py nabiz` runs without being asked, on a schedule
(`scripts/run_kosu.sh nabiz`, weekdays after the US close):

```
screener (deterministic, no LLM)  →  panel (4 agents, parallel)  →  arbiter
     │                                      │                          │
  signals                            predictions                  Telegram
```

The universe is the whole Turkish market by catalogue — 729 instruments —
but only the liquid half is screened. Measured across 625 BIST stocks,
median daily turnover is 33M TRY; the 50M threshold keeps 247 of them.
Below that a stock trades a handful of times a day, and a 5% move is not
information, it is the footprint of a single order. Anything held or
watch-listed is screened regardless of volume — ignoring something you own
because it is illiquid would be the wrong kind of tidy.

History is fetched incrementally. Re-pulling 410 days for every symbol took
17m52s across 253 symbols, which would have exceeded the pulse's 20-minute
timeout and had launchd kill the job. Fetching only since the last stored
bar, with five days of overlap for corrections, takes 3m14s. The full pulse
now measures 11.8 minutes end to end.

The screener scans that universe with fixed rules and no model:
unusual daily move, volume anomaly, moving-average break, RSI extreme,
event-study significance, and portfolio-level risk (concentration, open
loss). **Thresholds scale with measured volatility rather than being fixed
percentages** — a 5% day is extraordinary on the AEX and unremarkable for a
micro-cap coin whose daily volatility is 5.7%. Fixed thresholds would turn
the crypto side into a permanent signal generator.

Only candidates above a strength threshold reach the LLM. Four agents —
technical, fundamental, event, risk — run in parallel and **do not see each
other's output**. That is deliberate: agents that read each other converge on
whoever spoke first, and the point is independence. The arbiter then
surfaces *disagreement* rather than smoothing it, because a technical "up"
against a fundamental "expensive" is precisely what you need to know.

**Silence is a valid outcome.** A system obliged to find an opportunity every
day will manufacture one. If nothing clears the threshold, no message is sent.

### Protection levels

The system has no proven edge — the 2026-08-20 backtest could not
separate 22 of 24 signal cells from zero. So it has no business saying
"buy". A protection level needs none of that evidence, because it is not
a forecast but a **measurement**: this instrument's own average daily
range is N, and a move of more than twice that is no longer ordinary
noise. Same epistemic footing as a thesis break — honest to report
before any hit rate is known.

One level per position, `stop = last close − 2N`, where N is the 20-day
ATR. A fixed percentage would be wrong in both directions: "down 10%"
fires weekly on a micro-cap coin and never on the AEX. 2N is also the
stop the backtest used, so the two layers cannot drift apart.

Three rules:

- **The level only ever moves up.** As price rises so does the level,
  locking in gains; when price falls it stays put. Letting it fall too
  would mean it could never be hit — a protection that voids itself.
- **It speaks once.** A stock oscillating around the threshold would
  otherwise alarm every run, and the value of an alarm comes from its
  rarity.
- **It re-arms on recovery.** A broken level left dead forever would
  leave that position unprotected from then on. Once price closes back
  2% above the threshold, the level is rebuilt.

The alarm is delivered **before the panel** and stamped only after
delivery succeeds — the ordering that was hardened the same morning
after a killed run swallowed a thesis alarm permanently.

Two gates keep the level honest. A stale series never raises an alarm:
claiming "your stop broke" from a five-day-old close reports an event
that did not happen. And a capital action inside the ATR window is
excluded — a split makes N measure the *split*, not the instrument's
daily range, producing an absurdly wide stop that protects nothing. If
the uninterrupted segment is too short to measure, the position is
skipped and counted, not silently given a fake level.

Ask `koruma` in chat ("nereye kadar dayanır?") to see every level and
how far price currently sits from it. Measured 2026-08-21 across the
live portfolio: 21 levels, distances from +1.9% (VUSA) to +16.1%
(MRVL); cash and stablecoin positions carry no series and are skipped.

**No orders, ever.** The level is a measurement; what to do about it is
the owner's call.

### The prediction journal

Every structured opinion is written down before the outcome is known, then
scored when its horizon expires. This is the part that makes the rest
meaningful.

The arithmetic: at 0.1% commission per side, daily trading costs 4.2% a
month. Simulated on ROSE's real volatility, a 50% hit rate returns **−4.2%**
monthly and a 55% hit rate returns **+5.6%**. Everything hinges on which of
those two numbers is real, and it cannot be assumed — only measured. A
system that does not record its own calls will later remember only the ones
that worked.

Scoring uses **abnormal** return, not raw return. "Up" that trails the market
is not a hit; otherwise every prediction looks good in a rising market. The
scorecard reports a Wilson confidence interval and says plainly when the
sample is too small (under 20) to conclude anything.

### Event impact (`/etki`)

A classic event study: an estimation window (120 trading days, ending 10 days
before the event) sets the expected daily return; the difference over the event
window (t−1..t+3) is the abnormal return (AR); the sum is the cumulative
abnormal return (CAR), with a t-statistic. |t| > 2 is roughly the significance
threshold.

Three deliberate constraints:

- **The measurement belongs to the day, not the headline.** All news falling in
  the same window is grouped into one measurement. Listing them separately
  implied two independent pieces of evidence for one number.
- **Market model where a proxy exists.** Returns are regressed on a market
  proxy over the estimation window, so a day when the whole market fell is
  not counted as an abnormal move. The proxy is chosen **by currency**,
  because regressing against an index in another currency pulls FX movement
  into beta: EUR→AEX, USD→QQQ, USDT→BTC. Raw index symbols do not work on
  Alpha Vantage (`^NDX` returns `{}`), so index-tracking ETFs stand in — the
  return series is what the model needs, and it is effectively identical.
  Measured on NVDA: beta ≈ 1.21 against QQQ with R² ≈ 0.47, and residual
  volatility falls from 2.52% to 1.83%. That 27% noise reduction is the
  whole point — a real event has to clear a lower bar to show up.
  Where no proxy exists (BIST, or the proxy itself) the mean-adjusted model
  is used *and declared*.
- **Correlation, never causation.** Every output states that other factors sit
  in the same window and cannot be separated with this data. A non-significant
  result is reported as "not measurable", never as "no effect".

---

## 8. Architecture

```
run.py                          CLI entry point
└── src/finagent/
    ├── config.py               settings.yaml + selectors.yaml + .env
    │                           (strips ANTHROPIC_API_KEY in subscription mode)
    ├── llm.py                  auth probing, human-readable SDK errors
    ├── pipeline.py             orchestration: collect → analyse → report → notify
    ├── logging_setup.py        rich console + file logging
    ├── voice.py                whisper.cpp transcription (fully local)
    ├── bot/
    │   ├── listener.py         Telegram long-poll, commands, approval flow
    │   └── chat.py             ChatEngine: per-question context + system prompt
    ├── vision/screenshot.py    screen classification, two-pass reading, merge
    ├── browser/session.py      persistent Playwright context
    ├── collectors/
    │   ├── prices.py           Yahoo OHLCV via browser
    │   ├── xbrl.py             SEC companyfacts (period-length aware)
    │   ├── edgar.py            SEC filings index
    │   ├── kap.py              KAP disclosures
    │   ├── stocknews.py        tiered news + redirect resolution
    │   ├── indices.py          index membership
    │   ├── news.py             general RSS feeds
    │   ├── bist.py / bux.py    catalogs
    │   ├── midas.py            BIST portfolio (unused — mobile-only broker)
    │   └── isyatirim.py        BIST OHLC
    ├── research/
    │   ├── identity.py         SEC/KAP identity resolution (name must match)
    │   ├── sources.py          publisher → tier
    │   └── resolve_links.py    Google News redirect → publisher URL
    ├── pulse/                  the proactive loop (runs unprompted)
    │   ├── screener.py         deterministic scan, no LLM
    │   ├── agents.py           4 independent agents + arbiter
    │   ├── journal.py          prediction recording and scoring
    │   └── runner.py           orchestration
    ├── analysis/
    │   ├── indicators.py       SMA/EMA/RSI/volatility
    │   ├── portfolio.py        weights, concentration, P&L
    │   ├── events.py           event study (AR / CAR / t-stat)
    │   └── strategist.py       Claude synthesis + injection isolation
    ├── storage/
    │   ├── schema.sql          instruments, identities, watchlist, index_members,
    │   │                       prices, positions, disclosures, news, fundamentals,
    │   │                       analysis_runs, collector_runs
    │   └── db.py               idempotent UPSERTs + migrations
    ├── report/builder.py       Markdown + HTML (dark/light)
    └── notify/telegram.py      sending, HTML escaping, message splitting
```

### Context strategy

Chat context is **rebuilt from the database on every question** — never
accumulated across turns. A typical prompt is 2–5k tokens against a 1M window,
so the conversation does not grow unbounded no matter how long it runs.

### Model roles

`claude-opus-5` for macro synthesis and risk. `claude-fable-5` for tactical and
vision work. Configured under `config/settings.yaml → analysis.llm`.

---

## 9. Testing

517 smoke tests, run directly (pytest is not installed):

```bash
.venv/bin/python tests/test_smoke.py
```

They cover the bugs that actually occurred: vision row-bleed, cut-off rows,
duplicate ticker/name collisions, XBRL period mixing, source tiering, event
study math, news deduplication, and the rule that voice cannot trigger
destructive commands.

---

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Bot does not respond | Check `pgrep -fl "run.py bot"`. If nothing, restart (§3). If two, `pkill` and start one. |
| `ModuleNotFoundError` although `pip list` shows the package | You are not using the venv Python. Use `.venv/bin/python` explicitly. |
| `Claude Code returned an error result: success` | Opaque SDK error. `llm.anlasilir_hata()` probes the actual auth path and explains it. Usually `ANTHROPIC_API_KEY` is set (shadowing the subscription) or the key is out of credit. Empty the variable — it must be **absent**, not blank. |
| `telegram-chatid` returns nothing | Telegram keeps updates ~24h. Send a fresh message, then run it again. |
| An instrument gets no prices or news | Its identity is probably unresolved. `/takip` shows the status; fix with `/kimlik NAME = TICKER`. Unresolved instruments are skipped on purpose. |
| Portfolio total is wrong after a screenshot | It likely spanned several screens. Re-send them within 20 minutes so they merge, or `/sil` and redo. |
| `greenlet` build failure | An old `playwright` is being installed. Versions in `requirements.txt` are intentionally `>=`; pins force a source build on Python 3.13+. `rm -rf .venv` and redo §2.1. |

---

## 11. Known limits

- **Midas/BIST portfolio is untested end to end.** The account exists but
  holds no balance, so no positions have flowed through. Public BIST market
  data is collected and working.
- **The pulse has no track record yet.** The first scheduled run is
  2026-08-17. Until roughly 20 predictions have been scored, the scorecard
  says so and declines to draw conclusions — treat any early hit rate as
  noise.
- **ETF holdings are opaque.** CNDX, VUSA and RBOT carry a theme the rest of
  the portfolio already carries, but their constituents are not collected, so
  true sector exposure cannot be measured — only inferred.
- **Return expectations are bounded by arithmetic.** Monthly targets of
  20–30% EUR or 60–70% TRY require annualised Sharpe ratios of 21 and 34;
  the best fund in history sits near 7. Leverage does not rescue this: at 10x
  on crypto, a simulation on real volatility puts a 50% drawdown inside one
  month at 54% probability against a 25% chance of hitting the target. The
  system is built to measure edge honestly, not to manufacture it.
- **EDGAR filings have no body** — only form type, date and URL.
- **News has no body** — headline and link only.
- **CNDX / VUSA** carry different Yahoo symbols and get no prices; ISINs are in
  the catalog.
- **ABN.AS / PRX.AS** do not file with the SEC, so no XBRL.
- **Terms of service.** Scraping broker or data pages may conflict with
  platform terms. Use it on your own account, at a modest frequency.

---

*This tool compiles and analyses information. It is not investment advice, and
it never places orders.*
