#!/usr/bin/env bash
# =============================================================================
#  Panel entrypoint: migrate -> seed -> serve
#  Works on Render (PORT injected), Docker Compose and bare metal.
# =============================================================================
set -euo pipefail

PORT="${PORT:-8000}"
WORKERS="${WEB_CONCURRENCY:-1}"
export PORT WEB_CONCURRENCY="$WORKERS"

echo "[entrypoint] booting ${APP_NAME:-Xray Panel} (env=${ENVIRONMENT:-production}, workers=${WORKERS})"

# Railway mounts fresh volumes as root-owned directories. Prepare the SQLite
# and backup paths before dropping to the unprivileged application account.
mkdir -p /app/data/backups /app/logs
chown -R panel:panel /app/data /app/logs
if [ "$(id -u)" -eq 0 ]; then
  exec gosu panel bash /app/entrypoint.sh
fi

# 1. Schema is created idempotently. Use Alembic for real migrations:
#      alembic upgrade head
python -m app.cli init-db --seed

# 2. Serve
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${WORKERS}" \
  --proxy-headers \
  --forwarded-allow-ips '*' \
  --timeout-keep-alive 30 \
  --no-server-header \
  --log-level "${LOG_LEVEL:-info}"
