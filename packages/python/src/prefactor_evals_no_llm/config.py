"""Reading user written config without crashing on it.

Config is typed by hand into a YAML file. Wrong types are not an edge case,
they are the normal failure. Before this module existed, an eval did
`int(config.get("max_spans", 45))` and a user who wrote `max_spans: ""` got a
ValueError, which surfaced as "this is a bug in the eval, please report it".
That message is wrong and gives them nothing to fix.

Every reader here either returns a usable value or raises ConfigError naming the
key, what was expected, and what arrived. The runner turns that into a skip, so
a typo costs a check rather than the run.

Coercion is deliberately narrow. `"5"` becomes 5 because a YAML quoting slip is
obvious and harmless. `True` does not become 1, and `"yes"` does not become
True, because guessing intent from a wrong type is how a check ends up silently
measuring something nobody asked for.
"""

from __future__ import annotations

from typing import Any, Optional


class ConfigError(ValueError):
    """A config value that cannot be used. Carries what to tell the user."""

    def __init__(self, key: str, expected: str, got: Any):
        self.key = key
        self.expected = expected
        self.got = got
        got_type = type(got).__name__
        super().__init__(
            'Config key "%s" should be %s, but got %s (%r).'
            % (key, expected, got_type, got if not isinstance(got, (list, dict))
               else (str(got)[:60] + "..." if len(str(got)) > 60 else got))
        )

    @property
    def remedy(self) -> str:
        return 'Set "%s" to %s in the pack file.' % (self.key, self.expected)


def _missing(value: Any) -> bool:
    return value is None


def cfg_int(config: dict, key: str, default: Optional[int] = None) -> Optional[int]:
    """A whole number. Accepts an int, or a string of digits."""
    value = config.get(key)
    if _missing(value):
        return default
    if isinstance(value, bool):
        raise ConfigError(key, "a whole number", value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            raise ConfigError(key, "a whole number", value) from None
    raise ConfigError(key, "a whole number", value)


def cfg_float(config: dict, key: str, default: Optional[float] = None) -> Optional[float]:
    """A number, whole or decimal."""
    value = config.get(key)
    if _missing(value):
        return default
    if isinstance(value, bool):
        raise ConfigError(key, "a number", value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            raise ConfigError(key, "a number", value) from None
    raise ConfigError(key, "a number", value)


def cfg_bool(config: dict, key: str, default: Optional[bool] = None) -> Optional[bool]:
    value = config.get(key)
    if _missing(value):
        return default
    if isinstance(value, bool):
        return value
    raise ConfigError(key, "true or false", value)


def cfg_str(config: dict, key: str, default: Optional[str] = None) -> Optional[str]:
    value = config.get(key)
    if _missing(value):
        return default
    if isinstance(value, str):
        return value
    raise ConfigError(key, "a piece of text", value)


def cfg_list(config: dict, key: str, of: Optional[type] = None) -> list:
    """A list. A bare string is rejected rather than treated as one item.

    Silently wrapping `forbidden: refund` into `["refund"]` would be friendly
    right up until someone writes `forbidden: refund, cancel` and gets one
    nonsense entry instead of an error.
    """
    value = config.get(key)
    if _missing(value):
        return []
    if isinstance(value, (str, bytes, dict)):
        raise ConfigError(key, "a list", value)
    try:
        items = list(value)
    except TypeError:
        raise ConfigError(key, "a list", value) from None
    if of is not None:
        for item in items:
            if not isinstance(item, of):
                raise ConfigError(
                    key, "a list of %s" % _describe(of), item)
    return items


def cfg_str_set(config: dict, key: str) -> set:
    """A set of names. Every entry must be text and hashable."""
    return set(cfg_list(config, key, of=str))


def cfg_dict(config: dict, key: str) -> dict:
    value = config.get(key)
    if _missing(value):
        return {}
    if not isinstance(value, dict):
        raise ConfigError(key, "a mapping", value)
    return value


def cfg_dicts(config: dict, key: str) -> list:
    """A list of entries, each a mapping. The shape that broke first."""
    items = cfg_list(config, key)
    for item in items:
        if not isinstance(item, dict):
            raise ConfigError(key, "a list of entries, each a mapping", item)
    return items


def _describe(kind: type) -> str:
    return {str: "text", int: "whole numbers", float: "numbers",
            bool: "true or false", dict: "mappings", list: "lists"}.get(
                kind, kind.__name__)
