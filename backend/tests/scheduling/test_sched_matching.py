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


# ---- NameIndex equivalence: the pre-NameIndex matcher, frozen as the oracle -----------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import jellyfish  # noqa: E402

from scheduling.catalog_index import build_index  # noqa: E402
from scheduling.names import (_NAME_WORD_SCORE, _NEVER_NAMES, _CLUE_STOPWORDS, _HONORIFICS, _TITLE_WORDS,  # noqa: E402
                              MIN_PROVIDER_SCORE, NameCandidate, _top_tier, clue_words)
from scheduling.text import normalize, phonetic_keys, tokens  # noqa: E402

_EVAL = Path(__file__).resolve().parents[3] / "eval"
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
    words = [w for w in tokens(phrase or "") if w not in _TITLE_WORDS]
    if not words:
        return []
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
    tier = _top_tier(scored, MIN_PROVIDER_SCORE)
    if within and not tier:
        return _oracle_match_providers(index, phrase)
    if not tier and len(words) > 1:
        names = [w for w in words if _oracle_is_name_word(index, w)]
        if names and names != words:
            return _oracle_match_providers(index, " ".join(names), within)
    return tier


def _oracle_clue_words(index, phrase, ids):
    ids = list(ids)
    return tuple(w for w in tokens(phrase or "")
                 if w not in _HONORIFICS and w not in _CLUE_STOPWORDS and not _oracle_is_name_word(index, w, ids))


def _provider_phrases() -> list[str]:
    out = {"Dr. Nwin", "Dr. Win", "Dr. Gwen", "Dr. Shen", "Emily", "Dr. Hannah Nwin", "Maria Garcia", "Dr. Zzyzx",
           "David", "Mc Donald", "Dr. Ng Uyen", "doctor", "", "the woman who speaks Spanish", "Dr. Chen the heart one"}
    for path in _EVAL.glob("cases*.jsonl"):
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
