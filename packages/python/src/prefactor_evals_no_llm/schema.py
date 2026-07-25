"""Normalized eval schema, version v1.

The shapes here are the only thing evals ever see. See spec/schema/v1/ for the
field by field definitions. Nothing in this module talks to Prefactor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

SCHEMA_VERSION = "v1"

# Instance lifecycle. `terminated` is server set and means Prefactor stopped the
# run externally. It is deliberately distinct from `cancelled`.
INSTANCE_STATES = ("pending", "active", "complete", "failed", "cancelled", "terminated")

# Spans have no terminated state. A span belonging to a terminated instance
# very often stays `active` forever, because nothing closed it.
SPAN_STATES = ("pending", "active", "complete", "failed", "cancelled")

SPAN_TYPES = ("tool_call", "llm_call", "retrieval", "handoff", "output", "other")

# Built in schema_name to type mapping, exact match. See spec/schema/v1/span.md
# note 2. Verified against live traces on 20 Jul 2026.
BUILTIN_TYPE_EXACT = {
    "langchain:tool": "tool_call",
    "langchain:llm": "llm_call",
    "langchain:retriever": "retrieval",
    "langchain:chain": "other",
    "langchain:agent": "other",
    "ai-sdk:llm": "llm_call",
    "ai-sdk:agent": "other",
}

# Closed prefix list. Both the ai-sdk and langchain conventions encode the tool
# name as a third segment (ai-sdk:tool:issue_refund, langchain:tool:deposit_btc),
# so exact matching alone would type every named tool span as `other` and
# silently disable the evals that key on tool identity. Verified against two
# live agents: one ai-sdk, one langchain.
BUILTIN_TYPE_PREFIX = (
    ("ai-sdk:tool:", "tool_call"),
    ("langchain:tool:", "tool_call"),
)

# `handoff` and `output` have no built in mapping at all. Evals needing them
# skip until the user supplies span_type_map or explicit span names.


def parse_timestamp(value: Any) -> Optional[datetime]:
    """ISO 8601 string to an aware datetime. Returns None on anything unusable.

    Evals never call this. The seam parses once, at the boundary.
    """
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def duration_ms(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    """Milliseconds between two instants, or None if either is missing.

    None means unknown and must never be read as zero.
    """
    if start is None or end is None:
        return None
    return (end - start).total_seconds() * 1000.0


def resolve_span_type(schema_name: str, span_type_map: Optional[dict] = None) -> str:
    """schema_name to normalized type. Config wins, then exact, then prefix.

    There is deliberately no substring guessing: a rule like "contains tool"
    would mistype `protocol:sync`, and a silently mistyped span produces a
    silently wrong result, which is worse than no result.
    """
    if span_type_map:
        mapped = span_type_map.get(schema_name)
        if mapped in SPAN_TYPES:
            return mapped
    if schema_name in BUILTIN_TYPE_EXACT:
        return BUILTIN_TYPE_EXACT[schema_name]
    for prefix, span_type in BUILTIN_TYPE_PREFIX:
        if schema_name.startswith(prefix):
            return span_type
    return "other"


def resolve_span_name(schema_name: str, payload_name: Optional[str]) -> str:
    """Tool identity first, then the display label, then the raw schema name.

    The prefix step comes first because `payload.name` may be shared across
    tools or absent, while `ai-sdk:tool:issue_refund` is unambiguous.
    """
    for prefix, _ in BUILTIN_TYPE_PREFIX:
        if schema_name.startswith(prefix):
            remainder = schema_name[len(prefix):]
            if remainder:
                return remainder
    if payload_name:
        return payload_name
    return schema_name


@dataclass
class EvalSpan:
    id: str
    parent_id: Optional[str] = None
    instance_id: str = ""
    type: str = "other"
    name: str = ""
    schema_name: str = ""
    input: Any = None
    output: Any = None
    state: str = "pending"
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    # Always None against Prefactor: there is no per span cost field anywhere.
    # Kept so a replacement source that does have one has somewhere to put it.
    cost: Optional[float] = None
    tokens: Optional[dict] = None
    error: Optional[dict] = None
    metadata: dict = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.state == "failed"

    @property
    def finished(self) -> bool:
        return self.state in ("complete", "failed", "cancelled")


@dataclass
class EvalInstance:
    id: str
    agent_id: str = ""
    agent_version: Optional[str] = None
    environment: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    state: str = "pending"
    duration_ms: Optional[float] = None
    spans: list = field(default_factory=list)
    input: Any = None
    output: Any = None
    cost: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    def spans_of_type(self, *types: str) -> list:
        wanted = set(types)
        return [s for s in self.spans if s.type in wanted]

    def root_spans(self) -> list:
        return [s for s in self.spans if s.parent_id is None]

    def span_by_id(self, span_id: str) -> Optional[EvalSpan]:
        for s in self.spans:
            if s.id == span_id:
                return s
        return None


def sort_spans(spans: Iterable[EvalSpan]) -> list:
    """Start time ascending, then ID as a stable tiebreak.

    Concurrent spans share a start time often enough that an unstable sort
    would make order sensitive evals nondeterministic, which breaks the whole
    premise of the library.
    """
    def key(s: EvalSpan):
        ts = s.started_at
        return (0, ts.timestamp(), s.id) if ts is not None else (1, 0.0, s.id)

    return sorted(spans, key=key)
