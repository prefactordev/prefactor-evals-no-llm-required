"""Render a run report as a standalone HTML page.

One self-contained file, no assets, no network. Useful for sharing a result with
someone who will not run the CLI, and as a worked proposal for what an eval view
could look like inside a trace UI.

Everything interpolated is escaped: eval details carry quotes, regex fragments
and JSON snippets that would otherwise break the markup.
"""

from __future__ import annotations

import html
from typing import Optional

from .publish import PUBLISHABLE_STATES, _pretty, overall_result
from .result import FAIL, PASS, SKIP

# Normalized span type to a word a person reads. `other` is the common case for
# real instrumentation, so it gets a neutral label rather than "other".
_KIND = {
    "llm_call": ("Model", "model"),
    "tool_call": ("Tool", "tool"),
    "retrieval": ("Search", "step"),
    "handoff": ("Handoff", "msg"),
    "output": ("Output", "step"),
    "other": ("Step", "step"),
}

MAX_SLOWEST = 5

def _is_quality_payload(payload: dict) -> bool:
    """True when a span's output looks like a judgement rather than a result.

    Detected by shape, not by name. A judgement rates something and says why, so
    it pairs at least one rating (a 0..1 score, or a boolean flag) with at least
    one piece of text. A tool result normally carries data without a rating, and
    a model call carries text without one, so neither matches.

    Naming the known schemas instead would only ever work for the agents whose
    names we happened to have seen.
    """
    has_rating = False
    has_text = False
    for value in payload.values():
        if isinstance(value, bool):
            has_rating = True
        elif isinstance(value, (int, float)) and _score_pct(value) is not None:
            has_rating = True
        elif isinstance(value, dict) and value and all(
                _score_pct(v) is not None for v in value.values()):
            has_rating = True
        elif isinstance(value, str) and value.strip():
            has_text = True
    return has_rating and has_text


def _quality_spans(instance) -> tuple:
    """The run's own quality spans, split into whole run judgements and per turn
    signals. Returns (judgements, signals), each a list of (span, output).

    This is quality the agent recorded about itself while running. It is a
    different kind of evidence from the deterministic checks in this library, so
    the page shows them apart rather than merged: one is the agent's opinion,
    the other is measurement.

    A judgement that carries a group of named scores is treated as a whole run
    verdict; anything else is a point in time signal.
    """
    judgements, signals = [], []
    for span in instance.spans:
        payload = span.output if isinstance(span.output, dict) else None
        if not payload or not _is_quality_payload(payload):
            continue
        grouped = any(isinstance(v, dict) and v and
                      all(_score_pct(x) is not None for x in v.values())
                      for v in payload.values())
        (judgements if grouped else signals).append((span, payload))
    return judgements, signals


def _score_pct(value) -> Optional[float]:
    """Normalize a 0..1 or 0..100 score to a percentage, or None."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    if 0.0 <= number <= 1.0:
        return number * 100.0
    if 0.0 <= number <= 100.0:
        return number
    return None


# A string longer than this is prose to read, not a label to glance at.
_PROSE_LEN = 90


def _classify_fields(payload: dict) -> dict:
    """Sort a quality payload's own fields into display shapes by their value.

    Grouping is name blind on purpose, so a field this library has never seen
    still renders: numbers that look like scores become bars, booleans become
    flags, short strings become labels, long strings become prose.

    Deciding what a field *means* is a separate job, and is not name blind. See
    the tone helpers: colour is only applied to fields whose meaning is known,
    because a renderer that guesses polarity paints a 0.85 distress score green.
    """
    groups = {"labels": [], "scores": [], "flags": [], "prose": [], "other": []}
    for key, value in payload.items():
        if isinstance(value, dict):
            nested = [(k, v) for k, v in value.items() if _score_pct(v) is not None]
            if nested and len(nested) == len(value):
                groups["scores"].extend(nested)
            else:
                groups["other"].append((key, value))
        elif isinstance(value, bool):
            groups["flags"].append((key, value))
        elif isinstance(value, (int, float)) and _score_pct(value) is not None:
            groups["scores"].append((key, value))
        elif isinstance(value, str):
            if len(value) > _PROSE_LEN:
                groups["prose"].append((key, value))
            elif value.strip():
                groups["labels"].append((key, value))
        elif value is not None:
            groups["other"].append((key, value))
    return groups


def _humanise(key: str) -> str:
    return str(key).replace("_", " ").replace(":", " ").strip()


# Meaning for the fields agents actually record. Colour is a claim about whether
# something is good or bad, so it is only made where the meaning is known.
# Anything unrecognised renders neutral: a wrong claim is worse than no claim,
# and a purely shape driven renderer paints a 0.85 distress score green because
# it cannot tell a severity from a quality rating.
_CONCERN_FLAGS = frozenset({
    "struggling", "failed", "error", "blocked", "stuck", "escalated",
    "abandoned", "confused", "at_risk",
})
_ACTION_FLAGS = frozenset({
    "intervened", "handled", "resolved", "recovered", "corrected", "clarified",
})
_CALM_VALUES = frozenset({
    "none", "ok", "good", "normal", "healthy", "pass", "fine", "nominal",
})


def _flag_tone(key: str, value: bool) -> Optional[str]:
    """Tone for a boolean, or None when the field's meaning is unknown."""
    if not value:
        return None
    name = str(key).lower()
    if name in _CONCERN_FLAGS:
        return "f"
    if name in _ACTION_FLAGS:
        return "a"
    return "n"


