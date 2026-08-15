#!/usr/bin/env bash
# Gunluk calistirma sarmalayicisi (cron/launchd icin).
# ONEMLI: login gerektiren kaynaklar icin headless KULLANMA -
# bazi platformlar headless oturumu dusurur.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true
# Alpha Vantage GUNDE BIR calisir — ucretsiz anahtar 25 istek/gun.
# Saatlik kripto isine KOYULMADI: saatte bir calissaydi kota ilk birkac
# saatte biterdi.
.venv/bin/python run.py collect --site alphavantage >> data/daily.log 2>&1 || true
.venv/bin/python run.py daily >> data/daily.log 2>&1
