#!/usr/bin/env python3
"""join_blend_core.py — make this CLI node a Blend Core provider, observably.

Blend Core eligibility is the gate the referral program's reward model relies on. On a
Basecamp node the UI drives this; on a CLI node there was no equivalent. This script is that
equivalent: an agent (or an operator) runs it, watches each step, and gets a node declared and
earning — or a clear reason why not.

It is **stdlib-only** (urllib/json/argparse — no pip), like the rest of this repo, and mirrors the
flow proven on sneg with the 0.3.1 node (2026-10-12):

    one note >= 1B (MIN_STAKE) on the Blend ZK key + fees on the SDP funding key
      ->  POST /blend/join {locator, service_note_id}  ->  active at created+2 epochs

The 1B minimum is checked against that single note (core/src/mantle/ops/sdp/declare.rs), and the join
call only submits the tx: a smaller note still gets a declaration id back, then the tx is dropped at
block validation. So this script refuses to join without a big enough note.

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
    funds for the SDP key (fees only) if it is empty.
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

# ── config discovery defaults (aligned to node-setup: ~/logos-node-030/user_config.yaml) ──────
DEFAULT_NODE_HOME = Path(os.environ.get("NODE_HOME", str(Path.home() / "logos-node-030")))
DEFAULT_CONFIG = Path(os.environ.get("NODE_CONFIG", str(DEFAULT_NODE_HOME / "user_config.yaml")))
DEFAULT_NODE_API = os.environ.get("NODE_API", os.environ.get("API", "http://127.0.0.1:8080"))
DEFAULT_FAUCET = os.environ.get("FAUCET_BACKEND", "https://testnet.blockchain.logos.co/web/faucet-backend")
DEFAULT_BLEND_PORT = 3400
# sdp_config.min_stake.threshold of the 0.3.x testnet (nodes/node/binary/src/config/deployment/settings.yaml)
MIN_STAKE = int(os.environ.get("BLEND_MIN_STAKE", "1000000000"))
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
    # Probe /cryptarchia/info, not /blend/info: the blend endpoints are not served until the node is
    # Online (during bootstrap /blend/info refuses the connection), but /cryptarchia/info is up as soon
    # as the API is, so this correctly reports a live-but-still-syncing node as up.
    try:
        http_get(f"{node_api}/cryptarchia/info", timeout=3)
        return True
    except (urllib.error.URLError, OSError):
        return False

def node_online(node_api: str) -> bool:
    """True only once the node reports state Online (Blend needs an Online node)."""
    try:
        _, data = http_get_json(f"{node_api}/cryptarchia/info", timeout=4)
    except (urllib.error.URLError, OSError):
        return False
    ci = (data or {}).get("cryptarchia_info") or (data or {})
    return str(ci.get("state", "")).lower() == "online"

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

def blend_zk_pk(text: str) -> str:
    """The Blend ZK key (blend: ... zk: secret_key_kms_id). The stake note must be owned by it: the
    declaration's zk_id is this key, and it is where Blend rewards are paid."""
    in_blend = in_zk = False
    for ln in text.splitlines():
        if re.match(r"^blend:\s*$", ln):
            in_blend = True
            continue
        if in_blend:
            if re.match(r"^\S", ln):
                break
            if ln.strip() == "zk:":
                in_zk = True
                continue
            m = re.search(r"secret_key_kms_id:\s*\"?([0-9a-fA-F]{32,})\"?", ln)
            if in_zk and m:
                return m.group(1)
    raise fail("E_ZK_KEY_MISSING", "no blend zk secret_key_kms_id in the config — "
                                   "this node has no Blend ZK key to stake with")

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