def _label_tone(value: str, severity: bool = False) -> str:
    """Calm values read as fine. Anything else reads as a concern only when the
    payload it sits in is already flagging one, so an unrecognised label is
    never coloured on a guess."""
    if str(value).strip().lower() in _CALM_VALUES:
        return "p"
    return "f" if severity else "n"


def _severity_polarity(payload: dict) -> bool:
    """True when a bare score in this payload means severity, not quality.

    A per turn signal scores how badly things are going, so a high number is
    bad. A whole run judgement scores how well the agent did, so a high number
    is good. Same field name, opposite meaning, which is exactly what a name
    blind renderer gets wrong.
    """
    for key, value in payload.items():
        name = str(key).lower()
        if isinstance(value, bool) and value and name in _CONCERN_FLAGS:
            return True
        if isinstance(value, str) and name in ("signal", "state", "status"):
            if value.strip().lower() not in _CALM_VALUES:
                return True
    return False


def _score_tone(pct: float, severity: bool) -> str:
    """Green means good in both directions, which is why polarity matters."""
    if severity:
        return "bad" if pct >= 70 else ("warn" if pct >= 40 else "ok")
    return "ok" if pct >= 70 else ("warn" if pct >= 40 else "bad")


def format_duration(ms: Optional[float]) -> str:
    """Milliseconds to something a person reads at a glance.

    Raw milliseconds are a machine unit. A five figure number in a table tells a
    reader nothing about whether it is slow.
    """
    if ms is None:
        return "unknown"
    if ms < 1000:
        return "%dms" % round(ms)
    if ms < 60000:
        return "%.1fs" % (ms / 1000.0)
    minutes, seconds = divmod(int(round(ms / 1000.0)), 60)
    return "%dm %02ds" % (minutes, seconds)


def _kind(span) -> tuple:
    return _KIND.get(span.type, _KIND["other"])


def _span_label(span) -> str:
    """Prefer the human name, fall back to the schema name."""
    return span.name or span.schema_name or span.id


def _is_waiting_on_run(instance, result) -> bool:
    """A skip caused by the run being unfinished, not by missing config.

    Worth separating: one is a job for the user, the other resolves itself when
    the run ends. Lumping them together hides that.
    """
    if instance.state in PUBLISHABLE_STATES:
        return False
    text = (result.details or "").lower()
    return "still in state" in text or "has not finished" in text


def _latency_ceiling(pack) -> Optional[float]:
    config = pack.config_for("core.latency_budget") if pack else {}
    ceiling = config.get("max_span_ms")
    return float(ceiling) if isinstance(ceiling, (int, float)) else None


