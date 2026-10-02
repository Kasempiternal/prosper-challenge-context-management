# Phase 2 design: catalog context management

Status: approved 2026-10-02, built, and synced with the code on 2026-10-03. Decisions: speak-direct templates ON (with a config switch to turn off); build order = resolver + eval (free) first, then agent wiring, then JEV and the national catalog. The last section lists what changed after building.
Labels: **M** = measured in this repo, **E** = estimate, **I** = inferred from docs/code.

## The idea in one paragraph

The LLM never sees the catalog. It turns what the caller says into one tool call, `update_request`, carrying the caller's own words. A deterministic resolver in our process does all catalog work. It keeps a precomputed table of every bookable (appointment type, provider, location) row. It filters that table by the booking policies. Then it returns one small next move: offer up to 3 concrete times, ask the single question that best splits what is left, or refuse with a specific reason. Booking happens in code, on an edge the LLM can only take after the caller said yes to a read-back. Policies are code, so a policy-violating booking cannot be produced. The prompt stays the same size whether the catalog has 50 providers or 5,000: about 1,600 tokens per LLM call on a live national call (M, 20,805 tokens over 13 calls).

## Why not the alternatives

| Approach | Cost per call | Accuracy | Latency | Verdict |
|---|---|---|---|---|
| Dump catalog in prompt | SF: 8,405 tok per turn (M) x 15 LLM calls ≈ $0.315 on gpt-4o input (E). National: 664,495 tok (M), 5.2x a 128k window, so it does not fit. | Model must apply 6 cross-entity policies by reading. Nothing stops an MRI at a site without imaging. | Higher time-to-first-token every turn | Baseline we measure against |
| RAG over catalog chunks | Low tokens | Retrieves similar text but cannot do joins (capability × location × provider). Disambiguation still on the LLM. | +150–300 ms embedding hop per lookup (E) | Rejected at this scale. Kept as the scaling path for 1k+ appointment types. |
| One graph node per step (specialty → type → provider → location) | Low tokens | Good | 4–5 forced turns even when the caller said everything in one sentence | Rejected: worst caller experience |
| **Resolver + policy table (this)** | SF: 779 tok at turn 1, 2,324 at turn 15 (M); ≈ $0.058 per 15 LLM calls (E) | Policy violations impossible by construction. Ambiguity resolved by the caller or by policy, never guessed. | Resolver p95 5 ms SF, 7.7 ms national (M), no extra network hop | **Chosen** |

## Ambiguity is a catalog fact, not a probability

Three findings from the SF data shaped this (all M):

1. "Dr. Chen, the heart doctor" matches **two** cardiologists: David Chen (prov_000, not accepting new patients) and Emily Chen (prov_046). No model can pick between them; only the caller can, or policy can. For a **new** patient, policy removes David and the answer is Emily with no question asked.
2. Near-duplicate types (Annual Physical vs Annual Wellness Visit; Skin Cancer Screening vs Full Body Skin Exam) are offered by **identical provider sets with identical durations**. Choosing between them changes the booked label, not who or when.
3. 8 appointment types are offered by **no provider** (all 5 ophthalmology, both physical therapy, urology). The correct answer is "we don't offer that".

So the resolver's core rule: **ask only the question whose answer changes the valid set**, and pick the question that splits it best (specialty, then location, then first name, then spelling). Often the question collapses into a time offer: "Dr. Emily Chen has Tuesday 9:30 at Downtown or Wednesday 2 at Richmond." Picking a time settles the location too.

## Geography (national catalog)

The national catalog (synthetic, seed 20261002) has 40 metros, 299 sites, 5,000 providers, 314 types and 138,870 bookable rows (M). Its sites carry coordinates. There is no geocoder: `scheduling/geo.py` resolves place words against the catalog's own geography.

- A bare clinic name means that clinic. Anything else names an area: a city, state, ZIP, ZIP3, neighborhood or "near X". Each area kind has a radius (site, neighborhood and ZIP 5 mi, ZIP3 15 mi, metro 25 mi, state 150 mi).
- The search runs the area's radius, then twice that, then 50 miles. Past 50 miles the resolver refuses with `none_nearby` and returns the nearest valid site as a pickable alternative.
- With no place and no provider to pin one, a multi-metro catalog asks "Which city are you in?". A town name that exists in two metros gets an either/or.
- An area phrase lets the caller's time pick settle the site.
- A catalog without coordinates (SF) loads as one implicit metro and behaves exactly as before.

