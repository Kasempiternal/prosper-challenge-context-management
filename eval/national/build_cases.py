"""Author eval/cases_national.jsonl from the national catalog, independently of the resolver.

Ground truth comes only from catalog queries here (the 6 policies + haversine distance). Caller
wording comes from DeepSeek, which sees scenario facts in plain language and nothing else.

  python eval/national/build_cases.py scenarios   # seeded sampler -> eval/national/scenarios.json
  python eval/national/build_cases.py phrase      # DeepSeek via cmdc -> eval/national/deepseek_raw/*.json
  python eval/national/build_cases.py merge       # scenarios + phrasings -> eval/cases_national.jsonl

`phrase` skips batches whose raw file exists, so reruns cost nothing and reproduce the same file.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CATALOG = ROOT / "backend" / "data" / "national" / "catalog.json"
META = ROOT / "backend" / "data" / "national" / "catalog.meta.json"
SCENARIOS = HERE / "scenarios.json"
RAW = HERE / "deepseek_raw"
OUT = ROOT / "eval" / "cases_national.jsonl"
SEED = 20261002
MODEL = "deepseek/deepseek-v4.1-flash"
BATCH = 12

EXISTING = {"is_new": False, "has_referral": True}
NEW = {"is_new": True, "has_referral": True}

# Real-world points that are not in the catalog, used only to pick the expected nearest metro.
PORTLAND_ME = (43.6591, -70.2568)
MAINE_CENTROID = (45.3695, -69.2428)

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}

# Plain-language service needs -> acceptable catalog types. Authored from type names in catalog.json.
GEO_SERVICES = [
    ("a flu shot", ["appt_011", "appt_010"]),
    ("routine blood work their doctor ordered", ["appt_072", "appt_073"]),
    ("a teeth cleaning", ["appt_074"]),
    ("a routine screening mammogram", ["appt_068", "appt_217"]),
    ("an X-ray of their knee that their doctor ordered", ["appt_219", "appt_062"]),
    ("a physical therapy evaluation (they have a referral)", ["appt_070"]),
]
SYMPTOMS = [
    ("twisted their knee playing soccer last weekend; it is swollen", ["appt_132", "appt_143", "appt_144", "appt_032"],
     ["orthop", "sports medicine"]),
    ("has had lower back pain for a few months that will not go away", ["appt_133", "appt_279", "appt_277", "appt_138"],
     ["orthop", "pain management"]),
    ("has an itchy red rash spreading on both arms", ["appt_121", "appt_026", "appt_123"], ["dermatolog"]),
    ("gets a burning feeling in the throat after meals, like acid coming back up, most nights",
     ["appt_187", "appt_053"], ["gastro", "gerd", "gi "]),
    ("keeps getting bad headaches with nausea and sensitivity to light", ["appt_151", "appt_036"], ["neurolog"]),
    ("has sharp heel pain with the first steps every morning", ["appt_288", "appt_285", "appt_291"],
     ["podiatr", "plantar"]),
    ("has trouble hearing people in noisy rooms and keeps turning the TV up", ["appt_051", "appt_176", "appt_050"],
     ["audiolog", "ent "]),
    ("has swollen, stiff, painful finger joints every morning for weeks", ["appt_261", "appt_259"],
     ["rheumatolog", "arthritis"]),
]
NEW_OK_TYPES = ["appt_011", "appt_124", "appt_051", "appt_019", "appt_288", "appt_027"]
NO_NEW_TYPES = ["appt_235", "appt_237", "appt_126"]  # no new-patient-allowed near-duplicate
UNOFFERED = ["appt_313", "appt_312", "appt_130", "appt_229"]
CAPABILITY_TYPES = ["appt_065", "appt_068", "appt_183", "appt_074", "appt_070", "appt_063"]
EYE_TYPES = ["appt_045", "appt_046"]
GENERIC_TYPE_WORDS = ("follow-up", "session", "visit", "management", "consultation")
# Speech-to-text style misspellings, written by hand for this eval.
MISSPELLED = {"albuquerque-nm": "Albukerky", "sacramento-ca": "Sacremento", "minneapolis-mn": "Minneapolous",
              "philadelphia-pa": "Philedelphia", "indianapolis-in": "Indianopolis", "pittsburgh-pa": "Pitsburg",
              "cleveland-oh": "Cleaveland", "nashville-tn": "Nashvile", "san-antonio-tx": "San Antonyo"}


def miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


class Catalog:
    """The 6 policies as set queries. No resolver code."""

    def __init__(self, raw: dict):
        self.types = {t["id"]: t for t in raw["appointment_types"]}
        self.provs = {p["id"]: p for p in raw["providers"]}
        self.locs = {loc["id"]: loc for loc in raw["locations"]}
        self.metros = {m["id"]: m for m in raw["metros"]}
        self.by_type: dict[str, list[dict]] = defaultdict(list)
        for p in raw["providers"]:
            for t in p["appointment_type_ids"]:
                self.by_type[t].append(p)
        self.by_name: dict[str, list[dict]] = defaultdict(list)
        for p in raw["providers"]:
            self.by_name[p["name"]].append(p)

    def xy(self, lid: str) -> tuple[float, float]:
        return self.locs[lid]["lat"], self.locs[lid]["lon"]

    def metro_xy(self, mid: str) -> tuple[float, float]:
        return self.metros[mid]["lat"], self.metros[mid]["lon"]

    def metro_of(self, lid: str) -> str:
        return self.locs[lid]["metro_id"]

    def prov_metros(self, p: dict) -> set[str]:
        return {self.metro_of(lid) for lid in p["location_ids"]}

    def rows(self, type_ids, patient: dict, provider_ids=None):
        """(type_id, provider_id, location_id) satisfying policies 1-5 for this patient."""
        for tid in type_ids:
            t = self.types[tid]
            if t["requires_referral"] and not patient.get("has_referral"):
                continue
            if patient.get("is_new") and not t["new_patients_allowed"]:
                continue
            for p in self.by_type.get(tid, ()):
                if provider_ids is not None and p["id"] not in provider_ids:
                    continue
                if patient.get("is_new") and not p["accepting_new_patients"]:
                    continue
                for lid in p["location_ids"]:
                    cap = t.get("required_capability")
                    if cap and cap not in self.locs[lid]["capabilities"]:
                        continue
                    yield tid, p["id"], lid

    def valid_locs(self, type_ids, patient, provider_ids=None) -> set[str]:
        return {lid for _, _, lid in self.rows(type_ids, patient, provider_ids)}

    def in_metro(self, lids, mid) -> list[str]:
        return sorted(lid for lid in lids if self.metro_of(lid) == mid)

    def within(self, lids, anchor, radius) -> list[str]:
        return sorted(lid for lid in lids if miles(anchor, self.xy(lid)) <= radius)


def scenario(sid, category, kind, patient, situation, fields, expected, expect_t1=None, fixed=None, checks=None,
             truth=None):
    """fields: {phrase_key: instruction for DeepSeek}. fixed: phrases set by hand (misspellings)."""
    return {"id": sid, "category": category, "kind": kind, "patient": patient, "situation": situation,
            "fields": fields, "fixed": fixed or {}, "checks": checks or {}, "expect_t1": expect_t1,
            "expected": expected, "truth": truth or {}}


def offer(types, lids, **extra):
    return {"status": "offer", "types_any": sorted(types), "location_ids_subset": sorted(lids), **extra}


def build(cat: Catalog) -> list[dict]:
    rng = random.Random(SEED)
    out: list[dict] = []
    metro_ids = sorted(cat.metros)
    state_metros: dict[str, list[str]] = defaultdict(list)
    for mid in metro_ids:
        state_metros[cat.metros[mid]["state"]].append(mid)
    used_metros: Counter = Counter()

    def pick_metro(pred, avoid_used=True):
        cands = [m for m in metro_ids if pred(m)]
        if not cands:
            return None
        fresh = [m for m in cands if not used_metros[m]] if avoid_used else cands
        m = rng.choice(fresh or cands)
        used_metros[m] += 1
        return m

    def svc_order():
        order = list(GEO_SERVICES)
        rng.shuffle(order)
        return order

    def metro_strict(mid, types, patient):
        """Valid sites in the metro, requiring that some metro site is not valid (so location matters)."""
        valid = cat.in_metro(cat.valid_locs(types, patient), mid)
        allsites = [lid for lid in cat.locs if cat.metro_of(lid) == mid]
        return valid if valid and len(valid) < len(allsites) else []

    def label(m):
        return "Washington, DC" if m == "washington-dc" else cat.metros[m]["name"]

    def loc_field(desc):
        return {"location_phrase": desc}

    def svc_field(desc):
        return {"service_phrase": f"how the caller asks for {desc}"}

    # ---------------- geo ----------------
    n = 0

    def gid():
        nonlocal n
        n += 1
        return f"nat-geo-{n:02d}"

    for kind in ("city", "city", "im_in", "im_in"):
        for desc, types in svc_order():
            m = pick_metro(lambda m: len(metro_strict(m, types, EXISTING)) >= 1 and len(
                [x for x in cat.locs.values() if x["metro_id"] == m]) >= 5)
            if m:
                valid = metro_strict(m, types, EXISTING)
                break
        name = label(m)
        how = ("names the city only" if kind == "city" else "says they are in / live in the city")
        out.append(scenario(gid(), "geo", kind, EXISTING,
                            f"Existing patient who needs {desc}. They are in {name}, "
                            f"{STATE_NAMES[cat.metros[m]['state']]}.",
                            {**svc_field(desc), **loc_field(f"where they are: the caller {how} ({name})")},
                            offer(types, valid), checks={"location_phrase": {"must": [name]}},
                            truth={"metro": m}))

    # suburb city: anchor is that city's sites; 25 mi like a city anchor
    suburbs = sorted({(x["city"], x["metro_id"]) for x in cat.locs.values()
                      if x["city"] != cat.metros[x["metro_id"]]["name"]})
    suburb_city_count = Counter(c for c, _ in suburbs)
    suburbs = [s for s in suburbs if suburb_city_count[s[0]] == 1]
    rng.shuffle(suburbs)
    for city, m in suburbs:
        pts = [cat.xy(lid) for lid, x in cat.locs.items() if x["city"] == city]
        anchor = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        hit = None
        for desc, types in svc_order():
            valid = cat.within(cat.valid_locs(types, EXISTING), anchor, 25)
            far = cat.in_metro(cat.valid_locs(types, EXISTING), m)
            if valid and set(far) - set(valid):
                hit = desc, types, valid
                break
        if hit:
            desc, types, valid = hit
            st = STATE_NAMES[cat.metros[m]["state"]]
            out.append(scenario(gid(), "geo", "suburb_city", EXISTING,
                                f"Existing patient who needs {desc}. They live in {city}, {st} "
                                f"(a suburb; do not mention the bigger nearby city).",
                                {**svc_field(desc), **loc_field(f"where they are: the town name {city}")},
                                offer(types, valid, max_miles=25, anchor=[round(anchor[0], 4), round(anchor[1], 4)]),
                                checks={"location_phrase": {"must": [city], "must_not": [cat.metros[m]["name"]]}},
                                truth={"metro": m}))
            break

    # zip of a site that offers the service
    zips_used = 0
    lids = sorted(cat.locs)
    rng.shuffle(lids)
    for lid in lids:
        if zips_used == 2:
            break
        for desc, types in svc_order():
            valid_all = cat.valid_locs(types, EXISTING)
            if lid not in valid_all:
                continue
            valid = cat.within(valid_all, cat.xy(lid), 10)
            z = cat.locs[lid]["zip"]
            out.append(scenario(gid(), "geo", "zip", EXISTING,
                                f"Existing patient who needs {desc}. Their ZIP code is {z}.",
                                {**svc_field(desc), **loc_field(f"where they are: the caller gives the ZIP code {z}")},
                                offer(types, valid, max_miles=10, anchor=list(cat.xy(lid))),
                                checks={"location_phrase": {"must": [z]}}, truth={"site": lid}))
            zips_used += 1
            break

    # zip3 only: an uncataloged ZIP whose first 3 digits belong to one metro
    zip3s: dict[str, set[str]] = defaultdict(set)
    for x in cat.locs.values():
        zip3s[x["zip"][:3]].add(x["metro_id"])
    all_zips = {x["zip"] for x in cat.locs.values()}
    cands = sorted(z for z, ms in zip3s.items() if len(ms) == 1)
    rng.shuffle(cands)
    for z3 in cands:
        m = next(iter(zip3s[z3]))
        done = False
        for desc, types in svc_order():
            valid = metro_strict(m, types, EXISTING)
            if not valid:
                continue
            z = next(f"{z3}{i:02d}" for i in range(1, 100) if f"{z3}{i:02d}" not in all_zips)
            out.append(scenario(gid(), "geo", "zip3", EXISTING,
                                f"Existing patient who needs {desc}. Their ZIP code is {z}.",
                                {**svc_field(desc), **loc_field(f"where they are: the caller gives the ZIP code {z}")},
                                offer(types, valid), checks={"location_phrase": {"must": [z]}},
                                truth={"metro": m, "zip3": z3}))
            done = True
            break
        if done:
            break

    # neighborhoods unique nationally, not a city name
    nb_count = Counter(x["neighborhood"] for x in cat.locs.values())
    city_names = {x["city"] for x in cat.locs.values()} | {m["name"] for m in cat.metros.values()}
    nbs = sorted(lid for lid, x in cat.locs.items()
                 if nb_count[x["neighborhood"]] == 1 and x["neighborhood"] not in city_names)
    rng.shuffle(nbs)
    made = Counter()
    for lid in nbs:
        x = cat.locs[lid]
        for desc, types in svc_order():
            valid_all = cat.valid_locs(types, EXISTING)
            near5 = cat.within(valid_all, cat.xy(lid), 5)
            near10 = cat.within(valid_all, cat.xy(lid), 10)
            if lid in valid_all and made["nbhd"] < 2:
                kind, instr, situ = ("neighborhood", f"the neighborhood they are in: {x['neighborhood']}",
                                     f"They live in the {x['neighborhood']} neighborhood of {x['city']}.")
            elif lid not in valid_all and near5 and made["near"] < 2:
                kind, instr, situ = ("near_neighborhood", f"that they are near / around {x['neighborhood']}",
                                     f"They are near the {x['neighborhood']} neighborhood of {x['city']} "
                                     f"(any clinic close by is fine).")
            else:
                continue
            made["nbhd" if kind == "neighborhood" else "near"] += 1
            out.append(scenario(gid(), "geo", kind, EXISTING, f"Existing patient who needs {desc}. {situ}",
                                {**svc_field(desc), **loc_field(f"where they are: {instr} (do not name the city)")},
                                offer(types, near10, max_miles=10, anchor=list(cat.xy(lid))),
                                checks={"location_phrase": {"must": [x["neighborhood"]],
                                                            "must_not": [x["city"], "clinic", "center"]}},
                                truth={"site": lid, "site_valid": lid in valid_all}))
            break
        if made["nbhd"] == 2 and made["near"] == 2:
            break

    # state only, state with several metros
    multi_states = sorted(s for s, ms in state_metros.items() if len(ms) >= 2 and STATE_NAMES[s] not in city_names)
    st = rng.choice(multi_states)
    desc, types = rng.choice(GEO_SERVICES)
    out.append(scenario(gid(), "geo", "state_only", EXISTING,
                        f"Existing patient who needs {desc}. They only say which state they live in: {STATE_NAMES[st]}.",
                        {**svc_field(desc), **loc_field(f"the state only: {STATE_NAMES[st]} (no city)")},
                        {"status": "ask", "ask_field": "metro"},
                        checks={"location_phrase": {"must": [STATE_NAMES[st]],
                                                    "must_not": [cat.metros[m]["name"] for m in state_metros[st]]}},
                        truth={"state": st}))

    # either/or: a town name that exists in two metros
    twins = sorted((c, sorted(ms)) for c, ms in
                   ((c, {mm for cc, mm in {(x["city"], x["metro_id"]) for x in cat.locs.values()} if cc == c})
                    for c in {x["city"] for x in cat.locs.values()}) if len(ms) == 2)
    rng.shuffle(twins)
    for city, ms in twins[:2]:
        chosen = rng.choice(ms)
        st_names = [STATE_NAMES[cat.locs[lid]["state"]] for lid in cat.locs if cat.locs[lid]["city"] == city]
        pts = [cat.xy(lid) for lid, x in cat.locs.items() if x["city"] == city and x["metro_id"] == chosen]
        anchor = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        for desc, types in svc_order():
            valid = cat.within(cat.valid_locs(types, EXISTING), anchor, 25)
            other = [lid for lid, x in cat.locs.items() if x["city"] == city and x["metro_id"] != chosen]
            other_pt = cat.xy(other[0])
            if valid and cat.within(cat.valid_locs(types, EXISTING), other_pt, 25):
                break
        chosen_state = STATE_NAMES[cat.locs[next(lid for lid in cat.locs if cat.locs[lid]["city"] == city
                                                 and cat.locs[lid]["metro_id"] == chosen)]["state"]]
        out.append(scenario(gid(), "geo", "either_or", EXISTING,
                            f"Existing patient who needs {desc}. They live in {city}, {chosen_state}. In the first "
                            f"sentence they only say the town name; when asked which one, they say the state.",
                            {**svc_field(desc),
                             "location_phrase": f"the town name {city} only (no state, no bigger city)",
                             "followup_location_phrase": f"their answer to 'which {city}?': the state {chosen_state}"},
                            offer(types, valid, max_miles=25, anchor=[round(anchor[0], 4), round(anchor[1], 4)]),
                            expect_t1={"status": "ask", "ask_field": "metro"},
                            checks={"location_phrase": {"must": [city], "must_not": sorted(set(st_names)) + [
                                cat.metros[m]["name"] for m in ms]},
                                    "followup_location_phrase": {"must": [chosen_state]}},
                            truth={"metros": ms, "chosen": chosen}))

    # Portland, Maine: no clinic in Maine; nearest metro must win from both Portland ME and the Maine centroid
    for desc, types in svc_order():
        valid = cat.valid_locs(types, EXISTING)

        def nearest_metro(pt):
            return cat.metro_of(min(valid, key=lambda lid: miles(pt, cat.xy(lid))))
        if nearest_metro(PORTLAND_ME) == nearest_metro(MAINE_CENTROID):
            m = nearest_metro(PORTLAND_ME)
            near = cat.in_metro(valid, m)
            d = min(miles(PORTLAND_ME, cat.xy(lid)) for lid in near)
            out.append(scenario(gid(), "geo", "portland_maine", EXISTING,
                                f"Existing patient who needs {desc}. They live in Portland, Maine.",
                                {**svc_field(desc), **loc_field("where they are: Portland, Maine (say both words)")},
                                {"status": "refuse", "refuse_reason": "none_nearby", "location_ids_subset": near},
                                checks={"location_phrase": {"must": ["Portland", "Maine"]}},
                                truth={"nearest_metro": m, "nearest_miles": round(d)}))
            break

    # misspelled metro name (fixed by hand, not by DeepSeek)
    miss = sorted(m for m in MISSPELLED if m in cat.metros)
    for m in rng.sample(miss, 2):
        for desc, types in svc_order():
            valid = metro_strict(m, types, EXISTING)
            if valid:
                break
        out.append(scenario(gid(), "geo", "misspelled_city", EXISTING,
                            f"Existing patient who needs {desc}. They are in {label(m)}.",
                            svc_field(desc), offer(types, valid), fixed={"location_phrase": MISSPELLED[m]},
                            truth={"metro": m}))

    # ---------------- symptoms -> specialty ----------------
    for i, (symptom, types, leak) in enumerate(SYMPTOMS, 1):
        m = pick_metro(lambda m: bool(cat.in_metro(cat.valid_locs(types, EXISTING), m)))
        valid = cat.in_metro(cat.valid_locs(types, EXISTING), m)
        name = label(m)
        out.append(scenario(f"nat-sym-{i:02d}", "symptom", "symptom", EXISTING,
                            f"Existing patient (has any referral needed) who {symptom}. They do not know which "
                            f"kind of doctor they need. They are in {name}.",
                            {"service_phrase": "the reason for the visit, described as the symptom in everyday words "
                                               "(no specialty or doctor type)",
                             **loc_field(f"the city: {name}")},
                            offer(types, valid),
                            checks={"service_phrase": {"must_not": leak}, "location_phrase": {"must": [name]}},
                            truth={"metro": m}))

    # ---------------- duplicate provider names across metros ----------------
    def distinctive(tid):
        nm = cat.types[tid]["name"].lower()
        return not any(w in nm for w in GENERIC_TYPE_WORDS) and cat.types[tid]["specialty"] not in ("General",)

    def namesakes_by_metro(ps, tid):
        per_metro: dict[str, list[str]] = defaultdict(list)
        for p in ps:
            if cat.valid_locs([tid], EXISTING, {p["id"]}):
                for mm in cat.prov_metros(p):
                    per_metro[mm].append(p["id"])
        return dict(sorted(per_metro.items()))

    def same_family(p, tid):
        """The type plus the provider's types whose name extends it ("Annual Physical - Established Patient")."""
        base = cat.types[tid]["name"].lower()
        return sorted({tid} | {t for t in p["appointment_type_ids"] if cat.types[t]["name"].lower().startswith(base)})

    shared_dup, other_dup = [], []
    for name, ps in sorted(cat.by_name.items()):
        if len(ps) < 2 or any(len(cat.prov_metros(p)) != 1 for p in ps)                 or len(set().union(*(cat.prov_metros(p) for p in ps))) < 2:
            continue
        shared = sorted(t for t in set.intersection(*(set(p["appointment_type_ids"]) for p in ps)) if distinctive(t))
        hit = next(((name, t, pm) for t in shared for pm in [namesakes_by_metro(ps, t)]
                    if len(pm) in (2, 3) and all(len(v) == 1 for v in pm.values())), None)
        (shared_dup if hit else other_dup).append(hit or (name, None, None))
    rng.shuffle(shared_dup)
    rng.shuffle(other_dup)
    for i, (name, tid, per_metro) in enumerate(shared_dup[:3] + other_dup[:3], 1):
        ps = cat.by_name[name]
        no_city = tid is not None
        if no_city:
            mm = rng.choice(sorted(per_metro))
            p = cat.provs[per_metro[mm][0]]
        else:
            solo = [p for p in ps if sum(cat.prov_metros(p) == cat.prov_metros(q) for q in ps) == 1]
            p = rng.choice(solo)
            mm = next(iter(cat.prov_metros(p)))
            tid = rng.choice([t for t in p["appointment_type_ids"]
                              if distinctive(t) and cat.valid_locs([t], EXISTING, {p["id"]})])
            per_metro = {m: [q["id"] for q in ps if m in cat.prov_metros(q)]
                         for m in sorted(set().union(*(cat.prov_metros(q) for q in ps)))}
        types = same_family(p, tid)
        valid = cat.in_metro(cat.valid_locs(types, EXISTING, {p["id"]}), mm)
        city = label(mm)
        first, last = name.replace("Dr. ", "").split(" ", 1)
        svc = cat.types[tid]["name"]
        base = {"service_phrase": f"the visit they want: {svc} (said naturally)",
                "provider_phrase": f"the doctor's full name: {name}"}
        checks = {"provider_phrase": {"must": [first, last.split()[-1]]}}
        exp = offer(types, valid, provider_ids=[p["id"]])
        if not no_city:
            out.append(scenario(f"nat-dup-{i:02d}", "dup_name_metro", "with_city", EXISTING,
                                f"Existing patient who wants {svc} with {name}, their doctor in {city}.",
                                {**base, **loc_field(f"the city: {city}")}, exp,
                                checks={**checks, "location_phrase": {"must": [city]}},
                                truth={"namesakes": per_metro}))
        else:
            out.append(scenario(f"nat-dup-{i:02d}", "dup_name_metro", "no_city", EXISTING,
                                f"Existing patient who wants {svc} with {name}. They do not say where at first; "
                                f"asked which city, they answer {city}.",
                                {**base, "followup_location_phrase": f"their answer to 'which city?': {city}"}, exp,
                                expect_t1={"status": "ask", "ask_field": "metro"},
                                checks={**checks, "followup_location_phrase": {"must": [city]}},
                                truth={"namesakes": per_metro}))

    # ---------------- capability gating by metro ----------------
    cap_types = list(CAPABILITY_TYPES)
    rng.shuffle(cap_types)
    made_cap = 0
    for tid in cap_types:
        if made_cap == 3:
            break
        t = cat.types[tid]
        cap = t["required_capability"]

        def gated(m):
            offering = {lid for p in cat.by_type[tid] for lid in p["location_ids"] if cat.metro_of(lid) == m}
            return bool(cat.in_metro(cat.valid_locs([tid], EXISTING), m)) and any(
                cap not in cat.locs[lid]["capabilities"] for lid in offering)
        m = pick_metro(gated)
        valid = cat.in_metro(cat.valid_locs([tid], EXISTING), m)
        city = label(m)
        made_cap += 1
        out.append(scenario(f"nat-cap-{made_cap:02d}", "capability_metro", "gated_offer", EXISTING,
                            f"Existing patient (with a referral) who needs {t['name']}. They are in {city}.",
                            {"service_phrase": f"the service: {t['name']} (said naturally)", **loc_field(f"the city: {city}")},
                            offer([tid], valid), checks={"location_phrase": {"must": [city]}},
                            truth={"metro": m, "capability": cap}))
    eye_metros = sorted({cat.metro_of(lid) for lid in cat.valid_locs(EYE_TYPES, EXISTING)})
    m = rng.choice(eye_metros)
    valid = cat.in_metro(cat.valid_locs(EYE_TYPES, EXISTING), m)
    made_cap += 1
    out.append(scenario(f"nat-cap-{made_cap:02d}", "capability_metro", "metro_only_service", EXISTING,
                        f"Existing patient who needs a routine eye exam. They are in {label(m)}.",
                        {**svc_field("a routine eye exam"), **loc_field(f"the city: {label(m)}")},
                        offer(EYE_TYPES, valid), checks={"location_phrase": {"must": [cat.metros[m]["name"]]}},
                        truth={"metro": m, "eye_metros": eye_metros}))
    # a named site that lacks the capability while a provider offering the type practices there
    name_count = Counter(x["name"] for x in cat.locs.values())
    for tid in cap_types:
        cap = cat.types[tid]["required_capability"]
        sites = sorted({lid for p in cat.by_type[tid] for lid in p["location_ids"]
                        if cap not in cat.locs[lid]["capabilities"] and name_count[cat.locs[lid]["name"]] == 1})
        sites = [s for s in sites if cat.in_metro(cat.valid_locs([tid], EXISTING), cat.metro_of(s))]
        if not sites:
            continue
        s = rng.choice(sites)
        m = cat.metro_of(s)
        site = cat.locs[s]
        key = site["name"].split(" ")[0]
        made_cap += 1
        out.append(scenario(f"nat-cap-{made_cap:02d}", "capability_metro", "named_site_lacks_capability", EXISTING,
                            f"Existing patient (with a referral) who needs {cat.types[tid]['name']} and insists on going "
                            f"to the clinic called {site['name']} in {site['city']}, which they always use.",
                            {"service_phrase": f"the service: {cat.types[tid]['name']} (said naturally)",
                             "location_phrase": f"that specific clinic by name: {site['name']} (not 'near' it)"},
                            {"status": "refuse", "refuse_reason": "location_type",
                             "location_ids_subset": cat.in_metro(cat.valid_locs([tid], EXISTING), m)},
                            checks={"location_phrase": {"must": [key], "must_not": ["near", "around", "close to"]}},
                            truth={"site": s, "capability": cap}))
        break

    # ---------------- new-patient rules ----------------
    nt = list(NEW_OK_TYPES)
    rng.shuffle(nt)
    k = 0
    for tid in nt:
        if k == 2:
            break

        def mixed(m):
            provs = {pid for _, pid, lid in cat.rows([tid], EXISTING) if cat.metro_of(lid) == m}
            ok = {pid for _, pid, lid in cat.rows([tid], NEW) if cat.metro_of(lid) == m}
            return bool(ok) and bool(provs - ok)
        cands = [m for m in metro_ids if mixed(m)]
        if not cands:
            continue
        m = pick_metro(mixed)
        rows = [(pid, lid) for _, pid, lid in cat.rows([tid], NEW) if cat.metro_of(lid) == m]
        k += 1
        svc = cat.types[tid]["name"]
        out.append(scenario(f"nat-new-{k:02d}", "new_patient", "accepting_only", NEW,
                            f"A brand-new patient who has never been seen at these clinics needs {svc}. "
                            f"They are in {label(m)}.",
                            {"service_phrase": f"the service: {svc} (said naturally)",
                             **loc_field(f"the city: {label(m)}")},
                            offer([tid], sorted({lid for _, lid in rows}), provider_ids=sorted({p for p, _ in rows})),
                            checks={"location_phrase": {"must": [cat.metros[m]["name"]]}}, truth={"metro": m}))
    closed = []
    for p in sorted(cat.provs.values(), key=lambda p: p["id"]):
        if p["accepting_new_patients"] or len(cat.by_name[p["name"]]) != 1 or len(cat.prov_metros(p)) != 1:
            continue
        for tid in p["appointment_type_ids"]:
            t = cat.types[tid]
            if t["new_patients_allowed"] and distinctive(tid) and cat.valid_locs([tid], EXISTING, {p["id"]}):
                closed.append((p, tid))
                break
    for p, tid in rng.sample(closed, 2):
        k += 1
        m = next(iter(cat.prov_metros(p)))
        first, last = p["name"].replace("Dr. ", "").split(" ", 1)
        out.append(scenario(f"nat-new-{k:02d}", "new_patient", "provider_not_accepting", NEW,
                            f"A brand-new patient wants {cat.types[tid]['name']} with {p['name']} in "
                            f"{label(m)} (a friend recommended this doctor).",
                            {"service_phrase": f"the service: {cat.types[tid]['name']} (said naturally)",
                             "provider_phrase": f"the doctor's full name: {p['name']}",
                             **loc_field(f"the city: {label(m)}")},
                            {"status": "refuse", "refuse_reason": "new_patient_provider"},
                            checks={"provider_phrase": {"must": [first, last.split()[-1]]},
                                    "location_phrase": {"must": [cat.metros[m]["name"]]}},
                            truth={"provider": p["id"], "type": tid}))
    tid = rng.choice(NO_NEW_TYPES)
    m = pick_metro(lambda m: bool(cat.in_metro(cat.valid_locs([tid], EXISTING), m)))
    k += 1
    out.append(scenario(f"nat-new-{k:02d}", "new_patient", "type_not_for_new", NEW,
                        f"A brand-new patient who has never been seen here wants {cat.types[tid]['name']}. "
                        f"They are in {label(m)}.",
                        {"service_phrase": f"the service: {cat.types[tid]['name']} (said naturally)",
                         **loc_field(f"the city: {label(m)}")},
                        {"status": "refuse", "refuse_reason": "new_patient_type"},
                        checks={"location_phrase": {"must": [cat.metros[m]["name"]]}}, truth={"type": tid}))

    # ---------------- no location given ----------------
    for i in range(1, 4):
        desc, types = GEO_SERVICES[[0, 2, 1][i - 1]]
        m = pick_metro(lambda m: bool(cat.in_metro(cat.valid_locs(types, EXISTING), m)))
        city = label(m)
        out.append(scenario(f"nat-noloc-{i:02d}", "no_location", "no_location", EXISTING,
                            f"Existing patient who needs {desc}. They do not say where they are at first; asked which "
                            f"city, they answer {city}.",
                            {**svc_field(desc), "followup_location_phrase": f"their answer to 'which city?': {city}"},
                            offer(types, cat.in_metro(cat.valid_locs(types, EXISTING), m)),
                            expect_t1={"status": "ask", "ask_field": "metro"},
                            checks={"followup_location_phrase": {"must": [city]}}, truth={"metro": m}))

    # ---------------- unoffered ----------------
    for i, tid in enumerate(rng.sample(UNOFFERED, 2), 1):
        assert not cat.by_type.get(tid)
        m = pick_metro(lambda m: True)
        out.append(scenario(f"nat-unoff-{i:02d}", "unoffered", "unoffered", EXISTING,
                            f"Existing patient who wants {cat.types[tid]['name'].lower()}. They are in "
                            f"{label(m)}.",
                            {"service_phrase": f"the service: {cat.types[tid]['name'].lower()} (said naturally)",
                             **loc_field(f"the city: {label(m)}")},
                            {"status": "refuse", "refuse_reason": "not_offered"},
                            checks={"location_phrase": {"must": [cat.metros[m]["name"]]}}, truth={"type": tid}))

    # ---------------- ring expansion: small metro without the service ----------------
    small = sorted(metro_ids, key=lambda m: (sum(1 for x in cat.locs.values() if x["metro_id"] == m), m))
    ring = None
    for m in small:
        anchor = cat.metro_xy(m)
        for types in ([EYE_TYPES] + [t for _, t in GEO_SERVICES] + [[tid] for tid in CAPABILITY_TYPES]):
            valid = cat.valid_locs(types, EXISTING)
            if not valid or cat.within(valid, anchor, 60):
                continue
            by_metro = defaultdict(lambda: math.inf)
            for lid in valid:
                by_metro[cat.metro_of(lid)] = min(by_metro[cat.metro_of(lid)], miles(anchor, cat.xy(lid)))
            (m1, d1), (_, d2) = sorted(by_metro.items(), key=lambda kv: kv[1])[:2]
            by_center = sorted(by_metro, key=lambda mm: miles(anchor, cat.metro_xy(mm)))
            if d2 >= 1.25 * d1 and by_center[0] == m1:
                ring = m, types, m1, d1
                break
        if ring:
            break
    m, types, m1, d1 = ring
    svc_name = cat.types[types[0]]["name"]
    out.append(scenario("nat-ring-01", "ring", "none_nearby", EXISTING,
                        f"Existing patient who needs {svc_name.lower()} (has any referral needed). They are in "
                        f"{label(m)}.",
                        {"service_phrase": f"the service: {svc_name.lower()} (said naturally)",
                         **loc_field(f"the city: {label(m)}")},
                        {"status": "refuse", "refuse_reason": "none_nearby",
                         "location_ids_subset": cat.in_metro(cat.valid_locs(types, EXISTING), m1)},
                        checks={"location_phrase": {"must": [cat.metros[m]["name"]]}},
                        truth={"metro": m, "nearest_metro": m1, "nearest_miles": round(d1)}))
    return out


