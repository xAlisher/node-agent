#!/usr/bin/env python3
"""join_blend_core.py — make this CLI node a Blend Core provider, observably.

Blend Core eligibility is the gate the referral program's reward model relies on. On a
Basecamp node the UI drives this; on a CLI node there was no equivalent. This script is that
equivalent: an agent (or an operator) runs it, watches each step, and gets a node declared and
earning — or a clear reason why not.

It is **stdlib-only** (urllib/json/argparse — no pip), like the rest of this repo, and mirrors the
proven-on-sneg flow used by the logos_node_1click module backend:

    fund the SDP funding key  ->  POST /blend/join {locator, locked_note_id}  ->  active at created+2

Subcommands
    status    (default)  read-only: config, node reachability, our declaration, in-Core peers
    join                 declare this node as a Blend provider (ON-CHAIN, locks a note, pays a fee,
                         publishes your public IP). Idempotent: if already declared, just reports.
    withdraw             schedule withdrawal of our declaration (POST /sdp/withdrawal)

Every step prints progress; failures print `ERROR <ID>: ...` and exit non-zero. `--json` adds a
final machine-readable `RESULT: {...}` line (the dashboard parses this).

Safety
    `join`/`withdraw` mutate on-chain state. Non-interactive callers (the agent, the dashboard)
    must pass `--yes` to confirm; interactively you are prompted. `--fund` will request faucet
    funds for the SDP key if it is empty.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── config discovery defaults (aligned to node-setup: ~/logos-node/user_config.yaml) ──────────
DEFAULT_NODE_HOME = Path(os.environ.get("NODE_HOME", str(Path.home() / "logos-node")))
DEFAULT_CONFIG = Path(os.environ.get("NODE_CONFIG", str(DEFAULT_NODE_HOME / "user_config.yaml")))
DEFAULT_NODE_API = os.environ.get("NODE_API", os.environ.get("API", "http://127.0.0.1:8080"))
DEFAULT_FAUCET = os.environ.get("FAUCET_BACKEND", "https://testnet.blockchain.logos.co/web/faucet-backend")
DEFAULT_BLEND_PORT = 3400
IP_ECHO_URLS = ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com")
HEX_RE = re.compile(r"^[0-9a-fA-F]{32,}$")

# ── observable output ─────────────────────────────────────────────────────────────────────────
_USE_COLOR = sys.stdout.isatty()
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COLOR else s

def step(msg: str) -> None:
    print(_c("1;36", f"▶ {msg}"), flush=True)

def ok(msg: str) -> None:
    print(_c("1;32", f"  ✓ {msg}"), flush=True)

def info(msg: str) -> None:
    print(f"  · {msg}", flush=True)

def warn(msg: str) -> None:
    print(_c("1;33", f"  ⚠ {msg}"), flush=True)

class BlendError(Exception):
    def __init__(self, err_id: str, message: str):
        super().__init__(message)
        self.err_id = err_id
        self.message = message

def fail(err_id: str, message: str) -> "BlendError":
    return BlendError(err_id, message)

# ── HTTP (stdlib) ───────────────────────────────────────────────────────────────────────────
def http_get(url: str, timeout: float = 6.0):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")

def http_get_json(url: str, timeout: float = 6.0):
    status, body = http_get(url, timeout)
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, None

def http_post(url: str, payload, timeout: float = 25.0):
    """POST JSON. Returns (status, text). Raises only on network failure."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")

def node_up(node_api: str) -> bool:
    try:
        http_get(f"{node_api}/blend/info", timeout=3)
        return True
    except (urllib.error.URLError, OSError):
        return False

# ── config parsing (a tiny, targeted YAML read — no pip) ────────────────────────────────────
def read_config_text(config_path: Path) -> str:
    if not config_path.exists():
        raise fail("E_NO_CONFIG", f"node config not found: {config_path} "
                                  "(set up the node first, or pass --config)")
    return config_path.read_text(errors="replace")

