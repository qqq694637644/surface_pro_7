#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests

wheel_dir="$(mktemp -d)"
trap 'rm -rf "$wheel_dir"' EXIT
python -m pip wheel . --no-deps -w "$wheel_dir"
python - "$wheel_dir" <<'PY'
import glob
import sys
import zipfile

wheel_dir = sys.argv[1]
wheels = glob.glob(f"{wheel_dir}/sp7_powerlab-*.whl")
assert len(wheels) == 1, wheels
with zipfile.ZipFile(wheels[0]) as archive:
    names = set(archive.namelist())
assert "sp7_powerlab/schemas/envelope-trial.schema.json" in names
print("packaged schema present in wheel")
PY

bash -n scripts/*.sh
git diff --check
