#!/usr/bin/env bash
# GUN ICI KOSU — piyasa saatinde esik kontrolu. LLM YOK.
#
# NEDEN `run_kosu.sh` DEGIL
#   O betik SABIT SAATLI kipler icin: kaynak listesini `ritim.kipler`den
#   okuyor, panel butcesi uyguluyor ve kabuk butcesi dakikalar mertebesinde.
#   Gun ici kosu ARALIKLA calisiyor (StartInterval), panel calistirmiyor ve
#   saniyeler suruyor. Ikisini tek betige sikistirmak, `run_hafif.sh` ile
#   `run_pulse.sh`'in kopyalanip AYRISMASIYLA ayni hatayi tekrarlardi —
#   yalnizca ters yonde.
#
# PIYASA KAPALIYSA HICBIR SEY YAPMAZ ve bu ARIZA DEGILDIR. Kapi Python
# tarafinda (`gunici.acik_borsalar`), cunku seans bilgisi zaten orada ve
# ikinci bir saat tablosu yazmak bu projenin tekrar eden kusur sinifi.
set -uo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/_ortak.sh
. "$(dirname "$0")/_ortak.sh"

mkdir -p data

# --- ayar: kabuk butcesi ------------------------------------------------
# TEK DOGRULUK KAYNAGI settings.yaml. Ayar okunamazsa kosu HIC baslamaz
# ve bu SESSIZ kalmaz.
if ! AZAMI_SN=$(.venv/bin/python - 2>>data/gunici.log <<'PY'
import sys
sys.path.insert(0, "src")
from finagent.config import load_settings
print(int(load_settings().gunici_ayari()["kabuk_butce_sn"]))
PY
); then
  echo "[gunici] $(date '+%F %T') ayar okunamadi, kosu iptal" >> data/gunici.log
  bildir "🔴 <b>gunici: ayar okunamadi</b>

<code>ritim.gunici</code> tanimli degil ya da gecersiz; gun ici kontrol
HIC calismadi."
  exit 2
fi

# --- tek ornek ----------------------------------------------------------
# 30 dakikada bir kosuyor ve normalde saniyeler suruyor; yine de takilan
# bir istek bir sonrakiyle CAKISMAMALI. flock, PID dosyasi degil: surec
# cokerse cekirdek kilidi kendisi birakir.
exec 9>>"data/kosu_gunici.lock"
if ! .venv/bin/python - <<'PY'
import fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)
PY
then
  echo "[gunici] $(date '+%F %T') zaten calisiyor, atlaniyor" >> data/gunici.log
  exit 0
fi

# --- duvar saati --------------------------------------------------------
# `run_kosu.sh` ile ayni mekanizma ve ayni sira: once haber ver, sonra
# oldur (bekci de oldurulen surec grubunda).
sure_bekcisi_baslat "gunici" "$AZAMI_SN" $$
trap sure_bekcisi_temizle EXIT

# --- kontrol ------------------------------------------------------------
# LOG AYRI DOSYADA (`data/gunici.log`): gunde ~16 kosu, `pulse.log`'a
# yazsaydi dort zamanlanmis kosunun izini gurultuye gomerdi.
if ! .venv/bin/python run.py gunici >> data/gunici.log 2>&1; then
  KOD=$?
  bildir "🔴 <b>Gun ici kosu COKTU</b> (cikis kodu ${KOD})

Koruma seviyeleri ve tez kosullari bu turda kontrol EDILMEDI.

Son satirlar:
<pre>$(son_satirlar_dosya data/gunici.log)</pre>"
  exit "$KOD"
fi