def render_html(instance, report, agent_name: str = "") -> str:
    """A standalone HTML page for one instance's results."""
    esc = html.escape
    pack = report.pack
    results = [r for r in report.results if r.instance_id == instance.id]

    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    ran = counts[PASS] + counts[FAIL]
    verdict = overall_result(counts[PASS], counts[FAIL], counts[SKIP])
    verdict_word = {PASS: "Pass", FAIL: "Fail", SKIP: "No verdict"}[verdict]

    spans_by_id = {s.id: s for s in instance.spans}
    ceiling = _latency_ceiling(pack)

    # --- checks that ran -----------------------------------------------------
    ran_rows = sorted((r for r in results if r.status in (PASS, FAIL)),
                      key=lambda r: (0 if r.status == FAIL else 1, r.eval_id))
    check_rows = []
    for result in ran_rows:
        span_ids = (result.evidence or {}).get("span_ids") or []
        if span_ids:
            chips = []
            for span_id in span_ids[:4]:
                span = spans_by_id.get(span_id)
                name = _span_label(span) if span else span_id
                dur = format_duration(span.duration_ms) if span else ""
                chips.append(
                    '<span class="chip"><span class="cn">%s</span>'
                    '<span class="ci">%s</span><span class="cd">%s</span></span>'
                    % (esc(name), esc(span_id[:10]), esc(dur))
                )
            if len(span_ids) > 4:
                chips.append('<span class="none">and %d more</span>'
                             % (len(span_ids) - 4))
            where = '<div class="chips">%s</div>' % "".join(chips)
        else:
            where = '<span class="none">Whole run</span>'

        cls = "f" if result.status == FAIL else "p"
        check_rows.append(
            '<tr><td class="stripe"><span class="bar %s"></span></td>'
            '<td><div class="cname">%s</div><div class="cid">%s</div></td>'
            '<td><span class="pill %s">%s</span></td>'
            '<td class="what">%s</td><td>%s</td></tr>'
            % (cls, esc(_pretty(result.eval_id).capitalize()), esc(result.eval_id),
               cls, "Fail" if result.status == FAIL else "Pass",
               esc(result.details or ""), where)
        )

    # --- slowest spans -------------------------------------------------------
    timed = [s for s in instance.spans if s.duration_ms is not None]
    timed.sort(key=lambda s: s.duration_ms, reverse=True)
    slowest = timed[:MAX_SLOWEST]
    flagged = {sid for r in results if r.status == FAIL
               for sid in ((r.evidence or {}).get("span_ids") or [])}

    gauge_rows = []
    longest = slowest[0].duration_ms if slowest else 0
    for span in slowest:
        width = (span.duration_ms / longest * 100.0) if longest else 0
        over = span.id in flagged or (ceiling is not None and span.duration_ms > ceiling)
        mark = ""
        if ceiling is not None and longest:
            mark = '<div class="ceil" style="left:%.1f%%"></div>' % min(
                100.0, ceiling / longest * 100.0)
        label, kind_cls = _kind(span)
        gauge_rows.append(
            '<div class="grow"><div class="gname">'
            '<span class="kind %s">%s</span>%s</div>'
            '<div class="track"><div class="fill %s" style="width:%.1f%%"></div>%s</div>'
            '<div class="gval %s">%s</div></div>'
            % (kind_cls, esc(label), esc(_span_label(span)),
               "" if over else "ok", max(2.0, width), mark,
               "over" if over else "", esc(format_duration(span.duration_ms)))
        )

    # --- not run, split by what is blocking ----------------------------------
    waiting, needs_config = [], []
    for result in (r for r in results if r.status == SKIP):
        row = ('<div class="nr"><div class="n">%s</div><div class="r">%s</div></div>'
               % (esc(_pretty(result.eval_id).capitalize()),
                  esc(result.remedy or result.details or "")))
        (waiting if _is_waiting_on_run(instance, result) else needs_config).append(row)

    not_run_html = ""
    if needs_config:
        not_run_html += (
            '<details><summary>%d %s need configuration</summary>'
            '<div class="dbody">%s</div></details>'
            % (len(needs_config), "check" if len(needs_config) == 1 else "checks",
               "".join(needs_config)))
    if waiting:
        not_run_html += (
            '<details><summary>%d %s waiting on this run to finish</summary>'
            '<div class="dbody">%s</div></details>'
            % (len(waiting), "check is" if len(waiting) == 1 else "checks are",
               "".join(waiting)))

    # --- quality the run recorded about itself -------------------------------
    # Rendered from the fields each span actually carries. No assumed schema.
    judgements, signals = _quality_spans(instance)
    recorded_html = ""
    for span, payload in judgements + signals:
        groups = _classify_fields(payload)
        severity = _severity_polarity(payload)
        head = ['<span class="kind msg">%s</span>' % esc(_span_label(span))]
        for key, value in groups["labels"]:
            head.append('<span class="pill %s">%s</span>'
                        % (_label_tone(value, severity),
                           esc("%s: %s" % (_humanise(key), value))))
        for key, value in groups["flags"]:
            tone = _flag_tone(key, value)
            if tone:
                head.append('<span class="pill %s">%s</span>'
                            % (tone, esc(_humanise(key))))
        ctx = ""
        if isinstance(span.input, dict):
            bits = ["%s %s" % (_humanise(k), v) for k, v in span.input.items()
                    if isinstance(v, (str, int, float)) and not isinstance(v, bool)]
            if bits:
                ctx = '<span class="none">%s</span>' % esc(", ".join(bits))

        bars = []
        for key, value in groups["scores"]:
            pct = _score_pct(value)
            # Grouped named scores rate quality; a bare score beside a concern
            # flag rates severity. Colour follows the meaning, not the number.
            tone = _score_tone(pct, severity and len(groups["scores"]) == 1)
            bars.append('<div class="srow"><div class="sname">%s</div>'
                        '<div class="track"><div class="fill %s" style="width:%.1f%%"></div></div>'
                        '<div class="gval">%s</div></div>'
                        % (esc(_humanise(key)), tone, pct, esc(str(value))))
        prose = "".join('<p class="qtext"><b>%s</b> %s</p>'
                        % (esc(_humanise(k)), esc(v)) for k, v in groups["prose"])
        extra = "".join('<p class="qtext"><b>%s</b> %s</p>'
                        % (esc(_humanise(k)), esc(str(v))) for k, v in groups["other"])
        recorded_html += (
            '<div class="qcard"><div class="qhead">%s%s</div>%s%s%s</div>'
            % ("".join(head), ctx,
               ('<div class="gauge">%s</div>' % "".join(bars)) if bars else "",
               prose, extra))

    if recorded_html:
        recorded_html = (
            '<section class="card sec"><h2>Quality recorded by this run</h2>'
            '<p class="lead">%d %s in this instance carry a quality judgement the agent '
            'made about itself. Every field below is read straight from those spans.</p>'
            '%s</section>'
            % (len(judgements) + len(signals),
               "span" if len(judgements) + len(signals) == 1 else "spans",
               recorded_html))
    else:
        recorded_html = (
            '<section class="card sec"><h2>Quality recorded by this run</h2>'
            '<p class="lead">No span in this instance carries a quality judgement. '
            'Everything below is measured from the trace by code instead.</p></section>')

    started = instance.started_at.strftime("%d %b %Y %H:%M:%S") if instance.started_at else "unknown"
    unfinished = instance.state not in PUBLISHABLE_STATES
    subtitle = "%d of %d checks that ran failed." % (counts[FAIL], ran) if counts[FAIL] \
        else ("All %d checks that ran passed." % counts[PASS] if ran
              else "No checks could run on this instance.")
    if unfinished:
        subtitle += " Scored while the run was still %s, so this verdict can change." % esc(instance.state)

    ceiling_note = ""
    if ceiling is not None:
        ceiling_note = (' The marked line is the %s limit the latency check enforces.'
                        % format_duration(ceiling))

    return _PAGE % {
        "css": _CSS,
        "title": esc("Quality: %s" % (agent_name or instance.agent_id)),
        "agent": esc(agent_name or instance.agent_id),
        "instance_id": esc(instance.id),
        "state": esc(instance.state),
        "started": esc(started),
        "span_count": len(instance.spans),
        "verdict_cls": {PASS: "p", FAIL: "f", SKIP: "n"}[verdict],
        "verdict": esc(verdict_word),
        "pack": esc(pack.id),
        "subtitle": subtitle,
        "passed": counts[PASS],
        "failed": counts[FAIL],
        "not_run": counts[SKIP],
        "check_rows": "".join(check_rows) or
                      '<tr><td colspan="5" class="none">No checks ran.</td></tr>',
        "gauge_rows": "".join(gauge_rows) or '<p class="none">No spans carry a duration.</p>',
        "slowest_n": len(slowest),
        "total_spans": len(instance.spans),
        "ceiling_note": ceiling_note,
        "not_run_html": not_run_html or '<p class="none">Every check ran.</p>',
        "recorded_html": recorded_html,
    }


