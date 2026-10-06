#!/usr/bin/env bash
# IBKR CLIENT PORTAL GATEWAY — launchd altinda, acilista baslar.
#
# NEDEN (2026-10-06): Mac 10:48'de yeniden basladi; bot launchd ile geri
# geldi ama gateway elle baslatildigi icin GELMEDI ve bekci "IBKR oturumu
# kapandi" dedi. Gateway'in AYAKTA olmasi otomatik olabilir; GIRIS (tarayici
# + telefon onayi) otomatik DEGILDIR ve bu betik onu yapmaz.
#
# PORT DOLUYSA BEKLER, CIKMAZ. Gateway elle (terminalden) calisiyorsa
# ikinci ornek 5001'e baglanamaz ve coker; launchd onu dakikada bir yeniden
# baslatip log'u boguyordu. Bunun yerine port bosalana kadar bekleyip SONRA
# devralir: elle acilan surec kapaninca launchd'ninki onun yerine gecer.
#
# `exec`: java bu kabugun yerine gecer, launchd'nin SIGTERM'i dogrudan ona
# gider (araya giren kabuk sinyali yutmasin).
set -uo pipefail

GW_DIZIN="${GW_DIZIN:-$HOME/Downloads/clientportal.gw}"
GW_AYAR="${GW_AYAR:-root/conf.finagent.yaml}"
GW_PORT="${GW_PORT:-5001}"

zaman() { date '+%F %T'; }

if [ ! -x "$GW_DIZIN/bin/run.sh" ] || [ ! -f "$GW_DIZIN/$GW_AYAR" ]; then
  # Cikis 0 DEGIL: launchd (SuccessfulExit=false) yeniden denesin; dizin
  # bir harici diskte/iCloud'da gec gelirse kendiliginden toparlanir.
  echo "[gateway] $(zaman) EKSIK: $GW_DIZIN/bin/run.sh ya da $GW_AYAR"
  exit 1
fi

bekledi=0
while lsof -nP -iTCP:"$GW_PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  if [ "$bekledi" -eq 0 ]; then
    echo "[gateway] $(zaman) $GW_PORT dolu (elle acilmis gateway?) — bosalinca devralacagim"
    bekledi=1
  fi
  sleep 30
done
[ "$bekledi" -eq 1 ] && echo "[gateway] $(zaman) $GW_PORT bosaldi, devraliyorum"

echo "[gateway] $(zaman) baslatiliyor: $GW_DIZIN ($GW_AYAR)"
cd "$GW_DIZIN" || exit 1
exec bin/run.sh "$GW_AYAR"
