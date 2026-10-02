"""Author eval/cases_heldout3.jsonl from the SF catalog, blind to the resolver and its results.

The target comes first: each scenario names a catalog appointment type (or the types a scheduler would
have to ask between) and the patient. Expectations come only from catalog queries below (the booking
rules in backend/data/README.md). DeepSeek then writes the caller's words from plain-language facts;
it never sees type names it should echo, aliases, resolver code or the expected answer.

  python eval/heldout3/build_cases.py scenarios          # specs + catalog -> eval/heldout3/scenarios.json
  python eval/heldout3/build_cases.py phrase             # DeepSeek via cmdc -> eval/heldout3/deepseek_raw/*.json
  python eval/heldout3/build_cases.py repair ID,ID [tag] # re-ask DeepSeek for drifted phrasings
  python eval/heldout3/build_cases.py merge              # scenarios + phrasings -> eval/cases_heldout3.jsonl

`phrase` skips batches whose raw file exists, so reruns reproduce the same file at no cost.
Relabels made after reading a phrasing are recorded in eval/heldout3/label_notes.md.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CATALOG = ROOT / "backend" / "data" / "catalog.json"
SCENARIOS = HERE / "scenarios.json"
RAW = HERE / "deepseek_raw"
OUT = ROOT / "eval" / "cases_heldout3.jsonl"

_spec = importlib.util.spec_from_file_location("national_build", ROOT / "eval" / "national" / "build_cases.py")
nb = sys.modules["national_build"] = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nb)

EXISTING = {"is_new": False, "has_referral": True}
NEW = {"is_new": True, "has_referral": True}

PROMPT = """You write realistic phone-call wording for a clinic scheduling test set.
For each scenario below, write what the caller would actually say for each listed field, the way a
note-taking assistant would record the caller's own words for that field (a short phrase, not a full
sentence). Vary the style across scenarios: casual, terse, older caller, non-native speaker, rambling.
Use only the facts in the scenario; do not add any other doctor, clinic or service. When the scenario
says the caller does not know the medical name, describe the need the way a layperson would, without
naming a medical specialty or a type of appointment. Do not use tools or open files; answer directly.

Return ONLY a JSON array, one object per scenario: {"id": ..., "<field>": "...", ...} with exactly the
fields listed for that scenario.

