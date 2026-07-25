"""Live, per-span evaluation.

The batch checks in this library grade a run after it finishes. These grade a run
while it is still running, one span at a time, so a breach can stop the run
before it does more harm.

The detectors here are the streaming form of the same ideas as the batch checks:
each holds a running tally for one run and updates it as spans arrive, so a loop
trips on the offending call rather than after the run ends. Nothing here reads a
clock or the network; feeding the same spans in the same order always produces
the same breaches.
"""

from .detectors import Breach, LiveEvaluator, POLICY_OFF, POLICY_TERMINATE, POLICY_WARN

__all__ = [
    "Breach", "LiveEvaluator", "POLICY_OFF", "POLICY_WARN", "POLICY_TERMINATE",
]
