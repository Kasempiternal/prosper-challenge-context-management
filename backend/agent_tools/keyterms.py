"""STT keyterms: catalog words the speech-to-text model should be biased towards.

ElevenLabs realtime STT accepts up to 50 keyterms of at most 20 characters each
(https://elevenlabs.io/docs/capabilities/speech-to-text, "Keyterm prompting"). The catalog has
far more names than that, so terms are taken in priority order: proper nouns first (they are
what STT mangles and what the resolver matches on), then visit words.
"""

from __future__ import annotations

import re

from scheduling.catalog_index import CatalogIndex

MAX_KEYTERMS = 50
MAX_KEYTERM_CHARS = 20


def _type_head(name: str) -> str:
    """'Vaccination / Immunization' -> 'Vaccination', 'Bone Density Scan (DEXA)' -> 'Bone Density Scan'."""
    return re.split(r" / | \(", name)[0].strip()


def stt_keyterms(index: CatalogIndex) -> list[str]:
    types = list(index.types.values())
    tiers = [
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
