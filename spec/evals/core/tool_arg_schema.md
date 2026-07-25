# core.tool_arg_schema

## ID

`core.tool_arg_schema`

## Name

Tool argument schema

## Requires

1. `EvalInstance.spans`
2. `EvalSpan.type`
3. `EvalSpan.name`
4. `EvalSpan.input`
5. `EvalSpan.id`

Skips when the instance has no spans of type `tool_call`, and skips when no
configured schema matches any tool present in the instance.

## Config

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `schemas` | record<string, object> | required | Map of tool `name` to a JSON Schema for that tool's `input`. |
| `null_input` | enum | `"fail"` | How to treat a `tool_call` span with null `input` when a schema exists for it. One of `fail`, `skip_span`. |

`schemas` is required and has no default. There is no such thing as a generic
correct tool argument shape, so an invented default would be an invented
contract. The eval skips when `schemas` is absent or empty, with `details`
naming `schemas`.

## Pass criteria

### Draft

Schemas are interpreted as **JSON Schema draft 2020-12**. The `$schema` keyword,
if present in a supplied schema, must declare 2020-12 or be absent. A schema
declaring any other draft is a config error and causes the eval to `skip` with
that tool named, rather than being validated under the wrong draft. Draft
2020-12 is chosen because it is the draft both language ecosystems implement
current-generation validators against, which is what keeps the Python and
TypeScript results identical on the same fixture.

Validation is structural only. `format` is treated as an annotation and is not
asserted, in both implementations, because `format` assertion behaviour is
optional in the draft and differs between validators. A schema relying on
`format` for enforcement will not get it. Use `pattern` instead.

Remote references are not resolved. A schema containing a `$ref` to anything
other than an internal `#/...` pointer is a config error and skips that tool,
because resolving it would mean a network call.

### Matching and coverage

For each span of type `tool_call`, look up `schemas[span.name]` by exact,
case-sensitive string equality. There is no pattern matching and no fallback
key.

Spans whose `name` has no entry in `schemas` are **not checked and not counted
as failures**. They are counted as uncovered and reported in
`evidence.values.uncovered_tools` on every result, pass or fail. Partial
coverage is the normal state: a user who wrote schemas for three of eleven tools
gets a verdict on those three and an explicit statement of the eight that went
unchecked. It is never reported as a clean bill of health for the instance.

If no `tool_call` span in the instance matches any key in `schemas`, the eval
returns `skip`, with `details` naming the tools present and stating that none
were covered. A pass earned by checking nothing would be indistinguishable from
a pass earned by checking everything, which is the failure mode this library
exists to avoid.

### Verdict

The eval passes when every covered span's `input` validates against its tool's
schema. It fails when any covered span's `input` does not validate.

A covered span with null `input` fails by default, on the grounds that a tool
with a declared argument schema was called with no arguments at all. Setting
`null_input` to `skip_span` moves those spans into the uncovered count instead,
for agents whose instrumentation drops payloads.

## Failure output

`status`: `fail`

`details`: one sentence naming the count and the first offender, for example:
`3 of 14 covered tool_call inputs failed schema validation, first: "create_ticket" missing required property "priority".`

`evidence.span_ids`: the IDs of every span whose `input` failed validation,
sorted by start time then ID.

`evidence.values`:

```
{
  "checked_spans": 14,
  "invalid_spans": 3,
  "uncovered_spans": 22,
  "uncovered_tools": ["send_email", "lookup_account"],
  "violations": [
    {
      "span_id": "01kxye3p2m7synth88k1v5t7n2ra94xc",
      "tool": "create_ticket",
      "pointer": "",
      "keyword": "required",
      "message": "missing required property \"priority\""
    }
  ]
}
```

`pointer` is a JSON Pointer to the offending location in the instance data,
empty string for the document root. Only the first validation error per span is
recorded, so that one badly shaped input does not produce fifty entries. The
error `message` text is normalized to the short forms listed in the conformance
fixtures, because raw validator messages differ between the Python and
TypeScript libraries and byte-identical results are required.

No argument values appear in the output. `pointer` and `keyword` locate the
problem without reproducing the data, which can be sensitive.

## Notes

This is the eval that catches an LLM inventing an argument, dropping a required
one, or passing a string where a number belongs. It is the highest signal check
in the core pack for agents whose failures are malformed calls rather than wrong
plans.

Tool identity comes from `EvalSpan.name`, which per `span.md` note 3 falls back
to `schema_name` when instrumentation omits the payload name. Instrumentation
that reuses one `schema_name` for every tool will present many tools as one, and
a single schema will then be applied to all of them and fail most. That is a
property of the source data and the library cannot detect it. If every tool in
an instance appears under one name, fix the instrumentation before writing
schemas.

Deliberately not checked:

1. **Tool outputs.** Only `input` is validated. Output shape is not in v1 at
   span level, and `core.output_schema` covers the instance's final output only.
2. **Whether the arguments were correct.** A validly shaped call with the wrong
   date in it passes. Schema conformance is not semantic correctness and the
   difference is the whole gap between this library and an LLM judge.
3. **Whether the right tool was called.** Selection is not checked here or
   anywhere in v1.
4. **Tools with no schema.** Silently unchecked would be the dangerous
   behaviour, so they are loudly unchecked instead, in
   `evidence.values.uncovered_tools` on every result.
5. **`format` assertions.** Annotations only. See above.
6. **Remote `$ref`.** Not resolved, tool skipped. Network access is forbidden.
7. **Spans of other types.** `retrieval`, `llm_call`, and `other` spans are not
   validated even when a schema key matches their name.