Exact full-name duplicates arise naturally in the generator: 0.46% of national providers (M). Names are drawn from census frequency tables, never forced. As collisions grow, the resolver asks for city or specialty first.

## Where JEV fits

JEV (Command Code decision model) returns calibrated probabilities over named options. Measured live: p50 about 550 ms, p95 720-780 ms, $0.04/M input tokens. "Can I see Dr. Chen?" returned 0.64 / 0.34 / 0.02 with confidence 0.45, so it correctly declines to guess.

It does **not** go in the hot path for bare name or place ambiguity. Those are catalog facts (finding 1), and an instant question-picker resolves them in 0 ms.

It **does** earn its wait where the caller's words carry information the lexical matcher cannot use:

| Role | Trigger | Status |
|---|---|---|
| Type chooser | No lexical match, only a specialty default, or a confusable tie with words the tied names do not explain ("checkups while I'm expecting"). On a large catalog JEV sees a shortlist of at most 20 types. | Built |
| Provider chooser | Two or more policy-valid providers match and the phrase has a clue beyond the name ("the one who speaks Spanish") | Built |
| Site chooser | A descriptive clinic clue matches several sites | Built |
| Post-call grader | After hang-up, five questions over the transcript and decisions: booked correctly, unnecessary questions, unsupported claims, caller effort, outcome. About $0.00004 per call (M). | Built (`backend/grader.py`, Call review in the UI) |
| Eval judge | Scores the paid dialog simulation | Not built. The simulation itself is not built. |

The gate `act_p 0.8, margin 0.6, pair_p 0.85` was tuned on a separate tune set only. Above it the resolver acts, between it asks an either/or of the top two, below it behaves as if JEV were absent. Live calls cap the wait at 1.2 s per turn with no retry. A spoken "One moment." covers waits over 0.3 s. A 20-option warm-up request at call start moves the connection setup cost off the first caller turn.

The eval decided that JEV stays. On national2 (held-out) it raised top-1 from 68.5% to 92.6% and cut questions per booking from 0.54 to 0.21, with 1 wrong commit either way (M).

## Data shape

