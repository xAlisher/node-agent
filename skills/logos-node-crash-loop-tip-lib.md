---
id: logos-node-crash-loop-tip-lib
title: Recover logos-node crash loop after dirty shutdown by setting tip = lib
phase: recovery
type: pattern
severity: high
severity_reason: Without this fix the node crash-loops every ~10 seconds and IBD never starts
machines: ["sneg"]
distro: "ubuntu-24.04"
kernel: ""
source: extracted-local
last_used: "2026-06-16"
created: "2026-05-09"
status: active
---

> **Legacy layout (0.1.x/0.2.x runbook).** Written for `state/live-v0.1.2/` with a
> `recovery/consensus/chain_service.json` snapshot. The 0.3.x node (`setup-node-030.sh`) has neither: its
> chain data is one RocksDB in `$NODE_HOME/state/db` and its log is `$NODE_HOME/node.log`. On 0.3.x, a node
> that can't recover its chain is resynced instead: stop it, move `state/` aside, start it again. Keys live
> in `user_config.yaml` + `keystore.yaml`, so they survive.

## Problem

After a dirty shutdown (kernel panic, power loss, watchdog reset), logos-node crash-loops:
```
ERROR: Could not retrieve block parent for 0x<tip-hash> from storage during recovery
PANIC at services/chain/chain-service/src/lib.rs:1089
```
The tip block is stored but its parent is missing from RocksDB — partial write during shutdown.
Recurs on every dirty shutdown (seen 2026-06-09 / 11 / 16). Upstream bug
`logos-blockchain/logos-blockchain#2569` (still open).

**Logs are file-only:** `node.yaml` sets `tracing.logger.stdout/stderr: false`, so
`journalctl --user -u logos-node` shows ONLY systemd's restart lines — the actual panic
is in `state/live-v0.1.2/logs/logos-blockchain.<YYYY-MM-DD-HH>`. Always read the file.

## Recipe

```bash
# Stop the crash loop
systemctl --user stop logos-node

# Backup
cp state/live-v0.1.2/recovery/consensus/chain_service.json \
   state/live-v0.1.2/recovery/consensus/chain_service.json.bak

# Set tip = lib
python3 -c "
import json
path = 'state/live-v0.1.2/recovery/consensus/chain_service.json'
with open(path) as f:
    d = json.load(f)
print('tip before:', d['tip'])
print('lib:       ', d['lib'])
d['tip'] = d['lib']
with open(path, 'w') as f:
    json.dump(d, f)
print('tip after: ', d['tip'])
"

# Restart
systemctl --user start logos-node

# Verify (logs are file-only — NOT journald): look for "0 blocks recovered"
tail -f "state/live-v0.1.2/logs/$(ls -t state/live-v0.1.2/logs/ | head -1)"
# then confirm API is back and advancing:
curl -s http://127.0.0.1:8080/cryptarchia/info   # expect HTTP 200, height rising, mode Bootstrapping->Online
```

This is the manual fix. The launcher now self-heals automatically on restart — see
`logos-node-auto-rollback-guard`.

## Why

`lib` (last irreversible block) is the safe checkpoint. Setting `tip = lib` tells the
chain service to start from the finalized block and sync forward, skipping the corrupt
tip. You lose only the unfinalized blocks between lib and the corrupt tip (typically a few).

**Sneg paths:** working dir `~/logos-blockchain-runbook/`, state `state/live-v0.1.2/`,
logs `state/live-v0.1.2/logs/`, recovery snapshot
`state/live-v0.1.2/recovery/consensus/chain_service.json`,
service `~/.config/systemd/user/logos-node.service` → `run-node.sh`.

## See also

- logos-node-auto-rollback-guard (automated self-heal of this exact crash loop)
- logos-node-migration (docs/skills — port conflict resolution, full restart procedure)
