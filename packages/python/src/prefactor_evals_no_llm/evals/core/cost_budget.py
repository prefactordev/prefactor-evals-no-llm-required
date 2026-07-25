"""core.cost_budget

Spec: spec/evals/core/cost_budget.md
"""

from __future__ import annotations

from ...helpers import read_path_one
from ...registry import register
from ...result import failed, passed, skipped

EVAL_ID = "core.cost_budget"


@register(EVAL_ID, pack="core")
def run(instance, config, context):
    max_cost = config.get("max_cost")
    if max_cost is None:
        return skipped(
            EVAL_ID, instance.id,
            "No cost budget configured, so there is no ceiling to judge against.",
            remedy=("Set max_cost for this eval in the pack file. Cost is in "
                    "whatever currency the account reports and the tolerable "
                    "spend for one run is a business decision."),
        )
    max_cost = float(max_cost)

    # Explicit null check, never a falsiness check: a cost of exactly 0 is a
    # real value, and null means not requested, never free.
    if instance.cost is None:
        return skipped(
            EVAL_ID, instance.id,
            "Instance cost was not fetched, so cost is null and cannot be judged.",
            remedy=("Fetch instances with cost data so EvalInstance.cost is "
                    "populated. Treating a null cost as zero would pass every "
                    "instance the seam fetched cheaply."),
            values={"max_cost": max_cost},
        )

    cost = float(instance.cost)
    values = {
        "cost": instance.cost,
        "max_cost": max_cost,
        "overage": cost - max_cost if cost > max_cost else 0,
        "cost_breakdown": read_path_one(instance.metadata, "cost_breakdown"),
        "span_count": len(instance.spans),
    }

    if cost <= max_cost:
        return passed(
            EVAL_ID, instance.id,
            "Instance cost %.4f is within budget of %.4f." % (cost, max_cost),
            values=values,
        )

    # No span_ids ever: cost is not attributable to any span, and naming spans
    # here would imply an attribution the data does not support.
    return failed(
        EVAL_ID, instance.id,
        "Instance cost %.4f exceeds budget of %.4f." % (cost, max_cost),
        values=values,
    )
