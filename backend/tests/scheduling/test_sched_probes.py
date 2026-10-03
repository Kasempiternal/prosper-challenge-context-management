"""The review probe phrases, on the real catalogs, each asserting the safe outcome with no model and
with models that answer every question confidently (the worst case for a commit): a state is not
taken for a city across its line, and no distance is said from it; a place heard by sound or by
part of its name is confirmed, never booked; a visit we do not offer, said as context, is not
refused; a stated doubt is asked between the visits named, or openly, and never as a question
about one visit."""

import re
from pathlib import Path

import pytest

from scheduling.availability import MockAvailability
from scheduling.catalog_index import CatalogIndex
from scheduling.decision import Verdict
from scheduling.lexicon import stated_doubt
from scheduling.request import Request, Update, merge
from scheduling.resolver import NoDisambiguator, resolve

DATA = Path(__file__).resolve().parents[2] / "data"
RETURNING = {"is_new": False, "has_referral": True}


@pytest.fixture(scope="module")
def sf() -> CatalogIndex:
    return CatalogIndex.load(DATA / "catalog.json")


@pytest.fixture(scope="module")
def nat() -> CatalogIndex:
    return CatalogIndex.load(DATA / "national" / "catalog.json")


class Sure(NoDisambiguator):
    """Picks the first visit of `prefer` the question offers (else the first offered), sure of
    it; `by_word` maps a word of the phrase to the visit picked instead. Every check confirms."""

    def __init__(self, prefer=(), by_word=None):
        self.prefer, self.by_word = prefer, by_word or {}

    def pick_type(self, phrase, hint, candidate_ids):
        worded = [t for w, t in self.by_word.items() if w in phrase and t in candidate_ids]
        pick = next(iter(worded), next((t for t in self.prefer if t in candidate_ids), candidate_ids[0]))
        return Verdict(act=pick, top=((pick, 0.97),), called=True)

    def check_type(self, phrase, hint, first, rival):
        chosen = first.act or first.ask[0]
        return Verdict(act=chosen, top=((chosen, 0.95), (rival, 0.03), ("either", 0.02)), called=True)


def _talk(ix, *updates, model=None):
    av, req, plan = MockAvailability(ix), Request(), None
    for i, u in enumerate(updates):
        req = merge(req, Update.from_args({**RETURNING, **u} if i == 0 else u))
        plan = resolve(ix, req, av, model)
        req = plan.req
    return plan


def _types(plan):
    return {o.type_id for o in plan.offers}


# ---- states ---------------------------------------------------------------------------------

@pytest.mark.parametrize("place, state", [
    ("Virginia", "VA"), ("Virginia Beach", "VA"), ("I live in Virginia Beach", "VA"), ("Richmond, Virginia", "VA"),
    ("Roanoke, Virginia", "VA"), ("Kansas", "KS"), ("Wichita, Kansas", "KS"), ("New Jersey", "NJ"),
    ("Newark, New Jersey", "NJ"), ("Hoboken, NJ", "NJ"), ("Memphis, Tennessee", "TN"), ("Buffalo, New York", "NY"),
])
def test_a_state_or_a_city_we_do_not_know_in_it_names_the_state_s_clinic_never_how_far(nat, place, state):
    """Review round 4: "flu shot, Virginia Beach" was offered Downtown Washington, DC and Alexandria,
    about 150 miles away, with no word of where or how far. Review round 5: the distance said then was
    from the state's centre ("Richmond, Virginia ... about 135 miles", truly about 95), and "Memphis,
    Tennessee" was offered Nashville, 200 miles away, as if it were Memphis."""
    for model in (None, Sure()):
        plan = _talk(nat, {"service_phrase": "flu shot", "location_phrase": place}, model=model)
        assert (plan.status, plan.refusal.code) == ("refuse", "none_nearby")
        assert re.search(r"^Our nearest clinic in .+ for a flu shot is [^,]+(, near| in) [^.]+\. "
                         r"Want me to look there\?$", plan.say)
        assert "mile" not in plan.say
        (_, _, lid), = plan.refusal.alternatives
        assert nat.locations[lid].state == state


def test_the_first_turn_virginia_names_alexandria_and_its_city(nat):
    plan = _talk(nat, {"service_phrase": "flu shot", "location_phrase": "Virginia"})
    assert plan.say == "Our nearest clinic in Virginia for a flu shot is Alexandria, near Washington. Want me to look there?"
    plan = _talk(nat, {"service_phrase": "flu shot", "location_phrase": "Virginia"}, {"pick_offer": 1})
    assert plan.status == "offer" and {o.location_id for o in plan.offers} == {"loc_255"}


