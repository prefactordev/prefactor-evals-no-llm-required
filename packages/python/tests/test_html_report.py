"""The HTML view, rendered from real report objects with no network."""

from __future__ import annotations

import os

import pytest

from prefactor_evals_no_llm import runner
from prefactor_evals_no_llm.fixtures import load_instances
from prefactor_evals_no_llm.html_report import format_duration, render_html
from prefactor_evals_no_llm.pack import Pack
from prefactor_evals_no_llm.result import FAIL

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TRACES = os.path.join(REPO, "examples", "synthetic-traces")

yaml = pytest.importorskip("yaml")


def _report(name="standard"):
    pack = Pack.load(os.path.join(REPO, "spec", "packs", "%s.yaml" % name))
    instances = load_instances(os.path.join(TRACES, "standard.json"), pack.span_type_map)
    return pack, instances, runner.run(pack, instances)


@pytest.mark.parametrize("ms,expected", [
    (None, "unknown"), (0, "0ms"), (1, "1ms"), (999, "999ms"),
    (1000, "1.0s"), (34895, "34.9s"), (59999, "60.0s"),
    (60000, "1m 00s"), (125000, "2m 05s"),
])
def test_durations_are_human_readable(ms, expected):
    """Raw milliseconds are a machine unit. A five figure number tells a reader
    nothing about whether it is slow."""
    assert format_duration(ms) == expected


def test_page_renders_for_every_instance():
    pack, instances, report = _report()
    for instance in instances:
        page = render_html(instance, report, agent_name="Test agent")
        assert page.startswith("<title>")
        assert instance.id in page
        assert "Checks that ran" in page
        # No unresolved template placeholders left behind.
        assert "%(" not in page


def test_failing_checks_name_the_spans_they_failed_on():
    pack, instances, report = _report()
    target = next(
        i for i in instances
        if any(r.instance_id == i.id and r.status == FAIL
               and (r.evidence or {}).get("span_ids") for r in report.results)
    )
    page = render_html(target, report)
    failing = [r for r in report.results
               if r.instance_id == target.id and r.status == FAIL
               and (r.evidence or {}).get("span_ids")]
    for result in failing:
        for span_id in result.evidence["span_ids"][:4]:
            assert span_id[:10] in page


def test_eval_text_is_escaped_not_injected():
    """Eval details carry quotes, angle brackets and regex fragments."""
    pack, instances, report = _report()
    hostile = '<script>alert("x")</script>'
    for result in report.results:
        if result.instance_id == instances[0].id:
            result.details = hostile
            break
    page = render_html(instances[0], report)
    assert hostile not in page
    assert "&lt;script&gt;" in page


def test_unfinished_runs_say_the_verdict_can_change():
    pack, instances, report = _report()
    unfinished = [i for i in instances if i.state not in
                  ("complete", "failed", "cancelled", "terminated")]
    if not unfinished:
        pytest.skip("fixture set has no unfinished instance")
    page = render_html(unfinished[0], report)
    assert "verdict can change" in page


def test_renders_for_every_pack():
    for name in ["standard", "advanced"]:
        pack, instances, report = _report(name)
        page = render_html(instances[0], report)
        assert "%(" not in page
        assert pack.id in page


# --- quality span detection, by payload shape rather than schema name --------

from prefactor_evals_no_llm.html_report import _classify_fields, _is_quality_payload


def test_a_judgement_pairs_a_rating_with_an_explanation():
    # real eval:realtime shape
    assert _is_quality_payload(
        {"signal": "confused", "score": 0.75, "struggling": True,
         "reason": "User explicitly states confusion."})
    # real eval:llm-judge shape
    assert _is_quality_payload(
        {"verdict": "good", "scores": {"tone": 0.95, "resolution": 0.9},
         "summary": "The assistant guided the user through onboarding."})


def test_plain_results_are_not_judgements():
    # a tool result: data, no rating
    assert not _is_quality_payload({"address": "bc1q...", "amount": "0.01"})
    # a model reply: text, no rating
    assert not _is_quality_payload({"content": "Here is how to stake."})
    # empty
    assert not _is_quality_payload({})


def test_fields_are_grouped_by_value_not_by_name():
    """Nothing knows the key names, so an unseen field still renders."""
    groups = _classify_fields({
        "novel_rating": 0.42,
        "novel_flag": True,
        "novel_label": "amber",
        "novel_prose": "x" * 200,
    })
    assert ("novel_rating", 0.42) in groups["scores"]
    assert ("novel_flag", True) in groups["flags"]
    assert ("novel_label", "amber") in groups["labels"]
    assert groups["prose"] and groups["prose"][0][0] == "novel_prose"


# --- meaning, not just shape: colour is a claim ------------------------------

from prefactor_evals_no_llm.html_report import (
    _flag_tone, _label_tone, _score_tone, _severity_polarity)

REALTIME = {"signal": "explicit_frustration", "score": 0.85,
            "struggling": True, "intervened": True,
            "reason": "User expressed explicit frustration about the process."}
JUDGE = {"verdict": "good", "scores": {"tone": 0.95, "resolution": 0.9},
         "summary": "The assistant guided the user through onboarding."}


def test_score_polarity_inverts_between_severity_and_quality():
    """0.85 distress is bad; 0.95 tone is good. Same shape, opposite meaning."""
    assert _severity_polarity(REALTIME) is True
    assert _severity_polarity(JUDGE) is False
    assert _score_tone(85.0, severity=True) == "bad"
    assert _score_tone(95.0, severity=False) == "ok"


def test_a_calm_signal_is_not_treated_as_severity():
    assert _severity_polarity({"signal": "none", "score": 0.1}) is False
    assert _score_tone(10.0, severity=False) == "bad"  # low quality reads bad


def test_intervening_is_not_a_failure():
    """The agent stepping in is it doing the right thing, not a fault."""
    assert _flag_tone("intervened", True) == "a"
    assert _flag_tone("struggling", True) == "f"


def test_unknown_fields_get_no_colour_claim():
    """A wrong claim is worse than no claim, so unseen fields stay neutral."""
    assert _flag_tone("wibble", True) == "n"
    assert _flag_tone("struggling", False) is None
    assert _label_tone("explicit_frustration") == "n"          # no context, no claim
    assert _label_tone("explicit_frustration", severity=True) == "f"  # flagged payload
    assert _label_tone("none") == "p"
