"""Spoken strings for the resolver's moves. Short, never more than 3 options, names and times
come straight from the catalog and the availability source so they cannot be paraphrased."""

from __future__ import annotations

import re
from datetime import datetime

from .catalog_index import AppointmentType, CatalogIndex
from .geo import US_STATES
from .lexicon import fasting_pair
from .names import street_of
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


def city_of(index: CatalogIndex, location_id: str) -> str:
    """The city a site is said to be in: its metro, else its own city."""
    loc = index.locations[location_id]
    metro = index.metros.get(loc.metro_id)
    return metro.name if metro and metro.name else loc.city


def site_in_city(index: CatalogIndex, location_id: str) -> str:
    """"Downtown in Oakland"; the site alone when it has no city to name."""
    city = city_of(index, location_id)
    return f"{site_label(index, location_id)} in {city}" if city else site_label(index, location_id)


def site_near_city(index: CatalogIndex, location_id: str) -> str:
    """"Cherry Hill, near Philadelphia" for a site in a city of its own; "The Avenues in Salt Lake City"."""
    loc, city = index.locations[location_id], city_of(index, location_id)
    if loc.city and city and normalize(loc.city) != normalize(city):
        return f"{site_label(index, location_id)}, near {city}"
    return site_in_city(index, location_id)


def place_said(name: str, city: str | None, state: str | None) -> str:
    """"Renton, Washington"; "Lincoln Park in Chicago, Illinois"; "New York, New York"; "Virginia"."""
    where = name if not city or normalize(city) == normalize(name) else f"{name} in {city}"
    is_the_state = not city and normalize(name) == normalize(state_name(state or ""))
    return f"{where}, {state_name(state)}" if state and not is_the_state else where


def _at(sites: str) -> str:
    """" at Mission Bay", or " there" when no site was named: never a phrase around an empty label."""
    return f" at {sites}" if sites else " there"


def _site_names(index: CatalogIndex, ids: list[str]) -> list[str]:
    """Short names, with the neighborhood when two are the same."""
    labels = [index.locations[i].short_name for i in ids]
    return [site_label(index, i) for i in ids] if len(set(labels)) < len(labels) else labels


def _distinct_site_labels(index: CatalogIndex, ids: list[str]) -> list[str]:
    streets = [street_of(index.locations[i]) for i in ids]
    if len(ids) > 1 and len({s.name for s in streets}) == 1 and len({s.number for s in streets} - {None}) == len(ids):
        # Sites on one street are told apart by number: "Downtown at 1812 Market".
        return [f"{index.locations[i].short_name} at {s.number} {s.name}" for i, s in zip(ids, streets)]
    labels = _site_names(index, ids)
    if len(set(labels)) < len(labels):
        labels = [f"{lab} on {index.locations[i].address.split(' ', 1)[-1]}" for lab, i in zip(labels, ids)]
    return labels


def named_sites(index: CatalogIndex, ids: tuple[str, ...], on_street: bool = False) -> str:
    """The clinics the caller named, as said back to them: "Willow Glen on Market Street"."""
    streets = {street_of(index.locations[i]).said for i in ids}
    return join_or(_site_names(index, list(ids))) + (f" on {streets.pop()}" if on_street and len(streets) == 1 else "")


def state_name(abbrev: str) -> str:
    return _STATE_NAMES.get(abbrev, abbrev)


def metro_labels(index: CatalogIndex, ids: list[str]) -> list[str]:
    """"Austin or Dallas"; every city with its state when one name is also another place's:
    "Seattle, Washington or Washington, DC", "Portland, Oregon or Portland, Maine". A bare
    "Washington" could be the state or the city, and the caller's answer would be just as unclear."""
    names = [index.metros[m].name for m in ids]
    if not any(_names_another_place(index, m) for m in ids):
        return names
    return [f"{n}, {'DC' if index.metros[m].state == 'DC' else state_name(index.metros[m].state)}"
            for n, m in zip(names, ids)]


def _names_another_place(index: CatalogIndex, metro_id: str) -> bool:
    name = normalize(index.metros[metro_id].name)
    return (name in {normalize(n) for n in _STATE_NAMES.values()}
            or any(normalize(m.name) == name for mid, m in index.metros.items() if mid != metro_id))


def miles(d: float) -> str:
    n = max(1, round(d))
    return "1 mile" if n == 1 else f"{n} miles"


