from datetime import datetime, timedelta

from scheduling.availability import DEMO_NOW, MIN_LEAD, MockAvailability, Slot, spread
from scheduling.request import TimePref

MONDAY = DEMO_NOW.date() - timedelta(days=DEMO_NOW.weekday())


def _emily_consult_rows(index):
    return [r for r in index.rows_by_type["appt_020"] if r.provider.id == "prov_046"]


def test_find_is_deterministic_soonest_first_one_offer_per_day(index):
    a = MockAvailability(index).find(_emily_consult_rows(index), TimePref())
    b = MockAvailability(index).find(_emily_consult_rows(index), TimePref())
    assert a == b and len(a) == 3
    assert [s.start for s in a] == sorted(s.start for s in a)
    assert len({s.start.date() for s in a}) == 3


def _slot(provider, location, day, hour, minute=0):
    return Slot("appt_002", provider, location, datetime(2026, 10, day, hour, minute), 30)


def test_spread_one_offer_per_day_before_reusing_a_day():
    days = [[_slot("p1", "l1", 8, 10), _slot("p1", "l1", 8, 10, 30)], [_slot("p1", "l1", 9, 9)],
            [_slot("p1", "l1", 12, 14)]]
    assert [s.start.day for s in spread(days, 3)] == [8, 9, 12]


def test_spread_reuses_a_day_only_three_hours_apart():
    one_day = [[_slot("p1", "l1", 8, 10), _slot("p1", "l1", 8, 11), _slot("p1", "l1", 8, 13),
                _slot("p1", "l1", 8, 16)]]
    assert [s.start.hour for s in spread(one_day, 3)] == [10, 13, 16]


def test_spread_with_several_doctors_reuses_a_day_only_at_another_site():
    one_day = [[_slot("p1", "l1", 8, 10), _slot("p2", "l1", 8, 13, 30), _slot("p3", "l2", 8, 14)]]
    assert [(s.provider_id, s.location_id) for s in spread(one_day, 3)] == [("p1", "l1"), ("p3", "l2")]


def test_no_slot_inside_the_minimum_lead_time(index):
    av = MockAvailability(index)
    soonest = av.find(index.rows_by_type["appt_002"], TimePref(), limit=1)[0]
    assert soonest.start >= DEMO_NOW + MIN_LEAD
    assert soonest.start.date() == DEMO_NOW.date()


def test_slots_respect_hours_duration_and_now(index):
    av = MockAvailability(index)
    for s in av.find(index.rows_by_type["appt_002"], TimePref(), limit=3):
        assert s.start > DEMO_NOW
        assert s.start.weekday() < 5
        assert 8 * 60 <= s.start.hour * 60 + s.start.minute
        assert s.end <= s.start.replace(hour=17, minute=0)
        assert s.duration_min == 30


def test_provider_never_at_two_sites_on_one_day(index):
    av = MockAvailability(index)
    for prov in index.providers.values():
        for week in range(3):
            days = av.clinic_days(prov, MONDAY + timedelta(weeks=week))
            flat = [d for ds in days.values() for d in ds]
            assert len(flat) == len(set(flat))


def test_time_preferences(index):
    av = MockAvailability(index)
    slots = av.find(index.rows_by_type["appt_002"], TimePref(days=("thursday",), part_of_day="afternoon"))
    assert slots and all(s.start.weekday() == 3 and s.start.hour >= 12 for s in slots)
    later = av.find(index.rows_by_type["appt_002"], TimePref(not_before="2026-10-19"))
    assert all(s.start >= datetime(2026, 10, 19) for s in later)


def test_hold_is_idempotent_and_blocks_overlaps(index):
    av = MockAvailability(index)
    slot = av.find(_emily_consult_rows(index), TimePref())[0]
    first, again = av.hold(slot), av.hold(slot)
    assert first.ok and first == again and first.ref.startswith("H-")
    assert not av.is_open(slot)
    assert slot not in av.find(_emily_consult_rows(index), TimePref())
    av.release(slot.id)
    assert av.is_open(slot)
