"""Caller phrase -> scored appointment-type candidates (type names + hand-written aliases)."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from itertools import takewhile
from types import MappingProxyType
from typing import Callable, Mapping

import jellyfish

from .catalog_index import Alias, CatalogIndex
from .names import is_catalog_name
from .per_index import per_index
from .request import TIME_WORDS
from .text import normalize, stem, tokens

_GENERIC = {"consultation", "consult", "visit", "exam", "test", "session", "evaluation", "screening", "of",
            "appointment", "a", "an", "the", "my", "for", "and", "with", "i", "need", "want", "to", "get", "some"}
MIN_SCORE = 0.4
_OFF_HINT_PENALTY = 0.6
_NON_NAME_WHEN_NAMED = 0.85
_STRONG = 0.7
_SPECIALTY_DEFAULT_SCORE = 0.75
# Words a type name carries that say nothing about it: "MRI - Brain", "Vaccination / Immunization".
_NAME_STOP = {"of", "a", "an", "the", "and", "with", "for"}
# Words callers use for a type-name word, either way: "yearly physical" says the name "Annual
# Physical". English, not catalog data, so every catalog shares it (aliases.json is per catalog).
_SAME_AS = {"yearly": "annual"}
_SAME = {**_SAME_AS, **{v: k for k, v in _SAME_AS.items()}}
# A model choosing among every offered type is fine at SF's 74; at national scale (~300) the
# request is cut to the types the phrase plausibly reaches.
SHORTLIST_ABOVE = 80
SHORTLIST_SIZE = 20


@dataclass(frozen=True)
class TypeCandidate:
    type_id: str
    score: float
    via: str  # "name" | "alias" | "words" | "specialty"


def _token_match(a: str, b: str) -> bool:
    if a == b or stem(a) == stem(b) or _SAME.get(a) == b:
        return True
    # Fuzzy only for long, similar-length words: "checkup" must not match "check".
    return (min(len(a), len(b)) > 4 and abs(len(a) - len(b)) <= 1
            and jellyfish.jaro_winkler_similarity(a, b) >= 0.92)


def _find_spans(hay: list[str], needle: list[str]) -> list[int]:
    n = len(needle)
    return [i for i in range(len(hay) - n + 1) if all(_token_match(hay[i + k], needle[k]) for k in range(n))]


def _find_span(hay: list[str], needle: list[str]) -> int:
    return next(iter(_find_spans(hay, needle)), -1)


# eq=False: identity hash, so _word_hits can cache per vocabulary.
@dataclass(frozen=True, slots=True, eq=False)
class _Vocab:
    """Every word a type name or alias uses, bucketed so a phrase word is compared only with
    words it could match: the same stem, or (fuzzy) a long word of similar length."""

    by_stem: dict[str, tuple[str, ...]]
    by_len: dict[int, tuple[str, ...]]
    aliases_by_word: dict[str, tuple[int, ...]]   # word -> positions in index.aliases
    alias_words: tuple[frozenset[str], ...]
    types_by_name_word: dict[str, tuple[str, ...]]
    name_words: dict[str, tuple[str, ...]]        # type id -> non-generic name words
    by_name: dict[str, tuple[str, ...]]           # normalized type name -> type ids
    name_parts: dict[str, tuple[tuple[str, ...], ...]]  # type id -> each "/" alternative's words
    lay_term_words: int                           # words in the longest lay term
    lay_words: frozenset[str]                     # every word of a lay term
    place_words: frozenset[str]                   # every word of a clinic's or city's name
    said_words: dict[str, frozenset[str]]         # type id -> every word of its name and its aliases

    @classmethod
    def build(cls, index: CatalogIndex) -> "_Vocab":
        name_words = {t.id: tuple(w for w in tokens(t.name) if w not in _GENERIC) for t in index.types.values()}
        alias_words = tuple(frozenset(a.phrase.split()) for a in index.aliases)
        said_words: dict[str, set[str]] = {t.id: set(tokens(t.name)) for t in index.types.values()}
        for a, ws in zip(index.aliases, alias_words):
            for tid, _ in a.weights:
                said_words[tid] |= ws
        name_parts = {t.id: tuple(p for p in (tuple(w for w in tokens(part) if w not in _NAME_STOP)
                                              for part in t.name.split("/")) if p)
                      for t in index.types.values()}
        words = {w for ps in name_parts.values() for p in ps for w in p} | {w for ws in alias_words for w in ws}
        by_stem: dict[str, list[str]] = defaultdict(list)
        by_len: dict[int, list[str]] = defaultdict(list)
        for w in sorted(words):
            by_stem[stem(w)].append(w)
            by_len[len(w)].append(w)
        aliases_by_word: dict[str, list[int]] = defaultdict(list)
        types_by_name_word: dict[str, list[str]] = defaultdict(list)
        by_name: dict[str, list[str]] = defaultdict(list)
        for i, ws in enumerate(alias_words):
            for w in ws:
                aliases_by_word[w].append(i)
        for tid, ws in name_words.items():
            for w in set(ws):
                types_by_name_word[w].append(tid)
        for t in index.types.values():
            by_name[normalize(t.name)].append(t.id)

        def freeze(d: dict) -> dict:
            return {k: tuple(v) for k, v in d.items()}
        places = [loc.name for loc in index.locations.values()] + [m.name for m in index.metros.values()]
        return cls(freeze(by_stem), freeze(by_len), freeze(aliases_by_word), alias_words,
                   freeze(types_by_name_word), name_words, freeze(by_name), name_parts,
                   max((len(t.split()) for t in index.lay_terms), default=0),
                   frozenset(w for t in index.lay_terms for w in t.split()),
                   frozenset(w for p in places for w in tokens(p)),
                   {tid: frozenset(ws) for tid, ws in said_words.items()})

    def matched(self, heard: list[str]) -> set[str]:
        """Vocabulary words v with _token_match(h, v) for some heard word h."""
        return set().union(*(_word_hits(self, h) for h in heard))


@lru_cache(maxsize=8192)
def _word_hits(vocab: _Vocab, h: str) -> frozenset[str]:
    out = set(vocab.by_stem.get(stem(h), ()))
    if h in _SAME:
        out.update(v for v in vocab.by_stem.get(stem(_SAME[h]), ()) if v == _SAME[h])
    if len(h) > 4:
        jw = jellyfish.jaro_winkler_similarity
        for n in (len(h) - 1, len(h), len(h) + 1):
            out.update(v for v in vocab.by_len.get(n, ()) if len(v) > 4 and jw(h, v) >= 0.92)
    return frozenset(out)


_vocab = per_index(_Vocab.build)


@per_index
def _scorer(index: CatalogIndex) -> Callable[[str | None, str | None], Mapping[str, TypeCandidate]]:
    """_score_types for this catalog, remembered per phrase and hint: one model turn scores the same
    phrase for the lexical match, the request's ranking, the check's rival and the shortlist."""
    return lru_cache(maxsize=1024)(lambda phrase, hint: MappingProxyType(_score_types(index, phrase, hint)))


def types_named(index: CatalogIndex, phrase: str) -> tuple[str, ...]:
    """Types whose full name is exactly the phrase, in catalog order."""
    return _vocab(index).by_name.get(normalize(phrase), ())


def match_types(index: CatalogIndex, phrase: str | None, specialty_hint: str | None = None) -> list[TypeCandidate]:
    """Score every appointment type against the phrase. Longest alias match wins over the
    shorter aliases it contains ("physical therapy" beats "physical")."""
    return sorted((c for c in _scorer(index)(phrase, specialty_hint).values() if c.score >= MIN_SCORE),
                  key=lambda c: (-c.score, c.type_id))


def _score_types(index: CatalogIndex, phrase: str | None, specialty_hint: str | None) -> dict[str, TypeCandidate]:
    best: dict[str, TypeCandidate] = {}
    alias_support: dict[str, set[int]] = defaultdict(set)

    def offer(tid: str, score: float, via: str) -> None:
        if tid not in best or score > best[tid].score:
            best[tid] = TypeCandidate(tid, round(score, 3), via)

    words = tokens(phrase or "")
    vocab = _vocab(index)
    if words:
        for tid in vocab.by_name.get(" ".join(words), ()):
            offer(tid, 1.0, "name")

        for start, end, alias in _said_aliases(index, words):
            coverage = (end - start) / len(words)
            for tid, w in alias.weights:
                offer(tid, w * (0.6 + 0.4 * coverage), "alias")
                alias_support[tid].update(range(start, end))

        content = [w for w in words if w not in _GENERIC]
        if content:
            hit_content = vocab.matched(content)
            reached = {tid for w in hit_content for tid in vocab.types_by_name_word.get(w, ())}
            for tid in index.types:
                if tid not in reached:
                    continue
                name_words = vocab.name_words[tid]
                hits = sum(1 for nw in name_words if nw in hit_content)
                recall = hits / len(name_words)
                precision = hits / len(content)
                offer(tid, 0.85 * (0.6 * recall + 0.4 * precision), "words")

    _drop_unoffered_context(index, vocab, words, best, alias_support)
    named = _drop_dominated(index, vocab, words, best, alias_support)
    _drop_inside_aliases(vocab, words, best, alias_support)

    if any(c.via == "name" for c in best.values()):
        for tid, c in list(best.items()):
            if c.via != "name":
                best[tid] = TypeCandidate(tid, round(c.score * _NON_NAME_WHEN_NAMED, 3), c.via)

    if specialty_hint:
        for tid, c in list(best.items()):
            if index.types[tid].specialty not in (specialty_hint, "General"):
                best[tid] = TypeCandidate(tid, round(c.score * _OFF_HINT_PENALTY, 3), c.via)

    strong = [c for c in best.values() if c.score >= _STRONG]
    if not strong and not named:
        specialty = _pointed_specialty(index, words, specialty_hint)
        default = index.specialty_default.get(specialty) if specialty else None
        # "an echo for my heart": an alias already names a visit of the specialty "heart" points
        # to, so the specialty's default visit adds no evidence.
        named_in_specialty = any(c.via in ("name", "alias") and index.types[c.type_id].specialty == specialty
                                 for c in best.values())
        # "start PT after my shoulder surgery" names physical therapy, which no clinic offers;
        # "shoulder" does not turn it into an orthopedic consultation.
        names_unoffered = any(c.via in ("name", "alias") and c.type_id in index.unoffered_types
                              for c in best.values())
        if default and not named_in_specialty and not names_unoffered:
            offer(default, _SPECIALTY_DEFAULT_SCORE, "specialty")
    return best


def _said_aliases(index: CatalogIndex, words: list[str]) -> list[tuple[int, int, Alias]]:
    """(start, end, alias) for each alias said in the phrase, except one inside a longer said alias
    ("physical therapy" hides "physical")."""
    vocab = _vocab(index)
    hit = vocab.matched(words)
    # An alias can span the phrase only if each of its words matched some phrase word.
    maybe = sorted({i for w in hit for i in vocab.aliases_by_word.get(w, ()) if vocab.alias_words[i] <= hit})
    spans = []
    for i in maybe:
        alias = index.aliases[i]
        a_words = alias.phrase.split()
        start = _find_span(words, a_words)
        if start >= 0:
            spans.append((start, start + len(a_words), alias))
    return [s for s in spans
            if not any(o is not s and o[0] <= s[0] and s[1] <= o[1] and (o[1] - o[0]) > (s[1] - s[0]) for o in spans)]


def _pointed_specialty(index: CatalogIndex, words: list[str], hint: str | None) -> str | None:
    """The specialty the hint or the phrase's lay terms point to. Lay terms of two specialties
    ("throat" and "stomach") are no single specialty's evidence."""
    lay = _lay_specialties(index, words)
    return hint or (lay[0] if len(lay) == 1 else None)


