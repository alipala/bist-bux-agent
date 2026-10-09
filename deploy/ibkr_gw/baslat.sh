#!/usr/bin/env bash
set -euo pipefail
# SIFRESIZ ACILMAZ: GW_SIFRE yoksa gateway de Caddy de BASLAMAZ.
if [ -z "${GW_SIFRE:-}" ]; then
  echo "[gw] GW_SIFRE tanimli degil — giris sayfasi korumasiz ACILMAZ; bekleniyor"
  exec sleep infinity
fi
export GW_HASH="$(caddy hash-password --plaintext "$GW_SIFRE")"
unset GW_SIFRE
cd /gw
bin/run.sh root/conf.yaml > /tmp/gw.log 2>&1 &
tail -n0 -F /tmp/gw.log &
for i in $(seq 1 60); do
  curl -sk -o /dev/null https://127.0.0.1:5000/ && break
  sleep 1
done
echo "[gw] gateway hazir; sifre kapisi :${PORT} uzerinde"
exec caddy run --config /etc/Caddyfile --adapter caddyfile
