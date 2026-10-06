#!/usr/bin/env bash
# Kapsayici girisi: kalici dizin + cron (supercronic) + bot denetcisi.
#
# launchd KARSILIKLARI
#   bot plist  RunAtLoad + KeepAlive{SuccessfulExit=false} + Throttle 60
#              -> asagidaki dongu: temiz cikis (0) kapsayiciyi bitirir,
#                 cokme 60 sn sonra yeniden baslatir.
#   kosu plist'leri -> deploy/crontab_uret.py (plist'lerden URETILIR; bekci
#              da ayni plist'leri okuyor — iki takvim AYRISAMAZ).
set -uo pipefail
cd /srv/bist-bux-agent

KALICI="${KALICI_DIZIN:-/data}"
mkdir -p "$KALICI/bot" "$KALICI/models"
# Repo icindeki `data/` -> volume. Imajda data/ YOK (.dockerignore).
if [ ! -L data ]; then
  rm -rf data
  ln -s "$KALICI" data
fi

# Ayar katmani (bulut farklari) — Railway degiskeniyle de verilebilir.
export FINAGENT_AYAR_EK="${FINAGENT_AYAR_EK:-config/settings.bulut.yaml}"
export IBKR_MCP_TASIMA="${IBKR_MCP_TASIMA:-dogrudan}"
export BOT_STATE_DIR="${BOT_STATE_DIR:-$KALICI/bot}"
export BROWSER_PROFILE_DIR="${BROWSER_PROFILE_DIR:-$KALICI/browser_profile}"

# Whisper modelleri volume'da; yoksa BIR KEZ indirilir (2 GB, imaja konmaz).
for m in ggml-small.bin ggml-large-v3-turbo.bin; do
  if [ ! -s "data/models/$m" ]; then
    echo "[baslat] $m indiriliyor..."
    curl -fsSL -o "data/models/$m.part" \
      "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$m" \
      && mv "data/models/$m.part" "data/models/$m" \
      || echo "[baslat] UYARI: $m indirilemedi — ses/reel okuma kapali kalir"
  fi
done

# Takvim: plist'lerden uret ve dogrula (bos ya da bozuksa BASLAMA).
if ! .venv/bin/python deploy/crontab_uret.py > /tmp/crontab; then
  echo "[baslat] crontab uretilemedi — kapsayici durduruluyor"
  exit 1
fi
echo "[baslat] crontab:"; cat /tmp/crontab
supercronic -passthrough-logs /tmp/crontab &

# Railway loglarinda gorunsun: bot.log'u stdout'a da akit.
touch data/bot.log
tail -n0 -F data/bot.log &

while true; do
  .venv/bin/python run.py bot >> data/bot.log 2>&1
  kod=$?
  if [ "$kod" -eq 0 ]; then
    echo "[baslat] bot temiz cikti — kapsayici bitiyor"
    exit 0
  fi
  echo "[baslat] bot cikti (kod $kod) — 60 sn sonra yeniden"
  sleep 60
done
