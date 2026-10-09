#!/usr/bin/env bash
# start-030-on-boot.sh — bring the RAW 0.3.0 node (logos-blockchain-node) back up after a reboot.
# SUDO-FREE: invoked by a user `@reboot` crontab entry (install-persistence-030.sh). Idempotent: if the
# node API already answers it leaves it. cron runs with a bare env, so we set HOME/PATH and log to boot.log.
# Supersedes start-on-boot.sh (which started the 0.2.x logoscore + module dead-chain path).
set -uo pipefail
export HOME="${HOME:-/home/$(id -un)}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"          # node-setup/
REPO_ROOT="$(cd "$HERE/.." && pwd)"
source "$HERE/config/node-030.env" 2>/dev/null || true
NODE_HOME="${NODE_HOME:-$HOME/logos-node-030}"
API="${API:-http://127.0.0.1:${API_PORT:-8080}}"
NODE_TMUX="${NODE_TMUX:-node}"                                    # sneg overrides to 'bcnode' in its @reboot line
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
mkdir -p "$NODE_HOME"; LOG="$NODE_HOME/boot.log"
ts(){ date '+%Y-%m-%d %H:%M:%S'; }
log(){ printf '%s  %s\n' "$(ts)" "$*" >>"$LOG" 2>&1; }

log "=== start-030-on-boot: waking after reboot (tmux '$NODE_TMUX', api $API) ==="

[ -f "$NODE_HOME/user_config.yaml" ] || { log "no $NODE_HOME/user_config.yaml — node not set up here; nothing to start"; exit 0; }
[ -x "$NODE_HOME/logos-blockchain-node" ] || { log "no logos-blockchain-node binary in $NODE_HOME; nothing to start"; exit 0; }

# cron @reboot fires early — wait (up to ~60s) for the network.
for _ in $(seq 1 30); do curl -fsS --max-time 3 https://github.com >/dev/null 2>&1 && { log "network up"; break; }; sleep 2; done

# 1. Node: start in a persistent tmux session unless the API already answers (resumes from on-disk state+keys).
if curl -s --max-time 5 "$API/cryptarchia/info" >/dev/null 2>&1; then
  log "node API already up on $API — leaving it"
else
  log "starting logos-blockchain-node in tmux '$NODE_TMUX'"
  tmux kill-session -t "$NODE_TMUX" 2>/dev/null || true
  tmux new-session -d -s "$NODE_TMUX" "cd $NODE_HOME && ./logos-blockchain-node user_config.yaml >>$NODE_HOME/node.log 2>&1"
fi

# 2. Wait for Online, then (re)enable PoW mining + auto-claim (both are RUNTIME state, cleared by a restart).
online=0
for _ in $(seq 1 90); do
  st=$(curl -s --max-time 5 "$API/cryptarchia/info" 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin)['cryptarchia_info']['state'])" 2>/dev/null || true)
  [ "$st" = "Online" ] && { online=1; break; }
  sleep 8
done
if [ "$online" = 1 ]; then
  curl -s --max-time 10 -X PUT "$API/pow/mining/start"     >/dev/null 2>&1 && log "PoW mining started"
  curl -s --max-time 10 -X PUT "$API/pow/auto-claim/start" >/dev/null 2>&1 && log "PoW auto-claim armed"
else
  log "warn: node did not reach Online within ~12min — mining NOT enabled (check $NODE_HOME/node.log)"
fi

# 3. Dashboard (optional): (re)start if present + not already up. Reads the same :API endpoints.
if [ -f "$REPO_ROOT/dashboard/run.sh" ] && command -v tmux >/dev/null 2>&1; then
  if tmux has-session -t dashboard 2>/dev/null; then
    log "dashboard tmux already up"
  else
    tmux new-session -d -s dashboard "cd '$REPO_ROOT' && bash dashboard/run.sh >>'$NODE_HOME/dashboard.log' 2>&1" \
      && log "dashboard started" || log "warn: could not start dashboard"
  fi
fi

log "=== start-030-on-boot done ==="
