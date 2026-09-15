# Logos node 0.2.4 — agent setup runbook

Bring a **clean Ubuntu 24.04 box (tailscale-ssh already up)** to a **green Logos blockchain node
+ dashboard**, the way the upstream docs prescribe, in one prompt. Built for the 2026-08-13 demo;
reusable by anyone's agent.

> **Skip** (assumed done): Ubuntu install, SSH/Tailscale, Claude Code login.
> **Target:** node = `blockchain_module` **0.2.4** via `logoscore`/`lgpd`/`lgpm` (core tools `0.2.0`).
> **Green** = `n_peers > 0` AND `height` climbing → eventually `state: Online`. Judge by **height**, never the UI.

## Prerequisites & dependencies

**Hardware / OS**
- Ubuntu 24.04 LTS, **x86_64** · **glibc ≥ 2.39** (24.04 ships 2.39) · **≥ 64 GB** free disk · ~**8 GB** RAM.

**Reboot-persistence is automatic and sudo-free.** `setup-node.sh` installs a user **`@reboot` crontab**
(`install-persistence.sh` → `start-on-boot.sh`) that restarts the node + dashboard on every boot — **no
`systemd` linger, no sudo**. That covers OS reboots. (SSH logout is already covered: the node is a detached
`logoscore` daemon, the dashboard runs in tmux.)

**BIOS — auto-restart after power *outage*** (optional, one-time, needs physical/BIOS access):
- To also survive a full power loss, set BIOS/UEFI **"Restore on AC Power Loss"** (a.k.a. *AC Power Recovery* /
  *After Power Failure* / *Restore Power State*) to **Power On** (or **Last State**), so the machine powers
  itself back on after an outage. On that boot, the `@reboot` cron above brings the node back automatically.
  This is firmware, not sudo. BIOS-power-on = the physical layer; `@reboot` cron = the software layer.

**Assumed already set up** (the box-prep phase — done once, before the sudo-free workshop):
- **Tailscale** + **Tailscale SSH** (this is our access path *and* how phones reach the dashboard).
- **Claude Code** (the on-box agent that runs this skill).

**System packages** — the only step that needs **sudo**, done **once during box prep**:
```bash
sudo apt update && sudo apt install -y git curl jq tmux python3 cron gh fuse3
```
- `curl`, `python3`, `cron` are usually already on Ubuntu 24.04; **`jq` and `tmux` are the ones commonly missing**.
- `jq` → verify/healthcheck JSON · `tmux` → keep the dashboard alive without a login manager · `cron` →
  sudo-free **reboot-persistence** (a user `@reboot` job restarts the node on boot — no `systemd --user`
  linger) · `python3` (stdlib only, no pip) → the dashboard · `git` → clone the repo (`gh` optional, for
  GitHub ops) · `fuse3` → the node tools are AppImages that mount via FUSE (setup auto-extracts if absent).
- **After this, everything is sudo-free.** `install-node-tools.sh` drops `logoscore`/`lgpd`/`lgpm` into
  `~/logos-node/bin` (no sudo); the node, dashboard, and cleanup all run in userspace. The one thing that
  still needs sudo — **reboot-persistence** (`loginctl enable-linger`) — is deferred to end-of-workshops.

**Network egress:** GitHub (tools + module download) and the testnet **bootstrap peers over UDP/QUIC**
(outbound UDP must not be blocked). Dashboard is reached over the tailnet, not the public internet.

`scripts/setup-node.sh` re-checks all of the above in preflight and prints the exact `apt` line for anything missing.

## The flow (each step = a script; bump only `config/node.env` per release)

