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
FIXED_WAS_ENABLED="$(systemctl --user is-enabled sp7-powerlab-fixed.service 2>/dev/null || true)"

# Remove the old scheduled review units. Review packs are now explicit/on-demand.
systemctl --user disable --now sp7-powerlab-hourly.timer 2>/dev/null || true
systemctl --user stop sp7-powerlab-hourly.service 2>/dev/null || true
rm -f "$USER_DIR/sp7-powerlab-hourly.service" "$USER_DIR/sp7-powerlab-hourly.timer"

sed   -e "s|@ROOT@|$ROOT|g"   -e "s|@POWERLAB_BIN@|$BIN|g"   "$ROOT/systemd/sp7-powerlab.service.in"   > "$USER_DIR/sp7-powerlab.service"
sed   -e "s|@ROOT@|$ROOT|g"   -e "s|@POWERLAB_BIN@|$BIN|g"   "$ROOT/systemd/sp7-powerlab-fixed.service.in"   > "$USER_DIR/sp7-powerlab-fixed.service"

systemctl --user daemon-reload
if [[ "$FIXED_WAS_ENABLED" == "enabled" ]]; then
  systemctl --user disable --now sp7-powerlab.service 2>/dev/null || true
  systemctl --user enable --now sp7-powerlab-fixed.service
else
  systemctl --user disable --now sp7-powerlab-fixed.service 2>/dev/null || true
  systemctl --user enable --now sp7-powerlab.service
fi

echo "PowerLab user services installed; the previously selected Dynamic/Fixed runtime mode was preserved."
