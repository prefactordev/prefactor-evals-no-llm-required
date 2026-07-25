"""Publish eval scores into Prefactor's Quality tab.

The payload shaping here is pure and has no idea Prefactor exists. The actual
HTTP PUT lives in source.py, which is the only file allowed to know. A publisher
object is passed in, so this module and its tests never touch the network.

Publishing is always opt in. The runner and a plain CLI run never call it.
"""

from __future__ import annotations

from typing import Any, Optional

from .result import FAIL, PASS, SKIP
from .runner import RunReport

# States where a score is meaningful. A run still in flight has an incomplete
# span set, so scoring it would publish a verdict that changes on the next poll.
PUBLISHABLE_STATES = ("complete", "failed", "cancelled", "terminated")


def overall_result(pass_count: int, fail_count: int, skip_count: int) -> str:
    """One word for the whole instance. Any failure dominates. An instance that
    only skipped is reported as skip, never as a pass, matching the rule that a
    skip is never a pass."""
    if fail_count:
        return FAIL
    if pass_count:
        return PASS
    return SKIP


# The canonical quality schema an agent declares once, at register time, to get
# formatted eval summaries in its Prefactor Quality tab. The template is a plain
# passthrough of the `report` field this tool emits: the whole markdown report
# is built here, in code we can test, rather than in Liquid, whose for-loops
# cannot reach nested fields. Rendering binds to the schema version at
# registration, so an agent that wants formatted summaries must include this in
# its own registration. An external eval tool can only PUT the payload; it
# cannot make an already registered instance render.
QUALITY_TEMPLATE = "{{ report }}"

QUALITY_SCHEMA = {
    "title": "Agent evals",
    "description": "Eval results for this run: what the run recorded about "
                   "itself, and what the deterministic checks measured.",
    "schema": {
        "type": "object",
        "properties": {
            "report": {"type": "string", "title": "Summary"},
            "result": {"type": "string", "title": "Check result",
                       "enum": ["pass", "fail", "skip"]},
            "checks_passed": {"type": "integer", "title": "Checks passed",
                              "minimum": 0},
            "checks_failed": {"type": "integer", "title": "Checks failed",
                              "minimum": 0},
            "checks_not_run": {"type": "integer", "title": "Checks not run",
                               "minimum": 0},
            # What the agent's own evals recorded, lifted out of its spans so
            # the dashboard reflects the evals the team already runs.
            "recorded_verdict": {"type": "string", "title": "Recorded verdict"},
            "recorded_scores": {"type": "object", "title": "Recorded scores"},
            "signals": {"type": "integer", "title": "Quality signals", "minimum": 0},
            "signals_flagged": {"type": "integer", "title": "Signals of concern",
                                "minimum": 0},
            "worst_signal": {"type": "string", "title": "Worst signal"},
            "pack": {"type": "string", "title": "Pack"},
            "scored_while_running": {"type": "boolean", "title": "Scored mid run"},
        },
        "required": ["report"],
    },
    "template": QUALITY_TEMPLATE,
}


def summarise_recorded_quality(instance) -> dict:
    """Lift the agent's own eval results out of its spans.

    The evals a team already runs are written into spans by their own
    instrumentation. Nothing carries them to the instance, which is why the
    Quality tab stays empty even though the results exist. This reads them so
    they can be published where the dashboard looks.

    Imported lazily from the HTML renderer to reuse one definition of what a
    quality span is, rather than keeping two that can drift apart.
    """
    from .html_report import _quality_spans, _score_pct, _CALM_VALUES

    judgements, signals = _quality_spans(instance)
    out = {}

    for _span, payload in judgements:
        for key, value in payload.items():
            if isinstance(value, str) and value.strip() and len(value) < 40:
                out.setdefault("recorded_verdict", value)
            elif isinstance(value, dict) and value and all(
                    _score_pct(v) is not None for v in value.values()):
                out.setdefault("recorded_scores", dict(value))

    if signals:
        out["signals"] = len(signals)
        flagged, worst, worst_score = [], None, -1.0
        for _span, payload in signals:
            label = payload.get("signal")
            concerning = any(
                isinstance(v, bool) and v and k.lower() == "struggling"
                for k, v in payload.items())
            if isinstance(label, str) and label.strip().lower() not in _CALM_VALUES:
                concerning = True
            if concerning:
                flagged.append(payload)
                score = _score_pct(payload.get("score")) or 0.0
                if score > worst_score:
                    worst_score, worst = score, label
        out["signals_flagged"] = len(flagged)
        if worst:
            out["worst_signal"] = str(worst)
    return out


