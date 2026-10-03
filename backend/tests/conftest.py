import importlib
import json
import sys
from pathlib import Path

import dotenv
import pytest
from loguru import logger

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def example_agent() -> dict:
    return json.loads((BACKEND_DIR / "example_flow.json").read_text(encoding="utf-8"))


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
