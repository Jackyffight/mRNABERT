#!/usr/bin/env python3
"""Write or verify the frozen workflow contract for the current version."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from design_flow.workflow import (  # noqa: E402
    WORKFLOW_VERSION,
    workflow_contract,
    workflow_contract_sha256,
)


def frozen_contract() -> dict[str, object]:
    contract = workflow_contract()
    contract["contract_sha256"] = workflow_contract_sha256()
    return contract


def rendered_contract() -> str:
    return json.dumps(frozen_contract(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="write the current version snapshot")
    args = parser.parse_args()
    path = ROOT / "docs" / f"workflow-v{WORKFLOW_VERSION}.json"
    expected = rendered_contract()
    if args.write:
        if path.is_file() and path.read_text(encoding="utf-8") != expected:
            print(
                f"Refusing to rewrite frozen workflow version {WORKFLOW_VERSION}: {path}; "
                "increment WORKFLOW_VERSION first",
                file=sys.stderr,
            )
            return 1
        path.write_text(expected, encoding="utf-8")
        print(f"Wrote {path}")
        return 0
    if not path.is_file() or path.read_text(encoding="utf-8") != expected:
        print(f"Frozen workflow differs from executable contract: {path}", file=sys.stderr)
        return 1
    print(f"Workflow contract is frozen and current: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
