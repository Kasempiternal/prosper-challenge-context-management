"""Author the SF held-out sets (eval/cases_heldout3.jsonl .. eval/cases_heldout5.jsonl) from the SF
catalog, blind to the resolver and its results.

The target comes first: each scenario names a catalog appointment type (or the types a scheduler would
have to ask between) and the patient. Expectations come only from catalog queries below (the booking
rules in backend/data/README.md). DeepSeek then writes the caller's words from plain-language facts;
it never sees type names it should echo, aliases, resolver code or the expected answer.

  python eval/heldout3/build_cases.py [--set heldout3|heldout4|heldout5] scenarios # -> eval/<set>/scenarios.json
  python eval/heldout3/build_cases.py [--set ...] phrase             # DeepSeek via cmdc -> eval/<set>/deepseek_raw/*.json
  python eval/heldout3/build_cases.py [--set ...] repair ID,ID [tag] # re-ask DeepSeek for drifted phrasings
  python eval/heldout3/build_cases.py [--set ...] merge              # scenarios + phrasings -> eval/cases_<set>.jsonl

A set is a Profile: its single-turn needs and doctor phrases, plus two-turn picks (heldout4 on), where
turn 1 must ask and the caller's answer picks one option. `phrase` skips batches whose raw file exists,
so reruns reproduce the same file at no cost. Relabels made after reading a phrasing are recorded in
eval/<set>/label_notes.md.
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
CATALOG = ROOT / "backend" / "data" / "catalog.json"

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


@dataclass(frozen=True)
class Pick:
    """Two turns. Turn 1 (`first`) must ask; asked `question`, the caller says `answer`, which updates
    `key` and picks `choice` (a type id after a service ask, a provider id after a provider ask). `site`:
    a clinic the answer names. `must`/`must_not` check the answer's wording."""
    id: str
    first: Need | Doctor
    question: str
    answer: str
    key: str
    choice: str
    must: tuple[tuple[str, ...], ...]
    must_not: tuple[str, ...] = ()
    site: str | None = None


def first(p: dict) -> str:
    return p["name"].split()[1]


