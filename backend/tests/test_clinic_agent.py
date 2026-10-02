import asyncio
import json
from pathlib import Path

import pytest
from pipecat.flows import ContextStrategy, FlowManager

from agent_tools.context import shared_availability

from agent_builder import AgentBuilder, validate_agent


CLINIC = Path(__file__).resolve().parent.parent / "agents" / "clinic-scheduler.json"


@pytest.fixture
def clinic(monkeypatch) -> dict:
    monkeypatch.delenv("CMD_API_KEY", raising=False)
    return json.loads(CLINIC.read_text(encoding="utf-8"))


def test_clinic_agent_is_valid(clinic):
    assert validate_agent(clinic) == []


def test_unknown_tool_is_a_path_error(clinic):
    clinic["nodes"][1]["tools"] = ["update_request", "teleport"]
    assert validate_agent(clinic) == [{
        "path": "nodes[1].tools[1]",
        "message": "Unknown tool 'teleport'. Available: book_offer, lookup, update_request.",
    }]


def test_tool_named_like_an_edge_is_rejected(clinic):
    clinic["nodes"][1]["edges"][0]["function"] = "lookup"
    assert [e["path"] for e in validate_agent(clinic)] == ["nodes[1].tools[1]"]


def test_bad_context_strategy(clinic):
    clinic["nodes"][1]["context_strategy"] = "reset_with_summary"
    assert validate_agent(clinic) == [{
        "path": "nodes[1].context_strategy",
        "message": "context_strategy must be one of: append, reset.",
    }]


def test_tools_need_a_catalog(clinic):
    del clinic["catalog"]
    assert validate_agent(clinic) == [
        {"path": "catalog", "message": "Nodes use scheduling tools, so the agent needs a catalog."}]


@pytest.mark.parametrize("catalog, message", [
    ("data/missing.json", "Catalog file 'data/missing.json' not found."),
    ("../README.md", "catalog must be a path inside backend/."),
    (7, "catalog must be a file path relative to backend/."),
])
def test_bad_catalog(clinic, catalog, message):
    clinic["catalog"] = catalog
    assert validate_agent(clinic) == [{"path": "catalog", "message": message}]


def test_bad_resolver_config(clinic):
    clinic["resolver"] = {"speak_direct": "yes", "jev": {"enabled": 1, "timeout_ms": 0}}
    assert [e["path"] for e in validate_agent(clinic)] == [
        "resolver.speak_direct", "resolver.jev.enabled", "resolver.jev.timeout_ms"]


def test_bad_respond_immediately(clinic):
    clinic["nodes"][0]["respond_immediately"] = "no"
    assert [e["path"] for e in validate_agent(clinic)] == ["nodes[0].respond_immediately"]


def test_builds_and_every_tool_resolves(clinic):
    builder = AgentBuilder.from_json(CLINIC)
    initial = builder.build_initial_node()
    assert initial["name"] == "greeting"
    assert [f.name for f in initial["functions"]] == ["start", "transfer_to_staff"]
    assert "context_strategy" not in initial

    schedule = builder._make_node(builder._nodes_by_name["schedule"])
    assert [f.name for f in schedule["functions"]] == ["update_request", "lookup", "hold_slot", "transfer_to_staff"]
    assert schedule["context_strategy"].strategy is ContextStrategy.RESET
    confirm = builder._make_node(builder._nodes_by_name["confirm"])
    assert [f.name for f in confirm["functions"]] == [
        "book_offer", "update_request", "lookup", "finish", "book_another", "transfer_to_staff"]
    assert builder.tool_context.jev_client is None
    assert builder.tool_context.speak_direct is True


class RenderingFlowManager:
    def __init__(self):
        self.state = {}
        self.entered = None

    async def initialize(self, node):
        self.entered = FlowManager._render_node(self, node["name"], node)


def test_summary_placeholder_renders_from_the_start(clinic):
    builder = AgentBuilder.from_dict(clinic)
    fm = RenderingFlowManager()
    asyncio.run(builder.start(fm))
    assert fm.state == {"summary": ""}

    schedule = builder._make_node(builder._nodes_by_name["schedule"])
    rendered = FlowManager._render_node(fm, "schedule", schedule)
    assert rendered["task_messages"][0]["content"].startswith(
        "You are helping the caller book an appointment. What we know so far: \n\n")


