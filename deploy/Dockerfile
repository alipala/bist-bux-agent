# finagent — BULUT (Railway) kapsayicisi.
#
# Mac'teki kurulumun Linux karsiligi; davranis farklari TEK yerde:
# `config/settings.bulut.yaml` (FINAGENT_AYAR_EK). Kalici her sey /data
# volume'unda; repo icindeki `data/` ona baglanir (deploy/baslat.sh), bu
# yuzden kodun "data/..." yollari DEGISMEDEN calisir. Kok: /srv/bist-bux-agent.
#
# Railway: servis degiskeni RAILWAY_DOCKERFILE_PATH=deploy/Dockerfile.

# --- whisper.cpp: Mac'teki surumle AYNI (brew whisper-cpp 1.9.2) ----------
FROM python:3.14-slim AS whisper
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch v1.9.2 https://github.com/ggml-org/whisper.cpp /w \
    && cmake -S /w -B /w/build -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF \
             -DWHISPER_BUILD_EXAMPLES=ON -DGGML_NATIVE=OFF \
    && cmake --build /w/build --target whisper-cli -j"$(nproc)"

# --- calisma imaji ----------------------------------------------------------
FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 \
    TZ=Europe/Amsterdam \
    LANG=C.UTF-8 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# ffmpeg: ses (whisper girdisi); procps: `ps` (bekci, kuyruk); tini: PID 1.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg procps tini tzdata curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# supercronic: launchd takviminin yerine (kapsayici dostu cron).
ARG SUPERCRONIC_VER=v0.2.33
RUN curl -fsSL -o /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VER}/supercronic-linux-amd64" \
    && chmod +x /usr/local/bin/supercronic

# Klasor ADI repo ile AYNI: plist tutarlilik testleri (ve worktree kurali)
# calisma dizininin ADINI depo adiyla karsilastiriyor; `/app` bunu kirdi.
WORKDIR /srv/bist-bux-agent
COPY deploy/requirements.lock /tmp/requirements.lock
RUN python -m venv /srv/bist-bux-agent/.venv \
    && /srv/bist-bux-agent/.venv/bin/pip install --no-cache-dir -r /tmp/requirements.lock \
    && /srv/bist-bux-agent/.venv/bin/python -m playwright install --with-deps chromium

COPY --from=whisper /w/build/bin/whisper-cli /usr/local/bin/whisper-cli

COPY . /srv/bist-bux-agent
RUN chmod +x deploy/baslat.sh scripts/*.sh

# -g: SIGTERM surec GRUBUNA — bot ve cron birlikte kibarca iner.
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/srv/bist-bux-agent/deploy/baslat.sh"]
