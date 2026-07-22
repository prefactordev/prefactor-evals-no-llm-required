"""Run a pack over a set of instances."""

from __future__ import annotations

from typing import Any, Optional

from . import registry
from .config import ConfigError
from .pack import Pack
from .result import EvalResult, FAIL, PASS, SKIP, skipped

# Evals that read instance level cost, so the seam knows to make the extra
# detail call. Fetching cost for every instance when nothing reads it would
# multiply the request count for no reason.
COST_EVALS = ("core.cost_budget",)


class RunReport:
    def __init__(self, pack: Pack, results: list, instances: list,
                 unknown_eval_ids: Optional[list] = None):
        self.pack = pack
        self.results = results
        self.instances = instances
        self.unknown_eval_ids = unknown_eval_ids or []

    # -- Aggregates ---------------------------------------------------------

    @property
    def counts(self) -> dict:
        out = {PASS: 0, FAIL: 0, SKIP: 0}
        for result in self.results:
            out[result.status] = out.get(result.status, 0) + 1
        return out

    def by_eval(self) -> dict:
        grouped: dict = {}
        for result in self.results:
            entry = grouped.setdefault(
                result.eval_id, {PASS: 0, FAIL: 0, SKIP: 0, "remedy": None, "details": []}
            )
            entry[result.status] += 1
            if result.status == SKIP and result.remedy and not entry["remedy"]:
                entry["remedy"] = result.remedy
            if result.status == FAIL and len(entry["details"]) < 3:
                entry["details"].append((result.instance_id, result.details))
        return grouped

    @property
    def coverage(self) -> tuple:
        """How many evals actually ran, out of how many were requested.

        Coverage going up is the first week of using this library, so it is
        reported as a first class number rather than buried.
        """
        grouped = self.by_eval()
        ran = sum(1 for e in grouped.values() if e[PASS] or e[FAIL])
        return ran, len(grouped)

    @property
    def resolution(self) -> tuple:
        """How many runs reached completion, and the rate.

        A fleet level health signal in its own right: the share of runs that
        actually finished, rather than ending cancelled, failed, or still
        active. Reported as a headline number rather than a per run check,
        because "did this one run finish" is what termination_state already
        answers and the rate is the aggregate of that.
        """
        finished = sum(1 for i in self.instances if i.state == "complete")
        total = len(self.instances)
        rate = (finished / total) if total else 0.0
        return finished, total, rate

    @property
    def failed(self) -> bool:
        return self.counts.get(FAIL, 0) > 0

    def to_dict(self, run_timestamp: Optional[str] = None) -> dict:
        return {
            "schema_version": self.pack.schema_version,
            "pack": {"id": self.pack.id, "version": self.pack.version,
                     "path": self.pack.path},
            "run_timestamp": run_timestamp,
            "instance_count": len(self.instances),
            "counts": self.counts,
            "coverage": {"ran": self.coverage[0], "requested": self.coverage[1]},
            "unknown_eval_ids": self.unknown_eval_ids,
            "results": [r.to_dict() for r in self.results],
        }


def run(pack: Pack, instances: list) -> RunReport:
    """Execute every eval in the pack against every instance."""
    registry.load_all()
    context = registry.EvalContext(instances=instances, span_type_map=pack.span_type_map)

    results: list = []
    unknown: list = []

    for eval_id in pack.eval_ids:
        registered = registry.get(eval_id)
        if registered is None:
            unknown.append(eval_id)
            continue
        config = pack.config_for(eval_id)
        for instance in instances:
            try:
                result = registered.fn(instance, config, context)
            except ConfigError as error:
                # A wrong value in a hand written file. Name the key and the
                # expected type. This is the single most likely failure and it
                # is the user's to fix, so it must not read like a defect.
                result = skipped(eval_id, instance.id, str(error),
                                 remedy=error.remedy)
            except Exception as error:  # noqa: BLE001
                # Anything else. Still never fatal and never a pass, but do not
                # assert whose fault it is: a malformed config reaches code that
                # predates the typed readers just as easily as a real defect.
                result = skipped(
                    eval_id, instance.id,
                    "%s could not run: %s: %s"
                    % (eval_id, type(error).__name__, error),
                    remedy=("Check this eval's settings in the pack file first. "
                            "If they look right, this is a bug worth reporting."),
                )
            if result is not None:
                results.append(result)

    return RunReport(pack, results, instances, unknown)


def run_pack(pack_path: Optional[str], agent_id: str, since: Any = None,
             until: Any = None, state: Optional[str] = None,
             environment: Optional[str] = None, purpose: Optional[str] = "live",
             limit: Optional[int] = None, source: Any = None) -> RunReport:
    """Load a pack, fetch instances through the seam, run everything.

    With no pack path, the built in standard pack is used: the zero config
    health checks that run on any agent. That is the default experience.
    """
    from .pack import STANDARD_PACK
    pack = Pack.load(pack_path) if pack_path else Pack.from_dict(STANDARD_PACK)
    needs_cost = any(e in pack.evals for e in COST_EVALS)

    if source is None:
        from .source import PrefactorSource
        source = PrefactorSource(span_type_map=pack.span_type_map)

    instances = source.fetch_instances(
        agent_id, since=since, until=until, state=state, environment=environment,
        purpose=purpose, limit=limit, with_cost=needs_cost,
    )
    return run(pack, instances)
