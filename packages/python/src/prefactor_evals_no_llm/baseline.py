"""Self calibrating baselines.

A benchmark check does not judge a run against a number someone typed, or against
an industry figure that is always arguable for a given agent. It judges the run
against the agent's own normal, computed from the other runs in the same batch.

"This run took four times the median" needs no configuration, calibrates to
whatever the agent actually does, and catches the agent getting worse rather
than merely differing from an external ideal.

Everything here is deterministic: the median of a fixed set of runs is the same
every time, which keeps the whole library reproducible.
"""

from __future__ import annotations

from typing import Callable, Optional

# A baseline drawn from too few runs is noise. Below this, a benchmark check
# skips rather than flagging a run for differing from a median of two.
MIN_RUNS_FOR_BASELINE = 5

# How many times the median a run may reach before it is flagged. Generous on
# purpose: the point is to catch a run that is thrashing, not one that is merely
# above average.
DEFAULT_TOLERANCE = 3.0

# A run below this absolute figure is never flagged however far above the median
# it sits, so a batch of tiny runs does not make a slightly larger one look
# pathological. Three times a median of one is still only three.
DEFAULT_FLOOR = 12


def median(values: list) -> Optional[float]:
    """The middle value. None for an empty list."""
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        return None
    mid = count // 2
    if count % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def completed(instances: list) -> list:
    """Runs that reached completion. The baseline is built from these only.

    A run that failed or was cancelled took as many steps as it did because it
    broke, not because that is how much work the task needs. Mixing those into
    the baseline would inflate what counts as normal.
    """
    return [i for i in instances if i.state == "complete"]


class Baseline:
    """The agent's own normal for one measurement, and whether a run exceeds it."""

    def __init__(self, values: list, tolerance: float = DEFAULT_TOLERANCE,
                 floor: int = DEFAULT_FLOOR):
        self.sample_size = len(values)
        self.median = median(values)
        self.tolerance = tolerance
        self.floor = floor

    @property
    def ready(self) -> bool:
        return self.sample_size >= MIN_RUNS_FOR_BASELINE and self.median is not None

    @property
    def ceiling(self) -> Optional[float]:
        """The value at which a run is flagged: the larger of a multiple of the
        median and the absolute floor."""
        if self.median is None:
            return None
        return max(self.median * self.tolerance, float(self.floor))

    def exceeds(self, value: float) -> bool:
        ceiling = self.ceiling
        return ceiling is not None and value > ceiling


def build_baseline(instances: list, measure: Callable, tolerance: float = DEFAULT_TOLERANCE,
                   floor: int = DEFAULT_FLOOR) -> Baseline:
    """A baseline for `measure` over the completed runs in the batch."""
    values = [measure(i) for i in completed(instances)]
    values = [v for v in values if v is not None]
    return Baseline(values, tolerance=tolerance, floor=floor)
