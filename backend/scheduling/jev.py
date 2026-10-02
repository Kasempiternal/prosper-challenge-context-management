"""JEV (Command Code decision model) client and the two resolver hooks built on it.

The client never raises into the resolver: any failure, timeout or malformed answer is None,
and the hooks turn None into a declined Verdict, which makes the resolver behave exactly as it
does with no model.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger

from .catalog_index import CatalogIndex
from .decision import DECLINE, Gate, Verdict
from .lexicon import ranked_types

URL = "https://api.commandcode.ai/provider/v1/systemone"
MODEL = "typesafe/jev"
USD_PER_INPUT_TOKEN = 0.04 / 1_000_000
_QUESTION = "pick"
MAX_CHOICE_OPTIONS = 255  # JEV answers HTTP 400 "TypeSafe Choice questions support at most 255 options"


@dataclass(frozen=True)
class JevAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class JevCall:
    key: str
    source: str            # "live" | "cache" | "memo" | "failed"
    latency_ms: float      # this process for live/failed; the original fetch for cache hits
    input_tokens: int

    @property
    def usd(self) -> float:
        return 0.0 if self.source in ("memo", "failed") else self.input_tokens * USD_PER_INPUT_TOKEN


class JevClient:
    """mode "live": every request not yet seen in this process goes to the network and refreshes
    the disk cache. mode "cache": disk cache only, never the network (offline, deterministic).
    mode "auto": cache first, network on a miss.

    Defaults are for a live call: 1.2 s per request, no retry, and at most `turn_budget_s` of JEV
    time between begin_turn() calls (one resolve() can consult JEV twice: type, then provider).
    The offline eval passes a longer timeout and one retry on connect errors."""

    provider = "jev"

    def __init__(self, api_key: str | None, *, mode: str = "auto", cache_path: Path | None = None,
                 timeout_s: float = 1.2, retries: int = 0, turn_budget_s: float | None = 1.2,
                 warmup_timeout_s: float = 15.0, transport: httpx.BaseTransport | None = None):
        if mode not in ("live", "cache", "auto"):
            raise ValueError(f"unknown JEV mode {mode!r}")
        self.mode = mode
        self.timeout_s, self.retries = timeout_s, retries
        self.turn_budget_s, self.warmup_timeout_s = turn_budget_s, warmup_timeout_s
        self._deadline: float | None = None
        self.api_key = api_key
        self.cache_path = cache_path
        self.calls: list[JevCall] = []
        self._disk: dict[str, dict] = self._load_cache()
        self._memo: dict[str, dict] = {}
        self._dirty = False
        # httpx timeouts are per phase (connect, then read); the pool lets us bound the total wait.
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jev")
        self._http = httpx.Client(transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}"} if api_key else {})

    @classmethod
    def from_env(cls, env_file: Path | None = None, **kw) -> "JevClient":
        from dotenv import load_dotenv
        load_dotenv(env_file or Path(__file__).resolve().parents[1] / ".env")
        return cls(os.environ.get("CMD_API_KEY"), **kw)

    # ---- public --------------------------------------------------------------------------

    def begin_turn(self) -> None:
        """Start the per-turn JEV budget. Call once before each resolve()."""
        self._deadline = time.perf_counter() + self.turn_budget_s if self.turn_budget_s is not None else None

    def choice(self, state: str, instructions: str, criteria: dict[str, str],
               ranking: list[str] = ()) -> JevAnswer | None:
        """ranking: option ids best first; decides which options survive the MAX_CHOICE_OPTIONS cap."""
        criteria = cap_options(criteria, ranking)
        entry = self._fetch(_choice_body(state, instructions, criteria))
        return _parse(entry["answer"], criteria) if entry else None

    def warm_up(self, criteria: dict[str, str]) -> JevCall | None:
        """Pays the server-side cold path with a request shaped like the ones the caller will need.
        Always goes to the network and is never cached, so it measures and warms the real path."""
        body = _choice_body("A patient calling a multi-specialty clinic said they want: 'an appointment'",
                            "Which appointment type is the caller asking for?", cap_options(criteria))
        entry, call = self._post(body, _key(body), timeout_s=self.warmup_timeout_s, use_budget=False)
        self.calls.append(call)
        return call if entry else None

    def save(self) -> None:
        if not (self.cache_path and self._dirty):
            return
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._disk, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.cache_path)
        self._dirty = False

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._http.close()

    # ---- internals -----------------------------------------------------------------------

    def _load_cache(self) -> dict[str, dict]:
        if self.cache_path and self.cache_path.exists():
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        return {}

    def _fetch(self, body: dict) -> dict | None:
        key = _key(body)
        if key in self._memo:
            entry = self._memo[key]
            self.calls.append(JevCall(key, "memo", 0.0, entry["input_tokens"]))
            return entry
        if self.mode != "live" and key in self._disk:
            entry = self._memo[key] = self._disk[key]
            self.calls.append(JevCall(key, "cache", entry["latency_ms"], entry["input_tokens"]))
            return entry
        if self.mode == "cache" or not self.api_key:
            self.calls.append(JevCall(key, "failed", 0.0, 0))
            return None
        entry, call = self._post(body, key)
        self.calls.append(call)
        if entry:
            self._memo[key] = self._disk[key] = entry
            self._dirty = True
        return entry

    def _post(self, body: dict, key: str, timeout_s: float | None = None,
              use_budget: bool = True) -> tuple[dict | None, JevCall]:
        t0 = time.perf_counter()
        resp = None
        for _ in range(1 + self.retries):
            timeout = timeout_s or self.timeout_s
            if use_budget and self._deadline is not None:
                timeout = min(timeout, self._deadline - time.perf_counter())
            if timeout <= 0.05:
                break
            try:
                resp = self._pool.submit(self._http.post, URL, json=body, timeout=timeout).result(timeout=timeout)
                break
            except httpx.ConnectError:
                continue
            except (httpx.HTTPError, FutureTimeout):
                break
        ms = round((time.perf_counter() - t0) * 1000, 1)
        try:
            data = resp.json() if resp is not None and resp.status_code == 200 else None
            answer = data["answers"][_QUESTION] if data else None
            tokens = int(data.get("usage", {}).get("input_tokens", 0)) if data else 0
        except (ValueError, KeyError, TypeError, AttributeError):
            answer, tokens = None, 0
        if answer is None:
            return None, JevCall(key, "failed", ms, 0)
        entry = {"state": body["state"], "answer": answer, "input_tokens": tokens, "latency_ms": ms}
        return entry, JevCall(key, "live", ms, tokens)


def _choice_body(state: str, instructions: str, criteria: dict[str, str]) -> dict:
    return {"model": MODEL, "state": state,
            "questions": {_QUESTION: {"type": "choice", "instructions": instructions, "criteria": criteria}}}


def cap_options(criteria: dict[str, str], ranking: list[str] = ()) -> dict[str, str]:
    """At most MAX_CHOICE_OPTIONS options: the ranked ones first, then in the given order. Kept
    options stay in the given order, so a request under the cap is unchanged."""
    if len(criteria) <= MAX_CHOICE_OPTIONS:
        return criteria
    keep = set(list(dict.fromkeys([k for k in ranking if k in criteria] + list(criteria)))[:MAX_CHOICE_OPTIONS])
    logger.warning(f"JEV choice has {len(criteria)} options; keeping the {MAX_CHOICE_OPTIONS} best ranked")
    return {k: v for k, v in criteria.items() if k in keep}


def _key(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _parse(answer: dict, criteria: dict[str, str]) -> JevAnswer | None:
    try:
        probs = {k: float(v) for k, v in answer["probabilities"].items() if k in criteria}
        return JevAnswer(str(answer["choice"]), float(answer.get("confidence", 0.0)), probs) if probs else None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


# ---- resolver hooks ----------------------------------------------------------------------

def type_criteria(index: CatalogIndex, type_ids: list[str]) -> dict[str, str]:
    out = {}
    for tid in sorted(type_ids):
        t = index.types[tid]
        out[tid] = f"{t.name} ({t.specialty}, {t.duration_min} min)"
    return out


def offered_type_criteria(index: CatalogIndex) -> dict[str, str]:
    return type_criteria(index, [t for t in index.types if t not in index.unoffered_types])


def provider_criteria(index: CatalogIndex, provider_ids: list[str]) -> dict[str, str]:
    out = {}
    for pid in sorted(provider_ids):
        p = index.providers[pid]
        sites = ", ".join(index.locations[l].short_name for l in p.location_ids)
        out[pid] = f"{p.name}, {p.title}, {p.specialty}; works at {sites}; speaks {', '.join(p.languages)}"
    return out


class JevTypeDisambiguator:
    def __init__(self, index: CatalogIndex, client: JevClient, gate: Gate = Gate()):
        self.ix, self.client, self.gate = index, client, gate

    def pick_type(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> Verdict:
        said = f"A patient calling a multi-specialty clinic said they want: '{phrase}'"
        if hint:
            said += f" (specialty mentioned: {hint})"
        ans = self.client.choice(said, "Which appointment type is the caller asking for?",
                                 type_criteria(self.ix, candidate_ids), ranking=ranked_types(self.ix, phrase, hint))
        return self.gate.decide(ans.probabilities) if ans else DECLINE


class JevProviderChooser:
    def __init__(self, index: CatalogIndex, client: JevClient, gate: Gate = Gate()):
        self.ix, self.client, self.gate = index, client, gate

    def pick_provider(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict:
        visit = self.ix.types[type_id].name if type_id else "an appointment"
        said = (f"A patient calling a multi-specialty clinic to book {visit} asked for the provider: "
                f"'{phrase}'")
        ans = self.client.choice(said, "Which provider does the caller mean?",
                                 provider_criteria(self.ix, candidate_ids))
        return self.gate.decide(ans.probabilities) if ans else DECLINE


def site_criteria(index: CatalogIndex, location_ids: list[str]) -> dict[str, str]:
    out = {}
    for lid in sorted(location_ids):
        loc = index.locations[lid]
        where = ", ".join(filter(None, [loc.neighborhood, loc.address, loc.city]))
        caps = ", ".join(sorted(loc.capabilities)) or "general visits only"
        out[lid] = f"{loc.name}; {where}; on site: {caps}"
    return out


class JevSiteChooser:
    """Splits the clinics of an area search using the caller's description of the place."""

    def __init__(self, index: CatalogIndex, client: JevClient, gate: Gate = Gate()):
        self.ix, self.client, self.gate = index, client, gate

    def pick_site(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict:
        visit = self.ix.types[type_id].name if type_id else "an appointment"
        said = (f"A patient calling a multi-specialty clinic to book {visit} described the clinic location "
                f"they want: '{phrase}'")
        ans = self.client.choice(said, "Which clinic location does the caller mean?",
                                 site_criteria(self.ix, candidate_ids))
        return self.gate.decide(ans.probabilities) if ans else DECLINE
