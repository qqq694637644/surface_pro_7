from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .measurement import MinimalMeter


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sp7-powerlab-meter",
        description="Minimal low-overhead Surface Pro 7 battery meter for A/B baselines.",
    )
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--count", type=int)
    p.add_argument("--sys-root", default="/sys")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.interval <= 0:
        raise SystemExit("--interval must be > 0")
    if args.count is not None and args.count <= 0:
        raise SystemExit("--count must be > 0")

    meter = MinimalMeter(Path(args.sys_root))
    count = 0
    while True:
        started = time.monotonic()
        print(
            json.dumps(
                meter.sample(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            flush=True,
        )
        count += 1
        if args.count is not None and count >= args.count:
            break
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, args.interval - elapsed))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
