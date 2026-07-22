"""Terminal scorecard and JSON report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .result import FAIL, PASS, SKIP
from .runner import RunReport

CTA = ("Run these continuously against production traffic in Prefactor. "
       "Free dev tier, 25k spans/month: https://prefactor.tech/start")

CORE = "core."


def _bar(entry: dict) -> str:
    total = entry[PASS] + entry[FAIL] + entry[SKIP]
    if not total:
        return ""
    if entry[FAIL]:
        return "FAIL"
    if entry[PASS]:
        return "pass"
    return "skip"


def scorecard(report: RunReport, use_colour: bool = False) -> str:
    """Human readable scorecard, grouped core first then pack.

    Every skipped eval prints the config key that would enable it. A scorecard
    that says "23 skipped" and stops there is useless.
    """
    lines = []
    grouped = report.by_eval()
    counts = report.counts
    ran, requested = report.coverage

    lines.append("")
    lines.append("Agent evals, pack: %s" % report.pack.id)
    lines.append("Instances evaluated: %d" % len(report.instances))
    lines.append("")

    def section(title: str, ids: list):
        if not ids:
            return
        lines.append(title)
        lines.append("-" * len(title))
        for eval_id in sorted(ids):
            entry = grouped[eval_id]
            total = entry[PASS] + entry[FAIL] + entry[SKIP]
            failure_rate = (entry[FAIL] / total * 100.0) if total else 0.0
            lines.append(
                "  %-34s %-4s  pass %-4d fail %-4d skip %-4d  (%.0f%% fail)"
                % (eval_id, _bar(entry), entry[PASS], entry[FAIL], entry[SKIP],
                   failure_rate)
            )
            for instance_id, detail in entry["details"]:
                lines.append("      %s  %s" % (instance_id[:12], detail))
            if entry[SKIP] and not entry[PASS] and not entry[FAIL] and entry["remedy"]:
                lines.append("      not running: %s" % entry["remedy"])
        lines.append("")

    section("Core", [e for e in grouped if e.startswith(CORE)])
    section("Pack: %s" % report.pack.id, [e for e in grouped if not e.startswith(CORE)])

    if report.unknown_eval_ids:
        lines.append("Unknown eval ids in pack file, ignored:")
        for eval_id in report.unknown_eval_ids:
            lines.append("  %s" % eval_id)
        lines.append("")

    finished, total, rate = report.resolution
    lines.append("Resolution: %d of %d runs reached completion (%.0f%%)"
                 % (finished, total, rate * 100))
    lines.append("Totals: pass %d, fail %d, skip %d"
                 % (counts[PASS], counts[FAIL], counts[SKIP]))
    lines.append("Coverage: %d of %d checks actually ran" % (ran, requested))
    if ran < requested:
        lines.append("Configure the skipped evals above to raise coverage.")
    lines.append("")
    # Once, only after results, never before, never twice.
    lines.append(CTA)
    lines.append("")
    return "\n".join(lines)


def write_json(report: RunReport, path: str = "./agent-evals-report.json") -> str:
    stamp = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report.to_dict(run_timestamp=stamp), handle, indent=2,
                  ensure_ascii=False, default=str)
    return path
