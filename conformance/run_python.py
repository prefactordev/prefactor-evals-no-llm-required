"""Emit the Python implementation's results for the conformance fixtures.

Prints one JSON document to stdout. The TypeScript side emits the same shape and
the two are diffed. Nothing here is clever on purpose: any normalization would
risk hiding a real difference between the implementations.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "packages", "python", "src"))

from prefactor_evals_no_llm import runner  # noqa: E402
from prefactor_evals_no_llm.fixtures import load_instances  # noqa: E402
from prefactor_evals_no_llm.pack import STANDARD_PACK, Pack  # noqa: E402

def main() -> int:
    # Optional args: a pack JSON and a fixture JSON. Defaults are the standard
    # pack over the standard fixture, so the common case needs no arguments.
    if len(sys.argv) > 1:
        pack = Pack.load(sys.argv[1])
    else:
        pack = Pack.from_dict(STANDARD_PACK)
    fixture = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        REPO, "examples", "synthetic-traces", "standard.json")
    instances = load_instances(fixture, pack.span_type_map)
    report = runner.run(pack, instances)

    finished, total, rate = report.resolution
    ran, requested = report.coverage
    payload = {
        "results": [r.to_dict() for r in report.results],
        "counts": report.counts,
        "coverage": {"ran": ran, "requested": requested},
        "resolution": {"finished": finished, "total": total, "rate": rate},
    }
    json.dump(payload, sys.stdout, sort_keys=True, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
