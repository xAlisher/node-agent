#!/usr/bin/env bash
# assess.sh — the first thing an agent runs. Reports what's already on this box and RECOMMENDS the next
# step, so the agent picks up exactly where work is needed (idempotent — never redoes done work).
# Read-only: touches nothing. Works with no arguments.
set -uo pipefail
API="${API:-http://localhost:8080}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Current testnet (v0.3.x) = the raw single-binary node in ~/logos-node-030 (node-030.env). The 0.2.x
# logoscore layout (~/logos-node) is only detected so it can be called out as legacy.
GENESIS_MS=""; source "$ROOT/node-setup/config/node-030.env" 2>/dev/null || true
if [ -z "${NODE_HOME:-}" ]; then
  NODE_HOME="$HOME/logos-node-030"
  [ -d "$NODE_HOME" ] || { [ -d "$HOME/logos-node" ] && NODE_HOME="$HOME/logos-node"; }
fi
say() { printf '%s\n' "$*"; }
have(){ command -v "$1" >/dev/null 2>&1; }

say "════════ logos-node-agent · box assessment ════════"

# ── box readiness ──
OS=$(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")
ARCH=$(uname -m); GLIBC=$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')
FREE_GB=$(df -BG --output=avail "$HOME" 2>/dev/null | awk 'NR==2{gsub(/[^0-9]/,"");print}')
MISS=""; for c in git curl jq tmux python3; do have "$c" || MISS="$MISS $c"; done   # core (gating)
REC="";  for c in gh; do have "$c" || REC="$REC $c"; done                           # recommended (non-gating)
have crontab || REC="$REC cron"                                                      # cron → sudo-free reboot-persistence
ldconfig -p 2>/dev/null | grep -qE 'libfuse3\.so\.3|libfuse\.so\.2' || REC="$REC fuse3"  # AppImage tools mount via FUSE
TS=$(have tailscale && (tailscale ip -4 2>/dev/null | head -1) || echo "NO"); CC=$(have claude && echo yes || echo no)
say ""; say "BOX"
say "  os=$OS  arch=$ARCH  glibc=${GLIBC:-?}  free=${FREE_GB:-?}G"
say "  deps missing:${MISS:- none}   recommended:${REC:- none}   tailscale=${TS:-NO}   claude-code=$CC"
BOX_READY=yes
[ "$ARCH" = "x86_64" ] || BOX_READY=no
[ -z "$MISS" ] || BOX_READY=no
[ "$TS" != "NO" ] || BOX_READY=no

# ── node state ──
say ""; say "NODE"
TOOLS=no; RAW=no
[ -x "$NODE_HOME/logos-blockchain-node" ] && TOOLS=yes && RAW=yes
[ "$TOOLS" = no ] && { [ -x "$NODE_HOME/bin/logoscore" ] || have logoscore; } && TOOLS=legacy-logoscore
CFG=$([ -f "$NODE_HOME/user_config.yaml" ] && echo yes || echo no)
J=$(curl -s --max-time 5 "$API/cryptarchia/info" 2>/dev/null)
# a real Logos node answers with a `state`/`mode` and a `height`; anything else on :8080 is not our node
st=$(echo "${J:-}" | jq -r '.cryptarchia_info.state // .mode // empty' 2>/dev/null)
h=$(echo "${J:-}"  | jq -r '.cryptarchia_info.height // .height // empty' 2>/dev/null)
NODE_STATE=absent; GREEN=no
if [ -n "$st" ] || [ -n "$h" ]; then
  peers=$(curl -s --max-time 5 "$API/network/info" 2>/dev/null | jq -r '.n_peers // 0' 2>/dev/null)
  say "  tools=$TOOLS  config=$CFG  api=UP  state=${st:-?}  height=${h:-?}  peers=${peers:-0}"
  NODE_STATE=running
  { [ "$st" = "Online" ] || [ "${peers:-0}" -gt 0 ]; } && GREEN=likely
  gen=$(curl -s --max-time 5 "$API/time/info" 2>/dev/null | jq -r '.genesis_time_unix_ms // empty' 2>/dev/null)
  if [ -n "$GENESIS_MS" ] && [ -n "$gen" ] && [ "$gen" != "$GENESIS_MS" ]; then
    say "  ⚠ OLD CHAIN: genesis=$gen, current testnet ($NODE_VERSION) is $GENESIS_MS"
    NODE_STATE=old-chain; GREEN=no
  fi
else
  [ -n "$J" ] && say "  (something answered on $API but it's not a Logos node — ignoring)"
  say "  tools=$TOOLS  config=$CFG  api=DOWN (no Logos node on $API)"
  [ "$TOOLS" = yes ] && [ "$CFG" = yes ] && NODE_STATE=installed-stopped
fi

# ── reboot-persistence (sudo-free @reboot cron) ──
PERSIST=no
have crontab && crontab -l 2>/dev/null | grep -Fq 'logos-node-agent @reboot' && PERSIST=yes
say "  reboot-persistence=$PERSIST (sudo-free @reboot cron)"

# ── dashboard ──
# It binds the Tailscale IP by default (else localhost), so try both.
DASH=000; DASH_URL="http://localhost:8090"
for h in "$(tailscale ip -4 2>/dev/null | head -1)" 127.0.0.1; do
  [ -n "$h" ] || continue
  c=$(curl -s --max-time 4 -o /dev/null -w '%{http_code}' "http://$h:8090/" 2>/dev/null || echo 000)
  if [ "$c" = 200 ]; then DASH=200; DASH_URL="http://$h:8090"; break; fi
done
say ""; say "DASHBOARD"
say "  $DASH_URL → $([ "$DASH" = 200 ] && echo UP || echo down)"

# ── recommendation ──
say ""; say "──────── RECOMMENDATION ────────"
[ -n "$REC" ] && say "  (recommended, optional: sudo apt install -y$REC  —  gh: GitHub ops · fuse3: lets the AppImage tools mount instead of extract; setup-node auto-falls-back if absent)"
if [ "$BOX_READY" != yes ]; then
  say "→ BOX NOT READY. Run the optional box-setup skill first (see box-setup/README.md):"
  [ -n "$MISS" ] && say "    system deps:  sudo apt update && sudo apt install -y$MISS"
  [ "$TS" = "NO" ] && say "    install + log in to Tailscale (box-setup/reference/03-tailscale.md)"
  [ "$ARCH" != x86_64 ] && say "    ⚠ arch $ARCH — this kit targets linux-x86_64"
elif [ "$NODE_STATE" = old-chain ] || [ "$TOOLS" = legacy-logoscore ]; then
  say "→ NODE IS ON AN OLD CHAIN / LEGACY LAYOUT. Move to the current testnet ($NODE_VERSION):"
  say "    node-setup/scripts/setup-node-030.sh   (stops the old node, moves its config/keys/state to oldchain-<ver>/, starts fresh)"
  say "  then verify:                          node-setup/scripts/healthcheck-030.sh"
elif [ "$NODE_STATE" = absent ]; then
  say "→ BOX READY, NO NODE.  Run node-setup:   node-setup/scripts/setup-node-030.sh"
  say "  then verify:                          node-setup/scripts/healthcheck-030.sh"
elif [ "$NODE_STATE" = installed-stopped ]; then
  say "→ NODE INSTALLED BUT STOPPED. Re-start it:   node-setup/scripts/start-030-on-boot.sh"
elif [ "$GREEN" = likely ]; then
  say "→ NODE IS UP & MESHED. Confirm green:   node-setup/scripts/healthcheck-030.sh   (Online + mining; ~1h after start)"
  [ "$DASH" != 200 ] && say "  then bring up the dashboard:          dashboard/run.sh   (see dashboard/README.md)"
  [ "$PERSIST" = no ] && say "  ⚠ make it survive reboots (sudo-free): node-setup/scripts/install-persistence-030.sh"
  say "  funding: PoW mining + auto-claim (started by setup-node-030.sh; no faucet needed)"
else
  say "→ NODE RUNNING but not clearly healthy. Diagnose:   node-setup/scripts/healthcheck-030.sh"
  say "  and see skills/ (recovery, crash-loop, circuits-and-wallet)."
fi
say ""
