# BIST / BUX Analysis Agent

A personal investment-analysis agent that runs entirely on a local machine.
It tracks a **BUX (ABN AMRO, EUR)** portfolio and **Binance crypto**, is wired
for **Midas (BIST, TRY)**, collects public market data, computes technical,
fundamental and event-study metrics deterministically, and uses Claude only to
*interpret* what was measured.

Data goes in through a **Telegram bot**, and so do the questions. The chat
model has real tools — it decides what to fetch, runs collectors, and can
stage portfolio writes for one-tap approval. No command vocabulary to learn.

```
phone screenshot ─┐
voice message  ───┼─→ Telegram bot ─→ SQLite ─→ Claude (interprets) ─→ answer
typed question ───┘         ↑
                    public collectors
        (SEC EDGAR · XBRL · KAP · Yahoo · Binance · CoinGecko · news)
```

---

## Table of contents

1. [Why it is built this way](#1-why-it-is-built-this-way)
2. [Setup from scratch](#2-setup-from-scratch)
3. [Running and restarting the backend](#3-running-and-restarting-the-backend)
4. [Telegram bot commands](#4-telegram-bot-commands)
5. [CLI commands](#5-cli-commands)
6. [Data layers](#6-data-layers)
7. [How the analysis works](#7-how-the-analysis-works)
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

### Restart (the command you will use most)

```bash
cd ~/github/bist-bux-agent && pkill -f "run.py bot"; sleep 1
nohup .venv/bin/python run.py bot > data/bot.log 2>&1 &
```

> **Never run two instances.** Both poll the same Telegram queue and each
> message goes to a random one. Always `pkill` first.

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
| `/onayla` | Save all pending screenshot readings |
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
| `/unut` | Clear chat history |
| `/yardim` | Help |

---

## 5. CLI commands

All are run as `.venv/bin/python run.py <command>`.

| Command | What it does |
|---|---|
| `init-db` | Create/migrate the SQLite schema |
| `bot` | **Start the Telegram listener (the backend)** |
| `status` | Database summary + recent collector runs |
| `collect [--site ...] [--headless]` | Run collectors |
| `analyze [--no-llm]` | Print analysis to the console |
| `report [--no-llm]` | Write `.md` + `.html` into `reports/` |
| `daily [--skip-collect] [--no-llm] [--no-notify]` | Full cycle + Telegram delivery |
| `telegram-chatid` | List chats that messaged the bot (find `TELEGRAM_CHAT_ID`) |
| `telegram-test` | Send a test notification |
| `discover --site X [--url ...]` | Dump DOM and propose selectors |
| `login --site {bux,midas}` | Legacy manual-login flow — **not usable**, both brokers are mobile-only |

Collector names for `--site`: `isyatirim`, `kap`, `bist`, `binance`, `bux`,
`coingecko`, `edgar`, `indices`, `kripto`, `midas`, `news`, `prices`,
`stocknews`, `xbrl`.

**Crypto ordering matters:** `kripto` (identity) must run before `binance`
and `coingecko`; both silently skip any symbol whose identity is not
`dogrulandi`.

### Scheduled runs

`scripts/run_daily.sh` wraps `run.py daily` for cron. After the BIST close,
weekdays at 18:30:

```bash
crontab -e
30 18 * * 1-5 /Users/alipala/github/bist-bux-agent/scripts/run_daily.sh
```

> Do not add `--headless`. Some sites drop headless sessions and trigger bot
> challenges. Running normally with the screen locked is more reliable.

Crypto trades 24/7, so it collects hourly rather than once after a close:

```bash
crontab -e
5 * * * * /Users/alipala/github/bist-bux-agent/scripts/run_hourly_crypto.sh
```

Minute 5 is deliberate: the hourly candle closes on the hour, and waiting a
few minutes makes the closed bar certain. (The collector already discards the
still-forming candle; this is belt and braces.)

---

## 6. Data layers

Analysis quality is set by **data**, not by prompt wording. Current coverage:

| Layer | Source | Coverage (2026-08-15) | Collector |
|---|---|---|---|
| Crypto identity | Binance + CoinGecko | 10 verified of 12 | `kripto` |
| Crypto daily OHLCV | Binance klines | 999 bars each, 2.7 years | `binance` |
| Crypto hourly OHLCV | Binance klines | 719 bars each, 30 days | `binance` |
| Tokenomics | CoinGecko | market cap, supply, FDV, ATH | `coingecko` |
| Price OHLCV | Yahoo chart via browser | 10,413 bars, 2 years | `prices` |
| Fundamentals (XBRL) | SEC `companyfacts` | 5,221 facts, 11 companies, 5 years | `xbrl` |
| Regulatory filings | SEC EDGAR | form + date + URL (no body) | `edgar` |
| Turkish disclosures | KAP | tier 1 for BIST | `kap` |
| Press | Google News, tiered | 745 items, ~20% usable as evidence | `stocknews` |
| Catalog | indices + KAP + BUX | 1,600 instruments | `indices`, `bist`, `bux` |
| BIST prices | İş Yatırım | 2,840 bars | `isyatirim` |
| Event impact | prices + tier 1–2 news | AR / CAR / t-stat | `analysis/events.py` |

None of these require an API key or a login.

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
- **Mean-adjusted model, not a market model.** Alpha/beta regression needs an
  index series, which is not collected yet. The weaker model is used *and
  declared* in the output rather than quietly approximated.
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

31 smoke tests, run directly (pytest is not installed):

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

- **Midas/BIST is untested end to end.** The infrastructure is in place (KAP
  collector, TRY, BIST venue) but no account is registered yet.
- **No index series**, so the event study uses a mean-adjusted model instead of
  a market model. This weakens its power to detect real effects.
- **No share count.** `dei` namespace facts are not mapped yet, so market cap
  cannot be computed. The model correctly says so rather than guessing.
- **No FX series.** The portfolio is EUR while prices and EPS are USD. The
  model flags the assumption but cannot convert.
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
