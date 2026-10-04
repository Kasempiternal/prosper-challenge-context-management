"""Per-call dependencies of the scheduling tools.

The catalog index is immutable and loaded once per process. Bookings (holds) are process-wide,
so two concurrent calls cannot book the same slot; the request (flow_manager.state["req"]) is
per call. The agent's resolver.chooser picks the model behind the resolver's hooks
(scheduling.choosers): JEV (needs CMD_API_KEY), OpenAI (OPENAI_API_KEY), local embeddings
(fastembed installed) or none. A chooser whose key or package is missing runs as none. A call's
api_keys (by env var name) override the environment for that call's client only.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional

from loguru import logger

from scheduling.availability import Availability, Hold, MockAvailability, Slot
from scheduling.catalog_index import CatalogIndex
from scheduling.choosers import CHOOSERS, EMBEDDINGS_AVAILABLE, NO_KEYS
from scheduling.decision import Gate, Verdict
from scheduling.embed_chooser import shared_embedder, warm_catalog
from scheduling.model_client import REQUEST_TIMEOUT_S, TURN_BUDGET_MS
from scheduling.resolver import NoDisambiguator

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


def preload_catalogs(agents_dir: Path, embeddings: bool = EMBEDDINGS_AVAILABLE) -> threading.Thread:
    """Build, in a background thread, the index of every catalog an agent in agents_dir names, so the
    first call on a large catalog does not wait for its build. With embeddings, also load the
    embedding model and each catalog's type and site vectors (from the disk cache after the first
    boot): any agent can be switched to the embeddings chooser right before a call."""
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
        indexes = []
        for catalog in sorted(catalogs):
            try:
                indexes.append(load_index(resolve_catalog_path(catalog)))
            except (OSError, ValueError) as e:
                logger.warning(f"Catalog preload failed for {catalog}: {e}")
        if embeddings:
            for index in indexes:
                started = time.perf_counter()
                try:
                    warm_catalog(shared_embedder(), index)
                except Exception as e:  # noqa: BLE001 - the chooser then fails per call and asks instead
                    logger.warning(f"Embedding preload failed: {e!r}")
                    return
                logger.info(f"Warmed {len(index.types)} type and {len(index.locations)} site vectors in "
                            f"{(time.perf_counter() - started) * 1000:.0f} ms")

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
    bookable for it (a retried booking converges); one held by another call is taken."""

    def __init__(self, shared: MockAvailability, lock: threading.Lock):
        self._shared, self._lock = shared, lock
        self._mine: set[str] = set()
        self.now = shared.now

    def find(self, rows, time_pref, limit: int = 3, exclude=()) -> list[Slot]:
        rows = list(rows)
        with self._lock:
            return self._shared.find(rows, time_pref, limit, exclude)

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


