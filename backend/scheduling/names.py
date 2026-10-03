"""Provider and location phrases -> scored candidates, tolerant of speech-to-text spellings."""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from itertools import takewhile
from typing import TYPE_CHECKING, Callable, Iterable

import jellyfish

from .per_index import per_index
from .text import normalize, phonetic_keys, stem, tokens

if TYPE_CHECKING:
    from .catalog_index import CatalogIndex, Location, Provider

_TITLE_WORDS = {"dr", "doctor", "doc", "the", "with", "md", "np", "pa", "do", "nurse", "practitioner"}
_LOCATION_GENERIC = {"health", "center", "centre", "clinic", "family", "specialty", "medical", "group",
                     "community", "care", "the", "one", "office", "location", "in", "at", "on"}
# Street types as written in an address -> as said aloud. Never evidence for a site: "Lincoln
# Avenue" names Lincoln, and "avenue" must not reach The Avenues.
STREET_TYPE_NAMES = {
    "st": "Street", "street": "Street", "ave": "Avenue", "av": "Avenue", "avenue": "Avenue", "blvd": "Boulevard",
    "boulevard": "Boulevard", "rd": "Road", "road": "Road", "dr": "Drive", "drive": "Drive", "ln": "Lane",
    "lane": "Lane", "way": "Way", "pl": "Place", "place": "Place", "ct": "Court", "court": "Court",
    "pkwy": "Parkway", "parkway": "Parkway", "hwy": "Highway", "highway": "Highway", "ter": "Terrace",
    "terrace": "Terrace", "cir": "Circle", "circle": "Circle"}
STREET_TYPES = frozenset(STREET_TYPE_NAMES)
MIN_PROVIDER_SCORE = 0.78
MIN_LOCATION_SCORE = 0.4
# The score that names a site outright, not a word of it: an answer to "which clinic?" that
# strongly names a different site is a new choice; anything weaker only describes the options.
STRONG_LOCATION_SCORE = 0.7
TIE_GAP = 0.08
_SOUNDALIKE_HIT = 0.9


@dataclass(frozen=True)
class NameCandidate:
    id: str
    score: float
    via: str  # "exact" | "fuzzy" | "phonetic" | "first"; locations: "exact" | "street" | "address"

    @property
    def by_street(self) -> bool:
        """A location the caller named by its street or address, not by its name."""
        return self.via in ("street", "address")


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
    # "Dr. Chen, the family medicine one": "one" sounds like Nguyen ("win") but names nobody here.
    ni = index.name_index
    words = [w for w in tokens(phrase or "") if w not in _TITLE_WORDS
             and (w not in _NEVER_NAMES or w in ni.by_last or w in ni.by_first)]
    if not words:
        return []
    within = list(within) if within else None
    tier = _top_tier(_score_providers(index.name_index, words, within), MIN_PROVIDER_SCORE)
    if len(words) > 1 and words[-1] not in ni.keys:
        # "Dr. Inna Volkov, a friend told me about her": the last word is no name of ours, and read
        # as a surname, "about" sounds like Abbott. A name is said first or right after "Dr.".
        for said in _names_said(index, phrase):
            named = _top_tier(_score_providers(ni, said, within), MIN_PROVIDER_SCORE)
            if named and (not tier or named[0].score > tier[0].score):
                tier = named
    if within:
        # An answer to a provider confirmation may name someone else: a
        # better match outside the options asked about wins, but only when the answer actually
        # says that provider's name. Describing the options instead ("the one who speaks
        # another language") names nobody outside them, and a fuzzy echo of a description never widens.
        anywhere = match_providers(index, phrase)
        # A catalog language or credential is an attribute even if its sound collides with a name.
        raw = tokens(phrase or "")
        # A new name starts the answer, possibly after a correction or honorific. Words later
        # in "the one at ..." or "the one who speaks ..." remain descriptions, even when fuzzy
        # name matching gives those words a high score.
        lead = {"actually", "instead", "rather", "sorry", "wait"} | _NO | _YES | _HONORIFICS
        start = next((w for w in raw if w not in lead), None)
        named = (anywhere and start and start not in _provider_attributes(index)
                 and _is_name_word(index, start, [c.id for c in anywhere]))
        if named and (not tier or anywhere[0].score > tier[0].score):
            return anywhere
        return tier
    if not tier and len(words) > 1:
        # "Dr. Chen, the heart doctor": retry on the words that sound like a name, so the
        # clue words do not break the match. The clue itself is read by clue_words().
        names = [w for w in words if _is_name_word(index, w)]
        if names and names != words:
            return match_providers(index, " ".join(names), within)
    return tier


