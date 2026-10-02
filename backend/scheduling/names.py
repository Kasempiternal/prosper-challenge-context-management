"""Provider and location phrases -> scored candidates, tolerant of speech-to-text spellings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import jellyfish

from .catalog_index import CatalogIndex, Location, Provider
from .text import normalize, phonetic_keys, tokens

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


def _word_score(heard: str, actual: str) -> tuple[float, str]:
    if heard == actual:
        return 1.0, "exact"
    jw = jellyfish.jaro_winkler_similarity(heard, actual)
    if phonetic_keys(heard) & phonetic_keys(actual):
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
    pool: list[Provider] = [index.providers[i] for i in within] if within else list(index.providers.values())
    scored = []
    for prov in pool:
        last, first = normalize(prov.last_name), normalize(prov.first_name)
        if len(words) == 1:
            s_last, via_last = _word_score(words[0], last)
            s_first, _ = _word_score(words[0], first)
            # A lone first name only counts when it is an exact hit ("Emily"), never a fuzzy one.
            if s_first == 1.0 and s_first > s_last:
                scored.append(NameCandidate(prov.id, 0.95, "first"))
            else:
                scored.append(NameCandidate(prov.id, round(s_last, 3), via_last))
        else:
            s_last, via = _word_score(words[-1], last)
            s_first, _ = _word_score(words[0], first)
            joined, _ = _word_score("".join(words), last)  # "Mc Donald", "Ng Uyen"
            if joined > s_last:
                scored.append(NameCandidate(prov.id, round(joined, 3), "fuzzy"))
                continue
            first_factor = 1.0 if s_first >= 0.88 else 0.9
            scored.append(NameCandidate(prov.id, round(s_last * first_factor, 3), via))
    tier = _top_tier(scored, MIN_PROVIDER_SCORE)
    if within and not tier:
        return match_providers(index, phrase)
    if not tier and len(words) > 1:
        # "Dr. Chen, the heart doctor": retry on the words that sound like a name, so the
        # clue words do not break the match. The clue itself is read by clue_words().
        names = [w for w in words if _is_name_word(index, w)]
        if names and names != words:
            return match_providers(index, " ".join(names), within)
    return tier


# Stricter than MIN_PROVIDER_SCORE: every misheard surname we have seen ("Nwin", "Garsha",
# "Smyth") scores >= 0.93 through its sound key, while clue words like "woman" (vs Omar) or
# "family" (vs Emily) land at 0.78-0.83. Short function words still collide through sound
# keys ("who" ~ Wei, "saw" ~ Sofia, "i" ~ Wei), so they are never names.
_NAME_WORD_SCORE = 0.88
_NEVER_NAMES = {"who", "he", "she", "her", "him", "his", "saw", "one", "the"}


def _is_name_word(index: CatalogIndex, word: str, provider_ids: Iterable[str] | None = None) -> bool:
    if word in _NEVER_NAMES or word in _CLUE_STOPWORDS:
        return False
    pool = [index.providers[i] for i in provider_ids] if provider_ids else index.providers.values()
    return any(_word_score(word, normalize(n))[0] >= _NAME_WORD_SCORE
               for p in pool for n in (p.first_name, p.last_name))


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


def _location_words(loc: Location) -> set[str]:
    return {w for w in tokens(loc.name) if w not in _LOCATION_GENERIC}


def _street_words(loc: Location) -> set[str]:
    return {w for w in tokens(loc.address) if w not in _LOCATION_GENERIC and not w.isdigit()}


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
