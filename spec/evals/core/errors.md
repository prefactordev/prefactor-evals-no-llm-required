# core.errors

## ID

`core.errors`

## Name

Errors

## Requires

1. `EvalInstance.spans`
2. `EvalSpan.state`
3. `EvalSpan.error`
4. `EvalSpan.name`

Never skips: the failed state exists on every span, so this check applies to any
agent with no configuration.

## Config

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `max_failures` | integer | `0` | Number of failed steps tolerated before the check fails. Strict by default. |

## Pass criteria

Passes when the count of spans in state `failed` is at or below `max_failures`.
The default tolerance is zero, so any failed step fails the check.

## Failure output

`details` names the number of failed steps and quotes the first failure's name
and error message. `evidence.span_ids` lists every failed span.

## Why it exists

A failed step is the most universal health signal there is, and the one thing a
person wants first: did anything the agent tried actually break. It needs no
configuration on any agent, because `failed` is a state the platform records,
not a judgement the user has to define.