def ring_preface(index: CatalogIndex, location_id: str, distance_mi: float, near: str,
                 near_metros: tuple[str, ...] = ()) -> str:
    """Said before offers when nothing was close: "The closest one is 18 miles away, in Round Rock."
    A clinic in another of our cities is named by that city."""
    loc = index.locations[location_id]
    metro = index.metros.get(loc.metro_id)
    if near_metros and loc.metro_id not in near_metros and city_of(index, location_id):
        where = city_of(index, location_id)
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
    if field == "service" and fasting_pair(index, options):
        # A caller can say whether they were told to fast; "a blood draw or a fasting blood test?" they often can't.
        return "Did your doctor say to fast for it?"
    if field == "service":
        return f"Is that {join_or([with_article(type_label(index.types[o])) for o in options])}?"
    if field == "service_suggest":
        why = f", since you said {context}" if context else ""
        return f"It sounds like {with_article(type_label(index.types[options[0]]))}{why}. Shall I go with that?"
    if field == "service_open":
        return "What's the visit for?"
    if field == "service_hint":
        return f"What kind of visit is it: {join_or([with_article(type_label(index.types[o])) for o in options])}, or something else?"
    if field == "provider":
        return f"Do you mean {join_or(_distinct_provider_labels(index, options))}?"
    if field == "provider_first_name":
        return f"Which {context or 'doctor'} is it? Do you know the first name?"
    if field == "provider_confirm_again":
        return f"Sorry, I didn't catch that. Do you mean {join_or(_distinct_provider_labels(index, options))}? Yes or no?"
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
    if field == "location_zip":
        # The same place said again would get the same miss: ask for something else to go on.
        return "I couldn't find that place. Could you tell me a nearby city, or your ZIP code?"
    if field == "place_confirm":
        return f"Did you mean {context}?"
    if field == "metro" and options:
        return f"Is that {join_or(metro_labels(index, options))}?"
    if field == "metro_again":
        return f"Sorry, I still need to know which one: {join_or(metro_labels(index, options))}? Or tell me your ZIP code."
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
               radius_mi: float | None = None, nearest_mi: float | None = None, inside: bool = False,
               related: tuple[str, str, float] | None = None) -> str:
    """`at`: the clinics the caller named (location_type, provider_location, and none_nearby when
    nothing near a named clinic has the visit); `location_id`: the nearest clinic that has it
    (none_nearby), `inside` the state the caller named; `related`: a more general visit near the
    caller (type, clinic, miles), asked about beside the far one (none_nearby)."""
    what = with_article(type_label(index.types[type_id])) if type_id else "that"
    What = what[0].upper() + what[1:]
    loc = named_sites(index, at, on_street)
    alt = _alternatives_sentence(index, alternatives, at) if code != "none_nearby" else ""

    if code == "not_offered":
        s = f"Sorry, we don't offer {index.specialty_spoken.get(specialty, 'that')} at our clinics."
        if alt_type_id:
            s += f" I can book {with_article(type_label(index.types[alt_type_id]))} instead. Want that?"
        return s
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
            return f"{who} isn't{_at(loc)}; {who} sees patients at {sites}.{alt}"
        return f"{who} isn't{_at(loc)}.{alt}"
    if code == "location_type":
        return f"We can't do {what}{_at(loc)}.{alt}"
    if code == "no_availability":
        return f"I don't see any openings for {what} in the next three weeks with those preferences. Want me to try other days?"
    if code == "none_nearby" and related:
        kin_type, kin_loc, kin_mi = related
        kin = f"{with_article(type_label(index.types[kin_type]))} is {miles(kin_mi)} away, at {site_label(index, kin_loc)}"
        if location_id:
            city = city_of(index, location_id) or site_label(index, location_id)
            far = f"about {miles(nearest_mi)} away, in {city}" if nearest_mi is not None else f"in {city}"
            return (f"The nearest {type_label(index.types[type_id])} is {far}; {kin}. "
                    f"Would that work, or should I look in {city}?")
        return f"We don't offer {what} within {miles(radius_mi or 0)} of {near}; {kin}. Would that work?"
    if code == "none_nearby":
        if near_kind == "state":
            # A state's centre is nowhere the caller is: the clinic is named, never a distance to it.
            nearest = site_near_city(index, location_id) if location_id else ""
        else:
            nearest = f"{site_in_city(index, location_id)}, about {miles(nearest_mi or 0)} away" if location_id else ""
        where = f"in {near}" if near_kind == "state" else f"within {miles(radius_mi or 0)} of {near}"
        if inside:
            # The nearest is in the state named: "we don't offer it in Kansas" would be false.
            whose = f"{who[0].upper()}{who[1:]}'s" if who else "Our"
            s = f"{whose} nearest clinic {where}{f' for {what}' if type_id else ''} is {nearest}."
        elif who:
            s = f"{who[0].upper()}{who[1:]} isn't at any of our clinics {where}."
        elif loc:
            s = f"We can't do {what} at {loc}, or anywhere within {miles(radius_mi or 0)} of it."
        else:
            s = f"We don't offer {what} {where}." if type_id else f"We don't have a clinic {where}."
        if location_id and not inside:
            s += f" The nearest is {nearest}."
        if location_id and alternatives:
            s += " Want me to look there?"
        elif not type_id:
            # Only the place is known ("in Trenton"): the call goes on with the visit, not silence.
            s += " What's the visit for?"
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
        return site_in_city(index, l) if away else site_label(index, l)
    parts = [f"{index.providers[p].name} at {where(l)}" for _, p, l in alternatives]
    question = "Would that work?" if len(parts) == 1 else "Would either of those work?"
    return f" I can book {join_or(parts)}. {question}"
