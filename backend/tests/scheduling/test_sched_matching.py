import pytest

from scheduling.lexicon import match_types
from scheduling.lookup import lookup
from scheduling.names import match_locations, match_providers


def _ids(cands):
    return [c.type_id if hasattr(c, "type_id") else c.id for c in cands]


@pytest.mark.parametrize("phrase,top", [
    ("cardiology consultation", ["appt_020"]),
    ("knee mri", ["appt_065"]),
    ("MRI of my knee", ["appt_065"]),
    ("MRI knee", ["appt_065"]),
    ("teeth cleaning", ["appt_074"]),
    ("blood work", ["appt_072"]),
    ("physical therapy", ["appt_070"]),
    ("flu shot", ["appt_011"]),
    ("eye exam", ["appt_045"]),
])
def test_lexicon_top1(index, phrase, top):
    assert _ids(match_types(index, phrase))[:1] == top


@pytest.mark.parametrize("phrase,tied", [
    ("checkup", {"appt_002", "appt_003"}),
    ("physical", {"appt_002", "appt_003"}),
    ("skin check", {"appt_027", "appt_028"}),
    ("mri", {"appt_063", "appt_064", "appt_065"}),
])
def test_lexicon_reports_genuine_ties(index, phrase, tied):
    cands = match_types(index, phrase)
    top = cands[0].score
    assert {c.type_id for c in cands if c.score >= top - 0.1} == tied


def test_lexicon_specialty_hint_and_lay_terms(index):
    assert _ids(match_types(index, None, "Cardiology")) == ["appt_020"]
    assert _ids(match_types(index, "my heart is acting up"))[:1] == ["appt_020"]
    assert _ids(match_types(index, "checkup", "Cardiology"))[:1] == ["appt_020"]


NGUYENS = {"prov_015", "prov_016", "prov_023", "prov_024", "prov_028", "prov_030", "prov_036", "prov_037"}
CHENS = {"prov_000", "prov_001", "prov_004", "prov_012", "prov_039", "prov_046", "prov_047"}


@pytest.mark.parametrize("phrase,expected", [
    ("Dr. Nwin", NGUYENS),
    ("Dr. Win", NGUYENS),
    ("Dr. Gwen", NGUYENS),
    ("Dr. Shen", CHENS),
    ("Dr. Chen", CHENS),
    ("Dr. Emily Chen", {"prov_046"}),
    ("Emily", {"prov_046"}),
    ("Dr. Hannah Nwin", {"prov_015"}),
    ("Maria Garcia", {"prov_002", "prov_003"}),
    ("Dr. Garsha", {"prov_002", "prov_003", "prov_008"}),
    ("Dr. Jonson", {"prov_041"}),
    ("Dr. Zzyzx", set()),
])
def test_provider_matching(index, phrase, expected):
    assert set(_ids(match_providers(index, phrase))) == expected


def test_provider_matching_within_asked_options(index):
    assert _ids(match_providers(index, "David", within=["prov_000", "prov_046"])) == ["prov_000"]
    assert set(_ids(match_providers(index, "David"))) == {"prov_000", "prov_041"}


@pytest.mark.parametrize("phrase,expected", [
    ("Mission Bay", {"loc_000"}),
    ("Mission District", {"loc_001"}),
    ("Mission", {"loc_000", "loc_001"}),
    ("North Beach", {"loc_002"}),
    ("North Gate", {"loc_003"}),
    ("Mission Bae", {"loc_000"}),
    ("down town", {"loc_004"}),
    ("the one on Geary", {"loc_004", "loc_006"}),
    ("Narnia", set()),
])
def test_location_matching(index, phrase, expected):
    assert set(_ids(match_locations(index, phrase))) == expected


def test_lookup_facts(index):
    assert lookup(index, "location_info", "Midtown")[:3] == [
        "Midtown Medical Group", "Address: 2099 Market St, San Francisco", "Hours: Mon-Fri 8:00-17:00"]
    assert lookup(index, "do_you_offer", "eye exam")[0] == "Eye Exam: not offered at our clinics."
    assert lookup(index, "provider_info", "Dr. David Chen") == [
        "Dr. David Chen, MD, Cardiology; at Mission Bay, Mission District, Downtown, and Richmond; "
        "speaks English; not accepting new patients"]
    assert all(len(lookup(index, k, p)) <= 5 for k, p in [("provider_info", "Dr. Nguyen"), ("do_you_offer", "mri")])
