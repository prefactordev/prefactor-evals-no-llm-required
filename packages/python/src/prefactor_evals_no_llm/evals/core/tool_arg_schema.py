"""core.tool_arg_schema

Spec: spec/evals/core/tool_arg_schema.md
"""

from __future__ import annotations

from ...helpers import schema_problem, span_ids
from ...registry import register
from ...result import failed, passed, skipped
from ...schema import sort_spans
from ...config import cfg_str

EVAL_ID = "core.tool_arg_schema"

SCHEMA_EXTRA_REMEDY = (
    "Install the schema extra to enable this eval: "
    "pip install \"prefactor-evals-no-llm[schema]\"."
)

JSON_TYPE_NAMES = (
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
)


def json_type_of(value) -> str:
    if value is None:
        return "null"
    for python_type, name in JSON_TYPE_NAMES:
        if isinstance(value, python_type):
            return name
    return "unknown"




def pointer_of(path) -> str:
    parts = [str(p).replace("~", "~0").replace("/", "~1") for p in path]
    return "/" + "/".join(parts) if parts else ""


def short_message(error) -> str:
    """Normalized message text.

    Raw validator messages differ between the Python and TypeScript libraries
    and byte identical results are required, so only these short forms are
    emitted.
    """
    keyword = error.validator
    if keyword == "required":
        instance = error.instance if isinstance(error.instance, dict) else {}
        missing = [p for p in (error.validator_value or []) if p not in instance]
        name = missing[0] if missing else ""
        return 'missing required property "%s"' % name
    if keyword == "type":
        expected = error.validator_value
        if isinstance(expected, (list, tuple)):
            expected = " or ".join(str(t) for t in expected)
        return "expected %s, got %s" % (expected, json_type_of(error.instance))
    if keyword == "enum":
        return "value not in enum"
    if keyword == "pattern":
        return 'string does not match pattern "%s"' % error.validator_value
    if keyword in ("minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum",
                   "minLength", "maxLength", "minItems", "maxItems",
                   "minProperties", "maxProperties", "multipleOf"):
        return "violates %s %s" % (keyword, error.validator_value)
    if keyword == "additionalProperties":
        return "additional properties are not allowed"
    if keyword == "uniqueItems":
        return "array items are not unique"
    return "failed %s constraint" % keyword


def first_error(validator, value):
    errors = list(validator.iter_errors(value))
    if not errors:
        return None
    # Stable pick: validator iteration order is not guaranteed across versions.
    errors.sort(key=lambda e: (pointer_of(e.absolute_path), str(e.validator)))
    return errors[0]


@register(EVAL_ID, pack="core")
def run(instance, config, context):
    schemas = config.get("schemas") or {}
    if not schemas:
        return skipped(
            EVAL_ID, instance.id,
            "No tool argument schemas configured, so nothing can be validated.",
            remedy=("Set schemas for this eval in the pack file, mapping each "
                    "tool name to a JSON Schema for its input. There is no "
                    "generic correct tool argument shape."),
        )

    tool_calls = sort_spans(instance.spans_of_type("tool_call"))
    if not tool_calls:
        return skipped(
            EVAL_ID, instance.id,
            "No tool_call spans on this instance, nothing to check.",
            remedy=("If this agent does call tools, its span schema names are not "
                    "recognised. Map them with span_type_map in the pack file."),
        )

    covered = [s for s in tool_calls if s.name in schemas]
    uncovered = [s for s in tool_calls if s.name not in schemas]
    uncovered_tools = sorted({s.name for s in uncovered})

    if not covered:
        return skipped(
            EVAL_ID, instance.id,
            "No configured schema matches any tool in this instance. Tools "
            "present: %s." % ", ".join(sorted({s.name for s in tool_calls})),
            remedy=("Add an entry to schemas for one of the tools this agent "
                    "actually calls. Matching is exact and case sensitive on the "
                    "span name."),
            values={"uncovered_tools": uncovered_tools},
        )

    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return skipped(
            EVAL_ID, instance.id,
            "The jsonschema package is not installed, so schemas cannot be "
            "validated.",
            remedy=SCHEMA_EXTRA_REMEDY,
            values={"uncovered_tools": uncovered_tools},
        )

    validators = {}
    for tool in sorted({s.name for s in covered}):
        problem = schema_problem(schemas[tool])
        if problem is not None:
            return skipped(
                EVAL_ID, instance.id,
                'The schema for tool "%s" %s.' % (tool, problem),
                remedy=("Fix the schemas entry for \"%s\" in the pack file. It is "
                        "not validated under the wrong draft and remote "
                        "references are never fetched." % tool),
                values={"uncovered_tools": uncovered_tools},
            )
        validators[tool] = Draft202012Validator(schemas[tool])

    null_input = cfg_str(config, "null_input", 'fail')

    checked = []
    violations = []
    invalid_spans = []
    uncovered_span_count = len(uncovered)

    for span in covered:
        if span.input is None and null_input == "skip_span":
            uncovered_span_count += 1
            continue
        checked.append(span)
        if span.input is None:
            invalid_spans.append(span)
            violations.append({
                "span_id": span.id,
                "tool": span.name,
                "pointer": "",
                "keyword": "null_input",
                "message": "input is null",
            })
            continue
        error = first_error(validators[span.name], span.input)
        if error is None:
            continue
        invalid_spans.append(span)
        # One violation per span, so a single badly shaped input cannot produce
        # fifty entries. No argument values appear here.
        violations.append({
            "span_id": span.id,
            "tool": span.name,
            "pointer": pointer_of(error.absolute_path),
            "keyword": str(error.validator),
            "message": short_message(error),
        })

    values = {
        "checked_spans": len(checked),
        "invalid_spans": len(invalid_spans),
        "uncovered_spans": uncovered_span_count,
        "uncovered_tools": uncovered_tools,
        "violations": violations,
    }

    if not violations:
        return passed(
            EVAL_ID, instance.id,
            "All %d covered tool_call inputs validated, %d span(s) uncovered."
            % (len(checked), uncovered_span_count),
            values=values,
        )

    first = violations[0]
    return failed(
        EVAL_ID, instance.id,
        '%d of %d covered tool_call inputs failed schema validation, first: '
        '"%s" %s.' % (len(violations), len(checked), first["tool"],
                      first["message"]),
        span_ids=span_ids(invalid_spans),
        values=values,
    )
