"""Validate referential and business consistency of the data. Exit code 1 if any check fails.

Usage:  uv run python -m scripts.validate_demo_data
"""

from __future__ import annotations

from app.infrastructure.demo.validation import validate_dataset
from scripts._common import engine_and_settings


def main() -> int:
    engine, _ = engine_and_settings()
    results = validate_dataset(engine)
    for r in results:
        mark = "INFO" if r.informational else ("PASS" if r.passed else "FAIL")
        print(f"[{mark}] {r.name:<44} {r.violations:>4}  {r.description}")
    failed = [r for r in results if not r.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