_PAGE = """<title>%(title)s</title>
<style>%(css)s</style>
<div class="wrap">
<section class="card"><div class="head">
<div class="title-row"><div class="mark">&#9654;</div>
<h1>%(agent)s <span>instance</span></h1><span class="pill n">%(state)s</span></div>
<div class="meta">
<div><b>Instance</b><span>%(instance_id)s</span></div>
<div><b>Started</b><span>%(started)s</span></div>
<div><b>Spans</b><span>%(span_count)d</span></div>
</div></div></section>

<section class="card verdict %(verdict_cls)s"><div class="vmain">
<div class="vtop"><span class="vword">%(verdict)s</span>
<span class="pill n">%(pack)s pack</span></div>
<div class="vsub">%(subtitle)s</div></div>
<div class="counts">
<div class="count p"><b>%(passed)d</b> passed</div>
<div class="count f"><b>%(failed)d</b> failed</div>
<div class="count"><b>%(not_run)d</b> not run</div>
</div></section>

<section class="card sec"><h2>Checks that ran</h2><div class="scroll"><table>
<thead><tr><th class="stripe"></th><th>Check</th><th>Result</th>
<th>What it found</th><th>Where</th></tr></thead>
<tbody>%(check_rows)s</tbody></table></div></section>

%(recorded_html)s

<section class="card sec"><h2>How long each span took</h2>
<p class="lead">The %(slowest_n)d slowest spans of the %(total_spans)d in this run. A span is
one thing the agent did: a message, a call to the model, or a tool it used.%(ceiling_note)s</p>
<div class="gauge">%(gauge_rows)s</div>
<p class="legend"><span class="swatch fail"></span>Over the limit
<span class="swatch pass"></span>Within the limit
<span class="swatch line"></span>Limit</p></section>

<section class="card sec"><h2>Not run</h2>%(not_run_html)s</section>
</div>
"""