| # | Do | Command *(run from the repo root)* | sudo? |
|---|----|--------|-------|
| 0–4 | preflight → install tools → install+load `blockchain_module` → configure (peers) → start → **install reboot-persistence** | `node-setup/scripts/setup-node.sh` | no |
| — | verify green (height climbing) | `node-setup/scripts/healthcheck.sh` | no |
| 5 | dashboard on `:8090`, reached over the tailnet (no `tailscale serve`) | `dashboard/run.sh` | no |
| 6 | fund it (curl the faucet, no web form) | `node-setup/scripts/fund-node.sh` *(or `AUTOFUND=1`)* | no |
| — | reset box to blank state (node + cron removed, keys backed up) | `node-setup/scripts/uninstall.sh` | no |
| 7 | *(optional)* survive a power **outage** too | BIOS "Restore on AC Power Loss → Power On" (firmware, one-time) | firmware |

The **whole path is sudo-free** — tools install into `~/logos-node/bin`; node, dashboard, cleanup, **and
reboot-persistence** all run in userspace. Reboot-persistence is installed **by default** as a user `@reboot`
crontab (step 4 above; `PERSIST=0 node-setup/scripts/setup-node.sh` to skip) — **no `loginctl enable-linger`,
no sudo**. The only sudo is box-prep `apt` (done once). Surviving a power *outage* is the one-time BIOS setting
(firmware, not sudo).

## Run it

```bash
git clone https://github.com/xAlisher/logos-node-agent.git && cd logos-node-agent
node-setup/scripts/setup-node.sh      # install + config + start + dashboard; node bootstrapping (~1h to Online)
node-setup/scripts/healthcheck.sh     # → GREEN when peers > 0 and height climbs
# the dashboard now auto-starts on :8090 (tmux 'dashboard') — open it from your phone over the tailnet
```

**Fast workshop path — pre-warm the box (green in ~5–10 s).** The ~255 MB download (tools + module) is the
bulk of setup. Do it ONCE during box-prep, then the workshop run skips it:
```bash
PREWARM=1 node-setup/scripts/setup-node.sh   # box-prep: stage tools+module, do NOT start the node
# … at workshop time, on the same box:
node-setup/scripts/setup-node.sh             # skips download+install → GREEN in ~5–10 s
```
Measured on optiplex: **28 s** cold → **~22 s** (RPC-readiness poll replaces fixed waits) → **~5–10 s
pre-warmed** (floor = daemon start + first block landing). `healthcheck.sh` green = peers > 0 and height
climbing; reaching `Online` still takes the ~1 h bootstrap window (that's a separate, network-bound lever —
see the state-snapshot skill).

## Fund it (curl the faucet — no web form)

```bash
node-setup/scripts/fund-node.sh          # reads funding_pk from user_config.yaml, POSTs it to the faucet,
                                          # then polls the wallet balance until the funds land
```
It sends only the wallet's **public** key (the faucet's `POST <FAUCET_BACKEND>/<pubkey>`); the private key
never leaves the box. Run `setup-node.sh` with **`AUTOFUND=1`** to fund automatically at the end of setup.

Manual fallback (web form):
```bash
grep -A3 known_keys ~/logos-node/user_config.yaml     # copy the key id
# → paste into "Destination Public Key (Hex)" at https://testnet.blockchain.logos.co/web/faucet/
curl -s http://localhost:8080/wallet/<key>/balance    # 200 with a balance once it lands (404 until then)
```
Tokens auto-stake; the node becomes consensus-eligible ~**3.5 h** after funding (can't be waited out live —
for the demo, show *funded + Online + height tracking tip*).

## Blockers this runbook already handles (or you must watch)

