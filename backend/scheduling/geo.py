"""Place phrases -> sites or area anchors, over the catalog's own geography (no geocoding).

A bare clinic name means that clinic ("Riverside" -> book at Riverside). Anything else names an
area to search around: "near Riverside", a neighborhood, a ZIP, a city or a state. A catalog
without coordinates (SF) has no areas, so every phrase is a site phrase, matched exactly as
names.match_locations always has.

A place matched by how it sounds, by spelling, or by part of its name is a guess ("Trenton" sounds
like Renton, Washington): the resolver confirms it before searching. Only a plausible misspelling
of one name (_misspelled) is taken as that name.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Callable, Iterable, Mapping

import jellyfish

from .names import NameCandidate, _location_words, _same_word, hear_place, match_locations
from .text import normalize, phonetic_keys, tokens

if TYPE_CHECKING:
    from .catalog_index import CatalogIndex, Location, Metro

EARTH_RADIUS_MI = 3958.8
RADIUS_MI = {"site": 5.0, "neighborhood": 5.0, "zip": 5.0, "zip3": 15.0, "metro": 25.0, "state": 150.0}
# A site match at least this good means every distinctive word named the clinic itself.
STRONG_SITE_SCORE = 0.7
_FUZZY_SPELLING = 0.9
_FUZZY_WITH_SOUND = 0.8

# (abbrev, name, approximate geographic centre). Only used to answer "I'm in Montana" with the
# nearest clinic when the catalog has no metro there; ~50 mi of error does not change that.
US_STATES: tuple[tuple[str, str, float, float], ...] = (
    ("AL", "Alabama", 32.8, -86.8), ("AK", "Alaska", 64.7, -152.0), ("AZ", "Arizona", 34.3, -111.7),
    ("AR", "Arkansas", 34.9, -92.4), ("CA", "California", 37.2, -119.5), ("CO", "Colorado", 39.0, -105.5),
    ("CT", "Connecticut", 41.6, -72.7), ("DE", "Delaware", 39.0, -75.5),
    ("DC", "District of Columbia", 38.9, -77.0), ("FL", "Florida", 28.6, -82.4),
    ("GA", "Georgia", 32.7, -83.4), ("HI", "Hawaii", 20.3, -156.4), ("ID", "Idaho", 44.4, -114.6),
    ("IL", "Illinois", 40.0, -89.2), ("IN", "Indiana", 39.9, -86.3), ("IA", "Iowa", 42.1, -93.5),
    ("KS", "Kansas", 38.5, -98.4), ("KY", "Kentucky", 37.5, -85.3), ("LA", "Louisiana", 31.1, -92.0),
    ("ME", "Maine", 45.4, -69.2), ("MD", "Maryland", 39.0, -76.8), ("MA", "Massachusetts", 42.3, -71.8),
    ("MI", "Michigan", 44.3, -85.4), ("MN", "Minnesota", 46.3, -94.3), ("MS", "Mississippi", 32.7, -89.7),
    ("MO", "Missouri", 38.4, -92.5), ("MT", "Montana", 47.0, -109.6), ("NE", "Nebraska", 41.5, -99.8),
    ("NV", "Nevada", 39.3, -116.6), ("NH", "New Hampshire", 43.7, -71.6), ("NJ", "New Jersey", 40.2, -74.7),
    ("NM", "New Mexico", 34.4, -106.1), ("NY", "New York", 42.9, -75.5),
    ("NC", "North Carolina", 35.6, -79.4), ("ND", "North Dakota", 47.5, -100.5), ("OH", "Ohio", 40.3, -82.8),
    ("OK", "Oklahoma", 35.6, -97.5), ("OR", "Oregon", 43.9, -120.6), ("PA", "Pennsylvania", 40.9, -77.8),
    ("RI", "Rhode Island", 41.7, -71.5), ("SC", "South Carolina", 33.9, -80.9),
    ("SD", "South Dakota", 44.4, -100.2), ("TN", "Tennessee", 35.9, -86.4), ("TX", "Texas", 31.5, -99.3),
    ("UT", "Utah", 39.3, -111.7), ("VT", "Vermont", 44.1, -72.7), ("VA", "Virginia", 37.5, -78.9),
    ("WA", "Washington", 47.4, -120.5), ("WV", "West Virginia", 38.6, -80.6),
    ("WI", "Wisconsin", 44.6, -89.9), ("WY", "Wyoming", 43.0, -107.6),
)
# Two-letter state codes that are also everyday words; alone they are never a state.
_WORD_ABBREVS = {"in", "me", "or", "hi", "ok", "oh", "pa", "de", "la", "co", "id", "ma", "al", "md", "ne", "mt"}

_NEAR_PREFIXES = (("close", "to"), ("closest", "to"), ("nearest", "to"), ("next", "to"), ("near", "to"),
                  ("near",), ("nearby",), ("around",), ("by",))
_LEAD_FILLER = {"i", "m", "am", "im", "we", "re", "live", "living", "work", "stay", "staying", "located",
                "based", "in", "at", "from", "um", "uh", "so", "well", "the", "over", "out", "here",
                "somewhere", "anywhere", "just", "a", "s", "it", "one", "of", "my", "is", "zip", "zipcode",
                "code", "postal"}
_TRAILING_FILLER = {"area", "please", "neighborhood", "region", "metro", "city"}
_CLINIC_WORDS = frozenset({"clinic", "clinics", "center", "centre", "office"})
_ZIP = re.compile(r"^\d{5}$")


@dataclass(frozen=True, slots=True)
class Place:
    key: str                    # "metro:austin-tx", "state:TX", "zip:78701", "zip3:787", "nbhd:austin-tx:riverside", "site:loc_012"
    kind: str                   # site | neighborhood | zip | zip3 | metro | state
    label: str                  # how to say it: "Austin", "Texas", "Riverside", "78701"
    lat: float | None
    lon: float | None
    metro_ids: tuple[str, ...]  # metros the place touches; empty for a state with no clinics
    radius_mi: float
    city: str | None = None     # the city it is or is in: "Boston" for Dorchester; None for a state
    state: str | None = None    # None when its clinics straddle a state line


@dataclass(frozen=True, slots=True)
class PlaceMatch:
    """`sites`: the caller named a clinic, book exactly there (match_locations' top tier).
    `anchors`: the caller named an area, search around it; anchors in different metros
    ("Portland") are an ambiguity the resolver asks about. Both empty: nothing understood.
    `guess`: the phrase only sounds like, is spelled like or names part of them, or names a clinic
    that is no place we know, with no city said: confirmed before any search.
    `unknown_city`: the anchors are the state said with a city we have no record of ("Memphis,
    Tennessee"); the state bounds where the caller is but is not taken for one of its cities."""

    sites: tuple[NameCandidate, ...] = ()
    anchors: tuple[Place, ...] = ()
    guess: bool = False
    unknown_city: bool = False

    @property
    def site_ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.sites)

    def metro_ids(self, ix: CatalogIndex) -> frozenset[str]:
        return frozenset(ix.locations[c.id].metro_id for c in self.sites) | frozenset(
            m for p in self.anchors for m in p.metro_ids)


@dataclass(frozen=True, slots=True)
class Gazetteer:
    areas: dict[str, tuple[Place, ...]]   # normalized name -> metros, states, neighborhoods, suburbs
    zips: dict[str, Place]
    zip3s: dict[str, Place]
    sites: dict[str, Place]               # location id -> the site as an anchor ("near Riverside")
    sounds: tuple[tuple[str, frozenset[str]], ...]  # area names (no 2-letter codes) with sound keys
    state_sites: dict[str, tuple[str, ...]]  # "state:VA" -> the clinics in that state


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def _centroid(locs: list[Location]) -> tuple[float, float]:
    return sum(l.lat for l in locs) / len(locs), sum(l.lon for l in locs) / len(locs)


def build_gazetteer(locations: Mapping[str, Location], metros: Mapping[str, Metro]) -> Gazetteer:
    real_metros = [m for m in metros.values() if m.lat is not None]
    located = [l for l in locations.values() if l.lat is not None]
    areas: dict[str, list[Place]] = defaultdict(list)

    def add(name: str, place: Place) -> None:
        for key in {normalize(name), normalize(re.sub(r"^the\s+", "", name, flags=re.I))}:
            if key and place not in areas[key]:
                areas[key].append(place)

    for m in sorted(real_metros, key=lambda m: m.id):
        place = Place(f"metro:{m.id}", "metro", m.name, m.lat, m.lon, (m.id,), RADIUS_MI["metro"], m.name, m.state)
        for name in (m.name, *m.aliases):
            add(name, place)
    if real_metros or located:
        for abbrev, name, lat, lon in US_STATES:
            # A state's cities are those with a clinic in it: Arlington, Virginia is a Washington, DC
            # clinic. A city across the state line only answers "which city?" (over_state_line).
            in_state = tuple(sorted({m.id for m in real_metros if m.state == abbrev}
                                    | {l.metro_id for l in located if l.state == abbrev and l.metro_id in metros}))
            place = Place(f"state:{abbrev}", "state", name, lat, lon, in_state, RADIUS_MI["state"], state=abbrev)
            add(name, place)
            add(abbrev, place)

    by_nbhd: dict[tuple[str, str], list[Location]] = defaultdict(list)
    by_city: dict[tuple[str, str], list[Location]] = defaultdict(list)
    by_zip: dict[str, list[Location]] = defaultdict(list)
    by_zip3: dict[str, list[Location]] = defaultdict(list)
    for loc in sorted(located, key=lambda l: l.id):
        if loc.neighborhood:
            by_nbhd[(loc.metro_id, loc.neighborhood)].append(loc)
        if loc.city:
            by_city[(loc.metro_id, loc.city)].append(loc)
        if loc.zip:
            by_zip[loc.zip].append(loc)
            by_zip3[loc.zip[:3]].append(loc)

    def area_place(kind: str, key: str, label: str, locs: list[Location]) -> Place:
        lat, lon = _centroid(locs)
        cities, states = {l.city for l in locs}, {l.state for l in locs}
        return Place(key, kind, label, lat, lon, tuple(sorted({l.metro_id for l in locs})), RADIUS_MI[kind],
                     cities.pop() if len(cities) == 1 else None, states.pop() if len(states) == 1 else None)

    nbhd_names = {(mid, normalize(n)) for mid, n in by_nbhd}
    for (mid, name), locs in sorted(by_nbhd.items()):
        add(name, area_place("neighborhood", f"nbhd:{mid}:{normalize(name)}", name, locs))
    for (mid, city), locs in sorted(by_city.items()):
        # A suburb inside a metro ("Round Rock") is searched like a neighborhood; the metro's own
        # city name is already the metro.
        metro_name = normalize(metros[mid].name) if mid in metros else ""
        if normalize(city) != metro_name and (mid, normalize(city)) not in nbhd_names:
            add(city, area_place("neighborhood", f"nbhd:{mid}:{normalize(city)}", city, locs))

    sounds = tuple((name, phonetic_keys(name)) for name in sorted(areas) if len(name) > 3)
    return Gazetteer(
        areas={k: tuple(v) for k, v in areas.items()},
        zips={z: area_place("zip", f"zip:{z}", z, locs) for z, locs in sorted(by_zip.items())},
        zip3s={z: area_place("zip3", f"zip3:{z}", z, locs) for z, locs in sorted(by_zip3.items())},
        sites={l.id: Place(f"site:{l.id}", "site", l.short_name, l.lat, l.lon, (l.metro_id,), RADIUS_MI["site"],
                           l.city, l.state)
               for l in located},
        sounds=sounds,
        state_sites={f"state:{abbrev}": tuple(sorted(l.id for l in located if l.state == abbrev))
                     for abbrev, *_ in US_STATES} if real_metros or located else {},
    )


def over_state_line(ix: CatalogIndex, place: Place) -> bool:
    """"Virginia": every city with a clinic there is another state's. The state is then not taken
    for that city: the nearest of its own clinics is named, never a distance from the state's centre."""
    state = place.key.removeprefix("state:")
    return place.kind == "state" and bool(place.metro_ids) and all(ix.metros[m].state != state
                                                                    for m in place.metro_ids)


def resolve_place(ix: CatalogIndex, phrase: str | None) -> PlaceMatch:
    gz = ix.gazetteer
    if not phrase:
        return PlaceMatch()
    if not gz.areas and not gz.sites:
        return PlaceMatch(sites=tuple(match_locations(ix, phrase)))

    head_raw, _, qual_raw = phrase.partition(",")
    near, words = _strip_lead(gz, tokens(head_raw))
    region: tuple[Place, ...] = ()
    if qual_raw.strip():
        region = _region(gz, tokens(qual_raw))
        if not region:
            words += tokens(qual_raw)
    # "Austin area" drops "area", but "... in Kansas City" keeps "city".
    if words and words[-1] in _TRAILING_FILLER and not any(" ".join(words[-n:]) in gz.areas for n in (1, 2, 3)):
        words = words[:-1]
    if not region:
        words, region = _split_region(gz, words)
    if not region:
        return _resolve_head(ix, words, near, None)
    said = _prefer_metros(list(region))
    metro_ids = frozenset(m for p in said for m in p.metro_ids)
    found = _resolve_head(ix, words, near, metro_ids) if words else PlaceMatch()
    if found.sites or found.anchors:
        # "Cherry Hill, Pennsylvania": a match outside the state said is not what was said.
        return found if _inside(ix, found, said) else replace(found, guess=True)
    states = tuple(p for p in region if p.kind == "state")
    if words and states:
        # "Buffalo, New York": after a city we do not know, "New York" is the state, not our city.
        return PlaceMatch(anchors=states, unknown_city=True)
    return PlaceMatch(anchors=said)


def _inside(ix: CatalogIndex, found: PlaceMatch, region: tuple[Place, ...]) -> bool:
    states = {p.state for p in region if p.kind == "state"}
    metros = {m for p in region if p.kind != "state" for m in p.metro_ids}

    def inside(state: str | None, metro_ids: Iterable[str]) -> bool:
        return state in states or bool(metros & set(metro_ids))
    return (all(inside(ix.locations[c.id].state, (ix.locations[c.id].metro_id,)) for c in found.sites)
            and all(inside(p.state, p.metro_ids) for p in found.anchors))


def names_own_area(ix: CatalogIndex, phrase: str | None, location_id: str) -> bool:
    """The phrase is just the name of the neighborhood or suburb the clinic is in ("Lakewood" for
    Lakewood Family Clinic, in Lakewood): the caller may mean the clinic or the place."""
    loc = ix.locations[location_id]
    _, words = _strip_lead(ix.gazetteer, tokens((phrase or "").partition(",")[0]))
    own = {f"nbhd:{loc.metro_id}:{normalize(n)}" for n in (loc.neighborhood, loc.city) if n}
    return any(p.key in own for p in ix.gazetteer.areas.get(" ".join(words), ()))


def nearby(ix: CatalogIndex, place: Place, radius_mi: float | None = None,
           within: Iterable[str] | None = None) -> tuple[tuple[str, float], ...]:
    """(location id, miles) for sites within `radius_mi` (default: the place's own radius) of the
    place, nearest first. Pass math.inf to rank every site. Empty when either side lacks coordinates."""
    if place.lat is None:
        return ()
    limit = place.radius_mi if radius_mi is None else radius_mi
    pool = (ix.locations[i] for i in within) if within is not None else ix.locations.values()
    found = []
    for loc in pool:
        if loc.lat is None:
            continue
        d = haversine(place.lat, place.lon, loc.lat, loc.lon)
        if d <= limit:
            found.append((loc.id, d))
    found.sort(key=lambda x: (x[1], x[0]))
    return tuple(found)


def _strip_lead(gz: Gazetteer, words: list[str]) -> tuple[bool, list[str]]:
    """Drop "I'm in", "over in", "near", ... from the front; report whether a "near" word was seen.
    Stops as soon as the rest is itself a known area, so "The Heights" keeps its article."""
    near = False
    while words and " ".join(words) not in gz.areas:
        for prefix in _NEAR_PREFIXES:
            if tuple(words[:len(prefix)]) == prefix:
                near, words = True, words[len(prefix):]
                break
        else:
            if words[0] not in _LEAD_FILLER:
                break
            words = words[1:]
    return near, words


def _region(gz: Gazetteer, words: list[str]) -> tuple[Place, ...]:
    """Every city and state the words name; "New York" is both."""
    return tuple(p for p in gz.areas.get(" ".join(words), ()) if p.kind in ("metro", "state"))


def _split_region(gz: Gazetteer, words: list[str]) -> tuple[list[str], tuple[Place, ...]]:
    """"Riverside clinic in Austin", "downtown Dallas", "Portland Oregon": the trailing city or
    state narrows the head. A phrase that is itself an area ("West Virginia") is never split."""
    if not words or " ".join(words) in gz.areas:
        return words, ()
    for i in range(len(words) - 2, 0, -1):
        if words[i] == "in":
            region = _region(gz, words[i + 1:])
            if region:
                return words[:i], region
    for n in (3, 2, 1):
        if len(words) > n:
            region = _region(gz, words[-n:])
            if region:
                return words[:-n], region
    return words, ()


def _prefer_metros(places: list[Place]) -> tuple[Place, ...]:
    """"New York" is the city, not the state that contains it; "Washington" stays ambiguous
    between the state and a DC metro, because that metro is not in Washington state."""
    metro_ids = {m for p in places if p.kind == "metro" for m in p.metro_ids}
    kept = [p for p in places if p.kind != "state" or not metro_ids & set(p.metro_ids)]
    return tuple(sorted(kept, key=lambda p: (p.kind != "metro", p.key)))


def _resolve_head(ix: CatalogIndex, words: list[str], near: bool, within: frozenset[str] | None) -> PlaceMatch:
    gz = ix.gazetteer
    text = " ".join(words)
    if not text:
        return PlaceMatch()

    def keep(p: Place) -> bool:
        return within is None or bool(within & set(p.metro_ids))

    if _ZIP.match(text):
        place = gz.zips.get(text) or gz.zip3s.get(text[:3]) or _nearest_zip3(gz, text)
        if place is None or not keep(place):
            return PlaceMatch()
        return PlaceMatch(anchors=(replace(place, label=text),))  # say the caller's ZIP, not "370"

    exact = [p for p in gz.areas.get(text, ()) if keep(p)]
    if text in _WORD_ABBREVS:
        exact = [p for p in exact if p.kind != "state"]
    regions = [p for p in exact if p.kind in ("metro", "state")]
    if regions:
        return PlaceMatch(anchors=_prefer_metros(regions))

    sites = _sites(ix, text, within)
    # A clinic's name, "Market Street" or "3330 Market" named the site outright.
    strong = bool(sites) and sites[0].score >= STRONG_SITE_SCORE
    by_street = strong and sites[0].by_street
    if not by_street and not any(set(words) <= _location_words(ix.locations[c.id]) for c in sites):
        # "Philedelphia", "San Antonyo": a misheard city name is the city, not the clinics whose
        # names merely share some of its words.
        misheard, sure = _fuzzy_areas(gz, text, keep)
        if misheard and all(p.kind in ("metro", "state") for p in misheard):
            return _heard_areas(misheard, sure)
    # "Brooklyn" is a place of ours by that very name, so its clinics are never a guess.
    guess_sites = not exact and all(_site_guessed(ix, text, c, bare=within is None) for c in sites)
    if strong:
        return _named_sites(ix, sites, near, guess_sites)
    neighborhoods = [p for p in exact if p.kind == "neighborhood"]
    if neighborhoods:
        return PlaceMatch(anchors=tuple(neighborhoods))
    fuzzy, sure = _fuzzy_areas(gz, text, keep)
    if fuzzy:
        return _heard_areas(fuzzy, sure)
    if sites:
        return _named_sites(ix, sites, near, guess_sites)
    return PlaceMatch()


def _heard_areas(places: list[Place], sure: bool) -> PlaceMatch:
    """Areas the phrase sounds or is spelled like. Unsure, a city is a guess to confirm; a state
    ("Virginia Beach" ~ Virginia) only bounds where the caller is, as with a city we do not know:
    it is never narrowed to one of its cities, and the refusal or question that follows names it."""
    anchors = _prefer_metros(places)
    if sure:
        return PlaceMatch(anchors=anchors)
    if all(p.kind == "state" for p in anchors):
        return PlaceMatch(anchors=anchors, unknown_city=True)
    return PlaceMatch(anchors=anchors, guess=True)


def _nearest_zip3(gz: Gazetteer, zip_code: str) -> Place | None:
    """A ZIP whose 3-digit area has no clinic: ZIP areas are numbered geographically within their
    2-digit region (370-385 is Tennessee), so the numerically nearest catalog area in the same
    region stands in. Another region is never guessed."""
    region = [z for z in gz.zip3s if z[:2] == zip_code[:2]]
    if not region:
        return None
    return gz.zip3s[min(region, key=lambda z: (abs(int(z) - int(zip_code[:3])), z))]


def _sites(ix: CatalogIndex, text: str, within: frozenset[str] | None) -> tuple[NameCandidate, ...]:
    if within is None:
        return tuple(match_locations(ix, text))
    pool = [lid for m in sorted(within) for lid in ix.locs_by_metro.get(m, ())]
    if not pool:
        return ()
    allowed = set(pool)
    # match_locations widens to every site when nothing in `within` matches; here that would
    # leave the city the caller named, so the widened answer is dropped.
    return tuple(c for c in match_locations(ix, text, pool) if c.id in allowed)


def _named_sites(ix: CatalogIndex, sites: tuple[NameCandidate, ...], near: bool, guess: bool) -> PlaceMatch:
    """The clinics a phrase matched by name: the clinics themselves, or with "near" the areas around them."""
    anchors = tuple(ix.gazetteer.sites[c.id] for c in sites if c.id in ix.gazetteer.sites) if near else ()
    return PlaceMatch(anchors=anchors, guess=guess) if anchors else PlaceMatch(sites=sites, guess=guess)


def _site_guessed(ix: CatalogIndex, text: str, site: NameCandidate, bare: bool) -> bool:
    """The phrase does not name this clinic: a word only sounds or is spelled like its name
    ("Toledo" ~ Duluth), it names part of it ("Lincoln" for Lincoln Park), or, with no city said and
    no word saying it is a clinic, the name is no place of ours, so it may be a city we do not know
    ("Richmond", our Richmond District clinic in San Francisco; "the Richmond clinic" is the clinic).
    A street or address match is never a guess."""
    if site.by_street:
        return False
    loc = ix.locations[site.id]
    name = _location_words(loc)
    heard = list(hear_place(text).words)
    if len(heard) > 1 and "".join(heard) in name:
        heard = ["".join(heard)]  # "down town" -> "downtown", as match_locations hears it
    named: set[str] = set()
    for w in heard:
        spelled = {n for n in name if _misspelled(w, n)}
        if spelled:
            named |= spelled
        elif any(_same_word(w, n) for n in name):
            return True
    said_clinic = bool(set(tokens(text)) & _CLINIC_WORDS)
    return named != name or (bare and not said_clinic and normalize(loc.short_name) not in ix.gazetteer.areas)


def _misspelled(heard: str, name: str) -> bool:
    """`heard` is `name`, or a plausible misspelling or mishearing of it: same first letter and one
    edit, or two when the spellings still share a sound key ("Hueston", "Minneapolous"). Callers and
    speech-to-text keep a name's first sound; a misspelling changes about one letter in a word, a
    mishearing two only if it keeps the sound. Two edits that change the sound ("Fresno" ~ Frisco)
    or another first letter ("Trenton" ~ Renton) is another name. Words under four letters match
    only exactly: one edit there is a different word."""
    if heard == name:
        return True
    if heard[:1] != name[:1] or min(len(heard), len(name)) < 4:
        return False
    edits = jellyfish.damerau_levenshtein_distance(heard, name)
    return edits <= 1 or (edits == 2 and bool(phonetic_keys(heard) & phonetic_keys(name)))


def _spelled(text: str, name: str) -> bool:
    """Each word of `text` is a misspelling of the same word of `name`: "San Antonyo" spells San
    Antonio, but "Newark" (one word) does not spell New York."""
    said, named = text.split(), name.split()
    return len(said) == len(named) and all(_misspelled(h, n) for h, n in zip(said, named))


def _fuzzy_areas(gz: Gazetteer, text: str, keep: Callable[[Place], bool]) -> tuple[list[Place], bool]:
    """Misheard area names: "Austen" -> Austin. Best spelling score wins; ties keep all. Sure: the
    phrase came close to that one name only, and spells it (_spelled)."""
    if len(text) <= 3:
        return [], False
    keys = phonetic_keys(text)
    best, found, close = 0.0, [], []
    for name, name_keys in gz.sounds:
        jw = jellyfish.jaro_winkler_similarity(text, name)
        if jw < _FUZZY_SPELLING and not (jw >= _FUZZY_WITH_SOUND and keys & name_keys):
            continue
        places = [p for p in gz.areas[name] if keep(p)]
        if not places:
            continue
        close.append(name)
        if jw > best + 1e-9:
            best, found = jw, places
        elif abs(jw - best) <= 1e-9:
            found += [p for p in places if p not in found]
    return found, len(close) == 1 and _spelled(text, close[0])
