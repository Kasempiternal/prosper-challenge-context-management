# Solution overview

Agent Studio (Phase 1) edits a voice agent's node graph and places a live test call. The scheduling agent (Phase 2) books across a clinic catalog without ever putting the catalog in the LLM's context. It runs unchanged on the provided SF catalog and on a generated national catalog with 183 times as many bookable rows.

How to run it, every number and the full design: [README.md](README.md), [docs/PHASE2_DESIGN.md](docs/PHASE2_DESIGN.md), [eval/README.md](eval/README.md).

## The requirements, and where each is met

| Brief | Where |
|---|---|
| A UI to create an agent and place a test call | Agent Studio: **New agent**, graph editor, inspector, live validation, **Test call** panel with transcript, decisions, latency and cost |
| Create appointments | `confirm_booking` edge: refused until the caller picked a time, heard it read back and said yes. The booking happens in code and speaks the real reference |
| Offer available slots | Up to 3 times from a seeded availability mock, spoken from a template |
| Match type, location and provider; disambiguate; honor preferences | The resolver: aliases and lay terms for the visit, names plus catalog facts for the doctor, clinic / street / house number / city / ZIP for the place, "soonest" and day / part-of-day preferences |
| Answer questions about locations, doctors and visit types | `lookup` tool: hours, address, languages, new-patient status, referral and new-patient rules, "do you offer X". At most 5 facts per answer |
| OpenAI and ElevenLabs | Both, unchanged from the starter pipeline. Command Code JEV is an optional third provider for disambiguation |

## The core decision: the LLM extracts, code decides

The naive approach puts the catalog in the prompt. On the SF sample that is 8,252 tokens per LLM call. On the national catalog it is 664,495 tokens, 5.2 times a 128k window, so it does not fit at all (measured, tiktoken `o200k_base`). Size is the smaller problem. With the list in context, the model must also apply six cross-entity policies by reading: referral rules, new-patient rules, capability-gated services and providers at several sites. Nothing stops it from booking an MRI at a site with no imaging.

So the LLM never sees the catalog:

1. **Extraction only.** The LLM turns the caller's words into one `update_request` tool call: what the visit is for, the doctor, the place, new or returning, referral, timing. It passes the caller's words, not catalog ids.
2. **A precomputed policy table.** At server start, `CatalogIndex` joins providers, locations, visit types and capabilities into bookable rows (760 for SF, 138,870 national). `policy.check` is the only home of the booking rules. It runs in the resolver and again at booking time, so a model mistake cannot book a policy violation.
3. **A deterministic resolver returns one move.** Offer up to 3 times, ask the one question that changes the valid set, or refuse with the reason and the nearest valid alternative. "Dr. Chen" for a new patient needs no question, because policy removes the Dr. Chen who takes no new patients. For a returning patient both are valid, so the agent asks.
4. **Templates speak the result directly.** Names, times and clinics go straight to text-to-speech from the catalog. The LLM cannot paraphrase them into something false, and its second round trip is skipped.

Result: the prompt stays near 600-2,300 tokens whatever the catalog size. Estimated gpt-4o input for 15 SF calls drops from about $0.32 to about $0.06. The resolver answers in under 7 ms at p95 on the national catalog (measured). After this, speech synthesis is about 80% of a call's cost, so the LLM is no longer the cost to cut.

## Where a model still helps, and how it is kept safe

Words alone cannot settle everything: "the lady doctor", "something for my back pain", "a scope". For those turns the resolver may consult a disambiguator: Command Code JEV, gpt-4o-mini logprobs, local embeddings, or none. The **Disambiguator** switch in the call panel picks the mode per call. The model only picks among options the resolver names. Its answer is gated:

- A choice stands only if a second question, the check, confirms it ahead of the rival and an "either" answer. Otherwise the caller is asked.
- A model answer that times out or fails is never acted on: the caller is asked.
- A stated doubt ("I don't remember if it goes down my throat or up from below") is never narrowed by a model.
- A doctor inferred from gender is confirmed by name, never booked silently.
- A place heard by sound is confirmed first ("Did you mean Renton, Washington?"). Nothing is offered past 50 miles without saying so.

The guiding rule: **in healthcare a wrong booking is the costly error, and one more question is cheap.** So the headline metric is wrong commits per commit, and top-1 accuracy comes second.

## Evidence

