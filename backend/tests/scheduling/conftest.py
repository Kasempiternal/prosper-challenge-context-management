from pathlib import Path

import pytest

from scheduling.availability import MockAvailability
from scheduling.catalog_index import CatalogIndex
from scheduling.request import Request, Update, merge
from scheduling.resolver import resolve

CATALOG = Path(__file__).resolve().parents[2] / "data" / "catalog.json"


@pytest.fixture(scope="session")
def index() -> CatalogIndex:
    return CatalogIndex.load(CATALOG)


@pytest.fixture
def availability(index) -> MockAvailability:
    return MockAvailability(index)


@pytest.fixture
def converse(index, availability):
    """Run tool-call updates through merge + resolve, returning the last Plan."""

    def run(*updates: dict):
        req, plan = Request(), None
        for u in updates:
            req = merge(req, Update.from_args(u))
            plan = resolve(index, req, availability)
            req = plan.req
        return plan

    return run
