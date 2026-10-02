import asyncio

from agent_builder import AgentBuilder


class FakeFlowManager:
    def __init__(self):
        self.state = {}
        self.initialized_with = None

    async def initialize(self, node):
        self.initialized_with = node


def test_live_call_events(example_agent):
    events = []

    async def on_event(event):
        events.append(event)

    async def scenario():
        builder = AgentBuilder.from_dict(example_agent, on_event=on_event)
        fm = FakeFlowManager()
        await builder.start(fm)
        greeting = fm.initialized_with
        result, next_node = await greeting["functions"][0].handler({"intent": "book"}, fm)
        assert result == {"status": "success", "intent": "book"}
        assert next_node["name"] == "collect_details"

        confirm = builder._make_node(builder._nodes_by_name["confirm"])
        assert [a["type"] for a in confirm["post_actions"]] == ["function", "end_conversation"]
        await confirm["post_actions"][0]["handler"](confirm["post_actions"][0], fm)

    asyncio.run(scenario())
    assert events == [
        {"type": "node_entered", "node": "greeting", "state": {"summary": ""}},
        {
            "type": "edge_taken",
            "function": "choose_intent",
            "from": "greeting",
            "to": "collect_details",
            "args": {"intent": "book"},
        },
        {
            "type": "node_entered",
            "node": "collect_details",
            "state": {"summary": "", "intent": "book"},
        },
        {"type": "call_ended", "reason": "end_node"},
    ]
