"""Resolver behaviour on a catalog with metros and coordinates, built from a small synthetic
fixture: two Portlands, two Downtowns, a Dr. Maria Garcia in two cities, a metro without
imaging, and a dentist only reachable by widening the search."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from fake_models import RandomHooks
from scheduling.availability import MockAvailability, Slot as TimeSlot
from scheduling.catalog_index import build_index
from scheduling.decision import DECLINE, Verdict
from scheduling.lexicon import SHORTLIST_SIZE, match_types, type_shortlist
from scheduling.request import Request, Update, merge
from scheduling.resolver import Ask, resolve


def _loc(lid, name, metro, nbhd, lat, lon, state, city, caps=(), address="1 Main St"):
    return {"id": lid, "name": name, "address": address, "city": city, "hours": "Mon-Fri 8:00-17:00",
            "capabilities": list(caps), "metro_id": metro, "neighborhood": nbhd, "zip": None, "state": state,
            "lat": lat, "lon": lon}


METROS = [
    {"id": "austin-tx", "name": "Austin", "state": "TX", "aliases": ["ATX"], "lat": 30.27, "lon": -97.74},
    {"id": "dallas-tx", "name": "Dallas", "state": "TX", "aliases": [], "lat": 32.78, "lon": -96.80},
    {"id": "portland-or", "name": "Portland", "state": "OR", "aliases": [], "lat": 45.52, "lon": -122.68},
    {"id": "portland-me", "name": "Portland", "state": "ME", "aliases": [], "lat": 43.66, "lon": -70.26},
    {"id": "denver-co", "name": "Denver", "state": "CO", "aliases": [], "lat": 39.74, "lon": -104.99},
]
LOCATIONS = [
    _loc("loc_a1", "Riverside Health Center", "austin-tx", "Riverside", 30.24, -97.72, "TX", "Austin",
         ("lab", "imaging")),
    _loc("loc_a2", "Downtown Health Center", "austin-tx", "Downtown", 30.27, -97.74, "TX", "Austin", ("lab",),
         address="400 N Lamar Blvd"),
    _loc("loc_a3", "Mueller Clinic", "austin-tx", "Hyde Park", 30.30, -97.73, "TX", "Austin"),
    _loc("loc_a4", "Cedar Park Health Center", "austin-tx", "Cedar Park", 30.51, -97.68, "TX", "Round Rock",
         ("dental",)),
    _loc("loc_d1", "Downtown Health Center", "dallas-tx", "Downtown", 32.78, -96.80, "TX", "Dallas",
         ("lab", "imaging")),
    _loc("loc_d2", "Oak Lawn Health Center", "dallas-tx", "Oak Lawn", 32.81, -96.81, "TX", "Dallas"),
    _loc("loc_p1", "Pearl District Health Center", "portland-or", "Pearl District", 45.53, -122.68, "OR", "Portland"),
    _loc("loc_m1", "Old Port Health Center", "portland-me", "Old Port", 43.66, -70.25, "ME", "Portland"),
    _loc("loc_v1", "Capitol Hill Health Center", "denver-co", "Capitol Hill", 39.73, -104.98, "CO", "Denver"),
]
TYPES = [
    {"id": "appt_000", "name": "New Patient Visit", "specialty": "General", "duration_min": 30,
     "requires_referral": False, "new_patients_allowed": True},
    {"id": "appt_001", "name": "Follow-up Visit", "specialty": "General", "duration_min": 20,
     "requires_referral": False, "new_patients_allowed": False},
    {"id": "appt_002", "name": "MRI - Knee", "specialty": "Radiology", "duration_min": 45,
     "requires_referral": True, "new_patients_allowed": True, "required_capability": "imaging"},
    {"id": "appt_003", "name": "Dental Cleaning", "specialty": "Dental", "duration_min": 60,
     "requires_referral": False, "new_patients_allowed": True, "required_capability": "dental"},
]
EVERYWHERE_BUT_DALLAS = [l["id"] for l in LOCATIONS if l["metro_id"] != "dallas-tx"]
PROVIDERS = [
    {"id": "prov_0", "name": "Dr. Maria Garcia", "location_ids": ["loc_a1", "loc_a2"],
     "appointment_type_ids": ["appt_000", "appt_001"]},
    {"id": "prov_1", "name": "Dr. Maria Garcia", "location_ids": ["loc_d1"],
     "appointment_type_ids": ["appt_000", "appt_001"]},
    {"id": "prov_2", "name": "Dr. Ken Ito", "location_ids": EVERYWHERE_BUT_DALLAS,
     "appointment_type_ids": ["appt_000", "appt_001"]},
    {"id": "prov_3", "name": "Dr. Ana Lee", "specialty": "Radiology", "location_ids": ["loc_a1", "loc_d1", "loc_v1"],
     "appointment_type_ids": ["appt_002"]},
    {"id": "prov_4", "name": "Dr. Sam Park", "specialty": "Dental", "location_ids": ["loc_a4"],
     "appointment_type_ids": ["appt_003"]},
    {"id": "prov_5", "name": "Dr. Joe Chen", "location_ids": ["loc_d2"], "accepting_new_patients": False,
     "appointment_type_ids": ["appt_000"]},
]
ALIASES = {"aliases": {"knee mri": {"appt_002": 1.0}, "cleaning": {"appt_003": 1.0}, "follow up": {"appt_001": 1.0}},
           "lay_terms": {"teeth": "Dental", "hurt my knee": "Radiology", "knee": "General"},
           "specialty_default": {"General": "appt_000", "Radiology": "appt_002", "Dental": "appt_003"}}


def national_raw() -> dict:
    providers = [{"specialty": "General", "accepting_new_patients": True, **p} for p in PROVIDERS]
    return {"metros": METROS, "locations": LOCATIONS, "providers": providers, "appointment_types": TYPES}


@pytest.fixture(scope="module")
def nat():
    return build_index(national_raw(), ALIASES)


class Recorder:
    """Model hooks that record each consultation and answer with a fixed verdict."""

    def __init__(self, site: Verdict = DECLINE):
        self.site, self.calls = site, []

    def pick_type(self, phrase, hint, candidate_ids):
        self.calls.append(("type", phrase, list(candidate_ids)))
        return DECLINE

    def pick_provider(self, phrase, type_id, candidate_ids):
        self.calls.append(("provider", phrase, list(candidate_ids)))
        return DECLINE

    def pick_site(self, phrase, type_id, candidate_ids):
        self.calls.append(("site", phrase, list(candidate_ids)))
        return self.site


@pytest.fixture
def talk(nat):
    def run(*updates, hooks=None, av=None):
        av = av or MockAvailability(nat)
        h = hooks or Recorder()
        req, plan = Request(), None
        for u in updates:
            req = merge(req, Update.from_args(u))
            plan = resolve(nat, req, av, h, h, h)
            req = plan.req
        return plan
    return run


VISIT = {"service_phrase": "new patient visit", "is_new": True}
FOLLOW_UP = {"service_phrase": "follow up", "is_new": False}


def _offered_sites(plan):
    return {o.location_id for o in plan.offers}


# ---- which city ---------------------------------------------------------------------------

def test_no_place_on_a_multi_metro_catalog_asks_the_city(talk):
    plan = talk(VISIT)
    assert (plan.status, plan.say, plan.ask) == ("ask", "Which city are you in?", Ask("metro"))
    assert plan.result["ask"] == "metro"


def test_portland_is_asked_as_either_or_and_the_state_settles_it(talk):
    plan = talk({**VISIT, "location_phrase": "Portland"})
    assert plan.say == "Is that Portland, Maine or Portland, Oregon?"
    assert plan.ask.options == ("portland-me", "portland-or")
    assert plan.result["ask"] == {"field": "metro", "options": ["Portland, Maine", "Portland, Oregon"]}
    plan = talk({**VISIT, "location_phrase": "Portland"}, {"location_phrase": "Oregon"})
    assert plan.status == "offer" and _offered_sites(plan) == {"loc_p1"}
    assert plan.req.location.heard == "Portland" and plan.req.location.region == "Oregon"


def test_a_city_answer_keeps_the_clinic_name(talk):
    plan = talk({**VISIT, "location_phrase": "Downtown"})
    assert plan.say == "Is that Austin or Dallas?"
    plan = talk({**VISIT, "location_phrase": "Downtown"}, {"location_phrase": "Dallas"})
    assert plan.status == "offer" and _offered_sites(plan) == {"loc_d1"}


def test_answering_with_another_city_searches_that_city(talk):
    plan = talk({**VISIT, "location_phrase": "Portland"}, {"location_phrase": "Denver"})
    assert [a.key for a in plan.area.anchors] == ["metro:denver-co"] and plan.area.location_ids == ("loc_v1",)
    assert (plan.req.location.heard, plan.req.location.region) == ("Denver", None)


def test_same_named_doctor_in_two_cities_asks_the_city(talk):
    plan = talk({**FOLLOW_UP, "provider_phrase": "Dr. Maria Garcia"})
    assert plan.say == "Is that Austin or Dallas?"
    plan = talk({**FOLLOW_UP, "provider_phrase": "Dr. Maria Garcia"}, {"location_phrase": "Austin"})
    assert plan.status == "offer" and {o.provider_id for o in plan.offers} == {"prov_0"}


def test_a_doctor_in_one_city_pins_the_city(talk):
    plan = talk({**FOLLOW_UP, "provider_phrase": "Dr. Ken Ito", "location_phrase": "Downtown"})
    assert plan.status == "offer" and _offered_sites(plan) == {"loc_a2"}


def test_texas_asks_which_city_in_texas(talk, nat):
    raw = national_raw()
    raw["metros"] = METROS + [{"id": "houston-tx", "name": "Houston", "state": "TX", "lat": 29.76, "lon": -95.37},
                              {"id": "el-paso-tx", "name": "El Paso", "state": "TX", "lat": 31.76, "lon": -106.49}]
    raw["locations"] = LOCATIONS + [_loc("loc_h1", "Heights Health Center", "houston-tx", "Heights", 29.79, -95.39,
                                         "TX", "Houston"),
                                    _loc("loc_e1", "Sunset Health Center", "el-paso-tx", "Sunset", 31.77, -106.5,
                                         "TX", "El Paso")]
    ix = build_index(raw, ALIASES)
    req = merge(Request(), Update.from_args({**VISIT, "location_phrase": "Texas"}))
    assert resolve(ix, req, MockAvailability(ix)).say == "Which city in Texas are you in?"


# ---- areas and rings ------------------------------------------------------------------------

def test_an_area_offers_across_its_sites_without_a_site_question(talk):
    plan = talk({**FOLLOW_UP, "location_phrase": "I'm in Austin"})
    assert plan.status == "offer"
    assert plan.area.location_ids == ("loc_a2", "loc_a3", "loc_a1", "loc_a4")
    assert _offered_sites(plan) <= set(plan.area.location_ids) and len(_offered_sites(plan)) > 1


def test_ring_widens_and_says_how_far(talk):
    plan = talk({"service_phrase": "dental cleaning", "is_new": True, "location_phrase": "Hyde Park"})
    assert plan.status == "offer" and _offered_sites(plan) == {"loc_a4"}
    assert plan.area.radius_mi == 50
    assert plan.say.startswith("There's nothing closer to Hyde Park; the nearest is 15 miles away, in Round Rock. "
                               "For a dental cleaning, Dr. Sam Park has ")


def test_nothing_nearby_names_the_nearest_city_and_it_is_pickable(talk):
    knee = {"service_phrase": "knee MRI", "is_new": True, "has_referral": True, "location_phrase": "Denver"}
    plan = talk(knee)
    assert plan.status == "refuse" and plan.refusal.code == "none_nearby"
    assert plan.say == ("We don't offer a knee MRI within 50 miles of Denver. The nearest is Downtown in Dallas, "
                        "about 662 miles away. Want me to look there?")
    assert plan.refusal.alternatives == (("appt_002", "prov_3", "loc_d1"),)
    plan = talk(knee, {"pick_offer": 1})
    assert plan.status == "offer" and _offered_sites(plan) == {"loc_d1"}


def test_a_state_without_clinics_refuses_with_the_nearest(talk):
    plan = talk({**VISIT, "location_phrase": "Montana"})
    assert plan.refusal.code == "none_nearby"
    assert plan.say.startswith("We don't offer a new patient visit in Montana. The nearest is Capitol Hill in Denver")


def test_a_state_with_no_city_of_ours_refuses_instead_of_ringing_past_50_miles(talk):
    plan = talk({**FOLLOW_UP, "location_phrase": "out near Cheyenne, Wyoming"})
    assert plan.status == "refuse" and plan.refusal.code == "none_nearby"
    assert plan.say == ("We don't offer a follow-up visit in Wyoming. The nearest is Capitol Hill in Denver, "
                        "about 264 miles away. Want me to look there?")
    assert [a[2] for a in plan.refusal.alternatives] == ["loc_v1"]
    plan = talk({**FOLLOW_UP, "location_phrase": "out near Cheyenne, Wyoming"}, {"pick_offer": 1})
    # Picking it searches there (the fixture's Dr. Ito has no open days at that site).
    assert "adopted alternative 1" in plan.notes and plan.req.location.resolved_id == "loc_v1"


def _with_plano() -> dict:
    """A second city 18 miles from Dallas, inside Dallas's 25-mile radius."""
    raw = national_raw()
    raw["metros"] = METROS + [{"id": "plano-tx", "name": "Plano", "state": "TX", "aliases": [],
                               "lat": 33.02, "lon": -96.70}]
    raw["locations"] = LOCATIONS + [_loc("loc_x1", "Legacy Health Center", "plano-tx", "Legacy", 33.02, -96.70,
                                         "TX", "Plano", ("dental",))]
    for p in raw["providers"]:
        if p["id"] in ("prov_2", "prov_4"):
            p["location_ids"] = p["location_ids"] + ["loc_x1"]
    return raw