- **Empty IBD peers → "syncs nothing"** (GUI bug #3153 / module#54): we pass peers to `generate_user_config`,
  so both config blocks get populated. The CLI path is immune.
- **Single-host bootstrap abort + seed outages** (#3166): the four release peers are all one host
  (`65.109.51.37`). If it's unreachable the node can't onboard. **Seen in the wild 2026-08-10** — a failed
  deployment took those seeds offline (logos-blockchain#3293); every *fresh/restarted* node was locked out
  (0 peers, QUIC handshake timeouts) while already-synced nodes survived on discovered peers. Worse, a node
  that loses all peers **stops dialing and won't self-recover** even after the seeds return — it needs a
  manual restart (logos-blockchain#3294). **Mitigations:** add a diverse peer you control via `EXTRA_PEERS`
  in `config/node.env`; if stuck at 0 peers, first check whether a *known-good* node is failing the *same*
  seeds (shared outage, not your box) before wiping anything — a wipe never fixes a peer-connectivity problem.
- **v0.2.1 chain halts (node-wallet UTXO double-spend)**: under L1-fee pressure (new in 0.2.1) the node
  wallet could double-spend a UTXO paying fees → chain halt (logos-blockchain#3287). Fixed in the **patched
  0.2.2 module** (see `node.env` — bump once it's in the lgpd registry). Heavy-fee-paying nodes (e.g. LEZ)
  also bump `pending_note_expiry_blocks` 10→120; a plain staking node is unlikely to hit it.
- **Re-genesis every release**: 0.2.0→0.2.1 wiped balances. Never restore a pre-genesis snapshot; run 1 syncs
  from scratch → we snapshot *that* synced state for runs 2–3.
- **Restart during bootstrap loses progress** — don't restart a bootstrapping node; let it reach Online.
- **`generate_user_config` is one-shot** — it won't overwrite an existing `user_config.yaml`; delete
  `user_config.yaml` + the db/state to redo.
- **Bootstrapping ~1h is normal** — `state` stays `Bootstrapping`, `LIB` sits at genesis; only `height`
  climbing proves life. The node reaches `Online` after this window.
- **Blend starts automatically and "waits" — that's expected.** Blend is the node's built-in privacy /
  mix-network service (anonymized message routing). It comes up with the node and logs
  `Blend service: Waiting for chain to become Online mode` — it stays in that waiting state throughout the
  ~1h bootstrap and activates once the chain is `Online`. Early `Blend … Starting` errors are transient
  noise, not a failure. Nothing to do — just let bootstrap finish.

## Rehearsal plan (optiplex, user `dar`)

1. reset to clean Ubuntu (`uninstall.sh`) → 2. `setup-node.sh` → green → 3. repeat ×3, tightening scripts →
4. snapshot the first synced node for fast runs 2–3 → 5. fresh-agent test (Claude Code is on optiplex).

## Final checklist — the node is *done* when

**Box prep (once, needs sudo / physical access)**
- [ ] `git curl jq tmux python3 cron` installed · `tailscale` up with Tailscale SSH · Claude Code logged in
- [ ] *(optional, to survive a power outage)* BIOS **"Restore on AC Power Loss" → Power On**

**Node (sudo-free)**
- [ ] `blockchain_module 0.2.4` installed; `user_config.yaml` generated with the **current** bootstrap peers
      (refreshed from the target release's notes) + a diverse peer added
- [ ] `scripts/healthcheck.sh` → **GREEN** — `n_peers > 0`, `height` climbing, `state: Online`
- [ ] Node **survives SSH logout** (detached `logoscore` daemon + dashboard in tmux `dashboard`)
- [ ] Node **survives a reboot** — `@reboot` cron installed (`crontab -l | grep logos-node-agent`); sudo-free
- [ ] Node **keys backed up off-box** (`user_config.yaml` + keystore)

**Funding & consensus** (demo shows *funded + Online + tracking tip*; a won slot needs ~3.5 h)
- [ ] Funded from the faucet; `curl …/wallet/<key>/balance` shows a balance
- [ ] (later) node participating — `state` stays `Online`, `height` keeps climbing

**Dashboard (sudo-free)**
- [ ] `dashboard` running; reachable over the tailnet at `http://100.x.x.x:8090`
      (phones: the Tailscale IP; no `tailscale serve` needed)
- [ ] Dashboard shows live state/height/peers/balance (0.2.1 nested-schema aware)

**Resilience proof (optional but ideal for the sovereign story)**
- [ ] Reboot the box → node + dashboard come back **on their own** (`@reboot` cron; sudo-free)
- [ ] Pull the plug → box powers back on + node returns (adds the one-time BIOS "power-on after AC loss")
