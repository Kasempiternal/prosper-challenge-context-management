"""Local embedding chooser: cosine similarity between the caller's phrase and each option's text,
softmax with a temperature, then the same Gate as JEV. No network, no cost per turn.

Option vectors live on the embedder, which is shared by the whole process: the startup preload
warms every type and site of a catalog (providers are embedded on first use, since only
same-named providers ever reach the chooser).
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np
from loguru import logger

from .catalog_index import CatalogIndex
from .decision import DECLINE, Gate, Verdict
from .jev import provider_criteria, site_criteria
from .model_client import ModelCall

MODEL = "BAAI/bge-small-en-v1.5"
# Chosen on eval/cases_tune.jsonl + eval/cases_national.jsonl (eval/tune_embed_temperature.py).
TEMPERATURE = 0.0125
VECTOR_CACHE = Path(__file__).resolve().parents[1] / ".cache" / "embeddings"  # gitignored
PHRASES_KEPT = 1024


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        """One L2-normalized row per text."""

    def warm(self, texts: list[str]) -> None:
        """Have these texts' vectors (a catalog's options) and the model ready before a call needs them."""


class FastEmbedder:
    """fastembed (ONNX, CPU). The model (~67 MB) is downloaded once to the fastembed cache.

    Warmed texts are kept for the process and saved under cache_dir, keyed by the model and the
    texts, so a restart reads a catalog's ~600 vectors instead of embedding them again. Other texts,
    the callers' phrases, stay in an LRU. Embedding runs outside the lock: ONNX runs calls
    concurrently, so a call's phrase never waits behind a catalog being warmed."""

    def __init__(self, model_name: str = MODEL, cache_dir: Path | None = VECTOR_CACHE):
        self.model_name, self.cache_dir = model_name, cache_dir
        self._model = None
        self._kept: dict[str, np.ndarray] = {}
        self._recent: OrderedDict[str, np.ndarray] = OrderedDict()
        self._warmed: set[str] = set()
        self._lock = threading.Lock()       # the vector stores and the model load
        self._warming = threading.Lock()    # one catalog warm at a time, so none is embedded twice

    def embed(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            found = {t: v for t in texts if (v := self._lookup(t)) is not None}
        missing = [t for t in dict.fromkeys(texts) if t not in found]
        if missing:
            found.update(zip(missing, self._compute(missing)))
            with self._lock:
                for t in missing:
                    self._recent[t] = found[t]
                while len(self._recent) > PHRASES_KEPT:
                    self._recent.popitem(last=False)
        return np.stack([found[t] for t in texts])

    def warm(self, texts: list[str]) -> None:
        digest = hashlib.sha256("\n".join([self.model_name, *texts]).encode()).hexdigest()
        with self._warming:
            if digest in self._warmed:
                return
            path = self.cache_dir / f"{digest}.npy" if self.cache_dir else None
            vectors = np.load(path) if path and path.exists() else None
            if vectors is None or len(vectors) != len(texts):
                vectors = np.stack(self._compute(texts))
                if path:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix(".tmp")
                    with open(tmp, "wb") as f:
                        np.save(f, vectors)
                    tmp.replace(path)
            self._load()
            with self._lock:
                self._kept.update(zip(texts, vectors))
            self._warmed.add(digest)

    def _lookup(self, text: str) -> np.ndarray | None:
        vec = self._kept.get(text)
        if vec is None and (vec := self._recent.get(text)) is not None:
            self._recent.move_to_end(text)
        return vec

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                self._model = TextEmbedding(self.model_name)
            return self._model

    def _compute(self, texts: list[str]) -> list[np.ndarray]:
        return [vec / np.linalg.norm(vec) for vec in self._load().embed(texts)]


@lru_cache(maxsize=None)
def shared_embedder() -> FastEmbedder:
    return FastEmbedder()


def type_text(index: CatalogIndex, type_id: str) -> str:
    t = index.types[type_id]
    return f"{t.name} ({t.specialty})"


class EmbedClient:
    """Per call: records each ranking for telemetry. The vectors live on the shared embedder."""

    provider = "embed"

    def __init__(self, embedder: Embedder, temperature: float = TEMPERATURE):
        self.embedder, self.temperature = embedder, temperature
        self.calls: list[ModelCall] = []

    def begin_turn(self) -> None:
        pass

    def save(self) -> None:
        pass

    def close(self) -> None:
        pass

    def probabilities(self, purpose: str, query: str, options: dict[str, str]) -> dict[str, float] | None:
        t0 = time.perf_counter()
        try:
            vecs = self.embedder.embed([query, *options.values()])
        except Exception as e:  # noqa: BLE001 - a broken model must not break the call
            logger.warning(f"Embedding chooser failed: {e!r}")
            self.calls.append(ModelCall("", "failed", round((time.perf_counter() - t0) * 1000, 1), purpose=purpose))
            return None
        probs = softmax_probs(vecs[1:] @ vecs[0], list(options), self.temperature)
        self.calls.append(ModelCall("", "local", round((time.perf_counter() - t0) * 1000, 1), purpose=purpose,
                                    p=round(max(probs.values()), 3)))
        return probs

    def warm_up(self, index: CatalogIndex) -> ModelCall:
        """Loads the model and every type and site vector of the catalog."""
        t0 = time.perf_counter()
        warm_catalog(self.embedder, index)
        call = ModelCall("", "local", round((time.perf_counter() - t0) * 1000, 1), purpose="warmup")
        self.calls.append(call)
        return call


def warm_catalog(embedder: Embedder, index: CatalogIndex) -> None:
    embedder.warm([type_text(index, t) for t in sorted(index.types)]
                  + list(site_criteria(index, sorted(index.locations)).values()))


def softmax_probs(sims: np.ndarray, ids: list[str], temperature: float) -> dict[str, float]:
    z = sims / temperature
    e = np.exp(z - z.max())
    p = e / e.sum()
    return {oid: float(x) for oid, x in zip(ids, p)}


class EmbedChooser:
    """All three resolver hooks. The query is the caller's words alone; the option texts are the
    catalog's (type name and specialty; the JEV provider and site descriptions)."""

    def __init__(self, index: CatalogIndex, client: EmbedClient, gate: Gate = Gate()):
        self.ix, self.client, self.gate = index, client, gate

    def pick_type(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> Verdict:
        query = f"{phrase} ({hint})" if hint else phrase
        return self._decide("type", query, {t: type_text(self.ix, t) for t in sorted(candidate_ids)})

    def pick_provider(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict:
        return self._decide("provider", phrase, provider_criteria(self.ix, candidate_ids))

    def pick_site(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict:
        return self._decide("site", phrase, site_criteria(self.ix, candidate_ids))

    def check_type(self, phrase: str, hint: str | None, first: Verdict, rival: str) -> Verdict | None:
        """Cosines have no "either": there is no second question to ask."""
        return None

    def prefetch_check(self, phrase: str, hint: str | None, pair: tuple[str, str]) -> None:
        pass

    def provider_genders(self, candidate_ids: list[str]) -> dict[str, float] | None:
        """A cosine between "the lady doctor" and a name says nothing reliable about gender."""
        return None

    def _decide(self, purpose: str, query: str, options: dict[str, str]) -> Verdict:
        probs = self.client.probabilities(purpose, query, options) if options else None
        return self.gate.decide(probs) if probs else DECLINE
