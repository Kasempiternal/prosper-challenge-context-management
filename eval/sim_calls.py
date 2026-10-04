"""Unscripted text calls: a model plays a realistic caller, the real agent answers, code scores the booking.

The agent side is the shipped one: the agent JSON's node prompts and tools, gpt-4o as the conversation
model, the real tool handlers, the resolver and the disambiguator (JEV, OpenAI or none). Only the audio
is left out (no speech recognition, no voice), so a call costs LLM tokens only.

The truth is picked from the catalog by code before any caller is written, so the harness cannot grade
itself: a caller model only turns a chosen target into a person who half-remembers it.

    backend/.venv/Scripts/python eval/sim_calls.py make   --set pilot [--n 20]   # freeze targets + personas
    backend/.venv/Scripts/python eval/sim_calls.py run    --set pilot --mode jev # run the calls
    backend/.venv/Scripts/python eval/sim_calls.py report --set pilot            # tables

Files: eval/sim_calls/<set>_targets.jsonl (frozen before any run), <set>_<mode>.jsonl (results),
<set>_<mode>.md (transcripts). Modes are the resolver's choosers: jev | openai | embed | none.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import random
import re
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402
from loguru import logger  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from pipecat.flows import NO_RESPONSE, FlowManager  # noqa: E402
from pipecat.frames.frames import TTSSpeakFrame  # noqa: E402

from agent_builder import AgentBuilder  # noqa: E402
from agent_builder.schema import AgentConfig  # noqa: E402
from scheduling.catalog_index import CatalogIndex  # noqa: E402
from scheduling.policy import check  # noqa: E402
from scheduling.request import Patient  # noqa: E402
from scheduling.templates import type_label  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")
OUT = ROOT / "eval" / "sim_calls"
SEED = 20261004
CALLER_MODEL = "gpt-4o-mini"
MAX_TURNS = 22
CATALOGS = {
    "sf": ("clinic-scheduler.json", "backend/data/catalog.json"),
    "national": ("national-scheduler.json", "backend/data/national/catalog.json"),
}
ARCHETYPES = [
    "an 80-year-old who rambles and tells small stories before getting to the point",
    "a hard-of-hearing elder who often says 'what?' and needs things repeated",
    "a Spanish speaker with limited English: short sentences, a few wrong words",
    "a busy parent, distracted by kids in the background, answers in fragments",
    "an anxious person who over-explains symptoms and apologises a lot",
    "a terse young adult who answers in as few words as possible",
    "a forgetful person who cannot remember the doctor's name and describes them instead",
    "a chatty person who goes off topic and has to be brought back",
    "a person who changes their mind about the day or time halfway through",
    "an impatient person who gets short with questions",
]
DOCTOR_MODES = ["full name", "last name only", "a description (no name)", "no doctor in mind"]
PLACE_MODES = ["clinic or neighborhood name", "street", "only the city or area", "a landmark nearby"]
VISIT_MODES = ["a concrete everyday reason or symptom", "an everyday description that names the body part or the need but not the medical term", "the visit named casually"]


# ---------------------------------------------------------------- targets and personas


GENERIC = re.compile(r"follow-up|new patient|consultation", re.IGNORECASE)


def pick_targets(n: int, rng: random.Random, generic: bool = True) -> list[dict]:
    """n targets from the catalogs: bookable rows for a patient the rules allow, plus ~10% the agent must not book."""
    indexes = {c: CatalogIndex.load(ROOT / path) for c, (_, path) in CATALOGS.items()}
    plan = [("sf", "normal")] * round(n * 0.5) + [("national", "normal")] * round(n * 0.4)
    plan += [("sf", "no_new"), ("national", "no_referral")][: n - len(plan)]
    rng.shuffle(plan)
    targets = []
    for i, (catalog, kind) in enumerate(plan[:n]):
        index = indexes[catalog]
        rows = list(index.bookable)
        for _ in range(5000):
            row = rng.choice(rows)
            t, p = row.type, row.provider
            if not generic and GENERIC.search(t.name) and kind == "normal":
                continue
            is_new = rng.random() < 0.4
            if kind == "no_new":
                if p.accepting_new_patients or not t.new_patients_allowed or t.requires_referral:
                    continue
                is_new, has_referral = True, False
            elif kind == "no_referral":
                if not t.requires_referral:
                    continue
                is_new, has_referral = False, False
            else:
                has_referral = t.requires_referral or rng.random() < 0.3
                if check(row, Patient(is_new=is_new, has_referral=has_referral)):
                    continue
            break
        targets.append({
            "id": f"{catalog}-{i:02d}", "catalog": catalog, "kind": kind,
            "is_new": is_new, "has_referral": has_referral,
            "visit": type_label(t), "visit_full_name": t.name, "specialty": t.specialty,
            "provider": p.name, "provider_accepts_new": p.accepting_new_patients,
            "location": row.location.short_name, "location_address": row.location.address,
            "location_city": row.location.city, "metro": row.location.metro_id,
            "requires_referral": t.requires_referral,
            "doctor_mode": rng.choice(DOCTOR_MODES), "place_mode": rng.choice(PLACE_MODES),
            "visit_mode": rng.choice(VISIT_MODES), "archetype": ARCHETYPES[i % len(ARCHETYPES)],
        })
    return targets


PERSONA_PROMPT = """Write a realistic phone caller for a test of a clinic scheduling assistant. Return JSON only.

