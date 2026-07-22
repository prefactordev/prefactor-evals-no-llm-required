"""Every eval, against config and traces designed to break it.

An eval is handed two things it does not control: config a person typed, and
trace data from someone else's agent. Both arrive malformed routinely. The
contract is that an eval always returns pass, fail or skip, and never raises,
because a raised exception reaches the user as "this is a bug in the eval",
which is both wrong and unactionable when the real problem is a typo.

This suite exists because exactly that happened during development: a check
crashed on config written in the wrong shape, and the crash reached the user as
a false bug report. The fix for one check is worth little if the others share
the flaw, so this exercises every registered check.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import pytest

from prefactor_evals_no_llm import registry
from prefactor_evals_no_llm.pack import Pack
from prefactor_evals_no_llm.runner import run
from prefactor_evals_no_llm.config import ConfigError
from prefactor_evals_no_llm.result import FAIL, PASS, SKIP
from prefactor_evals_no_llm.schema import EvalInstance, EvalSpan

SRC = os.path.join(os.path.dirname(__file__), "..", "src", "prefactor_evals_no_llm", "evals")
VALID = (PASS, FAIL, SKIP)

# Values a person might plausibly put in a config file by mistake, plus a few
# that are outright hostile. Each is wrong for almost any key.
HOSTILE_VALUES = [
    None,
    "",
    "a string where a structure belongs",
    0,
    -1,
    10 ** 12,
    -0.5,
    True,
    [],
    {},
    ["not", "a", "mapping"],
    {"wrong": "shape"},
    [{"missing": "keys"}],
    [None],
    {"nested": {"too": {"deep": [1, 2]}}},
    "(unclosed group",          # invalid regex
    "(a+)+$",                   # catastrophic backtracking if fed to a matcher
    {"type": "not-a-real-json-schema-keyword"},
    [[["deeply", "nested"]]],
]


def _config_keys(eval_id: str) -> set:
    """The config keys an eval actually reads, scraped from its source."""
    module = eval_id.split(".", 1)[-1]
    keys = set()
    for root, _dirs, files in os.walk(SRC):
        for name in files:
            if name != module + ".py":
                continue
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                text = handle.read()
                keys |= set(re.findall(r'config\.get\("([a-z_]+)"', text))
                # The typed readers are the normal way to read config now, so a
                # scraper that only knew config.get would test almost nothing.
                keys |= set(re.findall(r'cfg_\w+\(config, "([a-z_]+)"', text))
    return keys


def _span(span_id="s1", **kwargs):
    base = dict(
        id=span_id, instance_id="i1", type="tool_call", name="do_thing",
        schema_name="x:do_thing", state="complete",
        started_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 7, 1, 0, 0, 1, tzinfo=timezone.utc),
        duration_ms=1000.0, input={"a": 1}, output={"b": 2},
    )
    base.update(kwargs)
    return EvalSpan(**base)


def _instance(**kwargs):
    base = dict(
        id="i1", agent_id="a1", state="complete",
        started_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 7, 1, 0, 1, tzinfo=timezone.utc),
        duration_ms=60000.0, spans=[_span()], input={"q": "hi"}, output={"a": "yes"},
        cost=0.1, metadata={},
    )
    base.update(kwargs)
    return EvalInstance(**base)


# Traces that are legal but awkward. Every one of these shapes was seen in real
# Prefactor data during development, except where noted.
HOSTILE_INSTANCES = {
    "no spans": _instance(spans=[]),
    "null output": _instance(output=None),
    "null input": _instance(input=None),
    "null cost": _instance(cost=None),
    "no timestamps": _instance(started_at=None, ended_at=None, duration_ms=None),
    "span with no duration": _instance(spans=[_span(duration_ms=None, ended_at=None)]),
    "span with null payloads": _instance(spans=[_span(input=None, output=None)]),
    "span with empty name": _instance(spans=[_span(name="", schema_name="")]),
    "unfinished run": _instance(state="active", ended_at=None, duration_ms=None),
    "string payloads": _instance(spans=[_span(input="text", output="text")],
                                 input="text", output="text"),
    "list payloads": _instance(spans=[_span(input=[1, 2], output=[3])]),
    "many identical spans": _instance(spans=[_span("s%d" % i) for i in range(50)]),
}


def _all_evals():
    registry.load_all()
    return sorted(registry.all_evals().items())


@pytest.mark.parametrize("eval_id,registered", _all_evals())
def test_bad_config_never_kills_a_run_or_blames_the_eval(eval_id, registered):
    """The contract a user actually experiences, so it is tested through the
    runner rather than by calling an eval directly.

    Whatever an eval does internally, a wrong config value must come back as a
    skip carrying something the user can act on, and must never stop the run.

    A pass is not asserted against here, because some of these values are
    legitimately absent: `None` means the key was not set, so the eval falls
    back to its default and may correctly pass. The dangerous subset, a value
    that looks configured but is unusable, is pinned separately below.

    An eval that raises a plain TypeError still
    satisfies this because the runner catches it; what it loses is the precise
    message naming the key, which is tracked separately below.
    """
    from prefactor_evals_no_llm.pack import Pack
    from prefactor_evals_no_llm.runner import run

    instance = _instance()
    failures = []
    for key in _config_keys(eval_id):
        for value in HOSTILE_VALUES:
            pack = Pack.from_dict({"pack": "t", "evals": {eval_id: {key: value}}})
            for result in run(pack, [instance]).results:
                if result.status not in VALID:
                    failures.append("%s=%r gave status %r" % (key, value, result.status))
                elif result.status == SKIP and not (result.remedy or result.details):
                    failures.append("%s=%r skipped with no explanation" % (key, value))
    assert not failures, "%s mishandled its own config:\n  %s" % (
        eval_id, "\n  ".join(failures[:8]))


# How many evals reject bad config precisely, naming the offending key, rather
# than falling back to the runner's generic catch. Precision is the goal, the
# generic catch is the safety net. Recorded so the number only moves up.
PRECISE_TODAY = 9


def test_precise_config_errors_do_not_regress():
    """Evals that name the bad key, by raising ConfigError from config.py.

    The remaining few read config in shapes an automated conversion could not
    prove the type of, so they were deliberately left alone: an unproven
    conversion silently changes what a check measures, which is worse than a
    less precise message. The runner still catches them, so nothing crashes.

    Two earlier attempts are worth not repeating. Guessing the type from the key
    name broke 21 tests, because `required_fields`, `target_fields` and
    `identifier_patterns` are mappings that read like lists. Reading the AST but
    dropping the `or (...)` fallback silently changed escalation_rule's default
    from ("handoff",) to nothing, which made it find no handoffs at all.
    """
    instance = _instance()
    context = registry.EvalContext(instances=[instance], span_type_map={})
    precise = set()
    for eval_id, registered in _all_evals():
        for key in _config_keys(eval_id):
            for value in HOSTILE_VALUES:
                try:
                    registered.fn(instance, {key: value}, context)
                except ConfigError:
                    precise.add(eval_id)
                except Exception:  # noqa: BLE001
                    pass
    assert len(precise) >= PRECISE_TODAY, (
        "evals naming the bad config key fell to %d, below the recorded %d"
        % (len(precise), PRECISE_TODAY))


@pytest.mark.parametrize("eval_id,registered", _all_evals())
def test_no_eval_raises_on_hostile_traces(eval_id, registered):
    """Trace data comes from someone else's agent and is often incomplete."""
    failures = []
    for label, instance in HOSTILE_INSTANCES.items():
        context = registry.EvalContext(instances=[instance], span_type_map={})
        try:
            result = registered.fn(instance, {}, context)
        except ConfigError:
            continue
        except Exception as error:  # noqa: BLE001
            failures.append("%s raised %s: %s" % (label, type(error).__name__, error))
            continue
        if result is not None and result.status not in VALID:
            failures.append("%s returned status %r" % (label, result.status))
    assert not failures, "%s did not survive real trace shapes:\n  %s" % (
        eval_id, "\n  ".join(failures[:10]))


