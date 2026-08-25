# IBKR AI Trading Assistant
## Claude Opus 4.8 — Local Development Prompt Sequence

---

# TARGET ARCHITECTURE

```text
                           USER
                            │
                            ▼
                  ┌──────────────────┐
                  │ LOCAL DASHBOARD  │
                  │   Streamlit UI   │
                  └────────┬─────────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │   FASTAPI CORE   │
                  │   Orchestrator   │
                  └────────┬─────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
 ┌────────────────┐ ┌──────────────┐ ┌───────────────┐
 │ IBKR WEB API   │ │ FUNDAMENTAL  │ │ NEWS PROVIDER │
 │    ADAPTER     │ │   PROVIDER   │ │    ADAPTER    │
 └───────┬────────┘ └──────┬───────┘ └───────┬───────┘
         │                 │                 │
         └─────────────────┼─────────────────┘
                           ▼
                 ┌────────────────────┐
                 │ DATA NORMALIZATION │
                 │ + QUALITY CHECKS   │
                 └─────────┬──────────┘
                           │
                           ▼
                 ┌────────────────────┐
                 │ FEATURE ENGINE     │
                 │ Python only        │
                 │                    │
                 │ RSI                │
                 │ MACD               │
                 │ SMA / EMA          │
                 │ ATR                │
                 │ Volatility         │
                 │ Volume metrics     │
                 │ Trend metrics      │
                 └─────────┬──────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │ CLAUDE OPUS 4.8│
                  │ ANALYSIS ENGINE │
                  └────────┬────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   TECHNICAL          FUNDAMENTAL          NEWS
   ANALYSIS            ANALYSIS          ANALYSIS
         │                 │                 │
         └─────────────────┼─────────────────┘
                           ▼
                  ┌─────────────────┐
                  │ PORTFOLIO +     │
                  │ RISK ENGINE     │
                  │ Deterministic   │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │ FINAL DECISION  │
                  │     AGENT       │
                  └────────┬────────┘
                           │
                           ▼
             ┌──────────────────────────┐
             │ TRADE PROPOSAL           │
             │                          │
             │ BUY / HOLD / REDUCE /    │
             │ SELL / AVOID             │
             │                          │
             │ Confidence               │
             │ Thesis                   │
             │ Risks                    │
             │ Entry zone               │
             │ Invalidation             │
             └────────────┬─────────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │ HUMAN APPROVAL  │
                 │                 │
                 │ APPROVE         │
                 │ REJECT          │
                 └────────┬────────┘
                          │
                    APPROVE ONLY
                          │
                          ▼
                 ┌─────────────────┐
                 │ ORDER VALIDATOR │
                 │                 │
                 │ Quantity        │
                 │ Buying power    │
                 │ Exposure        │
                 │ Duplicate order │
                 │ Price sanity    │
                 └────────┬────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │ IBKR PAPER      │
                 │ TRADING         │
                 └────────┬────────┘
                          │
                          ▼
                 EXECUTION STATUS
```

## ABSOLUTE SAFETY BOUNDARY

```text
Claude analysis
      │
      ▼
TradeProposal
      │
      X
      │ CANNOT directly call place_order()
      │
      ▼
Human approval
      │
      ▼
Deterministic validation
      │
      ▼
Paper trading order
```

The AI model must never have direct unrestricted access to the IBKR order endpoint.

---

# PROMPT 0 — MASTER PROJECT CONTEXT

Paste this first and keep it as the main project context.

