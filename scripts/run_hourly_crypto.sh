#!/usr/bin/env bash
# Saatlik kripto toplama (cron icin).
#
# Kripto 7/24 isler, bu yuzden hisse tarafindaki "kapanistan sonra bir kez"
# mantigi burada gecerli degil. Her saat basi:
#   kripto    -> sembol kimligi (Binance cifti + CoinGecko coin'i)
#   binance   -> gunluk + saatlik OHLCV
#   coingecko -> piyasa degeri, arz, FDV
#
# HICBIRI ANAHTAR VEYA GIRIS GEREKTIRMEZ. Portfoy bakiyeleri bu isin
# parcasi DEGIL — onlar Telegram ekran goruntusu kanalindan, onayla gelir.
#
# Kurulum:
#   crontab -e
#   5 * * * * /Users/alipala/github/bist-bux-agent/scripts/run_hourly_crypto.sh
#
# Dakika 5 secildi: saatlik mum saat basinda kapanir, birkac dakika beklemek
# kapanmis barin kesin olmasini saglar. (Collector zaten olusmakta olan son
# mumu atiyor, bu ek guvenlik.)
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python run.py collect --site kripto binance coingecko \
    >> data/crypto.log 2>&1
