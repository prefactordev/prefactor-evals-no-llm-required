"""End to end: real pack files, real fixture traces, full runner and report.

The unit tests check each eval in isolation against hand built objects. This
file checks the parts nobody tests individually: that the shipped pack files
name evals that exist, that fixtures load, that the runner produces a result for
every eval and instance pair, and that the scorecard renders.
"""

from __future__ import annotations

import json
import os

import pytest

from prefactor_evals_no_llm import registry, runner
from prefactor_evals_no_llm.fixtures import FixtureSource, load_instances
from prefactor_evals_no_llm.pack import Pack
from prefactor_evals_no_llm.report import CTA, scorecard, write_json
from prefactor_evals_no_llm.result import FAIL, PASS, SKIP

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PACKS = os.path.join(REPO, "spec", "packs")
TRACES = os.path.join(REPO, "examples", "synthetic-traces")

PACK_NAMES = ["standard", "advanced"]

yaml = pytest.importorskip("yaml", reason="PyYAML is a runtime dependency")


def load_pack(name: str) -> Pack:
    return Pack.load(os.path.join(PACKS, "%s.yaml" % name))


@pytest.mark.parametrize("name", PACK_NAMES)
def test_pack_references_only_real_evals(name):
    """A pack naming an eval that does not exist is a shipping defect: the
    user would silently lose a check they think they configured."""
    registry.load_all()
    pack = load_pack(name)
    unknown = [e for e in pack.eval_ids if registry.get(e) is None]
    assert unknown == [], "pack %s names unknown evals: %s" % (name, unknown)


def test_advanced_pack_includes_every_registered_check():
    """Standard omits the optional config-needing checks on purpose; advanced is
    the everything pack, so it should name every check that exists."""
    registry.load_all()
    every = set(registry.all_evals())
    assert every.issubset(set(load_pack("advanced").eval_ids))


def test_standard_pack_is_all_zero_config():
    """The standard pack must run clean on a fresh agent: nothing in it should
    skip for missing config on a normal completed run."""
    pack = load_pack("standard")
    instances = load_instances(os.path.join(TRACES, "standard.json"), pack.span_type_map)
    report = runner.run(pack, instances)
    needs_config = [r for r in report.results if r.status == SKIP
                    and "config" in (r.remedy or "").lower()]
    assert not needs_config, "standard pack should need no config: %s" % (
        [(r.eval_id, r.remedy) for r in needs_config[:3]])


@pytest.mark.parametrize("name", PACK_NAMES)
def test_fixture_file_loads(name):
    instances = load_instances(os.path.join(TRACES, "standard.json"))
    assert len(instances) >= 6
    for instance in instances:
        assert instance.id
        assert instance.state in (
            "pending", "active", "complete", "failed", "cancelled", "terminated")


@pytest.mark.parametrize("name", PACK_NAMES)
def test_pack_runs_against_its_fixtures(name):
    pack = load_pack(name)
    instances = load_instances(os.path.join(TRACES, "standard.json"),
                               pack.span_type_map)
    report = runner.run(pack, instances)

    # One result per eval per instance, no silent drops.
    assert len(report.results) == len(pack.eval_ids) * len(instances)
    assert report.unknown_eval_ids == []
    for result in report.results:
        assert result.status in (PASS, FAIL, SKIP)
        assert result.details
        assert isinstance(result.evidence["span_ids"], list)
        if result.status == SKIP:
            assert result.remedy, "%s skipped without a remedy" % result.eval_id


@pytest.mark.parametrize("name", PACK_NAMES)
def test_fixtures_actually_trigger_failures(name):
    """The fixtures are built to fail specific checks. If they all pass, either
    the fixtures or the evals have stopped doing their job."""
    pack = load_pack(name)
    instances = load_instances(os.path.join(TRACES, "standard.json"),
                               pack.span_type_map)
    report = runner.run(pack, instances)
    assert report.counts[FAIL] > 0
    assert report.counts[PASS] > 0
    assert report.failed is True


def test_results_are_deterministic():
    """Same input, byte identical output. This is the whole premise."""
    pack = load_pack("standard")
    instances = load_instances(os.path.join(TRACES, "standard.json"),
                               pack.span_type_map)
    first = [r.to_dict() for r in runner.run(pack, instances).results]
    second = [r.to_dict() for r in runner.run(pack, instances).results]
    assert json.dumps(first, sort_keys=True, default=str) == \
        json.dumps(second, sort_keys=True, default=str)


