import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def example_agent() -> dict:
    return json.loads((BACKEND_DIR / "example_flow.json").read_text(encoding="utf-8"))
