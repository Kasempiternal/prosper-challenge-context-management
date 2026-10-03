"""The stress suite (eval/cases_stress_sf.jsonl, eval/cases_stress.jsonl) cannot rot: format, catalog ids,
severity and source coverage, and that the files are exactly what eval/stress/build_stress.py derives from
the catalogs. No resolver runs here; the stress sets are dev sets, never blind."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from eval_cases import EVAL, dev_case_files, eval_sets

STRESS = ("stress_sf", "stress")
DATA = Path(__file__).resolve().parents[2] / "data"
SEVERITIES = {"critical", "hard"}
SOURCES = set("abcdef")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def validator():
    sys.path.insert(0, str(EVAL))
    try:
        return _load_module("validate_case_format", EVAL / "validate_case_format.py")
    finally:
        sys.path.remove(str(EVAL))


def _cases(name: str) -> list[dict]:
    path = EVAL / eval_sets.SETS[name].file
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_stress_sets_are_registered_dev_sets_with_the_right_catalog():
    for name in STRESS:
        s = eval_sets.SETS[name]
        assert not s.blind and not s.in_all and (EVAL / s.file).exists()
        assert (EVAL / s.file) in dev_case_files()
        assert s.file not in eval_sets.BLIND_FILES
    assert not eval_sets.SETS["stress_sf"].national and eval_sets.SETS["stress"].national


@pytest.mark.parametrize("name", STRESS)
def test_format_and_catalog_ids(validator, name):
    keys = set(eval_sets.EXPECT_KEYS)
    errs = validator.validate(name, keys, validator.known_update_keys({eval_sets.SETS[name].file}))
    assert errs == []


@pytest.mark.parametrize("name", STRESS)
def test_every_case_is_scorable(name):
    cases = _cases(name)
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["severity"] in SEVERITIES, c["id"]
        assert set(c["source"].split(",")) <= SOURCES, c["id"]
        last = c["turns"][-1]
        assert "update" in last and "expect" not in last, c["id"]
        assert c["expected"]["status"] in {"offer", "ask", "refuse"}, c["id"]
        for alt in c.get("also_ok", []):
            assert alt["status"] in {"offer", "ask", "refuse"} and set(alt) <= set(eval_sets.EXPECT_KEYS), c["id"]
        if c["expected"]["status"] == "refuse":
            assert c["expected"].get("refuse_code"), c["id"]


def test_the_suite_covers_the_brief():
    sf, nat = _cases("stress_sf"), _cases("stress")
    assert 30 <= len(sf) <= 45 and 40 <= len(nat) <= 55
    both = sf + nat
    assert {s for c in both for s in c["source"].split(",")} == SOURCES
    assert {c["severity"] for c in both} == SEVERITIES
    assert sum(c["severity"] == "critical" for c in both) > len(both) // 2
    assert {c["expected"]["status"] for c in both} == {"offer", "ask", "refuse"}
    assert sum(len(c["turns"]) > 1 for c in both) >= 8
    umbrella = {"my stomach doctor said I need a scope", "my baby's checkup", "some blood work", "my six-month dental checkup"}
    said = {t["update"].get("service_phrase") for c in sf for t in c["turns"]}
    assert umbrella <= said


def test_national_cases_pin_the_catalog_and_sf_cases_do_not():
    meta = json.loads((DATA / "national" / "catalog.meta.json").read_text(encoding="utf-8"))["sha256"]
    assert hashlib.sha256((DATA / "national" / "catalog.json").read_bytes()).hexdigest() == meta
    assert {c["catalog_sha256"] for c in _cases("stress")} == {meta}
    assert not any("catalog_sha256" in c for c in _cases("stress_sf"))


def test_files_are_what_the_builder_derives_from_the_catalogs():
    """Ground truth comes from catalog queries: rebuilding must reproduce the committed files byte for byte.
    A catalog edit that changes an answer fails here (or in the builder's own catalog asserts)."""
    sys.path.insert(0, str(EVAL / "stress"))
    try:
        build = _load_module("build_stress", EVAL / "stress" / "build_stress.py")
    finally:
        sys.path.remove(str(EVAL / "stress"))
    build.sf()
    build.national()
    for which, name in (("sf", "stress_sf"), ("nat", "stress")):
        want = "\n".join(json.dumps(c, ensure_ascii=False) for c, n in zip(build._cases, build._notes) if n["which"] == which) + "\n"
        got = (EVAL / eval_sets.SETS[name].file).read_bytes().decode("utf-8")
        assert got == want, f"{eval_sets.SETS[name].file} is stale: run python eval/stress/build_stress.py"


def test_the_blind_sets_stay_out_of_the_stress_files():
    text = " ".join((EVAL / eval_sets.SETS[n].file).read_text(encoding="utf-8") for n in STRESS)
    for blind in eval_sets.BLIND_FILES:
        assert blind not in text