def pointed_default(index: CatalogIndex, phrase: str | None, hint: str | None) -> str | None:
    """The default visit of the specialty the phrase points to ("PT for my sore knee" -> Orthopedic
    Consultation): what a refusal of an unoffered visit suggests instead."""
    specialty = _pointed_specialty(index, tokens(phrase or ""), hint)
    return index.specialty_default.get(specialty) if specialty else None


# A visit we do not offer, said as what happened or what someone said rather than as what the
# caller asks for: the object of a time word ("my knee still hurts after PT", "before I start PT",
# but not "after surgery, PT"), or the subject of a verb right after it ("my PT says ...", "physio
# did not help").
_CONTEXT_BEFORE = frozenset({"after", "before", "since", "until", "despite", "during", "following"})
_CONTEXT_WINDOW = 3
_STARTING = frozenset({"start", "starting", "started", "begin", "beginning", "began", "doing", "finished", "finishing",
                       "had"})
_SUBJECT_VERBS = frozenset({"says", "said", "told", "wants", "wanted", "thinks", "recommended", "suggested",
                            "referred", "sent", "ordered", "did", "didn", "does", "doesn", "isn", "wasn", "hasn",
                            "hadn", "helped", "helps", "won"})


def _drop_unoffered_context(index: CatalogIndex, vocab: _Vocab, words: list[str], best: dict[str, TypeCandidate],
                            alias_support: dict[str, set[int]]) -> None:
    """An unoffered visit said only as context is no candidate: it neither refuses the call nor
    hides the default of the body part the caller asks about."""
    hits = [_word_hits(vocab, w) for w in words]

    def context(start: int, end: int) -> bool:
        governed = any(words[j] in _CONTEXT_BEFORE and all(w in _FILLER or w in _STARTING for w in words[j + 1:start])
                       for j in range(max(0, start - _CONTEXT_WINDOW), start))
        return governed or (end < len(words) and words[end] in _SUBJECT_VERBS)

    for tid in [t for t in best if t in index.unoffered_types and best[t].via in ("alias", "words")]:
        said = sorted(set(alias_support.get(tid, ())) | {
            i for i, h in enumerate(hits) if words[i] not in _GENERIC and not h.isdisjoint(vocab.name_words[tid])})
        runs: list[list[int]] = []  # each mention: consecutive word positions
        for i in said:
            if runs and i == runs[-1][-1] + 1:
                runs[-1].append(i)
            else:
                runs.append([i])
        if runs and all(context(r[0], r[-1] + 1) for r in runs):
            del best[tid]
            alias_support.pop(tid, None)


