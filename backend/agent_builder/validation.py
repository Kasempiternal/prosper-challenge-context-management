#
# Agent validation — the single rule set for "is this agent runnable?".
#
# Works on the raw dict (not AgentConfig) so it can report every problem in a
# half-edited agent from the UI, including ones that would make
# AgentConfig.from_dict fail. AgentBuilder runs the same function before compiling.
#

import re
from pathlib import Path
from typing import Any

from agent_tools import GUARD_NAMES, TOOL_NAMES

from .schema import CONTEXT_STRATEGIES

BACKEND_DIR = Path(__file__).resolve().parent.parent
FUNCTION_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


class AgentValidationError(ValueError):
    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("; ".join(f"{e['path']}: {e['message']}" for e in errors))


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def validate_agent(data: Any) -> list[dict]:
    """Return every rule violation as {path, message}; an empty list means valid."""
    errors: list[dict] = []

    def err(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    if not isinstance(data, dict):
        err("", "Agent must be a JSON object.")
        return errors

    if not _is_nonempty_str(data.get("name")):
        err("name", "Agent name is required.")
    for key in ("persona", "voice_id", "model"):
        if key in data and not isinstance(data[key], str):
            err(key, f"'{key}' must be a string.")

    _validate_catalog(data.get("catalog"), err)
    _validate_resolver(data.get("resolver"), err)

    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        err("nodes", "Agent has no nodes.")
        nodes = []

    node_names: set[str] = set()
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            err(f"nodes[{i}]", "Node must be a JSON object.")
            continue
        name = node.get("name")
        if not _is_nonempty_str(name):
            err(f"nodes[{i}].name", "Node name is required.")
        elif name in node_names:
            err(f"nodes[{i}].name", f"Duplicate node name '{name}'.")
        else:
            node_names.add(name)

    initial_node = data.get("initial_node")
    if not _is_nonempty_str(initial_node):
        err("initial_node", "initial_node is required.")
    elif initial_node not in node_names:
        err("initial_node", f"initial_node '{initial_node}' is not a defined node.")

    for i, node in enumerate(nodes):
        if isinstance(node, dict):
            _validate_node(node, f"nodes[{i}]", node_names, err)

    uses_tools = any(isinstance(n, dict) and isinstance(n.get("tools"), list) and n["tools"] for n in nodes)
    if uses_tools and data.get("catalog") is None:
        err("catalog", "Nodes use scheduling tools, so the agent needs a catalog.")

    return errors


def _validate_catalog(catalog: Any, err) -> None:
    if catalog is None:
        return
    if not _is_nonempty_str(catalog):
        err("catalog", "catalog must be a file path relative to backend/.")
        return
    path = (BACKEND_DIR / catalog).resolve()
    if Path(catalog).is_absolute() or not path.is_relative_to(BACKEND_DIR):
        err("catalog", "catalog must be a path inside backend/.")
    elif not path.is_file():
        err("catalog", f"Catalog file '{catalog}' not found.")


def _validate_resolver(resolver: Any, err) -> None:
    if resolver is None:
        return
    if not isinstance(resolver, dict):
        err("resolver", "resolver must be an object.")
        return
    if "speak_direct" in resolver and not isinstance(resolver["speak_direct"], bool):
        err("resolver.speak_direct", "speak_direct must be true or false.")
    jev = resolver.get("jev")
    if jev is None:
        return
    if not isinstance(jev, dict):
        err("resolver.jev", "jev must be an object.")
        return
    if "enabled" in jev and not isinstance(jev["enabled"], bool):
        err("resolver.jev.enabled", "enabled must be true or false.")
    timeout = jev.get("timeout_ms", 2500)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 30000:
        err("resolver.jev.timeout_ms", "timeout_ms must be an integer between 1 and 30000.")


def _validate_node(node: dict, path: str, node_names: set[str], err) -> None:
    is_end = node.get("end", False)
    if not isinstance(is_end, bool):
        err(f"{path}.end", "'end' must be true or false.")
        is_end = False

    if "role_message" in node and node["role_message"] is not None and not isinstance(
        node["role_message"], str
    ):
        err(f"{path}.role_message", "role_message must be a string.")

    for key in ("pre_actions", "post_actions"):
        if key in node and not isinstance(node[key], list):
            err(f"{path}.{key}", f"'{key}' must be a list.")

    strategy = node.get("context_strategy", "append")
    if strategy not in CONTEXT_STRATEGIES:
        err(f"{path}.context_strategy", f"context_strategy must be one of: {', '.join(CONTEXT_STRATEGIES)}.")
    respond = node.get("respond_immediately")
    if respond is not None and not isinstance(respond, bool):
        err(f"{path}.respond_immediately", "respond_immediately must be true or false.")

    task_messages = node.get("task_messages", [])
    if not isinstance(task_messages, list):
        err(f"{path}.task_messages", "task_messages must be a list.")
        task_messages = []
    if not is_end and not task_messages:
        err(f"{path}.task_messages", "Node needs at least one task message.")
    for j, msg in enumerate(task_messages):
        if not isinstance(msg, dict):
            err(f"{path}.task_messages[{j}]", "Task message must be a JSON object.")
        elif not is_end and not _is_nonempty_str(msg.get("content")):
            err(f"{path}.task_messages[{j}].content", "Task message content is empty.")

    edges = node.get("edges", [])
    if not isinstance(edges, list):
        err(f"{path}.edges", "edges must be a list.")
        edges = []
    if not edges and not is_end:
        err(f"{path}.edges", "Node has no edges; mark it as an end node or add an edge.")

    functions: set[str] = set()
    for k, edge in enumerate(edges):
        epath = f"{path}.edges[{k}]"
        if not isinstance(edge, dict):
            err(epath, "Edge must be a JSON object.")
            continue

        function = edge.get("function")
        if not isinstance(function, str) or not FUNCTION_NAME_RE.match(function):
            err(
                f"{epath}.function",
                "Function name must start with a letter or underscore and contain only "
                "letters, digits and underscores (max 64 chars).",
            )
        elif function in functions:
            err(f"{epath}.function", f"Duplicate function '{function}' in this node.")
        else:
            functions.add(function)

        if not isinstance(edge.get("description"), str):
            err(f"{epath}.description", "Edge description is required.")

        target = edge.get("target")
        if not _is_nonempty_str(target):
            err(f"{epath}.target", "Edge target is required.")
        elif target not in node_names:
            err(f"{epath}.target", f"Edge targets unknown node '{target}'.")

        precondition = edge.get("precondition")
        if precondition is not None and precondition not in GUARD_NAMES:
            err(f"{epath}.precondition",
                f"Unknown precondition {precondition!r}. Available: {', '.join(sorted(GUARD_NAMES))}.")

        properties = edge.get("properties", {})
        if not isinstance(properties, dict):
            err(f"{epath}.properties", "properties must be an object.")
            properties = {}
        required = edge.get("required", [])
        if not isinstance(required, list):
            err(f"{epath}.required", "required must be a list.")
            required = []
        missing = [r for r in required if not isinstance(r, str) or r not in properties]
        if missing:
            err(
                f"{epath}.required",
                f"Required fields not in properties: {', '.join(map(str, missing))}.",
            )

    tools = node.get("tools", [])
    if not isinstance(tools, list):
        err(f"{path}.tools", "tools must be a list of tool names.")
        tools = []
    seen: set[str] = set()
    for j, tool in enumerate(tools):
        tpath = f"{path}.tools[{j}]"
        if tool not in TOOL_NAMES:
            err(tpath, f"Unknown tool {tool!r}. Available: {', '.join(sorted(TOOL_NAMES))}.")
        elif tool in seen:
            err(tpath, f"Duplicate tool '{tool}' in this node.")
        elif tool in functions:
            err(tpath, f"Tool '{tool}' has the same name as an edge function in this node.")
        else:
            seen.add(tool)
