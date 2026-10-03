# Held-out round 4: label notes

Covers `eval/cases_heldout4.jsonl` (SF) and `eval/cases_national4.jsonl`. Both were authored blind on
2026-10-03. I read no resolver code (`backend/scheduling/**`), no resolver results (`eval/results/**`),
no `.kstack` files and no README sections about results or misses, and I scored nothing. Every
expectation comes from catalog queries in the two generators. This file lists every change made after
reading a DeepSeek phrasing, and every label I was not fully sure of. Nothing was dropped.

## Changes made after reading a phrasing

SF (`heldout4`). All four were re-asked in one call (`deepseek_raw/repair_01.json`); no label changed.

- **h4-01** (Hearing Test / Audiogram). DeepSeek wrote "the hearing test where you sit in a quiet
  booth...", which names the type. I reworded the scenario so the caller does not call it a hearing
  test. The new phrasing is "want to get my hearing looked at ... raise your hand every time you hear
  the beep".
- **h4-23** (Pacemaker Check, slang "pacer"). DeepSeek wrote "I've got a pacemaker ... my pacer check",
  because my scenario mentioned the pacemaker. I reworded it to describe the device without naming it.
- **h4-05** (Cardiology Consultation) and **h4-08** (Pulmonology Consultation). Both phrasings passed
  the checks but dropped the organ: "my doctor wants me to see a specialist". Without "heart" or
  "lung", three months of breathlessness and cough could mean either specialist, so a single-type
  label would no longer be the clear answer. I added a required word (`heart`, `lung*`) and
  regenerated. The new phrasings say "heart specialist" and "lung specialist".

National (`national4`): no changes. All 48 first-pass phrasings passed the checks and read as labeled.

## Generator changes

- `eval/heldout3/build_cases.py` takes `--set heldout3|heldout4`. A set is a Profile; heldout4 adds
  two-turn picks. heldout3 still regenerates byte-identical content (`scenarios.json` and
  `cases_heldout3.jsonl` differ from the commit only by CRLF on Windows).
- `eval/national/build_cases.py` gains the `national4` profile (seed 20261004) with new service,
  symptom and type lists. Only 18 cross-metro name groups exist, and the first three sets used 16.
  A new `reuse_names` flag (national4 only) lets the with-city duplicate-name kind reuse a group,
  choosing a type whose provider+type and type+metro no earlier set used. national, national2 and
  national3 regenerate unchanged. The `scenarios` step now also reports provider+type overlap. For
  national4 it is 0/48, and type+metro overlap is 0/48.

## Labels I am unsure about (kept)

SF (`heldout4`):

- **h4-29** "sign up here and see a doctor for the first time, no real problem" (new patient) -> ask
  New Patient Consultation vs New Patient Visit. The two types have identical providers. Booking either
  one without asking could be argued correct, as with h3-32 and h3-33.
- **h4-26** "my next one with the psychiatrist, the usual" -> ask Therapy Session vs Medication
  Management. I left out Psychiatric Evaluation because it is a first visit. A resolver that asks
  between all three fails on the exact `ask_options`.
- **h4-27** "some blood work" -> ask Blood Draw / Lab Work vs Fasting Blood Test. A scheduler could
  default to plain lab work.
- **h4-28** "a scan done of my head" -> ask MRI - Brain vs CT Scan.
- **h4-m04** turn 1 "a scan of my belly" -> ask CT Scan vs Ultrasound. The SF MRI types are brain,
  spine and knee only, and I do not count a plain X-ray as a "scan".
- **h4-m05** turn 1 "my baby's checkup" -> ask Well-Child Visit vs Newborn Visit. Turn 2's
  `service_phrase` is only the age, "two weeks old". Mapping it to Newborn Visit needs the pending
  question's context.
- **h4-m03** turn 2 replaces the provider phrase with "the nurse practitioner". Five NPs offer Sick
  Visit, so the right pick (Maria Garcia, NP) depends on the resolver keeping the two Maria Garcias
  from turn 1.