```text
You are the lead software architect and senior Python engineer for a local-first
AI-assisted investment analysis application.

We are building a production-quality but intentionally simple application called:

IBKR AI Trading Assistant

The application connects to Interactive Brokers Web API, collects market and
portfolio data, performs deterministic quantitative calculations in Python,
uses Claude Opus 4.8 for qualitative interpretation and synthesis, generates
structured trade proposals, and allows the human user to manually approve or
reject a proposed PAPER trade.

This is NOT an autonomous trading bot.

The architecture must make it technically impossible for the LLM itself to
submit an order without an explicit human approval event.

<primary_goal>

Create a local application that lets the user:

1. Search for a stock.
2. Retrieve current IBKR market data.
3. Retrieve historical OHLCV data.
4. Retrieve current portfolio positions and account information.
5. Compute technical metrics deterministically.
6. Optionally retrieve fundamentals from an external provider.
7. Optionally retrieve recent news from an external provider.
8. Send normalized analysis inputs to Claude Opus 4.8.
9. Receive a structured investment analysis.
10. Combine AI analysis with deterministic portfolio risk rules.
11. Display an easy-to-understand Trade Proposal.
12. Require explicit human approval.
13. Validate the order again after approval.
14. Submit ONLY to an IBKR paper-trading account.
15. Display order/execution status.

</primary_goal>

<technology_stack>

Backend:
- Python 3.12+
- FastAPI
- Pydantic v2
- httpx
- pandas
- numpy
- SQLAlchemy
- SQLite for local persistence

Frontend:
- Streamlit

AI:
- Anthropic Python SDK
- model: claude-opus-4-8
- structured JSON output
- configurable effort level

Testing:
- pytest
- pytest-asyncio
- respx or equivalent HTTP mocking

Configuration:
- pydantic-settings
- .env
- .env.example

Logging:
- standard Python logging or structlog
- never log credentials or authentication tokens

</technology_stack>

<engineering_principles>

Follow these rules throughout the entire project.

1. Separate data acquisition from analysis.
2. Separate deterministic calculations from LLM reasoning.
3. Never ask Claude to calculate indicators from raw candle arrays when Python
   can calculate them exactly.
4. Every external datum must carry:
   - source
   - timestamp
   - freshness
   - availability status

5. Never silently replace missing data with invented values.

6. Represent missing values explicitly as null or unavailable.

7. Claude must distinguish:
   FACT
   CALCULATED_METRIC
   MODEL_INTERPRETATION

8. AI confidence must never be treated as mathematical probability.

9. AI analysis must be advisory.

10. LLM output cannot directly trigger trade execution.

11. Trading execution requires:
    ANALYSIS
        ->
    TRADE PROPOSAL
        ->
    USER APPROVAL
        ->
    ORDER VALIDATION
        ->
    PAPER ORDER

12. Live trading must be disabled.

13. Do not implement a hidden shortcut that bypasses human approval.

14. Never expose the IBKR order-submission function as an unrestricted AI tool.

15. All monetary calculations must use Decimal where appropriate.

16. Store timestamps in UTC internally.

17. Make failures explicit.

18. Prefer typed Pydantic models over unstructured dictionaries.

19. Prefer small composable services over giant classes.

20. Avoid unnecessary framework complexity.

</engineering_principles>

<repository_structure>

Use approximately this repository structure:

ibkr-ai-trader/
│
├── app/
│   ├── main.py
│   │
│   ├── api/
│   │   ├── routes_analysis.py
│   │   ├── routes_market.py
│   │   ├── routes_portfolio.py
│   │   └── routes_trade.py
│   │
│   ├── core/
│   │   ├── config.py
│   │   ├── logging.py
│   │   └── exceptions.py
│   │
│   ├── models/
│   │   ├── market.py
│   │   ├── portfolio.py
│   │   ├── analysis.py
│   │   ├── risk.py
│   │   └── trading.py
│   │
│   ├── services/
│   │   ├── ibkr/
│   │   │   ├── client.py
│   │   │   ├── session.py
│   │   │   ├── contracts.py
│   │   │   ├── market_data.py
│   │   │   ├── portfolio.py
│   │   │   └── orders.py
│   │   │
│   │   ├── analytics/
│   │   │   ├── indicators.py
│   │   │   ├── volatility.py
│   │   │   └── features.py
│   │   │
│   │   ├── providers/
│   │   │   ├── fundamentals.py
│   │   │   └── news.py
│   │   │
│   │   ├── ai/
│   │   │   ├── client.py
│   │   │   ├── prompts.py
│   │   │   ├── schemas.py
│   │   │   └── analyzer.py
│   │   │
│   │   ├── risk/
│   │   │   ├── portfolio_risk.py
│   │   │   └── trade_validator.py
│   │   │
│   │   └── orchestration/
│   │       └── analysis_pipeline.py
│   │
│   ├── repositories/
│   │   ├── analyses.py
│   │   └── trade_proposals.py
│   │
│   └── db/
│       └── database.py
│
├── ui/
│   └── streamlit_app.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── scripts/
│   ├── test_ibkr_connection.py
│   └── seed_demo_data.py
│
├── .env.example
├── pyproject.toml
├── README.md
├── ARCHITECTURE.md
└── DECISIONS.md

</repository_structure>

<working_method>

Work incrementally.

For each implementation phase:

1. Inspect existing code.
2. State briefly what you are going to change.
3. Implement the smallest coherent change.
4. Add or update tests.
5. Run relevant tests.
6. Fix failures before continuing.
7. Update documentation if architectural behavior changed.

Do not rewrite unrelated working code.

When something is ambiguous, make the safest conservative engineering
assumption and record it in DECISIONS.md.

Do not invent API behavior.

Keep external service behavior behind adapters.

</working_method>

The project should remain runnable after every major phase.

A phase is complete only when:
- code compiles/imports successfully;
- relevant tests pass;
- configuration is documented;
- no credentials have been hard-coded.

Acknowledge these project constraints and then inspect the repository.
Do not implement unrelated features yet.
```

---

# PROMPT 1 — CREATE THE FOUNDATION

```text
Continue using the master project context.

PHASE 1: Build the project foundation.

Create the repository structure and minimum runnable application.

Implement:

1. Python project configuration.
2. FastAPI application.
3. Streamlit application.
4. Pydantic settings.
5. SQLite configuration.
6. basic health endpoint.
7. structured logging.
8. custom exception hierarchy.
9. .env.example.
10. initial README.
11. ARCHITECTURE.md.
12. DECISIONS.md.

Configuration must include at least:

APP_ENV
LOG_LEVEL

IBKR_BASE_URL
IBKR_ACCOUNT_ID
IBKR_MODE=paper

ANTHROPIC_API_KEY
ANTHROPIC_MODEL=claude-opus-4-8

ENABLE_FUNDAMENTALS=false
ENABLE_NEWS=false
ENABLE_PAPER_TRADING=false
ENABLE_LIVE_TRADING=false

Important:

ENABLE_LIVE_TRADING must default to false and the application should reject
live trading configuration.

Create a typed Settings object.

Add startup validation preventing:

IBKR_MODE=live

unless a future explicit architectural change implements it.

For this version, live mode is unsupported.

Create:

GET /health

Expected result:

{
  "status": "ok",
  "environment": "local",
  "trading_mode": "paper"
}

Add tests.

Run tests.

Finally give me:

FILES CREATED
TEST RESULTS
HOW TO RUN BACKEND
HOW TO RUN STREAMLIT
NEXT TECHNICAL STEP

Do not start IBKR integration yet.
```

