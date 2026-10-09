# logos-node-agent

> This is a personal, experimental hobby project. It is not an official Logos product. Not audited.


**Point your AI agent at this repo and get a running Logos blockchain node.**

Clone it onto a Linux box, tell your agent (Claude Code or similar) to read **[AGENT.md](AGENT.md)**, and it
will assess what's already there, ask you the few things it can't detect, and bring the box to a **green
Logos testnet node (0.3.1, the Oct 9 2026 relaunch) mining for itself + the node web UI**, picking up from
wherever the box already is, without redoing done work or breaking a healthy node. A node left on an old
chain (0.3.0) is moved to the new one, with its old config and keys kept aside.

Built for the Logos EcoDev node-operator workshops; reusable by anyone.

## Quickstart

```bash
git clone https://github.com/xAlisher/node-agent.git
cd node-agent
bash scripts/assess.sh        # what's here + the recommended next step
```

Then point your agent at **AGENT.md** and let it drive — or follow the steps by hand:

```bash
node-setup/scripts/setup-node-030.sh          # download 0.3.1, config + keys, start, mine once Online (no sudo)
node-setup/scripts/healthcheck-030.sh         # GREEN = right genesis, Online, height climbing, mining
node-setup/scripts/install-persistence-030.sh # come back after a reboot (user @reboot cron, no sudo)
dashboard/run.sh                      # node web UI on :8090, reached over your tailnet
```

## What you get

- **A synced 0.3.1 node** using the official `logos-blockchain-node` release binary, funding itself by PoW
  mining (1 thread, stops at a balance threshold; there is no faucet on 0.3.x). The official docs run the same
  chain through `logosctl` + `blockchain_module` ([Run a node](https://docs.logos.co/run-a-node)).
- **A dashboard**: the [Logos node web UI](https://github.com/xAlisher/logos-node-webui) (same views as the
  official node app: node, rewards, explorer, wallet, mining), fetched as a release tarball and served by
  stdlib Python (no Node.js, no pip), reachable from your phone over Tailscale. The older runbook dashboard
  (logs, zone-board) is still there: `DASHBOARD=legacy dashboard/run.sh`.
- **Opt-in Blend Core** — once the node is green, `node-setup/scripts/join_blend_core.py` makes it a Blend
  provider (the referral-program eligibility gate): observable `status` / `join` / `withdraw`, and a Blend
  panel on the dashboard. On-chain join is gated behind an explicit go (locks stake, pays a fee, publishes IP).
- **Sudo-free** end to end (the box's one-time `apt` prep and reboot-persistence are the only sudo touches).
- An agent that **resumes intelligently**: box not ready → optional box-setup; box ready, no node →
  node-setup; node on an old chain → move it to the current one; node already green → just verify + dashboard.

## Layout

| Path | What |
|---|---|
| **[AGENT.md](AGENT.md)** | The orchestrator your agent reads first (assess → ask → route → resume). |
| `scripts/assess.sh` | Read-only probe: what's on the box + the next step. |
| `node-setup/` | The node: `config/node-030.env` (version, genesis, peers: bump per release) and `scripts/*-030.sh`. `node.env` + the non-030 scripts are the retired 0.2.x `logoscore` path. Runbook: [`README.md`](node-setup/README.md). |
| `dashboard/` | `run.sh` starts the node web UI on `:8090` (tailnet-reachable; fetched by `fetch-webui.sh`). `run-legacy.sh` is the older runbook dashboard (logs, zone-board, **Blend Core** panel). |
| `node-setup/scripts/join_blend_core.py` | Opt-in: make the node a **Blend Core** provider (`status`/`join`/`withdraw`), observable. See `skills/logos-node-blend-core.md`. |
| `box-setup/` | **Optional** fresh-box prep (Ubuntu / deps / Tailscale / Claude Code / BIOS). *Skipped in workshops.* |
| `skills/` | Recovery + pitfall playbooks (crash-loop, circuits/wallet, proposals, state-copy). |

## Requirements

Ubuntu 24.04 x86_64 · glibc ≥ 2.39 · ≥ 64 GB disk · ~8 GB RAM · Tailscale (access + dashboard). Full list
and the one-time `apt` line: **[node-setup/README.md](node-setup/README.md)** → *Prerequisites & dependencies*.

## Credits

Distilled from the Logos node runbook built for Circle stewards, aligned to the official
[Run a node from the CLI](https://docs.logos.co/blockchain/get-started/run-a-logos-blockchain-node-from-cli)
guide. License: MIT.
