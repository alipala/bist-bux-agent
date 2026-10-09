#!/usr/bin/env bash
# ZAMANLANMIS KOSU — kip adiyla: sabah | ogle | kapanis | nabiz
#
# NEDEN TEK BETIK
#   Once `run_hafif.sh` (sabah/ogle, LLM'siz) ve `run_pulse.sh` (nabiz,
#   panelli) vardi. Ikisi ayni isi kopyalanmis kodla yapiyordu ve
#   kopyalar AYRISTI: `run_pulse.sh` cokmeyi bildiriyordu, `run_hafif.sh`
#   bildirmiyordu; ikisi de sure asimini bildirmiyordu (olculdu
#   2026-08-19, bkz. scripts/_ortak.sh). Ayrica `run_hafif.sh` adi
#   yanlislasmisti: ritim v2'de o kipler de panel kosuyor. Beyan ile
#   gercegin ayrismasi bu projenin tekrar eden kusur sinifidir.
#
# KIP ADI BU DOSYADA GECMEZ
#   Hangi kip ne toplar, ne kadar sure alir, panel kosar mi — hepsi
#   `config/settings.yaml -> ritim.kipler`. Burada `if KIP = sabah`
#   gibi bir dal kalirsa ritim v2 uygulanmamis demektir.
#
# SURE SINIRI PLIST'TE DEGIL BURADA
#   `ExitTimeOut` bir calisma suresi siniri DEGILDIR: launchd isi
#   DURDURURKEN SIGTERM ile SIGKILL arasinda tanidigi suredir ve uzun
#   suren zamanlanmis bir isi oldurmez. Gercek koruma iki parca:
#   (a) asagidaki duvar saati, (b) kip basina tek-ornek kilidi.
set -uo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/_ortak.sh
. "$(dirname "$0")/_ortak.sh"

KIP="${1:-}"
if [ -z "$KIP" ]; then
  # Gecerli kip listesi BURADA SAYILMAZ: ayardan turer ve `ritim_kip`
  # zaten "Tanimli olanlar: ..." diye yaziyor. Burada bir liste tutmak,
  # ayar degistiginde sessizce yanlislasan ikinci bir kaynak olurdu.
  echo "kullanim: $(basename "$0") <kip>" >&2
  echo "gecerli kipler: config/settings.yaml -> ritim.kipler" >&2
  exit 2
fi

mkdir -p data

# --- ayar: kaynaklar + kabuk butcesi -----------------------------------
# TEK DOGRULUK KAYNAGI settings.yaml. Bilinmeyen kip BURADA duser
# (`ritim_kip` ValueError firlatir) ve kosu HIC baslamaz — sessizce
# baska bir kipin ayarina dusmek, yanlis kaynaklari toplayip yanlis
# kisilere mesaj atmak demektir.
if ! AYAR=$(.venv/bin/python - "$KIP" 2>>data/pulse.log <<'PY'
import sys
sys.path.insert(0, "src")
from finagent.config import load_settings
k = load_settings().ritim_kip(sys.argv[1])
print(" ".join(k["kaynaklar"]))
print(int(k["kabuk_butce_sn"]))
PY
); then
  echo "[run_kosu] $(date '+%F %T') ${KIP}: ayar okunamadi, kosu iptal" \
    >> data/pulse.log
  # SESSIZ IPTAL YOK: ayar hatasi kosunun HIC olmamasi demek ve bu
  # disaridan "bugun bir sey olmadi" gibi gorunur.
  bildir "🔴 <b>${KIP}: ayar okunamadi</b>

<code>ritim.kipler.${KIP}</code> tanimli degil ya da gecersiz; bu kosu
HIC calismadi.

Son satirlar:
<pre>$(son_satirlar)</pre>"
  exit 2
fi
KAYNAKLAR=$(printf '%s\n' "$AYAR" | sed -n '1p')
AZAMI_SN=$(printf '%s\n' "$AYAR" | sed -n '2p')

