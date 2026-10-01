#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/runtime/hourly-pack.json}"

mkdir -p "$(dirname "$OUT")"
sp7-powerlab-agent hourly >"$OUT"
cat "$OUT"
