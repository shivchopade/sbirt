"""Export the API contract to contracts/openapi.json (the frontend generates its typed client from it).

Usage:  uv run python scripts/export_openapi.py [--check]
--check exits non-zero when the committed contract is out of date (used by the test suite).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import REPO_ROOT
from app.main import create_app

CONTRACT = REPO_ROOT / "contracts" / "openapi.json"


def render() -> str:
    spec = create_app(load_models=False).openapi()
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = render()
    if args.check:
        current = CONTRACT.read_text() if CONTRACT.exists() else ""
        if current != content:
            print("contracts/openapi.json is out of date — run: make contract", file=sys.stderr)
            return 1
        print("contract up to date")
        return 0
    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT.write_text(content)
    print(f"wrote {CONTRACT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
