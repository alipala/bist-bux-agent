#!/usr/bin/env bash
# VIDEO OZETI — zamanlanmis is (10 Eki 2026, `video/akis.py`).
#
# Nabizdan ONCE kosar: takip edilen kaynaklarin yeni videolarini bulur,
# yaziya doker, ozetler ve `video_ozet`e yazar. Nabiz (22:15) teslim
# edilmemis ozetleri ayri mesajla gonderir. Iki parca AYRI: bu is dusse
# nabiz etkilenmez, yalnizca o aksam video mesaji gitmez.
#
# HER GUN (hafta sonu dahil): hafta sonu yuklenen videolar Pazartesi nabzina
# kadar islenmis olur; pencere `video_ozet.pencere_saat` (72 sa).
#
# LOG `pulse.log` — Railway'e `[kosu]` onekiyle akar (deploy/baslat.sh).
set -uo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/_ortak.sh
. "$(dirname "$0")/_ortak.sh"

mkdir -p data

# --- tek ornek (run_yedek.sh ile ayni kalip) ------------------------------
exec 9>>"data/video.lock"
if ! .venv/bin/python - <<'PY'
import fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)
PY
then
  echo "[video] $(date '+%F %T') zaten calisiyor, atlaniyor" >> data/pulse.log
  exit 0
fi

echo "[video] $(date '+%F %T') basladi" >> data/pulse.log
KOD=0
.venv/bin/python run.py video-ozet >> data/pulse.log 2>&1 || KOD=$?
if [ "$KOD" -ne 0 ]; then
  bildir "🟠 <b>Video özeti çalışmadı</b> (çıkış kodu ${KOD})

Bu akşam video mesajı gitmeyecek; nabız etkilenmedi.

Son satirlar:
<pre>$(son_satirlar)</pre>"
  exit "$KOD"
fi
echo "[video] $(date '+%F %T') bitti" >> data/pulse.log
exit 0
