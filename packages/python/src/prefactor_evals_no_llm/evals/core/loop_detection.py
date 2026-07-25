"""core.loop_detection

Spec: spec/evals/core/loop_detection.md
"""

from __future__ import annotations

from collections import defaultdict

from ...helpers import digest, span_ids, strip_keys, tool_signature
from ...registry import register
from ...result import failed, passed, skipped
from ...config import cfg_int, cfg_list

EVAL_ID = "core.loop_detection"


@register(EVAL_ID, pack="core")
def run(instance, config, context):
    max_occurrences = cfg_int(config, "max_occurrences", 3)
    ignore_tools = cfg_list(config, "ignore_tools")
    ignore_arg_keys = config.get("ignore_arg_keys") or ()

    tool_calls = [s for s in instance.spans_of_type("tool_call")
                  if s.name not in ignore_tools]
    if not tool_calls:
        return skipped(
            EVAL_ID, instance.id,
            "No tool_call spans on this instance, nothing to check.",
            remedy=("If this agent does call tools, its span schema names are not "
                    "recognised. Map them with span_type_map in the pack file."),
        )

    groups = defaultdict(list)
    for span in tool_calls:
        groups[tool_signature(span, ignore_arg_keys)].append(span)

    offenders = [(sig, spans) for sig, spans in groups.items()
                 if len(spans) > max_occurrences]

    if not offenders:
        worst = max((len(v) for v in groups.values()), default=0)
        return passed(
            EVAL_ID, instance.id,
            "No tool signature repeated more than %d times." % max_occurrences,
            values={"max_occurrences": max_occurrences, "worst_count": worst},
        )

    # Deterministic ordering: worst first, then by tool name for stable ties.
    offenders.sort(key=lambda item: (-len(item[1]), item[1][0].name))
    worst_spans = offenders[0][1]

    evidence_spans = [s for _, spans in offenders for s in spans]
    return failed(
        EVAL_ID, instance.id,
        'Tool "%s" called %d times with identical arguments, limit is %d.'
        % (worst_spans[0].name, len(worst_spans), max_occurrences),
        span_ids=span_ids(evidence_spans),
        values={
            "max_occurrences": max_occurrences,
            "offenders": [
                {
                    "tool": spans[0].name,
                    "count": len(spans),
                    "limit": max_occurrences,
                    # A hash, not the raw arguments: arguments can be large and
                    # can contain sensitive values. They are one span lookup
                    # away in Prefactor.
                    "arg_digest": digest(strip_keys(spans[0].input, ignore_arg_keys)),
                }
                for _, spans in offenders
            ],
        },
    )
