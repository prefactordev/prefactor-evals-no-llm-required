"""Command line entry point.

Flag names are identical to the TypeScript package by design.
"""

from __future__ import annotations

import argparse
import os
import sys

from .pack import PackError
from .report import scorecard, write_json
from .runner import run_pack


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prefactor-evals-no-llm",
        description="Deterministic, non-LLM evals for AI agents.",
    )
    sub = parser.add_subparsers(dest="command")

    run_cmd = sub.add_parser("run", help="Run a pack against an agent's traces.")
    run_cmd.add_argument("--pack", default=None,
                         help="Path to a pack file. Defaults to the built in standard pack, "
                              "the zero config health checks that run on any agent.")
    run_cmd.add_argument("--agent", required=True, help="Agent ID.")
    run_cmd.add_argument("--since", default=None, help="Time window, for example 7d, 24h, or an ISO timestamp.")
    run_cmd.add_argument("--until", default=None, help="Upper bound on instance start time.")
    run_cmd.add_argument("--state", default=None, help="Only instances in this state.")
    run_cmd.add_argument("--environment", default=None, help="Only instances in this environment ID.")
    run_cmd.add_argument("--purpose", default="live",
                         help="live, smoke_test, or eval. Defaults to live so you measure the agent, not your test harness. Pass 'any' to disable.")
    run_cmd.add_argument("--limit", type=int, default=None, help="Maximum instances to fetch.")
    run_cmd.add_argument("--report", default="./agent-evals-report.json", help="JSON report path.")
    run_cmd.add_argument("--no-report", action="store_true", help="Do not write the JSON report.")
    run_cmd.add_argument("--html", default=None, metavar="DIR",
                         help="Also write a standalone HTML page per instance into DIR. "
                              "Self contained, no assets, safe to share.")
    run_cmd.add_argument("--no-fail-exit", action="store_true",
                         help="Always exit 0, even when evals fail.")
    run_cmd.add_argument("--env-file", default=None, metavar="PATH",
                         help="Read PREFACTOR_* credentials from this file. Values already "
                              "set in the environment win. Secrets are never printed.")
    run_cmd.add_argument("--open", action="store_true",
                         help="Open the generated dashboard in your browser. Needs --html.")
    run_cmd.add_argument("--publish", action="store_true",
                         help="Write each run's score into its Prefactor Quality tab. "
                              "Off by default: a plain run only reads. This writes to your live account.")
    run_cmd.add_argument("--include-active", action="store_true",
                         help="Also score and publish runs that are still active. Off by default, "
                              "since a score over an incomplete run can change. Useful for a demo "
                              "instance that stays active. The payload records that it was scored mid run.")
    schema_cmd = sub.add_parser(
        "declare-quality-schema",
        help="Declare the eval quality schema on an agent, so its Quality tab renders "
             "published results. Run once per agent. Only affects runs registered after it.")
    schema_cmd.add_argument("--agent", required=True, help="Agent ID.")
    schema_cmd.add_argument("--version", default="agent-evals",
                            help="Agent version external identifier to declare it under.")
    schema_cmd.add_argument("--schema-version", default="agent-evals-quality-v1",
                            help="Schema version external identifier.")
    schema_cmd.add_argument("--environment", default=None,
                            help="Environment ID. Defaults to PREFACTOR_ENVIRONMENT_ID.")
    schema_cmd.add_argument("--print", action="store_true",
                            help="Print the schema and exit, without declaring it. "
                                 "Use this to paste it into your agent's own registration.")

    watch_cmd = sub.add_parser(
        "watch",
        help="Evaluate an agent's live runs span by span, and optionally stop a run "
             "that breaches a check. Runs until you stop it.")
    watch_cmd.add_argument("--agent", required=True, help="Agent ID.")
    watch_cmd.add_argument("--terminate", default="",
                           help="Comma separated checks that should STOP a breaching "
                                "run: loops, redundant_calls, errors, latency, runaway. "
                                "Everything else only warns. Off by default: nothing is "
                                "stopped unless you name it here.")
    watch_cmd.add_argument("--poll", type=float, default=2.0,
                           help="Seconds between checks for new spans. Default 2.")
    watch_cmd.add_argument("--env-file", default=None,
                           help="Read PREFACTOR_* credentials from this file.")
    watch_cmd.add_argument("--once", action="store_true",
                           help="Sweep the active runs once and exit, instead of looping. "
                                "Useful for a scheduled run or a smoke test.")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "declare-quality-schema":
        return _declare_quality_schema(args)

    if args.command == "watch":
        return _watch(args)

    if args.command != "run":
        parser.print_help()
        return 0

    if getattr(args, "env_file", None):
        _load_env_file(args.env_file)

    try:
        report = run_pack(
            args.pack, args.agent, since=args.since, until=args.until,
            state=args.state, environment=args.environment,
            purpose=None if args.purpose == "any" else args.purpose,
            limit=args.limit,
        )
    except PackError as error:
        print("Pack error: %s" % error, file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001
        print("%s" % error, file=sys.stderr)
        return 2

    print(scorecard(report))
    if not args.no_report:
        path = write_json(report, args.report)
        print("JSON report written to %s\n" % path)

    if args.html:
        index = _write_html(report, args.html, args.agent)
        print("Dashboard written to %s" % index)
        print("  %d run pages alongside it" % len(report.instances))
        print("")
        if args.open:
            import webbrowser
            webbrowser.open("file://" + os.path.abspath(index))

    if args.publish:
        _publish(report, include_active=args.include_active)

    if report.failed and not args.no_fail_exit:
        return 1
    return 0


def _watch(args) -> int:
    """Evaluate an agent's live runs span by span.

    Subscribes in the simple, dependency free way: it lists the agent's active
    runs and re-reads each one's new spans on a short interval, feeding every new
    span through the per-span checks. A breach warns by default; a check named in
    --terminate stops the breaching run through the terminate endpoint, and the
    agent halts on its next span.
    """
    import time

    if getattr(args, "env_file", None):
        _load_env_file(args.env_file)

    from .live import LiveEvaluator, POLICY_TERMINATE, POLICY_WARN
    from .live.detectors import DETECTORS
    from .live.watch import evaluate_spans
    from .pack import STANDARD_PACK
    from .source import PrefactorController, PrefactorSource, PrefactorSourceError

    terminate_checks = {c.strip() for c in args.terminate.split(",") if c.strip()}
    unknown = terminate_checks - set(DETECTORS)
    if unknown:
        print("Unknown check(s) in --terminate: %s. Valid: %s"
              % (", ".join(sorted(unknown)), ", ".join(sorted(DETECTORS))), file=sys.stderr)
        return 2
    policy = {name: (POLICY_TERMINATE if name in terminate_checks else POLICY_WARN)
              for name in DETECTORS}
    # Detector config borrows the standard pack's thresholds where they line up.
    config = {
        "loops": STANDARD_PACK["evals"]["core.loop_detection"],
        "redundant_calls": STANDARD_PACK["evals"]["core.redundant_tool_calls"],
        "latency": STANDARD_PACK["evals"]["core.latency_budget"],
    }

    try:
        source = PrefactorSource()
        controller = PrefactorController()
    except PrefactorSourceError as error:
        print("%s" % error, file=sys.stderr)
        return 2

    if terminate_checks:
        print("Watching agent %s. Will STOP a run that breaches: %s."
              % (args.agent, ", ".join(sorted(terminate_checks))))
    else:
        print("Watching agent %s. Warn only, nothing will be stopped. Name checks "
              "in --terminate to stop breaching runs." % args.agent)
    print("")

    # Per run: its evaluator, and how many spans of it have been seen.
    watched = {}
    seen_counts = {}

    def sweep() -> bool:
        active = [i for i in source.fetch_instances(args.agent, purpose=None,
                                                    state="active", limit=50)]
        stopped_any = False
        for instance in active:
            if instance.id not in watched:
                watched[instance.id] = LiveEvaluator(policy, config)
                seen_counts[instance.id] = 0
            spans = controller.live_spans(instance.id, seen_counts[instance.id])
            if not spans:
                continue
            seen_counts[instance.id] += len(spans)

            def on_breach(breach, iid=instance.id):
                mark = "STOP" if breach.terminate else "warn"
                print("  [%s] %s on %s: %s" % (mark, breach.check, iid[:14], breach.reason))

            result = evaluate_spans(
                instance.id, spans, watched[instance.id],
                terminate=lambda reason, iid=instance.id: controller.terminate(iid, reason),
                on_breach=on_breach)
            if result.terminated:
                stopped_any = True
                print("  terminated %s" % instance.id)
        return stopped_any

    try:
        if args.once:
            sweep()
        else:
            while True:
                sweep()
                time.sleep(max(0.2, args.poll))
    except KeyboardInterrupt:
        print("\nStopped watching.")
    except PrefactorSourceError as error:
        print("%s" % error, file=sys.stderr)
        return 2
    return 0


def _declare_quality_schema(args) -> int:
    """Declare the eval quality schema on an agent.

    The Quality tab renders from a schema declared on the agent's schema
    version, so an agent without one shows nothing no matter what is published.
    Declaring binds at registration, so this affects runs registered afterwards,
    not existing ones.
    """
    import json

    from .publish import QUALITY_SCHEMA

    if args.print:
        print(json.dumps(QUALITY_SCHEMA, indent=2))
        return 0

    from .source import PrefactorSourceError, declare_quality_schema

    try:
        result = declare_quality_schema(
            args.agent, version=args.version, schema_version=args.schema_version,
            environment_id=args.environment,
        )
    except PrefactorSourceError as error:
        print("%s" % error, file=sys.stderr)
        return 2

    print("Quality schema declared on agent %s" % args.agent)
    print("  agent version:  %s" % args.version)
    print("  schema version: %s" % args.schema_version)
    print("  probe instance: %s" % result.get("instance_id"))
    print("")
    print("Runs registered from now on will render published eval results in their")
    print("Quality tab. Existing runs cannot be changed: rendering binds at")
    print("registration. To make this permanent, add the same quality_schema to your")
    print("agent's own registration (see --print).")
    return 0


def _load_env_file(path: str) -> None:
    """Read PREFACTOR_* settings from a file into the environment.

    Accepts both `KEY=value` and `KEY: value`, because real env files in the
    wild carry a mix. Values already set in the environment win, so an explicit
    export is never silently overridden. Nothing from the file is printed:
    these are credentials.
    """
    import re

    if not os.path.exists(path):
        print("No env file at %s, using the environment as is." % path, file=sys.stderr)
        return
    pattern = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(.*)$")
    loaded = 0
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = pattern.match(line)
            if not match:
                continue
            key, value = match.group(1), match.group(2).strip().strip('"').strip("'")
            if key.startswith("PREFACTOR_") and not os.environ.get(key):
                os.environ[key] = value
                loaded += 1
    print("Loaded %d setting%s from %s" % (loaded, "" if loaded == 1 else "s", path))


def _write_html(report, directory: str, agent_name: str = "") -> str:
    """One page per instance plus an index across them. Returns the index path.

    Pages are named by instance id so they never overwrite each other.
    """
    from .html_report import render_html, render_index

    os.makedirs(directory, exist_ok=True)
    for instance in report.instances:
        path = os.path.join(directory, "%s.html" % instance.id)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(render_html(instance, report, agent_name=agent_name))
    index = os.path.join(directory, "index.html")
    with open(index, "w", encoding="utf-8") as handle:
        handle.write(render_index(report, agent_name=agent_name, agent_id=agent_name))
    return index


def _publish(report, include_active: bool = False) -> None:
    from datetime import datetime, timezone

    from . import __version__
    from .publish import publish_report, summarize
    from .source import PrefactorPublisher, PrefactorSourceError

    try:
        publisher = PrefactorPublisher()
    except PrefactorSourceError as error:
        print("Publish skipped: %s" % error, file=sys.stderr)
        return
    stamp = datetime.now(timezone.utc).isoformat()
    outcomes = publish_report(report, publisher, __version__, run_timestamp=stamp,
                              include_active=include_active)
    print(summarize(outcomes))


if __name__ == "__main__":
    raise SystemExit(main())
