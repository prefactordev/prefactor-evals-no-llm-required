# schema/v1: EvalSpan

The normalized view of one step within a run. Produced by the seam, consumed by
every eval.

## Fields

| Field | Type | Null? | Description |
| --- | --- | --- | --- |
| `id` | string | no | Prefactor span ID. |
| `parent_id` | string | yes | Parent span ID. Null for root spans. |
| `instance_id` | string | no | Owning instance. See note 1. |
| `type` | enum | no | `tool_call`, `llm_call`, `retrieval`, `handoff`, `output`, `other`. See note 2. |
| `name` | string | no | Step name. Tool name for `tool_call` spans. See note 3. |
| `schema_name` | string | no | Raw, unnormalized source type. See note 2. |
| `input` | any | yes | Structured arguments. |
| `output` | any | yes | Structured result. |
| `state` | enum | no | `pending`, `active`, `complete`, `failed`, `cancelled`. |
| `started_at` | timestamp | yes | |
| `ended_at` | timestamp | yes | Null while the span is open. |
| `duration_ms` | number | yes | Derived. Null if either endpoint is null. |
| `cost` | number | yes | Always null against Prefactor. See note 4. |
| `tokens` | record | yes | `{prompt, completion, total}`. See note 5. |
| `error` | record | yes | `{type, message, stacktrace}` when the span failed. |
| `metadata` | record | no | Pass-through. Never null, may be empty. |

## State

```
pending -> active -> complete | failed | cancelled
```

Spans have no `terminated` state. That exists at instance level only. A span
belonging to a terminated instance keeps whatever state it last reached, very
often `active` forever, because termination stops the process before anything
closes the span. Evals that count unfinished spans must expect this and must
not read a stuck `active` span as a distinct bug from the termination itself.

## Note 1: instance_id

Not in the build spec's EvalSpan shape. Added because the source API returns
spans in a flat list keyed by `agent_instance_id`, several evals compare spans
across instances (`sdlc.idempotency`, `wf.duplicate_guard`), and without it
those evals would have to thread ownership through their own bookkeeping.

## Note 2: type normalization is heuristic, and that matters

This is the single most consequential mapping decision in schema/v1, so it is
stated plainly rather than buried.

**Prefactor has no span type enum on the wire.** The SDKs define an internal
`SpanType` (`agent`, `llm`, `tool`, `chain`, `retriever`) but it never reaches
the API. What the API stores and returns is `schema_name`, a free-form string
chosen by whoever instrumented the agent. Common values in practice are
`langchain:tool`, `langchain:llm`, `langchain:agent`, `langchain:chain`,
`langchain:retriever`, and entirely bespoke names such as `agent:shutdown` or
`booking:create`.

So `type` is derived, and derivation can be wrong. Three layers, in order:

1. **Explicit config.** A pack may set `span_type_map`, an exact
   `schema_name -> type` mapping. Always wins.
2. **Built-in exact matches.** First table below.
3. **Built-in prefixes.** Second table below, a closed and enumerated list.
4. **Fallback.** `other`.

There is deliberately **no substring guessing**. A rule like "contains `tool`
means `tool_call`" would silently mistype a span named `protocol:sync`, and a
silently mistyped span produces a silently wrong eval result, which is worse
than no result. Unmapped means `other`, visibly.

Exact matches:

| `schema_name` | `type` |
| --- | --- |
| `langchain:tool` | `tool_call` |
| `langchain:llm` | `llm_call` |
| `langchain:retriever` | `retrieval` |
| `langchain:chain` | `other` |
| `langchain:agent` | `other` |
| `ai-sdk:llm` | `llm_call` |
| `ai-sdk:agent` | `other` |

Prefixes, matched only at the start of the string, only on this closed list:

| prefix | `type` | tool name |
| --- | --- | --- |
| `ai-sdk:tool:` | `tool_call` | the remainder after the prefix |
| `langchain:tool:` | `tool_call` | the remainder after the prefix |

