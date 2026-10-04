"""Choosing among options by position or label: "the first one" and "in pediatrics" answer the question
that listed them (real runs of one call: the caller was asked "which Dr. Maria Garcia?" three times), and
a reject sent with a pick wins over it."""

from dataclasses import replace
from pathlib import Path

import pytest

from scheduling.availability import MockAvailability
from scheduling.catalog_index import CatalogIndex
from scheduling.geo import option_at
from scheduling.request import PendingAsk, Request, Update, merge
from scheduling.resolver import resolve

DATA = Path(__file__).resolve().parents[2] / "data"
RETURNING = {"is_new": False, "has_referral": True}
FLU_SHOT = {**RETURNING, "service_phrase": "flu shot"}   # offers: both Dr. Maria Garcias, then Dr. Carlos Garcia
PEDIATRICS, FAMILY, CARLOS = "prov_002", "prov_003", "prov_008"


@pytest.fixture(scope="module")
def nat() -> CatalogIndex:
    return CatalogIndex.load(DATA / "national" / "catalog.json")


def _talk(index, *updates):
    availability = MockAvailability(index)
    req, plan = Request(), None
    for u in updates:
        req = merge(req, Update.from_args(u))
        plan = resolve(index, req, availability)
        req = plan.req
    return plan


def _providers(plan):
    return {o.provider_id for o in plan.offers}


@pytest.mark.parametrize("answer, options, picked", [
    ("the first one", ("a", "b"), "a"),
    ("The second.", ("a", "b"), "b"),
    ("the last one", ("a", "b", "c"), "c"),
    ("number two", ("a", "b", "c"), "b"),
    ("option 3, please", ("a", "b", "c"), "c"),
    ("I'll take the first one", ("a", "b"), "a"),
    ("the second", ("a", "b", "c"), "b"),
    ("the third one", ("a", "b"), None),
    ("the first name is Maria", ("a", "b"), None),
    ("the first or the second", ("a", "b"), None),
    ("not the first one", ("a", "b"), None),
    ("the one", ("a", "b"), None),
    ("Dr. Chen", ("a", "b"), None),
    ("the first one", ("a",), None),
])
def test_an_answer_is_a_position_only_when_it_says_nothing_else(answer, options, picked):
    assert option_at(answer, options) == picked


@pytest.mark.parametrize("answer, provider", [
    ("The first one.", PEDIATRICS), ("the second", FAMILY), ("the last one", FAMILY), ("number one", PEDIATRICS),
])
def test_a_doctor_question_is_answered_by_position(index, answer, provider):
    plan = _talk(index, FLU_SHOT, {"pick_offer": 3}, {"provider_phrase": "Dr. Maria Garcia"}, {"provider_phrase": answer})
    assert plan.status == "offer" and _providers(plan) == {provider}


def test_the_call_that_looped_on_which_maria_garcia(index):
    plan = _talk(index, FLU_SHOT, {"pick_offer": 3})
    assert (plan.status, plan.confirm.provider_id) == ("confirm", CARLOS)
    plan = _talk(index, FLU_SHOT, {"pick_offer": 3}, {"provider_phrase": "Dr. Maria Garcia"})
    assert (plan.status, plan.ask.options) == ("ask", (PEDIATRICS, FAMILY))
    plan = _talk(index, FLU_SHOT, {"pick_offer": 3}, {"provider_phrase": "Dr. Maria Garcia"},
                 {"provider_phrase": "The first one."}, {"time_pref": {"days": ["friday"]}})
    assert plan.status == "offer" and _providers(plan) == {PEDIATRICS}


@pytest.mark.parametrize("answer, provider", [
    ("in pediatrics", PEDIATRICS), ("the pediatrics one", PEDIATRICS), ("pediatrics", PEDIATRICS),
    ("family medicine", FAMILY), ("in family medicine", FAMILY), ("the family medicine one", FAMILY),
])
def test_a_doctor_question_is_answered_with_the_label_it_gave(index, answer, provider):
    plan = _talk(index, FLU_SHOT, {"pick_offer": 3}, {"provider_phrase": "Dr. Maria Garcia"}, {"provider_phrase": answer})
    assert plan.status == "offer" and _providers(plan) == {provider}


@pytest.mark.parametrize("answer", ["pediatrics or family medicine", "the third one", "the first name is Maria"])
def test_an_answer_that_fits_two_options_or_none_asks_again(index, answer):
    plan = _talk(index, FLU_SHOT, {"pick_offer": 3}, {"provider_phrase": "Dr. Maria Garcia"}, {"provider_phrase": answer})
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "provider", (PEDIATRICS, FAMILY))


def test_the_first_one_with_no_question_open_picks_no_doctor(index):
    plan = _talk(index, FLU_SHOT, {"provider_phrase": "the first one"})
    assert (plan.status, plan.ask.field) == ("ask", "provider_retry")
    assert plan.req.provider.resolved_id is None


def test_a_one_doctor_question_is_still_yes_or_no(index):
    req = merge(Request(), Update.from_args({**FLU_SHOT, "provider_phrase": "Dr. Emily Chen"}))
    req = Request(provider=req.provider, patient=req.patient, service=req.service,
                  pending_ask=PendingAsk("provider", ("prov_046",)))
    plan = resolve(index, merge(req, Update.from_args({"provider_phrase": "the first one"})), MockAvailability(index))
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "provider_confirm_again", ("prov_046",))


