"""Shared deterministic primitives.

Everything here is a pure function. No clock reads, no randomness, no network.
Both language implementations must agree on these byte for byte, so changing
one of them is a conformance affecting change.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Optional

from .schema import EvalSpan, sort_spans

# ---------------------------------------------------------------------------
# Field paths
# ---------------------------------------------------------------------------
# Syntax, shared by every eval that reads a user configured location:
#   "a.b"      object key
#   "a[0]"     array index
#   "a[]"      every element of an array, flattened
# A path that does not resolve yields no values, which is distinct from
# resolving to None.

_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\d+)\]|(\[\])")


def _segments(path: str):
    for match in _SEGMENT.finditer(path):
        key, index, spread = match.groups()
        if key is not None:
            yield ("key", key)
        elif index is not None:
            yield ("index", int(index))
        elif spread is not None:
            yield ("spread", None)


def read_path(root: Any, path: str) -> list:
    """Resolve a field path, returning every matching value.

    Returns a list because `[]` spreads. Callers wanting a single value use
    read_path_one.
    """
    if not path:
        return []
    current = [root]
    for kind, value in _segments(path):
        nxt = []
        for node in current:
            if kind == "key":
                if isinstance(node, dict) and value in node:
                    nxt.append(node[value])
            elif kind == "index":
                if isinstance(node, (list, tuple)) and -len(node) <= value < len(node):
                    nxt.append(node[value])
            elif kind == "spread":
                if isinstance(node, (list, tuple)):
                    nxt.extend(node)
        current = nxt
        if not current:
            return []
    return current


def read_path_one(root: Any, path: str) -> Any:
    values = read_path(root, path)
    return values[0] if values else None


def path_exists(root: Any, path: str) -> bool:
    return bool(read_path(root, path))


# ---------------------------------------------------------------------------
# Canonical serialization
# ---------------------------------------------------------------------------

def canonical(value: Any) -> str:
    """Stable text for any JSON-ish value.

    Object keys sorted recursively, array order preserved, numbers rendered
    without trailing zeros. Two structurally identical inputs always produce
    one string, which is what makes signature and hash based evals reproducible.
    """
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        # 1.0 and 1 must hash identically.
        if value.is_integer():
            return int(value)
        return value
    return value


def digest(value: Any) -> str:
    """Short content hash. Used instead of raw arguments in evidence, because
    arguments can be large and can contain sensitive values."""
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()[:16]


def strip_keys(value: Any, keys: Iterable[str]) -> Any:
    drop = set(keys or ())
    if not drop or not isinstance(value, dict):
        return value
    return {k: v for k, v in value.items() if k not in drop}


def tool_signature(span: EvalSpan, ignore_arg_keys: Iterable[str] = ()) -> str:
    """Identity of a tool call for repetition detection.

    Null input is included as a distinct value rather than skipped: an agent
    calling the same argument free tool twenty times is exactly the failure
    these evals exist to catch.
    """
    return canonical([span.name, strip_keys(span.input, ignore_arg_keys)])


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def span_ids(spans: Iterable[EvalSpan]) -> list:
    """Always sorted by start time then ID, so results are byte identical
    across runs."""
    return [s.id for s in sort_spans(spans)]


# ---------------------------------------------------------------------------
# Span selection
# ---------------------------------------------------------------------------

def select_spans(instance, names: Optional[Iterable[str]] = None,
                 schema_names: Optional[Iterable[str]] = None,
                 span_type: Optional[str] = None) -> list:
    """Select by explicit name, raw schema name, or normalized type.

    Name matching checks both `name` and `schema_name`, because users reading
    the Prefactor UI see schema names and reasonably paste those into config.
    """
    name_set = set(names or ())
    schema_set = set(schema_names or ())
    out = []
    for span in instance.spans:
        if name_set and (span.name in name_set or span.schema_name in name_set):
            out.append(span)
        elif schema_set and span.schema_name in schema_set:
            out.append(span)
        elif span_type and not name_set and not schema_set and span.type == span_type:
            out.append(span)
    return sort_spans(out)


def is_configured(*values) -> bool:
    """True when at least one config value is non empty."""
    return any(bool(v) for v in values)


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

# Constructs Python supports but other regex engines do not, or do differently.
# A pattern using one of these cannot give identical results in both language
# implementations, which is the whole promise of this library.
UNPORTABLE = (
    (re.compile(r"\(\?<[=!]"), "lookbehind"),
    (re.compile(r"\(\?P?<[A-Za-z_]"), "named group"),
    (re.compile(r"\(\?P="), "named backreference"),
    (re.compile(r"\(\?[aiLmsux]+[):]"), "inline flag group"),
    (re.compile(r"\(\?>"), "atomic group"),
    (re.compile(r"\(\?\("), "conditional"),
    (re.compile(r"\(\?R\)"), "recursion"),
    (re.compile(r"\[\[:"), "POSIX character class"),
    (re.compile(r"(?<!\\)\\[1-9]"), "backreference"),
    (re.compile(r"(?<!\\)[*+?}]\+"), "possessive quantifier"),
    # Case folding outside ASCII differs between the engines (Python folds the
    # Kelvin sign to k, JavaScript does not), so a portable pattern is ASCII
    # only. Non-ASCII strings belong on the exact match lists, which both
    # languages fold identically.
    (re.compile(r"[^\x00-\x7f]"), "a non-ASCII character"),
)

# A quantified group that is itself quantified. This is the shape behind
# catastrophic backtracking: (a+)+ against a long non-matching string explores
# exponentially many paths and hangs the process. Python's re has no timeout, so
# the only defence is refusing the pattern.
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[*+][^()]*\)\s*[*+{]")
_QUANTIFIED_ALTERNATION = re.compile(r"\((?=[^()]*\|)[^()]*\)\s*[*+]")

# A pattern long enough to be pathological even without nesting.
MAX_PATTERN_LENGTH = 1000


class UnsafePattern(ValueError):
    """A user regex that must not be compiled. Carries why, for the skip."""

    def __init__(self, pattern: str, reason: str):
        self.pattern = pattern
        self.reason = reason
        super().__init__('Pattern "%s" %s.' % (pattern[:80], reason))


def pattern_problem(pattern: str, portable: bool = False) -> Optional[str]:
    """Reason a pattern must be refused, or None.

    Two separate concerns, deliberately not merged.

    Safety is always checked. A quantifier nested inside a quantified group is
    the shape behind catastrophic backtracking, and Python's re has no timeout,
    so a pattern from a config file that may be shared or generated can hang a
    run in CI. Refusing is the only defence available.

    Portability is opt in, because it is a stricter rule that only some evals
    want. Applying it everywhere was tried and it rejected patterns other evals
    legitimately need: policy_thresholds uses a named capture group to pull out
    the value it compares, and refusal_correctness uses an inline flag. Those
    are Python specific spellings and matter for cross language agreement, but
    only where an eval has committed to that guarantee.
    """
    if not isinstance(pattern, str):
        return "is not text"
    if len(pattern) > MAX_PATTERN_LENGTH:
        return "is longer than %d characters" % MAX_PATTERN_LENGTH
    if _NESTED_QUANTIFIER.search(pattern) or _QUANTIFIED_ALTERNATION.search(pattern):
        return ("nests one quantifier inside another, which can take "
                "exponential time to fail and hang the run")
    if portable:
        for probe, label in UNPORTABLE:
            if probe.search(pattern):
                return ("uses %s, which is outside the portable regex subset "
                        "this eval accepts" % label)
    return None


def compile_patterns(patterns: Iterable[str], case_sensitive: bool = True,
                     portable: bool = False):
    """Compile user regexes, refusing unsafe ones.

    Raises UnsafePattern for a pattern that cannot be run safely, and re.error
    for one that will not compile. Callers turn both into a skip rather than a
    silent drop: a rule that quietly stopped matching reports a clean pass,
    which is worse than one that refuses to run.
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled = []
    for pattern in (patterns or ()):
        problem = pattern_problem(pattern, portable=portable)
        if problem:
            raise UnsafePattern(pattern, problem)
        compiled.append(re.compile(pattern, flags))
    return compiled


