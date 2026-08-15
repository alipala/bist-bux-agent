#!/usr/bin/env bash
# Proaktif dongu — hafta ici 22:15 (launchd).
#
# SIRA ONEMLI: once veri tazelenir, sonra taranir. Bayat veriyle
# uretilen sinyal, sinyal degil gurultudur.
#
# HATA POLITIKASI
#   Toplama adimlari `|| true` ile korumali: bir kaynak duserse nabiz
#   yine calisir. Ama NABZIN KENDISI coker ya da hicbir sey uretmezse
#   bu SESSIZ KALMAMALI — kullaniciya Telegram'dan bildirilir.
#   (Bot ayakta oldugu icin bu senaryo tamamen cozulebilir; makinenin
#   kendisi kapaliysa bkz. bot/watchdog.py — kesinti raporu.)
set -uo pipefail
cd "$(dirname "$0")/.."

bildir() {
  .venv/bin/python - "$1" <<'PY' 2>/dev/null || true
import sys
sys.path.insert(0, "src")
from finagent.config import load_settings
from finagent.notify import TelegramNotifier
TelegramNotifier(load_settings()).send_message(sys.argv[1])
PY
}

# 1) Veri tazeleme — tek tek korumali
.venv/bin/python run.py collect --site kripto binance coingecko alphavantage \
    >> data/pulse.log 2>&1 || true
.venv/bin/python run.py collect --site isyatirim midas edgar xbrl \
    >> data/pulse.log 2>&1 || true
.venv/bin/python run.py collect --site prices stocknews kap \
    >> data/pulse.log 2>&1 || true

# 2) Tara + panel + bildir
if ! .venv/bin/python run.py nabiz >> data/pulse.log 2>&1; then
  KOD=$?
  SON=$(tail -20 data/pulse.log | tr '<>&' '   ' | tail -c 600)
  bildir "🔴 <b>Nabiz COKTU</b> (cikis kodu ${KOD})

Son satirlar:
<pre>${SON}</pre>

Tam log: <code>tail -80 data/pulse.log</code>"
  exit "$KOD"
fi