@per_index
def _provider_attributes(index: CatalogIndex) -> set[str]:
    return ({w for p in index.providers.values() for lang in p.languages for w in tokens(lang)}
            | set(_TITLE_CLUES) | _CLUE_LINKS)


def _names_said(index: CatalogIndex, phrase: str | None) -> list[list[str]]:
    """The catalog names (first, last; at most two) that open the phrase or follow "Dr."/"doctor":
    "Dr. Inna Volkov, a friend told me" -> [["inna", "volkov"]]. A surname that is also a word
    ("friend") elsewhere in the phrase is not taken for a name."""
    raw = tokens(phrase or "")
    starts = [0] + [i + 1 for i, w in enumerate(raw) if w in _HONORIFICS]
    runs = [list(takewhile(lambda w: is_catalog_name(index, w), raw[i:i + 2])) for i in starts]
    return [r for r in dict.fromkeys(map(tuple, runs)) if r and r[-1] in index.name_index.by_last]


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
            # The first name is said right before the surname: "actually Linda Ramirez".
            s_first, _ = score(words[-2], first)
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
_YES = frozenset({"yes", "yeah", "yep", "yup", "yea", "correct", "right", "exactly", "sure", "ok", "okay", "mhm",
                  "mmhmm", "uhhuh"})
_NO = frozenset({"no", "nope", "nah", "wrong", "different"})
# Yes and no words too, and the sounds of one ("uh-huh", "mm-hmm"): "no, Lucas Chen" must not read
# "no" as the first name, and a bare "nah" or "yup" is no Dr. Na or Dr. Yap.
_NEVER_NAMES = frozenset({"who", "he", "she", "her", "him", "his", "saw", "one", "the", "not", "uh", "huh", "mm",
                          "hmm"}) | _YES | _NO


def is_catalog_name(index: CatalogIndex, word: str) -> bool:
    """Exactly some provider's first or last name, and never a function word ("he", "who")."""
    return word in index.name_index.keys and word not in _NEVER_NAMES and word not in _CLUE_STOPWORDS


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
                   "just", "there", "um", "uh", "okay", "ok", "yes", "yeah",
                   # A change of mind says nothing about the doctor: "Dr. Michael Sato instead".
                   "actually", "instead", "rather", "sorry", "wait"}


def clue_words(index: CatalogIndex, phrase: str | None, candidate_ids: Iterable[str]) -> tuple[str, ...]:
    """Words in a provider phrase beyond the name, honorific and filler: specialty, site, title,
    gender or history words that could tell same-named candidates apart. Empty means the caller
    gave only a name, so only a question can split the candidates."""
    ids = list(candidate_ids)
    return tuple(w for w in tokens(phrase or "")
                 if w not in _HONORIFICS and w not in _CLUE_STOPWORDS and not _is_name_word(index, w, ids))


_TITLE_CLUES = {"np": "NP", "nurse": "NP", "practitioner": "NP", "pa": "PA", "assistant": "PA", "md": "MD"}
# Words that only tie a fact to the doctor ("who speaks", "works at", "sees kids").
_CLUE_LINKS = frozenset({"speaks", "speak", "speaking", "language", "works", "work", "working", "sees", "seeing",
                         "treats", "practices", "located", "office", "specialist", "physician", "he", "she", "s"})
