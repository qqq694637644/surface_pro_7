#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PUSH=false
if [[ "${1:-}" == "--push" ]]; then
  PUSH=true
fi

sp7-powerlab knowledge-export

git add -- history/continuous proposals
if git diff --cached --quiet; then
  echo "No PowerLab knowledge changes to commit."
  exit 0
fi

STAMP="$(date -Iseconds)"
git commit -m "Update SP7 PowerLab knowledge $STAMP"

if [[ "$PUSH" == true ]]; then
  git push origin HEAD:refs/heads/main
fi
