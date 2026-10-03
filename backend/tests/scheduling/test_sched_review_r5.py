"""The round 5 review's probe phrases, on the real catalogs, each asserting the safe outcome with no
model and with models that answer every question confidently (the worst case for a commit). The
related-visit probes are in test_sched_false_refusals.py."""

from pathlib import Path

import pytest

from scheduling.availability import MockAvailability
from scheduling.catalog_index import CatalogIndex
from scheduling.decision import Verdict
from scheduling.request import Request, Update, merge
from scheduling.resolver import NoDisambiguator, resolve

from fake_models import Sure

DATA = Path(__file__).resolve().parents[2] / "data"
RETURNING = {"is_new": False, "has_referral": True}
SCOPE, BABY, BLOOD = ("appt_054", "appt_055"), ("appt_015", "appt_016"), ("appt_072", "appt_073")


@pytest.fixture(scope="module")
def sf() -> CatalogIndex:
    return CatalogIndex.load(DATA / "catalog.json")


@pytest.fixture(scope="module")
def nat() -> CatalogIndex:
    return CatalogIndex.load(DATA / "national" / "catalog.json")


class PicksFirst(NoDisambiguator):
    """A provider chooser sure of the first candidate whatever was said."""

    def pick_provider(self, phrase, type_id, candidate_ids):
        return Verdict(act=candidate_ids[0], top=((candidate_ids[0], 0.99),), called=True)


def _talk(ix, *updates, model=None, chooser=None):
    av, req, plan = MockAvailability(ix), Request(), None
    for i, u in enumerate(updates):
        req = merge(req, Update.from_args({**RETURNING, **u} if i == 0 else u))
        plan = resolve(ix, req, av, model, chooser)
        req = plan.req
    return plan


def _service(phrase):
    return {"service_phrase": phrase}


# ---- 3: words that fit several visits alike, however the caller says the rest -------------------

# The three dev requests (h4-m06, h4-m05, h4-27) said other ways. The review's 20 were not kept;
# these 20 were written before the fix, three of them the review's own.
PARAPHRASES = [
    ("my gut doctor scheduled me for a scope", SCOPE),
    ("my GI doctor said I'm due for a scope", SCOPE),
    ("the stomach doctor wants me to get a scope", SCOPE),
    ("I need a scope, my stomach specialist ordered it", SCOPE),
    ("my gastroenterologist told me I need a scope", SCOPE),
    ("my physician referred me for a scope", SCOPE),
    ("I have to get a scope done, my GI doc said so", SCOPE),
    ("my baby is due for a checkup", BABY),
    ("I need a checkup for my baby", BABY),
    ("can I book a checkup for my baby", BABY),
    ("my baby needs to come in for a checkup", BABY),
    ("the pediatrician's nurse said my baby needs a checkup", BABY),
    ("it's time for my baby's checkup", BABY),
    ("I'd like to schedule a checkup for the baby", BABY),
    ("my nurse said I need some blood work", BLOOD),
    ("my doctor wants me to get blood work done", BLOOD),
    ("I need to get some blood work", BLOOD),
    ("I'm due for blood work", BLOOD),
    ("my GP ordered blood work for me", BLOOD),
    ("I have to come in for blood work", BLOOD),
]


@pytest.mark.parametrize("phrase, options", PARAPHRASES)
def test_a_paraphrase_of_an_umbrella_request_asks_whatever_the_model_prefers(sf, phrase, options):
    for model in (Sure((options[0],)), Sure((options[1],))):
        plan = _talk(sf, _service(phrase), model=model)
        assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "service", options)


# ---- 2: the umbrella and a doctor ------------------------------------------------------------

