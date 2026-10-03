"""The models that can answer the resolver's hooks, by the name an agent's resolver.chooser uses.

One row per chooser: whether it can run here, how to build its client, and the hooks resolve()
takes from that client. A networked client reads its API key through call_key(): the call's api_keys
(by env var name), else the environment.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .catalog_index import CatalogIndex
from .decision import Gate
from .embed_chooser import EmbedChooser, EmbedClient, shared_embedder
from .jev import ChoiceProviderChooser, JevClient, JevProviderChooser, JevSiteChooser, JevTypeDisambiguator
from .openai_chooser import OpenAIChoiceClient

EMBEDDINGS_AVAILABLE = importlib.util.find_spec("fastembed") is not None

Hooks = dict[str, Any]  # resolve() keyword arguments: disambiguator, chooser, site_chooser
NO_KEYS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True)
class Chooser:
    name: str
    label: str
    available: Callable[..., bool]                         # (api_keys=) -> can run here
    make_client: Callable[..., Any]                        # (api_keys=, client settings) -> client (None: no model)
    hooks_for: Callable[[CatalogIndex, Any, Gate], Hooks]


def call_key(name: str, api_keys: Mapping[str, str] = NO_KEYS) -> str | None:
    """The API key `name` (an env var name) for one call or request: the one it brought, else the
    server's environment. Every key a call uses is read here (bot.py, api_keys.py, the choosers)."""
    return (api_keys.get(name) or "").strip() or (os.environ.get(name) or "").strip() or None


def _has_key(cls) -> Callable[..., bool]:
    return lambda api_keys=NO_KEYS: bool(call_key(cls.key_env, api_keys))


def _keyed_client(cls) -> Callable[..., Any]:
    return lambda api_keys=NO_KEYS, **settings: cls(call_key(cls.key_env, api_keys), **settings)


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
    Chooser("embed", "embeddings", lambda **_: EMBEDDINGS_AVAILABLE, lambda **settings: EmbedClient(shared_embedder()),
            _embed_hooks),
    Chooser("none", "no model", lambda **_: True, lambda **settings: None, lambda index, client, gate: {}),
)}
NAMES = tuple(CHOOSERS)