---

# PROMPT 2 — IBKR SESSION + CONNECTION LAYER

```text
PHASE 2: Implement the Interactive Brokers Web API connection layer.

Do not implement AI analysis yet.

Create a clean IBKR client abstraction.

Responsibilities:

IBKRClient
- base URL handling
- request timeouts
- HTTP error normalization
- safe logging
- session reuse
- JSON parsing

Create IBKRSessionService responsible for:

- checking session state
- verifying whether the brokerage session is usable
- initializing required brokerage-session behavior when appropriate
- returning explicit session status

Create a typed model:

IBKRSessionStatus

fields:

authenticated: bool
brokerage_session_active: bool
connected: bool
message: str
checked_at: datetime

Add:

GET /api/ibkr/status

The endpoint must not leak:
- cookies
- tokens
- authentication headers
- account secrets

Implement retries ONLY for safe GET operations.

Do not automatically retry order submissions.

Use dependency injection so the HTTP layer can be mocked.

Create unit tests for:

- successful connection
- authentication failure
- timeout
- malformed response
- server 500
- disconnected gateway

Create:

scripts/test_ibkr_connection.py

The script should clearly display:

IBKR gateway reachable: YES/NO
Authenticated: YES/NO
Brokerage session: ACTIVE/INACTIVE

Do not implement any trading endpoint.

At the end run all tests and summarize what was implemented.
```

---

# PROMPT 3 — CONTRACT SEARCH + MARKET DATA

```text
PHASE 3: Implement IBKR instrument resolution and market data.

The application must never rely only on ticker text for order identity.

Internally use IBKR conid as the canonical instrument identifier once resolved.

Implement:

ContractSearchService
MarketDataService

Create typed models:

Instrument

- symbol
- conid
- name
- security_type
- currency
- exchange
- primary_exchange

MarketSnapshot

- instrument
- last_price
- bid
- ask
- bid_size
- ask_size
- volume if available
- source
- source_timestamp
- received_at
- freshness_seconds

OHLCBar

- timestamp
- open
- high
- low
- close
- volume

HistoricalSeries

- instrument
- bar_size
- requested_period
- bars
- source
- received_at

Implement:

GET /api/market/search?symbol=AAPL

GET /api/market/{conid}/snapshot

GET /api/market/{conid}/history?period=1y&bar=1d

Validation rules:

- reject empty history
- verify timestamps are monotonically increasing
- detect duplicate candles
- reject impossible OHLC relationships:
  high < low
  open > high
  open < low
  close > high
  close < low

Do not fabricate missing volume.

If volume is unavailable:
volume = null

Introduce:

DataQualityStatus

values:

GOOD
PARTIAL
STALE
INVALID

Market data returned to downstream analysis must include a data-quality result.

Add comprehensive mocked tests.

No AI yet.
No orders yet.
```

---

# PROMPT 4 — DETERMINISTIC QUANTITATIVE ENGINE

```text
PHASE 4: Build the quantitative feature engine.

CRITICAL RULE:

Claude must NOT calculate technical indicators from raw OHLC values.

Python calculates all indicators.

Claude receives calculated metrics.

Implement deterministic calculations for:

PRICE:
- current price
- 1-day return
- 5-day return
- 20-day return
- 60-day return
- 252-day return when data exists

MOVING AVERAGES:
- SMA20
- SMA50
- SMA200
- EMA12
- EMA26

MOMENTUM:
- RSI14
- MACD
- MACD signal
- MACD histogram

VOLATILITY:
- ATR14
- ATR percentage
- 20-day annualized volatility
- 60-day annualized volatility

RANGE:
- 52-week high
- 52-week low
- percentage below 52-week high
- percentage above 52-week low

VOLUME:
- 20-day average volume
- current/average volume ratio
if volume exists

TREND:
derive deterministic states:

STRONG_UPTREND
UPTREND
NEUTRAL
DOWNTREND
STRONG_DOWNTREND

Do not use an LLM to derive these states.

Define clear deterministic rules and document them.

Create:

TechnicalFeatures

with all metrics as typed nullable fields.

Every indicator must report:

value
status

where status may be:

AVAILABLE
INSUFFICIENT_HISTORY
SOURCE_MISSING

Implement edge cases.

Examples:

SMA200 must not pretend to exist with 60 observations.

RSI must handle flat-price periods.

ATR must handle missing candles safely.

Tests must use known deterministic sample series.

Add numerical tolerance tests.

Create documentation:

docs/INDICATORS.md

Explain formulas and interpretation.

Run tests.

No Claude calls yet.
```

---

# PROMPT 5 — PORTFOLIO CONTEXT

