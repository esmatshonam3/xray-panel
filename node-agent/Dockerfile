# =============================================================================
#  Xray node agent — runs the Xray core and exposes a small authenticated API
#  Base: alpine keeps the image small; Xray is installed at build time.
# =============================================================================
FROM alpine:3.20

ARG XRAY_VERSION=1.8.24
ARG TARGETARCH=amd64

RUN apk add --no-cache ca-certificates curl tzdata bash python3 py3-pip \
 && update-ca-certificates

# --- Xray core ---------------------------------------------------------------
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) XRAY_ARCH=64 ;; \
      arm64) XRAY_ARCH=arm64-v8a ;; \
      *) XRAY_ARCH=64 ;; \
    esac; \
    curl -fsSL -o /tmp/xray.zip \
      "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip"; \
    apk add --no-cache unzip; \
    unzip -o /tmp/xray.zip -d /usr/local/bin/; \
    chmod +x /usr/local/bin/xray; \
    rm -f /tmp/xray.zip; \
    apk del unzip; \
    /usr/local/bin/xray version

WORKDIR /app
COPY requirements.txt ./
RUN apk add --no-cache --virtual .build-deps build-base python3-dev linux-headers \
 && pip3 install --no-cache-dir --break-system-packages -r requirements.txt \
 && apk del .build-deps

COPY agent ./agent
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /etc/xray

ENV XRAY_BIN=/usr/local/bin/xray \
    XRAY_CONFIG_PATH=/etc/xray/config.json \
    XRAY_STATE_PATH=/etc/xray/state.json \
    XRAY_API_PORT=10085 \
    AGENT_PORT=8081 \
    PYTHONUNBUFFERED=1

EXPOSE 8081
EXPOSE 443 8443 2053

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD curl -fsS "http://127.0.0.1:${AGENT_PORT}/health" || exit 1

CMD ["/entrypoint.sh"]