@pytest.mark.parametrize("answer, state", [("Virginia", "VA"), ("Texas", "TX")])
def test_a_state_still_answers_which_city(nat, answer, state):
    plan = _talk(nat, {"service_phrase": "flu shot", "location_phrase": "Arlington"}, {"location_phrase": answer})
    assert plan.status == "offer" and {nat.locations[o.location_id].state for o in plan.offers} == {state}


@pytest.mark.parametrize("place, options", [("Maryland", ("baltimore-md", "washington-dc")),
                                            ("Minnesota", ("minneapolis-mn", "st-paul-mn"))])
def test_a_state_with_clinics_of_two_cities_asks_which(nat, place, options):
    plan = _talk(nat, {"service_phrase": "flu shot", "location_phrase": place})
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "metro", options)


# ---- places heard, not named --------------------------------------------------------------------

@pytest.mark.parametrize("place, said, metro", [
    ("Trenton", "Renton, Washington", "seattle-wa"),                      # sounds like it, another first letter
    ("Reno", "Renton, Washington", "seattle-wa"),                         # two edits that change the sound
    ("Toledo", "Duluth, Georgia", "atlanta-ga"),                          # same sound key only
    ("Newark", "New York, New York", "new-york-ny"),                      # one word is not two
    ("Charleston", "Charlotte, North Carolina", "charlotte-nc"),
    ("Lincoln", "Lincoln Park in Chicago, Illinois", "chicago-il"),       # part of the name
    ("Jackson", "Jackson Heights, New York", "new-york-ny"),
    ("Richmond", "Richmond in San Francisco, California", "san-francisco-ca"),  # a clinic's name, no place of ours
    ("Cherry Hill, Pennsylvania", "Cherry Hill, New Jersey", "philadelphia-pa"),  # not in the state said
])
def test_a_place_heard_by_sound_or_part_of_its_name_is_confirmed_never_booked(nat, place, said, metro):
    """Review round 5: "Trenton" was offered Renton, Washington, 2,400 miles away."""
    for model in (None, Sure()):
        plan = _talk(nat, {"service_phrase": "flu shot", "location_phrase": place}, model=model)
        assert (plan.status, plan.ask.field, plan.ask.options, plan.say) == (
            "ask", "place_confirm", (metro,), f"Did you mean {said}?")


@pytest.mark.parametrize("answer, sites", [("yes", {"loc_055"}), ("yeah, that's right", {"loc_055"}),
                                           ("Renton", {"loc_055"})])
def test_a_confirmed_guess_is_searched(nat, answer, sites):
    plan = _talk(nat, {"service_phrase": "flu shot", "location_phrase": "Trenton"}, {"location_phrase": answer})
    assert plan.status == "offer" and {o.location_id for o in plan.offers} == sites


def test_a_guess_turned_down_asks_the_city_afresh(nat):
    plan = _talk(nat, {"service_phrase": "flu shot", "location_phrase": "Trenton"}, {"location_phrase": "no"})
    assert (plan.status, plan.ask.field, plan.ask.options, plan.say) == ("ask", "metro", (), "Which city are you in?")
    assert plan.req.location.heard is None


@pytest.mark.parametrize("place", ["Brooklyn", "the Richmond clinic", "Richmond District", "Cherry Hill"])
def test_a_place_or_clinic_named_outright_is_no_guess(nat, place):
    plan = _talk(nat, {"service_phrase": "flu shot", "location_phrase": place})
    assert plan.status == "offer"


def test_a_guess_turned_down_with_the_real_place_searches_that_place(nat):
    plan = _talk(nat, {"service_phrase": "flu shot", "location_phrase": "Trenton"},
                 {"location_phrase": "no, Trenton, New Jersey"})
    assert (plan.status, plan.refusal.code) == ("refuse", "none_nearby")
    assert plan.say == ("Our nearest clinic in New Jersey for a flu shot is Cherry Hill, near Philadelphia. "
                        "Want me to look there?")


@pytest.mark.parametrize("place, metro", [
    ("Cleaveland", "cleveland-oh"), ("Philedelphia", "philadelphia-pa"), ("San Antonyo", "san-antonio-tx"),
    ("Minneapolous", "minneapolis-mn"), ("Hueston", "houston-tx"), ("Sacremento", "sacramento-ca"),
])
def test_a_misspelled_city_is_that_city(nat, place, metro):
    plan = _talk(nat, {"service_phrase": "flu shot", "location_phrase": place})
    assert plan.status == "offer" and {nat.locations[o.location_id].metro_id for o in plan.offers} == {metro}


# ---- a visit we do not offer ------------------------------------------------------------------

ORTHO = "appt_032"