The caller is {archetype}.
The appointment they want (hidden truth; the caller does NOT know these exact catalog words):
- reason for the visit: {visit} ({specialty}). They talk about it as: {visit_mode}. Never use the catalog name "{visit_full_name}" unless it is a plain everyday phrase.
- doctor: {provider}. They refer to the doctor as: {doctor_mode}. For "a description" give a vague memory (gender, a trait, "the one my neighbour saw"), not the name.
- place: {location}, {location_address}, {location_city}. They refer to the place as: {place_mode}.
- patient status: {status}. The caller is an adult talking about their own visit, unless the visit is a child's (well-child, infant, pediatric).
Real callers are imperfect: they leave things out, say them in the wrong order, hedge, and use everyday words. But every caller knows concretely what they came for: a symptom, a body part, a need such as a shot, a form, a test. Never write the words "vague" or "thing" as the whole reason, and never make the reason unanswerable. For "a description" of the doctor, give gender and at least one other concrete trait (where they work, who recommended them, something they said).

JSON keys: name, age, background (1-2 sentences), speaking_style, visit_in_their_words, doctor_in_their_words (or "" if none in mind), place_in_their_words, time_preference_in_their_words, status_in_their_words (do they say they are new, a returning patient, have a referral, or not know), forgets (what they do NOT remember or will not volunteer)."""

CALLER_SYSTEM = """You are role-playing a person on the phone with a clinic's automated scheduling assistant. Stay in character. You are NOT an assistant: you are a patient.

Who you are: {name}, {age}. {background}
How you speak: {speaking_style}
What you want: {visit_in_their_words}
Doctor: {doctor_in_their_words}
Place: {place_in_their_words}
When: {time_preference_in_their_words}
Your status: {status_in_their_words}
What you do not remember or will not volunteer: {forgets}

Rules:
- Say one short spoken turn at a time (1-2 sentences, no stage directions, no quotes). Do not tell your whole story at once: answer what you are asked, as a real caller would.
- You know your own situation precisely: when asked to choose between two options, pick the one closest to what you want, even without the medical word. Never say you do not know why you called.
- Use only the facts above. If asked something you do not know, say so in character. Do not invent exact clinic names, street numbers or medical terms.
- Accept an appointment only if the doctor, place and kind of visit match what you wanted. If you are offered something else, say that is not what you wanted, or ask. If it matches, choose a time that fits what you said you wanted, and say yes when the agent reads the booking back.
- After the agent confirms your booking, or tells you it cannot help or will transfer you, say goodbye. If you have been asked the same thing three times or feel you are getting nowhere, you may give up.
- When your call is over, answer with exactly: [HANGUP]"""


async def write_personas(targets: list[dict]) -> list[dict]:
    client = AsyncOpenAI()

    async def one(t: dict) -> dict:
        status = ("a new patient" if t["is_new"] else "a returning patient") + (
            ", with a referral" if t["has_referral"] else ", no referral")
        prompt = PERSONA_PROMPT.format(status=status, **t)
        for _ in range(3):
            r = await client.chat.completions.create(
                model=CALLER_MODEL, temperature=0.9, response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}])
            persona = json.loads(r.choices[0].message.content)
            keys = ("name", "age", "background", "speaking_style", "visit_in_their_words", "doctor_in_their_words",
                    "place_in_their_words", "time_preference_in_their_words", "status_in_their_words", "forgets")
            if all(k in persona for k in keys):
                return {**t, "persona": {k: str(persona[k]) for k in keys}}
        raise RuntimeError(f"no persona for {t['id']}")

    return await asyncio.gather(*(one(t) for t in targets))


# ---------------------------------------------------------------- the agent, text only


class SimWorker:
    def __init__(self, heard: list[str]):
        self.heard = heard

    async def queue_frame(self, frame) -> None:
        if isinstance(frame, TTSSpeakFrame):
            self.heard.append(frame.text)


class SimFM:
    """The slice of FlowManager the builder and handlers use, keeping the conversation the way Flows does:
    a node with context_strategy reset starts a fresh context of the persona and the node's task."""

    def __init__(self):
        self.state: dict = {}
        self.heard: list[str] = []
        self.worker = SimWorker(self.heard)
        self.node: dict | None = None
        self.messages: list[dict] = []

    def get_current_context(self) -> list[dict]:
        return self.messages

    async def initialize(self, node: dict) -> None:
        self.enter(node)

    def enter(self, node: dict) -> None:
        self.node = FlowManager._render_node(self, node["name"], node)
        strategy = node.get("context_strategy")
        persona = {"role": "system", "content": self.node.get("role_message") or ""}
        if (strategy and strategy.strategy.value == "reset") or not self.messages:
            self.messages = [persona]
        self.messages += [{"role": "system", "content": m["content"]} for m in self.node["task_messages"]]

    def tools(self) -> list[dict]:
        out = []
        for f in self.node["functions"]:
            s = f.to_function_schema()
            out.append({"type": "function", "function": {
                "name": s.name, "description": s.description,
                "parameters": {"type": "object", "properties": s.properties, "required": s.required}}})
        return out

    def handler(self, name: str):
        for f in self.node["functions"]:
            if f.name == name:
                return f.handler
        return None


