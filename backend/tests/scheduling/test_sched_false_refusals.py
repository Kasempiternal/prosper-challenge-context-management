"""False refusals (round 5, class 3): "we don't offer that" only for what the caller asked for, and
"none nearby" only when no visit their words name is near."""

from pathlib import Path

import pytest

from scheduling.availability import MockAvailability
from scheduling.catalog_index import CatalogIndex
from scheduling.decision import Verdict
from scheduling.lexicon import fitting_kin
from scheduling.request import Request, Update, merge
from scheduling.resolver import resolve

from fake_models import Sure

DATA = Path(__file__).resolve().parents[2] / "data"
RETURNING = {"is_new": False, "has_referral": True}
HAY_FEVER = ("every spring I sneeze nonstop and my eyes get itchy and watery, the drugstore pills don't work "
             "anymore, doctor told me to see a specialist")


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


class Unsure(Sure):
    """Picks like Sure; its check finds the caller's words fit both visits."""

    def check_type(self, phrase, hint, first, rival):
        chosen = first.act or first.ask[0]
        return Verdict(ask=(chosen, rival), top=((chosen, 0.4), (rival, 0.1), ("either", 0.5)), called=True)


class Recording(Sure):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.pools = []

    def pick_type(self, phrase, hint, candidate_ids):
        self.pools.append(list(candidate_ids))
        return super().pick_type(phrase, hint, candidate_ids)


# ---- a body word that points at a visit no clinic offers --------------------------------------

def test_a_body_word_does_not_refuse_what_the_whole_phrase_fits(sf):
    model = Recording(("appt_080",))
    plan = _talk(sf, {"service_phrase": HAY_FEVER}, model=model)
    assert plan.status == "offer" and {o.type_id for o in plan.offers} == {"appt_080"}
    assert "appt_045" in model.pools[0]  # the eye visit is weighed beside the offered ones


@pytest.mark.parametrize("model", [None, Sure(("appt_045",)), Unsure(("appt_080",))])
def test_eye_care_is_refused_when_the_model_hears_eye_care_or_is_not_sure(sf, model):
    plan = _talk(sf, {"service_phrase": HAY_FEVER}, model=model)
    assert (plan.status, plan.refusal.code) == ("refuse", "not_offered")


@pytest.mark.parametrize("phrase", ["I need an eye exam", "contact lens fitting", "my eyes are blurry"])
def test_a_visit_we_do_not_offer_is_refused(sf, phrase):
    plan = _talk(sf, {"service_phrase": phrase}, model=Sure(("appt_045", "appt_048")))
    assert (plan.status, plan.refusal.code) == ("refuse", "not_offered")


def test_a_visit_we_do_not_offer_asked_for_by_name_never_reaches_the_model(sf):
    model = Recording(("appt_080",))
    plan = _talk(sf, {"service_phrase": "I need an eye exam"}, model=model)
    assert (plan.status, plan.refusal.code, model.pools) == ("refuse", "not_offered", [])


# ---- a related visit near the caller --------------------------------------------------------

@pytest.mark.parametrize("phrase, type_id, kin", [
    ("my doctor ordered a CT scan of my chest", "appt_210", ("appt_066",)),
    ("an ultrasound of my abdomen", "appt_213", ("appt_067",)),
    ("my doctor ordered an MRI of my ankle", "appt_207", ()),
    ("a CT of my chest", "appt_210", ()),
])
def test_kin_are_visits_the_words_name_in_full(nat, phrase, type_id, kin):
    assert fitting_kin(nat, phrase, type_id) == kin


def test_a_related_visit_the_words_name_is_offered_when_the_one_meant_is_far(nat):
    plan = _talk(nat, {"service_phrase": "my doctor ordered a CT scan of my chest",
                       "location_phrase": "the ZIP code is 60657"}, model=Sure(("appt_210",)))
    assert plan.status == "offer" and {o.type_id for o in plan.offers} == {"appt_066"}
    assert plan.say.startswith("We don't offer a CT - chest nearby. For a CT scan,")


def test_no_related_visit_leaves_the_refusal(nat):
    plan = _talk(nat, {"service_phrase": "need an MRI of my ankle, doctor ordered it", "location_phrase": "Boise, Idaho"},
                 model=Sure(("appt_207",)))
    assert (plan.status, plan.refusal.code) == ("refuse", "none_nearby")
