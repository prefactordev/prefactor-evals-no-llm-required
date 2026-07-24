"""Cross language conformance: do both implementations agree, check by check.

The spec's central claim is that a check means the same thing in both languages.
Nothing enforces that except this, so it runs in CI and fails the build on any
divergence.

It compares parsed values, not raw JSON text. Python writes an integral float as
`5.0` and JavaScript writes `5`; those are the same number and a text diff would
report a difference that does not exist. Every other difference is real and is
reported with the exact path to it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def numbers_equal(a, b) -> bool:
    """5 and 5.0 are the same number. Booleans are not numbers: Python treats
    True as 1, and letting that through would hide a real type difference."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return float(a) == float(b)


def diff(a, b, path: str = "") -> list:
    """Every place the two documents disagree, as readable paths."""
    out = []
    if isinstance(a, bool) or isinstance(b, bool):
        if a is not b:
            out.append("%s: python=%r typescript=%r" % (path or "<root>", a, b))
        return out
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if not numbers_equal(a, b):
            out.append("%s: python=%r typescript=%r" % (path or "<root>", a, b))
        return out
    if type(a) is not type(b):
        out.append("%s: type differs, python=%s typescript=%s"
                   % (path or "<root>", type(a).__name__, type(b).__name__))
        return out
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                out.append("%s.%s: missing in python" % (path, key))
            elif key not in b:
                out.append("%s.%s: missing in typescript" % (path, key))
            else:
                out.extend(diff(a[key], b[key], "%s.%s" % (path, key)))
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            out.append("%s: length differs, python=%d typescript=%d" % (path, len(a), len(b)))
        for i in range(min(len(a), len(b))):
            out.extend(diff(a[i], b[i], "%s[%d]" % (path, i)))
        return out
    if a != b:
        out.append("%s:\n    python     = %r\n    typescript = %r" % (path or "<root>", a, b))
    return out


def key(result: dict) -> tuple:
    return (result.get("eval_id", ""), result.get("instance_id", ""))


def main() -> int:
    print("Running both implementations over the same fixtures...\n")

    py = subprocess.run([sys.executable, os.path.join(HERE, "run_python.py")],
                        capture_output=True, text=True)
    if py.returncode != 0:
        print("Python side failed:\n%s" % py.stderr[-2000:])
        return 2

    ts = subprocess.run(
        ["node", "--experimental-strip-types", os.path.join(HERE, "run_typescript.ts")],
        capture_output=True, text=True, cwd=REPO, shell=(os.name == "nt"))
    if ts.returncode != 0:
        print("TypeScript side failed:\n%s" % ts.stderr[-2000:])
        return 2

    try:
        python_doc = json.loads(py.stdout)
        ts_doc = json.loads(ts.stdout)
    except json.JSONDecodeError as error:
        print("Could not parse output: %s" % error)
        return 2

    # Compare results check by check, keyed rather than by position, so a
    # difference in ordering is reported as ordering rather than as every row
    # being wrong.
    py_results = {key(r): r for r in python_doc["results"]}
    ts_results = {key(r): r for r in ts_doc["results"]}

    problems = []
    only_python = sorted(set(py_results) - set(ts_results))
    only_ts = sorted(set(ts_results) - set(py_results))
    for k in only_python:
        problems.append("only python produced a result for %s" % (k,))
    for k in only_ts:
        problems.append("only typescript produced a result for %s" % (k,))

    for k in sorted(set(py_results) & set(ts_results)):
        problems.extend(diff(py_results[k], ts_results[k], "%s/%s" % k))

    for field in ("counts", "coverage", "resolution"):
        problems.extend(diff(python_doc[field], ts_doc[field], field))

    shared = len(set(py_results) & set(ts_results))
    if problems:
        print("CONFORMANCE FAILED: %d difference(s) across %d compared results\n"
              % (len(problems), shared))
        for line in problems[:40]:
            print("  " + line)
        if len(problems) > 40:
            print("  ... and %d more" % (len(problems) - 40))
        return 1

    counts = python_doc["counts"]
    print("CONFORMANCE PASSED")
    print("  %d results compared, identical in both languages" % shared)
    print("  pass %d, fail %d, skip %d"
          % (counts.get("pass", 0), counts.get("fail", 0), counts.get("skip", 0)))
    print("  coverage %(ran)d of %(requested)d checks ran" % python_doc["coverage"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
