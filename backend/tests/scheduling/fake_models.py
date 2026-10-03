"""Model hooks for property tests."""

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
