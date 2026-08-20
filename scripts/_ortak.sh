# shellcheck shell=bash
#
# ZAMANLANMIS KOSULARIN ORTAK KABUK KATMANI.
# `run_pulse.sh` ve `run_hafif.sh` bunu `source` eder.
#
# NEDEN AYRI DOSYA
#   Iki betik de ayni uc seyi yapiyor: Telegram'a haber vermek, duvar
#   saati bekcisi kurmak, bekciyi cocuklariyla birlikte temizlemek.
#   Kopyalanmis hali 2026-08-19'da sinandi ve DUSTU: `run_pulse.sh`'te
#   `bildir()` VARDI ama yalnizca cikis kodu sifirdan farkliysa
#   cagriliyordu; sure sinirinda surec grubu SIGTERM aldigi icin o yola
#   HIC gelinmedi. `run_hafif.sh`'te ise `bildir()` hic yoktu.
#   Iki kopya = iki farkli davranis; tek kaynak = tek davranis.
#
# OLCULEN ARIZA (data/pulse.log, 2026-08-19)
#   22:15:00 pulse basladi
#   23:00:00 [run_pulse] 2700 sn asildi, olduruluyor
#   -> log'a TEK SATIR yazildi ve KIMSEYE HABER VERILMEDI.
#      Ali ertesi sabaha kadar nabzin oldugunu bilmiyordu; bekci de
#      fark etmedi (bkz. watchdog.py, IZ_KIPLERI).

# --- Telegram ----------------------------------------------------------
# ASLA KOSUYU DUSURMEZ: bildirim gonderilemezse bile ana is devam eder
# (ya da olum sirasi degismez). `|| true` bilincli.
bildir() {
  .venv/bin/python - "$1" <<'PY' 2>/dev/null || true
import sys
sys.path.insert(0, "src")
from finagent.config import load_settings
from finagent.notify import TelegramNotifier
TelegramNotifier(load_settings()).send_message(sys.argv[1])
PY
}

# Log kuyrugunu HTML'e gomulebilir hale getirir (< > & temizlenir).
son_satirlar() {
  tail -20 data/pulse.log 2>/dev/null | tr '<>&' '   ' | tail -c 600
}

# --- duvar saati bekcisi -----------------------------------------------
# sure_bekcisi_baslat <etiket> <azami_sn> <hedef_pid>
#
# Sure asilirsa: log'a yaz -> TELEGRAM'A HABER VER -> sonra oldur.
# Sira onemli: `kill -TERM -PID` surec GRUBUNU olduruyor ve bekcinin
# kendisi de o gruba dahil; once haber vermezse mesaj hic gitmez.
sure_bekcisi_baslat() {
  local etiket="$1" azami="$2" hedef="$3"
  ( sleep "$azami"
    if kill -0 "$hedef" 2>/dev/null; then
      echo "[$etiket] $(date '+%F %T') ${azami} sn asildi, olduruluyor" \
        >> data/pulse.log
      bildir "🔴 <b>${etiket}: sure siniri asildi</b>

Kosu ${azami} sn (~$((azami / 60)) dk) doldurdu ve DURDURULDU. Yarim kalan
kosu bildirim uretmez ve iz birakmaz — bu mesaj o bosluğun yerine geciyor.

Son satirlar:
<pre>$(son_satirlar)</pre>

Tam log: <code>tail -80 data/pulse.log</code>"
      kill -TERM "-${hedef}" 2>/dev/null || kill -TERM "${hedef}" 2>/dev/null
    fi ) &
  BEKCI_PID=$!
}

# BEKCIYI TEMIZLEMEK ICIN COCUKLARINI DA OLDUR.
#
# Yalnizca alt kabugu oldurmek YETMIYOR: `sleep` onun COCUGU ve oksuz
# kalip calismaya devam ediyor — olculdu, kosu bittikten sonra 45 dakika
# yasayan bir `sleep 2700` kaldi ve miras aldigi log fd'sini acik
# tutarak cagiran surecin de bitmesini engelledi. Once cocuklar.
sure_bekcisi_temizle() {
  [ -n "${BEKCI_PID:-}" ] || return 0
  pkill -P "$BEKCI_PID" 2>/dev/null || true
  kill "$BEKCI_PID" 2>/dev/null || true
}
