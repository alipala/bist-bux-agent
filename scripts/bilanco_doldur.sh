#!/usr/bin/env bash
# BIST 100 bilancolarini TEK SEFERDE doldurur.
#
# NEDEN AYRI BIR BETIK: nabizdaki donusum (10 sembol/kosu) veriyi TAZE
# TUTMAK icindir, SIFIRDAN DOLDURMAK icin degil. Dogal hizda 89 sembol
# 9 IS GUNU surerdi. Bu betik bir kerelik ~20 dakikada bitirir; sonra
# donusum devralir ve ceyreklik guncellemelere yetisir.
#
# Nabiz sirasinda CALISTIRMA — ayni sayfalari ayni anda acar.
set -euo pipefail
cd "$(dirname "$0")/.."
ADET="${1:-100}"
echo "BIST 100 bilanco doldurma basliyor (per_run=$ADET)..."
FINAGENT_BILANCO_PER_RUN="$ADET" \
  .venv/bin/python run.py collect --site midasbilanco
