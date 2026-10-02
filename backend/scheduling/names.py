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
                     "community", "care", "the", "one", "office", "location", "in", "at", "on", "st", "street",
                     "blvd", "boulevard", "ave"}
MIN_PROVIDER_SCORE = 0.78
MIN_LOCATION_SCORE = 0.4
TIE_GAP = 0.08


@dataclass(frozen=True)
class NameCandidate:
    id: str
    score: float
    via: str  # "exact" | "fuzzy" | "phonetic" | "first"


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
    return frozenset(w for w in tokens(loc.name) if w not in _LOCATION_GENERIC)


@lru_cache(maxsize=8192)
def _street_words(loc: Location) -> frozenset[str]:
    return frozenset(w for w in tokens(loc.address) if w not in _LOCATION_GENERIC and not w.isdigit())


def match_locations(index: CatalogIndex, phrase: str | None, within: Iterable[str] | None = None) -> list[NameCandidate]:
    """Distinctive-word overlap: "Mission Bay" -> loc_000 only; "Mission" -> both Missions;
    "North Beach" never matches "North Gate" because "beach" is the deciding word."""
    words = [w for w in tokens(phrase or "") if w not in _LOCATION_GENERIC]
    if not words:
        return []
    if len(words) > 1 and any("".join(words) in _location_words(loc) for loc in index.locations.values()):
        words = ["".join(words)]  # "down town" -> "downtown"
    pool = [index.locations[i] for i in within] if within else list(index.locations.values())
    scored = []
    for loc in pool:
        name_words = _location_words(loc)
        street = _street_words(loc)
        hits, street_hits = 0, 0
        for w in words:
            if any(_same_word(w, nw) for nw in name_words):
                hits += 1
            elif any(_same_word(w, sw) for sw in street):
                street_hits += 1
        if not hits and not street_hits:
            continue
        phrase_cov = (hits + 0.6 * street_hits) / len(words)
        name_cov = hits / len(name_words) if name_words else 0.0
        scored.append(NameCandidate(loc.id, round(0.7 * phrase_cov + 0.3 * name_cov, 3),
                                    "exact" if hits else "street"))
    tier = _top_tier(scored, MIN_LOCATION_SCORE)
    if within and not tier:
        return match_locations(index, phrase)
    return tier
