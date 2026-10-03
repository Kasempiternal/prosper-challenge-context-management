import argparse
import asyncio
import importlib
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import dotenv
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

import api_keys
from agent_builder import AgentBuilder, AgentConfig
from agent_tools.context import make_context, resolver_mode_event, warm_up_model
from agents_api import create_router
from scheduling.jev import URL as JEV_URL, JevClient

CLINIC = Path(__file__).resolve().parent.parent / "agents" / "clinic-scheduler.json"
ENVS = ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "CMD_API_KEY")
SERVER = {"OPENAI_API_KEY": "sk-server-openai-0000", "ELEVENLABS_API_KEY": "el-server-0000",
          "CMD_API_KEY": "server-key-0000"}
BROWSER = {"OPENAI_API_KEY": "sk-proj-browser4Hq9Zt7Lm2Wx", "ELEVENLABS_API_KEY": "sk_el_browser8f2d6c1a9b3e",
           "CMD_API_KEY": "cc-browser-9f3a7Kq2Lw81xYz"}
PROVIDER_ENV = {"openai": "OPENAI_API_KEY", "elevenlabs": "ELEVENLABS_API_KEY", "jev": "CMD_API_KEY"}
GRADE_BODY = {"transcript": [{"role": "user", "text": "Hi"}, {"role": "bot", "text": "Hello"}]}


@pytest.fixture
def server_keys(monkeypatch):
    for name, key in SERVER.items():
        monkeypatch.setenv(name, key)


@pytest.fixture
def no_server_keys(monkeypatch):
    for name in ENVS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def logs(caplog):
    """Loguru (ours and Pipecat's) into caplog, through the redaction bot.py installs."""
    api_keys.install_log_redaction()
    sink = logger.add(caplog.handler, format="{message}", level="DEBUG")
    yield caplog
    logger.remove(sink)
    logger.configure(patcher=None)


@pytest.fixture
def runner(monkeypatch):
    """pipecat.runner.run and bot.py load a .env when first imported (the runner's search finds
    backend/.env from the venv); in tests that would put real keys in this process."""
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    return importlib.import_module("pipecat.runner.run")


@pytest.fixture
def bot(runner):
    module = importlib.import_module("bot")
    yield module
    logger.configure(patcher=None)


def bearer(client) -> str:
    return client._http.headers.get("Authorization", "")


def assert_no_key(*texts: str) -> None:
    for text in texts:
        for key in (*BROWSER.values(), *SERVER.values()):
            assert key not in text


# ---- call_key: the call's key over the server's, per provider ----------------------------------

@pytest.mark.parametrize("name", ENVS)
@pytest.mark.parametrize("server, sent, expected", [
    (True, "browser", "browser"),
    (True, None, "server"),
    (False, "browser", "browser"),
    (False, None, None),
    (True, "blank", "server"),
])
def test_call_key_prefers_the_call_key(monkeypatch, name, server, sent, expected):
    monkeypatch.delenv(name, raising=False)
    if server:
        monkeypatch.setenv(name, SERVER[name])
    keys = {"browser": {name: f" {BROWSER[name]}\n"}, "blank": {name: "  "}, None: {}}[sent]
    want = {"browser": BROWSER[name], "server": SERVER[name], None: None}[expected]
    assert api_keys.call_key(name, keys) == want


def test_call_key_reads_only_its_own_name(server_keys):
    assert api_keys.call_key("OPENAI_API_KEY", {"CMD_API_KEY": BROWSER["CMD_API_KEY"]}) == SERVER["OPENAI_API_KEY"]