class AgentSide:
    def __init__(self, target: dict, mode: str, client: AsyncOpenAI):
        file, _ = CATALOGS[target["catalog"]]
        data = json.loads((ROOT / "backend" / "agents" / file).read_text(encoding="utf-8"))
        data["resolver"] = {**data.get("resolver", {}), "chooser": mode}
        self.config = AgentConfig.from_dict(data)
        self.client = client
        self.fm = SimFM()
        self.events: list[dict] = []
        self.forced: str | None = None
        self.tool_calls: list[dict] = []
        self.prompt_tokens = 0
        self.requests = 0
        self.builder = AgentBuilder(self.config, on_event=self._on_event, force_tool=self._force)

    async def _on_event(self, event: dict) -> None:
        self.events.append(event)

    def _force(self, name: str) -> None:
        self.forced = name

    @property
    def over(self) -> bool:
        return bool(self.fm.node and self.fm.node.get("post_actions"))

    async def start(self) -> list[str]:
        await self.builder.start(self.fm)
        return await self.step()

    async def step(self) -> list[str]:
        """Everything the agent says until it waits for the caller."""
        spoken: list[str] = []
        for _ in range(8):
            tools = self.fm.tools()
            kwargs = {"tools": tools, "parallel_tool_calls": False} if tools else {}
            if self.forced and any(t["function"]["name"] == self.forced for t in tools):
                kwargs["tool_choice"] = {"type": "function", "function": {"name": self.forced}}
            self.forced = None
            resp = await self.client.chat.completions.create(
                model=self.config.model, messages=self.fm.messages, **kwargs)
            self.requests += 1
            self.prompt_tokens += resp.usage.prompt_tokens
            msg = resp.choices[0].message
            if not msg.tool_calls:
                if msg.content:
                    spoken.append(msg.content)
                    self.fm.messages.append({"role": "assistant", "content": msg.content})
                return spoken
            call = msg.tool_calls[0]
            args = json.loads(call.function.arguments or "{}")
            self.tool_calls.append({"name": call.function.name, "args": args})
            self.fm.messages.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": call.id, "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments}}]})
            handler = self.fm.handler(call.function.name)
            if handler is None:
                result, nxt = {"status": "error", "error": f"unknown function {call.function.name}"}, None
            else:
                result, nxt = await handler(args, self.fm)
            self.fm.messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": json.dumps(result, ensure_ascii=False)})
            while self.fm.heard:
                said = self.fm.heard.pop(0)
                spoken.append(said)
                self.fm.messages.append({"role": "assistant", "content": said})
            if nxt is NO_RESPONSE:
                return spoken
            if nxt:
                self.fm.enter(nxt)
                if nxt.get("respond_immediately") is False:
                    return spoken
        return spoken


# ---------------------------------------------------------------- the caller, and speech noise


def _noise_words(index: CatalogIndex) -> set[str]:
    words = {p.last_name.lower() for p in index.providers.values()}
    words |= {w.lower() for loc in index.locations.values() for w in re.findall(r"[A-Za-z]{5,}", loc.short_name)}
    return words


