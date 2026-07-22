# core.output_schema

## ID

`core.output_schema`

## Name

Output schema

## Requires

1. `EvalInstance.output`
2. `EvalInstance.spans` (to establish the root span, and for evidence)
3. `EvalSpan.parent_id`
4. `EvalSpan.started_at`
5. `EvalSpan.id`

Skips when no schema is configured, and skips when `EvalInstance.output` is null
for any of the reasons below.

## Config

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `schema` | object | required | JSON Schema for the instance's final output. |
| `null_output` | enum | `"skip"` | How to treat a resolvable root span whose `output` is null. One of `skip`, `fail`. |

`schema` is required and has no default. There is no generic shape a correct
agent output takes, and an invented one would assert a contract the user never
wrote. The eval skips when `schema` is absent, naming it.

## Pass criteria

### Draft

Interpreted as **JSON Schema draft 2020-12**, on the same terms as
`core.tool_arg_schema`: a `$schema` keyword must declare 2020-12 or be absent, a
schema declaring another draft causes a `skip` rather than validation under the
wrong draft, `format` is an annotation and is never asserted, and a `$ref` to
anything other than an internal `#/...` pointer causes a `skip` because
resolving it would mean a network call.

### Output is derived, and that produces real skips

Per `instance.md` note 5, the Prefactor instance record carries no output field.
`EvalInstance.output` is **derived** from the root span, defined as the earliest
span with `parent_id == null`, and is that span's normalized `output`.

When there is no root span, or more than one root span, `EvalInstance.output` is
null by construction and this eval returns `skip`, with `details` naming the
condition:

| Condition | `details` names |
| --- | --- |
| No span has `parent_id == null` | no root span |
| More than one span has `parent_id == null` | multiple root spans, with the count |
| A single root span whose `output` is null | root span output is null |

The first two are common rather than exceptional. Any instance whose spans were
sampled, truncated, or fetched with a page limit can lose its root or orphan its
children, and an instance assembled from parallel sub-agents can legitimately
have several roots. A pack running this eval over a sampled fetch should expect
a substantial skip rate, and that rate is a statement about the fetch rather
than about the agent.

The third case is governed by `null_output`. The default `skip` treats a null
output as absent evidence. Setting it to `fail` treats it as a contract
violation, which is the right setting for an agent that is required to produce
structured output on every run. The choice is left to the user because both
readings are defensible and guessing would produce confident verdicts either
way.

The eval does not attempt to reconstruct an output from the last span, the
latest `output`-typed span, or any other heuristic. A guessed output validated
against a real schema produces a real-looking verdict about the wrong data.

### Verdict

The eval passes when `EvalInstance.output` validates against `schema`. It fails
when it does not.

## Failure output

`status`: `fail`

`details`: one sentence naming the first violation, for example:
`Instance output failed schema validation: missing required property "booking_id".`

`evidence.span_ids`: the ID of the root span the output was derived from, as a
single-element list. The output belongs to that span and pointing at it is what
makes the failure investigable in Prefactor.

`evidence.values`:

```
{
  "root_span_id": "01kxye3nz47ewyfq10j6b3m9q8dc57yv",
  "root_span_name": "booking:complete",
  "violations": [
    { "pointer": "", "keyword": "required", "message": "missing required property \"booking_id\"" },
    { "pointer": "/confirmed", "keyword": "type", "message": "expected boolean, got string" }
  ],
  "violation_count": 2
}
```

`pointer` is a JSON Pointer into the output document, empty string for the root.
Up to five violations are recorded with `violation_count` carrying the true
total, since a wholly wrong output can otherwise produce hundreds. Error
`message` text is normalized to the short forms listed in the conformance
fixtures, because raw validator messages differ between the Python and
TypeScript libraries and byte-identical results are required.

Output values are never reproduced in evidence. `pointer` and `keyword` locate
the problem without copying data that may be sensitive.

## Notes

This is the closest thing in the core pack to an end-to-end assertion, and it is
still only a shape check. It answers whether the run produced something of the
right form, which is the question a downstream consumer of the agent actually
depends on.

Pair it with `core.termination_state`. A run can reach `complete` with a
malformed output and a run can produce a well-formed output and still be
`terminated`. The two evals are independent and a pack wants both.

Deliberately not checked:

1. **Whether the output is correct.** A perfectly shaped answer containing the
   wrong booking reference passes. Schema conformance is not truth, and the gap
   between them is the gap between this library and an LLM judge.
2. **Intermediate outputs.** Only the derived instance output. Span-level output
   shape is not validated anywhere in v1.
3. **Inputs.** `EvalInstance.input` is derived the same way and is not checked
   here. `core.tool_arg_schema` covers tool-call inputs and nothing covers the
   instance input in v1.
4. **A reconstructed output when the root is ambiguous.** Skipped, never
   guessed. See above.
5. **`format` assertions.** Annotations only, matching `core.tool_arg_schema`.
   Use `pattern` where enforcement is wanted.
6. **Remote `$ref`.** Not resolved, eval skips. Network access is forbidden.
7. **Whether the schema itself is sensible.** A schema of `{}` validates
   everything and passes every instance. The library does not evaluate the
   user's contract, only conformance to it.
