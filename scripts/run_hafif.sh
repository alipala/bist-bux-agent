#!/usr/bin/env bash
# HAFIF KOSU — sabah 09:30 ve oglen 18:00 (launchd). LLM CALISMAZ.
#
# Neden LLM'siz: bu kosularin icerigi yoruma ihtiyac duymuyor. "ROSE
# gunluk oynakliginin 2,8 kati dustu, portfoy agirligin %18" cumlesi
# deterministik ve TAM; modelden gecirmek onu dogru yapmaz, yalnizca
# uzun yapar ve abonelik kullanimini uce katlar.
#
# Sure siniri ve tek-ornek kilidi run_pulse.sh ile AYNI gerekcelerle
# burada da var (ExitTimeOut bir calisma suresi siniri DEGILDIR).
set -uo pipefail
cd "$(dirname "$0")/.."

KIP="${1:-sabah}"

mkdir -p data
exec 9>>"data/hafif_${KIP}.lock"
if ! .venv/bin/python - <<'PY'
import fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)
PY
then
  echo "[run_hafif] $(date '+%F %T') ${KIP} zaten calisiyor, atlaniyor" \
    >> data/pulse.log
  exit 0
fi

AZAMI_SN="${HAFIF_TIMEOUT:-900}"
( sleep "$AZAMI_SN"
  if kill -0 $$ 2>/dev/null; then
    echo "[run_hafif] ${KIP}: ${AZAMI_SN} sn asildi, olduruluyor" >> data/pulse.log
    kill -TERM -$$ 2>/dev/null || kill -TERM $$ 2>/dev/null
  fi ) &
BEKCI=$!
trap 'kill "$BEKCI" 2>/dev/null || true' EXIT

# KISMI TOPLAMA — tam zincir degil, kipin ihtiyaci kadar.
#   sabah 09:30 : gece ABD/Asya kapanislari + kripto (7/24)
#   ogle  18:00 : Avrupa ve BIST kapanisi
if [ "$KIP" = "sabah" ]; then
  KAYNAKLAR="prices binance"
else
  KAYNAKLAR="isyatirim midas prices kap"
fi
.venv/bin/python run.py collect --site $KAYNAKLAR >> data/pulse.log 2>&1 || true

.venv/bin/python run.py nabiz --kip "$KIP" >> data/pulse.log 2>&1
