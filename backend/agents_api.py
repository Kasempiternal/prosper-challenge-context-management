#
# Agents REST API — CRUD + validation for agent JSON files, mounted on the
# Pipecat runner's FastAPI app by bot.py.
#
# Storage is one file per agent: <agents_dir>/<id>.json. The stored document is
# exactly what the client PUT (after validation passes), so UI-owned keys such
# as `id`, node `ui`, and future fields round-trip untouched.
#

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Response
from fastapi.responses import JSONResponse
from loguru import logger

import grader
from agent_builder import validate_agent
from agent_builder.schema import DEFAULT_MODEL, DEFAULT_VOICE_ID
from scheduling.jev import JevClient

BACKEND_DIR = Path(__file__).parent
DEFAULT_AGENTS_DIR = BACKEND_DIR / "agents"
EXAMPLE_FLOW = BACKEND_DIR / "example_flow.json"
SEED_ID = "prosper-scheduler"
UI_X_STEP = 420

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

VOICES = [
    {"id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel", "description": "Calm, warm female voice"},
    {"id": "pNInz6obpgDQGcFmaJgB", "name": "Adam", "description": "Deep, clear male voice"},
    {"id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella", "description": "Soft, friendly female voice"},
    {"id": "ErXwAiVsAtByTTF9i0hv", "name": "Antoni", "description": "Well-rounded male voice"},
    {"id": "AZnzlk1XvdvUeBnXmlld", "name": "Domi", "description": "Strong, confident female voice"},
    {"id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh", "description": "Young, energetic male voice"},
]
MODELS = ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"]

DEFAULT_PERSONA = (
    "You are a friendly voice assistant. Your responses are spoken aloud, so avoid "
    "emojis, lists, or anything that can't be read out. Keep replies short."
)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48].strip("-")
    return slug or "agent"


def _write_json_atomic(path: Path, data: Any) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def seed_agents_dir(agents_dir: Path) -> None:
    """Create the agents dir and, if it has no agents, seed it with example_flow.json."""
    agents_dir.mkdir(parents=True, exist_ok=True)
    if any(agents_dir.glob("*.json")):
        return
    agent = json.loads(EXAMPLE_FLOW.read_text(encoding="utf-8"))
    agent = {"id": SEED_ID, **agent}
    for i, node in enumerate(agent["nodes"]):
        node["ui"] = {"x": i * UI_X_STEP, "y": 0}
    _write_json_atomic(agents_dir / f"{SEED_ID}.json", agent)
    logger.info(f"Seeded {agents_dir / f'{SEED_ID}.json'}")


def agents_dir_from_env() -> Path:
    return Path(os.environ.get("AGENTS_DIR") or DEFAULT_AGENTS_DIR)


COUNT_KEYS = ("locations", "providers", "appointment_types")