def _pretty(eval_id: str) -> str:
    """core.latency_budget to "latency budget", for human readable summaries."""
    tail = eval_id.split(".", 1)[-1]
    return tail.replace("_", " ")


_STATUS_LABEL = {PASS: "Pass", FAIL: "Fail", SKIP: "Not run"}
# Failures first, then passes, then not run, in the per check breakdown.
_STATUS_ORDER = {FAIL: 0, PASS: 1, SKIP: 2}


def _line(text: str) -> str:
    """Collapse to a single line. The summary field is rendered as plain text
    with whitespace collapsed, so embedded newlines would silently run together
    anyway. Doing it here keeps the stored value honest about how it renders."""
    return " ".join((text or "").split()).strip()


def _report_line(result_word: str, counts: dict, ran: int, rows: list,
                 pack: str, scored_while_running: bool,
                 recorded: Optional[dict] = None) -> str:
    """The one line summary the Quality tab renders.

    Verified against the live tab, twice, the hard way: this field is
    HTML-escaped plain text inserted into HTML. Markdown renders as literal
    characters, HTML tags render as literal characters, and newlines collapse to
    spaces. So there is no layout available here at all. Structure has to come
    from sentence order and punctuation, and every fragment closes its own
    sentence or the next one reads as a continuation of it.

    The per check breakdown lives in the `checks` array instead, which the tab
    shows as formatted JSON under Raw quality payload.
    """
    bits = []
    recorded = recorded or {}
    if recorded.get("recorded_verdict"):
        scores = recorded.get("recorded_scores") or {}
        # An integral float renders without the trailing .0, because JSON in
        # the TypeScript implementation collapses 1.0 to 1 before formatting
        # ever runs, and this string is rendered by the Quality tab, where the
        # two publishers must produce the same bytes.
        detail = ", ".join(
            "%s %s" % (k, int(v) if isinstance(v, float) and v.is_integer() else v)
            for k, v in sorted(scores.items()))
        bits.append("Agent's own eval: %s%s."
                    % (recorded["recorded_verdict"], " (%s)" % detail if detail else ""))
    if recorded.get("signals"):
        flagged = recorded.get("signals_flagged", 0)
        line = "%d quality signals recorded" % recorded["signals"]
        if flagged:
            line += ", %d of concern" % flagged
            if recorded.get("worst_signal"):
                line += " (worst: %s)" % recorded["worst_signal"]
        bits.append(line + ".")
    bits.append("%s pack: %s." % (pack, result_word.upper()))
    stat = "%d of %d checks passed" % (counts[PASS], ran)
    if counts[FAIL]:
        stat += ", %d failed" % counts[FAIL]
    bits.append(stat + ".")

    failures = sorted((r for r in rows if r["status"] == FAIL), key=lambda r: r["name"])
    for failure in failures:
        detail = _line(failure["detail"]).rstrip(".")
        where = " (at %s)" % failure["where"] if failure["where"] else ""
        bits.append("FAILED %s: %s%s." % (_pretty(failure["name"]), detail, where))
    if counts[SKIP]:
        bits.append("%d checks not run, they need config." % counts[SKIP])
    if scored_while_running:
        bits.append("Scored mid run, this instance had not finished.")
    return " ".join(bits)