def test_scorecard_renders_and_shows_cta_exactly_once():
    pack = load_pack("standard")
    instances = load_instances(os.path.join(TRACES, "standard.json"),
                               pack.span_type_map)
    text = scorecard(runner.run(pack, instances))
    assert text.count(CTA) == 1
    assert "Coverage:" in text
    assert "Totals:" in text
    # Never shown before results.
    assert text.index("Totals:") < text.index(CTA)


def test_json_report_written(tmp_path):
    pack = load_pack("standard")
    instances = load_instances(os.path.join(TRACES, "standard.json"),
                               pack.span_type_map)
    path = write_json(runner.run(pack, instances), str(tmp_path / "report.json"))
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["schema_version"] == "v1"
    assert data["pack"]["id"] == "standard"
    assert data["run_timestamp"]
    assert data["counts"]["pass"] + data["counts"]["fail"] + data["counts"]["skip"] \
        == len(data["results"])


def test_fixture_source_is_a_drop_in_seam_replacement():
    """The README claims you can replace the seam. This proves it: a source that
    has never heard of Prefactor drives the same runner."""
    pack = load_pack("standard")
    source = FixtureSource(os.path.join(TRACES, "standard.json"),
                           pack.span_type_map)
    instances = source.fetch_instances("", limit=3)
    assert len(instances) == 3
    assert runner.run(pack, instances).results


def test_skip_is_never_counted_as_pass():
    pack = Pack.from_dict({
        "pack": "minimal",
        # max_cost is required and absent, so this must skip, not pass.
        "evals": {"core.cost_budget": {}},
    })
    instances = load_instances(os.path.join(TRACES, "standard.json"))
    report = runner.run(pack, instances)
    assert report.counts[PASS] == 0
    assert report.counts[SKIP] == len(instances)
    assert report.coverage == (0, 1)
    assert report.failed is False


def test_builtin_standard_pack_matches_the_shipped_yaml():
    """The default pack lives in code so an installed package always has it, and
    spec/packs/standard.yaml is its documented copy. They must not drift."""
    from prefactor_evals_no_llm.pack import STANDARD_PACK
    shipped = Pack.load(os.path.join(PACKS, "standard.yaml"))
    assert STANDARD_PACK["evals"] == shipped.evals


def test_bundled_advanced_pack_matches_the_spec_copy():
    """The advanced pack ships inside the package so a pip installed user has
    it, and spec/packs/advanced.yaml is its documented copy. They must not
    drift, byte for byte."""
    import prefactor_evals_no_llm.pack as pack_module
    bundled = os.path.join(os.path.dirname(pack_module.__file__),
                           "packs", "advanced.yaml")
    with open(bundled, encoding="utf-8") as a, \
            open(os.path.join(PACKS, "advanced.yaml"), encoding="utf-8") as b:
        assert a.read() == b.read()


def test_pack_load_resolves_bundled_names():
    """`--pack advanced` works without a checkout: a bare built in name loads
    the bundled pack, and the not-found error names the built ins."""
    from prefactor_evals_no_llm.pack import PackError, STANDARD_PACK
    assert Pack.load("standard").evals == STANDARD_PACK["evals"]
    advanced = Pack.load("advanced")
    assert "core.cost_budget" in advanced.evals
    assert len(advanced.eval_ids) == 12
    try:
        Pack.load("no-such-pack")
        assert False, "should have raised"
    except PackError as error:
        assert "standard, advanced" in str(error)


def test_run_defaults_to_the_standard_pack_with_no_pack_file(tmp_path):
    """`run` with no --pack uses the built in standard pack, so the tool works
    out of the box with nothing but an agent id."""
    from prefactor_evals_no_llm.pack import STANDARD_PACK
    from prefactor_evals_no_llm.fixtures import FixtureSource
    pack = Pack.from_dict(STANDARD_PACK)
    source = FixtureSource(os.path.join(TRACES, "standard.json"))
    instances = source.fetch_instances("")
    report = runner.run(pack, instances)
    # The standard pack has no optional checks, so nothing skips for missing config.
    assert not [r for r in report.results if r.status == SKIP
                and "config" in (r.remedy or "").lower()]
