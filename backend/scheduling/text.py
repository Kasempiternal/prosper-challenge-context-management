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


# English function words (pronouns, determiners, auxiliaries, conjunctions, prepositions of place
# and time, contraction pieces): words that say nothing about what a phrase is about. Directions
# ("up", "down", "below") and negations ("not", "no") are left out: they can tell things apart.
FUNCTION_WORDS = frozenset({
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your", "yours", "yourself", "he", "him",
    "his", "himself", "she", "her", "hers", "herself", "it", "its", "itself", "they", "them", "their", "theirs",
    "themselves", "what", "which", "who", "whom", "whose", "this", "that", "these", "those", "am", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "having", "do", "does", "did", "doing", "will", "would",
    "shall", "should", "can", "could", "may", "might", "must", "a", "an", "the", "and", "but", "if", "or", "because",
    "as", "until", "while", "of", "at", "by", "for", "with", "about", "between", "to", "from", "in", "on", "again",
    "then", "once", "here", "there", "when", "where", "why", "how", "all", "any", "both", "each", "some", "such",
    "so", "than", "too", "very", "just", "also", "really", "now", "s", "t", "d", "ll", "m", "re", "ve", "don",
    "um", "uh", "oh", "like", "okay", "ok", "please", "yeah", "yes", "one", "ones",
})


def normalize(text: str) -> str:
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def stem(token: str) -> str:
    """Crude stem so 'vaccine'/'vaccination', 'test'/'testing' share a key."""
    for suffix in ("ations", "ation", "ings", "ing", "ies", "ed", "es", "s", "e"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


@lru_cache(maxsize=4096)
def phonetic_keys(word: str) -> frozenset[str]:
    w = normalize(word).replace(" ", "")
    for pattern, repl in _SPOKEN_RESPELL:
        w = pattern.sub(repl, w)
    return frozenset(code for code in doublemetaphone(w) if code)
