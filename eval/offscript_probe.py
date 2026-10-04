"""Off-script probe: which tool does the conversation model call for a messy caller sentence?

Each probe is a state of a National call plus one sentence. The states are reached once with real
caller turns against gpt-4o (sim_calls.AgentSide: the shipped node prompts, tools and resolver,
chooser "none"), snapshotted (conversation messages and flow state), and every probe run starts
from a deep copy of its snapshot in a fresh AgentSide, so runs never see each other. Only the
first model request of the probe turn is graded: its tool call and arguments, or plain text.
A FAIL run that has not spoken yet gets up to two more requests so its reply can be shown.

    backend/.venv/Scripts/python eval/offscript_probe.py [--only ID[,ID]] [--out FILE] [--budget N] [--details]

Expected specs (written before the first run, from the node prompts and tool descriptions):
    text                no tool call, the model answers in its own words
    tool                that tool, any arguments
    tool{}              that tool with no meaningful argument
    tool{~}             no argument, or only string arguments made of the caller's own words
    tool(k=v, k~sub, k) argument k equals v (or contains v, for a list), contains sub, or is set
    OPEN                the design is silent; the call is recorded, not graded
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sim_calls as S  # noqa: E402

from agent_tools.context import shared_availability  # noqa: E402
from loguru import logger  # noqa: E402
from pipecat.flows import NO_RESPONSE, FlowManager  # noqa: E402

logger.remove()

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "offscript_probe_2026-10-04.txt"
MODE = "none"
RUNS = 2
MAX_REQUESTS = 120
FOLLOW_UP = 2
RESERVE = 12  # requests kept back from follow-ups for a rerun of a probe that errored (setup is redone)

# State -> (base state, caller line that reaches it, check on the reached flow state).
# Offers in the reached state: 1 Wed Oct 7 11am Dr. Helen Altman, 2 Thu Oct 8 8am Dr. Helen Altman,
# 3 Fri Oct 9 8am Dr. Sylvia Smith, all at Flushing. Confirm reads back offer 1 (today at 11).
SETUP = {
    "start": (None, None, lambda st, node: node == "greeting"),
    "q_city": ("start", "Hi, I need a dental cleaning.",
               lambda st, node: st.get("status") == "ask" and (st["req"].get("pending_ask") or {}).get("field") == "metro"),
    "q_visit": ("start", "Hi, I need to make an appointment, I'm in New York.",
                lambda st, node: st.get("status") == "ask" and not (st["req"].get("pending_ask") or {}).get("field") in ("metro", None)),
    "offers": ("q_city", "I'm in New York.", lambda st, node: st.get("status") == "offer"),
    "confirm": ("offers", "The first one, today at eleven.", lambda st, node: st.get("status") == "confirm"),
}

# id, state, situation, sentence, expected (alternatives), expected key argument, reason.
PROBES = [
    # ---- start: greeting node (lookup, start, transfer_to_staff)
    ("S1", "start", "rambling story",
     "Oh hi, yes, so my sister-in-law, she was telling me last week at the barbecue that I really ought to get my "
     "teeth looked at, it's been, gosh, two years? So I suppose I need a cleaning.",
     ["start(request~cleaning)"], "request~cleaning",
     "greeting: anything they might want goes to start with their words"),
    ("S2", "start", "only uh / hello",
     "Uh... hello? Is this, um, is this the clinic?",
     ["text"], "none",
     "greeting: small talk with nothing else waits for more"),
    ("S3", "start", "asking for a person",
     "Can I just talk to a real person please, I hate these machines.",
     ["transfer_to_staff"], "none",
     "greeting: talk to a person -> transfer_to_staff"),
    ("S4", "start", "invented doctor or clinic",
     "I need to see Doctor Pemberton-Hale at the Riverside Wellness Center.",
     ["start(request~Pemberton)"], "request~Pemberton",
     "greeting: start with their words, garbled or unknown names included; schedule sorts it out"),
    ("S5", "start", "parking / insurance / cost",
     "Before anything, do you take Blue Cross insurance?",
     ["lookup", "text"], "none",
     "greeting: a question about our clinics -> lookup; facts lack insurance, so 'I don't have that' is also fine"),
    ("S6", "start", "booking for someone else",
     "Hi, I'm calling for my mother, she's eighty-two, she needs her eyes checked, she lives in Chicago.",
     ["start(request~mother)"], "request~mother",
     "greeting: start with the caller's exact words; who the visit is for must survive"),
    ("S7", "start", "two requests in one",
     "Yeah I need a flu shot, and also, what time does your Brooklyn office close?",
     ["start(request~flu)"], "request~flu",
     "greeting: the moment they mention a need, call start right away; the hours question has no tool here then"),
    ("S8", "start", "interruption mid-sentence",
     "I need to book a, sorry, hang on, my dog, okay, a, the physio thing, for my knee.",
     ["start(request~knee)"], "request~knee",
     "greeting: start with their words, garbled parts included"),
    # ---- question: schedule node, "Which city are you in?" pending
    ("Q1", "q_city", "wrong fact then the fix",
     "I'm in Boston, no wait, sorry, I moved, I'm in New York now.",
     ["update_request(location_phrase~New York)"], "location_phrase~New York",
     "schedule: place words to update_request; the corrected city, not the first one"),
    ("Q2", "q_city", "what did you say / repeat",
     "Sorry, what did you say? The line cut out.",
     ["update_request{~}", "text"], "none",
     "schedule: nothing usable -> update_request with no arguments (re-asks); persona also allows repeating 'spoken'"),
    ("Q3", "q_city", "only uh / hello",
     "Uhhh...",
     ["update_request{~}"], "none",
     "schedule: every turn goes to a tool; nothing usable -> update_request with no arguments"),
    ("Q4", "q_city", "complaint",
     "Ugh, this is taking forever, I already told the last person all of this.",
     ["update_request{~}"], "none",
     "schedule: a complaint is not a request for a person; nothing usable -> update_request with no arguments"),
    ("Q5", "q_city", "rambling story",
     "Well, I live with my daughter now, since my husband passed, she has the place out in Queens, so I guess Queens, "
     "New York.",
     ["update_request(location_phrase~Queens)"], "location_phrase~Queens",
     "schedule: pass the place words as said"),
    ("Q6", "q_city", "invented doctor or clinic",
     "The one on Maple Street, the Lakeside Family Clinic, you know it?",
     ["update_request(location_phrase~Lakeside)", "update_request(location_phrase~Maple)", "lookup(kind=location_info)"],
     "location_phrase~Lakeside|Maple",
     "schedule: an answer to the city question is place words for update_request; 'you know it?' could be a lookup"),
    # ---- question: schedule node, "What's the visit for?" pending (caller said New York)
    ("Q7", "q_visit", "how long the visit takes",
     "How long does a cleaning usually take? I have to pick up my kids at three.",
     ["lookup(kind=do_you_offer)"], "kind=do_you_offer",
     "schedule: 'how long it takes' is named as a lookup question"),
    ("Q8", "q_visit", "booking for someone else",
     "It's not for me actually, it's for my husband, he's got this rash on his arm.",
     ["update_request(service_phrase~rash)"], "service_phrase~rash",
     "schedule: the reason goes to update_request; nothing in schedule takes who it is for (only book_another has it)"),
    ("Q9", "q_visit", "yes/no to the wrong thing",
     "Yes.",
     ["update_request{~}"], "none",
     "schedule: 'yes' answers nothing here; no arguments, and never an invented visit"),
    ("Q10", "q_visit", "wait, go back",
     "Wait, go back, I didn't say New York, I said Newark.",
     ["update_request(location_phrase~Newark)"], "location_phrase~Newark",
     "schedule: the corrected place words to update_request"),
    # ---- offers: three times open
    ("O1", "offers", "number said as a word",
     "The second one.",
     ["update_request(pick_offer=2)"], "pick_offer=2",
     "schedule: picking an offered time -> pick_offer"),
    ("O2", "offers", "number said as a word",
     "Uh, option three I guess.",
     ["update_request(pick_offer=3)"], "pick_offer=3",
     "schedule: picking an offered time -> pick_offer"),
    ("O3", "offers", "number said as a word",
     "The last one, the Friday.",
     ["update_request(pick_offer=3)"], "pick_offer=3",
     "schedule: 'the last one' of three is 3"),
    ("O4", "offers", "changing the visit type with offers open",
     "Actually, hmm, forget the cleaning, can I get a tooth looked at instead, it's been throbbing.",
     ["update_request(service_phrase~tooth)"], "service_phrase~tooth",
     "schedule: a new reason replaces the old one via service_phrase (not clear, not reject)"),
    ("O5", "offers", "changing the visit type with offers open",
     "Hmm, actually, while I have you, my back's been killing me, could I see somebody for my back instead of the "
     "cleaning?",
     ["update_request(service_phrase~back)"], "service_phrase~back",
     "schedule: a booking request asked as a question goes to update_request"),
    ("O6", "offers", "what did you say / repeat",
     "Can you repeat that? Which days were those?",
     ["update_request{~}", "text"], "none",
     "persona: repeat the options from 'spoken'; schedule: or update_request with no arguments"),
    ("O7", "offers", "correcting themselves",
     "Let's do Wednesday, no, sorry, I meant Thursday, the Thursday one.",
     ["update_request(pick_offer=2)", "update_request(time_pref.day=thursday)"], "pick_offer=2",
     "schedule: the corrected pick (Thursday is offer 2)"),
    ("O8", "offers", "complaint",
     "These are all way too early, I work nights. This is taking forever.",
     ["update_request(reject=time)", "update_request(time_pref)"], "reject=[time] or time_pref",
     "schedule: turning down the times -> reject time, or a new time preference"),
    ("O9", "offers", "asking for a person",
     "Look, can I just talk to someone at the front desk?",
     ["transfer_to_staff"], "none",
     "schedule: the caller wants a person -> transfer_to_staff"),
    ("O10", "offers", "parking / insurance / cost",
     "Is there parking at the Flushing one?",
     ["lookup(kind=location_info)"], "kind=location_info",
     "lookup description: parking goes to lookup, which says it does not have that information"),
    ("O11", "offers", "parking / insurance / cost",
     "How much is a cleaning going to cost me without insurance?",
     ["lookup"], "none",
     "schedule: an information question -> lookup; facts lack price, so it says it does not know"),
    ("O12", "offers", "invented doctor or clinic",
     "Do you have Doctor Ferraguzzi? She did my last cleaning.",
     ["update_request(provider_phrase~Ferraguzzi)", "lookup(kind=provider_info)"], "provider_phrase~Ferraguzzi",
     "schedule: a doctor named for the booking goes to update_request; 'do you have' could be a provider lookup"),
    ("O13", "offers", "interruption mid-sentence",
     "Okay, the Thursday at eight with, oh hold on, someone's at the door.",
     ["OPEN"], "OPEN",
     "design silent: a half-finished pick; pick_offer 2 and an empty update are both defensible"),
    ("O14", "offers", "two requests in one",
     "Thursday at eight works, and can you also book my daughter right after?",
     ["update_request(pick_offer=2)"], "pick_offer=2",
     "schedule: the pick goes to pick_offer; the daughter waits for book_another after the booking"),
    ("O15", "offers", "wrong fact then the fix",
     "I'm a new patient, no wait, I came in last year, so I'm returning. And Friday's fine.",
     ["update_request(is_new=false)"], "is_new=false",
     "schedule: the corrected status (pick_offer 3 for Friday also expected in the same call)"),
    # ---- confirm: "Okay, a dental cleaning with Dr. Helen Altman, today at 11 at Flushing. Shall I book it?"
    ("C1", "confirm", "yes/no to the wrong thing",
     "Yes, Friday's perfect.",
     ["update_request(pick_offer=3)", "update_request(time_pref.day=friday)"], "pick_offer=3",
     "the read-back was today at 11: a yes to Friday is a new pick, booking today would be wrong"),
    ("C2", "confirm", "rambling story",
     "Oh that's lovely, you know my last dentist retired and I've been putting this off since, well, since before "
     "Christmas, so yes, that's fine, go ahead.",
     ["confirm_booking"], "none",
     "schedule: yes to the read-back -> confirm_booking"),
    ("C3", "confirm", "booking for someone else",
     "Yes, and can you book the same thing for my wife after?",
     ["confirm_booking"], "none",
     "schedule: yes -> confirm_booking even if they ask for something else in the same breath"),
    ("C4", "confirm", "how long the visit takes",
     "How long will it take? I need to be at work by one.",
     ["lookup(kind=do_you_offer)"], "kind=do_you_offer",
     "schedule: 'how long it takes' is a lookup question; not a yes"),
    ("C5", "confirm", "correcting themselves",
     "Yes, actually no, I meant tomorrow, can we do tomorrow at eight instead?",
     ["update_request(pick_offer=2)", "update_request(time_pref.day=tomorrow)"], "pick_offer=2",
     "the corrected answer is no to today and a pick of tomorrow at 8 (offer 2)"),
    ("C6", "confirm", "wait, go back",
     "Wait, go back, what were the other times again?",
     ["update_request{~}", "text", "update_request(reject=time)"], "none",
     "persona: repeat options from 'spoken'; schedule: empty update; 'other times' also reads as reject time"),
    ("C7", "confirm", "complaint",
     "Fine, yes, whatever, just book it, this took forever.",
     ["confirm_booking"], "none",
     "schedule: yes to the read-back -> confirm_booking"),
]


# ---------------------------------------------------------------- matching expected specs

_SPEC = re.compile(r"^(?P<tool>[a-z_]+)(?P<empty>\{~?\})?(?:\((?P<conds>.*)\))?$")


def _value(args: dict, key: str):
    v = args
    for part in key.split("."):
        v = v.get(part) if isinstance(v, dict) else None
    return v


def _literal(text: str):
    low = text.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    return int(low) if low.isdigit() else text.strip()


def _meaningful(args: dict) -> dict:
    return {k: v for k, v in args.items() if v not in (None, "", [], {}, False) or k in ("is_new", "has_referral")}


def matches(spec: str, call: dict | None, sentence: str) -> bool:
    if spec == "text":
        return call is None
    if call is None:
        return False
    m = _SPEC.match(spec)
    if not m or m["tool"] != call["name"]:
        return False
    args = call["args"]
    if m["empty"] == "{}":
        return not _meaningful(args)
    if m["empty"] == "{~}":
        heard = set(re.findall(r"[a-z']+", sentence.lower()))
        return all(isinstance(v, str) and set(re.findall(r"[a-z']+", v.lower())) <= heard
                   for v in _meaningful(args).values())
    for cond in filter(None, (c.strip() for c in (m["conds"] or "").split(","))):
        if "~" in cond:
            key, sub = cond.split("~", 1)
            if sub.lower() not in str(_value(args, key) or "").lower():
                return False
        elif "=" in cond:
            key, want = cond.split("=", 1)
            got, want = _value(args, key), _literal(want)
            if isinstance(got, list):
                if want not in got:
                    return False
            elif isinstance(got, str) and isinstance(want, str):
                if got.lower() != want.lower():
                    return False
            elif got != want:
                return False
        elif _value(args, cond) in (None, "", [], {}):
            return False
    return True


def verdict(expect: list[str], calls: list[dict | None], sentence: str) -> str:
    if expect == ["OPEN"]:
        return "OPEN"
    ok = [any(matches(s, c, sentence) for s in expect) for c in calls]
    return "PASS" if all(ok) else f"FAIL ({sum(ok)}/{len(ok)} ok)"


def show(call: dict | None, said: list[str], width: int = 70) -> str:
    if call is None:
        return "text: " + (" ".join(said)[:width - 6] if said else "(silence)")
    args = json.dumps(call["args"], ensure_ascii=False, separators=(",", ":"))
    return f"{call['name']}{args}"[:width]


# ---------------------------------------------------------------- states and runs


class Budget:
    def __init__(self, limit: int):
        self.limit, self.used = limit, 0

    def take(self, n: int = 1) -> bool:
        if self.used + n > self.limit:
            return False
        self.used += n
        return True


async def fresh(snap: dict | None, client) -> S.AgentSide:
    # The booking ledger is one per process: without this, a run that books today at 11 makes the
    # same slot "just taken" for every later run of a confirm probe.
    shared_availability.cache_clear()
    side = S.AgentSide({"catalog": "national"}, MODE, client)
    await side.builder.start(side.fm)
    if snap:
        side.fm.state = copy.deepcopy(snap["state"])
        node = side.builder._make_node(side.builder._nodes_by_name[snap["node"]])
        side.fm.node = FlowManager._render_node(side.fm, snap["node"], node)
        side.fm.messages = copy.deepcopy(snap["messages"])
    return side


def snapshot(side: S.AgentSide) -> dict:
    return {"node": side.fm.node["name"], "state": copy.deepcopy(side.fm.state),
            "messages": copy.deepcopy(side.fm.messages)}


async def step(side: S.AgentSide, budget: Budget, cap: int) -> tuple[list[str], bool]:
    """AgentSide.step, at most `cap` model requests. Returns what was spoken and whether the agent waits."""
    spoken: list[str] = []
    for _ in range(cap):
        if not budget.take():
            raise RuntimeError(f"request budget of {budget.limit} reached")
        tools = side.fm.tools()
        kwargs = {"tools": tools, "parallel_tool_calls": False} if tools else {}
        if side.forced and any(t["function"]["name"] == side.forced for t in tools):
            kwargs["tool_choice"] = {"type": "function", "function": {"name": side.forced}}
        side.forced = None
        resp = await side.client.chat.completions.create(model=side.config.model, messages=side.fm.messages, **kwargs)
        side.requests += 1
        msg = resp.choices[0].message
        if not msg.tool_calls:
            if msg.content:
                spoken.append(msg.content)
                side.fm.messages.append({"role": "assistant", "content": msg.content})
            return spoken, True
        call = msg.tool_calls[0]
        args = json.loads(call.function.arguments or "{}")
        side.tool_calls.append({"name": call.function.name, "args": args})
        side.fm.messages.append({"role": "assistant", "content": None, "tool_calls": [{
            "id": call.id, "type": "function",
            "function": {"name": call.function.name, "arguments": call.function.arguments}}]})
        handler = side.fm.handler(call.function.name)
        if handler is None:
            result, nxt = {"status": "error", "error": f"unknown function {call.function.name}"}, None
        else:
            result, nxt = await handler(args, side.fm)
        side.fm.messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result, ensure_ascii=False)})
        while side.fm.heard:
            said = side.fm.heard.pop(0)
            spoken.append(said)
            side.fm.messages.append({"role": "assistant", "content": said})
        if nxt is NO_RESPONSE:
            return spoken, True
        if nxt:
            side.fm.enter(nxt)
            if nxt.get("respond_immediately") is False:
                return spoken, True
    return spoken, False


async def build_states(client, budget: Budget) -> tuple[dict, dict]:
    snaps, heard = {}, {}
    for name, (base, line, ok) in SETUP.items():
        if base is None:
            side = await fresh(None, client)
            said, _ = await step(side, budget, 3)
        else:
            side = await fresh(snaps[base], client)
            side.fm.messages.append({"role": "user", "content": line})
            said, _ = await step(side, budget, 4)
        if not ok(side.fm.state, side.fm.node["name"]):
            raise RuntimeError(f"setup {name}: not reached; node {side.fm.node['name']}, status "
                               f"{side.fm.state.get('status')}, said {said}, calls {side.tool_calls}")
        snaps[name], heard[name] = snapshot(side), " ".join(said)
    return snaps, heard


async def run_probe(probe: tuple, snap: dict, client, budget: Budget, sem: asyncio.Semaphore) -> dict:
    pid, state, situation, sentence, expect, key, reason = probe
    async with sem:
        side = await fresh(snap, client)
        side.fm.messages.append({"role": "user", "content": sentence})
        said, waits = await step(side, budget, 1)
        call = side.tool_calls[0] if side.tool_calls else None
        ok = expect == ["OPEN"] or any(matches(s, call, sentence) for s in expect)
        return {"call": call, "said": said, "all_calls": side.tool_calls, "followed": False,
                "pending": not ok and not said and not waits, "side": side}


async def follow_up(runs: list[dict], budget: Budget, reserve: int) -> None:
    """After every graded request: let FAIL runs that have not spoken finish their turn, for the report."""
    for r in runs:
        if r.pop("pending") and budget.limit - budget.used - reserve >= FOLLOW_UP:
            more, _ = await step(r["side"], budget, FOLLOW_UP)
            r["said"], r["followed"] = r["said"] + more, True
        st = r.pop("side").fm.state
        r["after"] = {"status": st.get("status"), "booked": [b["when"] + " with " + b["provider"] for b in st.get("bookings", [])],
                      "started_with": st.get("started_with"),
                      "service_heard": ((st.get("req") or {}).get("service") or {}).get("heard")}


def table(rows: list[list[str]], widths: list[int]) -> list[str]:
    out = []
    for i, r in enumerate(rows):
        out.append("  ".join(str(c)[:w].ljust(w) for c, w in zip(r, widths)).rstrip())
        if i == 0:
            out.append("  ".join("-" * w for w in widths))
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated probe ids")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--budget", type=int, default=MAX_REQUESTS, help="model requests this run may use")
    ap.add_argument("--details", action="store_true", help="details and end state for every row, not only FAIL rows")
    a = ap.parse_args()
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    probes = [p for p in PROBES if not only or p[0] in only]
    budget = Budget(a.budget)
    client = S.AsyncOpenAI()
    snaps, heard = await build_states(client, budget)
    setup_used = budget.used
    sem = asyncio.Semaphore(8)
    runs = await asyncio.gather(*(run_probe(p, snaps[p[1]], client, budget, sem) for p in probes for _ in range(RUNS)))
    await follow_up(runs, budget, RESERVE)

    lines = [f"Off-script probe, National agent, chooser {MODE!r}, {RUNS} runs per probe, gpt-4o (agent JSON model).",
             "Each run starts from a deep copy of its state's snapshot; only the first model request of the probe turn",
             "is graded. States as reached by the setup turns:"]
    lines += [f"  {n:8} agent last said: {heard[n]!r}" for n in SETUP]
    rows = [["id", "state", "situation", "expected", "run 1", "run 2", "verdict"]]
    fails, counts = [], {}
    for i, p in enumerate(probes):
        pid, state, situation, sentence, expect, key, reason = p
        rs = runs[i * RUNS:(i + 1) * RUNS]
        v = verdict(expect, [r["call"] for r in rs], sentence)
        rows.append([pid, state, situation, " | ".join(expect), *(show(r["call"], r["said"], 58) for r in rs), v])
        counts.setdefault(state, []).append(v.split()[0])
        if v.startswith("FAIL") or a.details:
            fails.append((p, rs))
    lines += ["", *table(rows, [4, 8, 24, 44, 58, 58, 14])]
    lines += ["", "Totals by state (PASS / FAIL / OPEN):"]
    for state, vs in counts.items():
        lines.append(f"  {state:8} {vs.count('PASS')} / {vs.count('FAIL')} / {vs.count('OPEN')}")
    allv = [v for vs in counts.values() for v in vs]
    lines.append(f"  {'all':8} {allv.count('PASS')} / {allv.count('FAIL')} / {allv.count('OPEN')}")
    lines += ["", ("Details" if a.details else "FAIL details")
              + " (sentence, expected, each run's calls and what the caller heard):"]
    for (pid, state, situation, sentence, expect, key, reason), rs in fails:
        lines += ["", f"[{pid}] {state} / {situation}", f"  caller:   {sentence}",
                  f"  expected: {' | '.join(expect)}   ({reason})"]
        for k, r in enumerate(rs, 1):
            calls = ", ".join(f"{c['name']}{json.dumps(c['args'], ensure_ascii=False)}" for c in r["all_calls"]) or "(no tool)"
            lines += [f"  run {k}: calls {calls}" + (" [+follow-up]" if r["followed"] else ""),
                      f"         heard: {' '.join(r['said']) or '(nothing yet)'}"]
            if a.details:
                lines.append(f"         after: {json.dumps(r['after'], ensure_ascii=False)}")
    open_rows = [(p, runs[i * RUNS:(i + 1) * RUNS]) for i, p in enumerate(probes) if p[4] == ["OPEN"]]
    if open_rows:
        lines += ["", "OPEN rows (recorded, not graded):"]
        for (pid, state, situation, sentence, *_), rs in open_rows:
            lines.append(f"  [{pid}] {sentence}")
            for k, r in enumerate(rs, 1):
                lines.append(f"    run {k}: {show(r['call'], r['said'], 200)} | heard: {' '.join(r['said']) or '(nothing yet)'}")
    lines += ["", f"Model requests: {budget.used} (setup {setup_used}, probes {budget.used - setup_used}); "
                  f"limit {budget.limit}."]
    text = "\n".join(lines) + "\n"
    print(text)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