@pytest.mark.parametrize("chooser", ["jev", "openai"])
@pytest.mark.parametrize("env, keys, sent", [
    (SERVER, BROWSER, "browser"),
    (SERVER, {}, "server"),
    ({}, BROWSER, "browser"),
    ({}, {}, None),
])
def test_chooser_client_gets_the_call_key(monkeypatch, no_server_keys, chooser, env, keys, sent):
    for name, key in env.items():
        monkeypatch.setenv(name, key)
    name = PROVIDER_ENV[chooser]
    ctx = make_context("data/catalog.json", speak_direct=True, chooser=chooser, api_keys=keys)
    if sent is None:
        assert ctx.provider == "none"
        return
    want = (BROWSER if sent == "browser" else SERVER)[name]
    assert ctx.provider == chooser and ctx.model_client.api_key == want
    if chooser == "jev":
        assert bearer(ctx.model_client) == f"Bearer {want}"
    else:
        assert ctx.model_client._api.api_key == want


@pytest.mark.parametrize("chooser, other", [("openai", "CMD_API_KEY"), ("jev", "OPENAI_API_KEY")])
def test_a_key_never_reaches_another_chooser(no_server_keys, chooser, other):
    ctx = make_context("data/catalog.json", speak_direct=True, chooser=chooser, api_keys={other: BROWSER[other]})
    assert ctx.provider == "none"


@pytest.mark.parametrize("env, keys, sent", [(SERVER, BROWSER, BROWSER), (SERVER, {}, SERVER),
                                             ({}, BROWSER, BROWSER)])
def test_call_services_get_the_call_keys(bot, monkeypatch, no_server_keys, env, keys, sent):
    for name, key in env.items():
        monkeypatch.setenv(name, key)
    config = AgentConfig.from_dict(json.loads(CLINIC.read_text(encoding="utf-8")))
    stt, tts, llm = bot.make_services(config, keys, None)
    assert stt._api_key == tts._api_key == sent["ELEVENLABS_API_KEY"]
    assert llm._client.api_key == sent["OPENAI_API_KEY"]


@pytest.mark.parametrize("keys, missing", [
    ({}, "OPENAI_API_KEY or ELEVENLABS_API_KEY"),
    ({"OPENAI_API_KEY": BROWSER["OPENAI_API_KEY"]}, "ELEVENLABS_API_KEY"),
    ({"ELEVENLABS_API_KEY": BROWSER["ELEVENLABS_API_KEY"]}, "OPENAI_API_KEY"),
])
def test_a_call_without_its_keys_never_starts(bot, logs, no_server_keys, monkeypatch, keys, missing):
    def no_transport(*args):
        raise AssertionError("no transport without keys")

    monkeypatch.setattr(bot, "create_transport", no_transport)
    disconnected = []

    async def disconnect():
        disconnected.append(True)

    runner_args = SimpleNamespace(body={"api_keys": dict(keys)}, session_id="s1",
                                  webrtc_connection=SimpleNamespace(disconnect=disconnect))
    asyncio.run(bot.bot(runner_args))
    assert disconnected == [True]
    assert f"Not starting session s1: no {missing} in .env or the request" in logs.text
    assert runner_args.body == {}
    assert_no_key(logs.text)


# ---- the /start body ------------------------------------------------------------------------------

def test_start_body_keys_reach_the_builder_and_leave_the_body(server_keys):
    agent = json.loads(CLINIC.read_text(encoding="utf-8"))
    body = {"agent": agent, "api_keys": {name: f"  {key}\n" for name, key in BROWSER.items()}}
    keys = api_keys.take_start_keys(body)
    assert keys == BROWSER
    assert body == {"agent": agent}
    builder = AgentBuilder(AgentConfig.from_dict(agent), api_keys=keys)
    assert bearer(builder.tool_context.model_client) == f"Bearer {BROWSER['CMD_API_KEY']}"


