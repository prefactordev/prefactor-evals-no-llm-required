"""core.output_schema

Spec: spec/evals/core/output_schema.md
"""

from __future__ import annotations

from ...registry import register
from ...result import failed, passed, skipped
from ...schema import sort_spans
from ...helpers import schema_problem
from .tool_arg_schema import (SCHEMA_EXTRA_REMEDY, pointer_of, short_message)

EVAL_ID = "core.output_schema"

# A wholly wrong output can otherwise produce hundreds of entries.
MAX_VIOLATIONS = 5


@register(EVAL_ID, pack="core")
def run(instance, config, context):
    schema = config.get("schema")
    if not schema:
        return skipped(
            EVAL_ID, instance.id,
            "No output schema configured, so nothing can be validated.",
            remedy=("Set schema for this eval in the pack file. There is no "
                    "generic shape a correct agent output takes."),
        )

    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return skipped(
            EVAL_ID, instance.id,
            "The jsonschema package is not installed, so the output cannot be "
            "validated.",
            remedy=SCHEMA_EXTRA_REMEDY,
        )

    problem = schema_problem(schema)
    if problem is not None:
        return skipped(
            EVAL_ID, instance.id,
            "The configured output schema %s." % problem,
            remedy=("Fix the schema entry for this eval in the pack file. It is "
                    "not validated under the wrong draft and remote references "
                    "are never fetched."),
        )

    # The output is derived from the root span, so an instance whose spans were
    # sampled, truncated, or paged can lose its root. Never reconstructed from
    # the last span or any other heuristic.
    roots = [s for s in instance.spans if s.parent_id is None]
    if not roots:
        return skipped(
            EVAL_ID, instance.id,
            "No span has a null parent_id, so there is no root span and no "
            "instance output to validate.",
            remedy=("Nothing to change in config. Fetch the instance without "
                    "sampling or a page limit so the root span is present."),
            values={"root_span_count": 0},
        )
    if len(roots) > 1:
        return skipped(
            EVAL_ID, instance.id,
            "This instance has %d root spans, so the instance output is "
            "ambiguous and is not guessed." % len(roots),
            remedy=("Nothing to change in config. An instance assembled from "
                    "parallel sub-agents legitimately has several roots and has "
                    "no single output."),
            values={"root_span_count": len(roots)},
        )

    root = sort_spans(roots)[0]
    output = instance.output if instance.output is not None else root.output

    if output is None:
        if config.get("null_output", "skip") == "fail":
            return failed(
                EVAL_ID, instance.id,
                "Instance output is null, and null_output is set to fail.",
                span_ids=[root.id],
                values={
                    "root_span_id": root.id,
                    "root_span_name": root.name,
                    "violations": [{"pointer": "", "keyword": "null_output",
                                    "message": "output is null"}],
                    "violation_count": 1,
                },
            )
        return skipped(
            EVAL_ID, instance.id,
            "The root span output is null, so there is no output to validate.",
            remedy=("Set null_output to \"fail\" for this eval if this agent is "
                    "required to produce structured output on every run."),
            values={"root_span_id": root.id, "root_span_name": root.name},
        )

    errors = list(Draft202012Validator(schema).iter_errors(output))
    errors.sort(key=lambda e: (pointer_of(e.absolute_path), str(e.validator)))

    values = {
        "root_span_id": root.id,
        "root_span_name": root.name,
        # Locations and keywords only, never the offending values, which can be
        # sensitive.
        "violations": [
            {
                "pointer": pointer_of(e.absolute_path),
                "keyword": str(e.validator),
                "message": short_message(e),
            }
            for e in errors[:MAX_VIOLATIONS]
        ],
        "violation_count": len(errors),
    }

    if not errors:
        return passed(
            EVAL_ID, instance.id,
            "Instance output validated against the configured schema.",
            span_ids=[root.id],
            values=values,
        )

    return failed(
        EVAL_ID, instance.id,
        "Instance output failed schema validation: %s."
        % values["violations"][0]["message"],
        span_ids=[root.id],
        values=values,
    )