def test_a_named_city_offers_its_own_clinics_before_a_neighbor_city():
    ix = build_index(_with_plano(), ALIASES)
    req = merge(Request(), Update.from_args({**FOLLOW_UP, "location_phrase": "Dallas"}))
    plan = resolve(ix, req, MockAvailability(ix))
    assert plan.status == "offer" and _offered_sites(plan) == {"loc_d1"}
    req = merge(Request(), Update.from_args({"service_phrase": "dental cleaning", "is_new": True,
                                             "location_phrase": "Dallas"}))
    plan = resolve(ix, req, MockAvailability(ix))
    assert plan.status == "offer" and _offered_sites(plan) == {"loc_x1"}
    assert plan.say.startswith("There's nothing closer to Dallas; the nearest is 18 miles away, in Plano. ")


def test_a_clinic_named_after_its_suburb_searches_the_suburb_when_it_cannot_do_the_visit(talk):
    knee = {"service_phrase": "knee MRI", "is_new": True, "has_referral": True}
    plan = talk({**knee, "location_phrase": "Cedar Park"})
    assert plan.status == "offer" and _offered_sites(plan) == {"loc_a1"}
    assert plan.say.startswith("That's not available at Cedar Park. ")
    plan = talk({**knee, "location_phrase": "Cedar Park Health Center"})
    assert plan.status == "refuse" and plan.refusal.code == "location_type"


