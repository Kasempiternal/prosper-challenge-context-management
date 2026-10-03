# Live-call tests for two people

Open **http://localhost:5174**. Use **National Scheduler** and keep **Disambiguator: JEV** unless a test says otherwise. Press **D** for Dev view. One person speaks; the other watches the transcript, tool calls and Decisions tab. Swap roles halfway. Start a new call for each numbered test, so earlier bookings do not affect it.

The resolver steps below were checked offline: **12 scenarios, 24 turns**, using the committed JEV cache. The full text simulator also passed through the real tool handlers and booking path. These checks cannot verify speech recognition or how the conversation model extracts your words. That is what these live calls test. Live calls spend OpenAI and ElevenLabs credits when you start them.

The current evidence is **0 unsafe JEV outcomes on 88 seen hard cases**, but fresh blind round 5 still has **4/38 wrong commits on SF and 1/35 nationally**. The final section lets you try those known failures too. Passing a few calls does not prove the agent never fails.

## 1. Shared street, spoken house number, and booking consent

Say: **“I'd like a sick visit. I've had a fever since yesterday. I'm a returning patient.”**

If it asks “Is that a sick visit?”, say **“A sick visit.”** When it asks the city, say **“Market Street in San Jose.”**

It should ask **“Downtown at 1812 Market or Willow Glen at 3330 Market?”** Say **“Thirty-three thirty.”** It should offer **Willow Glen**, not Downtown or another city.

Before choosing a time, try **“Sure, book it.”** It must ask which time; no booking reference yet. Then say **“The first time.”** It must read that exact visit, doctor, clinic and time back. Say **“Yes, book that.”** Only then should a real confirmation reference appear in the transcript and tool results.

The script's offline replay passes the entire fever phrase to the resolver; with a missing cached answer it asks for the visit confirmation. The conversation model may extract only “sick visit” and skip that extra question. Both paths should reach the same street question.

## 2. Same name, language answer, no distant doctor swap

Say **“A sick visit with Dr. Maria Garcia in San Francisco. I'm a returning patient.”** When asked which Dr. Garcia, answer **“The one who speaks Arabic.”**

It should offer the family-medicine **Dr. Maria Garcia**, locally. The checked replay offers Richmond; that doctor's valid sites also include North Beach and Midtown. **Fort Worth, Philadelphia, or another distant namesake is a failure.**

## 3. A place heard by sound

Say **“A flu shot in Trenton. I'm a returning patient.”** It should ask **“Did you mean Renton, Washington?”**, before offering a time. Say **“No, Trenton, New Jersey.”**

It should explain that its nearest New Jersey clinic for this visit is **Cherry Hill, near Philadelphia**, and ask whether to look there. It must not book Renton after your “no.” The catalog has no Trenton point, so it cannot claim a measured distance from Trenton.

## 4. The Washington answer loop

Say **“A flu shot in Washington. I'm a returning patient.”** It should ask **“Seattle, Washington or Washington, DC?”** Say only **“Washington.”**

It should explain that it still needs one of those cities, or ask for a ZIP. The checked reply is **“Sorry, I still need to know which one… Or tell me your ZIP code.”** Then say **“Seattle, Washington.”** It should offer Seattle-area clinics. Repeating the same first question forever or switching to DC without your answer is a failure.

## 5. Unknown city with question words

Say **“A flu shot. What's the closest city to Boulder? I'm a returning patient.”**

It should ask you to clarify the place. This catalog has no Boulder anchor. **Offering Center City near Philadelphia is a failure.** Asking here is the safe behavior; there is no geocoder to invent Boulder coordinates.

## 6. Street versus clinic name, and ambiguous blood testing

Say **“A blood test on Lincoln Avenue in Salt Lake. I'm a returning patient.”** It should ask what test: **blood draw, fasting blood test, or lipid panel**. Answer **“Blood draw.”**

It should offer **Sugar House**, on Lincoln Avenue. **The Avenues clinic is a failure.** The “blood test” clarification is expected; the agent should not guess whether you need fasting or cholesterol testing.

## 7. An umbrella request that needs a question

Say **“My stomach doctor said I need a scope. I'm in Houston, I'm a returning patient, and I have a referral.”**

