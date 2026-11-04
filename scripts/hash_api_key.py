"""Print the SHA-256 digest of an API key for use in the API_KEYS setting.

Usage:  uv run python scripts/hash_api_key.py "<api-key>"
"""

from __future__ import annotations

import hashlib
import sys


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1]:
        print(__doc__)
        return 2
    print(hashlib.sha256(sys.argv[1].encode()).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