# Specialty words too common to name one specialty on their own.
_SPECIALTY_FILLER = frozenset({"general", "care", "medicine", "work", "and", "therapy"})
# "not the one who speaks Spanish", "isn't", "other than": the fact is the one the caller does not want.
_NEGATIONS = frozenset({"not", "t", "no", "never", "other", "except", "without", "nor", "neither"})
# The caller's relatives: "the one my daughter recommended" names no specialty. A patient group
# ("who sees kids") does, unless it is the caller's own ("my kids").
_KIN = frozenset({"son", "sons", "daughter", "daughters", "child", "kid", "boy", "boys", "girl", "girls", "baby",
                  "babies"})
_PATIENT_GROUPS = frozenset({"kids", "children"})
_POSSESSIVES = frozenset({"my", "our", "his", "her", "their", "your"})
# Only words that describe the doctor say their gender: "the woman", "a male doctor", "the guy".
# "women's health" names a field, and "her" / "his" are as often someone else's.
_GENDER_NOUNS = {"woman": "female", "lady": "female", "female": "female",
                 "man": "male", "male": "male", "guy": "male", "gentleman": "male"}
# "she's the one at Mission Bay" is the doctor; in "my wife says she's great" it is not.
_PRONOUNS = {"she": "female", "he": "male"}
_OTHER_PEOPLE = _KIN | _PATIENT_GROUPS | frozenset({
    "wife", "husband", "partner", "mom", "mother", "mum", "dad", "father", "sister", "brother", "friend",
    "neighbor", "neighbour", "aunt", "uncle", "grandma", "grandmother", "grandpa", "grandfather", "cousin",
    "boyfriend", "girlfriend", "coworker", "colleague", "boss"})


def _said_gender(raw: list[str]) -> tuple[str | None, set[str]]:
    """The doctor's gender as the caller described it, and the words that said it: a gender noun
    anywhere, or "he" / "she" before any other person is mentioned. Both genders: None."""
    said = {w: _GENDER_NOUNS[w] for w in raw if w in _GENDER_NOUNS}
    for i, w in enumerate(raw):
        if w in _PRONOUNS and not set(raw[:i]) & _OTHER_PEOPLE:
            said[w] = _PRONOUNS[w]
    genders = set(said.values())
    return (genders.pop() if len(genders) == 1 else None), set(said)


@dataclass(frozen=True)
class ProviderClues:
    """What a provider phrase says beyond the name. `fits`: the candidates left by every catalog fact
    the caller named (language, title, specialty, site); `sites`: the clinics a site fact named;
    `gender`: "female" or "male", a fact the catalog does not hold; `rest`: clue words neither
    explains ("the one I saw last time"); `negated`: the phrase says who the caller does not mean,
    so nothing narrows."""

    words: tuple[str, ...]
    fits: tuple[str, ...]
    facts: tuple[str, ...] = ()
    gender: str | None = None
    rest: tuple[str, ...] = ()
    negated: bool = False
    sites: tuple[str, ...] = ()


