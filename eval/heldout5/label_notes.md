# Held-out round 5: label notes

Covers `eval/cases_heldout5.jsonl` (SF) and `eval/cases_national5.jsonl`. Both were authored blind on
2026-10-03. I read no resolver code (`backend/scheduling/**`), no resolver results (`eval/results/**`),
no `.kstack` files and no README section that reports scores, misses or failure classes, and I scored
nothing. One exception, after authoring: when staging the commit, `git diff` context printed the last
three lines of the README's round 4 scored section (a national CT-chest miss and a gender-policy note).
Every case, label and note was final by then, and nothing changed after it. Every expectation comes
from catalog queries in the two generators. This file lists every
change made after reading a DeepSeek phrasing, every authoring choice made after reading a scenario
draft, and every label I was not fully sure of. Nothing was dropped.

## Changes made after reading a phrasing

SF (`heldout5`). All three were re-asked in one call (`deepseek_raw/repair_01.json`); no label changed.

- **h5-10** (Pediatric Sick Visit). DeepSeek wrote "my boy's got a stomach ache", dropping the age.
  Without it the caller could be an adult son, so the pediatric label would not be the clear answer.
  I added "They say how old he is" and a required `6`/`six`. New phrasing: "my son needs to be seen,
  he's 6, ...".
- **h5-20** (Annual Wellness Visit, abbreviation "AWV"). DeepSeek wrote "I'm on Medicare and I want to
  book my AWV", which fails the leak check: I banned "Medicare" so the case tests the abbreviation
  alone. I reworded the scenario (aged 70, no mention of insurance). New phrasing: "AWV".
- **h5-m02** turn 1 (ask Maria Garcia NP vs Carlos Garcia). DeepSeek wrote "Dr. Garcia, my family
  doctor". That reads as "my regular doctor", not a specialty, so it no longer narrows to the two
  family-medicine Garcias (Maria Garcia MD, a pediatrician, also gives flu shots). I asked for the
  specialty explicitly and required "family medicine". New phrasing: "Dr. Garcia, the family medicine
  doctor".

National (`national5`): no changes. All 49 first-pass phrasings passed the checks and read as labeled.
The two either/or cases were added after the first pass (see below) and phrased in one call
(`deepseek_raw/twins_01.json`).

## Authoring choices made after reading a scenario draft (no phrasing or score involved)

- Seed: 20261005 drew the same service (TB skin test) and metro (Philadelphia) for nat5-geo-02 and
  geo-03, two near-identical cases. I moved to seed 20261006 before any DeepSeek call.
- General-variant distance: at 60 mi, one draw had the specific test 68 mi away, too close to call
  "not nearby". I require no valid site for the specific test within 100 mi of the metro centre.
  Two draws then landed in Las Vegas, so the three general-variant cases now use distinct metros.
- Either/or: national5 first drew none, because national..national4 used every (town, metro) choice
  of the catalog's 4 twin towns. To keep national4's category mix, a `reuse_twins` flag adds 2 at the
  end of the draw, reusing a twin town with a service whose type+metro is new for every metro it
  touches. The first 49 scenarios did not change.

## Generator changes

- `eval/heldout3/build_cases.py` gains the `heldout5` profile, with two new kinds: `symptom_misdirect`
  (the body part named points to another specialty) and `acute` (acute symptoms with their onset).
- `eval/national/build_cases.py` gains the `national5` profile (seed 20261006), a `general_variant`
  kind (the specific test has no valid site within 100 mi, the general variant is bookable in the
  metro) and the `reuse_twins` flag. The either/or code was split into helpers with the same random
  draws. The `scenarios` step prints type+metro overlap 0/51 and provider+type overlap 0/51 against
  national, national2, national3 and national4.
- Both builders now write LF (`newline="\n"`), so the sha256 they print is the committed file's.
  heldout3, heldout4 and national..national4 regenerate byte-identical `scenarios.json` files.

## Labels I am unsure about (kept)

SF (`heldout5`):

- **h5-03** "pictures of my brain, you lie still in that long loud tube for like forty minutes" -> MRI -
  Brain. A CT is also a tube, so a scheduler could ask MRI vs CT as in h4-28.
