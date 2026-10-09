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

# KURULUM KAPISI. Eksik varken bot ve takvim BASLAMAZ, kapsayici bekler:
#   * veritabani yoksa bot BOS bir veritabani YARATIRDI — portfoy "bos"
#     gorunur, tahmin defteri sifirlanir; tasima oncesi ilk dagitimda
#     (volume'a dosya yuklemek calisan dagitim ister) tam bu olurdu.
#   * token'siz bot dakikada bir coker, zamanli kosular ise LLM'siz kosup
#     "calismadi" alarmlari uretirdi.
# Eksikler giderilince `railway redeploy`.
while true; do
  eksik=()
  [ -s "$KALICI/finagent.db" ] || eksik+=("$KALICI/finagent.db")
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] || eksik+=("TELEGRAM_BOT_TOKEN")
  [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}${ANTHROPIC_API_KEY:-}" ] || eksik+=("CLAUDE_CODE_OAUTH_TOKEN")
  [ ${#eksik[@]} -eq 0 ] && break
  echo "[baslat] KURULUM BEKLIYOR — eksik: ${eksik[*]} (bot ve takvim BASLATILMADI)"
  sleep 300
done

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
# TEST ASAMASI (9 Eki): Mac canliyken bulutta zamanli kosu CALISMAZ —
# yoksa nabiz/rapor IKI KEZ gider (biri canli bottan, biri test botundan).
# Gecis aninda `TAKVIM` degiskeni silinir. Kapaliyken bunu YUKSEK sesle soyle.
if [ "${TAKVIM:-acik}" = "kapali" ]; then
  echo "[baslat] TAKVIM KAPALI (TAKVIM=kapali) — zamanli kosular BU KAPSAYICIDA CALISMAYACAK"
else
  supercronic -passthrough-logs /tmp/crontab &
fi

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
