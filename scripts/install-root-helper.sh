#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HELPER_ROOT="${SP7_POWERLAB_HELPER_ROOT:-/opt/sp7-powerlab-helper}"
SOCKET="${SP7_POWERLAB_HELPER_SOCKET:-/run/sp7-powerlab/helper.sock}"
VENV="$HELPER_ROOT/venv"
UID_VALUE="$(id -u)"
TMP_DIR="$(mktemp -d)"
UNIT_TMP="$(mktemp)"

cleanup() {
  rm -rf "$TMP_DIR"
  rm -f "$UNIT_TMP"
}
trap cleanup EXIT

python3 -m pip wheel "$ROOT" -w "$TMP_DIR"

sudo systemctl stop sp7-powerlab-root-helper.service 2>/dev/null || true
sudo rm -rf "$HELPER_ROOT"
sudo python3 -m venv "$VENV"
sudo "$VENV/bin/python" -m pip install   --no-index   --find-links "$TMP_DIR"   sp7-powerlab

sudo chown -R root:root "$HELPER_ROOT"
sudo chmod -R go-w "$HELPER_ROOT"

sed   -e "s|@POWERLAB_BIN@|$VENV/bin/sp7-powerlab|g"   -e "s|@SOCKET@|$SOCKET|g"   -e "s|@UID@|$UID_VALUE|g"   "$ROOT/systemd/sp7-powerlab-root-helper.service.in" > "$UNIT_TMP"

sudo install -m 0644 "$UNIT_TMP" /etc/systemd/system/sp7-powerlab-root-helper.service
sudo systemctl daemon-reload
sudo systemctl enable --now sp7-powerlab-root-helper.service
sudo systemctl --no-pager status sp7-powerlab-root-helper.service || true

echo "Installed root-owned restricted HWP helper at $HELPER_ROOT"
