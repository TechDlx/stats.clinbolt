"""Sanity-check a generated dashboard JSON file before it is published.

refresh.sh runs this against the freshly built file in a temp directory and only
moves it into the served location if it passes, so a bad build leaves the last
good data in place.

    python -m common.validate <file.json> [--max-bytes N] [--min-records N]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_MAX_BYTES = 2 * 1024 * 1024        # 2 MB budget
DEFAULT_MIN_RECORDS = 10_000               # a real full run is ~600k


def validate(path: Path, max_bytes: int, min_records: int) -> list[str]:
    """Return a list of problems; empty means the file is fit to publish."""
    problems: list[str] = []

    if not path.exists():
        return ["file does not exist: {0}".format(path)]

    size = path.stat().st_size
    if size == 0:
        return ["file is empty: {0}".format(path)]
    if size > max_bytes:
        problems.append(
            "file is {0:,} bytes, over the {1:,} byte budget".format(size, max_bytes)
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return ["file is not readable JSON: {0}".format(exc)]

    for key in ("schema_version", "metadata", "dimensions", "cells"):
        if key not in data:
            problems.append("missing top-level key: {0}".format(key))
    if problems:
        return problems

    metadata = data["metadata"]
    for key in ("generated_at", "source", "total_fetched", "records_included",
                "exclusions", "bins", "ecdf_grid"):
        if key not in metadata:
            problems.append("missing metadata key: {0}".format(key))

    included = metadata.get("records_included", 0)
    if included < min_records:
        problems.append(
            "only {0:,} records included, expected at least {1:,}".format(
                included, min_records
            )
        )
    if metadata.get("total_fetched", 0) < included:
        problems.append("total_fetched is smaller than records_included")

    cells = data["cells"]
    if not cells:
        problems.append("no filter cells in output")
    if "all|all" not in cells:
        problems.append("the all|all cell is missing")

    grid_length = len(metadata.get("ecdf_grid", []))
    bin_count = len(metadata.get("bins", []))

    for key, cell in cells.items():
        for segment, entry in cell.items():
            where = "{0}/{1}".format(key, segment)
            for field in ("n", "zero", "bins", "pct", "ecdf"):
                if field not in entry:
                    problems.append("{0}: missing field {1}".format(where, field))
                    break
            else:
                if len(entry["bins"]) != bin_count:
                    problems.append("{0}: bin count mismatch".format(where))
                if sum(entry["bins"]) != entry["n"]:
                    problems.append("{0}: bins do not sum to n".format(where))
                if len(entry["ecdf"]) != grid_length:
                    problems.append("{0}: ecdf length mismatch".format(where))
                curve = entry["ecdf"]
                if any(a > b + 1e-9 for a, b in zip(curve, curve[1:])):
                    problems.append("{0}: ecdf is not monotonic".format(where))
                if entry["n"] and abs(curve[-1] - 1.0) > 1e-6:
                    problems.append("{0}: ecdf does not reach 1.0".format(where))

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument(
        "--min-records",
        type=int,
        default=DEFAULT_MIN_RECORDS,
        help="lower bound on records_included; pass 0 for development builds",
    )
    args = parser.parse_args()

    problems = validate(args.path, args.max_bytes, args.min_records)
    if problems:
        print("VALIDATION FAILED for {0}".format(args.path), file=sys.stderr)
        for problem in problems:
            print("  - {0}".format(problem), file=sys.stderr)
        return 1

    size_kb = args.path.stat().st_size / 1024
    print("OK {0} ({1:.1f} KB)".format(args.path, size_kb))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
