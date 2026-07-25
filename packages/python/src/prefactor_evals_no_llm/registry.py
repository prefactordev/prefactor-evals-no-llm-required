"""Eval registry.

Every eval registers itself with a stable ID. IDs appear in user config and in
stored reports, so renaming one is a breaking change and a semver major.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

_REGISTRY: dict = {}


@dataclass
class EvalContext:
    """Everything an eval may see beyond its own instance.

    `instances` is the full fetched set, needed only by the two cross instance
    evals. Within instance evals must ignore it.
    """
    instances: list
    span_type_map: dict


@dataclass
class RegisteredEval:
    eval_id: str
    pack: str
    fn: Callable
    cross_instance: bool = False


def register(eval_id: str, pack: str, cross_instance: bool = False):
    def decorate(fn: Callable):
        if eval_id in _REGISTRY:
            raise ValueError("duplicate eval id: " + eval_id)
        _REGISTRY[eval_id] = RegisteredEval(eval_id, pack, fn, cross_instance)
        return fn
    return decorate


def get(eval_id: str) -> Optional[RegisteredEval]:
    return _REGISTRY.get(eval_id)


def all_evals() -> dict:
    return dict(_REGISTRY)


def load_all() -> dict:
    """Import every eval module so the decorators run."""
    from . import evals  # noqa: F401
    return all_evals()
