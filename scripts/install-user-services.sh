#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${SP7_POWERLAB_BIN:-$(command -v sp7-powerlab || true)}"

if [[ -z "$BIN" ]]; then
  echo "sp7-powerlab is not in PATH" >&2
  exit 1
fi

USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$USER_DIR"

# Remove the old scheduled review units. Review packs are now explicit/on-demand.
systemctl --user disable --now sp7-powerlab-hourly.timer 2>/dev/null || true
systemctl --user stop sp7-powerlab-hourly.service 2>/dev/null || true
rm -f "$USER_DIR/sp7-powerlab-hourly.service" "$USER_DIR/sp7-powerlab-hourly.timer"

sed   -e "s|@ROOT@|$ROOT|g"   -e "s|@POWERLAB_BIN@|$BIN|g"   "$ROOT/systemd/sp7-powerlab.service.in"   > "$USER_DIR/sp7-powerlab.service"

systemctl --user daemon-reload
systemctl --user enable --now sp7-powerlab.service

echo "PowerLab user service installed. Review packs are explicit via 'sp7-powerlab review-pack'."