def test_old_agents_still_build(example_agent):
    builder = AgentBuilder.from_dict(example_agent)
    assert builder.tool_context is None
    node = builder.build_initial_node()
    assert "context_strategy" not in node and "respond_immediately" not in node


def test_unknown_precondition_is_a_path_error(clinic):
    clinic["nodes"][1]["edges"][0]["precondition"] = "caller_is_nice"
    assert validate_agent(clinic) == [{
        "path": "nodes[1].edges[0].precondition",
        "message": "Unknown precondition 'caller_is_nice'. Available: offer_confirmed.",
    }]


class StateFlowManager:
    def __init__(self, state):
        self.state = state


@pytest.mark.parametrize("status", [None, "offer", "ask", "refuse", "booked"])
def test_hold_slot_is_refused_until_a_read_back(clinic, status):
    builder = AgentBuilder.from_dict(clinic)
    schedule = builder._make_node(builder._nodes_by_name["schedule"])
    hold_slot = next(f for f in schedule["functions"] if f.name == "hold_slot")
    fm = StateFlowManager({"summary": "", **({"status": status} if status else {})})
    result, next_node = asyncio.run(hold_slot.handler({}, fm))
    assert next_node is None
    assert result["status"] == "error"
    assert result["error"].startswith("The caller has not been read back an appointment yet.")


def test_hold_slot_after_a_read_back_enters_confirm(clinic):
    builder = AgentBuilder.from_dict(clinic)
    schedule = builder._make_node(builder._nodes_by_name["schedule"])
    hold_slot = next(f for f in schedule["functions"] if f.name == "hold_slot")
    result, next_node = asyncio.run(hold_slot.handler({}, StateFlowManager({"status": "confirm"})))
    assert result == {"status": "success"}
    assert next_node["name"] == "confirm"


class BookingFlowManager:
    def __init__(self):
        self.state = {"summary": ""}
        self.worker = self
        self.spoken = []

    async def queue_frame(self, frame):
        self.spoken.append(frame.text)


def _function(node, name):
    return next(f for f in node["functions"] if f.name == name)


@pytest.fixture
def fresh_bookings():
    shared_availability.cache_clear()
    yield
    shared_availability.cache_clear()


def test_booking_reenters_confirm_with_a_prompt_that_no_longer_says_book(clinic, fresh_bookings):
    events = []

    async def on_event(event):
        events.append(event)

    builder = AgentBuilder.from_dict(clinic, on_event=on_event)
    fm = BookingFlowManager()
    schedule = builder._make_node(builder._nodes_by_name["schedule"])
    update = _function(schedule, "update_request")
    asyncio.run(update.handler({"service_phrase": "cardiology consultation", "specialty_hint": "Cardiology",
                                "provider_phrase": "Dr. Chen", "is_new": True, "has_referral": True,
                                "time_pref": {"soonest": True}}, fm))
    asyncio.run(update.handler({"pick_offer": 1}, fm))
    _, confirm = asyncio.run(_function(schedule, "hold_slot").handler({}, fm))
    before = FlowManager._render_node(fm, "confirm", confirm)["task_messages"][0]["content"]
    assert not before.startswith("The appointment: Booked:")

    result, reentered = asyncio.run(_function(confirm, "book_offer").handler({}, fm))
    assert result["status"] == "booked"
    assert reentered["name"] == "confirm"
    assert reentered["respond_immediately"] is False
    assert reentered["context_strategy"].strategy is ContextStrategy.RESET
    after = FlowManager._render_node(fm, "confirm", reentered)["task_messages"][0]["content"]
    assert after.startswith(
        f"The appointment: Booked: cardiology consultation with Dr. Emily Chen, tomorrow at 8 at Downtown, "
        f"confirmation {result['ref']}. The caller has heard the confirmation reference.")
    assert fm.spoken[-1] == result["spoken"]
    assert events[-1]["type"] == "node_entered" and events[-1]["node"] == "confirm"
    assert events[-1]["state"]["bookings"] == [
        {k: result[k] for k in ("ref", "visit", "provider", "location", "when")}]