NEEDS3 = [
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

DOCTORS3 = [
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

# ---------------- heldout4: round 4, authored blind; targets and clues mostly unused by heldout3 ----------------
NO_REFERRAL = {"is_new": False, "has_referral": False}
PA = ("pa", "p.a.", "physician assistant", "physician's assistant")

NEEDS4 = [
    # ---- named tests, in lay words ----
    Need("h4-01", "heldout4_type", "test", ("appt_051",), EXISTING,
         "Existing patient who wants the test where you sit in a quiet booth with headphones on and raise your "
         "hand each time you hear a beep, to see how well they hear. They do not know the test's name and do "
         "not call it a 'hearing test'.", must_not=("audiogram", "audiolog*", "hearing test")),
    Need("h4-02", "heldout4_type", "test", ("appt_068",), EXISTING,
         f"Existing patient who is due for the yearly screening where each breast is pressed between two plates "
         f"to look for cancer. {LAY}", must_not=("mammo*",)),
    Need("h4-03", "heldout4_type", "test", ("appt_043",), EXISTING,
         f"Existing patient who needs the swab test of the cervix that checks for early signs of cervical "
         f"cancer. {LAY}", must_not=("pap", "smear")),
    Need("h4-04", "heldout4_type", "test", ("appt_079",), EXISTING,
         f"Existing patient whose lung doctor ordered the breathing test where you blow as hard and as long as "
         f"you can into a tube. {LAY}", must_not=("spirometr*", "lung function", "pulmonary function", "pft")),
    # ---- symptoms ----
    Need("h4-05", "heldout4_type", "symptom", ("appt_020",), EXISTING,
         f"Existing patient who gets a tight, heavy feeling in the chest when walking uphill; their regular "
         f"doctor wants them to see a heart specialist, and they say 'heart specialist'. {LAY}",
         must=(("heart",),), must_not=("cardio*",)),
    Need("h4-06", "heldout4_type", "symptom", ("appt_026",), EXISTING,
         f"Existing patient with an itchy red rash on both arms for two months that drugstore creams have not "
         f"helped; their regular doctor wants them to see a skin specialist. {LAY}",
         must_not=("dermatolog*", "derm")),
    Need("h4-07", "heldout4_type", "symptom", ("appt_032",), EXISTING,
         f"Existing patient whose knee has been clicking and giving way on the stairs for months; their regular "
         f"doctor referred them to a bone and joint specialist. {LAY}", must_not=("ortho*",)),
    Need("h4-08", "heldout4_type", "symptom", ("appt_078",), EXISTING,
         f"Existing patient who has been short of breath and coughing for three months; their regular doctor "
         f"wants them to see a lung specialist, and they say 'lung specialist'. {LAY}",
         must=(("lung*",),), must_not=("pulmon*",)),
    Need("h4-09", "heldout4_type", "symptom", ("appt_080",), EXISTING,
         f"Existing patient who sneezes nonstop and has itchy, watery eyes every spring, and the drugstore pills "
         f"no longer help; their regular doctor told them to see a specialist about it. {LAY}",
         must_not=("allerg*", "immunolog*")),
    Need("h4-10", "heldout4_type", "symptom", ("appt_052",), EXISTING,
         f"Existing patient who has had a stuffed-up nose and pressure behind the cheeks and forehead for "
         f"months, with one infection after another; their regular doctor wants a specialist to look into it. "
         f"{LAY}", must_not=("sinus*", "ent", "otolaryng*", "ear, nose")),
    Need("h4-11", "heldout4_type", "symptom", ("appt_007",), EXISTING,
         f"Existing patient who has had a sore throat, a fever and body aches for three days and wants to see "
         f"their regular doctor this week. {LAY}", must_not=("sick visit", "urgent*")),
    # ---- everyday descriptions of a visit ----
    Need("h4-12", "heldout4_type", "colloquial", ("appt_006",), EXISTING,
         "Existing patient who wants to go over their recent test results with their doctor on a video call "
         "from home instead of coming in.", must_not=("telehealth", "follow-up", "follow up", "followup")),
    Need("h4-13", "heldout4_type", "colloquial", ("appt_013",), EXISTING,
         "Existing patient who is going to Kenya next month and needs to find out which shots and pills they "
         "need before the trip.", must_not=("vaccin*", "immuniz*", "consult*")),
    Need("h4-14", "heldout4_type", "colloquial", ("appt_019",), EXISTING,
         "Existing patient: a parent whose 14-year-old daughter needs a doctor to check her over and sign the "
         "form so she can play on the soccer team.", must_not=("physical",)),
    Need("h4-15", "heldout4_type", "colloquial", ("appt_044",), EXISTING,
         "Existing patient who wants to talk with a doctor about starting birth control, maybe the pill or an "
         "IUD.", must_not=("contracept*",)),
    Need("h4-16", "heldout4_type", "colloquial", ("appt_031",), EXISTING,
         "Existing patient who wants to ask about getting Botox for the wrinkles on their forehead.",
         must=(("botox",),), must_not=("cosmetic", "consult*")),
    Need("h4-17", "heldout4_type", "colloquial", ("appt_057",), EXISTING,
         "Existing patient who sees the hormone specialist here every three months to keep their blood sugar "
         "under control and is due for the next check; they call it 'my sugar'.", must=(("sugar",),),
         must_not=("diabet*", "endocrin*", "a1c")),
    Need("h4-18", "heldout4_type", "colloquial", ("appt_042",), EXISTING,
         "Existing patient who just found out she is about eight weeks pregnant and needs her first checkup for "
         "the pregnancy.", must_not=("prenatal", "obstetric*", "ob", "gyn*")),
    Need("h4-19", "heldout4_type", "colloquial", ("appt_016",), NEW,
         f"A new patient: a parent whose baby was born last week and needs the baby's first checkup since "
         f"leaving the hospital. {LAY}", must_not=("newborn",)),
    # ---- abbreviations and slang ----
    Need("h4-20", "heldout4_type", "abbreviation", ("appt_023",), EXISTING,
         "Existing patient whose doctor wants a quick tracing of their heartbeat; they call it an 'ECG'.",
         must=(("ecg",),), must_not=("ekg", "electrocardio*")),
    Need("h4-21", "heldout4_type", "abbreviation", ("appt_021",), EXISTING,
         "Existing patient whose heart doctor ordered an 'echo' of their heart; they say 'echo'.",
         must=(("echo",),), must_not=("echocardio*", "ultrasound")),
    Need("h4-22", "heldout4_type", "abbreviation", ("appt_069",), EXISTING,
         "Existing patient whose doctor ordered a 'DEXA scan' to check whether their bones are thinning; they "
         "say 'DEXA'.", must=(("dexa",),), must_not=("density",)),
    Need("h4-23", "heldout4_type", "slang", ("appt_025",), EXISTING,
         "Existing patient who has a small device in their chest that keeps their heartbeat steady and is due "
         "for its regular check; they call it their 'pacer check' and never say the device's full name.",
         must=(("pacer",),), must_not=("pacemaker",)),
    Need("h4-24", "heldout4_type", "slang", ("appt_066",), EXISTING,
         "Existing patient whose doctor ordered a 'CAT scan' of their belly; they say 'CAT scan'.",
         must=(("cat scan",),), must_not=("ct",)),
    Need("h4-25", "heldout4_type", "slang", ("appt_012",), EXISTING,
         "Existing patient who wants the latest COVID booster; they call it 'the covid jab'.",
         must=(("covid",),), must_not=("vaccin*",)),
    # ---- two types genuinely fit: a scheduler asks ----
    Need("h4-26", "heldout4_type", "ask_two", ("appt_060", "appt_061"), EXISTING,
         "Existing patient who sees a psychiatrist here regularly and wants to book their next usual "
         "appointment. They do not say what the appointment is for.", must=(("psychiatr*",),),
         must_not=("therap*", "medic*", "meds", "pill*", "prescri*", "evaluat*", "counsel*", "talk*", "dose")),
    Need("h4-27", "heldout4_type", "ask_two", ("appt_072", "appt_073"), EXISTING,
         "Existing patient whose doctor ordered blood work. They do not say what it is for or whether they "
         "were told to fast.", must=(("blood",),),
         must_not=("fast*", "eat*", "empty stomach", "cbc", "a1c", "cholesterol", "sugar", "glucose", "morning")),
    Need("h4-28", "heldout4_type", "ask_two", ("appt_063", "appt_066"), EXISTING,
         "Existing patient whose doctor ordered a scan of their head. They do not know what kind of scan it is "
         "and do not name one.", must=(("head", "brain"),), must_not=("mri", "ct", "cat", "magnet*", "x-ray*")),
    Need("h4-29", "heldout4_type", "ask_two", ("appt_000", "appt_001"), NEW,
         "A brand-new patient who wants to become a patient here and have a first visit with a regular doctor. "
         "They have no particular problem and do not ask for a physical or a checkup.",
         must_not=("physical", "checkup", "check-up", "check up", "wellness", "sick", "annual", "yearly")),
    # ---- nobody here offers it ----
    Need("h4-30", "heldout4_type", "not_offered", ("appt_045",), EXISTING,
         "Existing patient who wants their eyes checked because they think they need new glasses."),
    Need("h4-31", "heldout4_type", "not_offered", ("appt_049",), EXISTING,
         f"Existing patient whose vision has gone cloudy, like looking through a foggy window; their regular "
         f"doctor wants a specialist to check whether the lens of the eye needs surgery. {LAY}",
         must_not=("cataract*",)),
    # ---- booking rules ----
    Need("h4-x01", "heldout4_policy", "new_patient_type", ("appt_064",), NEW,
         "A brand-new patient who has never been seen here needs an MRI of their lower back; an outside doctor "
         "referred them."),
    Need("h4-x02", "heldout4_policy", "new_patient_type", ("appt_023",), NEW,
         "A brand-new patient who has never been seen here wants an EKG of their heart."),
    Need("h4-x03", "heldout4_policy", "referral", ("appt_036",), NO_REFERRAL,
         "Existing patient with no referral who wants to see a neurologist about numbness and tingling in their "
         "feet; they say 'neurologist'.", must=(("neurolog*",),)),
    Need("h4-x04", "heldout4_policy", "new_patient_provider", ("appt_041",), NEW,
         "A brand-new patient who wants her yearly women's exam with the gynecologist here."),
    Need("h4-x05", "heldout4_policy", "new_patient_provider", ("appt_044",), NEW,
         "A brand-new patient who wants to talk with the gynecologist here about birth control options."),
]

DOCTORS4 = [
    Doctor("h4-p01", "specialty", "appt_030", "acne follow-up", EXISTING, "Kim",
           "Dr. Kim, and that it is the skin doctor (no first name)",
           lambda p: p["specialty"] == "Dermatology", (("kim",), ("skin", "dermatolog*", "derm"))),
    Doctor("h4-p02", "specialty", "appt_034", "fracture follow-up", EXISTING, "Rodriguez",
           "Dr. Rodriguez, and that it is the bone doctor (no first name)",
           lambda p: p["specialty"] == "Orthopedics", (("rodriguez",), ("bone*", "ortho*"))),
    Doctor("h4-p03", "specialty", "appt_078", "pulmonology consultation", EXISTING, "Nguyen",
           "Dr. Nguyen, and that it is the lung doctor (no first name)",
           lambda p: p["specialty"] == "Pulmonology", (("nguyen",), ("lung*", "pulmon*"))),
    Doctor("h4-p04", "title", "appt_009", "medication review", EXISTING, "Sato",
           "Dr. Sato, and that it is the physician assistant (no first name)",
           lambda p: p["title"] == "PA", (("sato",), PA)),
    Doctor("h4-p05", "title", "appt_002", "annual physical", NEW, "Sato",
           "Dr. Sato, and that it is the physician assistant (no first name)",
           lambda p: p["title"] == "PA", (("sato",), PA)),
    Doctor("h4-p06", "language", "appt_002", "annual physical", EXISTING, "Patel",
           "Dr. Patel, the one who speaks Hindi (no first name)",
           lambda p: "Hindi" in p["languages"], (("patel",), ("hindi",))),
    Doctor("h4-p07", "language", "appt_007", "sick visit", EXISTING, "Patel",
           "Dr. Patel, the one who speaks Spanish (no first name)",
           lambda p: "Spanish" in p["languages"], (("patel",), ("spanish",))),
    Doctor("h4-p08", "full_name", "appt_003", "annual wellness visit", EXISTING, "Sato",
           "the doctor's full name, Dr. Michael Sato, and nothing else about him",
           lambda p: first(p) == "Michael", (("michael",), ("sato",))),
    Doctor("h4-p09", "full_name", "appt_001", "new patient visit", NEW, "Sato",
           "the doctor's full name, Dr. Michael Sato, and nothing else about him",
           lambda p: first(p) == "Michael", (("michael",), ("sato",))),
    Doctor("h4-p10", "gender", "appt_078", "pulmonology consultation", EXISTING, "Chen",
           "Dr. Chen, making clear the doctor is a woman, e.g. 'she' or 'the lady doctor' (no first name)",
           lambda p: first(p) in {"Emily", "Nina"}, (("chen",), ("she", "her", "woman", "lady", "female"))),
    Doctor("h4-p11", "gender", "appt_032", "orthopedic consultation", EXISTING, "Nguyen",
           "Dr. Nguyen, making clear the doctor is a man, e.g. 'he' or 'the male one' (no first name)",
           lambda p: first(p) in {"Daniel", "Carlos"},
           (("nguyen",), ("he", "him", "his", "man", "male", "guy", "gentleman"))),
    Doctor("h4-p12", "site", "appt_011", "flu shot", EXISTING, "Patel",
           "Dr. Patel, the one at the Sunset clinic (no first name)",
           lambda p: "loc_006" in p["location_ids"], (("patel",), ("sunset",)), site="loc_006"),
    Doctor("h4-p13", "site", "appt_027", "skin cancer screening", EXISTING, "Smith",
           "Dr. Smith, the one at the Richmond clinic (no first name)",
           lambda p: "loc_007" in p["location_ids"], (("smith",), ("richmond",)), site="loc_007"),
    Doctor("h4-p14", "site", "appt_015", "well-child visit", EXISTING, "Garcia",
           "Dr. Garcia, the one at the Mission Bay clinic (no first name)",
           lambda p: "loc_000" in p["location_ids"], (("garcia",), ("mission bay",)), site="loc_000"),
]

PICKS4 = [
    Pick("h4-m01", Doctor("h4-m01", "site", "appt_079", "spirometry", EXISTING, "Nguyen",
                          "Dr. Nguyen, the one at the Richmond clinic (no first name)",
                          lambda p: "loc_007" in p["location_ids"], (("nguyen",), ("richmond",)), site="loc_007"),
         "which Dr. Nguyen at Richmond they mean, Dr. Maria Nguyen or Dr. Daniel Nguyen",
         "Maria; they give only her first name", "provider_phrase", "prov_024", (("maria",),), ("daniel",)),
    Pick("h4-m02", Doctor("h4-m02", "surname", "appt_002", "annual physical", EXISTING, "Nguyen",
                          "Dr. Nguyen, and nothing else about the doctor (no first name)",
                          lambda p: True, (("nguyen",),)),
         "which Dr. Nguyen they mean, Dr. Nina Nguyen or Dr. Elizabeth Nguyen",
         "the one at the Downtown clinic; they name only the clinic, not the doctor", "location_phrase",
         "prov_016", (("downtown",),), ("nina", "elizabeth", "nguyen"), site="loc_004"),
    Pick("h4-m03", Doctor("h4-m03", "full_name", "appt_007", "sick visit", EXISTING, "Garcia",
                          "the doctor's full name, Dr. Maria Garcia, and nothing else about her",
                          lambda p: first(p) == "Maria", (("maria",), ("garcia",))),
         "which Dr. Maria Garcia they mean, the pediatrician or the nurse practitioner",
         "the nurse practitioner", "provider_phrase", "prov_003",
         (("nurse practitioner", "np", "n.p."),), ("pediatric*", "kid*", "child*")),
    Pick("h4-m04", Need("h4-m04", "heldout4_multiturn", "service_pick", ("appt_066", "appt_067"), EXISTING,
                        "Existing patient whose doctor ordered a scan of their belly. They do not know what kind of "
                        "scan it is and do not name one.", must=(("belly", "stomach", "abdom*", "tummy"),),
                        must_not=("ct", "cat", "ultrasound", "sonogram", "mri", "x-ray*", "xray*")),
         "whether the scan is a CT scan or an ultrasound", "the ultrasound", "service_phrase", "appt_067",
         (("ultrasound", "sonogram"),), ("ct", "cat")),
    Pick("h4-m05", Need("h4-m05", "heldout4_multiturn", "service_pick", ("appt_015", "appt_016"), EXISTING,
                        "Existing patient: a parent who wants to book their baby's checkup. They do not say how "
                        "old the baby is.", must=(("baby",),),
                        must_not=("newborn", "new", "born", "week*", "month*", "year*", "old")),
         "how old the baby is", "two weeks old", "service_phrase", "appt_016",
         (("two weeks", "2 weeks", "two-week*", "2-week*"),), ("newborn", "month*")),
    Pick("h4-m06", Need("h4-m06", "heldout4_multiturn", "service_pick", ("appt_054", "appt_055"), EXISTING,
                        "Existing patient whose stomach doctor said they need a scope. They do not say which kind.",
                        must=(("scope",),),
                        must_not=("colonoscop*", "endoscop*", "colon", "throat", "mouth", "below", "bottom", "rear")),
         "whether it is the scope that goes down the throat or the one from below", "the one down the throat",
         "service_phrase", "appt_055", (("throat", "mouth"),), ("colon*", "below", "bottom")),
]

# ---------------- heldout5: round 5, authored blind; adds symptoms whose body part points to the wrong
# specialty (symptom_misdirect) and acute symptoms with their onset (acute) ----------------
NP = ("nurse practitioner", "np", "n.p.")
FEMALE = ("she", "her", "woman", "lady", "female")
MALE = ("he", "him", "his", "man", "male", "guy", "gentleman")

NEEDS5 = [
    # ---- named tests, in lay words ----
    Need("h5-01", "heldout5_type", "test", ("appt_021",), EXISTING,
         f"Existing patient whose heart doctor ordered a moving picture of their heart made with sound waves, "
         f"where gel goes on the chest and a wand is moved over it. {LAY}", must=(("heart",),),
         must_not=("echo*", "cardiogram")),
    Need("h5-02", "heldout5_type", "test", ("appt_023",), EXISTING,
         f"Existing patient whose doctor wants the quick test where about a dozen sticky pads go on the chest, "
         f"arms and legs and the heartbeat is printed out on paper; it takes a few minutes. {LAY}",
         must_not=("ekg", "ecg", "electro*", "holter", "monitor")),
    Need("h5-03", "heldout5_type", "test", ("appt_063",), EXISTING,
         f"Existing patient whose doctor ordered pictures of their brain, taken while they lie still inside a "
         f"long, loud tube for about forty minutes. {LAY}", must=(("brain", "head"),),
         must_not=("mri", "magnet*", "ct", "cat")),
    # ---- symptoms ----
    Need("h5-04", "heldout5_type", "symptom", ("appt_039",), EXISTING,
         f"Existing patient: an adult child calling for their 78-year-old mother, who keeps forgetting recent "
         f"conversations and got lost driving home from the grocery store; her regular doctor wants a "
         f"specialist to check her. {LAY}",
         must_not=("memory", "dementia", "alzheimer*", "neuro*", "cognitive")),
    Need("h5-05", "heldout5_type", "symptom", ("appt_026",), EXISTING,
         f"Existing patient whose hair has been coming out in clumps in the shower for two months, leaving bald "
         f"patches; their regular doctor wants them to see a specialist about it. {LAY}",
         must_not=("derm*", "alopecia")),
    # ---- the body part named points to a different specialty ----
    Need("h5-06", "heldout5_type", "symptom_misdirect", ("appt_080",), EXISTING,
         f"Existing patient whose eyes itch, burn and water every time the pollen count is high or they visit "
         f"their sister's cat. Their regular doctor says the eyes themselves are healthy, that it is a reaction "
         f"to the pollen and the cat, and wants them to see a specialist for that. {LAY}",
         must=(("eye*",),), must_not=("allerg*", "immun*", "hay fever")),
    Need("h5-07", "heldout5_type", "symptom_misdirect", ("appt_026",), EXISTING,
         f"Existing patient who has had thick, red, scaly patches on both elbows and knees that flake and itch "
         f"for months; their regular doctor wants them to see a specialist. {LAY}",
         must=(("elbow*", "knee*"),),
         must_not=("derm*", "psoriasis", "eczema", "skin doctor", "skin specialist")),
    Need("h5-08", "heldout5_type", "symptom_misdirect", ("appt_053",), EXISTING,
         f"Existing patient who has had a burning pain in the middle of the chest after big meals and when lying "
         f"down at night for two months. Their regular doctor checked their heart, found it healthy, says the "
         f"problem is in their digestion and wants a specialist to look into it. {LAY}",
         must=(("chest",),),
         must_not=("heartburn", "acid", "reflux", "gerd", "gastro*", "gi", "stomach doctor")),
    Need("h5-09", "heldout5_type", "symptom_misdirect", ("appt_020",), EXISTING,
         f"Existing patient whose ankles swell up every evening and who gets out of breath lying flat in bed; "
         f"their regular doctor listened to their heart and wants a specialist to check it. {LAY}",
         must=(("ankle*",), ("heart",)), must_not=("cardio*", "heart doctor", "heart specialist")),
    # ---- acute symptoms, with their onset ----
    Need("h5-10", "heldout5_type", "acute", ("appt_017",), EXISTING,
         f"Existing patient: a parent whose 6-year-old son has had a stomach ache and has thrown up twice since "
         f"this morning, and they want a doctor to look at him. They say how old he is. {LAY}",
         must=(("morning",), ("stomach", "tummy", "belly"), ("6", "six")),
         must_not=("pediatric*", "sick visit", "urgent*")),
    Need("h5-11", "heldout5_type", "acute", ("appt_008",), EXISTING,
         f"Existing patient who rolled their ankle stepping off a curb about an hour ago; it is swollen, they "
         f"can barely put weight on it, and they want to be seen today. {LAY}",
         must=(("ankle",), ("hour*", "today")), must_not=("urgent*", "sprain*", "x-ray*", "ortho*")),
    Need("h5-12", "heldout5_type", "acute", ("appt_007",), EXISTING,
         f"Existing patient who woke up this morning with one eye red, goopy and crusted shut, and wants to see "
         f"their regular doctor in the next day or so. {LAY}",
         must=(("eye",), ("morning", "today", "woke")),
         must_not=("pink eye", "pinkeye", "conjunctivitis", "sick visit", "urgent*")),
    Need("h5-13", "heldout5_type", "acute", ("appt_075",), EXISTING,
         f"Existing dental patient who has had a throbbing toothache in a back tooth since Sunday, and the "
         f"cheek on that side is getting puffy. {LAY}", must=(("tooth", "toothache", "teeth", "molar"),),
         must_not=("dental", "exam", "cleaning", "emergency")),
    # ---- everyday descriptions of a visit ----
    Need("h5-14", "heldout5_type", "colloquial", ("appt_010",), EXISTING,
         "Existing patient in their sixties who wants the shot that prevents shingles; they say 'shingles'.",
         must=(("shingles",),), must_not=("vaccin*", "immuniz*")),
    Need("h5-15", "heldout5_type", "colloquial", ("appt_035",), EXISTING,
         "Existing patient who is having a hip replacement next month and needs the sit-down with the bone "
         "surgeon here to go over the operation beforehand.", must=(("hip",),),
         must_not=("pre-surg*", "presurg*", "pre-op*", "preop*", "consult*", "ortho*")),
    Need("h5-16", "heldout5_type", "colloquial", ("appt_054",), EXISTING,
         "Existing patient who just turned 45; their regular doctor said it is time for the colon cancer "
         "screening where they look inside the bowel, and they need to set it up with the stomach doctor.",
         must_not=("colonoscop*", "scope", "endoscop*")),
    Need("h5-17", "heldout5_type", "colloquial", ("appt_078",), EXISTING,
         "Existing patient whose chest X-ray showed a spot on their lung; their regular doctor wants them to see "
         "the lung specialist about it.", must=(("lung*",),), must_not=("pulmon*",)),
    # ---- abbreviations and slang ----
    Need("h5-18", "heldout5_type", "abbreviation", ("appt_079",), EXISTING,
         "Existing patient whose lung doctor ordered a 'PFT'; they say 'PFT'.", must=(("pft",),),
         must_not=("spirometr*", "lung function", "pulmonary function", "breathing test")),
    Need("h5-19", "heldout5_type", "abbreviation", ("appt_055",), EXISTING,
         "Existing patient whose stomach doctor wants to do an 'EGD'; they say 'EGD'.", must=(("egd",),),
         must_not=("endoscop*", "scope", "camera")),
    Need("h5-20", "heldout5_type", "abbreviation", ("appt_003",), EXISTING,
         "Existing patient aged 70 who wants to book their 'AWV'. They say only 'AWV', do not spell it out and do "
         "not mention their insurance.",
         must=(("awv",),), must_not=("wellness", "medicare", "annual")),
    Need("h5-21", "heldout5_type", "slang", ("appt_058",), EXISTING,
         "Existing patient who takes Synthroid and sees the specialist here to keep the dose right; they are "
         "due for the regular check, call it 'my Synthroid check' and never name the gland.",
         must=(("synthroid",),), must_not=("thyroid", "endocrin*", "hormone*")),
    Need("h5-22", "heldout5_type", "slang", ("appt_059",), EXISTING,
         "Existing patient who has never seen a psychiatrist and wants a first appointment with one to figure "
         "out what is going on with their mood; they call a psychiatrist a 'shrink'.", must=(("shrink",),),
         must_not=("psychiatr*", "evaluat*", "therap*", "counsel*")),
    Need("h5-23", "heldout5_type", "slang", ("appt_012",), EXISTING,
         "Existing patient who wants the latest Moderna booster; they say 'Moderna' and do not name the disease.",
         must=(("moderna",),), must_not=("covid", "corona*", "vaccin*")),
    # ---- two types genuinely fit: a scheduler asks ----
    Need("h5-24", "heldout5_type", "ask_two", ("appt_010", "appt_012"), EXISTING,
         "Existing patient who says they are due for 'a booster shot'. They do not say what it protects against.",
         must=(("booster",),),
         must_not=("covid", "corona*", "tetanus", "tdap", "flu", "shingles", "moderna", "pfizer", "whooping",
                   "pneumo*")),
    Need("h5-25", "heldout5_type", "ask_two", ("appt_057", "appt_058"), EXISTING,
         "Existing patient who sees the hormone specialist here regularly and wants to book their usual "
         "follow-up. They do not say what it is for.", must=(("hormone*", "endocrin*", "gland*"),),
         must_not=("sugar", "diabet*", "thyroid", "insulin", "a1c", "synthroid", "first", "new", "consult*")),
    Need("h5-26", "heldout5_type", "ask_two", ("appt_064", "appt_066"), EXISTING,
         "Existing patient whose doctor ordered a scan of their lower back. They do not know what kind of scan it "
         "is and do not name one.", must=(("back",),),
         must_not=("mri", "ct", "cat", "magnet*", "x-ray*", "xray*")),
    Need("h5-27", "heldout5_type", "ask_two", ("appt_067", "appt_068"), EXISTING,
         "Existing patient who found a lump in her breast; her doctor ordered imaging of the breast, but she "
         "does not know which kind and does not name one.", must=(("breast",),),
         must_not=("mammo*", "ultrasound", "sonogram", "mri", "x-ray*")),
    # ---- nobody here offers it ----
    Need("h5-28", "heldout5_type", "not_offered", ("appt_047",), EXISTING,
         "Existing patient whose mother went blind from high pressure inside her eyes; their regular doctor "
         "wants them to have their eye pressure checked by an eye specialist.", must_not=("glaucoma",)),
    Need("h5-29", "heldout5_type", "not_offered", ("appt_071",), EXISTING,
         "Existing patient who has been doing weekly exercise sessions for their back with a physical therapist "
         "at another place and wants to continue those weekly sessions here; they call it 'rehab'.",
         must=(("rehab*",),), must_not=("physical therapy", "pt", "therapist")),
    # ---- booking rules ----
    Need("h5-x01", "heldout5_policy", "new_patient_type", ("appt_025",), NEW,
         "A brand-new patient who has never been seen here has a pacemaker and needs it checked; an outside "
         "doctor referred them."),
    Need("h5-x02", "heldout5_policy", "new_patient_type", ("appt_081",), NEW,
         "A brand-new patient who has never been seen here wants the skin tests that show exactly what they are "
         "allergic to; an outside doctor referred them."),
    Need("h5-x03", "heldout5_policy", "referral", ("appt_032",), NO_REFERRAL,
         "Existing patient with no referral whose shoulder has hurt for months and who wants to see an "
         "orthopedist; they say 'orthopedist'.", must=(("orthop*",),)),
    Need("h5-x04", "heldout5_policy", "new_patient_provider", ("appt_042",), NEW,
         "A brand-new patient who just moved here, is about twelve weeks pregnant and needs a doctor here to "
         "look after the pregnancy.", must_not=("prenatal",)),
    Need("h5-x05", "heldout5_policy", "new_patient_provider", ("appt_056",), NEW,
         "A brand-new patient whose outside doctor found an overactive thyroid and referred them to a specialist "
         "for it; they mention the thyroid.", must=(("thyroid",),), must_not=("endocrin*",)),
]

DOCTORS5 = [
    Doctor("h5-p01", "specialty", "appt_007", "sick visit", EXISTING, "Nguyen",
           "Dr. Nguyen, and that it is the internal medicine doctor (no first name)",
           lambda p: p["specialty"] == "Internal Medicine", (("nguyen",), ("internal",))),
    Doctor("h5-p02", "specialty", "appt_003", "annual wellness visit", EXISTING, "Sato",
           "Dr. Sato, and that it is the internal medicine doctor (no first name)",
           lambda p: p["specialty"] == "Internal Medicine", (("sato",), ("internal",))),
    Doctor("h5-p03", "specialty", "appt_003", "annual wellness visit", NEW, "Sato",
           "Dr. Sato, and that it is the internal medicine doctor (no first name)",
           lambda p: p["specialty"] == "Internal Medicine", (("sato",), ("internal",))),
    Doctor("h5-p04", "title", "appt_011", "flu shot", EXISTING, "Nguyen",
           "Dr. Nguyen, and that it is the physician assistant (no first name)",
           lambda p: p["title"] == "PA", (("nguyen",), PA)),
    Doctor("h5-p05", "title", "appt_002", "annual physical", EXISTING, "Ramirez",
           "Dr. Ramirez, and that it is the nurse practitioner (no first name)",
           lambda p: p["title"] == "NP", (("ramirez",), NP)),
    Doctor("h5-p06", "language", "appt_061", "medication management", EXISTING, "Smith",
           "Dr. Smith, the one who speaks Portuguese (no first name)",
           lambda p: "Portuguese" in p["languages"], (("smith",), ("portuguese",))),
    Doctor("h5-p07", "language", "appt_078", "pulmonology consultation", EXISTING, "Nguyen",
           "Dr. Nguyen, the one who speaks Spanish (no first name)",
           lambda p: "Spanish" in p["languages"], (("nguyen",), ("spanish",))),
    Doctor("h5-p08", "language", "appt_029", "mole removal", EXISTING, "Chen",
           "Dr. Chen, the one who speaks Arabic (no first name)",
           lambda p: "Arabic" in p["languages"], (("chen",), ("arabic",))),
    Doctor("h5-p09", "full_name", "appt_059", "psychiatric evaluation", NEW, "Sato",
           "the doctor's full name, Dr. Olivia Sato, and nothing else about her",
           lambda p: first(p) == "Olivia", (("olivia",), ("sato",))),
    Doctor("h5-p10", "full_name", "appt_013", "travel vaccination consult", EXISTING, "Patel",
           "the doctor's full name, Dr. Kenji Patel, and nothing else about him",
           lambda p: first(p) == "Kenji", (("kenji",), ("patel",))),
    Doctor("h5-p11", "gender", "appt_011", "flu shot", EXISTING, "Patel",
           "Dr. Patel, making clear the doctor is a man, e.g. 'he' or 'the male one' (no first name)",
           lambda p: first(p) in {"Andre", "Kenji"}, (("patel",), MALE)),
    Doctor("h5-p12", "gender", "appt_007", "sick visit", EXISTING, "Hernandez",
           "Dr. Hernandez, making clear the doctor is a man, e.g. 'he' or 'the male one' (no first name)",
           lambda p: first(p) in {"Tomas"}, (("hernandez",), MALE)),
    Doctor("h5-p13", "site", "appt_059", "psychiatric evaluation", EXISTING, "Smith",
           "Dr. Smith, the one at the Mission District clinic (no first name)",
           lambda p: "loc_001" in p["location_ids"], (("smith",), ("mission district",)), site="loc_001"),
    Doctor("h5-p14", "site", "appt_023", "EKG", EXISTING, "Ramirez",
           "Dr. Ramirez, the one at the Midtown clinic (no first name)",
           lambda p: "loc_005" in p["location_ids"], (("ramirez",), ("midtown",)), site="loc_005"),
]

PICKS5 = [
    Pick("h5-m01", Doctor("h5-m01", "site", "appt_020", "cardiology consultation", EXISTING, "Chen",
                          "Dr. Chen, the one at the Richmond clinic (no first name)",
                          lambda p: "loc_007" in p["location_ids"], (("chen",), ("richmond",)), site="loc_007"),
         "which Dr. Chen at Richmond they mean, Dr. David Chen or Dr. Emily Chen",
         "the woman; they give no name", "provider_phrase", "prov_046", (FEMALE,), ("david", "emily")),
    Pick("h5-m02", Doctor("h5-m02", "specialty", "appt_011", "flu shot", EXISTING, "Garcia",
                          "Dr. Garcia, and that it is the family medicine doctor, said as a specialty, not 'my "
                          "family doctor' (no first name)",
                          lambda p: p["specialty"] == "Family Medicine", (("garcia",), ("family medicine",))),
         "which Dr. Garcia they mean, Dr. Maria Garcia or Dr. Carlos Garcia",
         "Carlos; they give only his first name", "provider_phrase", "prov_008", (("carlos",),), ("maria",)),
    Pick("h5-m03", Doctor("h5-m03", "surname", "appt_007", "sick visit", EXISTING, "Chen",
                          "Dr. Chen, and nothing else about the doctor (no first name)",
                          lambda p: True, (("chen",),)),
         "which Dr. Chen they mean: Dr. Wei Chen, Dr. Michael Chen or Dr. Lucas Chen",
         "the one who sees kids; they give no name", "provider_phrase", "prov_012",
         (("kid*", "child*", "children", "pediatric*"),), ("wei", "michael", "lucas")),
    Pick("h5-m04", Doctor("h5-m04", "surname", "appt_002", "annual physical", EXISTING, "Hernandez",
                          "Dr. Hernandez, and nothing else about the doctor (no first name)",
                          lambda p: True, (("hernandez",),)),
         "which Dr. Hernandez they mean, Dr. Tomas Hernandez or Dr. Patricia Hernandez",
         "the nurse practitioner", "provider_phrase", "prov_025", (NP,), ("tomas", "patricia")),
    Pick("h5-m05", Need("h5-m05", "heldout5_multiturn", "service_pick", ("appt_072", "appt_073"), EXISTING,
                        "Existing patient whose doctor ordered blood work. They do not say what it is for or "
                        "whether they were told to fast.", must=(("blood",),),
                        must_not=("fast*", "eat*", "empty stomach", "cbc", "a1c", "cholesterol", "sugar",
                                  "glucose", "morning", "food")),
         "whether it is a regular blood draw or a fasting blood test",
         "the doctor told them not to eat anything after midnight before it; they do not say 'fasting'",
         "service_phrase", "appt_073", (("eat*", "food", "midnight", "empty stomach"),), ("fast*",)),
    Pick("h5-m06", Need("h5-m06", "heldout5_multiturn", "service_pick", ("appt_074", "appt_075"), EXISTING,
                        "Existing dental patient who wants to book their regular visit with the dentist. They do not "
                        "say whether it is a cleaning or an exam.", must=(("dentist", "dental", "teeth", "tooth"),),
                        must_not=("clean*", "exam*", "check*", "x-ray*")),
         "whether it is a cleaning or an exam", "the cleaning", "service_phrase", "appt_074",
         (("clean*",),), ("exam*",)),
    Pick("h5-m07", Need("h5-m07", "heldout5_multiturn", "service_pick", ("appt_060", "appt_061"), EXISTING,
                        "Existing patient who sees a psychiatrist here regularly and wants to book their next usual "
                        "appointment. They do not say what the appointment is for.", must=(("psychiatr*",),),
                        must_not=("therap*", "medic*", "meds", "pill*", "prescri*", "evaluat*", "counsel*", "talk*",
                                  "dose", "refill*")),
         "whether it is a therapy session or a medication check",
         "the one where they go over their medication and get refills", "service_phrase", "appt_061",
         (("med*", "prescri*", "refill*", "pill*"),), ("therap*", "talk*", "counsel*")),
    Pick("h5-m08", Need("h5-m08", "heldout5_multiturn", "service_pick", ("appt_018", "appt_019"), EXISTING,
                        "Existing patient: a parent whose 10-year-old needs a physical and a form signed by the "
                        "doctor. They do not say what the form is for.", must=(("form",),),
                        must_not=("school", "sport*", "team", "soccer", "basketball", "football", "camp", "class",
                                  "play*")),
         "whether the physical is for school or for sports", "it is for the basketball team", "service_phrase",
         "appt_019", (("basketball", "team", "sport*"),), ("school",)),
]


@dataclass(frozen=True)
class Profile:
    name: str
    needs: list[Need]
    doctors: list[Doctor]
    picks: list[Pick] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return ROOT / "eval" / self.name

    @property
    def scenarios(self) -> Path:
        return self.dir / "scenarios.json"

    @property
    def raw(self) -> Path:
        return self.dir / "deepseek_raw"

    @property
    def out(self) -> Path:
        return ROOT / "eval" / f"cases_{self.name}.jsonl"


PROFILES = {p.name: p for p in (Profile("heldout3", NEEDS3, DOCTORS3), Profile("heldout4", NEEDS4, DOCTORS4, PICKS4),
                                Profile("heldout5", NEEDS5, DOCTORS5, PICKS5))}


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
        wanted = n.kind if n.kind == "not_offered" or n.category.endswith("_policy") else None
        assert code == wanted, f"{n.id}: catalog says {code}, spec says {wanted}"
        exp = {"status": "refuse", "refuse_code": code} if code else offer(cat, tid, n.patient)
    return {"id": n.id, "category": n.category, "kind": n.kind, "patient": n.patient,
            "situation": n.situation, "fields": dict(SVC), "fixed": {},
            "checks": {"service_phrase": {"must": [list(g) for g in n.must], "must_not": list(n.must_not)}},
            "expect_t1": None, "expected": exp,
            "truth": {"target": list(n.target), "type_names": [cat.types[t]["name"] for t in n.target]}}


def doctor_scenario(cat: Catalog, d: Doctor, category: str) -> dict:
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
    return {"id": d.id, "category": category, "kind": d.kind, "patient": d.patient,
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


def pick_scenario(cat: Catalog, p: Pick, prof: str) -> dict:
    s = need_scenario(cat, p.first) if isinstance(p.first, Need) else doctor_scenario(cat, p.first, "")
    t1 = s["expected"]
    assert t1["status"] == "ask" and p.choice in t1["ask_options"], f"{p.id}: turn 1 expects {t1}"
    if t1["ask_field"] == "service":
        exp = offer(cat, p.choice, s["patient"], site=p.site)
    else:
        exp = offer(cat, p.first.type_id, s["patient"], {p.choice}, p.site or p.first.site)
    follow = f"followup_{p.key}"
    return {**s, "id": p.id, "category": f"{prof}_multiturn", "kind": f"{t1['ask_field']}_pick",
            "situation": f"{s['situation']} Asked {p.question}, they answer: {p.answer}.",
            "fields": {**s["fields"], follow: f"their answer when asked {p.question}: {p.answer}"},
            "checks": {**s["checks"], follow: {"must": [list(g) for g in p.must], "must_not": list(p.must_not)}},
            "expect_t1": t1, "expected": exp, "truth": {**s["truth"], "choice": p.choice}}


def build(prof: Profile) -> list[dict]:
    cat = Catalog(json.loads(CATALOG.read_text(encoding="utf-8")))
    return ([need_scenario(cat, n) for n in prof.needs]
            + [doctor_scenario(cat, d, f"{prof.name}_provider") for d in prof.doctors]
            + [pick_scenario(cat, p, prof.name) for p in prof.picks])


def merge(scen: list[dict], prof: Profile) -> None:
    by_id = {s["id"]: s for s in scen}
    phrasings: dict[str, dict] = {}
    for path in sorted(prof.raw.glob("*.json")):
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
        turns = [{"update": update}]
        for key in s["fields"]:
            if key.startswith("followup_"):
                turns[0]["expect"] = s["expect_t1"]
                turns.append({"update": {key.removeprefix("followup_"): got[key]}})
        lines.append({"id": s["id"], "category": s["category"], "kind": s["kind"], "patient": s["patient"],
                      "turns": turns, "expected": s["expected"], "phrasing_source": got["_file"]})
    out = prof.out
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in lines), encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(ROOT)}: {len(lines)} cases; dropped {len(drops)}")
    for sid, errs, got in drops:
        print(f"  DROP {sid}: {'; '.join(errs)}  got={got}")
    print("per category:", dict(Counter(c["category"] for c in lines)))
    print("per kind:", dict(Counter(c["kind"] for c in lines)))
    print("sha256", hashlib.sha256(out.read_bytes()).hexdigest())


def main() -> None:
    args = sys.argv[1:]
    name = "heldout3"
    if args[:1] == ["--set"]:
        name, args = args[1], args[2:]
    if name not in PROFILES:
        sys.exit(f"unknown set {name}; known: {sorted(PROFILES)}")
    prof = PROFILES[name]
    step = args[0] if args else "scenarios"
    if step == "scenarios":
        scen = build(prof)
        prof.dir.mkdir(parents=True, exist_ok=True)
        prof.scenarios.write_text(json.dumps(scen, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"wrote {prof.scenarios.relative_to(ROOT)}: {len(scen)} scenarios")
        print("per category:", dict(Counter(s["category"] for s in scen)))
        for s in scen:
            e = s["expected"]
            t1 = f"after {s['expect_t1']['ask_options']} " if s["expect_t1"] else ""
            print(f"  {s['id']:<7} {s['kind']:<20} {t1}{e['status']:<7} "
                  f"{e.get('type_id') or e.get('refuse_code') or e.get('ask_options')} "
                  f"{ {k: v for k, v in e.items() if k in ('provider_ids', 'location_ids')} }")
        return
    scen = json.loads(prof.scenarios.read_text(encoding="utf-8"))
    if step == "phrase":
        nb.phrase(scen, prof.raw, preamble=PROMPT)
    elif step == "repair":
        nb.phrase(scen, prof.raw, only=args[1].split(","), tag=args[2] if len(args) > 2 else "repair",
                  preamble=PROMPT)
    elif step == "merge":
        merge(scen, prof)
    else:
        sys.exit(f"unknown step {step}")


if __name__ == "__main__":
    main()
