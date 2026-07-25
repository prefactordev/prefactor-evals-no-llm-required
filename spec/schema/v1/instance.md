# schema/v1: EvalInstance

The normalized view of one agent run. Evals only ever see this shape. The seam
(`source.py` / `source.ts`) is responsible for producing it.

Field names below are the normalized names. They are identical in both
languages at the data level. The TypeScript surface exposes them in camelCase
at the API boundary only, as allowed by build spec rule 4.

## Fields

| Field | Type | Null? | Description |
| --- | --- | --- | --- |
| `id` | string | no | Prefactor agent instance ID. |
| `agent_id` | string | no | The agent that produced this run. |
| `agent_version` | string | yes | Version identifier for the agent. See note 1. |
| `environment` | string | yes | Environment the run executed in. See note 2. |
| `started_at` | timestamp | yes | When execution began. Null if never started. |
| `ended_at` | timestamp | yes | When execution finished. Null while running. |
| `state` | enum | no | One of `pending`, `active`, `complete`, `failed`, `cancelled`, `terminated`. |
| `duration_ms` | number | yes | Derived, not fetched. See note 3. |
| `spans` | EvalSpan[] | no | Ordered list. See note 4. Empty list is valid. |
| `input` | any | yes | The initiating input. Derived. See note 5. |
| `output` | any | yes | The final output. Derived. See note 5. |
| `cost` | number | yes | Total run cost. See note 6. |
| `metadata` | record | no | Pass-through. See note 7. Never null, may be empty. |

## Timestamps

All timestamps are ISO 8601 strings on the wire. Implementations parse to the
native type (`datetime` in Python, `Date` in TypeScript) at the seam boundary.
Evals must not parse strings themselves. Any eval comparing times operates on
parsed values or on `duration_ms`.

## State

`state` is the run lifecycle. Values are lowercase, matching the API verbatim.

```
pending -> active -> complete | failed | cancelled | terminated
```

`terminated` means Prefactor stopped the run externally. It is a server-set
state that no SDK can write. It is distinct from `cancelled`, which means the
run ended before it ever started. Evals that treat "did this finish cleanly"
as a question must treat `complete` as the only pass state, and must not fold
`terminated` into `cancelled`.

## Notes

1. **agent_version.** The API returns `agent_version_id`, an opaque Prefactor
   ID, not a semantic version string. The seam passes it through unchanged and
   does not attempt to resolve it to a human readable version. Evals treat it
   as an opaque grouping key only. Do not write an eval that parses it.

2. **environment.** The API returns `environment_id`, an opaque ID. Same
   treatment as `agent_version`. Filtering by environment means filtering by
   that ID.

3. **duration_ms.** The Prefactor API exposes no duration field at any level.
   The seam computes `ended_at - started_at` in milliseconds. Null when either
   endpoint is null. This is a schema/v1 addition to the build spec shape,
   added because four evals need it and every one of them would otherwise
   recompute the same subtraction.

4. **Span ordering.** Spans are sorted by `started_at` ascending, then by `id`
   ascending as a stable tiebreak. Concurrent spans share a start time often
   enough that an unstable sort would make order-sensitive evals
   nondeterministic, which breaks the whole premise. The list is flat, not a
   tree. Parentage is available via `EvalSpan.parent_id` and evals that need
   the tree build it themselves.

5. **input and output.** The instance record carries neither, so both are
   derived, by one of two rules.

   **Rule A, single root.** If exactly one span has `parent_id == null`, that
   span's `input` and `output` are the instance's.

   **Rule B, flat trace.** Otherwise `input` is the `input` of the earliest
   span that has one, and `output` is the `output` of the latest span that has
   one.

   Rule B exists because real traces are almost always flat. Verified against
   live data: every span of every sampled instance reported a null parent, so
   Rule A resolved on none of them. Deriving from the root alone would leave
   `input` and `output` null on every real instance, and every eval reading
   them would skip while looking merely unconfigured.

   Rule B is a definition rather than a guess: the earliest span is what the
   agent did first, and the last span carrying an output holds the final one.
   But it is only sound on a flat trace, so which rule fired is always recorded
   in `metadata.input_source` and `metadata.output_source`, alongside
   `metadata.root_span_count`. Values are `root_span`, `first_span`,
   `last_output_span`, or null when nothing was derivable.

   Both fields are still null when no span carries the relevant value, and
   evals requiring them still `skip` with a reason.

   An eval whose meaning depends on the distinction, such as one comparing the
   user's original request against the final answer, should read
   `metadata.input_source` and skip when it is not `root_span`. A flat trace
   cannot tell you which span was the request.

6. **cost.** Instance-level only, and only when the seam requested it. There is
   no per-span cost anywhere in the Prefactor API. The value is
   `cost_breakdown.total_cost` from the instance detail endpoint. Units are
   whatever the API reports, documented as account currency, and the library
   does no conversion. Null when costs were not requested or not available.
   See `span.md` note on the same subject.

7. **metadata.** A pass-through record. The seam populates these keys when the
   source provides them, and evals may read any of them:

   | Key | Source |
   | --- | --- |
   | `account_id` | instance `account_id` |
   | `termination_reason` | instance `termination_reason` |
   | `purpose` | instance `purpose`: `live`, `smoke_test`, or `eval` |
   | `inserted_at`, `updated_at` | instance record timestamps |
   | `span_counts` | instance `span_counts` when requested |
   | `span_schema_counts` | instance `span_schema_counts` when requested |
   | `risk_score` | instance `risk_score` when requested |
   | `cost_breakdown` | full breakdown when requested |

   Evals that read a metadata key must declare it under **Requires** and must
   `skip` when it is absent. A replacement seam is under no obligation to
   provide any of them.

   Ground-truth labels used by evals such as `voice.data_capture_accuracy` and
   `sdlc.routing_accuracy` are supplied by the user through pack config, not
   through this record, unless the pack explicitly configures a metadata key
   to read them from.

## Example

```json
{
  "id": "01kxye0kcz7synth14w30ebex5zya7jf",
  "agent_id": "01kxqc9myk7synthxsqbsnnebf9kj73d",
  "agent_version": "01kxq9v2n07synthk1p5x8m2c4tq81bd",
  "environment": "01kxq7m4b27synthz9r3w6h8d5yu20ac",
  "started_at": "2026-07-20T09:14:02.114Z",
  "ended_at": "2026-07-20T09:14:47.902Z",
  "state": "complete",
  "duration_ms": 45788,
  "spans": [],
  "input": { "caller_intent": "book_appointment" },
  "output": { "booking_id": "BK-4471", "confirmed": true },
  "cost": 0.0184,
  "metadata": {
    "account_id": "01kxq5j8t07synthv2n6y4b9c1sw73mz",
    "termination_reason": null
  }
}
```
