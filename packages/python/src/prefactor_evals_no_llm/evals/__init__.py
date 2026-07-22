"""Importing this package registers every check.

There is one set of checks, all generic: they measure agent behaviour without
knowing or caring what the agent does. There are no domain packs. An earlier
version shipped per use case packs (support, voice, rag), but every check in
them needed rules only the user could supply, so the whole thing skipped on a
fresh install. A generic tool that works on any agent with no setup is the
product; the rest was friction.
"""

from . import core  # noqa: F401
