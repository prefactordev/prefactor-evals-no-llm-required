"""core.conversation_length

How many back and forth turns a run took to reach completion, against the
agent's own median. A conversational agent that needs far more turns than usual
to finish is going in circles or failing to understand. Self calibrating.

A turn is counted from the messages back to the person. Which spans those are
varies by instrumentation, so the check looks for spans typed `output`, then
falls back to the `handoff` and `output` family, and skips honestly when it
cannot tell what a turn is rather than guessing.

Spec: spec/evals/core/conversation_length.md
"""

from __future__ import annotations

from ...baseline import DEFAULT_TOLERANCE, build_baseline
from ...config import cfg_float, cfg_int, cfg_str_set
from ...registry import register
from ...result import failed, passed, skipped

EVAL_ID = "core.conversation_length"

# A conversation long enough to be a concern in any domain, so a batch of short
# chats does not make a slightly longer one look pathological.
TURN_FLOOR = 10


def _turn_spans(instance, turn_names):
    if turn_names:
        return [s for s in instance.spans
                if s.name in turn_names or s.schema_name in turn_names]
    # No names configured: output spans are messages back to the person.
    return [s for s in instance.spans if s.type == "output"]


@register(EVAL_ID, pack="core")
def run(instance, config, context):
    tolerance = cfg_float(config, "tolerance", DEFAULT_TOLERANCE)
    floor = cfg_int(config, "floor", TURN_FLOOR)
    turn_names = cfg_str_set(config, "turn_span_names")

    def turns(inst):
        return len(_turn_spans(inst, turn_names))

    # If nothing in this run looks like a turn, the metric does not apply to this
    # agent. Skip rather than reporting zero turns as a clean pass.
    if turns(instance) == 0 and not turn_names:
        return skipped(
            EVAL_ID, instance.id,
            "No output spans in this run, so it does not look like a "
            "conversation and turns cannot be counted.",
            remedy=("If this agent is conversational, set turn_span_names to the "
                    "spans that carry a message back to the person. Otherwise "
                    "this check does not apply and can be left off."),
        )

    baseline = build_baseline(context.instances, turns,
                              tolerance=tolerance, floor=floor)
    if not baseline.ready:
        return skipped(
            EVAL_ID, instance.id,
            "Only %d completed runs in this batch, too few to calibrate turn "
            "length against the agent's own normal." % baseline.sample_size,
            remedy="Evaluate more runs at once so the median has something to stand on.",
            values={"sample_size": baseline.sample_size},
        )

    count = turns(instance)
    values = {
        "turns": count,
        "median": baseline.median,
        "ceiling": baseline.ceiling,
        "tolerance": tolerance,
        "sample_size": baseline.sample_size,
    }

    if not baseline.exceeds(count):
        return passed(
            EVAL_ID, instance.id,
            "Took %d turns, the agent's median is %g." % (count, baseline.median),
            values=values,
        )

    return failed(
        EVAL_ID, instance.id,
        "Took %d turns to finish, more than %.1f times the agent's median of %g."
        % (count, tolerance, baseline.median),
        values=values,
    )
