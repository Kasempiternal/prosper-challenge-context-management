"""Immutable, typed view of catalog.json plus the derived lookup tables the resolver needs."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from .geo import Gazetteer, build_gazetteer
from .names import NameIndex
from .text import normalize, stem, tokens

_HOURS = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)-(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_LOCATION_SUFFIX_WORDS = {"health", "center", "clinic", "family", "specialty", "medical", "group", "community", "care"}
# Words that every type family shares; matching on them would make every consultation confusable.
_GENERIC_TYPE_WORDS = {"consultation", "consult", "visit", "exam", "test", "session", "evaluation", "screening", "of"}
_ZIP = re.compile(r"^\d{5}$")
_STATE = re.compile(r"^[A-Z]{2}$")

# A catalog without `metros` (the SF catalog) is one metro with no geography.
IMPLICIT_METRO = "_"


class CatalogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Metro:
    id: str
    name: str
    state: str
    aliases: tuple[str, ...]
    lat: float | None
    lon: float | None


@dataclass(frozen=True, slots=True)
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
    metro_id: str = IMPLICIT_METRO
    state: str | None = None
    zip: str | None = None
    neighborhood: str | None = None
    lat: float | None = None
    lon: float | None = None


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
class AppointmentType:
    id: str
    name: str
    specialty: str
    duration_min: int
    requires_referral: bool
    new_patients_allowed: bool
    required_capability: str | None


@dataclass(frozen=True, slots=True)
class BookableRow:
    """A (type, provider, location) triple that satisfies the structural policies 1-3."""

    type: AppointmentType
    provider: Provider
    location: Location

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.type.id, self.provider.id, self.location.id)


@dataclass(frozen=True, slots=True)
class Alias:
    phrase: str
    weights: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
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
    metros: dict[str, Metro]
    locs_by_metro: dict[str, tuple[str, ...]]
    rows_by_type_loc: dict[tuple[str, str], tuple[BookableRow, ...]]
    rows_by_provider: dict[str, tuple[BookableRow, ...]]
    metros_by_type: dict[str, frozenset[str]]
    gazetteer: Gazetteer
    name_index: NameIndex

    @classmethod
    def load(cls, catalog_path: str | Path, aliases_path: str | Path | None = None) -> "CatalogIndex":
        catalog_path = Path(catalog_path)
        aliases_path = Path(aliases_path) if aliases_path else catalog_path.with_name("aliases.json")
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        raw_aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
        return build_index(raw, raw_aliases)

    def row(self, type_id: str, provider_id: str, location_id: str) -> BookableRow | None:
        return self.row_by_key.get((type_id, provider_id, location_id))

    @property
    def has_geo(self) -> bool:
        """Every location has lat/lon (validated all-or-none at load)."""
        return next(iter(self.locations.values())).lat is not None if self.locations else False

    @property
    def multi_metro(self) -> bool:
        return len(self.metros) > 1


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


def _optional_str(obj: dict, key: str, pattern: re.Pattern | None = None) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or (pattern and not pattern.match(value)):
        raise CatalogError(f"{obj.get('id', '?')}: field {key!r} malformed: {value!r}")
    return value


def _coords(obj: dict) -> tuple[float | None, float | None]:
    lat, lon = obj.get("lat"), obj.get("lon")
    if (lat is None) != (lon is None):
        raise CatalogError(f"{obj.get('id', '?')}: lat and lon must be given together")
    if lat is None:
        return None, None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (lat, lon)) \
            or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise CatalogError(f"{obj.get('id', '?')}: bad coordinates {lat!r}, {lon!r}")
    return float(lat), float(lon)


def _parse_metros(raw_metros) -> dict[str, Metro]:
    if raw_metros is None:
        return {IMPLICIT_METRO: Metro(IMPLICIT_METRO, "", "", (), None, None)}
    if not isinstance(raw_metros, list) or not raw_metros:
        raise CatalogError("metros must be a non-empty list")
    metros: dict[str, Metro] = {}
    for m in raw_metros:
        mid = _require(m, "id", str)
        if mid in metros or mid == IMPLICIT_METRO:
            raise CatalogError(f"duplicate or reserved metro id {mid!r}")
        aliases = m.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise CatalogError(f"{mid}: aliases must be a list of strings")
        lat, lon = _coords(m)
        if lat is None:
            raise CatalogError(f"{mid}: metro needs lat and lon")
        state = _optional_str(m, "state", _STATE)
        if state is None:
            raise CatalogError(f"{mid}: metro needs a two-letter state")
        metros[mid] = Metro(mid, _require(m, "name", str), state, tuple(aliases), lat, lon)
    return metros


def _parse_location(loc: dict, metros: dict[str, Metro], explicit_metros: bool) -> Location:
    weekdays, open_m, close_m = _parse_hours(_require(loc, "hours", str))
    name = _require(loc, "name", str)
    metro_id = loc.get("metro_id")
    if explicit_metros and metro_id not in metros:
        raise CatalogError(f"{loc['id']}: metro_id {metro_id!r} is not a declared metro")
    if not explicit_metros and metro_id is not None:
        raise CatalogError(f"{loc['id']}: metro_id {metro_id!r} but the catalog declares no metros")
    lat, lon = _coords(loc)
    return Location(
        id=loc["id"], name=name, short_name=_short_location_name(name),
        address=_require(loc, "address", str), city=loc.get("city", ""), phone=loc.get("phone", ""),
        hours=loc["hours"], open_weekdays=weekdays, open_minute=open_m, close_minute=close_m,
        capabilities=frozenset(_require(loc, "capabilities", list)),
        metro_id=metro_id or IMPLICIT_METRO, state=_optional_str(loc, "state", _STATE),
        zip=_optional_str(loc, "zip", _ZIP), neighborhood=_optional_str(loc, "neighborhood"), lat=lat, lon=lon,
    )


def build_index(raw: dict, raw_aliases: dict) -> CatalogIndex:
    metros = _parse_metros(raw.get("metros"))
    locations: dict[str, Location] = {}
    for loc in raw["locations"]:
        locations[loc["id"]] = _parse_location(loc, metros, "metros" in raw)
    located = [l.id for l in locations.values() if l.lat is not None]
    if located and len(located) != len(locations):
        missing = sorted(set(locations) - set(located))
        raise CatalogError(f"some locations have lat/lon, these do not: {missing[:5]}")

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
    rows_by_type_loc: dict[tuple[str, str], list[BookableRow]] = defaultdict(list)
    rows_by_provider: dict[str, list[BookableRow]] = defaultdict(list)
    for row in bookable:
        rows_by_type[row.type.id].append(row)
        rows_by_type_loc[(row.type.id, row.location.id)].append(row)
        rows_by_provider[row.provider.id].append(row)

    aliases = []
    for phrase, weights in raw_aliases["aliases"].items():
        missing = [tid for tid in weights if tid not in types]
        if missing:
            raise CatalogError(f"alias {phrase!r} references unknown types {missing}")
        aliases.append(Alias(normalize(phrase), tuple(sorted(weights.items(), key=lambda kv: -kv[1]))))

    rows_by_type_t = {tid: tuple(rows_by_type.get(tid, ())) for tid in types}
    locs_by_metro: dict[str, list[str]] = {mid: [] for mid in metros}
    for loc in locations.values():
        locs_by_metro[loc.metro_id].append(loc.id)
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
        metros=metros,
        locs_by_metro={mid: tuple(sorted(ids)) for mid, ids in locs_by_metro.items()},
        rows_by_type_loc={k: tuple(v) for k, v in rows_by_type_loc.items()},
        rows_by_provider={pid: tuple(rows_by_provider.get(pid, ())) for pid in providers},
        metros_by_type={tid: frozenset(r.location.metro_id for r in rows) for tid, rows in rows_by_type_t.items()},
        gazetteer=build_gazetteer(locations, metros),
        name_index=NameIndex.build(providers.values()),
    )


def _name_stems(t: AppointmentType) -> set[str]:
    return {stem(w) for w in tokens(t.name) if w not in _GENERIC_TYPE_WORDS}


def _compute_confusables(types, rows_by_type, aliases) -> dict[str, frozenset[str]]:
    """Two types are confusable when a caller could plausibly mean either: they share a
    distinctive name word or a lay alias, AND they live in the same specialty or are
    offered by largely the same providers."""
    providers_of = {tid: {r.provider.id for r in rows} for tid, rows in rows_by_type.items()}
    stems = {tid: _name_stems(t) for tid, t in types.items()}
    shared_alias: set[frozenset[str]] = set()
    for alias in aliases:
        ids = [tid for tid, _ in alias.weights]
        shared_alias.update(frozenset(pair) for pair in combinations(ids, 2))

    out: dict[str, set[str]] = defaultdict(set)
    for a, b in combinations(sorted(types), 2):
        if not (stems[a] & stems[b] or frozenset((a, b)) in shared_alias):
            continue
        if types[a].specialty != types[b].specialty:
            pa, pb = providers_of[a], providers_of[b]
            union = len(pa | pb)
            if not union or len(pa & pb) / union < 0.5:
                continue
        out[a].add(b)
        out[b].add(a)
    return {tid: frozenset(out.get(tid, ())) for tid in types}
