"""Caller phrase -> scored appointment-type candidates (type names + hand-written aliases)."""

from __future__ import annotations

from dataclasses import dataclass

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


def match_types(index: CatalogIndex, phrase: str | None, specialty_hint: str | None = None) -> list[TypeCandidate]:
    """Score every appointment type against the phrase. Longest alias match wins over the
    shorter aliases it contains ("physical therapy" beats "physical")."""
    best: dict[str, TypeCandidate] = {}

    def offer(tid: str, score: float, via: str) -> None:
        if tid not in best or score > best[tid].score:
            best[tid] = TypeCandidate(tid, round(score, 3), via)

    words = tokens(phrase or "")
    norm = " ".join(words)
    if words:
        for t in index.types.values():
            if normalize(t.name) == norm:
                offer(t.id, 1.0, "name")

        spans = []
        for alias in index.aliases:
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

        content = [w for w in words if w not in _GENERIC]
        if content:
            for t in index.types.values():
                name_words = [w for w in tokens(t.name) if w not in _GENERIC]
                if not name_words:
                    continue
                hits = sum(1 for nw in name_words if any(_token_match(cw, nw) for cw in content))
                if hits:
                    recall = hits / len(name_words)
                    precision = hits / len(content)
                    offer(t.id, 0.85 * (0.6 * recall + 0.4 * precision), "words")

    if any(c.via == "name" for c in best.values()):
        for tid, c in list(best.items()):
            if c.via != "name":
                best[tid] = TypeCandidate(tid, round(c.score * _NON_NAME_WHEN_NAMED, 3), c.via)

    if specialty_hint:
        for tid, c in list(best.items()):
            if index.types[tid].specialty not in (specialty_hint, "General"):
                best[tid] = TypeCandidate(tid, round(c.score * _OFF_HINT_PENALTY, 3), c.via)

    strong = [c for c in best.values() if c.score >= _STRONG]
    if not strong:
        specialty = specialty_hint or _lay_specialty(index, words)
        default = index.specialty_default.get(specialty) if specialty else None
        if default:
            offer(default, _SPECIALTY_DEFAULT_SCORE, "specialty")

    return sorted((c for c in best.values() if c.score >= MIN_SCORE), key=lambda c: (-c.score, c.type_id))


def _lay_specialty(index: CatalogIndex, words: list[str]) -> str | None:
    for w in words:
        if w in index.lay_terms:
            return index.lay_terms[w]
    return None


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
