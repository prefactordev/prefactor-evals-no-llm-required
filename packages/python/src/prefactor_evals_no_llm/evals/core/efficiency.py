"""core.efficiency

How much work a run took to reach completion, measured against the agent's own
median. A run that took far more steps than this agent normally needs is either
thrashing or stuck. Self calibrating, so it needs no configuration and no
industry benchmark that would be arguable for any given agent.

Spec: spec/evals/core/efficiency.md
"""

from __future__ import annotations

from ...baseline import DEFAULT_FLOOR, DEFAULT_TOLERANCE, build_baseline
from ...config import cfg_float, cfg_int
from ...helpers import span_ids
from ...registry import register
from ...result import failed, passed, skipped
from ...schema import sort_spans

EVAL_ID = "core.efficiency"


def _span_count(instance) -> int:
    return len(instance.spans)


@register(EVAL_ID, pack="core")
def run(instance, config, context):
    tolerance = cfg_float(config, "tolerance", DEFAULT_TOLERANCE)
    floor = cfg_int(config, "floor", DEFAULT_FLOOR)

    baseline = build_baseline(context.instances, _span_count,
                              tolerance=tolerance, floor=floor)

    if not baseline.ready:
        return skipped(
            EVAL_ID, instance.id,
            "Only %d completed runs in this batch, too few to calibrate against "
            "the agent's own normal." % baseline.sample_size,
            remedy=("Evaluate more runs at once. This check compares each run "
                    "against the median of the others, so it needs a handful to "
                    "work from."),
            values={"sample_size": baseline.sample_size},
        )

    count = _span_count(instance)
    values = {
        "span_count": count,
        "median": baseline.median,
        "ceiling": baseline.ceiling,
        "tolerance": tolerance,
        "sample_size": baseline.sample_size,
    }

    if not baseline.exceeds(count):
        return passed(
            EVAL_ID, instance.id,
            "Used %d steps, the agent's median is %g." % (count, baseline.median),
            values=values,
        )

    return failed(
        EVAL_ID, instance.id,
        "Used %d steps to finish, more than %.1f times the agent's median of %g. "
        "The run took far more work than this agent normally needs."
        % (count, tolerance, baseline.median),
        span_ids=span_ids(sort_spans(instance.spans)),
        values=values,
    )
