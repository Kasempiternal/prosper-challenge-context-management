"""Real US cities the national catalog has no clinic in, said alone, as "City, ST" and as "City,
State": the resolver asks, refuses naming a clinic and its city with a true distance (if any), or
offers clinics near the real city. A silent offer far from where the caller is never passes."""

import re
from pathlib import Path

import pytest

from scheduling import templates as T
from scheduling.availability import MockAvailability
from scheduling.catalog_index import CatalogIndex
from scheduling.geo import US_STATES, haversine
from scheduling.request import Request, Update, merge
from scheduling.resolver import resolve

DATA = Path(__file__).resolve().parents[2] / "data"
OFFER_WITHIN_MI = 50.0
DISTANCE_TOLERANCE = 0.15
STATE_NAMES = {abbrev: name for abbrev, name, _, _ in US_STATES}

# (city, state, latitude, longitude) of each city's centre.
CITIES = [
    ("Trenton", "NJ", 40.2171, -74.7429), ("Newark", "NJ", 40.7357, -74.1724),
    ("Richmond", "VA", 37.5407, -77.4360), ("Wichita", "KS", 37.6872, -97.3301),
    ("Boise", "ID", 43.6150, -116.2023), ("Tulsa", "OK", 36.1540, -95.9928),
    ("Omaha", "NE", 41.2565, -95.9345), ("Fresno", "CA", 36.7378, -119.7871),
    ("El Paso", "TX", 31.7619, -106.4850), ("Spokane", "WA", 47.6588, -117.4260),
    ("Akron", "OH", 41.0814, -81.5190), ("Dayton", "OH", 39.7589, -84.1916),
    ("Toledo", "OH", 41.6528, -83.5379), ("Buffalo", "NY", 42.8864, -78.8784),
    ("Rochester", "NY", 43.1566, -77.6088), ("Syracuse", "NY", 43.0481, -76.1474),
    ("Albany", "NY", 42.6526, -73.7562), ("Hartford", "CT", 41.7658, -72.6734),
    ("Providence", "RI", 41.8240, -71.4128), ("Worcester", "MA", 42.2626, -71.8023),
    ("Springfield", "IL", 39.7817, -89.6501), ("Lansing", "MI", 42.7325, -84.5555),
    ("Madison", "WI", 43.0731, -89.4012), ("Milwaukee", "WI", 43.0389, -87.9065),
    ("Des Moines", "IA", 41.5868, -93.6250), ("Lincoln", "NE", 40.8136, -96.7026),
    ("Billings", "MT", 45.7833, -108.5007), ("Reno", "NV", 39.5296, -119.8138),
    ("Tucson", "AZ", 32.2226, -110.9747), ("Mesa", "AZ", 33.4152, -111.8315),
    ("Anchorage", "AK", 61.2181, -149.9003), ("Honolulu", "HI", 21.3069, -157.8583),
    ("Jackson", "MS", 32.2988, -90.1848), ("Memphis", "TN", 35.1495, -90.0490),
    ("Knoxville", "TN", 35.9606, -83.9207), ("Louisville", "KY", 38.2527, -85.7585),
    ("Lexington", "KY", 38.0406, -84.5037), ("Charleston", "SC", 32.7765, -79.9311),
    ("Savannah", "GA", 32.0809, -81.0912), ("Mobile", "AL", 30.6954, -88.0399),
    ("Little Rock", "AR", 34.7465, -92.2896), ("Shreveport", "LA", 32.5252, -93.7502),
    ("Baton Rouge", "LA", 30.4515, -91.1871), ("Corpus Christi", "TX", 27.8006, -97.3964),
    ("Laredo", "TX", 27.5306, -99.4803), ("Lubbock", "TX", 33.5779, -101.8552),
    ("Amarillo", "TX", 35.2220, -101.8313), ("Greenville", "SC", 34.8526, -82.3940),
    ("Chattanooga", "TN", 35.0456, -85.3097), ("Fort Wayne", "IN", 41.0793, -85.1394),
]
PHRASES = [(phrase, lat, lon) for city, st, lat, lon in CITIES
           for phrase in (city, f"{city}, {st}", f"{city}, {STATE_NAMES[st]}")]


@pytest.fixture(scope="module")
def nat() -> CatalogIndex:
    return CatalogIndex.load(DATA / "national" / "catalog.json")


def _resolve(ix, place):
    req = merge(Request(), Update.from_args({"is_new": False, "has_referral": True, "service_phrase": "flu shot",
                                             "location_phrase": place}))
    return resolve(ix, req, MockAvailability(ix))


def _unsafe(ix, plan, lat, lon) -> str | None:
    """Why the plan could mislead a caller at (lat, lon); None when it is safe."""
    def miles_to(lid):
        loc = ix.locations[lid]
        return haversine(lat, lon, loc.lat, loc.lon)

    if plan.status == "ask":
        return None
    if plan.status == "offer":
        far = {o.location_id: round(miles_to(o.location_id)) for o in plan.offers
               if miles_to(o.location_id) > OFFER_WITHIN_MI}
        return f"offered {far} miles away" if far else None
    if plan.status == "refuse" and plan.refusal.alternatives:
        lid = plan.refusal.alternatives[0][2]
        if ix.locations[lid].short_name not in plan.say or T.city_of(ix, lid) not in plan.say:
            return "the refusal does not name its clinic and city"
        true = miles_to(lid)
        wrong = [int(n) for n in re.findall(r"(\d+) miles?", plan.say)
                 if abs(int(n) - true) > DISTANCE_TOLERANCE * true]
        return f"said {wrong} miles; it is {true:.0f}" if wrong else None
    return f"{plan.status} {plan.refusal.code if plan.refusal else ''} names no clinic"


@pytest.mark.parametrize("place, lat, lon", PHRASES, ids=[p for p, _, _ in PHRASES])
def test_a_city_we_have_no_clinic_in_is_never_silently_placed_far_away(nat, place, lat, lon):
    plan = _resolve(nat, place)
    assert _unsafe(nat, plan, lat, lon) is None, f"{_unsafe(nat, plan, lat, lon)}: {plan.say}"
