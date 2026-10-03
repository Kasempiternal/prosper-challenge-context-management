"""Model hooks for tests: random answers (property tests) and sure answers (the worst case for a commit)."""

import random

from scheduling.decision import DECLINE, Verdict
from scheduling.resolver import NoDisambiguator


class RandomHooks(NoDisambiguator):
    """Every model hook, answering at random: decline, act or pair, on the candidates or on any id
    of `universe` (kind -> ids: "type", "provider", "site"), and genders sure or unsure."""

    def __init__(self, rng: random.Random, universe: dict[str, list[str]]):
        self.rng, self.universe = rng, universe

    def _verdict(self, candidates, kind: str) -> Verdict:
        pool = list(candidates) if candidates and self.rng.random() < 0.6 else self.universe[kind]
        roll = self.rng.random()
        if roll < 0.3:
            return DECLINE
        if roll < 0.7:
            return Verdict(act=self.rng.choice(pool), called=True)
        return Verdict(ask=(self.rng.choice(pool), self.rng.choice(self.universe[kind])), called=True)

    def pick_type(self, phrase, hint, candidate_ids):
        return self._verdict(candidate_ids, "type")

    def check_type(self, phrase, hint, first, rival):
        return self._verdict([first.act or first.ask[0], rival], "type")

    def pick_provider(self, phrase, type_id, candidate_ids):
        return self._verdict(candidate_ids, "provider")

    def provider_genders(self, candidate_ids):
        return {p: self.rng.choice((0.02, 0.5, 0.98)) for p in candidate_ids if self.rng.random() < 0.7}

    def pick_site(self, phrase, type_id, candidate_ids):
        return self._verdict(candidate_ids, "site")


class Sure(NoDisambiguator):
    """Picks the first visit of `prefer` the question offers (else the first offered), sure of
    it; `by_word` maps a word of the phrase to the visit picked instead. Every check confirms."""

    def __init__(self, prefer=(), by_word=None):
        self.prefer, self.by_word = prefer, by_word or {}

    def pick_type(self, phrase, hint, candidate_ids):
        worded = [t for w, t in self.by_word.items() if w in phrase and t in candidate_ids]
        pick = next(iter(worded), next((t for t in self.prefer if t in candidate_ids), candidate_ids[0]))
        return Verdict(act=pick, top=((pick, 0.97),), called=True)

    def check_type(self, phrase, hint, first, rival):
        chosen = first.act or first.ask[0]
        return Verdict(act=chosen, top=((chosen, 0.95), (rival, 0.03), ("either", 0.02)), called=True)
