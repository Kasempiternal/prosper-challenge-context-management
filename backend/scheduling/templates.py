"""Spoken strings for the resolver's moves. Short, never more than 3 options, names and times
come straight from the catalog and the availability source so they cannot be paraphrased."""

from __future__ import annotations

import re
from datetime import datetime

from .catalog_index import AppointmentType, CatalogIndex
from .geo import US_STATES
from .names import STREET_TYPES
from .text import normalize

_STATE_NAMES = {abbrev: name for abbrev, name, _, _ in US_STATES}

_KEEP_CASE = re.compile(r"^[A-Z0-9/\-]{2,}$")
# Letters whose spoken name starts with a vowel sound: "an MRI", "an EKG", "an X-ray".
_VOWEL_SOUND_LETTERS = set("AEFHILMNORSX")


def type_label(t: AppointmentType) -> str:
    name = re.split(r" / | \(", t.name)[0]
    m = re.match(r"^(MRI) - (\w+)$", name)
    if m:
        return f"{m[2].lower()} {m[1]}"
    return " ".join(w if _KEEP_CASE.match(w) and w != "X-Ray" else w.lower() for w in name.split())


def with_article(label: str) -> str:
    first = label.split()[0]
    if _KEEP_CASE.match(first):
        vowel = first[0] in _VOWEL_SOUND_LETTERS
    else:
        vowel = first[0] in "aeiou" or first.startswith("x-")
    return ("an " if vowel else "a ") + label


