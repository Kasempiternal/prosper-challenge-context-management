"""Answers to "which Dr. Maria Garcia?" that describe the doctor instead of naming them (round 5,
class 4): a title, specialty, site or language narrows the doctors asked about."""

import pytest

from scheduling.request import Request, Update, merge
from scheduling.resolver import resolve

RETURNING = {"is_new": False, "has_referral": True}
WHICH_GARCIA = {**RETURNING, "service_phrase": "sick visit", "provider_phrase": "Dr. Maria Garcia"}


def _talk(index, availability, *updates):
    req, plan = Request(), None
    for u in updates:
        req = merge(req, Update.from_args(u))
        plan = resolve(index, req, availability)
        req = plan.req
    return plan


def test_the_question_is_between_the_two_maria_garcias(index, availability):
    plan = _talk(index, availability, WHICH_GARCIA)
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "provider", ("prov_002", "prov_003"))


@pytest.mark.parametrize("answer, provider", [
    ("the nurse practitioner", "prov_003"),
    ("the NP", "prov_003"),
    ("the pediatrician", "prov_002"),
    ("the one who speaks Mandarin", "prov_002"),
    ("the one at Mission Bay", "prov_002"),
    ("the one who works at Richmond", "prov_003"),
])
def test_a_described_doctor_among_those_asked_about_is_booked(index, availability, answer, provider):
    plan = _talk(index, availability, WHICH_GARCIA, {"provider_phrase": answer})
    assert plan.status == "offer" and {o.provider_id for o in plan.offers} == {provider}


@pytest.mark.parametrize("answer", ["the cardiologist", "not the one at Richmond"])
def test_a_description_that_settles_nothing_asks_between_them_again(index, availability, answer):
    plan = _talk(index, availability, WHICH_GARCIA, {"provider_phrase": answer})
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "provider", ("prov_002", "prov_003"))


def test_an_answer_with_nothing_to_hear_asks_again(index, availability):
    plan = _talk(index, availability, WHICH_GARCIA, {"provider_phrase": "uh"})
    assert (plan.status, plan.ask.field) == ("ask", "provider_retry")
