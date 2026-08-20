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
# ELLE YAZILMIYOR: depodaki plist'lerden turuyor. Elle yazilan liste,
# yeni bir kip eklendiginde SESSIZCE eksik kalirdi — `kapanis` kipi
# eklendiginde tam olarak bu olurdu ve o kosu hic kurulmazdi.
ETIKETLER=()
for _p in "$KOK"/launchd/*.plist; do
  ETIKETLER+=("$(basename "$_p" .plist)")
done

# ARTIK OLMAYAN ETIKETLER — bunlar YUKLU KALIRSA hayalet is olur.
# `pulse` -> `nabiz` yeniden adlandirildi (ritim v2 §3.5): eskisi
# unload edilmezse IKI is 22:15'te birden kosar, kilit birini duşurur
# ve bu SESSIZ bir kayiptir.
ESKI_ETIKETLER=(com.alipala.finagent.pulse)

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
[ -x "$KOK/scripts/run_kosu.sh" ] || { kirmizi "run_kosu.sh calistirilabilir degil"; exit 1; }
yesil "  venv ve script hazir"

# 2b) HER KIP AYARDA TANIMLI MI? Plist var ama ayar yoksa is kosar ve
#     `run_kosu.sh` ilk adimda duser — sessizce degil, ama gunde bir kez
#     ve ancak Telegram'dan. Kurulumda yakalamak ucuz.
for e in "${ETIKETLER[@]}"; do
  kip="${e##*.}"
  [ "$kip" = "bot" ] && continue
  # MUTLAK YOL: betik herhangi bir dizinden calistirilabilir ve
  # `sys.path.insert(0, "src")` gibi GORECELI bir yol o durumda sessizce
  # yanlis (ya da hic olmayan) bir paketi yuklerdi.
  if ! "$KOK/.venv/bin/python" - "$KOK" "$kip" <<'PY' 2>/dev/null
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / "src"))
from finagent.config import load_settings
load_settings(pathlib.Path(sys.argv[1])).ritim_kip(sys.argv[2])
PY
  then
    kirmizi "plist var ama ayar YOK: ritim.kipler.$kip"
    exit 1
  fi
done
yesil "  her kip config/settings.yaml'da tanimli"

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

# ESKI ETIKETLER: yuklu kalirlarsa hayalet is olurlar. `pulse` ->
# `nabiz` yeniden adlandirmasinda ikisi de 22:15'te kosardi ve kilit
# birini duşururdu — sessiz kayip.
for e in "${ESKI_ETIKETLER[@]}"; do
  if launchctl print "gui/$UID/$e" >/dev/null 2>&1; then
    launchctl bootout "gui/$UID/$e" 2>/dev/null || true
    yesil "  eski servis cikarildi: $e"
  fi
  rm -f "$AJAN_DIZIN/$e.plist"
done

mavi "==> Servisler kuruluyor"
mkdir -p "$AJAN_DIZIN"

# ONCE HEPSINI CIKAR, SONRA HEPSINI YUKLE.
#
# Tek dongude `bootout; bootstrap` YAPMA: `bootout` SENKRON DEGIL ve
# servis hala kapanirken gelen `bootstrap` "Bootstrap failed: 5:
# Input/output error" veriyor. Olculdu 2026-08-20 — betik tam burada
# durdu ve BOT KAPALI KALDI (pkill onu zaten oldurmustu). Cikis ile
# yukleme arasina servisin gercekten gitmesini bekleyen bir kapi kondu.
for e in "${ETIKETLER[@]}"; do
  launchctl bootout "gui/$UID/$e" 2>/dev/null || true
done
for _ in $(seq 1 20); do
  launchctl list | grep -q "com.alipala.finagent" || break
  sleep 1
done
if launchctl list | grep -q "com.alipala.finagent"; then
  kirmizi "  servisler 20 sn'de cikmadi:"
  launchctl list | grep "com.alipala.finagent" || true
  exit 1
fi

hata_kurulum=0
for e in "${ETIKETLER[@]}"; do
  cp "$KOK/launchd/$e.plist" "$AJAN_DIZIN/$e.plist"
  if launchctl bootstrap "gui/$UID" "$AJAN_DIZIN/$e.plist"; then
    yesil "  yuklendi: $e"
  else
    kirmizi "  YUKLENEMEDI: $e"
    hata_kurulum=1
  fi
done
# TEK BIR SERVIS DUSSE BILE DEVAM ET ve SONDA SOYLE: `set -e` ile
# ortada durmak, bot yuklenmeden cikmak demekti.
[ "$hata_kurulum" = 0 ] || kirmizi "  en az bir servis yuklenemedi (asagida dogrulama var)"

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
  kosu ELLE    launchctl kickstart -p gui/$UID/com.alipala.finagent.nabiz
               (sabah | ogle | kapanis | nabiz)
  loglar       tail -f data/bot.log   ·   tail -f data/pulse.log
  KALDIR       scripts/launchd_uninstall.sh
YARDIM
exit $hata