```text
PHASE 5: Implement portfolio and account context.

Add IBKR portfolio services.

Retrieve and normalize:

Account summary:
- net liquidation value
- cash
- available funds
- buying power
- excess liquidity when available

Positions:
- conid
- symbol
- position quantity
- average price
- current market price
- market value
- unrealized PnL
- realized PnL
- currency
- sector when available

Create:

PortfolioSnapshot

AccountMetrics
Position

Never send unnecessary account identifiers to Claude.

Create a sanitized model:

AIInvestmentContext

It may contain:

portfolio_value
available_cash
existing_position
existing_position_market_value
existing_position_weight
sector_exposure
number_of_positions

It must NOT contain:

IBKR username
account ID
authentication tokens
session identifiers

Implement:

GET /api/portfolio

Add tests.

No trading yet.
```

---

# PROMPT 6 — FUNDAMENTALS + NEWS PROVIDER INTERFACES

```text
PHASE 6: Introduce optional external data provider interfaces.

Do not bind business logic directly to a single external provider.

Create protocols/interfaces:

FundamentalsProvider

NewsProvider

Create normalized models.

FundamentalData:

symbol
company_name
sector
industry

market_cap
pe_ratio
forward_pe
eps_ttm

revenue_ttm
revenue_growth_yoy
gross_margin
operating_margin
net_margin

total_cash
total_debt
free_cash_flow

earnings_date

source
source_timestamp
received_at

All fields nullable.

NewsItem:

headline
summary
published_at
source
url
related_symbols

NewsBundle:

items
window_start
window_end
received_at
data_quality

Create disabled/default providers that return:

status = NOT_CONFIGURED

rather than failing the entire analysis.

Feature flags:

ENABLE_FUNDAMENTALS
ENABLE_NEWS

The application must still function with IBKR-only technical analysis when
both flags are false.

Do not scrape arbitrary websites.

Do not invent fundamental values.

Add provider contract tests.

Do not implement a paid provider unless credentials/configuration already exist.
```

---

# PROMPT 7 — PREPARE THE AI ANALYSIS PAYLOAD

```text
PHASE 7: Build the normalized AI analysis payload.

Create:

AnalysisInput

The LLM should receive a concise normalized snapshot rather than giant raw API
responses.

Schema concept:

{
  "instrument": {...},

  "market": {
    "price": ...,
    "bid": ...,
    "ask": ...,
    "timestamp": ...
  },

  "technical": {...},

  "fundamentals": {...},

  "news": {...},

  "portfolio": {...},

  "data_quality": {
    "market": "...",
    "historical": "...",
    "fundamentals": "...",
    "news": "...",
    "portfolio": "..."
  }
}

Do not send hundreds of raw candles to Claude by default.

Instead provide calculated features.

Optionally provide:

recent_daily_closes: maximum 20 values

only if useful for qualitative context.

Implement token-conscious serialization.

Add:

build_analysis_input()

The function must make a defensive copy and remove sensitive fields.

Create tests that explicitly verify that these strings never appear in
serialized AI input:

account_id
username
password
token
cookie
authorization

Add snapshot tests for the AI payload.
```

---

# PROMPT 8 — CLAUDE OPUS 4.8 ANALYSIS ENGINE

```text
PHASE 8: Implement the Claude Opus 4.8 investment-analysis engine.

Use the Anthropic SDK.

Model:
claude-opus-4-8

Use structured JSON output.

The model must return an object conforming exactly to:

InvestmentAnalysis

Fields:

symbol: string

analysis_status:
- COMPLETE
- PARTIAL
- INSUFFICIENT_DATA

market_regime:
- BULLISH
- NEUTRAL
- BEARISH
- UNCERTAIN

technical_score:
integer 0..100 or null

fundamental_score:
integer 0..100 or null

news_score:
integer 0..100 or null

risk_score:
integer 0..100

overall_score:
integer 0..100

stance:
- STRONG_BUY
- BUY
- WATCH
- HOLD
- REDUCE
- SELL
- AVOID

confidence:
integer 0..100

time_horizon:
- SHORT_TERM
- SWING
- MEDIUM_TERM
- LONG_TERM

thesis:
array of maximum 5 strings

positive_factors:
array of maximum 5 strings

negative_factors:
array of maximum 5 strings

key_risks:
array of maximum 5 strings

data_gaps:
array of strings

invalidation_conditions:
array of maximum 5 strings

summary:
string, maximum approximately 120 words

IMPORTANT SEMANTICS:

confidence is model confidence in the quality of the interpretation.

It is NOT:
- probability of price increase
- expected return
- probability of profit

Do not allow the LLM to output:

guaranteed return
certain profit
future exact price presented as fact

The AI must explain insufficient data rather than compensate for it.

Implement schema validation.

If output fails validation:
- allow one structured retry
- include validation error
- request corrected JSON
- do not silently patch semantic fields

Store:
model
analysis timestamp
prompt version
input data timestamps

in the analysis record.

Create tests with a mocked Anthropic client.
```

---

# PROMPT 9 — USE THIS EXACT RUNTIME SYSTEM PROMPT

Create this as a versioned prompt in:

`app/services/ai/prompts.py`

