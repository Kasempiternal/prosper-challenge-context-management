import asyncio
import re
import time

import pytest
from pipecat.flows import NO_RESPONSE
from pipecat.frames.frames import TTSSpeakFrame

from agent_tools import build_tool, make_context, stt_keyterms
from agent_tools.context import BACKEND_DIR, ModelHooks, shared_availability
from agent_tools.keyterms import LAY_TERMS
from agent_tools.scheduling_tools import (caller_turn, grounded, REFUSED_PREFACE, TAKEN_PREFACE, EdgeOutcome, book_confirmed, new_request,
                                          spoken_ref)
from scheduling.decision import DECLINE, Verdict
from scheduling.request import OfferRef, PendingAsk, Request
from scheduling.resolver import NoDisambiguator


class FakeWorker:
    def __init__(self):
        self.frames = []

    async def queue_frame(self, frame):
        self.frames.append(frame)


class FakeFlowManager:
    def __init__(self, messages=None):
        self.state = {"summary": ""}
        self.worker = FakeWorker()
        self.messages = messages if messages is not None else []

    def get_current_context(self):
        return self.messages


@pytest.fixture
def events():
    return []


@pytest.fixture(autouse=True)
def fresh_bookings():
    shared_availability.cache_clear()
    yield
    shared_availability.cache_clear()


@pytest.fixture
def make_ctx(monkeypatch, events):
    monkeypatch.delenv("CMD_API_KEY", raising=False)

    async def on_event(event):
        events.append(event)

    def make(speak_direct=True):
        return make_context("data/catalog.json", speak_direct=speak_direct, chooser="jev",
                            timeout_ms=2500, on_event=on_event)

    return make


def call(ctx, fm, tool, args):
    return asyncio.run(build_tool(tool, ctx).handler(args, fm))


BEAT_1 = {"service_phrase": "cardiology consultation", "specialty_hint": "Cardiology",
          "provider_phrase": "Dr. Chen", "is_new": True, "has_referral": True, "time_pref": {"soonest": True}}


def test_new_patient_gets_only_emily_chen_spoken_direct(make_ctx, events):
    ctx, fm = make_ctx(), FakeFlowManager()
    result, next_node = call(ctx, fm, "update_request", BEAT_1)

    assert next_node is NO_RESPONSE
    assert result["status"] == "offer"
    assert "say" not in result
    assert result["spoken"] == fm.worker.frames[0].text
    assert len(result["offers"]) == 3
    assert all(o.endswith("Dr. Emily Chen") for o in result["offers"])
    assert [type(f) for f in fm.worker.frames] == [TTSSpeakFrame]
    spoken = fm.worker.frames[0].text
    assert spoken.startswith("For a cardiology consultation, Dr. Emily Chen has ")
    assert "David" not in spoken

    assert fm.state["req"]["patient"] == {"is_new": True, "has_referral": True}
    assert fm.state["summary"].startswith("new patient: yes; referral: yes; visit: Cardiology Consultation; "
                                          "doctor: Dr. Emily Chen; offered: 1) ")
    [event] = events
    assert event["type"] == "resolver_decision"
    assert event["status"] == "offer"
    assert event["say"] == spoken
    assert event["offers"] == result["offers"]
    assert event["model"] == {"used": False}
    assert event["notes"] == ["policy removed 4 rows: prov_000:new_patient_provider"]
    assert 0 < event["tokens"]["result"] < 160


def test_speak_direct_off_returns_say_for_the_llm(make_ctx):
    ctx, fm = make_ctx(speak_direct=False), FakeFlowManager()
    result, next_node = call(ctx, fm, "update_request", BEAT_1)
    assert next_node is None
    assert fm.worker.frames == []
    assert result["say"].startswith("For a cardiology consultation, Dr. Emily Chen has ")
    assert "spoken" not in result


