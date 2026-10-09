#!/usr/bin/env bash
# Start the node dashboard on :8090 (foreground; start-on-boot.sh runs it in tmux 'dashboard').
#
# Default: the Logos node web UI (https://github.com/xAlisher/logos-node-webui), the same UI as the
# official node app and the DAppNode package. Fetched on first run into ~/logos-node-webui; update
# with dashboard/fetch-webui.sh. Its server proxies /api to the node, so it works with any node that
# serves the HTTP API (logosctl + blockchain_module 0.3.x, or the standalone binary).
#
# DASHBOARD=legacy runs the older runbook dashboard (dashboard/run-legacy.sh: logs, zone-board).
#
# Env: HOST (default: Tailscale IPv4, else 127.0.0.1), PORT (8090), NODE_API (http://127.0.0.1:8080).
# The UI has mining + wallet actions and no login: HOST=0.0.0.0 only on a trusted network.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ "${DASHBOARD:-webui}" = legacy ]; then
  exec bash "$HERE/run-legacy.sh"
fi
WEBUI_DIR="${WEBUI_DIR:-$HOME/logos-node-webui}"
[ -x "$WEBUI_DIR/run.sh" ] || bash "$HERE/fetch-webui.sh"
export NODE_API="${NODE_API:-${API:-http://127.0.0.1:8080}}"
exec bash "$WEBUI_DIR/run.sh"
