# Skill — join Blend Core (become a Blend provider) and stay eligible

**When:** the operator wants this node to earn as a **Blend Core** provider (the eligibility gate the
referral program's rewards depend on), and the node is already a **green** node (Online, peers > 0,
height climbing). Blend Core is opt-in and comes *after* a healthy node — don't offer it before green.

**What it needs first (0.3.x):** **one note of at least 1,000,000,000 (1B) on the node's Blend ZK key**
(the stake), plus a small balance on the SDP funding key (the fee, about 2k). Notes are not summed: a 1B
balance made of small mining notes does not qualify. Merge them by sending the amount to the Blend ZK key
with `POST /wallet/transactions/transfer-funds` (at most 32 input notes per tx, so merge in rounds). The
join call itself only submits the tx: with a smaller note it still returns a declaration id, and the tx is
then dropped at block validation (nothing locks, nothing shows up). The script refuses to join without a
qualifying note (`E_STAKE_TOO_SMALL`).

**What it does (on-chain, real money/stake):** declares this node an SDP Blend provider — it **locks a
note as stake**, **pays a declaration fee**, and **publishes this node's public IP on-chain** (the
locator, so other nodes can reach it). This is not reversible for free: undoing it is a `withdraw` that
winds down over a couple of epochs. So it is gated behind an explicit operator go (see below).

## The one tool: `node-setup/scripts/join_blend_core.py`

Stdlib Python, no pip. Everything is observable — each step prints progress, failures print
`ERROR <ID>: ...` and exit non-zero, and `--json` adds a final `RESULT: {...}` line you can parse.

```bash
# 1. ALWAYS start read-only — see where the node stands (safe, no mutation):
node-setup/scripts/join_blend_core.py status

# 2. Only after the operator says go, declare (idempotent — if already declared it just reports):
node-setup/scripts/join_blend_core.py join --yes            # --fund tops up the SDP (fee) key from the faucet
```

Common flags: `--config <user_config.yaml>` (default `$NODE_HOME/user_config.yaml`), `--node-api`
(default `http://127.0.0.1:8080`), `--fund` (faucet the SDP key if empty), `--yes` (confirm the
on-chain action non-interactively), `--locator`/`--locked-note` (overrides; the note must be on the Blend ZK key), `--json`.

## How you (the agent) should drive it — GATES, like the rest of this repo

1. **Run `status` first** and read it back to the operator in plain words: declared or not, whether the
   SDP funding key is funded, the largest note on the Blend ZK key vs the 1B minimum, and — if declared —
   the activation epoch, nonce and in-Core peer count.
2. **Explain the cost and get an explicit "go"** before `join`: it locks stake, pays a fee, and puts
   the node's public IP on-chain. Never run `join`/`withdraw` without that go (`--yes` is only for
   *after* the operator agrees).
3. **Run `join`** (add `--fund` if `status` said the SDP key is empty and the operator agrees to faucet
   it). Read the observable output — on success it prints the declaration id and the created/active
   epochs (active = created + 2). The declaration shows in `/mantle/sdp/declarations` as soon as its
   block lands (about a minute), long before it is active. On the 0.3.x testnet an epoch is 300,000
   slots (about 3.5 days), so activation is 3.5 to 7 days away depending on when in the epoch you join.
4. **Then the port-forward, or it never earns** (this is the #1 failure): declaring succeeds even when
   the node is unreachable, but Blend activity proofs need *incoming* peer traffic, so the `nonce` will
   stay flat and the node drops from Core. Tell the operator to **port-forward the Blend UDP port**
   (printed by the script, usually 3400, plus the node's quic/discovery port) to this machine's LAN IP.
   Pin `external_address` in `user_config.yaml` if the public IP is dynamic.
5. **Verify by watching the nonce advance** across epochs: re-run `status` — `nonce` climbing and
   `core_peers > 0` means it's earning. State changes apply at **epoch boundaries**, not instantly.

## Error IDs you'll see (and what to do)

- `E_NODE_UNREACHABLE` — node not up / wrong `--node-api`. Get the node green first.
- `E_SDP_KEY_MISSING` — no `sdp.wallet.funding_pk` in the config; this node can't pay the fee.
- `E_ZK_KEY_MISSING` — no `blend: … zk: secret_key_kms_id` in the config; this node can't stake.
- `E_NO_FUNDS` — the SDP key has nothing to pay the fee. Send it a little (e.g. 20M), or `--fund`.
- `E_STAKE_TOO_SMALL` — no single note >= 1B on the Blend ZK key. Merge into one note (see above).
- `E_NOTE_NOT_OURS` — `--locked-note` isn't a note on the Blend ZK key.
- `E_NO_LOCATOR` — couldn't resolve the public IP. Pin `external_address` or pass `--locator`.
- `E_JOIN_NETWORK` / `E_NO_DECL_ID` — outcome uncertain (a paid tx may have gone through). **Do not
  blindly retry** — run `status` first; a retry could duplicate a paid transaction.
- `E_NEEDS_CONFIRM` — you ran a mutating action without `--yes` and there's no TTY. Get the go, add `--yes`.

## The dashboard

The dashboard (`dashboard/`) shows a **Blend Core** panel (declared / earning / in-Core peers / nonce)
from the same script. The on-chain **Join / Withdraw buttons are off by default** because the dashboard
is unauthenticated; enable them deliberately on a trusted network with `BLEND_ACTIONS=1` when starting
it. Prefer running the script on the box for the actual join.

## References

- Operator debugging guide (symptoms → fixes, port-forward detail): the EcoDev
  `connect-node-to-blend-core` guide.
- The join contract (0.3.x, proven on sneg 2026-10-12): `1B note on the Blend ZK key + fee on the SDP
  funding key → POST /blend/join {locator, service_note_id} → {declaration_id, tx_id} → active at
  created+2`. Minimum stake: `sdp_config.min_stake.threshold` in the node's deployment settings; the check
  is `core/src/mantle/ops/sdp/declare.rs` in logos-blockchain.
