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

# --- KOSU KAYNAGI ------------------------------------------------------
# Bu dosyayi launchd'nin kosturdugu UC betigin ucu de source ediyor
# (`run_kosu.sh`, `run_gunici.sh`, `run_yedek.sh`), yani tek satir tum
# zamanlanmis kosulari isaretliyor.
#
# NEDEN GEREKLI — OLCULDU 2026-08-29 15:14 (CUMARTESI, zamanlanmis
# hicbir kosu yokken): conid duzeltmesi dogrulanirken `ibkrkimlik`
# ELLE uc kez kosuldu, ucu de `partial` dondu ve bekci "3 kosudur
# partial" alarmi gonderdi. Bekcinin besinci olcutu ZAMANLANMIS
# kosulardaki SISTEMLI kaybi ariyor; 90 saniyede yapilan uc elle kosum
# o soruya cevap DEGIL.
export FINAGENT_KOSU_KAYNAK=zamanlanmis

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
  son_satirlar_dosya data/pulse.log
}

# Ayni is, ama LOG DOSYASI PARAMETRE. Gun ici kosu `data/gunici.log`'a
# yaziyor (gunde ~16 kosu; `pulse.log`'a karissa dort zamanlanmis kosunun
# izini gurultuye gomerdi) ve hata mesajinda KENDI son satirlari gerekli.
# KUTUPHANE GURULTUSU MESAJA GIRMEZ.
#
# OLCULDU 2026-08-21: ogle kosusu oldurulunce giden mesajin "son
# satirlar" kutusunda DORT kez ayni satir vardi —
# "Using bundled Claude Code CLI: /Users/.../site-packages/..." — ve
# 600 karakterlik pencerenin tamamini yiyip GERCEK sebebi disari itti.
# Kullanicinin gordugu sey mutlak dosya yollariydi; ne olduguna dair
# tek kelime yoktu.
#
# Filtre GORULTUYU eliyor, HATAYI degil: `Traceback`, `ERROR`,
# `WARNING` ve kosu ozet satirlari GECER.
_log_gurultusu() {
  # SARILMIS SATIRLARA DIKKAT: `rich` uzun yollari BOLUYOR, yani
  # "…/site" bir satirda, "-packages/…" digerinde kaliyor. Yalnizca tam
  # ifadeyi elemek yetmedi (olculdu: filtreden sonra hala uc parca
  # geciyordu); parcalarin kendisi de eleniyor.
  #
  # PROJE KODUNA AIT yollar ELENMEZ: traceback'ler
  # `.../src/finagent/...` gosterir ve bu kaliplarin HICBIRINE uymaz —
  # yani gercek hata korunuyor, yalnizca kutuphane gurultusu dusuyor.
  grep -avE 'Using bundled Claude Code CLI|\.venv/lib/python|-packages/|_bundled/claude|_warn_if_|CanUseToolShadowedWarning|^[[:space:]]*$'
}

son_satirlar_dosya() {
  tail -60 "${1:-data/pulse.log}" 2>/dev/null | _log_gurultusu \
    | tr '<>&' '   ' | tail -20 | tail -c 600
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
