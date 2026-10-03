"""API keys a request brings: a key the browser sends overrides the server's .env for that one call
or request (scheduling.choosers.call_key). A key is never written to disk, echoed or logged, nor
any part or the length of one.

The Pipecat runner logs every /start body at DEBUG (and the WebRTC handler logs it again with the
offer), so install_log_redaction() blanks key fields in every log line.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx
from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from loguru import logger

from scheduling.choosers import NO_KEYS, call_key
from scheduling.jev import MODEL as JEV_MODEL, URL as JEV_URL, JevClient
from scheduling.model_client import ssl_context
from scheduling.openai_chooser import OpenAIChoiceClient

OPENAI_ENV = OpenAIChoiceClient.key_env
ELEVENLABS_ENV = "ELEVENLABS_API_KEY"
JEV_ENV = JevClient.key_env
START_FIELD = "api_keys"           # in the /start body: {env var name: key}
LEGACY_START_FIELD = "cmd_api_key"  # the JEV key alone, as the studio sent it before
HEADER = "X-Api-Key"               # on /api/keys/test
JEV_HEADER = "X-CMD-API-Key"       # on /api/grade; still accepted by /api/keys/test
TEST_TIMEOUT_S = 15.0              # JEV's cold start, as for a call's warm-up

OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
ELEVENLABS_API = "https://api.elevenlabs.io"

# A two-option choice: about the smallest request JEV answers.
JEV_PROBE = {"model": JEV_MODEL, "state": "An API key check.",
             "questions": {"check": {"type": "choice", "instructions": "Is this a check?",
                                     "criteria": {"yes": "Yes", "no": "No"}}}}

Probe = Callable[[httpx.Client, str], dict]  # (client, key) -> {"ok"} plus "error" or "limited"


def _verdict(response: httpx.Response, answered: bool) -> dict:
    if response.status_code in (401, 403):
        return {"ok": False, "error": "invalid key"}
    return {"ok": True} if answered else {"ok": False, "error": "unexpected response"}


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def probe_openai(http: httpx.Client, key: str) -> dict:
    """The model list: free, and any working key may read it."""
    response = http.get(OPENAI_MODELS_URL, headers={"Authorization": f"Bearer {key}"}, timeout=TEST_TIMEOUT_S)
    body = _json(response)
    return _verdict(response, response.status_code == 200 and isinstance(body, dict) and isinstance(body.get("data"), list))


def _missing_permission(response: httpx.Response) -> bool:
    """ElevenLabs knows the key but it is restricted: 401 {"detail": {"status": "missing_permissions"}}."""
    body = _json(response)
    detail = body.get("detail") if isinstance(body, dict) else None
    return response.status_code in (401, 403) and isinstance(detail, dict) and detail.get("status") == "missing_permissions"


def probe_elevenlabs(http: httpx.Client, key: str) -> dict:
    """The account (free). A restricted key without user_read falls back to the model list; a key
    ElevenLabs recognizes but restricts is valid with limited permissions, not rejected."""
    for path in ("/v1/user", "/v1/models"):
        response = http.get(ELEVENLABS_API + path, headers={"xi-api-key": key}, timeout=TEST_TIMEOUT_S)
        if _missing_permission(response):
            continue
        verdict = _verdict(response, response.status_code == 200 and _json(response) is not None)
        return {**verdict, "limited": True} if verdict["ok"] and path != "/v1/user" else verdict
    return {"ok": True, "limited": True}


def probe_jev(http: httpx.Client, key: str) -> dict:
    response = http.post(JEV_URL, json=JEV_PROBE, timeout=TEST_TIMEOUT_S, headers={"Authorization": f"Bearer {key}"})
    try:
        answered = response.status_code == 200 and "choice" in response.json()["answers"]["check"]
    except (ValueError, KeyError, TypeError):
        answered = False
    return _verdict(response, answered)


@dataclass(frozen=True)
class Provider:
    env: str
    probe: Probe


PROVIDERS: dict[str, Provider] = {
    "openai": Provider(OPENAI_ENV, probe_openai),
    "elevenlabs": Provider(ELEVENLABS_ENV, probe_elevenlabs),
    "jev": Provider(JEV_ENV, probe_jev),
}
CALL_ENVS = (OPENAI_ENV, ELEVENLABS_ENV)  # a call cannot start without these
START_ENVS = frozenset(p.env for p in PROVIDERS.values())


def check_key(provider: str, api_key: str, transport: Optional[httpx.BaseTransport] = None) -> dict:
    """One free or tiny live request with `api_key`: {"ok", "ms"}, plus "error" on failure and
    "limited" for a key that works with restricted permissions."""
    started = time.perf_counter()
    try:
        with httpx.Client(transport=transport, verify=ssl_context()) as http:
            verdict = PROVIDERS[provider].probe(http, api_key)
    except httpx.HTTPError:
        verdict = {"ok": False, "error": "network"}
    return {**verdict, "ms": round((time.perf_counter() - started) * 1000)}


# ---- the /start body ------------------------------------------------------------------------------

def take_start_keys(body: Any) -> dict[str, str]:
    """This call's API keys by env var name, popped from the /start body: the runner keeps that body
    as its session record for the life of the process. Only the keys a call uses are kept."""
    if not isinstance(body, dict):
        return {}
    sent = body.pop(START_FIELD, None)
    legacy = body.pop(LEGACY_START_FIELD, None)
    keys = dict(sent) if isinstance(sent, dict) else {}
    keys.setdefault(JEV_ENV, legacy)
    return {name: key.strip() for name, key in keys.items()
            if name in START_ENVS and isinstance(key, str) and key.strip()}


# ---- logs -------------------------------------------------------------------------------------------

_NAMES = (r"cmd_api_key|openai_api_key|elevenlabs_api_key|api_keys?|x-cmd-api-key|x-api-key|xi-api-key"
          r"|authorization")
_KEY_FIELD = re.compile(
    rf"""(?P<name>["']?\b(?:{_NAMES})["']?\s*[:=]\s*)"""
    r"""(?:(?P<q>["'])(?:\\.|(?!(?P=q)).)*(?P=q)|\{[^{}]*\}|(?:bearer\s+)?[^\s'",;{}\[\]()]+)""",
    re.IGNORECASE)
_BEARER = re.compile(r"\b(bearer\s+)[^\s'\",;{}\[\]()]+", re.IGNORECASE)


def _blank(match: re.Match) -> str:
    quote = match.group("q") or ""
    return f"{match.group('name')}{quote}[redacted]{quote}"


def redact(text: str) -> str:
    """Blanks the value of every key field and header (any quoting, a whole api_keys object, a bare
    header value) and every bearer token."""
    return _BEARER.sub(r"\1[redacted]", _KEY_FIELD.sub(_blank, text))


def install_log_redaction() -> None:
    """A loguru patcher outlives the runner's logger.remove(), so it covers the sinks it adds later."""
    logger.configure(patcher=lambda record: record.update(message=redact(record["message"])))


# ---- /api/keys ----------------------------------------------------------------------------------------

def create_keys_router(transport: Optional[httpx.BaseTransport] = None) -> APIRouter:
    router = APIRouter(prefix="/api/keys")
    in_flight = {name: threading.Lock() for name in PROVIDERS}

    @router.get("/status")
    def status() -> dict:
        """Whether the server has each key. Never a key, a part of one or its length."""
        return {name: {"server_key": call_key(p.env, NO_KEYS) is not None} for name, p in PROVIDERS.items()}

    @router.post("/test")
    def test(provider: str = Query(default="jev"),
             key: Optional[str] = Header(default=None, alias=HEADER),
             jev_key: Optional[str] = Header(default=None, alias=JEV_HEADER)):
        if provider not in PROVIDERS:
            return JSONResponse(status_code=400, content={"ok": False, "ms": 0, "error": "unknown provider"})
        key = (key or "").strip() or (jev_key or "").strip()
        if not key:
            return JSONResponse(status_code=400, content={"ok": False, "ms": 0, "error": "missing key"})
        lock = in_flight[provider]
        if not lock.acquire(blocking=False):
            return JSONResponse(status_code=429, content={"ok": False, "ms": 0, "error": "busy"})
        try:
            return check_key(provider, key, transport)
        finally:
            lock.release()

    return router
