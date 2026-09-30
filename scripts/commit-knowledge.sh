#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PUSH=false
if [[ "${1:-}" == "--push" ]]; then
  PUSH=true
fi

sp7-powerlab knowledge-export >/dev/null

git add -- config/machine.toml config/envelopes.toml history/continuous proposals
if git diff --cached --quiet; then
  echo "No PowerLab v2 knowledge changes to commit."
  exit 0
fi

git commit -m "Update SP7 PowerLab v2 knowledge $(date -Iseconds)"

if [[ "$PUSH" == true ]]; then
  git push
fi