_CSS = r"""
:root{--page:#f6f6fa;--card:#fff;--ink:#1a1a23;--ink-soft:#4a4a5a;--muted:#6f6f80;
--line:#e5e5ee;--line-soft:#f0f0f6;--accent:#5b5bd6;--accent-soft:#eeeefc;
--fail:#d81e5b;--fail-soft:#fdeaf1;--pass:#0f9d6e;--pass-soft:#e6f6f0;
--idle:#8a8a9a;--idle-soft:#f1f1f5;--radius:12px;
--mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
--ui:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root{--page:#121218;--card:#1a1a22;--ink:#ecebf2;
--ink-soft:#c3c2d0;--muted:#8f8fa2;--line:#2c2c38;--line-soft:#22222c;--accent:#8f8ff0;
--accent-soft:#23233a;--fail:#ff6b96;--fail-soft:#33121f;--pass:#3ad39b;--pass-soft:#0f2c22;
--idle:#7c7c8e;--idle-soft:#24242e}}
:root[data-theme="dark"]{--page:#121218;--card:#1a1a22;--ink:#ecebf2;--ink-soft:#c3c2d0;
--muted:#8f8fa2;--line:#2c2c38;--line-soft:#22222c;--accent:#8f8ff0;--accent-soft:#23233a;
--fail:#ff6b96;--fail-soft:#33121f;--pass:#3ad39b;--pass-soft:#0f2c22;--idle:#7c7c8e;--idle-soft:#24242e}
:root[data-theme="light"]{--page:#f6f6fa;--card:#fff;--ink:#1a1a23;--ink-soft:#4a4a5a;
--muted:#6f6f80;--line:#e5e5ee;--line-soft:#f0f0f6;--accent:#5b5bd6;--accent-soft:#eeeefc;
--fail:#d81e5b;--fail-soft:#fdeaf1;--pass:#0f9d6e;--pass-soft:#e6f6f0;--idle:#8a8a9a;--idle-soft:#f1f1f5}
body{background:var(--page);color:var(--ink);font-family:var(--ui);line-height:1.5;
margin:0;padding:28px 20px 64px}
.wrap{max-width:1080px;margin:0 auto;display:flex;flex-direction:column;gap:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius)}
.head{padding:18px 20px;display:flex;flex-direction:column;gap:10px}
.title-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.mark{width:34px;height:34px;border-radius:9px;background:var(--accent);display:grid;
place-items:center;color:#fff;font-size:15px;flex:none}
h1{font-size:20px;margin:0;font-weight:620;letter-spacing:-.01em}
h1 span{color:var(--muted);font-weight:400}
.meta{display:flex;gap:18px;flex-wrap:wrap;font-size:12.5px;color:var(--muted)}
.meta b{display:block;font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;
font-weight:600;margin-bottom:2px}
.meta span{color:var(--ink-soft);font-weight:500;font-family:var(--mono)}
.verdict{display:flex;gap:18px;align-items:stretch;flex-wrap:wrap;padding:18px 20px;
border-left:4px solid var(--idle);border-radius:var(--radius)}
.verdict.f{border-left-color:var(--fail)}.verdict.p{border-left-color:var(--pass)}
.vmain{flex:1 1 300px;display:flex;flex-direction:column;gap:5px}
.vtop{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.vword{font-size:25px;font-weight:700;letter-spacing:-.02em;color:var(--idle)}
.verdict.f .vword{color:var(--fail)}.verdict.p .vword{color:var(--pass)}
.vsub{font-size:13.5px;color:var(--ink-soft)}
.counts{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.count{display:flex;align-items:baseline;gap:6px;padding:7px 12px;border-radius:9px;
background:var(--idle-soft);font-size:12.5px;color:var(--muted)}
.count b{font-size:17px;font-weight:650;font-variant-numeric:tabular-nums;color:var(--ink)}
.count.p{background:var(--pass-soft)}.count.p b{color:var(--pass)}
.count.f{background:var(--fail-soft)}.count.f b{color:var(--fail)}
.pill{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:999px;
font-size:11.5px;font-weight:600;letter-spacing:.02em}
.pill.f{background:var(--fail-soft);color:var(--fail)}
.pill.p{background:var(--pass-soft);color:var(--pass)}
.pill.n{background:var(--idle-soft);color:var(--idle)}
.pill.a{background:var(--accent-soft);color:var(--accent)}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
margin:0;font-weight:620}
.sec{padding:16px 20px 18px;display:flex;flex-direction:column;gap:12px}
.lead{font-size:13px;color:var(--muted);margin:-2px 0 0;max-width:68ch}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px;min-width:640px}
th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;
color:var(--muted);font-weight:620;padding:0 12px 8px;border-bottom:1px solid var(--line)}
td{padding:12px;border-bottom:1px solid var(--line-soft);vertical-align:top}
tr:last-child td{border-bottom:none}
.cname{font-weight:560;color:var(--ink)}
.cid{font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:2px}
.what{color:var(--ink-soft)}
td.stripe{padding-left:0;width:3px}
.bar{width:3px;height:100%;min-height:34px;border-radius:2px;background:var(--line);display:block}
.bar.f{background:var(--fail)}.bar.p{background:var(--pass)}
.chips{display:flex;flex-direction:column;gap:6px}
.chip{display:inline-flex;align-items:center;gap:8px;padding:5px 9px;border:1px solid var(--line);
border-radius:8px;background:var(--page);font-size:12px;color:var(--ink)}
.chip .cn{font-weight:560}
.chip .ci{font-family:var(--mono);font-size:10.5px;color:var(--muted)}
.chip .cd{font-family:var(--mono);font-size:11px;color:var(--fail);
font-variant-numeric:tabular-nums;margin-left:auto}
.none{color:var(--muted);font-size:12.5px}
.gauge{display:flex;flex-direction:column;gap:9px;margin-top:2px}
.grow{display:grid;grid-template-columns:210px 1fr 62px;gap:12px;align-items:center;font-size:12.5px}
.gname{color:var(--ink-soft);font-weight:520;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap;display:flex;align-items:center;gap:7px}
.kind{flex:none;font-size:10px;font-weight:620;letter-spacing:.04em;padding:2px 6px;border-radius:5px}
.kind.msg{background:var(--accent-soft);color:var(--accent)}
.kind.model{background:var(--idle-soft);color:var(--muted)}
.kind.tool{background:var(--pass-soft);color:var(--pass)}
.kind.step{background:var(--line-soft);color:var(--muted)}
.track{position:relative;height:20px;background:var(--line-soft);border-radius:5px;overflow:visible}
.fill{position:absolute;top:0;bottom:0;left:0;border-radius:5px;background:var(--fail)}
.fill.ok{background:var(--pass)}
.ceil{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--ink);opacity:.5;border-radius:1px}
.gval{font-family:var(--mono);font-size:12.5px;text-align:right;font-variant-numeric:tabular-nums;
color:var(--ink-soft)}
.gval.over{color:var(--fail);font-weight:620}
.legend{font-size:11.5px;color:var(--muted);display:flex;gap:16px;flex-wrap:wrap;align-items:center;margin:0}
.swatch{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.swatch.fail{background:var(--fail)}.swatch.pass{background:var(--pass)}
.swatch.line{width:2px;height:13px;border-radius:1px;background:var(--ink);opacity:.5;margin-left:4px}
.qcard{border:1px solid var(--line);border-radius:10px;padding:14px;display:flex;flex-direction:column;gap:10px;background:var(--page)}
.qhead{display:flex;align-items:center;gap:9px;flex-wrap:wrap;font-weight:560}
.qtext{margin:0;font-size:13px;color:var(--ink-soft);max-width:78ch;line-height:1.55}
.srow{display:grid;grid-template-columns:120px 1fr 46px;gap:12px;align-items:center;font-size:12.5px}
.sname{color:var(--muted);text-transform:capitalize}
.fill.warn{background:#d98324}.fill.bad{background:var(--fail)}
details{border:1px solid var(--line);border-radius:10px;background:var(--page)}
summary{cursor:pointer;padding:11px 14px;font-size:13px;font-weight:560;list-style:none;
display:flex;align-items:center;gap:9px}
summary::-webkit-details-marker{display:none}
summary::after{content:"+";margin-left:auto;color:var(--muted);font-size:15px}
details[open] summary::after{content:"-"}
summary:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.dbody{padding:0 14px 13px;display:flex;flex-direction:column;gap:9px}
.nr{display:grid;grid-template-columns:165px 1fr;gap:12px;font-size:12.5px;padding:9px 0;
border-top:1px solid var(--line-soft)}
.nr .n{font-weight:560;color:var(--ink-soft)}.nr .r{color:var(--muted)}
@media (max-width:640px){.grow{grid-template-columns:140px 1fr 54px;gap:8px}
.gname{font-size:11.5px}.nr{grid-template-columns:1fr;gap:3px}}
"""


