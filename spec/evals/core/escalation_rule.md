# core.escalation_rule

## ID

`core.escalation_rule`

## Name

Escalation rule

## Requires

1. `EvalInstance.spans`
2. `EvalSpan.type`
3. `EvalSpan.name`
4. `EvalSpan.output`
5. `EvalSpan.started_at`
6. `EvalSpan.id`
7. Pack config `span_type_map`, mapping at least one `schema_name` to `handoff`.

Requirement 7 is not optional and it is the reason this eval skips by default.
See the section below before implementing anything else here.

## Config

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `trigger_names` | string[] | `[]` | Span names that constitute an escalation trigger. Exact, case-sensitive match. |
| `trigger_values` | record<string, any> | `{}` | Map of dotted path within a span's `output` to a value that constitutes a trigger. |
| `handoff_types` | string[] | `["handoff"]` | Normalized span types that satisfy the escalation. |

`span_type_map` is pack-level config shared with the seam, not a key of this
eval, and is listed under Requires rather than here.

At least one of `trigger_names` and `trigger_values` must be non-empty. Neither
has a default, because what counts as an escalation trigger is specific to one
agent's design and an invented list would fire on nothing or on everything. The
eval skips when both are empty, naming both keys.

### This eval skips by default, and that is correct

Per `span.md` note 2, Prefactor has no span type enum on the wire. `type` is
derived from `schema_name`, and `handoff` has **no built-in mapping at all**.
There is no convention for it in Prefactor instrumentation, so nothing in the
built-in table ever produces a `handoff` span. Without configuration, every span
in every instance normalizes to something else and this eval would find zero
handoffs on a trace that contains several.

Rather than fail every triggered instance for lack of a span type that cannot
exist, the eval checks first whether `span_type_map` maps any `schema_name` to a
type in `handoff_types`. If it does not, the eval returns `skip` immediately,
before evaluating any trigger, with `details` naming `span_type_map` and stating
that no handoff type is configured.

The consequence is that this eval skips on a fresh install, on every instance,
until a user tells the library what a handoff looks like in their agent. Per the
spec README, skip is not a pass and the scorecard counts it separately, so this
is visible rather than silent. A user who wants this check configures, for
example, `span_type_map: { "support:transfer_to_human": "handoff" }`, and the
eval starts running.

The same applies to `voice.transfer_target` and `voice.action_completion`.

## Pass criteria

Spans are considered in schema order, `started_at` ascending then `id`
ascending, per `instance.md` note 4.

### Triggers

A span is a trigger when either rule matches:

1. Its `name` appears in `trigger_names`, compared with exact, case-sensitive
   string equality. No pattern matching.
2. Its `output` is a mapping and, for some key in `trigger_values`, resolving
   that key as a dotted path into `output` yields a value equal to the
   configured value. Path resolution walks object keys only, never array
   indices, and a path that does not resolve is not a match. Equality is exact
   and type-sensitive: the string `"true"` does not match the boolean `true`,
   an integer `1` does not match a string `"1"`. Numeric comparison is by value,
   so `1` and `1.0` are equal. Spans with null `output`, or with a non-mapping
   `output`, never match this rule.

### Verdict

Let `first_trigger` be the earliest trigger span in schema order.

If there is no trigger span, the eval returns `pass`, with `details` stating
that no trigger fired. This is a vacuous pass and it is stated as such in
`details` so that a reader does not mistake it for an escalation having been
handled correctly.

If there is a trigger span, the eval passes when at least one span exists whose
`type` is in `handoff_types` and whose position in schema order is strictly
after `first_trigger`. Strictly after means a later index in the ordered list,
not a later timestamp, so that concurrent spans sharing a start time are
resolved by the same stable tiebreak everything else uses. A handoff span that
is itself the trigger span does not satisfy the rule.

The eval fails when a trigger fired and no such span exists, including the case
where a handoff span exists but occurs only before the trigger.

## Failure output

`status`: `fail`

`details`: one sentence naming the trigger, for example:
`Trigger "sentiment_negative" fired at span 3 of 19 and no handoff span followed.`

`evidence.span_ids`: the ID of the first trigger span, plus the IDs of any
handoff-typed spans that occurred before it and therefore did not count, sorted
by start time then ID.

`evidence.values`:

```
{
  "trigger_span_id": "01kxye3p2m7ewyfq88k1v5t7n2ra94xc",
  "trigger_rule": "name",
  "trigger_matched": "sentiment_negative",
  "trigger_index": 3,
  "span_count": 19,
  "handoff_types": ["handoff"],
  "handoff_spans_before_trigger": 1,
  "handoff_spans_after_trigger": 0,
  "trigger_count": 2
}
```

`trigger_rule` is `name` or `value`, and `trigger_matched` is the list entry or
dotted path that fired. Output values are not reproduced in evidence, only the
path that matched, since outputs can carry customer data.

## Notes

The check is "did the agent hand off after it should have", one direction only.
It says nothing about whether the handoff went to the right place, which is
`voice.transfer_target` in the voice pack.

Only the first trigger is used for the verdict. An instance where trigger one
was handled and trigger two was not still passes, because a handoff exists after
the first trigger. `trigger_count` is reported so that this is visible. Checking
every trigger independently would fail the common and correct pattern of one
escalation resolving several concurrent triggers, and v1 takes the conservative
reading.

Deliberately not checked:

1. **Handoff target.** Any span of a configured handoff type satisfies the rule.
   Where it went is not inspected.
2. **Whether the handoff succeeded.** A handoff span in state `failed` still
   satisfies the rule. The agent did the right thing and the transfer broke,
   which is a different finding.
3. **Latency to escalate.** A handoff twenty spans after the trigger passes
   identically to one immediately after. Time to escalate is not budgeted here.
   Use `core.latency_budget` for run-level time.
4. **Whether escalation was warranted.** The trigger list is the user's
   definition. Semantic detection of a customer who should have been escalated
   is a judgment call and out of scope for a no-LLM library.
5. **Triggers after the first.** See above. Reported, not enforced.
6. **Escalation without a trigger.** An agent that hands off constantly for no
   configured reason passes. Over-escalation is not checked in v1.
7. **Arrays in `trigger_values` paths.** Dotted paths walk object keys only.
   A trigger buried in a list is not reachable and will not fire.
