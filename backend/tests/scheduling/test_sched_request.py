import json
from dataclasses import replace

import pytest

from scheduling.policy import Patient
from scheduling.request import OfferRef, PendingAsk, Rejected, Request, TimePref, Update, merge


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


OFFERS = (OfferRef(1, "appt_020", "prov_046", "loc_004", "2026-10-08T08:00:00", 40),
          OfferRef(2, "appt_020", "prov_046", "loc_005", "2026-10-09T08:00:00", 40),
          OfferRef(3, "appt_020", "prov_000", "loc_004", "2026-10-12T08:00:00", 40))
SLOTS = ("appt_020|prov_046|loc_004|20261008T0800", "appt_020|prov_046|loc_005|20261009T0800",
         "appt_020|prov_000|loc_004|20261012T0800")


def _offered(**kw):
    req = merge(Request(), Update.from_args({"service_phrase": "physical", "location_phrase": "Downtown"}))
    return replace(req, offered=OFFERS, **kw)


@pytest.mark.parametrize("kind, field, ids", [("location", "locations", ("loc_004", "loc_005")),
                                              ("provider", "providers", ("prov_046", "prov_000")),
                                              ("time", "slots", SLOTS)])
def test_reject_turns_down_the_offered_options_of_that_kind(kind, field, ids):
    req = _offered()
    out = merge(req, Update.from_args({"reject": [kind]}))
    assert getattr(out.rejected, field) == ids
    assert out.rejected == Rejected(**{field: ids})
    assert (out.offered, out.pick, out.pending_ask) == ((), None, None)
    assert (out.service, out.location) == (req.service, req.location)


def test_reject_several_kinds_and_again_accumulates():
    out = merge(_offered(), Update.from_args({"reject": ["provider", "location"]}))
    assert out.rejected == Rejected(locations=("loc_004", "loc_005"), providers=("prov_046", "prov_000"))
    more = (OfferRef(1, "appt_020", "prov_009", "loc_006", "2026-10-08T09:00:00", 40),)
    again = merge(replace(out, offered=more), Update.from_args({"reject": ["provider"]}))
    assert again.rejected.providers == ("prov_046", "prov_000", "prov_009")
    assert again.rejected.locations == ("loc_004", "loc_005")


def test_reject_with_nothing_offered_changes_nothing():
    req = merge(Request(), Update.from_args({"service_phrase": "physical"}))
    assert merge(req, Update.from_args({"reject": ["location", "time"]})).rejected == Rejected()


def test_rejecting_the_doctor_named_withdraws_the_name():
    req = _offered(provider=merge(Request(), Update.from_args({"provider_phrase": "Dr. Chen"})).provider)
    out = merge(req, Update.from_args({"reject": ["provider"]}))
    assert out.provider.heard is None and out.rejected.providers == ("prov_046", "prov_000")


ALL_REJECTED = Rejected(locations=("loc_004",), providers=("prov_046",), slots=SLOTS[:1])


@pytest.mark.parametrize("args, kept", [
    ({"service_phrase": "flu shot"}, Rejected()),
    ({"provider_phrase": "Dr. Patel"}, Rejected(locations=("loc_004",))),
    ({"location_phrase": "Sunset"}, Rejected(providers=("prov_046",))),
    ({"time_pref": {"days": ["friday"]}}, Rejected(locations=("loc_004",), providers=("prov_046",))),
    ({"is_new": True}, ALL_REJECTED),
])
def test_a_new_choice_forgets_what_was_turned_down_of_its_kind(args, kept):
    assert merge(Request(rejected=ALL_REJECTED), Update.from_args(args)).rejected == kept


def test_reject_with_a_new_place_in_the_same_turn_keeps_only_the_place():
    out = merge(_offered(), Update.from_args({"reject": ["location", "time"], "location_phrase": "Sunset"}))
    assert out.rejected == Rejected(slots=SLOTS)


def test_reject_must_name_known_kinds():
    with pytest.raises(ValueError, match="cannot reject"):
        Update.from_args({"reject": ["clinic"]})
    with pytest.raises(ValueError, match="reject must be a list"):
        Update.from_args({"reject": "location"})


def test_rejected_round_trips_through_the_flow_state():
    req = merge(_offered(), Update.from_args({"reject": ["location", "provider", "time"]}))
    restored = Request.from_dict(json.loads(json.dumps(req.to_dict())))
    assert restored == req and restored.rejected.slots == SLOTS
    assert Request.from_dict({}).rejected == Rejected()


def test_pick_with_a_change_keeps_offers_for_the_resolver_to_judge():
    offered = (OfferRef(1, "appt_020", "prov_046", "loc_004", "2026-10-08T08:00:00", 40),)
    merged = merge(Request(offered=offered), Update.from_args({"pick_offer": 1, "location_phrase": "Downtown"}))
    assert (merged.offered, merged.pick, merged.changed) == (offered, 1, ("location",))