def test_policy_beats_none_nearby_when_rows_exist(talk):
    plan = talk({"service_phrase": "follow up", "is_new": True, "location_phrase": "Austin"})
    assert plan.refusal.code == "new_patient_type"


def test_offers_name_the_neighborhood_when_the_site_name_does_not(talk):
    plan = talk({**FOLLOW_UP, "location_phrase": "Mueller"})
    assert plan.status == "offer" and _offered_sites(plan) == {"loc_a3"}
    assert " at Mueller in Hyde Park" in plan.say
    confirm = talk({**FOLLOW_UP, "location_phrase": "Mueller"}, {"pick_offer": 1})
    assert confirm.status == "confirm" and confirm.say.endswith("at Mueller in Hyde Park. Shall I book it?")


class SameTimeEverywhere:
    """Every row is open at the same three times, so only distance can break the tie."""

    def __init__(self, ix):
        self.ix, self.now = ix, datetime(2026, 10, 7, 9, 0)
        self.starts = [datetime(2026, 10, 8 + d, 10, 0) for d in range(3)]

    def find(self, rows, time_pref, limit=3):
        rows = sorted(rows, key=lambda r: r.key)
        return [TimeSlot(r.type.id, r.provider.id, r.location.id, s, r.type.duration_min)
                for s, r in zip(self.starts, rows)][:limit]

    def is_open(self, slot):
        return slot.start in self.starts and self.ix.row(slot.type_id, slot.provider_id, slot.location_id)


