"""OpenAI shortlist chooser: the JEV hooks' questions, answered by one gpt-4o-mini output token.

The model sees the caller's phrase and the same option texts JEV sees (scheduling.jev *_criteria),
each behind a numeric key, and answers one key. The logprobs of that single token are the
distribution the Gate reads. Probability on anything that is not an option key (the "0" escape,
stray text, the tail past top_logprobs) is NOT renormalized away: it stays as uncertainty and only
lowers the top option's p, so a hesitant model asks the caller instead of acting.

Like JevClient it never raises into the resolver: a failure, timeout or malformed answer is None,
which the hooks turn into a declined Verdict.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path

import httpx
import openai

MODEL = "gpt-4o-mini"
USD_PER_INPUT_TOKEN = 0.15 / 1_000_000
USD_PER_OUTPUT_TOKEN = 0.60 / 1_000_000
TOP_LOGPROBS = 20  # the API maximum
NONE_KEY = "0"
SYSTEM = ("You route phone calls for a multi-specialty clinic. Read what the caller said and pick the "
          "option it identifies. Reply with the option's number only. Reply 0 if their words do not "
          "point to one option.")


class SpendCapExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAIAnswer:
    probabilities: dict[str, float]   # option id -> p; sums to 1 - other_mass
    other_mass: float                 # "0", non-key tokens and the unseen tail


@dataclass(frozen=True)
class OpenAICall:
    key: str
    source: str            # "live" | "cache" | "memo" | "failed"
    latency_ms: float      # this process for live/failed; the original fetch for cache hits
    input_tokens: int
    output_tokens: int = 0

    @property
    def usd(self) -> float:
        if self.source in ("memo", "failed"):
            return 0.0
        return self.input_tokens * USD_PER_INPUT_TOKEN + self.output_tokens * USD_PER_OUTPUT_TOKEN


class OpenAIChoiceClient:
    """Same contract as JevClient: mode "live" (network for everything not seen in this process),
    "cache" (disk only), "auto" (disk first, network on a miss); per-request timeout, no retry, and
    at most `turn_budget_s` of model time between begin_turn() calls. `live_limit` aborts a run
    that would send more than that many network requests (the eval's spend cap)."""

    provider = "openai"

    def __init__(self, api_key: str | None, *, mode: str = "auto", cache_path: Path | None = None,
                 model: str = MODEL, timeout_s: float = 2.5, turn_budget_s: float | None = 2.5,
                 live_limit: int | None = None, transport: httpx.BaseTransport | None = None):
        if mode not in ("live", "cache", "auto"):
            raise ValueError(f"unknown OpenAI mode {mode!r}")
        self.mode, self.model = mode, model
        self.timeout_s, self.turn_budget_s, self.live_limit = timeout_s, turn_budget_s, live_limit
        self.api_key, self.cache_path = api_key, cache_path
        self.calls: list[OpenAICall] = []
        self._deadline: float | None = None
        self._disk: dict[str, dict] = json.loads(cache_path.read_text(encoding="utf-8")) \
            if cache_path and cache_path.exists() else {}
        self._memo: dict[str, dict] = {}
        self._dirty = False
        self._sent = 0
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="openai-chooser")
        self._api = openai.OpenAI(api_key=api_key or "missing", max_retries=0,
                                  http_client=httpx.Client(transport=transport))

    @classmethod
    def from_env(cls, env_file: Path | None = None, **kw) -> "OpenAIChoiceClient":
        from dotenv import load_dotenv
        load_dotenv(env_file or Path(__file__).resolve().parents[1] / ".env")
        return cls(os.environ.get("OPENAI_API_KEY"), **kw)

    # ---- public --------------------------------------------------------------------------

    def begin_turn(self) -> None:
        self._deadline = time.perf_counter() + self.turn_budget_s if self.turn_budget_s is not None else None

    def choice(self, state: str, instructions: str, criteria: dict[str, str],
               ranking: list[str] = ()) -> OpenAIAnswer | None:
        """`ranking` is accepted for JevClient compatibility; every option is shown, in id order."""
        keys = option_keys(criteria)
        entry = self._fetch(request_body(self.model, state, instructions, criteria, keys))
        return distribution(entry["top_logprobs"], keys) if entry else None

    def warm_up(self) -> OpenAICall | None:
        """Opens the TLS connection with a free request (the model list), so the first real
        choice of a call does not pay the handshake. Not cached, never billed."""
        if not self.api_key:
            return None
        t0 = time.perf_counter()
        try:
            self._pool.submit(self._api.models.retrieve, self.model, timeout=self.timeout_s).result(self.timeout_s)
            ok = True
        except (openai.OpenAIError, httpx.HTTPError, FutureTimeout):
            ok = False
        call = OpenAICall("warmup", "live" if ok else "failed", round((time.perf_counter() - t0) * 1000, 1), 0)
        self.calls.append(call)
        return call if ok else None

    def save(self) -> None:
        if not (self.cache_path and self._dirty):
            return
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._disk, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.cache_path)
        self._dirty = False

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._api.close()

    # ---- internals -----------------------------------------------------------------------

    def _fetch(self, body: dict) -> dict | None:
        key = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if key in self._memo:
            entry = self._memo[key]
            self.calls.append(OpenAICall(key, "memo", 0.0, entry["input_tokens"], entry["output_tokens"]))
            return entry
        if self.mode != "live" and key in self._disk:
            entry = self._memo[key] = self._disk[key]
            self.calls.append(OpenAICall(key, "cache", entry["latency_ms"], entry["input_tokens"],
                                         entry["output_tokens"]))
            return entry
        if self.mode == "cache" or not self.api_key:
            self.calls.append(OpenAICall(key, "failed", 0.0, 0))
            return None
        if self.live_limit is not None and self._sent >= self.live_limit:
            raise SpendCapExceeded(f"more than {self.live_limit} uncached OpenAI requests in one run")
        self._sent += 1
        entry, call = self._post(body, key)
        self.calls.append(call)
        if entry:
            self._memo[key] = self._disk[key] = entry
            self._dirty = True
        return entry

    def _post(self, body: dict, key: str) -> tuple[dict | None, OpenAICall]:
        t0 = time.perf_counter()
        timeout = self.timeout_s
        if self._deadline is not None:
            timeout = min(timeout, self._deadline - time.perf_counter())
        resp = None
        if timeout > 0.05:
            try:
                resp = self._pool.submit(self._api.chat.completions.create, **body,
                                         timeout=timeout).result(timeout=timeout)
            except (openai.OpenAIError, httpx.HTTPError, FutureTimeout):
                resp = None
        ms = round((time.perf_counter() - t0) * 1000, 1)
        try:
            first = resp.choices[0].logprobs.content[0]
            top = [[t.token, t.logprob] for t in first.top_logprobs] or [[first.token, first.logprob]]
            usage = resp.usage
            entry = {"answer": first.token, "top_logprobs": top, "input_tokens": int(usage.prompt_tokens),
                     "output_tokens": int(usage.completion_tokens), "latency_ms": ms}
        except (AttributeError, IndexError, TypeError, ValueError):
            return None, OpenAICall(key, "failed", ms, 0)
        return entry, OpenAICall(key, "live", ms, entry["input_tokens"], entry["output_tokens"])


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
    Everything else, including the mass outside the top_logprobs, is `other_mass`."""
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
        total = 1.0
    return OpenAIAnswer(probs, round(1.0 - total, 6))