```text
You are an investment-analysis engine inside a decision-support application.

Your task is to interpret market, technical, fundamental, news, and portfolio
information supplied to you.

You are NOT the market-data source.

You are NOT responsible for calculating technical indicators.

You are NOT an order-execution system.

You must distinguish between:

<facts>
Values directly supplied by trusted data providers.
</facts>

<calculated_metrics>
Values deterministically calculated by the application's quantitative engine.
</calculated_metrics>

<interpretations>
Your qualitative conclusions derived from facts and calculated metrics.
</interpretations>

<core_rules>

1. Use only information supplied in the analysis input.

2. Never invent missing financial data.

3. Never silently assume unavailable fundamental values.

4. Never infer current news that has not been provided.

5. Treat stale data as lower-quality evidence.

6. Explicitly mention meaningful data gaps.

7. Assess conflicting signals rather than hiding them.

Example:

Bullish price trend + extreme valuation risk should result in a mixed
interpretation rather than blindly bullish output.

8. Portfolio context matters.

A stock can have attractive standalone characteristics but still be unsuitable
for increasing exposure when the portfolio already has excessive concentration.

9. Confidence represents confidence in the analysis quality.

Confidence does NOT represent the probability that the trade will make money.

10. Be conservative when critical information is missing.

11. Do not recommend position size. Position sizing is performed by the
deterministic risk engine.

12. Do not generate executable brokerage orders.

13. Do not claim certainty about future prices.

14. Separate evidence from interpretation.

</core_rules>

<analysis_process>

Evaluate the supplied information in this order:

A. DATA QUALITY

Determine whether the data is:
- sufficient
- partial
- stale
- contradictory

B. MARKET STRUCTURE

Interpret:
- trend
- moving-average structure
- momentum
- volatility
- distance from highs/lows
- volume behavior

C. FUNDAMENTALS

When fundamental data is available, interpret:
- valuation
- profitability
- growth
- balance-sheet quality
- cash generation

When unavailable, mark fundamental analysis unavailable.

D. NEWS

When news is provided:
- identify material positive catalysts
- identify material negative catalysts
- distinguish meaningful events from low-information headlines
- consider news freshness

Do not manufacture sentiment when news is absent.

E. PORTFOLIO CONTEXT

Consider:
- whether a position already exists
- position weight
- portfolio concentration
- available cash

Do not determine final quantity.

F. CONTRADICTIONS

Explicitly identify conflicting signals.

Examples:

strong trend / weak fundamentals

good fundamentals / deteriorating momentum

bullish news / extreme volatility

attractive asset / excessive portfolio concentration

G. FINAL SYNTHESIS

Produce a cautious evidence-based stance.

The strength of the stance must reflect:
- quality of evidence
- agreement between evidence categories
- uncertainty
- portfolio context

</analysis_process>

<score_guidance>

Scores are analytical summaries, not probabilities.

0-20:
very weak / very high concern

21-40:
weak

41-60:
mixed / neutral

61-80:
positive

81-100:
very strong

Do not create false precision.

A score of 73 does not imply a 73% chance of profit.

</score_guidance>

<stance_guidance>

STRONG_BUY:
Use rarely. Requires unusually strong evidence across multiple available
categories with manageable risk and good data quality.

BUY:
Positive evidence outweighs negative evidence.

WATCH:
Potential opportunity exists but timing, evidence quality, or risk does not
justify a stronger stance.

HOLD:
Most appropriate when an existing position remains reasonable but increasing
or reducing exposure is not clearly justified.

REDUCE:
Evidence supports lowering an existing position but not necessarily exiting it.

SELL:
Material deterioration or unfavorable risk/reward supports exiting an existing
position.

AVOID:
For a new position where evidence, risk, or data quality is not acceptable.

</stance_guidance>

Return only the required structured InvestmentAnalysis object.
```

---

# PROMPT 10 — CREATE A DETERMINISTIC RISK ENGINE

```text
PHASE 9: Build the deterministic risk engine.

IMPORTANT:

Claude produces analysis.

Python determines whether a trade is permissible and the maximum permissible
position size.

Create configurable RiskPolicy.

Initial defaults:

max_single_position_pct = 10%
max_sector_exposure_pct = 30%
max_trade_value_pct = 5%
minimum_cash_reserve_pct = 10%
max_new_trade_atr_risk_pct = configurable
allow_short_selling = false
allow_options = false
allow_leverage = false

Do not hard-code these throughout the codebase.

Create:

RiskAssessment

fields:

risk_status:
- PASS
- WARNING
- BLOCKED

current_position_pct

projected_position_pct

current_sector_pct

projected_sector_pct

available_cash

maximum_allowed_trade_value

blocking_reasons

warnings

Create deterministic validation.

Examples:

AI says BUY
but projected position = 18%
and policy maximum = 10%

Result:

AI STANCE: BUY
RISK STATUS: BLOCKED
ACTIONABLE TRADE: NO

Do NOT alter the AI's historical analysis record.

Risk engine is a separate decision layer.

Another example:

AI says BUY
risk rules pass

Result:

AI STANCE: BUY
RISK STATUS: PASS

The application may create a TradeProposal.

Add extensive boundary-value tests.
```

---

# PROMPT 11 — CREATE THE TRADE PROPOSAL

