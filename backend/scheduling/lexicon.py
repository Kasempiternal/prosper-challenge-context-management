"""Caller phrase -> scored appointment-type candidates (type names + hand-written aliases)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

import jellyfish

from .catalog_index import CatalogIndex
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
    if a == b or stem(a) == stem(b):
        return True
    # Fuzzy only for long, similar-length words: "checkup" must not match "check".
    return (min(len(a), len(b)) > 4 and abs(len(a) - len(b)) <= 1
            and jellyfish.jaro_winkler_similarity(a, b) >= 0.92)


def _find_span(hay: list[str], needle: list[str]) -> int:
    n = len(needle)
    for i in range(len(hay) - n + 1):
        if all(_token_match(hay[i + k], needle[k]) for k in range(n)):
            return i
    return -1


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

    @classmethod
    def build(cls, index: CatalogIndex) -> "_Vocab":
        name_words = {t.id: tuple(w for w in tokens(t.name) if w not in _GENERIC) for t in index.types.values()}
        alias_words = tuple(frozenset(a.phrase.split()) for a in index.aliases)
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
        return cls(freeze(by_stem), freeze(by_len), freeze(aliases_by_word), alias_words,
                   freeze(types_by_name_word), name_words, freeze(by_name), name_parts,
                   max((len(t.split()) for t in index.lay_terms), default=0))

    def matched(self, heard: list[str]) -> set[str]:
        """Vocabulary words v with _token_match(h, v) for some heard word h."""
        return set().union(*(_word_hits(self, h) for h in heard))


@lru_cache(maxsize=8192)
def _word_hits(vocab: _Vocab, h: str) -> frozenset[str]:
    out = set(vocab.by_stem.get(stem(h), ()))
    if len(h) > 4:
        jw = jellyfish.jaro_winkler_similarity
        for n in (len(h) - 1, len(h), len(h) + 1):
            out.update(v for v in vocab.by_len.get(n, ()) if len(v) > 4 and jw(h, v) >= 0.92)
    return frozenset(out)


# Keyed by id(index); the entry holds the index, so its id cannot be reused while cached.
_VOCABS: dict[int, tuple[CatalogIndex, _Vocab]] = {}


def _vocab(index: CatalogIndex) -> _Vocab:
    hit = _VOCABS.get(id(index))
    if hit is None or hit[0] is not index:
        hit = _VOCABS[id(index)] = (index, _Vocab.build(index))
    return hit[1]


def types_named(index: CatalogIndex, phrase: str) -> tuple[str, ...]:
    """Types whose full name is exactly the phrase, in catalog order."""
    return _vocab(index).by_name.get(normalize(phrase), ())


def match_types(index: CatalogIndex, phrase: str | None, specialty_hint: str | None = None) -> list[TypeCandidate]:
    """Score every appointment type against the phrase. Longest alias match wins over the
    shorter aliases it contains ("physical therapy" beats "physical")."""
    return sorted((c for c in _score_types(index, phrase, specialty_hint).values() if c.score >= MIN_SCORE),
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
        kept = [s for s in spans
                if not any(o is not s and o[0] <= s[0] and s[1] <= o[1] and (o[1] - o[0]) > (s[1] - s[0]) for o in spans)]
        for start, end, alias in kept:
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

    named = _drop_dominated(index, vocab, words, best, alias_support)

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
        lay = _lay_specialties(index, words)
        # Lay terms of two specialties ("throat" and "stomach") are no single default's evidence.
        specialty = specialty_hint or (lay[0] if len(lay) == 1 else None)
        default = index.specialty_default.get(specialty) if specialty else None
        if default:
            offer(default, _SPECIALTY_DEFAULT_SCORE, "specialty")
    return best


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
            at = _find_span([words[i] for i in kept], list(part)) if all(heard) else -1
            if at >= 0:
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


def type_shortlist(index: CatalogIndex, phrase: str | None, hint: str | None,
                   metros: frozenset[str] | None = None) -> list[str]:
    """Offered types (offered in `metros`, when given) for a model to choose among: at most
    SHORTLIST_SIZE lexical candidates at any score and types of the specialties the hint or a lay
    term names, plus every specialty's default, so a symptom no word of ours reaches ("swollen
    stiff fingers") can still land in any specialty. Sorted by id, like the full request."""
    def offered(tid: str) -> bool:
        return tid in index.types and tid not in index.unoffered_types and (
            metros is None or bool(index.metros_by_type[tid] & metros))

    scored = sorted(_score_types(index, phrase, hint).values(), key=lambda c: (-c.score, c.type_id))
    ranked = [c.type_id for c in scored]
    for spec in ([hint] if hint else []) + _lay_specialties(index, tokens(phrase or "")):
        default = index.specialty_default.get(spec)
        ranked += ([default] if default else []) + sorted(t.id for t in index.types.values() if t.specialty == spec)
    evidence = [tid for tid in dict.fromkeys(ranked) if offered(tid)][:SHORTLIST_SIZE]
    defaults = [index.specialty_default[s] for s in sorted(index.specialty_default)]
    return sorted(set(evidence) | {tid for tid in defaults if offered(tid)})


# Filler that carries no meaning about which visit is wanted (contractions arrive split: "i m").
_FILLER = _GENERIC | {"m", "s", "ve", "d", "ll", "t", "is", "be", "it", "this", "that", "while", "just", "please",
                      "like", "so", "um", "uh", "also", "me", "you", "have", "got", "do", "can", "could", "would",
                      "one", "up", "done", "book", "schedule", "make", "set"}


def unexplained_words(index: CatalogIndex, phrase: str | None, type_ids: list[str]) -> tuple[str, ...]:
    """Content words of the phrase that no name or alias of `type_ids` accounts for. "checkup"
    against Annual Physical / Wellness Visit leaves nothing, so only the caller can choose;
    "checkups while I'm expecting" leaves "expecting", which a model can weigh."""
    ids = set(type_ids)
    known = [w for t in type_ids for w in tokens(index.types[t].name)]
    known += [w for a in index.aliases if ids & {tid for tid, _ in a.weights} for w in a.phrase.split()]
    return tuple(w for w in tokens(phrase or "")
                 if w not in _FILLER and not any(_token_match(w, k) for k in known))
