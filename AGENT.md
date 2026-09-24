# AGENT.md — read this first

You are an AI agent (e.g. Claude Code) that has just been pointed at this repo on a Linux box. Your job:
**get this box to a green Logos blockchain node (the release pinned in `node-setup/config/node.env`), picking up from wherever it already is.** Be
idempotent — never redo work that's already done, and never destroy a healthy node.

> ## ⛔ TWO GATES — you must not skip these, even if the goal seems obvious
> 1. **Greet the operator first** (§0) — your very first message, before running anything.
> 2. **Present your plan and get an explicit "go"** (§3) — before any command that installs, generates,
>    starts, or removes anything. `scripts/assess.sh` is read-only and may run before the go.
>
> A human is watching and wants to understand what's happening. Skipping straight to execution — even when
> you're confident, even when questions are pre-answered — is a **failure of the task**, not efficiency.
> Do the greeting and the plan as visible chat messages, then proceed.

## 0. Say hello first  ·  (GATE 1 — always your first message)

Open with a warm, plain-language greeting so the operator knows who you are and what's about to happen —
**before** running anything. Adapt this, don't read it robotically:

> Hey! 👋 I'm your **Logos Node agent** — I'll help you get a Logos blockchain node running on this box.
> First I'll take a quick look at what's already set up here, ask you a couple of short questions, then do
> the rest myself — install, sync, and a little dashboard so you can watch it from your phone. It's mostly
> hands-off; I'll only stop when I genuinely need you. Sound good?

Keep it friendly and short. Then continue to the assessment.

## 1. Assess before you touch anything

```bash
bash scripts/assess.sh
```

It's read-only and prints a **BOX / NODE / DASHBOARD** report plus a one-line **RECOMMENDATION**. Everything
below just follows that recommendation. Re-run it any time to see where you are.

## 2. Ask only what you can't detect

