"""Post-call grader: one JEV request with five named questions over a finished test call.

Nobody is on the line, so the client gets a generous timeout and one retry.
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scheduling.jev import MODEL, URL, USD_PER_INPUT_TOKEN, JevClient

TIMEOUT_S = 8.0
RETRIES = 1
TRANSCRIPT_TOKEN_BUDGET = 6000
CHARS_PER_TOKEN = 4
COLLECTED_CHAR_BUDGET = 2000
PERSONA_CHAR_BUDGET = 400

EFFORT_LEVELS = ["1 very easy", "2 easy", "3 moderate", "4 hard", "5 very hard"]
OUTCOMES = {
    "booked": "An appointment was booked.",
    "refused_correctly": "The agent correctly declined because the request cannot be served (not offered, policy, no slot).",
    "handed_off": "The caller was transferred or handed off to staff.",
    "abandoned": "The caller hung up or gave up before anything was resolved.",
    "unclear": "The transcript does not show a clear outcome.",
}

QUESTIONS: dict[str, dict] = {
    "booked_correctly": {
        "type": "noul",
        "instructions": "Did the agent book (or correctly refuse) what the caller actually asked for, honoring their stated preferences?",
    },
    "unnecessary_questions": {
        "type": "noul",
        "instructions": "Did the agent ask any question whose answer it already had or that didn't change the outcome?",
    },
    "unsupported_claims": {
        "type": "noul",
        "instructions": "Did the agent state any fact (doctor, time, location, policy) not supported by the decisions/tool results shown?",
    },
    "caller_effort": {
        "type": "score",
        "instructions": "How much effort did the caller need? 1 = very easy, 5 = very hard.",
        "criteria": EFFORT_LEVELS,
    },
    "outcome": {
        "type": "choice",
        "instructions": "What was the outcome of the call?",
        "criteria": OUTCOMES,
    },
}


class GradeError(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


class Turn(BaseModel):
    role: Literal["user", "bot"]
    text: str


class GradeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent_id: Optional[str] = None
    agent: Optional[dict[str, Any]] = None
    transcript: list[Turn] = Field(min_length=1)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    collected: dict[str, Any] = Field(default_factory=dict)


def parse_request(payload: Any) -> GradeRequest:
    try:
        req = GradeRequest.model_validate(payload)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "body"
        raise GradeError(422, f"{where}: {first['msg']}") from None
    if not any(t.text.strip() for t in req.transcript):
        raise GradeError(422, "transcript: every turn is empty")
    return req


def build_state(req: GradeRequest, agent: Optional[dict] = None) -> str:
    parts = []
    if agent:
        persona = str(agent.get("persona", ""))[:PERSONA_CHAR_BUDGET]
        parts.append(f"AGENT: {agent.get('name', agent.get('id', 'agent'))}. {persona}".strip())
    parts.append("TRANSCRIPT (caller = user, agent = bot):\n" + _tail_transcript(req.transcript))
    if req.decisions:
        parts.append("RESOLVER DECISIONS (what the scheduling tools returned, in order):\n"
                     + "\n".join(_decision_line(d) for d in req.decisions))
    else:
        parts.append("RESOLVER DECISIONS: none recorded.")
    if req.collected:
        collected = json.dumps(req.collected, ensure_ascii=False, separators=(",", ":"), default=str)
        parts.append("COLLECTED DATA: " + collected[:COLLECTED_CHAR_BUDGET])
    return "\n\n".join(parts)


def _tail_transcript(turns: list[Turn]) -> str:
    budget = TRANSCRIPT_TOKEN_BUDGET * CHARS_PER_TOKEN
    lines: list[str] = []
    for t in reversed(turns):
        text = " ".join(t.text.split())
        if not text:
            continue
        line = f"{'Caller' if t.role == 'user' else 'Agent'}: {text}"
        if len(line) > budget:
            if not lines:
                lines.append(line[-budget:])
            break
        lines.append(line)
        budget -= len(line) + 1
    if len(lines) < sum(1 for t in turns if t.text.strip()):
        lines.append("[earlier turns omitted]")
    return "\n".join(reversed(lines))


def _decision_line(d: dict) -> str:
    out = f"- {d.get('status', '?')}"
    if d.get("say"):
        out += f': said "{d["say"]}"'
    offers = d.get("offers")
    if isinstance(offers, list) and offers:
        out += "; offers: " + " | ".join(str(o) for o in offers)
    if d.get("reason"):
        out += f"; reason: {d['reason']}"
    return out


def grade(client: JevClient, req: GradeRequest, agent: Optional[dict] = None) -> dict:
    if not client.api_key:
        raise GradeError(503, "JEV not configured")
    body = {"model": MODEL, "state": build_state(req, agent), "questions": QUESTIONS}
    t0 = time.perf_counter()
    resp = _post(client, body)
    ms = round((time.perf_counter() - t0) * 1000)
    if resp.status_code in (401, 403):
        raise GradeError(503, "JEV rejected the API key")
    if resp.status_code != 200:
        raise GradeError(502, f"JEV returned {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
        scores = parse_answers(data["answers"])
        input_tokens = int(data.get("usage", {}).get("input_tokens", 0))
        output_tokens = int(data.get("usage", {}).get("output_tokens", 0))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise GradeError(502, f"JEV returned an unexpected answer: {e!r}") from None
    return {
        "ok": True,
        "scores": scores,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "usd": round(input_tokens * USD_PER_INPUT_TOKEN, 8)},
        "ms": ms,
    }


def _post(client: JevClient, body: dict) -> httpx.Response:
    # JevClient._post only extracts its single "pick" answer and folds timeouts into a generic
    # failure; the grader needs every answer and a distinct timeout, so it uses the client's session.
    last: Exception | None = None
    for _ in range(1 + client.retries):
        try:
            return client._http.post(URL, json=body, timeout=client.timeout_s)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            last = e
        except httpx.HTTPError as e:
            raise GradeError(502, f"JEV request failed: {e.__class__.__name__}") from None
    if isinstance(last, httpx.TimeoutException):
        raise GradeError(504, f"JEV timed out after {client.timeout_s:g} s")
    raise GradeError(502, "Could not reach JEV")


def parse_answers(answers: dict) -> dict:
    """Probabilities of "yes" for the noul checks, an expected 1..5 effort, and the outcome choice."""
    scores: dict[str, Any] = {}
    for name in ("booked_correctly", "unnecessary_questions", "unsupported_claims"):
        scores[name] = {"p": _prob(answers[name]["noul"])}
    effort = answers["caller_effort"]
    by_index = {int(k): _prob(v) for k, v in effort["probabilities"].items()}
    if not by_index:
        raise ValueError("caller_effort has no probabilities")
    scores["caller_effort"] = {
        "level": round(1 + sum(i * p for i, p in by_index.items()) / (sum(by_index.values()) or 1), 2),
        "probabilities": {str(i + 1): by_index[i] for i in sorted(by_index)},
        "confidence": _prob(effort.get("confidence", 0)),
    }
    outcome = answers["outcome"]
    if outcome["choice"] not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome['choice']!r}")
    scores["outcome"] = {
        "choice": outcome["choice"],
        "confidence": _prob(outcome.get("confidence", 0)),
        "probabilities": {k: _prob(v) for k, v in outcome["probabilities"].items() if k in OUTCOMES},
    }
    return scores


def _prob(v: Any) -> float:
    p = float(v)
    if not 0 <= p <= 1:
        raise ValueError(f"probability out of range: {p}")
    return round(p, 4)


def client_from_env() -> JevClient:
    return JevClient.from_env(mode="live", timeout_s=TIMEOUT_S, retries=RETRIES, turn_budget_s=None)
