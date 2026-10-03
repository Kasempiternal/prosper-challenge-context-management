"""Clinics named by street, house number or full address, on the national catalog."""

import json
from collections import defaultdict
from pathlib import Path

import pytest

from scheduling import templates as T
from scheduling.availability import MockAvailability
from scheduling.catalog_index import CatalogIndex
from scheduling.geo import resolve_place
from scheduling.names import hear_place, match_locations, street_of
from scheduling.request import Request, Update, merge
from scheduling.resolver import resolve
from eval_cases import dev_case_files

DATA = Path(__file__).resolve().parents[2] / "data"
RETURNING = {"is_new": False, "has_referral": True}
SAN_JOSE = ["loc_013", "loc_014", "loc_015", "loc_016", "loc_017", "loc_018"]


@pytest.fixture(scope="module")
def nat() -> CatalogIndex:
    return CatalogIndex.load(DATA / "national" / "catalog.json")


def _say(ix, service, location, *more):
    req, plan, av = Request(), None, MockAvailability(ix)
    for u in ({**RETURNING, "service_phrase": service, "location_phrase": location}, *more):
        req = merge(req, Update.from_args(u))
        plan = resolve(ix, req, av)
        req = plan.req
    return plan


@pytest.mark.parametrize("phrase,words,number", [
    ("3330 Market Street", ("market",), 3330),
    ("33 30 Market St", ("market",), 3330),
    ("thirty-three thirty Market Street", ("market",), 3330),
    ("eighteen twelve Market", ("market",), 1812),
    ("five oh five Hill Street", ("hill",), 505),
    ("forty eight hundred Center Street", ("center",), 4800),
    ("the one on Lincoln Avenue", ("lincoln",), None),
    ("the clinic on Medical Center Drive", ("medical", "center"), None),
    ("second street", ("2nd",), None),
    ("Dr. Phillips", ("phillips",), None),
    ("Downtown Health Center", ("downtown",), None),
])
def test_hear_place_splits_number_street_and_name_words(phrase, words, number):
    heard = hear_place(phrase)
    assert (heard.words, heard.number) == (words, number)


def test_every_address_names_its_clinic(nat):
    for loc in nat.locations.values():
        phrase = f"{loc.address}, {nat.metros[loc.metro_id].name}"
        assert resolve_place(nat, phrase).site_ids == (loc.id,), phrase


def test_every_street_names_the_clinics_on_it_in_that_city(nat):
    on_street = defaultdict(set)
    for loc in nat.locations.values():
        on_street[(loc.metro_id, T.street_label(nat, loc.id))].add(loc.id)
    for (metro_id, street), ids in on_street.items():
        phrase = f"the clinic on {street} in {nat.metros[metro_id].name}"
        # "Washington" is also a state with its own 1st Avenue: the resolver asks which city.
        found = {i for i in resolve_place(nat, phrase).site_ids if nat.locations[i].metro_id == metro_id}
        assert found == ids, phrase


def test_street_type_words_are_never_evidence(nat):
    assert resolve_place(nat, "the one on Lincoln Avenue in Salt Lake").site_ids == ("loc_086",)
    assert resolve_place(nat, "The Avenues in Salt Lake City").site_ids == ("loc_087",)
    assert match_locations(nat, "the avenue", within=["loc_085", "loc_086", "loc_087"]) == []


def test_house_number_picks_the_site_on_a_shared_street(nat):
    assert [c.id for c in match_locations(nat, "3330 Market Street")] == ["loc_014"]
    # 3300 is far nearer 3330 than 1812: a misheard number, not another clinic.
    assert [c.id for c in match_locations(nat, "3300 Market Street", within=SAN_JOSE)] == ["loc_014"]
    # Halfway between the two: nothing to go on, both stay to be asked about.
    assert {c.id for c in match_locations(nat, "2571 Market Street", within=SAN_JOSE)} == {"loc_013", "loc_014"}
    # A number nobody on Market Street has, across many cities: no guess.
    assert len(match_locations(nat, "100 Market Street")) > 2


