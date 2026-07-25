"""core.escalation_rule

Spec: spec/evals/core/escalation_rule.md
"""

from __future__ import annotations

from ...helpers import is_configured, read_path, span_ids
from ...registry import register
from ...result import failed, passed, skipped
from ...schema import sort_spans
from ...config import cfg_dict, cfg_list

EVAL_ID = "core.escalation_rule"


def _equal(left, right) -> bool:
    """Exact, type sensitive equality.

    Python treats True as 1, so booleans are separated explicitly: the string
    "true" must not match the boolean true and the integer 1 must not match it
    either. Numeric comparison stays by value, so 1 and 1.0 are equal.
    """
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, bool):
        return left is right
    left_num = isinstance(left, (int, float))
    right_num = isinstance(right, (int, float))
    if left_num != right_num:
        return False
    return left == right


@register(EVAL_ID, pack="core")
def run(instance, config, context):
    handoff_types = (cfg_list(config, "handoff_types") or ('handoff',))

    # Prefactor has no wire level span type enum and `handoff` has no built in
    # mapping at all, so without configuration this eval would find zero
    # handoffs on a trace containing several. Check that before any trigger.
    mapped = any(v in handoff_types
                 for v in (getattr(context, "span_type_map", None) or {}).values())
    present = any(s.type in handoff_types for s in instance.spans)
    if not mapped and not present and not config.get("handoff_types"):
        return skipped(
            EVAL_ID, instance.id,
            "No handoff span type is configured, and nothing in this instance "
            "carries one, so an escalation could not be detected even if it "
            "happened.",
            remedy=("Map at least one schema_name to \"handoff\" in the pack "
                    "file's span_type_map, for example "
                    "{\"support:transfer_to_human\": \"handoff\"}."),
            values={"handoff_types": handoff_types},
        )

    trigger_names = cfg_list(config, "trigger_names")
    trigger_values = cfg_dict(config, "trigger_values")
    if not is_configured(trigger_names, trigger_values):
        return skipped(
            EVAL_ID, instance.id,
            "No escalation trigger configured, so nothing can fire.",
            remedy=("Set trigger_names or trigger_values for this eval in the "
                    "pack file. What counts as an escalation trigger is specific "
                    "to one agent's design."),
            values={"handoff_types": handoff_types},
        )

    ordered = sort_spans(instance.spans)

    triggers = []
    for index, span in enumerate(ordered):
        hit = None
        if span.name in trigger_names:
            hit = ("name", span.name)
        elif isinstance(span.output, dict):
            for path in sorted(trigger_values):
                found = read_path(span.output, path)
                if found and _equal(found[0], trigger_values[path]):
                    hit = ("value", path)
                    break
        if hit is not None:
            triggers.append((index, span, hit))

    handoff_indexes = [i for i, s in enumerate(ordered) if s.type in handoff_types]

    if not triggers:
        return passed(
            EVAL_ID, instance.id,
            "No escalation trigger fired, so the rule was never engaged. This is "
            "a vacuous pass, not evidence that an escalation was handled.",
            values={
                "trigger_count": 0,
                "span_count": len(ordered),
                "handoff_types": handoff_types,
            },
        )

    trigger_index, trigger_span, (rule, matched) = triggers[0]
    before = [ordered[i] for i in handoff_indexes if i < trigger_index]
    after = [ordered[i] for i in handoff_indexes if i > trigger_index]

    # Positions are reported one based for readability. Ordering comparisons
    # above use the zero based index into schema order.
    values = {
        "trigger_span_id": trigger_span.id,
        "trigger_rule": rule,
        "trigger_matched": matched,
        "trigger_index": trigger_index + 1,
        "span_count": len(ordered),
        "handoff_types": handoff_types,
        "handoff_spans_before_trigger": len(before),
        "handoff_spans_after_trigger": len(after),
        "trigger_count": len(triggers),
    }

    if after:
        return passed(
            EVAL_ID, instance.id,
            'Trigger "%s" fired at span %d of %d and a handoff span followed.'
            % (matched, trigger_index + 1, len(ordered)),
            span_ids=span_ids([trigger_span] + after),
            values=values,
        )

    return failed(
        EVAL_ID, instance.id,
        'Trigger "%s" fired at span %d of %d and no handoff span followed.'
        % (matched, trigger_index + 1, len(ordered)),
        span_ids=span_ids([trigger_span] + before),
        values=values,
    )