```text
PHASE 10: Implement TradeProposal generation.

A TradeProposal is NOT an order.

Create typed model:

TradeProposal

proposal_id
created_at
expires_at

symbol
conid
side

analysis_id

ai_stance
ai_confidence

reference_price

suggested_quantity
estimated_trade_value

risk_status
risk_warnings

reason_summary

status:
- PROPOSED
- APPROVED
- REJECTED
- EXPIRED
- SUBMITTED
- FAILED

Rules:

TradeProposal may only be generated when:

1. analysis exists;
2. data quality is acceptable;
3. RiskAssessment != BLOCKED;
4. instrument has an IBKR conid;
5. price is recent enough;
6. paper trading mode is enabled.

The AI must not determine final quantity directly.

Suggested quantity must be calculated deterministically from:

- risk policy
- account value
- available cash
- current position
- reference price

For the first version:

Only support:

BUY
SELL

Only support:

STK

No:
- options
- futures
- short sales
- leverage
- margin strategies

Create persistent proposal records.

Create:

POST /api/trade/proposals/{analysis_id}

This endpoint generates a proposal but DOES NOT send anything to IBKR.

Add tests.
```

---

# PROMPT 12 — HUMAN APPROVAL GATE

```text
PHASE 11: Implement an explicit human approval gate.

The architecture must enforce approval server-side.

Do not rely only on a UI button.

Implement:

POST /api/trade/proposals/{proposal_id}/approve

POST /api/trade/proposals/{proposal_id}/reject

Approval should create:

ApprovalRecord

proposal_id
approved_at
approval_nonce
proposal_hash

The proposal hash must represent important immutable trade fields:

conid
side
quantity
reference price context
proposal ID

If the proposal changes after approval:

approval becomes invalid.

The execution endpoint must require a valid approval record.

Claude must have no method of manufacturing an ApprovalRecord.

Approval must originate from the application/user interaction.

Add tests proving:

- unapproved order cannot execute
- rejected proposal cannot execute
- expired proposal cannot execute
- changed proposal invalidates approval
- duplicate approval does not create duplicate trades
```

---

# PROMPT 13 — PAPER TRADING ORDER EXECUTION

```text
PHASE 12: Implement PAPER trading order execution.

Before sending an order, perform PRE-TRADE VALIDATION again using current data.

Re-fetch:

- current market snapshot
- account buying power
- position
- open orders

Check:

1. proposal is APPROVED
2. approval is valid
3. proposal has not expired
4. IBKR mode = paper
5. ENABLE_PAPER_TRADING=true
6. live trading is false
7. instrument conid matches
8. quantity matches approved proposal
9. no duplicate active order
10. sufficient buying power
11. position concentration still passes
12. market price has not moved beyond configurable slippage threshold

Create:

PreTradeValidation

status:
PASS
WARNING
BLOCKED

If BLOCKED:
do not submit.

Create:

IBKROrderRequest

Only after all validation passes.

Submit through one narrowly scoped method:

submit_approved_paper_order(...)

Do NOT expose:

generic_execute_any_order()

Do NOT expose the order submission function as an AI tool.

Store:

broker_order_id
submitted_at
submission payload hash
IBKR response
order status

Never automatically retry POST order submission after a network ambiguity.

A timeout after an order submission should be treated as:

EXECUTION_STATE_UNKNOWN

Then reconcile against IBKR open orders/trades before doing anything else.

This is important to prevent duplicate orders.

Add comprehensive tests.

No live trading.
```

---

# PROMPT 14 — STREAMLIT DASHBOARD

```text
PHASE 13: Build the local Streamlit dashboard.

The UI should prioritize clarity over decorative complexity.

PAGE 1 — ANALYZE

Ticker:
[ MRNA ]

[ ANALYZE ]

Display:

MRNA
Price: $XX.XX
Data updated: ...
Data quality: GOOD

TECHNICAL
Trend
RSI
MACD
SMA20
SMA50
SMA200
ATR
52-week range

FUNDAMENTAL
Show available metrics.

If unavailable:

Fundamental data not configured

Do not show fake zeros.

NEWS
Show recent material headlines if configured.

AI ANALYSIS

Overall Score: XX / 100

Technical: XX
Fundamental: XX / unavailable
News: XX / unavailable
Risk: XX

STANCE

BUY / WATCH / HOLD / etc.

Confidence: XX / 100

Clearly display:

"Confidence is analysis confidence, not probability of profit."

Display:

Positive factors
Negative factors
Key risks
Data gaps
Invalidation conditions

PAGE 2 — PORTFOLIO

Show:

Net liquidation value
Available cash
Positions
Unrealized PnL
Position weights

PAGE 3 — TRADE PROPOSAL

Example:

TRADE PROPOSAL

MRNA

Action:
BUY

Quantity:
5

Reference price:
$42.10

Estimated value:
$210.50

AI stance:
BUY

AI confidence:
74 / 100

Risk status:
PASS

Warnings:
...

Buttons:

[ APPROVE PAPER TRADE ]

[ REJECT ]

Approval must NOT immediately make the UI optimistic.

Show a second confirmation:

"You are approving a PAPER trading order."

After approval:

[ SUBMIT APPROVED PAPER ORDER ]

The backend must still enforce all validation.

PAGE 4 — HISTORY

Show:

analyses
trade proposals
approvals
paper orders
execution status

No account credentials may appear in the UI.
```

---

# PROMPT 15 — ANALYSIS ORCHESTRATOR

