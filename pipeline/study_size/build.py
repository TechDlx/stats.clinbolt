"""Build the precomputed summary for the "How Big Are Studies?" dashboard.

Reads every study record from the ClinicalTrials.gov API (via the cached client)
and writes a single small JSON file that the dashboard page filters entirely
client side.

Aggregation is streaming: each segment keeps a Counter of enrollment value ->
frequency rather than a list of observations, so memory stays flat enough for a
1 GB VM while percentiles remain exact.

Usage:
    python pipeline/study_size/build.py --limit-pages 2
    python pipeline/study_size/build.py --refresh
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "pipeline"))

from common import stats  # noqa: E402
from common.ctgov_client import API_URL, CtgovClient  # noqa: E402

# ---------------------------------------------------------------------------
# Classification rules.  Everything tunable about how studies are grouped or
# excluded lives in this one dict.
# ---------------------------------------------------------------------------
CONFIG = {
    "status_groups": {
        "active": [
            "NOT_YET_RECRUITING",
            "RECRUITING",
            "ENROLLING_BY_INVITATION",
            "ACTIVE_NOT_RECRUITING",
            "SUSPENDED",
        ],
        "closed": [
            "COMPLETED",
            "TERMINATED",
            "WITHDRAWN",
        ],
        "unknown": [
            "UNKNOWN",
        ],
    },
    # Expanded access records have no meaningful enrollment distribution.
    "expanded_access_statuses": [
        "AVAILABLE",
        "NO_LONGER_AVAILABLE",
        "TEMPORARILY_NOT_AVAILABLE",
        "APPROVED_FOR_MARKETING",
    ],
    "expanded_access_study_type": "EXPANDED_ACCESS",
    # Statuses that are neither active, closed nor unknown.  Verified present on
    # the live API but absent from the original spec.
    "dropped_statuses": {
        "WITHHELD": "status_withheld",
    },
    "study_types": ["INTERVENTIONAL", "OBSERVATIONAL"],
    # The API has no combined phase enum value; combinations arrive as arrays,
    # so the map is keyed by the sorted tuple of the phases field.
    "phase_map": {
        ("EARLY_PHASE1",): "EARLY_PHASE1",
        ("PHASE1",): "PHASE1",
        ("PHASE1", "PHASE2"): "PHASE1_PHASE2",
        ("PHASE2",): "PHASE2",
        ("PHASE2", "PHASE3"): "PHASE2_PHASE3",
        ("PHASE3",): "PHASE3",
        ("PHASE4",): "PHASE4",
        ("NA",): "NA",
    },
    # phases key absent entirely -- almost always an observational study.
    "phase_when_absent": "NOT_SPECIFIED",
    "enrollment_types": ["ESTIMATED", "ACTUAL"],
}

PHASE_ORDER = [
    "all",
    "EARLY_PHASE1",
    "PHASE1",
    "PHASE1_PHASE2",
    "PHASE2",
    "PHASE2_PHASE3",
    "PHASE3",
    "PHASE4",
    "NA",
    "NOT_SPECIFIED",
]

PHASE_LABELS = {
    "all": "All phases",
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE1_PHASE2": "Phase 1/2",
    "PHASE2": "Phase 2",
    "PHASE2_PHASE3": "Phase 2/3",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "Not Applicable",
    "NOT_SPECIFIED": "Not Specified",
}

STUDY_TYPE_ORDER = ["all", "INTERVENTIONAL", "OBSERVATIONAL"]

STUDY_TYPE_LABELS = {
    "all": "All study types",
    "INTERVENTIONAL": "Interventional",
    "OBSERVATIONAL": "Observational",
}

STATUS_GROUP_ORDER = ["active", "closed", "unknown"]

STATUS_GROUP_LABELS = {
    "active": "Active",
    "closed": "Closed",
    "unknown": "Unknown",
}

SEGMENTS = [
    "active_estimated",
    "active_actual",
    "closed_estimated",
    "closed_actual",
    "unknown_estimated",
    "unknown_actual",
]

SEGMENT_LABELS = {
    "active_estimated": "Active · Estimated",
    "active_actual": "Active · Actual",
    "closed_estimated": "Closed · Estimated",
    "closed_actual": "Closed · Actual",
    "unknown_estimated": "Unknown · Estimated",
    "unknown_actual": "Unknown · Actual",
}

EXCLUSION_LABELS = {
    "expanded_access": "Expanded access record (no enrollment distribution)",
    "status_withheld": "Status withheld by sponsor",
    "missing_study_type": "Study type not reported",
    "missing_enrollment_count": "Enrollment count not reported",
    "missing_enrollment_type": "Enrollment reported without Estimated/Actual type",
    "invalid_enrollment_count": "Enrollment count negative or non-numeric",
    "unclassified_status": "Overall status outside the known vocabulary",
}

# Reverse lookup: status -> group name.
_STATUS_TO_GROUP = {}
for _group, _statuses in CONFIG["status_groups"].items():
    for _status in _statuses:
        _STATUS_TO_GROUP[_status] = _group


class Aggregator:
    """Accumulates per-cell distributions while records stream past."""

    def __init__(self) -> None:
        # cells[(study_type, phase)][segment] -> Counter of enrollment values
        self.cells: dict[tuple[str, str], dict[str, Counter]] = defaultdict(
            lambda: defaultdict(Counter)
        )
        # Zero-enrollment studies are real records but cannot sit on a log axis.
        self.zeros: dict[tuple[str, str], Counter] = defaultdict(Counter)
        # Studies with a count but no Estimated/Actual type, by status group.
        self.missing_type: dict[tuple[str, str], Counter] = defaultdict(Counter)
        self.exclusions: Counter = Counter()
        self.unmapped_phases: Counter = Counter()
        self.total_fetched = 0
        self.records_included = 0
        self.zero_total = 0
        self.max_value = 0

    @staticmethod
    def _keys(study_type: str, phase: str) -> list[tuple[str, str]]:
        """Every filter cell a record contributes to (its own plus the rollups)."""
        return [
            (st, ph)
            for st in ("all", study_type)
            for ph in ("all", phase)
        ]

    def add(self, record: dict) -> None:
        self.total_fetched += 1
        protocol = record.get("protocolSection") or {}
        status_module = protocol.get("statusModule") or {}
        design = protocol.get("designModule") or {}

        status = status_module.get("overallStatus")
        study_type = design.get("studyType")

        # -- exclusions, in a fixed order so the accounting is unambiguous ----
        if (
            study_type == CONFIG["expanded_access_study_type"]
            or status in CONFIG["expanded_access_statuses"]
        ):
            self.exclusions["expanded_access"] += 1
            return

        if status in CONFIG["dropped_statuses"]:
            self.exclusions[CONFIG["dropped_statuses"][status]] += 1
            return

        status_group = _STATUS_TO_GROUP.get(status)
        if status_group is None:
            self.exclusions["unclassified_status"] += 1
            return

        if study_type not in CONFIG["study_types"]:
            self.exclusions["missing_study_type"] += 1
            return

        phase = self._phase_group(design.get("phases"))
        keys = self._keys(study_type, phase)

        enrollment = design.get("enrollmentInfo") or {}
        count = enrollment.get("count")
        if count is None:
            self.exclusions["missing_enrollment_count"] += 1
            return
        if not isinstance(count, int) or count < 0:
            self.exclusions["invalid_enrollment_count"] += 1
            return

        enrollment_type = enrollment.get("type")
        if enrollment_type not in CONFIG["enrollment_types"]:
            # Confirmed to happen on live records, e.g. NCT00081835.
            self.exclusions["missing_enrollment_type"] += 1
            for key in keys:
                self.missing_type[key][status_group] += 1
            return

        # -- included ---------------------------------------------------------
        self.records_included += 1
        segment = status_group + "_" + enrollment_type.lower()

        if count == 0:
            self.zero_total += 1
            for key in keys:
                self.zeros[key][segment] += 1
            return

        if count > self.max_value:
            self.max_value = count

        for key in keys:
            self.cells[key][segment][count] += 1

    def _phase_group(self, phases) -> str:
        if not phases:
            return CONFIG["phase_when_absent"]
        signature = tuple(sorted(phases))
        mapped = CONFIG["phase_map"].get(signature)
        if mapped is None:
            self.unmapped_phases["+".join(signature)] += 1
            return CONFIG["phase_when_absent"]
        return mapped


def build_output(agg: Aggregator, total_count: int | None, grid: list[int]) -> dict:
    """Assemble the JSON document from the accumulated counters."""
    cells: dict[str, dict] = {}
    for (study_type, phase), segments in agg.cells.items():
        key = study_type + "|" + phase
        cell: dict[str, dict] = {}
        for segment in SEGMENTS:
            counter = segments.get(segment)
            zero = agg.zeros[(study_type, phase)].get(segment, 0)
            if not counter and not zero:
                continue  # omit empty cells to keep the file small
            entry = stats.summarize(counter or Counter(), grid)
            entry["zero"] = zero
            cell[segment] = entry
        if cell:
            cells[key] = cell

    # Cells that only ever saw zero-enrollment studies still need an entry.
    for (study_type, phase), zero_counts in agg.zeros.items():
        key = study_type + "|" + phase
        cell = cells.setdefault(key, {})
        for segment, zero in zero_counts.items():
            if segment not in cell:
                entry = stats.summarize(Counter(), grid)
                entry["zero"] = zero
                cell[segment] = entry

    missing_type = {
        study_type + "|" + phase: dict(counts)
        for (study_type, phase), counts in agg.missing_type.items()
        if counts
    }

    return {
        "schema_version": 1,
        "metadata": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": {
                "name": "ClinicalTrials.gov API v2",
                "endpoint": API_URL,
                "site": "https://clinicaltrials.gov/",
                "retrieved_date": time.strftime("%Y-%m-%d", time.gmtime()),
                "registry_total_count": total_count,
            },
            "total_fetched": agg.total_fetched,
            "records_included": agg.records_included,
            "zero_enrollment_total": agg.zero_total,
            "exclusions": dict(agg.exclusions),
            "exclusion_labels": EXCLUSION_LABELS,
            "unmapped_phase_combinations": dict(agg.unmapped_phases),
            "classification_rules": {
                "status_groups": CONFIG["status_groups"],
                "expanded_access_statuses": CONFIG["expanded_access_statuses"],
                "dropped_statuses": CONFIG["dropped_statuses"],
                "phase_map": {
                    "+".join(k): v for k, v in CONFIG["phase_map"].items()
                },
                "phase_when_absent": CONFIG["phase_when_absent"],
            },
            "bins": [
                {"label": label, "min": lo, "max": hi}
                for label, (lo, hi) in zip(stats.BIN_LABELS, stats.BINS)
            ],
            "ecdf_grid": grid,
        },
        "dimensions": {
            "study_types": STUDY_TYPE_ORDER,
            "study_type_labels": STUDY_TYPE_LABELS,
            "phases": PHASE_ORDER,
            "phase_labels": PHASE_LABELS,
            "segments": SEGMENTS,
            "segment_labels": SEGMENT_LABELS,
            "status_groups": STATUS_GROUP_ORDER,
            "status_group_labels": STATUS_GROUP_LABELS,
        },
        "cells": cells,
        "missing_type": missing_type,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit-pages",
        type=int,
        default=None,
        help="stop after N API pages (development runs)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="bypass the raw cache and refetch from the API",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "site" / "dashboards" / "study-size" / "data",
        help="directory to write study_size.json into",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=REPO_ROOT / ".cache" / "ctgov",
        help="directory for the gzipped raw JSONL cache",
    )
    parser.add_argument(
        "--page-size", type=int, default=1000, help="API page size (max 1000)"
    )
    args = parser.parse_args()

    client = CtgovClient(cache_dir=args.cache_dir, page_size=args.page_size)
    agg = Aggregator()

    for record in client.iter_studies(limit_pages=args.limit_pages, refresh=args.refresh):
        agg.add(record)

    grid = stats.make_ecdf_grid(1, stats.grid_ceiling(agg.max_value))
    output = build_output(agg, client.total_count, grid)

    args.out.mkdir(parents=True, exist_ok=True)
    out_file = args.out / "study_size.json"
    # Compact separators: this file is read by a browser, not a human.
    payload = json.dumps(output, separators=(",", ":"), ensure_ascii=False)
    out_file.write_text(payload, encoding="utf-8")

    # Tiny sidecar so the landing page can show freshness without downloading
    # the whole dataset.
    meta_file = args.out / "meta.json"
    meta_file.write_text(
        json.dumps(
            {
                "generated_at": output["metadata"]["generated_at"],
                "records_included": agg.records_included,
                "source": output["metadata"]["source"]["name"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    size_kb = out_file.stat().st_size / 1024
    print("")
    print("Wrote {0} ({1:.1f} KB)".format(out_file, size_kb))
    print("  records fetched : {0:,}".format(agg.total_fetched))
    print("  records included: {0:,}".format(agg.records_included))
    print("  zero enrollment : {0:,}".format(agg.zero_total))
    print("  filter cells    : {0}".format(len(output["cells"])))
    for reason, count in agg.exclusions.most_common():
        print("  excluded [{0}]: {1:,}".format(reason, count))
    if agg.unmapped_phases:
        print("  unmapped phase combinations: {0}".format(dict(agg.unmapped_phases)))
    if size_kb > 2048:
        print("WARNING: output exceeds the 2 MB budget", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
