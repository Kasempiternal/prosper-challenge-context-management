import json
import math

import pytest

from scheduling.catalog_index import CatalogIndex, build_index
from scheduling.geo import haversine, names_own_area, nearby, resolve_place
from scheduling.names import match_locations
from eval_cases import dev_case_files



def _loc(lid, name, metro, nbhd, zip_, lat, lon, state, city):
    return {"id": lid, "name": name, "address": "1 Main St", "city": city, "hours": "Mon-Fri 8:00-17:00",
            "capabilities": [], "metro_id": metro, "neighborhood": nbhd, "zip": zip_, "state": state,
            "lat": lat, "lon": lon}


METROS = [
    {"id": "austin-tx", "name": "Austin", "state": "TX", "aliases": ["ATX"], "lat": 30.27, "lon": -97.74},
    {"id": "dallas-tx", "name": "Dallas", "state": "TX", "aliases": [], "lat": 32.78, "lon": -96.80},
    {"id": "portland-or", "name": "Portland", "state": "OR", "aliases": [], "lat": 45.52, "lon": -122.68},
    {"id": "portland-me", "name": "Portland", "state": "ME", "aliases": [], "lat": 43.66, "lon": -70.26},
    {"id": "new-york-ny", "name": "New York", "state": "NY", "aliases": ["NYC"], "lat": 40.71, "lon": -74.01},
]
LOCATIONS = [
    _loc("loc_a1", "Riverside Health Center", "austin-tx", "Riverside", "78741", 30.24, -97.72, "TX", "Austin"),
    _loc("loc_a2", "Downtown Health Center", "austin-tx", "Downtown", "78701", 30.27, -97.74, "TX", "Austin"),
    _loc("loc_a3", "Mueller Clinic", "austin-tx", "Hyde Park", "78751", 30.30, -97.73, "TX", "Austin"),
    _loc("loc_a4", "Cedar Park Health Center", "austin-tx", "Cedar Park", "78664", 30.51, -97.68, "TX", "Round Rock"),
    _loc("loc_d1", "Downtown Health Center", "dallas-tx", "Downtown", "75201", 32.78, -96.80, "TX", "Dallas"),
    _loc("loc_d2", "Oak Lawn Health Center", "dallas-tx", "Oak Lawn", "75219", 32.81, -96.81, "TX", "Dallas"),
    _loc("loc_p1", "Pearl District Health Center", "portland-or", "Pearl District", "97209", 45.53, -122.68,
         "OR", "Portland"),
    _loc("loc_m1", "Old Port Health Center", "portland-me", "Old Port", "04101", 43.66, -70.25, "ME", "Portland"),
    _loc("loc_n1", "Harlem Health Center", "new-york-ny", "Harlem", "10027", 40.81, -73.95, "NY", "New York"),
]
TYPES = [{"id": "appt_000", "name": "New Patient Visit", "specialty": "General", "duration_min": 30,
          "requires_referral": False, "new_patients_allowed": True}]


def national_raw(**overrides) -> dict:
    providers = [
        {"id": "prov_0", "name": "Dr. Maria Garcia", "specialty": "General", "location_ids": ["loc_a1", "loc_a2"],
         "accepting_new_patients": True, "appointment_type_ids": ["appt_000"]},
        {"id": "prov_1", "name": "Dr. Maria Garcia", "specialty": "General", "location_ids": ["loc_d1"],
         "accepting_new_patients": True, "appointment_type_ids": ["appt_000"]},
        {"id": "prov_2", "name": "Dr. Ken Ito", "specialty": "General",
         "location_ids": [l["id"] for l in LOCATIONS if l["metro_id"] != "dallas-tx"],
         "accepting_new_patients": True, "appointment_type_ids": ["appt_000"]},
    ]
    raw = {"metros": json.loads(json.dumps(METROS)), "locations": json.loads(json.dumps(LOCATIONS)),
           "providers": providers, "appointment_types": TYPES}
    raw.update(overrides)
    return raw


@pytest.fixture(scope="module")
def nat() -> CatalogIndex:
    return build_index(national_raw(), {"aliases": {}})


def _anchors(m):
    return [p.key for p in m.anchors]


