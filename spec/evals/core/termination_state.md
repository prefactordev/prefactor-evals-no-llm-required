# core.termination_state

## ID

`core.termination_state`

## Name

Termination state

## Requires

1. `EvalInstance.state`
2. `EvalInstance.duration_ms` (only when `max_duration_ms` is set)
3. `EvalInstance.metadata.termination_reason` (optional, used for evidence only,
   absence does not skip)

`state` is never null, so the state half of this eval never skips for missing
data. The duration half skips as described below.

## Config

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `max_duration_ms` | integer | `null` | Wall clock ceiling for the whole run. Null disables the duration half of the check. |
| `allow_states` | string[] | `["complete"]` | Instance states treated as a clean finish. |

`max_duration_ms` has no invented default. A run length ceiling is agent
specific in exactly the way a step budget is, and null means the check is off
rather than the check being applied against a number nobody chose. Set it in
pack YAML.

`allow_states` exists as an escape hatch for agents whose lifecycle genuinely
ends somewhere other than `complete`. Widening it is a deliberate act recorded
in a committed file. The default is the single value `complete`.

## Pass criteria

The eval evaluates two conditions and fails if either fails.

### Condition 1: final state

The instance passes this condition when `state` appears in `allow_states`. At
the default that means `state` is exactly `complete`.

The four failing states are distinct and are reported distinctly. Per
`instance.md`, `terminated` means Prefactor stopped the run externally and is a
server-set state no SDK can write. It is not folded into `cancelled`, which
means the run ended before it ever started. The `details` string names the
actual state verbatim and never substitutes a category word for it.

| `state` | Verdict at default config | Meaning reported |
| --- | --- | --- |
| `complete` | pass | Finished cleanly. |
| `failed` | fail | The run errored to completion. |
| `cancelled` | fail | The run ended before it started. |
| `terminated` | fail | Prefactor stopped the run externally. |
| `active` | see condition 2 | Still running at fetch time. |
| `pending` | see condition 2 | Never started at fetch time. |

### Condition 2: duration ceiling

Applied only when `max_duration_ms` is non-null.

The instance fails this condition when `duration_ms` is non-null and greater
than `max_duration_ms`. This applies to every state including `complete`: a run
that finished cleanly but took four times its ceiling failed its budget and says
so.

`duration_ms` is derived as `ended_at - started_at` per `instance.md` note 3 and
is null when either endpoint is null. Elapsed time for a run with no `ended_at`
is not computable without reading a clock, and clock reads are forbidden by the
determinism rule. This eval therefore never estimates elapsed time for an
in-flight run.

### Interaction, stated exhaustively

1. `state` in `allow_states`, duration within ceiling or unmeasurable: `pass`.
2. `state` in `allow_states`, `duration_ms` over ceiling: `fail`, on duration.
3. `state` is `failed`, `cancelled`, or `terminated`: `fail`, on state. Duration
   is still reported in `evidence.values` when available but the state is the
   stated reason.
4. `state` is `active` or `pending` and `duration_ms` is non-null and over the
   ceiling: `fail`. An instance with both endpoints set and a non-terminal state
   is a stuck record, and it is over budget by a measurable amount.
5. `state` is `active` or `pending` and `duration_ms` is null: `skip`, with
   `details` naming the state and the missing `ended_at`. The run had not
   finished when it was fetched, so there is no verdict to give. Re-fetching
   later is the answer, not guessing now.

## Failure output

`status`: `fail`

`details`: one sentence naming the state or the overrun, for example:
`Instance ended in state "terminated", only "complete" is accepted.` or
`Instance completed in 812004 ms, ceiling is 300000 ms.`

`evidence.span_ids`: for a state failure, the IDs of every span whose own
`state` is `failed`, sorted by start time then ID, which points at where the run
came apart. Empty when no span failed, which is normal for `cancelled` and
common for `terminated`. For a duration-only failure, empty.

`evidence.values`:

```
{
  "state": "terminated",
  "allow_states": ["complete"],
  "duration_ms": 812004,
  "max_duration_ms": 300000,
  "termination_reason": "wall_clock_exceeded",
  "failed_span_count": 0
}
```

`termination_reason` is read from `EvalInstance.metadata.termination_reason` and
is null when the seam did not supply it. It is evidence only and never drives
the verdict, because a replacement seam is under no obligation to provide it.

## Notes

This is the cheapest eval in the pack and the one most likely to be the real
answer when a suite of richer evals all skip. If an agent's instances are
terminating, nothing downstream of that is worth reading yet.

A `terminated` instance very often leaves spans stuck in `active` forever,
because termination stops the process before anything closes them. That is
documented in `span.md` and it is the same event, not a second bug. This eval
reports the termination. `core.latency_budget` reports the open spans as
unmeasured rather than as failures for the same reason.

Deliberately not checked:

1. **Why the run failed.** `error` records on spans are not inspected here and
   the failure taxonomy is not interpreted. This eval answers "did it finish
   cleanly", one bit.
2. **Whether `terminated` was justified.** An external stop can be a correct
   safety action. The eval fails it either way, because a run that Prefactor had
   to stop did not end on its own terms.
3. **Whether the output is any good.** A run can reach `complete` with a
   useless result and pass this eval. `core.output_schema` checks shape and
   nothing checks quality.
4. **Elapsed time of in-flight runs.** Requires a clock read. Skipped, not
   estimated. See condition 2.
5. **Span-level states.** Failed spans appear in evidence and never change the
   verdict. An instance that recovered from a failed span and reached `complete`
   passes, which is the intended reading of a retry that worked.
6. **`termination_reason` values.** Passed through as evidence, never matched
   against a list. Its vocabulary is Prefactor's and is not part of this
   library's contract.