def _drop_dominated(index: CatalogIndex, vocab: _Vocab, words: list[str], best: dict[str, TypeCandidate],
                    alias_support: dict[str, set[int]]) -> bool:
    """A type whose full name the caller said, in order ("I need to get a colonoscopy", "knee
    x-ray please"), drops every candidate whose evidence lies inside that name: Colonoscopy
    Consultation, X-Ray, Therapy Session inside "physical therapy evaluation". This is the
    exact-name rule for a name said inside a longer phrase; a name inside a longer said name
    ("x-ray" in "knee x-ray") goes too. Returns whether some candidate at or above MIN_SCORE has
    every distinctive name word heard (in any order)."""
    kept = [i for i, w in enumerate(words) if w not in _NAME_STOP]
    hits = [_word_hits(vocab, w) for w in words]
    heard_anywhere = set().union(*(hits[i] for i in kept)) if kept else set()
    spans: dict[str, frozenset[int]] = {}
    named = False
    for tid, cand in best.items():
        for part in vocab.name_parts[tid]:
            heard = [nw in heard_anywhere for nw in part]
            distinctive = [h for h, nw in zip(heard, part) if nw not in _GENERIC]
            named = named or (cand.score >= MIN_SCORE and bool(distinctive) and all(distinctive))
            # Every time it was said: "a thyroid ultrasound, yeah a thyroid ultrasound".
            for at in _find_spans([words[i] for i in kept], list(part)) if all(heard) else ():
                spans[tid] = spans.get(tid, frozenset()) | frozenset(kept[at:at + len(part)])
    if not spans:
        return named

    def support(tid: str) -> set[int]:
        out = set(alias_support.get(tid, ()))
        for part in vocab.name_parts[tid]:
            out.update(i for i, h in enumerate(hits) if not h.isdisjoint(part))
        return out

    dominated = {b for b in best for a, span in spans.items()
                 if a != b and spans.get(b) != span and support(b) <= span}
    for tid in dominated:
        del best[tid]
    return True