def test_a_street_the_city_lacks_falls_back_to_the_city(nat):
    m = resolve_place(nat, "Elm Street in San Jose")
    assert m.sites == () and [p.key for p in m.anchors] == ["metro:san-jose-ca"]


def test_shared_street_asks_with_house_numbers_and_takes_a_spoken_number(nat):
    plan = _say(nat, "sick visit", "the clinic on Market Street in San Jose")
    assert plan.say == "Is that Downtown at 1812 Market or Willow Glen at 3330 Market?"
    plan = _say(nat, "sick visit", "the clinic on Market Street in San Jose", {"location_phrase": "thirty three thirty"})
    assert plan.status == "offer" and {o.location_id for o in plan.offers} == {"loc_014"}


def test_a_street_said_as_an_ordinal_is_no_description_for_a_model(nat):
    """"second street in Orlando" found both clinics on 2nd Street; "second" must not then be read
    as a description that picks one of them (gpt-4o-mini put 0.82 on one)."""
    assert {"second", "2nd"} <= hear_place("second street in Orlando").street_words

    class Picky:
        def pick_site(self, phrase, type_id, candidate_ids):
            raise AssertionError("the site chooser has nothing to go on")

    plan = resolve(nat, merge(Request(), Update.from_args({**RETURNING, "service_phrase": "sick visit",
                                                           "location_phrase": "second street in Orlando"})),
                   MockAvailability(nat), site_chooser=Picky())
    assert (plan.status, plan.ask.options) == ("ask", ("loc_236", "loc_237"))


def test_shared_street_with_one_valid_site_books_there(nat):
    plan = _say(nat, "dermatology consultation", "Market Street, San Jose")
    assert plan.status == "offer" and {o.location_id for o in plan.offers} == {"loc_013"}


def test_refusal_names_the_street_clinics_never_none(nat):
    plan = _say(nat, "blood test", "the clinic on Market Street in San Jose")
    assert plan.say == ("We can't do a blood draw at Downtown or Willow Glen on Market Street. I can book "
                        "Dr. Jennifer Malabanan at Santa Clara or Dr. Ryan Wahlstrom at Downtown in Oakland. "
                        "Would either of those work?")
    plan = _say(nat, "blood test", "3330 Market Street, San Jose")
    assert plan.say.startswith("We can't do a blood draw at Willow Glen on Market Street. ")


def test_pinned_site_without_the_visit_anywhere_near_names_the_nearest(nat):
    plan = _say(nat, "x-ray", "the one on Lincoln Avenue in Salt Lake")
    assert plan.refusal.code == "none_nearby" and plan.refusal.alternatives[0][2] == "loc_076"
    assert plan.say.startswith("We can't do an x-ray at Sugar House on Lincoln Avenue. ")


def test_location_refusal_template_formats_every_named_site(nat):
    say = T.say_refuse(nat, "location_type", type_id="appt_072", at=("loc_013", "loc_014"))
    assert say == "We can't do a blood draw at Downtown or Willow Glen."


def _case_sets():
    sf = [p for p in dev_case_files() if "national" not in p.name and "street" not in p.name]
    national = [p for p in dev_case_files() if p not in sf]
    return [(p, DATA / "catalog.json") for p in sf] + [(p, DATA / "national" / "catalog.json") for p in national]


@pytest.mark.parametrize("cases,catalog", _case_sets(), ids=lambda v: Path(v).name)
def test_no_eval_turn_speaks_a_python_none(cases, catalog):
    ix = CatalogIndex.load(catalog)
    for line in cases.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        req, av = Request(), MockAvailability(ix)
        for i, turn in enumerate(t for t in case["turns"] if "update" in t):
            args = {**case.get("patient", {}), **turn["update"]} if i == 0 else turn["update"]
            plan = resolve(ix, merge(req, Update.from_args(args)), av)
            req = plan.req
            assert "None" not in plan.say, (case["id"], plan.say)


def test_street_of_parses_catalog_addresses(nat):
    assert (street_of(nat.locations["loc_152"]).number, street_of(nat.locations["loc_152"]).words) == (
        3956, ("medical", "center"))
    assert T.street_label(nat, "loc_152") == "Medical Center Drive"
