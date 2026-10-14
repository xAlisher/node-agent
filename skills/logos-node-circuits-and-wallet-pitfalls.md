---
id: logos-node-circuits-and-wallet-pitfalls
title: Two delayed-onset node crashes — missing circuits on first slot win, wallet crash on quick restart
phase: ops
type: pattern
severity: medium
severity_reason: Node runs fine for hours then crashes the first time it wins a slot, or the wallet silently dies after a fast restart
status: active
---

> **Legacy (0.1.x/0.2.x runbook).** The paths and the circuits v0.4.2 bundle below are from the old runbook
> layout and are not re-verified on the 0.3.x single-binary node (`setup-node-030.sh`).

## Pitfall 1 — node panics the first time it WINS a slot (circuits missing)

The node runs fine, syncs, connects to peers — then crashes the first time it wins a slot lottery
and tries to propose a block (could be hours/days after setup):

```
ERROR ...panic: A panic occurred
  panic.payload="Could not find logos-blockchain-circuits directory.
  Please either: 1. Set the LOGOS_BLOCKCHAIN_CIRCUITS environment variable, or
  2. Place the circuits at /home/<user>/.logos-blockchain-circuits"
```

**Cause:** the node needs the ZK circuits to generate a leadership proof and looks for them at
`~/.logos-blockchain-circuits` by default. Easy to miss because nothing fails until the first win.

**Fix:** symlink the versioned circuits dir to the expected path (point at the versioned dir, not
its parent), and/or set the env var in your launcher:

```bash
ln -sfn ~/logos-blockchain-runbook/artifacts/circuits/logos-blockchain-circuits-v0.4.2-linux-x86_64 \
        ~/.logos-blockchain-circuits
ls ~/.logos-blockchain-circuits   # must show: poc pol poq prover verifier VERSION zksign
# (run-node.sh also exports LOGOS_BLOCKCHAIN_CIRCUITS=.../artifacts/circuits/...)
```
If `ls` shows `logos-blockchain-circuits-v*` instead of `poc/pol/...`, the symlink points one level
too high — redo it. Do this at setup time, before the node ever wins a slot.

## Pitfall 2 — wallet service crash after a quick restart

If the node is killed mid-run and restarted fast, the wallet service may fail:

```
ERROR: Failed to apply backfill block to wallet  err=Requested wallet state for unknown block
ERROR: ServiceStatuses: Wallet: ServiceStatus::Starting
```

The wallet channel closes permanently; the node keeps syncing but
`/wallet/<pk>/balance` returns 408 and the dashboard shows no balance.

**Fix:** just restart the node once more — on the second clean start the wallet service initialises
correctly. `systemctl --user restart logos-node`.

## See also
- logos-node-fresh-sync-copy-state (bringing a node Online in the first place)
- logos-node-recovery (dirty-shutdown crash loop)
