"""Tests for status grouping, phase mapping and the exclusion ladder."""

import pytest

from study_size.build import SEGMENTS, Aggregator, build_output
from common import stats


def make_record(status="COMPLETED", study_type="INTERVENTIONAL", phases=("PHASE2",),
                count=100, enrollment_type="ACTUAL"):
    """Build a record shaped like a real API response."""
    design = {}
    if study_type is not None:
        design["studyType"] = study_type
    if phases is not None:
        design["phases"] = list(phases)
    enrollment = {}
    if count is not None:
        enrollment["count"] = count
    if enrollment_type is not None:
        enrollment["type"] = enrollment_type
    if enrollment:
        design["enrollmentInfo"] = enrollment
    return {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT00000001"},
            "statusModule": {"overallStatus": status},
            "designModule": design,
        }
    }


# -- status grouping ---------------------------------------------------------

@pytest.mark.parametrize(
    "status,group",
    [
        ("NOT_YET_RECRUITING", "active"),
        ("RECRUITING", "active"),
        ("ENROLLING_BY_INVITATION", "active"),
        ("ACTIVE_NOT_RECRUITING", "active"),
        ("SUSPENDED", "active"),
        ("COMPLETED", "closed"),
        ("TERMINATED", "closed"),
        ("WITHDRAWN", "closed"),
        ("UNKNOWN", "unknown"),
    ],
)
def test_status_maps_to_expected_group(status, group):
    agg = Aggregator()
    agg.add(make_record(status=status, enrollment_type="ACTUAL"))
    assert agg.records_included == 1
    segment = group + "_actual"
    assert agg.cells[("all", "all")][segment][100] == 1


@pytest.mark.parametrize(
    "status",
    ["AVAILABLE", "NO_LONGER_AVAILABLE", "TEMPORARILY_NOT_AVAILABLE", "APPROVED_FOR_MARKETING"],
)
def test_expanded_access_statuses_are_excluded(status):
    agg = Aggregator()
    agg.add(make_record(status=status))
    assert agg.exclusions["expanded_access"] == 1
    assert agg.records_included == 0


def test_expanded_access_study_type_is_excluded():
    agg = Aggregator()
    agg.add(make_record(study_type="EXPANDED_ACCESS"))
    assert agg.exclusions["expanded_access"] == 1


def test_withheld_status_is_excluded_with_its_own_reason():
    # WITHHELD exists on the live API but was not in the original spec.
    agg = Aggregator()
    agg.add(make_record(status="WITHHELD"))
    assert agg.exclusions["status_withheld"] == 1
    assert agg.records_included == 0


def test_unknown_vocabulary_status_is_counted_not_crashed():
    agg = Aggregator()
    agg.add(make_record(status="SOME_FUTURE_STATUS"))
    assert agg.exclusions["unclassified_status"] == 1


# -- phase mapping -----------------------------------------------------------

@pytest.mark.parametrize(
    "phases,expected",
    [
        (["NA"], "NA"),
        (["EARLY_PHASE1"], "EARLY_PHASE1"),
        (["PHASE1"], "PHASE1"),
        (["PHASE1", "PHASE2"], "PHASE1_PHASE2"),
        (["PHASE2"], "PHASE2"),
        (["PHASE2", "PHASE3"], "PHASE2_PHASE3"),
        (["PHASE3"], "PHASE3"),
        (["PHASE4"], "PHASE4"),
        (None, "NOT_SPECIFIED"),
        ([], "NOT_SPECIFIED"),
    ],
)
def test_phase_signatures_map_to_groups(phases, expected):
    agg = Aggregator()
    assert agg._phase_group(phases) == expected


def test_phase_order_within_array_does_not_matter():
    # The API is not guaranteed to order the array, so the map keys off a sorted tuple.
    agg = Aggregator()
    assert agg._phase_group(["PHASE2", "PHASE1"]) == "PHASE1_PHASE2"


def test_unmapped_phase_combination_is_recorded():
    agg = Aggregator()
    assert agg._phase_group(["PHASE1", "PHASE3"]) == "NOT_SPECIFIED"
    assert agg.unmapped_phases["PHASE1+PHASE3"] == 1