def _drop_inside_aliases(vocab: _Vocab, words: list[str], best: dict[str, TypeCandidate],
                         alias_support: dict[str, set[int]]) -> None:
    """The same rule for a said alias: in "I need my allergy shots" the alias "allergy shots"
    (Allergy Shots) covers "allergy", the only evidence for Allergy Consultation, which a name
    word alone reached. A candidate with an alias of its own keeps its place, and so does one
    whose evidence is the whole alias: in "I need my a1c" the alias "a1c" (Diabetes Management)
    says no more than the type named A1C Test does."""
    hits = [_word_hits(vocab, w) for w in words]
    for tid in [t for t, c in best.items() if c.via == "words" and t not in alias_support]:
        evidence = {i for i, h in enumerate(hits) for part in vocab.name_parts[tid] if not h.isdisjoint(part)}
        if evidence and any(other != tid and evidence < span for other, span in alias_support.items()):
            del best[tid]


def _lay_specialties(index: CatalogIndex, words: list[str]) -> list[str]:
    """Specialties the phrase's lay terms point to: longer terms first ("hurt my knee" before
    "knee"), then earlier in the phrase. With one-word terms only, that is phrase order."""
    longest = _vocab(index).lay_term_words
    hits = []
    for n in range(min(longest, len(words)), 0, -1):
        for i in range(len(words) - n + 1):
            spec = index.lay_terms.get(" ".join(words[i:i + n]))
            if spec:
                hits.append(spec)
    return list(dict.fromkeys(hits))


