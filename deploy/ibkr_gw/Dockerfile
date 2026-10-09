# IBKR Client Portal Gateway — Railway OLCUM servisi (9 Eki, Ali: "her sey
# Railway'de, Mac kapali"). Bireysel hesapta emir gondermenin TEK resmi yolu
# bu gateway (OAuth yok). Onunde Caddy SIFRE KAPISI: gateway giris yapildiktan
# sonra hesabin TAM islem yetkisini tasir; internete korumasiz ACILMAZ.
FROM caddy:2.8.4-alpine AS caddy

FROM eclipse-temurin:17-jre-jammy
COPY --from=caddy /usr/bin/caddy /usr/bin/caddy
RUN apt-get update && apt-get install -y --no-install-recommends unzip curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /gw
# IBKR'nin resmi dagitimi (Mac'teki ile ayni kaynak).
RUN curl -fsSL -A "Mozilla/5.0" -o gw.zip \
      https://download2.interactivebrokers.com/portal/clientportal.gw.zip \
    && unzip -q gw.zip && rm gw.zip && chmod +x bin/run.sh
COPY conf.yaml /gw/root/conf.yaml
COPY Caddyfile /etc/Caddyfile
COPY baslat.sh /baslat.sh
RUN chmod +x /baslat.sh
CMD ["/baslat.sh"]
