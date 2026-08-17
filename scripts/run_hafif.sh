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

# OLCULDU 2026-08-16: sabah 63 sn, ogle 7 dk 12 sn (Is Yatirim baskin).
# Nabiz kismi IKI sahiple 5,8 sn — LLM olmadigi icin sahip sayisi
# sureyi pratikte artirmiyor; baskin maliyet toplama.
AZAMI_SN="${HAFIF_TIMEOUT:-900}"
( sleep "$AZAMI_SN"
  if kill -0 $$ 2>/dev/null; then
    echo "[run_hafif] ${KIP}: ${AZAMI_SN} sn asildi, olduruluyor" >> data/pulse.log
    kill -TERM -$$ 2>/dev/null || kill -TERM $$ 2>/dev/null
  fi ) &
BEKCI=$!
# BEKCIYI TEMIZLEMEK ICIN COCUKLARINI DA OLDUR.
#
# Yalnizca alt kabugu oldurmek YETMIYOR: `sleep` onun COCUGU ve oksuz
# kalip calismaya devam ediyor — olculdu, kosu bittikten sonra 45 dakika
# yasayan bir `sleep 2700` kaldi ve miras aldigi log fd'sini acik
# tutarak cagiran surecin de bitmesini engelledi. Once cocuklar.
temizle() {
  pkill -P "$BEKCI" 2>/dev/null || true
  kill "$BEKCI" 2>/dev/null || true
}
trap temizle EXIT

# KISMI TOPLAMA — tam zincir degil, kipin ihtiyaci kadar.
#   sabah 09:30 : gece ABD/Asya kapanislari + kripto (7/24)
#   ogle  18:00 : Avrupa ve BIST kapanisi
# `makro` IKISINDE DE var: endeks/emtia/kur paneli her iki kosunun da
# baglami. Enstruman basina tek Yahoo istegi, ~20 sn ekliyor.
if [ "$KIP" = "sabah" ]; then
  KAYNAKLAR="prices makro binance"
else
  KAYNAKLAR="isyatirim midas prices makro takvim kap"
fi
.venv/bin/python run.py collect --site $KAYNAKLAR >> data/pulse.log 2>&1 || true

.venv/bin/python run.py nabiz --kip "$KIP" >> data/pulse.log 2>&1