def asr_noise(text: str, words: set[str], rng: random.Random, p: float = 0.5) -> str:
    """A crude speech-recognition error: lowercased, unpunctuated, and a vowel of a name changed."""
    def garble(m: re.Match) -> str:
        w = m.group(0)
        if w.lower() in words and rng.random() < p:
            i = [k for k, c in enumerate(w) if c.lower() in "aeiou"]
            if i:
                k = rng.choice(i)
                return w[:k] + rng.choice([v for v in "aeiou" if v != w[k].lower()]) + w[k + 1:]
        return w
    return re.sub(r"[.,!?]", "", re.sub(r"[A-Za-z]+", garble, text)).lower()


async def caller_line(client: AsyncOpenAI, system: str, dialog: list[tuple[str, str]]) -> str:
    messages = [{"role": "system", "content": system}]
    if not dialog:
        messages.append({"role": "user", "content": "(The line is ringing. You hear nothing yet. Wait for the agent.)"})
    for who, text in dialog:
        messages.append({"role": "user" if who == "agent" else "assistant", "content": text})
    r = await client.chat.completions.create(model=CALLER_MODEL, temperature=0.8, max_tokens=90, messages=messages)
    return (r.choices[0].message.content or "").strip().strip('"')


# ---------------------------------------------------------------- scoring


def score(t: dict, bookings: list[dict], index: CatalogIndex, handoff: bool, turns: int) -> tuple[str, str]:
    """(outcome, why). right | wrong | safe | stuck. The first booking is the one scored."""
    booked = bookings[0] if bookings else None
    if t["kind"] != "normal":
        if booked is None:
            return "right", "refused or handed over, as it had to"
        bad = booked["provider"] == t["provider"] if t["kind"] == "no_new" else booked["visit"] == t["visit"]
        return ("wrong", f"booked the forbidden {booked['visit']} with {booked['provider']}") if bad else (
            "right", f"offered an allowed alternative: {booked['visit']} with {booked['provider']}")
    if booked is None:
        return ("stuck", "no booking after the turn limit") if turns >= MAX_TURNS else (
            "safe", "no booking" + (", handed to staff" if handoff else ", caller left"))
    problems = []
    if booked["visit"] != t["visit"]:
        problems.append(f"visit {booked['visit']!r} != {t['visit']!r}")
    surnames = [p.last_name for p in index.providers.values()]
    named = t["doctor_mode"] == "full name" or (
        t["doctor_mode"] == "last name only" and surnames.count(t["provider"].split()[-1]) == 1)
    if named and booked["provider"] != t["provider"]:
        problems.append(f"doctor {booked['provider']!r} != {t['provider']!r}")
    locs = [loc for loc in index.locations.values() if loc.short_name == booked["location"]]
    if t["place_mode"] == "only the city or area":
        if not any(loc.metro_id == t["metro"] for loc in locs):
            problems.append(f"place {booked['location']!r} is outside {t['metro']}")
    elif booked["location"] != t["location"]:
        problems.append(f"place {booked['location']!r} != {t['location']!r}")
    if problems:
        return "wrong", "; ".join(problems)
    other = "" if not named and booked["provider"] == t["provider"] or named else f" (doctor not named by the caller: {booked['provider']})"
    return "right", "booked the target" + other


# ---------------------------------------------------------------- one call


async def run_call(t: dict, mode: str, client: AsyncOpenAI, sem: asyncio.Semaphore, noisy: bool, rng: random.Random):
    async with sem:
        index = CatalogIndex.load(ROOT / CATALOGS[t["catalog"]][1])
        words = _noise_words(index)
        side = AgentSide(t, mode, client)
        system = CALLER_SYSTEM.format(**t["persona"])
        dialog: list[tuple[str, str]] = []
        transcript: list[tuple[str, str]] = []
        turns = 0
        try:
            for line in await side.start():
                dialog.append(("agent", line))
                transcript.append(("agent", line))
            while turns < MAX_TURNS and not side.over:
                said = await caller_line(client, system, dialog)
                if "[HANGUP]" in said or not said:
                    break
                turns += 1
                heard = asr_noise(said, words, rng) if noisy else said
                dialog.append(("caller", said))
                transcript.append(("caller", said if not noisy else f"{said}   (agent heard: {heard})"))
                side.fm.messages.append({"role": "user", "content": heard})
                for line in await side.step():
                    dialog.append(("agent", line))
                    transcript.append(("agent", line))
            error = ""
        except Exception as e:  # a failed call is reported, never silently dropped
            error = f"{type(e).__name__}: {e}"
        bookings = side.fm.state.get("bookings", [])
        handoff = bool(side.fm.node and side.fm.node.get("name") == "handoff")
        outcome, why = score(t, bookings, index, handoff, turns) if not error else ("error", error)
        decisions = [e for e in side.events if e.get("type") == "resolver_decision"]
        return {
            "id": t["id"], "mode": mode, "noisy": noisy, "outcome": outcome, "why": why, "turns": turns,
            "bookings": bookings, "handoff": handoff,
            "model_used_turns": sum(1 for d in decisions if d.get("model")),
            "agent_requests": side.requests, "agent_prompt_tokens": side.prompt_tokens,
            "tool_calls": side.tool_calls, "transcript": transcript,
        }


