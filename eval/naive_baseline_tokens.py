"""Token cost of the naive "catalog in the prompt" agent vs ours, per LLM turn. Offline (tiktoken).

The persona/task prompt and tool schemas here are representative drafts of the schedule node.
Spoken conversation history is the same for both approaches and is left out of both sides. What
is NOT shared is our tool traffic: every update_request call and its result stay in the LLM
context, so our prompt grows each turn. That growth is measured per exchange over the eval cases
and added on our side at turn 1, 5 and 15 (15 = the design doc's calls-per-call estimate).
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "eval"))

from run_resolver_eval import run_case  # noqa: E402
from scheduling.catalog_index import CatalogIndex  # noqa: E402
from scheduling.resolver import plan_json  # noqa: E402

ENC = tiktoken.get_encoding("o200k_base")
LLM_CALLS_PER_CALL = 15  # estimate from the design doc, not measured
GPT4O_INPUT_PER_M = 2.50  # USD, list price used in the design doc (estimate)

PERSONA_TASK = """You are Sam, the scheduling assistant for Bayview Health's clinics in San Francisco.
Speak naturally and briefly; this is a phone call. Help the caller book an appointment or answer
questions about our locations, doctors and visit types.
Every time the caller tells you something about what they need (the reason for the visit, a doctor,
a location, whether they are a new patient, whether they have a referral, or when they want to
come in), call update_request with their own words. Do not guess catalog details; the tool decides
what is bookable and returns what to say. For questions about hours, addresses, doctors or whether
we offer something, call lookup. Never invent doctors, times or locations.
Current request: {{ summary }}"""


def update_request_schema(index: CatalogIndex, with_service_enum: bool) -> dict:
    props = {
        "service_phrase": {"type": "string", "description": "Reason for the visit in the caller's words"},
        "specialty_hint": {"type": "string", "enum": list(index.specialties)},
        "provider_phrase": {"type": "string", "description": "Doctor name as heard"},
        "location_phrase": {"type": "string", "description": "Clinic or neighborhood as heard"},
        "is_new": {"type": "boolean", "description": "True if never seen at our clinics"},
        "has_referral": {"type": "boolean"},
        "time_pref": {"type": "object", "properties": {
            "days": {"type": "array", "items": {"type": "string", "enum": [
                "monday", "tuesday", "wednesday", "thursday", "friday"]}},
            "part_of_day": {"type": "string", "enum": ["morning", "afternoon"]},
            "not_before": {"type": "string", "description": "ISO date"}}},
        "pick_offer": {"type": "integer", "enum": [1, 2, 3], "description": "Offered time the caller chose"},
        "clear": {"type": "array", "items": {"type": "string", "enum": ["service", "provider", "location", "time_pref"]}},
    }
    if with_service_enum:
        props["service_name"] = {"type": "string", "enum": sorted(t.name for t in index.types.values())}
    return {"type": "function", "function": {
        "name": "update_request", "description": "Record what the caller just said and get the next step.",
        "parameters": {"type": "object", "properties": props}}}


LOOKUP_SCHEMA = {"type": "function", "function": {
    "name": "lookup", "description": "Answer a caller question with up to 5 catalog facts.",
    "parameters": {"type": "object", "required": ["kind", "phrase"], "properties": {
        "kind": {"type": "string", "enum": ["location_info", "provider_info", "do_you_offer"]},
        "phrase": {"type": "string"}}}}}
CONFIRM_SCHEMA = {"type": "function", "function": {
    "name": "confirm_booking", "description": "Book the held time after the caller says yes.",
    "parameters": {"type": "object", "properties": {}}}}


def tok(obj) -> int:
    text = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"))
    return len(ENC.encode(text))


def exchange_tokens(args: dict, plan) -> int:
    """One update_request round trip as it sits in the history: the call and the speak-direct
    result (production default: no `say`, but the `spoken` echo the handler adds)."""
    call = {"name": "update_request", "arguments": json.dumps(args, separators=(",", ":"))}
    return tok(call) + tok({**plan.tool_result(speak_direct=True), "spoken": plan.say})


def main() -> None:
    raw = json.loads((ROOT / "backend" / "data" / "catalog.json").read_text(encoding="utf-8"))
    index = CatalogIndex.load(ROOT / "backend" / "data" / "catalog.json")

    summaries, results, exchanges = [], [], []
    for line in (ROOT / "eval" / "cases.jsonl").read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        for turn, t in zip(case["turns"], run_case(index, case, {})):
            if t["kind"] == "resolve":
                summaries.append(tok(t["plan"].summary))
                results.append(tok(plan_json(t["plan"])))
                exchanges.append(exchange_tokens(turn["update"], t["plan"]))

    persona = tok(PERSONA_TASK)
    catalog = tok(raw)
    naive = persona + catalog
    tools_lean = tok([update_request_schema(index, False), LOOKUP_SCHEMA, CONFIRM_SCHEMA])
    tools_enum = tok([update_request_schema(index, True), LOOKUP_SCHEMA, CONFIRM_SCHEMA])
    base = persona + max(summaries) + tools_lean
    per_exchange = statistics.mean(exchanges)

    def ours_at(turn: int) -> int:
        return round(base + turn * per_exchange)

    rows = [
        ("catalog alone (compact JSON)", catalog),
        ("persona + task prompt (draft)", persona),
        ("NAIVE per turn = persona + catalog", naive),
        ("", None),
        ("{{ summary }} (max over eval)", max(summaries)),
        ("tool schemas: update_request (no type enum) + lookup + confirm", tools_lean),
        ("tool schemas: with 82-name service_name enum (not shipped)", tools_enum),
        ("tool result with say (max over eval)", max(results)),
        ("tool exchange in history, call + result (mean over eval)", round(per_exchange)),
        ("tool exchange in history, call + result (max over eval)", max(exchanges)),
        ("OURS base = persona + summary + lean schemas", base),
        ("OURS at turn 1  (base + 1 exchange)", ours_at(1)),
        ("OURS at turn 5  (base + 5 exchanges)", ours_at(5)),
        ("OURS at turn 15 (base + 15 exchanges)", ours_at(15)),
    ]
    width = max(len(k) for k, _ in rows)
    print(f"Per-turn input tokens, spoken history excluded on both sides (o200k_base; measured over "
          f"{len(exchanges)} eval turns; turn 5/15 rows = base + n x mean exchange)")
    print("-" * (width + 12))
    for k, v in rows:
        print(f"{k:<{width}}  {v:>8,}" if v is not None else "")
    print("-" * (width + 12))
    for turn in (1, 15):
        print(f"naive / ours at turn {turn:>2}: {naive / ours_at(turn):.1f}x fewer prompt tokens per turn")
    naive_total = naive * LLM_CALLS_PER_CALL
    ours_total = sum(ours_at(n) for n in range(1, LLM_CALLS_PER_CALL + 1))
    for label, total in (("naive", naive_total), ("ours", ours_total)):
        print(f"estimate, {LLM_CALLS_PER_CALL} LLM calls/call: {label:<6} {total:>8,} tok  "
              f"~${total * GPT4O_INPUT_PER_M / 1e6:.3f} on gpt-4o input")


if __name__ == "__main__":
    main()
