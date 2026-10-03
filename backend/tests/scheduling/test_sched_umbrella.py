"""Umbrella words (round 5, class 1): words the catalog's names and aliases attach to several
offered visits alike are asked about, whatever a model prefers; words that tell the visits apart
are not."""

from pathlib import Path

import pytest

from scheduling.availability import MockAvailability
from scheduling.catalog_index import CatalogIndex
from scheduling.lexicon import umbrella
from scheduling.request import Request, Update, merge
from scheduling.resolver import resolve

from fake_models import Sure

DATA = Path(__file__).resolve().parents[2] / "data"
RETURNING = {"is_new": False, "has_referral": True}


@pytest.fixture(scope="module")
def sf() -> CatalogIndex:
    return CatalogIndex.load(DATA / "catalog.json")


@pytest.fixture(scope="module")
def nat() -> CatalogIndex:
    return CatalogIndex.load(DATA / "national" / "catalog.json")


def _talk(ix, *updates, model=None):
    av, req, plan = MockAvailability(ix), Request(), None
    for i, u in enumerate(updates):
        req = merge(req, Update.from_args({**RETURNING, **u} if i == 0 else u))
        plan = resolve(ix, req, av, model)
        req = plan.req
    return plan


@pytest.mark.parametrize("phrase, options", [
    ("my stomach doctor said I need a scope", ("appt_054", "appt_055")),
    ("I need to book my baby's checkup", ("appt_015", "appt_016")),
    ("my doctor sent me over for some blood work", ("appt_072", "appt_073")),
    ("my GI doc wants me to get a scope", ("appt_054", "appt_055")),
])
def test_words_that_fit_several_visits_alike_ask_between_them_whatever_the_model_prefers(sf, phrase, options):
    for model in (Sure((options[0],)), Sure((options[1],))):
        plan = _talk(sf, {"service_phrase": phrase}, model=model)
        assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "service", options)


def test_the_umbrella_of_blood_work_is_asked_without_a_model_too(sf):
    plan = _talk(sf, {"service_phrase": "some blood work"})
    assert (plan.status, plan.ask.options) == ("ask", ("appt_072", "appt_073"))


@pytest.mark.parametrize("phrase, type_id", [
    ("flu shot", "appt_011"),
    ("I need a flu shot", "appt_011"),
    ("colonoscopy", "appt_054"),
    ("fasting blood test", "appt_073"),
    ("I need a fasting blood test", "appt_073"),
    ("newborn visit", "appt_016"),
    ("sick visit", "appt_007"),
    ("lung doctor", "appt_078"),
    ("upper endoscopy", "appt_055"),
])
def test_words_that_name_one_visit_are_booked(sf, phrase, type_id):
    assert umbrella(sf, phrase, type_id) == ()
    for model in (None, Sure((type_id,))):
        plan = _talk(sf, {"service_phrase": phrase}, model=model)
        assert plan.status == "offer" and {o.type_id for o in plan.offers} == {type_id}


@pytest.mark.parametrize("phrase, type_id", [
    ("my baby is two weeks old and needs her first checkup", "appt_016"),
    ("the scope thing for my colon", "appt_054"),
    ("a scope down my throat", "appt_055"),
])
def test_a_word_no_visit_carries_is_the_model_s_to_weigh(sf, phrase, type_id):
    assert umbrella(sf, phrase, type_id) == ()
    plan = _talk(sf, {"service_phrase": phrase}, model=Sure((type_id,)))
    assert plan.status == "offer" and {o.type_id for o in plan.offers} == {type_id}


def test_the_answer_to_the_umbrella_question_books(sf):
    model = Sure(("appt_015",), by_word={"weeks": "appt_016"})
    first = _talk(sf, {"service_phrase": "I need to book my baby's checkup"}, model=model)
    assert first.ask.options == ("appt_015", "appt_016")
    plan = _talk(sf, {"service_phrase": "I need to book my baby's checkup"}, {"service_phrase": "two weeks old"},
                 model=model)
    assert plan.status == "offer" and {o.type_id for o in plan.offers} == {"appt_016"}


def test_a_named_specialty_keeps_the_umbrella_inside_it(nat):
    assert umbrella(nat, "I need a scope", "appt_184") == (
        "appt_054", "appt_055", "appt_164", "appt_183", "appt_184", "appt_247")
    assert umbrella(nat, "my stomach doctor said I need a scope", "appt_184") == (
        "appt_054", "appt_055", "appt_183", "appt_184")
