"""Drive per-span evaluation over live runs.

The loop is deliberately split from where the spans come from. `watch_run` takes
a source of spans and a way to terminate, and knows nothing about Prefactor, so
it runs the same against the live API or against a fake in a test. Delivery,
polling the API or reacting to a subscription push, is the caller's job.
"""

from __future__ import annotations

from typing import Callable, Optional

from .detectors import LiveEvaluator


class WatchResult:
    def __init__(self, instance_id: str, breaches: list, terminated: bool,
                 spans_seen: int):
        self.instance_id = instance_id
        self.breaches = breaches
        self.terminated = terminated
        self.spans_seen = spans_seen

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "spans_seen": self.spans_seen,
            "terminated": self.terminated,
            "breaches": [b.to_dict() for b in self.breaches],
        }


def evaluate_spans(instance_id: str, spans, evaluator: LiveEvaluator,
                   terminate: Optional[Callable] = None,
                   on_breach: Optional[Callable] = None,
                   stop_on_terminate: bool = True) -> WatchResult:
    """Feed spans into the evaluator in order, acting on breaches.

    `spans` is any iterable of spans in arrival order. `terminate(reason)` is
    called at most once, on the first breach whose policy says terminate. Warn
    breaches are recorded and passed to `on_breach` but do not stop anything.

    Returns once the spans run out or the run is terminated. Pure apart from the
    two callbacks, so it is fully testable without a network.
    """
    breaches = []
    terminated = False
    seen = 0
    for span in spans:
        seen += 1
        for breach in evaluator.on_span(span):
            breaches.append(breach)
            if on_breach is not None:
                on_breach(breach)
            if breach.terminate and terminate is not None and not terminated:
                terminate(breach.reason)
                terminated = True
        if terminated and stop_on_terminate:
            break
    return WatchResult(instance_id, breaches, terminated, seen)
