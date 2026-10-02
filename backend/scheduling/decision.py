"""Confidence gate: a probability distribution over options -> act / either-or / open.

Thresholds were chosen on eval/cases_tune.jsonl only; see eval/README.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    """act: commit to this id. pair: ask the caller between these two. Neither: decline, and the
    resolver does what it would do with no model at all."""

    act: str | None = None
    pair: tuple[str, str] | None = None
    top: tuple[tuple[str, float], ...] = ()
    called: bool = False

    def describe(self) -> str:
        probs = " ".join(f"{k}={p:.2f}" for k, p in self.top)
        kind = f"act {self.act}" if self.act else f"pair {self.pair}" if self.pair else "open"
        return f"{kind} [{probs}]" if self.called else f"{kind} (no call)"


DECLINE = Verdict()


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
            return Verdict(pair=(a, b), top=top, called=True)
        return Verdict(top=top, called=True)
