# Logos Node Dashboard

> **Default is now the Logos node web UI.** `dashboard/run.sh` fetches the
> [logos-node-webui](https://github.com/xAlisher/logos-node-webui) release (`fetch-webui.sh`, into
> `~/logos-node-webui`) and serves it on `:8090`, proxying `/api` to the node. It matches the official node
> app and works with 0.3.x nodes. Update: `dashboard/fetch-webui.sh`, then restart the `dashboard` tmux session.
> The rest of this page documents the **legacy** runbook dashboard: start it with
> `DASHBOARD=legacy dashboard/run.sh` (it calls `run-legacy.sh`).

Small local dashboard for a Logos Blockchain node and `zone-board`.

Compatible with:

- Logos testnet **v0.2 / v0.2.1** (reads liveness from `cryptarchia_info.state`, with a fallback for 0.2.0)

It shows:

- node state, height, tip, LIB, slot, and LIB slot from `/cryptarchia/info`
- wallet balance from `/wallet/<public_key>/balance`
- peer count from `/network/info`
- recent block proposal activity from recent node logs
- recent node logs from the newest log file
- zone-board messages from cache
- live zone-board TUI messages from tmux
- local publish and subscribe actions through the running zone-board tmux session
- best-effort finality labels for local live messages by matching node logs against current LIB slot

The `Block Proposals` panel includes:

- recent proposal count
- non-empty vs `0-tx` proposals
- latest proposal slot and block id
- recent proposal table with tx counts and mempool removals

## Requirements

- Python 3.10+
- `tmux` if you want live zone-board messages and publish/subscribe controls
- a running Logos node API, normally `http://127.0.0.1:8080`
- optional: `zone-board` running inside tmux

No Python package install is required; the server uses only the standard library.

## Quick Start

From the runbook root:

```bash
DASHBOARD=legacy ./dashboard/run.sh
```

Open:

```text
http://127.0.0.1:8090/
```

## Configuration

`dashboard/run.sh` reads these environment variables:

```bash
HOST=127.0.0.1
PORT=8090
NODE_API=http://127.0.0.1:8080
NODE_LOG_DIR=state/live-v0.1.2/logs
ZONE_BOARD_DIR=state/zone-board-v0.2.2
ZONE_BOARD_TMUX_SESSION=zone-board
ZONE_CHANNEL=your-channel
WALLET_PUBLIC_KEY=
```

Example:

```bash
ZONE_CHANNEL=alice DASHBOARD=legacy ./dashboard/run.sh
```

If your node logs or zone-board state are somewhere else:

```bash
NODE_LOG_DIR=/path/to/node/logs \
ZONE_BOARD_DIR=/path/to/zone-board-state \
ZONE_CHANNEL=alice \
DASHBOARD=legacy ./dashboard/run.sh
```

You can also call the server directly:

```bash
python3 dashboard/server.py \
  --host 127.0.0.1 \
  --port 8090 \
  --node-api http://127.0.0.1:8080 \
  --log-dir state/live-v0.1.2/logs \
  --zone-board-dir state/zone-board-v0.2.2 \
  --zone-board-tmux-session zone-board \
  --local-zone-channel alice \
  --wallet-public-key <your-wallet-public-key>
```

## Running Zone Board

The dashboard expects `zone-board` to already be running in tmux:

```bash
tmux new -ds zone-board \
  'artifacts/zone-sdk-test-v0.2.2/zone-board --node-url http://127.0.0.1:8080 --data-dir state/zone-board-v0.2.2 --channel alice'
```

Use the same value for `--channel` and `ZONE_CHANNEL`.

This runbook also includes a user `systemd` unit, `zone-board.service`, which starts and supervises that tmux session through `run-zone-board.sh`.

## Safety

Do not publish state directories or keys.

Private or local-only files include:

- `state/zone-board-v0.2.2/sequencer.key`
- `state/zone-board-v0.2.2/`
- `state/live-v0.1.2/`
- node databases and logs
- downloaded binaries in `artifacts/`

The dashboard code is safe to publish; runtime state is not.

## Known Limits

- Local live messages can show before zone-board writes them to cache.
- Finality labels are best-effort and depend on node logs containing `ChannelInscribe` debug records.
- Proposal activity is intentionally based on a bounded recent log window, not full historical indexing.
- The dashboard controls the zone-board TUI through tmux keystrokes, so keep a dedicated tmux session for it.
