---
id: logos-node-auto-rollback-guard
title: Self-heal logos-node dirty-shutdown crash loop with a pre-start guard in the launcher
phase: recovery
type: pattern
severity: high
severity_reason: Without it, every dirty shutdown needs a manual recovery edit or the node crash-loops indefinitely
machines: ["sneg", "optiplex"]
distro: "ubuntu-24.04"
kernel: ""
source: extracted-local
last_used: "2026-06-21"
created: "2026-06-16"
status: active
---

> **Legacy layout (0.1.x/0.2.x runbook).** Written for `state/live-v0.1.2/` with a
> `recovery/consensus/chain_service.json` snapshot. The 0.3.x node (`setup-node-030.sh`) has neither: its
> chain data is one RocksDB in `$NODE_HOME/state/db` and its log is `$NODE_HOME/node.log`. On 0.3.x, a node
> that can't recover its chain is resynced instead: stop it, move `state/` aside, start it again. Keys live
> in `user_config.yaml` + `keystore.yaml`, so they survive.

## Problem

logos-node crash-loops after every dirty shutdown (orphan tip, upstream #2569 — see
`logos-node-crash-loop-tip-lib`). Manually editing the recovery snapshot each time is not
sustainable. Automate the `tip = lib` fix as a guard the launcher runs before each start.

**This guard now ships in `scripts/run-node.sh` by default** — a fresh install self-heals out
of the box. The recipe below is the reference / explanation for that code.

> **The trigger MUST match how your node logs.** This kit's init-generated `node.yaml` logs to
> **stdout → journald**, so the panic is in `journalctl --user -u logos-node`. Sneg's older
> config set `tracing.logger.stdout/stderr: false`, so its panic is **file-only** under
> `state/.../logs/`. A guard that only tails a log file silently never fires on a journald box
> (this is exactly why optiplex shipped without working self-heal and crash-looped on
> 2026-06-21 after a power cut). The shipped guard checks **journald first, then a log file**,
> so it works either way.

## Recipe

Call `guard_recovery` from `~/logos-blockchain-runbook/run-node.sh` before launching the node.
With systemd `Restart=always` (`RestartUSec=5s`), a dirty boot self-heals in one restart cycle:
first launch panics → systemd restarts → guard sees the panic → rolls `tip = lib` → node
recovers and re-syncs `(lib, head]`.

```bash
# Recovery snapshot path, auto-detected for base_folder `state/` OR `state/live-vX.Y.Z/`.
RECOVERY="$(ls -t "$RUNBOOK"/state*/recovery/consensus/chain_service.json 2>/dev/null | head -1 || true)"

# True if the previous launch died on the orphan-tip recovery panic.
# Journald first (stdout logging), file-only logs as fallback.
recovery_panic_detected() {
  local sig="Could not retrieve block parent.*during recovery"
  journalctl --user -u logos-node -n 50 --no-pager 2>/dev/null | grep -qa "$sig" && return 0
  local logdir latest
  logdir="$(ls -d "$RUNBOOK"/state*/logs 2>/dev/null | head -1 || true)"
  [ -n "$logdir" ] || return 1
  latest="$(ls -t "$logdir" 2>/dev/null | head -1 || true)"
  [ -n "$latest" ] || return 1
  tail -n 60 "$logdir/$latest" 2>/dev/null | grep -qa "$sig"
}

guard_recovery() {
  [ -n "$RECOVERY" ] && [ -f "$RECOVERY" ] || return 0
  recovery_panic_detected || return 0
  echo "[run-node] recovery panic detected — evaluating auto-rollback"   # -> journald
  python3 - "$RECOVERY" <<'PY'
import json, os, shutil, sys
p = sys.argv[1]
try:
    d = json.load(open(p))
except Exception as e:
    print(f"[run-node] cannot parse recovery snapshot ({e}); leaving for manual fix"); sys.exit(0)
if d.get("tip") == d.get("lib"):
    print("[run-node] tip already == lib; nothing to do"); sys.exit(0)
if d.get("storage_blocks_to_remove") != []:
    print("[run-node] unexpected storage_blocks_to_remove; leaving for manual fix"); sys.exit(0)
shutil.copy2(p, p + ".bak.autorollback")
d["tip"] = d["lib"]
json.dump(d, open(p + ".tmp", "w"))
os.replace(p + ".tmp", p)
print("[run-node] auto-rollback applied: tip <- lib")
PY
}
guard_recovery
```

## Why

Design rules that matter, learned the hard way:
- **Trigger on the panic signal, NOT on `tip != lib`.** A healthy syncing node normally has
  tip ahead of lib, so keying off `tip != lib` would clobber good state on every restart. The
  recovery panic is the only reliable pre-start signal — the node dies before the API binds,
  so nothing is queryable over HTTP.
- **Read the panic where the node actually logs it** — journald for stdout configs, the log
  file for `stdout/stderr: false` configs. Check journald first, file second. Getting this
  wrong = a guard that looks installed but never fires.
- **Idempotent** — once `tip == lib` it never fires again, so deeper corruption fails loudly
  for manual intervention instead of silently looping rollbacks.
- **Conservative** — bails without mutating on unparseable JSON or unexpected
  `storage_blocks_to_remove`.

## Verifying it works (without staging a real power cut)

You can't easily reproduce the exact orphan condition on demand, so test the two halves:
1. **Trigger** — confirm the regex matches your node's real panic text:
   `journalctl --user -u logos-node --since today | grep -a "Could not retrieve block parent.*during recovery"`
2. **Logic** — run the embedded python against synthetic snapshots: orphan (`tip!=lib`,
   `storage_blocks_to_remove: []`) → fixes + writes `.bak.autorollback`; rerun → no-op;
   non-empty `storage_blocks_to_remove` → bails; `tip==lib` → no-op.
3. **Healthy restart** — `systemctl --user restart logos-node` and confirm the guard prints
   nothing (clean no-op) and tip is preserved (NOT clobbered to lib).

## See also

- logos-node-crash-loop-tip-lib (the manual fix this automates)
