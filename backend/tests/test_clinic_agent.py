import asyncio
import json
from pathlib import Path

import pytest
from pipecat.flows import NO_RESPONSE, ContextStrategy, FlowManager

from agent_tools.context import shared_availability

from agent_builder import AgentBuilder, validate_agent


CLINIC = Path(__file__).resolve().parent.parent / "agents" / "clinic-scheduler.json"
NATIONAL = CLINIC.with_name("national-scheduler.json")
TODAY = "Wednesday, October 7, 2026"


@pytest.fixture
def clinic(monkeypatch) -> dict:
    monkeypatch.delenv("CMD_API_KEY", raising=False)
    return json.loads(CLINIC.read_text(encoding="utf-8"))


def test_clinic_agent_is_valid(clinic):
    assert validate_agent(clinic) == []


def test_national_agent_is_valid():
    assert validate_agent(json.loads(NATIONAL.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize("path", sorted(CLINIC.parent.glob("*.json")), ids=lambda p: p.stem)
def test_every_saved_agent_is_valid(path):
    assert validate_agent(json.loads(path.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize("path", [CLINIC, NATIONAL], ids=lambda p: p.stem)
def test_a_booking_asked_as_a_question_goes_to_book_another_in_the_callers_words(path):
    """Live call: after a booking, "Can I have a flu shot in Trenton?" went to lookup, the LLM said "We
    do not offer flu shots in Trenton, but there are many other locations", then called book_another
    with "flu shot in the closest city to Trenton"."""
    agent = json.loads(path.read_text(encoding="utf-8"))
    nodes = {n["name"]: n for n in agent["nodes"]}
    booked = nodes["booked"]["task_messages"][0]["content"]
    assert "even when they ask it as a question" in booked and "never call lookup first" in booked
    assert "never add a claim of your own" in agent["persona"]
    for node in ("greeting", "booked"):
        [request] = [e["properties"]["request"] for e in nodes[node]["edges"] if e.get("action") == "new_request"]
        assert "Never paraphrase, summarize or add words of your own" in request["description"]
    assert "never answer in your own words except a lookup answer" in nodes["schedule"]["task_messages"][0]["content"]


def test_unknown_tool_is_a_path_error(clinic):
    clinic["nodes"][1]["tools"] = ["update_request", "teleport"]
    assert validate_agent(clinic) == [{
        "path": "nodes[1].tools[1]",
        "message": "Unknown tool 'teleport'. Available: lookup, update_request.",
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


def test_edge_actions_need_a_catalog(clinic):
    del clinic["catalog"]
    for node in clinic["nodes"]:
        node.pop("tools", None)
    assert validate_agent(clinic) == [
        {"path": "catalog", "message": "Nodes use scheduling tools, so the agent needs a catalog."}]


def test_unknown_action_is_a_path_error(clinic):
    clinic["nodes"][0]["edges"][0]["action"] = "teleport"
    assert validate_agent(clinic) == [{
        "path": "nodes[0].edges[0].action",
        "message": "Unknown action 'teleport'. Available: book_confirmed, new_request.",
    }]


def test_an_action_needs_the_fields_it_reads(clinic):
    start = clinic["nodes"][0]["edges"][0]
    start["required"] = []
    assert validate_agent(clinic) == [{
        "path": "nodes[0].edges[0].required",
        "message": "Action 'new_request' needs required fields: request.",
    }]


@pytest.mark.parametrize("catalog, message", [
    ("data/missing.json", "Catalog file 'data/missing.json' not found."),
    ("../README.md", "catalog must be a path inside backend/."),
    (7, "catalog must be a file path relative to backend/."),
])
def test_bad_catalog(clinic, catalog, message):
    clinic["catalog"] = catalog
    assert validate_agent(clinic) == [{"path": "catalog", "message": message}]


def test_bad_resolver_config(clinic):
    clinic["resolver"] = {"speak_direct": "yes", "chooser": "jev", "timeout_ms": True}
    assert [e["path"] for e in validate_agent(clinic)] == ["resolver.speak_direct", "resolver.timeout_ms"]


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
    assert [f.name for f in schedule["functions"]] == ["update_request", "lookup", "confirm_booking", "transfer_to_staff"]
    assert schedule["context_strategy"].strategy is ContextStrategy.RESET
    booked = builder._make_node(builder._nodes_by_name["booked"])
    assert [f.name for f in booked["functions"]] == ["lookup", "finish", "book_another", "transfer_to_staff"]
    assert builder.tool_context.model_client is None
    assert builder.tool_context.speak_direct is True


class RenderingFlowManager:
    def __init__(self):
        self.state = {}
        self.entered = None

    async def initialize(self, node):
        self.entered = FlowManager._render_node(self, node["name"], node)


def test_summary_and_today_placeholders_render_from_the_start(clinic):
    builder = AgentBuilder.from_dict(clinic)
    fm = RenderingFlowManager()
    asyncio.run(builder.start(fm))
    assert fm.state == {"summary": "", "today": TODAY}
    assert fm.entered["role_message"].startswith(f"Today is {TODAY}. You are the scheduling assistant")

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
def test_confirm_booking_is_refused_until_a_read_back(clinic, status):
    builder = AgentBuilder.from_dict(clinic)
    schedule = builder._make_node(builder._nodes_by_name["schedule"])
    fm = StateFlowManager({"summary": "", **({"status": status} if status else {})})
    result, next_node = asyncio.run(_function(schedule, "confirm_booking").handler({}, fm))
    assert next_node is None
    assert result["status"] == "error"
    assert result["error"].startswith("The caller has not been read back an appointment yet.")
    assert "bookings" not in fm.state


class BookingFlowManager:
    def __init__(self):
        self.state = {"summary": "", "today": TODAY}
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


def test_saying_yes_books_in_code_and_enters_booked(clinic, fresh_bookings):
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

    result, booked = asyncio.run(_function(schedule, "confirm_booking").handler({}, fm))
    assert result["status"] == "booked"
    assert fm.spoken[-1] == result["spoken"] == (
        f"You're all booked. Your confirmation is {', '.join(result['ref'].replace('-', ''))}. "
        "Is there anything else I can help with?")
    assert booked["name"] == "booked"
    assert booked["respond_immediately"] is False
    assert "context_strategy" not in booked
    prompt = FlowManager._render_node(fm, "booked", booked)["task_messages"][0]["content"]
    assert prompt.startswith(
        "This appointment is booked and the caller has been told its confirmation number: Booked: cardiology "
        f"consultation with Dr. Emily Chen, tomorrow at 8 at Downtown, confirmation {result['ref']}.")
    assert [e["type"] for e in events[-2:]] == ["edge_taken", "node_entered"]
    assert events[-1]["node"] == "booked"
    assert events[-1]["state"]["bookings"] == [
        {k: result[k] for k in ("ref", "visit", "provider", "location", "when")}]


def test_a_taken_slot_stays_on_schedule_and_offers_again(clinic, fresh_bookings):
    calls = []
    for _ in range(2):  # one builder per call, as bot.py does
        builder = AgentBuilder.from_dict(clinic)
        schedule, fm = builder._make_node(builder._nodes_by_name["schedule"]), BookingFlowManager()
        update = _function(schedule, "update_request")
        asyncio.run(update.handler({"service_phrase": "cardiology consultation", "specialty_hint": "Cardiology",
                                    "provider_phrase": "Dr. Chen", "is_new": True, "has_referral": True}, fm))
        asyncio.run(update.handler({"pick_offer": 1}, fm))
        calls.append((schedule, fm))
    (first, first_fm), (second, second_fm) = calls
    asyncio.run(_function(first, "confirm_booking").handler({}, first_fm))
    result, next_node = asyncio.run(_function(second, "confirm_booking").handler({}, second_fm))
    assert next_node is NO_RESPONSE
    assert result["booked"] is False
    assert second_fm.spoken[-1].startswith("I'm sorry, that time was just taken. For a cardiology consultation")
    assert second_fm.state["status"] == "offer" and "bookings" not in second_fm.state


def test_another_appointment_starts_fresh_from_the_callers_latest_words(fresh_bookings, monkeypatch):
    """The second live national call: after a booking the caller said "Yes, I need a dental cleaning.
    In Maine for tomorrow." and the old sports-injury request came back instead."""
    monkeypatch.delenv("CMD_API_KEY", raising=False)
    builder = AgentBuilder.from_json(NATIONAL)
    fm = BookingFlowManager()
    schedule = builder._make_node(builder._nodes_by_name["schedule"])
    update = _function(schedule, "update_request")
    asyncio.run(update.handler({"service_phrase": "sports injury evaluation", "location_phrase": "Austin",
                                "is_new": True, "has_referral": False, "provider_phrase": "Dr. Tiffany Garcia"}, fm))
    asyncio.run(update.handler({"pick_offer": 1}, fm))
    _, booked = asyncio.run(_function(schedule, "confirm_booking").handler({}, fm))

    words = "Yes, I need a dental cleaning. In Maine for tomorrow."
    result, entered = asyncio.run(_function(booked, "book_another").handler({"request": words}, fm))
    assert result == {"status": "success", "request": words}
    assert entered["name"] == "schedule" and entered["respond_immediately"] is True
    prompt = FlowManager._render_node(fm, "schedule", entered)["task_messages"][0]["content"]
    assert prompt.startswith(f"You are helping the caller book an appointment. What we know so far: {words}\n\n")
    assert "Garcia" not in prompt and "sports" not in prompt
    assert fm.state["req"]["patient"] == {"is_new": True, "has_referral": False}
    assert fm.state["req"]["provider"]["heard"] is None and fm.state["req"]["location"]["heard"] is None

    update = _function(entered, "update_request")
    result, _ = asyncio.run(update.handler({"service_phrase": "dental cleaning", "location_phrase": "In Maine",
                                            "time_pref": {"day": "tomorrow"}}, fm))
    assert (result["status"], result["reason"]) == ("refuse", "none_nearby")
    assert result["spoken"] == "We don't offer a dental cleaning in Maine. The nearest is Downtown in Boston. Want me to look there?"
    assert result["known"]["time"] == "thursday from 2026-10-08"


def test_book_another_without_the_callers_words_is_refused(clinic):
    builder = AgentBuilder.from_dict(clinic)
    booked = builder._make_node(builder._nodes_by_name["booked"])
    fm = StateFlowManager({"summary": "Booked: x", "status": "booked", "req": {}})
    result, next_node = asyncio.run(_function(booked, "book_another").handler({}, fm))
    assert next_node is None and result["status"] == "error"
    assert fm.state["summary"] == "Booked: x"


def test_text_sim_replays_the_second_live_national_call(fresh_bookings, monkeypatch, capsys):
    monkeypatch.delenv("CMD_API_KEY", raising=False)
    from tools import text_sim

    fm, heard = asyncio.run(text_sim.run_beat("N3"))
    refs = [b["ref"] for b in fm.state["bookings"]]
    assert [b["visit"] for b in fm.state["bookings"]] == ["sports injury evaluation", "dental cleaning"]
    assert [b["location"] for b in fm.state["bookings"]] == ["Downtown", "Downtown"]
    confirmations = [h for h in heard if h.startswith("You're all booked.")]
    assert confirmations == [f"You're all booked. Your confirmation is {', '.join(r.replace('-', ''))}. "
                             "Is there anything else I can help with?" for r in refs]
    assert "We don't offer a dental cleaning in Maine. The nearest is Downtown in Boston. Want me to look there?" in heard
    assert heard[-1] == confirmations[-1]
