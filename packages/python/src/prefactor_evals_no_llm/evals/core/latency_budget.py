"""core.latency_budget

Spec: spec/evals/core/latency_budget.md
"""

from __future__ import annotations

from ...helpers import is_configured, span_ids
from ...registry import register
from ...result import failed, passed, skipped
from ...schema import sort_spans
from ...config import cfg_list

EVAL_ID = "core.latency_budget"

# A pathological run must not produce a result object larger than the trace
# summary, so the reported list is capped and the true total carried alongside.
MAX_SLOWEST = 10


# A single agent step taking longer than this is a hang in almost any domain.
# Generous on purpose, so zero config catches only clear stalls and never a
# merely slow step. Tune it down for a latency sensitive agent like voice.
DEFAULT_MAX_SPAN_MS = 60000

# By default, measure only the agent's own work. A message or output span covers
# the time a person spent reading and replying, which can be minutes and is not
# the agent being slow. Measuring it would blame the agent for human think time.
DEFAULT_SPAN_TYPES = ("llm_call", "tool_call", "retrieval")


@register(EVAL_ID, pack="core")
def run(instance, config, context):
    # Runs zero config with a generous default. max_instance_ms stays opt in,
    # because a sensible whole run ceiling is genuinely agent specific.
    max_span_ms = config.get("max_span_ms", DEFAULT_MAX_SPAN_MS)
    max_instance_ms = config.get("max_instance_ms")
    span_types = cfg_list(config, "span_types") or list(DEFAULT_SPAN_TYPES)
    ignore_spans = cfg_list(config, "ignore_spans")

    measured = []
    unmeasured = []
    over = []
    if max_span_ms is not None:
        for span in sort_spans(instance.spans):
            if span_types and span.type not in span_types:
                continue
            if span.name in ignore_spans:
                continue
            # Null duration means an open span. Inferring one would need a clock
            # read, so it is reported and never counted as a pass or a failure.
            if span.duration_ms is None:
                unmeasured.append(span)
                continue
            measured.append(span)
            if span.duration_ms > max_span_ms:
                over.append(span)

    instance_measured = (max_instance_ms is not None
                         and instance.duration_ms is not None)
    instance_over = bool(instance_measured
                         and instance.duration_ms > max_instance_ms)

    span_half_ran = max_span_ms is not None and bool(measured)
    if not span_half_ran and not instance_measured:
        return skipped(
            EVAL_ID, instance.id,
            "No duration was measurable on this instance, so no latency verdict "
            "is possible.",
            remedy=("Nothing to change in config. Re-fetch once the run and its "
                    "spans have closed, or check core.termination_state, which "
                    "reports the termination that left them open."),
            values={
                "max_span_ms": max_span_ms,
                "max_instance_ms": max_instance_ms,
                "unmeasured_spans": len(unmeasured),
                "unmeasured_span_ids": span_ids(unmeasured),
            },
        )

    slowest = sorted(over, key=lambda s: (-s.duration_ms, s.id))[:MAX_SLOWEST]
    values = {
        "max_span_ms": max_span_ms,
        "max_instance_ms": max_instance_ms,
        "instance_duration_ms": instance.duration_ms,
        "instance_over": instance_over,
        "slowest_spans": [
            {
                "span_id": s.id,
                "name": s.name,
                "type": s.type,
                "duration_ms": s.duration_ms,
            }
            for s in slowest
        ],
        "over_span_count": len(over),
        "measured_spans": len(measured),
        "unmeasured_spans": len(unmeasured),
        "unmeasured_span_ids": span_ids(unmeasured),
    }

    if over:
        worst = slowest[0]
        return failed(
            EVAL_ID, instance.id,
            'Span "%s" took %d ms, ceiling is %d ms.'
            % (worst.name, worst.duration_ms, max_span_ms),
            span_ids=span_ids(over),
            values=values,
        )

    if instance_over:
        return failed(
            EVAL_ID, instance.id,
            "Instance took %d ms, ceiling is %d ms."
            % (instance.duration_ms, max_instance_ms),
            values=values,
        )

    return passed(
        EVAL_ID, instance.id,
        "Every measured duration was within its ceiling.",
        values=values,
    )