@pytest.mark.parametrize("phrase, offers_ortho", [
    ("my knee still hurts after PT, I want a doctor to look at it", True),
    ("I want my shoulder looked at before I start PT", True),
    ("my PT says my hip needs to be seen by a specialist", True),
    ("knee pain, PT didn't help", True),
    ("my back is killing me and physio did not help", False),
])
def test_a_visit_we_do_not_offer_said_as_context_is_not_refused(sf, phrase, offers_ortho):
    """Review round 4: all of these were told "we don't offer physical therapy"."""
    plan = _talk(sf, {"service_phrase": phrase})
    assert plan.refusal is None
    assert (plan.status == "offer" and _types(plan) == {ORTHO}) == offers_ortho
    plan = _talk(sf, {"service_phrase": phrase}, model=Sure((ORTHO,)))
    assert plan.refusal is None and _types(plan) == {ORTHO}


@pytest.mark.parametrize("phrase", ["PT for my sore knee", "start PT after my shoulder surgery",
                                    "I need physical therapy for my hip", "after my knee surgery, PT please"])
def test_a_visit_we_do_not_offer_asked_for_is_refused_with_the_body_part_s_visit_suggested(sf, phrase):
    for model in (None, Sure((ORTHO,))):
        plan = _talk(sf, {"service_phrase": phrase}, model=model)
        assert (plan.status, plan.refusal.code, plan.refusal.alt_type_id) == ("refuse", "not_offered", ORTHO)


# ---- stated doubts ----------------------------------------------------------------------------

def _not_leading(plan) -> bool:
    """Asked about the visit, openly or between two or more: never "Is that X?"."""
    return plan.status == "ask" and (plan.ask.field == "service_open"
                                     or (plan.ask.field == "service" and len(plan.ask.options) >= 2))


@pytest.mark.parametrize("phrase, options", [
    ("I'm not really sure if it's a colonoscopy or an endoscopy", ("appt_054", "appt_055")),
    ("dunno if it's the cleaning or the exam", ("appt_074", "appt_075")),
    ("not sure if it's a mole removal or a skin cancer screening or an acne follow-up",
     ("appt_027", "appt_029", "appt_030")),
    ("either a colonoscopy or an endoscopy, I'm not sure, the GI doctor ordered it", ("appt_054", "appt_055")),
])
def test_a_doubt_between_visits_named_asks_between_them(sf, phrase, options):
    plan = _talk(sf, {"service_phrase": phrase})
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "service", options)
    for model in (Sure(), Sure((options[0],))):
        plan = _talk(sf, {"service_phrase": phrase}, model=model)
        assert _not_leading(plan) and set(plan.ask.options) >= {o for o in options if o != "appt_075"}


SCOPE = ["the GI doc wants a scope, I don't remember if it goes down my throat or up from below",
         "a scope, I'm not really sure if it goes down my throat or up from below",
         "I'm not sure, maybe it goes down my throat or up from below",
         "a scope, I think it either goes down my throat or up from below",
         "not 100 percent sure if it goes down my throat or up from below"]


@pytest.mark.parametrize("phrase", SCOPE)
def test_a_doubt_between_two_descriptions_is_never_booked_or_led(sf, phrase):
    """Review round 4: without a model each of these booked an ENT consultation; with a model sure of
    one visit for both, the lexical guess was asked alone ("Is that an ENT consultation?")."""
    for model in (None, Sure(), Sure(("appt_055",))):
        assert _not_leading(_talk(sf, {"service_phrase": phrase}, model=model))
    split = Sure(by_word={"throat": "appt_055", "below": "appt_054"})
    plan = _talk(sf, {"service_phrase": phrase}, model=split)
    assert (plan.status, plan.ask.options) == ("ask", ("appt_054", "appt_055"))


@pytest.mark.parametrize("phrase, type_id", [
    ("a flu shot, I don't know if my insurance covers it or not", "appt_011"),
    ("annual physical, not sure if morning or afternoon", "appt_002"),
    ("a dental cleaning, I forget if I usually go to Mission Bay or North Beach", "appt_074"),
    ("a dental cleaning, can't remember if it's been six months or a year", "appt_074"),
])
def test_a_doubt_about_something_else_asks_nothing_about_the_visit(sf, phrase, type_id):
    assert stated_doubt(sf, phrase) is None
    plan = _talk(sf, {"service_phrase": phrase})
    assert plan.status == "offer" and _types(plan) == {type_id}


@pytest.mark.parametrize("phrase", ["a dental cleaning, I'm not sure if I'm a new patient or not",
                                    "a follow-up, not sure if it was Dr. Chen or Dr. Patel I saw"])
def test_a_doubt_about_the_patient_or_the_doctor_is_no_doubt_about_the_visit(sf, phrase):
    assert stated_doubt(sf, phrase) is None
