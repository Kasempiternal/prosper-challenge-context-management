"""Scheduling tools: the LLM's only way to touch the catalog.

Handlers are the only writers of flow_manager.state["req"] (a Request dict),
state["status"] (the last plan status, or "booked") and state["summary"] (the recap rendered
into {{ summary }} after a context reset). They never raise: bad arguments come back as
{"status": "error", ...} so the LLM can retry, and state is written only after the resolver
succeeded.

Consent: only update_request produces status "confirm", and only after the caller picked an
offer and it was read back. hold_slot (precondition offer_confirmed) and book_offer both require it,
and book_offer books exactly that read-back offer; the LLM cannot name a different one.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from functools import lru_cache

import tiktoken
from loguru import logger
from pipecat.flows import NO_RESPONSE, FlowManager, FlowsFunctionSchema
from pipecat.frames.frames import TTSSpeakFrame

from scheduling.availability import Slot as TimeSlot
from scheduling.lookup import KINDS, MAX_FACTS, lookup as catalog_lookup
from scheduling.policy import check
from scheduling.request import PARTS_OF_DAY, SLOT_NAMES, WEEKDAY_NAMES, Request, Update, merge
from scheduling.resolver import Offer, Plan, resolve
from scheduling.templates import spoken_when, type_label

from .context import ToolContext

FILLER = "One moment."
FILLER_AFTER_S = 0.3


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding("o200k_base")  # gpt-4o / gpt-4.1 tokenizer


def count_tokens(result: dict) -> int:
    return len(_encoding().encode(json.dumps(result, separators=(",", ":"))))


def _error(message: str) -> dict:
    return {"status": "error", "error": message}


def _request(flow_manager: FlowManager) -> Request:
    return Request.from_dict(flow_manager.state.get("req") or {})


# ---- update_request ------------------------------------------------------------------------

def _update_request_properties(ctx: ToolContext) -> dict:
    return {
        "service_phrase": {"type": "string",
                           "description": "What the caller wants to be seen for, in their own words "
                                          "(e.g. 'cardiology consultation', 'knee MRI', 'a checkup')."},
        "specialty_hint": {"type": "string", "enum": list(ctx.index.specialties),
                           "description": "Specialty, only if the caller named or clearly implied one."},
        "provider_phrase": {"type": "string",
                            "description": "The doctor as the caller said it (e.g. 'Dr. Chen', 'the heart doctor Chen')."},
        "location_phrase": {"type": "string", "description": "The clinic location as the caller said it."},
        "is_new": {"type": "boolean", "description": "True if the caller is a new patient, false if established."},
        "has_referral": {"type": "boolean", "description": "Whether the caller has a referral."},
        "time_pref": {
            "type": "object",
            "description": "When the caller wants to come in. Send {\"soonest\": true} for 'as soon as possible'.",
            "properties": {
                "soonest": {"type": "boolean"},
                "days": {"type": "array", "items": {"type": "string", "enum": list(WEEKDAY_NAMES)}},
                "part_of_day": {"type": "string", "enum": list(PARTS_OF_DAY)},
                "not_before": {"type": "string", "description": "ISO date (YYYY-MM-DD), earliest acceptable day."},
            },
        },
        "pick_offer": {"type": "integer", "enum": [1, 2, 3],
                       "description": "Number of the offered time, or of the suggested alternative, the caller chose."},
        "clear": {"type": "array", "items": {"type": "string", "enum": [*SLOT_NAMES, "time_pref"]},
                  "description": "Choices the caller withdrew without replacing (e.g. 'any doctor is fine')."},
    }


def _decision_event(ctx: ToolContext, plan: Plan, result: dict, jev_ms: float) -> dict:
    event = {"type": "resolver_decision", "status": plan.status, "say": plan.say,
             "summary": plan.summary, "notes": list(plan.notes), "valid_rows": plan.valid_rows}
    if "offers" in result:
        event["offers"] = result["offers"]
    if "ask" in result:
        event["candidates"] = result["ask"]
    if plan.refusal:
        event["reason"] = plan.refusal.code
    called = [v for v in ctx.disambiguator.verdicts if v.called]
    if called:
        event["jev"] = {"used": True, "p": called[-1].top[0][1] if called[-1].top else None,
                        "ms": round(jev_ms)}
    else:
        event["jev"] = {"used": False}
    event["tokens"] = {"result": count_tokens(result)}
    return event


async def _resolve_with_filler(ctx: ToolContext, req: Request, flow_manager: FlowManager) -> Plan:
    """Resolve off the event loop. If a model hook is still running after FILLER_AFTER_S (a cache
    hit returns in microseconds, so this is a network call), tell the caller to hold on."""
    ctx.jev_client.begin_turn()
    task = asyncio.ensure_future(asyncio.to_thread(resolve, ctx.index, req, ctx.availability,
                                                   ctx.disambiguator, ctx.disambiguator))
    done, _ = await asyncio.wait({task}, timeout=FILLER_AFTER_S)
    if not done and ctx.disambiguator.consulting:
        await flow_manager.worker.queue_frame(TTSSpeakFrame(text=FILLER))
    return await task


def _held_summary(ctx: ToolContext, offer: Offer, req: Request) -> str:
    """The confirm node's {{ summary }}: the one offer read back, never the other options."""
    flags = []
    if req.patient.is_new is not None:
        flags.append("new patient" if req.patient.is_new else "established patient")
    if req.patient.has_referral is not None:
        flags.append("has a referral" if req.patient.has_referral else "no referral")
    return (f"{type_label(ctx.index.types[offer.type_id])} with "
            f"{ctx.index.providers[offer.provider_id].name}, "
            f"{spoken_when(offer.start, ctx.availability.now)} at "
            f"{ctx.index.locations[offer.location_id].short_name}"
            + (f" ({', '.join(flags)})" if flags else "") + ".")


