"""Tests for binning, percentiles and ECDF."""

from collections import Counter

import pytest

from common import stats


# -- binning -----------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected_index",
    [
        (1, 0),
        (10, 0),      # upper edge of bin 0 is inclusive
        (11, 1),      # lower edge of bin 1
        (25, 1),
        (26, 2),
        (50, 2),
        (51, 3),
        (100, 3),
        (101, 4),
        (250, 4),
        (251, 5),
        (500, 5),
        (501, 6),
        (1_000, 6),
        (1_001, 7),
        (5_000, 7),
        (5_001, 8),
        (10_000, 8),
        (10_001, 9),  # open-ended final bin
        (5_000_000, 9),
    ],
)
def test_bin_index_edges(value, expected_index):
    assert stats.bin_index(value) == expected_index


@pytest.mark.parametrize("value", [0, -1, -500])
def test_bin_index_rejects_non_positive(value):
    # Zero-enrollment studies are counted separately, never binned on a log axis.
    with pytest.raises(ValueError):
        stats.bin_index(value)


def test_bin_counts_sums_to_total():
    counter = Counter({5: 3, 40: 2, 12_000: 1})
    counts = stats.bin_counts(counter)
    assert sum(counts) == 6
    assert counts[0] == 3    # 5 -> bin "1-10"
    assert counts[2] == 2    # 40 -> bin "26-50"
    assert counts[9] == 1    # 12000 -> bin ">10,000"


def test_bins_and_labels_line_up():
    assert len(stats.BINS) == len(stats.BIN_LABELS) == 10


# -- percentiles -------------------------------------------------------------

def test_quantiles_match_linear_interpolation():
    # [10, 20, 30, 40]: numpy's default 'linear' method gives these values.
    dist = stats.Distribution(Counter({10: 1, 20: 1, 30: 1, 40: 1}))
    assert dist.quantile(0.0) == 10.0
    assert dist.quantile(0.25) == 17.5
    assert dist.quantile(0.5) == 25.0
    assert dist.quantile(0.75) == 32.5
    assert dist.quantile(1.0) == 40.0


def test_quantiles_respect_frequencies():
    # Counter form must behave exactly like the expanded list [1,1,1,5,9].
    dist = stats.Distribution(Counter({1: 3, 5: 1, 9: 1}))
    assert dist.n == 5
    assert dist.quantile(0.5) == 1.0
    assert dist.quantile(1.0) == 9.0


def test_value_at_rank_walks_the_expanded_array():
    dist = stats.Distribution(Counter({7: 2, 8: 1, 9: 3}))
    expanded = [7, 7, 8, 9, 9, 9]
    assert [dist.value_at_rank(i) for i in range(len(expanded))] == expanded


def test_summary_reports_mean_and_max():
    dist = stats.Distribution(Counter({10: 1, 20: 1, 60: 1}))
    summary = dist.summary()
    assert summary["p50"] == 20.0
    assert summary["mean"] == 30.0
    assert summary["max"] == 60.0


def test_empty_distribution_is_safe():
    dist = stats.Distribution(Counter())
    assert dist.n == 0
    assert dist.summary() == {}
    assert dist.ecdf([1, 10, 100]) == [0.0, 0.0, 0.0]
    with pytest.raises(ValueError):
        dist.quantile(0.5)


def test_single_value_distribution():
    dist = stats.Distribution(Counter({42: 1}))
    assert dist.quantile(0.0) == 42.0
    assert dist.quantile(0.5) == 42.0
    assert dist.quantile(1.0) == 42.0


# -- ECDF --------------------------------------------------------------------

def test_ecdf_grid_is_sorted_unique_and_bounded():
    grid = stats.make_ecdf_grid(1, 100_000, 60)
    assert grid[0] == 1
    assert grid[-1] == 100_000
    assert grid == sorted(set(grid))
    assert len(grid) <= 60


def test_ecdf_is_monotonic_and_reaches_one():
    dist = stats.Distribution(Counter({5: 1, 50: 1, 500: 1, 5_000: 1}))
    grid = stats.make_ecdf_grid()
    curve = dist.ecdf(grid)
    assert all(a <= b for a, b in zip(curve, curve[1:]))
    assert curve[-1] == 1.0


def test_ecdf_reports_proportion_at_or_below():
    dist = stats.Distribution(Counter({10: 1, 20: 1, 30: 1, 40: 1}))
    # P(X <= 20) == 0.5, and the bound is inclusive.
    assert dist.ecdf([20])[0] == 0.5
    assert dist.ecdf([19])[0] == 0.25
    assert dist.ecdf([9])[0] == 0.0


def test_summarize_bundles_everything():
    grid = stats.make_ecdf_grid()
    out = stats.summarize(Counter({10: 2, 1_000: 1}), grid)
    assert out["n"] == 3
    assert sum(out["bins"]) == 3
    assert len(out["ecdf"]) == len(grid)
    assert out["pct"]["max"] == 1_000.0


def test_grid_ceiling_rounds_up_to_a_power_of_ten():
    # A 500,000-participant study must fit inside the grid, or its ECDF never
    # reaches 1.0 -- this was a real bug caught by the output validator.
    assert stats.grid_ceiling(500_000) == 1_000_000
    assert stats.grid_ceiling(180_000) == 1_000_000
    assert stats.grid_ceiling(100_000) == 100_000
    assert stats.grid_ceiling(1_000_000) == 1_000_000


def test_grid_ceiling_has_a_floor_for_small_datasets():
    assert stats.grid_ceiling(0) == 100_000
    assert stats.grid_ceiling(42) == 100_000


def test_ecdf_reaches_one_when_the_grid_is_sized_from_the_data():
    counter = Counter({5: 1, 500_000: 1})
    grid = stats.make_ecdf_grid(1, stats.grid_ceiling(max(counter)))
    curve = stats.Distribution(counter).ecdf(grid)
    assert curve[-1] == 1.0