def test_existing_patient_is_asked_which_chen(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    result, _ = call(ctx, fm, "update_request", {**BEAT_1, "is_new": False})
    assert result["ask"] == {"field": "provider", "options": ["Dr. David Chen", "Dr. Emily Chen"]}
    assert fm.worker.frames[0].text == "Do you mean Dr. David Chen or Dr. Emily Chen?"


def test_state_carries_the_request_across_calls(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    call(ctx, fm, "update_request", {**BEAT_1, "is_new": False})
    result, _ = call(ctx, fm, "update_request", {"provider_phrase": "Emily Chen"})
    assert result["status"] == "offer"
    assert result["known"]["doctor"] == "Dr. Emily Chen"


@pytest.mark.parametrize("args", [
    {"time_pref": {"part_of_day": "evening"}},
    {"time_pref": {"days": ["someday"]}},
    {"time_pref": {"not_before": "next week"}},
    {"time_pref": {"not_before": 20261012}},
    {"time_pref": "tomorrow"},
    {"time_pref": {"day": "someday"}},
    {"time_pref": {"day": 3}},
    {"pick_offer": 7},
    {"is_new": "yes"},
    {"doctor": "Dr. Chen"},
])
def test_malformed_args_are_tool_errors(make_ctx, events, args):
    ctx, fm = make_ctx(), FakeFlowManager()
    result, next_node = call(ctx, fm, "update_request", args)
    assert result["status"] == "error"
    assert result["error"].startswith("invalid arguments: ")
    assert next_node is None
    assert fm.state == {"summary": ""}
    assert fm.worker.frames == [] and events == []


def test_handoff_refusal_is_left_to_the_llm(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    for _ in range(3):
        result, next_node = call(ctx, fm, "update_request", {"provider_phrase": "Dr. Zzyzxqw"})
    assert result["reason"] == "handoff"
    assert next_node is None
    assert "say" in result
    assert [type(f) for f in fm.worker.frames] == [TTSSpeakFrame, TTSSpeakFrame]


def test_lookup_returns_at_most_five_facts(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    result, next_node = call(ctx, fm, "lookup", {"kind": "do_you_offer", "phrase": "eye exam"})
    assert (result, next_node) == ({"status": "ok", "facts": ["Eye Exam: not offered at our clinics."]}, None)
    result, _ = call(ctx, fm, "lookup", {"kind": "provider_info", "phrase": "Dr. Chen"})
    assert 2 <= len(result["facts"]) <= 5
    assert all("Chen" in f for f in result["facts"])


def test_lookup_bad_kind_is_a_tool_error(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    result, _ = call(ctx, fm, "lookup", {"kind": "prices", "phrase": "MRI"})
    assert result == {"status": "error", "error": "kind must be one of ['location_info', 'provider_info', 'do_you_offer']"}


def confirm_pick(ctx, fm, pick=1):
    call(ctx, fm, "update_request", BEAT_1)
    result, _ = call(ctx, fm, "update_request", {"pick_offer": pick})
    assert result["status"] == "confirm"
    return result


def book(ctx, fm, args=None) -> EdgeOutcome:
    return asyncio.run(book_confirmed(ctx, args or {}, fm))


def spoken(fm):
    return [f.text for f in fm.worker.frames if isinstance(f, TTSSpeakFrame)]


def test_booking_speaks_the_real_reference(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm)
    outcome = book(ctx, fm)

    ref = outcome.result["ref"]
    assert re.fullmatch(r"H-\d{4}", ref)
    assert (outcome.proceed, outcome.respond) == (True, False)
    assert outcome.result == {"status": "booked", "ref": ref, "visit": "cardiology consultation",
                              "provider": "Dr. Emily Chen", "location": "Downtown", "when": "tomorrow at 8",
                              "spoken": f"You're all booked. Your confirmation is {spoken_ref(ref)}. "
                                        "Is there anything else I can help with?"}
    assert spoken(fm)[-1] == outcome.result["spoken"]
    assert fm.state["bookings"] == [{"ref": ref, "visit": "cardiology consultation",
                                     "provider": "Dr. Emily Chen", "location": "Downtown", "when": "tomorrow at 8"}]
    assert fm.state["status"] == "booked"
    assert fm.state["summary"] == (f"Booked: cardiology consultation with Dr. Emily Chen, tomorrow at 8 at Downtown, "
                                   f"confirmation {ref}.")


def test_a_retried_booking_converges_on_one_booking(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm)
    before = {k: fm.state[k] for k in ("req", "status")}
    first = book(ctx, fm)
    fm.state.update(before)  # as if the call died after holding the slot, before the caller heard it
    again = book(ctx, fm)
    assert again.result == first.result
    assert [b["ref"] for b in fm.state["bookings"]] == [first.result["ref"]]


def test_spoken_ref_spells_one_character_at_a_time():
    assert spoken_ref("H-6034") == "H, 6, 0, 3, 4"


def test_booking_without_speak_direct_gives_the_llm_the_words(make_ctx):
    ctx, fm = make_ctx(speak_direct=False), FakeFlowManager()
    confirm_pick(ctx, fm)
    spoken_before = len(fm.worker.frames)
    outcome = book(ctx, fm)
    assert (outcome.proceed, outcome.respond) == (True, True)
    assert "spoken" not in outcome.result
    assert outcome.result["say"] == (f"You're all booked. Your confirmation is {spoken_ref(outcome.result['ref'])}. "
                                     "Is there anything else I can help with?")
    assert len(fm.worker.frames) == spoken_before


def test_stt_keyterms_are_capped_deduped_and_proper_nouns_first(make_ctx):
    terms = stt_keyterms(make_ctx().index)
    assert len(terms) == 50
    assert len({t.casefold() for t in terms}) == 50
    assert all(len(t) <= 20 for t in terms)
    assert terms[:3] == ["Chen", "Garcia", "Ramirez"]
    assert {"Downtown", "Mission Bay", "Eye Exam", "Ophthalmology", "Cardiology"} <= set(terms)
    assert "Comprehensive Eye Exam" not in terms  # 22 characters: over the realtime limit


def test_confirm_summary_names_only_the_held_offer(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm, pick=2)
    assert fm.state["status"] == "confirm"
    assert fm.state["summary"] == ("cardiology consultation with Dr. Emily Chen, Friday at 8 at Richmond "
                                   "(new patient, has a referral).")


def test_booking_needs_a_read_back(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    call(ctx, fm, "update_request", BEAT_1)
    outcome = book(ctx, fm, {"pick": 2})
    assert outcome == EdgeOutcome({"status": "error", "error": "nothing confirmed to book: the caller must pick a time "
                                                              "with update_request and say yes to its read-back first"},
                                  proceed=False, respond=True)
    assert "bookings" not in fm.state


def test_booking_books_the_read_back_offer_whatever_the_llm_sends(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm, pick=1)
    result = book(ctx, fm, {"pick": 2}).result
    assert (result["status"], result["when"], result["location"]) == ("booked", "tomorrow at 8", "Downtown")


def test_booking_resets_the_request_but_keeps_the_patient(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm)
    first = book(ctx, fm).result

    result, _ = call(ctx, fm, "update_request", {"service_phrase": "dermatology appointment",
                                                 "specialty_hint": "Dermatology"})
    assert result["status"] == "offer"
    assert result["known"] == {"new_patient": "yes", "referral": "yes", "visit": "Dermatology Consultation"}
    assert len({" ".join(o.split()[1:4]) for o in result["offers"]}) == 3  # "Thu Oct 8": one offer per day
    assert fm.state["req"]["provider"]["heard"] is None

    confirm_pick(ctx, fm, pick=2)
    second = book(ctx, fm).result
    assert [b["ref"] for b in fm.state["bookings"]] == [first["ref"], second["ref"]]


def test_booking_rechecks_policy_and_offers_again(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm, pick=2)
    fm.state["req"]["patient"]["has_referral"] = False
    outcome = book(ctx, fm)
    assert (outcome.proceed, outcome.result["booked"]) == (False, False)
    assert outcome.result["spoken"].startswith(REFUSED_PREFACE)
    assert spoken(fm)[-1] == outcome.result["spoken"]
    assert "bookings" not in fm.state
    assert fm.state["status"] != "confirm"


def test_a_slot_taken_by_another_call_is_re_offered_not_booked(make_ctx):
    (a, fm_a), (b, fm_b) = (make_ctx(), FakeFlowManager()), (make_ctx(), FakeFlowManager())
    confirm_pick(a, fm_a)
    confirm_pick(b, fm_b)
    booked = book(a, fm_a).result
    taken = book(b, fm_b)
    assert booked["status"] == "booked"
    assert (taken.proceed, taken.respond) == (False, False)
    assert taken.result["status"] == "offer" and taken.result["booked"] is False
    assert taken.result["spoken"].startswith(TAKEN_PREFACE + "For a cardiology consultation, Dr. Emily Chen has ")
    assert "tomorrow at 8 " not in taken.result["spoken"]
    assert spoken(fm_b)[-1] == taken.result["spoken"]
    assert "bookings" not in fm_b.state


READ_BACK = "Okay, a cardiology consultation with Dr. Emily Chen, tomorrow at 8 at Downtown. Shall I book it?"


@pytest.mark.parametrize("said", [
    "Yes.", "Yes please.", "Sure.", "That works.", "Yes, tomorrow at 8 is fine.", "Yes, Thursday works.",
    "Yes, 8 a.m. is great.", "Yes, at 8:00.", "Yes, Dr. Chen.", "Yes, Dr. Emily Chen at Downtown.",
    "Yes, and can you book the same thing for my wife after?", "Yes, my 2 kids will come along.", "I'm 82, so yes.",
    "Yes, Dr. Pemberton said I should come.", "Fine, yes, whatever, just book it, this took forever.",
])
def test_a_yes_that_names_nothing_else_books_the_read_back(make_ctx, said):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm)
    fm.messages = turn(said, asked=READ_BACK)
    result = book(ctx, fm).result
    assert (result["status"], result["when"], result["location"]) == ("booked", "tomorrow at 8", "Downtown")


@pytest.mark.parametrize("said,named", [
    ("Yes, Friday's perfect.", "friday"),
    ("Yes, today works.", "today"),
    ("Yes, at 11.", "11"),
    ("Yes, eight thirty.", "8:30"),
    ("Yes, 8 pm.", "8 pm"),
    ("Yes, with Dr. Garcia.", "Dr. Garcia"),
    ("Yes, Dr. David Chen.", "Dr. David Chen"),
    ("Yes, at Richmond.", "Richmond"),
])
def test_a_yes_that_names_something_else_is_not_booked(make_ctx, said, named):
    """Probe C1: "Yes, Friday's perfect." to a read-back for today at 11 booked today at 11."""
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm)
    spoken_before = len(fm.worker.frames)
    fm.messages = turn(said, asked=READ_BACK)
    outcome = book(ctx, fm)
    assert (outcome.proceed, outcome.respond) == (False, True)
    assert (outcome.result["status"], outcome.result["booked"]) == ("not_booked", False)
    assert f"the caller said {named!r}" in outcome.result["error"]
    assert "update_request" in outcome.result["error"]
    assert "bookings" not in fm.state and fm.state["status"] != "confirm"
    assert len(fm.worker.frames) == spoken_before


def test_a_new_choice_in_the_yes_is_read_back_before_it_is_booked(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm)
    fm.messages = turn("Yes, Friday's perfect.", asked=READ_BACK)
    book(ctx, fm)
    # A later yes no longer stands for the first read-back.
    fm.messages = turn("Yes.", asked="Did you want Friday?")
    assert book(ctx, fm).result["status"] == "error" and "bookings" not in fm.state
    result, _ = call(ctx, fm, "update_request", {"pick_offer": 2})
    assert result["status"] == "confirm"
    fm.messages = turn("Yes, Friday's perfect.", asked=spoken(fm)[-1])
    result = book(ctx, fm).result
    assert (result["status"], result["when"], result["location"]) == ("booked", "Friday at 8", "Richmond")


def new(ctx, fm, args):
    return asyncio.run(new_request(ctx, args, fm))


def test_another_appointment_starts_a_fresh_request_from_the_callers_words(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm)
    book(ctx, fm)
    outcome = new(ctx, fm, {"request": " Yes, I need a dental cleaning. In Maine for tomorrow. "})
    assert outcome == EdgeOutcome({"status": "success", "request": "Yes, I need a dental cleaning. In Maine for tomorrow."},
                                  proceed=True, respond=True)
    assert fm.state["summary"] == "Yes, I need a dental cleaning. In Maine for tomorrow."
    assert "status" not in fm.state
    req = fm.state["req"]
    assert req["patient"] == {"is_new": True, "has_referral": True}
    assert (req["service"]["heard"], req["provider"]["heard"], req["offered"], req["pick"]) == (None, None, (), None)
    assert len(fm.state["bookings"]) == 1


@pytest.mark.parametrize("args", [{}, {"request": "  "}, {"request": 7}])
def test_a_new_request_needs_the_callers_words(make_ctx, args):
    ctx, fm = make_ctx(), FakeFlowManager()
    outcome = new(ctx, fm, args)
    assert (outcome.proceed, outcome.result["status"]) == (False, "error")
    assert fm.state == {"summary": ""}


@pytest.mark.parametrize("day, days, not_before", [
    ("tomorrow", ["thursday"], "2026-10-08"),
    ("Today", ["wednesday"], "2026-10-07"),
    ("friday", ["friday"], None),
])
def test_day_words_become_the_requested_day(make_ctx, day, days, not_before):
    ctx, fm = make_ctx(), FakeFlowManager()
    result, _ = call(ctx, fm, "update_request", {**BEAT_1, "time_pref": {"day": day}})
    assert result["status"] == "offer"
    assert fm.state["req"]["time_pref"] == {"days": tuple(days), "part_of_day": None, "not_before": not_before}


def test_a_date_in_the_past_is_refused(make_ctx, events):
    ctx, fm = make_ctx(), FakeFlowManager()
    result, next_node = call(ctx, fm, "update_request", {**BEAT_1, "time_pref": {"not_before": "2023-10-18"}})
    assert next_node is None
    assert result == {"status": "error", "error": "that date has passed: time_pref.not_before 2023-10-18 is before "
                                                  "today, Wednesday, October 7, 2026. Pass the caller's day words in "
                                                  "time_pref.day instead."}
    assert fm.state == {"summary": ""} and events == []


class SlowTypes:
    def pick_type(self, phrase, hint, candidate_ids):
        time.sleep(0.5)
        return DECLINE


class FakeJevClient:
    provider = "jev"

    def __init__(self):
        self.turns = 0
        self.calls = []

    def begin_turn(self):
        self.turns += 1


class ConsultingTypes:
    """A JEV type hook that makes one (fake) request and declines with probabilities."""

    def __init__(self, client):
        self.client = client

    def pick_type(self, phrase, hint, candidate_ids):
        from scheduling.model_client import ModelCall
        self.client.calls.append(ModelCall("k", "live", 540.0, 900, purpose="type", p=0.79, usd=900 * 0.04 / 1_000_000))
        return Verdict(top=(("appt_002", 0.79), ("appt_003", 0.21)), called=True)


def _jev_ctx(make_ctx, types):
    ctx = make_ctx()
    ctx.model_client = FakeJevClient()
    ctx.hooks = ModelHooks(types, NoDisambiguator())
    return ctx


def test_filler_is_spoken_while_a_slow_model_call_runs(make_ctx):
    ctx, fm = _jev_ctx(make_ctx, SlowTypes()), FakeFlowManager()
    call(ctx, fm, "update_request", {"service_phrase": "my zorbly thing"})
    assert fm.worker.frames[0].text == "One moment."
    assert len(fm.worker.frames) == 2
    assert ctx.model_client.turns == 1


def test_no_filler_when_the_model_is_not_consulted(make_ctx):
    ctx, fm = _jev_ctx(make_ctx, SlowTypes()), FakeFlowManager()
    call(ctx, fm, "update_request", BEAT_1)
    assert [f.text for f in fm.worker.frames if f.text == "One moment."] == []


def test_refusal_alternatives_are_in_the_result_and_pickable(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    result, _ = call(ctx, fm, "update_request", {"is_new": False, "has_referral": True,
                                                 "service_phrase": "MRI knee", "location_phrase": "North Beach"})
    assert result["status"] == "refuse"
    assert result["alternatives"] == ["1 MRI - Knee Dr. Linda Ramirez Downtown", "2 MRI - Knee Dr. Hannah Nguyen Midtown"]
    assert "Dr. Hannah Nguyen at Midtown" in result["spoken"]
    result, _ = call(ctx, fm, "update_request", {"pick_offer": 2})
    assert result["status"] == "offer"
    assert all(o.endswith("Midtown Dr. Hannah Nguyen") for o in result["offers"])


def _multi_metro_index():
    """The SF catalog spread over three metros: Austin gets 4 sites, Boston 3, Ann Arbor 1."""
    import json
    from collections import Counter

    from scheduling.catalog_index import build_index

    data = BACKEND_DIR / "data"
    raw = json.loads((data / "catalog.json").read_text(encoding="utf-8"))
    raw_aliases = json.loads((data / "aliases.json").read_text(encoding="utf-8"))
    raw["metros"] = [
        {"id": "ann-arbor-mi", "name": "Ann Arbor", "state": "MI", "lat": 42.28, "lon": -83.74},
        {"id": "austin-tx", "name": "Austin", "state": "TX", "lat": 30.27, "lon": -97.74},
        {"id": "boston-ma", "name": "Boston", "state": "MA", "lat": 42.36, "lon": -71.06},
    ]
    for i, loc in enumerate(raw["locations"]):
        metro = raw["metros"][1 if i < 4 else 2 if i < 7 else 0]
        loc.update(metro_id=metro["id"], state=metro["state"], lat=metro["lat"], lon=metro["lon"])
    index = build_index(raw, raw_aliases)
    surnames = Counter(p.last_name for p in index.providers.values())
    return index, sorted(surnames, key=lambda s: (-surnames[s], s))


def test_stt_keyterms_put_metros_then_lay_terms_then_common_surnames_first_on_a_multi_metro_catalog():
    index, surnames_by_frequency = _multi_metro_index()
    terms = stt_keyterms(index)
    assert terms[:3] == ["Austin", "Boston", "Ann Arbor"]
    assert terms[3:13] == list(LAY_TERMS)
    assert terms[13:16] == surnames_by_frequency[:3]
    assert len(terms) == 50
    assert len({t.casefold() for t in terms}) == 50
    assert all(len(t) <= 20 for t in terms)
    assert "Mission Bay" not in terms  # site names give way to metros and surnames


SF_KEYTERMS = [
    "Chen", "Garcia", "Ramirez", "Smith", "Patel", "Hernandez", "Rodriguez", "Williams", "Nguyen", "Singh",
    "Lee", "Martinez", "Sato", "Kim", "Johnson", "Mission Bay", "Mission District", "North Beach",
    "North Gate", "Downtown", "Midtown", "Sunset", "Richmond", "Eye Exam", "Glaucoma Screening",
    "Contact Lens Fitting", "Cataract Evaluation", "Urology Consultation", "Allergy/Immunology",
    "Cardiology", "Dental", "Dermatology", "ENT", "Endocrinology", "Family Medicine", "Gastroenterology",
    "Internal Medicine", "Lab", "Neurology", "OB/GYN", "Ophthalmology", "Orthopedics", "Pediatrics",
    "Physical Therapy", "Psychiatry", "Pulmonology", "Radiology", "Urology", "New Patient Visit",
    "Annual Physical"
]


def test_sf_keyterms_are_unchanged(make_ctx):
    assert stt_keyterms(make_ctx().index) == SF_KEYTERMS


def test_national_keyterms_keep_the_lay_terms(monkeypatch):
    monkeypatch.delenv("CMD_API_KEY", raising=False)
    index = make_context("data/national/catalog.json", speak_direct=True).index
    terms = stt_keyterms(index)
    assert len(terms) == 50
    assert set(LAY_TERMS) <= set(terms)
    assert terms.index("sports injury") < 50 and "Austin" in terms



def test_each_model_request_is_reported_for_the_dev_view(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    ctx.model_client = FakeJevClient()
    ctx.hooks = ModelHooks(ConsultingTypes(ctx.model_client), NoDisambiguator())
    events = []

    async def on_event(event):
        events.append(event)

    ctx.on_event = on_event
    call(ctx, fm, "update_request", {"service_phrase": "my zorbly thing"})
    calls = [e for e in events if e["type"] == "model_call"]
    assert calls == [{"type": "model_call", "provider": "jev", "purpose": "type", "ms": 540, "input_tokens": 900,
                      "usd": 900 * 0.04 / 1_000_000, "ok": True, "source": "live", "p": 0.79}]
    decision = next(e for e in events if e["type"] == "resolver_decision")
    assert isinstance(decision["ms"], int)
    assert decision["model"] == {"used": True, "provider": "jev", "p": 0.79, "ms": decision["ms"]}


class CheckedTypes:
    """A type hook that chooses Annual Physical at 0.97, then checks it: 0.71 against the rival."""

    def pick_type(self, phrase, hint, candidate_ids):
        return Verdict(act="appt_002", top=(("appt_002", 0.97), ("appt_003", 0.03)), called=True)

    def check_type(self, phrase, hint, first, rival):
        return Verdict(act="appt_002", top=(("appt_002", 0.71), (rival, 0.04), ("either", 0.25)), called=True)


def test_the_dev_view_reports_the_check_and_its_final_p(make_ctx, events):
    ctx, fm = make_ctx(), FakeFlowManager()
    ctx.model_client = FakeJevClient()
    ctx.hooks = ModelHooks(CheckedTypes(), NoDisambiguator())
    call(ctx, fm, "update_request", {"is_new": False, "service_phrase": "my zorbly thing"})
    decision = next(e for e in events if e["type"] == "resolver_decision")
    assert decision["model"]["p"] == 0.71

    from scheduling.request import Request, Update, merge
    from scheduling.resolver import resolve
    plan = resolve(ctx.index, merge(Request(), Update.from_args({"is_new": False, "service_phrase": "my zorbly thing"})),
                   ctx.availability, CheckedTypes())
    assert [(purpose, v.p) for purpose, v in plan.consults] == [("type", 0.97), ("type check", 0.71)]


def test_a_gender_question_no_chooser_can_ask_is_no_model_use(make_ctx, events):
    ctx, fm = make_ctx(), FakeFlowManager()
    result, _ = call(ctx, fm, "update_request", {"is_new": False, "has_referral": True, "provider_phrase": "Dr. Chen, the woman",
                                                 "service_phrase": "cardiology consultation"})
    assert result["ask"]["field"] == "provider"
    assert next(e for e in events if e["type"] == "resolver_decision")["model"] == {"used": False}


def turn(*said, asked="Do you mean Dr. David Chen or Dr. Emily Chen?"):
    return [{"role": "assistant", "content": asked},
            *({"role": "user", "content": t} for t in said),
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}]


def test_caller_turn_is_what_was_said_since_the_last_question():
    assert caller_turn(turn("The lady one.")) == "The lady one."
    assert caller_turn(turn("In...", "DC.")) == "In... DC."
    assert caller_turn([]) == ""


@pytest.mark.parametrize("field,options,args,said,passed", [
    ("provider", ("p1", "p2"), {"provider_phrase": "Dr. Emily Chen"}, "The lady one.", "The lady one."),
    ("metro", ("sea", "dc"), {"location_phrase": "Washington, DC"}, "Washington.", "Washington."),
    ("provider", ("p1", "p2"), {"provider_phrase": "Dr. Emily Chen"}, "Doctor Emily Chen.", None),
    ("provider", ("p1", "p2"), {"provider_phrase": "The one who speaks Arabic"}, "The one who speaks Arabic.", None),
    ("metro", ("sea", "dc"), {"location_phrase": "Seattle, Washington"}, "Seattle, Washington.", None),
    ("provider", ("p1",), {"provider_phrase": "Dr. Emily Chen"}, "Yes.", None),
    ("service", ("t1", "t2"), {"service_phrase": "Upper Endoscopy (EGD)"}, "Upper endoscopy.", "Upper endoscopy."),
    ("service", ("t1", "t2"), {"service_phrase": "dental cleaning"}, "Dental cleaning.", None),
])
def test_an_answer_names_only_what_the_caller_said(field, options, args, said, passed):
    from scheduling.request import PendingAsk, Request
    out, replaced = grounded(args, Request(pending_ask=PendingAsk(field, options)), said)
    key = next(iter(args))
    assert out[key] == (passed or args[key])
    assert bool(replaced) == (passed is not None)


@pytest.mark.parametrize("said,day,kept", [
    ("Day of checkup.", "wednesday", False),
    ("Next Wed works.", "wednesday", True),
    ("Tomorrow, please.", "tomorrow", True),
])
def test_a_day_is_one_the_caller_said(said, day, kept):
    from scheduling.request import Request
    out, _ = grounded({"time_pref": {"day": day}}, Request(), said)
    assert ("day" in out["time_pref"]) == kept


def test_the_lady_one_is_not_rewritten_into_a_booking(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    call(ctx, fm, "update_request", {**BEAT_1, "is_new": False})
    fm.messages = turn("The lady one.")
    result, _ = call(ctx, fm, "update_request", {"provider_phrase": "Dr. Emily Chen"})
    assert result["status"] == "ask", result
    assert fm.state["req"]["provider"]["heard"] == "The lady one."


@pytest.mark.parametrize("phrase,said,sent", [
    ("needs a physical and a form signed by the doctor",
     "My 10-year-old needs a physical and a form signed by the doctor. We are returning patients and we have a referral.",
     "My 10-year-old needs a physical and a form signed by the doctor."),
    ("scope", "The GI doc wants a scope. I don't remember if it goes down my throat. Or up from below. I'm a returning patient.",
     "scope. I don't remember if it goes down my throat. Or up from below."),
    ("flu shot", "A flu shot in Washington. I'm a returning patient.", "flu shot"),
    ("sick visit", "I'm a person with a fever, for a sick visit.", "sick visit"),
    ("well-child visit for my daughter", "My daughter needs her well-child visit.", "well-child visit for my daughter"),
    ("eyes checked, in Chicago",
     "Hi, I'm calling for my mother, she's eighty-two, she needs her eyes checked, she lives in Chicago.",
     "Hi, I'm calling for my mother, she's eighty-two, she needs her eyes checked, she lives in Chicago."),
    ("rash on his arm", "It's not for me actually, it's for my husband, he's got this rash on his arm.",
     "It's not for me actually, it's for my husband, he's got this rash on his arm."),
    ("physical", "My dad needs a physical.", "My dad needs a physical."),
    ("checkup", "I'd like a checkup. It's for my elderly grandmother.", "checkup. It's for my elderly grandmother."),
    ("dental cleaning", "A dental cleaning for my wife's teeth.", "A dental cleaning for my wife's teeth."),
    # Someone who is not the patient: the caller is.
    ("eye exam with Dr. Chen", "My mother recommended Dr. Chen. I need an eye exam.", "eye exam with Dr. Chen"),
    ("flu shot", "My friend told me you do flu shots. I'd like one.", "flu shot"),
    ("eye check for mother", "I'm calling for my mother, she needs her eyes checked.", "eye check for mother"),
])
def test_the_visit_keeps_whom_it_is_for_and_a_stated_doubt(phrase, said, sent):
    from agent_tools.scheduling_tools import with_dropped_clauses
    assert with_dropped_clauses(phrase, said) == sent


def test_start_keeps_whom_the_visit_is_for():
    """Probe S6: start got "eyes checked, in Chicago", and the schedule node, its context reset, had
    nothing else to check the first update against."""
    said = "Hi, I'm calling for my mother, she's eighty-two, she needs her eyes checked, she lives in Chicago."
    fm = FakeFlowManager(turn(said, asked="Hi, what can I help you book today?"))
    outcome = asyncio.run(new_request(None, {"request": "eyes checked, in Chicago"}, fm))
    assert outcome.result == {"status": "success", "request": said}
    assert fm.state["started_with"] == fm.state["summary"] == said
    fm = FakeFlowManager(turn("My mother recommended Dr. Chen, I need a checkup.", asked="Hi!"))
    asyncio.run(new_request(None, {"request": "checkup with Dr. Chen"}, fm))
    assert fm.state["started_with"] == "checkup with Dr. Chen"


def test_an_answer_to_a_visit_question_is_the_callers():
    from scheduling.request import PendingAsk, Request
    req = Request(pending_ask=PendingAsk("service", ("appt_cleaning", "appt_exam")))
    out, replaced = grounded({"service_phrase": "dental exam"}, req, "Mm, day of checkup.")
    assert out["service_phrase"] == "Mm, day of checkup." and replaced == ["dental exam"]


def test_the_first_update_after_a_context_reset_is_checked_against_the_start_words(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    asyncio.run(new_request(ctx, {"request": "My 10-year-old needs a physical and a form signed by the doctor. "
                                             "We are returning patients and we have a referral."}, fm))
    call(ctx, fm, "update_request", {"service_phrase": "physical and form signed", "is_new": False})
    assert fm.state["req"]["service"]["heard"] == "My 10-year-old needs a physical and a form signed by the doctor."
    assert "started_with" not in fm.state


@pytest.mark.parametrize("phrase,said,sent", [
    ("Doctor Chen", "I need to see the heart doctor again. The lady one. Doctor. Chen. It has to be in the morning.",
     "Doctor Chen. The lady one."),
    ("Dr. Garcia", "The one who speaks Arabic. Dr. Garcia.", "Dr. Garcia. The one who speaks Arabic."),
    ("Dr. Chen, the lady one", "Dr. Chen, the lady one.", "Dr. Chen, the lady one"),
    ("Dr. Patel", "My wife says Dr. Patel is great.", "Dr. Patel"),
])
def test_the_doctor_keeps_how_the_caller_described_them(phrase, said, sent):
    from agent_tools.scheduling_tools import with_dropped_description
    assert with_dropped_description(phrase, said) == sent


def test_the_decision_event_shows_the_words_kept_over_the_models(make_ctx, events):
    ctx, fm = make_ctx(), FakeFlowManager()
    call(ctx, fm, "update_request", {**BEAT_1, "is_new": False})
    assert "kept_words" not in [e for e in events if e["type"] == "resolver_decision"][-1]
    fm.messages = turn("The lady one.")
    call(ctx, fm, "update_request", {"provider_phrase": "Dr. Emily Chen"})
    last = [e for e in events if e["type"] == "resolver_decision"][-1]
    assert last["kept_words"] == {"model": ["Dr. Emily Chen"], "caller": "The lady one."}


def test_a_yes_sent_as_a_pick_with_nothing_offered_answers_the_open_question():
    """Live call: "Yes." to "Do you mean Dr. Emily Chen?" arrived as pick_offer 1, twice."""
    from scheduling.request import PendingAsk, Request
    req = Request(pending_ask=PendingAsk("provider", ("prov_046",)))
    out, replaced = grounded({"pick_offer": 1}, req, "Yes.")
    assert out == {"provider_phrase": "Yes."} and replaced == ["pick_offer 1"]


def test_a_pick_with_offers_on_the_table_is_left_alone():
    from scheduling.request import OfferRef, PendingAsk, Request
    req = Request(offered=(OfferRef(1, "t", "p", "l", "2026-10-08T08:00:00", 30),),
                  pending_ask=PendingAsk("provider", ("prov_046",)))
    out, _ = grounded({"pick_offer": 1}, req, "The first one.")
    assert out == {"pick_offer": 1}


def test_a_run_on_request_adds_no_doctor_description():
    from agent_tools.scheduling_tools import with_dropped_description
    said = "health doctor again lady one dog Chin Chen morning bus"
    assert with_dropped_description("Dr. Chen", said) == "Dr. Chen"


def test_booking_for_someone_else_asks_their_details_again(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    fm.state["req"] = {"patient": {"is_new": False, "has_referral": True}}
    asyncio.run(new_request(ctx, {"request": "my husband Frank, a flu shot", "for_someone_else": True}, fm))
    assert fm.state["req"]["patient"] == {"is_new": None, "has_referral": None}
    fm2 = FakeFlowManager()
    fm2.state["req"] = {"patient": {"is_new": False, "has_referral": True}}
    asyncio.run(new_request(ctx, {"request": "a flu shot for me too"}, fm2))
    assert fm2.state["req"]["patient"] == {"is_new": False, "has_referral": True}


def test_clearing_the_doctor_for_a_named_doctor_keeps_the_name():
    """Improvised call: "Let's go with Dr. Kalem" arrived as clear provider; the name and the time were lost."""
    out, replaced = grounded({"clear": ["provider"]}, Request(), "Okay. Let's go with Dr. Kalem. Yeah, today.")
    assert out == {"provider_phrase": "Dr. Kalem"} and replaced == ["clear provider"]


def test_clearing_the_doctor_for_any_doctor_is_left_alone():
    out, replaced = grounded({"clear": ["provider"]}, Request(), "Any doctor is fine.")
    assert out == {"clear": ["provider"]} and replaced == []


OFFERED = (OfferRef(1, "appt_074", "prov_1", "loc_278", "2026-10-07T11:00:00", 45),)


def test_clearing_the_place_for_another_clinic_becomes_reject():
    """Improvised call: "Is there any other clinic I can book on?" cleared the place and asked the city again."""
    out, replaced = grounded({"clear": ["location"]}, Request(offered=OFFERED), "Is there any other clinic I can book on?")
    assert out == {"reject": ["location"]} and replaced == ["clear location"]


def test_clearing_the_place_when_nothing_was_offered_is_left_alone():
    out, _ = grounded({"clear": ["location"]}, Request(), "Is there any other clinic I can book on?")
    assert out == {"clear": ["location"]}


@pytest.mark.parametrize("cleared, said, kind", [
    ("location", "Do you have a different location?", "location"),
    ("location", "Is there another office closer?", "location"),
    ("provider", "Do you have another doctor?", "provider"),
    ("provider", "Can I see someone else?", "provider"),
    ("provider", "I'd rather have a different doctor.", "provider"),
    ("time_pref", "None of those work, anything later?", "time"),
    ("time_pref", "Do you have other times?", "time"),
    ("time_pref", "Those don't work for me.", "time"),
])
def test_clearing_for_another_option_becomes_reject(cleared, said, kind):
    out, replaced = grounded({"clear": [cleared]}, Request(offered=OFFERED), said)
    assert out == {"reject": [kind]} and replaced == [f"clear {cleared}"]


@pytest.mark.parametrize("cleared, said", [
    ("provider", "Any doctor is fine."),
    ("location", "I don't care which clinic."),
    ("time_pref", "Any time works."),
    ("provider", "Whoever is free."),
])
def test_clearing_for_a_withdrawn_choice_stays_a_clear(cleared, said):
    out, replaced = grounded({"clear": [cleared]}, Request(offered=OFFERED), said)
    assert out == {"clear": [cleared]} and replaced == []


def test_clearing_doctor_and_place_for_another_doctor_elsewhere_rejects_both():
    out, replaced = grounded({"clear": ["provider", "location"]}, Request(offered=OFFERED),
                             "Is there another doctor at a different clinic?")
    assert out == {"reject": ["location", "provider"]} and replaced == ["clear location", "clear provider"]


def test_a_named_doctor_with_offers_open_stays_a_doctor_phrase():
    out, _ = grounded({"clear": ["provider"]}, Request(offered=OFFERED), "Let's go with Dr. Kalem. Yeah, today.")
    assert out == {"provider_phrase": "Dr. Kalem"}


def test_a_reject_the_model_sent_is_kept_when_a_clear_joins_it():
    out, _ = grounded({"reject": ["provider"], "clear": ["time_pref"]}, Request(offered=OFFERED),
                      "Someone else, and none of those times.")
    assert out == {"reject": ["provider", "time"]}
