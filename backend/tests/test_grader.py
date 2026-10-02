import json
import os

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import grader
from agents_api import create_router
from scheduling.jev import JevClient

TRANSCRIPT = [
    {"role": "user", "text": "Hi, I'm a new patient with a referral. I need a cardiology consultation with Dr. Chen, soonest you have."},
    {"role": "bot", "text": "For a cardiology consultation, Dr. Emily Chen has tomorrow at 8 at Downtown, Friday at 8 at Richmond, or Monday at 8 at Downtown. Which works best?"},
    {"role": "user", "text": "The first one works."},
    {"role": "bot", "text": "Okay, a cardiology consultation with Dr. Emily Chen, tomorrow at 8 at Downtown. Shall I book it?"},
    {"role": "user", "text": "Yes, please."},
    {"role": "bot", "text": "You're booked with Dr. Emily Chen tomorrow at 8 at Downtown. Your confirmation is H-CA2C9F0E."},
]
DECISIONS = [
    {"status": "offer", "say": TRANSCRIPT[1]["text"],
     "offers": ["1 Thu Oct 8 8am Downtown Dr. Emily Chen", "2 Fri Oct 9 8am Richmond Dr. Emily Chen"]},
    {"status": "confirm", "say": TRANSCRIPT[3]["text"]},
]
BODY = {"transcript": TRANSCRIPT, "decisions": DECISIONS, "collected": {"visit": "Cardiology Consultation"}}

# Shape recorded from the live API (2026-10-02).
JEV_RESPONSE = {
    "model": "typesafe/jev",
    "answers": {
        "booked_correctly": {"type": "noul", "noul": 0.93},
        "unnecessary_questions": {"type": "noul", "noul": 0.08},
        "unsupported_claims": {"type": "noul", "noul": 0.04},
        "caller_effort": {"type": "score", "score": 0.02, "confidence": 0.9,
                          "legend": {str(i): lvl for i, lvl in enumerate(grader.EFFORT_LEVELS)},
                          "probabilities": {"0": 0.9, "1": 0.1, "2": 0, "3": 0, "4": 0}},
        "outcome": {"type": "choice", "choice": "booked", "confidence": 0.97,
                    "probabilities": {"booked": 0.97, "refused_correctly": 0.01, "handed_off": 0.0,
                                      "abandoned": 0.0, "unclear": 0.02}},
    },
    "usage": {"input_tokens": 1000, "output_tokens": 120},
}


def make_client(tmp_path, handler, key="test-key"):
    jev = JevClient(key, mode="live", timeout_s=grader.TIMEOUT_S, retries=grader.RETRIES,
                    turn_budget_s=None, transport=httpx.MockTransport(handler))
    app = FastAPI()
    app.include_router(create_router(tmp_path / "agents", jev=jev))
    return TestClient(app)


def test_grade_success_sends_one_request_with_five_questions(tmp_path):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=JEV_RESPONSE)

    res = make_client(tmp_path, handler).post("/api/grade", json={**BODY, "agent_id": "prosper-scheduler"})

    assert res.status_code == 200
    out = res.json()
    assert {k: v for k, v in out.items() if k != "ms"} == {
        "ok": True,
        "scores": {
            "booked_correctly": {"p": 0.93},
            "unnecessary_questions": {"p": 0.08},
            "unsupported_claims": {"p": 0.04},
            "caller_effort": {"level": 1.1, "probabilities": {"1": 0.9, "2": 0.1, "3": 0.0, "4": 0.0, "5": 0.0},
                              "confidence": 0.9},
            "outcome": {"choice": "booked", "confidence": 0.97,
                        "probabilities": JEV_RESPONSE["answers"]["outcome"]["probabilities"]},
        },
        "usage": {"input_tokens": 1000, "output_tokens": 120, "usd": 0.00004},
    }
    assert len(seen) == 1
    sent = seen[0]
    assert sent["model"] == "typesafe/jev"
    assert {k: q["type"] for k, q in sent["questions"].items()} == {
        "booked_correctly": "noul", "unnecessary_questions": "noul", "unsupported_claims": "noul",
        "caller_effort": "score", "outcome": "choice",
    }
    assert sent["questions"]["caller_effort"]["criteria"] == grader.EFFORT_LEVELS
    assert list(sent["questions"]["outcome"]["criteria"]) == [
        "booked", "refused_correctly", "handed_off", "abandoned", "unclear"]
    assert "AGENT: Prosper Scheduler." in sent["state"]
    assert "Caller: The first one works." in sent["state"]
    assert "offers: 1 Thu Oct 8 8am Downtown Dr. Emily Chen | 2 Fri Oct 9 8am Richmond Dr. Emily Chen" in sent["state"]
    assert 'COLLECTED DATA: {"visit":"Cardiology Consultation"}' in sent["state"]