def ranked_types(index: CatalogIndex, phrase: str | None, hint: str | None) -> list[str]:
    """Types any word of the phrase or the hint reaches, best lexical score first."""
    scored = sorted(_scorer(index)(phrase, hint).values(), key=lambda c: (-c.score, c.type_id))
    return [c.type_id for c in scored]


def nearest_type(index: CatalogIndex, chosen: str, pool: list[str], phrase: str | None, hint: str | None) -> str | None:
    """The type in `pool` a caller who said `phrase` most plausibly meant instead of `chosen`: one
    the catalog calls confusable with it (a shared name word or alias, and the same specialty or
    the same providers), else another visit of its specialty. Among several, the one the phrase
    reaches first lexically, then the specialty's default, then by id. None: no neighbour."""
    spec = index.types[chosen].specialty
    others = [t for t in pool if t != chosen]
    near = ([t for t in others if t in index.confusables.get(chosen, ())]
            or [t for t in others if index.types[t].specialty == spec])
    if not near:
        return None
    rank = {t: i for i, t in enumerate(ranked_types(index, phrase, hint))}
    default = index.specialty_default.get(spec)
    return min(near, key=lambda t: (rank.get(t, len(rank)), t != default, t))


@per_index
def _attachments(index: CatalogIndex) -> dict[str, frozenset[str]]:
    """Vocabulary word -> the offered types it attaches to: a word of the type's name, or of any
    alias that lists the type, at any weight ("blood work" lists Fasting Blood Test at 0.7). A
    visit noun ("test", "visit") attaches by names only: it says what kind of visit."""
    offered = {t for t in index.types if t not in index.unoffered_types}
    out: dict[str, set[str]] = defaultdict(set)
    for tid in offered:
        for w in tokens(index.types[tid].name):
            if w not in _NAME_STOP:
                out[w].add(tid)
    for a in index.aliases:
        for w in a.phrase.split():
            if w not in _FILLER and w not in _VISIT_NOUNS and w not in _TITLES:
                out[w].update(t for t, _ in a.weights if t in offered)
    return {w: frozenset(ts) for w, ts in out.items()}


@per_index
def _name_tails(index: CatalogIndex) -> tuple[tuple[str, frozenset[str]], ...]:
    """Each distinctive name word of an offered type, with the types whose names carry it."""
    by_word: dict[str, set[str]] = defaultdict(set)
    for tid, t in index.types.items():
        if tid not in index.unoffered_types:
            for w in tokens(t.name):
                if w not in _GENERIC and w not in _NAME_STOP:
                    by_word[w].add(tid)
    return tuple((w, frozenset(ts)) for w, ts in sorted(by_word.items()))


def _attached(index: CatalogIndex, word: str) -> frozenset[str]:
    """The offered types a caller's word attaches to: by the vocabulary words it matches, or as the
    combining form that ends a compound name word ("scope": Colonoscopy, Endoscopy)."""
    att = _attachments(index)
    out = set().union(*(att.get(v, ()) for v in _word_hits(_vocab(index), word)))
    root = stem(word)
    if len(root) >= _ROOT_MIN:
        for name_word, tids in _name_tails(index):
            at = name_word.find(root)
            if at >= _ROOT_PREFIX_MIN and len(name_word) - at - len(root) <= _ROOT_ENDING_MAX:
                out |= tids
    return frozenset(out)