@pytest.mark.parametrize("phrase,anchors", [
    ("Austin", ["metro:austin-tx"]),
    ("I'm in Austin", ["metro:austin-tx"]),
    ("around atx", ["metro:austin-tx"]),
    ("Austen", ["metro:austin-tx"]),                                  # misheard city name
    ("the Austin area", ["metro:austin-tx"]),
    ("Portland", ["metro:portland-me", "metro:portland-or"]),         # two cities: the resolver asks
    ("Portland, Oregon", ["metro:portland-or"]),
    ("Portland ME", ["metro:portland-me"]),
    ("Austin, TX", ["metro:austin-tx"]),
    ("I'm in New York", ["metro:new-york-ny"]),                       # the city, not the state
    ("NYC", ["metro:new-york-ny"]),
    ("Texas", ["state:TX"]),
    ("Montana", ["state:MT"]),                                        # no clinics there, still a place
    ("Hyde Park", ["nbhd:austin-tx:hyde park"]),
    ("Round Rock", ["nbhd:austin-tx:round rock"]),
    ("Hyde Park in Austin", ["nbhd:austin-tx:hyde park"]),
    ("near Riverside", ["site:loc_a1"]),                              # near a clinic = the area around it
    ("close to Riverside", ["site:loc_a1"]),
    ("78701", ["zip:78701"]),
    ("78799", ["zip3:787"]),                                          # unknown ZIP, known prefix
])
def test_area_anchors(nat, phrase, anchors):
    m = resolve_place(nat, phrase)
    assert (m.site_ids, _anchors(m)) == ((), anchors)


@pytest.mark.parametrize("phrase,sites", [
    ("Riverside", ("loc_a1",)),
    ("the Riverside clinic", ("loc_a1",)),
    ("Riverside clinic in Austin", ("loc_a1",)),
    ("Downtown", ("loc_a2", "loc_d1")),                               # same name in two cities
    ("Downtown in Dallas", ("loc_d1",)),
    ("downtown Dallas", ("loc_d1",)),
    ("Downtown, Austin", ("loc_a2",)),
    ("Pearl District", ("loc_p1",)),
    ("Oak Lawn in Dallas", ("loc_d2",)),
])
def test_site_phrases(nat, phrase, sites):
    m = resolve_place(nat, phrase)
    assert (m.site_ids, m.anchors) == (sites, ())


@pytest.mark.parametrize("phrase", ["", None, "Narnia", "99999", "in", "near me", "me"])
def test_unknown_places_match_nothing(nat, phrase):
    m = resolve_place(nat, phrase)
    assert (m.sites, m.anchors) == ((), ())


def test_site_missing_from_named_city_falls_back_to_the_city(nat):
    m = resolve_place(nat, "Pearl District in Austin")
    assert (m.site_ids, _anchors(m)) == ((), ["metro:austin-tx"])


def test_match_metro_ids(nat):
    assert resolve_place(nat, "Downtown").metro_ids(nat) == {"austin-tx", "dallas-tx"}
    assert resolve_place(nat, "Texas").metro_ids(nat) == {"austin-tx", "dallas-tx"}
    assert resolve_place(nat, "Montana").metro_ids(nat) == set()


def test_haversine_known_distance():
    # San Francisco City Hall -> Los Angeles City Hall is about 347 miles.
    assert haversine(37.7793, -122.4193, 34.0537, -118.2428) == pytest.approx(347.4, abs=1.0)
    assert haversine(30.0, -97.0, 30.0, -97.0) == 0.0


def test_nearby_rings(nat):
    riverside = nat.gazetteer.sites["loc_a1"]
    assert [lid for lid, _ in nearby(nat, riverside)] == ["loc_a1", "loc_a2", "loc_a3"]
    ring = nearby(nat, riverside, 25)
    assert [lid for lid, _ in ring] == ["loc_a1", "loc_a2", "loc_a3", "loc_a4"]
    assert [d for _, d in ring] == sorted(d for _, d in ring)
    montana = resolve_place(nat, "Montana").anchors[0]
    assert nearby(nat, montana) == ()
    assert nearby(nat, montana, math.inf, within=["loc_a1", "loc_p1"])[0][0] == "loc_p1"


def test_new_indexes_on_national(nat):
    assert nat.multi_metro and nat.has_geo
    assert nat.locs_by_metro["austin-tx"] == ("loc_a1", "loc_a2", "loc_a3", "loc_a4")
    assert nat.metros_by_type["appt_000"] == {"austin-tx", "dallas-tx", "portland-or", "portland-me", "new-york-ny"}
    assert [r.location.id for r in nat.rows_by_provider["prov_1"]] == ["loc_d1"]
    assert [r.provider.id for r in nat.rows_by_type_loc[("appt_000", "loc_a2")]] == ["prov_0", "prov_2"]
    assert nat.locations["loc_a1"].zip == "78741" and nat.locations["loc_a1"].metro_id == "austin-tx"


def _sf_location_phrases() -> set[str]:
    out = {"Mission Bae", "down town", "the one on Geary", "near Mission Bay", "Narnia", "Downtown, SF",
           "I'm in San Francisco", "California", "94103"}
    for path in dev_case_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            for turn in json.loads(line)["turns"]:
                if turn.get("update", {}).get("location_phrase"):
                    out.add(turn["update"]["location_phrase"])
    return out


