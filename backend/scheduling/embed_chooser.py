"""Local embedding chooser: cosine similarity between the caller's phrase and each option's text,
softmax with a temperature, then the same Gate as JEV. No network, no cost per turn.

Option vectors are cached per text on the embedder, which is shared by the whole process: the
startup preload embeds every type and site of a catalog once (providers are embedded on first use,
since only same-named providers ever reach the chooser).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import numpy as np
from loguru import logger

from .catalog_index import CatalogIndex
from .decision import DECLINE, Gate, Verdict
from .jev import provider_criteria, site_criteria

MODEL = "BAAI/bge-small-en-v1.5"
# Chosen on eval/cases_tune.jsonl + eval/cases_national.jsonl (eval/tune_embed_temperature.py).
TEMPERATURE = 0.0125


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        """One L2-normalized row per text."""


class FastEmbedder:
    """fastembed (ONNX, CPU). The model (~67 MB) is downloaded once to the fastembed cache."""

    def __init__(self, model_name: str = MODEL):
        self.model_name = model_name
        self._model = None
        self._vectors: dict[str, np.ndarray] = {}
        self._lock = threading.Lock()

    def embed(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            missing = list(dict.fromkeys(t for t in texts if t not in self._vectors))
            if missing:
                if self._model is None:
                    from fastembed import TextEmbedding
                    self._model = TextEmbedding(self.model_name)
                for text, vec in zip(missing, self._model.embed(missing)):
                    self._vectors[text] = vec / np.linalg.norm(vec)
            return np.stack([self._vectors[t] for t in texts])


@lru_cache(maxsize=None)
def shared_embedder() -> FastEmbedder:
    return FastEmbedder()


@dataclass(frozen=True)
class EmbedCall:
    purpose: str           # the hook: type | provider | site | warmup
    source: str            # "live" | "failed"
    latency_ms: float
    input_tokens: int = 0
    usd: float = 0.0
    p: float | None = None


def type_text(index: CatalogIndex, type_id: str) -> str:
    t = index.types[type_id]
    return f"{t.name} ({t.specialty})"


class EmbedClient:
    """Per call: records each ranking for telemetry. The vectors live on the shared embedder."""

    provider = "embed"

    def __init__(self, embedder: Embedder, temperature: float = TEMPERATURE):
        self.embedder, self.temperature = embedder, temperature
        self.calls: list[EmbedCall] = []

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
            self.calls.append(EmbedCall(purpose, "failed", round((time.perf_counter() - t0) * 1000, 1)))
            return None
        probs = softmax_probs(vecs[1:] @ vecs[0], list(options), self.temperature)
        self.calls.append(EmbedCall(purpose, "live", round((time.perf_counter() - t0) * 1000, 1),
                                    p=round(max(probs.values()), 3)))
        return probs

    def warm_up(self, index: CatalogIndex) -> EmbedCall:
        """Loads the model and embeds every type and site of the catalog."""
        t0 = time.perf_counter()
        warm_catalog(self.embedder, index)
        call = EmbedCall("warmup", "live", round((time.perf_counter() - t0) * 1000, 1))
        self.calls.append(call)
        return call


def warm_catalog(embedder: Embedder, index: CatalogIndex) -> None:
    embedder.embed([type_text(index, t) for t in sorted(index.types)]
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

    def provider_genders(self, candidate_ids: list[str]) -> dict[str, float] | None:
        """A cosine between "the lady doctor" and a name says nothing reliable about gender."""
        return None

    def _decide(self, purpose: str, query: str, options: dict[str, str]) -> Verdict:
        probs = self.client.probabilities(purpose, query, options) if options else None
        return self.gate.decide(probs) if probs else DECLINE