def umbrella(index: CatalogIndex, phrase: str | None, type_id: str, within: tuple[str, ...] = ()) -> tuple[str, ...]:
    """The offered visits the caller's words fit exactly as well as `type_id`, sorted; () when the
    words tell `type_id` apart. "my baby's checkup": "baby" and "checkup" attach to Well-Child
    Visit and Newborn Visit alike ("well baby", "kid checkup", "newborn checkup"). A visit named
    outright ("flu shot", "newborn visit") is told apart; so is one whose words a caller said in
    full when the others' are not ("fasting blood test"). Words that say who sent the caller
    ("my doctor said") and words that only name the visit's specialty ("my stomach doctor") tell
    nothing apart. Any other word no visit's name or alias carries ("down the throat", "two weeks
    old") may, so only a model can weigh it: ()."""
    words = tokens(phrase or "")
    vocab = _vocab(index)
    kept = [w for w in words if w not in _NAME_STOP]
    if not words or any(_find_spans(kept, list(part)) for part in vocab.name_parts[type_id]):
        return ()
    spec = index.types[type_id].specialty
    offered = set(index.types) - index.unoffered_types
    elsewhere = _parsed_elsewhere(index, words)
    # A said alias of several words is one term ("lung doctor" is a pulmonology consultation, not
    # every visit "lung" attaches to); the other words count one by one.
    multi = [(start, end, alias) for start, end, alias in _said_aliases(index, words) if end - start > 1]
    terms: list[tuple[str, frozenset[str]]] = [
        (alias.phrase, frozenset(t for t, _ in alias.weights if t in offered)) for _, _, alias in multi]
    covered = {i for start, end, _ in multi for i in range(start, end)}
    terms += [(w, _attached(index, w)) for i, w in enumerate(words)
              if i not in covered and i not in elsewhere and w not in TIME_WORDS and w not in _NO_VISIT
              and (w not in _FILLER or w in _VISIT_NOUNS)]
    fit: set[str] | None = None
    names_specialty = False
    for w, tids in terms:
        if type_id in tids:
            fit = set(tids) if fit is None else fit & tids
            continue
        specialties = {index.types[t].specialty for t in tids} | (
            {index.lay_terms[w]} if w in index.lay_terms else set())
        if not specialties or not specialties <= {spec}:
            return ()
        names_specialty = True
    if fit is None:
        return ()
    if names_specialty:
        # "my stomach doctor said I need a scope": the scopes of that specialty.
        fit = {t for t in fit if index.types[t].specialty == spec}
    heard = vocab.matched(words)

    def said_in_full(tid: str) -> bool:
        return all(nw in heard for nw in vocab.name_words[tid])
    full = said_in_full(type_id)
    out = sorted(t for t in fit if said_in_full(t) == full and (not within or t in within))
    return tuple(out) if len(out) > 1 else ()


def fitting_kin(index: CatalogIndex, phrase: str | None, type_id: str) -> tuple[str, ...]:
    """Offered visits besides `type_id` that the caller's words name too, and that the catalog
    relates to it: a shared distinctive name word, or confusable (catalog_index.confusables). "a CT
    scan of my chest" names CT - Chest and, in full, CT Scan; "an MRI of my ankle" names no other
    MRI in full ("ct" is an alias of every CT, but "head" names CT - Head and was not said)."""
    vocab = _vocab(index)
    heard = vocab.matched(tokens(phrase or ""))
    own = set(vocab.name_words[type_id])
    return tuple(sorted(
        t for t in index.types
        if t != type_id and t not in index.unoffered_types
        and (own & set(vocab.name_words[t]) or t in index.confusables.get(type_id, ()))
        and vocab.name_words[t] and all(nw in heard for nw in vocab.name_words[t])))


def type_shortlist(index: CatalogIndex, phrase: str | None, hint: str | None,
                   metros: frozenset[str] | None = None) -> list[str]:
    """Offered types (offered in `metros`, when given) for a model to choose among: at most
    SHORTLIST_SIZE lexical candidates at any score and types of the specialties the hint or a lay
    term names, plus every specialty's default, so a symptom no word of ours reaches ("swollen
    stiff fingers") can still land in any specialty, and the sick visit, so a new problem can land
    in primary care: its default is a routine visit (Annual Physical). Sorted by id, like the full
    request."""
    def offered(tid: str) -> bool:
        return tid in index.types and tid not in index.unoffered_types and (
            metros is None or bool(index.metros_by_type[tid] & metros))

    ranked = ranked_types(index, phrase, hint)
    for spec in ([hint] if hint else []) + _lay_specialties(index, tokens(phrase or "")):
        default = index.specialty_default.get(spec)
        ranked += ([default] if default else []) + sorted(t.id for t in index.types.values() if t.specialty == spec)
    evidence = [tid for tid in dict.fromkeys(ranked) if offered(tid)][:SHORTLIST_SIZE]
    defaults = [index.specialty_default[s] for s in sorted(index.specialty_default)] + list(_sick_visits(index))
    return sorted(set(evidence) | {tid for tid in defaults if offered(tid)})


# The catalog's alias for being sick names the visit a new problem goes to ("sick": Sick Visit).
_SICK = "sick"


@per_index
def _sick_visits(index: CatalogIndex) -> tuple[str, ...]:
    """The visits the alias "sick" lists at its top weight; () if the catalog has no such alias."""
    alias = next((a for a in index.aliases if a.phrase == _SICK), None)
    if alias is None:
        return ()
    top = max(w for _, w in alias.weights)
    return tuple(sorted(t for t, w in alias.weights if w >= top))