@pytest.mark.parametrize("body, keys", [
    ({"cmd_api_key": BROWSER["CMD_API_KEY"]}, {"CMD_API_KEY": BROWSER["CMD_API_KEY"]}),
    ({"api_keys": {"OPENAI_API_KEY": "a-key"}, "cmd_api_key": "legacy"}, {"OPENAI_API_KEY": "a-key", "CMD_API_KEY": "legacy"}),
    ({"api_keys": {"CMD_API_KEY": "new"}, "cmd_api_key": "legacy"}, {"CMD_API_KEY": "new"}),
    ({"api_keys": {"PATH": "x", "openai": "y", "OPENAI_API_KEY": 42}}, {}),
    ({"api_keys": "sk-not-a-dict"}, {}),
    ({"api_keys": {"OPENAI_API_KEY": "  "}, "cmd_api_key": ""}, {}),
])
def test_start_body_fields(body, keys):
    assert api_keys.take_start_keys(body) == keys
    assert "api_keys" not in body and "cmd_api_key" not in body


@pytest.mark.parametrize("body", [{}, {"cmd_api_key": "   "}, {"cmd_api_key": 42}, None, []])
def test_start_body_without_a_usable_key_has_no_call_keys(body):
    assert api_keys.take_start_keys(body) == {}


# ---- /api/grade: X-CMD-API-Key over the env -------------------------------------------------------

def grade_client(tmp_path, handler) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(tmp_path / "agents", jev_transport=httpx.MockTransport(handler)))
    return TestClient(app)


@pytest.mark.parametrize("header, sent", [(BROWSER["CMD_API_KEY"], BROWSER["CMD_API_KEY"]),
                                          ("  ", SERVER["CMD_API_KEY"]), (None, SERVER["CMD_API_KEY"])])
