"""The only home of booking rules. Runs in the resolver and again at booking time.

Catalog policy 6 ("a multi-location provider's location must be disambiguated") is enforced
by construction: a BookableRow always carries one concrete location, and only rows are offered
or booked. Policies 1-3 are also guaranteed for rows built by the index, but are re-checked here
so a row assembled anywhere else (e.g. from a stale held slot) cannot slip through.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .catalog_index import BookableRow


class IssueKind(str, Enum):
    VIOLATION = "violation"
    NEEDS_INFO = "needs_info"


class Rule(str, Enum):
    PROVIDER_LOCATION = "provider_location"
    PROVIDER_TYPE = "provider_type"
    CAPABILITY = "capability"
    REFERRAL = "referral"
    NEW_PATIENT_TYPE = "new_patient_type"
    NEW_PATIENT_PROVIDER = "new_patient_provider"


@dataclass(frozen=True)
class Patient:
    is_new: bool | None = None
    has_referral: bool | None = None


@dataclass(frozen=True)
class Violation:
    kind: IssueKind
    rule: Rule
    field: str | None = None  # patient field whose answer would settle a NEEDS_INFO


def check(row: BookableRow, patient: Patient) -> list[Violation]:
    t, prov, loc = row.type, row.provider, row.location
    out: list[Violation] = []
    if loc.id not in prov.location_ids:
        out.append(Violation(IssueKind.VIOLATION, Rule.PROVIDER_LOCATION))
    if t.id not in prov.appointment_type_ids:
        out.append(Violation(IssueKind.VIOLATION, Rule.PROVIDER_TYPE))
    if t.required_capability and t.required_capability not in loc.capabilities:
        out.append(Violation(IssueKind.VIOLATION, Rule.CAPABILITY))

    if t.requires_referral:
        if patient.has_referral is None:
            out.append(Violation(IssueKind.NEEDS_INFO, Rule.REFERRAL, "has_referral"))
        elif not patient.has_referral:
            out.append(Violation(IssueKind.VIOLATION, Rule.REFERRAL))

    for closed, rule in ((not t.new_patients_allowed, Rule.NEW_PATIENT_TYPE),
                         (not prov.accepting_new_patients, Rule.NEW_PATIENT_PROVIDER)):
        if not closed:
            continue
        if patient.is_new is None:
            out.append(Violation(IssueKind.NEEDS_INFO, rule, "is_new"))
        elif patient.is_new:
            out.append(Violation(IssueKind.VIOLATION, rule))
    return out


def has_violation(issues: list[Violation]) -> bool:
    return any(i.kind is IssueKind.VIOLATION for i in issues)
