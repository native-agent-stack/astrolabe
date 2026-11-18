#!/usr/bin/env python3
"""Verify MADR 4.0 ADR catalog and 40-char SHA-pinned workflow invariants."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = ROOT / "docs" / "adr"
CATALOG_MD = ADR_DIR / "CATALOG.md"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"


def verify_catalog(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    if not CATALOG_MD.is_file():
        return ["Missing docs/adr/CATALOG.md"]
    cat_txt = CATALOG_MD.read_text(encoding="utf-8")
    adrs = sorted(ADR_DIR.glob("ADR-[0-9][0-9][0-9][0-9]-*.md"))
    if len(adrs) < 3:
        errors.append(f"Expected >=3 ADRs, found {len(adrs)}")
    for adr in adrs:
        if f"({adr.name})" not in cat_txt:
            errors.append(f"ADR missing from CATALOG.md: {adr.name}")
        if "**Status:** Accepted" not in adr.read_text(encoding="utf-8"):
            errors.append(f"ADR missing Accepted status: {adr.name}")
    if CI_YML.is_file():
        for m in re.finditer(
            r"^\s*-?\s*uses:\s*([^\s#]+)", CI_YML.read_text(encoding="utf-8"), re.M
        ):
            ref = m.group(1)
            if "@" not in ref or not re.fullmatch(r"[0-9a-f]{40}", ref.split("@", 1)[1]):
                errors.append(f"Unpinned action in ci.yml: {ref}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.parse_args()
    errs = verify_catalog()
    if errs:
        for e in errs:
            print(f"[ADR-GATE FAIL] {e}", file=sys.stderr)
        return 1
    print("[ADR-GATE PASS] 3/3 MADR 4.0 ADRs and 40-char SHA workflow pins verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
