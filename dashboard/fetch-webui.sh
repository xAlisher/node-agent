#!/usr/bin/env bash
# Download (or update) the Logos node web UI release: a self-contained tarball that needs only python3.
#   WEBUI_DIR      install dir (default ~/logos-node-webui)
#   WEBUI_VERSION  release tag, e.g. v0.1.0 (default: latest)
# Re-run to update in place. Sudo-free.
set -euo pipefail
WEBUI_DIR="${WEBUI_DIR:-$HOME/logos-node-webui}"
REPO="https://github.com/xAlisher/logos-node-webui/releases"
if [ -n "${WEBUI_VERSION:-}" ]; then
  URL="$REPO/download/$WEBUI_VERSION/logos-node-webui.tar.gz"
else
  URL="$REPO/latest/download/logos-node-webui.tar.gz"
fi
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$URL" | tar -xz -C "$TMP"
mkdir -p "$(dirname "$WEBUI_DIR")"
rm -rf "$WEBUI_DIR.new" && mv "$TMP/logos-node-webui" "$WEBUI_DIR.new"
rm -rf "$WEBUI_DIR" && mv "$WEBUI_DIR.new" "$WEBUI_DIR"
echo "web UI $(cat "$WEBUI_DIR/VERSION") installed in $WEBUI_DIR"
