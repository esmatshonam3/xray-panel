# =============================================================================
#  Xray Panel - control plane image (API + admin UI + Telegram bot)
# =============================================================================
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root runtime user; /app/data is the mount point for the Render disk.
RUN useradd --create-home --uid 10001 panel \
 && mkdir -p /app/data/backups /app/logs \
 && chown -R panel:panel /app
USER panel

ENV PORT=8000 \
    WEB_CONCURRENCY=1 \
    BACKUP_DIR=/app/data/backups

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/v1/health/live" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash", "entrypoint.sh"]
