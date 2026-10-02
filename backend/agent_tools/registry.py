"""Tool name -> factory(ToolContext) -> FlowsFunctionSchema. Agent JSON names tools from here;
the handler owns the parameter schema. Edge guards are named the same way: an edge with
"precondition": "<name>" is refused (a tool error, no transition) while the guard returns a reason.
A tool handler may return Reenter as its next node: the builder enters the current node again,
re-rendering its prompts from state."""

from typing import Callable, Optional

from pipecat.flows import FlowsFunctionSchema

from .context import ToolContext
from .scheduling_tools import Reenter, book_offer_tool, lookup_tool, offer_confirmed, update_request_tool

ToolFactory = Callable[[ToolContext], FlowsFunctionSchema]
EdgeGuard = Callable[[dict], Optional[str]]  # flow state -> reason the edge is not allowed yet

TOOLS: dict[str, ToolFactory] = {
    "update_request": update_request_tool,
    "lookup": lookup_tool,
    "book_offer": book_offer_tool,
}

EDGE_GUARDS: dict[str, EdgeGuard] = {
    "offer_confirmed": offer_confirmed,
}

TOOL_NAMES = frozenset(TOOLS)
GUARD_NAMES = frozenset(EDGE_GUARDS)


def build_tool(name: str, ctx: ToolContext) -> FlowsFunctionSchema:
    return TOOLS[name](ctx)
