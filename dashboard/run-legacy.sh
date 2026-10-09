#!/usr/bin/env bash
# LEGACY dashboard (v0.2.x runbook dashboard: logs, zone-board, Block Proposals panel).
# The default is now the logos-node-webui release: see dashboard/run.sh. Start this one with DASHBOARD=legacy.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNBOOK_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

# Defaults are aligned to what node-setup actually produces (~/logos-node) — override any via env.
NS_ENV="$RUNBOOK_ROOT/node-setup/config/node.env"
[ -f "$NS_ENV" ] && source "$NS_ENV" 2>/dev/null || true
NODE_HOME="${NODE_HOME:-$HOME/logos-node}"

# Bind to THIS box's Tailscale IP by default: reachable from your phone over the tailnet, but NOT exposed
# on a public NIC. Falls back to loopback if Tailscale isn't up. The dashboard is unauthenticated and
# serves node logs, so binding all interfaces must be a deliberate choice: set HOST=0.0.0.0 explicitly
# (only on a trusted/NAT'd network).
if [ -z "${HOST:-}" ]; then
  HOST="$(tailscale ip -4 2>/dev/null | head -1 || true)"
  HOST="${HOST:-127.0.0.1}"
fi
PORT="${PORT:-8090}"
NODE_API="${NODE_API:-${API:-http://127.0.0.1:8080}}"
NODE_LOG_DIR="${NODE_LOG_DIR:-$NODE_HOME}"
NODE_CONFIG="${NODE_CONFIG:-$NODE_HOME/user_config.yaml}"
NODE_UNIT="${NODE_UNIT:-}"                       # the logoscore path has no systemd unit
export NODE_BINARY="${NODE_BINARY:-}"            # nor a raw binary
WALLET_PUBLIC_KEY="${WALLET_PUBLIC_KEY:-}"

if [[ -z "$WALLET_PUBLIC_KEY" && -f "$NODE_CONFIG" ]]; then
  WALLET_PUBLIC_KEY="$(awk '/funding_pk:/ { print $2; exit }' "$NODE_CONFIG" | tr -d '"')"
fi

if [[ -z "$WALLET_PUBLIC_KEY" && -f "$NODE_CONFIG" ]]; then
  WALLET_PUBLIC_KEY="$(grep -A1 known_keys "$NODE_CONFIG" | tail -1 | tr -d " " | cut -d: -f1)"
fi

# Blend Core: status is always shown; the on-chain join/withdraw buttons are OFF unless BLEND_ACTIONS
# is set truthy (the dashboard is unauthenticated and join/withdraw lock stake + pay a fee + publish IP).
BLEND_ACTIONS="${BLEND_ACTIONS:-0}"
BLEND_FLAG=()
case "$BLEND_ACTIONS" in 1|true|yes|on) BLEND_FLAG=(--allow-blend-actions) ;; esac

cd "$RUNBOOK_ROOT"
exec python3 dashboard/server.py \
  --host "$HOST" \
  --port "$PORT" \
  --node-api "$NODE_API" \
  --log-dir "$NODE_LOG_DIR" \
  --node-unit "$NODE_UNIT" \
  --wallet-public-key "$WALLET_PUBLIC_KEY" \
  --node-config "$NODE_CONFIG" \
  "${BLEND_FLAG[@]}"