# -- enrollment exclusions ---------------------------------------------------

def test_missing_enrollment_count_is_excluded():
    agg = Aggregator()
    agg.add(make_record(count=None, enrollment_type=None))
    assert agg.exclusions["missing_enrollment_count"] == 1


def test_missing_enrollment_type_is_excluded_but_tracked_by_status():
    # Real case: NCT00081835 reports count 400 with no type.
    agg = Aggregator()
    agg.add(make_record(status="COMPLETED", count=400, enrollment_type=None))
    assert agg.exclusions["missing_enrollment_type"] == 1
    assert agg.missing_type[("all", "all")]["closed"] == 1
    assert agg.records_included == 0


def test_negative_enrollment_is_flagged_separately():
    agg = Aggregator()
    agg.add(make_record(count=-5))
    assert agg.exclusions["invalid_enrollment_count"] == 1


def test_zero_enrollment_is_included_but_kept_off_the_log_axis():
    agg = Aggregator()
    agg.add(make_record(status="WITHDRAWN", count=0, enrollment_type="ACTUAL"))
    assert agg.records_included == 1
    assert agg.zero_total == 1
    assert agg.zeros[("all", "all")]["closed_actual"] == 1
    assert agg.cells[("all", "all")]["closed_actual"] == {}


def test_exclusion_ladder_order_expanded_access_beats_missing_count():
    # An expanded-access record with no enrollment is counted once, as EA.
    agg = Aggregator()
    agg.add(make_record(study_type="EXPANDED_ACCESS", count=None, enrollment_type=None))
    assert agg.exclusions["expanded_access"] == 1
    assert agg.exclusions["missing_enrollment_count"] == 0


# -- rollups -----------------------------------------------------------------

def test_record_lands_in_all_four_rollup_cells():
    agg = Aggregator()
    agg.add(make_record(study_type="INTERVENTIONAL", phases=["PHASE3"], count=250))
    expected = {
        ("all", "all"),
        ("all", "PHASE3"),
        ("INTERVENTIONAL", "all"),
        ("INTERVENTIONAL", "PHASE3"),
    }
    assert set(agg.cells) == expected
    for key in expected:
        assert agg.cells[key]["closed_actual"][250] == 1


def test_observational_record_does_not_leak_into_interventional():
    agg = Aggregator()
    agg.add(make_record(study_type="OBSERVATIONAL", phases=None, count=80))
    assert ("INTERVENTIONAL", "all") not in agg.cells
    assert agg.cells[("OBSERVATIONAL", "NOT_SPECIFIED")]["closed_actual"][80] == 1


# -- output document ---------------------------------------------------------

def test_build_output_shape_and_invariants():
    agg = Aggregator()
    for count in (5, 30, 300, 3_000, 30_000):
        agg.add(make_record(count=count))
    agg.add(make_record(status="RECRUITING", count=50, enrollment_type="ESTIMATED"))

    grid = stats.make_ecdf_grid()
    output = build_output(agg, total_count=123, grid=grid)

    assert output["schema_version"] == 1
    assert output["metadata"]["records_included"] == 6
    assert output["dimensions"]["segments"] == SEGMENTS

    cell = output["cells"]["all|all"]["closed_actual"]
    assert cell["n"] == 5
    assert sum(cell["bins"]) == cell["n"]
    assert len(cell["ecdf"]) == len(grid)
    assert cell["zero"] == 0
    # Empty segments are omitted so the file stays small.
    assert "unknown_actual" not in output["cells"]["all|all"]


def test_build_output_keeps_zero_only_segments():
    agg = Aggregator()
    agg.add(make_record(status="WITHDRAWN", count=0, enrollment_type="ACTUAL"))
    output = build_output(agg, total_count=1, grid=stats.make_ecdf_grid())
    cell = output["cells"]["all|all"]["closed_actual"]
    assert cell["n"] == 0
    assert cell["zero"] == 1
    assert cell["pct"] == {}