```text
PHASE 14: Implement the complete analysis orchestration service.

Create:

AnalysisPipeline

Expected flow:

resolve instrument
      ↓
retrieve snapshot
      ↓
retrieve history
      ↓
retrieve portfolio
      ↓
retrieve optional fundamentals
      ↓
retrieve optional news
      ↓
validate data
      ↓
calculate deterministic features
      ↓
build sanitized AnalysisInput
      ↓
Claude Opus 4.8
      ↓
validate structured AI output
      ↓
deterministic risk assessment
      ↓
persist complete analysis
      ↓
return result to UI
```

The pipeline must support partial degradation.

Example:

IBKR market data: GOOD
Historical data: GOOD
Portfolio: GOOD
Fundamentals: NOT_CONFIGURED
News: NOT_CONFIGURED

Analysis should still run.

Claude should receive:

fundamentals.status = NOT_CONFIGURED
news.status = NOT_CONFIGURED

and should NOT fabricate either.

But:

market history invalid
or
current price unavailable

may make analysis:

INSUFFICIENT_DATA

Implement explicit rules.

Add integration tests for:

FULL DATA

IBKR-ONLY

STALE DATA

MISSING FUNDAMENTALS

MISSING NEWS

MISSING PRICE

INVALID HISTORY

ANTHROPIC FAILURE

PARTIAL ANTHROPIC OUTPUT

Make failures understandable to the UI.
```

---

# PROMPT 16 — AI REQUEST EFFICIENCY

```text
PHASE 15: Optimize Claude Opus 4.8 usage for cost and reliability.

Do not reduce analytical correctness merely to save tokens.

Implement prompt versioning.

Example:

ANALYSIS_PROMPT_VERSION = "1.0.0"

Use a stable system prompt.

Keep volatile market data in the user/input payload.

Do not repeatedly place massive static instructions in dynamically generated
strings if the SDK supports more appropriate stable message construction.

Analysis payload must be compact.

Do NOT send:
- complete IBKR raw API responses
- authentication data
- hundreds of raw candles
- redundant fields
- UI text

Send only normalized evidence.

Set reasonable max output tokens because structured analysis is concise.

Use configurable effort:

CLAUDE_ANALYSIS_EFFORT=high

Use xhigh only for explicit deep-analysis mode.

Add configuration:

AI_MODE:
STANDARD
DEEP

STANDARD:
effort = high

DEEP:
effort = xhigh

Do not allow a UI request to arbitrarily change model name.

Log:

model
effort
prompt version
input token count if available
output token count if available
latency

Never log full sensitive portfolio payloads by default.
```

---

# PROMPT 17 — ADD AN ANALYSIS AUDIT TRAIL

```text
PHASE 16: Make every recommendation explainable later.

Persist an AnalysisRecord containing:

analysis_id
symbol
conid
created_at

market_data_timestamp
historical_data_timestamp
fundamental_data_timestamp
news_data_timestamp

technical_features

data_quality

AI model
AI prompt version

InvestmentAnalysis

RiskAssessment

Do not depend on retrieving today's market data when reviewing yesterday's
analysis.

The historical record must preserve the evidence snapshot that produced the
recommendation.

Add a UI action:

VIEW ANALYSIS DETAILS

Show sections:

DATA USED
CALCULATED FEATURES
AI INTERPRETATION
RISK ASSESSMENT

This is important for debugging and future backtesting.
```

---

# PROMPT 18 — TESTING AND FAILURE MODES

```text
PHASE 17: Perform a comprehensive reliability pass.

Review the entire repository.

Create tests for dangerous failure modes.

At minimum test:

IBKR:
- gateway unavailable
- authentication expired
- session inactive
- 401
- 429
- 500
- timeout
- malformed JSON

MARKET DATA:
- stale price
- no price
- crossed bid/ask
- missing volume
- duplicate bars
- out-of-order bars
- insufficient history

CLAUDE:
- timeout
- invalid JSON
- schema mismatch
- missing required field
- impossible score
- null fields
- partial analysis
- unsupported stance

PORTFOLIO:
- account summary unavailable
- existing concentrated position
- zero cash
- insufficient buying power

TRADING:
- no approval
- expired approval
- modified proposal
- duplicate submission
- IBKR rejects order
- submission timeout
- unknown execution state
- price moved too far after approval

SECURITY:
- sensitive fields excluded from AI payload
- credentials excluded from logs
- account ID excluded from AI request

Verify that no execution path exists from:

Claude response
directly to
IBKR order submission.

Produce a short security architecture report in:

docs/TRADING_SAFETY.md
```

---

# PROMPT 19 — PAPER-TRADING ACCEPTANCE TEST

```text
PHASE 18: Prepare the project for first end-to-end PAPER trading test.

Do NOT switch to live trading.

Create an acceptance checklist and, where possible, automated verification.

Expected manual journey:

1. Start IBKR authentication/session.
2. Start FastAPI.
3. Start Streamlit.
4. Open dashboard.
5. Enter AAPL.
6. Resolve IBKR conid.
7. Retrieve current market price.
8. Retrieve historical data.
9. Calculate indicators.
10. Retrieve portfolio.
11. Run Claude analysis.
12. Show AI stance.
13. Show deterministic risk assessment.
14. Create TradeProposal.
15. Approve PAPER proposal.
16. Run pre-trade validation.
17. Submit paper order.
18. Read IBKR order status.
19. Persist result.
20. Display result in History.

For each step show:

PASS
FAIL
NOT CONFIGURED

