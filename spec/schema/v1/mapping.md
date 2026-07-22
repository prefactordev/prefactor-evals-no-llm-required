# schema/v1: Prefactor to normalized mapping

How the seam turns Prefactor API records into `EvalInstance` and `EvalSpan`.
Everything here was derived from the installed SDKs and the captured server
contract, not from guesswork. Sources are named at the bottom.

## The finding that shapes this whole file

**Both Prefactor SDKs are write-only.** `@prefactor/core` 0.4.3 and
`prefactor-core` 0.2.0 (Python) expose span and instance *creation* and nothing
else. Neither ships a list call, a get call, a pagination helper, or any GET
request at all. The Python transport issues only POSTs.

Build spec rule 5 says the eval packages are thin layers over the SDKs and must
not reimplement fetch. For the read path that is not possible, because the read
path does not exist in the SDKs. The seam therefore issues its own HTTP GETs
against the documented endpoints below.

This is a deviation from rule 5 and is flagged for review rather than quietly
absorbed. It is contained: the seam is one file per package, which is exactly
where build spec rule 3 says all Prefactor coupling belongs, so the blast
radius is unchanged. What changes is that the file contains an HTTP client
rather than an SDK import.

Two consequences worth deciding on before Phase 3:

1. **Token type.** The spans list endpoint requires an admin API token. The SDK
   ingestion token returns 401. Build spec rule 5 assumes evals reuse the same
   `PREFACTOR_API_TOKEN` the user already set for instrumentation. That
   assumption does not hold for reads. The seam reads `PREFACTOR_API_TOKEN` and
   documents the token type requirement, and the error message on 401 says so
   explicitly rather than reporting a generic auth failure.
2. **Base URL.** The SDK has no env fallback for the API URL, and the two
   observed defaults on this machine disagree (`https://app.prefactorai.com`
   and `https://api.prefactor.ai`). The seam reads `PREFACTOR_API_URL` with no
   default and fails loudly if unset. Guessing a host would produce confusing
   401s against the wrong service.

## Endpoints the seam calls

| Purpose | Call |
| --- | --- |
| List instances | `GET /api/v1/agent_instance?agent_id=...` |
| Instance detail | `GET /api/v1/agent_instance/{id}` |
| List spans | `GET /api/v1/agent_spans?agent_instance_id=...` |

**Send no `include_*` query parameters.** Verified live: `cost_breakdown`,
`span_counts`, `span_schema_counts`, `risk_score`, `quality_payload`, and
`quality_summary` are returned on the detail endpoint unconditionally, so
`include_costs` and `include_counts` are no-ops. `include_risk_score=true`
returns HTTP 500. The seam must never send it. This was a known defect a month
before schema/v1 was written and it is still live, so treat it as the standing
behaviour rather than a transient outage.

Auth is `Authorization: Bearer {token}` on all three.

Pagination on the list endpoints uses the structured form. The bare
`sorting=field` spelling is rejected by the server:

```
pagination[offset]=0&pagination[page_size]=100&sorting[field]=started_at&sorting[direction]=desc
```

Responses carry `pagination.item_count` and `pagination.next_page_offset`. The
seam pages until exhausted or until the caller's `limit` is met.

**Server-side filtering is close to nonexistent.** The instance list accepts
`agent_id` and `agent_version_id` only. It has no time window parameter, and it
ignores a status parameter if you send one. So the seam applies `since`,
`until`, `state`, and `environment` filters client-side after fetching, and
`limit` is applied last. This makes a wide `since` window expensive, which is
documented in the README rather than hidden. The span list does accept
`start_time` and `end_time`, but the seam does not use them: filtering spans by
time inside an instance would hand evals a partial run, and a partial run
produces confidently wrong results for loop detection, sequencing, and every
budget eval.

Instance detail is a second call per instance and is only made when the pack
contains an eval that needs `cost` (`core.cost_budget`). Otherwise the seam
uses list summaries alone. Fetching cost for every instance when nothing reads
it would multiply the request count for no reason.

Verified live field lists, 20 Jul 2026. Instance list summaries return:
`id`, `account_id`, `agent_id`, `agent_version_id`, `environment_id`,
`started_at`, `finished_at`, `inserted_at`, `updated_at`, `status`,
`termination_reason`, `purpose`, `risk_score`, `type`.

