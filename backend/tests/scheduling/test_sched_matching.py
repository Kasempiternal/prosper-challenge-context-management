from pathlib import Path

import pytest

from scheduling.availability import MockAvailability
from scheduling.catalog_index import CatalogIndex
from scheduling.lexicon import match_types
from scheduling.lookup import lookup
from scheduling.names import match_locations, match_providers, read_provider_clues
from scheduling.request import Request, Update, merge
from scheduling.resolver import TYPE_TIE_GAP, resolve


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
    ("want my teeth cleaned", ["appt_074"]),             # "-ed" stems like "-ing"
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


@pytest.mark.parametrize("phrase,tied", [
    ("I need my yearly physical", {"appt_002"}),          # says the name "Annual Physical"
    ("my yearly exam", {"appt_002", "appt_003"}),         # "annual exam": still either
    ("yearly checkup", {"appt_002", "appt_003"}),
])
def test_yearly_says_annual(index, phrase, tied):
    cands = match_types(index, phrase)
    assert {c.type_id for c in cands if c.score >= cands[0].score - 0.1} == tied


@pytest.mark.parametrize("phrase,ids", [
    ("an echo for my heart", ["appt_021"]),                          # not Cardiology Consultation too
    ("the doctor wants me to do a breathing test", ["appt_079"]),    # not Pulmonology Consultation too
    ("lung test", ["appt_078"]),                                     # no alias: the default stays
])
def test_a_specialty_default_is_no_evidence_beside_an_alias_of_that_specialty(index, phrase, ids):
    assert [c.type_id for c in match_types(index, phrase) if c.score >= 0.6] == ids


@pytest.fixture(scope="module")
def nat() -> CatalogIndex:
    return CatalogIndex.load(Path(__file__).resolve().parents[2] / "data" / "national" / "catalog.json")


def _tier(cands):
    return {c.type_id for c in cands if c.score >= cands[0].score - TYPE_TIE_GAP} if cands else set()


@pytest.mark.parametrize("phrase,kept", [   # review round 3, finding 3 (national catalog)
    ("I need my a1c", {"appt_232"}),                           # A1C Test, not Diabetes Management
    ("I need my upper endoscopy", {"appt_055", "appt_184"}),   # Upper Endoscopy (EGD) stays beside the alias
    ("I need my tonsils", {"appt_175"}),                       # Tonsil Evaluation, not ENT Consultation
    ("I need my depression", {"appt_095"}),                    # Depression Screening Visit
])
def test_an_alias_that_only_matches_a_named_type_word_for_word_does_not_drop_it(nat, phrase, kept):
    assert _tier(match_types(nat, phrase)) == kept


def test_a_new_patient_asking_for_allergy_shots_is_still_refused(nat):
    req = merge(Request(), Update.from_args({"is_new": True, "has_referral": True, "location_phrase": "I'm in Sacramento",
                                             "service_phrase": "I need my allergy shots"}))
    plan = resolve(nat, req, MockAvailability(nat))
    assert (plan.status, plan.refusal.code) == ("refuse", "new_patient_type")


NGUYENS = {"prov_015", "prov_016", "prov_023", "prov_024", "prov_028", "prov_030", "prov_036", "prov_037"}
CHENS = {"prov_000", "prov_001", "prov_004", "prov_012", "prov_039", "prov_046", "prov_047"}
PULMONOLOGY_NGUYENS = {"prov_023", "prov_024", "prov_028"}


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
    ("Dr. Chen, the family medicine one", CHENS),   # "one" sounds like Nguyen but names nobody
])
def test_provider_matching(index, phrase, expected):
    assert set(_ids(match_providers(index, phrase))) == expected


@pytest.mark.parametrize("phrase,fits,facts,gender,rest", [
    ("Dr. Nguyen, he speaks Spanish", {"prov_023", "prov_028"}, ("language",), "male", ()),
    ("Dr. Nguyen, the lady doctor", PULMONOLOGY_NGUYENS, (), "female", ()),
    ("Dr. Nguyen over in Richmond", {"prov_024", "prov_028"}, ("site",), None, ()),
    ("Dr. Chen, the pediatrician", {"prov_012"}, ("specialty",), None, ()),
    ("Dr. Maria Garcia who sees kids", {"prov_002"}, ("specialty",), None, ()),
    ("Maria Garcia, the nurse practitioner", {"prov_003"}, ("title",), None, ()),
    ("Dr. Nguyen who speaks French", PULMONOLOGY_NGUYENS, (), None, ("speaks", "french")),  # fits nobody
    ("Dr. Nguyen, the one I saw last time", PULMONOLOGY_NGUYENS, (), None, ("saw", "last", "time")),
    ("Dr. Nguyen, she's the one at Midtown", {"prov_023"}, ("site",), "female", ()),
    ("Dr. Nguyen, my daughter's doctor, she is great", PULMONOLOGY_NGUYENS, (), None, ("daughter", "s", "she", "great")),
    ("Dr. Nguyen, the women's health one", PULMONOLOGY_NGUYENS, (), None, ("women", "s", "health")),
])
def test_provider_clues(index, phrase, fits, facts, gender, rest):
    pool = PULMONOLOGY_NGUYENS if "Nguyen" in phrase else {c.id for c in match_providers(index, phrase)}
    clues = read_provider_clues(index, phrase, pool)
    assert (set(clues.fits), clues.facts, clues.gender, clues.rest) == (fits, facts, gender, rest)