def test_a_doctor_who_does_only_the_other_visit_does_not_get_it_booked(nat):
    """Review: with Dr. Leila Sato, who does no lipid panel, the umbrella's other visit was offered."""
    plan = _talk(nat, {"service_phrase": "cholesterol blood test, doctor ordered it",
                       "provider_phrase": "Dr. Leila Sato"}, model=Sure(("appt_231",)))
    assert (plan.status, plan.refusal.code, plan.refusal.type_id) == ("refuse", "provider_type", "appt_231")
    assert plan.say.startswith("I can't book a lipid panel with Dr. Leila Sato.")
    assert {a[0] for a in plan.refusal.alternatives} == {"appt_231"}


# ---- 4: a description of nobody asked about --------------------------------------------------

WHICH_GARCIA = {"service_phrase": "sick visit", "provider_phrase": "Dr. Maria Garcia"}


@pytest.mark.parametrize("answer", ["the cardiologist", "the one who speaks Spanish", "the one my daughter sees"])
@pytest.mark.parametrize("chooser", [None, PicksFirst()])
def test_a_description_that_fits_neither_doctor_asks_again_without_the_model(sf, answer, chooser):
    plan = _talk(sf, WHICH_GARCIA, {"provider_phrase": answer}, chooser=chooser)
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "provider", ("prov_002", "prov_003"))


@pytest.mark.parametrize("chooser", [None, PicksFirst()])
def test_a_change_of_mind_word_is_no_description(sf, chooser):
    plan = _talk(sf, WHICH_GARCIA, {"provider_phrase": "Dr. Michael Sato instead"}, chooser=chooser)
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "provider", ("prov_026", "prov_032"))


# ---- 5: an answer that repeats the vague word ----------------------------------------------

@pytest.mark.parametrize("answer", ["the checkup", "just the regular checkup", "a checkup for the baby"])
def test_an_answer_that_fits_both_options_alike_asks_the_same_question(sf, answer):
    model = Sure(("appt_015",))
    plan = _talk(sf, _service("I need to book my baby's checkup"), _service(answer), model=model)
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "service", BABY)
    assert plan.say == "Is that a well-child visit or a newborn visit?"


def test_three_answers_that_say_nothing_hand_off(sf):
    plan = _talk(sf, _service("I need to book my baby's checkup"), _service("the checkup"), _service("the checkup"),
                 _service("the checkup"), model=Sure(("appt_015",)))
    assert (plan.status, plan.refusal.code) == ("refuse", "handoff")


def test_an_answer_that_describes_one_option_is_still_the_model_s(sf):
    model = Sure(("appt_054",), by_word={"throat": "appt_055"})
    plan = _talk(sf, _service("my stomach doctor said I need a scope"), _service("the one down the throat"),
                 model=model)
    assert plan.status == "offer" and {o.type_id for o in plan.offers} == {"appt_055"}


# ---- 6: the fasting question ---------------------------------------------------------------

def test_blood_work_asks_whether_the_doctor_said_to_fast(sf):
    plan = _talk(sf, _service("some blood work"))
    assert (plan.status, plan.ask.options, plan.say) == ("ask", BLOOD, "Did your doctor say to fast for it?")


@pytest.mark.parametrize("answer, type_id", [
    ("yes", "appt_073"), ("yeah, nothing to eat after midnight", "appt_073"), ("I have to fast", "appt_073"),
    ("no", "appt_072"), ("nope", "appt_072"), ("I don't need to fast", "appt_072"),
])
@pytest.mark.parametrize("model", [None, Sure(("appt_072",)), Sure(("appt_073",))])
def test_the_fasting_answer_picks_the_visit(sf, answer, type_id, model):
    plan = _talk(sf, _service("some blood work"), _service(answer), model=model)
    assert plan.status == "offer" and {o.type_id for o in plan.offers} == {type_id}


@pytest.mark.parametrize("answer", ["I'm not sure", "I don't know, whatever my doctor ordered", "the first one"])
@pytest.mark.parametrize("model", [None, Sure(("appt_072",))])
def test_an_unsure_fasting_answer_asks_again(sf, answer, model):
    plan = _talk(sf, _service("some blood work"), _service(answer), model=model)
    assert (plan.status, plan.ask.options, plan.say) == ("ask", BLOOD, "Did your doctor say to fast for it?")


