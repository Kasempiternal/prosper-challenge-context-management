"""Confidence gate: a probability distribution over options -> act / either-or / open.

Thresholds were chosen on eval/cases_tune.jsonl only; see eval/README.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Verdict:
    """act: commit to this id. ask: ask the caller between these (one alone: confirm it). Neither: decline,
    and the resolver does what it would do with no model at all. failed: a model was asked and no
    answer came (timeout, error); the resolver then asks the caller instead of committing."""

    act: str | None = None
    ask: tuple[str, ...] | None = None
    top: tuple[tuple[str, float], ...] = ()
    called: bool = False
    failed: bool = False

    @property
    def p(self) -> float | None:
        """The probability behind the decision: the chosen option's, else the most likely answer's."""
        probs = dict(self.top)
        return probs[self.act] if self.act in probs else max(probs.values(), default=None)

    def describe(self) -> str:
        if self.failed:
            return "no answer"
        probs = " ".join(f"{k}={p:.2f}" for k, p in self.top)
        kind = f"act {self.act}" if self.act else f"ask {self.ask}" if self.ask else "open"
        return f"{kind} [{probs}]" if self.called else f"{kind} (no call)"


DECLINE = Verdict()
FAILED = Verdict(called=True, failed=True)


def unanswered(candidates: Iterable[str]) -> Verdict:
    """A model was asked and no usable answer came. Never committed on: the caller is asked among
    `candidates`, what the resolver understood without that answer."""
    return Verdict(ask=tuple(candidates), called=True, failed=True)


@dataclass(frozen=True)
class Gate:
    act_p: float = 0.8
    margin: float = 0.6
    pair_p: float = 0.85

    def decide(self, probs: dict[str, float]) -> Verdict:
        ranked = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))
        if not ranked:
            return Verdict(called=True)
        (a, pa), (b, pb) = ranked[0], ranked[1] if len(ranked) > 1 else ("", 0.0)
        top = tuple((k, round(p, 3)) for k, p in ranked[:3])
        if pa >= self.act_p and pa - pb >= self.margin:
            return Verdict(act=a, top=top, called=True)
        if b and pa + pb >= self.pair_p:
            return Verdict(ask=(a, b), top=top, called=True)
        return Verdict(top=top, called=True)


@dataclass(frozen=True)
class Check:
    """A second, focused question after a choice: does the caller mean `chosen`, `rival`, or do
    their words fit both alike ("either")?"""

    chosen: float = 0.0
    rival: float = 0.0
    either: float = 0.0


@dataclass(frozen=True)
class CheckGate:
    """Settles a choice with its check (eval/README.md, rounds 3 and 4):

    - margin: a choice the first question was sure of stands only if the check's top answer is
      that choice, ahead of both the rival and "either" by at least this much. A check whose top
      answer is "either" ("my yearly all-over skin check": 0.34 / 0.25 / either 0.41) or that is
      split between the choice and "either" (0.52 / either 0.48) does not confirm it. No dev check
      leads by 0.14 to 0.23.
    - settle: a first question that left two gets its answer only from a check more than this
      sure, with "either" under settle_either. Strictly more: h2-28 sits at 0.65 exactly, and a
      case on the boundary takes the safe side (eval/README.md, threshold sensitivity).
    """

    margin: float = 0.2
    settle: float = 0.65
    settle_either: float = 0.5

    def decide(self, first: Verdict, rival: str, check: Check | None) -> Verdict:
        """`first` acted or asked; `rival` is the option the check weighed against its top. The
        result's `top` is the check's answer; a check that never came (None) asks."""
        chosen = first.act or first.ask[0]
        pair = first.ask or (chosen, rival)
        if check is None:
            # A confident choice is confirmed with the caller; the rival may be a long shot.
            return unanswered((chosen,) if first.act else pair)
        top = ((chosen, round(check.chosen, 3)), (rival, round(check.rival, 3)), ("either", round(check.either, 3)))
        if first.act:
            if round(check.chosen - max(check.rival, check.either), 9) >= self.margin:
                return Verdict(act=first.act, top=top, called=True)
            return Verdict(ask=pair, top=top, called=True)
        lead, p_lead, p_other = (chosen, check.chosen, check.rival) if check.chosen >= check.rival \
            else (rival, check.rival, check.chosen)
        if p_lead > self.settle and check.either < self.settle_either and p_lead > p_other:
            return Verdict(act=lead, top=top, called=True)
        return Verdict(ask=pair, top=top, called=True)


# Inferred gender: the model's probability that a provider is a woman, read off the first name (the
# catalog has no gender field). It counts only this sure either way: clearly female names scored
# 0.82-0.89 in round 3, and the score moves with the other names in the request. Pre-registered
# with the policy that gender narrows the doctors but never books one on its own (eval/README.md).
GENDER_SURE = 0.9


def gender_of(p_woman: float | None) -> str | None:
    """"female" at p >= GENDER_SURE, "male" at p <= 1 - GENDER_SURE, else None (unknown)."""
    if p_woman is None:
        return None
    if p_woman >= GENDER_SURE:
        return "female"
    return "male" if p_woman <= round(1 - GENDER_SURE, 9) else None