def text_of(value: Any) -> str:
    """Flatten a value to searchable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return canonical(value)


# ---------------------------------------------------------------------------
# JSON Schema safety
# ---------------------------------------------------------------------------

def _collect_refs(node, out):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            out.append(ref)
        for value in node.values():
            _collect_refs(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_refs(value, out)


# The keywords the bundled validator implements, and the annotation only ones it
# accepts and ignores. Spec: spec/shared/json-schema-subset.md. Any assertion
# keyword outside these is refused by name rather than silently skipped, because
# validating against a weaker schema than the author wrote reports a pass that
# checked less than they asked for.
_SUPPORTED_KEYWORDS = frozenset({
    "type", "enum", "const",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern",
    "items", "minItems", "maxItems", "uniqueItems",
    "required", "minProperties", "maxProperties", "properties",
    "additionalProperties",
})

_ANNOTATION_KEYWORDS = frozenset({
    "$schema", "$id", "$anchor", "$comment", "$ref", "$defs", "definitions",
    "title", "description", "default", "examples",
    "readOnly", "writeOnly", "deprecated", "format",
})


def _keyword_problem(node) -> Optional[str]:
    """Walk the schema by its structure, refusing any unsupported keyword.

    Only subschema positions are followed (properties, items, a schema valued
    additionalProperties). enum, const and default hold data, not schemas, and
    are never walked, so an object sitting inside enum data is not mistaken for
    a schema.
    """
    if isinstance(node, bool):
        return None
    if not isinstance(node, dict):
        return None
    for key in node:
        if key not in _SUPPORTED_KEYWORDS and key not in _ANNOTATION_KEYWORDS:
            return ('uses unsupported keyword "%s", outside the documented '
                    "draft 2020-12 subset this validator implements" % key)
    if "items" in node and not isinstance(node["items"], (dict, bool)):
        return ("uses items as an array (tuple validation), which is not "
                "supported; use a single schema for every element")
    pattern = node.get("pattern")
    if isinstance(pattern, str):
        # portable=True: a schema pattern is validated by both language
        # implementations, so a Python only spelling like (?i) or a named group
        # would compile here and throw there, splitting the verdict. Refused by
        # name instead, like every other cross language hazard.
        unsafe = pattern_problem(pattern, portable=True)
        if unsafe is not None:
            return "has a pattern that %s" % unsafe
    props = node.get("properties")
    if isinstance(props, dict):
        for subschema in props.values():
            problem = _keyword_problem(subschema)
            if problem is not None:
                return problem
    items = node.get("items")
    if isinstance(items, (dict, bool)):
        problem = _keyword_problem(items)
        if problem is not None:
            return problem
    additional = node.get("additionalProperties")
    if isinstance(additional, dict):
        problem = _keyword_problem(additional)
        if problem is not None:
            return problem
    return None


def schema_problem(schema) -> Optional[str]:
    """Reason a user supplied JSON Schema cannot be used, or None.

    Spec: spec/shared/json-schema-subset.md.

    Any $ref is refused. A remote one would make the library fetch a URL chosen
    by whoever wrote the config, from inside CI, on every run: a request forgery
    primitive in a library documented to make no network calls outside the seam.
    A local #/... one is refused because this validator does not resolve
    references at all, so honouring it would validate against less than the
    schema says. A schema declaring a different draft is refused rather than
    reinterpreted under rules its author did not write, and any keyword outside
    the supported subset is refused by name rather than skipped in silence.
    """
    if not isinstance(schema, dict):
        return "is not an object"
    declared = schema.get("$schema")
    if isinstance(declared, str) and "2020-12" not in declared:
        return 'declares draft "%s", only draft 2020-12 is interpreted' % declared
    refs = []
    _collect_refs(schema, refs)
    for ref in refs:
        if not ref.startswith("#"):
            return ('contains a remote $ref "%s", which would mean fetching a '
                    "URL from config on every run" % ref)
        return ('contains a $ref "%s", which this validator does not resolve; '
                "inline the referenced definition instead" % ref)
    return _keyword_problem(schema)