- **h5-05** hair coming out in clumps, doctor wants a specialist -> Dermatology Consultation.
  Endocrinology could also be argued.
- **h5-06** itchy, watering eyes from pollen and a cat, "doctor says my eyes are fine and it's a
  reaction" -> Allergy Consultation. Eye care is not offered; refusing `not_offered` is wrong by this
  label.
- **h5-07** scaly patches on elbows and knees -> Dermatology Consultation, not Orthopedics.
- **h5-08** chest burning after big meals, "doctor checked my heart and says it's my digestion" -> GI
  Consultation, not Cardiology.
- **h5-09** ankles swell every night, out of breath lying flat, doctor listened to the heart ->
  Cardiology Consultation, not Orthopedics. Pulmonology could be argued.
- **h5-10** 6-year-old with a stomach ache and vomiting since this morning -> Pediatric Sick Visit.
  Sick Visit or Urgent Care (pediatricians offer both) and GI Consultation could be argued.
- **h5-11** rolled ankle an hour ago, wants to be seen today -> Urgent Care Visit, following h3-09
  (injury, today). Orthopedic Consultation or X-Ray could be argued. Urgent Care and Sick Visit have
  identical providers, so only the label differs.
- **h5-12** eye red, goopy and crusted shut since this morning, "next day or two" -> Sick Visit,
  following h4-11 (illness, within days). Urgent Care could be argued. Eye care is not offered, but
  primary care treats this, so `not_offered` is wrong by this label.
- **h5-13** back tooth throbbing since Sunday, cheek puffy -> Dental Exam. SF has no emergency dental
  type.
- **h5-15** "sit down with the bone doctor here and go over [the hip replacement] first" -> Pre-surgical
  Consultation, not Pre-operative Evaluation (primary-care clearance) or Orthopedic Consultation.
- **h5-16** colon cancer screening, "set it up with the stomach doctor" -> Colonoscopy Consultation,
  not GI Consultation.
- **h5-17** "chest x-ray showed a spot on my lung ... see the lung specialist" -> Pulmonology
  Consultation, not X-Ray or CT.
- **h5-20** "AWV" (one word) -> Annual Wellness Visit.
- **h5-24** "I'm due for a booster shot" -> ask Vaccination / Immunization vs COVID-19 Vaccine. I left out
  Flu Shot (not called a booster). A resolver that asks among three fails on the exact `ask_options`.
- **h5-25** "my usual follow-up with the hormone doctor here" -> ask Diabetes Management vs Thyroid
  Follow-up. I left out Endocrinology Consultation (a first visit) and the General follow-up types.
- **h5-26** "a scan of my lower back, not sure what kind" -> ask MRI - Spine vs CT Scan. X-Ray is left
  out, as in h4-28 and h4-m04.
- **h5-27** breast lump, "imaging of it, I don't know which kind" -> ask Ultrasound vs Mammogram. A
  diagnostic work-up often uses both.
- **h5-29** "keep up my rehab, the weekly sessions for my back" -> refuse `not_offered` (Physical Therapy
  Session). Booking Orthopedics is a wrong commit by this label.
- **h5-x01** new patient, "I have a pacemaker and I need it checked" -> refuse `new_patient_type`. A
  scheduler might suggest a Cardiology Consultation, which new patients may book.
- **h5-x05** new patient, "my doctor found an overactive thyroid and sent me to a specialist" -> refuse
  `new_patient_provider` (Endocrinology Consultation; its only provider is not accepting new
  patients). Reading it as Thyroid Follow-up gives `new_patient_type` instead.
- **h5-p05**: DeepSeek added "she's" ("Dr. Ramirez, she's the nurse practitioner"). Priya Ramirez is
  the only NP Ramirez and the first name reads female, so the label stands.
- **h5-p02, h5-p07, h5-p11, h5-p13** and turn 1 of **h5-m01..m04** expect `ask_field: provider` with
  exactly the doctors who fit the clue and offer the type. A resolver that asks the equivalent question
  under another field name fails on the field.
