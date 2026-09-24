#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock


ROOT = Path(__file__).resolve().parent
RUNBOOK_ROOT = ROOT.parent
# Blend Core: the dashboard delegates the on-chain join/withdraw + status read to the shared,
# audited script (single source of truth) rather than re-implementing the money path here.
BLEND_SCRIPT = RUNBOOK_ROOT / "node-setup" / "scripts" / "join_blend_core.py"
DEFAULT_NODE_CONFIG = os.environ.get("NODE_CONFIG", str(Path.home() / "logos-node" / "user_config.yaml"))
DEFAULT_NODE_API = "http://127.0.0.1:8080"
DEFAULT_NODE_BINARY = Path(os.environ.get("NODE_BINARY", str(RUNBOOK_ROOT / "artifacts/node/logos-blockchain-node")))
DEFAULT_LOG_DIR = RUNBOOK_ROOT / "state/live-v0.1.2/logs"
PROPOSAL_REFRESH_SECS = 60
MAX_RECENT_PROPOSALS = 20
MAX_PROPOSAL_LOG_FILES = 4
# Journal window for nodes that log to stdout -> journald (no useful file logs).
# Bounded by time so the scan cost stays flat as the journal grows; --grep keeps
# the returned output tiny regardless of node verbosity.
PROPOSAL_JOURNAL_SINCE = "-24 hours"
PROPOSAL_JOURNAL_TIMEOUT_SECS = 30
DEFAULT_NODE_UNIT = "logos-node"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LOG_TIMESTAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")
PROPOSAL_RE = re.compile(
    r"propose_block\{parent=HeaderId\(([0-9a-f]+)\)\s+slot=Slot\((\d+)\)\}.*?"
    r"proposed block with id HeaderId\(([0-9a-f]+)\) containing (\d+) transactions \((\d+) removed\)"
)
PROPOSAL_APPLIED_RE = re.compile(
    r"Successfully applied our own proposed block\. Publishing it to the blend network: HeaderId\(([0-9a-f]+)\)"
)
# v0.2 chain-leader wording (no parent/slot, no "with id"/"containing"):
#   "proposed block HeaderId(<id>) with <n> transactions (<m> removed)"
PROPOSAL_RE_V02 = re.compile(
    r"proposed block HeaderId\(([0-9a-f]+)\) with (\d+) transactions \((\d+) removed\)"
)
NODE_VERSION_RE = re.compile(r"^logos-blockchain-node\s+([^\s]+)$", re.MULTILINE)