def test_same_start_prefers_the_nearer_site(talk, nat):
    plan = talk({**FOLLOW_UP, "provider_phrase": "Dr. Ken Ito", "location_phrase": "Hyde Park"},
                av=SameTimeEverywhere(nat))
    assert plan.offers[0].location_id == "loc_a3"


# ---- site chooser ---------------------------------------------------------------------------

def test_site_chooser_only_runs_on_descriptive_words(talk):
    for phrase in ("Austin", "I'm in Austin", "Austen", "the Austin area", "ATX"):
        hooks = Recorder()
        plan = talk({**FOLLOW_UP, "location_phrase": phrase}, hooks=hooks)
        assert plan.status == "offer" and not [c for c in hooks.calls if c[0] == "site"], phrase
    hooks = Recorder()
    talk({**FOLLOW_UP, "location_phrase": "Pearl District in Austin"}, hooks=hooks)
    assert hooks.calls == [("site", "Pearl District in Austin", ["loc_a2", "loc_a3", "loc_a1", "loc_a4"])]


def test_a_site_choice_that_got_no_answer_asks_instead_of_offering_the_area(talk):
    plan = talk({**FOLLOW_UP, "location_phrase": "the big new one in Austin"},
                hooks=Recorder(Verdict(called=True, failed=True)))
    assert plan.status == "ask" and plan.ask.field in ("location", "location_open")


