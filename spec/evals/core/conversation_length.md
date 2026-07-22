# core.conversation_length

## ID

`core.conversation_length`

## Name

Conversation length

## Requires

1. `EvalInstance.spans`
2. `EvalSpan.type` (spans typed `output` are counted as turns by default)
3. `EvalContext.instances` (this is a cross instance check)

Skips when the run has no `output` spans and no `turn_span_names` is configured,
because it then does not look like a conversation. Also skips when fewer than 5
runs in the batch reached completion.

## Config

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `tolerance` | number | `3.0` | How many times the median turn count a run may reach before it is flagged. |
| `floor` | integer | `10` | A run below this many turns is never flagged. |
| `turn_span_names` | string[] | `[]` | Span names or schema names that count as a turn. Defaults to spans typed `output`. |

## How it calibrates

A turn is a message back to the person. By default those are spans typed
`output`; `turn_span_names` overrides that for instrumentation that names them
differently. The baseline is the median turn count across the completed runs in
the batch, and the ceiling is `max(median * tolerance, floor)`.

Self calibrating for the same reason as efficiency: a conversational agent that
needs far more turns than it usually does to finish is going in circles, and the
agent's own median is a fairer bar than a fixed number.

## Pass criteria

Passes when the run's turn count is at or below the ceiling, or when the check
skips.

## Determinism

Deterministic: a median over a fixed set, no clock read.
