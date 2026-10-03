"""National geography and descriptive answers must not silently change the caller's scope."""

from pathlib import Path
from dataclasses import replace

import pytest

from scheduling.availability import MockAvailability
from scheduling.catalog_index import CatalogIndex
from scheduling.choosers import CHOOSERS
from scheduling.decision import Gate
from scheduling.geo import haversine, resolve_place
from scheduling.names import match_providers
from scheduling.request import OfferRef, Request, Update, merge
from scheduling.resolver import FINAL_RING_MI, resolve

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def national():
    return CatalogIndex.load(ROOT / "backend/data/national/catalog.json")


@pytest.mark.parametrize("phrase", ["what's the closest city to Boulder", "which is the nearest town to Boulder",
                                   "could you tell me the closest city to Boulder"])
def test_question_scaffolding_preserves_the_place(national, phrase):
    baseline = resolve_place(national, "near Boulder")
    heard = resolve_place(national, phrase)
    # Boulder has no catalog point: preserve that absence instead of inventing Center City.
    assert heard == baseline
    assert not heard.sites


@pytest.mark.parametrize("answer,expected", [
    ("the one who speaks Arabic", {"prov_003"}),
    ("the nurse practitioner", {"prov_003"}),
    ("the MD", {"prov_002"}),
    ("the one at Mission Bay", {"prov_002"}),
    ("the one who speaks Finnish", None),
])
def test_attribute_answers_stay_with_the_asked_doctors(national, answer, expected):
    av = MockAvailability(national)
    first = resolve(national, merge(Request(), Update.from_args({
        "service_phrase": "sick visit", "provider_phrase": "Dr. Maria Garcia", "location_phrase": "San Francisco",
        "is_new": False, "has_referral": True})), av)
    assert first.ask.field == "provider"
    asked = set(first.ask.options)
    assert not match_providers(national, answer, first.ask.options)
    second = resolve(national, merge(first.req, Update.from_args({"provider_phrase": answer})), av)
    if expected is None:
        assert second.status == "ask"
        assert second.ask.field == "provider"
        assert set(second.ask.options) <= asked
    else:
        assert second.status == "offer"
        assert {o.provider_id for o in second.offers} == expected
        assert expected <= asked


@pytest.mark.parametrize("prefix", ["actually, Dr.", "instead", "rather", "wait, Dr."])
def test_correction_uses_the_first_name_next_to_the_surname(index, prefix):
    matches = match_providers(index, f"{prefix} Linda Ramirez")
    assert {c.id for c in matches} == {"prov_005", "prov_014"}
    assert matches[0].score == 1.0


def test_a_stale_distant_offer_cannot_be_confirmed(national):
    av = MockAvailability(national)
    req = merge(Request(), Update.from_args({"service_phrase": "flu shot", "location_phrase": "San Francisco",
                                           "is_new": False, "has_referral": True}))
    far = next(r for r in national.rows_by_type["appt_011"] if r.location.city == "Philadelphia")
    slot = av.find([far], req.time_pref, 1)[0]
    ref = OfferRef(1, slot.type_id, slot.provider_id, slot.location_id, slot.start.isoformat(), slot.duration_min)
    req = replace(req, offered=(ref,), pick=1, changed=())
    plan = resolve(national, req, av)
    assert plan.status != "confirm"
    assert any("beyond the place anchor" in note for note in plan.notes)


@pytest.mark.parametrize("mode", tuple(CHOOSERS))
def test_national_offers_never_leave_the_50_mile_anchor(national, mode):
    """Enumerate every catalog site and metro in all four real chooser modes, without network.
    Include a service available locally and a rarer service that exercises widening/refusal.
    Check against the original place, independently of the resolver's derived request slots.
    """
    entry = CHOOSERS[mode]
    settings = {"mode": "cache", "cache_path": ROOT / f"eval/.{mode}_cache.json"} if mode in {"jev", "openai"} else {}
    client = entry.make_client(**settings)
    hooks = entry.hooks_for(national, client, Gate()) if client else {}
    av = MockAvailability(national)
    phrases = {f"{m.name}, {m.state}" for m in national.metros.values()}
    phrases |= {l.address for l in national.locations.values()}
    offered = 0
    try:
        for phrase in sorted(phrases):
            place = resolve_place(national, phrase)
            anchors = list(place.anchors) + [national.gazetteer.sites[c.id] for c in place.sites]
            anchors = [a for a in anchors if a.lat is not None and a.lon is not None]
            for service in ("flu shot", "MRI knee"):
                req = merge(Request(), Update.from_args({"service_phrase": service, "location_phrase": phrase,
                                                        "is_new": False, "has_referral": True}))
                plan = resolve(national, req, av, **hooks)
                if plan.offers:
                    assert anchors, phrase
                for offer in plan.offers:
                    loc = national.locations[offer.location_id]
                    assert min(haversine(a.lat, a.lon, loc.lat, loc.lon) for a in anchors) <= FINAL_RING_MI, (mode, phrase, offer)
                    offered += 1
        assert offered > 100, "The invariant must exercise actual offers, not only asks."
    finally:
        if client:
            client.close()
