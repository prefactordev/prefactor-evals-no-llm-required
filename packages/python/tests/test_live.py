"""Per-span live evaluation, with a fake terminate so nothing real is stopped.

The point of live mode is to catch a run misbehaving while it is still running
and, if told to, stop it. These tests feed spans in one at a time and assert the
breach fires on the offending span, that terminate is called exactly when policy
says so and not otherwise, and that the same spans always produce the same
result.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from prefactor_evals_no_llm.live import (
    LiveEvaluator, POLICY_OFF, POLICY_TERMINATE, POLICY_WARN)
from prefactor_evals_no_llm.live.watch import evaluate_spans
from prefactor_evals_no_llm.schema import EvalSpan

BASE = datetime(2026, 7, 20, tzinfo=timezone.utc)


def span(name, span_id, type="tool_call", second=0, state="complete",
         input=None, duration_ms=5.0):
    return EvalSpan(
        id=span_id, instance_id="i1", type=type, name=name,
        schema_name="x:" + name, state=state,
        started_at=BASE + timedelta(seconds=second),
        ended_at=BASE + timedelta(seconds=second, milliseconds=duration_ms),
        duration_ms=duration_ms, input=input if input is not None else {"q": 1})


def _terminator():
    calls = []
    return calls, (lambda reason: calls.append(reason))


def test_a_loop_trips_on_the_offending_span_not_at_the_end():
    """The fourth identical call, with a limit of three, is the breach. It must
    fire on that span, not after the run finishes."""
    ev = LiveEvaluator({"loops": POLICY_WARN}, {"loops": {"max_occurrences": 3}})
    spans = [span("fetch", "s%d" % i, input={"same": 1}) for i in range(6)]
    breaches = []
    for i, s in enumerate(spans):
        got = ev.on_span(s)
        if got:
            assert i == 3, "loop should trip on the 4th call, tripped on %d" % (i + 1)
            breaches += got
    assert len(breaches) == 1
    assert "4 times" in breaches[0].reason


def test_terminate_is_called_once_on_a_terminate_policy_breach():
    calls, terminate = _terminator()
    ev = LiveEvaluator({"loops": POLICY_TERMINATE}, {"loops": {"max_occurrences": 2}})
    spans = [span("fetch", "s%d" % i, input={"x": 1}) for i in range(5)]
    result = evaluate_spans("i1", spans, ev, terminate=terminate)
    assert result.terminated is True
    assert len(calls) == 1
    assert "fetch" in calls[0]
    # Stopped at the breaching span, did not keep reading the rest.
    assert result.spans_seen == 3


def test_warn_policy_records_but_never_terminates():
    calls, terminate = _terminator()
    ev = LiveEvaluator({"loops": POLICY_WARN}, {"loops": {"max_occurrences": 2}})
    spans = [span("fetch", "s%d" % i, input={"x": 1}) for i in range(5)]
    result = evaluate_spans("i1", spans, ev, terminate=terminate)
    assert result.terminated is False
    assert calls == []
    assert len(result.breaches) == 1


def test_off_policy_does_not_run_the_detector():
    ev = LiveEvaluator({"loops": POLICY_OFF}, {"loops": {"max_occurrences": 1}})
    spans = [span("fetch", "s%d" % i, input={"x": 1}) for i in range(5)]
    result = evaluate_spans("i1", spans, ev)
    assert result.breaches == []


def test_errors_trip_the_moment_a_step_fails():
    calls, terminate = _terminator()
    ev = LiveEvaluator({"errors": POLICY_TERMINATE})
    spans = [span("a", "s1"), span("charge", "s2", state="failed"), span("b", "s3")]
    result = evaluate_spans("i1", spans, ev, terminate=terminate)
    assert result.terminated is True
    assert result.spans_seen == 2  # stopped on the failed span
    assert "charge" in calls[0] or "failed" in calls[0]


def test_latency_trips_on_a_slow_agent_span_but_ignores_a_slow_message():
    ev = LiveEvaluator({"latency": POLICY_WARN},
                       {"latency": {"max_span_ms": 10000}})
    # A message span the user spent two minutes on is not the agent being slow.
    slow_message = span("user-message", "s1", type="output", duration_ms=120000)
    slow_model = span("llm", "s2", type="llm_call", duration_ms=15000)
    result = evaluate_spans("i1", [slow_message, slow_model], ev)
    assert len(result.breaches) == 1
    assert result.breaches[0].span_id == "s2"


def test_runaway_trips_once_past_the_supplied_ceiling():
    ev = LiveEvaluator({"runaway": POLICY_WARN}, {"runaway": {"ceiling": 5}})
    spans = [span("x", "s%d" % i, type="other") for i in range(9)]
    result = evaluate_spans("i1", spans, ev)
    assert len(result.breaches) == 1
    assert "past the agent's normal ceiling of 5" in result.breaches[0].reason


def test_runaway_is_off_without_a_baseline():
    ev = LiveEvaluator({"runaway": POLICY_WARN}, {"runaway": {}})
    spans = [span("x", "s%d" % i, type="other") for i in range(50)]
    assert evaluate_spans("i1", spans, ev).breaches == []


def test_each_check_fires_only_once_per_run():
    ev = LiveEvaluator({"loops": POLICY_WARN}, {"loops": {"max_occurrences": 2}})
    # The loop is past its limit for many spans, but it is one finding.
    spans = [span("fetch", "s%d" % i, input={"x": 1}) for i in range(10)]
    result = evaluate_spans("i1", spans, ev)
    assert len(result.breaches) == 1


def test_live_evaluation_is_deterministic():
    def build():
        return LiveEvaluator({"loops": POLICY_TERMINATE, "errors": POLICY_WARN},
                             {"loops": {"max_occurrences": 2}})
    spans = [span("fetch", "s%d" % i, input={"x": 1}) for i in range(4)]
    a = evaluate_spans("i1", spans, build(), terminate=lambda r: None).to_dict()
    b = evaluate_spans("i1", spans, build(), terminate=lambda r: None).to_dict()
    assert a == b


def test_an_unknown_policy_value_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        LiveEvaluator({"loops": "destroy"})


def test_terminate_is_never_the_default():
    """A watch with no explicit terminate policy must not stop anything."""
    ev = LiveEvaluator({"loops": POLICY_WARN, "errors": POLICY_WARN})
    assert ev.wants_terminate is False