- **`CatalogIndex`** (immutable, built at server start from the agent's catalog)
  - lookups by id
  - `bookable`: list of valid `(type_id, provider_id, location_id)` rows. Built from provider locations ∩ provider types ∩ location capability. 760 rows for SF, 138,870 national (M).
  - row indexes per (type, location), per provider and per metro
  - name index: surname, first name, Double Metaphone of surname (for speech-to-text misspellings like "Dr. Nwin" → Nguyen)
  - `type_lexicon`: type names plus hand-reviewed aliases (`data/aliases.json`: 237 aliases and 47 lay terms for SF; 426 and 80 national)
  - `lay_terms → specialty` ("heart" → Cardiology)
  - places: metros, sites, neighborhoods, ZIPs (empty for SF)
  - `unoffered_types`: types no provider offers
- **`Request`** in `FlowManager.state["req"]` (JSON-serializable; tool handlers and edge actions are the only writers)
  - `patient`: `is_new`, `has_referral` (each may be unknown)
  - `service`, `provider`, `location`: each a slot with `heard`, `candidates`, `resolved_id`, `asks`
  - `time_pref`: soonest, day, days, part of day, not before
  - `offered` (≤3), `pick`, `alternatives`, `pending_ask`, `turn`, `changed`
- **`policy.check(row, patient)`**: the only place booking rules live. It runs in the resolver and again inside `book_confirmed`.
- **`resolve(index, req, availability, choosers) -> Plan`**: returns `offer | ask | refuse | confirm`, plus a short summary.

## What the LLM sees

| Tool | Purpose | Returns |
|---|---|---|
| `update_request(service_phrase?, specialty_hint?, provider_phrase?, location_phrase?, is_new?, has_referral?, time_pref?, pick_offer?, clear?)` | Merge what the caller just said | `{status, spoken or say, offers?, ask?, reason?}`. 115 tok mean, 154 max on national2 (M). |
| `lookup(kind, phrase)` | Answer questions: hours, address, languages, "do you offer X" | ≤5 facts |
| `confirm_booking` edge (no arguments) | Books exactly the offer the caller said yes to after the read-back. Precondition `offer_confirmed`, action `book_confirmed`. | Spoken confirmation; moves to `booked` |

- `specialty_hint` is an enum of the catalog's specialties.
- A strict `service_name` enum of every type name was measured and not shipped: it costs 799 tok of schema on SF and 2,006 on national, against 422 and 472 for the lean schemas (M).
- **Speak-direct**: offers, questions, refusals and the booking confirmation come from templates. The handler queues the text to TTS and returns `NO_RESPONSE`, which skips the second LLM round trip. Names, times and confirmation references are never paraphrased, so they cannot be invented. Requires `parallel_tool_calls=False`.

## Conversation graph

Five nodes, on purpose. Every transition costs an LLM round trip (live: a transition turn takes about 2-3 s against 1.1-1.3 s in-node, M), so the work lives in tools.

```
greeting --start(request) [action: new_request]--> schedule   (tools: update_request, lookup; context reset)
schedule --confirm_booking [precondition: offer_confirmed, action: book_confirmed]--> booked   (tools: lookup)
booked   --finish--> done (end)
booked   --book_another(request) [action: new_request]--> schedule
greeting | schedule | booked --transfer_to_staff--> handoff (end)
```

`schedule` uses `RESET` plus a `{{ summary }}` placeholder rendered from state. `new_request` writes the caller's latest words into `summary`, so a request survives the reset. `RESET_WITH_SUMMARY` is deprecated in this Pipecat version (M: `types.py:142`) and costs an extra LLM call, so it is not used. Prompts carry today's date from the availability source.

## Agent JSON changes (stay editable in the Phase 1 UI)

- `Node.tools: [str]` names tools from a code registry. The handler owns the parameter schema; the UI shows it read-only.
- `Node.context_strategy` (`"append"` or `"reset"`), `Node.respond_immediately`.
- `Edge.precondition`: a state guard (`offer_confirmed`); the edge returns a tool error until it holds.
- `Edge.action`: code run before the transition (`book_confirmed`, `new_request`); it may keep the call on the node.
- `AgentConfig.catalog`: path to the catalog, picked in the UI from `GET /api/catalogs`.
- `AgentConfig.resolver`: `{speak_direct, jev: {enabled, timeout_ms}}`. JEV gate thresholds are tuned offline (`eval/tune_jev_gate.py`).

Full contract: [AGENT_FORMAT.md](AGENT_FORMAT.md).

## Mock availability

`Availability` interface: `find(rows, time_pref, limit=3)`, `hold(slot)`. The mock seeds each (provider, location, week) to 2–3 clinic days per location. A multi-site provider is at different sites on different days, so location changes the times offered. Slots are duration-sized, inside location hours, about 55% open. `DEMO_NOW` is fixed for reproducible demos. Holds are idempotent per call and process-wide across calls. A real EHR adapter replaces the mock behind the same interface.

## Failure handling

- **Misheard name**: Jaro-Winkler + Double Metaphone. One match is confirmed implicitly ("Dr. Hannah Nguyen…"). Several get a splitting question. Two misses: "could you spell the last name?" Three: handoff.
- **Misheard place**: fuzzy and phonetic matching against the catalog's own place names.
- **Change of mind**: `update_request` overwrites and recomputes. Dependent choices are kept only if still valid; otherwise the agent says so.
- **Nothing valid**: a specific reason plus the nearest valid alternative. Example (M): a new patient asking for a knee MRI is refused, because MRI - Knee is closed to new patients and needs a referral.
- **Nothing nearby**: `none_nearby` past 50 miles, with the nearest valid site as an alternative.
- **Referral**: asked only when every remaining option requires one.
- **Booking fails at the last step** (policy or slot taken): the agent says so and offers fresh times without leaving `schedule`.
- **JEV slow or down**: 1.2 s total budget per turn, no retry; on timeout the resolver behaves as without JEV (ask the caller).

## Eval (the evidence)

Every case is a sequence of `update_request` arguments. The LLM's extraction is not measured offline. Full method and results: [eval/README.md](../eval/README.md).

**Dev vs held-out.** A dev set is one we read failures from and fixed against. Its after-fix numbers are reported, but labelled as seen. A held-out set is authored before tuning or fixing, frozen, and scored once. Only held-out numbers are evidence of generalization.

| Set | Role | Result, JEV off → on (M) |
|---|---|---|
| SF main | Rules written against | 0/48 wrong → 0/48; top-1 105/105 → 105/105 |
| SF tune | Gate thresholds chosen on it | Not evidence |
| SF heldout + heldout2 | Held-out | wrong 4/22 (18.2%) → 2/39 (5.1%); top-1 27/61 → 49/61 |
| national | Dev. First run 6/27 wrong, 37/56 top-1 (JEV off); fixed against it | After fixes, JEV on: 0/39 wrong, 56/56 |
| **national2** | **Held-out**: different seed, no type and metro overlap with the dev set, authored without access to resolver code or dev failures, frozen before the fixes were scored | **wrong 1/24 (4.2%) → 1/37 (2.7%); top-1 37/54 (68.5%) → 50/54 (92.6%); questions per booking 0.54 → 0.21** |

Also free and run with the tests: the policy property test over every bookable row × patient flags, and a 6,000-conversation fuzz test against an independent oracle. Both find 0 policy violations (M).

Not built: the paid text-only dialog simulation (ours vs the naive dump, same model). Three live voice calls cover the LLM side qualitatively.

## Scaling past the national catalog

- The prompt does not change. The national fixed part is 625 tok (M), and the naive prompt at that size does not fit at all.
- At 138,870 rows the table lives in memory; the index builds in about 1 s at server start (M). A larger catalog moves it to SQLite or Postgres with the same indexes.
- Types: hierarchy (specialty → family → variant). Within a specialty, embeddings are precomputed offline; JEV picks among the shortlist.
- Multi-clinic: one index per organization. Messy feeds normalize at ingest through per-source adapters, and a new clinic must pass its eval cases before going live.

## Not built, on purpose

EHR integration, identity verification, real reschedule and cancel (they go to handoff), insurance, a vector database (not needed at 314 types), LLM-interpreted policies, a rules DSL, persistence across restarts, non-English conversation (language is only a provider filter), and a catalog admin UI (optional per the brief).

## Demo (each beat checked against the catalog)

1. **New patient**, has a referral: "Cardiology consultation with Dr. Chen, soonest." Policy removes David Chen (not accepting new patients). The agent offers Dr. Emily Chen's times. Booked with no disambiguation question.
2. **Existing patient**, same sentence. Now both Chens are valid, so the agent asks "Dr. David Chen or Dr. Emily Chen?" Same words, different correct behavior, decided by policy.
3. **New patient**: "MRI of my knee with Dr. Nwin." Phonetic match → Hannah Nguyen. The agent refuses: an MRI needs an established patient with a referral. Then "Do you do eye exams?" → not offered.
4. **National**: "I hurt my knee playing football, I'm in Austin." Then "a dental cleaning, I live in Maine" → `none_nearby` with the nearest Boston site.

## What changed after building

| Design said | Built | Why |
|---|---|---|
| `hold_slot` edge to a `confirm` node, then an LLM-called `book_offer` tool | One `confirm_booking` edge with the `offer_confirmed` precondition and the `book_confirmed` action, into a `booked` node | Live call 2: the LLM claimed a booking and invented a confirmation code without calling the tool. A booking the LLM must remember to call cannot be trusted. Now the edge books in code and speaks the real reference from a template. |
| `start(summary)` | `start(request)` and `book_another(request)` with the `new_request` action | Live call 2 lost a second request across the context reset. The caller's words now become the summary. |
| No date handling in prompts | Today's date in the prompts; past dates rejected | Live call 2: the LLM invented a 2023 date. |
| Turn start on any transcript | Turn start on VAD only; STT keyterms from the catalog | Live call 1: a late transcript fragment caused about 6 s of dead air; "eye exams" was heard as "ISX and SAMS". |
| JEV for confusable types only | Type, provider and site choosers; type choices on large catalogs use a ≤20-type shortlist; warm-up sends 20 options | JEV choice questions accept at most 255 options. The first warm-up was rejected in live call 2. |
| 100× synthetic catalog scale test (`eval/scale_catalog.py`) | National v2 generator (`backend/tools/gen_national_catalog.py`) with real metros and geography, plus dev and held-out eval sets | A national catalog needs geography: at 299 sites, "near me" is the hard part (I). |
| gpt-4.1-nano logprob fallback without a JEV key | Not built. Without a key the resolver asks the caller. | Not prioritized. |
| `service_name` enum compared in the eval | Not shipped | It costs 799 tok of schema on SF and 2,006 on national (M). |
| JEV eval judge and paid dialog simulation | Not built | Costs money. Needs approval. |
