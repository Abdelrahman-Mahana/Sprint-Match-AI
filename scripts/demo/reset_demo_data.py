"""Remove all application data and re-seed the demo dataset.

Usage:  uv run python -m scripts.reset_demo_data [--reference-date YYYY-MM-DD] [--seed N]
"""

from __future__ import annotations

import sys

from scripts.seed_demo_data import main

if __name__ == "__main__":
    raise SystemExit(main(["--reset", *sys.argv[1:]]))
