# prefactor-evals-no-llm

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

This is the Python package, in `packages/python`. There is a second
implementation in TypeScript in `packages/typescript` with the same checks, the
same seam, live mode, and CLI; the two are held byte identical by the
conformance suite. The HTML dashboard is the one piece still only here.

## Install

Not published to a package index yet. Install from the repo subdirectory:

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
export PREFACTOR_API_TOKEN=your-api-token

prefactor-evals-no-llm agents                          # lists your agents and their ids
prefactor-evals-no-llm run --agent <agent-id> --since 7d
```

There is no login step: the CLI reads that environment variable, or a file you
point it at with `--env-file .env`. The token is an **account-wide API token**,
created in the Prefactor Admin UI under Account, API Tokens, Create API token.
The SDK ingestion token you instrument with returns 401 on the read endpoints.
The API host defaults to `https://app.prefactorai.com`; set `PREFACTOR_API_URL`
only if your account lives somewhere else.

No pack file needed: it uses the standard checks. You get a terminal scorecard,
a JSON report at `./agent-evals-report.json`, and a non-zero exit code if
anything failed, so it works as a CI gate. `--no-fail-exit` turns that off.

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
`core.escalation_rule`. Turn them on with `--pack advanced`, which ships inside
the package, then fill in the values it names. The documented copy is
[spec/packs/advanced.yaml](../../spec/packs/advanced.yaml).

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

Prefactor itself never stops a run on its own: risk classification there is a
label for human review, and enforcement stays with your team. This command is
that enforcement, made explicit. It acts through the same terminate endpoint a
human operator would use, only when you have named the check, and the breach
reason is recorded on the run.

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
preserved. It scores finished runs only.

This library never writes activity spans: a span is what the agent did, a
score is a judgement made afterwards. Publishing sets the instance's quality
payload, and Prefactor itself records that change as a quality span
(`prefactor:quality`) on the instance. One publish, one span, and it counts
toward span volume like any other, so know that before publishing a large
backfill.

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

## The spec, and the TypeScript port

The behaviour of every check is pinned in [`spec/`](../../spec), one file per
check plus the normalized schema every check reads. The spec is the source of
truth; where an implementation and the spec disagree, the implementation is
wrong.

The second implementation in TypeScript lives alongside this one in
[`packages/typescript`](../typescript). The two are held to the same results by
a conformance suite that runs both over the same fixtures and diffs every
result, status, message, evidence and values. Any divergence fails CI.

## Contributing

Read [`spec/README.md`](../../spec/README.md) first. The spec file comes before
the implementation, and tests assert the two never drift.

House rules: no em dashes or smart quotes anywhere, no LLM calls anywhere
including in tooling, synthetic and fictional examples only.

## License

Apache 2.0. See [LICENSE](LICENSE).
