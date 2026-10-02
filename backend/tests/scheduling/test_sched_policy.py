import itertools

from scheduling.policy import IssueKind, Patient, Rule, check
from scheduling.request import Request, Update, merge
from scheduling.resolver import resolve

FLAGS = (True, False, None)
PATIENTS = [Patient(is_new=n, has_referral=r) for n, r in itertools.product(FLAGS, FLAGS)]


def _rules(issues, kind):
    return {(i.rule, i.field) for i in issues if i.kind is kind}


def test_new_patient_knee_mri_violates_type_rule(index):
    row = index.row("appt_065", "prov_015", "loc_005")
    issues = check(row, Patient(is_new=True, has_referral=True))
    assert _rules(issues, IssueKind.VIOLATION) == {(Rule.NEW_PATIENT_TYPE, None)}


def test_unknown_flags_need_info_not_pass(index):
    row = index.row("appt_020", "prov_000", "loc_004")  # cardiology consult, David Chen (closed to new)
    issues = check(row, Patient())
    assert _rules(issues, IssueKind.NEEDS_INFO) == {(Rule.REFERRAL, "has_referral"),
                                                    (Rule.NEW_PATIENT_PROVIDER, "is_new")}
    assert _rules(issues, IssueKind.VIOLATION) == set()


def test_existing_patient_with_referral_passes(index):
    assert check(index.row("appt_020", "prov_000", "loc_004"), Patient(is_new=False, has_referral=True)) == []


def test_structural_rules_catch_forged_row(index):
    from scheduling.catalog_index import BookableRow
    forged = BookableRow(index.types["appt_065"], index.providers["prov_014"], index.locations["loc_002"])
    rules = _rules(check(forged, Patient(is_new=False, has_referral=True)), IssueKind.VIOLATION)
    assert rules == {(Rule.PROVIDER_LOCATION, None), (Rule.CAPABILITY, None)}


def _assert_offers_pass(plan, index):
    assert len(plan.offers) <= 3
    for o in plan.offers:
        row = index.row(o.type_id, o.provider_id, o.location_id)
        assert row is not None
        assert check(row, plan.req.patient) == [], (o, plan.req.patient)
    if plan.confirm:
        row = index.row(plan.confirm.type_id, plan.confirm.provider_id, plan.confirm.location_id)
        assert check(row, plan.req.patient) == []


def _flags(p: Patient) -> dict:
    return {k: v for k, v in (("is_new", p.is_new), ("has_referral", p.has_referral)) if v is not None}


def test_property_targeted_requests_never_offer_a_violation(index, availability):
    """Every bookable row x every patient-flag combination, asked for by exact type name,
    full provider name and site: anything offered (or confirmed after picking) passes policy."""
    offered = 0
    for row in index.bookable:
        for patient in PATIENTS:
            req = merge(Request(), Update.from_args({
                "service_name": row.type.name, "provider_phrase": row.provider.name,
                "location_phrase": row.location.short_name, **_flags(patient)}))
            plan = resolve(index, req, availability)
            _assert_offers_pass(plan, index)
            if plan.status == "offer":
                offered += 1
                picked = resolve(index, merge(plan.req, Update.from_args({"pick_offer": 1})), availability)
                assert picked.status == "confirm"
                _assert_offers_pass(picked, index)
    assert offered > 0


def test_property_broad_requests_never_offer_a_violation(index, availability):
    """Type only (no provider/site) for every offered type x every flag combination."""
    for type_id, rows in index.rows_by_type.items():
        if not rows:
            continue
        for patient in PATIENTS:
            req = merge(Request(), Update.from_args({"service_name": index.types[type_id].name, **_flags(patient)}))
            _assert_offers_pass(resolve(index, req, availability), index)


def test_property_valid_unique_rows_are_offered(index, availability):
    """Completeness: when a row passes policy and its provider's full name is unique,
    the resolver offers exactly that row rather than asking or refusing."""
    names = [p.name for p in index.providers.values()]
    patient = Patient(is_new=False, has_referral=True)
    for row in index.bookable:
        if names.count(row.provider.name) > 1:
            continue
        req = merge(Request(), Update.from_args({
            "service_name": row.type.name, "provider_phrase": row.provider.name,
            "location_phrase": row.location.short_name, **_flags(patient)}))
        plan = resolve(index, req, availability)
        assert plan.status == "offer", (row.key, plan.status, plan.say)
        assert {(o.type_id, o.provider_id, o.location_id) for o in plan.offers} == {row.key}