def _check_rollup(report) -> tuple:
    """Every check in the pack, aggregated across runs.

    The per run counts answer "how did this run do". This answers "what was
    actually tested, and what wasn't", which is the question a reader has first
    and which a total like "92 skipped" actively obscures: that is the same
    handful of checks skipping once per run.
    """
    by_eval = {}
    for result in report.results:
        entry = by_eval.setdefault(result.eval_id, {
            PASS: 0, FAIL: 0, SKIP: 0, "detail": "", "remedy": ""})
        entry[result.status] += 1
        if result.status == FAIL and not entry["detail"]:
            entry["detail"] = result.details or ""
        if result.status == SKIP and not entry["remedy"]:
            entry["remedy"] = result.remedy or result.details or ""

    ran, not_run = [], []
    for eval_id in report.pack.eval_ids:
        entry = by_eval.get(eval_id)
        if not entry:
            continue
        (ran if entry[PASS] or entry[FAIL] else not_run).append((eval_id, entry))
    ran.sort(key=lambda item: (0 if item[1][FAIL] else 1, item[0]))
    not_run.sort(key=lambda item: item[0])
    return ran, not_run


def render_index(report, agent_name: str = "", agent_id: str = "") -> str:
    """A dashboard across every run in this report, linking to each run's page.

    One page per run is a set of files, not a dashboard. This is the overview:
    what each run scored, what the agent's own evals said about it, and which
    runs need attention, newest first.
    """
    esc = html.escape
    rows = []
    totals = {PASS: 0, FAIL: 0, SKIP: 0}
    flagged_runs = 0

    ordered = sorted(report.instances,
                     key=lambda i: (i.started_at is not None, i.started_at),
                     reverse=True)
    for instance in ordered:
        results = [r for r in report.results if r.instance_id == instance.id]
        counts = {PASS: 0, FAIL: 0, SKIP: 0}
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
        verdict = overall_result(counts[PASS], counts[FAIL], counts[SKIP])
        totals[verdict] = totals.get(verdict, 0) + 1

        recorded = _recorded_summary(instance)
        if recorded.get("flagged"):
            flagged_runs += 1

        started = instance.started_at.strftime("%d %b %H:%M") if instance.started_at else ""
        own = []
        if recorded.get("verdict"):
            own.append('<span class="pill %s">%s</span>'
                       % (_label_tone(recorded["verdict"]), esc(str(recorded["verdict"]))))
        if recorded.get("signals"):
            tone = "f" if recorded.get("flagged") else "n"
            text = "%d signal%s" % (recorded["signals"],
                                    "" if recorded["signals"] == 1 else "s")
            if recorded.get("flagged"):
                text += ", %d of concern" % recorded["flagged"]
            own.append('<span class="pill %s">%s</span>' % (tone, esc(text)))
        if not own:
            own.append('<span class="none">none recorded</span>')

        cls = {PASS: "p", FAIL: "f", SKIP: "n"}[verdict]
        label = {PASS: "Pass", FAIL: "Fail", SKIP: "No verdict"}[verdict]
        rows.append(
            '<tr><td class="stripe"><span class="bar %s"></span></td>'
            '<td><a class="runlink" href="%s.html"><span class="cname">%s</span></a>'
            '<div class="cid">%s%s</div></td>'
            '<td><span class="pill %s">%s</span></td>'
            '<td class="gval">%d / %d / %d</td>'
            '<td>%s</td><td class="what">%d</td></tr>'
            % (cls, esc(instance.id), esc(instance.id[:16]),
               esc(started), esc(" &middot; " + instance.state) if instance.state else "",
               cls, label, counts[PASS], counts[FAIL], counts[SKIP],
               " ".join(own), len(instance.spans))
        )

    ran_checks, not_run_checks = _check_rollup(report)
    total_runs = len(ordered)

    check_rows = []
    for eval_id, entry in ran_checks:
        if entry[FAIL]:
            verdict = ('<span class="pill f">Failed</span>')
            where = "on %d of %d runs" % (entry[FAIL], entry[FAIL] + entry[PASS])
            detail = esc(entry["detail"])
        else:
            verdict = '<span class="pill p">Passed</span>'
            where = "on %d run%s" % (entry[PASS], "" if entry[PASS] == 1 else "s")
            detail = ""
        if entry[SKIP]:
            where += ", could not run on %d" % entry[SKIP]
        check_rows.append(
            '<tr><td class="stripe"><span class="bar %s"></span></td>'
            '<td><div class="cname">%s</div><div class="cid">%s</div></td>'
            '<td>%s</td><td class="what">%s</td><td class="what">%s</td></tr>'
            % ("f" if entry[FAIL] else "p", esc(_pretty(eval_id).capitalize()),
               esc(eval_id), verdict, esc(where), detail))

    not_run_rows = []
    for eval_id, entry in not_run_checks:
        not_run_rows.append(
            '<tr><td class="stripe"><span class="bar"></span></td>'
            '<td><div class="cname">%s</div><div class="cid">%s</div></td>'
            '<td><span class="pill n">Not run</span></td>'
            '<td class="what" colspan="2">%s</td></tr>'
            % (esc(_pretty(eval_id).capitalize()), esc(eval_id), esc(entry["remedy"])))

    total_checks = len(ran_checks) + len(not_run_checks)
    subtitle = ("%d runs evaluated with the %s pack. %d of %d checks ran; the rest "
                "need a setting only you can supply."
                % (total_runs, esc(report.pack.id), len(ran_checks), total_checks))
    if flagged_runs:
        subtitle += " %d recorded a quality signal of concern." % flagged_runs

    return _INDEX % {
        "css": _CSS,
        "title": esc("Quality: %s" % (agent_name or agent_id or "agent")),
        "agent": esc(agent_name or agent_id or "Agent"),
        "agent_id": esc(agent_id),
        "subtitle": subtitle,
        "passed": totals[PASS], "failed": totals[FAIL], "skipped": totals[SKIP],
        "rows": "".join(rows) or '<tr><td colspan="6" class="none">No runs.</td></tr>',
        "checks_ran": len(ran_checks),
        "checks_total": total_checks,
        "check_rows": "".join(check_rows) or
                      '<tr><td colspan="5" class="none">No check could run.</td></tr>',
        "not_run_rows": "".join(not_run_rows),
        "not_run_block": (
            '<section class="card sec"><h2>Checks that could not run</h2>'
            '<p class="lead">These need a setting that cannot be guessed from a trace: '
            'what counts as a handoff for your agent, which actions are forbidden, what '
            'a run may cost. Each row names the exact key to set in the pack file. They '
            'are counted apart from results and never as passes.</p>'
            '<div class="scroll"><table><thead><tr><th class="stripe"></th><th>Check</th>'
            '<th>Result</th><th>What it needs</th></tr></thead><tbody>%s</tbody></table>'
            '</div></section>' % "".join(not_run_rows)) if not_run_rows else "",
    }