# --- tek ornek ---------------------------------------------------------
# PID dosyasi degil FLOCK: surec cokerse cekirdek kilidi kendisi birakir,
# PID dosyasi ise oksuz kalir ve bir sonraki kosuyu sonsuza dek bloke eder.
#
# Kilit fd 9'da tutuluyor ve ALT SUREC tarafindan aliniyor. Bu calisir
# cunku flock kilidi fd'ye degil ACIK DOSYA TANIMINA baglidir: alt surec
# fd 9'u miras alir, kilidi alir, cikar — ama tanim kabugun fd'si
# uzerinden acik kaldigi icin kilit KABUK YASADIKCA surer.
#
# KIP BASINA AYRI KILIT: sabah kosusu uzarsa ogle kosusu beklemesin.
exec 9>>"data/kosu_${KIP}.lock"
if ! .venv/bin/python - <<'PY'
import fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)
PY
then
  echo "[run_kosu] $(date '+%F %T') ${KIP} zaten calisiyor, atlaniyor" \
    >> data/pulse.log
  exit 0
fi

# --- duvar saati siniri ------------------------------------------------
# macOS'ta `timeout` yok (olculdu: command not found). Sure asilirsa
# TUM surec grubu oldurulur — ama ONCE Telegram'a haber verilir.
# 2026-08-19'da bu sinir asildi, log'a tek satir yazildi ve kimseye
# gitmedi; o gece nabiz kayboldu ve kimse fark etmedi.
sure_bekcisi_baslat "$KIP" "$AZAMI_SN" $$
trap sure_bekcisi_temizle EXIT

# --- SON TARIH ICERIYE GECIRILIYOR -------------------------------------
# Bekci "ne zaman oldurecegini" biliyor, Python tarafi BILMIYORDU. Bedeli
# olculdu (2026-08-21 sabah kosusu): toplama uzadi (tuik 201 sn), panel
# yine tam butcesini istedi, toplam 1500 sn'yi asti ve surec grubu
# olduruldu. Oldurulen kosu iz birakmaz, tahmin yazmaz, mesaj gondermez
# — ustelik o sabah ROSE'un tez alarmi tespit edilmisti ve KALICI olarak
# kayboldu.
#
# Bu damga ile `Nabiz` panel butcesini kalan sureye gore KISIYOR ve
# kosu her zaman kendi ayaklariyla, teslimat payi kalmisken bitiyor.
# Bekcinin `sleep` ile ayni ani kullaniyor: tek dogruluk kaynagi.
BASLANGIC_TS=$(date +%s)
KOSU_BITIS_TS=$(( BASLANGIC_TS + AZAMI_SN ))
export KOSU_BITIS_TS

# --- TOPLAMA SON TARIHI ------------------------------------------------
# Toplama panelin ve teslimatin payina DOKUNAMAZ: `kabuk - panel -
# teslimat` (`runner.toplama_azami_sn`). Olculdu 5 Eki: nabiz toplamasi
# 4106 sn surdu, kabuk 4800'de oldurdu, panel/mesaj/iz HIC uretilmedi.
# Son tarih yalnizca TOPLAMA adimina verilir; gecince kalan kaynaklar
# kosmaz, kesilir ve nabiz mesaji bunu SOYLER.
#
# AYRI ADIM VE DUSURMEZ: yukaridaki ayar adimina konsaydi runner'daki bir
# import hatasi butun kosuyu "ayar okunamadi" diye iptal ederdi. Burada
# hesaplanamazsa toplama SINIRSIZ calisir (eski davranis) ve loglanir.
TOPLAMA_SN=$(.venv/bin/python - "$KIP" 2>>data/pulse.log <<'PY'
import sys
sys.path.insert(0, "src")
from finagent.config import load_settings
from finagent.pulse.runner import toplama_azami_sn
print(int(toplama_azami_sn(load_settings().ritim_kip(sys.argv[1]))))
PY
) || TOPLAMA_SN=""
TOPLAMA_BITIS_TS=""
if [ -n "$TOPLAMA_SN" ] && [ "$TOPLAMA_SN" -gt 0 ] 2>/dev/null; then
  TOPLAMA_BITIS_TS=$(( BASLANGIC_TS + TOPLAMA_SN ))
  echo "[run_kosu] $(date '+%F %T') ${KIP}: toplama son tarihi ${TOPLAMA_SN} sn" \
    >> data/pulse.log
