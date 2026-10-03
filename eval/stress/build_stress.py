"""Author eval/cases_stress_sf.jsonl and eval/cases_stress.jsonl (national), and the case table in
eval/stress/README.md, from catalog queries and the booking rules. The resolver is never imported:
every expectation below is computed from the catalog (rows after the six booking rules, see
catq.rows) or written from a rule in docs/PHASE2_DESIGN.md.

  python eval/stress/build_stress.py            # rewrite both case files and the README table

Each case: the stress layer adds `severity` ("critical": a wrong commit is a patient-safety or billing
error; "hard") and `source` (a..f, the brief's source groups: a earlier failures, b safety probes,
c policy, d geography, e multi-turn, f voice noise). `also_ok` lists whole expectations that are
equally right (a different catalog-valid answer); the main `expected` is the preferred one.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import catq  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "eval"
README = EVAL / "stress" / "README.md"
MARK_A, MARK_B = "<!-- cases:begin -->", "<!-- cases:end -->"

OLD_REF = {"is_new": False, "has_referral": True}
NEW_REF = {"is_new": True, "has_referral": True}
SRC_NAME = {"a": "earlier failure", "b": "safety probe", "c": "policy", "d": "geography", "e": "multi-turn",
            "f": "voice noise"}

_cases: list[dict] = []
_notes: list[dict] = []
_current = {"which": "sf"}


# ---- catalog queries ---------------------------------------------------------------------

def rows(tid, new=None, ref=None, metro=None, locs=None, provs=None):
    w = _current["which"]
    c = catq.cat(w)
    out = catq.rows(w, tid, new, ref)
    if metro:
        out = [(p, l) for p, l in out if c["locs"][l].get("metro_id") == metro]
    if locs:
        out = [(p, l) for p, l in out if l in locs]
    if provs:
        out = [(p, l) for p, l in out if p in provs]
    return out


def P(rs):
    return sorted({p for p, _ in rs})


def Lc(rs):
    return sorted({l for _, l in rs})


def need(rs, what):
    assert rs, f"no catalog rows for {what}"
    return rs


def metro_locs(metro):
    return sorted(l["id"] for l in catq.cat("nat")["locs"].values() if l["metro_id"] == metro)


def state_locs(state):
    return sorted(l["id"] for l in catq.cat("nat")["locs"].values() if l["state"] == state)


def miles(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def nearest_metro_miles(anchor):
    ms = catq.cat("nat")["raw"]["metros"]
    return min((miles(anchor, (m["lat"], m["lon"])), m["name"]) for m in ms)


def prov_named(name, **kw):
    c = catq.cat(_current["which"])
    out = [p for p in c["provs"].values() if p["name"] == name and all(p[k] == v for k, v in kw.items())]
    assert len(out) == 1, (name, kw, [p["id"] for p in out])
    return out[0]


# ---- expectation builders ----------------------------------------------------------------

def offer(type_id=None, types_any=None, providers=None, locations=None, subset=None, **kw):
    e = {"status": "offer"}
    if type_id:
        e["type_id"] = type_id
    if types_any:
        e["types_any"] = types_any
    if providers:
        e["provider_ids"] = providers
    if locations:
        e["location_ids"] = locations
    if subset:
        e["location_ids_subset"] = subset
    e.update(kw)
    return e


def ask(field=None, options=None):
    e = {"status": "ask"}
    if field:
        e["ask_field"] = field
    if options:
        e["ask_options"] = options
    return e


def refuse(code, **kw):
    return {"status": "refuse", "refuse_code": code, **kw}


def T(update, expect=None):
    t = {"update": update}
    if expect:
        t["expect"] = expect
    return t


def add(cid, category, kind, src, sev, patient, turns, expected, why, also_ok=None, unsure=None):
    which = _current["which"]
    cid = f"{'sf' if which == 'sf' else 'nat'}-{sum(n['which'] == which for n in _notes) + 1:02d}"  # sequential; the arg is a mnemonic
    case = {"id": cid, "category": category, "kind": kind}
    if which == "nat":
        case["catalog_sha256"] = catq.sha256("nat")
    case.update({"severity": sev, "source": src, "patient": patient, "turns": turns, "expected": expected})
    if also_ok:
        case["also_ok"] = also_ok
    case["phrasing_source"] = "hand-written; ground truth from catalog queries (eval/stress/build_stress.py)"
    _cases.append(case)
    _notes.append({"id": cid, "which": which, "sev": sev, "src": src, "why": why, "unsure": unsure,
                   "phrase": " -> ".join(" + ".join(v for v in t["update"].values() if isinstance(v, str))
                                         for t in turns)})


# ---- SF catalog --------------------------------------------------------------------------

def sf():
    _current["which"] = "sf"
    PEDS = ["prov_002", "prov_012", "prov_013"]

    add("sf-01", "umbrella", "scope", "a", "critical", OLD_REF,
        [T({"service_phrase": "my stomach doctor said I need a scope"})],
        ask("service", ["appt_054", "appt_055"]),
        "Umbrella word. Round 5 sent this to an ENT consultation; a colonoscopy and an endoscopy consultation both fit, "
        "so only the caller can choose.")
    add("sf-02", "umbrella", "baby_checkup", "a", "critical", OLD_REF,
        [T({"service_phrase": "my baby's checkup"})],
        ask("service", ["appt_015", "appt_016"]),
        "Umbrella word. Well-Child or Newborn; an adult annual physical or a silent pick is wrong.")
    add("sf-03", "umbrella", "blood_work", "a", "critical", OLD_REF,
        [T({"service_phrase": "some blood work"})],
        ask("service", ["appt_072", "appt_073"]),
        "Umbrella word. Fasting changes the preparation and the slot, so Blood Draw vs Fasting Blood Test is the caller's call "
        "(review r5: h4-27 is right).")
    add("sf-04", "umbrella", "dental_checkup", "a", "critical", OLD_REF,
        [T({"service_phrase": "my six-month dental checkup"})],
        ask("service", ["appt_074", "appt_075"]),
        "Umbrella word. A six-month checkup is a cleaning plus an exam; h3-33 committed to an exam (round 3).")
    add("sf-05", "failed_round", "named_procedure", "a", "hard", OLD_REF,
        [T({"service_phrase": "I need a mole removed"})],
        offer("appt_029", providers=P(need(rows("appt_029", False, True), "mole removal")),
              locations=Lc(rows("appt_029", False, True))),
        "The caller names a procedure. Probe_everyday asked 'removal or skin cancer screening?' with and without a model. An ask is safe, an offer "
        "must be Mole Removal at a surgery site.")
    add("sf-06", "failed_round", "named_test", "a", "hard", OLD_REF,
        [T({"service_phrase": "my cardiologist ordered a stress test"})],
        offer("appt_022", providers=P(rows("appt_022", False, True)), locations=Lc(rows("appt_022", False, True))),
        "The caller names the test and who ordered it; the resolver asked 'cardiology consultation or stress test?'.")
    add("sf-07", "failed_round", "cardiac_substitution", "a", "critical", OLD_REF,
        [T({"service_phrase": "I need a cholesterol check"})],
        ask("service", ["appt_072", "appt_073"]),
        "No-model mode offered Pacemaker Check (appt_025) for a cholesterol check (probe_everyday). A lipid test is blood work "
        "(fasting or not); a cardiac visit is wrong.",
        also_ok=[offer(types_any=["appt_072", "appt_073"], providers=P(rows("appt_072", False, True) + rows("appt_073", False, True)),
                       locations=Lc(rows("appt_072", False, True) + rows("appt_073", False, True)))],
        unsure="Whether a cholesterol check should ask or book Fasting Blood Test; both are accepted, anything else is not.")

    add("sf-08", "wrong_doctor", "site_word_man", "b", "critical", OLD_REF,
        [T({"service_phrase": "annual physical", "provider_phrase": "Dr. Patel, the man"})],
        ask(),
        "Review r3 #1: 'man' sounded like Main St and booked Fatima Patel (and 'the one my boy sees': 'boy' sounded like Bay). "
        "Two male-named Patels do this visit; gender never books alone. Any question is fine, a booking is not.")
    add("sf-10", "wrong_doctor", "negation", "b", "critical", OLD_REF,
        [T({"service_phrase": "annual physical", "provider_phrase": "Dr. Garcia, not the one who speaks Spanish"})],
        ask("provider", ["prov_002", "prov_003"]),
        "Review r3 #1: negation ignored, so the Spanish speaker (Carlos Garcia) was booked. The two Marias are the only fit.",
        also_ok=[offer("appt_002", providers=["prov_002", "prov_003"],
                       locations=Lc(rows("appt_002", False, True, provs=["prov_002", "prov_003"])))])
    add("sf-11", "wrong_doctor", "kin_as_specialty", "b", "critical", OLD_REF,
        [T({"service_phrase": "annual physical", "provider_phrase": "Dr. Chen, the one my daughter recommended"})],
        ask("provider"),
        "Review r3 #1: 'daughter' was read as Pediatrics and Michael Chen (NP, Pediatrics) was booked.")
    add("sf-12", "wrong_doctor", "ramirez_swap", "b", "critical", OLD_REF,
        [T({"service_phrase": "sick visit", "provider_phrase": "Dr. Linda Ramirez"})],
        refuse("provider_type"),
        "Neither Dr. Linda Ramirez (cardiology, radiology) does a sick visit; Dr. Priya Ramirez (NP) does. Booking Priya is a "
        "different person (review r5, out-of-scope note).")
    add("sf-13", "wrong_doctor", "ramirez_swap_midcall", "b", "critical", OLD_REF,
        [T({"service_phrase": "sick visit", "provider_phrase": "Dr. Priya Ramirez"},
           offer("appt_007", providers=["prov_021"])),
         T({"provider_phrase": "actually, Dr. Linda Ramirez"})],
        refuse("provider_type"),
        "Same swap after a mind change: 'actually Dr. Linda Ramirez' offered Dr. Priya Ramirez without saying so (review r5).")
    add("sf-14", "context_word", "pt_as_context", "b", "critical", OLD_REF,
        [T({"service_phrase": "My knee still hurts after PT, I want a doctor to look at it"})],
        offer("appt_032", providers=P(rows("appt_032", False, True)), locations=Lc(rows("appt_032", False, True))),
        "Review r4 #2: 'PT' as context made the agent refuse 'we don't offer physical therapy'. The request is an orthopedic look. "
        "A refusal is a false refusal; an ask is fine.",
        also_ok=[ask("service")])
    add("sf-15", "context_word", "pt_as_request", "b", "critical", OLD_REF,
        [T({"service_phrase": "I need physical therapy for my shoulder"})],
        refuse("not_offered"),
        "The contrast to the PT-as-context case: here PT is the request, and the SF catalog has no PT provider. Offering any visit as if it were PT is wrong.",
        also_ok=[ask("service")])
    add("sf-18", "policy", "new_returning_only_test", "c", "critical", NEW_REF,
        [T({"service_phrase": "an MRI of my knee"})],
        refuse("new_patient_type"),
        "New patients cannot book MRI - Knee (returning only). Dev set covers echo; this is the imaging variant from the design doc.")
    add("sf-19", "policy", "closed_doctor_namesake", "c", "critical", NEW_REF,
        [T({"service_phrase": "cardiology consultation", "provider_phrase": "Dr. Chen, the heart doctor"})],
        offer("appt_020", providers=P(rows("appt_020", True, True, provs=["prov_046"])),
              locations=Lc(rows("appt_020", True, True, provs=["prov_046"]))),
        "Design doc example: Dr. David Chen is not accepting new patients, so a new patient gets Dr. Emily Chen with no question.")
    add("sf-20", "policy", "missing_referral", "c", "critical", {"is_new": False, "has_referral": False},
        [T({"service_phrase": "a brain MRI"})],
        refuse("referral"),
        "A brain MRI needs a referral and the caller has none.")
    add("sf-22", "policy", "doctor_wrong_site", "c", "critical", OLD_REF,
        [T({"service_phrase": "annual physical", "provider_phrase": "Dr. Lucas Chen", "location_phrase": "Downtown"})],
        refuse("provider_location"),
        "Dr. Lucas Chen works only at Mission Bay. Booking him at Downtown, or another Chen there, is wrong.")
    add("sf-23", "policy", "doctor_wrong_type", "c", "critical", OLD_REF,
        [T({"service_phrase": "annual physical", "provider_phrase": "Dr. Emily Chen"})],
        refuse("provider_type"),
        "Dr. Emily Chen is a cardiologist and does not do annual physicals.")

    add("sf-24", "geo", "street_shared", "d", "hard", OLD_REF,
        [T({"service_phrase": "sick visit", "location_phrase": "the clinic on Market Street"})],
        ask("location", sorted(Lc(rows("appt_007", False, True, locs=["loc_001", "loc_005"])))),
        "Two clinics on one street (Mission District 4732 and Midtown 2099 Market St): no number, so ask which.")
    add("sf-25", "geo", "street_number", "d", "critical", OLD_REF,
        [T({"service_phrase": "sick visit", "location_phrase": "the one at 4732 Market Street"})],
        offer("appt_007", locations=["loc_001"]),
        "Street plus house number picks Mission District; Midtown (loc_005) is the wrong clinic.")
    add("sf-26", "geo", "number_in_words", "d,f", "critical", OLD_REF,
        [T({"service_phrase": "sick visit", "location_phrase": "forty seven thirty two Market Street"})],
        offer("appt_007", locations=["loc_001"]),
        "A house number said as words (4732). STT writes numbers many ways.")
    add("sf-27", "geo", "street_capability", "d", "critical", OLD_REF,
        [T({"service_phrase": "blood draw", "location_phrase": "the clinic on Geary Boulevard"})],
        refuse("location_type", location_ids_subset=["loc_000"]),
        "Two Geary clinics, neither has lab providers (Dr. Leila Sato works at Mission Bay, North Beach and North Gate; only Mission Bay has "
        "the lab). Offering Mission Bay silently is a wrong site; the right answer names it as the alternative.")
    assert not rows("appt_072", False, True, locs=["loc_004", "loc_006"])

    add("sf-28", "multi_turn", "answer_by_label", "e", "critical", OLD_REF,
        [T({"service_phrase": "some blood work"}),
         T({"service_phrase": "the fasting one"})],
        offer("appt_073", providers=P(need(rows("appt_073", False, True), "fasting")), locations=Lc(rows("appt_073", False, True))),
        "The caller answers the umbrella question by label. Must land on Fasting Blood Test, not the other option. (Turn 1 is not scored here, "
        "so an umbrella commit is counted once, in the blood-work umbrella case.)")
    add("sf-29", "multi_turn", "answer_by_ordinal", "e", "critical", OLD_REF,
        [T({"service_phrase": "my baby's checkup"}, ask("service", ["appt_015", "appt_016"])),
         T({"service_phrase": "the second one"})],
        offer("appt_016", providers=P(need(rows("appt_016", False, True), "newborn")), locations=Lc(rows("appt_016", False, True))),
        "Answer by ordinal: the second spoken option is Newborn Visit. Picking the first, or anything else, is wrong.",
        unsure="Assumes the spoken order is catalog order (Well-Child, then Newborn), as the first-turn ask_options pins.")
    add("sf-30", "multi_turn", "repeat_vague_word", "e", "critical", OLD_REF,
        [T({"service_phrase": "my baby's checkup"}, ask("service", ["appt_015", "appt_016"])),
         T({"service_phrase": "the checkup"})],
        ask("service", ["appt_015", "appt_016"]),
        "The caller repeats the vague word. The same question is asked again; review r5 #5 saw it jump to adult annual-physical options.")
    add("sf-31", "multi_turn", "answer_by_attribute", "e", "hard", OLD_REF,
        [T({"service_phrase": "sick visit", "provider_phrase": "Dr. Maria Garcia"}, ask("provider")),
         T({"provider_phrase": "the nurse practitioner"})],
        offer("appt_007", providers=["prov_003"], locations=Lc(rows("appt_007", False, True, provs=["prov_003"]))),
        "Two Dr. Maria Garcias: prov_002 is an MD (pediatrics), prov_003 an NP. 'The nurse practitioner' means prov_003.")
    add("sf-32", "multi_turn", "no_to_confirmation", "e", "critical", OLD_REF,
        [T({"service_phrase": "cardiology consultation", "provider_phrase": "Dr. Chen"}, ask("provider")),
         T({"provider_phrase": "the lady one"}),
         T({"provider_phrase": "no, not her, the other one"})],
        offer("appt_020", providers=["prov_000"], locations=Lc(rows("appt_020", False, True, provs=["prov_000"]))),
        "The agent gets a woman Chen (Emily, prov_046), the caller says no. The other Chen is the answer; Emily must not come back.",
        unsure="Turn 2 is left unscored (confirm or offer are both defensible); only the 'no' turn is.")
    add("sf-33", "multi_turn", "mind_change_service", "e", "hard", {"is_new": False},
        [T({"service_phrase": "flu shot"}, offer("appt_011")),
         T({"service_phrase": "actually, make it the COVID vaccine instead"})],
        offer("appt_012", providers=P(rows("appt_012", False, None)), locations=Lc(rows("appt_012", False, None))),
        "A mid-call change of mind: the first type must not linger.")
    add("sf-34", "multi_turn", "mind_change_into_policy", "e", "critical", {"is_new": True},
        [T({"service_phrase": "annual physical"}, offer("appt_002")),
         T({"service_phrase": "actually I need a follow-up visit instead"})],
        refuse("new_patient_type"),
        "The change of mind lands on a returning-only visit; the earlier offer must not carry over.")

    add("sf-35", "voice", "stt_by_sound", "f", "hard", OLD_REF,
        [T({"service_phrase": "X-ray", "provider_phrase": "Dr. Hanna Nwin"})],
        offer("appt_062", providers=["prov_015"], locations=Lc(rows("appt_062", False, True, provs=["prov_015"]))),
        "STT 'Nwin' for Nguyen. Hannah Nguyen (prov_015) is the only Hanna Nguyen; the X-ray site must have imaging.")
    add("sf-36", "voice", "filler_gender_only", "f", "critical", OLD_REF,
        [T({"service_phrase": "annual physical", "provider_phrase": "uh, um, the, the lady one"})],
        ask(),
        "Fillers and a gender word with no name. Gender never books alone and the catalog has no gender field.")
    add("sf-37", "voice", "false_start", "f", "critical", OLD_REF,
        [T({"service_phrase": "I need a, uh, a flu, no wait, a COVID shot"})],
        offer("appt_012", providers=P(rows("appt_012", False, True)), locations=Lc(rows("appt_012", False, True))),
        "Self-correction in the same breath: the caller ends on COVID. A flu shot is the wrong vaccine.")
    add("sf-38", "voice", "run_on_two_visits", "f", "hard", OLD_REF,
        [T({"service_phrase": "I need a flu shot and also a dental cleaning"})],
        ask("service"),
        "Two visits in one sentence. One visit per booking: ask which first, or take one; a merged or third visit is wrong.",
        also_ok=[offer(types_any=["appt_011", "appt_074"],
                       providers=P(rows("appt_011", False, True) + rows("appt_074", False, True)),
                       locations=Lc(rows("appt_011", False, True) + rows("appt_074", False, True)))])

    # Wrong commits and substitutions of the round 3 dev sets (cases_heldout3.jsonl, labels from catalog queries), in their words.
    add("sf-39", "failed_round", "pt_request_after_surgery", "a", "critical", OLD_REF,
        [T({"service_phrase": "start PT after my shoulder surgery"})],
        refuse("not_offered"),
        "h3-34: round 3 booked an Orthopedic Consultation. PT here is the request and the SF catalog has no PT provider.",
        also_ok=[ask("service")])
    add("sf-40", "failed_round", "closed_specialty_substitution", "a", "critical", NEW_REF,
        [T({"service_phrase": "my doctor, he found problem with my hormones in the blood test, said I need a specialist for hormones and the glands"})],
        refuse("new_patient_provider"),
        "h3-x04: the only endocrinologist is closed to new patients; round 3 booked a Blood Draw instead, a different visit.")
    assert all(not p_["accepting_new_patients"] for p_ in catq.cat("sf")["provs"].values() if "appt_056" in p_["appointment_type_ids"])
    assert [p_["id"] for p_ in catq.cat("sf")["provs"].values() if p_["name"].endswith(" Hernandez") and p_["title"] == "NP"] == ["prov_025"]
    assert not catq.cat("sf")["provs"]["prov_025"]["accepting_new_patients"]
    add("sf-41", "failed_round", "closed_nurse_practitioner", "a", "critical", NEW_REF,
        [T({"service_phrase": "sick visit", "provider_phrase": "the nurse practitioner, Dr. Hernandez"})],
        refuse("new_patient_provider"),
        "h3-p04: the only NP Hernandez (Patricia, Internal Medicine) is closed to new patients; round 3 booked Dr. Tomas Hernandez (MD).")
    add("sf-42", "failed_round", "pediatric_adult_substitution", "a", "critical", NEW_REF,
        [T({"service_phrase": "my 3-year-old daughter, she's had a fever since last night and she keeps tugging at her ear"})],
        offer("appt_017", providers=P(need(rows("appt_017", True, True), "ped sick")), locations=Lc(rows("appt_017", True, True))),
        "h3-10: a 3-year-old needs the Pediatric Sick Visit; round 3 booked the adult Sick Visit.")
    add("sf-43", "failed_round", "vaccine_substitution", "a", "critical", NEW_REF,
        [T({"service_phrase": "I need my Tdap booster, it's been more than ten years"})],
        offer("appt_010", providers=P(need(rows("appt_010", True, True), "vaccination")), locations=Lc(rows("appt_010", True, True))),
        "h3-24: Tdap is a Vaccination / Immunization; round 3 booked the COVID-19 vaccine.")
    add("sf-44", "failed_round", "specialty_substitution", "a", "critical", OLD_REF,
        [T({"service_phrase": "doctor wants pictures of my lungs, the chest films"})],
        offer("appt_062", providers=P(need(rows("appt_062", False, True), "xray")), locations=["loc_004", "loc_005"]),
        "h3-26: chest films are an X-Ray at an imaging site; round 3 booked a Pulmonology Consultation.")
    assert Lc(rows("appt_062", False, True)) == ["loc_004", "loc_005"]

    # Facts the expectations above rest on, asserted from the catalog so a catalog change fails loudly.
    pr = catq.cat("sf")["provs"]
    has = lambda pid, tid: tid in pr[pid]["appointment_type_ids"]  # noqa: E731
    assert has("prov_002", "appt_002") and has("prov_003", "appt_002") and not has("prov_008", "appt_999")
    assert has("prov_008", "appt_002") and "Spanish" in pr["prov_008"]["languages"]
    assert not any("Spanish" in pr[p]["languages"] for p in ("prov_002", "prov_003"))
    assert not has("prov_005", "appt_007") and not has("prov_014", "appt_007") and has("prov_021", "appt_007")
    assert has("prov_002", "appt_007") and has("prov_003", "appt_007") and pr["prov_003"]["title"] == "NP"
    assert pr["prov_021"]["name"] == "Dr. Priya Ramirez"
    assert pr["prov_036"]["name"] == "Dr. Linda Nguyen"  # unrelated; guards id stability
    assert [p["id"] for p in pr.values() if p["name"].endswith(" Chen") and has(p["id"], "appt_020")] == ["prov_000", "prov_046"]
    assert pr["prov_027"]["location_ids"] and pr["prov_047"]["location_ids"] == ["loc_000"] and not has("prov_046", "appt_002")
    assert len(Lc(rows("appt_007", False, True, locs=["loc_001", "loc_005"]))) == 2
    patels = [p for p in pr.values() if p["name"].endswith(" Patel") and has(p["id"], "appt_002")]
    assert {p["id"] for p in patels} >= {"prov_031", "prov_044", "prov_042", "prov_045"}
    assert [p for p in P(rows("appt_018", False, True))] == PEDS and not has("prov_007", "appt_018")
    assert pr["prov_015"]["name"] == "Dr. Hannah Nguyen" and has("prov_015", "appt_062")
    chens_002 = {p["id"] for p in pr.values() if p["name"].endswith(" Chen") and has(p["id"], "appt_002")}
    assert "prov_012" in chens_002 and len(chens_002) >= 3


# ---- national catalog --------------------------------------------------------------------

def national():
    _current["which"] = "nat"
    CHI, HOU, SEA, DAL, PHI, NYC = "chicago-il", "houston-tx", "seattle-wa", "dallas-tx", "philadelphia-pa", "new-york-ny"

    def in_metro(tid, metro, new=False, ref=True):
        return Lc(need(rows(tid, new, ref, metro=metro), f"{tid} in {metro}"))

    add("nat-01", "umbrella", "scope", "a", "critical", OLD_REF,
        [T({"service_phrase": "my stomach doctor said I need a scope", "location_phrase": "Houston"})],
        ask("service"),
        "Umbrella word on the national catalog: Colonoscopy, Upper Endoscopy and two consultations all fit.")
    add("nat-02", "umbrella", "blood_work", "a", "critical", OLD_REF,
        [T({"service_phrase": "some blood work", "location_phrase": "Chicago"})],
        ask("service"),
        "Umbrella word: Blood Draw, Fasting, Lipid Panel, A1C and others all fit.")
    add("nat-03", "umbrella", "baby_checkup", "a", "critical", OLD_REF,
        [T({"service_phrase": "my baby's checkup", "location_phrase": "Seattle"})],
        ask("service"),
        "Umbrella word: Well-Child, Newborn, Developmental Screening fit.")
    add("nat-04", "umbrella", "dental_checkup", "a", "critical", OLD_REF,
        [T({"service_phrase": "my six-month dental checkup", "location_phrase": "Dallas"})],
        ask("service"),
        "Umbrella word: cleaning and exam, plus dental X-rays. Review r5 relabelled the national2 dental cases the same way.")
    add("nat-05", "failed_round", "named_type_dropped", "a", "critical", OLD_REF,
        [T({"service_phrase": "I need my a1c", "location_phrase": "Dallas"})],
        offer("appt_232", subset=in_metro("appt_232", DAL)),
        "Review r3 #3: 'a1c' booked Diabetes Management and dropped the named A1C Test.")
    add("nat-06", "failed_round", "named_type_dropped", "a", "critical", OLD_REF,
        [T({"service_phrase": "I need an upper endoscopy", "location_phrase": "Nashville"})],
        offer("appt_184", subset=in_metro("appt_184", "nashville-tn")),
        "Review r3 #3: 'upper endoscopy' dropped Upper Endoscopy (EGD) for a consultation.")
    add("nat-07", "failed_round", "procedure_vs_consult", "a", "hard", OLD_REF,
        [T({"service_phrase": "I'd like to book a colonoscopy", "location_phrase": "Philadelphia"})],
        offer("appt_183", subset=in_metro("appt_183", PHI)),
        "Probe_everyday: the named procedure is Colonoscopy, not Colonoscopy Consultation.",
        unsure="A reviewer may accept an ask between the procedure and the consultation; an ask is scored safe either way.")
    add("nat-08", "failed_round", "false_refusal_therapy", "a", "hard", {"is_new": True, "has_referral": False},
        [T({"service_phrase": "I want to see a therapist", "location_phrase": "Seattle"})],
        offer("appt_199", subset=in_metro("appt_199", SEA, True, False)),
        "A new patient asking for therapy: Therapy Session (appt_060) is returning-only. Individual Therapy is open to new patients. "
        "Probe_everyday booked the returning-only visit without a model.")

    add("nat-09", "safety", "mammogram_for_lump", "b", "critical", OLD_REF,
        [T({"service_phrase": "I need a mammogram for a lump in my breast", "location_phrase": "Houston"})],
        offer("appt_216", subset=in_metro("appt_216", HOU)),
        "Review r5: a lump needs the Diagnostic Mammogram; a screening or 3D screening mammogram is the wrong test.")
    add("nat-10", "safety", "pelvic_floor_crossing", "b", "critical", OLD_REF,
        [T({"service_phrase": "pelvic floor therapy after my delivery", "location_phrase": "Chicago"})],
        offer("appt_226", subset=in_metro("appt_226", CHI)),
        "Review r5 blocker: related-visit fallback sent this to psychiatry's Therapy Session.")
    add("nat-11", "safety", "pediatric_echo", "b", "critical", OLD_REF,
        [T({"service_phrase": "my daughter needs a pediatric echocardiogram", "location_phrase": "New York"})],
        offer("appt_294", subset=in_metro("appt_294", NYC)),
        "Review r5 blocker: the adult Echocardiogram (appt_021) is a different visit. Pediatric Cardiology offers appt_294 in New York.")
    add("nat-12", "safety", "pediatric_echo_far", "b", "critical", OLD_REF,
        [T({"service_phrase": "my daughter needs a pediatric echocardiogram", "location_phrase": "Chicago"})],
        refuse("none_nearby"),
        "No Chicago clinic does appt_294 (the nearest is another metro). The adult echo is not a stand-in; ask or name the far option.",
        also_ok=[ask()])
    assert not rows("appt_294", False, True, metro=CHI)
    add("nat-13", "safety", "adult_adhd", "b", "critical", OLD_REF,
        [T({"service_phrase": "adult ADHD evaluation", "location_phrase": "Chicago"})],
        offer("appt_198", subset=in_metro("appt_198", CHI)),
        "Review r5 blocker: the related-visit fallback offered the pediatric ADHD Evaluation (appt_106).")
    add("nat-14", "safety", "nuclear_stress", "b", "critical", OLD_REF,
        [T({"service_phrase": "a nuclear stress test", "location_phrase": "Houston"})],
        offer("appt_117", subset=in_metro("appt_117", HOU)),
        "Review r5 blocker: 'nuclear stress test' was generalised to the plain Stress Test.")
    add("nat-15", "safety", "prenatal_ultrasound", "b", "critical", OLD_REF,
        [T({"service_phrase": "a prenatal ultrasound, I'm 12 weeks", "location_phrase": "Houston"})],
        offer("appt_156", subset=in_metro("appt_156", HOU)),
        "Review r5 blocker: sent to the Prenatal Visit. At 12 weeks the right scan is Prenatal Ultrasound (not the 20-week anatomy scan).")
    add("nat-16", "safety", "mri_spine", "b", "critical", OLD_REF,
        [T({"service_phrase": "MRI of my spine", "location_phrase": "Chicago"})],
        offer("appt_064", subset=in_metro("appt_064", CHI)),
        "Review r5 blocker: sent to the Spine Consultation (orthopedics).")
    dal_metro = next(m for m in catq.cat("nat")["raw"]["metros"] if m["id"] == DAL)
    dal_anchor = (dal_metro["lat"], dal_metro["lon"])
    ct_near_dal = sorted(l for l in Lc(need(rows("appt_210", False, True), "CT - chest"))
                         if miles(dal_anchor, (catq.cat("nat")["locs"][l]["lat"],
                                               catq.cat("nat")["locs"][l]["lon"])) <= 50)
    add("nat-17", "safety", "ct_chest_far", "b", "critical", OLD_REF,
        [T({"service_phrase": "a CT of my chest, my doctor ordered it", "location_phrase": "Dallas"})],
        refuse("none_nearby"),
        "Dallas offers CT Scan (appt_066) but not CT - Chest (appt_210; the nearest is Fort Worth). Review r5: the generic CT is a "
        "different visit; say the chest CT is farther and ask.",
        also_ok=[ask(), offer("appt_210", subset=ct_near_dal, max_miles=50, anchor=list(dal_anchor))],
        unsure="relabelled by the lead: a disclosed offer within 50 mi follows the pre-registered geo policy.")
    assert rows("appt_066", False, True, metro=DAL) and not rows("appt_210", False, True, metro=DAL)
    add("nat-18", "safety", "pt_as_context", "b", "critical", OLD_REF,
        [T({"service_phrase": "my PT says my hip needs to be seen by a specialist", "location_phrase": "Chicago"})],
        offer(types_any=["appt_032", "appt_135", "appt_142", "appt_139"],
              subset=sorted(set(in_metro("appt_032", CHI)) | set(in_metro("appt_135", CHI)) | set(in_metro("appt_139", CHI)))),
        "Review r4 #2: PT is context. The request is a specialist look at the hip (orthopedic consultation, hip evaluation or sports "
        "medicine). Refusing 'we do not offer PT' is a false refusal; so is booking PT.",
        also_ok=[ask("service")],
        unsure="Which orthopedic-side visit is best is a judgement; the allowed set is Orthopedic Consultation, Hip Pain Evaluation, "
               "Sports Medicine Consultation, Joint Replacement Consultation.")
    add("nat-19", "safety", "joseph_white_wrong_type", "b", "hard", OLD_REF,
        [T({"service_phrase": "a knee MRI", "provider_phrase": "Dr. Joseph White", "location_phrase": "near Raleigh"})],
        refuse("provider_type"),
        "Review r4 #6: Dr. Joseph White (PA, Family Medicine) is in Raleigh but does not do knee MRIs; the other Joseph White is a "
        "cardiologist in Nashville. The reason is 'he does not do that visit', not 'he is not nearby'. (The review used IUD "
        "insertion, which he does offer in this catalog, so the knee MRI is the faithful probe.)",
        also_ok=[refuse("none_nearby")])
    assert "appt_065" not in prov_named("Dr. Joseph White", title="PA")["appointment_type_ids"]

    add("nat-20", "policy", "new_returning_only_test", "c", "critical", NEW_REF,
        [T({"service_phrase": "an MRI of my shoulder", "location_phrase": "Chicago"})],
        refuse("new_patient_type"),
        "MRI - Shoulder is returning-only. A new patient cannot book it.")
    add("nat-21", "policy", "new_returning_only_pt", "c", "critical", NEW_REF,
        [T({"service_phrase": "a physical therapy session", "location_phrase": "Houston"})],
        refuse("new_patient_type"),
        "PT as the request, and the Session is returning-only for new patients (the Evaluation is open). Demo scenario.",
        also_ok=[ask("service"), offer("appt_070", subset=in_metro("appt_070", HOU, True, True))])
    jw = prov_named("Dr. Joseph White", title="MD")
    assert "appt_020" in jw["appointment_type_ids"] and not jw["accepting_new_patients"]
    add("nat-22", "policy", "closed_doctor", "c", "critical", NEW_REF,
        [T({"service_phrase": "cardiology consultation", "provider_phrase": "Dr. Joseph White", "location_phrase": "Nashville"})],
        refuse("new_patient_provider"),
        "The Nashville Dr. Joseph White (cardiology) is not accepting new patients.")
    add("nat-23", "policy", "missing_referral", "c", "critical", {"is_new": False, "has_referral": False},
        [T({"service_phrase": "a colonoscopy", "location_phrase": "Philadelphia"})],
        refuse("referral"),
        "Colonoscopy needs a referral and the caller has none.")
    add("nat-24", "policy", "not_offered_alias_near", "c", "critical", OLD_REF,
        [T({"service_phrase": "I need a chiropractor for my back", "location_phrase": "Houston"})],
        refuse("not_offered"),
        "Chiropractic exists in the catalog but no provider offers it. Back Pain Evaluation (orthopedics) may be suggested, never booked.",
        also_ok=[ask("service")])
    # a doctor who exists, but not in this city
    far = None
    for p in catq.cat("nat")["provs"].values():
        ms = {catq.cat("nat")["locs"][l]["metro_id"] for l in p["location_ids"]}
        same = [q for q in catq.cat("nat")["provs"].values() if q["name"] == p["name"]]
        if len(same) == 1 and ms == {"boston-ma"} and "appt_011" in p["appointment_type_ids"] and p["accepting_new_patients"]:
            far = p
            break
    assert far, "no Boston-only provider with flu shots"
    add("nat-26", "policy", "doctor_not_in_city", "c", "critical", OLD_REF,
        [T({"service_phrase": "flu shot", "provider_phrase": far["name"], "location_phrase": "Chicago"})],
        refuse("provider_location"),
        f"{far['name']} ({far['id']}) works only in Boston. Offering a Chicago doctor with a similar name, or Boston silently, is wrong.",
        also_ok=[refuse("none_nearby")])

    # geography
    chi_church = rows("appt_011", False, True, locs=["loc_155", "loc_159"])
    add("nat-27", "geo", "street_shared", "d", "hard", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "the clinic on Church Street in Chicago"})],
        ask("location", Lc(chi_church)),
        "Two Chicago clinics on Church Street (Pilsen 371, West Ridge 5926): no number, so ask which.")
    assert len(Lc(chi_church)) == 2
    add("nat-28", "geo", "street_number", "d", "critical", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "the clinic at 4162 Broadway"})],
        offer("appt_011", subset=["loc_258"]),
        "Broadway is also in Oakland and King of Prussia; the number 4162 is Fishtown only. Wrong clinic = wrong city.")
    assert rows("appt_011", False, True, locs=["loc_258"])
    anchor_trenton = (40.2171, -74.7429)
    add("nat-30", "geo", "fuzzy_place", "d", "critical", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "Trenton"})],
        ask("place_confirm"),
        "Trenton, NJ is not a clinic city; Renton, WA is (loc_055, Seattle, 2,400 miles away). A fuzzy match to Renton must be confirmed, "
        "never booked.",
        also_ok=[ask("metro"), offer("appt_011", subset=[l for l in in_metro("appt_011", PHI, False, True)],
                                     max_miles=60, anchor=list(anchor_trenton))])
    add("nat-31", "geo", "state_vs_dc", "d", "critical", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "Washington"})],
        ask("metro"),
        "Washington is both a state (Seattle, Bellevue, Renton) and DC. Never pick one silently.",
        also_ok=[ask("place_confirm")])
    va = [l for l in state_locs("VA")]
    va_rows = rows("appt_011", False, True, locs=va)
    add("nat-32", "geo", "state_only_virginia", "d", "critical", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "Virginia"})],
        offer("appt_011", subset=Lc(need(va_rows, "flu shot in VA"))),
        "Review r4 #1: a lone state became its metro and booked DC Downtown, ~150 miles from Virginia Beach. Only the Virginia clinics "
        "(Arlington, Alexandria) are 'in Virginia'.",
        also_ok=[ask("metro"), ask("place_confirm"), refuse("none_nearby", location_ids_subset=Lc(va_rows))])
    add("nat-33", "geo", "far_city_virginia_beach", "d", "critical", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "Virginia Beach"})],
        refuse("none_nearby"),
        "Review r4 #1: the nearest clinic is Alexandria, ~190 miles away, and was booked without saying a distance.",
        also_ok=[ask()])
    add("nat-34", "geo", "far_city_wichita", "d", "critical", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "Wichita, Kansas"})],
        refuse("none_nearby"),
        "Review r4 #1: Kansas City MO is ~175 miles from Wichita and was booked silently.",
        also_ok=[ask()])
    nj = state_locs("NJ")
    add("nat-35", "geo", "state_only_new_jersey", "d", "hard", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "New Jersey"})],
        offer("appt_011", subset=Lc(need(rows("appt_011", False, True, locs=nj), "flu shot NJ"))),
        "Review r4 #1: New Jersey has one clinic (Cherry Hill); the state became the Philadelphia metro. Only Cherry Hill is in the state.",
        also_ok=[ask("metro"), ask("place_confirm"),
                 refuse("none_nearby", location_ids_subset=Lc(rows("appt_011", False, True, locs=nj)))])
    sea_locs = metro_locs(SEA)
    add("nat-37", "geo", "zip_in_words", "d,f", "hard", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "my zip code is nine eight one zero eight"})],
        offer("appt_011", subset=Lc(need(rows("appt_011", False, True, locs=sea_locs), "flu shot Seattle"))),
        "ZIP said as words (98108 is Capitol Hill, Seattle). A mis-parse would land on another metro.",
        also_ok=[ask("metro")])
    add("nat-38", "geo", "neighbourhood_two_cities", "d", "critical", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "Capitol Hill"})],
        ask("metro"),
        "Capitol Hill is a Seattle clinic (loc_049) and a Washington DC clinic (loc_246).",
        also_ok=[ask("place_confirm")])
    add("nat-39", "geo", "unserved_city", "d", "critical", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "I live in Boise, Idaho"})],
        refuse("none_nearby"),
        "No clinic in Idaho; Salt Lake City is ~340 miles away. Say so; never book it as if near.",
        also_ok=[ask()])
    denv = in_metro("appt_011", "denver-co", False, True)
    add("nat-40", "geo", "closest_city_to", "d", "critical", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "what's the closest city to Boulder"})],
        offer("appt_011", subset=denv, max_miles=50, anchor=[40.015, -105.2705]),
        "Boulder is not a clinic city; Denver is ~25 miles. A fair answer names Denver clinics and the distance.",
        also_ok=[ask()])
    add("nat-40b", "geo", "unserved_city_near_metro", "d", "critical", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "I'm in Boulder, Colorado"})],
        offer("appt_011", subset=denv, max_miles=50, anchor=[40.015, -105.2705]),
        "Same Boulder, said plainly. Denver is the nearest metro; any other city is far wrong.",
        also_ok=[ask(), refuse("none_nearby", location_ids_subset=denv)])

    # multi-turn
    add("nat-41", "multi_turn", "answer_by_label", "e", "critical", OLD_REF,
        [T({"service_phrase": "some blood work", "location_phrase": "Philadelphia"}),
         T({"service_phrase": "the A1C test"})],
        offer("appt_232", subset=in_metro("appt_232", PHI)),
        "Answer to the umbrella question by name; must book the A1C Test. (Turn 1 is not scored here; the umbrella commit is counted once, in the blood-work umbrella case.)")
    add("nat-42", "multi_turn", "answer_by_attribute", "e", "hard", OLD_REF,
        [T({"service_phrase": "sick visit", "provider_phrase": "Dr. Maria Garcia", "location_phrase": "San Francisco"}, ask("provider")),
         T({"provider_phrase": "the one who speaks Arabic"})],
        offer("appt_007", providers=["prov_003"], locations=Lc(rows("appt_007", False, True, provs=["prov_003"], metro="san-francisco-ca"))),
        "Two Dr. Maria Garcias in San Francisco: prov_002 speaks Mandarin, prov_003 speaks Arabic. Languages are catalog facts.")
    add("nat-43", "multi_turn", "mind_change_city", "e", "hard", OLD_REF,
        [T({"service_phrase": "flu shot", "location_phrase": "Seattle"}, offer("appt_011", subset=sea_locs)),
         T({"location_phrase": "actually I'll be in Dallas that week"})],
        offer("appt_011", subset=Lc(need(rows("appt_011", False, True, metro=DAL), "flu Dallas"))),
        "A mid-call change of city; no Seattle site may survive.")
    add("nat-44", "multi_turn", "mind_change_after_refusal", "e", "critical", NEW_REF,
        [T({"service_phrase": "a knee X-ray", "location_phrase": "Chicago"}, refuse("new_patient_type")),
         T({"service_phrase": "ok, then an orthopedic consultation for my knee"})],
        offer("appt_032", subset=in_metro("appt_032", CHI, True, True)),
        "After a refusal the caller switches to a visit a new patient may book; the refusal must clear and the new type must be exact.")

    # Wrong commits and false refusals of the round 3 dev sets (cases_national3.jsonl), in their words, with their dev labels.
    add("nat-dev-1", "failed_round", "false_refusal_doctor_here", "a", "critical", OLD_REF,
        [T({"service_phrase": "I'd like to get an IUD inserted", "provider_phrase": "Dr. Joseph White", "location_phrase": "near Raleigh"})],
        offer("appt_160", providers=["prov_3621"], subset=["loc_213"]),
        "nat3-dup-06: Dr. Joseph White (PA) is in Raleigh and does IUD insertion; round 3 said he 'isn't at any of our clinics within 50 miles'. "
        "A false refusal.")
    add("nat-dev-2", "failed_round", "substitution_and_wrong_site", "a", "critical", OLD_REF,
        [T({"service_phrase": "I need a thyroid ultrasound, yeah a thyroid ultrasound",
            "location_phrase": "River North Care Center, over in Chicago \u2014 that's the one I always go to"})],
        refuse("location_type", location_ids_subset=["loc_153", "loc_154", "loc_157", "loc_159"]),
        "nat3-cap-05: River North has no imaging; round 3 booked a Thyroid Follow-up there, a different visit at the named site.")
    add("nat-dev-3", "failed_round", "zip_and_substitution", "a", "critical", OLD_REF,
        [T({"service_phrase": "want to get my spine X-rayed, doctor ordered it", "location_phrase": "60626"})],
        offer(types_any=["appt_062", "appt_220"], subset=["loc_153", "loc_154", "loc_155", "loc_157"], max_miles=10,
              anchor=[41.8565, -87.6523]),
        "nat3-geo-07: round 3 booked a Spine Consultation at River North and Pilsen, farther than the ZIP.")
    add("nat-dev-4", "failed_round", "neighbourhood_too_far", "a", "hard", OLD_REF,
        [T({"service_phrase": "I gotta get a drug test for a new job", "location_phrase": "I'm over by Rockridge"})],
        offer(types_any=["appt_072", "appt_233"], subset=["loc_000", "loc_005", "loc_008", "loc_011"], max_miles=13,
              anchor=[37.841, -122.2527]),
        "nat3-geo-10: round 3 booked clinics in Santa Clara and Evergreen for a caller in Rockridge (Oakland).")
    add("nat-dev-5", "failed_round", "wrong_refusal_code", "a", "hard", NEW_REF,
        [T({"service_phrase": "I want make an annual physical", "provider_phrase": "Dr. Inna Volkov, a friend told me about her",
            "location_phrase": "in Phoenix"})],
        refuse("new_patient_provider"),
        "nat3-new-03: Dr. Volkov is closed to new patients; round 3 said she is not within 50 miles and sent the caller to Atlanta, 1,588 miles away.")

    # voice
    jw3 = prov_named("Dr. Joseph White", title="PA")
    add("nat-45", "voice", "stt_by_sound", "f", "hard", OLD_REF,
        [T({"service_phrase": "sick visit", "provider_phrase": "Dr. Joseph Whyte", "location_phrase": "Raleigh"})],
        offer("appt_007", providers=[jw3["id"]], locations=Lc(rows("appt_007", False, True, provs=[jw3["id"]], metro="raleigh-nc"))),
        "STT 'Whyte' for White. Only the Raleigh Dr. Joseph White (PA) is in range; the other is in Nashville.")


def write() -> None:
    sf()
    national()
    for which, name in (("sf", "cases_stress_sf.jsonl"), ("nat", "cases_stress.jsonl")):
        sel = [c for c, n in zip(_cases, _notes) if n["which"] == which]
        ids = [c["id"] for c in sel]
        assert len(set(ids)) == len(ids)
        text = "\n".join(json.dumps(c, ensure_ascii=False) for c in sel) + "\n"
        (EVAL / name).write_bytes(text.encode("utf-8"))
        print(name, len(sel), "cases")
    table = ["| id | sev | src | phrase | why |", "|---|---|---|---|---|"]
    for n in _notes:
        why = n["why"] + (f" **Unsure:** {n['unsure']}" if n["unsure"] else "")
        table.append(f"| {n['id']} | {n['sev']} | {n['src']} | {n['phrase'].replace('|', '/')} | {why.replace('|', '/')} |")
    block = f"{MARK_A}\n" + "\n".join(table) + f"\n{MARK_B}"
    if README.exists():
        text = README.read_text(encoding="utf-8")
        if MARK_A in text:
            head, rest = text.split(MARK_A, 1)
            tail = rest.split(MARK_B, 1)[1]
            README.write_text(head + block + tail, encoding="utf-8", newline="\n")
            return
    print("README has no case-table markers; table not written")


if __name__ == "__main__":
    write()