def read_provider_clues(index: CatalogIndex, phrase: str | None, candidate_ids: Iterable[str]) -> ProviderClues:
    """"Dr. Nguyen, he speaks Spanish" -> fits the Spanish-speaking Nguyens, gender male, no rest.
    A fact that fits none of the candidates is ignored: the caller misremembered, and asking which
    one they mean is the honest answer."""
    ids = sorted(candidate_ids)
    words = clue_words(index, phrase, ids)
    raw = tokens(phrase or "")
    if words and set(raw) & _NEGATIONS:
        return ProviderClues(words, tuple(ids), negated=True)
    kin = {w for i, w in enumerate(raw)
           if w in _KIN or (w in _PATIENT_GROUPS and i and raw[i - 1] in _POSSESSIVES)}
    providers = [index.providers[p] for p in ids]
    explained: set[str] = set()
    facts: list[str] = []

    def narrow(kind: str, keep: Callable[[Provider], bool], used: set[str]) -> None:
        nonlocal providers
        kept = [p for p in providers if keep(p)]
        if used and kept:
            providers = kept
            facts.append(kind)
            explained.update(used)

    heard = set(words)
    languages = {lang for p in providers for lang in p.languages if set(tokens(lang)) <= heard}
    narrow("language", lambda p: bool(languages & set(p.languages)),
           {w for lang in languages for w in tokens(lang)})
    titles = {_TITLE_CLUES[w] for w in words if w in _TITLE_CLUES}
    narrow("title", lambda p: p.title in titles, {w for w in words if w in _TITLE_CLUES})
    cues = _specialty_cues(index)
    by_word = {w: specs for w in words if w not in kin and (specs := _specialties_named(cues, w))}
    specialties = set().union(*by_word.values()) if by_word else set()
    narrow("specialty", lambda p: p.specialty in specialties,
           set(by_word) | {w for w in words if w in _SPECIALTY_FILLER})
    gender, gender_words = _said_gender(raw)
    sites, said = _sites_named(index, [w for w in words if w not in explained and w not in _GENDER_NOUNS
                                       and w not in _CLUE_LINKS and w not in kin],
                               hear_place(phrase).street_words, {lid for p in providers for lid in p.location_ids})
    narrow("site", lambda p: bool(sites & set(p.location_ids)), said)
    if "site" in facts:
        # "the one over at the Midtown clinic": "clinic" belongs to the place, not to the doctor.
        explained.update(w for w in words if w in _LOCATION_GENERIC)

    if gender:
        explained.update(gender_words)
    if facts or gender:
        explained.update(w for w in words if w in _CLUE_LINKS)
    rest = tuple(w for w in words if w not in explained)
    return ProviderClues(words, tuple(p.id for p in providers), tuple(facts), gender, rest,
                         sites=tuple(sorted(sites)) if "site" in facts else ())


_CONFIRM_FILLER = frozenset({"that", "s", "it", "is", "the", "one", "her", "him", "she", "he", "um", "uh", "huh",
                             "please", "i", "mean", "meant", "who", "doctor", "dr", "a", "someone", "somebody",
                             "else", "isn", "wasn", "don", "doesn", "didn"})


def read_confirmation(phrase: str | None) -> bool | None:
    """The answer to a question about one doctor ("Do you mean Dr. Emily Chen?"). False when the
    caller says no in any words ("no", "not her", "the other one", "no, Lucas Chen"): that doctor
    is out, and a name said with the no is not trusted to pick another. True for a bare yes
    ("yes", "yeah, that's her"). None for anything else, which is matched as a name."""
    words = set(tokens(phrase or ""))
    if words & (_NO | _NEGATIONS):
        return False
    return True if words & _YES and words <= _YES | _CONFIRM_FILLER else None


_LEAD_FILLER = frozenset({"uh", "um", "oh", "well", "hmm", "mm"})


def leading_answer(phrase: str | None) -> bool | None:
    """A yes or a no that opens an answer to a yes-or-no question ("yeah, nothing after midnight",
    "no, I can eat"); None when it opens with anything else."""
    first = next((w for w in tokens(phrase or "") if w not in _LEAD_FILLER), None)
    return True if first in _YES else False if first in _NO else None


# Words before a surname that are no first name: "actually Dr. Ramirez", "with Ramirez".
_NOT_FIRST_NAMES = _TITLE_WORDS | _CLUE_STOPWORDS | _NEVER_NAMES | _HONORIFICS | frozenset({
    "mr", "mrs", "ms", "miss", "actually", "instead", "rather", "maybe", "sorry", "wait", "mean", "meant", "think",
    "guess", "book", "booked", "seeing", "seen", "prefer", "named", "called", "last", "name"})


