# logos-node-agent

> This is a personal, experimental hobby project. It is not an official Logos product. Not audited.


**Point your AI agent at this repo and get a running Logos blockchain node.**

Clone it onto a Linux box, tell your agent (Claude Code or similar) to read **[AGENT.md](AGENT.md)**, and it
will assess what's already there, ask you the few things it can't detect, and bring the box to a **green
Logos node (v0.2.4) + a live dashboard** — picking up from wherever the box already is, without redoing done
work or breaking a healthy node.

Built for the Logos EcoDev node-operator workshops; reusable by anyone.

## Quickstart

```bash
git clone https://github.com/xAlisher/logos-node-agent.git
cd logos-node-agent
bash scripts/assess.sh        # what's here + the recommended next step
```

Then point your agent at **AGENT.md** and let it drive — or follow the steps by hand:

```bash
node-setup/scripts/setup-node.sh      # install + configure + start the node (no sudo)
node-setup/scripts/healthcheck.sh     # GREEN = peers > 0 and height climbing → Online
dashboard/run.sh                      # node web UI on :8090, reached over your tailnet
```

## What you get

- **A synced node** via the upstream-documented path (`logoscore` + `blockchain_module`), not a fork or a
  raw binary — the same way the official docs tell strangers to do it.
- **A dashboard**: the [Logos node web UI](https://github.com/xAlisher/logos-node-webui) (same views as the
  official node app: node, rewards, explorer, wallet, mining), fetched as a release tarball and served by
  stdlib Python (no Node.js, no pip), reachable from your phone over Tailscale. The older runbook dashboard
  (logs, zone-board) is still there: `DASHBOARD=legacy dashboard/run.sh`.
- **Opt-in Blend Core** — once the node is green, `node-setup/scripts/join_blend_core.py` makes it a Blend
  provider (the referral-program eligibility gate): observable `status` / `join` / `withdraw`, and a Blend
  panel on the dashboard. On-chain join is gated behind an explicit go (locks stake, pays a fee, publishes IP).
- **Sudo-free** end to end (the box's one-time `apt` prep and reboot-persistence are the only sudo touches).
- An agent that **resumes intelligently**: box not ready → optional box-setup; box ready, no node →
  node-setup; node already green → just verify + dashboard.

## Layout

| Path | What |
|---|---|
| **[AGENT.md](AGENT.md)** | The orchestrator your agent reads first (assess → ask → route → resume). |
| `scripts/assess.sh` | Read-only probe: what's on the box + the next step. |
| `node-setup/` | The node — [`README.md`](node-setup/README.md) (runbook), `config/node.env` (bump per release), `scripts/`. |
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