The prefix rule exists because both the ai-sdk and langchain conventions encode
the tool identity in the schema name itself, as a third segment:
`ai-sdk:tool:lookup_customer`, `langchain:tool:deposit_btc`. Exact matching
alone would leave every named tool span typed `other`, and the three core evals
that key on tool identity would silently check nothing. Both prefixes were
confirmed against live agents, one of each instrumentation.

This is a closed list, not a pattern language. Adding a prefix to it is a
schema change. Users with other conventions use `span_type_map`.

`handoff` and `output` have **no built-in mapping at all**. No convention
exists for them in Prefactor instrumentation. Every eval that depends on them
(`core.escalation_rule`, `voice.transfer_target`, `voice.action_completion`,
`cs.escalation_accuracy`) therefore requires `span_type_map` config, or an
equivalent explicit span name list, and returns `skip` with an explicit reason
when the pack has not configured it. Those evals will skip by default on
a fresh install. That is correct behaviour, and build spec section 6 is
explicit that skip is not a pass. A user who wants those checks must tell the
library what a handoff looks like in their agent.

The raw `schema_name` is preserved on every span so evals can match on it
directly, and so a wrong normalization is always debuggable.

## Note 3: name

Source order is the tool name recovered by a prefix rule in note 2, then
`payload.name`, then `schema_name`. `name` is never null, because
`schema_name` is never null.

The prefix step comes first for a reason. Against ai-sdk instrumentation the
tool identity lives in `schema_name` as `ai-sdk:tool:issue_refund`, while
`payload.name` on the same span is a display label that may be shared across
tools or absent. Taking `payload.name` first would collapse distinct tools into
one name.

For `tool_call` spans, `name` is the tool identity used by
`core.redundant_tool_calls`, `core.loop_detection`, and
`core.forbidden_actions`. If instrumentation reuses one `schema_name` for every
tool and distinguishes tools only inside the payload, those evals will see one
tool where there are many. Verified against live traces: ai-sdk instrumentation
does not have this problem, since it emits one schema name per tool. Bespoke
instrumentation might. It is a limitation of the source data, not something the
library can detect, so the fix is `span_type_map` plus a naming convention.

## Note 4: cost is null, and the reason is structural

The Prefactor API exposes **no per-span cost field**. Neither SDK emits one.
Cost exists only as an instance-level `cost_breakdown` aggregate, and only when
explicitly requested at fetch time.

The build spec's `core.cost_budget` eval therefore operates on
`EvalInstance.cost`, not on a sum of span costs. Summing this field would
always produce zero, and an eval that always passes because its input is
always zero is a broken eval that looks like a healthy one.

The field is kept in the schema, always null against Prefactor, because a
replacement seam pointed at a source that does have per-span cost should have
somewhere to put it. Evals must treat null as "unknown" and skip, never as
zero.

## Note 5: tokens

Normalized to `{prompt, completion, total}` from the wire's
`token_usage.{prompt_tokens, completion_tokens, total_tokens}`, which is nested
inside the payload envelope rather than sitting at the top of the span record.
Null when the span reported no usage, which is every non-LLM span.

Token counts are available but **no v1 eval uses them as a budget**. Cost is
the budget dimension, at instance level. Tokens are exposed for forkers and for
evidence in failure output.

## Example

```json
{
  "id": "01kxye3p2m7ewyfq88k1v5t7n2ra94xc",
  "parent_id": "01kxye3nz47ewyfq10j6b3m9q8dc57yv",
  "instance_id": "01kxye0kcz7ewyfq14w30ebex5zya7jf",
  "type": "tool_call",
  "name": "check_availability",
  "schema_name": "langchain:tool",
  "input": { "date": "2026-07-24", "clinician": "any" },
  "output": { "slots": ["09:30", "11:00"] },
  "state": "complete",
  "started_at": "2026-07-20T09:14:11.480Z",
  "ended_at": "2026-07-20T09:14:12.006Z",
  "duration_ms": 526,
  "cost": null,
  "tokens": null,
  "error": null,
  "metadata": {}
}
```
