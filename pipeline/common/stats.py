"""Distribution helpers for precomputed dashboard data.

Everything here works from a ``collections.Counter`` mapping value -> frequency
rather than a list of observations.  Enrollment numbers repeat heavily across
studies (20, 30, 50, 100 ...), so the counter stays a few thousand entries per
segment while an expanded list would be millions of ints.  Percentiles are still
exact: they are read off the cumulative counts by rank.

These functions are pure so the tests can target them directly and a second
dashboard can reuse them.
"""

from __future__ import annotations

import bisect
import math
from collections import Counter
from typing import Iterable, Sequence

# Fixed log-scale bins, as specified by the dashboard design.  The final bin is
# open ended.  Edges are inclusive on both sides.
BINS: list[tuple[int, int | None]] = [
    (1, 10),
    (11, 25),
    (26, 50),
    (51, 100),
    (101, 250),
    (251, 500),
    (501, 1_000),
    (1_001, 5_000),
    (5_001, 10_000),
    (10_001, None),
]

BIN_LABELS: list[str] = [
    "1\u201310",
    "11\u201325",
    "26\u201350",
    "51\u2013100",
    "101\u2013250",
    "251\u2013500",
    "501\u20131,000",
    "1,001\u20135,000",
    "5,001\u201310,000",
    ">10,000",
]

# Upper edges of every bin except the open ended one, for bisect lookups.
_BIN_UPPERS: list[int] = [hi for _, hi in BINS if hi is not None]

PERCENTILE_KEYS: list[tuple[str, float]] = [
    ("p5", 0.05),
    ("p10", 0.10),
    ("p25", 0.25),
    ("p50", 0.50),
    ("p75", 0.75),
    ("p90", 0.90),
    ("p95", 0.95),
]


def bin_index(value: int) -> int:
    """Return the index into :data:`BINS` that ``value`` falls in.

    Values at or below zero have no place on a log scale and are rejected; the
    caller counts them separately.
    """
    if value < 1:
        raise ValueError(f"bin_index expects a positive value, got {value!r}")
    idx = bisect.bisect_left(_BIN_UPPERS, value)
    return min(idx, len(BINS) - 1)


def bin_counts(counter: Counter[int]) -> list[int]:
    """Bin every value in ``counter`` into the fixed log-scale bins."""
    out = [0] * len(BINS)
    for value, freq in counter.items():
        out[bin_index(value)] += freq
    return out


def grid_ceiling(max_value: int, minimum: int = 100_000) -> int:
    """Round the largest observed value up to the next power of ten.

    The ECDF grid has to span the data or the curves never reach 1.0.  Real
    records go well past 100,000 participants (large observational cohorts), so
    the ceiling is derived from the data rather than hard-coded.
    """
    value = max(int(max_value), minimum)
    return 10 ** math.ceil(math.log10(value))


def make_ecdf_grid(lo: int = 1, hi: int = 100_000, points: int = 60) -> list[int]:
    """Log-spaced integer x-grid shared by every ECDF series in the output.

    A single shared grid means the x values are stored once in the metadata
    instead of per cell, and the curves are directly comparable.  Rounding can
    collapse neighbouring points at the low end, so duplicates are dropped.
    """
    if lo < 1 or hi <= lo or points < 2:
        raise ValueError("invalid ecdf grid parameters")
    log_lo, log_hi = math.log10(lo), math.log10(hi)
    grid: list[int] = []
    for i in range(points):
        raw = 10 ** (log_lo + (log_hi - log_lo) * i / (points - 1))
        value = int(round(raw))
        if not grid or value > grid[-1]:
            grid.append(value)
    return grid


class Distribution:
    """Sorted view over a value->frequency counter, for rank-based queries."""

    __slots__ = ("values", "cumulative", "n", "total")

    def __init__(self, counter: Counter[int]):
        self.values: list[int] = sorted(counter)
        self.cumulative: list[int] = []
        running = 0
        total = 0
        for value in self.values:
            freq = counter[value]
            running += freq
            total += value * freq
            self.cumulative.append(running)
        self.n: int = running
        self.total: int = total

    def value_at_rank(self, rank: int) -> int:
        """Value at 0-based ``rank`` in the conceptually expanded sorted array."""
        idx = bisect.bisect_right(self.cumulative, rank)
        return self.values[idx]

    def quantile(self, q: float) -> float:
        """Linear-interpolated quantile, matching numpy's default method."""
        if self.n == 0:
            raise ValueError("quantile of an empty distribution")
        h = (self.n - 1) * q
        lower = math.floor(h)
        frac = h - lower
        low_value = self.value_at_rank(lower)
        if frac == 0 or lower + 1 > self.n - 1:
            return float(low_value)
        high_value = self.value_at_rank(lower + 1)
        return low_value + frac * (high_value - low_value)

    def summary(self) -> dict[str, float]:
        """P5/P10/P25/median/P75/P90/P95 plus mean and max."""
        if self.n == 0:
            return {}
        out: dict[str, float] = {}
        for key, q in PERCENTILE_KEYS:
            out[key] = round(self.quantile(q), 1)
        out["mean"] = round(self.total / self.n, 1)
        out["max"] = float(self.values[-1])
        return out

    def ecdf(self, grid: Sequence[int]) -> list[float]:
        """P(X <= g) for each g in ``grid``, rounded to keep the file small."""
        if self.n == 0:
            return [0.0] * len(grid)
        out: list[float] = []
        for g in grid:
            idx = bisect.bisect_right(self.values, g)
            out.append(0.0 if idx == 0 else round(self.cumulative[idx - 1] / self.n, 4))
        return out


def summarize(counter: Counter[int], grid: Sequence[int]) -> dict:
    """Bundle bins, percentiles and ECDF for one segment's non-zero values."""
    dist = Distribution(counter)
    return {
        "n": dist.n,
        "bins": bin_counts(counter),
        "pct": dist.summary(),
        "ecdf": dist.ecdf(grid),
    }
