---
id: logos-node-fresh-sync-copy-state
title: Bring up a fresh node by copying synced state — IBD-from-scratch fails (AllPeersFailed)
phase: setup
type: pattern
severity: high
severity_reason: A fresh node cannot reach Online via from-scratch IBD on a large testnet; it crash-loops
status: active
---

> **Legacy layout (0.1.x/0.2.x runbook).** Written for `state/live-v0.1.2/` with a
> `recovery/consensus/chain_service.json` snapshot. The 0.3.x node (`setup-node-030.sh`) has neither: its
> chain data is one RocksDB in `$NODE_HOME/state/db` and its log is `$NODE_HOME/node.log`. On 0.3.x, a node
> that can't recover its chain is resynced instead: stop it, move `state/` aside, start it again. Keys live
> in `user_config.yaml` + `keystore.yaml`, so they survive.

## Problem

A brand-new node has no chain. It tries an **Initial Block Download (IBD)** from the public
bootstrap peers, but on a large testnet (hundreds of thousands of blocks) this is unreliable:

```
ERROR ...bootstrap::ibd: No peers synced successfully during IBD
ERROR ...: Initial Block Download failed: AllPeersFailed. Initiating grace[period]
```

The node then **exits cleanly (code 0)** and systemd restarts it; each restart re-replays *all*
stored blocks ("N/N blocks applied during initialization", N growing every cycle) so it gets
slower and never reaches the live tip. Symptoms: public bootstrap host serves a chunk then drops
the stream (`sending stopped by peer: error 0`); even adding one healthy peer doesn't fix it
(internal `BlockProvider(RecvError(()))`).

## The real fix: don't run IBD

IBD has been broken on this testnet since ~mid-May. The upstream-recommended fix is to `init` with
**peerless** bootstrap multiaddrs (omit `/p2p/<peerid>`) → `ibd.peers: []` → IBD off; the node then
syncs via normal online sync. **The copy-state recipe below is an optional speed-up** (instant
Online) or a rescue if online sync stalls — not the primary fix. (We hit AllPeersFailed only because
an old community runbook config supplied peerids.)

## Recipe — copy the synced chain DB from a healthy canonical node

Only valid if the donor is on the **canonical** chain. You copy *chain data* but keep *your own*
`node.yaml` (your own `node_key`), so there's no p2p identity collision.

```bash
# easiest: use the starter kit's snapshot fetcher
systemctl --user stop logos-node
~/logos-node-starter/scripts/fetch-snapshot.sh
systemctl --user start logos-node

# OR copy directly from a node you control (stop both for a consistent RocksDB snapshot):
ssh <donor> 'systemctl --user stop logos-node'
systemctl --user stop logos-node
rm -rf ~/logos-blockchain-runbook/state/db ~/logos-blockchain-runbook/state/recovery
# mind base_folder mapping (donor ./state/live-v0.1.2  vs  fresh-init ./state):
ssh <donor> 'cd ~/logos-blockchain-runbook/state/<donor-base> && tar c --exclude=logs --exclude="*.bak*" db recovery' \
  | (cd ~/logos-blockchain-runbook/state && tar x)
ssh <donor> 'systemctl --user start logos-node'   # restart donor immediately
systemctl --user start logos-node
```

Only `db/` + `recovery/` are needed (~600 MB). The recovery snapshot lets the node **jump straight
to the tip — Online in seconds, no full replay.** A wallet-key mismatch in the copied state is
harmless (the node syncs regardless); it just won't show the donor's balance.

Verify: `curl -s http://127.0.0.1:8080/cryptarchia/info` → `mode` reaches `Online`, height tracks
the network; `systemctl --user show logos-node -p NRestarts --value` → `0`.

## Bonus — derive a node's libp2p peer ID from its `node_key`

The node never logs its own peer ID, but it's deterministic from `node_key` (32-byte ed25519 seed):

```python
import hashlib
ALPH='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
def b58(b):
    n=int.from_bytes(b,'big'); s=''
    while n: n,r=divmod(n,58); s=ALPH[r]+s
    return ALPH[0]*(len(b)-len(b.lstrip(b'\x00')))+s
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
pub=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(NODE_KEY)).public_key()\
    .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
proto=bytes([0x08,0x01,0x12,0x20])+pub          # protobuf PublicKey{Ed25519, pub}
print(b58(bytes([0x00,len(proto)])+proto))      # identity multihash -> base58 -> 12D3KooW...
```
A wrong peer ID in a dial shows `Unexpected peer ID <REAL> at Dialer .../p2p/<yours>` — which
reveals the real one, so it's self-correcting.

## See also
- logos-node-circuits-and-wallet-pitfalls (first-slot-win panic, wallet restart crash)
- logos-node-recovery / logos-node-crash-loop-tip-lib (dirty-shutdown recovery)