- **h4-m02** answers a provider question ("which Dr. Nguyen?") with a clinic ("the one at the
  Downtown clinic"), as a `location_phrase`.
- **h4-10** sinus symptoms -> Sinus Evaluation, not ENT Consultation. There is one provider for both,
  so the label is all that changes.
- **h4-11** "sore throat, fever and body aches for three days, I'd like to see my doctor this week" ->
  Sick Visit, not Urgent Care Visit. The two types have identical providers.
- **h4-18** "eight weeks pregnant ... my first check-up for the pregnancy" (existing patient) ->
  Prenatal Visit, not OB/GYN New Patient Visit.
- **h4-19** "my baby, born last week ... the first check-up" (new patient) -> Newborn Visit, not
  Well-Child Visit or New Patient Visit.
- **h4-12** "go over my recent test results ... on a video call" -> Telehealth Follow-up.
- **h4-16** "Botox for the wrinkles on my forehead" -> Cosmetic Consultation, the only SF type that fits.
- **h4-x02** new patient "I need an EKG of my heart" -> refuse `new_patient_type`. A scheduler might
  suggest a Cardiology Consultation, which new patients may book, but the type asked for is refused.
- **h4-x04** new patient "my yearly women's exam with the gynecologist" -> refuse
  `new_patient_provider` (the only OB/GYN is not accepting new patients). A resolver that reads it as
  Annual Physical would offer instead.
- **h4-p03, h4-p07, h4-p08, h4-p12** and turn 1 of **h4-m01..m03** expect `ask_field: provider` with
  exactly the doctors who fit the clue and offer the type. A resolver that asks the equivalent question
  under another field name fails on the field.
- **h4-p10, h4-p11**: gender is read from first names by hand (Emily and Nina female, Daniel and
  Carlos male). Wei is treated as unknown. Neither Wei offers the type, so the labels do not depend
  on it.
- **h4-p09** new patient + "Dr. Michael Sato" -> offer prov_032 with no question. The other Michael
  Sato is not accepting new patients.
- `ask_options` are sorted by id, as in heldout2 and heldout3.
- Not authored: a "says yes to a confirmation" turn. `validate_case_format.py` accepts only non-empty
  string updates, so the dev set's `pick_offer` (int) and `is_new` (bool) turns would be flagged. The
  six two-turn cases answer a service or provider question instead.

National (`national4`):

- **Symptom `types_any` lists** are my judgement from type names: night urination with a weak stream
  -> Urology Consultation or PSA screening; burning urination -> Sick Visit, Same-Day Sick Visit, Urgent
  Care or Urinalysis; a 2-year-old with few words -> Developmental Screening, Autism Evaluation or
  Well-Child; ingrown toenail -> Ingrown Toenail Removal, Podiatry Consultation or Minor Procedure
  Visit; swollen hand joints with a facial rash -> Rheumatology Consultation or Arthritis Evaluation.
  Other types could also be acceptable.
- **Strep test** (nat4-geo-06, geo-08, geo-17, noloc-01) accepts only Strep Test. A resolver that books
  a Sick Visit for "sore throat, need a strep test" fails these labels.
- **nat4-unoff-01** "lithotripsy" -> `not_offered`, although Kidney Stone Evaluation is offered.
  **nat4-unoff-02** "prenatal genetic counseling" -> `not_offered`, although Prenatal Visit is offered.
  Booking the near neighbour is a wrong commit by these labels.
- **nat4-new-05** brand-new patient wants a Postpartum Visit -> `new_patient_type`. OB/GYN New
  Patient Visit would be the natural alternative.
- **nat4-geo-16** "Boise, Idaho" + ankle MRI -> `none_nearby`. The nearest valid site is Seattle:
  405 mi from Boise and 443 mi from the Idaho centroid. Salt Lake City is closer but has no ankle-MRI
  site.
- **nat4-geo-13** "North Carolina" -> ask metro (Charlotte and Raleigh are about 130 mi apart).
- **nat4-geo-14/15** reuse the twin towns Lakewood (Ohio side) and Pasadena (California side), each
  with a metro no earlier set chose for it.
- **nat4-dup-01..03** (no city given) reuse the Scott Miller, Chioma Getachew and James Caskey groups
  with new types (COVID-19 Vaccine, Pelvic Pain Evaluation). dup-01 and dup-02 are both a COVID
  vaccine answered with Baltimore.
- **nat4-dup-04..06** (with city) reuse the Vijay Joshi, Laura Gomez and Omar Sabbagh groups through
  `reuse_names`. The provider+type and type+metro pairs are new, but the names appeared in earlier sets.
- **nat4-ring-01** eye exam in Cleveland -> `none_nearby`. The nearest eye care is in New York, 403 mi away.
