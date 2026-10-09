# IBKR Client Portal Gateway — Railway OLCUM servisi (9 Eki, Ali: "her sey
# Railway'de, Mac kapali"). Bireysel hesapta emir gondermenin TEK resmi yolu
# bu gateway (OAuth yok). Onunde Caddy SIFRE KAPISI: gateway giris yapildiktan
# sonra hesabin TAM islem yetkisini tasir; internete korumasiz ACILMAZ.
#
# ICERIDEN GIRIS (9 Eki, A secenegi): telefondan uzaktan giris IBKR oturumunu
# kullaniciya BAGLAMADI (Dispatcher USERID vermedi, iki deneme). Mac'teki gibi
# tarayici gateway ile AYNI makinede olsun diye kapsayicida basliksiz Chromium.
FROM caddy:2.8.4-alpine AS caddy

FROM python:3.14-slim
COPY --from=caddy /usr/bin/caddy /usr/bin/caddy
RUN apt-get update && apt-get install -y --no-install-recommends \
      default-jre-headless unzip curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir playwright==1.62.0 \
    && python -m playwright install --with-deps chromium
WORKDIR /gw
RUN curl -fsSL -A "Mozilla/5.0" -o gw.zip \
      https://download2.interactivebrokers.com/portal/clientportal.gw.zip \
    && unzip -q gw.zip && rm gw.zip && chmod +x bin/run.sh
COPY conf.yaml /gw/root/conf.yaml
COPY Caddyfile /etc/Caddyfile
COPY baslat.sh /baslat.sh
COPY sayfa_olc.py giris.py /gw/
RUN chmod +x /baslat.sh
CMD ["/baslat.sh"]
