import json

import pytest

from scheduling.policy import Patient
from scheduling.request import OfferRef, PendingAsk, Request, TimePref, Update, merge


def test_update_rejects_bad_args():
    with pytest.raises(ValueError, match="unknown update fields"):
        Update.from_args({"doctor": "Chen"})
    with pytest.raises(ValueError, match="pick_offer"):
        Update.from_args({"pick_offer": 4})
    with pytest.raises(ValueError, match="is_new"):
        Update.from_args({"is_new": "yes"})
    with pytest.raises(ValueError, match="weekday"):
        Update.from_args({"time_pref": {"days": ["someday"]}})


def test_merge_is_pure_and_records_what_changed():
    r0 = Request()
    r1 = merge(r0, Update.from_args({"service_phrase": "checkup", "is_new": True}))
    assert r0 == Request()
    assert r1.service.heard == "checkup" and r1.service.turn == 1
    assert r1.patient == Patient(is_new=True, has_referral=None)
    r2 = merge(r1, Update.from_args({"has_referral": False}))
    assert r2.patient == Patient(is_new=True, has_referral=False)
    assert r1.changed == ("service",)
    assert r2.changed == ()


def test_mind_change_drops_offers_and_pick():
    offered = (OfferRef(1, "appt_020", "prov_046", "loc_004", "2026-10-06T09:30:00", 40),)
    req = Request(offered=offered, pick=1, turn=3)
    changed = merge(req, Update.from_args({"service_phrase": "annual physical"}))
    assert changed.offered == () and changed.pick is None
    assert changed.service.heard == "annual physical" and changed.service.turn == 4


def test_flag_answer_keeps_offers_and_pick_offer_sets_pick():
    offered = (OfferRef(1, "appt_020", "prov_046", "loc_004", "2026-10-06T09:30:00", 40),)
    req = Request(offered=offered)
    assert merge(req, Update.from_args({"is_new": False})).offered == offered
    assert merge(req, Update.from_args({"pick_offer": 1})).pick == 1


def test_answer_to_pending_ask_is_scoped_to_its_options():
    req = Request(pending_ask=PendingAsk("provider", ("prov_000", "prov_046")))
    merged = merge(req, Update.from_args({"provider_phrase": "David"}))
    assert merged.provider.within == ("prov_000", "prov_046")
    unrelated = merge(req, Update.from_args({"location_phrase": "Downtown"}))
    assert unrelated.location.within == ()


def test_clear_resets_slot():
    req = merge(Request(), Update.from_args({"provider_phrase": "Dr. Chen", "time_pref": {"days": ["tuesday"]}}))
    cleared = merge(req, Update.from_args({"clear": ["provider", "time_pref"]}))
    assert cleared.provider.heard is None and cleared.time_pref == TimePref()


def test_request_json_round_trip():
    req = merge(Request(), Update.from_args({
        "service_phrase": "knee mri", "provider_phrase": "Dr. Nwin", "is_new": False,
        "time_pref": {"days": ["tuesday"], "part_of_day": "morning"}}))
    req = Request(**{**req.__dict__, "offered": (OfferRef(1, "appt_065", "prov_015", "loc_005",
                                                           "2026-10-06T09:00:00", 45),),
                     "pending_ask": PendingAsk("location", ("loc_004",))})
    restored = Request.from_dict(json.loads(json.dumps(req.to_dict())))
    assert restored == req


@pytest.mark.parametrize("bad", ["next week", 20261012, "2026-13-01"])
def test_not_before_must_be_an_iso_date(bad):
    with pytest.raises(ValueError, match="not_before"):
        Update.from_args({"time_pref": {"not_before": bad}})


def test_not_before_is_kept_as_iso_date():
    assert Update.from_args({"time_pref": {"not_before": "2026-10-19"}}).time_pref.not_before == "2026-10-19"


def test_pick_with_a_change_keeps_offers_for_the_resolver_to_judge():
    offered = (OfferRef(1, "appt_020", "prov_046", "loc_004", "2026-10-08T08:00:00", 40),)
    merged = merge(Request(offered=offered), Update.from_args({"pick_offer": 1, "location_phrase": "Downtown"}))
    assert (merged.offered, merged.pick, merged.changed) == (offered, 1, ("location",))
