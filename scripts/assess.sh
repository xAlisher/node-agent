#!/usr/bin/env bash
# assess.sh — the first thing an agent runs. Reports what's already on this box and RECOMMENDS the next
# step, so the agent picks up exactly where work is needed (idempotent — never redoes done work).
# Read-only: touches nothing. Works with no arguments.
set -uo pipefail
API="${API:-http://localhost:8080}"
NODE_HOME="${NODE_HOME:-$HOME/logos-node}"
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
TOOLS=$([ -x "$NODE_HOME/bin/logoscore" ] && echo yes || (have logoscore && echo yes || echo no))
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
elif [ "$NODE_STATE" = absent ]; then
  say "→ BOX READY, NO NODE.  Run node-setup:   node-setup/scripts/setup-node.sh"
  say "  then verify:                          node-setup/scripts/healthcheck.sh"
elif [ "$NODE_STATE" = installed-stopped ]; then
  say "→ NODE INSTALLED BUT STOPPED. Re-start it:   node-setup/scripts/start-on-boot.sh"
  say "  (launches the daemon in a persistent tmux 'node' with extract-and-run; do NOT re-run generate_user_config — one-shot. See skills/logos-node-recovery.md)"
elif [ "$GREEN" = likely ]; then
  say "→ NODE IS UP & MESHED. Confirm green:   node-setup/scripts/healthcheck.sh"
  [ "$DASH" != 200 ] && say "  then bring up the dashboard:          dashboard/run.sh   (see dashboard/README.md)"
  [ "$PERSIST" = no ] && say "  ⚠ make it survive reboots (sudo-free): node-setup/scripts/install-persistence.sh"
  say "  fund it if not yet:                   grep -A3 known_keys $NODE_HOME/user_config.yaml  →  faucet"
else
  say "→ NODE RUNNING but not clearly healthy. Diagnose:   node-setup/scripts/healthcheck.sh"
  say "  and see skills/ (recovery, crash-loop, circuits-and-wallet)."
fi
say ""