def test_every_eval_reads_at_least_one_config_key_or_needs_none():
    """A guard on the scraper itself. If it silently stopped finding keys, the
    config suite above would pass by testing nothing."""
    with_keys = [e for e, _ in _all_evals() if _config_keys(e)]
    # Every check reads config, so the scraper should find keys for all of them.
    assert len(with_keys) >= len(_all_evals()) - 1, (
        "config key scraping found keys for only %d evals, which means the "
        "hostile config suite is barely testing anything" % len(with_keys))


# Config keys whose value is a collection of names. Writing one as a bare string
# is the most natural mistake in YAML, and the most dangerous, because Python
# will happily iterate a string one character at a time.
STRING_FOR_LIST_KEYS = [
    ("core.forbidden_actions", "forbidden", "issue_refund"),
    ("core.loop_detection", "ignore_tools", "lookup_customer"),
]


@pytest.mark.parametrize("eval_id,key,value", STRING_FOR_LIST_KEYS)
def test_a_name_written_as_a_string_never_silently_passes(eval_id, key, value):
    """`forbidden: issue_refund` instead of `forbidden: [issue_refund]`.

    Python turns that string into a set of characters, nothing matches, and the
    check reports success. A green tick that checked nothing is worse than a
    crash: a crash gets investigated, a false pass gets trusted. So this must
    skip or fail, never pass.
    """
    from prefactor_evals_no_llm.pack import Pack
    from prefactor_evals_no_llm.runner import run

    # A run that genuinely contains the named span, so a working check has
    # something real to find.
    instance = _instance(spans=[_span("s1", name=value, schema_name="x:" + value)])
    pack = Pack.from_dict({"pack": "t", "evals": {eval_id: {key: value}}})
    results = run(pack, [instance]).results
    assert results, "eval did not run at all"
    for result in results:
        assert result.status != PASS, (
            '%s passed with %s written as a string. The name "%s" was treated as '
            "the characters %r, so the check silently measured nothing."
            % (eval_id, key, value, sorted(set(value))[:5]))
