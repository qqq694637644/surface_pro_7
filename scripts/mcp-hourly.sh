#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/runtime/hourly-pack-v2.json}"

mkdir -p "$(dirname "$OUT")"
sp7-powerlab hourly --output "$OUT" >/dev/null
cat "$OUT"