- **h5-p11** and **h5-m01**: gender is read from first names by hand (Andre and Kenji male; Emily
  female, David male).
- **h5-m03** turn 2 "the one who sees kids" -> Michael Chen NP (Pediatrics). This needs the resolver to
  keep the three Chens from turn 1.
- **h5-m05** turn 2 "not to eat anything after midnight before it" -> Fasting Blood Test, without the
  word "fasting".
- **h5-m05, h5-m06, h5-m07** turn 1 repeats an ask from an earlier set (h4-27 blood work, h3-33
  dental, h4-26 psychiatrist), as h4-m06 reused h3-31. Only the second turn is new.
- **h5-m08** turn 1 "my 10-year-old needs a physical and a form signed" -> ask School Physical vs Sports
  Physical. Well-Child Visit or Annual Physical could be argued.
- `ask_options` are sorted by id, as in earlier sets.

National (`national5`):

- **Symptom `types_any` lists** are my judgement from type names. Scaly elbow and knee patches ->
  Dermatology Consultation or Rash Evaluation (Psoriasis Follow-up is for diagnosed patients). Neck
  lump that moves on swallowing -> ENT, Endocrinology, Thyroid Nodule or Lump or Mass Evaluation.
  Side pain shooting to the groin since last night -> Kidney Stone Evaluation, Urology, Urgent Care or
  Same-Day Sick Visit. Pale, tired, low blood count -> Anemia Evaluation or Hematology. A teenager who
  barely eats -> Eating Disorder Evaluation or Child Psychiatry Evaluation. A newborn who cannot latch
  -> Lactation Consultation only. Leaking urine on coughing -> Urinary Incontinence Evaluation, Pelvic
  Floor Therapy or Urology. A 15-year-old who hit his head at practice yesterday -> Concussion
  Evaluation, Sports Injury Evaluation, Urgent Care, Same-Day Sick Visit or Pediatric Sick Visit.
  Other types could also be acceptable.
- **nat5-gen-01..03** (general variant). The caller asks for CT - Head (Raleigh), Breast Ultrasound
  (Las Vegas) or CT - Chest (Indianapolis). The nearest site for the specific test is 122, 225 and
  168 mi away. The label offers the general CT Scan or Ultrasound in the metro. Treating the general
  type as an acceptable substitute is my judgement. A resolver that refuses `none_nearby`, or offers the
  specific test far away, fails these.
- **nat5-geo-14** "Birmingham, Alabama" + abdominal MRI -> `none_nearby`. The nearest valid site is in
  Atlanta (140 mi; Nashville is 182 mi). The Alabama centroid is also nearest to Atlanta.
- **nat5-geo-13** "Florida" -> ask metro (Miami, Orlando, Tampa).
- **nat5-geo-17/18** (either/or) reuse Glendale (Arizona side) and Pasadena (California side). Earlier
  sets already chose these (town, metro) pairs; the services (ear wax removal, emergency dental) are
  new for those metros.
- **nat5-dup-01..06** reuse cross-metro name groups where needed (`reuse_pools`, `reuse_names`). The
  provider+type and type+metro pairs are new, but some names appeared in earlier sets.
- **nat5-unoff-01** "overnight sleep study" -> `not_offered`, although Sleep Study Consultation and Sleep
  Apnea Evaluation are offered. **nat5-unoff-02** "hair transplant consultation" -> `not_offered`,
  although Hair Loss Consultation is offered. Booking the near neighbour is a wrong commit by these
  labels.
- **nat5-new-05** brand-new patient wants an Occupational Therapy Session -> `new_patient_type`.
  Occupational Therapy Evaluation would be the natural alternative.
- **nat5-ring-01** nerve block in St. Paul -> `none_nearby`. The nearest valid site is in St. Louis,
  463 mi away.
- **nat5-cap-04** "an appointment for dry eyes" in New York -> Dry Eye Evaluation, offered only in
  Boston, Houston and New York.
- Diagnostic mammogram cases accept Mammogram too; shingles cases accept Vaccination / Immunization
  too; ear wax cases accept Ear Cleaning too.
