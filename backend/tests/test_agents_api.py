import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents_api import create_router


@pytest.fixture
def agents_dir(tmp_path):
    return tmp_path / "agents"


@pytest.fixture
def client(agents_dir):
    app = FastAPI()
    app.include_router(create_router(agents_dir))
    return TestClient(app)


def test_seeds_example_with_layout(client, agents_dir):
    assert [p.name for p in agents_dir.iterdir()] == ["prosper-scheduler.json"]
    agent = client.get("/api/agents/prosper-scheduler").json()
    assert agent["id"] == "prosper-scheduler"
    assert [n["ui"] for n in agent["nodes"]] == [
        {"x": 0, "y": 0},
        {"x": 420, "y": 0},
        {"x": 840, "y": 0},
        {"x": 1260, "y": 0},
    ]


def test_list_agents(client):
    listing = client.get("/api/agents").json()
    assert len(listing) == 1
    assert {k: v for k, v in listing[0].items() if k != "updated_at"} == {
        "id": "prosper-scheduler",
        "name": "Prosper Scheduler",
        "node_count": 4,
    }


def test_create_agent_unique_slug(client):
    first = client.post("/api/agents", json={"name": "My Agent!"})
    second = client.post("/api/agents", json={"name": "My Agent!"})
    assert first.status_code == 201
    assert first.json()["id"] == "my-agent"
    assert second.json()["id"] == "my-agent-2"
    assert first.json()["initial_node"] == "start"
    assert first.json()["nodes"][0]["ui"] == {"x": 0, "y": 0}
    assert client.get("/api/agents/my-agent-2").json()["name"] == "My Agent!"


def test_list_sorted_by_updated_desc(client, agents_dir):
    client.post("/api/agents", json={"name": "Old"})
    client.post("/api/agents", json={"name": "New"})
    os.utime(agents_dir / "old.json", (1_000_000_000, 1_000_000_000))
    os.utime(agents_dir / "new.json", (2_000_000_000, 2_000_000_000))
    os.utime(agents_dir / "prosper-scheduler.json", (1_500_000_000, 1_500_000_000))
    listing = client.get("/api/agents").json()
    assert [a["id"] for a in listing] == ["new", "prosper-scheduler", "old"]
    assert listing[0]["updated_at"] == "2033-05-18T03:33:20+00:00"


def test_put_round_trips_unknown_keys(client, agents_dir):
    agent = client.get("/api/agents/prosper-scheduler").json()
    agent["future_field"] = {"a": [1, 2]}
    agent["nodes"][0]["tools"] = ["lookup"]
    agent["nodes"][0]["edges"][0]["guard"] = "state.intent != null"
    response = client.put("/api/agents/prosper-scheduler", json=agent)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "agent": agent}
    assert client.get("/api/agents/prosper-scheduler").json() == agent
    on_disk = json.loads((agents_dir / "prosper-scheduler.json").read_text(encoding="utf-8"))
    assert on_disk == agent


def test_put_invalid_is_not_saved(client):
    original = client.get("/api/agents/prosper-scheduler").json()
    broken = json.loads(json.dumps(original))
    broken["nodes"][0]["edges"][0]["target"] = "nowhere"
    response = client.put("/api/agents/prosper-scheduler", json=broken)
    assert response.status_code == 422
    assert response.json() == {
        "ok": False,
        "errors": [
            {"path": "nodes[0].edges[0].target", "message": "Edge targets unknown node 'nowhere'."}
        ],
    }
    assert client.get("/api/agents/prosper-scheduler").json() == original


def test_put_mismatched_id_rejected(client):
    agent = client.get("/api/agents/prosper-scheduler").json()
    agent["id"] = "other"
    response = client.put("/api/agents/prosper-scheduler", json=agent)
    assert response.status_code == 422
    assert [e["path"] for e in response.json()["errors"]] == ["id"]


def test_validate_endpoint(client, example_agent):
    assert client.post("/api/agents/validate", json=example_agent).json() == {
        "ok": True,
        "errors": [],
    }
    example_agent["nodes"][1]["edges"][0]["function"] = "1bad"
    body = client.post("/api/agents/validate", json=example_agent).json()
    assert body["ok"] is False
    assert [e["path"] for e in body["errors"]] == ["nodes[1].edges[0].function"]


def test_delete(client):
    assert client.delete("/api/agents/prosper-scheduler").status_code == 204
    assert client.get("/api/agents/prosper-scheduler").status_code == 404
    assert client.delete("/api/agents/prosper-scheduler").status_code == 404


def test_get_missing_is_404(client):
    assert client.get("/api/agents/nope").status_code == 404


@pytest.mark.parametrize("bad_id", ["%2E%2E", "..%5C..%5Cbot", "Prosper", "a.b"])
def test_path_traversal_id_rejected(client, bad_id):
    assert client.get(f"/api/agents/{bad_id}").status_code == 400
    assert client.put(f"/api/agents/{bad_id}", json={}).status_code == 400
    assert client.delete(f"/api/agents/{bad_id}").status_code == 400


def test_voices_and_models(client):
    voices = client.get("/api/voices").json()
    assert [v["name"] for v in voices] == ["Rachel", "Adam", "Bella", "Antoni", "Domi", "Josh"]
    assert client.get("/api/models").json() == [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
    ]


def test_new_agent_is_valid_and_saves_unchanged(client):
    agent = client.post("/api/agents", json={"name": "Fresh"}).json()
    assert client.post("/api/agents/validate", json=agent).json() == {"ok": True, "errors": []}
    assert client.put(f"/api/agents/{agent['id']}", json=agent).status_code == 200
