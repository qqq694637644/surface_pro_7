#!/usr/bin/env bash
set -euo pipefail

USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
systemctl --user disable --now sp7-powerlab.service 2>/dev/null || true
systemctl --user disable --now sp7-powerlab-hourly.timer 2>/dev/null || true
systemctl --user stop sp7-powerlab-hourly.service 2>/dev/null || true
rm -f "$USER_DIR/sp7-powerlab.service" "$USER_DIR/sp7-powerlab-hourly.service" "$USER_DIR/sp7-powerlab-hourly.timer"
systemctl --user daemon-reload