# Filler that carries no meaning about which visit is wanted (contractions arrive split: "i m").
_FILLER = _GENERIC | {"m", "s", "ve", "d", "ll", "t", "is", "be", "it", "this", "that", "while", "just", "please",
                      "like", "so", "um", "uh", "also", "me", "you", "have", "got", "do", "can", "could", "would",
                      "one", "up", "done", "book", "schedule", "make", "set", "as"}
_TITLES = frozenset({"dr", "doctor", "doc"})
_PLACE_PREPOSITIONS = frozenset({"at", "in", "near", "by", "around"})
# What kind of visit a type is ("Lung Function Test" is a test, "Pulmonology Consultation" is not).
_VISIT_NOUNS = frozenset({"consultation", "consult", "visit", "exam", "test", "session", "evaluation", "screening"})
# Who sent the caller, never which visit: "my doctor sent me over for", "my stomach doctor said".
_NO_VISIT = _TITLES | frozenset({"said", "says", "told", "tells", "sent", "sends", "ordered", "orders", "wants",
                                 "wanted", "referred", "recommended", "suggested", "asked", "over"})
# A combining form ("scope", "gram") ends a compound name word: a root of at least _ROOT_MIN letters
# after a prefix of at least _ROOT_PREFIX_MIN ("colono-scop-y", "mammo-gram").
_ROOT_MIN = 4
_ROOT_PREFIX_MIN = 3
_ROOT_ENDING_MAX = 2


def unexplained_words(index: CatalogIndex, phrase: str | None, type_ids: list[str]) -> tuple[str, ...]:
    """Content words of the phrase that no name or alias of `type_ids` accounts for. "checkup"
    against Annual Physical / Wellness Visit leaves nothing, so only the caller can choose;
    "checkups while I'm expecting" leaves "expecting", which a model can weigh. Words another
    parser of the turn takes are accounted for: "a flu shot today" and "a flu shot with Dr.
    Chen" are as clear as "a flu shot"."""
    said = _vocab(index).said_words
    known = set().union(*(said[t] for t in type_ids))
    words = tokens(phrase or "")
    elsewhere = _parsed_elsewhere(index, words)
    return tuple(w for i, w in enumerate(words)
                 if i not in elsewhere and w not in _FILLER and not any(_token_match(w, k) for k in known))


def _parsed_elsewhere(index: CatalogIndex, words: list[str]) -> set[int]:
    """Positions of the words other parsers take: time-preference words ("today", "next week"),
    a doctor's name after a title ("Dr. Emily Chen"), a clinic or city after a preposition ("at
    Mission Bay"). A name or place word that is also a visit word ("in the back") stays with the
    lexicon, which hears it as one."""
    vocab = _vocab(index)
    out = {i for i, w in enumerate(words) if w in TIME_WORDS}

    def no_visit_word(w: str) -> bool:
        return not _word_hits(vocab, w) and w not in vocab.lay_words

    for i, w in enumerate(words):
        if w in _TITLES:
            names = list(takewhile(lambda j: is_catalog_name(index, words[j]) and no_visit_word(words[j]),
                                   range(i + 1, min(i + 3, len(words)))))
            out.update([i, *names] if names else [])
        elif w in _PLACE_PREPOSITIONS:
            start = i + 2 if words[i + 1:i + 2] == ["the"] else i + 1
            place = [k for k in takewhile(lambda k: words[k] in vocab.place_words, range(start, len(words)))
                     if no_visit_word(words[k])]
            out.update([*range(i, start), *place] if place else [])
    return out


# A stated doubt about the visit: a doubt marker, then alternatives joined by "or" in its clause or
# the next one ("I'm not really sure if it goes down my throat or up from below", "not sure, maybe A
# or B"), else before it in its clause or the one before ("either A or B, I don't know which").
# A marker is a knowing word up to three words after a negation ("don't remember", "not 100 percent
# sure", "no idea"), or a word that is doubt on its own.
_NEGATIONS = frozenset({"not", "t", "no", "never", "dont", "cant", "cannot"})
_NEGATION_REACH = 3
_KNOWING = frozenset({"sure", "certain", "positive", "remember", "recall", "know", "idea"})
_DOUBTFUL = frozenset({"unsure", "dunno", "forgot", "forget", "maybe", "either", "perhaps"})
_DOUBT_JOINS = frozenset({"if", "whether", "either", "maybe", "perhaps"})
# "Or not" is no alternative: "not sure if my insurance covers it or not".
_OPTION_FILLER = _DOUBT_JOINS | {"a", "an", "the", "my", "it", "s", "is", "was", "its", "one", "that", "this", "i",
                                 "m", "not", "no"}
