#!/usr/bin/env bash
# Proaktif dongu — hafta ici 22:15 (launchd).
#
# SIRA ONEMLI: once veri tazelenir, sonra taranir. Bayat veriyle
# uretilen sinyal, sinyal degil gurultudur.
#
# HATA POLITIKASI
#   Toplama adimlari `|| true` ile korumali: bir kaynak duserse nabiz
#   yine calisir. Ama NABZIN KENDISI coker ya da hicbir sey uretmezse
#   bu SESSIZ KALMAMALI — kullaniciya Telegram'dan bildirilir.
#   (Bot ayakta oldugu icin bu senaryo tamamen cozulebilir; makinenin
#   kendisi kapaliysa bkz. bot/watchdog.py — kesinti raporu.)
#   SURE SINIRI BURADA, plist'te DEGIL. `ExitTimeOut` bir calisma suresi
#   siniri SANILIYORDU — degil: launchd isi DURDURURKEN (unload, kapanma)
#   SIGTERM'den sonra tanidigi sure. Zamanlanmis uzun bir isi oldurmez ve
#   `launchctl print` zaten 60 gosteriyor, plist'e yazilan degeri degil.
#   Gercek koruma iki parca: (a) asagidaki duvar saati siniri, (b) tek
#   ornek kilidi — biri hala calisirken ikincisi baslamasin.
set -uo pipefail
cd "$(dirname "$0")/.."

# --- tek ornek ---------------------------------------------------------
# PID dosyasi degil FLOCK: surec cokerse cekirdek kilidi kendisi birakir,
# PID dosyasi ise oksuz kalir ve bir sonraki kosuyu sonsuza dek bloke eder.
#
# Kilit fd 9'da tutuluyor ve ALT SUREC tarafindan aliniyor. Bu calisir
# cunku flock kilidi fd'ye degil ACIK DOSYA TANIMINA baglidir: alt surec
# fd 9'u miras alir, kilidi alir, cikar — ama tanim kabugun fd'si
# uzerinden acik kaldigi icin kilit KABUK YASADIKCA surer.
mkdir -p data
exec 9>>data/pulse.lock
if ! .venv/bin/python - <<'PY'
import fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)
PY
then
  echo "[run_pulse] $(date '+%F %T') onceki kosu hala calisiyor, atlaniyor" \
    >> data/pulse.log
  exit 0
fi

# --- duvar saati siniri ------------------------------------------------
# macOS'ta `timeout` yok (olculdu: command not found). Arka planda bir
# bekci baslatiliyor; sure asilirsa TUM surec grubu oldurulur ve durum
# Telegram'a bildirilir — sessiz takilip kalmaktansa gurultulu olsun.
# OLCULDU 2026-08-16 (tahmin DEGIL):
#   toplama zinciri            7,8 dk
#   nabiz, IKI sahiple         8,5 dk (511 sn; panel basina ~4 dk)
#   toplam                    ~16,3 dk
# 45 dk siniri 2,75 kat pay birakiyor; degistirilmedi. Sahip basina
# ~4 dk eklendigi icin bu sinir kabaca 9 sahibe kadar yeter.
#
# IKI KADEMELI KORUMA: panel butcesi (nabiz icinde, varsayilan 30 dk)
# once devreye girer ve kalan sahibin panelini ATLAYIP ona BILDIRIR;
# asagidaki duvar saati yalnizca son care olarak sureci oldurur.
AZAMI_SN="${PULSE_TIMEOUT:-2700}"
( sleep "$AZAMI_SN"
  if kill -0 $$ 2>/dev/null; then
    echo "[run_pulse] ${AZAMI_SN} sn asildi, oldurul uyor" >> data/pulse.log
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

bildir() {
  .venv/bin/python - "$1" <<'PY' 2>/dev/null || true
import sys
sys.path.insert(0, "src")
from finagent.config import load_settings
from finagent.notify import TelegramNotifier
TelegramNotifier(load_settings()).send_message(sys.argv[1])
PY
}

# 1) Veri tazeleme — tek tek korumali
.venv/bin/python run.py collect --site kriptoevren kripto binance cgfiyat coingecko alphavantage \
    >> data/pulse.log 2>&1 || true
.venv/bin/python run.py collect --site isyatirim midas edgar xbrl \
    >> data/pulse.log 2>&1 || true
.venv/bin/python run.py collect --site prices stocknews kap \
    >> data/pulse.log 2>&1 || true
# Bilanco AYRI: tarayicili ve ~2 dk suruyor. Donusumlu oldugu icin
# BIST 100 ~10 gunde tamamlanir — ceyreklik veri icin fazlasiyla sik.
.venv/bin/python run.py collect --site midasbilanco \
    >> data/pulse.log 2>&1 || true

# 2) Tara + panel + bildir
if ! .venv/bin/python run.py nabiz >> data/pulse.log 2>&1; then
  KOD=$?
  SON=$(tail -20 data/pulse.log | tr '<>&' '   ' | tail -c 600)
  bildir "🔴 <b>Nabiz COKTU</b> (cikis kodu ${KOD})

Son satirlar:
<pre>${SON}</pre>

Tam log: <code>tail -80 data/pulse.log</code>"
  exit "$KOD"
fi
