"""Tables derived from a catalog index, built once per index."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def per_index(build: Callable[[Any], T]) -> Callable[[Any], T]:
    """build(index), computed on first use for each index. Keyed by id(index); the entry holds the
    index, so its id cannot be reused while cached."""
    cache: dict[int, tuple[Any, T]] = {}

    @wraps(build)
    def get(index) -> T:
        hit = cache.get(id(index))
        if hit is None or hit[0] is not index:
            hit = cache[id(index)] = (index, build(index))
        return hit[1]
    return get
