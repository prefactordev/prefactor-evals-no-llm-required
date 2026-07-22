# core.latency_budget

## ID

`core.latency_budget`

## Name

Latency budget

## Requires

1. `EvalInstance.duration_ms` (only when `max_instance_ms` is set)
2. `EvalInstance.spans` (only when `max_span_ms` is set)
3. `EvalSpan.duration_ms`
4. `EvalSpan.id`

Skips when neither `max_span_ms` nor `max_instance_ms` is configured, and skips
when neither half of the check has any measurable input. See below.

## Config

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `max_span_ms` | integer | `null` | Ceiling for any single span's duration. Null disables the span half. |
| `max_instance_ms` | integer | `null` | Ceiling for the whole run's duration. Null disables the instance half. |
| `span_types` | string[] | `[]` | When non-empty, only spans whose `type` is in this list are checked against `max_span_ms`. |
| `ignore_spans` | string[] | `[]` | Span names exempt from `max_span_ms`. Exact, case-sensitive match on `name`. |

Neither ceiling has a default. Acceptable latency is a product decision that
differs by an order of magnitude between a voice agent answering a caller and a
nightly batch workflow, and no number here would be right for both. At least one
ceiling must be set or the eval skips, naming both keys.

Both ceilings may be set together. They are independent conditions and the eval
fails if either fails.

## Pass criteria

All durations come from the derived `duration_ms` fields defined in
`instance.md` note 3 and `span.md`. Both are computed at the seam as
`ended_at - started_at` in milliseconds and are null when either endpoint is
null. This eval never parses timestamps itself and never reads a clock.
Comparison is strictly greater than: a duration exactly equal to its ceiling
passes.

### Span half, when max_span_ms is set

Let `candidates` be the spans remaining after applying `span_types` and
`ignore_spans`.

Partition `candidates` by `duration_ms`:

- **Measured**: `duration_ms` is non-null. Checked against `max_span_ms`.
- **Unmeasured**: `duration_ms` is null, meaning the span has no `started_at` or
  no `ended_at`.

The span half fails when any measured candidate's `duration_ms` is greater than
`max_span_ms`.

Unmeasured spans **never fail this eval and never pass it silently**. They are
counted in `evidence.values.unmeasured_spans` on every result, pass, fail, or
skip, with their IDs in `evidence.values.unmeasured_span_ids`. An open span has
no duration, and inferring one would require a clock read.

Open spans are common and usually not a separate bug. Per `span.md`, a span
belonging to a terminated instance keeps whatever state it last reached, very
often `active` forever, because termination stops the process before anything
closed it. Those spans are unmeasured, are reported as such, and are not read as
latency failures. The termination is the finding and
`core.termination_state` is the eval that reports it.

When `max_span_ms` is set and every candidate is unmeasured, the span half
produces no verdict. If the instance half also produces no verdict, the eval
returns `skip`, with `details` stating that no duration was measurable.

### Instance half, when max_instance_ms is set

The instance half fails when `EvalInstance.duration_ms` is non-null and greater
than `max_instance_ms`.

When `EvalInstance.duration_ms` is null, which happens for any run that has not
finished, the instance half produces no verdict. It is reported as unmeasured
and never treated as zero.

### Verdict

`fail` if either half failed. `pass` if at least one half produced a verdict and
no half failed. `skip` if no half produced a verdict, or if required config is
absent.

## Failure output

`status`: `fail`

`details`: one sentence naming the worse of the two breaches, span first when
both fired, for example:
`Span "generate_report" took 41220 ms, ceiling is 10000 ms.` or
`Instance took 96400 ms, ceiling is 60000 ms.`

`evidence.span_ids`: the IDs of every measured span over `max_span_ms`, sorted
by start time then ID. Empty when only the instance half failed.

`evidence.values`:

```
{
  "max_span_ms": 10000,
  "max_instance_ms": 60000,
  "instance_duration_ms": 96400,
  "instance_over": true,
  "slowest_spans": [
    { "span_id": "01kxye3p2m7ewyfq88k1v5t7n2ra94xc", "name": "generate_report", "type": "tool_call", "duration_ms": 41220 }
  ],
  "over_span_count": 1,
  "measured_spans": 43,
  "unmeasured_spans": 2,
  "unmeasured_span_ids": ["01kxye7r1p7ewyfq44m2d8s6j3vc90xk", "01kxye7r1p7ewyfq51n8e2t7l4wd21ym"]
}
```

`slowest_spans` contains every over-ceiling span, sorted by `duration_ms`
descending then by ID, capped at ten entries with `over_span_count` carrying the
true total. The cap keeps a pathological run from producing a result object
larger than the trace summary.

## Notes

The two halves answer different questions and a pack usually wants both. One
slow tool call inside a fast run and a fast run made long by fifty small steps
are different problems with different fixes, and only running both ceilings
distinguishes them.

Spans overlap. Concurrent spans share start times often enough that
`instance.md` note 4 specifies a stable tiebreak for ordering them. The sum of
span durations is therefore not the instance duration and this eval never
computes that sum. `EvalInstance.duration_ms` is wall clock for the whole run
and is the only correct figure for the instance half.

Deliberately not checked:

1. **Time between spans.** Gaps are not measured. A run that waited nine minutes
   for a human between two fast spans shows up only in the instance half, if at
   all. Queue and idle time are not separable from work time in this data.
2. **Elapsed time of open spans or in-flight runs.** Requires a clock read.
   Reported as unmeasured, never estimated. See above.
3. **Sum of span durations.** Not computed, not comparable to the instance
   duration, and not a meaningful number under concurrency.
4. **Percentiles or trends across instances.** Scope is one instance. Latency
   distributions are a reporting concern.
5. **Where the time went inside a span.** A slow span is one number. Nothing
   here distinguishes model time from network time from tool time.
6. **Whether the latency mattered.** A batch job that took an hour and a voice
   turn that took four seconds are judged only against the ceilings configured
   for them.
