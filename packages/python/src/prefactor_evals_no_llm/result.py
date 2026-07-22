"""The result object. Identical semantics in both languages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


@dataclass
class EvalResult:
    eval_id: str
    instance_id: str
    status: str
    details: str
    evidence: dict = field(default_factory=lambda: {"span_ids": [], "values": {}})
    # Only set on skip. Names the config key that would enable this eval, so the
    # scorecard can tell the user how to fix it rather than only that it broke.
    remedy: Optional[str] = None

    def to_dict(self) -> dict:
        out = {
            "eval_id": self.eval_id,
            "instance_id": self.instance_id,
            "status": self.status,
            "details": self.details,
            "evidence": self.evidence,
        }
        if self.remedy:
            out["remedy"] = self.remedy
        return out


def _evidence(span_ids: Optional[Iterable[str]], values: Optional[dict]) -> dict:
    return {"span_ids": list(span_ids or []), "values": dict(values or {})}


def passed(eval_id: str, instance_id: str, details: str,
           span_ids: Optional[Iterable[str]] = None,
           values: Optional[dict] = None) -> EvalResult:
    return EvalResult(eval_id, instance_id, PASS, details, _evidence(span_ids, values))


def failed(eval_id: str, instance_id: str, details: str,
           span_ids: Optional[Iterable[str]] = None,
           values: Optional[dict] = None) -> EvalResult:
    return EvalResult(eval_id, instance_id, FAIL, details, _evidence(span_ids, values))


def skipped(eval_id: str, instance_id: str, details: str,
            remedy: Optional[str] = None,
            values: Optional[dict] = None) -> EvalResult:
    """Skip is not a pass. It is counted separately everywhere.

    `remedy` should name the config key that would enable the eval. A scorecard
    that says "23 skipped" and stops there is useless.
    """
    return EvalResult(eval_id, instance_id, SKIP, details,
                      _evidence([], values), remedy=remedy)