def test_a_street_none_of_our_clinics_is_on_is_no_site_clue(talk):
    """"Peachtree Street in Atlanta": only geography the catalog lacks could place that street."""
    hooks = Recorder(Verdict(ask=("loc_a1", "loc_a2"), called=True))
    plan = talk({**FOLLOW_UP, "location_phrase": "the one on Elm Street in Austin"}, hooks=hooks)
    assert plan.status == "offer" and not [c for c in hooks.calls if c[0] == "site"]
    plan = talk({**FOLLOW_UP, "location_phrase": "the one by the lake in Austin"}, hooks=hooks)
    assert [c[0] for c in hooks.calls].count("site") == 1


def test_site_chooser_act_books_there_and_pair_asks(talk):
    act = Recorder(Verdict(act="loc_a3", called=True))
    plan = talk({**FOLLOW_UP, "location_phrase": "the big new one in Austin"}, hooks=act)
    assert plan.status == "offer" and _offered_sites(plan) == {"loc_a3"}
    pair = Recorder(Verdict(ask=("loc_a3", "loc_a1"), called=True))
    plan = talk({**FOLLOW_UP, "location_phrase": "the big new one in Austin"}, hooks=pair)
    assert plan.say == "Is that Riverside or Mueller?"
    stray = Recorder(Verdict(ask=("loc_a3", "loc_p1"), called=True))
    plan = talk({**FOLLOW_UP, "location_phrase": "the big new one in Austin"}, hooks=stray)
    assert plan.status == "offer" and len(_offered_sites(plan)) > 1


def test_site_chooser_call_rate_on_fixture_phrases(talk):
    phrases = ["Austin", "Hyde Park", "Downtown, Austin", "near Riverside", "the big new one in Austin",
               "Pearl District in Austin", "ATX", "somewhere in Austin", "the one by the airport in Austin", "Mueller"]
    called = 0
    for phrase in phrases:
        hooks = Recorder()
        talk({**FOLLOW_UP, "location_phrase": phrase}, hooks=hooks)
        called += any(c[0] == "site" for c in hooks.calls)
    assert called == 3


