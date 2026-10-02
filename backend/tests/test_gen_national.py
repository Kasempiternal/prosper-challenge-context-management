import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from tools import gen_national_catalog as gen

DATA = Path(__file__).resolve().parents[1] / "data"
NATIONAL = DATA / "national"
SF = json.loads((DATA / "catalog.json").read_text(encoding="utf-8"))
GEO_KEYS = ("state", "zip", "neighborhood", "lat", "lon", "metro_id")


@pytest.fixture(scope="module")
def small(tmp_path_factory) -> dict:
    """Two generations of the same seed at 1/5 scale."""
    outs = []
    for run in ("a", "b"):
        out = tmp_path_factory.mktemp(run)
        gen.main(["--seed", "7", "--scale", "0.2", "--out", str(out), "--skip-sf-meta"])
        outs.append(out)
    return {"dirs": outs, "catalog": json.loads((outs[0] / "catalog.json").read_text(encoding="utf-8"))}


@pytest.fixture(scope="module")
def committed() -> dict:
    return json.loads((NATIONAL / "catalog.json").read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_same_seed_same_bytes(small):
    a, b = small["dirs"]
    assert _sha(a / "catalog.json") == _sha(b / "catalog.json")
    assert _sha(a / "aliases.json") == _sha(b / "aliases.json")
    meta = json.loads((a / "catalog.meta.json").read_text(encoding="utf-8"))
    assert meta["sha256"] == _sha(a / "catalog.json")
    assert meta["label"] == "National (synthetic, 40 metros)"


def test_committed_meta_matches_committed_catalog(committed):
    meta = json.loads((NATIONAL / "catalog.meta.json").read_text(encoding="utf-8"))
    assert meta["sha256"] == _sha(NATIONAL / "catalog.json")
    assert meta["counts"]["providers"] == len(committed["providers"]) == 5000
    assert 120_000 <= meta["counts"]["bookable_rows"] <= 170_000
    sf_meta = json.loads((DATA / "catalog.meta.json").read_text(encoding="utf-8"))
    assert sf_meta["sha256"] == _sha(DATA / "catalog.json")
    assert sf_meta["label"] == "SF sample (provided)"


@pytest.mark.parametrize("which", ["small", "committed"])
def test_referential_integrity_and_geo(which, request):
    cat = request.getfixturevalue(which)
    cat = cat["catalog"] if which == "small" else cat
    metros = {m["id"] for m in cat["metros"]}
    locs = {loc["id"]: loc for loc in cat["locations"]}
    types = {t["id"] for t in cat["appointment_types"]}
    for loc in cat["locations"]:
        assert loc["metro_id"] in metros
        assert len(loc["zip"]) == 5 and loc["zip"].isdigit()
        assert -90 <= loc["lat"] <= 90 and -180 <= loc["lon"] <= 180
    for p in cat["providers"]:
        assert set(p["location_ids"]) <= set(locs) and set(p["appointment_type_ids"]) <= types
    assert cat["policies"] == SF["policies"]


def test_sf_subset_preserved_verbatim(committed):
    locs = {loc["id"]: loc for loc in committed["locations"]}
    for orig in SF["locations"]:
        got = locs[orig["id"]]
        assert json.dumps({k: v for k, v in got.items() if k not in GEO_KEYS}) == json.dumps(orig)
        assert got["metro_id"] == "san-francisco-ca"
    assert [json.dumps(p) for p in committed["providers"][:50]] == [json.dumps(p) for p in SF["providers"]]
    assert committed["appointment_types"][:82] == SF["appointment_types"]
    assert committed["appointment_types"][82]["id"] == "appt_082"


def test_capability_gating(committed):
    locs = {loc["id"]: loc for loc in committed["locations"]}
    caps = {t["id"]: t.get("required_capability") for t in committed["appointment_types"]}
    for p in committed["providers"]:
        site_caps = {c for lid in p["location_ids"] for c in locs[lid]["capabilities"]}
        gated = [t for t in p["appointment_type_ids"] if caps[t] and caps[t] not in site_caps]
        assert not gated, (p["id"], gated)


def test_metro_layout(committed):
    per_metro = Counter(loc["metro_id"] for loc in committed["locations"])
    assert len(committed["metros"]) == 40 and len(per_metro) == 40
    assert per_metro["san-francisco-ca"] == 8
    assert per_metro["new-york-ny"] == 18 and per_metro["austin-tx"] == 8
    assert min(per_metro.values()) == 3
    assert 290 <= len(committed["locations"]) <= 310
    downtown = [loc for loc in committed["locations"] if loc["name"] == "Downtown Health Center"]
    assert len({loc["metro_id"] for loc in downtown}) >= 25

    locs = {loc["id"]: loc for loc in committed["locations"]}
    eye = {locs[lid]["metro_id"] for p in committed["providers"] if p["specialty"] == "Ophthalmology"
           for lid in p["location_ids"]}
    assert eye == {"new-york-ny", "houston-tx", "boston-ma"}
    for metro in ("albuquerque-nm", "salt-lake-city-ut", "new-orleans-la"):
        assert not any("imaging" in loc["capabilities"] for loc in committed["locations"] if loc["metro_id"] == metro)


def test_exact_duplicate_names_are_rare(committed):
    stats = gen.name_stats(committed)
    assert stats["extra_copy_rate"] < 0.005
    assert stats["distinct_surnames"] > 2000


def test_unoffered_share(committed):
    offered = {t for p in committed["providers"] for t in p["appointment_type_ids"]}
    unoffered = [t for t in committed["appointment_types"] if t["id"] not in offered]
    assert 0.03 <= len(unoffered) / len(committed["appointment_types"]) <= 0.07
