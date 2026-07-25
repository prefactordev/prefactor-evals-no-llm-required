"""core.errors

Did any step in the run fail. The most universal health signal there is: a
failed step means something the agent tried did not work, and it needs no
configuration on any agent, because "failed" is a state the platform records,
not a judgement the user has to define.

Spec: spec/evals/core/errors.md
"""

from __future__ import annotations

from ...config import cfg_int
from ...helpers import span_ids
from ...registry import register
from ...result import failed, passed

EVAL_ID = "core.errors"


@register(EVAL_ID, pack="core")
def run(instance, config, context):
    # Zero config by default: any failed step is a concern. A tolerance is
    # allowed for agents where the odd retried failure is expected, but the
    # default is strict, because a silent tolerance would hide real breakage.
    max_failures = cfg_int(config, "max_failures", 0)

    failed_spans = [s for s in instance.spans if s.state == "failed"]
    values = {
        "failed_spans": len(failed_spans),
        "total_spans": len(instance.spans),
        "max_failures": max_failures,
    }

    if len(failed_spans) <= max_failures:
        return passed(
            EVAL_ID, instance.id,
            "No steps failed." if not failed_spans
            else "%d steps failed, within the tolerance of %d."
            % (len(failed_spans), max_failures),
            values=values,
        )

    first = failed_spans[0]
    error = first.error or {}
    detail = error.get("message") or error.get("type") or "no error detail recorded"
    return failed(
        EVAL_ID, instance.id,
        '%d steps failed. First: "%s" (%s).'
        % (len(failed_spans), first.name, detail),
        span_ids=span_ids(failed_spans),
        values=values,
    )