# ---- type shortlist and lay terms -----------------------------------------------------------

def _many_types_raw(n=90):
    raw = national_raw()
    extra = [{"id": f"appt_{100 + i}", "name": f"Specialty {i} Consultation", "specialty": f"Spec{i % 9}",
              "duration_min": 30, "requires_referral": False, "new_patients_allowed": True} for i in range(n)]
    raw["appointment_types"] = TYPES + extra
    raw["providers"][2] = {**raw["providers"][2], "appointment_type_ids": ["appt_000", "appt_001"]
                           + [t["id"] for t in extra]}
    return raw


def test_large_catalogs_send_the_model_a_shortlist(talk):
    ix = build_index(_many_types_raw(), ALIASES)
    hooks = Recorder()
    req = merge(Request(), Update.from_args({"service_phrase": "my zorbly thing", "location_phrase": "Austin"}))
    resolve(ix, req, MockAvailability(ix), hooks, hooks, hooks)
    (kind, _, ids), = [c for c in hooks.calls if c[0] == "type"]
    assert kind == "type" and 0 < len(ids) <= SHORTLIST_SIZE and ids == sorted(ids)
    assert len(type_shortlist(ix, "knee", None)) <= SHORTLIST_SIZE


def test_small_catalogs_send_every_offered_type(nat):
    hooks = Recorder()
    req = merge(Request(), Update.from_args({"service_phrase": "my zorbly thing", "location_phrase": "Austin"}))
    resolve(nat, req, MockAvailability(nat), hooks, hooks, hooks)
    assert [c[2] for c in hooks.calls if c[0] == "type"] == [["appt_000", "appt_001", "appt_002", "appt_003"]]


def test_multi_word_lay_terms_win_over_their_words(nat):
    assert [c.type_id for c in match_types(nat, "I hurt my knee")] == ["appt_002"]
    assert [c.type_id for c in match_types(nat, "my knee")][0] == "appt_000"
    assert [c.type_id for c in match_types(nat, "my teeth")] == ["appt_003"]


def test_every_specialty_stays_reachable_when_nothing_lexical_matched():
    raw = national_raw()
    extra = [{"id": f"appt_{100 + i}", "name": f"Specialty {i} Consultation", "specialty": f"Spec{i}",
              "duration_min": 30, "requires_referral": False, "new_patients_allowed": True} for i in range(90)]
    raw["appointment_types"] = TYPES + extra
    raw["providers"][2] = {**raw["providers"][2], "appointment_type_ids": [t["id"] for t in TYPES + extra]}
    defaults = {f"Spec{i}": f"appt_{100 + i}" for i in range(2 * SHORTLIST_SIZE)}
    ix = build_index(raw, {**ALIASES, "specialty_default": {**ALIASES["specialty_default"], **defaults}})
    assert set(defaults.values()) <= set(type_shortlist(ix, "my wrists ache when it rains", None))
    assert len(type_shortlist(ix, "specialty 7", None)) <= SHORTLIST_SIZE + len(ix.specialty_default)


# ---- lexicon: specificity -------------------------------------------------------------------

SPECIFIC_TYPES = [
    ("appt_010", "X-Ray", "Radiology"), ("appt_011", "Knee X-Ray", "Radiology"),
    ("appt_012", "Colonoscopy", "Gastroenterology"), ("appt_013", "Colonoscopy Consultation", "Gastroenterology"),
    ("appt_014", "Therapy Session", "Psychiatry"), ("appt_015", "Physical Therapy Evaluation", "Physical Therapy"),
    ("appt_016", "Physical Therapy Session", "Physical Therapy"), ("appt_017", "Orthopedic Consultation", "Orthopedics"),
    ("appt_018", "GI Consultation", "Gastroenterology"), ("appt_019", "ENT Consultation", "ENT"),
]
SPECIFIC_ALIASES = {
    "aliases": {"x ray": {"appt_010": 1.0, "appt_011": 0.6}, "colonoscopy": {"appt_012": 1.0, "appt_013": 1.0},
                "therapy": {"appt_014": 1.0}, "physical therapy": {"appt_015": 1.0, "appt_016": 0.9}},
    "lay_terms": {"knee": "Orthopedics", "stomach": "Gastroenterology", "throat": "ENT"},
    "specialty_default": {"Orthopedics": "appt_017", "Gastroenterology": "appt_018", "ENT": "appt_019"}}