def test_grade_header_overrides_the_server_key(tmp_path, server_keys, header, sent):
    seen = []

    def handler(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(401)

    headers = {api_keys.JEV_HEADER: header} if header is not None else {}
    res = grade_client(tmp_path, handler).post("/api/grade", json=GRADE_BODY, headers=headers)
    assert res.json() == {"ok": False, "reason": "JEV rejected the API key"}
    assert seen == [f"Bearer {sent}"]


def test_grade_with_only_a_browser_key(tmp_path, no_server_keys):
    res = grade_client(tmp_path, lambda r: httpx.Response(401)).post("/api/grade", json=GRADE_BODY)
    assert res.json() == {"ok": False, "reason": "JEV not configured"}
    res = grade_client(tmp_path, lambda r: httpx.Response(401)).post(
        "/api/grade", json=GRADE_BODY, headers={api_keys.JEV_HEADER: BROWSER["CMD_API_KEY"]})
    assert res.json() == {"ok": False, "reason": "JEV rejected the API key"}


# ---- /api/keys/status -----------------------------------------------------------------------------

def keys_client(handler=None) -> TestClient:
    app = FastAPI()
    app.include_router(api_keys.create_keys_router(httpx.MockTransport(handler) if handler else None))
    return TestClient(app)


def test_status_says_only_whether_the_server_has_each_key(monkeypatch, server_keys):
    res = keys_client().get("/api/keys/status")
    assert res.json() == {"openai": {"server_key": True}, "elevenlabs": {"server_key": True}, "jev": {"server_key": True}}
    assert_no_key(res.text)
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    monkeypatch.setenv("CMD_API_KEY", "  ")
    assert keys_client().get("/api/keys/status").json() == {
        "openai": {"server_key": True}, "elevenlabs": {"server_key": False}, "jev": {"server_key": False}}


# ---- /api/keys/test, per provider, against fake transports ----------------------------------------

JEV_OK = {"model": "typesafe/jev", "answers": {"check": {"type": "choice", "choice": "yes", "confidence": 0.9,
                                                         "probabilities": {"yes": 0.9, "no": 0.1}}},
          "usage": {"input_tokens": 40}}
OPENAI_OK = {"object": "list", "data": [{"id": "gpt-4o", "object": "model"}]}
ELEVENLABS_USER = {"subscription": {"tier": "creator"}, "is_new_user": False}
ELEVENLABS_MODELS = [{"model_id": "eleven_flash_v2_5"}]
MISSING_PERMISSION = {"detail": {"status": "missing_permissions",
                                 "message": "The API key you used is missing the permission user_read to execute this operation."}}
INVALID_ELEVENLABS = {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}


def try_key(client: TestClient, provider: str | None, key: str = "", header: str = api_keys.HEADER):
    params = {"provider": provider} if provider else {}
    return client.post("/api/keys/test", params=params, headers={header: key or BROWSER[PROVIDER_ENV[provider or "jev"]]})


def recorder(respond):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return respond(request)

    return seen, handler


def test_openai_test_lists_models_with_the_key():
    seen, handler = recorder(lambda r: httpx.Response(200, json=OPENAI_OK))
    res = try_key(keys_client(handler), "openai")
    out = res.json()
    assert res.status_code == 200 and set(out) == {"ok", "ms"} and out["ok"] is True and isinstance(out["ms"], int)
    [request] = seen
    assert (request.method, str(request.url)) == ("GET", "https://api.openai.com/v1/models")
    assert request.headers["Authorization"] == f"Bearer {BROWSER['OPENAI_API_KEY']}"


def test_elevenlabs_test_reads_the_account_with_the_key():
    seen, handler = recorder(lambda r: httpx.Response(200, json=ELEVENLABS_USER))
    out = try_key(keys_client(handler), "elevenlabs").json()
    assert set(out) == {"ok", "ms"} and out["ok"] is True
    [request] = seen
    assert (request.method, str(request.url)) == ("GET", "https://api.elevenlabs.io/v1/user")
    assert request.headers["xi-api-key"] == BROWSER["ELEVENLABS_API_KEY"]
    assert "authorization" not in request.headers


def test_a_restricted_elevenlabs_key_is_valid_with_limited_permissions():
    def respond(request):
        if request.url.path == "/v1/user":
            return httpx.Response(401, json=MISSING_PERMISSION)
        return httpx.Response(200, json=ELEVENLABS_MODELS)

    seen, handler = recorder(respond)
    out = try_key(keys_client(handler), "elevenlabs").json()
    assert {k: v for k, v in out.items() if k != "ms"} == {"ok": True, "limited": True}
    assert [r.url.path for r in seen] == ["/v1/user", "/v1/models"]
    assert {r.headers["xi-api-key"] for r in seen} == {BROWSER["ELEVENLABS_API_KEY"]}


def test_an_elevenlabs_key_restricted_everywhere_is_still_not_rejected():
    seen, handler = recorder(lambda r: httpx.Response(401, json=MISSING_PERMISSION))
    out = try_key(keys_client(handler), "elevenlabs").json()
    assert {k: v for k, v in out.items() if k != "ms"} == {"ok": True, "limited": True}
    assert [r.url.path for r in seen] == ["/v1/user", "/v1/models"]


def test_jev_test_sends_one_two_option_choice():
    seen, handler = recorder(lambda r: httpx.Response(200, json=JEV_OK))
    out = try_key(keys_client(handler), "jev").json()
    assert set(out) == {"ok", "ms"} and out["ok"] is True
    [request] = seen
    assert (request.method, str(request.url)) == ("POST", JEV_URL)
    assert request.headers["Authorization"] == f"Bearer {BROWSER['CMD_API_KEY']}"
    assert [q["criteria"] for q in json.loads(request.content)["questions"].values()] == [{"yes": "Yes", "no": "No"}]


def test_the_old_jev_header_and_no_provider_still_test_jev():
    seen, handler = recorder(lambda r: httpx.Response(200, json=JEV_OK))
    out = try_key(keys_client(handler), None, header=api_keys.JEV_HEADER).json()
    assert out["ok"] is True
    assert [str(r.url) for r in seen] == [JEV_URL]


def network_error(request):
    raise httpx.ConnectError("down", request=request)


def timeout(request):
    raise httpx.ReadTimeout("slow", request=request)


@pytest.mark.parametrize("provider", ["openai", "elevenlabs", "jev"])
@pytest.mark.parametrize("respond, error", [
    (lambda r: httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}}), "invalid key"),
    (lambda r: httpx.Response(401, json=INVALID_ELEVENLABS), "invalid key"),
    (lambda r: httpx.Response(403), "invalid key"),
    (timeout, "network"),
    (network_error, "network"),
    (lambda r: httpx.Response(500, text="oops"), "unexpected response"),
    (lambda r: httpx.Response(200, text="<html>not json</html>"), "unexpected response"),
])
def test_key_test_failures(provider, respond, error):
    res = try_key(keys_client(respond), provider)
    out = res.json()
    assert res.status_code == 200
    assert {k: v for k, v in out.items() if k != "ms"} == {"ok": False, "error": error}
    assert_no_key(res.text)


