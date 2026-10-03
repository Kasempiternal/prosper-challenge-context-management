"""Round 4 misses with a general cause (round 5, class 5): a visit named in the caller's own word
order, a reason said after the visit, a lay term inside an alias, a doubt about what happens in a
visit already named."""

from pathlib import Path

import pytest

from scheduling.availability import MockAvailability
from scheduling.catalog_index import CatalogIndex
from scheduling.lexicon import match_types
from scheduling.request import Request, Update, merge
from scheduling.resolver import resolve

DATA = Path(__file__).resolve().parents[2] / "data"
RETURNING = {"is_new": False, "has_referral": True}


@pytest.fixture(scope="module")
def sf() -> CatalogIndex:
    return CatalogIndex.load(DATA / "catalog.json")


@pytest.fixture(scope="module")
def nat() -> CatalogIndex:
    return CatalogIndex.load(DATA / "national" / "catalog.json")


def _types(ix, phrase):
    return [c.type_id for c in match_types(ix, phrase)]


def _offer(ix, phrase, **extra):
    req = merge(Request(), Update.from_args({**RETURNING, "service_phrase": phrase, **extra}))
    return resolve(ix, req, MockAvailability(ix))


@pytest.mark.parametrize("phrase, type_id", [
    ("I need an ultrasound of my abdomen, my doctor ordered it", "appt_213"),
    ("my doctor ordered an MRI of my ankle", "appt_207"),
    ("an x-ray of my knee", "appt_219"),
])
def test_a_name_said_in_the_caller_s_own_order_drops_what_lies_inside_it(nat, phrase, type_id):
    assert _types(nat, phrase) == [type_id]


def test_two_names_neither_inside_the_other_both_stay(nat):
    assert {"appt_066", "appt_210"} <= set(_types(nat, "my doctor ordered a CT scan of my chest"))


def test_a_visit_noun_is_part_of_the_name(sf):
    """"dental" alone names no dental visit in full: Dental Exam needs "exam"."""
    assert {"appt_074", "appt_075", "appt_076"} <= set(_types(sf, "I need a dental appointment"))


def test_what_follows_for_is_the_reason_not_another_visit(nat):
    assert _types(nat, "I need a strep test for a sore throat") == ["appt_234"]
    plan = _offer(nat, "I need a strep test for a sore throat", location_phrase="my zip code's 43201")
    assert plan.status == "offer" and {o.type_id for o in plan.offers} == {"appt_234"}


def test_without_a_reason_word_both_visits_stay(nat):
    assert {"appt_007", "appt_234"} <= set(_types(nat, "I've got a sore throat and I need a strep test"))


def test_a_lay_term_inside_an_alias_points_nowhere_else(sf):
    plan = _offer(sf, "sore throat, fever and body aches for three days, I'd like to see my doctor this week")
    assert plan.status == "offer" and {o.type_id for o in plan.offers} == {"appt_007"}


def test_a_doubt_about_what_happens_in_a_named_visit_is_no_doubt_about_the_visit(sf):
    plan = _offer(sf, "want to talk to a doctor about starting birth control, maybe the pill or an IUD")
    assert plan.status == "offer" and {o.type_id for o in plan.offers} == {"appt_044"}


def test_a_doubt_whose_alternatives_name_no_visit_still_asks(sf):
    plan = _offer(sf, "a scope, I'm not really sure if it goes down my throat or up from below")
    assert plan.status == "ask"