@pytest.fixture(scope="module")
def specific():
    raw = national_raw()
    raw["appointment_types"] = TYPES + [{"id": i, "name": n, "specialty": s, "duration_min": 30,
                                         "requires_referral": False, "new_patients_allowed": True}
                                        for i, n, s in SPECIFIC_TYPES]
    raw["providers"][2] = {**raw["providers"][2], "appointment_type_ids": [t["id"] for t in raw["appointment_types"]]}
    return build_index(raw, SPECIFIC_ALIASES)


@pytest.mark.parametrize("phrase,ids", [
    ("the clinic says I'm due for a colonoscopy", ["appt_012"]),          # not the consultation
    ("they want a knee x-ray done this week", ["appt_011"]),              # not the plain X-Ray
    ("booking a physical therapy evaluation after surgery", ["appt_015"]),  # not Psychiatry's Therapy Session
    ("just a therapy session", ["appt_014"]),
])
def test_a_type_named_in_full_drops_the_types_inside_its_name(specific, phrase, ids):
    assert [c.type_id for c in match_types(specific, phrase)] == ids


def test_lay_term_default_only_when_no_type_is_named_and_one_specialty_is_meant(specific):
    knee = [c.type_id for c in match_types(specific, "an x-ray of the knee, it's been sore")]
    assert "appt_017" not in knee and {"appt_010", "appt_011"} <= set(knee)
    assert [c.type_id for c in match_types(specific, "my stomach keeps hurting")] == ["appt_018"]
    assert match_types(specific, "my throat and my stomach both burn") == []


# ---- request merge ----------------------------------------------------------------------------

def test_metro_answer_narrows_instead_of_replacing(nat):
    req = merge(Request(), Update.from_args({"location_phrase": "Downtown"}))
    req = replace(resolve(nat, replace(req, service=req.service), MockAvailability(nat)).req)
    assert req.pending_ask.field == "metro"
    answered = merge(req, Update.from_args({"location_phrase": "Austin"}))
    assert (answered.location.heard, answered.location.region, answered.location.within) == (
        "Downtown", "Austin", ("austin-tx", "dallas-tx"))
    assert Request.from_dict(answered.to_dict()) == answered


def test_offers_stay_close_in_time_order(talk):
    plan = talk({**FOLLOW_UP, "location_phrase": "Austin"})
    starts = [o.start for o in plan.offers]
    assert starts == sorted(starts) and starts[-1] - starts[0] < timedelta(days=21)


# ---- property test: adversarial hooks never get an invalid or out-of-area offer -------------

def _oracle(raw, type_id, provider_id, location_id, patient) -> list[str]:
    t = next(x for x in raw["appointment_types"] if x["id"] == type_id)
    p = next(x for x in raw["providers"] if x["id"] == provider_id)
    loc = next(x for x in raw["locations"] if x["id"] == location_id)
    broken = []
    if location_id not in p["location_ids"]:
        broken.append("provider not at location")
    if type_id not in p["appointment_type_ids"]:
        broken.append("provider does not offer type")
    if t.get("required_capability") and t["required_capability"] not in loc["capabilities"]:
        broken.append("location lacks capability")
    if t["requires_referral"] and patient.has_referral is not True:
        broken.append("referral not confirmed")
    if (not t["new_patients_allowed"] or not p["accepting_new_patients"]) and patient.is_new is not False:
        broken.append("not confirmed established")
    return broken


_SERVICES = ["new patient visit", "follow up", "knee MRI", "cleaning", "my teeth", "I hurt my knee", "zorbly"]
_DOCTORS = ["Dr. Maria Garcia", "Dr. Garcia", "Dr. Ken Ito", "Dr. Ito", "Dr. Ana Lee", "Dr. Sam Park", "Dr. Chen"]
_PLACES = ["Austin", "Austen", "Portland", "Oregon", "Maine", "Dallas", "Downtown", "Downtown, Austin", "Hyde Park",
           "Round Rock", "Riverside", "near Riverside", "Texas", "Montana", "Denver", "Mueller", "Narnia",
           "the big new one in Austin", "Pearl District in Austin", "Old Port", "Oak Lawn in Dallas"]


