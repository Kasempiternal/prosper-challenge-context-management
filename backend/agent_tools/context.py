"""Per-call dependencies of the scheduling tools.

The catalog index is immutable and loaded once per process. Bookings (holds) are process-wide,
so two concurrent calls cannot book the same slot; the request (flow_manager.state["req"]) is
per call. JEV is used only when the agent enables it and CMD_API_KEY is set; otherwise the
resolver runs with its no-op disambiguator.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from scheduling.availability import Availability, Hold, MockAvailability, Slot
from scheduling.catalog_index import CatalogIndex
from scheduling.decision import Verdict
from scheduling.resolver import NoDisambiguator

try:
    from scheduling.jev import JevClient, JevProviderChooser, JevTypeDisambiguator, offered_type_criteria
except ImportError:  # the JEV module is optional; without it the resolver never consults a model
    JevClient = None

BACKEND_DIR = Path(__file__).resolve().parent.parent

EventCallback = Callable[[dict], Awaitable[None]]


_indexes: dict[Path, CatalogIndex] = {}
_index_locks: dict[Path, threading.Lock] = {}
_index_locks_guard = threading.Lock()


def load_index(catalog_path: Path) -> CatalogIndex:
    """Built once per process per catalog path. The per-path lock makes a call that arrives while
    the startup preload is still building the same catalog wait for it instead of building twice."""
    index = _indexes.get(catalog_path)
    if index is not None:
        return index
    with _index_locks_guard:
        lock = _index_locks.setdefault(catalog_path, threading.Lock())
    with lock:
        if catalog_path not in _indexes:
            started = time.perf_counter()
            index = CatalogIndex.load(catalog_path)
            logger.info(f"Loaded catalog {catalog_path.parent.name}/{catalog_path.name}: {len(index.bookable)} "
                        f"bookable rows in {(time.perf_counter() - started) * 1000:.0f} ms")
            _indexes[catalog_path] = index
        return _indexes[catalog_path]


def resolve_catalog_path(catalog: str) -> Path:
    return (BACKEND_DIR / catalog).resolve()


def preload_catalogs(agents_dir: Path) -> threading.Thread:
    """Build, in a background thread, the index of every catalog an agent in agents_dir names, so the
    first call on a large catalog does not wait for its build."""
    catalogs: set[str] = set()
    for path in sorted(Path(agents_dir).glob("*.json")):
        try:
            catalog = json.loads(path.read_text(encoding="utf-8")).get("catalog")
        except (OSError, ValueError, AttributeError) as e:
            logger.warning(f"Catalog preload skips {path.name}: {e}")
            continue
        if isinstance(catalog, str):
            catalogs.add(catalog)

    def run() -> None:
        for catalog in sorted(catalogs):
            try:
                load_index(resolve_catalog_path(catalog))
            except (OSError, ValueError) as e:
                logger.warning(f"Catalog preload failed for {catalog}: {e}")

    thread = threading.Thread(target=run, name="catalog-preload", daemon=True)
    thread.start()
    return thread


@lru_cache(maxsize=None)
def shared_availability(catalog_path: Path) -> tuple[MockAvailability, threading.Lock]:
    """One booking ledger per catalog for the whole process. Tests clear it with cache_clear()."""
    return MockAvailability(load_index(catalog_path)), threading.Lock()


class CallAvailability:
    """One call's view of the shared ledger. The resolver reads it from a worker thread while
    another call may be holding a slot, hence the lock. A slot this call already holds stays
    bookable for it (book_offer is idempotent); one held by another call is taken."""

    def __init__(self, shared: MockAvailability, lock: threading.Lock):
        self._shared, self._lock = shared, lock
        self._mine: set[str] = set()
        self.now = shared.now

    def find(self, rows, time_pref, limit: int = 3) -> list[Slot]:
        rows = list(rows)
        with self._lock:
            return self._shared.find(rows, time_pref, limit)

    def is_open(self, slot: Slot) -> bool:
        with self._lock:
            return self._shared.is_open(slot)

    def hold(self, slot: Slot) -> Hold:
        with self._lock:
            if slot.id not in self._mine and not self._shared.is_open(slot):
                return Hold(ok=False, slot=slot)
            hold = self._shared.hold(slot)
            if hold.ok:
                self._mine.add(slot.id)
            return hold

    def release(self, slot_id: str) -> None:
        with self._lock:
            if slot_id in self._mine:
                self._mine.discard(slot_id)
                self._shared.release(slot_id)


class RecordingDisambiguator:
    """Wraps the resolver's model hooks and keeps the verdicts of the current turn, so the
    resolver_decision event can show whether JEV was consulted and how sure it was."""

    def __init__(self, types: Any, providers: Any):
        self._types, self._providers = types, providers
        self.verdicts: list[Verdict] = []
        self.consulting = False  # a model hook is running right now (read from the event loop)

    def pick_type(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> Verdict:
        return self._record(self._types.pick_type, phrase, hint, candidate_ids)

    def pick_provider(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict:
        return self._record(self._providers.pick_provider, phrase, type_id, candidate_ids)

    def _record(self, hook, *args) -> Verdict:
        self.consulting = True
        try:
            verdict = hook(*args)
        finally:
            self.consulting = False
        self.verdicts.append(verdict)
        return verdict


@dataclass
class ToolContext:
    index: CatalogIndex
    availability: Availability
    speak_direct: bool = True
    disambiguator: RecordingDisambiguator = field(
        default_factory=lambda: RecordingDisambiguator(NoDisambiguator(), NoDisambiguator()))
    jev_client: Optional[Any] = None
    on_event: Optional[EventCallback] = None

    async def emit(self, event: dict) -> None:
        if self.on_event:
            await self.on_event(event)


def make_context(catalog: str, *, speak_direct: bool, jev_enabled: bool, jev_timeout_ms: int,
                 on_event: Optional[EventCallback] = None) -> ToolContext:
    catalog_path = resolve_catalog_path(catalog)
    index = load_index(catalog_path)
    api_key = os.environ.get("CMD_API_KEY")
    client = None
    if jev_enabled and api_key and JevClient is not None:
        # Per call, not shared: the client carries this call's per-turn budget (begin_turn).
        timeout_s = jev_timeout_ms / 1000
        client = JevClient(api_key, mode="auto", timeout_s=timeout_s, retries=0, turn_budget_s=timeout_s)
        dis = RecordingDisambiguator(JevTypeDisambiguator(index, client), JevProviderChooser(index, client))
    else:
        dis = RecordingDisambiguator(NoDisambiguator(), NoDisambiguator())
    return ToolContext(index=index, availability=CallAvailability(*shared_availability(catalog_path)),
                       speak_direct=speak_direct,
                       disambiguator=dis, jev_client=client, on_event=on_event)


async def warm_up_jev(ctx: ToolContext) -> None:
    """Pay JEV's cold start while the caller is still listening to the greeting."""
    if ctx.jev_client is None:
        return
    started = time.perf_counter()
    call = await asyncio.to_thread(ctx.jev_client.warm_up, offered_type_criteria(ctx.index))
    ms = (time.perf_counter() - started) * 1000
    if call is None:
        logger.warning(f"JEV warm-up failed after {ms:.0f} ms")
    else:
        logger.info(f"JEV warm-up {call.source} in {ms:.0f} ms (server {call.latency_ms:.0f} ms)")
