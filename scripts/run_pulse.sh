#!/usr/bin/env bash
# Proaktif dongu — gunde bir kez, piyasalar kapandiktan sonra.
#
# SIRA ONEMLI: once veri tazelenir, sonra taranir. Bayat veriyle
# uretilen sinyal, sinyal degil gurultudur.
#
# Kurulum (hafta ici 22:15 — BIST 18:00 TR, ABD 22:00 TR kapanir):
#   crontab -e
#   15 22 * * 1-5 /Users/alipala/github/bist-bux-agent/scripts/run_pulse.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# 1) Veri (tarayicisiz olanlar hizli, tarayicili olan ayri)
.venv/bin/python run.py collect --site kripto binance coingecko alphavantage \
    >> data/pulse.log 2>&1 || true
.venv/bin/python run.py collect --site isyatirim midas edgar xbrl \
    >> data/pulse.log 2>&1 || true
.venv/bin/python run.py collect --site prices stocknews kap \
    >> data/pulse.log 2>&1 || true

# 2) Tara + panel + bildir
.venv/bin/python run.py nabiz >> data/pulse.log 2>&1
