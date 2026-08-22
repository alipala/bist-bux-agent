#!/usr/bin/env bash
# VERITABANI YEDEGI — TEK GIRIS NOKTASI.
#
# NEDEN AYRI BIR BETIK VE AYRI BIR LAUNCHD ISI
#   Yedek onceden yalnizca `run_kosu.sh` icinden aliniyordu ve o betigi
#   dort launchd isi cagiriyor: sabah/ogle/kapanis/nabiz. DORDU DE
#   `Weekday 1-5`, yani HAFTA ICI. Sonuc: Cumartesi 0 yedekleme sansi,
#   Pazar 0. Bekcinin `yedek_bayat` esigi 2 gun ve gerekcesi
#   (`watchdog.py`) "gunde dort sans var" diyor — bu varsayim hafta sonu
#   GECERSIZDI.
#
#   OLCULDU 2026-08-23 (Pazar, saat 00:17): son yedek
#   `finagent-2026-08-21.db` (Cuma), `data/pulse.log`'un son yazimi Cuma
#   22:59. Alarm calmisti ve HAKLIYDI — ama sebebi ariza degil TAKVIMDI.
#   Ustelik alarm her Pazar tekrar calacakti (susturma 6 saat, yani
#   Pazartesi 08:00'e kadar ~4 mesaj).
#
#   Esigi 3-4 gune cikarmak YANLIS YON: gercek boslugu gizler. Bot
#   dinleyicisi ve gun ici kosu 7/24 calisiyor ve hafta sonu boyunca
#   sohbet arsivine yaziyor — o veri Pazartesi sabahina kadar YEDEKSIZ
#   kaliyordu. Dogru cozum, yedegi koşulardan AYIRIP her gun kosturmak.
#
# NEDEN KILIT
#   `yedek_al` gecici bir dosyaya yaziyor (`.finagent-<gun>.yaziliyor`)
#   ve o ad IKI kosuda AYNI. Iki surec ayni anda calisirsa ikinci
#   `unlink` birincinin dosyasini dizinden dusurur; birinci kendi
#   (artik adsiz) inode'una yazmaya devam eder, ama DOGRULAMA ve
#   `os.replace` YOLA bakar — yani bir dosya dogrulanip BASKA bir dosya
#   yedek diye tasinabilir. Sessiz bozuk yedek, hic yedek olmamasindan
#   kotudur.
#
#   Cakisma teorik degil: launchd uykuda KACIRILAN takvim islerini
#   uyaninca calistirir. Makine 07:30 ve 08:00'i uykuda gecirip 09:00'da
#   uyanirsa bu is ile `sabah` kosusu AYNI ANDA baslar.
#
#   Kilit bu yuzden BURADA: `run_kosu.sh` da yedegi bu betik uzerinden
#   aliyor, yani iki yolun TEK kilidi var. Kip basina kilit (`kosu_*.lock`)
#   bu isi goremezdi — cakisan sey kip degil, YEDEGIN KENDISI.
set -uo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/_ortak.sh
. "$(dirname "$0")/_ortak.sh"

# Cagiran taraf: mesajda kimin adina konustugumuzu soyler. Zamanlanmis
# is icin "yedek", `run_kosu.sh` icin kip adi (sabah/ogle/...).
CAGIRAN="${1:-yedek}"

mkdir -p data

# --- tek ornek ----------------------------------------------------------
# flock, PID dosyasi DEGIL: surec cokerse cekirdek kilidi kendisi birakir.
# Kilit fd 9'da ve ALT SUREC aliyor — kilit fd'ye degil ACIK DOSYA
# TANIMINA bagli oldugu icin bu calisir ve kabuk yasadikca surer.
exec 9>>"data/yedek.lock"
if ! .venv/bin/python - <<'PY'
import fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)
PY
then
  # ARIZA DEGIL: baska bir yol yedegi zaten aliyor. Gunde bir kez is
  # yapiliyor, dolayisiyla ikinci cagri nasil olsa atlayacakti.
  echo "[yedek] $(date '+%F %T') ${CAGIRAN}: yedek zaten aliniyor, atlaniyor" \
    >> data/pulse.log
  exit 0
fi

# --- yedek --------------------------------------------------------------
# `run.py yedek` gunde BIR KEZ is yapiyor: bugunun yedegi varsa ve
# DOGRULANIYORSA atliyor (~0 sn). Olculdu 2026-08-21: 120,8 MB, 1,06 sn.
#
# LOG `pulse.log`: gunde bir-iki satir yaziyor, ayri bir dosya acmak
# izini bolerdi. `run_kosu.sh` de ayni dosyaya yaziyor ve hata
# mesajindaki "son satirlar" oradan okunuyor.
if ! .venv/bin/python run.py yedek >> data/pulse.log 2>&1; then
  # SESSIZ BASARISIZLIK EN KOTUSU: aldigini sanirsin, yoktur.
  bildir "🔴 <b>${CAGIRAN}: VERITABANI YEDEGI ALINAMADI</b>

<b>Bugun yedek YOK.</b> Tahmin defteri, sohbet arsivi ve portfoy gecmisi
yeniden URETILEMEZ.

Son satirlar:
<pre>$(son_satirlar)</pre>

Elle dene: <code>.venv/bin/python run.py yedek</code>"
  exit 1
fi