@pytest.mark.parametrize("provider", ["openai", "jev"])
def test_a_200_without_the_expected_answer_is_unexpected(provider):
    out = try_key(keys_client(lambda r: httpx.Response(200, json={"answers": {}})), provider).json()
    assert out["error"] == "unexpected response"


@pytest.mark.parametrize("provider", ["openai", "elevenlabs", "jev"])
def test_key_test_needs_a_key(provider):
    def handler(request):
        raise AssertionError("no request without a key")

    res = try_key(keys_client(handler), provider, key="  ")
    assert (res.status_code, res.json()) == (400, {"ok": False, "ms": 0, "error": "missing key"})


def test_key_test_needs_a_known_provider():
    def handler(request):
        raise AssertionError("no request for an unknown provider")

    res = keys_client(handler).post("/api/keys/test", params={"provider": "anthropic"}, headers={api_keys.HEADER: "k"})
    assert (res.status_code, res.json()) == (400, {"ok": False, "ms": 0, "error": "unknown provider"})


def test_key_test_allows_one_in_flight_per_provider():
    entered, release = threading.Event(), threading.Event()

    def handler(request):
        if request.url.host == "api.openai.com":
            entered.set()
            release.wait(5)
            return httpx.Response(200, json=OPENAI_OK)
        return httpx.Response(200, json=JEV_OK)

    client = keys_client(handler)
    first: dict = {}
    worker = threading.Thread(target=lambda: first.update(try_key(client, "openai").json()))
    worker.start()
    assert entered.wait(5)
    second = try_key(client, "openai")
    other = try_key(client, "jev")
    release.set()
    worker.join(5)
    assert (second.status_code, second.json()) == (429, {"ok": False, "ms": 0, "error": "busy"})
    assert other.json()["ok"] is True
    assert first["ok"] is True
    assert try_key(client, "openai").json()["ok"] is True


# ---- no key ever reaches a log line ---------------------------------------------------------------

def runner_app(runner) -> tuple[FastAPI, dict]:
    """The Pipecat dev runner's own /start route, which logs the whole request at DEBUG."""
    app, sessions = FastAPI(), {}
    runner._setup_unified_start_route(app, argparse.Namespace(transport=None, ice_servers=[]), sessions)
    return app, sessions


def test_runner_start_log_never_shows_a_key(logs, server_keys, runner):
    app, sessions = runner_app(runner)
    agent = json.loads(CLINIC.read_text(encoding="utf-8"))
    body = {"agent": agent, "api_keys": dict(BROWSER), "cmd_api_key": BROWSER["CMD_API_KEY"]}
    res = TestClient(app).post("/start", json={"transport": "webrtc", "body": body})
    session = sessions[res.json()["sessionId"]]

    assert "Received request: " in logs.text
    assert "'api_keys': [redacted]" in logs.text and "'cmd_api_key': '[redacted]'" in logs.text
    assert_no_key(logs.text)
    assert api_keys.take_start_keys(session) == BROWSER
    assert "api_keys" not in session and "cmd_api_key" not in session


