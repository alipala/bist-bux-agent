#!/usr/bin/env bash
# launchd servislerini kaldirir. Veriye DOKUNMAZ.
set -euo pipefail
KOK="$(cd "$(dirname "$0")/.." && pwd)"
AJAN_DIZIN="$HOME/Library/LaunchAgents"

# ETIKETLER depodaki plist'lerden turer — elle yazilan liste, yeni bir
# kip eklendiginde onu KALDIRMAYI unutur ve hayalet is birakir.
ETIKETLER=()
for _p in "$KOK"/launchd/*.plist; do
  ETIKETLER+=("$(basename "$_p" .plist)")
done
# Artik depoda olmayan ama makinede yuklu kalmis olabilecekler.
ETIKETLER+=(com.alipala.finagent.pulse)

for e in "${ETIKETLER[@]}"; do
  launchctl bootout "gui/$UID/$e" 2>/dev/null && echo "  cikarildi: $e" \
    || echo "  zaten yuklu degil: $e"
  rm -f "$AJAN_DIZIN/$e.plist"
done
echo "Kaldirildi. Veritabani ve loglar duruyor."
