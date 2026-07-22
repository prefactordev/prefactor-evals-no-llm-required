# core.loop_detection

## ID

`core.loop_detection`

## Name

Loop detection

## Requires

1. `EvalInstance.spans`
2. `EvalSpan.type`
3. `EvalSpan.name`
4. `EvalSpan.input`
5. `EvalSpan.id`

Skips when the instance has no spans of type `tool_call`.

## Config

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `max_occurrences` | integer | `3` | Maximum times one tool signature may appear anywhere in the instance. |
| `ignore_tools` | string[] | `[]` | Tool names exempt from the check. |
| `ignore_arg_keys` | string[] | `[]` | Argument keys excluded from the signature before hashing. |

## Pass criteria

Build a signature for every `tool_call` span: the tuple of `name` and a
canonical serialization of `input` with `ignore_arg_keys` removed. Canonical
serialization sorts object keys recursively, preserves array order, and renders
numbers without trailing zeros, so that two structurally identical inputs
always produce one signature.

Spans whose `name` appears in `ignore_tools` are excluded.

The eval passes when every distinct signature occurs at most `max_occurrences`
times across the whole instance. It fails when any signature occurs more.

Position does not matter. Occurrences need not be consecutive, which is the
distinction between this eval and `core.redundant_tool_calls`.

Spans with null `input` are included, with the null treated as a distinct
signature value rather than skipped. An agent calling the same argument-free
tool twenty times is exactly the failure this eval exists to catch.

## Failure output

`status`: `fail`

`details`: one sentence naming the worst offending signature and its count, for
example: `Tool "search_docs" called 7 times with identical arguments, limit is 3.`

`evidence.span_ids`: the IDs of every span in every over-limit signature group,
sorted by start time then ID.

`evidence.values`:

```
{
  "offenders": [
    { "tool": "search_docs", "count": 7, "limit": 3, "arg_digest": "sha256:9f2a..." }
  ],
  "max_occurrences": 3
}
```

`arg_digest` is a hash rather than the raw arguments, because arguments can be
large and can contain sensitive values. The raw arguments are one span lookup
away in Prefactor.

## Notes

Catches the classic runaway: an agent retrying the same call forever because
nothing in its loop changes state.

Deliberately not checked:

1. **Semantically similar calls.** Two searches for "refund policy" and "refund
   policies" are different signatures and this eval will not connect them. Near
   duplicate detection needs a judgment call and v1 makes none.
2. **Whether repetition was justified.** Polling a job status endpoint eight
   times is correct behaviour and this eval will fail it. That is what
   `ignore_tools` is for. The library will not infer intent.
3. **Loops that vary their arguments.** An agent incrementing a page number
   forever produces a different signature each time and passes. `core.efficiency`
   is the check that catches that shape: the run takes far more steps than the
   agent's own normal.
4. **Cross-instance repetition.** Scope is one instance.

The default of 3 is a starting point, not a recommendation. Every agent has a
different tolerance and users are expected to set this from their own traces
rather than trusting the default.
