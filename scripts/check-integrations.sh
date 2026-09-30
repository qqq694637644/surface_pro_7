#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== doctor =="
sp7-powerlab doctor || true

echo "== actuators =="
sp7-powerlab actuator-inspect

echo "== one sample =="
sp7-powerlab collect-once

echo "== collector overhead =="
sp7-powerlab collector-benchmark --samples 8

echo "== service/database =="
sp7-powerlab service-status
