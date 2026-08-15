#!/usr/bin/env bash
#
# launchd kurulumu — botu ve nabzi macOS servis yoneticisine devreder.
#
# NEDEN launchd, cron/nohup DEGIL
#   * nohup ile baslatilan bot YENIDEN BASLATMAYI ATLATAMAZ. Guncelleme
#     ya da cokme sonrasi bot olu kalir; nabiz mesaji gelir ama cevap
#     veremezsin.
#   * cron, makine uykudayken kacirilan isi ATLAR. launchd uyaninca
#     calistirir.
#   * launchd cokmede yeniden baslatir (KeepAlive), cron baslatmaz.
#
# Idempotent: tekrar tekrar calistirilabilir.
set -euo pipefail

KOK="$(cd "$(dirname "$0")/.." && pwd)"
AJAN_DIZIN="$HOME/Library/LaunchAgents"
ETIKETLER=(com.alipala.finagent.bot com.alipala.finagent.pulse)

kirmizi() { printf '\033[31m%s\033[0m\n' "$*"; }
yesil()   { printf '\033[32m%s\033[0m\n' "$*"; }
mavi()    { printf '\033[36m%s\033[0m\n' "$*"; }

mavi "==> On kontroller"

# 1) plist icindeki yol GERCEKTEN bu depoya mi isaret ediyor?
#    Depo tasinmissa sessizce yanlis yeri calistirmaktansa DURMALI.
for e in "${ETIKETLER[@]}"; do
  p="$KOK/launchd/$e.plist"
  [ -f "$p" ] || { kirmizi "EKSIK: $p"; exit 1; }
  plutil -lint "$p" >/dev/null || { kirmizi "GECERSIZ plist: $p"; exit 1; }
  if ! grep -q "$KOK" "$p"; then
    kirmizi "plist icindeki yol bu depoyla uyusmuyor: $p"
    kirmizi "  beklenen: $KOK"
    kirmizi "  plist'teki WorkingDirectory'yi duzelt, sonra tekrar calistir."
    exit 1
  fi
done
yesil "  plist'ler gecerli ve yollar dogru"

# 2) venv ve script yerinde mi?
[ -x "$KOK/.venv/bin/python" ] || { kirmizi "venv yok: $KOK/.venv"; exit 1; }
[ -x "$KOK/scripts/run_pulse.sh" ] || { kirmizi "run_pulse.sh calistirilabilir degil"; exit 1; }
yesil "  venv ve script hazir"

# 3) .env okunabiliyor mu? (launchd ciplak ortamda calisir)
[ -f "$KOK/.env" ] || { kirmizi ".env yok — bot baslamaz"; exit 1; }
yesil "  .env mevcut"

mavi "==> Eski mekanizmalar kaldiriliyor"

# CRON: launchd ayni isi yapacak. Ikisi birden kalirsa nabiz GUNDE IKI KEZ
# calisir — cift bildirim, cift LLM maliyeti, ayni gune iki tahmin kaydi.
if crontab -l 2>/dev/null | grep -q "run_pulse.sh"; then
  crontab -l 2>/dev/null | grep -v "run_pulse.sh" \
    | grep -v "BIST/BUX Analiz Agent — proaktif nabiz" \
    | grep -v "^# Hafta ici 22:15" \
    | grep -v "^# Once veri tazelenir" \
    | grep -v "^# Durdurmak: crontab" \
    | crontab - || true
  yesil "  cron girdisi kaldirildi (launchd devraliyor)"
else
  yesil "  cron'da nabiz girdisi yok"
fi

# nohup ile elle baslatilmis bot: launchd kendi ornegini baslatacak.
if pgrep -f "run.py bot" >/dev/null 2>&1; then
  pkill -f "run.py bot" || true
  sleep 2
  yesil "  elle baslatilmis bot durduruldu"
fi

mavi "==> Servisler kuruluyor"
mkdir -p "$AJAN_DIZIN"
for e in "${ETIKETLER[@]}"; do
  cp "$KOK/launchd/$e.plist" "$AJAN_DIZIN/$e.plist"
  # Zaten yuklüyse once cikar; bootstrap iki kez calismaz.
  launchctl bootout "gui/$UID/$e" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$AJAN_DIZIN/$e.plist"
  yesil "  yuklendi: $e"
done

mavi "==> Dogrulama"
sleep 3
hata=0
for e in "${ETIKETLER[@]}"; do
  if launchctl print "gui/$UID/$e" >/dev/null 2>&1; then
    durum=$(launchctl print "gui/$UID/$e" | awk '/state = /{print $3; exit}')
    pid=$(launchctl print "gui/$UID/$e" | awk '/^\tpid = /{print $3; exit}')
    yesil "  $e: state=$durum pid=${pid:-yok}"
  else
    kirmizi "  $e: YUKLENEMEDI"
    hata=1
  fi
done

if pgrep -f "run.py bot" >/dev/null 2>&1; then
  yesil "  bot calisiyor (pid $(pgrep -f 'run.py bot' | head -1))"
else
  kirmizi "  bot CALISMIYOR — data/bot.log'a bak"
  hata=1
fi

echo
mavi "Komutlar:"
cat <<'YARDIM'
  durum        launchctl print gui/$UID/com.alipala.finagent.bot | head -20
  yeniden bas  launchctl kickstart -k gui/$UID/com.alipala.finagent.bot
  durdur       launchctl bootout gui/$UID/com.alipala.finagent.bot
  nabzi ELLE   launchctl kickstart -p gui/$UID/com.alipala.finagent.pulse
  loglar       tail -f data/bot.log   ·   tail -f data/pulse.log
  KALDIR       scripts/launchd_uninstall.sh
YARDIM
exit $hata
