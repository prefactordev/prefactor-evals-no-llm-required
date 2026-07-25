"""Deterministic, non-LLM evals for AI agents.

There is no model client anywhere in this package and there never will be. LLM
evals, when they ship, are separate packages, so this one cannot cost a token
by construction.
"""

from .pack import Pack, PackError
from .publish import build_block, publish_report
from .registry import EvalContext, all_evals, load_all, register
from .report import scorecard, write_json
from .result import EvalResult, FAIL, PASS, SKIP, failed, passed, skipped
from .runner import RunReport, run, run_pack
from .schema import SCHEMA_VERSION, EvalInstance, EvalSpan

__version__ = "0.1.0"

__all__ = [
    "EvalContext", "EvalInstance", "EvalResult", "EvalSpan", "Pack", "PackError",
    "RunReport", "SCHEMA_VERSION", "FAIL", "PASS", "SKIP",
    "all_evals", "build_block", "failed", "load_all", "passed", "publish_report",
    "register", "run", "run_pack", "scorecard", "skipped", "write_json",
    "__version__",
]