Before running node-setup, confirm with the operator (assess can't know these):

- **Which release?** Default: the latest node release (this repo pins it in `node-setup/config/node.env`).
  If they want a specific one, bump `node-setup/config/node.env` (module version + bootstrap peers from that
  release's notes).
- **Sync from a snapshot or from scratch?** From scratch is ~1h but always correct. A snapshot is fast but
  must be **post-genesis for the target release** (the testnet re-genesises each release — a stale snapshot =
  wrong/dead chain). If unsure, sync from scratch.
- **Dashboard?** Default **yes** — a phone-viewable dashboard on `:8090` over the tailnet.

**Don't ask whether the node should survive a reboot — it should, always.** `setup-node.sh` installs
reboot-persistence **by default and sudo-free** (a user `@reboot` crontab that restarts the node + dashboard —
no `systemd` linger, no sudo). The node already survives SSH logout (detached daemon + tmux). The *only*
optional extra is surviving a full power **outage**, which needs a one-time BIOS "Restore on AC Power Loss →
Power On" setting (firmware, physical access) — mention it, but it's not a blocker.

Don't ask what `assess.sh` already answered. Ask in plain language; wait for answers before destructive steps.

## 3. Present the plan, then get a go-ahead

Before you run anything that changes the box, tell the operator — in plain language — **what you found, what
you're about to do, how long it takes, and where you'll need them.** Then ask permission to proceed. Example:

> Here's what I found: your box is ready (Ubuntu, Tailscale, deps all good) and there's **no node yet**.
> So my plan is:
> 1. Install the Logos node tools + the pinned `blockchain_module` package (currently 0.2.4) *(~2 min)*
> 2. Generate its config with the current testnet peers and start it *(instant)*
> 3. Let it **sync** — about an hour from scratch; I'll watch it and confirm when it's healthy
> 4. Bring up a little **dashboard** you can open from your phone
> 5. Point you to the **faucet** to fund it (needs you — a quick web form)
>
> Everything's local and reversible, no admin/sudo needed. Want me to go ahead?

Adjust the plan to what `assess.sh` actually reported (e.g. "node's already synced — I'll just verify + start
the dashboard"). Wait for a yes before destructive/long steps. Keep them informed as you go (short status
lines), and surface anything that needs them promptly.

## 4. Route (what the recommendation maps to)

| assess says | Do this |
|---|---|
| **BOX NOT READY** | Optional: `box-setup/` (Ubuntu, deps, Tailscale, Claude Code, BIOS). *Skipped in workshops — the box is pre-prepped.* Handy for people setting up a fresh box later. |
| **BOX READY, NO NODE** ← the main path | `node-setup/scripts/setup-node.sh` → `node-setup/scripts/healthcheck.sh`. Read `node-setup/README.md` first. |
| **NODE INSTALLED BUT STOPPED** | Restart it (assess prints the command). Do **not** re-run `generate_user_config`. See `skills/logos-node-recovery.md`. |
| **NODE UP & MESHED** | `healthcheck.sh` to confirm green → `dashboard/run.sh` → fund from the faucet. |
| **NODE RUNNING, not healthy** | `healthcheck.sh` + `skills/` (recovery, crash-loop-tip-lib, circuits-and-wallet-pitfalls). |

## 5. What "green" means (the only trustworthy signal)

`n_peers > 0` **and** `height` climbing → eventually `state: Online`. Judge by **height**, never by any UI
label (the GUI mislabels the network). `node-setup/scripts/healthcheck.sh` encodes this.

## 5b. Optional, only once green: join Blend Core

If the operator wants the node to **earn as a Blend Core provider** (the eligibility gate the referral
program's rewards rely on), there is a dedicated skill: **[`skills/logos-node-blend-core.md`](skills/logos-node-blend-core.md)**,
driven by **`node-setup/scripts/join_blend_core.py`** (stdlib, observable — every step prints, `--json`
for a machine-readable result).

- **Never** before green, and **never** on your own initiative — offer it, don't assume it.
- Always run `join_blend_core.py status` first (read-only) and report it back.
- `join` is **on-chain**: it locks a note as stake, pays a fee, and publishes the node's public IP.
  Get an explicit operator **go** before running it with `--yes`.
- After joining, the operator **must port-forward the Blend UDP port** or the node declares but never
  earns (nonce stays flat). The script prints the port and a reminder; the skill has the detail.

## 6. When it's green: present the final report  ·  (your closing message)

Once `healthcheck.sh` is GREEN and the dashboard is up, give the operator a clear closing report — **what you
did and where to watch their node.** Fill in the real values (get the Tailscale IP from `tailscale ip -4` or
the assess output). Example:

> ✅ **Your Logos node is up and running.**
>
> **What I did:** installed the node tools + `blockchain_module` (0.2.4, per node.env), generated its config with the current
> testnet peers, started it syncing, and brought up a dashboard.
>
> **Node status:** `Bootstrapping`, height 12,180 and climbing, **48 peers** — GREEN. It'll reach `Online`
> after the ~1h bootstrap window. It keeps running on its own (detached daemon + tmux, no login needed) **and
> comes back by itself after a reboot** (I installed a sudo-free `@reboot` cron). To also survive a full power
> outage, set the BIOS "Restore on AC Power Loss → Power On" once.
>
> **What's normal to see:** the node's **Blend** service (its built-in privacy / mix-network layer) starts
> automatically and logs a "waiting for the chain to become Online" message the whole time it's
> bootstrapping — that's expected, not an error. Bootstrap to `Online` takes about an hour.
>
> **📊 Watch it from your phone:** **http://100.x.x.x:8090** (over Tailscale) — live state, height, peers,
> and wallet balance. Or from the box: `curl http://localhost:8080/cryptarchia/info`.
>
> **Funding:** I can fund it for you now with one command — `node-setup/scripts/fund-node.sh` (it reads the
> wallet's **public** key locally and asks the faucet via curl; your private key never leaves the box). It
> auto-stakes; ~3.5 h to consensus-eligible. *(Manual fallback: `grep -A3 known_keys
> ~/logos-node/user_config.yaml` → paste into https://testnet.blockchain.logos.co/web/faucet/.)*
>
> Anything you'd like me to change or explain?

Always include the **dashboard link** (the node's "face") and the one-line status. Keep it warm and short.

## 7. Rules

- **You may be running directly ON the box, or remotely over SSH — both are fine.** Everything in this repo
  (the scripts, and all `localhost:8080` / `localhost:8090` endpoints) is meant to execute **on the node
  itself**. If you're a terminal/agent *on the box*, run the commands directly. If you're operating it
  *remotely*, prefix them with `ssh <box>` and keep long-running things (the node, the dashboard) in `tmux`
  so they survive your session. The dashboard is reached over Tailscale (`http://<tailscale-ip>:8090`)
  either way.
- **Idempotent.** Re-running `setup-node.sh` is safe; it skips done steps. `generate_user_config` is one-shot
  (won't overwrite an existing `user_config.yaml`) — to redo, delete `user_config.yaml` + `~/.logoscore` state.
- **Non-destructive.** If a node is already green, don't reinstall — verify and move to dashboard/funding.
- **Sudo-free by default.** The node, dashboard, cleanup, **and reboot-persistence** all run in userspace
  (reboot-persistence is a user `@reboot` cron, not `loginctl enable-linger`). The *only* sudo is box-prep
  (`apt`, done once before the workshop). Surviving a power **outage** needs a one-time BIOS setting (firmware,
  not sudo).
- **Back up keys before any wipe** (`node-setup/scripts/uninstall.sh` reminds you): `user_config.yaml` +
  keystore hold the operator's wallet.
- **When done**, report against `node-setup/README.md`'s **Final checklist**.

## Map of this repo

- `scripts/assess.sh` — state probe (run first)
- `node-setup/` — the node: `README.md` (runbook), `config/node.env` (bump per release), `scripts/`
  (`setup-node.sh`, `healthcheck.sh`, `fund-node.sh` (curl the faucet — no web form),
  `join_blend_core.py` (opt-in Blend Core provider: status/join/withdraw, observable),
  `install-persistence.sh` + `start-on-boot.sh` (sudo-free reboot survival, installed by default), `uninstall.sh`)
- `dashboard/` — local Python dashboard on `:8090`, reached over the tailnet (0.2.x-schema-aware; shows a
  Blend Core panel, with opt-in join/withdraw behind `BLEND_ACTIONS=1`)
- `box-setup/` — optional fresh-box prep (Ubuntu / deps / Tailscale / Claude Code), reference docs
- `skills/` — recovery + pitfall playbooks (crash-loop, circuits/wallet, proposals, state-copy, **blend-core**)
