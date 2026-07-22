"""Per-span detectors and the evaluator that drives them.

A detector sees one span at a time and keeps whatever running state it needs.
When a span pushes it past a threshold, it returns a Breach; otherwise None. The
evaluator holds one set of detectors per live run and routes each span to them.

Determinism: the same spans in the same order always yield the same breaches. No
clock, no randomness, no network. The one time-derived thing, a span's duration,
comes off the span itself, computed by the seam at fetch time.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Optional

from ..config import cfg_float, cfg_int
from ..helpers import tool_signature

# What happens when a check breaches, per check. Off does not even run the
# detector. Warn records the breach. Terminate additionally asks the caller to
# stop the run. Terminate is never the default: killing a good run on a false
# positive is the one costly mistake, so it is always opt in.
POLICY_OFF = "off"
POLICY_WARN = "warn"
POLICY_TERMINATE = "terminate"
POLICIES = (POLICY_OFF, POLICY_WARN, POLICY_TERMINATE)


class Breach:
    """A check tripped on a live run."""

    def __init__(self, check: str, reason: str, span_id: str, policy: str):
        self.check = check
        self.reason = reason
        self.span_id = span_id
        self.policy = policy

    @property
    def terminate(self) -> bool:
        return self.policy == POLICY_TERMINATE

    def to_dict(self) -> dict:
        return {"check": self.check, "reason": self.reason,
                "span_id": self.span_id, "policy": self.policy}


# --- detectors -------------------------------------------------------------
# Each is a callable factory that returns a fresh per-run detector. A detector
# is `on_span(span) -> Optional[str]`: the breach reason, or None.


def _loops(config: dict) -> Callable:
    """The same tool signature repeated past a limit, anywhere in the run."""
    limit = cfg_int(config, "max_occurrences", 3)
    ignore_arg_keys = config.get("ignore_arg_keys") or ()
    counts: dict = defaultdict(int)

    def on_span(span) -> Optional[str]:
        if span.type != "tool_call":
            return None
        key = tool_signature(span, ignore_arg_keys)
        counts[key] += 1
        if counts[key] > limit:
            return ('tool "%s" called %d times with identical arguments, limit is %d'
                    % (span.name, counts[key], limit))
        return None

    return on_span


def _redundant(config: dict) -> Callable:
    """The same tool signature repeated back to back with no progress."""
    limit = cfg_int(config, "max_repeats", 2)
    ignore_arg_keys = config.get("ignore_arg_keys") or ()
    state = {"last": None, "run": 0}

    def on_span(span) -> Optional[str]:
        if span.type != "tool_call":
            return None
        key = tool_signature(span, ignore_arg_keys)
        if key == state["last"]:
            state["run"] += 1
        else:
            state["last"], state["run"] = key, 1
        if state["run"] > limit:
            return ('tool "%s" called %d times in a row with identical arguments, '
                    "limit is %d" % (span.name, state["run"], limit))
        return None

    return on_span


def _errors(config: dict) -> Callable:
    """A step failed."""
    tolerance = cfg_int(config, "max_failures", 0)
    seen = {"n": 0}

    def on_span(span) -> Optional[str]:
        if span.state != "failed":
            return None
        seen["n"] += 1
        if seen["n"] > tolerance:
            detail = (span.error or {}).get("message") or span.name
            return "step failed: %s" % detail
        return None

    return on_span


def _latency(config: dict) -> Callable:
    """A single agent-side step ran longer than the ceiling."""
    ceiling = cfg_float(config, "max_span_ms", 60000)
    agent_types = set(config.get("span_types") or ("llm_call", "tool_call", "retrieval"))

    def on_span(span) -> Optional[str]:
        if span.type not in agent_types or span.duration_ms is None:
            return None
        if span.duration_ms > ceiling:
            return ('step "%s" took %d ms, ceiling is %d ms'
                    % (span.name, round(span.duration_ms), round(ceiling)))
        return None

    return on_span


def _over_baseline(config: dict) -> Callable:
    """The run has already used more steps than this agent's normal ceiling.

    The batch efficiency check compares a finished run to the agent's median.
    Live, the useful question is the leading edge of that: has this run already
    passed the ceiling, while it is still going, so it can be stopped before it
    wastes more. The ceiling is passed in, computed once from the agent's recent
    history when the watch starts, so no baseline means this detector is off.
    """
    ceiling = config.get("ceiling")
    if not ceiling:
        return lambda span: None
    ceiling = float(ceiling)
    count = {"n": 0}

    def on_span(span) -> Optional[str]:
        count["n"] += 1
        if count["n"] == int(ceiling) + 1:
            return ("run has used %d steps, past the agent's normal ceiling of %d, "
                    "and is still going" % (count["n"], int(ceiling)))
        return None

    return on_span


DETECTORS = {
    "loops": _loops,
    "redundant_calls": _redundant,
    "errors": _errors,
    "latency": _latency,
    "runaway": _over_baseline,
}


class LiveEvaluator:
    """Per-span evaluation for one live run.

    Built from a policy mapping check name to off/warn/terminate. A check set to
    off is never even instantiated. Feed spans in with `on_span`; it returns the
    breaches that span caused, each already carrying its policy.
    """

    def __init__(self, policy: dict, config: Optional[dict] = None):
        self.policy = {k: v for k, v in (policy or {}).items()}
        for name, mode in self.policy.items():
            if mode not in POLICIES:
                raise ValueError('policy for "%s" must be one of %s, not %r'
                                 % (name, POLICIES, mode))
        config = config or {}
        self._detectors = {}
        for name, mode in self.policy.items():
            if mode == POLICY_OFF or name not in DETECTORS:
                continue
            self._detectors[name] = DETECTORS[name](config.get(name) or {})
        self._fired = set()

    def on_span(self, span) -> list:
        breaches = []
        for name, detector in self._detectors.items():
            reason = detector(span)
            if reason is None:
                continue
            # A given check fires once per run. A loop keeps tripping every call
            # after the limit; reporting it once is enough and one breach is
            # enough to terminate.
            if name in self._fired:
                continue
            self._fired.add(name)
            breaches.append(Breach(name, reason, span.id, self.policy[name]))
        return breaches

    @property
    def wants_terminate(self) -> bool:
        return any(m == POLICY_TERMINATE for m in self.policy.values())
