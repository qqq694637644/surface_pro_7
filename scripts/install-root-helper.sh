#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
SOCKET="${SP7_POWERLAB_HELPER_SOCKET:-/run/sp7-powerlab/helper.sock}"
HELPER_ROOT="${SP7_POWERLAB_HELPER_ROOT:-/opt/sp7-powerlab-helper}"
VENV="$HELPER_ROOT/venv"
TMP_DIR="$(mktemp -d)"
UNIT_TMP="$(mktemp)"

cleanup() {
  rm -rf "$TMP_DIR"
  rm -f "$UNIT_TMP"
}
trap cleanup EXIT

echo "Building an immutable helper wheel set..."
python3 -m pip wheel "$ROOT" -w "$TMP_DIR"

sudo systemctl stop sp7-powerlab-root-helper.service 2>/dev/null || true
sudo rm -rf "$HELPER_ROOT"
sudo python3 -m venv "$VENV"
sudo "$VENV/bin/python" -m pip install \
  --no-index \
  --find-links "$TMP_DIR" \
  sp7-powerlab

sudo chown -R root:root "$HELPER_ROOT"
sudo chmod -R go-w "$HELPER_ROOT"

BIN="$VENV/bin/sp7-powerlab"
sed \
  -e "s|@POWERLAB_BIN@|$BIN|g" \
  -e "s|@SOCKET@|$SOCKET|g" \
  -e "s|@USER@|$USER_NAME|g" \
  "$ROOT/systemd/sp7-powerlab-root-helper.service.in" > "$UNIT_TMP"

sudo install -m 0644 "$UNIT_TMP" /etc/systemd/system/sp7-powerlab-root-helper.service
sudo systemctl daemon-reload
sudo systemctl enable --now sp7-powerlab-root-helper.service
sudo systemctl --no-pager status sp7-powerlab-root-helper.service || true

echo "Root helper installed from a root-owned copy at $HELPER_ROOT"