def sdp_funding_pk(text: str) -> str:
    """The funding_pk under the top-level `sdp:` block — NOT leader.wallet.funding_pk."""
    in_sdp = False
    for ln in text.splitlines():
        if re.match(r"^sdp:\s*$", ln):
            in_sdp = True
            continue
        if in_sdp:
            # a new top-level key (column 0, ends with ':') ends the sdp block
            if re.match(r"^\S.*:\s*$", ln) and not ln.startswith(" "):
                break
            m = re.search(r"funding_pk:\s*\"?([0-9a-fA-F]{32,})\"?", ln)
            if m:
                return m.group(1)
    raise fail("E_SDP_KEY_MISSING", "no sdp.wallet.funding_pk in the config — "
                                    "this node has no SDP funding key to stake from")

def blend_port(text: str) -> int:
    """Mirror the module: explicit blend_port: else a /udp/N/quic on a blend line, else 3400."""
    m = re.search(r"blend_port:\s*(\d+)", text)
    if m:
        return int(m.group(1))
    for ln in text.splitlines():
        if "blend" in ln.lower():
            um = re.search(r"/udp/(\d+)/quic", ln)
            if um:
                return int(um.group(1))
    return DEFAULT_BLEND_PORT

def external_ip_from_config(text: str) -> str:
    for ln in text.splitlines():
        if "external" in ln.lower():
            m = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", ln)
            if m and m.group(1) != "127.0.0.1":
                return m.group(1)
    return ""

def resolve_public_ip(cfg_text: str) -> str:
    pinned = external_ip_from_config(cfg_text)
    if pinned:
        info(f"public IP from config external_address: {pinned}")
        return pinned
    for url in IP_ECHO_URLS:
        try:
            _, body = http_get(url, timeout=6)
            m = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", body)
            if m:
                info(f"public IP resolved via {url}: {m.group(1)}")
                return m.group(1)
        except (urllib.error.URLError, OSError):
            continue
    return ""

# ── declaration helpers ─────────────────────────────────────────────────────────────────────
def all_declarations(node_api: str) -> dict:
    """/mantle/sdp/declarations → {declaration_id: {..}} (ALL providers the node knows)."""
    try:
        _, data = http_get_json(f"{node_api}/mantle/sdp/declarations", timeout=6)
    except (urllib.error.URLError, OSError) as e:
        raise fail("E_NODE_UNREACHABLE", f"node API unreachable at {node_api}: {e}")
    return data if isinstance(data, dict) else {}

def our_note_ids(node_api: str, sdp_pk: str) -> set:
    """Note ids owned by the SDP funding key (empty set if the key is unfunded/404)."""
    try:
        status, data = http_get_json(f"{node_api}/wallet/{sdp_pk}/balance", timeout=6)
    except (urllib.error.URLError, OSError):
        return set()
    if status == 200 and isinstance(data, dict) and isinstance(data.get("notes"), dict):
        return set(data["notes"].keys())
    return set()

def note_values(node_api: str, sdp_pk: str) -> dict:
    try:
        status, data = http_get_json(f"{node_api}/wallet/{sdp_pk}/balance", timeout=6)
    except (urllib.error.URLError, OSError):
        return {}
    if status == 200 and isinstance(data, dict) and isinstance(data.get("notes"), dict):
        return data["notes"]
    return {}

def find_our_declaration(decls: dict, our_notes: set, stored_id: str = "") -> tuple:
    """Return (declaration_id, record) for THIS node's declaration, or (None, None).

    Match priority: a locally stored id, else any declaration whose locked_note_id is one of
    our SDP key's notes (the stake note stays visible in the wallet after locking)."""
    if stored_id and stored_id in decls:
        return stored_id, decls[stored_id]
    for did, rec in decls.items():
        if isinstance(rec, dict) and rec.get("locked_note_id") in our_notes:
            return did, rec
    return None, None

def state_path(config_path: Path) -> Path:
    return config_path.parent / "blend-declaration.json"

def load_stored_id(config_path: Path) -> str:
    p = state_path(config_path)
    if p.exists():
        try:
            return json.loads(p.read_text()).get("declaration_id", "") or ""
        except (json.JSONDecodeError, OSError):
            return ""
    return ""

def save_state(config_path: Path, obj: dict) -> None:
    try:
        state_path(config_path).write_text(json.dumps(obj, indent=2) + "\n")
    except OSError as e:
        warn(f"could not persist declaration state: {e}")