def test_a_question_that_did_not_list_its_doctors_has_no_first_one(index):
    """Four Dr. Nguyens are asked about by first name, not read out, so no answer is a place among them."""
    nguyens = tuple(p.id for p in index.providers.values() if p.last_name == "Nguyen")[:4]
    asked = _talk(index, {**FLU_SHOT, "provider_phrase": "Dr. Nguyen"}).req
    asked = replace(asked, pending_ask=PendingAsk("provider", nguyens))
    plan = resolve(index, merge(asked, Update.from_args({"provider_phrase": "the first one"})), MockAvailability(index))
    assert nguyens[0] not in _providers(plan) and not any("by its place" in note for note in plan.notes)


@pytest.mark.parametrize("answer, types", [
    ("the first one", {"appt_002"}), ("the second one", {"appt_003"}), ("the last one", {"appt_003"}),
    ("number two", {"appt_003"}),
])
def test_a_visit_question_is_answered_by_position(index, answer, types):
    plan = _talk(index, {"service_phrase": "checkup"}, {"service_phrase": answer})
    assert plan.say.startswith("For an annual") and {o.type_id for o in plan.offers} == types


def test_a_visit_question_of_three_is_answered_by_position(index):
    asked = {"service_phrase": "I have a headache", "specialty_hint": "General"}
    assert _talk(index, asked).ask.options == ("appt_001", "appt_007", "appt_036")
    plan = _talk(index, asked, {"service_phrase": "the second one"})
    assert plan.status == "offer" and {o.type_id for o in plan.offers} == {"appt_007"}
    plan = _talk(index, asked, {"service_phrase": "the last one"})
    assert (plan.status, plan.ask.field) == ("ask", "has_referral")  # the neurology consultation needs one


def test_the_fasting_question_lists_no_visits_for_a_position(index):
    plan = _talk(index, {"service_phrase": "cholesterol blood test"}, {"service_phrase": "the first one"})
    assert plan.status == "ask" and plan.say == "Did your doctor say to fast for it?"


@pytest.mark.parametrize("answer, site", [
    ("the first one", "loc_013"), ("the second one", "loc_014"), ("the last one", "loc_014"), ("number two", "loc_014"),
])
def test_a_clinic_question_is_answered_by_position(nat, answer, site):
    asked = {**RETURNING, "service_phrase": "sick visit", "location_phrase": "the clinic on Market Street in San Jose"}
    assert _talk(nat, asked).say == "Is that Downtown at 1812 Market or Willow Glen at 3330 Market?"
    plan = _talk(nat, asked, {"location_phrase": answer})
    assert plan.status == "offer" and {o.location_id for o in plan.offers} == {site}


def test_a_clinic_picked_by_position_stays_picked_when_the_time_changes(nat):
    asked = {**RETURNING, "service_phrase": "sick visit", "location_phrase": "the clinic on Market Street in San Jose"}
    plan = _talk(nat, asked, {"location_phrase": "the second one"}, {"time_pref": {"days": ["friday"]}})
    assert plan.status == "offer" and {o.location_id for o in plan.offers} == {"loc_014"}


def test_a_clinic_question_in_a_catalog_without_geography_is_answered_by_position(index):
    plan = _talk(index, {**RETURNING, "service_phrase": "sick visit", "location_phrase": "Mission"})
    assert plan.ask.field == "location" and len(plan.ask.options) == 2
    for answer, site in (("the first one", plan.ask.options[0]), ("the second one", plan.ask.options[1])):
        picked = _talk(index, {**RETURNING, "service_phrase": "sick visit", "location_phrase": "Mission"},
                       {"location_phrase": answer})
        assert picked.status == "offer" and {o.location_id for o in picked.offers} == {site}


# ---- a pick and a reject in one update ------------------------------------------------------------

def _offers(index):
    availability = MockAvailability(index)
    return resolve(index, merge(Request(), Update.from_args(FLU_SHOT)), availability), availability


@pytest.mark.parametrize("args", [{"reject": ["provider"], "pick_offer": 3}, {"pick_offer": 3, "reject": ["provider"]}])
def test_a_reject_sent_with_a_pick_wins(index, args):
    offered, _ = _offers(index)
    req = merge(offered.req, Update.from_args(args))
    assert req.pick is None and req.offered == () and CARLOS in req.rejected.providers


@pytest.mark.parametrize("args", [{"reject": ["provider"], "pick_offer": 3}, {"pick_offer": 3, "reject": ["time"]}])
def test_the_search_goes_on_without_what_was_turned_down_instead_of_reading_the_pick_back(index, args):
    offered, availability = _offers(index)
    plan = resolve(index, merge(offered.req, Update.from_args(args)), availability)
    assert plan.status == "offer" and plan.confirm is None
    if "provider" in args["reject"]:
        assert not _providers(plan) & {PEDIATRICS, FAMILY, CARLOS}


def test_a_pick_alone_is_still_read_back(index):
    offered, availability = _offers(index)
    plan = resolve(index, merge(offered.req, Update.from_args({"pick_offer": 3})), availability)
    assert (plan.status, plan.confirm.provider_id) == ("confirm", CARLOS)


@pytest.mark.parametrize("args", [
    {"provider_phrase": "Dr. Maria Garcia", "pick_offer": 3},
    {"provider_phrase": "Dr. Maria Garcia", "reject": ["provider"], "pick_offer": 3},
    {"pick_offer": 3, "reject": ["provider"], "provider_phrase": "Dr. Maria Garcia"},
])
def test_a_doctor_named_with_a_pick_of_another_doctor_is_asked_about_not_read_back(index, args):
    offered, availability = _offers(index)
    plan = resolve(index, merge(offered.req, Update.from_args(args)), availability)
    assert (plan.status, plan.ask.options) == ("ask", (PEDIATRICS, FAMILY))
    assert plan.confirm is None and plan.req.pick is None