def offer_confirmed(state: dict) -> str | None:
    """Edge guard: the caller picked an offer and it was read back ("Shall I book it?")."""
    if state.get("status") == "confirm":
        return None
    return ("The caller has not been read back an appointment yet. Pass the time they pick to "
            "update_request (pick_offer); it reads the appointment back. Call this only after they "
            "say yes to that read-back.")


def update_request_tool(ctx: ToolContext) -> FlowsFunctionSchema:
    async def handler(args: dict, flow_manager: FlowManager):
        try:
            update = Update.from_args(args)
        except (ValueError, TypeError) as e:
            return _error(f"invalid arguments: {e}"), None

        req = merge(_request(flow_manager), update)
        ctx.disambiguator.verdicts.clear()
        started = time.perf_counter()
        try:
            if ctx.jev_client is not None:
                plan = await _resolve_with_filler(ctx, req, flow_manager)
            else:
                plan = resolve(ctx.index, req, ctx.availability, ctx.disambiguator, ctx.disambiguator)
        except (ValueError, TypeError, KeyError) as e:
            logger.exception("update_request: resolver failed")
            return _error(f"could not process that request: {e}"), None
        elapsed_ms = (time.perf_counter() - started) * 1000

        flow_manager.state["req"] = plan.req.to_dict()
        flow_manager.state["status"] = plan.status
        flow_manager.state["summary"] = (_held_summary(ctx, plan.confirm, plan.req)
                                         if plan.status == "confirm" and plan.confirm else plan.summary)

        # A handoff refusal is left to the LLM: it must take the handoff edge, not wait silently.
        speak = ctx.speak_direct and bool(plan.say) and not (plan.refusal and plan.refusal.code == "handoff")
        result = plan.tool_result(speak_direct=speak)
        if speak:
            result["spoken"] = plan.say
        if plan.refusal:
            result["reason"] = plan.refusal.code
        logger.info(f"update_request {args} -> {plan.status}: {plan.say!r} ({elapsed_ms:.0f} ms)")
        await ctx.emit(_decision_event(ctx, plan, result, elapsed_ms))

        if speak:
            await flow_manager.worker.queue_frame(TTSSpeakFrame(text=plan.say))
            return result, NO_RESPONSE
        return result, None

    return FlowsFunctionSchema(
        name="update_request",
        description=(
            "Call this every time the caller says anything about the appointment they want: the reason "
            "for the visit, a doctor, a location, new or returning patient, referral, timing, or which "
            "offered time or suggested alternative they pick (pick_offer). Pass their own words; send only what changed. The result says what "
            "happens next: 'say' is for you to speak; 'spoken' was already spoken to the caller."
        ),
        properties=_update_request_properties(ctx),
        required=[],
        handler=handler,
    )


