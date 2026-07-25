# prefactor-evals-no-llm (TypeScript)

You do not need an LLM to evaluate your agents. Most agent failure is
behavioural: a silent loop, a step that failed, a run that never finished, a
task that took four times the work it usually does. Behaviour is checkable with
code, deterministically, for free.

This is the TypeScript implementation of the deterministic agent checks. No judge
models, no API key for a model, no token cost, no subjectivity. The same traces
always produce the same results.

There is no model client anywhere in this package and there never will be.

## Status

This is the TypeScript package, in `packages/typescript` of the
[prefactor-open-evals](https://github.com/prefactordev/prefactor-open-evals)
monorepo. It ships the twelve core checks, the runner, the Prefactor seam
(fetching traces straight from an instance), live per-span mode, and the CLI,
proven conformant against the Python implementation over a shared fixture set.
The HTML dashboard is the one piece still only in the Python package.

## Install

Not published to npm yet. Clone the monorepo and build this package:

```
git clone https://github.com/prefactordev/prefactor-open-evals
cd prefactor-open-evals/packages/typescript
npm install && npm run build
```

Node 18 or newer to use the library and CLI. Running the test suite needs Node
22.6 or newer, which is what its type stripping flag first appears in.

## Run it against Prefactor

```
export PREFACTOR_API_TOKEN=your-api-token

node dist/cli.js agents                          # lists your agents and their ids
node dist/cli.js run --agent <agent-id> --since 7d
```

`npm link` puts the same CLI on your path as `prefactor-evals-no-llm`, with the
same commands and flags as the Python build.

The token is an **account-wide API token**, created in the Prefactor Admin UI
under Account, API Tokens, Create API token. The SDK ingestion token you
instrument with returns 401 on the read endpoints. The API host defaults to
`https://app.prefactorai.com`; set `PREFACTOR_API_URL` only if your account
lives somewhere else.

## Use it as a library

Load traces that match the normalized `schema/v1` shape, run a pack over them,
read the results back:

```ts
import { loadInstances, run, STANDARD_PACK } from "@prefactor/open-evals";

// Any source that emits schema/v1 instances. loadInstances reads a JSON file.
const instances = loadInstances("traces.json", STANDARD_PACK.span_type_map);

const report = run(STANDARD_PACK, instances);

for (const result of report.results) {
  console.log(result.eval_id, result.status, result.details);
}

const [finished, total, rate] = report.resolution;
console.log(`resolution ${rate} (${finished}/${total})`);
```

`report.counts` breaks down pass, fail, and skip, and `report.coverage` reports
how many checks ran against how many were requested. **Skip is not a pass**, and
it is counted separately.

To run the optional checks, load the advanced pack instead of `STANDARD_PACK`
with `loadPack("advanced")` (bundled, like the CLI's `--pack advanced`), or
point `loadPack` at your own JSON pack file, and supply the config each
optional check needs.

## The checks

Every check is generic. It measures how the agent behaved, not what it was
doing, so the same set works on a support bot, a voice agent or a coding agent
unchanged.

| Check | What it catches |
| --- | --- |
| `core.errors` | Any step that failed |
| `core.loop_detection` | The agent repeating the same action, stuck |
| `core.redundant_tool_calls` | The same call twice in a row, no progress |
| `core.termination_state` | Runs that ended cancelled or failed, not completed |
| `core.latency_budget` | A single step hanging, past a generous default |
| `core.efficiency` | A run that took far more steps than this agent's own normal |
| `core.conversation_length` | A run that took far more turns than usual to finish |

Five optional checks are still generic but need one value only you can supply,
so they are off in the standard pack: `core.forbidden_actions`,
`core.tool_arg_schema`, `core.output_schema`, `core.cost_budget`,
`core.escalation_rule`.

## Held to the same result as Python

The behaviour of every check is pinned in a shared spec, and a conformance suite
runs this package and the Python one over the same fixtures, diffing every
result, status, message, evidence and value. An eval that behaves differently in
the two languages is a bug in whichever one departed from the spec.

## License

Apache 2.0. See [LICENSE](LICENSE).
