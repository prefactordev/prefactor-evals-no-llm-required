"""THE SEAM. The only file in this package that knows Prefactor exists.

Replace it with your own loader that emits schema/v1 shapes and everything else
in the library works unchanged. We think pulling real production traces from
Prefactor is the whole point, but the choice is yours.

A note on why this file speaks HTTP rather than importing the Prefactor SDK.
Both SDKs, TypeScript and Python, are write only: they create instances and
spans and expose no read path at all. There is nothing to wrap. So this file
issues its own GETs against the documented endpoints. The coupling is still
confined to one file, which is the property that matters.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .schema import (
    EvalInstance,
    EvalSpan,
    duration_ms,
    parse_timestamp,
    resolve_span_name,
    resolve_span_type,
    sort_spans,
)

DEFAULT_PAGE_SIZE = 100

# The documented Prefactor cloud host: docs.prefactor.ai/api publishes this as
# the API base URL. Used when PREFACTOR_API_URL is not set, so cloud users,
# which is nearly everyone, configure exactly one thing: the token. Anyone on a
# different host sets the variable and it wins.
DEFAULT_API_URL = "https://app.prefactorai.com"

# Where the one thing you must configure comes from. Matches the Admin UI and
# docs.prefactor.ai/admin-ui/account/api-tokens.
_TOKEN_REMEDY = (
    "PREFACTOR_API_TOKEN is not set. Create an account-wide API token in the "
    "Prefactor Admin UI under Account, API Tokens, Create API token, and copy "
    "it when shown, it is only displayed once."
)

# Sort key for rows with no start time, so they land last under newest first
# ordering rather than crashing the comparison.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class PrefactorSourceError(RuntimeError):
    pass


def _http_request(method: str, url: str, token: str, timeout: float,
                  body: Optional[dict] = None) -> dict:
    """One place for every Prefactor HTTP call, read or write.

    Kept module level so the read source and the write publisher share exactly
    the same auth, error mapping, and 401 message, and so all Prefactor
    coupling still lives in this single file.
    """
    headers = {"Authorization": "Bearer " + token}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text.strip() else {}
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise PrefactorSourceError(
                "Prefactor returned 401. The read and quality endpoints need an "
                "account-wide API token, created in the Admin UI under Account, "
                "API Tokens. A deployment scoped token or the SDK ingestion "
                "token used for instrumentation will not work here."
            ) from error
        raise PrefactorSourceError(
            "Prefactor returned HTTP %s for %s %s" % (error.code, method, url)
        ) from error
    except urllib.error.URLError as error:
        raise PrefactorSourceError("Could not reach %s: %s" % (url, error)) from error


def _unwrap_sensitive(value: Any) -> Any:
    """Spans written with sensitive_encoding carry {"$sensitive": type,
    "value": ...} wrappers. Comparing config against the wrapper would fail
    incorrectly, so unwrap to the value wherever it appears."""
    if isinstance(value, dict):
        if "$sensitive" in value:
            return _unwrap_sensitive(value.get("value"))
        return {k: _unwrap_sensitive(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_unwrap_sensitive(v) for v in value]
    return value


def _parse_since(since: Any) -> Optional[datetime]:
    """Accepts a datetime, an ISO string, or a shorthand like `7d`, `24h`, `30m`.

    The shorthand is relative to now, which is the one clock read in the whole
    library. It happens here, at fetch time, never inside an eval.
    """
    if since is None or isinstance(since, datetime):
        return since
    text = str(since).strip()
    if text and text[-1] in "dhm" and text[:-1].isdigit():
        amount = int(text[:-1])
        unit = {"d": "days", "h": "hours", "m": "minutes"}[text[-1]]
        return datetime.now(timezone.utc) - timedelta(**{unit: amount})
    return parse_timestamp(text)


class PrefactorSource:
    """Fetches instances and maps them onto the normalized schema."""

    def __init__(self, api_url: Optional[str] = None, api_token: Optional[str] = None,
                 span_type_map: Optional[dict] = None, timeout: float = 30.0,
                 max_workers: int = 8):
        self.api_url = (api_url or os.environ.get("PREFACTOR_API_URL")
                        or DEFAULT_API_URL).rstrip("/")
        self.api_token = api_token or os.environ.get("PREFACTOR_API_TOKEN") or ""
        self.span_type_map = span_type_map or {}
        self.timeout = timeout
        # Bounded on purpose. This reads someone's production API, and the
        # gain from more sockets flattens out well before the rudeness does.
        self.max_workers = max(1, int(max_workers))
        if not self.api_token:
            raise PrefactorSourceError(_TOKEN_REMEDY)

    # -- HTTP ---------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = self.api_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return _http_request("GET", url, self.api_token, self.timeout)

    def _iter_pages(self, path: str, params: dict):
        """Yield rows page by page, so callers can stop early.

        This matters more than it looks. Filtering is client side, so a caller
        asking for 10 instances would otherwise page through the agent's entire
        history first. On a busy agent that is thousands of requests before any
        eval runs.
        """
        offset = 0
        while True:
            page = dict(params)
            page["pagination[offset]"] = offset
            page["pagination[page_size]"] = DEFAULT_PAGE_SIZE
            body = self._get(path, page)
            rows = body.get("summaries") or []
            for row in rows:
                yield row
            pagination = body.get("pagination") or {}
            nxt = pagination.get("next_page_offset")
            if not rows or nxt is None or nxt == offset:
                return
            offset = nxt

    def _paged(self, path: str, params: dict, limit: Optional[int] = None) -> list:
        out = []
        for row in self._iter_pages(path, params):
            out.append(row)
            if limit is not None and len(out) >= limit:
                break
        return out

    # -- Mapping ------------------------------------------------------------

    def _map_span(self, row: dict) -> EvalSpan:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        schema_name = row.get("schema_name") or ""

        raw_input = payload.get("inputs")
        raw_output = row.get("result_payload")
        if raw_output in (None, {}):
            raw_output = payload.get("outputs")

        # sensitive_encoding is a top level span field, so the wrapper case is
        # detectable before parsing.
        if row.get("sensitive_encoding"):
            raw_input = _unwrap_sensitive(raw_input)
            raw_output = _unwrap_sensitive(raw_output)

        tokens = None
        usage = payload.get("token_usage")
        if isinstance(usage, dict):
            tokens = {
                "prompt": usage.get("prompt_tokens"),
                "completion": usage.get("completion_tokens"),
                "total": usage.get("total_tokens"),
            }

        error = None
        raw_error = payload.get("error")
        if isinstance(raw_error, dict):
            error = {
                "type": raw_error.get("error_type"),
                "message": raw_error.get("message"),
                "stacktrace": raw_error.get("stacktrace"),
            }

        started = parse_timestamp(row.get("started_at"))
        ended = parse_timestamp(row.get("finished_at"))

        return EvalSpan(
            # The server ID, not payload.span_id. Parentage refers to server
            # IDs, so using the client ID would produce a tree where no parent
            # ever resolves and every parentage eval would fail open.
            id=row.get("id") or "",
            parent_id=row.get("parent_span_id"),
            instance_id=row.get("agent_instance_id") or "",
            type=resolve_span_type(schema_name, self.span_type_map),
            name=resolve_span_name(schema_name, payload.get("name")),
            schema_name=schema_name,
            input=raw_input,
            output=raw_output,
            state=row.get("status") or "pending",
            started_at=started,
            ended_at=ended,
            duration_ms=duration_ms(started, ended),
            cost=None,  # No per span cost exists anywhere in the API.
            tokens=tokens,
            error=error,
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        )

    def _map_instance(self, row: dict, spans: list, detail: Optional[dict] = None) -> EvalInstance:
        started = parse_timestamp(row.get("started_at"))
        ended = parse_timestamp(row.get("finished_at"))
        ordered = sort_spans(spans)

        # The instance record carries neither input nor output, so both are
        # derived. Real traces are almost always flat: every span reports a null
        # parent, so "the single root span" resolves on essentially no real
        # instance. Deriving from the root alone would leave input and output
        # null everywhere and silently skip every eval that reads them.
        #
        # So there are two rules, and which one fired is recorded on the
        # instance as input_source and output_source. A derivation that can be
        # wrong must at least say where it came from.
        derived_input, input_source = None, None
        derived_output, output_source = None, None

        roots = [s for s in ordered if s.parent_id is None]
        if len(roots) == 1:
            derived_input, input_source = roots[0].input, "root_span"
            derived_output, output_source = roots[0].output, "root_span"
        else:
            # The earliest span is what the agent did first, so its input is the
            # initiating input. The last span carrying an output is the final
            # output. These are definitions, not guesses, but they are only
            # sound on a flat trace, which is why the source is recorded.
            for span in ordered:
                if span.input is not None:
                    derived_input, input_source = span.input, "first_span"
                    break
            for span in reversed(ordered):
                if span.output is not None:
                    derived_output, output_source = span.output, "last_output_span"
                    break

        metadata = {
            "account_id": row.get("account_id"),
            "termination_reason": row.get("termination_reason"),
            "purpose": row.get("purpose"),
            "inserted_at": row.get("inserted_at"),
            "updated_at": row.get("updated_at"),
            "input_source": input_source,
            "output_source": output_source,
            "root_span_count": len(roots),
        }
        cost = None
        if detail:
            breakdown = detail.get("cost_breakdown")
            if isinstance(breakdown, dict):
                cost = breakdown.get("total_cost")
                metadata["cost_breakdown"] = breakdown
            for key in ("span_counts", "span_schema_counts", "risk_score",
                        "quality_payload", "quality_summary"):
                if key in detail:
                    metadata[key] = detail[key]

        return EvalInstance(
            id=row.get("id") or "",
            agent_id=row.get("agent_id") or "",
            agent_version=row.get("agent_version_id"),
            environment=row.get("environment_id"),
            started_at=started,
            ended_at=ended,
            state=row.get("status") or "pending",
            duration_ms=duration_ms(started, ended),
            spans=ordered,
            input=derived_input,
            output=derived_output,
            cost=cost,
            metadata=metadata,
        )

    # -- Public API ---------------------------------------------------------

    def list_agents(self) -> list:
        """The account's agents, as {id, name} dicts.

        Exists because the agent id is the one thing setup needs that the user
        cannot type from memory. `agents` in the CLI prints this, so nobody has
        to fish an id out of a dashboard URL.
        """
        rows = self._paged("/api/v1/agent", {})
        return [{"id": r.get("id") or "", "name": r.get("name") or "",
                 "status": r.get("status") or ""}
                for r in rows]

    def fetch_instances(self, agent_id: str, since: Any = None, until: Any = None,
                        state: Optional[str] = None, environment: Optional[str] = None,
                        purpose: Optional[str] = "live", limit: Optional[int] = None,
                        with_cost: bool = False) -> list:
        """Fetch normalized instances for an agent.

        Server side filtering is close to nonexistent: the instance list accepts
        agent_id only, has no time window parameter, and ignores a status
        parameter. So everything below is filtered client side after fetching.
        A wide `since` window is therefore genuinely expensive.
        """
        since_dt, until_dt = _parse_since(since), _parse_since(until)

        # Newest first, ordered here rather than on the server. The instance
        # list has no working sort: the live API rejects every documented
        # `sorting` value with a validation error, and its default order is
        # oldest first. So `limit` would otherwise score the agent's oldest
        # runs, which is the opposite of what "evaluate my last N" means.
        #
        # Ordering client side means reading the whole list before taking the
        # newest N. Instances are far fewer than spans, and `since` is the knob
        # for bounding a large agent. All filtering already happens here anyway,
        # so no server round trip is saved by stopping early.
        rows = list(self._iter_pages("/api/v1/agent_instance", {"agent_id": agent_id}))
        rows.sort(
            key=lambda r: parse_timestamp(r.get("started_at")) or _EPOCH,
            reverse=True,
        )

        selected = []
        for row in rows:
            if state and row.get("status") != state:
                continue
            if environment and row.get("environment_id") != environment:
                continue
            # Default to live traffic. Running evals over your own eval and
            # smoke test runs measures the harness, not the agent.
            if purpose and row.get("purpose") and row.get("purpose") != purpose:
                continue
            started = parse_timestamp(row.get("started_at"))
            if since_dt and (started is None or started < since_dt):
                continue
            if until_dt and (started is None or started > until_dt):
                continue
            selected.append(row)
            if limit is not None and len(selected) >= limit:
                break

        instances = []
        # Spans are fetched per instance, so a hundred runs meant a hundred
        # round trips one after another and the wall clock was almost entirely
        # waiting. These requests are independent reads, so they overlap.
        # Concurrency is bounded rather than unbounded: this runs against
        # someone's production API, and opening a hundred sockets to be a little
        # faster is rude at best.
        def fetch(row):
            span_rows = self._paged(
                "/api/v1/agent_spans", {"agent_instance_id": row.get("id")}
            )
            spans = [self._map_span(s) for s in span_rows]
            detail = None
            if with_cost:
                # Send no include_* flags. cost_breakdown and the counts come
                # back unconditionally, and include_risk_score=true returns 500.
                body = self._get("/api/v1/agent_instance/%s" % row.get("id"))
                detail = body.get("details") if isinstance(body, dict) else None
            return self._map_instance(row, spans, detail)

        if len(selected) < 2 or self.max_workers <= 1:
            return [fetch(row) for row in selected]

        workers = min(self.max_workers, len(selected))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Mapped in order, so the result order matches `selected` no matter
            # which request finishes first. Ordering feeds straight into eval
            # results, and those have to be reproducible.
            return list(pool.map(fetch, selected))


def fetch_instances(agent_id: str, **filters) -> list:
    """Module level convenience wrapper, the one function the spec names."""
    span_type_map = filters.pop("span_type_map", None)
    source = PrefactorSource(span_type_map=span_type_map)
    return source.fetch_instances(agent_id, **filters)


class PrefactorController:
    """The one write action that stops a run: terminate.

    Kept in this file because it is Prefactor coupling. The endpoint is built
    for exactly this, an external party ending a live run with a reason. Only
    active runs can be terminated; the agent learns of it on the control flag
    that comes back on its next span, and stops.
    """

    def __init__(self, api_url: Optional[str] = None, api_token: Optional[str] = None,
                 timeout: float = 30.0):
        self.api_url = (api_url or os.environ.get("PREFACTOR_API_URL")
                        or DEFAULT_API_URL).rstrip("/")
        self.api_token = api_token or os.environ.get("PREFACTOR_API_TOKEN") or ""
        self.timeout = timeout
        if not self.api_token:
            raise PrefactorSourceError(_TOKEN_REMEDY)

    def terminate(self, instance_id: str, reason: str) -> dict:
        """Stop a live run. `reason` is required by the API and is what the agent
        and the audit trail see, so it should name the breach."""
        if not reason or not reason.strip():
            raise PrefactorSourceError("terminate needs a reason.")
        return _http_request(
            "POST", "%s/api/v1/agent_instance/%s/terminate" % (self.api_url, instance_id),
            self.api_token, self.timeout, body={"reason": reason},
        )

    def live_spans(self, instance_id: str, after_index: int = 0) -> list:
        """The instance's spans in start order, from `after_index` on.

        Used to pull only the spans that have arrived since the last look, so a
        live run is read forward one slice at a time rather than refetched whole.
        """
        rows = self._paged_spans(instance_id)
        return rows[after_index:]

    def _paged_spans(self, instance_id: str) -> list:
        source = PrefactorSource(api_url=self.api_url, api_token=self.api_token,
                                 timeout=self.timeout)
        span_rows = source._paged("/api/v1/agent_spans",
                                  {"agent_instance_id": instance_id})
        return [source._map_span(s) for s in sort_spans_rows(span_rows)]


def sort_spans_rows(rows: list) -> list:
    """Order raw span rows by start time then id, so successive live reads see a
    stable prefix and only genuinely new spans at the end."""
    def key(row):
        ts = parse_timestamp(row.get("started_at"))
        return (0, ts.timestamp(), row.get("id") or "") if ts else (1, 0.0, row.get("id") or "")
    return sorted(rows, key=key)


# The single key this library owns inside an instance's quality payload. Writing
# only under this key means a user who records their own quality data from their
# app is never clobbered: their keys are read, preserved, and written back
# untouched.
# Legacy nesting key. Kept only so an instance written by an older version can
# have that stale block removed on the next publish.
QUALITY_KEY = "evals_no_llm"

# The top level fields this library owns in a quality_payload. Anything else in
# the payload belongs to the agent and is never touched.
QUALITY_FIELDS = (
    "report", "result", "checks", "checks_passed", "checks_failed",
    "checks_not_run", "recorded_verdict", "recorded_scores", "signals",
    "signals_flagged", "worst_signal", "pack", "scored_while_running",
    "instance_state", "tool_version", "evaluated_at",
)


def declare_quality_schema(agent_id: str, version: str = "agent-evals",
                           schema_version: str = "agent-evals-quality-v1",
                           environment_id: Optional[str] = None,
                           api_url: Optional[str] = None,
                           api_token: Optional[str] = None,
                           timeout: float = 30.0) -> dict:
    """Declare the eval quality schema on an agent's schema version.

    The Quality tab renders from a schema declared at registration, so this is
    the step that makes published results visible at all. There is no endpoint
    for declaring a schema on its own: schemas are attached by registering, so
    this registers one probe instance carrying the schema, then finishes it.

    Rendering binds to the schema version an instance was registered under, so
    this affects later runs only. Existing runs cannot be retrofitted.
    """
    from .publish import QUALITY_SCHEMA

    url = (api_url or os.environ.get("PREFACTOR_API_URL")
           or DEFAULT_API_URL).rstrip("/")
    token = api_token or os.environ.get("PREFACTOR_API_TOKEN") or ""
    environment = environment_id or os.environ.get("PREFACTOR_ENVIRONMENT_ID")
    if not token:
        raise PrefactorSourceError(_TOKEN_REMEDY)

    body = {
        "agent_id": agent_id,
        "agent_version": {
            "external_identifier": version,
            "name": "Agent evals",
            "description": "Eval results published by prefactor-evals-no-llm",
        },
        "agent_schema_version": {
            "external_identifier": schema_version,
            "span_type_schemas": [],
            "quality_schema": QUALITY_SCHEMA,
        },
    }
    if environment:
        body["environment_id"] = environment

    registered = _http_request("POST", url + "/api/v1/agent_instance/register",
                               token, timeout, body=body)
    instance_id = (registered.get("details") or {}).get("id")
    if instance_id:
        # The probe instance exists only to carry the schema. Close it so it does
        # not sit in the agent's list looking like a run that never finished.
        try:
            _http_request("POST",
                          "%s/api/v1/agent_instance/%s/finish" % (url, instance_id),
                          token, timeout, body={"status": "cancelled"})
        except PrefactorSourceError:
            pass
    return {"instance_id": instance_id, "schema_version": schema_version}


class PrefactorPublisher:
    """Writes eval scores into an instance's Prefactor Quality tab.

    This is the only write path in the library, and it is opt in. A plain eval
    run never constructs one. All coupling still lives in this file: the class
    reuses the same HTTP helper and the same token as the read source.

    The write is a PUT of the instance's quality_payload. That endpoint replaces
    the whole payload, so this class reads the current payload first and merges
    under QUALITY_KEY, never touching anything else the user stored there.
    """

    def __init__(self, api_url: Optional[str] = None, api_token: Optional[str] = None,
                 timeout: float = 30.0):
        self.api_url = (api_url or os.environ.get("PREFACTOR_API_URL")
                        or DEFAULT_API_URL).rstrip("/")
        self.api_token = api_token or os.environ.get("PREFACTOR_API_TOKEN") or ""
        self.timeout = timeout
        if not self.api_token:
            raise PrefactorSourceError(_TOKEN_REMEDY)

    def current_quality_payload(self, instance_id: str) -> dict:
        """The instance's existing quality_payload, or an empty dict."""
        body = _http_request(
            "GET", "%s/api/v1/agent_instance/%s" % (self.api_url, instance_id),
            self.api_token, self.timeout,
        )
        details = body.get("details") if isinstance(body, dict) else None
        payload = (details or {}).get("quality_payload")
        return dict(payload) if isinstance(payload, dict) else {}

    def publish(self, instance_id: str, block: dict) -> dict:
        """Merge `block` into the quality_payload and PUT the whole thing.

        The block's fields go at the top level, because that is where the
        declared quality schema defines them and where its template reads them
        from. Nesting under a namespace key renders an empty summary: the
        template looks up `report`, finds nothing, and writes a blank string.

        Any existing keys that this library does not own are preserved, so
        quality data written by the agent itself survives.
        """
        merged = self.current_quality_payload(instance_id)
        merged.pop(QUALITY_KEY, None)  # drop the old nested form if present
        merged.update(block)
        # The details wrapper is required by the API. Without it the server
        # rejects the body with a validation error.
        _http_request(
            "PUT", "%s/api/v1/agent_instance/%s" % (self.api_url, instance_id),
            self.api_token, self.timeout,
            body={"details": {"quality_payload": merged}},
        )
        return merged

    def clear(self, instance_id: str, block_keys=None) -> dict:
        """Remove only the fields this library wrote, leaving anything else."""
        merged = self.current_quality_payload(instance_id)
        merged.pop(QUALITY_KEY, None)
        for key in (block_keys or QUALITY_FIELDS):
            merged.pop(key, None)
        _http_request(
            "PUT", "%s/api/v1/agent_instance/%s" % (self.api_url, instance_id),
            self.api_token, self.timeout,
            body={"details": {"quality_payload": merged}},
        )
        return merged
