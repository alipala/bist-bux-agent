"""Reproducible sensitivity checks for the IBKR trend strategy audit.

This file does not mutate production data. It compares the repository's
reported backtest with two audit variants:

1. US shares without the BIST-specific limit-down lock assumption.
2. Signals filled at the next session open instead of the signal-day close.
"""

from __future__ import annotations

import json
from collections import defaultdict

from finagent.analysis import trend_takip
from finagent.config import load_settings
from finagent.storage import Database


START = "2016-09-01"
END = "2026-08-28"
VENUE = "BUX"
INDEXES = ("S&P 500", "Nasdaq 100")
MIN_TURNOVER_USD = 1_000_000.0


def summarize(trades: list[dict], cost: float) -> dict:
    return {
        "trade_summary": trend_takip.ozet(trades, maliyet=cost),
        "monthly_summary": trend_takip.aylik_kumelenme(trades, maliyet=cost),
    }


def next_open_variant(db: Database, trades: list[dict]) -> tuple[list[dict], int]:
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        by_symbol[trade["sembol"]].append(trade)

    ids = {
        row["symbol"]: row["id"]
        for row in db.query(
            """SELECT i.symbol, i.id
               FROM instruments i
               WHERE i.venue = ?
                 AND EXISTS (
                   SELECT 1 FROM index_members m
                   WHERE m.instrument_id = i.id
                     AND m.index_name IN (?, ?)
                 )""",
            (VENUE, *INDEXES),
        )
    }

    adjusted: list[dict] = []
    dropped = 0
    for symbol, symbol_trades in by_symbol.items():
        instrument_id = ids.get(symbol)
        if instrument_id is None:
            dropped += len(symbol_trades)
            continue
        series = [dict(row) for row in db.fiyat_serisi(instrument_id, limit=100_000)]
        date_to_index = {bar["ts"]: i for i, bar in enumerate(series)}
        for trade in symbol_trades:
            entry_i = date_to_index.get(trade["giris_ts"])
            exit_i = date_to_index.get(trade["cikis_ts"])
            if entry_i is None or exit_i is None or entry_i + 1 >= len(series) or exit_i + 1 >= len(series):
                dropped += 1
                continue
            entry_bar = series[entry_i + 1]
            exit_bar = series[exit_i + 1]
            entry = entry_bar.get("open") or entry_bar.get("close")
            exit_price = exit_bar.get("open") or exit_bar.get("close")
            if not entry or not exit_price or entry <= 0:
                dropped += 1
                continue
            adjusted.append(
                {
                    **trade,
                    "giris_ts": entry_bar["ts"],
                    "cikis_ts": exit_bar["ts"],
                    "giris": entry,
                    "cikis": exit_price,
                    "getiri": exit_price / entry - 1,
                }
            )
    return adjusted, dropped


def main() -> None:
    settings = load_settings()
    db = Database(settings.db_path)
    original_islemler = trend_takip.islemler

    def without_us_limit_lock(series, borsa_limiti=None, taban_kilidi=None, asgari_devir=None):
        return original_islemler(
            series,
            borsa_limiti=borsa_limiti,
            taban_kilidi=None,
            asgari_devir=asgari_devir,
        )

    trend_takip.islemler = without_us_limit_lock
    try:
        result = trend_takip.kosu(
            db,
            START,
            END,
            venue=VENUE,
            maliyet=0.004,
            asgari_devir=MIN_TURNOVER_USD,
            settings=settings,
            endeksler=INDEXES,
            kiyas_kod="SPX",
        )
    finally:
        trend_takip.islemler = original_islemler

    trades = result["_islemler"]
    next_open, dropped = next_open_variant(db, trades)
    output = {
        "window": {"start": START, "end": END},
        "universe": result["kapsam"],
        "spx_buy_hold_pct": result["al_tut_endeks_%"],
        "us_no_limit_lock": {
            "cost_0_4_pct": summarize(trades, 0.004),
            "cost_1_9_pct": summarize(trades, 0.019),
        },
        "next_session_open": {
            "dropped_trades": dropped,
            "cost_0_4_pct": summarize(next_open, 0.004),
            "cost_1_9_pct": summarize(next_open, 0.019),
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    db.close()


if __name__ == "__main__":
    main()