def join_or(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + f", or {items[-1]}"


def join_and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def site_label(index: CatalogIndex, location_id: str) -> str:
    """"Mueller in Hyde Park": the site plus its neighborhood when the name does not already say it.
    A catalog without neighborhoods (SF) reads the short name alone."""
    loc = index.locations[location_id]
    if loc.neighborhood and normalize(loc.neighborhood) not in normalize(loc.short_name):
        return f"{loc.short_name} in {loc.neighborhood}"
    return loc.short_name


_STREET_TYPE_NAMES = {"st": "Street", "ave": "Avenue", "av": "Avenue", "blvd": "Boulevard", "rd": "Road",
                      "dr": "Drive", "ln": "Lane", "pl": "Place", "ct": "Court", "pkwy": "Parkway", "hwy": "Highway",
                      "ter": "Terrace", "cir": "Circle"}


def _address_parts(address: str) -> tuple[str, str, str]:
    """"3330 Market St" -> ("3330", "Market", "Street"); "7095 Broadway" -> ("7095", "Broadway", "")."""
    words = address.split()
    number = words.pop(0) if words and words[0].isdigit() else ""
    kind = words[-1].rstrip(".") if len(words) > 1 and words[-1].rstrip(".").lower() in STREET_TYPES else ""
    if kind:
        words.pop()
    return number, " ".join(words), _STREET_TYPE_NAMES.get(kind.lower(), kind)


def street_label(index: CatalogIndex, location_id: str) -> str:
    """"Market Street", as said aloud."""
    _, name, kind = _address_parts(index.locations[location_id].address)
    return f"{name} {kind}".strip()


def _distinct_site_labels(index: CatalogIndex, ids: list[str]) -> list[str]:
    parts = [_address_parts(index.locations[i].address) for i in ids]
    if len(ids) > 1 and len({p[1] for p in parts}) == 1 and len({p[0] for p in parts} - {""}) == len(ids):
        # Sites on one street are told apart by number: "Downtown at 1812 Market".
        return [f"{index.locations[i].short_name} at {num} {name}" for i, (num, name, _) in zip(ids, parts)]
    labels = [index.locations[i].short_name for i in ids]
    if len(set(labels)) < len(labels):
        labels = [site_label(index, i) for i in ids]
    if len(set(labels)) < len(labels):
        labels = [f"{lab} on {index.locations[i].address.split(' ', 1)[-1]}" for lab, i in zip(labels, ids)]
    return labels


def named_sites(index: CatalogIndex, ids: tuple[str, ...], on_street: bool = False) -> str:
    """The clinics the caller named, as said back to them: "Willow Glen on Market Street"."""
    labels = [index.locations[i].short_name for i in ids]
    if len(set(labels)) < len(labels):
        labels = [site_label(index, i) for i in ids]
    streets = {street_label(index, i) for i in ids}
    return join_or(labels) + (f" on {streets.pop()}" if on_street and len(streets) == 1 else "")


def state_name(abbrev: str) -> str:
    return _STATE_NAMES.get(abbrev, abbrev)


def metro_labels(index: CatalogIndex, ids: list[str]) -> list[str]:
    """"Austin", or "Portland, Oregon" when two metros share a name."""
    names = [index.metros[m].name for m in ids]
    return [f"{n}, {state_name(index.metros[m].state)}" if names.count(n) > 1 else n
            for n, m in zip(names, ids)]


def miles(d: float) -> str:
    n = max(1, round(d))
    return "1 mile" if n == 1 else f"{n} miles"


def ring_preface(index: CatalogIndex, location_id: str, distance_mi: float, near: str,
                 near_metros: tuple[str, ...] = ()) -> str:
    """Said before offers when nothing was close: "The closest one is 18 miles away, in Round Rock."
    A clinic in another of our cities is named by that city."""
    loc = index.locations[location_id]
    metro = index.metros.get(loc.metro_id)
    if metro and metro.name and near_metros and loc.metro_id not in near_metros:
        where = metro.name
    elif loc.city and normalize(loc.city) not in (normalize(near), normalize(metro.name) if metro else ""):
        where = loc.city
    else:
        where = loc.neighborhood or loc.short_name
    return f"There's nothing closer to {near}; the nearest is {miles(distance_mi)} away, in {where}. "


def _clock(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{hour}" if dt.minute == 0 else f"{hour}:{dt.minute:02d}"


def _spoken_clock(dt: datetime) -> str:
    return "noon" if (dt.hour, dt.minute) == (12, 0) else _clock(dt)


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def spoken_when(dt: datetime, now: datetime) -> str:
    days = (dt.date() - now.date()).days
    if days == 0:
        day = "today"
    elif days == 1:
        day = "tomorrow"
    elif days < 7:
        day = dt.strftime("%A")
    else:
        day = f"{dt.strftime('%A')} the {_ordinal(dt.day)}"
    return f"{day} at {_spoken_clock(dt)}"


def short_when(dt: datetime) -> str:
    return f"{dt:%a %b} {dt.day} {_clock(dt)}{'am' if dt.hour < 12 else 'pm'}"


# ---- moves -------------------------------------------------------------------------------

def say_offers(index: CatalogIndex, type_id: str, offers: list[tuple[str, str, datetime]], now: datetime,
               preface: str = "") -> str:
    """offers: (provider_id, location_id, start), already grouped by provider name."""
    intro = f"For {with_article(type_label(index.types[type_id]))}, "
    groups: dict[str, list[tuple[str, datetime]]] = {}
    for p, loc, start in offers:
        groups.setdefault(index.providers[p].name, []).append((loc, start))
    parts = []
    for name, times in groups.items():
        if len({loc for loc, _ in times}) == 1:
            when = f"{join_or([spoken_when(s, now) for _, s in times])} at {site_label(index, times[0][0])}"
        else:
            when = join_or([f"{spoken_when(s, now)} at {site_label(index, loc)}" for loc, s in times])
        parts.append(f"{name} has {when}")
    body = join_and(parts) + "."
    if len(offers) == 1:
        return f"{preface}{intro}{body} Does that work?"
    return f"{preface}{intro}{body} Which works best?"


def say_confirm(index: CatalogIndex, type_id: str, provider_id: str, location_id: str, start: datetime,
                now: datetime) -> str:
    return (f"Okay, {with_article(type_label(index.types[type_id]))} with "
            f"{index.providers[provider_id].name}, {spoken_when(start, now)} at "
            f"{site_label(index, location_id)}. Shall I book it?")


def say_ask(index: CatalogIndex, field: str, options: list[str], context: str | None = None) -> str:
    if field == "service":
        return f"Is that {join_or([with_article(type_label(index.types[o])) for o in options])}?"
    if field == "service_open":
        return "What's the visit for?"
    if field == "provider":
        return f"Do you mean {join_or(_distinct_provider_labels(index, options))}?"
    if field == "provider_first_name":
        return f"Which {context or 'doctor'} is it? Do you know the first name?"
    if field == "provider_retry":
        return "Sorry, which doctor was that?"
    if field == "provider_spelling":
        return "Could you spell the doctor's last name for me?"
    if field == "location":
        return f"Is that {join_or(_distinct_site_labels(index, options))}?"
    if field == "location_open":
        return "Which neighborhood is that location in?"
    if field == "location_retry":
        return "Sorry, which location was that?"
    if field == "metro" and options:
        return f"Is that {join_or(metro_labels(index, options))}?"
    if field == "metro":
        return f"Which city in {context} are you in?" if context else "Which city are you in?"
    if field == "is_new":
        return "Have you been seen at one of our clinics before?"
    if field == "has_referral":
        what = with_article(type_label(index.types[context])) if context else "that"
        return f"Do you have a referral for {what}?"
    raise ValueError(f"no template for ask field {field!r}")


def _distinct_provider_labels(index: CatalogIndex, ids: list[str]) -> list[str]:
    """Same full name twice (two Dr. Maria Garcias) -> add specialty, then site, until distinct."""
    provs = [index.providers[i] for i in ids]
    labels = [p.name for p in provs]
    if len(set(labels)) < len(labels):
        labels = [f"{p.name} in {index.specialty_spoken.get(p.specialty, p.specialty.lower())}" for p in provs]
    if len(set(labels)) < len(labels):
        labels = [f"{lab} at {join_or([index.locations[l].short_name for l in p.location_ids])}"
                  for lab, p in zip(labels, provs)]
    return labels


def say_refuse(index: CatalogIndex, code: str, *, type_id: str | None = None, who: str | None = None,
               provider_id: str | None = None, at: tuple[str, ...] = (), on_street: bool = False,
               location_id: str | None = None, specialty: str | None = None,
               alternatives: tuple[tuple[str, str, str], ...] = (), alt_type_id: str | None = None,
               needs_referral: bool = False, near: str | None = None, near_kind: str | None = None,
               radius_mi: float | None = None, nearest_mi: float | None = None) -> str:
    """`at`: the clinics the caller named (location_type, provider_location); `location_id`: the
    nearest clinic that has the visit (none_nearby)."""
    what = with_article(type_label(index.types[type_id])) if type_id else "that"
    What = what[0].upper() + what[1:]
    loc = named_sites(index, at, on_street)
    alt = _alternatives_sentence(index, alternatives, at) if code != "none_nearby" else ""

    if code == "not_offered":
        return f"Sorry, we don't offer {index.specialty_spoken.get(specialty, 'that')} at our clinics."
    if code == "new_patient_type":
        extra = " and needs a referral" if needs_referral else ""
        s = f"{What} is only for established patients{extra}, so I can't book it for a new patient."
        if alt_type_id:
            alt_what = with_article(type_label(index.types[alt_type_id]))
            s += f" {alt_what[0].upper()}{alt_what[1:]} is open to new patients. Want that instead?"
        return s
    if code == "new_patient_provider":
        return f"{who} isn't taking new patients.{alt}"
    if code == "referral":
        return f"{What} needs a referral first. Once you have one, we can book it."
    if code == "provider_type":
        return f"I can't book {what} with {who}.{alt}"
    if code == "provider_location":
        if provider_id:
            sites = join_and([index.locations[l].short_name for l in index.providers[provider_id].location_ids])
            return f"{who} isn't at {loc}; {who} sees patients at {sites}.{alt}"
        return f"{who} isn't at {loc}.{alt}"
    if code == "location_type":
        return f"We can't do {what} at {loc}.{alt}"
    if code == "no_availability":
        return f"I don't see any openings for {what} in the next three weeks with those preferences. Want me to try other days?"
    if code == "none_nearby":
        where = f"in {near}" if near_kind == "state" else f"within {miles(radius_mi or 0)} of {near}"
        if who:
            s = f"{who[0].upper()}{who[1:]} isn't at any of our clinics {where}."
        else:
            s = f"We don't offer {what} {where}." if type_id else f"We don't have a clinic {where}."
        if location_id:
            loc = index.locations[location_id]
            metro = index.metros.get(loc.metro_id)
            city = metro.name if metro and metro.name else loc.city
            s += f" The nearest is {site_label(index, location_id)} in {city}, about {miles(nearest_mi or 0)} away."
            if alternatives:
                s += " Want me to look there?"
        return s
    if code == "handoff":
        return "I'm having trouble finding that. Let me have someone from our front desk call you back."
    raise ValueError(f"no template for refusal {code!r}")


def _alternatives_sentence(index: CatalogIndex, alternatives: tuple[tuple[str, str, str], ...],
                           at: tuple[str, ...] = ()) -> str:
    """A suggestion in another of our cities than the clinics named says which: "Downtown in Oakland"."""
    if not alternatives:
        return ""
    home = {index.locations[l].metro_id for l in at}

    def where(l: str) -> str:
        metro = index.metros.get(index.locations[l].metro_id)
        away = home and metro and metro.name and metro.id not in home
        return f"{site_label(index, l)} in {metro.name}" if away else site_label(index, l)
    parts = [f"{index.providers[p].name} at {where(l)}" for _, p, l in alternatives]
    question = "Would that work?" if len(parts) == 1 else "Would either of those work?"
    return f" I can book {join_or(parts)}. {question}"
