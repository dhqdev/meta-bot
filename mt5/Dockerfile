# MetaTrader 5 em Docker para o Meta-Bot.
# Baseado em https://github.com/gmag11/MetaTrader5-Docker (MIT): KasmVNC (terminal no navegador)
# + Wine + MT5, com o bridge HTTP do Meta-Bot no lugar do RPyC aberto.
# Só roda em hosts x86_64/amd64 (Wine + MT5).
FROM ghcr.io/linuxserver/baseimage-kasmvnc:debianbookworm

ARG BUILD_DATE
ARG VERSION
LABEL build_version="Meta-Bot MT5:- ${VERSION} Build-date:- ${BUILD_DATE}"
LABEL org.opencontainers.image.source="https://github.com/dhqdev/meta-bot"
LABEL org.opencontainers.image.description="MetaTrader 5 (Wine + KasmVNC) com o bridge HTTP do Meta-Bot"

ENV TITLE=Meta-Bot-MT5 \
    WINEPREFIX="/config/.wine" \
    WINEDEBUG=-all \
    MT5_BRIDGE_PORT=8001

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-pip python3-venv wget curl gnupg2 ca-certificates iproute2 \
    && mkdir -pm755 /etc/apt/keyrings \
    && wget -O /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key \
    && wget -NP /etc/apt/sources.list.d/ https://dl.winehq.org/wine-builds/debian/dists/bookworm/winehq-bookworm.sources \
    && dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install --install-recommends -y winehq-stable \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY Metatrader /Metatrader
RUN chmod +x /Metatrader/start.sh
COPY root /

# 3000: painel web (KasmVNC) | 8001: bridge HTTP do Meta-Bot (só na rede interna!)
EXPOSE 3000 8001
VOLUME /config
