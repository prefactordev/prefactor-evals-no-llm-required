"""Load normalized instances from JSON files.

This is a second, deliberately tiny source. It exists for two reasons: the test
suite and demos need traces without a network call, and it is the worked example
of the claim in the README that the Prefactor seam is replaceable. It emits
schema/v1 shapes and knows nothing about Prefactor.

If you want to run these evals against your own trace store, this file is the
shape of the work: read your data, emit EvalInstance and EvalSpan, done.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from .schema import (
    EvalInstance,
    EvalSpan,
    duration_ms,
    parse_timestamp,
    resolve_span_type,
    sort_spans,
)


def _span_from_dict(raw: dict, instance_id: str, span_type_map: Optional[dict]) -> EvalSpan:
    started = parse_timestamp(raw.get("started_at"))
    ended = parse_timestamp(raw.get("ended_at"))
    schema_name = raw.get("schema_name") or ""
    # Fixtures state `type` directly, since their point is to exercise evals
    # rather than the normalization. Fall back to derivation when omitted.
    span_type = raw.get("type") or resolve_span_type(schema_name, span_type_map)
    return EvalSpan(
        id=raw["id"],
        parent_id=raw.get("parent_id"),
        instance_id=raw.get("instance_id") or instance_id,
        type=span_type,
        name=raw.get("name") or schema_name,
        schema_name=schema_name,
        input=raw.get("input"),
        output=raw.get("output"),
        state=raw.get("state") or "complete",
        started_at=started,
        ended_at=ended,
        duration_ms=raw.get("duration_ms", duration_ms(started, ended)),
        cost=raw.get("cost"),
        tokens=raw.get("tokens"),
        error=raw.get("error"),
        metadata=raw.get("metadata") or {},
    )


def instance_from_dict(raw: dict, span_type_map: Optional[dict] = None) -> EvalInstance:
    instance_id = raw["id"]
    spans = sort_spans(
        [_span_from_dict(s, instance_id, span_type_map) for s in raw.get("spans") or []]
    )
    started = parse_timestamp(raw.get("started_at"))
    ended = parse_timestamp(raw.get("ended_at"))
    return EvalInstance(
        id=instance_id,
        agent_id=raw.get("agent_id") or "",
        agent_version=raw.get("agent_version"),
        environment=raw.get("environment"),
        started_at=started,
        ended_at=ended,
        state=raw.get("state") or "complete",
        duration_ms=raw.get("duration_ms", duration_ms(started, ended)),
        spans=spans,
        input=raw.get("input"),
        output=raw.get("output"),
        cost=raw.get("cost"),
        metadata=raw.get("metadata") or {},
    )


def load_instances(path: str, span_type_map: Optional[dict] = None) -> list:
    """Read a fixture file, or every .json file in a directory."""
    paths = []
    if os.path.isdir(path):
        paths = [os.path.join(path, n) for n in sorted(os.listdir(path))
                 if n.endswith(".json")]
    else:
        paths = [path]

    out = []
    for file_path in paths:
        with open(file_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        rows = data.get("instances") if isinstance(data, dict) else data
        for row in rows or []:
            out.append(instance_from_dict(row, span_type_map))
    return out


class FixtureSource:
    """Drop-in replacement for PrefactorSource, backed by JSON files."""

    def __init__(self, path: str, span_type_map: Optional[dict] = None):
        self.path = path
        self.span_type_map = span_type_map or {}

    def fetch_instances(self, agent_id: str, **filters) -> list:
        instances = load_instances(self.path, self.span_type_map)
        if agent_id:
            scoped = [i for i in instances if i.agent_id == agent_id]
            if scoped:
                instances = scoped
        state = filters.get("state")
        if state:
            instances = [i for i in instances if i.state == state]
        limit = filters.get("limit")
        return instances[:limit] if limit else instances
