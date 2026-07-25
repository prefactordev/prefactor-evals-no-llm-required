"""Publishing scores to the Quality tab, tested without a network.

A fake publisher records what would be sent and simulates the read-merge-write
so the non-destructive behaviour can be asserted.
"""

from __future__ import annotations

import os

import pytest

from prefactor_evals_no_llm import runner
from prefactor_evals_no_llm.fixtures import load_instances
from prefactor_evals_no_llm.pack import Pack
from prefactor_evals_no_llm.publish import (
    PUBLISHABLE_STATES,
    build_block,
    overall_result,
    publish_report,
)
from prefactor_evals_no_llm.source import QUALITY_KEY
from prefactor_evals_no_llm.result import FAIL, PASS, SKIP

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TRACES = os.path.join(REPO, "examples", "synthetic-traces")

yaml = pytest.importorskip("yaml")


class FakePublisher:
    """Mimics PrefactorPublisher's read-merge-write against an in memory store."""

    def __init__(self, existing=None):
        self.store = dict(existing or {})
        self.puts = []

    def publish(self, instance_id, block):
        # Mirrors PrefactorPublisher: block fields at the top level, where the
        # declared schema defines them and its template reads them.
        merged = dict(self.store.get(instance_id) or {})
        merged.update(block)
        self.store[instance_id] = merged
        self.puts.append((instance_id, merged))
        return merged


class ExplodingPublisher:
    def publish(self, instance_id, block):
        raise RuntimeError("network down")


def _report(pack_name="standard"):
    pack = Pack.load(os.path.join(REPO, "spec", "packs", "%s.yaml" % pack_name))
    instances = load_instances(os.path.join(TRACES, "standard.json"),
                               pack.span_type_map)
    return runner.run(pack, instances)


def test_overall_result_prioritises_failure():
    assert overall_result(5, 1, 3) == FAIL
    assert overall_result(5, 0, 3) == PASS
    # All skipped is a skip, never a pass.
    assert overall_result(0, 0, 9) == SKIP


def test_block_shape_and_counts_match_the_report():
    report = _report()
    instance = report.instances[0]
    block = build_block(instance.id, report, "9.9.9")
    per_instance = [r for r in report.results if r.instance_id == instance.id]
    assert block["checks_passed"] + block["checks_failed"] + block["checks_not_run"] \
        == len(per_instance)
    assert block["tool_version"] == "9.9.9"
    assert block["pack"] == "standard"
    assert "evaluated_at" not in block  # omitted unless supplied
    # The rendered summary leads. One line of plain text with no markup: the tab
    # renders this field as escaped plain text with whitespace collapsed, so
    # both markdown and HTML tags would show as literal characters.
    assert list(block.keys())[0] == "report"
    assert "\n" not in block["report"]
    # The pack verdict is always stated. The line may lead with the agent's own
    # recorded eval when the run has one, so this is a containment check.
    assert "standard pack: " in block["report"]
    assert "<" not in block["report"]
    assert "|" not in block["report"]
    # Every check appears in the per check breakdown, failures first.
    assert len(block["checks"]) == len(per_instance)
    labels = [c["result"] for c in block["checks"]]
    assert labels == sorted(labels, key=lambda l: {"Fail": 0, "Pass": 1, "Not run": 2}[l])
    assert block["checks_failed"] == sum(1 for c in block["checks"] if c["result"] == "Fail")
    # Each failure names what happened, and appears in the one line summary.
    for check in block["checks"]:
        assert check["check"] and check["detail"]
        if check["result"] == "Fail":
            assert check["detail"] in block["report"]


def test_block_is_deterministic_without_a_timestamp():
    report = _report()
    iid = report.instances[0].id
    assert build_block(iid, report, "1.0.0") == build_block(iid, report, "1.0.0")


def test_publish_writes_one_block_per_finished_instance():
    report = _report()
    pub = FakePublisher()
    outcomes = publish_report(report, pub, "1.0.0")

    finished = [i for i in report.instances if i.state in PUBLISHABLE_STATES]
    published = [o for o in outcomes if o.status == "published"]
    assert len(published) == len(finished)
    for iid, payload in pub.puts:
        assert payload["result"] in (PASS, FAIL, SKIP)
        assert payload["report"]


def test_publish_never_clobbers_existing_quality_data():
    report = _report()
    iid = report.instances[0].id
    # The user already stores their own quality data under a different key.
    pub = FakePublisher(existing={iid: {"my_app_score": 0.87, "reviewed_by": "qa"}})
    publish_report(report, pub, "1.0.0")

    written = pub.store[iid]
    assert written["my_app_score"] == 0.87
    assert written["reviewed_by"] == "qa"
    assert written["report"]


def test_unfinished_instances_are_skipped_not_scored():
    report = _report()
    pub = FakePublisher()
    outcomes = publish_report(report, pub, "1.0.0")
    for outcome in outcomes:
        instance = next(i for i in report.instances if i.id == outcome.instance_id)
        if instance.state not in PUBLISHABLE_STATES:
            assert outcome.status == "skipped"
            assert instance.id not in pub.store


def test_include_active_scores_unfinished_runs_and_marks_them():
    report = _report()
    pub = FakePublisher()
    outcomes = publish_report(report, pub, "1.0.0", include_active=True)
    # Every instance is now published, none skipped for state.
    assert all(o.status == "published" for o in outcomes)
    for iid, payload in pub.puts:
        instance = next(i for i in report.instances if i.id == iid)
        block = payload
        assert block["instance_state"] == instance.state
        assert block["scored_while_running"] == (
            instance.state not in PUBLISHABLE_STATES)


def test_finished_instances_are_never_marked_scored_while_running():
    report = _report()
    pub = FakePublisher()
    publish_report(report, pub, "1.0.0")
    for _, payload in pub.puts:
        assert payload["scored_while_running"] is False


def test_one_publish_error_does_not_stop_the_rest():
    report = _report()
    outcomes = publish_report(report, ExplodingPublisher(), "1.0.0")
    finished = [i for i in report.instances if i.state in PUBLISHABLE_STATES]
    errored = [o for o in outcomes if o.status == "error"]
    assert len(errored) == len(finished)
    assert all("network down" in o.detail for o in errored)


def test_publishing_is_opt_in_runner_never_writes():
    """A plain run must not construct a publisher or write anything."""
    report = _report()
    # The runner returns results and touches no publisher. Nothing to assert on
    # a mock, so assert the contract directly: run() has no publish parameter.
    import inspect
    assert "publish" not in inspect.signature(runner.run).parameters
    assert "publisher" not in inspect.signature(runner.run).parameters