def unmatched_first_name(index: CatalogIndex, phrase: str | None, candidate_ids: Iterable[str]) -> str | None:
    """The first name the caller said with a surname ("Linda" in "actually Dr. Linda Ramirez") when
    none of the doctors that surname matched has a first name like it; None when no first name was
    said, or one matches. A first name is the word before the surname, said after "Dr." or opening
    the phrase, or one some doctor here has."""
    ids = list(candidate_ids)
    ni = index.name_index
    words = tokens(phrase or "")
    lasts = {ni.last_of[p] for p in ids}
    for i, w in enumerate(words):
        if i == 0 or not any(_same_word(w, last) for last in lasts):
            continue
        first = words[i - 1]
        if first in _NOT_FIRST_NAMES or first.isdigit() or first + w in ni.by_last:  # "Mc Donald"
            continue
        named = i == 1 or words[i - 2] in _HONORIFICS or first in ni.by_first
        if named and not any(ni.score(first, ni.first_of[p])[0] >= _NAME_WORD_SCORE for p in ids):
            return first
    return None


def only_no(phrase: str | None) -> bool:
    """A no and nothing else to hear ("no", "nope, not that one"); "no, Trenton, New Jersey" says more."""
    words = set(tokens(phrase or ""))
    return bool(words & (_NO | _NEGATIONS)) and words <= _NO | _NEGATIONS | _CONFIRM_FILLER


def _sites_named(index: CatalogIndex, words: list[str], street_words: frozenset[str],
                 pool: set[str]) -> tuple[set[str], set[str]]:
    """The sites of `pool` a provider description names, and the words that named them. Only a
    site's own name words count, exactly as said ("Mission Bay"), and its street when said as a
    street ("on Geary Boulevard"): an ordinary word that sounds like a site ("man" ~ Main St,
    "boy" ~ Bay) is no fact about the doctor. The sites matching the most words win."""
    hits = {lid: {w for w in words if w in _location_words(index.locations[lid])
                  or (w in street_words and w in street_of(index.locations[lid]).words)} for lid in pool}
    best = max((len(h) for h in hits.values()), default=0)
    sites = {lid for lid, h in hits.items() if h and len(h) == best}
    return sites, {w for lid in sites for w in hits[lid]}


# eq=False: identity hash, so _specialties_named can cache per catalog.
@dataclass(frozen=True, eq=False)
class _Cues:
    """The words that name a specialty: its name, its spoken form, a one-word lay term."""

    specialties: dict[str, frozenset[str]]  # cue -> the specialties it names
    stems: tuple[tuple[str, str], ...]      # (cue, stem of the cue)


@per_index
def _specialty_cues(index: CatalogIndex) -> _Cues:
    cues: dict[str, set[str]] = defaultdict(set)
    for spec in index.specialties:
        for w in tokens(spec) + tokens(index.specialty_spoken.get(spec, "")):
            cues[w].add(spec)
    for term, spec in index.lay_terms.items():
        if " " not in term:
            cues[term].add(spec)
    kept = {w: frozenset(s) for w, s in cues.items()
            if w not in _SPECIALTY_FILLER and w not in _CLUE_STOPWORDS and len(w) > 2}
    return _Cues(kept, tuple((w, stem(w)) for w in kept))


def specialties_named(index: CatalogIndex, word: str) -> frozenset[str]:
    """The specialties one word names: "pediatrician" Pediatrics, "cardiologist" Cardiology."""
    return _specialties_named(_specialty_cues(index), word)


@lru_cache(maxsize=8192)
def _specialties_named(cues: _Cues, word: str) -> frozenset[str]:
    """"pediatrician" names Pediatrics, "cardiologist" Cardiology, "kids" Pediatrics (a lay term):
    the same word, or a shared stem of at least six letters covering most of the shorter word."""
    word_stem = stem(word)
    out: set[str] = set()
    for cue, cue_stem in cues.stems:
        common = len(os.path.commonprefix([cue, word]))
        shared_stem = common >= 6 and common >= 0.75 * min(len(cue), len(word))
        if cue in (word, word.removesuffix("s")) or cue_stem == word_stem or shared_stem:
            out |= cues.specialties[cue]
    return frozenset(out)


