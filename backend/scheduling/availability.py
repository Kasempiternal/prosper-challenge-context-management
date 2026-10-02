"""Availability interface and a deterministic mock. A real EHR adapter replaces the mock."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Protocol

from .catalog_index import BookableRow, CatalogIndex, Provider
from .request import WEEKDAY_NAMES, TimePref

DEMO_NOW = datetime(2026, 10, 7, 9, 0)  # a Wednesday morning, so demos read "Thursday at 9:30"
HORIZON_DAYS = 21
OPEN_RATE = 0.55
MIN_LEAD = timedelta(hours=2)
SAME_DAY_GAP = timedelta(hours=3)


@dataclass(frozen=True)
class Slot:
    type_id: str
    provider_id: str
    location_id: str
    start: datetime
    duration_min: int

    @property
    def id(self) -> str:
        return f"{self.type_id}|{self.provider_id}|{self.location_id}|{self.start:%Y%m%dT%H%M}"

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_min)


@dataclass(frozen=True)
class Hold:
    ok: bool
    slot: Slot
    ref: str | None = None


class Availability(Protocol):
    now: datetime

    def find(self, rows: Iterable[BookableRow], time_pref: TimePref, limit: int = 3) -> list[Slot]: ...

    def is_open(self, slot: Slot) -> bool: ...

    def hold(self, slot: Slot) -> Hold: ...

    def release(self, slot_id: str) -> None: ...


def _unit(*parts: object) -> float:
    digest = hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


def _in_part(slot: Slot, part_of_day: str | None) -> bool:
    if part_of_day == "morning":
        return slot.start.hour < 12
    if part_of_day == "afternoon":
        return slot.start.hour >= 12
    return True


def spread(days: list[list[Slot]], limit: int) -> list[Slot]:
    """Pick `limit` offers a caller can tell apart from per-day lists of open slots (soonest
    first). The soonest slot of each day comes first, so three offers are three days. A day is
    reused only for a time at least SAME_DAY_GAP away from that day's other offers, and, when
    several doctors are in play, at another location: three doctors "today at 8:10" is not a
    choice. Returned soonest first."""
    picked = [d[0] for d in days[:limit]]
    several = len({s.provider_id for d in days for s in d}) > 1
    for todays in days:
        for s in todays:
            if len(picked) == limit:
                break
            same_day = [p for p in picked if p.start.date() == s.start.date()]
            if all(abs(s.start - p.start) >= SAME_DAY_GAP and not (several and p.location_id == s.location_id)
                   for p in same_day):
                picked.append(s)
    return sorted(picked, key=lambda s: s.start)


class MockAvailability:
    """Seeded by (provider, week): each week a provider spends 2-3 days at each of their sites,
    never two sites on one day, so the site genuinely changes which times are offered.
    Providers at 3-4 sites cannot fit 2 days everywhere in a 5-day week; later sites (rotated
    weekly) get what is left, possibly nothing that week."""

    def __init__(self, index: CatalogIndex, now: datetime = DEMO_NOW, horizon_days: int = HORIZON_DAYS,
                 open_rate: float = OPEN_RATE, seed: str = "prosper"):
        self.index = index
        self.now = now
        self.horizon_days = horizon_days
        self.open_rate = open_rate
        self.seed = seed
        self._holds: dict[str, Hold] = {}
        self._clinic_cache: dict[tuple[str, date], dict[str, frozenset[int]]] = {}

    def clinic_days(self, provider: Provider, week_start: date) -> dict[str, frozenset[int]]:
        key = (provider.id, week_start)
        if key not in self._clinic_cache:
            rng = random.Random(f"{self.seed}|{provider.id}|{week_start.isoformat()}")
            free = [0, 1, 2, 3, 4]
            rng.shuffle(free)
            locs = list(provider.location_ids)
            shift = rng.randrange(len(locs))
            locs = locs[shift:] + locs[:shift]
            out: dict[str, frozenset[int]] = {}
            for loc_id in locs:
                k = rng.choice((2, 3))
                out[loc_id], free = frozenset(free[:k]), free[k:]
            self._clinic_cache[key] = out
        return self._clinic_cache[key]

    def _works_at(self, row: BookableRow, day: date) -> bool:
        if day.weekday() not in row.location.open_weekdays:
            return False
        week_start = day - timedelta(days=day.weekday())
        return day.weekday() in self.clinic_days(row.provider, week_start).get(row.location.id, ())

    def _busy(self, provider_id: str, start: datetime, end: datetime, ignore: str | None = None) -> bool:
        return any(h.slot.provider_id == provider_id and h.slot.id != ignore
                   and h.slot.start < end and start < h.slot.end for h in self._holds.values())

    def _day_slots(self, row: BookableRow, day: date) -> list[Slot]:
        dur = row.type.duration_min
        out = []
        for minute in range(row.location.open_minute, row.location.close_minute - dur + 1, dur):
            start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=minute)
            if start < self.now + MIN_LEAD:
                continue
            if _unit(self.seed, row.provider.id, row.location.id, day.isoformat(), minute, dur) >= self.open_rate:
                continue
            slot = Slot(row.type.id, row.provider.id, row.location.id, start, dur)
            if not self._busy(row.provider.id, slot.start, slot.end):
                out.append(slot)
        return out

    def _days(self, time_pref: TimePref) -> list[date]:
        first = self.now.date()
        if time_pref.not_before:
            first = max(first, date.fromisoformat(time_pref.not_before))
        wanted = {WEEKDAY_NAMES.index(d) for d in time_pref.days}
        last = self.now.date() + timedelta(days=self.horizon_days)
        days, d = [], first
        while d <= last:
            if not wanted or d.weekday() in wanted:
                days.append(d)
            d += timedelta(days=1)
        return days

    def find(self, rows: Iterable[BookableRow], time_pref: TimePref, limit: int = 3) -> list[Slot]:
        rows = list(rows)
        days: list[list[Slot]] = []
        for day in self._days(time_pref):
            todays = sorted((s for row in rows if self._works_at(row, day) for s in self._day_slots(row, day)
                             if _in_part(s, time_pref.part_of_day)),
                            key=lambda s: (s.start, s.provider_id, s.location_id))
            if todays:
                days.append(todays)
                if len(days) == limit:
                    break
        return spread(days, limit)

    def is_open(self, slot: Slot) -> bool:
        row = self.index.row(slot.type_id, slot.provider_id, slot.location_id)
        if row is None or slot.id in self._holds or not self._works_at(row, slot.start.date()):
            return False
        return slot in self._day_slots(row, slot.start.date())

    def hold(self, slot: Slot) -> Hold:
        if slot.id in self._holds:
            return self._holds[slot.id]
        if not self.is_open(slot):
            return Hold(ok=False, slot=slot)
        hold = Hold(ok=True, slot=slot, ref=f"H-{int.from_bytes(hashlib.blake2b(slot.id.encode(), digest_size=4).digest(), 'big') % 10000:04d}")
        self._holds[slot.id] = hold
        return hold

    def release(self, slot_id: str) -> None:
        self._holds.pop(slot_id, None)