Create:

scripts/acceptance_check.py

The script must NOT submit an order automatically.

It may test everything up to order submission.

Actual paper order submission must still require user interaction.

Update README with exact local setup instructions.
```

---

# PROMPT 20 — FINAL CODE REVIEW

After the application is working, give Claude this separately:

```text
Act as a senior engineer performing a hostile pre-production code review of
this repository.

Do NOT assume that the architecture is safe merely because it was intended to
be safe.

Search the actual code.

Specifically look for:

1. any path allowing an LLM response to submit an IBKR order;
2. any bypass around human approval;
3. race conditions around approval;
4. duplicate-order risks;
5. automatic retries of order POST requests;
6. stale market-data use;
7. position-sizing bugs;
8. floating-point money bugs;
9. IBKR authentication/session bugs;
10. sensitive information sent to Claude;
11. sensitive information written to logs;
12. weak Pydantic validation;
13. malformed Claude-output handling;
14. missing timeout handling;
15. missing idempotency protections;
16. live-trading capability accidentally enabled;
17. incorrect handling of partial or stale data;
18. tests that give false confidence because mocks are unrealistic.

For every issue report:

SEVERITY:
CRITICAL / HIGH / MEDIUM / LOW

FILE

CODE LOCATION

WHY IT MATTERS

EXAMPLE FAILURE

RECOMMENDED FIX

Then implement fixes for CRITICAL and HIGH issues.

Run the complete test suite again.

Do not add new product features during this review.
```

---

# RUNTIME ANALYSIS FLOW

Once everything is built, a single stock analysis should internally behave like this:

```text
User enters:

MRNA
   │
   ▼
Resolve instrument
   │
   ▼
IBKR conid
   │
   ├─────────────► Live snapshot
   │
   ├─────────────► Historical OHLC
   │
   └─────────────► Portfolio position
                         │
                         ▼
                Python Feature Engine
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
             RSI        MACD       ATR
             SMA        Trend      Volatility
              │          │          │
              └──────────┼──────────┘
                         │
             Optional external data
               ┌─────────┴─────────┐
               ▼                   ▼
          Fundamentals            News
               │                   │
               └─────────┬─────────┘
                         ▼
                  Normalized JSON
                         │
                         ▼
                 Claude Opus 4.8
                         │
                         ▼
                InvestmentAnalysis
                         │
          ┌──────────────┼─────────────┐
          │              │             │
       Evidence       Risks       Interpretation
          │              │             │
          └──────────────┼─────────────┘
                         ▼
               Python Risk Engine
                         │
              ┌──────────┴───────────┐
              │                      │
            BLOCK                   PASS
              │                      │
              ▼                      ▼
        No trade proposal       TradeProposal
                                      │
                                      ▼
                              HUMAN APPROVAL
                                      │
                                      ▼
                             Pre-trade validation
                                      │
                           ┌──────────┴──────────┐
                           │                     │
                         BLOCK                  PASS
                           │                     │
                           ▼                     ▼
                       No order            IBKR PAPER
                                                │
                                                ▼
                                         Order status
```

---

# EXAMPLE FINAL USER EXPERIENCE

```text
MODERNA — MRNA
────────────────────────────────────

Market price                   $42.10
20-day trend                   Uptrend
RSI                            64
ATR                            4.8%
20-day volatility              High

SMA20                          $39.80
SMA50                          $37.40
SMA200                         $44.20

52-week high distance          -18.4%

────────────────────────────────────
AI ANALYSIS
────────────────────────────────────

Overall Score                  72 / 100

Technical                      76
Fundamental                    68
News                           70
Risk                           59

STANCE

BUY

Analysis confidence            78 / 100

⚠ 78% is analysis confidence.
It is not a 78% probability of profit.

WHY?

Positive

• Price remains above SMA20 and SMA50.
• Medium-term momentum is improving.
• Trading volume is above its recent average.
• Recent material news is moderately positive.

Risks

• Price remains below SMA200.
• Volatility is elevated.
• Biotech-specific event risk remains high.
• Current position already represents 6.8% of portfolio.

Invalidation

• Price loses SMA50 with expanding volume.
• Material negative clinical/regulatory development.
• Volatility expands materially without positive catalyst.

────────────────────────────────────
PORTFOLIO RISK
────────────────────────────────────

Current MRNA exposure            6.8%
Maximum permitted               10.0%

Risk status                     PASS

Maximum additional trade       €850

────────────────────────────────────
TRADE PROPOSAL
────────────────────────────────────

Action                          BUY
Quantity                        5
Reference price                 $42.10
Estimated value                 $210.50

This is a PAPER trade.

[ APPROVE PAPER TRADE ]

[ REJECT ]
```

---

# GOLDEN ARCHITECTURAL RULE

Keep this rule in `ARCHITECTURE.md`:

```text
┌─────────────────────────────────────────────────────┐
│                                                     │
│   THE LLM MAY PROPOSE.                              │
│                                                     │
│   THE RISK ENGINE MAY ALLOW OR BLOCK.               │
│                                                     │
│   ONLY THE HUMAN MAY APPROVE.                       │
│                                                     │
│   ONLY DETERMINISTIC APPLICATION CODE MAY EXECUTE.  │
│                                                     │
└─────────────────────────────────────────────────────┘
```