"""Pass, fail, and skip coverage for every eval in the core pack."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from prefactor_evals_no_llm.evals.core import (
    cost_budget,
    escalation_rule,
    forbidden_actions,
    latency_budget,
    loop_detection,
    output_schema,
    redundant_tool_calls,
    termination_state,
    tool_arg_schema,
)
from prefactor_evals_no_llm.registry import EvalContext
from prefactor_evals_no_llm.schema import EvalInstance, EvalSpan

BASE = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)

_counter = {"n": 0}


def span(name="tool", type="tool_call", input=None, output=None, at=None,
         state="complete", duration_ms=None, parent_id="root", schema_name=None,
         id=None):
    """One span with an auto-incrementing id and start time.

    Start times increment so schema order matches construction order, which is
    what the ordering sensitive evals key on.
    """
    _counter["n"] += 1
    n = _counter["n"]
    started = BASE + timedelta(seconds=n if at is None else at)
    return EvalSpan(
        id=id or ("s%04d" % n),
        parent_id=parent_id,
        type=type,
        name=name,
        schema_name=schema_name if schema_name is not None else name,
        input=input,
        output=output,
        state=state,
        started_at=started,
        duration_ms=duration_ms,
    )


def instance(spans=(), state="complete", duration_ms=None, cost=None,
             output=None, metadata=None, instance_id="i0001"):
    return EvalInstance(
        id=instance_id,
        agent_id="a0001",
        state=state,
        duration_ms=duration_ms,
        spans=list(spans),
        output=output,
        cost=cost,
        metadata=dict(metadata or {}),
    )


def ctx(instances=None, span_type_map=None):
    # Accepts a list of instances for the cross instance checks. A dict is
    # treated as a span_type_map for the many callers that pass one positionally.
    if isinstance(instances, dict):
        instances, span_type_map = None, instances
    return EvalContext(instances=list(instances or []),
                       span_type_map=dict(span_type_map or {}))


try:
    import jsonschema  # noqa: F401
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

# The two schema evals skip cleanly without the optional extra, so only the
# tests that assert on validation results need it.
needs_jsonschema = pytest.mark.skipif(not HAS_JSONSCHEMA,
                                      reason="jsonschema extra not installed")


# ---------------------------------------------------------------------------
# core.loop_detection
# ---------------------------------------------------------------------------

def test_loop_detection_passes_under_limit():
    inst = instance([span("get_status", input={"id": 1}) for _ in range(3)])
    result = loop_detection.run(inst, {}, ctx())
    assert result.status == "pass"
    assert result.evidence["values"]["worst_count"] == 3


def test_loop_detection_fails_over_limit():
    spans = [span("get_status", input={"id": 1}) for _ in range(5)]
    result = loop_detection.run(instance(spans), {}, ctx())
    assert result.status == "fail"
    offender = result.evidence["values"]["offenders"][0]
    assert offender["tool"] == "get_status"
    assert offender["count"] == 5
    assert len(result.evidence["span_ids"]) == 5


def test_loop_detection_skips_without_tool_calls():
    result = loop_detection.run(instance([span("think", type="llm_call")]), {}, ctx())
    assert result.status == "skip"
    assert "span_type_map" in result.remedy


# ---------------------------------------------------------------------------
# core.redundant_tool_calls
# ---------------------------------------------------------------------------

def test_redundant_tool_calls_passes_when_output_changes():
    spans = [
        span("poll", input={"q": 1}, output={"state": "pending"}),
        span("poll", input={"q": 1}, output={"state": "pending"}),
        span("poll", input={"q": 1}, output={"state": "done"}),
    ]
    result = redundant_tool_calls.run(instance(spans), {}, ctx())
    assert result.status == "pass"
    assert result.evidence["values"]["worst_run_length"] == 2


def test_redundant_tool_calls_fails_on_long_run():
    spans = [span("get_status", input={"q": 1}, output={"s": "same"})
             for _ in range(5)]
    result = redundant_tool_calls.run(instance(spans), {}, ctx())
    assert result.status == "fail"
    offender = result.evidence["values"]["offenders"][0]
    assert offender["tool"] == "get_status"
    assert offender["run_length"] == 5
    assert offender["limit"] == 2
    assert offender["arg_digest"].startswith("sha256:")
    assert offender["output_digest"].startswith("sha256:")
    assert len(result.evidence["span_ids"]) == 5


def test_redundant_tool_calls_run_broken_by_other_span_type():
    spans = [
        span("get_status", input={"q": 1}, output={"s": "same"}),
        span("get_status", input={"q": 1}, output={"s": "same"}),
        span("reason", type="llm_call"),
        span("get_status", input={"q": 1}, output={"s": "same"}),
        span("get_status", input={"q": 1}, output={"s": "same"}),
    ]
    result = redundant_tool_calls.run(instance(spans), {}, ctx())
    assert result.status == "pass"


def test_redundant_tool_calls_skips_without_tool_calls():
    result = redundant_tool_calls.run(instance([span("think", type="llm_call")]),
                                      {}, ctx())
    assert result.status == "skip"
    assert "span_type_map" in result.remedy


# ---------------------------------------------------------------------------
# core.termination_state
# ---------------------------------------------------------------------------

def test_termination_state_passes_on_complete():
    result = termination_state.run(instance(state="complete", duration_ms=1000),
                                   {}, ctx())
    assert result.status == "pass"
    assert result.evidence["values"]["state"] == "complete"


def test_termination_state_fails_on_terminated():
    inst = instance([span("t", state="failed")], state="terminated",
                    metadata={"termination_reason": "wall_clock_exceeded"})
    result = termination_state.run(inst, {}, ctx())
    assert result.status == "fail"
    assert "terminated" in result.details
    assert result.evidence["values"]["termination_reason"] == "wall_clock_exceeded"
    assert result.evidence["values"]["failed_span_count"] == 1
    assert result.evidence["span_ids"] == [inst.spans[0].id]


def test_termination_state_fails_on_duration_overrun():
    inst = instance(state="complete", duration_ms=812004)
    result = termination_state.run(inst, {"max_duration_ms": 300000}, ctx())
    assert result.status == "fail"
    assert result.evidence["values"]["duration_ms"] == 812004
    assert result.evidence["span_ids"] == []


def test_termination_state_skips_on_in_flight_run():
    result = termination_state.run(instance(state="active"), {}, ctx())
    assert result.status == "skip"
    assert result.evidence["values"]["state"] == "active"
    assert "allow_states" in result.remedy


# ---------------------------------------------------------------------------
# core.tool_arg_schema
# ---------------------------------------------------------------------------

TICKET_SCHEMA = {
    "type": "object",
    "properties": {"priority": {"type": "string"}},
    "required": ["priority"],
}


@needs_jsonschema
def test_tool_arg_schema_passes_and_reports_uncovered():
    spans = [
        span("create_ticket", input={"priority": "high"}),
        span("send_email", input={"to": "a@b.c"}),
    ]
    result = tool_arg_schema.run(instance(spans),
                                 {"schemas": {"create_ticket": TICKET_SCHEMA}},
                                 ctx())
    assert result.status == "pass"
    assert result.evidence["values"]["checked_spans"] == 1
    assert result.evidence["values"]["uncovered_tools"] == ["send_email"]


@needs_jsonschema
def test_tool_arg_schema_fails_on_missing_required():
    spans = [span("create_ticket", input={"title": "x"})]
    result = tool_arg_schema.run(instance(spans),
                                 {"schemas": {"create_ticket": TICKET_SCHEMA}},
                                 ctx())
    assert result.status == "fail"
    violation = result.evidence["values"]["violations"][0]
    assert violation["keyword"] == "required"
    assert violation["message"] == 'missing required property "priority"'
    assert violation["pointer"] == ""
    assert result.evidence["span_ids"] == [spans[0].id]


@needs_jsonschema
def test_tool_arg_schema_normalizes_type_message():
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
    spans = [span("create_ticket", input={"count": "two"})]
    result = tool_arg_schema.run(instance(spans),
                                 {"schemas": {"create_ticket": schema}}, ctx())
    assert result.status == "fail"
    violation = result.evidence["values"]["violations"][0]
    assert violation["pointer"] == "/count"
    assert violation["message"] == "expected integer, got string"


@needs_jsonschema
def test_tool_arg_schema_null_input_fails_by_default():
    spans = [span("create_ticket", input=None)]
    result = tool_arg_schema.run(instance(spans),
                                 {"schemas": {"create_ticket": TICKET_SCHEMA}},
                                 ctx())
    assert result.status == "fail"
    assert result.evidence["values"]["violations"][0]["keyword"] == "null_input"


@needs_jsonschema
def test_tool_arg_schema_null_input_skip_span_moves_to_uncovered():
    spans = [span("create_ticket", input=None)]
    result = tool_arg_schema.run(
        instance(spans),
        {"schemas": {"create_ticket": TICKET_SCHEMA}, "null_input": "skip_span"},
        ctx())
    assert result.status == "pass"
    assert result.evidence["values"]["checked_spans"] == 0
    assert result.evidence["values"]["uncovered_spans"] == 1


def test_tool_arg_schema_skips_without_schemas():
    result = tool_arg_schema.run(instance([span("create_ticket")]), {}, ctx())
    assert result.status == "skip"
    assert "schemas" in result.remedy


@needs_jsonschema
def test_tool_arg_schema_skips_when_nothing_covered():
    spans = [span("send_email", input={"to": "a"})]
    result = tool_arg_schema.run(instance(spans),
                                 {"schemas": {"create_ticket": TICKET_SCHEMA}},
                                 ctx())
    assert result.status == "skip"
    assert "send_email" in result.details


@needs_jsonschema
def test_tool_arg_schema_skips_on_remote_ref():
    schema = {"$ref": "https://example.com/x.json"}
    result = tool_arg_schema.run(instance([span("create_ticket", input={})]),
                                 {"schemas": {"create_ticket": schema}}, ctx())
    assert result.status == "skip"
    assert "$ref" in result.details


@needs_jsonschema
def test_tool_arg_schema_skips_on_wrong_draft():
    schema = {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"}
    result = tool_arg_schema.run(instance([span("create_ticket", input={})]),
                                 {"schemas": {"create_ticket": schema}}, ctx())
    assert result.status == "skip"
    assert "draft" in result.details


# ---------------------------------------------------------------------------
# core.forbidden_actions
# ---------------------------------------------------------------------------

def test_forbidden_actions_passes_when_absent():
    inst = instance([span("read_account"), span("send_email")])
    result = forbidden_actions.run(inst, {"forbidden": ["delete_customer_record"]},
                                   ctx())
    assert result.status == "pass"
    assert result.evidence["values"]["offenders"] == []


def test_forbidden_actions_fails_on_exact_and_pattern():
    spans = [span("delete_customer_record"), span("admin_force_refund")]
    result = forbidden_actions.run(
        instance(spans),
        {"forbidden": ["delete_customer_record"], "forbidden_patterns": ["^admin_"]},
        ctx())
    assert result.status == "fail"
    offenders = result.evidence["values"]["offenders"]
    assert [o["rule"] for o in offenders] == ["exact", "pattern"]
    assert offenders[0]["matched"] == "delete_customer_record"
    assert offenders[1]["matched"] == "^admin_"
    assert len(result.evidence["span_ids"]) == 2


def test_forbidden_actions_case_insensitive_exact():
    result = forbidden_actions.run(
        instance([span("DELETE_USER")]),
        {"forbidden": ["delete_user"], "case_sensitive": False}, ctx())
    assert result.status == "fail"
    assert result.evidence["values"]["offenders"][0]["rule"] == "exact"


def test_forbidden_actions_types_filter_excludes_span():
    result = forbidden_actions.run(
        instance([span("drop_table", type="other")]),
        {"forbidden": ["drop_table"], "types": ["tool_call"]}, ctx())
    assert result.status == "pass"


def test_forbidden_actions_skips_without_rules():
    result = forbidden_actions.run(instance([span("x")]), {}, ctx())
    assert result.status == "skip"
    assert "forbidden_patterns" in result.remedy


def test_forbidden_actions_skips_on_unportable_pattern():
    result = forbidden_actions.run(instance([span("x")]),
                                   {"forbidden_patterns": ["(?<=a)b"]}, ctx())
    assert result.status == "skip"
    assert "lookbehind" in result.details


def test_forbidden_actions_skips_on_invalid_pattern():
    result = forbidden_actions.run(instance([span("x")]),
                                   {"forbidden_patterns": ["([a-z"]}, ctx())
    assert result.status == "skip"
    assert "compile" in result.details


# ---------------------------------------------------------------------------
# core.cost_budget
# ---------------------------------------------------------------------------

def test_cost_budget_passes_within_budget():
    inst = instance(cost=0.1, metadata={"cost_breakdown": {"llm_cost": 0.1}})
    result = cost_budget.run(inst, {"max_cost": 0.25}, ctx())
    assert result.status == "pass"
    assert result.evidence["values"]["cost"] == 0.1
    assert result.evidence["values"]["cost_breakdown"] == {"llm_cost": 0.1}


def test_cost_budget_zero_cost_is_a_real_value():
    result = cost_budget.run(instance(cost=0), {"max_cost": 0.25}, ctx())
    assert result.status == "pass"
    assert result.evidence["values"]["cost"] == 0


def test_cost_budget_fails_over_budget():
    result = cost_budget.run(instance(cost=0.412), {"max_cost": 0.25}, ctx())
    assert result.status == "fail"
    assert result.details == "Instance cost 0.4120 exceeds budget of 0.2500."
    assert result.evidence["span_ids"] == []
    assert round(result.evidence["values"]["overage"], 6) == 0.162


def test_cost_budget_skips_on_null_cost():
    result = cost_budget.run(instance(cost=None), {"max_cost": 0.25}, ctx())
    assert result.status == "skip"
    assert "cost" in result.remedy


def test_cost_budget_skips_without_max_cost():
    result = cost_budget.run(instance(cost=0.4), {}, ctx())
    assert result.status == "skip"
    assert "max_cost" in result.remedy


# ---------------------------------------------------------------------------
# core.latency_budget
# ---------------------------------------------------------------------------

def test_latency_budget_passes_within_ceilings():
    spans = [span("fast", duration_ms=100), span("open", duration_ms=None)]
    result = latency_budget.run(instance(spans, duration_ms=5000),
                                {"max_span_ms": 1000, "max_instance_ms": 60000},
                                ctx())
    assert result.status == "pass"
    assert result.evidence["values"]["measured_spans"] == 1
    assert result.evidence["values"]["unmeasured_spans"] == 1
    assert result.evidence["values"]["unmeasured_span_ids"] == [spans[1].id]


def test_latency_budget_fails_on_slow_span():
    spans = [span("generate_report", duration_ms=41220), span("fast", duration_ms=5)]
    result = latency_budget.run(instance(spans), {"max_span_ms": 10000}, ctx())
    assert result.status == "fail"
    assert result.details == 'Span "generate_report" took 41220 ms, ceiling is 10000 ms.'
    assert result.evidence["values"]["over_span_count"] == 1
    assert result.evidence["values"]["slowest_spans"][0]["name"] == "generate_report"
    assert result.evidence["span_ids"] == [spans[0].id]


def test_latency_budget_fails_on_instance_only():
    result = latency_budget.run(instance([], duration_ms=96400),
                                {"max_instance_ms": 60000}, ctx())
    assert result.status == "fail"
    assert result.evidence["values"]["instance_over"] is True
    assert result.evidence["span_ids"] == []


def test_latency_budget_ignore_spans_exempts_by_name():
    spans = [span("generate_report", duration_ms=41220)]
    result = latency_budget.run(instance(spans, duration_ms=100),
                                {"max_span_ms": 10, "max_instance_ms": 60000,
                                 "ignore_spans": ["generate_report"]}, ctx())
    assert result.status == "pass"


def test_latency_budget_runs_on_defaults_without_config():
    # Latency now ships a generous default and runs zero config.
    slow = span("llm-call", type="llm_call", duration_ms=120000)
    result = latency_budget.run(instance([slow]), {}, ctx())
    assert result.status == "fail"




# core.escalation_rule
# ---------------------------------------------------------------------------

TYPE_MAP = {"support:transfer_to_human": "handoff"}


def test_escalation_rule_passes_when_handoff_follows():
    spans = [
        span("sentiment_negative"),
        span("transfer", type="handoff", schema_name="support:transfer_to_human"),
    ]
    result = escalation_rule.run(instance(spans),
                                 {"trigger_names": ["sentiment_negative"]},
                                 ctx(TYPE_MAP))
    assert result.status == "pass"
    assert result.evidence["values"]["handoff_spans_after_trigger"] == 1
    assert result.evidence["values"]["trigger_rule"] == "name"


def test_escalation_rule_vacuous_pass_without_trigger():
    spans = [span("greet"), span("t", type="handoff",
                                 schema_name="support:transfer_to_human")]
    result = escalation_rule.run(instance(spans),
                                 {"trigger_names": ["sentiment_negative"]},
                                 ctx(TYPE_MAP))
    assert result.status == "pass"
    assert result.evidence["values"]["trigger_count"] == 0
    assert "vacuous" in result.details


def test_escalation_rule_fails_when_handoff_only_before_trigger():
    spans = [
        span("early", type="handoff", schema_name="support:transfer_to_human"),
        span("sentiment_negative"),
        span("wrap_up"),
    ]
    result = escalation_rule.run(instance(spans),
                                 {"trigger_names": ["sentiment_negative"]},
                                 ctx(TYPE_MAP))
    assert result.status == "fail"
    values = result.evidence["values"]
    assert values["trigger_index"] == 2
    assert values["span_count"] == 3
    assert values["handoff_spans_before_trigger"] == 1
    assert values["handoff_spans_after_trigger"] == 0
    assert set(result.evidence["span_ids"]) == {spans[0].id, spans[1].id}


def test_escalation_rule_value_trigger_is_type_sensitive():
    spans = [span("check", output={"escalate": "true"})]
    result = escalation_rule.run(
        instance(spans), {"trigger_values": {"escalate": True}}, ctx(TYPE_MAP))
    assert result.status == "pass"
    assert result.evidence["values"]["trigger_count"] == 0


def test_escalation_rule_value_trigger_fires_on_exact_match():
    spans = [span("check", output={"flags": {"escalate": True}})]
    result = escalation_rule.run(
        instance(spans), {"trigger_values": {"flags.escalate": True}},
        ctx(TYPE_MAP))
    assert result.status == "fail"
    assert result.evidence["values"]["trigger_rule"] == "value"
    assert result.evidence["values"]["trigger_matched"] == "flags.escalate"


def test_escalation_rule_skips_without_handoff_type():
    spans = [span("sentiment_negative")]
    result = escalation_rule.run(instance(spans),
                                 {"trigger_names": ["sentiment_negative"]}, ctx())
    assert result.status == "skip"
    assert "span_type_map" in result.remedy


def test_escalation_rule_skips_without_triggers():
    spans = [span("t", type="handoff", schema_name="support:transfer_to_human")]
    result = escalation_rule.run(instance(spans), {}, ctx(TYPE_MAP))
    assert result.status == "skip"
    assert "trigger_names" in result.remedy


# ---------------------------------------------------------------------------
# core.output_schema
# ---------------------------------------------------------------------------

BOOKING_SCHEMA = {
    "type": "object",
    "properties": {"booking_id": {"type": "string"},
                   "confirmed": {"type": "boolean"}},
    "required": ["booking_id"],
}


@needs_jsonschema
def test_output_schema_passes_on_valid_output():
    root = span("booking:complete", parent_id=None,
                output={"booking_id": "b1", "confirmed": True})
    inst = instance([root], output=root.output)
    result = output_schema.run(inst, {"schema": BOOKING_SCHEMA}, ctx())
    assert result.status == "pass"
    assert result.evidence["span_ids"] == [root.id]
    assert result.evidence["values"]["violation_count"] == 0


@needs_jsonschema
def test_output_schema_fails_and_normalizes_messages():
    root = span("booking:complete", parent_id=None, output={"confirmed": "yes"})
    inst = instance([root], output=root.output)
    result = output_schema.run(inst, {"schema": BOOKING_SCHEMA}, ctx())
    assert result.status == "fail"
    messages = [v["message"] for v in result.evidence["values"]["violations"]]
    assert 'missing required property "booking_id"' in messages
    assert "expected boolean, got string" in messages
    assert result.evidence["values"]["violation_count"] == 2
    assert result.evidence["values"]["root_span_name"] == "booking:complete"


def test_output_schema_skips_without_schema():
    root = span("r", parent_id=None, output={})
    result = output_schema.run(instance([root], output={}), {}, ctx())
    assert result.status == "skip"
    assert "schema" in result.remedy


@needs_jsonschema
def test_output_schema_skips_on_multiple_roots():
    roots = [span("a", parent_id=None, output={}), span("b", parent_id=None,
                                                        output={})]
    result = output_schema.run(instance(roots), {"schema": BOOKING_SCHEMA}, ctx())
    assert result.status == "skip"
    assert result.evidence["values"]["root_span_count"] == 2


@needs_jsonschema
def test_output_schema_skips_on_no_root():
    result = output_schema.run(instance([span("a")]), {"schema": BOOKING_SCHEMA},
                               ctx())
    assert result.status == "skip"
    assert result.evidence["values"]["root_span_count"] == 0


@needs_jsonschema
def test_output_schema_null_output_skips_by_default():
    root = span("r", parent_id=None, output=None)
    result = output_schema.run(instance([root]), {"schema": BOOKING_SCHEMA}, ctx())
    assert result.status == "skip"
    assert "null_output" in result.remedy


@needs_jsonschema
def test_output_schema_null_output_can_fail():
    root = span("r", parent_id=None, output=None)
    result = output_schema.run(instance([root]),
                               {"schema": BOOKING_SCHEMA, "null_output": "fail"},
                               ctx())
    assert result.status == "fail"
    assert result.evidence["span_ids"] == [root.id]


# ---------------------------------------------------------------------------
# Optional jsonschema extra
# ---------------------------------------------------------------------------

@pytest.fixture
def no_jsonschema(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("no jsonschema")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_tool_arg_schema_skips_without_the_extra(no_jsonschema):
    spans = [span("create_ticket", input={"priority": "high"})]
    result = tool_arg_schema.run(instance(spans),
                                 {"schemas": {"create_ticket": TICKET_SCHEMA}},
                                 ctx())
    assert result.status == "skip"
    assert "schema]" in result.remedy


def test_output_schema_skips_without_the_extra(no_jsonschema):
    root = span("r", parent_id=None, output={"booking_id": "b1"})
    result = output_schema.run(instance([root], output=root.output),
                               {"schema": BOOKING_SCHEMA}, ctx())
    assert result.status == "skip"
    assert "schema]" in result.remedy


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_every_core_eval_is_registered():
    from prefactor_evals_no_llm import registry

    expected = {
        "core.cost_budget", "core.escalation_rule", "core.forbidden_actions",
        "core.latency_budget", "core.loop_detection", "core.output_schema",
        "core.redundant_tool_calls",
        "core.termination_state", "core.tool_arg_schema",
    }
    assert expected <= set(registry.all_evals())


# core.errors

def test_errors_passes_with_no_failed_spans():
    from prefactor_evals_no_llm.evals.core import errors
    inst = instance([span("a"), span("b", state="complete")])
    assert errors.run(inst, {}, ctx()).status == "pass"


def test_errors_fails_on_a_failed_span_and_names_it():
    from prefactor_evals_no_llm.evals.core import errors
    bad = span("charge_card", state="failed")
    bad.error = {"error_type": "Timeout", "message": "gateway timed out"}
    result = errors.run(instance([span("a"), bad]), {}, ctx())
    assert result.status == "fail"
    assert "charge_card" in result.details and "gateway timed out" in result.details


def test_errors_tolerance_can_be_raised():
    from prefactor_evals_no_llm.evals.core import errors
    inst = instance([span("a", state="failed")])
    assert errors.run(inst, {"max_failures": 1}, ctx()).status == "pass"


# core.efficiency, self calibrating

def _batch(span_counts, state="complete"):
    out = []
    for n, count in enumerate(span_counts):
        out.append(instance([span("s%d" % i) for i in range(count)],
                            instance_id="run-%02d" % n, state=state))
    return out


def test_efficiency_skips_with_too_few_runs_to_calibrate():
    from prefactor_evals_no_llm.evals.core import efficiency
    batch = _batch([5, 5])  # below MIN_RUNS_FOR_BASELINE
    result = efficiency.run(batch[0], {}, ctx(batch))
    assert result.status == "skip"
    assert "too few" in result.details


def test_efficiency_flags_a_run_far_above_the_agents_median():
    from prefactor_evals_no_llm.evals.core import efficiency
    # Six normal runs and one bloated. Median is 5, ceiling max(15, 12) = 15.
    batch = _batch([5, 5, 5, 5, 5, 5, 40])
    bloated = batch[-1]
    assert efficiency.run(bloated, {}, ctx(batch)).status == "fail"
    assert efficiency.run(batch[0], {}, ctx(batch)).status == "pass"


def test_efficiency_uses_only_completed_runs_for_the_baseline():
    from prefactor_evals_no_llm.evals.core import efficiency
    # A failed run took 60 spans because it broke; it must not inflate normal.
    batch = _batch([5, 5, 5, 5, 5, 5])
    batch += _batch([60], state="failed")
    # The 60 span run is not in the baseline, so median stays 5. A 20 span
    # completed run is still over the ceiling.
    twenty = instance([span("s%d" % i) for i in range(20)], instance_id="big")
    result = efficiency.run(twenty, {}, ctx(batch + [twenty]))
    assert result.status == "fail"


def test_efficiency_is_deterministic():
    from prefactor_evals_no_llm.evals.core import efficiency
    batch = _batch([5, 5, 5, 5, 5, 5, 40])
    a = efficiency.run(batch[-1], {}, ctx(batch)).to_dict()
    b = efficiency.run(batch[-1], {}, ctx(batch)).to_dict()
    assert a == b


# core.conversation_length, self calibrating

def _convo(turn_count, extra=0, state="complete", iid=None):
    spans = [span("reply", type="output") for _ in range(turn_count)]
    spans += [span("llm", type="llm_call") for _ in range(extra)]
    return instance(spans, instance_id=iid, state=state)


def test_conversation_length_skips_when_nothing_looks_like_a_turn():
    from prefactor_evals_no_llm.evals.core import conversation_length
    inst = instance([span("llm", type="llm_call"), span("tool", type="tool_call")])
    result = conversation_length.run(inst, {}, ctx([inst]))
    assert result.status == "skip"
    assert "conversation" in result.details


def test_conversation_length_flags_a_run_far_above_the_median():
    from prefactor_evals_no_llm.evals.core import conversation_length
    batch = [_convo(2, iid="c%d" % n) for n in range(6)] + [_convo(14, iid="long")]
    long = batch[-1]
    assert conversation_length.run(long, {}, ctx(batch)).status == "fail"
    assert conversation_length.run(batch[0], {}, ctx(batch)).status == "pass"
