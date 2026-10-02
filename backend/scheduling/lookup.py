"""Answers to caller questions (hours, address, a doctor's details, "do you offer X"), at most 5 facts."""

from __future__ import annotations

from .catalog_index import CatalogIndex
from .lexicon import match_types
from .names import match_locations, match_providers
from .policy import Patient, check
from .templates import join_and

MAX_FACTS = 5
_NEW_WITH_REFERRAL = Patient(is_new=True, has_referral=True)
KINDS = ("location_info", "provider_info", "do_you_offer")


def lookup(index: CatalogIndex, kind: str, phrase: str) -> list[str]:
    if kind == "location_info":
        return _location_facts(index, phrase)
    if kind == "provider_info":
        return _provider_facts(index, phrase)
    if kind == "do_you_offer":
        return _offer_facts(index, phrase)
    raise ValueError(f"unknown lookup kind {kind!r}; expected one of {KINDS}")


def _location_facts(index: CatalogIndex, phrase: str) -> list[str]:
    cands = match_locations(index, phrase)
    if not cands:
        return [f"No location matches '{phrase}'."]
    if len(cands) > 1:
        return [f"Several locations match: {join_and([index.locations[c.id].name for c in cands])}."]
    loc = index.locations[cands[0].id]
    caps = join_and(sorted(c.replace("_", " ") for c in loc.capabilities)) or "general visits only"
    return [loc.name, f"Address: {loc.address}, {loc.city}", f"Hours: {loc.hours}", f"Phone: {loc.phone}",
            f"On-site services: {caps}"][:MAX_FACTS]


def _provider_facts(index: CatalogIndex, phrase: str) -> list[str]:
    cands = match_providers(index, phrase)
    if not cands:
        return [f"No provider matches '{phrase}'."]
    facts = []
    for c in cands[:MAX_FACTS]:
        p = index.providers[c.id]
        sites = join_and([index.locations[l].short_name for l in p.location_ids])
        new = "accepting new patients" if p.accepting_new_patients else "not accepting new patients"
        facts.append(f"{p.name}, {p.title}, {p.specialty}; at {sites}; speaks {join_and(list(p.languages))}; {new}")
    return facts


def _offer_facts(index: CatalogIndex, phrase: str) -> list[str]:
    cands = match_types(index, phrase)
    if not cands:
        return [f"No appointment type matches '{phrase}'."]
    top = cands[0].score
    facts = []
    for c in [c for c in cands if c.score >= top - 0.1][:MAX_FACTS]:
        t = index.types[c.type_id]
        if c.type_id in index.unoffered_types:
            facts.append(f"{t.name}: not offered at our clinics.")
            continue
        rows = index.rows_by_type[c.type_id]
        sites = sorted({r.location.short_name for r in rows})
        # The type flag alone is not enough: no doctor offering it may be taking new patients.
        new_ok = any(not check(r, _NEW_WITH_REFERRAL) for r in rows)
        rules = ", ".join([
            "referral required" if t.requires_referral else "no referral needed",
            "new patients welcome" if new_ok else "established patients only",
        ])
        facts.append(f"{t.name}: offered, {t.duration_min} min, {rules}; at {join_and(sites)}")
    return facts
