#
# AgentBuilder — loads a declarative agent (JSON / dict) and compiles its node
# graph into Pipecat Flows objects.
#
#   JSON  ->  AgentConfig (validated)  ->  Pipecat Flows NodeConfig graph
#
# This is the seam between "agent as data" (what the Phase 2 Copilot produces)
# and "agent as a running conversation" (what bot.py executes). Keeping the
# compile + validation here means bot.py never touches the graph internals.
#
# Live call events (node_entered / edge_taken / call_ended) are emitted through
# an optional async `on_event(dict)` callback; bot.py forwards them to the client.
#

import json
from dataclasses import asdict
from pathlib import Path
from typing import Awaitable, Callable, Optional, Union

from loguru import logger
from pipecat_flows import FlowManager, FlowsFunctionSchema, NodeConfig

from .schema import AgentConfig, Edge, Node
from .validation import AgentValidationError, validate_agent

EventCallback = Callable[[dict], Awaitable[None]]


class AgentBuilder:
    """Builds a runnable Pipecat Flows graph from a declarative AgentConfig."""

    def __init__(self, config: AgentConfig, on_event: Optional[EventCallback] = None):
        self.config = config
        self._on_event = on_event
        self._nodes_by_name = {n.name: n for n in config.nodes}
        self._validate()

    # ---- loading -----------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict, on_event: Optional[EventCallback] = None) -> "AgentBuilder":
        # Validate the raw dict first so malformed input yields path-tagged errors
        # instead of a KeyError from AgentConfig.from_dict.
        errors = validate_agent(data)
        if errors:
            raise AgentValidationError(errors)
        return cls(AgentConfig.from_dict(data), on_event=on_event)

    @classmethod
    def from_json(
        cls, path: Union[str, Path], on_event: Optional[EventCallback] = None
    ) -> "AgentBuilder":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data, on_event=on_event)

    # ---- validation --------------------------------------------------------
    def _validate(self) -> None:
        errors = validate_agent(asdict(self.config))
        if errors:
            raise AgentValidationError(errors)

    # ---- running -----------------------------------------------------------
    async def start(self, flow_manager: FlowManager) -> None:
        """Enter the initial node and report it."""
        await flow_manager.initialize(self.build_initial_node())
        await self._emit(
            {
                "type": "node_entered",
                "node": self.config.initial_node,
                "state": dict(flow_manager.state),
            }
        )

    async def _emit(self, event: dict) -> None:
        if self._on_event:
            await self._on_event(event)

    # ---- compilation -------------------------------------------------------
    def build_initial_node(self) -> NodeConfig:
        """Return the entry NodeConfig; downstream nodes are built lazily on transition."""
        return self._make_node(self._nodes_by_name[self.config.initial_node])

    def _make_node(self, node: Node) -> NodeConfig:
        node_config: NodeConfig = {
            "name": node.name,
            "role_message": node.role_message or self.config.persona,
            "task_messages": node.task_messages,
            "functions": [self._make_edge_function(node, edge) for edge in node.edges],
        }
        if node.pre_actions:
            node_config["pre_actions"] = node.pre_actions
        # Explicit post_actions win; otherwise a terminal node ends the call.
        post_actions = list(node.post_actions)
        if not post_actions and node.end:
            post_actions = [{"type": "end_conversation"}]
        if node.end:
            # A "function" action makes Flows wait for it to finish before the
            # end_conversation EndFrame is queued, so the event reaches the client.
            post_actions.insert(0, {"type": "function", "handler": self._on_call_ended})
        if post_actions:
            node_config["post_actions"] = post_actions
        return node_config

    async def _on_call_ended(self, action: dict, flow_manager: FlowManager) -> None:
        await self._emit({"type": "call_ended", "reason": "end_node"})

    def _make_edge_function(self, node: Node, edge: Edge) -> FlowsFunctionSchema:
        async def handler(args: dict, flow_manager: FlowManager):
            # Persist what the caller gave us so later nodes can use it.
            flow_manager.state.update(args)
            logger.info(f"[{edge.function}] -> {edge.target} | collected: {args}")
            next_node = self._make_node(self._nodes_by_name[edge.target])
            await self._emit(
                {
                    "type": "edge_taken",
                    "function": edge.function,
                    "from": node.name,
                    "to": edge.target,
                    "args": dict(args),
                }
            )
            await self._emit(
                {"type": "node_entered", "node": edge.target, "state": dict(flow_manager.state)}
            )
            return {"status": "success", **args}, next_node

        return FlowsFunctionSchema(
            name=edge.function,
            description=edge.description,
            properties=edge.properties,
            required=edge.required,
            handler=handler,
        )
