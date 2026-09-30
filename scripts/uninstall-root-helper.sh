#!/usr/bin/env bash
set -euo pipefail

HELPER_ROOT="${SP7_POWERLAB_HELPER_ROOT:-/opt/sp7-powerlab-helper}"

sudo systemctl disable --now sp7-powerlab-root-helper.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/sp7-powerlab-root-helper.service
sudo rm -rf "$HELPER_ROOT"
sudo systemctl daemon-reload