def find_our_declaration(decls: dict, zk_pk: str, stored_id: str = "") -> tuple:
    """Return (declaration_id, record) for THIS node's declaration, or (None, None).

    Match priority: a locally stored id, else the declaration whose zk_id is our Blend ZK key
    (zk_id is unique per service, so there is at most one)."""
    if stored_id and stored_id in decls:
        return stored_id, decls[stored_id]
    for did, rec in decls.items():
        if isinstance(rec, dict) and rec.get("zk_id") == zk_pk:
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
    zk_pk = blend_zk_pk(cfg_text)
    port = blend_port(cfg_text)
    info(f"SDP funding key (fees): {sdp_pk[:12]}…{sdp_pk[-6:]}  ·  Blend ZK key (stake): {zk_pk[:12]}…{zk_pk[-6:]}"
         f"  ·  blend port: {port}")

    if not node_online(args.node_api):
        warn("node is still bootstrapping (not Online yet) — the Blend API isn't served until the node "
             "is Online, so there's nothing to declare/read yet. Re-run once it reaches Online.")
        emit_result(args.json, {"ok": True, "online": False, "blend_ready": False,
                                "blend_port": port, "declared": False})
        return 0

    notes = our_note_ids(args.node_api, sdp_pk)
    info(f"SDP key funded: {'yes' if notes else 'no'} ({len(notes)} note(s))")
    zk_vals = note_values(args.node_api, zk_pk)
    biggest = max((int(v) for v in zk_vals.values()), default=0)
    info(f"largest note on the Blend ZK key: {biggest:,} (need one note >= {MIN_STAKE:,})")

    decls = all_declarations(args.node_api)
    did, rec = find_our_declaration(decls, zk_pk, load_stored_id(Path(args.config)))
    result = {"ok": True, "declared": bool(rec), "sdp_key_funded": bool(notes),
              "stake_ready": biggest >= MIN_STAKE, "largest_stake_note": biggest, "min_stake": MIN_STAKE,
              "blend_port": port, "total_declarations_seen": len(decls)}
    if rec:
        d = describe_decl(args.node_api, did, rec)
        result.update(d)
        earning = (rec.get("nonce") or 0) > 0 and d["core_peers"] > 0
        ok(f"DECLARED — id {did[:12]}…  created@{d['created']} active@{d['active']} "
           f"nonce={d['nonce']} core_peers={d['core_peers']} withdraw_at={d['withdraw_at']}")
        info(f"locator: {d['locator']}")
        try:
            _, ti = http_get_json(f"{args.node_api}/time/info", timeout=4)
            epoch = int((ti or {}).get("current_epoch"))
        except (urllib.error.URLError, OSError, TypeError, ValueError):
            epoch = None
        result["current_epoch"] = epoch
        if earning:
            ok("earning: nonce is advancing and the node is in the Core set")
        elif epoch is not None and d["active"] is not None and epoch < int(d["active"]):
            info(f"not active yet: current epoch {epoch}, becomes active at epoch {d['active']} "
                 "(nonce and core peers stay 0 until then). Have the Blend UDP port forwarded by then.")
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
    if not node_online(args.node_api):
        raise fail("E_NOT_ONLINE", "node is still bootstrapping — Blend needs an Online node. "
                                   "Wait until it reaches Online (watch healthcheck-030.sh), then join.")
    ok(f"node reachable and Online at {args.node_api}")
    cfg_path = Path(args.config)
    cfg_text = read_config_text(cfg_path)
    sdp_pk = sdp_funding_pk(cfg_text)
    zk_pk = blend_zk_pk(cfg_text)
    port = blend_port(cfg_text)
    ok(f"SDP funding key {sdp_pk[:12]}…{sdp_pk[-6:]}  ·  Blend ZK key {zk_pk[:12]}…{zk_pk[-6:]}  ·  blend port {port}")

    step("Checking for an existing declaration (idempotent)")
    decls = all_declarations(args.node_api)
    notes = our_note_ids(args.node_api, sdp_pk)
    did, rec = find_our_declaration(decls, zk_pk, load_stored_id(cfg_path))
    if rec and rec.get("withdraw_at") is None:
        d = describe_decl(args.node_api, did, rec)
        ok(f"already declared — id {did[:12]}… (nonce={d['nonce']}, core_peers={d['core_peers']}). "
           "Nothing to do; /blend/join is idempotent.")
        emit_result(args.json, {"ok": True, "already_declared": True, **d})
        return 0
    info("no active declaration — proceeding to declare")

    step("Ensuring the SDP funding key can pay the declaration fee")
    if not notes:
        if args.fund:
            request_faucet(args.faucet, sdp_pk)
            if not wait_for_funds(args.node_api, sdp_pk):
                raise fail("E_NO_FUNDS", "funds did not land in time — re-run once the wallet shows a balance")
            notes = our_note_ids(args.node_api, sdp_pk)
        else:
            raise fail("E_NO_FUNDS", f"SDP key {sdp_pk[:12]}… has no notes to pay the declaration fee. "
                                     f"Send it a small amount (e.g. 20M) from a funded key with "
                                     "POST /wallet/transactions/transfer-funds, or re-run with --fund.")
    ok(f"SDP key has {len(notes)} note(s) for fees")

    step(f"Finding a stake note >= {MIN_STAKE:,} on the Blend ZK key")
    used = {r.get("service_note_id") for r in decls.values() if isinstance(r, dict)}
    vals = note_values(args.node_api, zk_pk)
    if args.locked_note:
        locked_note = args.locked_note
        if locked_note not in vals:
            raise fail("E_NOTE_NOT_OURS", f"note {locked_note[:12]}… is not on the Blend ZK key {zk_pk[:12]}…")
    else:
        free = [n for n, v in vals.items() if n not in used and int(v) >= MIN_STAKE]
        if not free:
            biggest = max((int(v) for v in vals.values()), default=0)
            raise fail("E_STAKE_TOO_SMALL",
                       f"no free note >= {MIN_STAKE:,} on the Blend ZK key (largest: {biggest:,}). The stake "
                       "must be ONE note: notes are not summed. Send the amount to the Blend ZK key with "
                       "POST /wallet/transactions/transfer-funds (recipient_public_key = "
                       f"{zk_pk}); a tx takes at most 32 input notes, so merge small mining notes in rounds.")
        locked_note = min(free, key=lambda n: int(vals[n]))
    if int(vals[locked_note]) < MIN_STAKE:
        raise fail("E_STAKE_TOO_SMALL", f"note {locked_note[:12]}… holds {int(vals[locked_note]):,}, "
                                        f"below the {MIN_STAKE:,} minimum (the tx would be dropped)")
    ok(f"stake note: {locked_note[:12]}…  (value {int(vals[locked_note]):,})")

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
                                 {"locator": locator, "service_note_id": locked_note})
    except (urllib.error.URLError, OSError) as e:
        raise fail("E_JOIN_NETWORK", f"the join request did not complete: {e}. "
                                     "The node MAY still have received it — run `status` before retrying "
                                     "(a retry could duplicate a paid transaction).")
    if not (200 <= status < 300):
        raise fail("E_JOIN_REJECTED", f"node rejected the declaration (http {status}): {body.strip() or '<empty>'}")
    # 0.3.x answers {"declaration_id": ..., "tx_id": ...}; older nodes answered a bare JSON string.
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = body.strip().strip('"')
    new_id = parsed.get("declaration_id", "") if isinstance(parsed, dict) else str(parsed)
    tx_id = parsed.get("tx_id") if isinstance(parsed, dict) else None
    if not new_id or not HEX_RE.match(new_id):
        raise fail("E_NO_DECL_ID", f"node returned no declaration id (got: {body.strip()!r}); "
                                   "outcome uncertain — run `status` to reconcile.")
    ok(f"declared — declaration id {new_id}" + (f"  (tx {tx_id})" if tx_id else ""))
    save_state(cfg_path, {"declaration_id": new_id, "tx_id": tx_id, "service_note_id": locked_note,
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
                            "locator": locator, "service_note_id": locked_note, "tx_id": tx_id,
                            "created": created, "active": active, "blend_port": port})
    return 0

def cmd_withdraw(args) -> int:
    step("Finding this node's declaration")
    if not node_up(args.node_api):
        raise fail("E_NODE_UNREACHABLE", f"node API not reachable at {args.node_api}")
    cfg_path = Path(args.config)
    cfg_text = read_config_text(cfg_path)
    zk_pk = blend_zk_pk(cfg_text)
    decls = all_declarations(args.node_api)
    did = args.declaration_id or find_our_declaration(decls, zk_pk, load_stored_id(cfg_path))[0]
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
    p.add_argument("--fund", action="store_true", help="request faucet funds for the SDP key (fees) if it is empty")
    p.add_argument("--locked-note", default="", help="Blend ZK key note id to lock as stake "
                                                     "(default: smallest free note >= the 1B minimum)")
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
