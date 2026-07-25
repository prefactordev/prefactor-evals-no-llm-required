"""A minimal JSON Schema validator, draft 2020-12 subset.

Spec: spec/shared/json-schema-subset.md

This is the whole reason the package has no jsonschema dependency. It implements
the keywords with a single obvious meaning and refuses anything else by name, so
that the TypeScript port can be the same code and the two can be proven to agree
on every verdict. It is deliberately small: coverage is traded for the guarantee
that both languages read a schema identically.

Nothing here reaches the network, compiles an unbounded regex, or reports an
offending value. Those are not omissions, they are the point.
"""

from __future__ import annotations

import math
import re
from typing import Any, List, NamedTuple, Optional

from .helpers import canonical

# Keywords whose value carries their own message text in short_message. The
# supported and annotation keyword sets live with schema_problem in helpers,
# which is what refuses everything outside them before iter_errors ever runs.
_BOUND_KEYWORDS = frozenset({
    "minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum",
    "minLength", "maxLength", "minItems", "maxItems",
    "minProperties", "maxProperties", "multipleOf",
})


class Error(NamedTuple):
    path: List[Any]
    keyword: str
    validator_value: Any
    instance: Any


def json_type_of(value: Any) -> str:
    """The JSON type name, with an integral float reported as an integer so the
    two languages agree on a number that one parsed as 1.0 and the other as 1."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "integer" if value.is_integer() else "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _is_number(value: Any) -> bool:
    # A boolean is not a number, matching the JSON data model.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _matches_type(value: Any, name: str) -> bool:
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return json_type_of(value) == "integer"
    if name == "number":
        return _is_number(value)
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    return False


def _equal(a: Any, b: Any) -> bool:
    # The canonical form is the same one used for signatures: 1 equals 1.0 and
    # key order does not matter, identically in both languages.
    return canonical(a) == canonical(b)


def _is_multiple(value: float, divisor: float) -> bool:
    if divisor == 0:
        return False
    quotient = value / divisor
    frac = quotient - math.floor(quotient)
    return frac < 1e-9 or frac > 1 - 1e-9


def _all_unique(items: List[Any]) -> bool:
    seen = set()
    for item in items:
        key = canonical(item)
        if key in seen:
            return False
        seen.add(key)
    return True


def _fmt_num(value: Any) -> str:
    """An integral bound prints without a trailing .0, so 5.0 in Python and 5 in
    JavaScript produce the same message text."""
    if _is_number(value) and float(value).is_integer():
        return str(int(value))
    return str(value)


def pointer_of(path: List[Any]) -> str:
    parts = [str(p).replace("~", "~0").replace("/", "~1") for p in path]
    return "/" + "/".join(parts) if parts else ""


def short_message(error: Error) -> str:
    """The one fixed phrase for a keyword. Raw validator wording never appears,
    and offending values never appear, because they can be sensitive."""
    keyword = error.keyword
    if keyword == "required":
        instance = error.instance if isinstance(error.instance, dict) else {}
        missing = [p for p in (error.validator_value or []) if p not in instance]
        name = missing[0] if missing else ""
        return 'missing required property "%s"' % name
    if keyword == "type":
        expected = error.validator_value
        if isinstance(expected, (list, tuple)):
            expected = " or ".join(str(t) for t in expected)
        return "expected %s, got %s" % (expected, json_type_of(error.instance))
    if keyword == "enum":
        return "value not in enum"
    if keyword == "const":
        return "value not equal to const"
    if keyword == "pattern":
        return 'string does not match pattern "%s"' % error.validator_value
    if keyword in _BOUND_KEYWORDS:
        return "violates %s %s" % (keyword, _fmt_num(error.validator_value))
    if keyword == "additionalProperties":
        return "additional properties are not allowed"
    if keyword == "uniqueItems":
        return "array items are not unique"
    return "failed %s constraint" % keyword


def iter_errors(schema: Any, value: Any, path: Optional[List[Any]] = None) -> List[Error]:
    """Every place the value violates the schema, as Error records.

    Assumes schema_problem has already accepted the schema, so only supported
    keywords are present. Order of production does not matter: callers sort by
    (pointer, keyword) before reporting.
    """
    if path is None:
        path = []
    errors: List[Error] = []

    if isinstance(schema, bool):
        if schema is False:
            errors.append(Error(list(path), "false", False, value))
        return errors
    if not isinstance(schema, dict):
        return errors

    if "type" in schema:
        declared = schema["type"]
        names = declared if isinstance(declared, list) else [declared]
        if not any(_matches_type(value, n) for n in names):
            errors.append(Error(list(path), "type", declared, value))

    if "enum" in schema:
        options = schema["enum"]
        if isinstance(options, list) and not any(_equal(value, o) for o in options):
            errors.append(Error(list(path), "enum", options, value))

    if "const" in schema and not _equal(value, schema["const"]):
        errors.append(Error(list(path), "const", schema["const"], value))

    if _is_number(value):
        if _is_number(schema.get("minimum")) and value < schema["minimum"]:
            errors.append(Error(list(path), "minimum", schema["minimum"], value))
        if _is_number(schema.get("exclusiveMinimum")) and value <= schema["exclusiveMinimum"]:
            errors.append(Error(list(path), "exclusiveMinimum", schema["exclusiveMinimum"], value))
        if _is_number(schema.get("maximum")) and value > schema["maximum"]:
            errors.append(Error(list(path), "maximum", schema["maximum"], value))
        if _is_number(schema.get("exclusiveMaximum")) and value >= schema["exclusiveMaximum"]:
            errors.append(Error(list(path), "exclusiveMaximum", schema["exclusiveMaximum"], value))
        if _is_number(schema.get("multipleOf")) and not _is_multiple(value, schema["multipleOf"]):
            errors.append(Error(list(path), "multipleOf", schema["multipleOf"], value))

    if isinstance(value, str):
        length = len(value)  # Python len counts code points.
        if _is_number(schema.get("minLength")) and length < schema["minLength"]:
            errors.append(Error(list(path), "minLength", schema["minLength"], value))
        if _is_number(schema.get("maxLength")) and length > schema["maxLength"]:
            errors.append(Error(list(path), "maxLength", schema["maxLength"], value))
        pattern = schema.get("pattern")
        # re.ASCII so \d, \w, \s and \b mean what they mean in JavaScript, where
        # they are ASCII only. Python's default is Unicode aware, so without this
        # the same pattern over the same value can pass here and fail there. The
        # JSON Schema spec defines pattern as an ECMA-262 regex anyway.
        if isinstance(pattern, str) and re.search(pattern, value, re.ASCII) is None:
            errors.append(Error(list(path), "pattern", pattern, value))

    if isinstance(value, list):
        count = len(value)
        if _is_number(schema.get("minItems")) and count < schema["minItems"]:
            errors.append(Error(list(path), "minItems", schema["minItems"], value))
        if _is_number(schema.get("maxItems")) and count > schema["maxItems"]:
            errors.append(Error(list(path), "maxItems", schema["maxItems"], value))
        if schema.get("uniqueItems") is True and not _all_unique(value):
            errors.append(Error(list(path), "uniqueItems", True, value))
        items = schema.get("items")
        if isinstance(items, (dict, bool)):
            for index, element in enumerate(value):
                errors.extend(iter_errors(items, element, path + [index]))

    if isinstance(value, dict):
        keys = list(value.keys())
        required = schema.get("required")
        if isinstance(required, list) and any(p not in value for p in required):
            errors.append(Error(list(path), "required", required, value))
        if _is_number(schema.get("minProperties")) and len(keys) < schema["minProperties"]:
            errors.append(Error(list(path), "minProperties", schema["minProperties"], value))
        if _is_number(schema.get("maxProperties")) and len(keys) > schema["maxProperties"]:
            errors.append(Error(list(path), "maxProperties", schema["maxProperties"], value))
        props = schema.get("properties")
        defined = set(props.keys()) if isinstance(props, dict) else set()
        if isinstance(props, dict):
            for name, subschema in props.items():
                if name in value:
                    errors.extend(iter_errors(subschema, value[name], path + [name]))
        additional = schema.get("additionalProperties")
        if additional is False:
            if any(k not in defined for k in keys):
                errors.append(Error(list(path), "additionalProperties", False, value))
        elif isinstance(additional, dict):
            for key in keys:
                if key not in defined:
                    errors.extend(iter_errors(additional, value[key], path + [key]))

    return errors


def first_error(schema: Any, value: Any) -> Optional[Error]:
    errors = iter_errors(schema, value)
    if not errors:
        return None
    errors.sort(key=lambda e: (pointer_of(e.path), e.keyword))
    return errors[0]


def sorted_errors(schema: Any, value: Any) -> List[Error]:
    errors = iter_errors(schema, value)
    errors.sort(key=lambda e: (pointer_of(e.path), e.keyword))
    return errors
