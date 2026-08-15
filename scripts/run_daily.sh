#!/usr/bin/env bash
# Gunluk calistirma sarmalayicisi (cron/launchd icin).
# ONEMLI: login gerektiren kaynaklar icin headless KULLANMA -
# bazi platformlar headless oturumu dusurur.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true
python run.py daily >> data/daily.log 2>&1