def _catalog_summary(path: Path, backend_dir: Path) -> dict:
    """Counts and labels come from the catalog.meta.json sidecar when present, so listing never parses
    a large catalog; without one they are counted from the catalog JSON and naive_tokens is unknown."""
    rel = path.relative_to(backend_dir).as_posix()
    meta_path = path.with_name("catalog.meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    counts = meta.get("counts")
    if not isinstance(counts, dict):
        raw = json.loads(path.read_text(encoding="utf-8"))
        counts = {k: len(raw.get(k) or []) for k in (*COUNT_KEYS, "metros")}
    naive = meta.get("naive_tokens")
    return {
        "path": rel,
        "label": meta.get("label") if isinstance(meta.get("label"), str) else rel,
        **{k: int(counts.get(k, 0)) for k in COUNT_KEYS},
        "metros": int(counts.get("metros") or 1),  # a catalog without metros is one implicit metro
        "naive_tokens": naive if isinstance(naive, int) else None,
    }


def catalog_lister(backend_dir: Path):
    """Returns list_catalogs(): every <backend>/data/**/catalog.json, shallowest path first (the SF
    catalog), each summary cached until its catalog or sidecar mtime changes."""
    cache: dict[Path, tuple[tuple[float, float], dict]] = {}

    def mtimes(path: Path) -> tuple[float, float]:
        meta = path.with_name("catalog.meta.json")
        return path.stat().st_mtime, meta.stat().st_mtime if meta.is_file() else 0.0

    def list_catalogs() -> list[dict]:
        paths = sorted((backend_dir / "data").glob("**/catalog.json"),
                       key=lambda p: (len(p.relative_to(backend_dir).parts), p.as_posix()))
        out = []
        for path in paths:
            try:
                stamp = mtimes(path)
                hit = cache.get(path)
                if hit is None or hit[0] != stamp:
                    cache[path] = hit = (stamp, _catalog_summary(path, backend_dir))
            except (OSError, ValueError, TypeError) as e:
                logger.warning(f"Skipping unreadable catalog {path}: {e}")
                continue
            out.append(hit[1])
        return out

    return list_catalogs


def create_router(agents_dir: Optional[Path] = None, jev: Optional[JevClient] = None,
                  backend_dir: Path = BACKEND_DIR) -> APIRouter:
    agents_dir = Path(agents_dir) if agents_dir else agents_dir_from_env()
    seed_agents_dir(agents_dir)
    router = APIRouter(prefix="/api")
    list_catalogs = catalog_lister(Path(backend_dir))

    def agent_path(agent_id: str) -> Path:
        if not ID_RE.match(agent_id):
            raise HTTPException(status_code=400, detail="Invalid agent id.")
        return agents_dir / f"{agent_id}.json"

    def read_agent(agent_id: str) -> dict:
        path = agent_path(agent_id)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    @router.get("/agents")
    def list_agents() -> list[dict]:
        summaries = []
        for path in agents_dir.glob("*.json"):
            try:
                agent = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Skipping unreadable agent file {path}: {e}")
                continue
            mtime = path.stat().st_mtime
            summaries.append(
                {
                    "id": path.stem,
                    "name": agent.get("name", path.stem),
                    "node_count": len(agent.get("nodes", [])),
                    "updated_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                    "_mtime": mtime,
                }
            )
        summaries.sort(key=lambda s: s.pop("_mtime"), reverse=True)
        return summaries

    @router.get("/agents/{agent_id}")
    def get_agent(agent_id: str) -> dict:
        return read_agent(agent_id)

    @router.post("/agents", status_code=201)
    def create_agent(payload: dict = Body(...)) -> dict:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=422, detail="'name' is required.")
        base = slugify(name)
        agent_id, n = base, 2
        while (agents_dir / f"{agent_id}.json").exists():
            agent_id, n = f"{base}-{n}", n + 1
        agent = {
            "id": agent_id,
            "name": name.strip(),
            "persona": DEFAULT_PERSONA,
            "voice_id": DEFAULT_VOICE_ID,
            "model": DEFAULT_MODEL,
            "initial_node": "start",
            "nodes": [
                {
                    "name": "start",
                    "task_messages": [
                        {
                            "role": "developer",
                            "content": "Greet the caller warmly and ask how you can help.",
                        }
                    ],
                    "edges": [
                        {
                            "function": "wrap_up",
                            "description": "The caller's request has been handled.",
                            "target": "goodbye",
                            "properties": {},
                            "required": [],
                        }
                    ],
                    "end": False,
                    "ui": {"x": 0, "y": 0},
                },
                {
                    "name": "goodbye",
                    "task_messages": [
                        {"role": "developer", "content": "Thank the caller and say goodbye."}
                    ],
                    "edges": [],
                    "end": True,
                    "ui": {"x": 420, "y": 0},
                },
            ],
        }
        _write_json_atomic(agents_dir / f"{agent_id}.json", agent)
        return agent

    @router.put("/agents/{agent_id}")
    def save_agent(agent_id: str, agent: Any = Body(...)):
        path = agent_path(agent_id)
        errors = validate_agent(agent)
        if isinstance(agent, dict) and agent.get("id", agent_id) != agent_id:
            errors.append({"path": "id", "message": f"Body id does not match URL id '{agent_id}'."})
        if errors:
            return JSONResponse(status_code=422, content={"ok": False, "errors": errors})
        agent = {**agent, "id": agent_id}
        _write_json_atomic(path, agent)
        return {"ok": True, "agent": agent}

    @router.delete("/agents/{agent_id}", status_code=204)
    def delete_agent(agent_id: str) -> Response:
        path = agent_path(agent_id)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
        path.unlink()
        return Response(status_code=204)

    @router.post("/agents/validate")
    def validate(agent: Any = Body(...)) -> dict:
        errors = validate_agent(agent)
        return {"ok": not errors, "errors": errors}

    @router.post("/grade")
    def grade(payload: Any = Body(...)):
        nonlocal jev
        try:
            req = grader.parse_request(payload)
            agent = req.agent
            if agent is None and req.agent_id:
                if not ID_RE.match(req.agent_id) or not agent_path(req.agent_id).is_file():
                    raise grader.GradeError(422, f"agent_id: agent '{req.agent_id}' not found")
                agent = read_agent(req.agent_id)
            jev = jev or grader.client_from_env()
            return grader.grade(jev, req, agent)
        except grader.GradeError as e:
            return JSONResponse(status_code=e.status, content={"ok": False, "reason": e.reason})

    @router.get("/catalogs")
    def catalogs() -> list[dict]:
        return list_catalogs()

    @router.get("/voices")
    def voices() -> list[dict]:
        return VOICES

    @router.get("/models")
    def models() -> list[str]:
        return MODELS

    return router