It should ask between **endoscopy consultation and upper endoscopy**. An immediate GI consultation offer is a failure. The specific pair is catalog-dependent; use the SF test below for the upper/lower doubt.

## 8. Dental checkup versus cleaning

Say **“My six-month dental checkup in Houston. I'm a returning patient and I have a referral.”**

It should ask **“Dental cleaning or dental exam?”** It must not silently choose one. Try replying **“The checkup.”** If that does not identify one, a clarification is appropriate; then name the visit you want. The initial turn is checked offline; the extra vague answer deliberately tests the live conversation.

## 9. A new-patient restriction

Say **“I need allergy shots in Sacramento. I'm a new patient and I have a referral.”**

It must refuse allergy shots for a new patient. It can ask if you want an **allergy consultation** instead. It must not turn the shots into a consultation without asking.

## 10. Explicit uncertainty, upper versus lower

Switch to **Clinic Scheduler (SF)**, JEV. Say **“The GI doc wants a scope. I don't remember if it goes down my throat or up from below. I'm a returning patient and I have a referral.”**

It should ask **“Colonoscopy consultation or endoscopy consultation?”** A confident offer before you answer is a failure.

## 11. Policy resolves an ambiguous doctor name

Use **Clinic Scheduler (SF)**. Say **“A cardiology consultation with Dr. Chen. I'm a new patient and I have a referral.”**

It should offer **Dr. Emily Chen**. David Chen does not take new patients. It should not ask between both as if both could be booked for you.

## 12. JEV Off versus On, with a confirmation

Use **Clinic Scheduler (SF)**. Make two separate calls with the same words:

**“A cardiology consultation with Dr. Chen. I'm a returning patient and I have a referral.”** Then answer **“The lady one.”**

- **Off:** it asks between David Chen and Emily Chen again. The catalog has no gender field.
- **JEV:** it should ask **“Do you mean Dr. Emily Chen?”** It must confirm the inferred doctor, not silently book from gender alone.

Live model answers can produce more questions than the cache replay. Check the Dev view's active mode and whether the reply stayed within the asked doctors. The switch is changed before starting each call.

## Known failures worth trying after the twelve checks

These exact phrases failed in the one-time round 5 score. They are **failure reproductions, not passing demos**. Code was not changed after scoring. Use a new call each time; say you are returning and have a referral.

| Agent | Say | Correct behavior | Frozen-code failure |
|---|---|---|---|
| SF | “Doctor ordered a scan of my lower back, not sure what kind.” | Ask which scan: spine MRI or CT scan | Offers spine MRI immediately |
| SF | “My next usual appointment with my psychiatrist.” Then “The one where we go over my medication and get refills.” | Ask initially, then medication management | Immediately offers medication management, then changes to medication review |
| SF | “My 10-year-old needs a physical and a form signed by the doctor.” Then “It's for the basketball team.” | Ask school or sports, then sports physical | Offers a school physical before the purpose is clear |
| National | “There's a lump at the front of my neck, it moves up and down when I swallow. I'm in Indianapolis.” | A suitable local visit | Refuses and suggests a distant lump/mass evaluation in Chicago |

Other open risks: a named doctor can narrow “blood test for my cholesterol” without a clarification; “Richmond or Mission Bay” can silently pick the latter; Off can choose adult neurology for a child; SF mammograms have no diagnostic marking. Record these if encountered. No case justifies a universal “never fails” claim.

## What to record

For each call, record **PASS / FAIL / EXTRA QUESTION**, the exact words you said, what the agent replied, and any booking reference. Save or keep the call transcript and Decisions tab. The observer should check:

- The transcript heard your city, language and house number correctly.
- Tool arguments kept your intended request and location.
- The chosen doctor stayed within the question's options unless you actually named a different doctor.
- A known place's offer stayed within 50 miles; a guessed place was confirmed first.
- A refusal's distant alternative was disclosed and asked about, not silently adopted.
- A booking happened only after a time choice, read-back and your “yes.”

To repeat the free resolver verification:

```powershell
backend/.venv/Scripts/python backend/tools/check_live_script.py
backend/.venv/Scripts/python backend/tools/text_sim.py
```

The machine-readable steps are in [live-call-cases.json](live-call-cases.json). Live voice, optional interruptions and extra challenge replies remain manual checks.