def test_missing_key_is_503_without_network(tmp_path):
    def handler(request):
        raise AssertionError("must not call JEV without a key")

    res = make_client(tmp_path, handler, key=None).post("/api/grade", json=BODY)
    assert res.status_code == 503
    assert res.json() == {"ok": False, "reason": "JEV not configured"}


def test_timeout_retries_once_then_504(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    res = make_client(tmp_path, handler).post("/api/grade", json=BODY)
    assert res.status_code == 504
    assert res.json() == {"ok": False, "reason": "JEV timed out after 8 s"}
    assert len(calls) == 2


def test_malformed_jev_answer_is_502(tmp_path):
    res = make_client(tmp_path, lambda r: httpx.Response(200, json={"answers": {"outcome": {}}})).post("/api/grade", json=BODY)
    assert res.status_code == 502
    assert res.json()["ok"] is False


@pytest.mark.parametrize("body, reason", [
    ({}, "transcript: Field required"),
    ({"transcript": []}, "transcript: List should have at least 1 item after validation, not 0"),
    ({"transcript": [{"role": "agent", "text": "hi"}]}, "transcript.0.role: Input should be 'user' or 'bot'"),
    ({"transcript": [{"role": "user", "text": "  "}]}, "transcript: every turn is empty"),
    ({"transcript": "hello"}, "transcript: Input should be a valid list"),
    ([1, 2], "body: Input should be a valid dictionary or instance of GradeRequest"),
    ({**BODY, "agent_id": "nope"}, "agent_id: agent 'nope' not found"),
    ({**BODY, "agent_id": "../etc"}, "agent_id: agent '../etc' not found"),
])
def test_bad_input_is_422_with_message(tmp_path, body, reason):
    def handler(request):
        raise AssertionError("must not call JEV on bad input")

    res = make_client(tmp_path, handler).post("/api/grade", json=body)
    assert res.status_code == 422
    assert res.json() == {"ok": False, "reason": reason}


def test_non_json_body_is_422(tmp_path):
    res = make_client(tmp_path, lambda r: httpx.Response(500)).post(
        "/api/grade", content=b"not json", headers={"Content-Type": "application/json"})
    assert res.status_code == 422


def test_transcript_keeps_the_latest_turns_within_budget():
    long = [{"role": "user", "text": "x" * 10_000}, {"role": "bot", "text": "y" * 10_000},
            {"role": "user", "text": "z" * 10_000}, {"role": "bot", "text": "last"}]
    state = grader.build_state(grader.parse_request({"transcript": long}))
    assert "[earlier turns omitted]" in state
    assert "x" * 100 not in state
    assert "Agent: last" in state
    assert len(state) < grader.TRANSCRIPT_TOKEN_BUDGET * grader.CHARS_PER_TOKEN + 500


@pytest.mark.skipif(os.environ.get("RUN_JEV_LIVE") != "1", reason="live JEV call; set RUN_JEV_LIVE=1")
def test_live_smoke():
    jev = grader.client_from_env()
    assert jev.api_key, "CMD_API_KEY missing from backend/.env"
    out = grader.grade(jev, grader.parse_request(BODY))
    print(json.dumps(out, indent=1))
    assert out["ok"] is True
    assert out["scores"]["outcome"]["choice"] == "booked"
    assert out["scores"]["booked_correctly"]["p"] > 0.5
    assert 1 <= out["scores"]["caller_effort"]["level"] <= 5
