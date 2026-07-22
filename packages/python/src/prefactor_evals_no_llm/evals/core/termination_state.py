"""core.termination_state

Spec: spec/evals/core/termination_state.md
"""

from __future__ import annotations

from ...helpers import read_path_one, span_ids
from ...registry import register
from ...result import failed, passed, skipped
from ...config import cfg_list

EVAL_ID = "core.termination_state"

# States that mean the run had not finished when it was fetched. Their elapsed
# time is not computable without a clock read, which is forbidden.
IN_FLIGHT_STATES = ("active", "pending")


@register(EVAL_ID, pack="core")
def run(instance, config, context):
    allow_states = (cfg_list(config, "allow_states") or ('complete',))
    max_duration_ms = config.get("max_duration_ms")
    duration_ms = instance.duration_ms
    state = instance.state

    if (state not in allow_states and state in IN_FLIGHT_STATES
            and duration_ms is None):
        return skipped(
            EVAL_ID, instance.id,
            'Instance is still in state "%s" with no ended_at, so its elapsed '
            "time is not computable without reading a clock." % state,
            remedy=("Re-fetch this instance once it has finished, or widen "
                    "allow_states in the pack file if this state is a clean "
                    "finish for this agent."),
            values={"state": state, "allow_states": allow_states},
        )

    failed_spans = [s for s in instance.spans if s.state == "failed"]
    values = {
        "state": state,
        "allow_states": allow_states,
        "duration_ms": duration_ms,
        "max_duration_ms": max_duration_ms,
        "termination_reason": read_path_one(instance.metadata, "termination_reason"),
        "failed_span_count": len(failed_spans),
    }

    over_ceiling = (max_duration_ms is not None and duration_ms is not None
                    and duration_ms > max_duration_ms)

    # A terminal state that is not allowed is reported as a state failure even
    # when the run also blew its ceiling: the state is the stated reason.
    if state not in allow_states and state not in IN_FLIGHT_STATES:
        return failed(
            EVAL_ID, instance.id,
            'Instance ended in state "%s", only %s accepted.'
            % (state, ", ".join('"%s"' % s for s in allow_states)),
            span_ids=span_ids(failed_spans),
            values=values,
        )

    if over_ceiling:
        return failed(
            EVAL_ID, instance.id,
            "Instance completed in %d ms, ceiling is %d ms."
            % (duration_ms, max_duration_ms),
            values=values,
        )

    if state not in allow_states:
        return failed(
            EVAL_ID, instance.id,
            'Instance is in state "%s" with both endpoints set, which is a stuck '
            "record, only %s accepted."
            % (state, ", ".join('"%s"' % s for s in allow_states)),
            span_ids=span_ids(failed_spans),
            values=values,
        )

    return passed(
        EVAL_ID, instance.id,
        'Instance ended in state "%s".' % state,
        values=values,
    )
