# core.cost_budget

## ID

`core.cost_budget`

## Name

Cost budget

## Requires

1. `EvalInstance.cost`

That is the entire requirement, and the shortness is the point. This eval does
not read `EvalSpan.cost` and must never be implemented to sum it. See below.

Skips when `EvalInstance.cost` is null, which is the default state unless the
seam requested costs at fetch time.

## Config

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `max_cost` | number | required | Ceiling for the run's total cost, in account currency. |

`max_cost` is required and has no default. Cost is denominated in whatever
currency the account reports, the library performs no conversion, and the
tolerable spend for one run is a business decision. Any number written here
would be a fabricated threshold in an unknown currency. The eval skips when
`max_cost` is absent, naming it.

## Pass criteria

The eval passes when `EvalInstance.cost` is less than or equal to `max_cost`. It
fails when it is greater.

### Null cost skips, and never reads as zero

Per `instance.md` note 6, `EvalInstance.cost` is `cost_breakdown.total_cost`
from the instance detail endpoint, populated only when the seam requested it.
Null means "not requested or not available", never "free".

When `cost` is null the eval returns `skip`, with `details` stating that
instance cost was not fetched and naming `cost`. Treating null as zero would
make this eval pass on every instance the seam fetched cheaply, which is most of
them on a default configuration. That is the exact failure this library is built
to avoid: a green result produced by an absent input.

A cost of exactly `0` is a real value and is evaluated normally. Zero and null
are different and the implementation must not conflate them, which in practice
means an explicit null check rather than a falsiness check in either language.

### Per-span cost is always null and is not summed

Per `span.md` note 4, the Prefactor API exposes no per-span cost field and
neither SDK emits one. `EvalSpan.cost` is present in the schema, always null
against Prefactor, so that a replacement seam pointed at a source that does have
per-span cost has somewhere to put it.

Summing `EvalSpan.cost` across an instance therefore yields zero for every
Prefactor instance, and an eval that always passes because its input is always
zero is a broken eval wearing a healthy one's result. This eval operates on the
instance aggregate only. A conformance fixture exists specifically to catch an
implementation that reintroduces the sum.

### Requirement on the seam

This eval only produces a verdict when the fetch requested cost data. That is a
seam-level choice, not an eval-level one, and it is visible: a pack that runs
this eval against a fetch without costs produces a run of skips, all naming
`cost`, which is a legible instruction to change the fetch.

## Failure output

`status`: `fail`

`details`: one sentence naming the cost and the ceiling, for example:
`Instance cost 0.4120 exceeds budget of 0.2500.`

`evidence.span_ids`: always empty. Cost is not attributable to any span, so
naming spans here would imply an attribution the data does not support.

`evidence.values`:

```
{
  "cost": 0.412,
  "max_cost": 0.25,
  "overage": 0.162,
  "cost_breakdown": { "llm_cost": 0.398, "tool_cost": 0.014 },
  "span_count": 87
}
```

`cost_breakdown` is passed through from `EvalInstance.metadata.cost_breakdown`
when the seam supplied it and is null otherwise. Its keys are Prefactor's and
are reproduced verbatim without interpretation, since a replacement seam may
provide different ones or none. `span_count` is included because the first
question after an overspend is whether the run was long or the steps were
expensive.

Numbers are compared and reported as they arrive, with no rounding. Formatting
in `details` is fixed to four decimal places for readability, while
`evidence.values` carries the unrounded value.

## Notes

This is the only budget eval in the core pack that depends on data the seam must
be told to fetch. Expect it to skip on a fresh install and treat that skip as a
configuration finding rather than noise.

Deliberately not checked:

1. **Per-span cost.** Not available. See above and `span.md` note 4. The eval
   cannot tell you which step was expensive and will not pretend to.
2. **Tokens.** Available per `span.md` note 5 and not used as a budget anywhere
   in v1. An agent can blow a token count without blowing this budget if its
   model is cheap, and vice versa.
3. **Currency.** No conversion, no unit checking. `max_cost` is assumed to be in
   the same units the account reports. Setting a dollar ceiling against an
   account reporting in something else produces a confidently wrong verdict and
   the library cannot detect it.
4. **Value for money.** A cheap useless run passes. Cost is not quality.
5. **Cost across instances.** Scope is one instance. A thousand instances each
   inside budget can still be an unaffordable day, and aggregate spend is a
   reporting concern rather than an eval.
6. **Cost attribution to a model or vendor.** `cost_breakdown` is echoed as
   evidence, never parsed or asserted on.
