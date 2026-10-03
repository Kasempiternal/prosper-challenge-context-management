"""OpenAI shortlist chooser: the JEV hooks' choice questions, answered by one gpt-4o-mini output token.

The model sees the caller's phrase and the same option texts JEV sees (scheduling.jev *_criteria),
each behind a numeric key, and answers one key. The logprobs of that single token are the
distribution the Gate reads. Probability on anything that is not an option key (the "0" escape,
stray text, the tail past top_logprobs) is NOT renormalized away: it stays as uncertainty and only
lowers the top option's p, so a hesitant model asks the caller instead of acting.

Like JevClient it never raises into the resolver: a failure, timeout or malformed answer is None,
which the hooks turn into a failed Verdict.
"""

from __future__ import annotations

import math
import time
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass

import httpx
import openai

from .model_client import POOL, CachedModelClient, ModelCall, ssl_context

MODEL = "gpt-4o-mini"
USD_PER_INPUT_TOKEN = 0.15 / 1_000_000
USD_PER_OUTPUT_TOKEN = 0.60 / 1_000_000
TOP_LOGPROBS = 20  # the API maximum
NONE_KEY = "0"
SYSTEM = ("You route phone calls for a multi-specialty clinic. Read what the caller said and pick the "
          "option it identifies. Reply with the option's number only. Reply 0 if their words do not "
          "point to one option.")


@dataclass(frozen=True)
class OpenAIAnswer:
    probabilities: dict[str, float]   # option id -> p; the rest of the mass is uncertainty


class OpenAIChoiceClient(CachedModelClient):
    """The shared ladder (scheduling.model_client) over the chat completions API."""

    provider = "openai"
    key_env = "OPENAI_API_KEY"
    _give_up_on = (openai.OpenAIError, httpx.HTTPError)

    def __init__(self, api_key: str | None, *, model: str = MODEL, transport: httpx.BaseTransport | None = None,
                 **kw):
        super().__init__(api_key, **kw)
        self.model = model
        self._api = openai.OpenAI(api_key=api_key or "missing", max_retries=0,
                                  http_client=httpx.Client(transport=transport, verify=ssl_context()))

    def choice(self, state: str, instructions: str, criteria: dict[str, str],
               ranking: list[str] = (), purpose: str = "") -> OpenAIAnswer | None:
        """`ranking` is accepted for JevClient compatibility; every option is shown, in id order."""
        keys = option_keys(criteria)

        def read(entry: dict) -> tuple[OpenAIAnswer | None, float | None]:
            answer = distribution(entry["top_logprobs"], keys)
            return answer, round(max(answer.probabilities.values()), 3) if answer and answer.probabilities else None
        return self._fetch(request_body(self.model, state, instructions, criteria, keys), purpose, read)

    def prefetch_choice(self, state: str, instructions: str, criteria: dict[str, str],
                        ranking: list[str] = ()) -> None:
        """Never sent early: the option numbers follow the criteria order, which only the choice
        before it decides, so an early request would often not be the one asked."""

    def warm_up(self, index=None) -> ModelCall | None:
        """Opens the TLS connection with a free request (the model list), so the first real
        choice of a call does not pay the handshake. Not cached, never billed."""
        if not self.api_key:
            return None
        t0 = time.perf_counter()
        try:
            POOL.submit(self._api.models.retrieve, self.model, timeout=self.timeout_s).result(self.timeout_s)
            ok = True
        except (openai.OpenAIError, httpx.HTTPError, FutureTimeout):
            ok = False
        call = ModelCall("warmup", "live" if ok else "failed", round((time.perf_counter() - t0) * 1000, 1),
                         purpose="warmup")
        self.calls.append(call)
        return call if ok else None

    def close(self) -> None:
        self._api.close()

    # ---- the request -----------------------------------------------------------------------

    def _send(self, body: dict, timeout: float):
        return self._api.chat.completions.create(**body, timeout=timeout)

    def _entry(self, body: dict, response, ms: float) -> dict | None:
        try:
            first = response.choices[0].logprobs.content[0]
            top = [[t.token, t.logprob] for t in first.top_logprobs] or [[first.token, first.logprob]]
            usage = response.usage
            return {"answer": first.token, "top_logprobs": top, "input_tokens": int(usage.prompt_tokens),
                    "output_tokens": int(usage.completion_tokens), "latency_ms": ms}
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    def _usd(self, entry: dict) -> float:
        return entry["input_tokens"] * USD_PER_INPUT_TOKEN + entry["output_tokens"] * USD_PER_OUTPUT_TOKEN


def option_keys(criteria: dict[str, str]) -> dict[str, str]:
    """Option id -> key "1".."N" in the criteria's (id-sorted) order. Every number below 1000 is one
    o200k token, so the answer is exactly one token whose logprobs cover the options."""
    return {oid: str(i + 1) for i, oid in enumerate(criteria)}


def request_body(model: str, state: str, instructions: str, criteria: dict[str, str],
                 keys: dict[str, str]) -> dict:
    options = "\n".join(f"{keys[oid]}: {text}" for oid, text in criteria.items())
    user = f"{state}\n\n{instructions}\n{options}\n{NONE_KEY}: none of these, or not enough to tell"
    return {"model": model, "temperature": 0, "max_completion_tokens": 1, "logprobs": True,
            "top_logprobs": TOP_LOGPROBS,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}


def distribution(top_logprobs: list[list], keys: dict[str, str]) -> OpenAIAnswer | None:
    """exp(logprob) per option key, summed over spellings (" 3" and "3" are distinct tokens).
    Everything else, including the mass outside the top_logprobs, stays off the options."""
    by_key = {k: oid for oid, k in keys.items()}
    probs: dict[str, float] = {}
    try:
        for token, logprob in top_logprobs:
            oid = by_key.get(str(token).strip())
            if oid is not None:
                probs[oid] = probs.get(oid, 0.0) + math.exp(float(logprob))
    except (TypeError, ValueError):
        return None
    total = sum(probs.values())
    if total > 1.0:  # rounding in the API's logprobs
        probs = {k: p / total for k, p in probs.items()}
    return OpenAIAnswer(probs)