def latest_log_file(log_dir: Path) -> Path | None:
    if not log_dir.exists():
        return None
    files = [path for path in log_dir.iterdir() if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def tail_lines(path: Path, line_count: int) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return list(deque(handle, maxlen=line_count))


def recent_log_files(log_dir: Path, limit: int) -> list[Path]:
    if not log_dir.exists():
        return []
    files = [path for path in log_dir.iterdir() if path.is_file()]
    return sorted(files, key=lambda path: path.name)[-limit:]


def parse_log_timestamp(line: str) -> str | None:
    match = LOG_TIMESTAMP_RE.search(ANSI_RE.sub("", line))
    return match.group(1) if match else None


def _flatten_cryptarchia(payload):
    info = payload.get("cryptarchia_info")
    if not isinstance(info, dict):
        return payload
    out = dict(info)
    m = payload.get("mode")
    if isinstance(m, dict):
        v = next(iter(m.values()), None)
        m = v if isinstance(v, str) else next(iter(m.keys()), None)
    # 0.2.1: liveness moved from a top-level `mode` to `cryptarchia_info.state`
    # (e.g. "Online"/"Bootstrapping"). Fall back to it so consumers that read
    # `mode` (the /api/status payload, the top-bar indicator) keep working.
    if m is None:
        m = info.get("state")
    if m is not None:
        out["mode"] = m
    for k in ("ok","url","node_version","network","wallet"):
        if k in payload:
            out[k] = payload[k]
    return out


def _ingest_proposal_line(proposals: dict[str, dict], raw_line: str) -> None:
    if "proposed block" not in raw_line and "Successfully applied our own proposed block" not in raw_line:
        return

    line = ANSI_RE.sub("", raw_line)
    if proposal_match := PROPOSAL_RE_V02.search(line):
        block_id, tx_count, removed_count = proposal_match.groups()
        proposal = proposals.setdefault(
            block_id,
            {
                "timestamp": parse_log_timestamp(line),
                "slot": None,
                "parent_block_id": "",
                "block_id": block_id,
                "tx_count": int(tx_count),
                "removed_count": int(removed_count),
                "applied_locally": False,
                "published_to_network": False,
                "status": "proposed",
            },
        )
        proposal.update(
            {
                "timestamp": proposal.get("timestamp") or parse_log_timestamp(line),
                "tx_count": int(tx_count),
                "removed_count": int(removed_count),
            }
        )
        return

    if proposal_match := PROPOSAL_RE.search(line):
        parent_block_id, slot, block_id, tx_count, removed_count = proposal_match.groups()
        proposal = proposals.setdefault(
            block_id,
            {
                "timestamp": parse_log_timestamp(line),
                "slot": int(slot),
                "parent_block_id": parent_block_id,
                "block_id": block_id,
                "tx_count": int(tx_count),
                "removed_count": int(removed_count),
                "applied_locally": False,
                "published_to_network": False,
                "status": "proposed",
            },
        )
        proposal.update(
            {
                "timestamp": proposal.get("timestamp") or parse_log_timestamp(line),
                "slot": int(slot),
                "parent_block_id": parent_block_id,
                "tx_count": int(tx_count),
                "removed_count": int(removed_count),
            }
        )
        return

    if applied_match := PROPOSAL_APPLIED_RE.search(line):
        block_id = applied_match.group(1)
        proposal = proposals.setdefault(
            block_id,
            {
                "timestamp": parse_log_timestamp(line),
                "slot": None,
                "parent_block_id": "",
                "block_id": block_id,
                "tx_count": 0,
                "removed_count": 0,
                "applied_locally": False,
                "published_to_network": False,
                "status": "proposed",
            },
        )
        proposal["applied_locally"] = True
        proposal["published_to_network"] = True


def _finalize_proposals(proposals: dict[str, dict]) -> list[dict]:
    sorted_proposals = sorted(
        proposals.values(),
        key=lambda proposal: parse_datetime(proposal.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    for proposal in sorted_proposals:
        if proposal["applied_locally"] and proposal["published_to_network"]:
            proposal["status"] = "applied+broadcast"
        elif proposal["applied_locally"]:
            proposal["status"] = "applied"
        elif proposal["published_to_network"]:
            proposal["status"] = "broadcast"
        else:
            proposal["status"] = "proposed"
    return sorted_proposals


def parse_recent_proposals(log_files: list[Path]) -> list[dict]:
    proposals: dict[str, dict] = {}
    for log_file in log_files:
        try:
            handle = log_file.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for raw_line in handle:
                _ingest_proposal_line(proposals, raw_line)
    return _finalize_proposals(proposals)


def journald_proposal_lines(unit: str, since: str = PROPOSAL_JOURNAL_SINCE) -> list[str]:
    """Proposal log lines for a user unit that logs to stdout -> journald.

    Filters server-side with `--grep` so only the (sparse) proposal lines are
    returned regardless of how chatty the node is, and bounds the scan with
    `--since` so the cost stays flat as the journal grows. Returns [] if
    journalctl is unavailable, the unit has no journal, or there are no matches
    (--grep exits non-zero) — the caller then keeps its file-based result, so
    file-logging nodes (Sneg) are unaffected.
    """
    try:
        output = subprocess.run(
            [
                "journalctl", "--user", "-u", unit,
                "-p", "info", "--since", since, "--no-pager",
                "--grep", "proposed block HeaderId|proposed block with id|Successfully applied our own proposed block",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=PROPOSAL_JOURNAL_TIMEOUT_SECS,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    return output.splitlines()


def parse_recent_proposals_from_journal(unit: str) -> list[dict]:
    proposals: dict[str, dict] = {}
    for raw_line in journald_proposal_lines(unit):
        _ingest_proposal_line(proposals, raw_line)
    return _finalize_proposals(proposals)


def parse_datetime(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def detect_node_version(node_binary: Path) -> str:
    try:
        output = subprocess.run(
            [str(node_binary), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""

    match = NODE_VERSION_RE.search(output)
    return match.group(1) if match else ""


class DashboardHandler(BaseHTTPRequestHandler):
    node_api = DEFAULT_NODE_API
    node_binary = DEFAULT_NODE_BINARY
    node_version = ""
    log_dir = DEFAULT_LOG_DIR
    node_unit = DEFAULT_NODE_UNIT
    wallet_public_key = ""
    node_config = DEFAULT_NODE_CONFIG
    blend_script = str(BLEND_SCRIPT)
    allow_blend_actions = False           # mutating join/withdraw from the (unauthenticated) UI, off by default
    _blend_cache: dict = {}
    _blend_cache_lock = Lock()
    _blend_cache_ts: float = 0.0
    _BLEND_CACHE_TTL: float = 15.0
    proposal_cache_lock = Lock()
    proposal_cache_last_refresh = 0.0
    proposal_cache: dict = {"summary": {}, "recent": []}
    _wallet_cache: dict = {}
    _wallet_cache_lock = Lock()
    _wallet_cache_ts: float = 0.0
    _WALLET_CACHE_TTL: float = 15.0

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._serve_file(ROOT / "index.html")
            return

        if self.path == "/api/status":
            self._serve_status()
            return

        if self.path == "/api/logs":
            self._serve_logs()
            return

        if self.path == "/api/proposals":
            self._serve_proposals()
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        if self.path in ("/api/blend/join", "/api/blend/withdraw"):
            self._serve_blend_action("join" if self.path.endswith("join") else "withdraw")
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _serve_blend_action(self, action: str) -> None:
        # Draining the request body avoids a broken pipe on the client side.
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                self.rfile.read(length)
        except (ValueError, OSError):
            pass
        if not type(self).allow_blend_actions:
            self._send_json(
                {"ok": False,
                 "error": "Blend actions are disabled on this dashboard. It is unauthenticated and "
                          "join/withdraw are on-chain (locks stake, pays a fee, publishes your IP). "
                          "Enable deliberately with BLEND_ACTIONS=1 on a trusted network, or run "
                          "node-setup/scripts/join_blend_core.py on the box."},
                status=HTTPStatus.FORBIDDEN,
            )
            return
        run = type(self)._run_blend(action)
        self._send_json(run, status=HTTPStatus.OK if run.get("ok") else HTTPStatus.BAD_GATEWAY)

    def log_message(self, fmt: str, *args) -> None:
        return

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _serve_file(self, path: Path) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        body = path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(path))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return


    def _serve_status(self) -> None:
        url = f"{self.node_api}/cryptarchia/info"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._send_json(
                {
                    "ok": False,
                    "error": f"Node API unavailable: {exc}",
                    "url": url,
                },
                status=HTTPStatus.BAD_GATEWAY,
            )
            return

        payload = _flatten_cryptarchia(payload)
        payload["ok"] = True
        payload["url"] = url
        payload["node_version"] = self.node_version
        payload["network"] = self._network_info()
        payload["wallet"] = self._wallet_balance()
        payload["voucher_count"] = self._voucher_count()
        payload["blend"] = self._blend_status()
        self._send_json(payload)

    def _voucher_count(self) -> int:
        # Claimable leadership vouchers = blocks this node has led (rewards pending).
        url = f"{self.node_api}/leader/claim/vouchers"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
            return 0
        if isinstance(payload, dict) and isinstance(payload.get("vouchers"), list):
            return len(payload["vouchers"])
        if isinstance(payload, list):
            return len(payload)
        return 0

    def _wallet_balance(self) -> dict:
        public_key = self.wallet_public_key.strip()
        if not public_key:
            return {
                "ok": False,
                "error": "Wallet public key not configured",
            }

        cls = self.__class__
        now = time.time()

        # Return cached result if still fresh (double-checked locking)
        if cls._wallet_cache and now - cls._wallet_cache_ts < cls._WALLET_CACHE_TTL:
            return dict(cls._wallet_cache)

        with cls._wallet_cache_lock:
            if cls._wallet_cache and time.time() - cls._wallet_cache_ts < cls._WALLET_CACHE_TTL:
                return dict(cls._wallet_cache)

            url = f"{self.node_api}/wallet/{public_key}/balance"
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
                result: dict = {
                    "ok": False,
                    "url": url,
                    "address": public_key,
                    "error": str(exc),
                }
                cls._wallet_cache = result
                cls._wallet_cache_ts = time.time()
                return dict(result)

            if isinstance(payload, dict):
                payload["ok"] = True
                payload["url"] = url
                result = payload
            else:
                result = {
                    "ok": False,
                    "url": url,
                    "address": public_key,
                    "error": "Unexpected wallet balance response",
                }

            cls._wallet_cache = result
            cls._wallet_cache_ts = time.time()
            return dict(result)

    def _network_info(self) -> dict:
        url = f"{self.node_api}/network/info"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
            return {
                "ok": False,
                "url": url,
                "error": str(exc),
            }

        if isinstance(payload, dict):
            payload["ok"] = True
            payload["url"] = url
            return payload

        return {
            "ok": False,
            "url": url,
            "error": "Unexpected network info response",
        }

    @classmethod
    def _run_blend(cls, action: str, timeout: float = 150.0) -> dict:
        """Run join_blend_core.py <action> --json and return {ok, output, result}.

        `status` is safe to run any time; `join`/`withdraw` mutate on-chain and pass --yes."""
        cmd = [sys.executable, cls.blend_script, action,
               "--config", cls.node_config, "--node-api", cls.node_api, "--json"]
        if action in ("join", "withdraw"):
            cmd.append("--yes")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            return {"ok": False, "error": f"blend script not found: {cls.blend_script}", "output": ""}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"blend {action} timed out after {int(timeout)}s", "output": ""}
        combined = (proc.stdout or "") + (proc.stderr or "")
        result = None
        for line in reversed(combined.splitlines()):
            if line.startswith("RESULT:"):
                try:
                    result = json.loads(line[len("RESULT:"):].strip())
                except json.JSONDecodeError:
                    result = None
                break
        return {
            "ok": proc.returncode == 0 and (result or {}).get("ok", proc.returncode == 0),
            "exit_code": proc.returncode,
            "output": combined.strip(),
            "result": result,
        }

    def _blend_status(self) -> dict:
        cls = type(self)
        now = time.time()
        if cls._blend_cache and now - cls._blend_cache_ts < cls._BLEND_CACHE_TTL:
            return dict(cls._blend_cache)
        with cls._blend_cache_lock:
            if cls._blend_cache and time.time() - cls._blend_cache_ts < cls._BLEND_CACHE_TTL:
                return dict(cls._blend_cache)
            run = cls._run_blend("status", timeout=20.0)
            res = run.get("result") or {}
            status = {
                "ok": bool(res.get("ok")),
                "supported": True,
                "declared": bool(res.get("declared")),
                "sdp_key_funded": res.get("sdp_key_funded"),
                "nonce": res.get("nonce"),
                "core_peers": res.get("core_peers"),
                "created": res.get("created"),
                "active": res.get("active"),
                "withdraw_at": res.get("withdraw_at"),
                "locator": res.get("locator"),
                "declaration_id": res.get("declaration_id"),
                "earning": bool((res.get("nonce") or 0) > 0 and (res.get("core_peers") or 0) > 0),
                "actions_enabled": cls.allow_blend_actions,
            }
            if not run.get("result"):
                status["error"] = run.get("error") or "blend status unavailable"
            cls._blend_cache = status
            cls._blend_cache_ts = time.time()
            return dict(status)

    def _serve_logs(self) -> None:
        latest = latest_log_file(self.log_dir)
        if latest is None:
            self._send_json(
                {
                    "ok": False,
                    "error": "No log file found",
                    "log_dir": str(self.log_dir),
                    "lines": [],
                },
                status=HTTPStatus.NOT_FOUND,
            )
            return

        lines = [ANSI_RE.sub("", line.rstrip("\n")) for line in tail_lines(latest, 80)]
        self._send_json(
            {
                "ok": True,
                "log_dir": str(self.log_dir),
                "latest_file": latest.name,
                "lines": lines,
            }
        )

    def _serve_proposals(self) -> None:
        cls = type(self)
        now = time.monotonic()
        if now - cls.proposal_cache_last_refresh >= PROPOSAL_REFRESH_SECS:
            with cls.proposal_cache_lock:
                now = time.monotonic()
                if now - cls.proposal_cache_last_refresh >= PROPOSAL_REFRESH_SECS:
                    # Prefer file logs (Sneg), but fall back to journald whenever the
                    # files yield no proposals. This covers stdout->journald nodes
                    # (optiplex) AND misconfigured/stale log dirs. journald returns []
                    # for genuine file-logging nodes, so there is no regression.
                    log_files = recent_log_files(cls.log_dir, MAX_PROPOSAL_LOG_FILES)
                    proposals = parse_recent_proposals(log_files) if log_files else []
                    source = "files"
                    if not proposals:
                        journal_proposals = parse_recent_proposals_from_journal(cls.node_unit)
                        if journal_proposals:
                            proposals = journal_proposals
                            source = "journal"
                    non_empty = sum(1 for proposal in proposals if proposal.get("tx_count", 0) > 0)
                    latest = proposals[-1] if proposals else None
                    cls.proposal_cache = {
                        "summary": {
                            "proposals_recent": len(proposals),
                            "non_empty_recent": non_empty,
                            "zero_tx_recent": len(proposals) - non_empty,
                            "last_proposal_at": latest.get("timestamp") if latest else "",
                            "last_slot": latest.get("slot") if latest else None,
                            "last_block_id": latest.get("block_id") if latest else "",
                            "window_files": len(log_files),
                            "source": source,
                        },
                        "recent": proposals[-MAX_RECENT_PROPOSALS:],
                    }
                    cls.proposal_cache_last_refresh = now

        payload = cls.proposal_cache
        self._send_json(
            {
                "ok": True,
                "summary": payload.get("summary", {}),
                "recent": payload.get("recent", []),
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Local dashboard for Logos node status")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--node-api", default=os.environ.get("NODE_API", DEFAULT_NODE_API))
    parser.add_argument(
        "--log-dir",
        default=os.environ.get("NODE_LOG_DIR", str(DEFAULT_LOG_DIR)),
    )
    parser.add_argument(
        "--node-unit",
        default=os.environ.get("NODE_UNIT", DEFAULT_NODE_UNIT),
        help="systemd --user unit for the node, used to read proposals from journald "
        "when no file logs exist (stdout-logging nodes).",
    )
    parser.add_argument(
        "--wallet-public-key",
        default=os.environ.get("WALLET_PUBLIC_KEY", ""),
        help="Wallet public key used for the balance card.",
    )
    parser.add_argument(
        "--node-config",
        default=DEFAULT_NODE_CONFIG,
        help="Path to the node's user_config.yaml (for the Blend Core panel + actions).",
    )
    parser.add_argument(
        "--allow-blend-actions",
        action="store_true",
        default=os.environ.get("BLEND_ACTIONS", "") not in ("", "0", "false", "no"),
        help="Enable the on-chain Blend join/withdraw buttons (off by default; the dashboard is "
             "unauthenticated). Also settable via BLEND_ACTIONS=1.",
    )
    args = parser.parse_args()

    DashboardHandler.node_api = args.node_api.rstrip("/")
    DashboardHandler.node_binary = DEFAULT_NODE_BINARY
    DashboardHandler.node_version = detect_node_version(DashboardHandler.node_binary)
    DashboardHandler.log_dir = Path(args.log_dir)
    DashboardHandler.node_unit = args.node_unit
    DashboardHandler.wallet_public_key = args.wallet_public_key
    DashboardHandler.node_config = args.node_config
    DashboardHandler.allow_blend_actions = bool(args.allow_blend_actions)

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard listening on http://{args.host}:{args.port}")
    print(f"Node API: {DashboardHandler.node_api}")
    print(f"Log dir: {DashboardHandler.log_dir}")
    print(f"Wallet public key: {DashboardHandler.wallet_public_key or '(not configured)'}")
    print(f"Node config: {DashboardHandler.node_config}")
    print(f"Blend actions: {'ENABLED' if DashboardHandler.allow_blend_actions else 'disabled (status only)'}")
    server.serve_forever()


if __name__ == "__main__":
    main()
