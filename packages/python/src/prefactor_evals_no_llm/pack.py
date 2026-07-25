"""Pack loading and config resolution.

Three layers, later wins: eval defaults, pack YAML, user overrides in their copy
of the pack YAML. There is deliberately no environment variable layer.
Thresholds belong in a file that gets committed and reviewed, not in shell state.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional


class PackError(RuntimeError):
    pass


# The built in standard pack. Defined here rather than read from a file so it is
# always available in an installed package, where the spec directory is not
# bundled. spec/packs/standard.yaml is the documented, editable copy of the same
# thing; this is the one the tool falls back to when no pack is given.
#
# Every check here is generic and runs with no configuration. The two benchmark
# checks calibrate to the agent's own normal. This is the whole product for most
# people: point it at any agent and get a health read, no setup.
STANDARD_PACK = {
    "pack": "standard",
    "version": 1,
    "schema_version": "v1",
    "evals": {
        "core.errors": {},
        "core.loop_detection": {"max_occurrences": 3},
        "core.redundant_tool_calls": {"max_repeats": 2},
        "core.termination_state": {"allow_states": ["complete"]},
        "core.latency_budget": {"max_span_ms": 60000},
        "core.efficiency": {"tolerance": 3.0, "floor": 12},
        "core.conversation_length": {"tolerance": 3.0, "floor": 10},
    },
}


def _load_yaml(text: str) -> dict:
    try:
        import yaml
    except ImportError as error:
        raise PackError(
            "Reading pack files needs PyYAML. Install it with "
            "`pip install PyYAML`, or pass a .json pack instead."
        ) from error
    return yaml.safe_load(text) or {}


# Packs shipped inside the package, so `--pack advanced` works for a pip
# installed user who has no checkout and no spec directory on disk. The spec
# copy is the documented original; a test asserts the two never drift.
_BUNDLED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "packs")
BUNDLED_PACKS = ("standard", "advanced")


class Pack:
    def __init__(self, data: dict, path: Optional[str] = None):
        self.path = path
        self.id = data.get("pack") or "custom"
        self.version = data.get("version", 1)
        self.schema_version = data.get("schema_version", "v1")
        self.description = data.get("description", "")
        self.span_type_map = data.get("span_type_map") or {}
        raw = data.get("evals") or {}
        if not isinstance(raw, dict):
            raise PackError("`evals` must be a mapping of eval id to config")
        # A bare `eval_id:` with no value parses as None. Treat it as empty
        # config rather than crashing, since it is a natural thing to write.
        self.evals = {k: (v if isinstance(v, dict) else {}) for k, v in raw.items()}

    @property
    def eval_ids(self) -> list:
        return list(self.evals.keys())

    def config_for(self, eval_id: str) -> dict:
        return dict(self.evals.get(eval_id) or {})

    @classmethod
    def load(cls, path: str) -> "Pack":
        # A bare built in name resolves to the bundled pack, so `--pack
        # advanced` works without a checkout. A real file of the same name in
        # the current directory still wins, since an explicit file is the more
        # deliberate act.
        if path in BUNDLED_PACKS and not os.path.exists(path):
            if path == "standard":
                return cls.from_dict(STANDARD_PACK)
            path = os.path.join(_BUNDLED_DIR, path + ".yaml")
        if not os.path.exists(path):
            raise PackError(
                "Pack file not found: %s. Pass a path to a pack file, or a "
                "built in name: %s." % (path, ", ".join(BUNDLED_PACKS)))
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        data = json.loads(text) if path.lower().endswith(".json") else _load_yaml(text)
        if not isinstance(data, dict):
            raise PackError("Pack file did not parse to a mapping: " + path)
        return cls(data, path)

    @classmethod
    def from_dict(cls, data: dict) -> "Pack":
        return cls(data)