else
  echo "[run_kosu] $(date '+%F %T') ${KIP}: toplama son tarihi HESAPLANAMADI" \
    "('${TOPLAMA_SN}') — toplama SINIRSIZ" >> data/pulse.log
fi

# --- 0) veritabani yedegi ----------------------------------------------
# TOPLAMADAN ONCE: yedek gunun verisini degil, ELDEKI veriyi korur.
# Toplama coker ya da butce dolarsa bile yedek alinmis olur.
#
# HER KOSUDA CAGRILIYOR ama gunde BIR KEZ is yapiyor (bugunun yedegi
# varsa atlar, ~0 sn). Dort kosu = dort sans: makine 17:45'te kapaliysa
# 08:00 ya da 12:30 kosusu yedegi almis olur.
#
# AMA DORDU DE HAFTA ICI. Bu dosya yedegin TEK sahibi oldugu surece
# Cumartesi ve Pazar hic sans yoktu (olculdu 2026-08-23, bkz.
# `run_yedek.sh` basligi). Yedek artik kendi gunluk launchd isinde;
# buradaki cagri emniyet kemeri olarak KALIYOR — 07:30'da makine
# kapaliysa gun icindeki ilk kosu yine de yedegi alir.
#
# `run.py yedek` DEGIL `run_yedek.sh`: kilit orada ve iki yolun ayni
# kilidi paylasmasi ZORUNLU (gecici dosya adi ortak; ayrintisi
# `run_yedek.sh`). Bildirimin sahibi de o betik — burada ikinci bir
# `bildir` cift mesaj demekti. Kosuyu DUSURMUYOR: yedek yoksa bile
# toplama ve panel calismali.
scripts/run_yedek.sh "$KIP" || true

# --- 1) veri tazeleme --------------------------------------------------
# TEK CAGRI: `pipeline.collect` tarayici oturumunu paylastiriyor ve
# collector basina hatayi zaten izole ediyor (`BaseCollector.run`).
# Tarayici HIC acilamazsa bile tarayicisiz collector'lar kosar.
# `|| true`: bir kaynak duserse analiz adimi yine calissin.
# shellcheck disable=SC2086
TOPLAMA_BITIS_TS="$TOPLAMA_BITIS_TS" \
  .venv/bin/python run.py collect --site $KAYNAKLAR >> data/pulse.log 2>&1 || true

# --- 2) tara + panel + bildir ------------------------------------------
# CIKIS KODU `if !` ICINDE OKUNMAZ: `!` sonucu tersine cevirir ve $? HEP 0
# olur (6 Eki incelemesi, 9 Eki yeniden uretildi: kod 3 -> KOD=0). launchd
# ve bulut platformu basarisizligi YALNIZCA cikis kodundan gorur.
KOD=0
.venv/bin/python run.py nabiz --kip "$KIP" >> data/pulse.log 2>&1 || KOD=$?
if [ "$KOD" -ne 0 ]; then
  bildir "🔴 <b>${KIP} kosusu COKTU</b> (cikis kodu ${KOD})

Son satirlar:
<pre>$(son_satirlar)</pre>

Tam log: <code>tail -80 data/pulse.log</code>"
  exit "$KOD"
fi

# FAZ 0 GOZLEMI (IBKR bulut baglayicisi) — nabiz BITTIKTEN SONRA, ayri
# surecte ve `|| true` ile. HANGI KIPTE kosacagi betikte DEGIL ayarda
# (`ritim.kipler.<kip>.mcp_gozlem`); kapali kipte komut hemen cikar.
# Sessiz: Telegram'a bir sey gondermez, sonucu data/mcp_gozlem.jsonl'e
# yazar. Cokse nabizin sonucu zaten belli; sure bekcisi bu adimi da kapsar.
.venv/bin/python run.py mcp-gozlem --kip "$KIP" >> data/pulse.log 2>&1 || true
