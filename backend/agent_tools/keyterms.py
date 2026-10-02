"""STT keyterms: catalog words the speech-to-text model should be biased towards.

ElevenLabs realtime STT accepts up to 50 keyterms of at most 20 characters each
(https://elevenlabs.io/docs/capabilities/speech-to-text, "Keyterm prompting"). The catalog has
far more names than that, so terms are taken in priority order: proper nouns first (they are
what STT mangles and what the resolver matches on), then visit words. A multi-metro catalog puts
metro names ahead of surnames.
"""

from __future__ import annotations

import re
from collections import Counter

from scheduling.catalog_index import CatalogIndex

MAX_KEYTERMS = 50
MAX_KEYTERM_CHARS = 20


def _type_head(name: str) -> str:
    """'Vaccination / Immunization' -> 'Vaccination', 'Bone Density Scan (DEXA)' -> 'Bone Density Scan'."""
    return re.split(r" / | \(", name)[0].strip()


def _multi_metro_tiers(index: CatalogIndex) -> list[list[str]]:
    """A national catalog has thousands of names, so the cap goes to what most callers say: their
    city (metros with the most sites first), then the commonest surnames, then visit words."""
    metros = sorted(index.metros.values(), key=lambda m: (-len(index.locs_by_metro.get(m.id, ())), m.name))
    surnames = Counter(p.last_name for p in index.providers.values())
    return [
        [m.name for m in metros],
        sorted(surnames, key=lambda name: (-surnames[name], name)),
        [_type_head(t.name) for t in index.types.values() if t.id not in index.unoffered_types],
    ]


def stt_keyterms(index: CatalogIndex) -> list[str]:
    types = list(index.types.values())
    tiers = _multi_metro_tiers(index) if index.multi_metro else [
        [p.last_name for p in index.providers.values()],
        [loc.short_name for loc in index.locations.values()],
        [_type_head(t.name) for t in types if t.id in index.unoffered_types],
        [s for s in index.specialties if s != "General"],
        [_type_head(t.name) for t in types if t.id not in index.unoffered_types],
        [a.phrase for a in index.aliases],
    ]
    terms: list[str] = []
    seen: set[str] = set()
    for tier in tiers:
        for term in tier:
            key = term.casefold()
            if not term or len(term) > MAX_KEYTERM_CHARS or key in seen:
                continue
            seen.add(key)
            terms.append(term)
            if len(terms) == MAX_KEYTERMS:
                return terms
    return terms