Note two corrections against the older captured contract: `purpose` is present
and `peak_classification` is not. `purpose` is one of `live`, `smoke_test`, or
`eval`, and it is the single most useful filter this library has, because
running evals over your own eval and smoke-test runs measures the harness
rather than the agent. The seam exposes it as a first-class filter and the
runner defaults to `purpose == "live"`.

## EvalInstance mapping

| Normalized | Source | Notes |
| --- | --- | --- |
| `id` | `summaries[].id` | |
| `agent_id` | `summaries[].agent_id` | |
| `agent_version` | `summaries[].agent_version_id` | Opaque ID, not a version string. |
| `environment` | `summaries[].environment_id` | Opaque ID. |
| `started_at` | `summaries[].started_at` | ISO 8601, parsed at the seam. |
| `ended_at` | `summaries[].finished_at` | Note the name change: `finished_at` to `ended_at`. |
| `state` | `summaries[].status` | Verbatim, lowercase. |
| `duration_ms` | derived | `ended_at - started_at`. |
| `spans` | span list call | Sorted per `instance.md` note 4. |
| `input` | derived from spans | Two rules, see `instance.md` note 5. |
| `output` | derived from spans | Two rules, see `instance.md` note 5. |
| `cost` | `details.cost_breakdown.total_cost` | Detail call only. Null otherwise. |
| `metadata` | assembled | Keys listed in `instance.md` note 7. |

Deliberately not mapped: `account_id`, `termination_reason`, `purpose`,
`inserted_at`, `updated_at`, `span_counts`, `span_schema_counts`,
`quality_payload`, `quality_summary`, and `risk_score` all land in `metadata`
rather than becoming top-level fields. `purpose` additionally drives a seam
filter, as described above, but it is still Prefactor-specific and does not
earn a place in the normalized shape. They are Prefactor-specific and a replacement seam cannot be
expected to supply them. Keeping the top level source-agnostic is the point of
having a normalized schema at all.

`risk_score` is passed through untouched and **no v1 eval reads it**. It is a
Prefactor scoring product, not a deterministic check, and an eval that consumed
it would be neither reproducible outside Prefactor nor code-checkable. Noted
because its presence in the payload invites exactly that mistake.

## EvalSpan mapping

The span list returns a record whose interesting content is nested one level
down inside `payload`, an envelope the SDK writes. Both levels are needed.

Top level of `summaries[]`, verified live over 119 spans: `id`, `account_id`,
`agent_id`, `agent_instance_id`, `parent_span_id`, `schema_name`,
`schema_title`, `status`, `started_at`, `finished_at`, `payload`,
`result_payload`, `summary`, `risk_level`, `risk_score`, `sensitive_encoding`,
`payload_byte_size_estimate`, `type`.

Inside `payload`, observed union: `span_id`, `trace_id`, `name`, `status`,
`inputs`, `outputs`, `token_usage`. The SDKs also write `metadata` and `tags`,
and `error` on failure, but none appeared in live ai-sdk traces.

Two consequences. `EvalSpan.metadata` is empty far more often than the SDK
source suggests, so an eval keying on span metadata will skip on most real
traces and should not be written without a fallback. And `sensitive_encoding`
is a **top-level span field**, not a payload field, which is what makes the
unwrapping rule in known gap 6 implementable: the seam can tell before parsing
whether values are wrapped.

| Normalized | Source | Notes |
| --- | --- | --- |
| `id` | `summaries[].id` | The server ID, not `payload.span_id`. See below. |
| `parent_id` | `summaries[].parent_span_id` | |
| `instance_id` | `summaries[].agent_instance_id` | |
| `schema_name` | `summaries[].schema_name` | Raw, preserved. |
| `type` | derived from `schema_name` | Table in `span.md` note 2. |
| `name` | `payload.name`, else `schema_name` | |
| `input` | `payload.inputs` | Null if absent. |
| `output` | `result_payload`, else `payload.outputs` | |
| `state` | `summaries[].status` | |
| `started_at` | `summaries[].started_at` | |
| `ended_at` | `summaries[].finished_at` | |
| `duration_ms` | derived | |
| `cost` | none | Always null. See `span.md` note 4. |
| `tokens` | `payload.token_usage.*` | Renamed, see `span.md` note 5. |
| `error` | `payload.error.*` | `error_type` becomes `type`. |
| `metadata` | `payload.metadata` | Empty record if absent. |

