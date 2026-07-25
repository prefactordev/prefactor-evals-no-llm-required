# The spec

This directory is the source of truth. Both the Python and TypeScript packages
implement what is written here. Where an implementation and this directory
disagree, the implementation is wrong.

## Why a spec directory exists

One spec, two implementations. IDs, semantics, config parameters, and pass
criteria are identical across languages. Only the API surface is idiomatic to
each. An eval that exists in one language and not the other is a bug, and an
eval whose two implementations disagree on a fixture is a bug in whichever one
departed from this directory.

It also makes the library portable. Someone implementing these evals in Go
against their own trace store needs this directory and nothing else.

## Layout

```
spec/
  README.md          this file
  schema/v1/
    instance.md      normalized EvalInstance, field by field
    span.md          normalized EvalSpan, field by field
    mapping.md       Prefactor API to normalized, and the known gaps
  shared/
    json-schema-subset.md   the bundled JSON Schema validator, both languages
  evals/
    core/            12 evals: 7 standard, zero config; 5 optional
  packs/
    *.yaml           pack definitions and default config
```

12 evals total. Every one is generic: it measures how an agent behaved, not
what the agent was for, so the same set runs on any agent unchanged. There are
deliberately no domain packs; a check that only makes sense for one kind of
agent does not belong here.

## Eval file format

One markdown file per eval at `spec/evals/core/<eval_id>.md`, with exactly
these seven sections, in this order.

### 1. ID

`snake_case`, namespaced by pack, globally unique, stable forever. Example:
`core.loop_detection`. IDs appear in user config files and in stored reports.
Renaming one is a breaking change and a semver major.

### 2. Name

Human readable, sentence case, no trailing full stop.

### 3. Requires

Every `EvalInstance` and `EvalSpan` field the eval reads. If a required field is
absent or null on an instance, the eval returns `skip` with a reason. This
section is what makes skip behaviour predictable rather than incidental.

### 4. Config

Every parameter, with type and default. `required` means the eval returns
`skip` when the user has not supplied it. Config with no safe default must be
required rather than given an invented one: a fabricated default threshold
produces confident results about a number nobody chose.

### 5. Pass criteria

A precise, deterministic statement. Written so that two people implementing it
independently produce the same behaviour on the same fixture. No hedging words.

### 6. Failure output

What `details` says and what `evidence` contains. Always includes the offending
span IDs where the concept of an offending span applies.

### 7. Notes

Edge cases, and an explicit statement of what the eval deliberately does not
check. The second half matters more than the first. Every eval here is a
deterministic check with a hard boundary, and users who mistake a narrow check
for a broad guarantee are the main way a library like this misleads people.

## Result object

Identical semantics in both languages.

```
{
  eval_id: string,
  instance_id: string,
  status: "pass" | "fail" | "skip",
  details: string,
  evidence: { span_ids: string[], values: object }
}
```

`details` is one sentence, human readable, no trailing newline.

`evidence.span_ids` is always present, empty list when not applicable.

`evidence.values` holds the numbers or strings that drove the verdict, so a
failure can be understood without re-running anything.

### Skip is not a pass

A skip means the eval could not run: a required field was missing, or required
config was absent. Scorecards report pass, fail, and skip as three separate
counts, never two. A suite that silently skips everything must be visible as
such, because a green scorecard that checked nothing is the worst output this
library could produce.

Every skip carries a reason in `details`, naming the specific missing field or
config key.

### Skips must be actionable, not just counted

The seven standard evals run with no configuration. The five optional ones
require config, because concepts like forbidden action, cost budget, output
shape, and escalation trigger do not exist in a trace and cannot be guessed. A
fresh install running the advanced pack therefore skips those five until they
are filled in.

That is correct, but a scorecard reporting "5 skipped" and stopping there is
useless. The runner must, for every skipped eval, print the exact config key
that would enable it and the pack file it belongs in. A user should be able to
go from first run to a fully configured pack by reading the output, without
opening this directory.

The scorecard also reports a coverage line: how many evals actually ran, out of
how many were requested. Coverage going up is the first week of using this
library, and it should be visible as progress rather than buried.

### Determinism

Every eval is a pure function of one `EvalInstance` (or, for the two
self calibrating evals, of the full fetched set). No network calls, no clock
reads, no randomness, no filesystem access, no LLM calls of any kind. Running
the same eval over the same fixture twice must produce byte-identical results,
including the ordering of `evidence.span_ids`, which is always sorted by span
start time then ID.

## Self calibrating evals

Two evals read across instances rather than within one: `core.efficiency` and
`core.conversation_length`. Each judges a run against the median of the agent's
own completed runs in the fetched set, rather than a fixed number that would be
arguable for any given agent. They skip, saying so, until the set holds enough
completed runs to make a baseline, and they never count a broken run toward
what normal means.

## Config resolution

Three layers, later wins:

1. Eval defaults, as written in the eval's Config section.
2. Pack YAML defaults.
3. User overrides in their copy of the pack YAML.

There is no environment variable layer for eval config. Thresholds belong in a
file that gets committed and reviewed, not in shell state.

## Adding an eval

1. Write the spec file first. It is not optional and it is not documentation
   written after the fact.
2. Add the ID to the relevant pack YAML.
3. Implement in both languages.
4. Add passing, failing, and skipping fixtures to `examples/synthetic-traces/`.
5. Confirm the conformance suite sees identical results from both.

## House rules

1. No em dashes anywhere, including in these spec files. Commas, colons, or
   full stops.
2. No LLM calls anywhere in v1, including in tooling and tests.
3. Synthetic examples only. Fictional businesses. No customer names, no design
   partner names, no real trace data.
4. No hype. No exclamation marks.