- **Blind rounds.** Case sets written without seeing the code, frozen in a commit before the fixes they judge, scored once. Latest (round 5, JEV): SF 4 wrong commits in 38 (10.5%), national 1 in 35 (2.9%). Dev sets read 0 wrong commits; that gap is the honest measure. Re-run after the later live-call fixes (no longer blind, not tuned on): SF 3 in 36, national 0 in 36, so those fixes broke nothing there.
- **Modes compared** on every blind set: JEV has the fewest wrong commits on SF doctor descriptions; OpenAI matches or beats it on some national sets; Off is the rules path and asks more.
- **88 hard cases** (the most critical and the ones that failed in earlier runs): 0 unsafe JEV outcomes.
- **Live voice calls** found what offline cases cannot: the conversation model claimed a booking it never made, and it rewrote callers' answers ("Washington" into "Washington, DC", "the lady one" into "Dr. Emily Chen", a doubt into "scope"). Each became a code-level guard, not a prompt tweak. Booking moved into an edge action, and tool arguments are now checked against the caller's own words. Replaying those turns showed gpt-4.1 makes the same rewrites, so a stronger model was not the fix.
- 1,314 backend and 125 frontend tests.

Three integrity exposures are disclosed with the scores they could have affected: a test that resolved the blind files (it asserted nothing about them), a worker whose search previewed five blind cases, and three lines of round 4 results shown to the round 5 author after every case was final. Details in the README and [eval/README.md](eval/README.md).

## Trade-offs accepted

| Choice | Cost of it |
|---|---|
| Hand-written aliases and lay terms (SF 237 / 47, national 426 / 80) | Cheap and auditable, but cover only phrasings someone wrote. The disambiguator covers the rest |
| A model on ambiguous turns only | Adds 0.5-1.4 s on those turns, bounded by a 2.5 s turn budget with a spoken "One moment." |
| Speak-direct templates | Less variety in wording; switchable per agent |
| Five graph nodes, work in tools | Fewer forced turns than one node per step; each node transition still costs an LLM hop |
| Ask rather than guess | More questions per booking than a confident agent; fewer wrong bookings |

Rejected alternatives: catalog in the prompt (does not fit, cannot apply policy reliably), RAG over catalog chunks (retrieves similar text but cannot join capability x location x provider, kept as the path for thousands of visit types), and one node per step (4-5 forced turns when the caller said everything at once).

## Scoping

- **Built:** Agent Studio, the resolver and policy table, geography down to the street and house number, four disambiguator modes, a national catalog generator, the offline eval with blind rounds, a text-mode call simulator, an API keys sheet, a Dev view and a post-call review.
- **Mocked:** availability (seeded slots inside clinic hours, behind one `Availability` interface) and bookings (in memory).
- **Left out:** EHR integration, identity, insurance, reschedule and cancel (these go to a handoff node), a catalog admin UI (optional in the brief), a vector database and LLM-interpreted policies (not needed at this scale; policies are code on purpose).

## Known limits

- Underspecified requests can still get a confident answer in blind tests: "my next usual appointment with my psychiatrist" gets medication management without asking which visit, and a child's physical gets a school physical without asking school or sports. They were left unfixed on purpose: fixing them on the scored cases would make the blind score meaningless.
- The offline eval feeds tool arguments, not audio. Speech recognition and LLM extraction are covered only by live calls.
- JEV probabilities move by 0.02-0.08 between identical requests, so a case near a threshold can flip between runs.
- A new client's catalog needs its own aliases and lay terms. They are data, not code (SF has 237 and 47); the resolver, policy table, geography and disambiguator carry over unchanged. Measured with all of it removed: with JEV, SF drops from 60/62 to 56/62 correct turns and national from 50/56 to 47/56, with at most one wrong booking per set. Without a model, SF drops from 37/62 to 22/62. Drafting the vocabulary offline with an LLM and having a person review it is the next step; it is not built.

## Demo

The README's demo script lists nine checked beats, among them: policy picking the right Dr. Chen, "the lady one" confirmed by name, a street and house number deciding the clinic, "Trenton" heard as "Renton", the Disambiguator switch flipped from JEV to Off on the same sentence, questions answered from the catalog, the booking guard refusing "sure, book it" before a time is chosen, and the Decisions tab showing the moment the conversation model tried to answer for the caller.
