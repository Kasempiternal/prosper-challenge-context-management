"""A live call: start carried "sick visit, fever since yesterday, returning patient" into the
schedule node, whose reset context held only that summary, and the LLM asked "which day" instead of
sending it. The edge action's first_tool now makes the node's first LLM request call update_request."""

import asyncio
import json
from pathlib import Path

import pytest
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.processors.aggregators.llm_context import LLMContext

from agent_builder import AgentBuilder, AgentConfig

NATIONAL = Path(__file__).resolve().parent.parent / "agents" / "national-scheduler.json"
WORDS = "sick visit, fever since yesterday, returning patient"
FORCED = {"type": "function", "function": {"name": "update_request"}}


class StateFlowManager:
    def __init__(self):
        self.state = {"summary": ""}


def _function(node, name):
    return next(f for f in node["functions"] if f.name == name)


def _config(monkeypatch) -> AgentConfig:
    monkeypatch.delenv("CMD_API_KEY", raising=False)
    return AgentConfig.from_dict(json.loads(NATIONAL.read_text(encoding="utf-8")))


@pytest.fixture
def forced():
    return []


@pytest.fixture
def builder(monkeypatch, forced):
    return AgentBuilder(_config(monkeypatch), force_tool=forced.append)


def test_start_with_the_callers_words_forces_update_request_once(builder, forced):
    greeting = builder.build_initial_node()
    result, schedule = asyncio.run(_function(greeting, "start").handler({"request": WORDS}, StateFlowManager()))
    assert (result, schedule["name"]) == ({"status": "success", "request": WORDS}, "schedule")
    assert forced == ["update_request"]


@pytest.mark.parametrize("args", [{}, {"request": "   "}])
def test_start_without_words_forces_nothing(builder, forced, args):
    greeting = builder.build_initial_node()
    result, next_node = asyncio.run(_function(greeting, "start").handler(args, StateFlowManager()))
    assert next_node is None and result["status"] == "error"
    assert forced == []


def test_only_book_another_forces_a_tool_out_of_booked(builder, forced):
    booked = builder._make_node(builder._nodes_by_name["booked"])
    for name in ("finish", "transfer_to_staff"):
        asyncio.run(_function(booked, name).handler({}, StateFlowManager()))
    assert forced == []
    _, schedule = asyncio.run(_function(booked, "book_another").handler({"request": "flu shot in Trenton"},
                                                                        StateFlowManager()))
    assert schedule["name"] == "schedule" and forced == ["update_request"]


class CapturedRequests:
    def __init__(self):
        self.sent = []

    async def create(self, **params):
        self.sent.append(params)
        return None


def _tools(node) -> ToolsSchema:
    return ToolsSchema(standard_tools=[f.to_function_schema() for f in node["functions"]])


def test_the_llm_request_after_start_carries_tool_choice_and_only_that_one(bot, monkeypatch):
    config = _config(monkeypatch)
    llm = bot.make_llm(config, {"OPENAI_API_KEY": "sk-test-0000"})
    requests = CapturedRequests()
    monkeypatch.setattr(llm._client.chat.completions, "create", requests.create)
    builder = AgentBuilder(config, force_tool=llm.force_tool_once)

    greeting = builder.build_initial_node()
    _, schedule = asyncio.run(_function(greeting, "start").handler({"request": WORDS}, StateFlowManager()))
    context = LLMContext(messages=[{"role": "developer", "content": f"What we know so far: {WORDS}"}],
                         tools=_tools(schedule))
    for _ in range(2):
        asyncio.run(llm.get_chat_completions(context))

    first, second = requests.sent
    assert first["tool_choice"] == FORCED and first["parallel_tool_calls"] is False
    assert not second["tool_choice"] and second["parallel_tool_calls"] is False


def test_a_forced_tool_the_request_does_not_offer_is_not_sent_and_is_spent(bot, monkeypatch):
    config = _config(monkeypatch)
    llm = bot.make_llm(config, {"OPENAI_API_KEY": "sk-test-0000"})
    requests = CapturedRequests()
    monkeypatch.setattr(llm._client.chat.completions, "create", requests.create)
    builder = AgentBuilder(config)
    greeting, schedule = (builder.build_initial_node(), builder._make_node(builder._nodes_by_name["schedule"]))

    llm.force_tool_once("update_request")
    asyncio.run(llm.get_chat_completions(LLMContext(messages=[{"role": "user", "content": "hi"}],
                                                    tools=_tools(greeting))))
    asyncio.run(llm.get_chat_completions(LLMContext(messages=[{"role": "user", "content": "hi"}],
                                                    tools=_tools(schedule))))
    assert [bool(p["tool_choice"]) for p in requests.sent] == [False, False]
