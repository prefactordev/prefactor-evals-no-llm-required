# core.redundant_tool_calls

## ID

`core.redundant_tool_calls`

## Name

Redundant tool calls

## Requires

1. `EvalInstance.spans`
2. `EvalSpan.type`
3. `EvalSpan.name`
4. `EvalSpan.input`
5. `EvalSpan.output`
6. `EvalSpan.id`

Skips when the instance has no spans of type `tool_call`.

## Config

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `max_repeats` | integer | `2` | Maximum length of a run of consecutive identical calls that makes no progress. |
| `ignore_tools` | string[] | `[]` | Tool names exempt from the check. |
| `ignore_arg_keys` | string[] | `[]` | Argument keys excluded from the signature before hashing. |

## Pass criteria

Walk the instance's spans in schema order, which is `started_at` ascending then
`id` ascending as defined in `instance.md` note 4.

Build a signature for every span: the tuple of `type`, `name`, and a canonical
serialization of `input` with `ignore_arg_keys` removed. Canonical
serialization sorts object keys recursively, preserves array order, and renders
numbers without trailing zeros, so that two structurally identical inputs always
produce one signature.

A **redundant run** is a maximal sequence of adjacent spans in that order for
which all three hold:

1. Every span in the sequence has `type` equal to `tool_call`.
2. Every span in the sequence has the same signature.
3. No span in the sequence differs in state progression from the first. State
   progression means either a change in canonical serialization of `output`, or
   the appearance of a span of a different `type` between two members of the
   sequence. A differing `output` ends the run at that span. Any intervening
   span of a different `type`, including `llm_call`, `retrieval`, `handoff`,
   `output`, and `other`, ends the run at that point, because something other
   than the repeated tool happened between the calls.

Spans whose `name` appears in `ignore_tools` are excluded from consideration and
also break a run, because an excluded call is still something that happened.

The eval passes when every redundant run has length at most `max_repeats`. It
fails when any run is longer.

Spans with null `input` are included, with the null treated as a distinct
signature value. Spans with null `output` are included, with null treated as a
distinct output value, so two consecutive calls that both returned null count as
making no progress.

### Distinction from core.loop_detection

The two evals catch different shapes and neither subsumes the other.

| | `core.redundant_tool_calls` | `core.loop_detection` |
| --- | --- | --- |
| Position | Consecutive only | Anywhere in the instance |
| Progress | Run broken by output change or intervening span type | Not considered |
| Config | `max_repeats`, default 2 | `max_occurrences`, default 3 |
| Typical catch | A tight retry with no state change | A tool hit repeatedly across a whole run |

An agent that calls `get_status` three times in a row with identical arguments
and identical results fails this eval and, at the default of 3, passes
`core.loop_detection`. An agent that calls `search_docs` seven times spread
across a long run, with reasoning in between each, passes this eval and fails
`core.loop_detection`. Packs run both.

## Failure output

`status`: `fail`

`details`: one sentence naming the worst offending run and its length, for
example: `Tool "get_status" called 5 times consecutively with identical arguments and no change in output, limit is 2.`

`evidence.span_ids`: the IDs of every span in every over-limit run, sorted by
start time then ID.

`evidence.values`:

```
{
  "offenders": [
    { "tool": "get_status", "run_length": 5, "limit": 2, "arg_digest": "sha256:41c7...", "output_digest": "sha256:e0b2..." }
  ],
  "max_repeats": 2
}
```

`arg_digest` and `output_digest` are hashes rather than raw values, because both
can be large and can contain sensitive data. The raw values are one span lookup
away in Prefactor.

## Notes

Catches the tight retry: an agent hammering one tool with the same arguments,
getting the same answer, and not reacting to it. This is the shape that burns a
step budget fastest and the shape most visible to a user watching a run.

The `output` comparison is what makes this a progress check rather than a
repetition check. Polling a queue until the queue answers differently is
legitimate and passes as soon as the answer changes.

Deliberately not checked:

1. **Whether the repetition was justified.** A poll loop that returns the same
   "still pending" payload eight times is correct behaviour against some APIs
   and this eval will fail it. That is what `ignore_tools` is for. The library
   will not infer intent.
2. **Semantically equivalent outputs.** Two responses that differ only in a
   timestamp field are different outputs, the run breaks, and this eval sees
   progress where a human would see none. `ignore_arg_keys` has no output-side
   equivalent in v1 because choosing which output fields count as noise is a
   judgment call.
3. **Non-consecutive repetition.** That is `core.loop_detection` and it is a
   separate eval on purpose.
4. **Repeats across span types.** A `tool_call` and a `retrieval` with the same
   name and arguments are different signatures and never form a run.
5. **Whether the tool should have been called at all.** Correctness of tool
   selection is not a deterministic question.
6. **Cross-instance repetition.** Scope is one instance.

The default of 2 says that calling a tool twice in a row with the same input and
the same result is tolerable and three times is not. It is a starting point, not
a recommendation. Set it from your own traces.
