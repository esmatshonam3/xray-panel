#!/usr/bin/env bash
# =============================================================================
#  Node agent entrypoint
# =============================================================================
set -euo pipefail

export AGENT_PORT="${AGENT_PORT:-8081}"
mkdir -p "$(dirname "${XRAY_CONFIG_PATH:-/etc/xray/config.json}")"

echo "[node-agent] name=${NODE_NAME:-node} port=${AGENT_PORT} xray=${XRAY_BIN:-/usr/local/bin/xray}"

# Render the base config if the agent has never run on this host.
if [ ! -f "${XRAY_STATE_PATH:-/etc/xray/state.json}" ]; then
  python3 - <<'PY'
import json, os
state = {"inbounds": [], "users": {}}
path = os.environ.get("XRAY_STATE_PATH", "/etc/xray/state.json")
with open(path, "w") as fh:
    json.dump(state, fh, indent=2)
print(f"[node-agent] initialised empty state at {path}")
PY
fi

exec python3 -m uvicorn agent.main:app \
  --host 0.0.0.0 \
  --port "${AGENT_PORT}" \
  --no-server-header \
  --log-level "${LOG_LEVEL:-info}"
