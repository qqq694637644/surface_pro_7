#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

sp7-powerlab hourly   --config "$ROOT/config/powerlab.toml"   --output "$ROOT/runtime/hourly-pack.json"

cat "$ROOT/runtime/hourly-pack.json"

cat <<'EOF' >&2

MCP workflow:
1. Read runtime/hourly-pack.json.
2. Return exactly one structured decision matching schemas/llm-decision-v1.schema.json.
3. Save it to runtime/llm-decision.json.
4. Run:
   sp7-powerlab llm-apply runtime/llm-decision.json
EOF