# ── faucet (only when --fund) ────────────────────────────────────────────────────────────────
def request_faucet(faucet: str, pk: str) -> None:
    step(f"Requesting faucet funds for the SDP key {pk[:12]}…")
    status, body = http_post(f"{faucet}/{pk}", {}, timeout=20)
    if 200 <= status < 300:
        ok(f"faucet accepted the request ({body.strip() or 'queued'})")
    elif status == 429:
        secs = re.search(r"\d+", body)
        raise fail("E_FAUCET_COOLDOWN", f"faucet cooldown — wait {secs.group(0) if secs else 'a bit'}s and retry")
    else:
        raise fail("E_FAUCET", f"faucet error (http {status}): {body.strip() or '<empty>'}")

def wait_for_funds(node_api: str, pk: str, tries: int = 18, gap: int = 10) -> bool:
    step("Waiting for funds to land (a few blocks)…")
    for _ in range(tries):
        time.sleep(gap)
        if our_note_ids(node_api, pk):
            ok("SDP key funded")
            return True
    return False

# ── summary emit ──────────────────────────────────────────────────────────────────────────────
def emit_result(as_json: bool, obj: dict) -> None:
    if as_json:
        print("RESULT: " + json.dumps(obj), flush=True)

def describe_decl(node_api: str, did: str, rec: dict) -> dict:
    core_peers = 0
    try:
        _, bi = http_get_json(f"{node_api}/blend/info", timeout=4)
        ci = (bi or {}).get("core_info") or {}
        core_peers = len((ci.get("current_epoch_peers") or []))
    except (urllib.error.URLError, OSError):
        pass
    return {
        "declaration_id": did,
        "service_type": rec.get("service_type"),
        "locator": (rec.get("locators") or [None])[0],
        "created": rec.get("created"),
        "active": rec.get("active"),
        "withdraw_at": rec.get("withdraw_at"),
        "nonce": rec.get("nonce"),
        "core_peers": core_peers,
    }

# ── subcommands ────────────────────────────────────────────────────────────────────────────────
def cmd_status(args) -> int:
    step("Reading node + Blend state (read-only)")
    if not node_up(args.node_api):
        raise fail("E_NODE_UNREACHABLE", f"node API not reachable at {args.node_api} — is the node running?")
    ok(f"node reachable at {args.node_api}")

    cfg_text = read_config_text(Path(args.config))
    sdp_pk = sdp_funding_pk(cfg_text)
    port = blend_port(cfg_text)
    info(f"SDP funding key: {sdp_pk[:12]}…{sdp_pk[-6:]}  ·  blend port: {port}")

    notes = our_note_ids(args.node_api, sdp_pk)
    info(f"SDP key funded: {'yes' if notes else 'no'} ({len(notes)} note(s))")

    decls = all_declarations(args.node_api)
    did, rec = find_our_declaration(decls, notes, load_stored_id(Path(args.config)))
    result = {"ok": True, "declared": bool(rec), "sdp_key_funded": bool(notes),
              "blend_port": port, "total_declarations_seen": len(decls)}
    if rec:
        d = describe_decl(args.node_api, did, rec)
        result.update(d)
        earning = (rec.get("nonce") or 0) > 0 and d["core_peers"] > 0
        ok(f"DECLARED — id {did[:12]}…  created@{d['created']} active@{d['active']} "
           f"nonce={d['nonce']} core_peers={d['core_peers']} withdraw_at={d['withdraw_at']}")
        info(f"locator: {d['locator']}")
        if earning:
            ok("earning: nonce is advancing and the node is in the Core set")
        else:
            warn("declared but not confirmed earning — if nonce stays flat, the Blend UDP port "
                 "is likely not reachable from the internet (port-forward it). See "
                 "connect-node-to-blend-core guide.")
    else:
        info("no Blend declaration for this node yet — run `join` to become a Core provider")
    emit_result(args.json, result)
    return 0

