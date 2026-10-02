"""Tool name -> factory(ToolContext) -> FlowsFunctionSchema. Agent JSON names tools from here;
the handler owns the parameter schema. Edges name code from here too: "precondition": "<guard>"
refuses the edge (a tool error, no transition) while the guard returns a reason, and
"action": "<action>" runs code before the transition, which may keep the call on the node."""

from typing import Callable, Optional

from pipecat.flows import FlowsFunctionSchema

from .context import ToolContext
from .scheduling_tools import (EdgeAction, book_confirmed, lookup_tool, new_request, offer_confirmed,
                               update_request_tool)

ToolFactory = Callable[[ToolContext], FlowsFunctionSchema]
EdgeGuard = Callable[[dict], Optional[str]]  # flow state -> reason the edge is not allowed yet

TOOLS: dict[str, ToolFactory] = {
    "update_request": update_request_tool,
    "lookup": lookup_tool,
}

EDGE_GUARDS: dict[str, EdgeGuard] = {
    "offer_confirmed": offer_confirmed,
}

EDGE_ACTIONS: dict[str, EdgeAction] = {
    "book_confirmed": EdgeAction(book_confirmed),
    "new_request": EdgeAction(new_request, params=("request",)),
}

TOOL_NAMES = frozenset(TOOLS)
GUARD_NAMES = frozenset(EDGE_GUARDS)
ACTION_NAMES = frozenset(EDGE_ACTIONS)


def build_tool(name: str, ctx: ToolContext) -> FlowsFunctionSchema:
    return TOOLS[name](ctx)
