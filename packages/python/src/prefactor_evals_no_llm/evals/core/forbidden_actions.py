"""core.forbidden_actions

Spec: spec/evals/core/forbidden_actions.md
"""

from __future__ import annotations

import re

from ...helpers import compile_patterns, is_configured, span_ids
from ...registry import register
from ...result import failed, passed, skipped
from ...schema import sort_spans
from ...config import cfg_list

EVAL_ID = "core.forbidden_actions"

# Constructs that mean different things in Python's re and JavaScript's RegExp,
# or exist in only one of them. Rejected rather than silently reinterpreted.
UNPORTABLE = (
    (re.compile(r"\(\?<[=!]"), "lookbehind"),
    (re.compile(r"\(\?P?<[A-Za-z_]"), "named group"),
    (re.compile(r"\(\?P=") , "named backreference"),
    (re.compile(r"\(\?[aiLmsux]+[):]"), "inline flag group"),
    (re.compile(r"\(\?>"), "atomic group"),
    (re.compile(r"\(\?\("), "conditional"),
    (re.compile(r"\(\?R\)"), "recursion"),
    (re.compile(r"\[\[:"), "POSIX character class"),
    (re.compile(r"(?<!\\)\\[1-9]"), "backreference"),
    (re.compile(r"(?<!\\)[*+?}]\+"), "possessive quantifier"),
)


def _ascii_lower(text: str) -> str:
    """ASCII case folding only.

    Python's str.lower and JavaScript's toLowerCase disagree on some non-ASCII
    input, and identical results across languages are non-negotiable.
    """
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


@register(EVAL_ID, pack="core")
def run(instance, config, context):
    forbidden = cfg_list(config, "forbidden")
    forbidden_patterns = cfg_list(config, "forbidden_patterns")
    case_sensitive = bool(config.get("case_sensitive", True))
    match_schema_name = bool(config.get("match_schema_name", False))
    types = cfg_list(config, "types")

    if not is_configured(forbidden, forbidden_patterns):
        return skipped(
            EVAL_ID, instance.id,
            "No forbidden actions configured, so there is nothing to forbid.",
            remedy=("Set forbidden or forbidden_patterns for this eval in the pack "
                    "file. A default list would be a guess about which tool names "
                    "are dangerous in someone else's agent."),
        )

    for pattern in forbidden_patterns:
        for probe, label in UNPORTABLE:
            if probe.search(pattern):
                return skipped(
                    EVAL_ID, instance.id,
                    'Pattern "%s" uses %s, which is outside the portable regex '
                    "subset this eval accepts." % (pattern, label),
                    remedy=("Rewrite this forbidden_patterns entry without %s, or "
                            "move the rule to the exact forbidden list." % label),
                )
    try:
        compiled = compile_patterns(forbidden_patterns, case_sensitive)
    except re.error as error:
        return skipped(
            EVAL_ID, instance.id,
            "A forbidden_patterns entry failed to compile: %s." % error,
            remedy=("Fix the invalid regex in forbidden_patterns. A rule that "
                    "quietly stopped enforcing is worse than one that refuses to "
                    "run."),
        )

    candidates = ([s for s in sort_spans(instance.spans) if s.type in set(types)]
                  if types else sort_spans(instance.spans))

    exact = {(v if case_sensitive else _ascii_lower(v)): v for v in forbidden}

    offenders = []
    offending_spans = []
    for span in candidates:
        targets = [span.name]
        if match_schema_name:
            targets.append(span.schema_name)
        hit = None
        # forbidden before forbidden_patterns, first match only, so a failure
        # identifies exactly one rule to argue with.
        for target in targets:
            probe = target if case_sensitive else _ascii_lower(target)
            if probe in exact:
                hit = ("exact", exact[probe])
                break
        if hit is None:
            for target in targets:
                for pattern, source in zip(compiled, forbidden_patterns):
                    if pattern.search(target):
                        hit = ("pattern", source)
                        break
                if hit is not None:
                    break
        if hit is None:
            continue
        offending_spans.append(span)
        offenders.append({
            "span_id": span.id,
            "name": span.name,
            "type": span.type,
            "rule": hit[0],
            "matched": hit[1],
        })

    values = {
        "offenders": offenders,
        "forbidden": forbidden,
        "forbidden_patterns": forbidden_patterns,
        "case_sensitive": case_sensitive,
    }

    if not offenders:
        return passed(
            EVAL_ID, instance.id,
            "No forbidden span appeared in this instance.",
            values=values,
        )

    first = offenders[0]
    return failed(
        EVAL_ID, instance.id,
        '%d forbidden span%s present, first: "%s" matched forbidden %s "%s".'
        % (len(offenders), "" if len(offenders) == 1 else "s", first["name"],
           "entry" if first["rule"] == "exact" else "pattern", first["matched"]),
        span_ids=span_ids(offending_spans),
        values=values,
    )
