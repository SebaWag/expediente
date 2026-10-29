"""The navigation agent: natural language in, recorded traversals out.

Three layers, and the separation is the argument:

* :mod:`expediente.agent.tools` — the verbs. The model picks one; deterministic code
  turns it into a graph traversal or a fetch. No verb writes to the graph.
* :mod:`expediente.agent.providers` — the model, behind a two-method interface, so
  the loop can be tested without one.
* :mod:`expediente.agent.runtime` — the plan/act/observe loop, the budget caps, and
  the dossier that keeps only what a citation supports.
"""

from .providers import (
    MIN_OUTPUT_TOKENS,
    MetaProvider,
    ModelReply,
    Provider,
    ProviderError,
    ScriptedProvider,
    ToolCall,
    tool_call,
)
from .runtime import SYSTEM_PROMPT, Investigation, Investigator, Settings
from .tools import SCHEMAS, TOOL_ORDER, ToolContext, execute, summarise

__all__ = [
    "MIN_OUTPUT_TOKENS",
    "SCHEMAS",
    "SYSTEM_PROMPT",
    "TOOL_ORDER",
    "Investigation",
    "Investigator",
    "MetaProvider",
    "ModelReply",
    "Provider",
    "ProviderError",
    "ScriptedProvider",
    "Settings",
    "ToolCall",
    "ToolContext",
    "execute",
    "summarise",
    "tool_call",
]
