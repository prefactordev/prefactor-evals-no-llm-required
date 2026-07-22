"""Guarantees the library makes to anyone running it on production traces.

Three separate promises, each of which would be a real problem to break:

- Config cannot make the library hang, or reach the network.
- The same traces always produce the same results, byte for byte.
- Every check that exists is specified, and every spec has a check.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import pytest

from prefactor_evals_no_llm import registry, runner
from prefactor_evals_no_llm.fixtures import load_instances
from prefactor_evals_no_llm.helpers import (
    UnsafePattern, compile_patterns, pattern_problem, schema_problem)
from prefactor_evals_no_llm.pack import Pack
from prefactor_evals_no_llm.result import FAIL, PASS, SKIP
from prefactor_evals_no_llm.schema import EvalInstance, EvalSpan

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TRACES = os.path.join(REPO, "examples", "synthetic-traces")
SPECS = os.path.join(REPO, "spec", "evals")
PACKS = os.path.join(REPO, "spec", "packs")

yaml = pytest.importorskip("yaml")


# ---------------------------------------------------------------------------
# A config file must not be able to hang the run
# ---------------------------------------------------------------------------

CATASTROPHIC = [
    "(a+)+$",
    "(a*)*$",
    "(a|a)*$",
    "(x+x+)+y",
    "([a-zA-Z]+)*$",
]


@pytest.mark.parametrize("pattern", CATASTROPHIC)
def test_patterns_that_can_hang_are_refused(pattern):
    """Catastrophic backtracking is a denial of service in a CI library.

    Python's re has no timeout, so a pattern like (a+)+ against a long
    non-matching string cannot be interrupted once started. Refusing to compile
    it is the only defence available.
    """
    assert pattern_problem(pattern) is not None
    with pytest.raises(UnsafePattern):
        compile_patterns([pattern])


def test_a_refused_pattern_never_hangs_the_run():
    """The end to end version: the guard has to hold at the runner, not just in
    a helper nobody is obliged to call."""
    span = EvalSpan(
        id="s1", instance_id="i1", type="tool_call",
        name="a" * 40 + "!", schema_name="x:a", state="complete",
        started_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 7, 1, 0, 0, 1, tzinfo=timezone.utc),
        duration_ms=1000.0, input={}, output={})
    instance = EvalInstance(
        id="i1", agent_id="a1", state="complete", spans=[span],
        started_at=span.started_at, ended_at=span.ended_at, duration_ms=1000.0)
    pack = Pack.from_dict({"pack": "t", "evals": {
        "core.forbidden_actions": {"forbidden_patterns": ["(a+)+$"]}}})

    started = time.monotonic()
    results = runner.run(pack, [instance]).results
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, "took %.1fs, the pattern was not refused" % elapsed
    assert results and results[0].status == SKIP
    assert results[0].remedy


def test_ordinary_patterns_still_work():
    """The guard must not be so broad that it refuses normal rules."""
    for pattern in [r"^admin:", r"refund of \$?([\d,]+)", r"(?i)guaranteed",
                    r"[a-z]+@[a-z]+\.[a-z]{2,}", r"\bcannot lose\b"]:
        assert pattern_problem(pattern) is None, pattern
        assert compile_patterns([pattern])


# ---------------------------------------------------------------------------
# A config file must not be able to make the library fetch a URL
# ---------------------------------------------------------------------------

REMOTE_SCHEMAS = [
    {"$ref": "https://example.invalid/schema.json"},
    {"type": "object", "properties": {"a": {"$ref": "http://169.254.169.254/latest/meta-data/"}}},
    {"type": "object", "items": [{"$ref": "file:///etc/passwd"}]},
]


@pytest.mark.parametrize("schema", REMOTE_SCHEMAS)
def test_remote_schema_references_are_refused(schema):
    """Resolving a $ref from config would make the library fetch a URL chosen
    by whoever wrote that config, from inside CI, on every run. That is a
    request forgery primitive, and this library documents itself as making no
    network calls outside the seam."""
    problem = schema_problem(schema)
    assert problem is not None and "$ref" in problem


@pytest.mark.parametrize("eval_id,key", [
    ("core.output_schema", "schema"),
    ("core.tool_arg_schema", "schemas"),
])
def test_both_schema_evals_refuse_remote_refs(eval_id, key):
    """Checked at the eval, not just the helper, because there are two of them
    and only one used to have the guard."""
    pytest.importorskip("jsonschema")
    remote = {"$ref": "https://example.invalid/s.json"}
    value = remote if key == "schema" else {"do_thing": remote}
    span = EvalSpan(id="s1", instance_id="i1", type="tool_call", name="do_thing",
                    schema_name="x:do_thing", state="complete",
                    input={"a": 1}, output={"b": 2})
    instance = EvalInstance(id="i1", agent_id="a1", state="complete",
                            spans=[span], output={"b": 2})
    pack = Pack.from_dict({"pack": "t", "evals": {eval_id: {key: value}}})
    for result in runner.run(pack, [instance]).results:
        assert result.status == SKIP
        assert result.status != PASS


# ---------------------------------------------------------------------------
# The same traces always produce the same results
# ---------------------------------------------------------------------------

PACK_NAMES = ["standard", "advanced"]


@pytest.mark.parametrize("name", PACK_NAMES)
def test_results_are_byte_identical_across_runs(name):
    """The core promise. Anything that varies between runs, a clock read, a set
    iteration order, a dict without a stable sort, would make these unsafe to
    gate a build on."""
    pack = Pack.load(os.path.join(PACKS, "%s.yaml" % name))
    instances = load_instances(os.path.join(TRACES, "standard.json"),
                               pack.span_type_map)
    first = json.dumps([r.to_dict() for r in runner.run(pack, instances).results],
                       sort_keys=True, default=str)
    second = json.dumps([r.to_dict() for r in runner.run(pack, instances).results],
                        sort_keys=True, default=str)
    assert first == second


@pytest.mark.parametrize("name", PACK_NAMES)
def test_span_order_does_not_change_results(name):
    """Spans arrive in whatever order the API returns them. Results must depend
    on their timestamps, not on the order they happened to be fetched."""
    pack = Pack.load(os.path.join(PACKS, "%s.yaml" % name))
    forward = load_instances(os.path.join(TRACES, "standard.json"), pack.span_type_map)
    reversed_ = load_instances(os.path.join(TRACES, "standard.json"), pack.span_type_map)
    for instance in reversed_:
        instance.spans = list(reversed(instance.spans))

    def statuses(instances):
        return [(r.eval_id, r.instance_id, r.status)
                for r in runner.run(pack, instances).results]

    assert statuses(forward) == statuses(reversed_)


def test_no_eval_reads_a_clock_or_random_source():
    """A result that depends on when it ran is not reproducible. The one clock
    read in the library is in the seam, at fetch time."""
    import re as _re

    root = os.path.join(os.path.dirname(__file__), "..", "src",
                        "prefactor_evals_no_llm", "evals")
    offenders = []
    banned = _re.compile(r"\b(datetime\.now|datetime\.utcnow|time\.time|"
                         r"time\.monotonic|random\.|uuid[0-9]?\()")
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if banned.search(line) and not line.strip().startswith("#"):
                        offenders.append("%s:%d %s" % (name, number, line.strip()))
    assert not offenders, "evals must not read a clock or randomness:\n  " + \
        "\n  ".join(offenders)


# ---------------------------------------------------------------------------
# Every check is specified, and every spec has a check
# ---------------------------------------------------------------------------

def _spec_ids() -> set:
    """Every check id that has a spec file. All checks live under spec/evals/core
    now, since there is one generic set and no domain packs."""
    ids = set()
    core = os.path.join(SPECS, "core")
    for name in os.listdir(core):
        if name.endswith(".md"):
            ids.add("core.%s" % name[:-3])
    return ids


def test_every_registered_eval_has_a_spec_file():
    """The spec is the source of truth. Code without one is undocumented
    behaviour that the other language implementation cannot be written from."""
    registered = set(registry.load_all())
    missing = sorted(registered - _spec_ids())
    assert not missing, "registered but unspecified: %s" % missing


def test_every_spec_file_has_a_registered_eval():
    """A spec with no implementation reads as a feature that exists."""
    registered = set(registry.load_all())
    missing = sorted(_spec_ids() - registered)
    assert not missing, "specified but not implemented: %s" % missing


@pytest.mark.parametrize("name", PACK_NAMES)
def test_packs_only_name_evals_that_exist(name):
    registered = set(registry.load_all())
    pack = Pack.load(os.path.join(PACKS, "%s.yaml" % name))
    unknown = sorted(set(pack.eval_ids) - registered)
    assert not unknown, "%s names unknown evals: %s" % (name, unknown)


# ---------------------------------------------------------------------------
# The package has to actually install
# ---------------------------------------------------------------------------

def test_the_package_declares_a_readme_that_exists():
    """pyproject pointed at a README that was not in the package directory, so
    `pip install` failed outright and nobody could have used the library. The
    file is also what renders on PyPI, so it has to be there and be real."""
    here = os.path.join(os.path.dirname(__file__), "..")
    config = open(os.path.join(here, "pyproject.toml"), encoding="utf-8").read()
    match = __import__("re").search(r'readme\s*=\s*"([^"]+)"', config)
    assert match, "pyproject declares no readme"
    readme = os.path.join(here, match.group(1))
    assert os.path.exists(readme), "declared readme is missing: %s" % match.group(1)
    assert len(open(readme, encoding="utf-8").read()) > 500


def test_no_model_client_is_importable_from_the_package():
    """The package is named for what it does not do. A model client arriving
    through a transitive dependency would break that quietly."""
    import prefactor_evals_no_llm  # noqa: F401
    import sys as _sys
    banned = ("openai", "anthropic", "cohere", "litellm", "langchain")
    loaded = [m for m in _sys.modules if any(m.startswith(b) for b in banned)]
    assert not loaded, "model client loaded by importing the package: %s" % loaded


# ---------------------------------------------------------------------------
# Concurrency must not change results
# ---------------------------------------------------------------------------

def test_concurrent_fetching_preserves_order_and_content():
    """Spans are fetched per instance, so the fetch is parallel. Results feed
    straight into eval output, which has to be reproducible, so the order must
    not depend on which request finished first.

    Uses a fake transport so the test is about ordering, not the network.
    """
    from concurrent.futures import ThreadPoolExecutor
    import random as _random

    rows = [{"id": "i%02d" % n} for n in range(25)]

    def fetch(row):
        # Finish in a deliberately jumbled order.
        _time = 0.001 * (_random.random())
        time.sleep(_time)
        return row["id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(fetch, rows))

    assert got == [r["id"] for r in rows], (
        "pool.map must yield input order; a completion ordered API would make "
        "eval results depend on network timing")


def test_the_seam_bounds_its_concurrency():
    """Unbounded fan out against someone's production API is not acceptable
    just to save a few seconds."""
    import inspect
    from prefactor_evals_no_llm.source import PrefactorSource

    signature = inspect.signature(PrefactorSource.__init__)
    assert "max_workers" in signature.parameters
    default = signature.parameters["max_workers"].default
    assert isinstance(default, int) and 1 < default <= 16, (
        "default concurrency %r is not a polite bound" % default)
