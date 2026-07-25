"""The standard checks. Importing this module registers every one.

Grouped by what they need, not by domain, because there is no domain here.

Standard, run with no configuration: errors, latency_budget, loop_detection,
redundant_tool_calls, termination_state, and the two self calibrating benchmarks
efficiency and conversation_length.

Optional, generic but needing config, off unless a pack turns them on:
cost_budget, escalation_rule, forbidden_actions, output_schema, tool_arg_schema.
"""

# Standard, zero config.
from . import conversation_length  # noqa: F401
from . import efficiency  # noqa: F401
from . import errors  # noqa: F401
from . import latency_budget  # noqa: F401
from . import loop_detection  # noqa: F401
from . import redundant_tool_calls  # noqa: F401
from . import termination_state  # noqa: F401

# Optional, need configuration.
from . import cost_budget  # noqa: F401
from . import escalation_rule  # noqa: F401
from . import forbidden_actions  # noqa: F401
from . import output_schema  # noqa: F401
from . import tool_arg_schema  # noqa: F401