@pytest.mark.parametrize("name", ENVS)
@pytest.mark.parametrize("line", [
    "Received request: {{'body': {{'api_keys': {{'{name}': '{key}'}}}}}}",
    "env {{'{name}': '{key}'}}",
    '{{"{name}": "{key}"}}',
    "{name}={key}",
    "headers: X-Api-Key: {key}",
    "headers {{'x-api-key': '{key}', 'accept': '*/*'}}",
    'headers {{"xi-api-key": "{key}"}}',
    "xi-api-key={key}",
    "Authorization: Bearer {key}",
    "{{'authorization': 'Bearer {key}'}}",
    "sent (b'Authorization', b'Bearer {key}')",
    'headers {{"X-CMD-API-Key": "{key}"}}',
    "cmd_api_key='{key}'",
])
def test_every_key_field_and_header_is_blanked(logs, name, line):
    key = BROWSER[name]
    logger.debug(line.format(name=name, key=key))
    assert key not in logs.text
    assert "[redacted]" in logs.text


def test_redaction_never_reveals_a_key_length(logs):
    for key in ("short1", "a-much-longer-key-0123456789abcdefghij"):
        logger.debug(f"{{'OPENAI_API_KEY': '{key}'}}")
    assert logs.messages[-2:] == ["{'OPENAI_API_KEY': '[redacted]'}"] * 2


def test_redaction_leaves_other_text_alone(logs):
    line = "Starting 'Clinic Scheduler' with 9 nodes; authorization header missing"
    logger.info(line)
    assert logs.messages[-1] == line


def test_redaction_outlives_the_runner_resetting_its_sinks(caplog):
    api_keys.install_log_redaction()
    logger.remove()  # what the runner's main() does before adding its own sink
    sink = logger.add(caplog.handler, format="{message}", level="DEBUG")
    try:
        logger.debug(f"Received request: {{'body': {{'api_keys': {{'OPENAI_API_KEY': '{BROWSER['OPENAI_API_KEY']}'}}}}}}")
        logger.debug(f'headers {{"X-CMD-API-Key": "{BROWSER["CMD_API_KEY"]}"}}')
    finally:
        logger.remove(sink)
        logger.add(sys.stderr)
        logger.configure(patcher=None)
    assert_no_key(caplog.text)
    assert caplog.text.count("[redacted]") == 2


def test_call_grade_and_key_tests_never_log_or_echo_a_key(logs, tmp_path, no_server_keys, monkeypatch):
    monkeypatch.setattr(JevClient, "_send", lambda self, body, timeout: httpx.Response(401))
    events: list[dict] = []

    async def on_event(event):
        events.append(event)

    agent = json.loads(CLINIC.read_text(encoding="utf-8"))
    keys = api_keys.take_start_keys({"agent": agent, "api_keys": dict(BROWSER)})
    ctx = AgentBuilder(AgentConfig.from_dict(agent), on_event=on_event, api_keys=keys).tool_context
    asyncio.run(on_event(resolver_mode_event(ctx)))
    asyncio.run(warm_up_model(ctx))
    grade = grade_client(tmp_path, lambda r: httpx.Response(401)).post(
        "/api/grade", json=GRADE_BODY, headers={api_keys.JEV_HEADER: BROWSER["CMD_API_KEY"]})
    client = keys_client(lambda r: httpx.Response(401, json=INVALID_ELEVENLABS))
    checks = [try_key(client, provider) for provider in PROVIDER_ENV]

    assert [(e["type"], e.get("ok")) for e in events] == [("resolver_mode", None), ("model_call", False)]
    assert grade.status_code == 503 and [c.json()["error"] for c in checks] == ["invalid key"] * 3
    assert "jev warm-up failed" in logs.text
    assert_no_key(logs.text, json.dumps(events), grade.text, *(c.text for c in checks))