# ---------------------------------------------------------------- commands


def load_targets(name: str) -> list[dict]:
    path = OUT / f"{name}_targets.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def cmd_make(args) -> None:
    path = OUT / f"{args.set}_targets.jsonl"
    if path.exists():
        sys.exit(f"{path} exists: targets are frozen once written. Delete it on purpose to regenerate.")
    rng = random.Random(SEED + args.seed)
    targets = asyncio.run(write_personas(pick_targets(args.n, rng, generic=args.seed == 0)))
    for t in targets:
        t["id"] = f"{args.set}-{t['id']}" if args.seed else t["id"]
    path.write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in targets) + "\n", encoding="utf-8")
    print(f"wrote {len(targets)} targets with personas to {path.relative_to(ROOT)}")


def cmd_run(args) -> None:
    targets = load_targets(args.set)
    rng = random.Random(SEED + 1)
    noisy = {t["id"]: rng.random() < 0.3 for t in targets}  # the same calls are noisy in every mode
    if args.only:
        targets = [t for t in targets if t["id"] in args.only]

    async def go():
        client, sem = AsyncOpenAI(), asyncio.Semaphore(args.parallel)
        return await asyncio.gather(*(
            run_call(t, args.mode, client, sem, noisy[t["id"]], random.Random(zlib.crc32(t["id"].encode())))
            for t in targets))

    results = asyncio.run(go())
    tag = f"{args.set}_{args.mode}"
    (OUT / f"{tag}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n", encoding="utf-8")
    by_id = {t["id"]: t for t in targets}
    lines = [f"# {tag}\n"]
    for r in sorted(results, key=lambda r: (r["outcome"] != "wrong", r["id"])):
        t = by_id[r["id"]]
        lines.append(f"\n## {r['id']}: {r['outcome'].upper()} ({r['why']})\n")
        lines.append(f"Target: {t['visit']} with {t['provider']} at {t['location']}; "
                     f"{'new' if t['is_new'] else 'returning'}, {'referral' if t['has_referral'] else 'no referral'}; "
                     f"kind {t['kind']}. Caller: {t['persona']['name']}, {t['archetype']}. Noisy speech: {r['noisy']}.\n")
        lines += [f"- **{who}**: {text}" for who, text in r["transcript"]]
    (OUT / f"{tag}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print_tally(results, tag)


def print_tally(results: list[dict], tag: str) -> None:
    n = len(results)
    tally = {k: sum(r["outcome"] == k for r in results) for k in ("right", "wrong", "safe", "stuck", "error")}
    reqs = sum(r["agent_requests"] for r in results)
    toks = sum(r["agent_prompt_tokens"] for r in results)
    print(f"{tag}: {n} calls  right {tally['right']}  wrong {tally['wrong']}  safe {tally['safe']}  "
          f"stuck {tally['stuck']}  error {tally['error']}  | mean turns {sum(r['turns'] for r in results) / n:.1f}  "
          f"agent requests/call {reqs / n:.1f}  prompt tokens/call {toks / n:,.0f}  "
          f"~${toks / n * 2.5 / 1e6 * 1.1:.3f}/call on gpt-4o")


def cmd_report(args) -> None:
    for path in sorted(OUT.glob(f"{args.set}_*.jsonl")):
        if path.name.endswith("_targets.jsonl"):
            continue
        print_tally([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()], path.stem)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("make", "run", "report"):
        p = sub.add_parser(name)
        p.add_argument("--set", default="pilot")
        if name == "make":
            p.add_argument("--n", type=int, default=20)
            p.add_argument("--seed", type=int, default=0, help="offset of the target seed; above 0 leaves out generic visit types")
        if name == "run":
            p.add_argument("--mode", default="jev", choices=["jev", "openai", "embed", "none"])
            p.add_argument("--parallel", type=int, default=4)
            p.add_argument("--only", nargs="*")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    logger.remove()
    {"make": cmd_make, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
