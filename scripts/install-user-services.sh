#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${SP7_POWERLAB_BIN:-$(command -v sp7-powerlab || true)}"

if [[ -z "$BIN" ]]; then
  echo "sp7-powerlab is not in PATH. Activate/install the project first." >&2
  exit 1
fi

USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$USER_DIR" "$ROOT/runtime"

render() {
  local src="$1" dst="$2"
  sed     -e "s|@ROOT@|$ROOT|g"     -e "s|@POWERLAB_BIN@|$BIN|g"     "$src" > "$dst"
}

render "$ROOT/systemd/sp7-powerlab-collector.service.in" "$USER_DIR/sp7-powerlab-collector.service"
render "$ROOT/systemd/sp7-powerlab-hourly.service.in" "$USER_DIR/sp7-powerlab-hourly.service"
INTERVAL="$("$BIN" config-get llm.analysis_interval_minutes --config "$ROOT/config/powerlab.toml")"
sed +  -e "s|OnUnitActiveSec=.*|OnUnitActiveSec=${INTERVAL}min|g" +  "$ROOT/systemd/sp7-powerlab-hourly.timer" +  > "$USER_DIR/sp7-powerlab-hourly.timer"

systemctl --user daemon-reload
systemctl --user enable --now sp7-powerlab-collector.service
systemctl --user enable --now sp7-powerlab-hourly.timer

echo "Installed:"
systemctl --user --no-pager status sp7-powerlab-collector.service || true
systemctl --user --no-pager status sp7-powerlab-hourly.timer || true
