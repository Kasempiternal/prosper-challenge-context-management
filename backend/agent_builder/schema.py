#
# Agent schema — the declarative contract the Phase 2 Copilot reads and writes.
#
# Design rule: stay as close to Pipecat Flows' own vocabulary as possible. A node
# carries Pipecat's native fields (`role_message`, `task_messages`, `pre/post_actions`)
# verbatim. The ONLY thing we add is `edges`: transitions expressed as DATA (a string
# `target`) rather than as Python closures — because a Copilot can emit a string, not
# a callable. `AgentBuilder` turns these strings back into the closures Pipecat wants.
#

from dataclasses import dataclass, field
from typing import Optional

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs "Rachel"
DEFAULT_MODEL = "gpt-4o"


@dataclass
class Edge:
    """A transition out of a node, exposed to the LLM as a callable tool."""

    function: str            # tool name the LLM calls to take this edge
    description: str         # when the model should call it
    target: str              # node to transition to (by name)
    # Fields to collect on this edge, as JSON-schema properties.
    properties: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    precondition: Optional[str] = None  # name in agent_tools.registry.EDGE_GUARDS; refuses the edge until met
    action: Optional[str] = None        # name in agent_tools.registry.EDGE_ACTIONS; code run before the transition

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        return cls(
            function=d["function"],
            description=d["description"],
            target=d["target"],
            properties=d.get("properties", {}),
            required=d.get("required", []),
            precondition=d.get("precondition"),
            action=d.get("action"),
        )


CONTEXT_STRATEGIES = ("append", "reset")


@dataclass
class Node:
    """A single conversational state. Fields mirror Pipecat Flows' NodeConfig."""

    name: str
    task_messages: list = field(default_factory=list)   # this node's objectives
    role_message: Optional[str] = None                  # overrides the global persona
    edges: list = field(default_factory=list)           # list[Edge]; transitions out
    pre_actions: list = field(default_factory=list)
    post_actions: list = field(default_factory=list)
    end: bool = False                                   # terminal -> ends the call
    tools: list = field(default_factory=list)           # list[str]; names in agent_tools.registry
    context_strategy: str = "append"                    # "append" | "reset" on entry
    respond_immediately: Optional[bool] = None          # None = Pipecat's default (True)

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            name=d["name"],
            task_messages=d.get("task_messages", []),
            role_message=d.get("role_message"),
            edges=[Edge.from_dict(e) for e in d.get("edges", [])],
            pre_actions=d.get("pre_actions", []),
            post_actions=d.get("post_actions", []),
            end=d.get("end", False),
            tools=d.get("tools", []),
            context_strategy=d.get("context_strategy", "append"),
            respond_immediately=d.get("respond_immediately"),
        )


@dataclass
class JevConfig:
    enabled: bool = True
    timeout_ms: int = 2500

    @classmethod
    def from_dict(cls, d: dict) -> "JevConfig":
        return cls(enabled=d.get("enabled", True), timeout_ms=d.get("timeout_ms", 2500))


CHOOSERS = ("jev", "openai", "embed", "none")


@dataclass
class ResolverConfig:
    """How the scheduling tools behave. speak_direct: templated offers/questions go straight
    to TTS and skip the LLM's second round trip. chooser: the model that answers the resolver's
    ambiguous cases; absent, it is "jev" when jev.enabled (the default) and "none" otherwise.
    timeout_ms: the per-turn budget of a networked chooser; absent, jev.timeout_ms."""

    speak_direct: bool = True
    jev: JevConfig = field(default_factory=JevConfig)
    chooser: str = "jev"
    timeout_ms: int = 2500

    @classmethod
    def from_dict(cls, d: dict) -> "ResolverConfig":
        jev = JevConfig.from_dict(d.get("jev") or {})
        return cls(speak_direct=d.get("speak_direct", True), jev=jev,
                   chooser=d.get("chooser") or ("jev" if jev.enabled else "none"),
                   timeout_ms=d.get("timeout_ms", jev.timeout_ms))


@dataclass
class AgentConfig:
    """A complete agent: identity + the conversation graph."""

    name: str
    initial_node: str
    nodes: list                          # list[Node]
    persona: str = ""                    # global role_message, applied to every node
    voice_id: str = DEFAULT_VOICE_ID
    model: str = DEFAULT_MODEL
    catalog: Optional[str] = None        # catalog JSON path, relative to backend/
    resolver: ResolverConfig = field(default_factory=ResolverConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "AgentConfig":
        return cls(
            name=d["name"],
            initial_node=d["initial_node"],
            nodes=[Node.from_dict(n) for n in d["nodes"]],
            persona=d.get("persona", ""),
            voice_id=d.get("voice_id", DEFAULT_VOICE_ID),
            model=d.get("model", DEFAULT_MODEL),
            catalog=d.get("catalog"),
            resolver=ResolverConfig.from_dict(d.get("resolver") or {}),
        )