def test_a_question_with_a_lipid_panel_keeps_the_names(nat):
    plan = _talk(nat, {"service_phrase": "I need a blood test", "location_phrase": "Houston"},
                 _service("the cholesterol one"), model=Sure(("appt_072",)))
    assert (plan.status, plan.ask.options) == ("ask", ("appt_073", "appt_231"))
    assert plan.say == "Is that a fasting blood test or a lipid panel?"


# ---- out of scope, also in base: a full name nobody has --------------------------------------

@pytest.mark.parametrize("chooser", [None, PicksFirst()])
def test_a_full_name_whose_doctors_cannot_do_the_visit_is_refused_by_that_name(sf, chooser):
    """Review: "actually Dr. Linda Ramirez" offered Dr. Priya Ramirez without saying so."""
    plan = _talk(sf, WHICH_GARCIA, {"provider_phrase": "actually Dr. Linda Ramirez"}, chooser=chooser)
    assert (plan.status, plan.refusal.code) == ("refuse", "provider_type")
    assert plan.say.startswith("I can't book a sick visit with Dr. Linda Ramirez.")
    assert "Priya" not in plan.say


@pytest.mark.parametrize("chooser", [None, PicksFirst()])
def test_a_full_name_nobody_has_is_said_and_asked(sf, chooser):
    plan = _talk(sf, {"service_phrase": "sick visit", "provider_phrase": "Dr. Karen Ramirez"}, chooser=chooser)
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "provider", ("prov_021",))
    assert plan.say == "I don't see a Dr. Karen Ramirez. Do you mean Dr. Priya Ramirez?"
    plan = _talk(sf, {"service_phrase": "sick visit", "provider_phrase": "Dr. Karen Ramirez"},
                 {"provider_phrase": "yes"}, chooser=chooser)
    assert plan.status == "offer" and {o.provider_id for o in plan.offers} == {"prov_021"}


@pytest.mark.parametrize("phrase", ["Dr. Priya Ramirez", "Dr. Ramirez", "Priya Ramirez", "actually Ramirez"])
def test_a_name_that_matches_or_says_no_first_name_is_not_questioned(sf, phrase):
    plan = _talk(sf, {"service_phrase": "sick visit", "provider_phrase": phrase})
    assert plan.status == "offer" and {o.provider_id for o in plan.offers} == {"prov_021"}


# ---- out of scope, also in base: a screening for a symptom -----------------------------------

@pytest.mark.parametrize("phrase", ["I need a mammogram for a lump in my breast",
                                    "I found a lump in my breast and need a mammogram"])
@pytest.mark.parametrize("model", [None, Sure(("appt_068",))])
def test_a_screening_for_a_symptom_is_the_diagnostic_visit(nat, phrase, model):
    plan = _talk(nat, {"service_phrase": phrase, "location_phrase": "Houston"}, model=model)
    assert plan.status == "offer" and {o.type_id for o in plan.offers} == {"appt_216"}
    assert plan.say.startswith("For a diagnostic mammogram,")


@pytest.mark.parametrize("phrase", ["my yearly screening mammogram", "a mammogram, no lumps or anything, just routine"])
def test_a_routine_screening_stays_a_screening(nat, phrase):
    plan = _talk(nat, {"service_phrase": phrase, "location_phrase": "Houston"}, model=Sure(("appt_068",)))
    assert plan.status == "offer" and {o.type_id for o in plan.offers} == {"appt_068"}


def test_a_screening_for_a_symptom_with_no_diagnostic_visit_asks(sf):
    plan = _talk(sf, _service("I need my skin checked for cancer, a mole is bleeding"), model=Sure(("appt_027",)))
    assert (plan.status, plan.ask.options) == ("ask", ("appt_026", "appt_027"))