def _recorded_summary(instance) -> dict:
    """The agent's own eval results for one run, condensed for the index."""
    judgements, signals = _quality_spans(instance)
    out = {}
    for _span, payload in judgements:
        for key, value in payload.items():
            if isinstance(value, str) and value.strip() and len(value) < 40:
                out.setdefault("verdict", value)
    if signals:
        out["signals"] = len(signals)
        out["flagged"] = sum(
            1 for _s, p in signals
            if any(isinstance(v, bool) and v and k.lower() in _CONCERN_FLAGS
                   for k, v in p.items())
            or str(p.get("signal", "")).strip().lower() not in _CALM_VALUES
        )
    return out


_INDEX = """<title>%(title)s</title>
<style>%(css)s
.runlink{color:inherit;text-decoration:none}
.runlink:hover .cname,.runlink:focus-visible .cname{color:var(--accent);text-decoration:underline}
</style>
<div class="wrap">
<section class="card"><div class="head">
<div class="title-row"><div class="mark">&#9654;</div>
<h1>%(agent)s <span>quality</span></h1></div>
<div class="meta"><div><b>Agent</b><span>%(agent_id)s</span></div></div>
</div></section>

<section class="card verdict"><div class="vmain">
<div class="vtop"><span class="vword">Runs</span></div>
<div class="vsub">%(subtitle)s</div></div>
<div class="counts">
<div class="count p"><b>%(passed)d</b> passing</div>
<div class="count f"><b>%(failed)d</b> failing</div>
<div class="count"><b>%(skipped)d</b> no verdict</div>
</div></section>

<section class="card sec"><h2>What was checked</h2>
<p class="lead">%(checks_ran)d of %(checks_total)d checks in this pack ran against these
runs. A check is a fixed rule measured from the trace, the same way every time.</p>
<div class="scroll"><table>
<thead><tr><th class="stripe"></th><th>Check</th><th>Result</th>
<th>Across runs</th><th>Example failure</th></tr></thead>
<tbody>%(check_rows)s</tbody></table></div></section>

%(not_run_block)s

<section class="card sec"><h2>Runs</h2>
<p class="lead">Newest first. Open a run for its checks and the spans behind them.</p>
<div class="scroll"><table>
<thead><tr><th class="stripe"></th><th>Run</th><th>Checks</th>
<th>Checks passed / failed / not run</th><th>Agent's own eval</th><th>Spans</th></tr></thead>
<tbody>%(rows)s</tbody></table></div></section>
</div>
"""