def test_a_never_name_word_still_matches_a_provider_with_that_name():
    raw = {"locations": [{"id": "l1", "name": "Main Clinic", "address": "1 Main St", "hours": "Mon-Fri 8:00-17:00",
                          "capabilities": []}],
           "appointment_types": [{"id": "t1", "name": "Sick Visit", "specialty": "General", "duration_min": 20,
                                  "requires_referral": False, "new_patients_allowed": True}],
           "providers": [{"id": "p1", "name": "Dr. Wen He", "specialty": "General", "location_ids": ["l1"],
                          "accepting_new_patients": True, "appointment_type_ids": ["t1"]}]}
    ix = build_index(raw, {"aliases": {}})
    assert _ids(match_providers(ix, "Dr. He")) == ["p1"]


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


# ---- NameIndex equivalence: the pre-NameIndex matcher, frozen as the oracle -----------------

import json  # noqa: E402
from itertools import takewhile  # noqa: E402


import jellyfish  # noqa: E402

from scheduling.catalog_index import build_index  # noqa: E402
from scheduling.names import (_NAME_WORD_SCORE, _NEVER_NAMES, _CLUE_STOPWORDS, _HONORIFICS, _TITLE_WORDS,  # noqa: E402
                              MIN_PROVIDER_SCORE, NameCandidate, _top_tier, clue_words)
from scheduling.text import normalize, phonetic_keys, tokens  # noqa: E402
from eval_cases import dev_case_files  # noqa: E402

_DATA = Path(__file__).resolve().parents[2] / "data"


def _oracle_word_score(heard, actual):
    if heard == actual:
        return 1.0, "exact"
    jw = jellyfish.jaro_winkler_similarity(heard, actual)
    if phonetic_keys(heard) & phonetic_keys(actual):
        return round(0.88 + 0.1 * jw, 3), "phonetic"
    return jw, "fuzzy"


def _oracle_is_name_word(index, word, provider_ids=None):
    if word in _NEVER_NAMES or word in _CLUE_STOPWORDS:
        return False
    pool = [index.providers[i] for i in provider_ids] if provider_ids else index.providers.values()
    return any(_oracle_word_score(word, normalize(n))[0] >= _NAME_WORD_SCORE
               for p in pool for n in (p.first_name, p.last_name))


def _oracle_match_providers(index, phrase, within=None):
    names = {normalize(n) for p in index.providers.values() for n in (p.first_name, p.last_name)}
    words = [w for w in tokens(phrase or "") if w not in _TITLE_WORDS and (w not in _NEVER_NAMES or w in names)]
    if not words:
        return []
    tier = _oracle_tier(index, words, within)
    if len(words) > 1 and words[-1] not in names:
        raw = tokens(phrase or "")
        lasts = {normalize(p.last_name) for p in index.providers.values()}
        for start in [0] + [i + 1 for i, w in enumerate(raw) if w in _HONORIFICS]:
            run = list(takewhile(lambda w: w in names and w not in _NEVER_NAMES and w not in _CLUE_STOPWORDS,
                                 raw[start:start + 2]))
            named = _oracle_tier(index, run, within) if run and run[-1] in lasts else []
            if named and (not tier or named[0].score > tier[0].score):
                tier = named
    if within:
        anywhere = _oracle_match_providers(index, phrase)
        return anywhere if not tier or (anywhere and anywhere[0].score > tier[0].score) else tier
    if not tier and len(words) > 1:
        names = [w for w in words if _oracle_is_name_word(index, w)]
        if names and names != words:
            return _oracle_match_providers(index, " ".join(names), within)
    return tier


def _oracle_tier(index, words, within):
    pool = [index.providers[i] for i in within] if within else list(index.providers.values())
    scored = []
    for prov in pool:
        last, first = normalize(prov.last_name), normalize(prov.first_name)
        if len(words) == 1:
            s_last, via_last = _oracle_word_score(words[0], last)
            s_first, _ = _oracle_word_score(words[0], first)
            if s_first == 1.0 and s_first > s_last:
                scored.append(NameCandidate(prov.id, 0.95, "first"))
            else:
                scored.append(NameCandidate(prov.id, round(s_last, 3), via_last))
        else:
            s_last, via = _oracle_word_score(words[-1], last)
            s_first, _ = _oracle_word_score(words[0], first)
            joined, _ = _oracle_word_score("".join(words), last)
            if joined > s_last:
                scored.append(NameCandidate(prov.id, round(joined, 3), "fuzzy"))
                continue
            scored.append(NameCandidate(prov.id, round(s_last * (1.0 if s_first >= 0.88 else 0.9), 3), via))
    return _top_tier(scored, MIN_PROVIDER_SCORE)


