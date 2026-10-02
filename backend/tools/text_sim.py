"""Text-mode call simulator: no LLM, no audio, no network.

Feeds a scripted sequence of tool calls (what the LLM would emit) through the real AgentBuilder
nodes and tool handlers, and prints what the caller would hear and every node transition.
Node prompts are rendered with Pipecat Flows' own placeholder renderer.

    backend/.venv/Scripts/python backend/tools/text_sim.py            # all demo beats
    backend/.venv/Scripts/python backend/tools/text_sim.py 2          # one beat
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from loguru import logger  # noqa: E402
from pipecat.flows import NO_RESPONSE, FlowManager  # noqa: E402
from pipecat.frames.frames import TTSSpeakFrame  # noqa: E402

from agent_builder import AgentBuilder  # noqa: E402

AGENT = BACKEND_DIR / "agents" / "clinic-scheduler.json"

# Each beat: ("caller", what they say) is narration; ("call", function, args) is the LLM's tool call.
BEATS: dict[str, list[tuple]] = {
    "1": [
        ("caller", "Hi, I'm a new patient and I have a referral. I need a cardiology consultation with Dr. Chen, soonest you have."),
        ("call", "start", {"summary": "New patient with a referral; cardiology consultation with Dr. Chen, as soon as possible."}),
        ("call", "update_request", {"service_phrase": "cardiology consultation", "specialty_hint": "Cardiology",
                                    "provider_phrase": "Dr. Chen", "is_new": True, "has_referral": True,
                                    "time_pref": {"soonest": True}}),
        ("caller", "The first one works."),
        ("call", "update_request", {"pick_offer": 1}),
        ("caller", "Yes, please."),
        ("call", "hold_slot", {}),
        ("call", "book_offer", {}),
        ("caller", "No, that's all. Thanks!"),
        ("call", "finish", {}),
    ],
    "2": [
        ("caller", "Hi, I have been a patient there for years and I have a referral. Cardiology consultation with Dr. Chen, soonest."),
        ("call", "start", {"summary": "Established patient with a referral; cardiology consultation with Dr. Chen, soonest."}),
        ("call", "update_request", {"service_phrase": "cardiology consultation", "specialty_hint": "Cardiology",
                                    "provider_phrase": "Dr. Chen", "is_new": False, "has_referral": True,
                                    "time_pref": {"soonest": True}}),
        ("caller", "Emily, please."),
        ("call", "update_request", {"provider_phrase": "Emily Chen"}),
    ],
    "3": [
        ("caller", "I'm a new patient, I need an MRI of my knee with Dr. Nwin."),
        ("call", "start", {"summary": "New patient; MRI of the knee with Dr. Nwin."}),
        ("call", "update_request", {"service_phrase": "MRI of my knee", "provider_phrase": "Dr. Nwin", "is_new": True}),
        ("caller", "Oh. Do you do eye exams?"),
        ("call", "lookup", {"kind": "do_you_offer", "phrase": "eye exam"}),
    ],
    "4": [
        ("caller", "I'm a new patient with a referral. Cardiology consultation with Dr. Chen, as soon as possible."),
        ("call", "start", {"summary": "New patient with a referral; cardiology consultation with Dr. Chen, soonest."}),
        ("call", "update_request", {"service_phrase": "cardiology consultation", "specialty_hint": "Cardiology",
                                    "provider_phrase": "Dr. Chen", "is_new": True, "has_referral": True,
                                    "time_pref": {"soonest": True}}),
        ("caller", "Sure, book it."),
        ("call", "hold_slot", {}),
        ("caller", "The second one."),
        ("call", "update_request", {"pick_offer": 2}),
        ("caller", "Yes."),
        ("call", "hold_slot", {}),
        ("call", "book_offer", {}),
        ("caller", "I also need a dermatology appointment."),
        ("call", "book_another", {}),
        ("call", "update_request", {"service_phrase": "dermatology appointment", "specialty_hint": "Dermatology"}),
    ],
    "5": [
        ("caller", "New patient with a referral, cardiology consultation with Dr. Chen, soonest."),
        ("call", "start", {"summary": "New patient with a referral; cardiology consultation with Dr. Chen, soonest."}),
        ("call", "update_request", {"service_phrase": "cardiology consultation", "specialty_hint": "Cardiology",
                                    "provider_phrase": "Dr. Chen", "is_new": True, "has_referral": True,
                                    "time_pref": {"soonest": True}}),
        ("caller", "The first one."),
        ("call", "update_request", {"pick_offer": 1}),
        ("caller", "Yes."),
        ("call", "hold_slot", {}),
        ("call", "book_offer", {}),
        ("caller", "And do you do eye exams?"),
        ("call", "lookup", {"kind": "do_you_offer", "phrase": "eye exams"}),
        ("caller", "Okay, that's all, bye."),
        ("call", "finish", {}),
    ],
}


class SimWorker:
    def __init__(self, heard: list[str]):
        self.heard = heard

    async def queue_frame(self, frame) -> None:
        if isinstance(frame, TTSSpeakFrame):
            self.heard.append(frame.text)


class SimFlowManager:
    """The slice of FlowManager the builder and handlers use: state, worker, node entry."""

    def __init__(self):
        self.state: dict = {}
        self.heard: list[str] = []
        self.worker = SimWorker(self.heard)
        self.node: dict | None = None

    async def initialize(self, node: dict) -> None:
        self.enter(node)

    def enter(self, node: dict) -> None:
        self.node = FlowManager._render_node(self, node["name"], node)
        strategy = node.get("context_strategy")
        waits = ", LLM waits for the caller" if node.get("respond_immediately") is False else ""
        print(f"\n  == node {node['name']} (context {strategy.strategy.value if strategy else 'append'}{waits}) ==")
        for message in self.node["task_messages"]:
            print("  prompt: " + message["content"].replace("\n\n", "\n          ").splitlines()[0][:220])

    def function(self, name: str):
        for f in self.node["functions"]:
            if f.name == name:
                return f
        raise SystemExit(f"node {self.node['name']} has no function {name}")


def _compact(result: dict) -> str:
    return json.dumps(result, separators=(",", ":"), ensure_ascii=False)


async def run_beat(key: str) -> None:
    print(f"\n######## Demo beat {key} ########")
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    builder = AgentBuilder.from_json(AGENT, on_event=on_event)
    fm = SimFlowManager()
    await builder.start(fm)
    for step in BEATS[key]:
        if step[0] == "caller":
            print(f"\nCALLER: {step[1]}")
            continue
        _, name, args = step
        print(f"  LLM -> {name}({_compact(args)})")
        events.clear()
        result, next_node = await fm.function(name).handler(dict(args), fm)
        for event in events:
            if event["type"] == "resolver_decision":
                print(f"  [resolver] status={event['status']} jev={event['jev']} tokens={event['tokens']} "
                      f"notes={event['notes']}")
        while fm.heard:
            print(f"AGENT (speak-direct): {fm.heard.pop(0)}")
        if next_node is NO_RESPONSE:
            print(f"  tool result (LLM stays silent): {_compact(result)}")
        elif next_node:
            fm.enter(next_node)
            if next_node.get("post_actions"):
                print("  (end node: LLM says goodbye, then the call ends)")
        else:
            print(f"  tool result (LLM phrases the reply): {_compact(result)}")
    print(f"\n  final summary: {fm.state.get('summary')!r}")


async def main(keys: list[str]) -> None:
    for key in keys:
        await run_beat(key)


if __name__ == "__main__":
    logger.remove()
    if "--jev" not in sys.argv:
        os.environ.pop("CMD_API_KEY", None)
    wanted = [a for a in sys.argv[1:] if a in BEATS] or list(BEATS)
    asyncio.run(main(wanted))
