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
page cannot stall the conversation. SQLite runs in WAL with a 15-second busy
timeout, which is what makes concurrent writes from two processes safe.

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

### Under launchd (how it actually runs)

Once `scripts/launchd_install.sh` has run, the bot is a managed service and
starts itself on login and after a crash. Manage it with:

```bash
launchctl print gui/$UID/com.alipala.finagent.bot | head -20   # status
launchctl kickstart -k gui/$UID/com.alipala.finagent.bot       # restart
launchctl bootout   gui/$UID/com.alipala.finagent.bot          # stop
launchctl kickstart -p gui/$UID/com.alipala.finagent.pulse     # run pulse now
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
| `nabiz` | **The proactive loop**: screen → agent panel → arbiter → Telegram |
| `nabiz --karne` | Hit-rate scorecard for past predictions |
| `nabiz --no-panel` | Deterministic screen only, no LLM |
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

### Services (launchd, not cron)

```bash
scripts/launchd_install.sh      # idempotent; removes any cron entry it replaces
scripts/launchd_uninstall.sh    # removes services, leaves data alone
```

Two user agents are installed into `~/Library/LaunchAgents`:

| Service | Trigger | Behaviour |
|---|---|---|
| `com.alipala.finagent.bot` | `RunAtLoad` | Restarts on crash, survives reboot |
| `com.alipala.finagent.pulse` | weekdays 22:15 | Runs once, then exits |

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

**The pulse takes about 9 minutes**, measured end to end under launchd, not
estimated: roughly 10 minutes of collection (İş Yatırım and the news scan
dominate at ~2 minutes each) and ~4 minutes for the panel and arbiter.
`ExitTimeOut` is 20 minutes so a hung page cannot block the next day's run or
hold the SQLite write lock. Collection steps are tolerated with `|| true`, so
one dead source cannot stop the pulse — on the verification run Alpha Vantage
had exhausted its daily quota and everything else completed normally.

Weekends are excluded on purpose: the markets are shut, so there is no new
close to screen. Crypto trades through the weekend, but its hourly collection
runs separately.

> Verify a scheduled job in a bare environment before trusting it. `env -i
> HOME=$HOME PATH=/usr/bin:/bin` reproduces roughly what the scheduler gives
> you — enough to catch a missing `.env` or an unavailable credential on the
> day you install it rather than on the first scheduled run.

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

The bot also watches the scheduled job it does not control: on a weekday after
23:00, if no signal rows exist for today, the pulse did not run and you are
told. **Silent failure of a scheduled job is the failure mode that matters**,
because nothing looks wrong.

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
| Tokenomics | CoinGecko | market cap, supply, FDV, ATH | `coingecko` |
| Price OHLCV | Yahoo chart via browser | 10,413 bars, 2 years | `prices` |
| Fundamentals (XBRL) | SEC `companyfacts` | 5,221 facts, 11 companies, 5 years | `xbrl` |
| Regulatory filings | SEC EDGAR | form + date + URL (no body) | `edgar` |
| Turkish disclosures | KAP | tier 1 for BIST | `kap` |
| Press | Google News, tiered | 745 items, ~20% usable as evidence | `stocknews` |
| Catalog | indices + KAP + BUX | 1,600 instruments | `indices`, `bist`, `bux` |
| BIST prices | İş Yatırım | 2,840 bars | `isyatirim` |
| FX rates | Alpha Vantage (Tiingo fallback) | EUR/USD, USD/TRY daily | `alphavantage`, `tiingo` |
| European quotes | Alpha Vantage `.AMS` | ASML/ADYEN/INGA/ABN in EUR | `alphavantage` |
| Shares outstanding | Alpha Vantage `OVERVIEW` | rotating, US listings | `alphavantage` |
| Crypto news | Alpha Vantage `NEWS_SENTIMENT` | rotating, majors only | `alphavantage` |
| Event impact | prices + tier 1–2 news | AR / CAR / t-stat | `analysis/events.py` |

None of these require an API key or a login.

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
(`scripts/run_pulse.sh`, weekdays after the US close):

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
