# agent-evals-no-llm

You do not need an LLM to evaluate your agents. Most agent failure is
behavioural: the wrong tool, a silent loop, a half-finished workflow, scope
creep. Behaviour is checkable with code, deterministically, for free.

A set of generic, deterministic health checks that run on any agent with no
configuration. No judge models, no model API key, no token cost, no
subjectivity. Runs in CI and exits non-zero when something is wrong.

There is no model client anywhere in this package and there never will be. LLM
based evals, when they ship, are separate packages (`prefactor-evals-llm`,
`@prefactor/evals-llm`), so this one cannot cost you a token by construction.

## Quickstart

1. **Instrument your agent** with the Prefactor SDK, TypeScript or Python, and
   let it run. You cannot write eval criteria for an agent you cannot see.
2. **Install.**
   ```
   pip install prefactor-evals-no-llm
   ```
3. **Run it.** No pack needed, it uses the standard checks.
   ```
   export PREFACTOR_API_URL=https://your-prefactor-host
   export PREFACTOR_API_TOKEN=your-admin-api-token
   prefactor-evals-no-llm run --agent <agent-id> --since 7d
   ```

You get a terminal scorecard, a JSON report at `./agent-evals-report.json`, and
a non-zero exit code if anything failed. Pass `--no-fail-exit` to disable that.

Note the token: the read endpoints need an admin API token. The SDK ingestion
token you used for instrumentation returns 401 here.

### Publishing scores back to Prefactor

By default a run only reads. Add `--publish` to write each run's score into its
instance in the Prefactor Quality tab, so the score lives next to the run
instead of only in your terminal.

```
prefactor-evals-no-llm run --agent <id> --since 7d --publish
```

This is opt in on purpose: a plain run never writes to your account. When it
does write, it writes only under a single key it owns, so any quality data your
own app records on the same instance is read, preserved, and left untouched. It
scores finished runs only, never a run still in flight. Eval results are never
written back as spans: a span is what the agent did, a score is a judgment made
afterwards, and mixing them would let the tool grade its own output.

## The checks

Every check is generic. It measures agent behaviour without knowing or caring
what the agent does, so the same set works on any agent unchanged. The standard
checks run on defaults with no configuration:

| Check | What it catches |
| --- | --- |
| `core.errors` | Any step that failed |
| `core.loop_detection` | The agent repeating the same action, stuck |
| `core.redundant_tool_calls` | The same call twice in a row, no progress |
| `core.termination_state` | Runs that ended cancelled or failed, not completed |
| `core.latency_budget` | A single step hanging, past a generous default |
| `core.efficiency` | A run that took far more steps than this agent's own normal |
| `core.conversation_length` | A run that took far more turns than usual to finish |

`efficiency` and `conversation_length` are self calibrating: each run is judged
against the median of the agent's own recent runs, not against a fixed number
that would be arguable for any given agent. The scorecard also reports a
**resolution rate**, the share of runs that reached completion.

A few optional checks are still generic but need one piece of information only
you can supply, and are off by default: `core.forbidden_actions`,
`core.tool_arg_schema`, `core.output_schema`, `core.cost_budget`,
`core.escalation_rule`. See [spec/packs/advanced.yaml](spec/packs/advanced.yaml).

## How it works

Prefactor records an **instance** per agent run, containing **spans**, one per
step. This library fetches them, normalizes them into a documented shape
(`schema/v1`), and runs every eval in your pack over every instance.

Each eval is a pure function over that shape. No network calls, no clock reads,
no randomness, no model calls. The same traces produce byte identical results
every time, which is what makes these safe to gate a build on.

Results are `pass`, `fail`, or `skip`. **Skip is not a pass.** A check skips
when a field it needs is missing, or when a self calibrating check has too few
runs to establish the agent's normal, and skips are counted separately from
passes everywhere. A green scorecard that checked nothing is the worst thing
this library could hand you, so it will not.

The standard checks almost never skip; that is the point of the set and go
design. The optional checks skip until configured, and each names the exact key
that would enable it.

The full field by field definitions, and one specification file per eval, are
in [`spec/`](spec/). The spec is the source of truth, not the code.

## Standalone use

All Prefactor coupling lives in exactly one file, `source.py`. Nothing else
imports it. Replace that file with your own loader
that emits `schema/v1` shapes and every eval keeps working.

`fixtures.py` in the Python package is a working example of exactly that: a
second source, backed by JSON files, that has never heard of Prefactor and
drives the same runner. The test suite uses it.

We think pulling real production traces from Prefactor is the whole point, and
the free tier is there. But the eval definitions are the gift, and they are
yours either way.

## Roadmap

**LLM-as-judge evals**, as a separate package, framed as the last resort for
what code cannot check. Tone, faithfulness, and whether an answer is actually
correct are real questions and no amount of deterministic checking answers
them.

When they ship they will follow the structure that makes judges trustworthy:
a judge scores one narrow property, not overall quality. It sees a rubric with
concrete anchors, not a vague scale. It is calibrated against human labelled
examples before you trust a number from it, and re-calibrated when the model
behind it changes. It reports its own disagreement rate. A judge you have not
calibrated is a vibe with a decimal point.

Everything code can check should be checked with code first. That is this
package.

## Contributing

Read [`spec/README.md`](spec/README.md) first. The spec file comes before the
implementation, both languages implement the same spec, and an eval that exists
in one language and not the other is a bug.

House rules: no em dashes anywhere, no LLM calls anywhere in the tooling, synthetic and fictional examples only.

## License

Apache 2.0. See [LICENSE](LICENSE).
