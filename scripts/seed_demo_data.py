"""Seed the reproducible synthetic demo dataset.

Usage:  uv run python -m scripts.seed_demo_data [--reset] [--reference-date YYYY-MM-DD] [--seed N]

Refuses to run in production and refuses to touch a non-empty database unless --reset is given.
"""

from __future__ import annotations

import argparse
import sys

from app.core.exceptions import SprintMatchError
from app.infrastructure.demo.seeder import seed_demo_data
from scripts._common import engine_and_settings, reference_date


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="truncate all application data first")
    parser.add_argument(
        "--reference-date", help="demo reference date (default: DEMO_REFERENCE_DATE or today)"
    )
    parser.add_argument("--seed", type=int, help="random seed (default: DEMO_SEED)")
    args = parser.parse_args(argv)
    engine, settings = engine_and_settings()
    try:
        ds = seed_demo_data(
            engine,
            environment=settings.environment,
            reference_date=reference_date(settings, args.reference_date),
            seed=args.seed if args.seed is not None else settings.demo_seed,
            reset=args.reset,
        )
    except SprintMatchError as exc:
        print(f"Seeding refused: {exc.message}", file=sys.stderr)
        return 1
    print(f"Seeded demo data for reference day {ds.reference_date} (now = {ds.now.isoformat()}):")
    for table, n in ds.counts().items():
        print(f"  {table:<28} {n:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
