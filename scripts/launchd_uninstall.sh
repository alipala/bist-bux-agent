#!/usr/bin/env bash
# launchd servislerini kaldirir. Veriye DOKUNMAZ.
set -euo pipefail
AJAN_DIZIN="$HOME/Library/LaunchAgents"
for e in com.alipala.finagent.bot com.alipala.finagent.pulse \
         com.alipala.finagent.sabah com.alipala.finagent.ogle; do
  launchctl bootout "gui/$UID/$e" 2>/dev/null && echo "  cikarildi: $e" || echo "  zaten yuklu degil: $e"
  rm -f "$AJAN_DIZIN/$e.plist"
done
echo "Kaldirildi. Veritabani ve loglar duruyor."
