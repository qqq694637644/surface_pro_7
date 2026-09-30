from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .config import load_config
from .envelopes import EnvelopeRegistry
from .experiments import TrialManager
from .llm import apply_decision, build_knowledge_pack
from .service import build_actuator
from .storage import Database, LegacyDatabaseError


def _default_root() -> Path:
    configured = os.environ.get("SP7_POWERLAB_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "config" / "powerlab.toml").exists():
        return cwd
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "config" / "powerlab.toml").exists():
        return source_root
    return cwd


ROOT = _default_root()


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _registry_stack():
    config = load_config(ROOT)
    db = Database(config.path("storage.database"))
    registry = EnvelopeRegistry(ROOT, db)
    registry.load()
    return config, db, registry


def _runtime_decision_path(raw: str, *, root: Path | None = None) -> Path:
    root = (root or ROOT).resolve()
    runtime_root = (root / "runtime").resolve()
    raw_path = Path(raw).expanduser()
    decision_path = raw_path.resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    try:
        decision_path.relative_to(runtime_root)
    except ValueError as exc:
        raise SystemExit(
            "agent decision files must live under the project runtime directory"
        ) from exc
    return decision_path


def cmd_observe(args: argparse.Namespace) -> int:
    _config_value, db, _registry = _registry_stack()
    try:
        _emit({"latest": db.latest_sample()})
    finally:
        db.close()
    return 0


def cmd_hourly(args: argparse.Namespace) -> int:
    config, db, registry = _registry_stack()
    try:
        pack = build_knowledge_pack(config, db, registry)
        _emit(pack)
    finally:
        db.close()
    return 0


def cmd_submit_decision(args: argparse.Namespace) -> int:
    config, db, registry = _registry_stack()
    actuator, _available, _mode = build_actuator(config)
    trials = TrialManager(config, db, registry, actuator)
    try:
        decision_path = _runtime_decision_path(args.decision)
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        _emit(
            apply_decision(
                decision,
                config=config,
                db=db,
                registry=registry,
                trials=trials,
            )
        )
    finally:
        db.close()
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sp7-powerlab-agent",
        description=(
            "Narrow PowerLab agent surface: read telemetry/knowledge and submit "
            "structured decisions. No human approval or direct HWP commands."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    observe = sub.add_parser("observe")
    observe.set_defaults(func=cmd_observe)

    hourly = sub.add_parser("hourly")
    hourly.set_defaults(func=cmd_hourly)

    submit = sub.add_parser("submit-decision")
    submit.add_argument("decision")
    submit.set_defaults(func=cmd_submit_decision)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except LegacyDatabaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
