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

sed   -e "s|@ROOT@|$ROOT|g"   -e "s|@POWERLAB_BIN@|$BIN|g"   "$ROOT/systemd/sp7-powerlab.service.in"   > "$USER_DIR/sp7-powerlab.service"

sed   -e "s|@ROOT@|$ROOT|g"   -e "s|@POWERLAB_BIN@|$BIN|g"   "$ROOT/systemd/sp7-powerlab-hourly.service.in"   > "$USER_DIR/sp7-powerlab-hourly.service"

cp "$ROOT/systemd/sp7-powerlab-hourly.timer" "$USER_DIR/sp7-powerlab-hourly.timer"

systemctl --user daemon-reload
systemctl --user enable --now sp7-powerlab.service
systemctl --user enable --now sp7-powerlab-hourly.timer

echo "PowerLab v2 user services installed."