def _oracle_clue_words(index, phrase, ids):
    ids = list(ids)
    return tuple(w for w in tokens(phrase or "")
                 if w not in _HONORIFICS and w not in _CLUE_STOPWORDS and not _oracle_is_name_word(index, w, ids))


def _provider_phrases() -> list[str]:
    out = {"Dr. Nwin", "Dr. Win", "Dr. Gwen", "Dr. Shen", "Emily", "Dr. Hannah Nwin", "Maria Garcia", "Dr. Zzyzx",
           "David", "Mc Donald", "Dr. Ng Uyen", "doctor", "", "the woman who speaks Spanish", "Dr. Chen the heart one"}
    for path in dev_case_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            for turn in json.loads(line)["turns"]:
                if turn.get("update", {}).get("provider_phrase"):
                    out.add(turn["update"]["provider_phrase"])
    return sorted(out)


def _assert_same_answers(ix, phrases, within_sets):
    for phrase in phrases:
        expected = _oracle_match_providers(ix, phrase)
        assert match_providers(ix, phrase) == expected, phrase
        ids = [c.id for c in expected]
        assert clue_words(ix, phrase, ids) == _oracle_clue_words(ix, phrase, ids), phrase
        for within in within_sets:
            assert match_providers(ix, phrase, within) == _oracle_match_providers(ix, phrase, within), (phrase, within)


def test_name_index_matches_previous_matcher_on_sf(index):
    phrases = _provider_phrases()
    assert len(phrases) > 50
    _assert_same_answers(index, phrases, [["prov_000", "prov_046"], ["prov_015", "prov_016", "prov_002"]])


def test_name_index_matches_previous_matcher_on_a_larger_roster():
    """Every SF first name with every SF surname plus near-miss surnames (~940 providers): the surname pre-filter must
    not drop anyone the full scan would have kept."""
    raw = json.loads((_DATA / "catalog.json").read_text(encoding="utf-8"))
    sf = raw["providers"]
    firsts = sorted({p["name"].split()[1] for p in sf})
    lasts = sorted({p["name"].split()[-1] for p in sf} | {
        "Chan", "Cheng", "Garza", "Gracia", "Win", "Wynn", "Nunez", "Patil", "Smyth", "Smith", "Johnston", "Jensen",
        "Lee", "Leigh", "Kimura", "Satou", "Ramos", "Hernandes", "Fernandez", "Wang", "Wong", "Ng", "McDonald"})
    template = sf[0]
    raw["providers"] = [dict(template, id=f"prov_{i:05d}", name=f"Dr. {f} {l}")
                        for i, (f, l) in enumerate((f, l) for f in firsts for l in lasts)]
    ix = build_index(raw, json.loads((_DATA / "aliases.json").read_text(encoding="utf-8")))
    assert len(ix.providers) > 900
    _assert_same_answers(ix, _provider_phrases(), [["prov_00000", "prov_00007", "prov_00100"]])


def _misspellings(ix) -> list[str]:
    """Dropped, swapped and doubled letters of every surname, alone and after a first name:
    these land in the 0.78-0.9 fuzzy band where a too-eager pre-filter would lose candidates."""
    out = set()
    firsts = sorted({p.first_name for p in ix.providers.values()})[:5]
    for last in sorted({p.last_name for p in ix.providers.values()}):
        w = last.lower()
        variants = {w[:i] + w[i + 1:] for i in range(len(w))}
        variants |= {w[:i] + w[i + 1] + w[i] + w[i + 2:] for i in range(len(w) - 1)}
        variants |= {w[:i] + w[i] + w[i:] for i in range(len(w))}
        for v in sorted(variants):
            out.add(f"Dr. {v}")
            out.update(f"{f} {v}" for f in firsts)
    return sorted(out)


def test_name_index_matches_previous_matcher_on_misspellings(index):
    phrases = _misspellings(index)
    assert len(phrases) > 500
    _assert_same_answers(index, phrases, [])


def test_candidate_rounding_edge_is_kept(index):
    # jaro_winkler("mckirezi", "ramirez") = 0.77976, which rounds to the 0.78 floor: the old
    # full scan kept Dr. Ramirez, so the surname pre-filter must keep him too.
    assert match_providers(index, "Dr. Mckirezi") == _oracle_match_providers(index, "Dr. Mckirezi")
    assert {c.id for c in match_providers(index, "Dr. Mckirezi")} == {
        p.id for p in index.providers.values() if p.last_name == "Ramirez"}
