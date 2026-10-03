# Stress suite

<!-- cases:begin -->
| id | sev | src | phrase | why |
|---|---|---|---|---|
| sf-01 | critical | a | my stomach doctor said I need a scope | Umbrella word. Round 5 sent this to an ENT consultation; a colonoscopy and an endoscopy consultation both fit, so only the caller can choose. |
| sf-02 | critical | a | my baby's checkup | Umbrella word. Well-Child or Newborn; an adult annual physical or a silent pick is wrong. |
| sf-03 | critical | a | some blood work | Umbrella word. Fasting changes the preparation and the slot, so Blood Draw vs Fasting Blood Test is the caller's call (review r5: h4-27 is right). |
| sf-04 | critical | a | my six-month dental checkup | Umbrella word. A six-month checkup is a cleaning plus an exam; h3-33 committed to an exam (round 3). |
| sf-05 | hard | a | I need a mole removed | The caller names a procedure. Probe_everyday asked 'removal or skin cancer screening?' with and without a model. An ask is safe, an offer must be Mole Removal at a surgery site. |
| sf-06 | hard | a | my cardiologist ordered a stress test | The caller names the test and who ordered it; the resolver asked 'cardiology consultation or stress test?'. |
| sf-07 | critical | a | I need a cholesterol check | No-model mode offered Pacemaker Check (appt_025) for a cholesterol check (probe_everyday). A lipid test is blood work (fasting or not); a cardiac visit is wrong. **Unsure:** Whether a cholesterol check should ask or book Fasting Blood Test; both are accepted, anything else is not. |
| sf-08 | critical | b | annual physical + Dr. Patel, the man | Review r3 #1: 'man' sounded like Main St and booked Fatima Patel (and 'the one my boy sees': 'boy' sounded like Bay). Two male-named Patels do this visit; gender never books alone. Any question is fine, a booking is not. |
| sf-09 | critical | b | annual physical + Dr. Garcia, not the one who speaks Spanish | Review r3 #1: negation ignored, so the Spanish speaker (Carlos Garcia) was booked. The two Marias are the only fit. |
| sf-10 | critical | b | annual physical + Dr. Chen, the one my daughter recommended | Review r3 #1: 'daughter' was read as Pediatrics and Michael Chen (NP, Pediatrics) was booked. |
| sf-11 | critical | b | sick visit + Dr. Linda Ramirez | Neither Dr. Linda Ramirez (cardiology, radiology) does a sick visit; Dr. Priya Ramirez (NP) does. Booking Priya is a different person (review r5, out-of-scope note). |
| sf-12 | critical | b | sick visit + Dr. Priya Ramirez -> actually, Dr. Linda Ramirez | Same swap after a mind change: 'actually Dr. Linda Ramirez' offered Dr. Priya Ramirez without saying so (review r5). |
| sf-13 | critical | b | My knee still hurts after PT, I want a doctor to look at it | Review r4 #2: 'PT' as context made the agent refuse 'we don't offer physical therapy'. The request is an orthopedic look. A refusal is a false refusal; an ask is fine. |
| sf-14 | critical | b | I need physical therapy for my shoulder | The contrast to the PT-as-context case: here PT is the request, and the SF catalog has no PT provider. Offering any visit as if it were PT is wrong. |
| sf-15 | critical | c | an MRI of my knee | New patients cannot book MRI - Knee (returning only). Dev set covers echo; this is the imaging variant from the design doc. |
| sf-16 | critical | c | cardiology consultation + Dr. Chen, the heart doctor | Design doc example: Dr. David Chen is not accepting new patients, so a new patient gets Dr. Emily Chen with no question. |
| sf-17 | critical | c | a brain MRI | A brain MRI needs a referral and the caller has none. |
| sf-18 | critical | c | annual physical + Dr. Lucas Chen + Downtown | Dr. Lucas Chen works only at Mission Bay. Booking him at Downtown, or another Chen there, is wrong. |
| sf-19 | critical | c | annual physical + Dr. Emily Chen | Dr. Emily Chen is a cardiologist and does not do annual physicals. |
| sf-20 | hard | d | sick visit + the clinic on Market Street | Two clinics on one street (Mission District 4732 and Midtown 2099 Market St): no number, so ask which. |
| sf-21 | critical | d | sick visit + the one at 4732 Market Street | Street plus house number picks Mission District; Midtown (loc_005) is the wrong clinic. |
| sf-22 | critical | d,f | sick visit + forty seven thirty two Market Street | A house number said as words (4732). STT writes numbers many ways. |
| sf-23 | critical | d | blood draw + the clinic on Geary Boulevard | Two Geary clinics, neither has lab providers (Dr. Leila Sato works at Mission Bay, North Beach and North Gate; only Mission Bay has the lab). Offering Mission Bay silently is a wrong site; the right answer names it as the alternative. |
| sf-24 | critical | e | some blood work -> the fasting one | The caller answers the umbrella question by label. Must land on Fasting Blood Test, not the other option. (Turn 1 is not scored here, so an umbrella commit is counted once, in the blood-work umbrella case.) |
| sf-25 | critical | e | my baby's checkup -> the second one | Answer by ordinal: the second spoken option is Newborn Visit. Picking the first, or anything else, is wrong. **Unsure:** Assumes the spoken order is catalog order (Well-Child, then Newborn), as the first-turn ask_options pins. |
| sf-26 | critical | e | my baby's checkup -> the checkup | The caller repeats the vague word. The same question is asked again; review r5 #5 saw it jump to adult annual-physical options. |
| sf-27 | hard | e | sick visit + Dr. Maria Garcia -> the nurse practitioner | Two Dr. Maria Garcias: prov_002 is an MD (pediatrics), prov_003 an NP. 'The nurse practitioner' means prov_003. |
| sf-28 | critical | e | cardiology consultation + Dr. Chen -> the lady one -> no, not her, the other one | The agent gets a woman Chen (Emily, prov_046), the caller says no. The other Chen is the answer; Emily must not come back. **Unsure:** Turn 2 is left unscored (confirm or offer are both defensible); only the 'no' turn is. |
| sf-29 | hard | e | flu shot -> actually, make it the COVID vaccine instead | A mid-call change of mind: the first type must not linger. |
| sf-30 | critical | e | annual physical -> actually I need a follow-up visit instead | The change of mind lands on a returning-only visit; the earlier offer must not carry over. |
| sf-31 | hard | f | X-ray + Dr. Hanna Nwin | STT 'Nwin' for Nguyen. Hannah Nguyen (prov_015) is the only Hanna Nguyen; the X-ray site must have imaging. |
| sf-32 | critical | f | annual physical + uh, um, the, the lady one | Fillers and a gender word with no name. Gender never books alone and the catalog has no gender field. |
| sf-33 | critical | f | I need a, uh, a flu, no wait, a COVID shot | Self-correction in the same breath: the caller ends on COVID. A flu shot is the wrong vaccine. |
| sf-34 | hard | f | I need a flu shot and also a dental cleaning | Two visits in one sentence. One visit per booking: ask which first, or take one; a merged or third visit is wrong. |
| sf-35 | critical | a | start PT after my shoulder surgery | h3-34: round 3 booked an Orthopedic Consultation. PT here is the request and the SF catalog has no PT provider. |
| sf-36 | critical | a | my doctor, he found problem with my hormones in the blood test, said I need a specialist for hormones and the glands | h3-x04: the only endocrinologist is closed to new patients; round 3 booked a Blood Draw instead, a different visit. |
| sf-37 | critical | a | sick visit + the nurse practitioner, Dr. Hernandez | h3-p04: the only NP Hernandez (Patricia, Internal Medicine) is closed to new patients; round 3 booked Dr. Tomas Hernandez (MD). |
| sf-38 | critical | a | my 3-year-old daughter, she's had a fever since last night and she keeps tugging at her ear | h3-10: a 3-year-old needs the Pediatric Sick Visit; round 3 booked the adult Sick Visit. |
| sf-39 | critical | a | I need my Tdap booster, it's been more than ten years | h3-24: Tdap is a Vaccination / Immunization; round 3 booked the COVID-19 vaccine. |
| sf-40 | critical | a | doctor wants pictures of my lungs, the chest films | h3-26: chest films are an X-Ray at an imaging site; round 3 booked a Pulmonology Consultation. |
| nat-01 | critical | a | my stomach doctor said I need a scope + Houston | Umbrella word on the national catalog: Colonoscopy, Upper Endoscopy and two consultations all fit. |
| nat-02 | critical | a | some blood work + Chicago | Umbrella word: Blood Draw, Fasting, Lipid Panel, A1C and others all fit. |
| nat-03 | critical | a | my baby's checkup + Seattle | Umbrella word: Well-Child, Newborn, Developmental Screening fit. |
| nat-04 | critical | a | my six-month dental checkup + Dallas | Umbrella word: cleaning and exam, plus dental X-rays. Review r5 relabelled the national2 dental cases the same way. |
| nat-05 | critical | a | I need my a1c + Dallas | Review r3 #3: 'a1c' booked Diabetes Management and dropped the named A1C Test. |
| nat-06 | critical | a | I need an upper endoscopy + Nashville | Review r3 #3: 'upper endoscopy' dropped Upper Endoscopy (EGD) for a consultation. |
| nat-07 | hard | a | I'd like to book a colonoscopy + Philadelphia | Probe_everyday: the named procedure is Colonoscopy, not Colonoscopy Consultation. **Unsure:** A reviewer may accept an ask between the procedure and the consultation; an ask is scored safe either way. |
| nat-08 | hard | a | I want to see a therapist + Seattle | A new patient asking for therapy: Therapy Session (appt_060) is returning-only. Individual Therapy is open to new patients. Probe_everyday booked the returning-only visit without a model. |
| nat-09 | critical | b | I need a mammogram for a lump in my breast + Houston | Review r5: a lump needs the Diagnostic Mammogram; a screening or 3D screening mammogram is the wrong test. |
| nat-10 | critical | b | pelvic floor therapy after my delivery + Chicago | Review r5 blocker: related-visit fallback sent this to psychiatry's Therapy Session. |
| nat-11 | critical | b | my daughter needs a pediatric echocardiogram + New York | Review r5 blocker: the adult Echocardiogram (appt_021) is a different visit. Pediatric Cardiology offers appt_294 in New York. |
| nat-12 | critical | b | my daughter needs a pediatric echocardiogram + Chicago | No Chicago clinic does appt_294 (the nearest is another metro). The adult echo is not a stand-in; ask or name the far option. |
| nat-13 | critical | b | adult ADHD evaluation + Chicago | Review r5 blocker: the related-visit fallback offered the pediatric ADHD Evaluation (appt_106). |
| nat-14 | critical | b | a nuclear stress test + Houston | Review r5 blocker: 'nuclear stress test' was generalised to the plain Stress Test. |
| nat-15 | critical | b | a prenatal ultrasound, I'm 12 weeks + Houston | Review r5 blocker: sent to the Prenatal Visit. At 12 weeks the right scan is Prenatal Ultrasound (not the 20-week anatomy scan). |
| nat-16 | critical | b | MRI of my spine + Chicago | Review r5 blocker: sent to the Spine Consultation (orthopedics). |
| nat-17 | critical | b | a CT of my chest, my doctor ordered it + Dallas | Dallas offers CT Scan (appt_066) but not CT - Chest (appt_210; the nearest is Fort Worth). Review r5: the generic CT is a different visit; say the chest CT is farther and ask. |
| nat-18 | critical | b | my PT says my hip needs to be seen by a specialist + Chicago | Review r4 #2: PT is context. The request is a specialist look at the hip (orthopedic consultation, hip evaluation or sports medicine). Refusing 'we do not offer PT' is a false refusal; so is booking PT. **Unsure:** Which orthopedic-side visit is best is a judgement; the allowed set is Orthopedic Consultation, Hip Pain Evaluation, Sports Medicine Consultation, Joint Replacement Consultation. |
| nat-19 | hard | b | a knee MRI + Dr. Joseph White + near Raleigh | Review r4 #6: Dr. Joseph White (PA, Family Medicine) is in Raleigh but does not do knee MRIs; the other Joseph White is a cardiologist in Nashville. The reason is 'he does not do that visit', not 'he is not nearby'. (The review used IUD insertion, which he does offer in this catalog, so the knee MRI is the faithful probe.) |
| nat-20 | critical | c | an MRI of my shoulder + Chicago | MRI - Shoulder is returning-only. A new patient cannot book it. |
| nat-21 | critical | c | a physical therapy session + Houston | PT as the request, and the Session is returning-only for new patients (the Evaluation is open). Demo scenario. |
| nat-22 | critical | c | cardiology consultation + Dr. Joseph White + Nashville | The Nashville Dr. Joseph White (cardiology) is not accepting new patients. |
| nat-23 | critical | c | a colonoscopy + Philadelphia | Colonoscopy needs a referral and the caller has none. |
| nat-24 | critical | c | I need a chiropractor for my back + Houston | Chiropractic exists in the catalog but no provider offers it. Back Pain Evaluation (orthopedics) may be suggested, never booked. |
| nat-25 | critical | c | flu shot + Dr. Caryn Lawler + Chicago | Dr. Caryn Lawler (prov_4733) works only in Boston. Offering a Chicago doctor with a similar name, or Boston silently, is wrong. |
| nat-26 | hard | d | flu shot + the clinic on Church Street in Chicago | Two Chicago clinics on Church Street (Pilsen 371, West Ridge 5926): no number, so ask which. |
| nat-27 | critical | d | flu shot + the clinic at 4162 Broadway | Broadway is also in Oakland and King of Prussia; the number 4162 is Fishtown only. Wrong clinic = wrong city. |
| nat-28 | critical | d | flu shot + Trenton | Trenton, NJ is not a clinic city; Renton, WA is (loc_055, Seattle, 2,400 miles away). A fuzzy match to Renton must be confirmed, never booked. |
| nat-29 | critical | d | flu shot + Washington | Washington is both a state (Seattle, Bellevue, Renton) and DC. Never pick one silently. |
| nat-30 | critical | d | flu shot + Virginia | Review r4 #1: a lone state became its metro and booked DC Downtown, ~150 miles from Virginia Beach. Only the Virginia clinics (Arlington, Alexandria) are 'in Virginia'. |
| nat-31 | critical | d | flu shot + Virginia Beach | Review r4 #1: the nearest clinic is Alexandria, ~190 miles away, and was booked without saying a distance. |
| nat-32 | critical | d | flu shot + Wichita, Kansas | Review r4 #1: Kansas City MO is ~175 miles from Wichita and was booked silently. |
| nat-33 | hard | d | flu shot + New Jersey | Review r4 #1: New Jersey has one clinic (Cherry Hill); the state became the Philadelphia metro. Only Cherry Hill is in the state. |
| nat-34 | hard | d,f | flu shot + my zip code is nine eight one zero eight | ZIP said as words (98108 is Capitol Hill, Seattle). A mis-parse would land on another metro. |
| nat-35 | critical | d | flu shot + Capitol Hill | Capitol Hill is a Seattle clinic (loc_049) and a Washington DC clinic (loc_246). |
| nat-36 | critical | d | flu shot + I live in Boise, Idaho | No clinic in Idaho; Salt Lake City is ~340 miles away. Say so; never book it as if near. |
| nat-37 | critical | d | flu shot + what's the closest city to Boulder | Boulder is not a clinic city; Denver is ~25 miles. A fair answer names Denver clinics and the distance. |
| nat-38 | critical | d | flu shot + I'm in Boulder, Colorado | Same Boulder, said plainly. Denver is the nearest metro; any other city is far wrong. |
| nat-39 | critical | e | some blood work + Philadelphia -> the A1C test | Answer to the umbrella question by name; must book the A1C Test. (Turn 1 is not scored here; the umbrella commit is counted once, in the blood-work umbrella case.) |
| nat-40 | hard | e | sick visit + Dr. Maria Garcia + San Francisco -> the one who speaks Arabic | Two Dr. Maria Garcias in San Francisco: prov_002 speaks Mandarin, prov_003 speaks Arabic. Languages are catalog facts. |
| nat-41 | hard | e | flu shot + Seattle -> actually I'll be in Dallas that week | A mid-call change of city; no Seattle site may survive. |
| nat-42 | critical | e | a knee X-ray + Chicago -> ok, then an orthopedic consultation for my knee | After a refusal the caller switches to a visit a new patient may book; the refusal must clear and the new type must be exact. |
| nat-43 | critical | a | I'd like to get an IUD inserted + Dr. Joseph White + near Raleigh | nat3-dup-06: Dr. Joseph White (PA) is in Raleigh and does IUD insertion; round 3 said he 'isn't at any of our clinics within 50 miles'. A false refusal. |
| nat-44 | critical | a | I need a thyroid ultrasound, yeah a thyroid ultrasound + River North Care Center, over in Chicago — that's the one I always go to | nat3-cap-05: River North has no imaging; round 3 booked a Thyroid Follow-up there, a different visit at the named site. |
| nat-45 | critical | a | want to get my spine X-rayed, doctor ordered it + 60626 | nat3-geo-07: round 3 booked a Spine Consultation at River North and Pilsen, farther than the ZIP. |
| nat-46 | hard | a | I gotta get a drug test for a new job + I'm over by Rockridge | nat3-geo-10: round 3 booked clinics in Santa Clara and Evergreen for a caller in Rockridge (Oakland). |
| nat-47 | hard | a | I want make an annual physical + Dr. Inna Volkov, a friend told me about her + in Phoenix | nat3-new-03: Dr. Volkov is closed to new patients; round 3 said she is not within 50 miles and sent the caller to Atlanta, 1,588 miles away. |
| nat-48 | hard | f | sick visit + Dr. Joseph Whyte + Raleigh | STT 'Whyte' for White. Only the Raleigh Dr. Joseph White (PA) is in range; the other is in Nashville. |
<!-- cases:end -->