def build_block(instance_id: str, report: RunReport,
                tool_version: str, run_timestamp: Optional[str] = None) -> dict:
    """The score block for one instance, shaped to read as a report.

    Deterministic unless a timestamp is passed, so tests can assert on it. The
    tab renders this payload, so the first key is a plain English summary and
    failures carry their actual descriptions, not just check IDs.
    """
    results = [r for r in report.results if r.instance_id == instance_id]
    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    failures = []
    rows = []
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
        span_ids = (result.evidence or {}).get("span_ids") or []
        # Where a failure happened: the offending spans. One is enough for the
        # table, the full list is in the JSON report.
        where = ""
        if result.status == FAIL and span_ids:
            where = "span %s" % span_ids[0]
            if len(span_ids) > 1:
                where += " +%d more" % (len(span_ids) - 1)
        detail = result.details
        if result.status == SKIP:
            # For a not run check, the useful "detail" is the config it needs.
            detail = result.remedy or result.details
        rows.append({"name": result.eval_id, "status": result.status,
                     "detail": detail, "where": where})
        if result.status == FAIL:
            failures.append({"check": result.eval_id, "detail": result.details,
                             "span_ids": span_ids})
    failures.sort(key=lambda f: f["check"])

    instance = next((i for i in report.instances if i.id == instance_id), None)
    state = instance.state if instance else None
    scored_while_running = state not in PUBLISHABLE_STATES

    ran = counts[PASS] + counts[FAIL]
    result_word = overall_result(counts[PASS], counts[FAIL], counts[SKIP])

    # Lean on purpose. The tab renders this raw and sorts the keys, so every
    # field has to earn its place or it just adds noise. Full structured detail,
    # including exact eval ids and per eval evidence, lives in the on disk JSON
    # report. This payload is the human glance.
    # The per check breakdown, failures first. This is what the tab shows as
    # formatted JSON under Raw quality payload, and it is the closest thing to a
    # table the current UI can render.
    checks = [
        {
            "check": _pretty(row["name"]),
            "result": _STATUS_LABEL[row["status"]],
            "detail": _line(row["detail"]),
            "where": row["where"],
        }
        for row in sorted(rows, key=lambda r: (_STATUS_ORDER[r["status"]], r["name"]))
    ]

    # The agent's own eval results, lifted from its spans, so the dashboard
    # reflects the evals the team already runs and not only these checks.
    recorded = summarise_recorded_quality(instance) if instance else {}

    block = {
        # Rendered as quality_summary when the agent has declared QUALITY_SCHEMA.
        # One line, no markdown: the tab collapses whitespace and does not render
        # markdown, so layout has to come from sentence order.
        "report": _report_line(result_word, counts, ran, rows,
                               report.pack.id, scored_while_running),
        "result": result_word,
        "checks_passed": counts[PASS],
        "checks_failed": counts[FAIL],
        "checks_not_run": counts[SKIP],
        "checks": checks,
        "pack": report.pack.id,
        "scored_while_running": scored_while_running,
        "instance_state": state,
        "tool_version": tool_version,
    }
    block.update(recorded)
    block["report"] = _report_line(result_word, counts, ran, rows,
                                   report.pack.id, scored_while_running, recorded)
    if run_timestamp:
        block["evaluated_at"] = run_timestamp
    return block


class PublishOutcome:
    def __init__(self, instance_id: str, status: str, detail: str,
                 block: Optional[dict] = None):
        self.instance_id = instance_id
        # "published", "skipped", or "error".
        self.status = status
        self.detail = detail
        self.block = block


def publish_report(report: RunReport, publisher: Any, tool_version: str,
                   run_timestamp: Optional[str] = None,
                   include_active: bool = False) -> list:
    """Push a score block for every publishable instance.

    A failure on one instance never stops the rest. Each instance gets an
    outcome, so the caller can report exactly what landed and what did not.

    Unfinished runs are skipped by default, because a score over an incomplete
    span set can change on the next poll. `include_active=True` scores them
    anyway, which is useful for a long lived demo instance that never finishes.
    The block records `scored_while_running` so the score is not mistaken for a
    verdict on a completed run.
    """
    outcomes = []
    for instance in report.instances:
        if instance.state not in PUBLISHABLE_STATES and not include_active:
            outcomes.append(PublishOutcome(
                instance.id, "skipped",
                "state is %s, not scorable until the run finishes "
                "(use include_active to score anyway)" % instance.state,
            ))
            continue
        block = build_block(instance.id, report, tool_version, run_timestamp)
        try:
            written = publisher.publish(instance.id, block)
            outcomes.append(PublishOutcome(
                instance.id, "published",
                "result %s, %d pass, %d fail, %d skip"
                % (block["result"], block["checks_passed"], block["checks_failed"],
                   block["checks_not_run"]),
                written,
            ))
        except Exception as error:  # noqa: BLE001
            outcomes.append(PublishOutcome(
                instance.id, "error", "%s: %s" % (type(error).__name__, error)))
    return outcomes


def summarize(outcomes: list) -> str:
    published = sum(1 for o in outcomes if o.status == "published")
    skipped = sum(1 for o in outcomes if o.status == "skipped")
    errored = sum(1 for o in outcomes if o.status == "error")
    lines = ["Published scores to Prefactor Quality tab: %d instances." % published]
    if skipped:
        lines.append("  Skipped %d (unfinished runs)." % skipped)
    for outcome in outcomes:
        if outcome.status == "error":
            lines.append("  Error on %s: %s" % (outcome.instance_id[:14], outcome.detail))
    if errored:
        lines.append("  %d instances failed to publish, see above." % errored)
    return "\n".join(lines)
