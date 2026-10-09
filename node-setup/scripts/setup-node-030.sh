#!/usr/bin/env bash
# setup-node-030.sh — bring a box to a running Logos 0.3.x blockchain node (currently 0.3.1, the 10-09 relaunch) using the RAW single-binary
# architecture (logos-blockchain-node). Supersedes setup-node.sh (logoscore+module) for 0.3.0+.
# Verified end-to-end on sneg 2026-09-30 (parallel node → correct 0.3.0 genesis, syncing, mining armed).
#
# Ref: https://github.com/logos-blockchain/logos-blockchain/releases
# Usage:  node-setup/scripts/setup-node-030.sh     (reads config/node-030.env)
#   Parallel to a live 0.2.x node on the same box:  SWARM_PORT=3010 API_PORT=8081 setup-node-030.sh
#   Fast Online for a caught-up node:               PROLONGED_BOOTSTRAP_SECS=120 setup-node-030.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/config/node-030.env"

log() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()  { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── Step 0: preflight ────────────────────────────────────────────────────────
log "Preflight"
[ "$(uname -m)" = "x86_64" ] || die "expected x86_64 (this asset is linux-x86_64)"
for c in curl tar tmux python3 jq; do command -v "$c" >/dev/null 2>&1 || echo "  ⚠ missing $c (install: sudo apt install -y $c)"; done
command -v curl >/dev/null 2>&1 || die "curl required"
mkdir -p "$NODE_HOME"; cd "$NODE_HOME"
ok "x86_64 · NODE_HOME=$NODE_HOME · swarm=$SWARM_PORT api=$API_PORT"

# ── Step 1: fetch the node binary ────────────────────────────────────────────
log "Fetch logos-blockchain-node ${NODE_VERSION}"
if [ -x "$NODE_HOME/logos-blockchain-node" ] && "$NODE_HOME/logos-blockchain-node" --version 2>/dev/null | head -1 | grep -q " ${NODE_VERSION}$"; then
  ok "binary ${NODE_VERSION} already present"
else
  if command -v gh >/dev/null 2>&1; then
    gh release download "$NODE_VERSION" --repo "$NODE_RELEASE_REPO" --pattern "$NODE_ASSET" --dir "$NODE_HOME" --clobber
  else
    curl -fL -o "$NODE_HOME/$NODE_ASSET" \
      "https://github.com/${NODE_RELEASE_REPO}/releases/download/${NODE_VERSION}/${NODE_ASSET}"
  fi
  tar xzf "$NODE_HOME/$NODE_ASSET" -C "$NODE_HOME"
  chmod +x "$NODE_HOME/logos-blockchain-node"
  ok "extracted $NODE_ASSET"
fi
# The network (testnet/devnet + ver) is baked into the binary; a mismatch = connects-but-never-syncs.
# The real check is Step 5 (correct genesis + height climbing).

# ── Step 2: generate a fresh config + keystore (once per chain) ──────────────
# A config from another chain (e.g. 0.3.0 before the 10-09 relaunch) still points at that chain's blocks in
# ./state, and the node panics replaying them ("The chain can't be recovered from storage ... FutureBlock").
# The chain a config belongs to is recorded in .chain-version; installs from before that marker are 0.3.0.
MARK="$NODE_HOME/.chain-version"
if [ -f "$NODE_HOME/user_config.yaml" ]; then
  OLD="$(cat "$MARK" 2>/dev/null || echo 0.3.0)"
  if [ "$OLD" != "$NODE_VERSION" ]; then
    log "Chain changed ($OLD -> $NODE_VERSION): moving the old config, keys and chain data aside"
    tmux kill-session -t node 2>/dev/null || true
    pkill -f "$NODE_HOME/logos-blockchain-node" 2>/dev/null || pkill -f "./logos-blockchain-node user_config.yaml" 2>/dev/null || true
    sleep 2
    ARCH="$NODE_HOME/oldchain-$OLD"; mkdir -p "$ARCH"
    for f in user_config.yaml keystore.yaml state db; do if [ -e "$NODE_HOME/$f" ]; then mv "$NODE_HOME/$f" "$ARCH/"; fi; done
    ok "moved to $ARCH (old keys kept there; the new chain gets fresh keys)"
  fi
fi
log "Init user_config.yaml + keystore.yaml (fresh keys)"
if [ -f "$NODE_HOME/user_config.yaml" ]; then
  ok "user_config.yaml exists (leaving it — delete to regenerate; note: new chain = fresh keys)"
else
  "$NODE_HOME/logos-blockchain-node" init-config -o "$NODE_HOME/user_config.yaml" -k "$NODE_HOME/keystore.yaml" --overwrite
  ok "generated"
fi
echo "$NODE_VERSION" > "$MARK"

# ── Step 3: patch config (ports, peers, ibd.peers, bootstrap window) ─────────
log "Patch config (ports, initial_peers, ibd.peers, bootstrap window)"
PATCHER="$NODE_HOME/.patch_config.py"
cat > "$PATCHER" <<'PYEOF'
import os, sys, json
PATH = sys.argv[1]
peers    = json.loads(os.environ["BOOTSTRAP_PEERS"])
peer_ids = json.loads(os.environ["BOOTSTRAP_PEER_IDS"])
swarm_port = os.environ["SWARM_PORT"]; api_port = os.environ["API_PORT"]
boot = os.environ["PROLONGED_BOOTSTRAP_SECS"]
with open(PATH) as f: lines = f.readlines()
out=[]; in_swarm=False; done_port=done_ip=done_ibd=False
for ln in lines:
    s=ln.rstrip("\n"); st=s.strip()
    if st=="swarm:": in_swarm=True
    # swarm.port (first "port: 3000" under swarm)
    if in_swarm and not done_port and st.startswith("port:"):
        ind=s[:len(s)-len(s.lstrip())]; out.append(f"{ind}port: {swarm_port}\n"); done_port=True; continue
    # initial_peers (multiaddrs)
    if not done_ip and st.startswith("initial_peers:"):
        ind=s[:len(s)-len(s.lstrip())]; out.append(f"{ind}initial_peers:\n")
        for p in peers: out.append(f"{ind}- {p}\n")
        done_ip=True; continue
    # bootstrap.ibd.peers (bare peer-IDs — the empty-by-default gap)
    if not done_ibd and st=="peers: []" and s.startswith("        peers"):
        ind=s[:len(s)-len(s.lstrip())]; out.append(f"{ind}peers:\n")
        for pid in peer_ids: out.append(f"{ind}- {pid}\n")
        done_ibd=True; continue
    # api listen_address
    if "listen_address: 127.0.0.1:8080" in s:
        out.append(s.replace("8080", api_port)+"\n"); continue
    # prolonged_bootstrap_period
    if st.startswith("prolonged_bootstrap_period:"):
        ind=s[:len(s)-len(s.lstrip())]; out.append(f"{ind}prolonged_bootstrap_period: '{float(boot):.9f}'\n"); continue
    out.append(ln)
with open(PATH,"w") as f: f.writelines(out)
print(f"port={done_port} initial_peers={done_ip} ibd.peers={done_ibd}")
PYEOF
BOOTSTRAP_PEERS="$BOOTSTRAP_PEERS" BOOTSTRAP_PEER_IDS="$BOOTSTRAP_PEER_IDS" \
  SWARM_PORT="$SWARM_PORT" API_PORT="$API_PORT" PROLONGED_BOOTSTRAP_SECS="$PROLONGED_BOOTSTRAP_SECS" \
  python3 "$PATCHER" "$NODE_HOME/user_config.yaml"
"$NODE_HOME/logos-blockchain-node" --check-config "$NODE_HOME/user_config.yaml" >/dev/null 2>&1 \
  && ok "config valid" || die "config failed --check-config"

# ── Step 4: start the node in tmux 'node' ────────────────────────────────────
log "Start the node (tmux 'node')"
if curl -s -m4 "$API/cryptarchia/info" >/dev/null 2>&1; then
  ok "node API already up — leaving it"
else
  tmux kill-session -t node 2>/dev/null || true
  tmux new-session -d -s node "cd $NODE_HOME && ./logos-blockchain-node user_config.yaml >$NODE_HOME/node.log 2>&1"
  ok "launched"
fi

# ── Step 5: wait for the correct chain + Online, then enable mining ─────────
log "Wait for Online on the ${NODE_VERSION} chain, then start PoW mining"
online=0
for _ in $(seq 1 90); do
  ci=$(curl -s -m5 "$API/cryptarchia/info" 2>/dev/null) || true
  st=$(echo "$ci" | python3 -c "import json,sys;print(json.load(sys.stdin)['cryptarchia_info']['state'])" 2>/dev/null || true)
  [ "$st" = "Online" ] && { online=1; break; }
  sleep 8
done
if [ "$online" = 1 ]; then
  curl -s -m10 -X PUT "$API/pow/mining/start" >/dev/null 2>&1 && ok "PoW mining started (PUT /pow/mining/start)"
  curl -s -m10 -X PUT "$API/pow/auto-claim/start" >/dev/null 2>&1 && ok "PoW auto-claim armed (PUT /pow/auto-claim/start)"
  echo "  verify:  curl -s $API/pow/status | jq"
else
  echo "  ⚠ node not Online yet (fresh chain block production can be sparse). Once Online, run:"
  echo "     curl -X PUT $API/pow/mining/start ; curl -X PUT $API/pow/auto-claim/start"
fi

echo
echo "Node:    tmux attach -t node     (log: $NODE_HOME/node.log)"
echo "Chain:   curl -s $API/cryptarchia/info | jq   (want state=Online, height climbing; genesis_time_unix_ms=${GENESIS_MS})"
echo "Mining:  curl -s $API/pow/status | jq         · claimable: curl -s $API/pow/rewards/claimable | jq"
echo "Vouchers (leader): curl -s $API/leader/claim/vouchers | jq · claim: curl -X POST $API/leader/claim"
