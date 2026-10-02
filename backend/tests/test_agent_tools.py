import asyncio
import time

import pytest
from pipecat.flows import NO_RESPONSE
from pipecat.frames.frames import TTSSpeakFrame

from agent_tools import Reenter, build_tool, make_context, stt_keyterms
from agent_tools.context import RecordingDisambiguator, shared_availability
from agent_tools.scheduling_tools import spoken_ref
from scheduling.decision import DECLINE
from scheduling.resolver import NoDisambiguator


class FakeWorker:
    def __init__(self):
        self.frames = []

    async def queue_frame(self, frame):
        self.frames.append(frame)


class FakeFlowManager:
    def __init__(self):
        self.state = {"summary": ""}
        self.worker = FakeWorker()


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
        return make_context("data/catalog.json", speak_direct=speak_direct, jev_enabled=True,
                            jev_timeout_ms=2500, on_event=on_event)

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
    assert event["jev"] == {"used": False}
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


def test_book_offer_is_idempotent(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm)

    first, next_node = call(ctx, fm, "book_offer", {})
    again, again_next = call(ctx, fm, "book_offer", {})
    assert next_node == Reenter(respond=False)
    assert again_next is None
    assert first == again
    assert first == {"status": "booked", "ref": first["ref"], "visit": "cardiology consultation",
                     "provider": "Dr. Emily Chen", "location": "Downtown", "when": "tomorrow at 8",
                     "spoken": f"You're all booked. Your confirmation is {spoken_ref(first['ref'])}. "
                               "Is there anything else I can help with?"}
    assert first["ref"].startswith("H-")
    assert fm.state["bookings"] == [{"ref": first["ref"], "visit": "cardiology consultation",
                                     "provider": "Dr. Emily Chen", "location": "Downtown", "when": "tomorrow at 8"}]
    assert [f.text for f in fm.worker.frames if isinstance(f, TTSSpeakFrame)][-1] == first["spoken"]


def test_spoken_ref_spells_one_character_at_a_time():
    assert spoken_ref("H-60A2F034") == "H, 6, 0, A, 2, F, 0, 3, 4"


def test_booking_without_speak_direct_lets_the_llm_tell_the_caller(make_ctx):
    ctx, fm = make_ctx(speak_direct=False), FakeFlowManager()
    confirm_pick(ctx, fm)
    spoken_before = len(fm.worker.frames)
    result, next_node = call(ctx, fm, "book_offer", {})
    assert next_node == Reenter(respond=True)
    assert "spoken" not in result
    assert len(fm.worker.frames) == spoken_before
    assert fm.state["summary"] == (
        f"Booked: cardiology consultation with Dr. Emily Chen, tomorrow at 8 at Downtown, confirmation "
        f"{result['ref']}. The caller may want another appointment; ask what for.")


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


def test_book_offer_cannot_book_an_unconfirmed_pick(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    call(ctx, fm, "update_request", BEAT_1)
    result, _ = call(ctx, fm, "book_offer", {"pick": 2})
    assert result == {"status": "error", "error": "nothing confirmed to book: the caller must pick a time with "
                                                  "update_request and say yes to its read-back first"}
    assert "bookings" not in fm.state


def test_book_offer_books_the_read_back_offer_whatever_pick_the_llm_sends(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm, pick=1)
    result, _ = call(ctx, fm, "book_offer", {"pick": 2})
    assert (result["status"], result["when"], result["location"]) == ("booked", "tomorrow at 8", "Downtown")


def test_booking_resets_the_request_but_keeps_the_patient(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm)
    booked, _ = call(ctx, fm, "book_offer", {})
    assert fm.state["status"] == "booked"
    assert fm.state["summary"] == (
        f"Booked: cardiology consultation with Dr. Emily Chen, tomorrow at 8 at Downtown, confirmation "
        f"{booked['ref']}. The caller has heard the confirmation reference. The caller may want another "
        "appointment; ask what for.")

    result, _ = call(ctx, fm, "update_request", {"service_phrase": "dermatology appointment",
                                                 "specialty_hint": "Dermatology"})
    assert result["status"] == "offer"
    assert result["known"] == {"new_patient": "yes", "referral": "yes", "visit": "Dermatology Consultation"}
    assert len({" ".join(o.split()[1:4]) for o in result["offers"]}) == 3  # "Thu Oct 8": one offer per day
    assert fm.state["req"]["provider"]["heard"] is None

    confirm_pick(ctx, fm, pick=2)
    second, _ = call(ctx, fm, "book_offer", {})
    assert [b["ref"] for b in fm.state["bookings"]] == [booked["ref"], second["ref"]]


def test_book_offer_rechecks_policy(make_ctx):
    ctx, fm = make_ctx(), FakeFlowManager()
    confirm_pick(ctx, fm, pick=2)
    fm.state["req"]["patient"]["has_referral"] = False
    result, _ = call(ctx, fm, "book_offer", {})
    assert result == {"status": "refused", "reason": "referral"}


def test_two_calls_cannot_book_the_same_slot(make_ctx):
    (a, fm_a), (b, fm_b) = (make_ctx(), FakeFlowManager()), (make_ctx(), FakeFlowManager())
    confirm_pick(a, fm_a)
    confirm_pick(b, fm_b)
    booked, _ = call(a, fm_a, "book_offer", {})
    taken, _ = call(b, fm_b, "book_offer", {})
    assert booked["status"] == "booked"
    assert taken == {"status": "taken", "reason": "taken", "next": "call update_request to get new times"}


class SlowTypes:
    def pick_type(self, phrase, hint, candidate_ids):
        time.sleep(0.5)
        return DECLINE


class FakeJevClient:
    def __init__(self):
        self.turns = 0

    def begin_turn(self):
        self.turns += 1


def _jev_ctx(make_ctx, types):
    ctx = make_ctx()
    ctx.jev_client = FakeJevClient()
    ctx.disambiguator = RecordingDisambiguator(types, NoDisambiguator())
    return ctx


def test_filler_is_spoken_while_a_slow_model_call_runs(make_ctx):
    ctx, fm = _jev_ctx(make_ctx, SlowTypes()), FakeFlowManager()
    call(ctx, fm, "update_request", {"service_phrase": "my zorbly thing"})
    assert fm.worker.frames[0].text == "One moment."
    assert len(fm.worker.frames) == 2
    assert ctx.jev_client.turns == 1


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