def _confirm(args, prompt: str) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise fail("E_NEEDS_CONFIRM", "on-chain action needs confirmation — re-run with --yes "
                                      "(this locks a note as stake, pays a fee, and publishes your public IP)")
    ans = input(f"{prompt} [y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        raise fail("E_ABORTED", "aborted by operator")

def cmd_join(args) -> int:
    step("Preflight: node + config")
    if not node_up(args.node_api):
        raise fail("E_NODE_UNREACHABLE", f"node API not reachable at {args.node_api} — start the node first")
    ok(f"node reachable at {args.node_api}")
    cfg_path = Path(args.config)
    cfg_text = read_config_text(cfg_path)
    sdp_pk = sdp_funding_pk(cfg_text)
    port = blend_port(cfg_text)
    ok(f"SDP funding key {sdp_pk[:12]}…{sdp_pk[-6:]}  ·  blend port {port}")

    step("Checking for an existing declaration (idempotent)")
    decls = all_declarations(args.node_api)
    notes = our_note_ids(args.node_api, sdp_pk)
    did, rec = find_our_declaration(decls, notes, load_stored_id(cfg_path))
    if rec and rec.get("withdraw_at") is None:
        d = describe_decl(args.node_api, did, rec)
        ok(f"already declared — id {did[:12]}… (nonce={d['nonce']}, core_peers={d['core_peers']}). "
           "Nothing to do; /blend/join is idempotent.")
        emit_result(args.json, {"ok": True, "already_declared": True, **d})
        return 0
    info("no active declaration — proceeding to declare")

    step("Ensuring the SDP funding key has a stake note")
    if not notes:
        if args.fund:
            request_faucet(args.faucet, sdp_pk)
            if not wait_for_funds(args.node_api, sdp_pk):
                raise fail("E_NO_FUNDS", "funds did not land in time — re-run once the wallet shows a balance")
            notes = our_note_ids(args.node_api, sdp_pk)
        else:
            raise fail("E_NO_FUNDS", f"SDP key {sdp_pk[:12]}… has no notes to stake. "
                                     f"Fund it first (scripts/fund-node.sh with FUND_PK={sdp_pk}, "
                                     "or re-run this with --fund).")
    used = {r.get("locked_note_id") for r in decls.values() if isinstance(r, dict)}
    vals = note_values(args.node_api, sdp_pk)
    free = [n for n in notes if n not in used]
    if not free:
        raise fail("E_NO_FREE_NOTE", "every note on the SDP key is already locked by a declaration — "
                                     "fund the key with a fresh note to declare again")
    locked_note = args.locked_note or max(free, key=lambda n: int(vals.get(n, 0)))
    ok(f"stake note: {locked_note[:12]}…  (value {vals.get(locked_note, '?')})")

    step("Building the on-chain locator (how peers reach you)")
    locator = args.locator or f"/ip4/{resolve_public_ip(cfg_text)}/udp/{port}/quic-v1"
    if "/ip4//" in locator or not re.search(r"/ip4/\d", locator):
        raise fail("E_NO_LOCATOR", "could not determine this node's public IP for the locator. "
                                   "Pin external_address in the config, or pass --locator, and retry.")
    ok(f"locator: {locator}")
    warn("this locator (your public IP) will be published ON-CHAIN, and the note above is locked as stake.")

    _confirm(args, "Declare this node as a Blend Core provider now?")

    step("POST /blend/join")
    try:
        status, body = http_post(f"{args.node_api}/blend/join",
                                 {"locator": locator, "locked_note_id": locked_note})
    except (urllib.error.URLError, OSError) as e:
        raise fail("E_JOIN_NETWORK", f"the join request did not complete: {e}. "
                                     "The node MAY still have received it — run `status` before retrying "
                                     "(a retry could duplicate a paid transaction).")
    if not (200 <= status < 300):
        raise fail("E_JOIN_REJECTED", f"node rejected the declaration (http {status}): {body.strip() or '<empty>'}")
    new_id = body.strip().strip('"')
    if not new_id or not HEX_RE.match(new_id):
        raise fail("E_NO_DECL_ID", f"node returned no declaration id (got: {body.strip()!r}); "
                                   "outcome uncertain — run `status` to reconcile.")
    ok(f"declared — declaration id {new_id}")
    save_state(cfg_path, {"declaration_id": new_id, "locked_note_id": locked_note,
                          "locator": locator, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    step("Verifying the declaration landed")
    created = active = None
    for _ in range(12):
        time.sleep(5)
        rec2 = all_declarations(args.node_api).get(new_id)
        if rec2:
            created, active = rec2.get("created"), rec2.get("active")
            ok(f"on-chain: created@{created}, becomes active@{active} (≈ created+2 epochs)")
            break
    else:
        warn("not yet visible in /mantle/sdp/declarations — it lands in a block shortly; re-run `status`.")

    print()
    warn("KEEP ELIGIBLE: forward the Blend UDP port to this machine, or the node declares but never "
         f"earns (nonce stays flat). Port to forward: {port} (and the node's quic/discovery port). "
         "Verify with `status` — nonce should advance each epoch.")
    emit_result(args.json, {"ok": True, "declared": True, "declaration_id": new_id,
                            "locator": locator, "locked_note_id": locked_note,
                            "created": created, "active": active, "blend_port": port})
    return 0

def cmd_withdraw(args) -> int:
    step("Finding this node's declaration")
    if not node_up(args.node_api):
        raise fail("E_NODE_UNREACHABLE", f"node API not reachable at {args.node_api}")
    cfg_path = Path(args.config)
    cfg_text = read_config_text(cfg_path)
    sdp_pk = sdp_funding_pk(cfg_text)
    decls = all_declarations(args.node_api)
    notes = our_note_ids(args.node_api, sdp_pk)
    did = args.declaration_id or find_our_declaration(decls, notes, load_stored_id(cfg_path))[0]
    if not did:
        raise fail("E_NO_DECLARATION", "no declaration found for this node to withdraw")
    ok(f"declaration {did[:12]}…")
    _confirm(args, "Schedule withdrawal of this Blend declaration?")
    step("POST /sdp/withdrawal")
    try:
        status, body = http_post(f"{args.node_api}/sdp/withdrawal", did)  # body = JSON-quoted id
    except (urllib.error.URLError, OSError) as e:
        raise fail("E_WITHDRAW_NETWORK", f"withdrawal request did not complete: {e} — run `status` before retry")
    if not (200 <= status < 300):
        raise fail("E_WITHDRAW_REJECTED", f"node rejected the withdrawal (http {status}): {body.strip() or '<empty>'}")
    ok("withdrawal scheduled — the declaration winds down over the next couple of epochs")
    emit_result(args.json, {"ok": True, "withdrawing": True, "declaration_id": did})
    return 0

# ── cli ─────────────────────────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Make this CLI node a Blend Core provider (observably).")
    p.add_argument("action", nargs="?", default="status", choices=["status", "join", "withdraw"],
                   help="status (default, read-only) · join · withdraw")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help=f"node user_config.yaml (default: {DEFAULT_CONFIG})")
    p.add_argument("--node-api", default=DEFAULT_NODE_API, help=f"node RPC (default: {DEFAULT_NODE_API})")
    p.add_argument("--faucet", default=DEFAULT_FAUCET, help="faucet backend base URL (for --fund)")
    p.add_argument("--yes", action="store_true", help="confirm on-chain actions non-interactively")
    p.add_argument("--fund", action="store_true", help="request faucet funds for the SDP key if it is empty")
    p.add_argument("--locked-note", default="", help="note id to lock as stake (default: largest free note)")
    p.add_argument("--locator", default="", help="override the on-chain locator multiaddr")
    p.add_argument("--declaration-id", default="", help="withdraw: the declaration id (default: auto-detect)")
    p.add_argument("--json", action="store_true", help="also print a final machine-readable RESULT line")
    return p

def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"status": cmd_status, "join": cmd_join, "withdraw": cmd_withdraw}
    try:
        return handlers[args.action](args)
    except BlendError as e:
        print(_c("1;31", f"ERROR {e.err_id}: {e.message}"), file=sys.stderr, flush=True)
        emit_result(args.json, {"ok": False, "error_id": e.err_id, "error": e.message})
        return 1
    except KeyboardInterrupt:
        print(_c("1;31", "ERROR E_INTERRUPTED: aborted"), file=sys.stderr)
        return 130

if __name__ == "__main__":
    sys.exit(main())
