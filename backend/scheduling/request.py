"""What is known about the caller's request. Lives in FlowManager.state["req"] as a dict.

Only `merge` (caller input) and the resolver's returned `Plan.req` (derived fields) produce new
Requests; both return new values and never mutate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from typing import Any

from .policy import Patient

SLOT_NAMES = ("service", "provider", "location")
REJECT_KINDS = ("location", "provider", "time")
WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
PARTS_OF_DAY = ("morning", "afternoon")
# Words about when, not what: the time preference takes them, so a service phrase that carries
# them ("a flu shot today", "next week") is no less clear.
TIME_WORDS = frozenset({*WEEKDAY_NAMES, *(d + "s" for d in WEEKDAY_NAMES), *PARTS_OF_DAY, "mornings", "afternoons",
                        "evening", "evenings", "today", "tomorrow", "tonight", "next", "this", "week", "weekend",
                        "month", "soon", "soonest", "asap", "earliest", "possible"})


@dataclass(frozen=True)
class Slot:
    heard: str | None = None
    hint: str | None = None          # specialty_hint; service slot only
    exact: bool = False              # heard came from the strict service_name enum
    resolved_id: str | None = None
    candidates: tuple[tuple[str, float], ...] = ()
    within: tuple[str, ...] = ()     # options of the question this phrase answered
    asks: int = 0                    # consecutive failed matches (drives spell -> handoff)
    hints: int = 0                   # times a question named the likeliest of too many visits
    turn: int = 0                    # turn on which heard/hint last changed
    region: str | None = None        # location only: the answer to "which city?", narrowing heard

    @property
    def given(self) -> bool:
        return bool(self.heard or self.hint)


@dataclass(frozen=True)
class TimePref:
    days: tuple[str, ...] = ()       # weekday names; empty = any
    part_of_day: str | None = None   # morning | afternoon
    not_before: str | None = None    # ISO date


@dataclass(frozen=True)
class OfferRef:
    n: int
    type_id: str
    provider_id: str
    location_id: str
    start: str                       # ISO datetime
    duration_min: int

    @property
    def slot_id(self) -> str:
        return slot_id(self.type_id, self.provider_id, self.location_id, datetime.fromisoformat(self.start))


def slot_id(type_id: str, provider_id: str, location_id: str, start: datetime) -> str:
    return f"{type_id}|{provider_id}|{location_id}|{start:%Y%m%dT%H%M}"


def slot_start(slot_id: str) -> datetime:
    return datetime.strptime(slot_id.rsplit("|", 1)[1], "%Y%m%dT%H%M")


@dataclass(frozen=True)
class Rejected:
    """Offered options the caller turned down ("any other clinic?", "someone else?", "anything later?")."""

    locations: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    slots: tuple[str, ...] = ()      # slot_id of each time turned down

    def __bool__(self) -> bool:
        return bool(self.locations or self.providers or self.slots)


@dataclass(frozen=True)
class AltRef:
    """A refusal's suggested alternative; pick_offer n adopts it. None = keep the caller's choice."""

    n: int
    type_id: str
    provider_id: str | None = None
    location_id: str | None = None


@dataclass(frozen=True)
class PendingAsk:
    field: str
    options: tuple[str, ...] = ()
    repeats: int = 0                 # empty updates since it was asked: the caller said nothing usable


@dataclass(frozen=True)
class Request:
    patient: Patient = Patient()
    service: Slot = Slot()
    provider: Slot = Slot()
    location: Slot = Slot()
    time_pref: TimePref = TimePref()
    offered: tuple[OfferRef, ...] = ()
    alternatives: tuple[AltRef, ...] = ()
    pick: int | None = None
    pending_ask: PendingAsk | None = None
    rejected: Rejected = Rejected()
    turn: int = 0
    changed: tuple[str, ...] = ()    # request parts the latest merge changed (slots, time_pref)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Request":
        def slot(x: dict) -> Slot:
            return Slot(**{**x, "candidates": tuple(tuple(c) for c in x.get("candidates", ())),
                           "within": tuple(x.get("within", ()))})
        tp = d.get("time_pref") or {}
        pa = d.get("pending_ask")
        return cls(
            patient=Patient(**(d.get("patient") or {})),
            service=slot(d.get("service") or {}),
            provider=slot(d.get("provider") or {}),
            location=slot(d.get("location") or {}),
            time_pref=TimePref(days=tuple(tp.get("days", ())), part_of_day=tp.get("part_of_day"),
                               not_before=tp.get("not_before")),
            offered=tuple(OfferRef(**o) for o in d.get("offered", ())),
            alternatives=tuple(AltRef(**a) for a in d.get("alternatives", ())),
            pick=d.get("pick"),
            pending_ask=PendingAsk(pa["field"], tuple(pa.get("options", ())), pa.get("repeats", 0)) if pa else None,
            rejected=Rejected(**{k: tuple(v) for k, v in (d.get("rejected") or {}).items()}),
            turn=d.get("turn", 0),
            changed=tuple(d.get("changed", ())),
        )


@dataclass(frozen=True)
class Update:
    """Arguments of one update_request tool call, validated at the boundary."""

    service_phrase: str | None = None
    service_name: str | None = None
    specialty_hint: str | None = None
    provider_phrase: str | None = None
    location_phrase: str | None = None
    is_new: bool | None = None
    has_referral: bool | None = None
    time_pref: TimePref | None = None
    pick_offer: int | None = None
    reject: tuple[str, ...] = field(default=())
    clear: tuple[str, ...] = field(default=())

    @classmethod
    def from_args(cls, args: dict[str, Any]) -> "Update":
        known = set(cls.__dataclass_fields__)
        unknown = set(args) - known
        if unknown:
            raise ValueError(f"unknown update fields: {sorted(unknown)}")
        out: dict[str, Any] = {}
        for key in ("service_phrase", "service_name", "specialty_hint", "provider_phrase", "location_phrase"):
            v = args.get(key)
            if v is not None:
                if not isinstance(v, str):
                    raise ValueError(f"{key} must be a string")
                if v.strip():
                    out[key] = v.strip()
        for key in ("is_new", "has_referral"):
            v = args.get(key)
            if v is not None:
                if not isinstance(v, bool):
                    raise ValueError(f"{key} must be a boolean")
                out[key] = v
        if args.get("pick_offer") is not None:
            n = args["pick_offer"]
            if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 3:
                raise ValueError("pick_offer must be 1, 2 or 3")
            out["pick_offer"] = n
        if args.get("clear"):
            if not isinstance(args["clear"], list):
                raise ValueError("clear must be a list")
            bad = [c for c in args["clear"] if c not in (*SLOT_NAMES, "time_pref")]
            if bad:
                raise ValueError(f"cannot clear {bad}")
            out["clear"] = tuple(args["clear"])
        if args.get("reject"):
            if not isinstance(args["reject"], list):
                raise ValueError("reject must be a list")
            bad = [r for r in args["reject"] if r not in REJECT_KINDS]
            if bad:
                raise ValueError(f"cannot reject {bad}")
            out["reject"] = tuple(dict.fromkeys(args["reject"]))
        if args.get("time_pref") is not None:
            out["time_pref"] = _parse_time_pref(args["time_pref"])
        return cls(**out)


def _parse_time_pref(tp: Any) -> TimePref:
    if not isinstance(tp, dict):
        raise ValueError("time_pref must be an object")
    raw_days = tp.get("days") or []
    if not isinstance(raw_days, list) or not all(isinstance(d, str) for d in raw_days):
        raise ValueError("time_pref.days must be a list of weekday names")
    days = tuple(d.lower() for d in raw_days)
    if any(d not in WEEKDAY_NAMES for d in days):
        raise ValueError(f"time_pref.days must be weekday names, got {days}")
    part = tp.get("part_of_day")
    if part is not None and part not in PARTS_OF_DAY:
        raise ValueError(f"time_pref.part_of_day must be one of {PARTS_OF_DAY}")
    not_before = tp.get("not_before")
    if not_before is not None:
        try:
            not_before = date.fromisoformat(not_before).isoformat()
        except (TypeError, ValueError):
            raise ValueError(f"time_pref.not_before must be an ISO date (YYYY-MM-DD), got {not_before!r}") from None
    return TimePref(days=days, part_of_day=part, not_before=not_before)


# Asks whose answer arrives in another slot's phrase: "Which city?" and "Did you mean Renton,
# Washington?" are answered as a location.
_ASK_SLOT = {"metro": "location", "place_confirm": "location"}


def _answered(req: Request, slot_name: str) -> tuple[str, ...]:
    pa = req.pending_ask
    return pa.options if pa and _ASK_SLOT.get(pa.field, pa.field) == slot_name else ()


_REJECTED_FIELD = {"location": "locations", "provider": "providers", "time": "slots"}


def _rejected(req: Request, changes: dict[str, Any], reject: tuple[str, ...]) -> Rejected:
    """What stays turned down after a turn. A new visit starts the search again and forgets it all;
    a new doctor or place forgets the doctors or places turned down; any change forgets the times.
    Then the offers of each kind rejected now ("any other clinic?") are added, unless the same turn
    names a new one of that kind."""
    offered = {"locations": [o.location_id for o in req.offered], "providers": [o.provider_id for o in req.offered],
               "slots": [o.slot_id for o in req.offered]}
    named = {k for k in ("service", "provider", "location") if k in changes and changes[k].given}
    out = {}
    for kind, f in _REJECTED_FIELD.items():
        forget = "service" in named or kind in named or (f == "slots" and bool(changes))
        out[f] = () if forget else getattr(req.rejected, f)
        if kind in reject and kind not in named:
            out[f] = tuple(dict.fromkeys((*out[f], *offered[f])))
    return Rejected(**out)


def merge(req: Request, update: Update) -> Request:
    """Apply one caller turn. A new phrase replaces the slot (the caller changed their mind);
    offers made against the old request are dropped, since they no longer answer it, unless the
    same turn picks one ("the Tuesday one at Downtown"): then the resolver confirms the pick if it
    satisfies the change and says so if it does not; a pick that comes with a reject is dropped.
    Provider and location choices survive a service change here and are re-validated by the
    resolver, which drops and reports any that no longer fit."""
    turn = req.turn + 1
    changes: dict[str, Any] = {}

    svc = req.service
    if update.service_phrase or update.service_name or update.specialty_hint:
        new_heard = update.service_name or update.service_phrase
        svc = Slot(
            heard=new_heard or svc.heard,
            hint=update.specialty_hint if (update.specialty_hint or new_heard) else svc.hint,
            exact=bool(update.service_name),
            within=_answered(req, "service"),
            asks=svc.asks,
            hints=svc.hints,
            turn=turn,
        )
        changes["service"] = svc
    for name, phrase in (("provider", update.provider_phrase), ("location", update.location_phrase)):
        if phrase:
            old: Slot = getattr(req, name)
            if name == "location" and old.heard and req.pending_ask and _ASK_SLOT.get(req.pending_ask.field):
                # "Downtown" -> "Which city?" -> "Austin": the city narrows the place, not replaces it.
                changes[name] = Slot(heard=old.heard, region=phrase, within=_answered(req, name), asks=old.asks,
                                     turn=turn)
            else:
                changes[name] = Slot(heard=phrase, within=_answered(req, name), asks=old.asks, turn=turn)

    if update.time_pref is not None:
        changes["time_pref"] = update.time_pref

    for name in update.clear:
        # "Yes" to "Did you mean Renton, Washington?" came as location "Renton" plus clear location:
        # what the same turn says wins over its own clear.
        if name not in changes:
            changes[name] = TimePref() if name == "time_pref" else Slot(turn=turn)

    patient = req.patient
    if update.is_new is not None or update.has_referral is not None:
        patient = Patient(
            is_new=update.is_new if update.is_new is not None else patient.is_new,
            has_referral=update.has_referral if update.has_referral is not None else patient.has_referral,
        )

    new = replace(req, **changes, patient=patient, turn=turn, changed=tuple(changes))
    reject = update.reject if req.offered else ()
    # A reject turns down the offers a pick would take, so the reject wins and the pick goes. It is
    # settled here, not in the tool layer, so any caller of merge gets the same reading.
    if update.pick_offer is not None and not reject:
        return replace(new, pick=update.pick_offer, pending_ask=None if changes else new.pending_ask)
    if changes or reject:
        new = replace(new, rejected=_rejected(req, changes, reject), offered=(), alternatives=(), pick=None,
                      pending_ask=None)
        if "provider" in reject and "provider" not in changes and req.provider.given:
            # "Someone else?" after naming a doctor: the name is what they turned down.
            new = replace(new, provider=Slot(turn=turn))
        return new
    if req.pending_ask and patient == req.patient:
        # An update with nothing in it ("I already told you"): the question is still open and unanswered.
        return replace(new, pending_ask=replace(req.pending_ask, repeats=req.pending_ask.repeats + 1))
    return new
