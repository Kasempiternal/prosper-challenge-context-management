"""Property test against an oracle built from raw catalog.json, independent of policy.check.

Random multi-turn conversations (phrases, flags, picks, picks with changes, clears, time
preferences) run through merge + resolve with adversarial model hooks that return arbitrary
verdicts, including ids outside the candidates they were given. Whatever the hooks say, nothing
offered or confirmed may break a catalog rule, and resolve never raises.
"""

import json
import random
from pathlib import Path

import pytest

from fake_models import RandomHooks
from scheduling.availability import MockAvailability
from scheduling.request import Request, Update, merge
from scheduling.resolver import resolve

CATALOG = Path(__file__).resolve().parents[2] / "data" / "catalog.json"
SEEDS = (0, 1, 2)
CONVERSATIONS_PER_SEED = 150

RAW = json.loads(CATALOG.read_text(encoding="utf-8"))
ALIASES = json.loads(CATALOG.with_name("aliases.json").read_text(encoding="utf-8"))
TYPES = {t["id"]: t for t in RAW["appointment_types"]}
PROVIDERS = {p["id"]: p for p in RAW["providers"]}
LOCATIONS = {loc["id"]: loc for loc in RAW["locations"]}


def oracle(type_id, provider_id, location_id, patient) -> list[str]:
    t, p, loc = TYPES[type_id], PROVIDERS[provider_id], LOCATIONS[location_id]
    broken = []
    if location_id not in p["location_ids"]:
        broken.append("provider not at location")
    if type_id not in p["appointment_type_ids"]:
        broken.append("provider does not offer type")
    if t.get("required_capability") and t["required_capability"] not in loc["capabilities"]:
        broken.append("location lacks capability")
    if t["requires_referral"] and patient.has_referral is not True:
        broken.append("referral not confirmed")
    if (not t["new_patients_allowed"] or not p["accepting_new_patients"]) and patient.is_new is not False:
        broken.append("not confirmed established")
    return broken


SERVICES = ([t["name"] for t in RAW["appointment_types"]] + list(ALIASES["aliases"])
            + ["checkup", "heart doctor", "skin", "eye exam", "routine checkup for my job", "my zorbly thing"])
DOCTORS = ([p["name"] for p in RAW["providers"]] + ["Dr. " + p["name"].split()[-1] for p in RAW["providers"]]
           + ["Dr. Nwin", "Dr. Shen", "Dr. Chen the heart doctor"])
SITES = [loc["name"] for loc in RAW["locations"]] + ["Mission", "North", "downtown"]
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]


def random_update(rng: random.Random) -> dict:
    a: dict = {}
    if rng.random() < 0.3:
        a["service_phrase"] = rng.choice(SERVICES)
    if rng.random() < 0.3:
        a["provider_phrase"] = rng.choice(DOCTORS)
    if rng.random() < 0.2:
        a["location_phrase"] = rng.choice(SITES)
    if rng.random() < 0.3:
        a["is_new"] = rng.choice([True, False])
    if rng.random() < 0.3:
        a["has_referral"] = rng.choice([True, False])
    if rng.random() < 0.35:
        a["pick_offer"] = rng.randint(1, 3)
    if rng.random() < 0.1:
        a["time_pref"] = {"days": [rng.choice(DAYS)], "part_of_day": rng.choice([None, "morning", "afternoon"]),
                          "not_before": rng.choice([None, "2026-10-12", "2026-10-20"])}
    if rng.random() < 0.05:
        a["clear"] = [rng.choice(["service", "provider", "location", "time_pref"])]
    return a or {"pick_offer": 1}


@pytest.mark.parametrize("seed", SEEDS)
def test_nothing_offered_or_confirmed_breaks_a_catalog_rule(index, seed):
    rng = random.Random(seed)
    adversary = RandomHooks(rng, {"type": list(TYPES), "provider": list(PROVIDERS)})
    commits = 0
    for _ in range(CONVERSATIONS_PER_SEED):
        av = MockAvailability(index)
        req = Request()
        hooks = {"disambiguator": adversary, "chooser": adversary} if rng.random() < 0.5 else {}
        for _turn in range(rng.randint(1, 7)):
            req = merge(req, Update.from_args(random_update(rng)))
            plan = resolve(index, req, av, **hooks)
            for o in list(plan.offers) + ([plan.confirm] if plan.confirm else []):
                commits += 1
                broken = oracle(o.type_id, o.provider_id, o.location_id, plan.req.patient)
                assert not broken, (broken, o, plan.req.patient, plan.say)
            assert len(plan.offers) <= 3
            req = plan.req
    assert commits > 100