Scenarios:
"""

LAY = "They do not know the medical name for it."
SVC = {"service_phrase": "what the caller says they need, in their own words"}


@dataclass(frozen=True)
class Need:
    """A loose service phrasing. `target` is one type, or the types a human scheduler would ask between.
    `must`: groups of alternatives, one of each group must appear. `must_not`: words that would name the
    type. A term ending in * matches as a word prefix, any other term as a whole word or phrase."""
    id: str
    category: str
    kind: str
    target: tuple[str, ...]
    patient: dict
    situation: str
    must: tuple[tuple[str, ...], ...] = ()
    must_not: tuple[str, ...] = ()


@dataclass(frozen=True)
class Doctor:
    """A provider phrase. `clue` is what the caller says about the doctor; `match` is the catalog fact
    that clue selects (gender is read from first names, listed by hand)."""
    id: str
    kind: str
    type_id: str
    service_phrase: str
    patient: dict
    surname: str
    clue: str
    match: Callable[[dict], bool]
    must: tuple[tuple[str, ...], ...]
    must_not: tuple[str, ...] = ()
    site: str | None = None


def first(p: dict) -> str:
    return p["name"].split()[1]


NEEDS = [
    # ---- named tests, in lay words ----
    Need("h3-01", "heldout3_type", "test", ("appt_022",), EXISTING,
         f"Existing patient whose heart doctor wants them to do the test where they walk on a treadmill while "
         f"their heart is watched. {LAY}", must_not=("stress",)),
    Need("h3-02", "heldout3_type", "test", ("appt_024",), EXISTING,
         f"Existing patient whose doctor wants them to wear a small heart monitor at home for a day or two; "
         f"they need to come in to have it put on. {LAY}", must_not=("holter",)),
    Need("h3-03", "heldout3_type", "test", ("appt_037",), EXISTING,
         f"Existing patient whose nerve doctor ordered a test that records their brain waves with wires stuck "
         f"to the scalp. {LAY}", must_not=("eeg", "electroencephalo*")),
    Need("h3-04", "heldout3_type", "test", ("appt_081",), EXISTING,
         "Existing patient who wants the scratch test on their back to find out exactly what they are "
         "allergic to.", must=(("scratch*",),), must_not=("skin test*",)),
    Need("h3-05", "heldout3_type", "test", ("appt_073",), EXISTING,
         f"Existing patient whose doctor wants their blood taken first thing in the morning before they have "
         f"eaten anything. {LAY}", must_not=("fasting", "fast")),
    Need("h3-06", "heldout3_type", "test", ("appt_064",), EXISTING,
         "Existing patient whose doctor ordered an MRI of their lower back.", must=(("mri",),),
         must_not=("spine", "spinal")),
    # ---- symptoms ----
    Need("h3-07", "heldout3_type", "symptom", ("appt_036",), EXISTING,
         f"Existing patient whose hands have started shaking when they hold a cup, and it is getting worse. "
         f"{LAY}", must_not=("neuro*",)),
    Need("h3-08", "heldout3_type", "symptom", ("appt_050",), EXISTING,
         f"Existing patient whose voice has been hoarse for a month and whose throat feels like something is "
         f"stuck in it. {LAY}", must_not=("ent", "otolaryng*", "ear, nose")),
    Need("h3-09", "heldout3_type", "symptom", ("appt_008",), NEW,
         f"A new patient who just cut their hand badly in the kitchen, thinks it needs stitches and wants to "
         f"be seen today. {LAY}", must_not=("urgent",)),
    Need("h3-10", "heldout3_type", "symptom", ("appt_017",), NEW,
         f"A new patient: a parent whose 3-year-old daughter has had a fever since last night and keeps "
         f"tugging at her ear. {LAY}", must_not=("pediatric*", "sick")),
    Need("h3-11", "heldout3_type", "symptom", ("appt_034",), EXISTING,
         f"Existing patient who broke their wrist six weeks ago; the bone doctor wants to see how it is "
         f"healing. {LAY}", must_not=("fracture*",)),
    Need("h3-12", "heldout3_type", "symptom", ("appt_030",), EXISTING,
         f"Existing patient who was given a prescription cream for their pimples and breakouts and needs to "
         f"check back in about it. {LAY}", must_not=("acne", "dermatolog*")),
    Need("h3-13", "heldout3_type", "symptom", ("appt_014",), EXISTING,
         f"Existing patient who is having their gallbladder taken out next month; the surgeon needs their "
         f"regular doctor to clear them for surgery first. {LAY}",
         must_not=("pre-op*", "preop*", "evaluation")),
    Need("h3-14", "heldout3_type", "symptom", ("appt_056",), EXISTING,
         f"Existing patient whose regular doctor says their hormone levels are off and wants them to see a "
         f"specialist for hormones and glands. {LAY}", must_not=("endocrin*", "thyroid")),
    # ---- everyday descriptions of a visit ----
    Need("h3-15", "heldout3_type", "colloquial", ("appt_029",), EXISTING,
         "Existing patient who wants a mole on their back cut off.", must_not=("removal", "remove*")),
    Need("h3-16", "heldout3_type", "colloquial", ("appt_033",), EXISTING,
         f"Existing patient who gets a cortisone shot in their arthritic knee every few months and is due for "
         f"the next one. {LAY}", must_not=("injection*", "joint")),
    Need("h3-17", "heldout3_type", "colloquial", ("appt_038",), EXISTING,
         "Existing patient who already sees the nerve doctor here for their migraines and needs their next "
         "check-in about them.", must=(("migraine*",),), must_not=("headache*", "neuro*")),
    Need("h3-18", "heldout3_type", "colloquial", ("appt_060",), EXISTING,
         "Existing patient who sees a counselor here every week to talk through their anxiety and needs to "
         "book the next weekly talk session.", must_not=("therap*", "psychiatr*")),
    Need("h3-19", "heldout3_type", "colloquial", ("appt_061",), EXISTING,
         "Existing patient whose psychiatrist prescribes their antidepressant; they need a short visit to "
         "check the dose and keep the prescription going.", must_not=("management",)),
    Need("h3-20", "heldout3_type", "colloquial", ("appt_009",), EXISTING,
         "Existing patient who takes about eight different pills from different doctors and wants their "
         "regular doctor to go through the whole list with them.", must_not=("review",)),
    Need("h3-21", "heldout3_type", "colloquial", ("appt_015",), NEW,
         f"A new patient: a parent whose son is turning 18 months and needs his regular checkup for that "
         f"age. {LAY}", must_not=("well-child", "well child", "well-baby", "well baby")),
    Need("h3-22", "heldout3_type", "colloquial", ("appt_003",), EXISTING,
         "Existing patient on Medicare who wants the once-a-year visit that Medicare covers for seniors; they "
         "mention Medicare.", must=(("medicare",),), must_not=("wellness", "physical")),
    Need("h3-23", "heldout3_type", "colloquial", ("appt_055",), EXISTING,
         f"Existing patient whose stomach doctor wants to put a camera down their throat to look at their "
         f"stomach. {LAY}", must_not=("endoscop*", "egd", "scope")),
    # ---- abbreviations and slang ----
    Need("h3-24", "heldout3_type", "abbreviation", ("appt_010",), NEW,
         "A new patient who needs a tetanus booster because it has been more than ten years; they call it "
         "'Tdap'.", must=(("tdap",),), must_not=("vaccin*", "immuniz*")),
    Need("h3-25", "heldout3_type", "abbreviation", ("appt_072",), EXISTING,
         "Existing patient whose doctor ordered a CBC blood test; they say 'CBC'.", must=(("cbc",),),
         must_not=("lab work", "draw")),
    Need("h3-26", "heldout3_type", "slang", ("appt_062",), EXISTING,
         "An older existing patient whose doctor ordered pictures of their lungs; they call it 'chest films'.",
         must=(("films",),), must_not=("x-ray*", "xray*", "x ray*")),
    Need("h3-27", "heldout3_type", "slang", ("appt_065",), EXISTING,
         "Existing patient whose doctor ordered the big magnet scan of their knee; they call it 'the magnet "
         "scan' and never say MRI.", must=(("magnet*",), ("knee",)), must_not=("mri",)),
    Need("h3-28", "heldout3_type", "slang", ("appt_067",), EXISTING,
         "Existing patient whose doctor ordered a sonogram of their thyroid; they say 'sonogram'.",
         must=(("sonogram",),), must_not=("ultrasound",)),
    Need("h3-29", "heldout3_type", "slang", ("appt_041",), EXISTING,
         "Existing patient who wants her yearly checkup with the gynecologist; she calls it her 'yearly with "
         "the gyno'.", must=(("gyno",),), must_not=("well-woman", "well woman", "pap")),
    # ---- two types genuinely fit: a scheduler asks ----
    Need("h3-30", "heldout3_type", "ask_two", ("appt_063", "appt_064", "appt_065"), EXISTING,
         "Existing patient whose doctor ordered an MRI. They do not say which body part.", must=(("mri",),),
         must_not=("brain", "spine", "knee", "back", "head", "neck", "leg", "shoulder", "hip")),
    Need("h3-31", "heldout3_type", "ask_two", ("appt_054", "appt_055"), EXISTING,
         "Existing patient whose stomach doctor said they need a scope, but they do not remember whether it "
         "goes down the throat or in from below.", must=(("scope",),),
         must_not=("colonoscop*", "endoscop*", "colon")),
    Need("h3-32", "heldout3_type", "ask_two", ("appt_027", "appt_028"), EXISTING,
         "Existing patient who wants their yearly all-over skin check to look for signs of skin cancer.",
         must_not=("screening", "full body", "full-body", "exam")),
    Need("h3-33", "heldout3_type", "ask_two", ("appt_074", "appt_075"), EXISTING,
         "Existing dental patient who is due for their regular six-month checkup at the dentist.",
         must_not=("cleaning", "clean", "exam")),
    # ---- nobody here offers it ----
    Need("h3-34", "heldout3_type", "not_offered", ("appt_070",), EXISTING,
         "Existing patient who just had shoulder surgery and needs to start PT; they say 'PT'.",
         must=(("pt",),), must_not=("physical therapy",)),
    Need("h3-35", "heldout3_type", "not_offered", ("appt_077",), EXISTING,
         f"Existing patient who keeps getting up at night to pee; their doctor told them to get their "
         f"prostate looked at by a specialist. {LAY}", must_not=("urolog*",)),
    # ---- booking rules ----
    Need("h3-x01", "heldout3_policy", "new_patient_type", ("appt_033",), NEW,
         "A brand-new patient who has never been seen here wants a cortisone shot in their knee; an outside "
         "doctor referred them.", must_not=("injection*", "joint")),
    Need("h3-x02", "heldout3_policy", "new_patient_type", ("appt_062",), NEW,
         "A brand-new patient who has never been seen here needs an X-ray of their ankle; an outside doctor "
         "referred them."),
    Need("h3-x03", "heldout3_policy", "referral", ("appt_053",), {"is_new": False, "has_referral": False},
         "Existing patient with no referral who has had heartburn and acid coming up almost every night for "
         "months; their regular doctor's pills did not help, so now they want a specialist for the stomach. "
         "They use the word 'specialist' but do not know the specialty's name.", must=(("specialist",),),
         must_not=("gastro*", "gi")),
    Need("h3-x04", "heldout3_policy", "new_patient_provider", ("appt_056",), NEW,
         f"A brand-new patient whose outside doctor found hormone problems in their blood tests and referred "
         f"them to a specialist for hormones and glands. {LAY}", must_not=("endocrin*", "thyroid")),
    Need("h3-x05", "heldout3_policy", "new_patient_provider", ("appt_040",), NEW,
         "A brand-new patient who just moved here and wants to start seeing a gynecologist at this clinic."),
]

DOCTORS = [
    Doctor("h3-p01", "specialty", "appt_061", "medication management", EXISTING, "Smith",
           "Dr. Smith, and that it is the psychiatrist (no first name)",
           lambda p: p["specialty"] == "Psychiatry", (("smith",), ("psychiatr*",))),
    Doctor("h3-p02", "specialty", "appt_059", "psychiatric evaluation", NEW, "Smith",
           "Dr. Smith, and that it is the psychiatrist (no first name)",
           lambda p: p["specialty"] == "Psychiatry", (("smith",), ("psychiatr*",))),
    Doctor("h3-p03", "title", "appt_007", "sick visit", EXISTING, "Hernandez",
           "Dr. Hernandez, and that it is the nurse practitioner (no first name)",
           lambda p: p["title"] == "NP", (("hernandez",), ("nurse practitioner", "np", "n.p."))),
    Doctor("h3-p04", "title", "appt_007", "sick visit", NEW, "Hernandez",
           "Dr. Hernandez, and that it is the nurse practitioner (no first name)",
           lambda p: p["title"] == "NP", (("hernandez",), ("nurse practitioner", "np", "n.p."))),
    Doctor("h3-p05", "language", "appt_003", "annual wellness visit", EXISTING, "Hernandez",
           "Dr. Hernandez, the one who speaks Spanish (no first name)",
           lambda p: "Spanish" in p["languages"], (("hernandez",), ("spanish",))),
    Doctor("h3-p06", "full_name", "appt_023", "an EKG", EXISTING, "Ramirez",
           "the doctor's full name, Dr. Linda Ramirez, and nothing else about her",
           lambda p: first(p) == "Linda", (("linda",), ("ramirez",))),
    Doctor("h3-p07", "language", "appt_007", "sick visit", EXISTING, "Sato",
           "Dr. Michael Sato, the one who speaks Vietnamese",
           lambda p: first(p) == "Michael" and "Vietnamese" in p["languages"],
           (("michael",), ("sato",), ("vietnamese",))),
    Doctor("h3-p08", "gender", "appt_011", "flu shot", EXISTING, "Garcia",
           "Dr. Garcia, making clear the doctor is a man, e.g. 'he' or 'the male one' (no first name)",
           lambda p: first(p) in {"Carlos"},
           (("garcia",), ("he", "him", "his", "man", "male", "guy", "gentleman"))),
    Doctor("h3-p09", "gender", "appt_002", "annual physical", EXISTING, "Patel",
           "Dr. Patel, making clear the doctor is a woman, e.g. 'she' or 'the lady doctor' (no first name)",
           lambda p: first(p) in {"Fatima", "Olivia", "Sofia"},
           (("patel",), ("she", "her", "woman", "lady", "female"))),
    Doctor("h3-p10", "site", "appt_004", "follow-up visit", EXISTING, "Nguyen",
           "Dr. Nguyen, the one at the Midtown clinic (no first name)",
           lambda p: "loc_005" in p["location_ids"], (("nguyen",), ("midtown",)), site="loc_005"),
    Doctor("h3-p11", "site", "appt_009", "medication review", EXISTING, "Chen",
           "Dr. Chen, the one at the North Gate clinic (no first name)",
           lambda p: "loc_003" in p["location_ids"], (("chen",), ("north gate",)), site="loc_003"),
    Doctor("h3-p12", "language", "appt_078", "pulmonology consultation", EXISTING, "Nguyen",
           "Dr. Nguyen, the one who speaks Mandarin (no first name)",
           lambda p: "Mandarin" in p["languages"], (("nguyen",), ("mandarin",))),
    Doctor("h3-p13", "full_name", "appt_015", "well-child visit", NEW, "Garcia",
           "the doctor's full name, Dr. Maria Garcia, and nothing else about her",
           lambda p: first(p) == "Maria", (("maria",), ("garcia",))),
    Doctor("h3-p14", "site", "appt_011", "flu shot", EXISTING, "Sato",
           "Dr. Michael Sato, the one at the Sunset clinic",
           lambda p: first(p) == "Michael" and "loc_006" in p["location_ids"],
           (("michael",), ("sato",), ("sunset",)), site="loc_006"),
]


class Catalog:
    """The SF booking rules as set queries. No resolver code."""

    def __init__(self, raw: dict):
        self.types = {t["id"]: t for t in raw["appointment_types"]}
        self.provs = {p["id"]: p for p in raw["providers"]}
        self.locs = {loc["id"]: loc for loc in raw["locations"]}

    def offering(self, tid: str) -> list[dict]:
        return [p for p in self.provs.values() if tid in p["appointment_type_ids"]]

    def rows(self, tid: str, patient: dict, provider_ids=None, site=None) -> list[tuple[str, str, str]]:
        t = self.types[tid]
        if t["requires_referral"] and not patient.get("has_referral"):
            return []
        if patient.get("is_new") and not t["new_patients_allowed"]:
            return []
        out = []
        for p in self.offering(tid):
            if provider_ids is not None and p["id"] not in provider_ids:
                continue
            if patient.get("is_new") and not p["accepting_new_patients"]:
                continue
            for lid in p["location_ids"]:
                cap = t.get("required_capability")
                if (site is None or lid == site) and (not cap or cap in self.locs[lid]["capabilities"]):
                    out.append((tid, p["id"], lid))
        return out

    def refusal(self, tid: str, patient: dict) -> str | None:
        """The one booking rule that blocks this type for this patient, if any."""
        t = self.types[tid]
        reasons = [code for code, hit in (
            ("not_offered", not self.offering(tid)),
            ("referral", t["requires_referral"] and not patient.get("has_referral")),
            ("new_patient_type", patient.get("is_new") and not t["new_patients_allowed"]),
            ("new_patient_provider", patient.get("is_new") and t["new_patients_allowed"] and self.offering(tid)
             and not any(p["accepting_new_patients"] for p in self.offering(tid)))) if hit]
        assert len(reasons) <= 1, f"{tid}: several rules apply {reasons}"
        return reasons[0] if reasons else None


def offer(cat: Catalog, tid: str, patient: dict, provider_ids=None, site=None) -> dict:
    rows = cat.rows(tid, patient, provider_ids, site)
    assert rows, f"{tid} not bookable for {patient} {provider_ids} {site}"
    exp = {"status": "offer", "type_id": tid}
    provs = sorted({p for _, p, _ in rows})
    if provider_ids is not None or provs != sorted(p["id"] for p in cat.offering(tid)):
        exp["provider_ids"] = provs
    locs = sorted({lid for _, _, lid in rows})
    if site or cat.types[tid].get("required_capability"):
        exp["location_ids"] = locs
    return exp


def need_scenario(cat: Catalog, n: Need) -> dict:
    if len(n.target) > 1:
        for tid in n.target:
            assert cat.rows(tid, n.patient), f"{n.id}: option {tid} not bookable"
        exp = {"status": "ask", "ask_field": "service", "ask_options": sorted(n.target)}
    else:
        tid = n.target[0]
        code = cat.refusal(tid, n.patient)
        wanted = n.kind if n.kind == "not_offered" or n.category == "heldout3_policy" else None
        assert code == wanted, f"{n.id}: catalog says {code}, spec says {wanted}"
        exp = {"status": "refuse", "refuse_code": code} if code else offer(cat, tid, n.patient)
    return {"id": n.id, "category": n.category, "kind": n.kind, "patient": n.patient,
            "situation": n.situation, "fields": dict(SVC), "fixed": {},
            "checks": {"service_phrase": {"must": [list(g) for g in n.must], "must_not": list(n.must_not)}},
            "expect_t1": None, "expected": exp,
            "truth": {"target": list(n.target), "type_names": [cat.types[t]["name"] for t in n.target]}}


def doctor_scenario(cat: Catalog, d: Doctor) -> dict:
    named = [p for p in cat.provs.values() if p["name"].split()[-1] == d.surname]
    fits = [p for p in named if d.match(p)]
    offering = [p for p in fits if d.type_id in p["appointment_type_ids"]
                and (d.site is None or d.site in p["location_ids"])]
    valid = sorted(p["id"] for p in offering if cat.rows(d.type_id, d.patient, {p["id"]}, d.site))
    if len(valid) == 1:
        exp = offer(cat, d.type_id, d.patient, set(valid), d.site)
    elif len(valid) > 1:
        exp = {"status": "ask", "ask_field": "provider", "ask_options": valid}
    else:
        assert offering and cat.types[d.type_id]["new_patients_allowed"], f"{d.id}: nobody fits"
        exp = {"status": "refuse", "refuse_code": "new_patient_provider"}
    first_names = sorted({first(p) for p in named})
    return {"id": d.id, "category": "heldout3_provider", "kind": d.kind, "patient": d.patient,
            "situation": f"Existing patient who wants a {d.service_phrase} with a specific doctor." if not
            d.patient["is_new"] else f"A brand-new patient who wants a {d.service_phrase} with a specific doctor.",
            "fields": {"provider_phrase": f"how the caller names the doctor: {d.clue}"},
            "fixed": {"service_phrase": d.service_phrase},
            "checks": {"provider_phrase": {"must": [list(g) for g in d.must],
                                           "must_not": [f.lower() for f in first_names
                                                        if not any(f.lower() in g for g in d.must)]}},
            "expect_t1": None, "expected": exp,
            "truth": {"surname": [p["id"] for p in named], "fits_clue": [p["id"] for p in fits],
                      "offers_type": [p["id"] for p in offering], "valid": valid}}


def term_in(term: str, text: str) -> bool:
    if term.endswith("*"):
        return re.search(rf"(?<![a-z0-9]){re.escape(term[:-1].lower())}", text.lower()) is not None
    return nb.word_in(term, text)


def violations(s: dict, got: dict) -> list[str]:
    errs = [f"missing {k}" for k in s["fields"] if not isinstance(got.get(k), str) or not got[k].strip()]
    for key, rule in s["checks"].items():
        v = got.get(key) or s["fixed"].get(key) or ""
        errs += [f"{key} lacks one of {g}" for g in rule["must"] if not any(term_in(t, v) for t in g)]
        errs += [f"{key} names {t!r}" for t in rule["must_not"] if term_in(t, v)]
    return errs


def build() -> list[dict]:
    cat = Catalog(json.loads(CATALOG.read_text(encoding="utf-8")))
    return [need_scenario(cat, n) for n in NEEDS] + [doctor_scenario(cat, d) for d in DOCTORS]


def merge(scen: list[dict]) -> None:
    by_id = {s["id"]: s for s in scen}
    phrasings: dict[str, dict] = {}
    for path in sorted(RAW.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for got in raw["parsed"]:
            s = by_id.get(got.get("id")) if isinstance(got, dict) else None
            if s is None or f"- id {s['id']}: {s['situation']} Fields: " not in raw["prompt"]:
                continue
            if got["id"] not in phrasings or not violations(s, got):
                phrasings[got["id"]] = {**got, "_file": path.name}
    lines, drops = [], []
    for s in scen:
        got = phrasings.get(s["id"], {})
        errs = violations(s, got)
        if errs:
            drops.append((s["id"], errs, {k: got.get(k) for k in s["fields"]}))
            continue
        said = {**got, **s["fixed"]}
        update = {k: said[k] for k in ("service_phrase", "provider_phrase") if k in said}
        lines.append({"id": s["id"], "category": s["category"], "kind": s["kind"], "patient": s["patient"],
                      "turns": [{"update": update}], "expected": s["expected"], "phrasing_source": got["_file"]})
    OUT.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in lines), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(lines)} cases; dropped {len(drops)}")
    for sid, errs, got in drops:
        print(f"  DROP {sid}: {'; '.join(errs)}  got={got}")
    print("per category:", dict(Counter(c["category"] for c in lines)))
    print("per kind:", dict(Counter(c["kind"] for c in lines)))
    print("sha256", hashlib.sha256(OUT.read_bytes()).hexdigest())


def main() -> None:
    args = sys.argv[1:]
    step = args[0] if args else "scenarios"
    if step == "scenarios":
        scen = build()
        HERE.mkdir(parents=True, exist_ok=True)
        SCENARIOS.write_text(json.dumps(scen, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {SCENARIOS.relative_to(ROOT)}: {len(scen)} scenarios")
        print("per category:", dict(Counter(s["category"] for s in scen)))
        for s in scen:
            e = s["expected"]
            print(f"  {s['id']:<7} {s['kind']:<20} {e['status']:<7} "
                  f"{e.get('type_id') or e.get('refuse_code') or e.get('ask_options')} "
                  f"{ {k: v for k, v in e.items() if k in ('provider_ids', 'location_ids')} }")
        return
    scen = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if step == "phrase":
        nb.phrase(scen, RAW, preamble=PROMPT)
    elif step == "repair":
        nb.phrase(scen, RAW, only=args[1].split(","), tag=args[2] if len(args) > 2 else "repair", preamble=PROMPT)
    elif step == "merge":
        merge(scen)
    else:
        sys.exit(f"unknown step {step}")


if __name__ == "__main__":
    main()
