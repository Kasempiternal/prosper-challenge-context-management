# Held-out round 3: label notes

Covers `eval/cases_heldout3.jsonl` (SF) and `eval/cases_national3.jsonl`. Both were authored blind on
2026-10-03, with no resolver code, resolver results or failure transcripts read and nothing scored.
Every expectation comes from catalog queries in the two generators. This file lists every change made
after reading a DeepSeek phrasing, and every label I was not fully sure of. Nothing was dropped.

## Changes made after reading a phrasing

- **h3-31** (ask: Colonoscopy vs Endoscopy Consultation). DeepSeek wrote "my stomach doctor said I need
  a scope, but I don't remember if it goes down my throat or up from below". My leak check had banned
  "throat" and "stomach". Because the phrase names both routes, it stays a genuine two-way ask. I
  relaxed the check and left the label unchanged.
- **h3-x03** (refuse: referral). The first two phrasings both said "see somebody/someone about my
  stomach". Without the word "specialist", that request fits a primary-care Sick Visit, which needs no
  referral, so a referral refusal would not be the clear answer. I rewrote the scenario so the caller
  asks for a specialist after their regular doctor's pills did not help, and regenerated once
  (`deepseek_raw/repair2_01.json`). The label is unchanged.
- **national3 batch_03**: DeepSeek replied in prose ("I'll look at existing case files") with no JSON.
  I re-asked the same 12 scenarios (`eval/national3/deepseek_raw/repair_01.json`). `cmdc` now runs from
  an empty temporary directory, so the model has no repo files to open.

## Labels I am unsure about (kept)

SF (`heldout3`):

- **h3-22** "I'm on Medicare and want the once-a-year visit Medicare covers for seniors" -> Annual
  Wellness Visit (appt_003). Medicare's yearly benefit is called the Annual Wellness Visit. Still, a
  scheduler could treat this like heldout2's "my annual" and ask between it and Annual Physical.
- **h3-32** "my yearly all-over skin check to look for skin cancer" -> ask Skin Cancer Screening vs
  Full Body Skin Exam. The two types have identical providers. A resolver that books either one
  without asking could be argued correct.
- **h3-33** "due for my regular six-month dental checkup" -> ask Dental Cleaning vs Dental Exam. A
  six-month visit usually includes both, but the catalog books one type.
- **h3-13** "my gallbladder's coming out next month and the surgeon wants my regular doctor to clear me
  first" -> Pre-operative Evaluation (Internal Medicine), not Orthopedics' Pre-surgical Consultation.
  Confident, but the two names are close.
- **h3-19** antidepressant dose with the psychiatrist -> Medication Management (Psychiatry), not the
  General Medication Review.
- **h3-25** "need a CBC blood test" -> Blood Draw / Lab Work, not Fasting Blood Test (a CBC needs no
  fasting).
- **h3-26** "doctor wants pictures of my lungs, the chest films" -> X-Ray. A CT also images the lungs,
  but "films" means a plain X-ray.
- **h3-p01, h3-p09, h3-p10** expect `ask_field: provider` with exactly the two doctors who fit the clue.
  A resolver that asks for the first name instead (`provider_first_name`) asks an equivalent question
  but fails on the field name.
- `ask_options` are compared in order by `check_plan`. They are sorted by id, as in heldout2.

National (`national3`):

- **nat3-geo-13** "Minnesota" -> ask metro. Minneapolis and St. Paul are about 10 miles apart, so a
  scheduler might offer Twin Cities sites directly.
- **nat3-geo-14/15** reuse the twin towns Glendale and Arlington from national/national2, this time
  with the other metro (Arizona, Virginia). All 4 twin towns in the catalog were already used.
- **nat3-dup-01..03** (no city given) reuse name groups from national/national2 (Scott Miller, James
  Caskey, Chioma Getachew). Each now uses the namesake not chosen before, with a different type. No
  unused cross-metro namesake group shares a bookable type.
- **Symptom `types_any` lists** are my judgement from type names, e.g. gout -> Rheumatology, Gout or
  Podiatry Consultation. Other types could also be acceptable.
- **nat3-ring-01** LASIK in Salt Lake City -> none_nearby. Per the catalog the nearest LASIK site is in
  Houston (1,176 mi), so the offered alternative is far.
- **nat3-unoff-01** "pet scan" (lowercase) means a PET scan, which no provider offers.