# A doubt about another part of the request is not about the visit: its cost or coverage, how long
# ago, the clinic or the doctor (said by name). Time words, clinic and city names and doctors'
# names are those parts' own words.
_OTHER_TOPIC = frozenset({"insurance", "insured", "cover", "covers", "covered", "coverage", "cost", "costs", "price",
                          "copay", "dollars", "pay", "days", "weeks", "months", "year", "years", "ago", "long",
                          "hours"})
_CLAUSE = re.compile(r"[,;.!?]|\bbut\b", re.IGNORECASE)


@dataclass(frozen=True)
class Doubt:
    """A caller unsure which visit they need: what they said before the doubt, and the
    alternatives they name."""

    sure: str
    options: tuple[str, ...]


def stated_doubt(index: CatalogIndex, phrase: str | None) -> Doubt | None:
    """"... a scope, but I don't remember if it goes down my throat or up from below" -> options
    ("it goes down my throat", "up from below"), sure "... a scope". None without a doubt, or
    without two alternatives about the visit: "a flu shot, not sure what day" or "a cleaning, I
    forget if I go to Mission Bay or North Beach" asks nothing about the visit."""
    raw = [c.strip() for c in _CLAUSE.split(phrase or "") if tokens(c)]
    clauses = [tokens(c) for c in raw]
    for i, words in enumerate(clauses):
        for start, end in _doubt_markers(words):
            nearby = ((words[end:], i), (clauses[i + 1] if i + 1 < len(clauses) else [], i + 1),
                      (words[:start], i), (clauses[i - 1] if i else [], i - 1))
            for said, at in nearby:
                options = _alternatives(index, said)
                if options:
                    return Doubt(", ".join(raw[:min(i, at)]), options)
    return None


def _doubt_markers(words: list[str]) -> list[tuple[int, int]]:
    out = []
    for k, w in enumerate(words):
        if w in _DOUBTFUL:
            out.append((k, k + 1))
        elif w in _NEGATIONS:
            known = next((j for j in range(k + 1, min(k + 1 + _NEGATION_REACH, len(words))) if words[j] in _KNOWING),
                         None)
            if known is not None:
                out.append((k, known + 1))
    return out


def _alternatives(index: CatalogIndex, words: list[str]) -> tuple[str, ...]:
    """"if it goes down my throat or up from below" -> ("it goes down my throat", "up from below");
    () unless at least two alternatives are about the visit."""
    parts, cur = [], []
    for w in [*words, "or"]:
        if w != "or":
            cur.append(w)
            continue
        while cur and cur[0] in _DOUBT_JOINS:
            cur = cur[1:]
        if _about_visit(index, cur):
            parts.append(" ".join(cur))
        cur = []
    return tuple(parts) if len(parts) >= 2 else ()


def _about_visit(index: CatalogIndex, words: list[str]) -> bool:
    """Something beyond filler and time words, and no other part of the request unless a visit
    word is said too ("a cleaning at Mission Bay")."""
    vocab = _vocab(index)
    content = [w for w in words if w not in _OPTION_FILLER and w not in TIME_WORDS]
    other = _place_positions(index, words) | {
        i for i, w in enumerate(words) if w in _OTHER_TOPIC or w.isdigit() or w in _TITLES
        or (is_catalog_name(index, w) and not _word_hits(vocab, w) and w not in vocab.lay_words)}
    if not content or not other:
        return bool(content)
    return any(i not in other and w in content and (_word_hits(vocab, w) or w in vocab.lay_words)
               for i, w in enumerate(words))


def _place_positions(index: CatalogIndex, words: list[str]) -> set[int]:
    """Positions of the words that say a clinic's or a city's name ("mission bay", "downtown")."""
    names = _place_names(index)
    longest = max((len(n) for n in names), default=0)
    out: set[int] = set()
    for n in range(min(longest, len(words)), 0, -1):
        for i in range(len(words) - n + 1):
            if tuple(words[i:i + n]) in names:
                out.update(range(i, i + n))
    return out


@per_index
def _place_names(index: CatalogIndex) -> frozenset[tuple[str, ...]]:
    names = [n for loc in index.locations.values() for n in (loc.short_name, loc.neighborhood, loc.city) if n]
    names += [n for m in index.metros.values() for n in (m.name, *m.aliases) if n]
    return frozenset(t for t in (tuple(tokens(n)) for n in names) if t)
