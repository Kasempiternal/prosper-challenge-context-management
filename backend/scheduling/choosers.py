"""The models that can answer the resolver's hooks, by the name an agent's resolver.chooser uses.

One row per chooser: whether it can run here, how to build its client, and the hooks resolve()
takes from that client. A networked client reads its API key from the environment.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from typing import Any, Callable

from .catalog_index import CatalogIndex
from .decision import Gate
from .embed_chooser import EmbedChooser, EmbedClient, shared_embedder
from .jev import ChoiceProviderChooser, JevClient, JevProviderChooser, JevSiteChooser, JevTypeDisambiguator
from .openai_chooser import OpenAIChoiceClient

EMBEDDINGS_AVAILABLE = importlib.util.find_spec("fastembed") is not None

Hooks = dict[str, Any]  # resolve() keyword arguments: disambiguator, chooser, site_chooser


@dataclass(frozen=True)
class Chooser:
    name: str
    label: str
    available: Callable[[], bool]
    make_client: Callable[..., Any]                        # client settings -> client (None: no model)
    hooks_for: Callable[[CatalogIndex, Any, Gate], Hooks]


def _has_key(cls) -> Callable[[], bool]:
    return lambda: bool(os.environ.get(cls.key_env))


def _keyed_client(cls) -> Callable[..., Any]:
    return lambda **settings: cls(os.environ.get(cls.key_env), **settings)


def _choice_hooks(provider_hook) -> Callable[[CatalogIndex, Any, Gate], Hooks]:
    return lambda index, client, gate: {"disambiguator": JevTypeDisambiguator(index, client, gate),
                                        "chooser": provider_hook(index, client, gate),
                                        "site_chooser": JevSiteChooser(index, client, gate)}


def _embed_hooks(index: CatalogIndex, client: EmbedClient, gate: Gate) -> Hooks:
    hook = EmbedChooser(index, client, gate)
    return {"disambiguator": hook, "chooser": hook, "site_chooser": hook}


CHOOSERS: dict[str, Chooser] = {c.name: c for c in (
    Chooser("jev", "JEV", _has_key(JevClient), _keyed_client(JevClient), _choice_hooks(JevProviderChooser)),
    # One answer token has no calibrated yes, so OpenAI is asked no gender question.
    Chooser("openai", "OpenAI", _has_key(OpenAIChoiceClient), _keyed_client(OpenAIChoiceClient),
            _choice_hooks(ChoiceProviderChooser)),
    Chooser("embed", "embeddings", lambda: EMBEDDINGS_AVAILABLE, lambda **settings: EmbedClient(shared_embedder()),
            _embed_hooks),
    Chooser("none", "no model", lambda: True, lambda **settings: None, lambda index, client, gate: {}),
)}
NAMES = tuple(CHOOSERS)
