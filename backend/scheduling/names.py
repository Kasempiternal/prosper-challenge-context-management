"""Provider and location phrases -> scored candidates, tolerant of speech-to-text spellings."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Iterable

import jellyfish

from .text import normalize, phonetic_keys, tokens

if TYPE_CHECKING:
    from .catalog_index import CatalogIndex, Location, Provider

_TITLE_WORDS = {"dr", "doctor", "doc", "the", "with", "md", "np", "pa", "do", "nurse", "practitioner"}
_LOCATION_GENERIC = {"health", "center", "centre", "clinic", "family", "specialty", "medical", "group",
                     "community", "care", "the", "one", "office", "location", "in", "at", "on"}
# Never evidence for a site: "Lincoln Avenue" names Lincoln, and "avenue" must not reach The Avenues.
STREET_TYPES = frozenset({"st", "street", "ave", "av", "avenue", "blvd", "boulevard", "rd", "road", "dr", "drive",
                          "ln", "lane", "way", "pl", "place", "ct", "court", "pkwy", "parkway", "hwy", "highway",
                          "ter", "terrace", "cir", "circle"})
MIN_PROVIDER_SCORE = 0.78
MIN_LOCATION_SCORE = 0.4
TIE_GAP = 0.08


@dataclass(frozen=True)
class NameCandidate:
    id: str
    score: float
    via: str  # "exact" | "fuzzy" | "phonetic" | "first"; locations: "exact" | "street" | "address"


# eq=False: identity hash, so the per-index word cache below can key on the index.
@dataclass(frozen=True, slots=True, eq=False)
class NameIndex:
    """Distinct normalized first and last names with their sound keys. Matching scores each
    distinct name once and then maps names to providers, so cost follows the number of distinct
    names, not the number of providers."""

    last_of: dict[str, str]                   # provider id -> normalized surname
    first_of: dict[str, str]                  # provider id -> normalized first name
    by_last: dict[str, tuple[str, ...]]       # normalized surname -> provider ids
    by_first: dict[str, tuple[str, ...]]      # normalized first name -> provider ids
    keys: dict[str, frozenset[str]]           # every distinct first or last name -> phonetic keys
    names_by_key: dict[str, tuple[str, ...]]  # phonetic key -> names carrying it

    @classmethod
    def build(cls, providers: Iterable[Provider]) -> NameIndex:
        last_of, first_of = {}, {}
        by_last: dict[str, list[str]] = defaultdict(list)
        by_first: dict[str, list[str]] = defaultdict(list)
        for p in providers:
            last, first = normalize(p.last_name), normalize(p.first_name)
            last_of[p.id], first_of[p.id] = last, first
            by_last[last].append(p.id)
            by_first[first].append(p.id)
        keys = {n: phonetic_keys(n) for n in sorted(set(by_last) | set(by_first))}
        names_by_key: dict[str, list[str]] = defaultdict(list)
        for n, ks in keys.items():
            for k in ks:
                names_by_key[k].append(n)
        return cls(last_of, first_of, {k: tuple(v) for k, v in by_last.items()},
                   {k: tuple(v) for k, v in by_first.items()}, keys,
                   {k: tuple(v) for k, v in names_by_key.items()})

    def score(self, heard: str, name: str) -> tuple[float, str]:
        return _word_score(heard, name, self.keys[name])

    def surnames_reaching(self, heard: str, floor: float) -> list[str]:
        """Surnames whose _word_score against `heard`, rounded as candidates are, is >= floor.
        Exact and sound-key hits always are (phonetic scores start at 0.88 > floor); the rest
        need their spelling score. One tight loop: this is the per-turn cost at national scale."""
        jw = jellyfish.jaro_winkler_similarity
        sounds = {n for k in phonetic_keys(heard) for n in self.names_by_key.get(k, ())}
        return [n for n in self.by_last
                if n in sounds or (s := jw(heard, n)) >= floor or (s >= floor - 0.001 and round(s, 3) >= floor)]

    def sounds_like_a_name(self, word: str) -> bool:
        """Some catalog name scores >= _NAME_WORD_SCORE against `word`."""
        return _sounds_like_a_name(self, word)


@lru_cache(maxsize=8192)
def _sounds_like_a_name(ni: NameIndex, word: str) -> bool:
    # Exact and shared-sound-key hits always reach 0.88 (phonetic scores start there), so only
    # spelling needs the scan over every distinct name.
    if word in ni.keys or any(k in ni.names_by_key for k in phonetic_keys(word)):
        return True
    jw = jellyfish.jaro_winkler_similarity
    return any(jw(word, n) >= _NAME_WORD_SCORE for n in ni.keys)


def _word_score(heard: str, actual: str, actual_keys: frozenset[str] | None = None) -> tuple[float, str]:
    if heard == actual:
        return 1.0, "exact"
    jw = jellyfish.jaro_winkler_similarity(heard, actual)
    if phonetic_keys(heard) & (phonetic_keys(actual) if actual_keys is None else actual_keys):
        # A shared sound key outranks any spelling-only neighbour, but never an exact hit.
        return round(0.88 + 0.1 * jw, 3), "phonetic"
    return jw, "fuzzy"


def _same_word(heard: str, actual: str) -> bool:
    return (jellyfish.jaro_winkler_similarity(heard, actual) >= 0.9
            or bool(phonetic_keys(heard) & phonetic_keys(actual)))


def _top_tier(cands: Iterable[NameCandidate], floor: float) -> list[NameCandidate]:
    ranked = sorted((c for c in cands if c.score >= floor), key=lambda c: (-c.score, c.id))
    if not ranked:
        return []
    top = ranked[0].score
    return [c for c in ranked if c.score >= top - TIE_GAP]


def match_providers(index: CatalogIndex, phrase: str | None, within: Iterable[str] | None = None) -> list[NameCandidate]:
    """Return the top tier of providers for a spoken name. "Dr. Chen" returns every Chen;
    "Emily Chen" returns one; "Dr. Nwin" reaches the Nguyens through the spoken-form key."""
    words = [w for w in tokens(phrase or "") if w not in _TITLE_WORDS]
    if not words:
        return []
    within = list(within) if within else None
    tier = _top_tier(_score_providers(index.name_index, words, within), MIN_PROVIDER_SCORE)
    if within and not tier:
        return match_providers(index, phrase)
    if not tier and len(words) > 1:
        # "Dr. Chen, the heart doctor": retry on the words that sound like a name, so the
        # clue words do not break the match. The clue itself is read by clue_words().
        names = [w for w in words if _is_name_word(index, w)]
        if names and names != words:
            return match_providers(index, " ".join(names), within)
    return tier


def _score_providers(ni: NameIndex, words: list[str], within: list[str] | None) -> list[NameCandidate]:
    memo: dict[tuple[str, str], tuple[float, str]] = {}

    def score(heard: str, name: str) -> tuple[float, str]:
        if (heard, name) not in memo:
            memo[(heard, name)] = ni.score(heard, name)
        return memo[(heard, name)]

    joined = "".join(words)  # "Mc Donald", "Ng Uyen"
    if within is not None:
        pool: Iterable[str] = within
    elif len(words) == 1:
        # Below the floor a provider is dropped anyway; only a lone exact first name ("Emily")
        # can lift a provider whose surname misses.
        pool = [pid for last in ni.surnames_reaching(words[0], MIN_PROVIDER_SCORE) for pid in ni.by_last[last]]
        pool += ni.by_first.get(words[0], ())
    else:
        lasts = dict.fromkeys(ni.surnames_reaching(words[-1], MIN_PROVIDER_SCORE)
                              + ni.surnames_reaching(joined, MIN_PROVIDER_SCORE))
        pool = [pid for last in lasts for pid in ni.by_last[last]]

    scored = {}
    for pid in pool:
        last, first = ni.last_of[pid], ni.first_of[pid]
        if len(words) == 1:
            s_last, via_last = score(words[0], last)
            s_first, _ = score(words[0], first)
            # A lone first name only counts when it is an exact hit ("Emily"), never a fuzzy one.
            if s_first == 1.0 and s_first > s_last:
                scored[pid] = NameCandidate(pid, 0.95, "first")
            else:
                scored[pid] = NameCandidate(pid, round(s_last, 3), via_last)
        else:
            s_last, via = score(words[-1], last)
            s_first, _ = score(words[0], first)
            s_joined, _ = score(joined, last)
            if s_joined > s_last:
                scored[pid] = NameCandidate(pid, round(s_joined, 3), "fuzzy")
                continue
            first_factor = 1.0 if s_first >= 0.88 else 0.9
            scored[pid] = NameCandidate(pid, round(s_last * first_factor, 3), via)
    return list(scored.values())


# Stricter than MIN_PROVIDER_SCORE: every misheard surname we have seen ("Nwin", "Garsha",
# "Smyth") scores >= 0.93 through its sound key, while clue words like "woman" (vs Omar) or
# "family" (vs Emily) land at 0.78-0.83. Short function words still collide through sound
# keys ("who" ~ Wei, "saw" ~ Sofia, "i" ~ Wei), so they are never names.
_NAME_WORD_SCORE = 0.88
_NEVER_NAMES = {"who", "he", "she", "her", "him", "his", "saw", "one", "the"}


def _is_name_word(index: CatalogIndex, word: str, provider_ids: Iterable[str] | None = None) -> bool:
    if word in _NEVER_NAMES or word in _CLUE_STOPWORDS:
        return False
    ni = index.name_index
    if not provider_ids:
        return ni.sounds_like_a_name(word)
    return any(ni.score(word, n)[0] >= _NAME_WORD_SCORE
               for pid in provider_ids for n in (ni.first_of[pid], ni.last_of[pid]))


_HONORIFICS = {"dr", "doctor", "doc"}
_CLUE_STOPWORDS = {"the", "a", "an", "one", "who", "that", "with", "is", "i", "me", "my", "to", "see", "can",
                   "could", "please", "at", "in", "on", "over", "of", "and", "for", "want", "like", "would",
                   "just", "there", "um", "uh", "okay", "ok", "yes", "yeah"}


def clue_words(index: CatalogIndex, phrase: str | None, candidate_ids: Iterable[str]) -> tuple[str, ...]:
    """Words in a provider phrase beyond the name, honorific and filler: specialty, site, title,
    gender or history words that could tell same-named candidates apart. Empty means the caller
    gave only a name, so only a question can split the candidates."""
    ids = list(candidate_ids)
    return tuple(w for w in tokens(phrase or "")
                 if w not in _HONORIFICS and w not in _CLUE_STOPWORDS and not _is_name_word(index, w, ids))


# Cached: every turn would otherwise re-tokenize every site's name and address.
@lru_cache(maxsize=8192)
def _location_words(loc: Location) -> frozenset[str]:
    return frozenset(w for w in tokens(loc.name) if w not in _LOCATION_GENERIC and w not in STREET_TYPES)


@dataclass(frozen=True, slots=True)
class Street:
    number: int | None
    words: tuple[str, ...]  # the street's name: ("market",), ("medical", "center"), ("2nd",)


@lru_cache(maxsize=8192)
def street_of(loc: Location) -> Street:
    """"3330 Market St" -> Street(3330, ("market",))."""
    words = tokens(loc.address)
    number = int(words.pop(0)) if words and words[0].isdigit() else None
    return Street(number, tuple(w for w in words if w not in STREET_TYPES))


@dataclass(frozen=True, slots=True)
class HeardPlace:
    words: tuple[str, ...]         # distinctive words, matched against site names and streets
    street_words: frozenset[str]   # words the caller put before a street type: streets only
    number: int | None             # a house number, from digits or spoken words


_ORDINALS = {"first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th", "sixth": "6th",
             "seventh": "7th", "eighth": "8th", "ninth": "9th", "tenth": "10th"}
_UNITS = {w: i for i, w in enumerate(("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                                      "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                                      "seventeen", "eighteen", "nineteen"))} | {"oh": 0}
_TENS = {w: 10 * i for i, w in enumerate(("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty",
                                          "ninety"), start=2)}
# Words that end a street name read backwards from its type: "the clinic on | Market Street".
_STREET_STOP = _LOCATION_GENERIC - {"health", "center", "centre", "family", "medical", "community", "care"} | {
    "near", "by", "off", "of", "to", "from", "and", "over", "is", "it", "s", "that", "my"}


def _is_number_word(w: str) -> bool:
    return w.isdigit() or w in _UNITS or w in _TENS or w == "hundred"


def _spoken_number(run: list[str]) -> int | None:
    """STT house numbers: "3330", "33 30", "thirty three thirty", "eighteen twelve", "five oh five",
    "forty eight hundred". Each spoken group of up to two digits is written out in turn."""
    out, cur = "", None
    for w in run:
        if w.isdigit():
            out, cur = out + ("" if cur is None else str(cur)) + w, None
        elif w == "hundred":
            cur = (1 if cur is None else cur) * 100
        elif w in _TENS:
            if cur is not None and cur >= 100 and cur % 100 == 0:
                cur += _TENS[w]
            else:
                out, cur = out + ("" if cur is None else str(cur)), _TENS[w]
        else:
            v = _UNITS[w]
            if cur is not None and ((cur >= 20 and cur % 10 == 0 and v < 10) or (cur >= 100 and cur % 100 == 0)):
                cur += v
            else:
                out, cur = out + ("" if cur is None else str(cur)), v
    out += "" if cur is None else str(cur)
    return int(out) if out else None


def hear_place(phrase: str | None) -> HeardPlace:
    raw = tokens(phrase or "")
    explicit: set[str] = set()
    for i, w in enumerate(raw):
        if w not in STREET_TYPES:
            continue
        if i and raw[i - 1] in _ORDINALS:
            raw[i - 1] = _ORDINALS[raw[i - 1]]  # "second street" -> "2nd"
        # The street's own name is the one or two words before its type.
        for j in range(i - 1, max(i - 3, -1), -1):
            if raw[j] in _STREET_STOP or raw[j] in STREET_TYPES or _is_number_word(raw[j]):
                break
            explicit.add(raw[j])
    number = None
    for i, w in enumerate(raw):
        if not _is_number_word(w):
            continue
        j = i
        while j < len(raw) and _is_number_word(raw[j]):
            j += 1
        # "the one on Lincoln": a lone "one" or "oh" is never a house number.
        if raw[i:j] not in (["one"], ["oh"], ["hundred"]):
            number = _spoken_number(raw[i:j])
            raw = raw[:i] + raw[j:]
            break
    words = tuple(w for w in raw if w in explicit or (w not in _LOCATION_GENERIC and w not in STREET_TYPES))
    return HeardPlace(words, frozenset(explicit), number)


def match_locations(index: CatalogIndex, phrase: str | None, within: Iterable[str] | None = None) -> list[NameCandidate]:
    """Distinctive-word overlap: "Mission Bay" -> loc_000 only; "Mission" -> both Missions;
    "North Beach" never matches "North Gate" because "beach" is the deciding word. Street words
    count too, fully when the caller said the street type ("Market Street"); a house number then
    picks the site on that street ("3330 Market" -> via "address")."""
    heard = hear_place(phrase)
    words = list(heard.words)
    if not words:
        if heard.number is None or not within:
            return []
        # "The 3330 one", answering "Downtown at 1812 Market or Willow Glen at 3330 Market?"
        return [NameCandidate(i, 1.0, "address") for i in within
                if street_of(index.locations[i]).number == heard.number]
    if len(words) > 1 and any("".join(words) in _location_words(loc) for loc in index.locations.values()):
        words = ["".join(words)]  # "down town" -> "downtown"
    pool = [index.locations[i] for i in within] if within else list(index.locations.values())
    scored, on_street = [], []
    for loc in pool:
        name_words = _location_words(loc)
        street = street_of(loc).words
        hits, street_hits, said_street = 0, 0.0, set()
        for w in words:
            if w not in heard.street_words and any(_same_word(w, nw) for nw in name_words):
                hits += 1
            elif any(_same_word(w, sw) for sw in street):
                street_hits += 1.0 if w in heard.street_words else 0.6
                if w in heard.street_words:
                    said_street.update(sw for sw in street if _same_word(w, sw))
        if not hits and not street_hits:
            continue
        if street_hits:
            on_street.append(loc)
        phrase_cov = (hits + street_hits) / len(words)
        # "Center Street" covers Center St whole, but only half of Medical Center Dr.
        name_cov = hits / len(name_words) if hits else len(said_street) / len(street) if said_street else 0.0
        scored.append(NameCandidate(loc.id, round(0.7 * phrase_cov + 0.3 * name_cov, 3),
                                    "exact" if hits else "street"))
    pinned = _by_house_number(on_street, heard.number) if heard.number is not None else []
    tier = [NameCandidate(l, 1.0, "address") for l in pinned] or _top_tier(scored, MIN_LOCATION_SCORE)
    if within and not tier:
        return match_locations(index, phrase)
    return tier


def _by_house_number(on_street: list[Location], number: int) -> list[str]:
    """Sites on the named street at that number. A number nobody has picks the site whose number
    is clearly nearest, within one city only: STT garbles digits ("3300" for "3330") far more
    often than a caller invents an address, and the offer names the site, so a wrong pick is
    heard and corrected. Close calls pick nothing and the street's sites stay to be asked about."""
    exact = [l.id for l in on_street if street_of(l).number == number]
    if exact or len({l.metro_id for l in on_street}) != 1:
        return exact
    ranked = sorted((abs(street_of(l).number - number), l.id) for l in on_street if street_of(l).number is not None)
    if len(ranked) == 1 or (ranked and 2 * ranked[0][0] < ranked[1][0]):
        return [ranked[0][1]]
    return []