# ---------------- DeepSeek ----------------
PROMPT = """You write realistic phone-call wording for a clinic scheduling test set.
For each scenario below, write what the caller would actually say for each listed field, the way a
note-taking assistant would record the caller's own words for that field (a short phrase, not a full
sentence; keep natural fillers like "I'm in", "near", "over by" when they fit). Vary the style across
scenarios: casual, terse, older caller, non-native speaker, rambling. Do not add facts that are not in
the scenario, and do not add any other place, doctor or service.

Return ONLY a JSON array, one object per scenario: {"id": ..., "<field>": "...", ...} with exactly the
fields listed for that scenario.

Scenarios:
"""


def ask_deepseek(batch: list[dict]) -> tuple[str, str]:
    lines = []
    for s in batch:
        fields = "; ".join(f"{k} = {v}" for k, v in s["fields"].items())
        lines.append(f"- id {s['id']}: {s['situation']} Fields: {fields}")
    prompt = PROMPT + "\n".join(lines)
    exe = shutil.which("cmdc.cmd") or shutil.which("cmdc")
    if not exe:
        sys.exit("cmdc not found on PATH")
    res = subprocess.run([exe, "-p", "-m", MODEL, "--no-session", "--skip-onboarding", "--max-turns", "1"],
                         input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if res.returncode != 0:
        sys.exit(f"cmdc failed ({res.returncode}): {res.stderr[:500]}\n--- stdout ---\n{res.stdout[:1500]}")
    return prompt, res.stdout


def parse_array(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.S)
    return json.loads(m.group(0)) if m else []


def phrase(scen: list[dict], only: list[str] | None = None, tag: str = "batch") -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    todo = [s for s in scen if s["fields"] and (only is None or s["id"] in only)]
    for i in range(0, len(todo), BATCH):
        path = RAW / f"{tag}_{i // BATCH + 1:02d}.json"
        if path.exists():
            print(f"skip {path.name} (exists)")
            continue
        batch = todo[i:i + BATCH]
        prompt, text = ask_deepseek(batch)
        path.write_text(json.dumps({"model": MODEL, "prompt": prompt, "response": text,
                                    "parsed": parse_array(text)}, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {path.name}: {len(batch)} scenarios")


def word_in(word: str, text: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word.lower())}(?![a-z0-9])", text.lower()) is not None


def violations(s: dict, got: dict, metro_names: list[str], state_names: list[str],
               aliases: dict[str, list[str]] | None = None) -> list[str]:
    """Rule violations of one phrasing. A `must` place name also accepts its catalog aliases."""
    errs = []
    aliases = aliases or {}
    for key in s["fields"]:
        if not isinstance(got.get(key), str) or not got[key].strip():
            errs.append(f"missing {key}")
    for key, rule in s["checks"].items():
        v = got.get(key) or s["fixed"].get(key) or ""
        errs += [f"{key} lacks {w!r}" for w in rule.get("must", ())
                 if not any(word_in(x, v) for x in [w, *aliases.get(w, ())])]
        errs += [f"{key} leaks {w!r}" for w in rule.get("must_not", ()) if w.strip() and w.lower().strip() in v.lower()]
    svc = got.get("service_phrase", "")
    errs += [f"service_phrase names place {w!r}" for w in metro_names + state_names if word_in(w, svc)]
    if "provider_phrase" in got and "location_phrase" not in s["fields"] and "location_phrase" not in s["fixed"]:
        errs += [f"provider_phrase names place {w!r}" for w in metro_names if word_in(w, got["provider_phrase"])]
    return errs


def merge_cases(scen: list[dict]) -> None:
    sha = json.loads(META.read_text(encoding="utf-8"))["sha256"]
    actual = hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    if sha != actual:
        sys.exit(f"catalog.meta.json sha {sha} != catalog.json {actual}")
    cat_raw = json.loads(CATALOG.read_text(encoding="utf-8"))
    metro_names = sorted({m["name"] for m in cat_raw["metros"]})
    aliases = {m["name"]: m["aliases"] for m in cat_raw["metros"]}
    aliases["Washington, DC"] = ["DC", "D.C.", "Washington DC", "Washington D.C."]
    state_names = sorted(STATE_NAMES.values())
    by_id = {s["id"]: s for s in scen}
    phrasings: dict[str, dict] = {}
    # A phrasing counts only if its file's prompt carried this scenario's current wording, so an edited
    # scenario can never inherit a stale phrasing. Later files (repairs) win when they pass the checks.
    for path in sorted(RAW.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for got in raw["parsed"]:
            s = by_id.get(got.get("id")) if isinstance(got, dict) else None
            if s is None or f"- id {s['id']}: {s['situation']} Fields: " not in raw["prompt"]:
                continue
            if got["id"] not in phrasings or not violations(s, got, metro_names, state_names, aliases):
                phrasings[got["id"]] = {**got, "_file": path.name}
    lines, drops = [], []
    for s in scen:
        got = phrasings.get(s["id"], {}) if s["fields"] else {}
        errs = violations(s, got, metro_names, state_names, aliases) if s["fields"] else []
        if errs:
            drops.append((s["id"], errs, {k: got.get(k) for k in s["fields"]}))
            continue
        first = {k: v for k, v in {**got, **s["fixed"]}.items()
                 if k in ("service_phrase", "provider_phrase", "location_phrase")}
        turns = [{"update": first}]
        if s["expect_t1"]:
            turns[0]["expect"] = s["expect_t1"]
            turns.append({"update": {"location_phrase": got["followup_location_phrase"]}})
        lines.append({"id": s["id"], "category": s["category"], "kind": s["kind"], "catalog_sha256": sha,
                      "patient": s["patient"], "turns": turns, "expected": s["expected"],
                      "phrasing_source": got.get("_file", "hand")})
    OUT.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in lines), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(lines)} cases; dropped {len(drops)}")
    for sid, errs, got in drops:
        print(f"  DROP {sid}: {'; '.join(errs)}  got={got}")
    print("per category:", dict(Counter(c["category"] for c in lines)))
    print("sha256", hashlib.sha256(OUT.read_bytes()).hexdigest())


def main() -> None:
    step = sys.argv[1] if len(sys.argv) > 1 else "scenarios"
    if step == "scenarios":
        cat = Catalog(json.loads(CATALOG.read_text(encoding="utf-8")))
        scen = build(cat)
        SCENARIOS.write_text(json.dumps(scen, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {SCENARIOS.relative_to(ROOT)}: {len(scen)} scenarios")
        print("per category:", dict(Counter(s["category"] for s in scen)))
        print("per kind:", dict(Counter(s["kind"] for s in scen)))
        for s in scen:
            e = s["expected"]
            print(f"  {s['id']:<14} {s['kind']:<28} {e['status']:<7} "
                  f"{e.get('refuse_reason') or e.get('ask_field') or ','.join(e.get('types_any', []))}"
                  f"  locs={len(e.get('location_ids_subset', []))} {s['truth']}")
        return
    scen = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if step == "phrase":
        phrase(scen)
    elif step == "repair":
        phrase(scen, only=sys.argv[2].split(","), tag=sys.argv[3] if len(sys.argv) > 3 else "repair")
    elif step == "merge":
        merge_cases(scen)
    else:
        sys.exit(f"unknown step {step}")


if __name__ == "__main__":
    main()
