"""Text normalization and phonetic keys shared by the index and the matchers."""

from __future__ import annotations

import re
from functools import lru_cache

from metaphone import doublemetaphone

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# English-speaker renderings of Vietnamese "Ng-" surnames: "Nguyen" is said "win"/"nwin",
# and STT writes it as "Nwin", "Win", "Gwen" or "When". Spelling-based metaphone cannot
# see that, so both the index and the query are respelled to the spoken form first.
_SPOKEN_RESPELL = (
    (re.compile(r"^nguy?e"), "wi"),
    (re.compile(r"^ngu"), "w"),
    (re.compile(r"^(nw|gw|wh)"), "w"),
    (re.compile(r"^wen"), "win"),
)


def normalize(text: str) -> str:
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def stem(token: str) -> str:
    """Crude stem so 'vaccine'/'vaccination', 'test'/'testing' share a key."""
    for suffix in ("ations", "ation", "ings", "ing", "ies", "es", "s", "e"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


@lru_cache(maxsize=4096)
def phonetic_keys(word: str) -> frozenset[str]:
    w = normalize(word).replace(" ", "")
    for pattern, repl in _SPOKEN_RESPELL:
        w = pattern.sub(repl, w)
    return frozenset(code for code in doublemetaphone(w) if code)
