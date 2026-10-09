#!/usr/bin/env bash
# install-persistence-030.sh — make the RAW 0.3.x node survive a reboot, sudo-free, via a user @reboot cron.
# Also REMOVES any old 0.2.x logoscore boot line (start-on-boot.sh) so a reboot doesn't resurrect the
# dead-chain node. Idempotent. Pass NODE_TMUX=bcnode (or any env) to bake it into the @reboot line.
#   e.g. sneg:  NODE_TMUX=bcnode node-setup/scripts/install-persistence-030.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOT="$HERE/scripts/start-030-on-boot.sh"
chmod +x "$BOOT" 2>/dev/null || true
MARKER="# logos-node-agent @reboot persistence (0.3.0 raw node)"
ENVPREFIX=""
[ -n "${NODE_HOME:-}" ] && ENVPREFIX="NODE_HOME=$NODE_HOME "
[ -n "${NODE_TMUX:-}" ] && ENVPREFIX="${ENVPREFIX}NODE_TMUX=$NODE_TMUX "
[ -n "${API_PORT:-}" ]  && ENVPREFIX="${ENVPREFIX}API_PORT=$API_PORT "
LINE="@reboot ${ENVPREFIX}$BOOT   $MARKER"

command -v crontab >/dev/null 2>&1 || { echo "  ✗ no crontab — install cron or use linger"; exit 1; }
CUR="$(crontab -l 2>/dev/null || true)"
# Drop: the old 0.2.x boot line, any prior 0.3.0 line, and the stale autoclaim cron.
NEW="$(printf '%s\n' "$CUR" \
  | grep -v 'logos-node-agent @reboot persistence' \
  | grep -v 'start-on-boot.sh' \
  | grep -v 'logos-autoclaim' \
  | grep -v '^[[:space:]]*$')"
{ printf '%s\n' "$NEW"; printf '%s\n' "$LINE"; } | grep -v '^[[:space:]]*$' | crontab -
echo "  ✓ @reboot now runs the 0.3.x raw-node boot script:"
echo "      $LINE"
echo "    Old 0.2.x logoscore boot line + stale autoclaim cron removed (if present)."
echo "    Undo:  crontab -l | grep -v 'logos-node-agent @reboot persistence (0.3.0' | crontab -"