**Two span IDs exist and they are not the same.** `summaries[].id` is the
server-assigned ID; `payload.span_id` is the client-generated one. Parentage
uses `parent_span_id`, which refers to the server ID. The seam uses
`summaries[].id` throughout so that `parent_id` lookups actually resolve.
Using the client ID would produce a span tree where no parent is ever found,
and every parentage-dependent eval would fail open. Evidence in failure output
reports server IDs, which are the ones that work in the Prefactor UI.

Fields not mapped: `schema_title`, `summary`, `risk_level`, `risk_score`,
`account_id`, `agent_id`, `payload_byte_size_estimate`, `payload.trace_id`,
`payload.tags`. Not needed by any
v1 eval. `tags` is a plausible future addition if pack configs start wanting
tag-based selectors.

## Status value alignment

Instance and span statuses come back lowercase and are used verbatim. No
casing translation happens anywhere.

The SDK-internal `SpanStatus` values (`running`, `success`, `error`) never
appear on the read path. The SDKs translate them on write: `running` becomes
`active`, `success` becomes `complete`, `error` becomes `failed`. Anyone
reading SDK source while implementing the seam will meet those three words and
should know they are not wire values.

The build spec's state list omits `terminated`, which the server does return at
instance level. schema/v1 includes it. Dropping it would have mapped an
externally killed run onto `cancelled`, and `core.termination_state` would then
report a killed agent as an ordinary cancellation.

## Known gaps

1. **No per-span cost.** Covered in `span.md` note 4.
2. **No duration field at any level.** Always derived.
3. **No instance input or output field.** Derived from spans, per
   `instance.md` note 5. Related and more consequential: **real traces are
   flat**. Every span in every sampled live instance had a null
   `parent_span_id`, so the span tree is a shape the API supports and real
   instrumentation does not populate. Any eval reasoning about parentage,
   nesting, or subtree containment will find nothing to work with. None of the
   37 v1 evals depend on parentage, and that should stay true until flat traces
   stop being the norm.
4. **No span type enum.** Derived from `schema_name`, heuristically.
5. **No server-side time filtering on instances.** Client-side, after fetch.
6. **Sensitive values.** Spans written with `sensitive_encoding: true` carry
   values shaped `{"$sensitive": "string"|"number", "value": ...}` rather than
   raw values. Any eval comparing values against config would compare against
   that wrapper and fail incorrectly. The seam unwraps to `value` when the
   wrapper is present, and evals whose comparison depends on an unwrapped value
   that is absent must `skip`. The `sensitive_encoding` flag is confirmed
   present as a top-level span field, so the seam can detect the case reliably.
   The wrapper shape itself has not been exercised against live encoded data,
   because no sampled span had encoding enabled. Phase 3 covers it with a
   fixture, and it stays the least certain rule in this document.

7. **`include_risk_score=true` returns HTTP 500.** Never send it. The other
   `include_*` flags are no-ops. See the endpoints section above.

## Sources

Verified 20 Jul 2026 against:

1. `@prefactor/core` 0.4.3 TypeScript type surface, as installed.
2. `prefactor-core` 0.2.0 Python package, as installed, built from
   `prefactordev/python-sdk` at commit `7f43451`.
3. The captured server OpenAPI contract, snapshot dated 24 Jun 2026. This is
   the only source for the GET list endpoints, cost and risk shapes, and the
   `pending` and `terminated` statuses. It is a point-in-time capture and is
   older than the SDK copies, so it is authoritative but stale.
4. Empirical verification notes from working instrumentation, 16 and 17 Jul
   2026, covering the state machine and the read-back endpoints.
5. `https://docs.prefactor.ai/llms-full.txt`, which confirms the lifecycle
   states and env var names but does not publish field-level JSON schemas.
6. **Live verification against a real account, 20 Jul 2026.** All three read
   endpoints called, 119 spans and multiple instances sampled across two
   agents. This supersedes point 3 wherever the two disagree.

The live pass corrected three things the captured contract had wrong, all of
which would have shipped as defects:

1. `peak_classification` is not returned on instance summaries. `purpose` is,
   and it is now a seam filter.
2. The ai-sdk tool convention encodes the tool name in `schema_name` as
   `ai-sdk:tool:<name>`. An exact-match-only type table would have typed every
   tool span as `other` and quietly disabled the three core evals that key on
   tool identity. Hence the prefix rule in `span.md` note 2.
3. `include_risk_score=true` returns 500, and the other `include_*` flags do
   nothing.

Remaining unverified: the sensitive-value wrapper shape, per known gap 6.
