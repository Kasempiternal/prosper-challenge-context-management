import pytest

from scheduling.catalog_index import CatalogError, build_index


def test_bookable_rows_are_the_structural_join(index):
    assert len(index.bookable) == 760
    for row in index.bookable:
        assert row.location.id in row.provider.location_ids
        assert row.type.id in row.provider.appointment_type_ids
        cap = row.type.required_capability
        assert cap is None or cap in row.location.capabilities


def test_knee_mri_only_at_imaging_sites(index):
    assert {r.key for r in index.rows_by_type["appt_065"]} == {
        ("appt_065", "prov_014", "loc_004"),
        ("appt_065", "prov_015", "loc_005"),
    }


def test_unoffered_types(index):
    assert index.unoffered_types == {"appt_045", "appt_046", "appt_047", "appt_048", "appt_049",
                                     "appt_070", "appt_071", "appt_077"}


def test_specialties_and_short_names(index):
    assert len(index.specialties) == 21
    assert {l.id: l.short_name for l in index.locations.values()} == {
        "loc_000": "Mission Bay", "loc_001": "Mission District", "loc_002": "North Beach",
        "loc_003": "North Gate", "loc_004": "Downtown", "loc_005": "Midtown",
        "loc_006": "Sunset", "loc_007": "Richmond",
    }


@pytest.mark.parametrize("a,b", [
    ("appt_002", "appt_003"),  # Annual Physical / Annual Wellness Visit
    ("appt_027", "appt_028"),  # Skin Cancer Screening / Full Body Skin Exam
    ("appt_000", "appt_001"),  # New Patient Consultation / New Patient Visit
    ("appt_004", "appt_005"),  # Follow-up Visit / Follow-up Consultation
    ("appt_063", "appt_064"), ("appt_063", "appt_065"), ("appt_064", "appt_065"),  # MRI family
    ("appt_010", "appt_011"), ("appt_010", "appt_012"), ("appt_010", "appt_013"),  # vaccination family
])
def test_required_confusables(index, a, b):
    assert b in index.confusables[a]
    assert a in index.confusables[b]


def test_unrelated_types_not_confusable(index):
    assert "appt_062" not in index.confusables["appt_063"]  # X-Ray vs MRI - Brain
    assert "appt_081" not in index.confusables["appt_027"]  # Allergy Skin Testing vs Skin Cancer Screening


def test_bad_catalog_fails_at_boundary():
    raw = {"locations": [{"id": "loc_x", "name": "X Clinic", "address": "1 St", "hours": "whenever",
                          "capabilities": []}], "providers": [], "appointment_types": []}
    with pytest.raises(CatalogError, match="unparseable hours"):
        build_index(raw, {"aliases": {}})