class ModelHooks:
    """The resolver's model hooks for one call, with a flag the event loop reads: a hook is running
    right now, so a slow turn can say "One moment"."""

    def __init__(self, types: Any, providers: Any, sites: Any = None):
        self._types, self._providers = types, providers
        self._sites = sites or NoDisambiguator()
        self.consulting = False

    def pick_type(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> Verdict:
        return self._run(self._types.pick_type, phrase, hint, candidate_ids)

    def check_type(self, phrase: str, hint: str | None, first: Verdict, rival: str) -> Verdict | None:
        return self._run(self._types.check_type, phrase, hint, first, rival)

    def prefetch_check(self, phrase: str, hint: str | None, pair: tuple[str, str]) -> None:
        self._types.prefetch_check(phrase, hint, pair)

    def prefetch_pick(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> None:
        self._types.prefetch_pick(phrase, hint, candidate_ids)

    def pick_provider(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict:
        return self._run(self._providers.pick_provider, phrase, type_id, candidate_ids)

    def provider_genders(self, candidate_ids: list[str]) -> dict[str, float] | None:
        return self._run(self._providers.provider_genders, candidate_ids)

    def pick_site(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict:
        return self._run(self._sites.pick_site, phrase, type_id, candidate_ids)

    def _run(self, hook, *args):
        self.consulting = True
        try:
            return hook(*args)
        finally:
            self.consulting = False


@dataclass
class ToolContext:
    index: CatalogIndex
    availability: Availability
    speak_direct: bool = True
    hooks: Optional[ModelHooks] = None  # None: the resolver runs without a model
    # A ModelClient-like: provider, calls (scheduling.model_client.ModelCall), begin_turn(), warm_up(), close().
    model_client: Optional[Any] = None
    chooser: str = "none"  # what the agent asked for; model_client.provider is what runs
    on_event: Optional[EventCallback] = None

    async def emit(self, event: dict) -> None:
        if self.on_event:
            await self.on_event(event)

    @property
    def provider(self) -> str:
        return self.model_client.provider if self.model_client else "none"


def make_context(catalog: str, *, speak_direct: bool, chooser: str = "none", timeout_ms: int = TURN_BUDGET_MS,
                 on_event: Optional[EventCallback] = None, api_keys: Mapping[str, str] = NO_KEYS) -> ToolContext:
    catalog_path = resolve_catalog_path(catalog)
    index = load_index(catalog_path)
    client = make_model_client(chooser, timeout_ms / 1000, api_keys)
    hooks = None
    if client is not None:
        h = CHOOSERS[chooser].hooks_for(index, client, Gate())
        hooks = ModelHooks(h["disambiguator"], h["chooser"], h["site_chooser"])
    return ToolContext(index=index, availability=CallAvailability(*shared_availability(catalog_path)),
                       speak_direct=speak_direct, hooks=hooks, model_client=client, chooser=chooser,
                       on_event=on_event)


def make_model_client(chooser: str, timeout_s: float, api_keys: Mapping[str, str] = NO_KEYS) -> Any:
    """Per call, not shared: a networked client carries this call's per-turn budget (begin_turn)
    and this call's key. One request may use at most REQUEST_TIMEOUT_S of the budget, so a slow
    choice still leaves time for its check."""
    spec = CHOOSERS.get(chooser)
    if spec is None or not spec.available(api_keys=api_keys):
        logger.warning(f"Chooser {chooser!r} is not available (missing key or package); resolving without a model")
        return None
    return spec.make_client(api_keys=api_keys, mode="auto", timeout_s=min(REQUEST_TIMEOUT_S, timeout_s),
                            turn_budget_s=timeout_s)


async def warm_up_model(ctx: ToolContext) -> None:
    """While the caller is still listening to the greeting: pay JEV's cold start, open the OpenAI
    connection, or make sure the embedding model and this catalog's vectors are loaded."""
    client = ctx.model_client
    if client is None:
        return
    started = time.perf_counter()
    call = await asyncio.to_thread(client.warm_up, ctx.index)
    ms = (time.perf_counter() - started) * 1000
    if call is None:
        logger.warning(f"{client.provider} warm-up failed after {ms:.0f} ms")
    else:
        logger.info(f"{client.provider} warm-up {call.source} in {ms:.0f} ms (measured {call.latency_ms:.0f} ms)")
    await ctx.emit(model_call_event(client.provider, "warmup", call, ms))


def resolver_mode_event(ctx: ToolContext) -> dict:
    """Which chooser this call runs, for the Dev view: `active` differs from `requested` when the
    requested one is unavailable."""
    return {"type": "resolver_mode", "requested": ctx.chooser, "active": ctx.provider}


def model_call_event(provider: str, purpose: str, call: Any, ms: float, p: float | None = None) -> dict:
    """Telemetry for the UI's Dev view: one model request, its latency, size and cost."""
    return {"type": "model_call", "provider": provider, "purpose": purpose, "ms": round(ms),
            "input_tokens": call.input_tokens if call else 0, "usd": call.usd if call else 0.0,
            "ok": bool(call) and call.source != "failed", "source": call.source if call else "failed", "p": p}
