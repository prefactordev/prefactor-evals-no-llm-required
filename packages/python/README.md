# prefactor-evals-no-llm

Deterministic evals for AI agents. No model calls, no token cost, no
subjectivity. The same traces always produce the same results, which is what
makes them safe to gate a build on.

Set and go: point it at any agent and get a health read with no configuration.
Every check is generic, it measures agent behaviour without knowing or caring
what the agent does, so the same checks work on a support bot, a voice agent, or
a coding agent unchanged.

There is no model client anywhere in this package and there never will be. LLM
based evals, when they ship, are a separate package, so this one cannot cost you
a token by construction.

```
pip install prefactor-evals-no-llm
```

## What it checks

The standard checks run on defaults, no setup:

| Check | What it catches |
| --- | --- |
| **Errors** | Any step that failed |
| **Loops** | The agent repeating the same action, stuck |
| **Redundant calls** | The same call twice in a row with no progress |
| **Termination** | Runs that ended cancelled or failed instead of completing |
| **Latency** | A single step hanging, past a generous default |
| **Efficiency** | A run that took far more steps than this agent normally needs |
| **Conversation length** | A run that took far more turns than usual to finish |

The last two calibrate to the agent's own normal, the median of its recent runs,
rather than to a fixed number that would be arguable for any given agent. They
catch the agent getting worse, not merely differing from an external ideal.

Alongside the checks, the scorecard reports a **resolution rate**: the share of
runs that actually reached completion. It is often the single most telling
number.

There are also a few optional checks that are still generic but need one piece
of information only you can supply, forbidden actions, tool argument schemas,
output schemas, cost budget, escalation rules. They are off by default. See
`spec/packs/advanced.yaml`.

## Running it

```
export PREFACTOR_API_URL=https://your-prefactor-host
export PREFACTOR_API_TOKEN=your-admin-api-token

prefactor-evals-no-llm run --agent <agent-id> --since 7d
```

No pack needed: it uses the standard checks. You get a terminal scorecard, a
JSON report, and a non-zero exit code if anything failed. Add `--html ./quality`
for a browsable dashboard, one page per run plus an index. Add `--publish` to
write each run's score back into its Prefactor Quality tab.

The read endpoints need an admin API token. The SDK ingestion token used for
instrumentation returns 401.

## What a skip means

Almost nothing skips on the standard checks; that is the point. When one does,
it names exactly what it needs, and the scorecard reports coverage separately
from results. The self calibrating checks skip when there are too few runs in
the batch to establish the agent's normal, so give them a handful of runs to
work from.

## Using it without Prefactor

All Prefactor coupling lives in one file, `source.py`. Nothing else imports it.
Replace that file with your own loader that emits the documented `schema/v1`
shapes and every check keeps working. `fixtures.py` is a working example: a
second source, backed by JSON files, that has never heard of Prefactor and
drives the same runner. The test suite uses it.

## Config is treated as hostile

Config is hand written, so wrong values are normal rather than exceptional. A
bad setting produces a skip naming the key and the expected type, never a crash
and never a silent pass. Patterns that could hang a run are refused, and a
schema with a remote `$ref` is refused rather than fetched.

## License

Apache 2.0. Source, and one specification file per check, at
https://github.com/prefactordev/prefactor-open-evals
