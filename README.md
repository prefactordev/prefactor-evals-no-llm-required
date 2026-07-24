# prefactor-open-evals

You do not need an LLM to evaluate your agents. Most agent failure is
behavioural: a silent loop, a step that failed, a run that never finished, a
task that took four times the work it usually does. Behaviour is checkable with
code, deterministically, for free.

A generic set of health checks that run on any agent with no configuration. No
judge models, no API key for a model, no token cost, no subjectivity. The same
traces always produce the same results, which is what makes them safe to gate a
build on.

There is no model client anywhere in this package and there never will be. LLM
based evals, if they ship, are a separate package, so this one cannot cost you a
token by construction.

## Install

From this repo. Nothing is published to a package index.

```
pip install "git+https://github.com/prefactordev/prefactor-open-evals.git#subdirectory=packages/python"
```

Or clone and install in place:

```
git clone https://github.com/prefactordev/prefactor-open-evals
cd prefactor-open-evals/packages/python
pip install -e .
```

Python 3.10 or newer.

## Run it

```
export PREFACTOR_API_URL=https://your-prefactor-host
export PREFACTOR_API_TOKEN=your-admin-api-token

prefactor-evals-no-llm run --agent <agent-id> --since 7d
```

No pack file needed: it uses the standard checks. You get a terminal scorecard,
a JSON report at `./agent-evals-report.json`, and a non-zero exit code if
anything failed, so it works as a CI gate. `--no-fail-exit` turns that off.

The read endpoints need an **admin** API token. The SDK ingestion token you
instrument with returns 401 here.

## The checks

Every check is generic. It measures how the agent behaved, not what it was
doing, so the same set works on a support bot, a voice agent or a coding agent
unchanged. All of these run on defaults with no configuration:

| Check | What it catches |
| --- | --- |
| `core.errors` | Any step that failed |
| `core.loop_detection` | The agent repeating the same action, stuck |
| `core.redundant_tool_calls` | The same call twice in a row, no progress |
| `core.termination_state` | Runs that ended cancelled or failed, not completed |
| `core.latency_budget` | A single step hanging, past a generous default |
| `core.efficiency` | A run that took far more steps than this agent's own normal |
| `core.conversation_length` | A run that took far more turns than usual to finish |

`efficiency` and `conversation_length` are **self calibrating**: each run is
judged against the median of the agent's own recent runs, not a fixed number
that would be arguable for any given agent. They catch the agent getting worse,
not merely differing from an external ideal.

The scorecard also reports a **resolution rate**, the share of runs that
actually reached completion.

A few optional checks are still generic but need one piece of information only
you can supply, so they are off by default: `core.forbidden_actions`,
`core.tool_arg_schema`, `core.output_schema`, `core.cost_budget`,
`core.escalation_rule`. See [spec/packs/advanced.yaml](spec/packs/advanced.yaml).

## Live mode: check a run while it is running, and stop it

The checks above grade a finished run. Live mode grades a run **span by span**
while it is still going, so a loop trips on the offending call rather than after
the damage is done.

```
prefactor-evals-no-llm watch --agent <agent-id> --terminate loops,errors
```

That watches the agent's active runs and **stops** any that loop or error. On a
breach it calls Prefactor's terminate endpoint with the breach as the reason,
and the agent halts on its next span.

Policy is per check: `off`, `warn`, or `terminate`. **Terminate is never the
default.** Run `watch` with no `--terminate` and it only warns, touching
nothing. You name the checks allowed to stop a run, deliberately, because
killing a good run on a false positive is the one costly mistake.

Live checks: `loops`, `redundant_calls`, `errors`, `latency`, and `runaway`
(the run has already passed the agent's normal step count while still going).

## A browsable dashboard

```
prefactor-evals-no-llm run --agent <agent-id> --html ./quality --open
```

Writes a self contained HTML page per run plus an index across them, newest
first, each linking to the spans behind its failures. No assets, no network,
safe to share. On Windows, `dashboard.bat <agent-id>` does the whole thing and
opens it.

## Publishing scores back to Prefactor

```
prefactor-evals-no-llm run --agent <agent-id> --publish
```

Off by default: a plain run only reads. When it does write, it writes only the
fields it owns, so quality data your own app records on the same run is
preserved. It scores finished runs only. Results are never written back as
spans: a span is what the agent did, a score is a judgement made afterwards.

For the scores to render, the agent's schema version needs a quality schema.
`prefactor-evals-no-llm declare-quality-schema --agent <id>` does it, or
`--print` gives you the JSON to paste into your agent's own registration.
Rendering binds at registration, so it affects runs registered after it, not
existing ones.

## How it works

Prefactor records an **instance** per agent run, containing **spans**, one per
step. This library fetches them, normalizes them into a documented shape
(`schema/v1`), and runs the checks over them.

Each check is a pure function over that shape. No network calls, no clock reads,
no randomness, no model calls. The same traces produce byte identical results
every time. That is tested, not assumed.

Results are `pass`, `fail`, or `skip`. **Skip is not a pass.** Skips are counted
separately everywhere. A green scorecard that checked nothing is the worst thing
this library could hand you, so it will not. The standard checks almost never
skip; the optional ones skip until configured and each names the exact key that
would enable it.

## Using it without Prefactor

All Prefactor coupling lives in one file, `source.py`. Nothing else imports it.
Replace it with your own loader that emits the documented `schema/v1` shapes and
every check keeps working.

`fixtures.py` is a working example of exactly that: a second source, backed by
JSON files, that has never heard of Prefactor and drives the same runner. The
whole test suite runs on it, with no account and no network.

## Config is treated as hostile

Config is hand written, so wrong values are normal rather than exceptional. A
bad setting produces a skip naming the key and the type expected, never a crash
and never a silent pass. Patterns that could hang a run are refused before they
compile, and a schema with a remote `$ref` is refused rather than fetched.

## Contributing

Read [`spec/README.md`](spec/README.md) first. The spec file comes before the
implementation: `spec/` is the source of truth, one file per check plus the
normalized schema every check reads, and tests assert the two never drift.

House rules: no em dashes or smart quotes anywhere, no LLM calls anywhere
including in tooling, synthetic and fictional examples only.

## Two implementations, held to the same result

`packages/python` and `packages/typescript` both implement the spec. The point
is not that two languages exist, it is that they agree: `conformance/` runs both
over the same fixtures and diffs every result, status, message, evidence and
values. Any divergence fails CI.

```
python conformance/compare.py
```

Python is the complete implementation: all checks, the Prefactor seam, live
mode, the dashboard and the CLI. TypeScript currently implements the seven
standard checks and the runner, and is proven conformant on those. It does not
yet have the seam, live mode, the optional checks or a CLI, so today it is a
library you drive from your own code against trace files.

## License

Apache 2.0. See [LICENSE](LICENSE).
