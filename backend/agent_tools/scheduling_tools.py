"""Scheduling tools and edge actions: the LLM's only way to touch the catalog and the bookings.

Handlers are the only writers of flow_manager.state["req"] (a Request dict),
state["status"] (the last plan status, or "booked"), state["bookings"] (one record per booking)
and state["summary"] (the recap rendered into {{ summary }} after a context reset). They never raise: bad arguments come back as
{"status": "error", ...} so the LLM can retry, and state is written only after the resolver
succeeded.

Consent: only update_request produces status "confirm", and only after the caller picked an
offer and it was read back. The confirm_booking edge (precondition offer_confirmed, action
book_confirmed) books exactly that read-back offer in code and speaks the confirmation itself, so
the LLM can neither name a different offer nor announce a booking that did not happen.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Awaitable, Callable

import tiktoken
from loguru import logger
from pipecat.flows import NO_RESPONSE, FlowError, FlowManager, FlowsFunctionSchema
from pipecat.frames.frames import TTSSpeakFrame

from scheduling.availability import Slot as TimeSlot
from scheduling.lookup import KINDS, MAX_FACTS, lookup as catalog_lookup
from scheduling.policy import check
from scheduling.request import PARTS_OF_DAY, SLOT_NAMES, WEEKDAY_NAMES, Request, Update, merge
from scheduling.resolver import Offer, Plan, resolve
from scheduling.templates import spoken_when, type_label
from scheduling.lexicon import says_unsure
from scheduling.names import GENDER_WORDS
from scheduling.text import tokens

from .context import ToolContext, model_call_event

FILLER = "One moment."
FILLER_AFTER_S = 0.3
DAY_WORDS = ("today", "tomorrow", *WEEKDAY_NAMES)


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding("o200k_base")  # gpt-4o / gpt-4.1 tokenizer


def count_tokens(result: dict) -> int:
    return len(_encoding().encode(json.dumps(result, separators=(",", ":"))))


@dataclass(frozen=True)
class EdgeOutcome:
    """What an edge action decided. proceed: take the edge's transition. respond: whether the LLM
    speaks next, on the target node or, when staying, on this result."""

    result: dict
    proceed: bool
    respond: bool


@dataclass(frozen=True)
class EdgeAction:
    """Code an edge runs before its transition. params: edge properties it reads, which the edge
    must declare as required. first_tool: when the action moves on, the target node's first LLM
    request must call this tool: the action left the caller's words where only that tool reads
    them, and a reset context gives the LLM nothing else to answer."""

    run: Callable[[ToolContext, dict, FlowManager], Awaitable[EdgeOutcome]]
    params: tuple[str, ...] = ()
    first_tool: str | None = None


def today_phrase(now: datetime) -> str:
    """'Wednesday, October 7, 2026': the {{ today }} the prompts show the LLM."""
    return f"{now:%A, %B} {now.day}, {now.year}"


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
        "location_phrase": {"type": "string", "description": "Where the caller wants to be seen, in their own words: clinic name, neighborhood, city, state or ZIP (e.g. \"I'm in Austin\", \"near Hyde Park\", \"78704\"). Never guess a place the caller did not say."},
        "is_new": {"type": "boolean", "description": "True if the caller is a new patient, false if established."},
        "has_referral": {"type": "boolean", "description": "Whether the caller has a referral."},
        "time_pref": {
            "type": "object",
            "description": "When the caller wants to come in. Send {\"soonest\": true} for 'as soon as possible'.",
            "properties": {
                "soonest": {"type": "boolean"},
                "day": {"type": "string", "enum": list(DAY_WORDS),
                        "description": "The one day the caller asked for, as they said it ('tomorrow', 'on Friday'). "
                                       "Prefer this to working out a date."},
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


def _decision_event(ctx: ToolContext, plan: Plan, result: dict, elapsed_ms: float,
                    kept: dict | None = None) -> dict:
    event = {"type": "resolver_decision", "status": plan.status, "say": plan.say,
             "summary": plan.summary, "notes": list(plan.notes), "valid_rows": plan.valid_rows}
    if kept:
        # What the conversation model sent in place of the caller's words, for the Decisions tab.
        event["kept_words"] = kept
    if "offers" in result:
        event["offers"] = result["offers"]
    if "ask" in result:
        event["candidates"] = result["ask"]
    if plan.refusal:
        event["reason"] = plan.refusal.code
    called = [v for _, v in plan.consults if v.called]
    if called:
        # The last verdict is the one that decided: a type check's, when the choice was checked.
        event["model"] = {"used": True, "provider": ctx.provider, "p": called[-1].p, "ms": round(elapsed_ms)}
    else:
        event["model"] = {"used": False}
    event["tokens"] = {"result": count_tokens(result)}
    event["ms"] = round(elapsed_ms)
    return event


async def _resolve_with_filler(ctx: ToolContext, req: Request, flow_manager: FlowManager) -> Plan:
    """Resolve off the event loop. If a model hook is still running after FILLER_AFTER_S (a cache
    hit returns in microseconds, so this is a network call), tell the caller to hold on."""
    ctx.model_client.begin_turn()
    task = asyncio.ensure_future(asyncio.to_thread(resolve, ctx.index, req, ctx.availability,
                                                   ctx.hooks, ctx.hooks, ctx.hooks))
    done, _ = await asyncio.wait({task}, timeout=FILLER_AFTER_S)
    if not done and ctx.hooks and ctx.hooks.consulting:
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


def _with_day_word(args: dict, today: date) -> dict:
    """time_pref.day ('tomorrow', 'friday') -> the days / not_before the request understands."""
    tp = args.get("time_pref")
    if not isinstance(tp, dict) or "day" not in tp:
        return args
    tp = dict(tp)
    word = tp.pop("day")
    if not isinstance(word, str) or word.lower() not in DAY_WORDS:
        raise ValueError(f"time_pref.day must be one of {list(DAY_WORDS)}, got {word!r}")
    word = word.lower()
    if word in ("today", "tomorrow"):
        day = today + timedelta(days=1 if word == "tomorrow" else 0)
        tp["not_before"], tp["days"] = day.isoformat(), [WEEKDAY_NAMES[day.weekday()]]
    else:
        tp["days"] = [word]
    return {**args, "time_pref": tp}


async def _resolve(ctx: ToolContext, req: Request, flow_manager: FlowManager) -> Plan:
    if ctx.model_client is None:
        return resolve(ctx.index, req, ctx.availability, ctx.hooks, ctx.hooks, ctx.hooks)
    first_call = len(ctx.model_client.calls)
    plan = await _resolve_with_filler(ctx, req, flow_manager)
    # One event per request: a hook may make several (a choice, then its check).
    for call in ctx.model_client.calls[first_call:]:
        await ctx.emit(model_call_event(ctx.provider, call.purpose or "model", call, call.latency_ms, call.p))
    return plan


async def _apply_plan(ctx: ToolContext, flow_manager: FlowManager, plan: Plan, elapsed_ms: float,
                      preface: str = "", kept: dict | None = None) -> tuple[dict, bool]:
    """Store the plan as the call's state, speak it when speak-direct applies, and report it.
    Returns the tool result and whether it was spoken."""
    flow_manager.state["req"] = plan.req.to_dict()
    flow_manager.state["status"] = plan.status
    flow_manager.state["summary"] = (_held_summary(ctx, plan.confirm, plan.req)
                                     if plan.status == "confirm" and plan.confirm else plan.summary)

    # A handoff refusal is left to the LLM: it must take the handoff edge, not wait silently.
    speak = ctx.speak_direct and bool(plan.say) and not (plan.refusal and plan.refusal.code == "handoff")
    result = plan.tool_result(speak_direct=speak)
    if speak:
        result["spoken"] = preface + plan.say
    elif "say" in result:
        result["say"] = preface + result["say"]
    if plan.refusal:
        result["reason"] = plan.refusal.code
    await ctx.emit(_decision_event(ctx, plan, result, elapsed_ms, kept))
    if speak:
        await flow_manager.worker.queue_frame(TTSSpeakFrame(text=result["spoken"]))
    return result, speak


# Asks a doctor or place answers, by the update field that carries the answer.
_ANSWER_FIELD = {"provider": "provider_phrase", "location": "location_phrase", "metro": "location_phrase",
                 "service": "service_phrase"}
_FILLER_WORDS = frozenset({"dr", "doctor", "the", "a", "an", "in", "at", "of", "on", "one", "with", "near",
                           "please", "i", "m", "im", "its", "it", "s", "is", "that", "and"})


def caller_turn(messages: list[dict]) -> str:
    """The caller's words since the assistant last spoke: the user messages after the last
    assistant text, skipping the tool calls and results the current reply is made of."""
    said: list[str] = []
    for m in reversed(messages):
        role, content = m.get("role"), m.get("content")
        if role == "user":
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            said.append(content if isinstance(content, str) else "")
        elif role == "assistant" and content and not m.get("tool_calls"):
            break
    return " ".join(reversed(said)).strip()


# Who the visit is for: "my 10-year-old", "my daughter". It decides between a child's visit and
# an adult's, and the conversation model drops it ("physical and a form signed by the doctor").
_WHO = re.compile(r"\b\d+[- ]?(?:year|month|week)s?[- ]?olds?\b|\b(?:son|daughter|kid|kids|child|children|baby|"
                  r"toddler|infant|newborn|teen|teenager|boy|girl)\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Patient status said in the same breath: the update carries it in is_new and has_referral.
_STATUS = re.compile(r"\b(?:i'?m|i am|we'?re|we are)\b[^.]*\b(?:patients?|referral)\b.*$", re.IGNORECASE)


def with_dropped_clauses(phrase: str, said: str) -> str:
    """The service phrase with the caller's sentences that change which visit fits and that the
    conversation model dropped: whom it is for, and a stated doubt with its other half ("I don't
    remember if it goes down my throat." "Or up from below"). A sentence that holds the whole
    phrase replaces it."""
    kept: list[str] = []
    for sentence in (_STATUS.sub("", x).strip() for x in _SENTENCE_END.split(said)):
        words = tokens(sentence)
        if not words:
            continue
        who = _WHO.search(sentence) and not _WHO.search(phrase)
        unsure = says_unsure(sentence) and not says_unsure(phrase)
        if who or unsure or (kept and words[0] == "or" and says_unsure(kept[-1])):
            kept.append(sentence)
    if not kept:
        return phrase
    if set(tokens(phrase)) <= set(tokens(kept[0])):
        return " ".join(kept)
    return " ".join([phrase.rstrip(".") + ".", *kept])


# Words that describe the doctor rather than name them: "the lady one", "he speaks Spanish".
_DESCRIBES_DOCTOR = GENDER_WORDS | {"speaks", "speak"}


def with_dropped_description(phrase: str, said: str) -> str:
    """The doctor phrase with the caller's sentences that describe the doctor and that the
    conversation model dropped: "Doctor Chen" from "...the lady one. Doctor Chen." The resolver
    reads the description (and still confirms a doctor picked by it by name)."""
    have = set(tokens(phrase))
    kept = [x.strip() for x in _SENTENCE_END.split(said)
            if (words := set(tokens(x))) & _DESCRIBES_DOCTOR and not words & _DESCRIBES_DOCTOR <= have]
    return " ".join([phrase.rstrip(".") + ".", *kept]) if kept else phrase


def grounded(args: dict, req: Request, said: str) -> tuple[dict, list[str]]:
    """What the caller answers must come from the caller, not from the conversation model: it
    turned "The lady one" into "Dr. Emily Chen" and "Washington" into "Washington, DC", skipping
    the confirmation the resolver asks for, and heard "Wednesday" in "day of checkup". While a
    doctor or place question is open, a phrase with a word the caller did not say is replaced by
    the caller's own words; a day the caller did not say is dropped. A one-option question ("Do
    you mean Dr. Emily Chen?") is a yes/no, where the model naming the option is the expected
    answer. Returns the arguments and what was replaced."""
    if not said:
        return args, []
    heard = set(tokens(said))
    replaced: list[str] = []
    pa = req.pending_ask
    key = _ANSWER_FIELD.get(pa.field) if pa and len(pa.options) != 1 else None
    phrase = args.get(key) if key else None
    if isinstance(phrase, str) and not all(w in heard or w in _FILLER_WORDS for w in tokens(phrase)):
        args, replaced = {**args, key: said}, [phrase]
    provider = args.get("provider_phrase")
    if key != "provider_phrase" and isinstance(provider, str) and provider.strip():
        fuller = with_dropped_description(provider, said)
        if fuller != provider:
            args, replaced = {**args, "provider_phrase": fuller}, [*replaced, provider]
    service = args.get("service_phrase")
    if key != "service_phrase" and isinstance(service, str) and service.strip():
        fuller = with_dropped_clauses(service, said)
        if fuller != service:
            args, replaced = {**args, "service_phrase": fuller}, [*replaced, service]
    tp = args.get("time_pref")
    day = tp.get("day") if isinstance(tp, dict) else None
    if isinstance(day, str) and not any(day.lower().startswith(w) for w in heard if len(w) >= 3):
        args, replaced = {**args, "time_pref": {k: v for k, v in tp.items() if k != "day"}}, [*replaced, day]
    return args, replaced


def update_request_tool(ctx: ToolContext) -> FlowsFunctionSchema:
    async def handler(args: dict, flow_manager: FlowManager):
        today = ctx.availability.now.date()
        try:
            said = caller_turn(flow_manager.get_current_context())
        except (FlowError, AttributeError):  # test doubles carry no conversation
            said = ""
        # The schedule node resets the context on entry: the first update after start has no caller
        # message to check against, only the words start was given.
        started = flow_manager.state.pop("started_with", "")
        said = said or started
        args, replaced = grounded(args, _request(flow_manager), said)
        if replaced:
            logger.info(f"update_request: the caller did not say {replaced!r}; they said {said!r}")
        try:
            update = Update.from_args(_with_day_word(args, today))
        except (ValueError, TypeError) as e:
            return _error(f"invalid arguments: {e}"), None
        not_before = update.time_pref.not_before if update.time_pref else None
        if not_before and date.fromisoformat(not_before) < today:
            return _error(f"that date has passed: time_pref.not_before {not_before} is before today, "
                          f"{today_phrase(ctx.availability.now)}. Pass the caller's day words in time_pref.day "
                          "instead."), None

        req = merge(_request(flow_manager), update)
        started = time.perf_counter()
        try:
            plan = await _resolve(ctx, req, flow_manager)
        except (ValueError, TypeError, KeyError) as e:
            logger.exception("update_request: resolver failed")
            return _error(f"could not process that request: {e}"), None
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(f"update_request {args} -> {plan.status}: {plan.say!r} ({elapsed_ms:.0f} ms)")
        kept = {"model": replaced, "caller": said} if replaced else None
        result, spoken = await _apply_plan(ctx, flow_manager, plan, elapsed_ms, kept=kept)
        return result, NO_RESPONSE if spoken else None

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
            "(provider_info), or a kind of visit: whether we offer it, how long it takes, whether it needs a referral "
            "and whether new patients can book it (do_you_offer, with the visit's name; for \"that\" or \"it\", the visit "
            "being booked). Only for a question that asks for "
            "information: a request to book something, even asked as \"do you have X in Y?\", is not a question for "
            "lookup. Say only what the facts state; never say whether something is or is not available in a place "
            "they do not name."
        ),
        properties={
            "kind": {"type": "string", "enum": list(KINDS)},
            "phrase": {"type": "string", "description": "The location, doctor or visit as the caller said it."},
        },
        required=["kind", "phrase"],
        handler=handler,
    )


# ---- edge actions ------------------------------------------------------------------------

def spoken_ref(ref: str) -> str:
    """'H-6034' -> 'H, 6, 0, 3, 4': one character at a time, so TTS spells it."""
    return ", ".join(c for c in ref if c.isalnum())


BOOKED_SAY = "You're all booked. Your confirmation is {ref}. Is there anything else I can help with?"
TAKEN_PREFACE = "I'm sorry, that time was just taken. "
REFUSED_PREFACE = "I'm sorry, I can't book that one after all. "


async def _offer_again(ctx: ToolContext, flow_manager: FlowManager, req: Request, preface: str) -> EdgeOutcome:
    """The read-back offer cannot be booked: tell the caller why and offer what is open now."""
    started = time.perf_counter()
    fresh = replace(req, offered=(), alternatives=(), pick=None, pending_ask=None)
    try:
        plan = await _resolve(ctx, fresh, flow_manager)
    except (ValueError, TypeError, KeyError) as e:
        logger.exception("confirm_booking: resolver failed")
        return EdgeOutcome(_error(f"could not book or find new times: {e}"), proceed=False, respond=True)
    result, spoken = await _apply_plan(ctx, flow_manager, plan, (time.perf_counter() - started) * 1000, preface)
    return EdgeOutcome({**result, "booked": False}, proceed=False, respond=not spoken)


async def book_confirmed(ctx: ToolContext, args: dict, flow_manager: FlowManager) -> EdgeOutcome:
    """Book exactly the offer update_request read back, whatever the LLM passed. Re-checks the
    booking rules and holds the slot; holding a slot this call already holds returns the same
    reference, so a retry converges on one booking."""
    state = flow_manager.state
    req = _request(flow_manager)
    offer = next((o for o in req.offered if o.n == req.pick), None)
    if state.get("status") != "confirm" or offer is None:
        return EdgeOutcome(_error("nothing confirmed to book: the caller must pick a time with update_request "
                                  "and say yes to its read-back first"), proceed=False, respond=True)

    slot = TimeSlot(offer.type_id, offer.provider_id, offer.location_id,
                    datetime.fromisoformat(offer.start), offer.duration_min)
    row = ctx.index.row(offer.type_id, offer.provider_id, offer.location_id)
    if row is None or check(row, req.patient):
        logger.info(f"confirm_booking {slot.id} refused by policy")
        return await _offer_again(ctx, flow_manager, req, REFUSED_PREFACE)
    hold = ctx.availability.hold(slot)
    if not hold.ok:
        logger.info(f"confirm_booking {slot.id} taken")
        return await _offer_again(ctx, flow_manager, req, TAKEN_PREFACE)

    booking = {
        "ref": hold.ref,
        "visit": type_label(ctx.index.types[offer.type_id]),
        "provider": ctx.index.providers[offer.provider_id].name,
        "location": ctx.index.locations[offer.location_id].short_name,
        "when": spoken_when(slot.start, ctx.availability.now),
    }
    logger.info(f"confirm_booking {slot.id} -> {hold.ref}")
    # Read by the UI and the post-call grader through node_entered.state.
    bookings = state.setdefault("bookings", [])
    if all(b["ref"] != hold.ref for b in bookings):
        bookings.append(booking)
    # A further appointment starts from scratch; only who the caller is carries over.
    state["req"] = Request(patient=req.patient).to_dict()
    state["status"] = "booked"
    state["summary"] = (f"Booked: {booking['visit']} with {booking['provider']}, {booking['when']} at "
                        f"{booking['location']}, confirmation {hold.ref}.")
    say = BOOKED_SAY.format(ref=spoken_ref(hold.ref))
    result = {"status": "booked", **booking}
    if ctx.speak_direct:
        result["spoken"] = say
        await flow_manager.worker.queue_frame(TTSSpeakFrame(text=say))
    else:
        result["say"] = say
    return EdgeOutcome(result, proceed=True, respond=not ctx.speak_direct)


async def new_request(ctx: ToolContext, args: dict, flow_manager: FlowManager) -> EdgeOutcome:
    """Every way into scheduling starts a fresh request from the caller's latest words: nothing of
    an earlier request (or booking) carries over except who the caller is."""
    words = args.get("request")
    if not isinstance(words, str) or not words.strip():
        return EdgeOutcome(_error("request is required: the caller's own words about what they want booked now"),
                           proceed=False, respond=True)
    state = flow_manager.state
    state["req"] = Request(patient=_request(flow_manager).patient).to_dict()
    state.pop("status", None)
    state["summary"] = words.strip()
    state["started_with"] = words.strip()
    return EdgeOutcome({"status": "success", "request": words.strip()}, proceed=True, respond=True)
