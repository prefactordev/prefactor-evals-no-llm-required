"""core.redundant_tool_calls

Spec: spec/evals/core/redundant_tool_calls.md
"""

from __future__ import annotations

from ...helpers import canonical, digest, span_ids, strip_keys, tool_signature
from ...registry import register
from ...result import failed, passed, skipped
from ...schema import sort_spans
from ...config import cfg_int, cfg_list

EVAL_ID = "core.redundant_tool_calls"


@register(EVAL_ID, pack="core")
def run(instance, config, context):
    max_repeats = cfg_int(config, "max_repeats", 2)
    ignore_tools = cfg_list(config, "ignore_tools")
    ignore_arg_keys = config.get("ignore_arg_keys") or ()

    if not instance.spans_of_type("tool_call"):
        return skipped(
            EVAL_ID, instance.id,
            "No tool_call spans on this instance, nothing to check.",
            remedy=("If this agent does call tools, its span schema names are not "
                    "recognised. Map them with span_type_map in the pack file."),
        )

    runs = []
    current = None
    current_key = None
    for span in sort_spans(instance.spans):
        # Anything that is not an unignored tool_call ends the run in progress,
        # because something other than the repeated tool happened.
        if span.type != "tool_call" or span.name in ignore_tools:
            current = None
            current_key = None
            continue
        key = (tool_signature(span, ignore_arg_keys), canonical(span.output))
        if current is not None and key == current_key:
            current.append(span)
            continue
        current = [span]
        current_key = key
        runs.append(current)

    offenders = [r for r in runs if len(r) > max_repeats]

    if not offenders:
        worst = max((len(r) for r in runs), default=0)
        return passed(
            EVAL_ID, instance.id,
            "No tool called more than %d times consecutively without progress."
            % max_repeats,
            values={"max_repeats": max_repeats, "worst_run_length": worst},
        )

    # Deterministic ordering: longest run first, then by tool name for stable ties.
    offenders.sort(key=lambda r: (-len(r), r[0].name))
    worst_run = offenders[0]

    evidence_spans = [s for r in offenders for s in r]
    return failed(
        EVAL_ID, instance.id,
        'Tool "%s" called %d times consecutively with identical arguments and no '
        "change in output, limit is %d."
        % (worst_run[0].name, len(worst_run), max_repeats),
        span_ids=span_ids(evidence_spans),
        values={
            "max_repeats": max_repeats,
            "offenders": [
                {
                    "tool": r[0].name,
                    "run_length": len(r),
                    "limit": max_repeats,
                    # Hashes, not raw values: both arguments and outputs can be
                    # large and can carry sensitive data.
                    "arg_digest": digest(strip_keys(r[0].input, ignore_arg_keys)),
                    "output_digest": digest(r[0].output),
                }
                for r in offenders
            ],
        },
    )