# Cached: every turn would otherwise re-tokenize every site's name and address.
@lru_cache(maxsize=8192)
def _location_words(loc: Location) -> frozenset[str]:
    return frozenset(w for w in tokens(loc.name) if w not in _LOCATION_GENERIC and w not in STREET_TYPES)


@dataclass(frozen=True, slots=True)
class Street:
    number: int | None
    words: tuple[str, ...]  # the street's name, to match: ("market",), ("medical", "center"), ("2nd",)
    name: str = ""          # the street's name as written: "Market", "N Lamar"
    kind: str = ""          # its type as said aloud: "Street", "Boulevard"; "" for "7095 Broadway"

    @property
    def said(self) -> str:
        """"Market Street", as said aloud."""
        return f"{self.name} {self.kind}".strip()


@lru_cache(maxsize=8192)
def street_of(loc: Location) -> Street:
    """"3330 Market St" -> Street(3330, ("market",), "Market", "Street")."""
    words = tokens(loc.address)
    number = int(words.pop(0)) if words and words[0].isdigit() else None
    written = loc.address.split()
    if written and written[0].isdigit():
        written.pop(0)
    kind = written[-1].rstrip(".") if len(written) > 1 and written[-1].rstrip(".").lower() in STREET_TYPES else ""
    if kind:
        written.pop()
    return Street(number, tuple(w for w in words if w not in STREET_TYPES), " ".join(written),
                  STREET_TYPE_NAMES.get(kind.lower(), kind))


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
            explicit.add(raw[i - 1])  # the street as said, so it is not taken for a description
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
    site_words = _site_words(index)
    if len(words) > 1 and "".join(words) in site_words.named:
        words = ["".join(words)]  # "down town" -> "downtown"
    reached = set().union(*(_sites_sounding(site_words, w) for w in words))
    pool = [index.locations[i] for i in within] if within else list(index.locations.values())
    scored, on_street = [], []
    for loc in pool:
        if loc.id not in reached:
            continue
        name_words = _location_words(loc)
        street = street_of(loc).words
        hits, street_hits, said_street = 0.0, 0.0, set()
        for w in words:
            if w not in heard.street_words and any(_same_word(w, nw) for nw in name_words):
                # "The Hill" names The Hill outright; Hialeah only shares its sound key.
                hits += 1.0 if w in name_words else _SOUNDALIKE_HIT
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
        # Answering "which clinic?" may name a different one outright; words that describe rather
        # than name ("the closer one") reach nobody past the options, and the question is repeated.
        widened = match_locations(index, phrase)
        return widened if widened and widened[0].score >= STRONG_LOCATION_SCORE else []
    return tier


# eq=False: identity hash, so _sites_sounding can cache per catalog.
@dataclass(frozen=True, eq=False)
class _SiteWords:
    """Each distinct word of the catalog's site names and of their streets, with the sites using it."""

    named: dict[str, tuple[str, ...]]
    on_street: dict[str, tuple[str, ...]]


@per_index
def _site_words(index: CatalogIndex) -> _SiteWords:
    named: dict[str, list[str]] = defaultdict(list)
    on_street: dict[str, list[str]] = defaultdict(list)
    for loc in index.locations.values():
        for w in _location_words(loc):
            named[w].append(loc.id)
        for w in street_of(loc).words:
            on_street[w].append(loc.id)
    return _SiteWords({w: tuple(ids) for w, ids in named.items()}, {w: tuple(ids) for w, ids in on_street.items()})


@lru_cache(maxsize=8192)
def _sites_sounding(site_words: _SiteWords, heard: str) -> frozenset[str]:
    """Sites with a name or street word that is `heard` as said or as misheard: the only sites
    match_locations can score."""
    return frozenset(lid for table in (site_words.named, site_words.on_street) for w, ids in table.items()
                     if _same_word(heard, w) for lid in ids)


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
