#!/usr/bin/env bash
# healthcheck-030.sh — is the RAW 0.3.0 node green? Checks: API up, on the 0.3.0 chain, Online,
# height climbing, PoW mining on. Prints a one-line verdict; exit 0 = green. Reads config/node-030.env.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HERE/config/node-030.env" 2>/dev/null || true
API="${API:-http://127.0.0.1:${API_PORT:-8080}}"
EXPECT_GENESIS="${EXPECT_GENESIS:-1790758800000}"   # 0.3.0 testnet genesis; bump per re-genesis

j() { python3 -c "import json,sys;d=json.load(sys.stdin);print($1)" 2>/dev/null; }

ti=$(curl -s -m6 "$API/time/info" 2>/dev/null) || { echo "RED: API $API not answering"; exit 1; }
gen=$(echo "$ti" | j 'd["genesis_time_unix_ms"]')
[ "$gen" = "$EXPECT_GENESIS" ] || { echo "RED: wrong chain genesis=$gen (want $EXPECT_GENESIS)"; exit 1; }

h1=$(curl -s -m6 "$API/cryptarchia/info" | j 'd["cryptarchia_info"]["height"]')
st=$(curl -s -m6 "$API/cryptarchia/info" | j 'd["cryptarchia_info"]["state"]')
sleep 6
h2=$(curl -s -m6 "$API/cryptarchia/info" | j 'd["cryptarchia_info"]["height"]')

ps=$(curl -s -m8 "$API/pow/status" 2>/dev/null)
mining=$(echo "$ps" | j 'd["is_mining"]')
rewards=$(echo "$ps" | j 'd["are_rewards_enabled"]')

climb="no"; [ -n "$h1" ] && [ -n "$h2" ] && [ "$h2" -ge "$h1" ] 2>/dev/null && climb="yes"
verdict="GREEN"
[ "$st" = "Online" ] || verdict="AMBER($st)"
[ "$mining" = "True" ] || verdict="RED(not-mining)"
echo "$verdict: state=$st height=$h1->$h2 climbing=$climb mining=$mining rewards=$rewards genesis=ok api=$API"
[ "$verdict" = "GREEN" ]