# ---- lookup --------------------------------------------------------------------------------

def lookup_tool(ctx: ToolContext) -> FlowsFunctionSchema:
    async def handler(args: dict, flow_manager: FlowManager):
        kind, phrase = args.get("kind"), args.get("phrase")
        if kind not in KINDS:
            return _error(f"kind must be one of {list(KINDS)}"), None
        if not isinstance(phrase, str) or not phrase.strip():
            return _error("phrase is required"), None
        facts = catalog_lookup(ctx.index, kind, phrase.strip())[:MAX_FACTS]
        return {"status": "ok", "facts": facts}, None

    return FlowsFunctionSchema(
        name="lookup",
        description=(
            "Answer a caller's question from the clinic catalog: a location's address, hours or phone "
            "(location_info), a doctor's specialty, sites, languages or whether they take new patients "
            "(provider_info), or whether we offer a kind of visit (do_you_offer). Answer only from the facts returned."
        ),
        properties={
            "kind": {"type": "string", "enum": list(KINDS)},
            "phrase": {"type": "string", "description": "The location, doctor or visit as the caller said it."},
        },
        required=["kind", "phrase"],
        handler=handler,
    )


# ---- book_offer ----------------------------------------------------------------------------

def book_offer_tool(ctx: ToolContext) -> FlowsFunctionSchema:
    async def handler(args: dict, flow_manager: FlowManager):
        state = flow_manager.state
        if state.get("status") == "booked" and state.get("last_booking"):
            return state["last_booking"], None
        req = _request(flow_manager)
        offer = next((o for o in req.offered if o.n == req.pick), None)
        if state.get("status") != "confirm" or offer is None:
            return _error("nothing confirmed to book: the caller must pick a time with update_request "
                          "and say yes to its read-back first"), None

        slot = TimeSlot(offer.type_id, offer.provider_id, offer.location_id,
                        datetime.fromisoformat(offer.start), offer.duration_min)
        row = ctx.index.row(offer.type_id, offer.provider_id, offer.location_id)
        issues = check(row, req.patient) if row else None
        if issues is None or issues:
            reasons = ", ".join(sorted({i.rule.value for i in issues or ()})) or "not a bookable combination"
            return {"status": "refused", "reason": reasons}, None

        hold = ctx.availability.hold(slot)
        if not hold.ok:
            return {"status": "taken", "reason": "taken",
                    "next": "call update_request to get new times"}, None

        result = {
            "status": "booked",
            "ref": hold.ref,
            "visit": type_label(ctx.index.types[offer.type_id]),
            "provider": ctx.index.providers[offer.provider_id].name,
            "location": ctx.index.locations[offer.location_id].short_name,
            "when": spoken_when(slot.start, ctx.availability.now),
        }
        state.setdefault("bookings", {})[slot.id] = hold.ref
        logger.info(f"book_offer {slot.id} -> {hold.ref}")
        # A further appointment starts from scratch; only who the caller is carries over.
        state["req"] = Request(patient=req.patient).to_dict()
        state["status"] = "booked"
        state["last_booking"] = result
        state["summary"] = (
            f"Booked: {result['visit']} with {result['provider']}, {result['when']} at "
            f"{result['location']}, confirmation {hold.ref}. The caller may want another appointment; ask what for.")
        return result, None

    return FlowsFunctionSchema(
        name="book_offer",
        description=(
            "Book the appointment update_request read back, once the caller said yes to it. Takes no "
            "arguments: it books exactly the read-back time. Re-checks booking rules and availability, and "
            "returns a confirmation reference to read to the caller. Safe to call again."
        ),
        properties={},
        required=[],
        handler=handler,
    )
