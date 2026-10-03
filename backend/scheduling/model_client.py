"""What the networked chooser clients (JEV, OpenAI) share: the request ladder, the disk cache, the
time budget and the telemetry record of every request.

A client never raises into the resolver: any failure, timeout or malformed answer is None, and
the hooks turn None into a failed Verdict. The resolver never commits on an answer that did not
arrive: it asks the caller what the model was asked.
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import httpx

# A type decision is a choice and then a check, each ~0.55 s (p95 ~0.9 s) live.
REQUEST_TIMEOUT_S = 1.5
# The default per-turn budget of a networked chooser (an agent's resolver.timeout_ms).
TURN_BUDGET_MS = 2500

# Shared by every call's client. httpx timeouts are per phase (connect, then read); waiting on the
# pool bounds the total. A turn may have a choice and its prefetched check in flight, and a request
# we stopped waiting for holds its worker until httpx's own timeout ends it.
POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="model")


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """One for every client: building a context loads the CA bundle (~250 ms, on the call's path)."""
    return httpx.create_ssl_context()


class SpendCapExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelCall:
    """One model request: what it decided, where the answer came from, how long it took, its cost."""

    key: str
    source: str             # "live" | "cache" | "memo" | "failed" | "local" (embeddings, on this CPU)
    latency_ms: float       # this process for live/failed/local; the original fetch for cache hits
    input_tokens: int = 0
    output_tokens: int = 0
    purpose: str = ""       # what the request decided, for the Dev view: "type", "type check", ...
    p: float | None = None  # the answer's top probability, when it has one
    usd: float = 0.0        # 0 for memo and failed; what a cache hit cost when it was fetched


def cache_key(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class CachedModelClient:
    """mode "live": every request not yet seen in this process goes to the network and refreshes
    the disk cache. mode "cache": disk cache only, never the network (offline, deterministic).
    mode "auto": cache first, network on a miss.

    Defaults are for a live call: REQUEST_TIMEOUT_S per request, no retry, and at most
    `turn_budget_s` of model time between begin_turn() calls (one resolve() can consult the model
    several times: a type choice and its check, then provider gender). `live_limit` aborts a run
    that would send more than that many network requests (the eval's spend cap).

    A subclass builds the request (_send) and reads its answer into a cache entry (_entry)."""

    provider = ""
    key_env = ""
    _retry_on: tuple[type[BaseException], ...] = ()
    _give_up_on: tuple[type[BaseException], ...] = ()

    def __init__(self, api_key: str | None, *, mode: str = "auto", cache_path: Path | None = None,
                 timeout_s: float = REQUEST_TIMEOUT_S, retries: int = 0,
                 turn_budget_s: float | None = TURN_BUDGET_MS / 1000, live_limit: int | None = None):
        if mode not in ("live", "cache", "auto"):
            raise ValueError(f"unknown {self.provider} mode {mode!r}")
        self.api_key, self.mode, self.cache_path = api_key, mode, cache_path
        self.timeout_s, self.retries, self.turn_budget_s, self.live_limit = timeout_s, retries, turn_budget_s, live_limit
        self.calls: list[ModelCall] = []
        self._deadline: float | None = None
        self._disk: dict[str, dict] = json.loads(cache_path.read_text(encoding="utf-8")) \
            if cache_path and cache_path.exists() else {}
        self._memo: dict[str, dict] = {}
        self._dirty = False
        self._sent = 0
        self._early: dict[str, tuple[float, float, Future]] = {}  # key -> (sent at, timeout, response)

    @classmethod
    def from_env(cls, env_file: Path | None = None, **kw):
        from dotenv import load_dotenv
        load_dotenv(env_file or Path(__file__).resolve().parents[1] / ".env")
        return cls(os.environ.get(cls.key_env), **kw)

    # ---- public --------------------------------------------------------------------------

    def begin_turn(self) -> None:
        """Start the per-turn budget. Call once before each resolve()."""
        self._deadline = time.perf_counter() + self.turn_budget_s if self.turn_budget_s is not None else None
        self._early.clear()

    def prefetch(self, body: dict) -> None:
        """Send `body` now without waiting: the next _fetch of the same request waits for this one
        instead of sending its own. Only a request that would go to the network goes early; one
        never asked for is dropped at the next turn, unrecorded."""
        key = cache_key(body)
        if (key in self._early or key in self._memo or self.mode == "cache" or not self.api_key
                or (self.mode != "live" and key in self._disk)
                or (self.live_limit is not None and self._sent >= self.live_limit)):
            return
        timeout = self._timeout()
        if timeout > 0.05:
            self._sent += 1
            self._early[key] = (time.perf_counter(), timeout, POOL.submit(self._timed_send, body, timeout))

    def save(self) -> None:
        if not (self.cache_path and self._dirty):
            return
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._disk, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.cache_path)
        self._dirty = False

    def close(self) -> None:
        raise NotImplementedError

    # ---- subclass ------------------------------------------------------------------------

    def _send(self, body: dict, timeout: float) -> Any:
        """The raw response to `body`; runs on the shared pool."""
        raise NotImplementedError

    def _entry(self, body: dict, response: Any, ms: float) -> dict | None:
        """The cache entry for a response (with input_tokens and latency_ms), or None if malformed."""
        raise NotImplementedError

    def _usd(self, entry: dict) -> float:
        raise NotImplementedError

    # ---- internals -----------------------------------------------------------------------

    def _fetch(self, body: dict, purpose: str, read: Callable[[dict], tuple[Any, float | None]]) -> Any:
        """read(entry) -> (answer, p). Memo, then disk, then the network; every request is recorded."""
        key = cache_key(body)
        entry, source = self._memo.get(key), "memo"
        if entry is None and self.mode != "live" and key in self._disk:
            entry, source = self._memo.setdefault(key, self._disk[key]), "cache"
        if entry is None:
            if self.mode == "cache" or not self.api_key:
                self.calls.append(ModelCall(key, "failed", 0.0, purpose=purpose))
                return None
            sent = self._early.pop(key, None)
            if sent is None:
                if self.live_limit is not None and self._sent >= self.live_limit:
                    raise SpendCapExceeded(f"more than {self.live_limit} uncached {self.provider} requests in one run")
                self._sent += 1
            entry, ms = self._post(body, sent=sent)
            if entry is None:
                self.calls.append(ModelCall(key, "failed", ms, purpose=purpose))
                return None
            self._memo[key] = self._disk[key] = entry
            self._dirty = True
            source = "live"
        answer, p = read(entry)
        self.calls.append(ModelCall(key, source, 0.0 if source == "memo" else entry["latency_ms"],
                                    entry["input_tokens"], entry.get("output_tokens", 0), purpose, p,
                                    0.0 if source == "memo" else self._usd(entry)))
        return answer

    def _post(self, body: dict, timeout_s: float | None = None, use_budget: bool = True,
              sent: tuple[float, float, Future] | None = None) -> tuple[dict | None, float]:
        """Send `body` within the request timeout and what is left of the turn: (entry, ms).
        `sent`: the same request, already sent by prefetch(); its first attempt is that one."""
        t0 = sent[0] if sent else time.perf_counter()
        response = done = None
        for attempt in range(1 + self.retries):
            if attempt == 0 and sent:
                timeout, future = max(0.0, sent[0] + sent[1] - time.perf_counter()), sent[2]
            else:
                timeout = self._timeout(timeout_s, use_budget)
                if timeout <= 0.05:
                    break
                future = POOL.submit(self._timed_send, body, timeout)
            try:
                response, done = future.result(timeout=timeout)
                break
            except self._retry_on:
                continue
            except (*self._give_up_on, FutureTimeout):
                break
        ms = round(((done or time.perf_counter()) - t0) * 1000, 1)
        return (self._entry(body, response, ms) if response is not None else None), ms

    def _timeout(self, timeout_s: float | None = None, use_budget: bool = True) -> float:
        timeout = timeout_s or self.timeout_s
        if use_budget and self._deadline is not None:
            timeout = min(timeout, self._deadline - time.perf_counter())
        return timeout

    def _timed_send(self, body: dict, timeout: float) -> tuple[Any, float]:
        return self._send(body, timeout), time.perf_counter()