def _within_area(nat, plan, location_id) -> bool:
    from scheduling.geo import haversine
    loc = nat.locations[location_id]
    for a in plan.area.anchors:
        if a.kind == "metro" and loc.metro_id in a.metro_ids:
            return True
        if haversine(a.lat, a.lon, loc.lat, loc.lon) <= plan.area.radius_mi + 1e-9:
            return True
    return False


@pytest.mark.parametrize("seed", (0, 1, 2))
def test_geo_offers_pass_policy_and_stay_in_the_final_ring(nat, seed):
    import random
    rng = random.Random(seed)
    raw = national_raw()
    universe = {"type": list(nat.types), "provider": list(nat.providers), "site": list(nat.locations)}
    commits = in_area = 0
    for _ in range(150):
        av = MockAvailability(nat)
        hooks = RandomHooks(rng, universe) if rng.random() < 0.6 else Recorder()
        req = Request()
        for _turn in range(rng.randint(1, 6)):
            a: dict = {}
            if rng.random() < 0.4:
                a["service_phrase"] = rng.choice(_SERVICES)
            if rng.random() < 0.25:
                a["provider_phrase"] = rng.choice(_DOCTORS)
            if rng.random() < 0.5:
                a["location_phrase"] = rng.choice(_PLACES)
            for flag in ("is_new", "has_referral"):
                if rng.random() < 0.35:
                    a[flag] = rng.choice([True, False])
            if rng.random() < 0.3:
                a["pick_offer"] = rng.randint(1, 3)
            req = merge(req, Update.from_args(a or {"pick_offer": 1}))
            plan = resolve(nat, req, av, hooks, hooks, hooks)
            assert len(plan.offers) <= 3
            for o in list(plan.offers) + ([plan.confirm] if plan.confirm else []):
                commits += 1
                broken = _oracle(raw, o.type_id, o.provider_id, o.location_id, plan.req.patient)
                assert not broken, (broken, o, plan.say)
            if plan.area and plan.offers:
                for o in plan.offers:
                    in_area += 1
                    assert o.location_id in plan.area.location_ids, (o, plan.area)
                    assert _within_area(nat, plan, o.location_id), (o, plan.area)
            req = plan.req
    assert commits > 50 and in_area > 30


def test_jev_site_chooser_request_and_gate(nat, talk):
    import json as _json
    import httpx
    from scheduling.jev import JevClient, JevSiteChooser

    bodies = []

    def server(request):
        body = _json.loads(request.content)
        bodies.append(body)
        probs = {k: (0.9 if k == "loc_a3" else 0.1 / 3) for k in body["questions"]["pick"]["criteria"]}
        return httpx.Response(200, json={"answers": {"pick": {"choice": "loc_a3", "confidence": 0.9,
                                                              "probabilities": probs}},
                                         "usage": {"input_tokens": 120}})

    client = JevClient("k", transport=httpx.MockTransport(server))
    hooks = Recorder()
    site = JevSiteChooser(nat, client)
    req = merge(Request(), Update.from_args({**FOLLOW_UP, "location_phrase": "the big new one in Austin"}))
    plan = resolve(nat, req, MockAvailability(nat), hooks, hooks, site)
    assert plan.status == "offer" and _offered_sites(plan) == {"loc_a3"}
    (body,) = bodies
    assert body["state"] == ("A patient calling a multi-specialty clinic to book Follow-up Visit described the clinic "
                             "location they want: 'the big new one in Austin'")
    assert body["questions"]["pick"]["criteria"]["loc_a2"] == (
        "Downtown Health Center; Downtown, 400 N Lamar Blvd, Austin; on site: lab")
    assert body["questions"]["pick"]["criteria"]["loc_a3"] == (
        "Mueller Clinic; Hyde Park, 1 Main St, Austin; on site: general visits only")
