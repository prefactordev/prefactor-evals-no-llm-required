# core.efficiency

## ID

`core.efficiency`

## Name

Efficiency

## Requires

1. `EvalInstance.spans`
2. `EvalInstance.state`
3. `EvalContext.instances` (this is a cross instance check)

Skips when fewer than 5 runs in the batch reached completion, because a baseline
drawn from too few runs is noise.

## Config

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `tolerance` | number | `3.0` | How many times the median span count a run may reach before it is flagged. |
| `floor` | integer | `12` | A run below this many spans is never flagged, however far above the median it sits. |

## How it calibrates

The baseline is the median span count across the completed runs in the batch.
The ceiling is `max(median * tolerance, floor)`. A run is flagged when its span
count exceeds that ceiling.

The check is self calibrating on purpose. A fixed industry number for "how many
steps a task should take" is always arguable for a given agent; the agent's own
median is not. This also catches the agent getting worse over time, not merely
differing from an external ideal.

Only completed runs feed the baseline: a run that failed or was cancelled took
as many steps as it did because it broke, not because that is how much work the
task needs, so including it would inflate what counts as normal.

## Pass criteria

Passes when the run's span count is at or below the ceiling, or when the check
skips for too small a sample.

## Determinism

The median of a fixed set of runs is the same every time, so results are
reproducible. No clock is read.