def test_sf_has_no_geography_and_places_are_site_phrases(index):
    assert list(index.metros) == ["_"] and not index.multi_metro and not index.has_geo
    assert not index.gazetteer.areas and not index.gazetteer.sites
    phrases = _sf_location_phrases()
    assert len(phrases) > 15
    for phrase in phrases:
        m = resolve_place(index, phrase)
        assert m.anchors == ()
        assert list(m.sites) == match_locations(index, phrase), phrase


def test_load_reads_geo_from_disk(tmp_path):
    (tmp_path / "catalog.json").write_text(json.dumps(national_raw()), encoding="utf-8")
    (tmp_path / "aliases.json").write_text(json.dumps({"aliases": {}}), encoding="utf-8")
    ix = CatalogIndex.load(tmp_path / "catalog.json")
    assert resolve_place(ix, "Portland, Oregon").anchors[0].key == "metro:portland-or"


@pytest.mark.parametrize("phrase,anchors", [
    ("my zip code is 78751", ["zip:78751"]),
    ("the postal code's 78741", ["zip:78741"]),
    ("zip 78999", ["zip3:787"]),            # 789 has no clinic; 787 is the nearest area in region 78
])
def test_a_zip_inside_a_sentence_is_the_zip(nat, phrase, anchors):
    m = resolve_place(nat, phrase)
    assert (m.site_ids, _anchors(m)) == ((), anchors)


def test_a_zip_anchor_is_said_as_the_callers_zip(nat):
    assert [p.label for p in resolve_place(nat, "it's 78999").anchors] == ["78999"]


def test_a_misheard_city_beats_clinics_named_after_it():
    extra = _loc("loc_d3", "Dallas Uptown Clinic", "dallas-tx", "Uptown", "75204", 32.80, -96.80, "TX", "Dallas")
    ix = build_index(national_raw(locations=LOCATIONS + [extra]), {"aliases": {}})
    assert _anchors(resolve_place(ix, "Dalas")) == ["metro:dallas-tx"]
    assert resolve_place(ix, "Dallas Uptown").site_ids == ("loc_d3",)


def test_names_own_area(nat):
    assert names_own_area(nat, "Cedar Park", "loc_a4")             # the clinic and the suburb it is in
    assert names_own_area(nat, "I'm in Riverside", "loc_a1")
    assert not names_own_area(nat, "Cedar Park Health Center", "loc_a4")
    assert not names_own_area(nat, "Mueller", "loc_a3")            # Mueller Clinic is in Hyde Park


def test_a_misheard_city_beats_a_clinic_sharing_only_some_of_its_words():
    """"San Antonyo" said "San" literally, which South San Antonio's name also has: the city wins."""
    metros = METROS + [{"id": "san-antonio-tx", "name": "San Antonio", "state": "TX", "aliases": [],
                        "lat": 29.42, "lon": -98.49}]
    extra = _loc("loc_s1", "South San Antonio Health Center", "san-antonio-tx", "South Side", "78221", 29.36, -98.50,
                 "TX", "San Antonio")
    ix = build_index(national_raw(metros=metros, locations=LOCATIONS + [extra]), {"aliases": {}})
    assert _anchors(resolve_place(ix, "San Antonyo")) == ["metro:san-antonio-tx"]
    assert resolve_place(ix, "South San Antonio").site_ids == ("loc_s1",)


def test_a_clinic_named_outright_beats_one_that_only_sounds_alike():
    """"near The Hill": Hialeah shares The Hill's sound key, but only The Hill was said."""
    metros = METROS + [{"id": "miami-fl", "name": "Miami", "state": "FL", "aliases": [], "lat": 25.76, "lon": -80.19},
                       {"id": "st-louis-mo", "name": "St. Louis", "state": "MO", "aliases": [], "lat": 38.63,
                        "lon": -90.20}]
    extra = [_loc("loc_h1", "The Hill Family Clinic", "st-louis-mo", "The Hill", "63110", 38.62, -90.28, "MO",
                  "St. Louis"),
             _loc("loc_h2", "Hialeah Health Center", "miami-fl", "Hialeah", "33010", 25.86, -80.28, "FL", "Hialeah")]
    ix = build_index(national_raw(metros=metros, locations=LOCATIONS + extra), {"aliases": {}})
    assert [c.id for c in match_locations(ix, "the hill")] == ["loc_h1"]
    assert _anchors(resolve_place(ix, "I'm near The Hill")) == ["site:loc_h1"]
    assert [c.id for c in match_locations(ix, "Hialeah")] == ["loc_h2"]
