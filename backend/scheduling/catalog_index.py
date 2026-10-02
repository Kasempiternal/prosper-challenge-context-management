"""Immutable, typed view of catalog.json plus the derived lookup tables the resolver needs."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from .text import normalize, stem, tokens

_HOURS = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)-(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_LOCATION_SUFFIX_WORDS = {"health", "center", "clinic", "family", "specialty", "medical", "group", "community", "care"}
# Words that every type family shares; matching on them would make every consultation confusable.
_GENERIC_TYPE_WORDS = {"consultation", "consult", "visit", "exam", "test", "session", "evaluation", "screening", "of"}


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    short_name: str
    address: str
    city: str
    phone: str
    hours: str
    open_weekdays: tuple[int, ...]
    open_minute: int
    close_minute: int
    capabilities: frozenset[str]


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    first_name: str
    last_name: str
    title: str
    specialty: str
    location_ids: tuple[str, ...]
    accepting_new_patients: bool
    languages: tuple[str, ...]
    appointment_type_ids: tuple[str, ...]


@dataclass(frozen=True)
class AppointmentType:
    id: str
    name: str
    specialty: str
    duration_min: int
    requires_referral: bool
    new_patients_allowed: bool
    required_capability: str | None


@dataclass(frozen=True)
class BookableRow:
    """A (type, provider, location) triple that satisfies the structural policies 1-3."""

    type: AppointmentType
    provider: Provider
    location: Location

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.type.id, self.provider.id, self.location.id)


@dataclass(frozen=True)
class Alias:
    phrase: str
    weights: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class CatalogIndex:
    types: dict[str, AppointmentType]
    providers: dict[str, Provider]
    locations: dict[str, Location]
    bookable: tuple[BookableRow, ...]
    rows_by_type: dict[str, tuple[BookableRow, ...]]
    row_by_key: dict[tuple[str, str, str], BookableRow]
    unoffered_types: frozenset[str]
    specialties: tuple[str, ...]
    aliases: tuple[Alias, ...]
    lay_terms: dict[str, str]
    specialty_default: dict[str, str]
    specialty_spoken: dict[str, str]
    confusables: dict[str, frozenset[str]]

    @classmethod
    def load(cls, catalog_path: str | Path, aliases_path: str | Path | None = None) -> "CatalogIndex":
        catalog_path = Path(catalog_path)
        aliases_path = Path(aliases_path) if aliases_path else catalog_path.with_name("aliases.json")
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        raw_aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
        return build_index(raw, raw_aliases)

    def row(self, type_id: str, provider_id: str, location_id: str) -> BookableRow | None:
        return self.row_by_key.get((type_id, provider_id, location_id))


def _parse_hours(text: str) -> tuple[tuple[int, ...], int, int]:
    m = _HOURS.match(text.strip())
    if not m:
        raise CatalogError(f"unparseable hours: {text!r}")
    d0, d1 = _WEEKDAYS.index(m[1]), _WEEKDAYS.index(m[2])
    return tuple(range(d0, d1 + 1)), int(m[3]) * 60 + int(m[4]), int(m[5]) * 60 + int(m[6])


def _short_location_name(name: str) -> str:
    words = name.split()
    while len(words) > 1 and words[-1].lower() in _LOCATION_SUFFIX_WORDS:
        words.pop()
    return " ".join(words)


def _split_provider_name(name: str) -> tuple[str, str]:
    parts = [p for p in name.replace(".", " ").split() if p.lower() not in {"dr", "doctor"}]
    if len(parts) < 2:
        raise CatalogError(f"provider name needs first and last: {name!r}")
    return parts[0], parts[-1]


def _require(obj: dict, key: str, kind: type):
    if key not in obj or not isinstance(obj[key], kind):
        raise CatalogError(f"{obj.get('id', '?')}: field {key!r} missing or not {kind.__name__}")
    return obj[key]


def build_index(raw: dict, raw_aliases: dict) -> CatalogIndex:
    locations: dict[str, Location] = {}
    for loc in raw["locations"]:
        weekdays, open_m, close_m = _parse_hours(_require(loc, "hours", str))
        name = _require(loc, "name", str)
        locations[loc["id"]] = Location(
            id=loc["id"], name=name, short_name=_short_location_name(name),
            address=_require(loc, "address", str), city=loc.get("city", ""), phone=loc.get("phone", ""),
            hours=loc["hours"], open_weekdays=weekdays, open_minute=open_m, close_minute=close_m,
            capabilities=frozenset(_require(loc, "capabilities", list)),
        )

    types: dict[str, AppointmentType] = {}
    for t in raw["appointment_types"]:
        types[t["id"]] = AppointmentType(
            id=t["id"], name=_require(t, "name", str), specialty=_require(t, "specialty", str),
            duration_min=_require(t, "duration_min", int),
            requires_referral=_require(t, "requires_referral", bool),
            new_patients_allowed=_require(t, "new_patients_allowed", bool),
            required_capability=t.get("required_capability"),
        )

    providers: dict[str, Provider] = {}
    for p in raw["providers"]:
        name = _require(p, "name", str)
        first, last = _split_provider_name(name)
        loc_ids = tuple(_require(p, "location_ids", list))
        type_ids = tuple(_require(p, "appointment_type_ids", list))
        unknown = [i for i in loc_ids if i not in locations] + [i for i in type_ids if i not in types]
        if unknown:
            raise CatalogError(f"{p['id']} references unknown ids {unknown}")
        providers[p["id"]] = Provider(
            id=p["id"], name=name, first_name=first, last_name=last, title=p.get("title", ""),
            specialty=_require(p, "specialty", str), location_ids=loc_ids,
            accepting_new_patients=_require(p, "accepting_new_patients", bool),
            languages=tuple(p.get("languages", [])), appointment_type_ids=type_ids,
        )

    bookable: list[BookableRow] = []
    for prov in providers.values():
        for type_id in prov.appointment_type_ids:
            t = types[type_id]
            for loc_id in prov.location_ids:
                loc = locations[loc_id]
                if t.required_capability and t.required_capability not in loc.capabilities:
                    continue
                bookable.append(BookableRow(t, prov, loc))
    bookable.sort(key=lambda r: r.key)

    rows_by_type: dict[str, list[BookableRow]] = defaultdict(list)
    for row in bookable:
        rows_by_type[row.type.id].append(row)

    aliases = []
    for phrase, weights in raw_aliases["aliases"].items():
        missing = [tid for tid in weights if tid not in types]
        if missing:
            raise CatalogError(f"alias {phrase!r} references unknown types {missing}")
        aliases.append(Alias(normalize(phrase), tuple(sorted(weights.items(), key=lambda kv: -kv[1]))))

    rows_by_type_t = {tid: tuple(rows_by_type.get(tid, ())) for tid in types}
    return CatalogIndex(
        types=types, providers=providers, locations=locations,
        bookable=tuple(bookable),
        rows_by_type=rows_by_type_t,
        row_by_key={r.key: r for r in bookable},
        unoffered_types=frozenset(tid for tid, rows in rows_by_type_t.items() if not rows),
        specialties=tuple(sorted({t.specialty for t in types.values()})),
        aliases=tuple(aliases),
        lay_terms={normalize(k): v for k, v in raw_aliases.get("lay_terms", {}).items()},
        specialty_default=dict(raw_aliases.get("specialty_default", {})),
        specialty_spoken=dict(raw_aliases.get("specialty_spoken", {})),
        confusables=_compute_confusables(types, rows_by_type_t, aliases),
    )


def _name_stems(t: AppointmentType) -> set[str]:
    return {stem(w) for w in tokens(t.name) if w not in _GENERIC_TYPE_WORDS}


def _compute_confusables(types, rows_by_type, aliases) -> dict[str, frozenset[str]]:
    """Two types are confusable when a caller could plausibly mean either: they share a
    distinctive name word or a lay alias, AND they live in the same specialty or are
    offered by largely the same providers."""
    providers_of = {tid: {r.provider.id for r in rows} for tid, rows in rows_by_type.items()}
    shared_alias: set[frozenset[str]] = set()
    for alias in aliases:
        ids = [tid for tid, _ in alias.weights]
        shared_alias.update(frozenset(pair) for pair in combinations(ids, 2))

    out: dict[str, set[str]] = defaultdict(set)
    for a, b in combinations(sorted(types), 2):
        ta, tb = types[a], types[b]
        lexical = bool(_name_stems(ta) & _name_stems(tb)) or frozenset((a, b)) in shared_alias
        pa, pb = providers_of[a], providers_of[b]
        overlap = len(pa & pb) / len(pa | pb) if pa | pb else 0.0
        if lexical and (ta.specialty == tb.specialty or overlap >= 0.5):
            out[a].add(b)
            out[b].add(a)
    return {tid: frozenset(out.get(tid, ())) for tid in types}
