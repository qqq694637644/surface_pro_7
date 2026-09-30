#!/usr/bin/env bash
set -euo pipefail

sp7-powerlab doctor

echo
echo "thermald:"
systemctl --no-pager status thermald || true

echo
echo "intel_pstate:"
cat /sys/devices/system/cpu/intel_pstate/status 2>/dev/null || true

echo
echo "RAPL:"
find /sys/class/powercap -maxdepth 2 -name energy_uj -print 2>/dev/null || true

echo
echo "Battery:"
find /sys/class/power_supply -maxdepth 2 -name power_now -print 2>/dev/null || true
