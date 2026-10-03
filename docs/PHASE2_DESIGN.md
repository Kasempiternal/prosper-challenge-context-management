# Phase 2 design: catalog context management

Status: approved 2026-10-02, built, and synced with the code on `phase1-ui` on 2026-10-03, through blind round 4. Decisions: speak-direct templates ON (with a config switch to turn off); build order = resolver + eval (free) first, then agent wiring, then the disambiguators and the national catalog. The last section lists what changed after building.
Labels: **M** = measured in this repo, **E** = estimate, **I** = inferred from docs/code.

## The idea in one paragraph

The LLM never sees the catalog. It turns what the caller says into one tool call, `update_request`, carrying the caller's own words. A deterministic resolver in our process does all catalog work. It keeps a precomputed table of every bookable (appointment type, provider, location) row. It filters that table by the booking policies. Then it returns one small next move: offer up to 3 concrete times, ask the single question that best splits what is left, or refuse with a specific reason. A small model may pick among named options when the caller's words carry more than the lexicon can read, but policy always runs after the pick. Booking happens in code, on an edge the LLM can only take after the caller said yes to a read-back. The prompt stays the same size whether the catalog has 50 providers or 5,000: about 1,600 tokens per LLM call on a live national call (M, 20,805 tokens over 13 calls).

## Why not the alternatives

| Approach | Cost per call | Accuracy | Latency | Verdict |
|---|---|---|---|---|
| Dump catalog in prompt | SF: 8,405 tok per turn (M) x 15 LLM calls ≈ $0.315 on gpt-4o input (E). National: 664,495 tok (M), 5.2x a 128k window, so it does not fit. | Model must apply 6 cross-entity policies by reading. Nothing stops an MRI at a site without imaging. | Higher time-to-first-token every turn | Baseline we measure against |
| RAG over catalog chunks | Low tokens | Retrieves similar text but cannot do joins (capability × location × provider). Disambiguation still on the LLM. | +150-300 ms embedding hop per lookup (E) | Rejected at this scale. Kept as the scaling path for 1k+ appointment types. |
| One graph node per step (specialty → type → provider → location) | Low tokens | Good | 4-5 forced turns even when the caller said everything in one sentence | Rejected: worst caller experience |
| **Resolver + policy table (this)** | SF: 779 tok at turn 1, 2,324 at turn 15 (M); ≈ $0.058 per 15 LLM calls (E) | Policy violations impossible by construction. Ambiguity resolved by the caller or by policy, never guessed. | Resolver alone p95 under 7 ms on every round 4 blind run (M), no network hop without a model | **Chosen** |

## Ambiguity is a catalog fact, not a probability

Three findings from the SF data shaped this (all M):

1. "Dr. Chen, the heart doctor" matches **two** cardiologists: David Chen (prov_000, not accepting new patients) and Emily Chen (prov_046). No model can pick between them; only the caller can, or policy can. For a **new** patient, policy removes David and the answer is Emily with no question asked.
2. Near-duplicate types (Annual Physical vs Annual Wellness Visit; Skin Cancer Screening vs Full Body Skin Exam) are offered by **identical provider sets with identical durations**. Choosing between them changes the booked label, not who or when.
3. 8 appointment types are offered by **no provider** (all 5 ophthalmology, both physical therapy, urology). The correct answer is "we don't offer that".

So the resolver's core rule: **ask only the question whose answer changes the valid set**, and pick the question that splits it best (specialty, then location, then first name, then spelling). Often the question collapses into a time offer: "Dr. Emily Chen has Tuesday 9:30 at Downtown or Wednesday 2 at Richmond." Picking a time settles the location too.

## Data shape

- **`CatalogIndex`** (immutable, built at server start from the agent's catalog)
  - lookups by id
  - `bookable`: list of valid `(type_id, provider_id, location_id)` rows. Built from provider locations ∩ provider types ∩ location capability. 760 rows for SF, 138,870 national (M).
  - row indexes per (type, location), per provider and per metro
  - name index: surname, first name, Double Metaphone of surname (for speech-to-text misspellings like "Dr. Nwin" → Nguyen)
  - `type_lexicon`: type names plus hand-reviewed aliases (`data/aliases.json`: 237 aliases and 47 lay terms for SF; 426 and 80 national)
  - `lay_terms → specialty` ("heart" → Cardiology)
  - `gazetteer`: areas (metros, states, neighborhoods, suburbs), ZIPs, ZIP3s, sites, sound keys, and the clinics in each state (empty for SF)
  - `unoffered_types`: types no provider offers
- **`Request`** in `FlowManager.state["req"]` (JSON-serializable; tool handlers and edge actions are the only writers)
  - `patient`: `is_new`, `has_referral` (each may be unknown)
  - `service`, `provider`, `location`: each a `Slot` with `heard`, `candidates`, `resolved_id`, `within` (the options of the question it answered), `asks`, `region` (the answer to "which city?")
  - `time_pref`: soonest, day, days, part of day, not before
  - `offered` (≤3), `pick`, `alternatives`, `pending_ask` (`field`, `options`), `turn`, `changed`
- **`policy.check(row, patient)`**: the only place booking rules live. It runs in the resolver and again inside `book_confirmed`.
- **`Verdict`** (`decision.py`): what a model hook returns. `act` commits to one id. `ask` asks between ids (one id alone: confirm it). Neither: decline, and the resolver does what it does with no model. `failed`: a model was asked and no answer came, so the resolver asks.
- **`Gate`** and **`CheckGate`** (`decision.py`): turn probabilities into a `Verdict` (see "The check").
- **`choosers.CHOOSERS`**: the one table of disambiguators. One row per mode (`jev`, `openai`, `embed`, `none`): whether it can run here, how to build its client, and the hooks `resolve()` takes from it.
- **`resolve(index, req, availability, disambiguator, chooser, site_chooser) -> Plan`**: returns `offer | ask | refuse | confirm`, plus a short summary.

## The resolver pipeline

`resolve()` runs once per `update_request`, in this order (`scheduling/resolver.py`). Every step can end the turn with an ask or a refusal.

1. **A picked offer** is re-checked and read back for confirmation.
2. **Visit.** `lexicon.match_types` scores type names, aliases, lay terms and specialty defaults against the caller's words.
   - **Doubt hook** (`lexicon.stated_doubt`, `resolver._doubted`). A doubt marker ("don't remember if", "not sure whether", "either ... or ..., I don't know") followed by alternatives is never committed on. Each alternative becomes a visit by name or alias, else by one model choice over that alternative. The caller is asked between the visits found. "Or not" is no alternative, and an alternative about cost, coverage, the clinic or the doctor is not about the visit.
   - **Not offered.** If the best tier is only visits no clinic offers, the resolver refuses `not_offered` before any model runs. A specialty default is not added when the caller named an unoffered visit. An unoffered visit said as context ("after PT", "my PT says") is no candidate.
   - **Model hook** (`_consult_types`, `_verify_types`, `_model_types`). A model is asked only when nothing matched, only a specialty default matched, or the caller said words the matched names and aliases do not explain. On a large catalog it sees a shortlist of at most 20 types. Its answer passes the `Gate`, then the check (below). A specialty default chosen from a shortlist is chosen again among that specialty's visits. A failed answer asks.
3. **Doctor** (`names.match_providers`, `resolver._described`). The doctors the caller means come from the name plus catalog facts in the description (language, title, specialty, clinic). This runs before the type, place and policy filters, so the refusals that follow speak for that doctor. A doctor who does not do the visit gets a `provider_type` refusal with alternatives.
4. **Place** (`geo.resolve_place`, `_geo_scope`, `_ring`). See "Geography". A clinic named to describe the doctor is where the caller goes, unless they gave a place of their own.
5. **Policy.** `policy.check` on every remaining row. Violations are removed and noted. Rows that only need an unknown fact (new patient, referral) make the agent ask for it. Nothing left gives a refusal with the reason and the nearest valid alternatives.
6. **Visit question.** Two or more live types, or a stated doubt, ask "which visit?".
7. **Provider hook** (`_consult_provider`). Several valid namesakes and a description: catalog facts first, then gender under the gender policy, then a model for words no fact explains. A bare "Dr. Chen" never calls a model.
8. **Site hook** (`_consult_site`). A descriptive place clue that leaves 2 to 20 sites may be settled by a model. A street that put the caller at no clinic is no site clue.
9. **Offer.** Up to 3 times from the availability source. After a widened search, the nearest clinic with openings comes first.

Round 4 found two more failure classes, umbrella words and triage nuance (see "Known failure classes"). No umbrella or triage hook exists on `phase1-ui`.

## The check

The first question chooses among many options. It is overconfident on words that fit two visits alike and under-confident on clear symptoms (M: a live replay of "my yearly exam" put 0.80 on Annual Physical, while "my tummy's been hurting for weeks" got 0.55 for GI). So every type choice is followed by a second, focused question: the front-runner against its rival (the runner-up, the lexical match the choice overruled, or the choice's nearest neighbour when the runner-up is under 0.05), plus "either: nothing the caller said tells these two visits apart".

| Gate | Rule | Chosen on |
|---|---|---|
| `Gate` (first question) | Act if top p ≥ 0.8 and it leads the runner-up by ≥ 0.6. Ask either/or if the top two sum to ≥ 0.85. Otherwise decline. | `cases_tune.jsonl` only (26 cases, 1,210-gate grid) |
| `CheckGate.margin` = 0.2 | A confident choice stands only if the check's top answer is that choice, ahead of both the rival and "either" by 0.2 | Dev check leads have a gap between 0.13 and 0.24 (M); 0.2 sits in it. Added after blind round 3 (h3-32: chosen 0.34, rival 0.25, either 0.41). |
| `CheckGate.settle` = 0.65, strict | A pair is settled only by a check leader above 0.65 | Dev experiment over 132 phrases; h2-28 sits at 0.65 exactly and takes the safe side |
| `CheckGate.settle_either` = 0.5 | ... and only with "either" under 0.5 | Same experiment |
| No answer | A check that never came asks; a confident choice is confirmed alone ("Is that a school physical?") | Never commit on a missing answer |

Moving each threshold by ±0.05 changed no wrong commit on any dev set in rounds 3 and 4 (M, `eval/threshold_sensitivity.py`).

**Gender policy (pre-registered before round 3 was scored).** The catalog has no gender field. JEV reads a probability that a doctor is a woman from the first name. It counts only at p ≥ 0.9 (female) or ≤ 0.1 (male); unknown is never ruled out. Gender may narrow the doctors but never books one alone: a doctor that gender alone singles out is confirmed by full name ("Do you mean Dr. Emily Chen?"). Only words about the doctor count ("the lady", "he speaks Spanish"), not "her baby" or "women's health".

## Geography (national catalog)

The national catalog (synthetic, seed 20261002) has 40 metros, 299 sites, 5,000 providers, 314 types and 138,870 bookable rows (M). Its sites carry coordinates. There is no geocoder: `scheduling/geo.py` resolves place words against the catalog's own geography.

- **Clinic and area.** A bare clinic name means that clinic. Anything else names an area: a city, state, ZIP, ZIP3, neighborhood or "near X". Each area kind has a radius (site, neighborhood and ZIP 5 mi, ZIP3 15 mi, metro 25 mi, state 150 mi).
- **Street level.** A street, a house number or a full address names the clinics on it. "The clinic on Market Street in San Jose" matches two clinics, so the agent asks "Is that Downtown at 1812 Market or Willow Glen at 3330 Market?". A spoken number ("thirty-three thirty", "five oh five", "forty eight hundred") picks the clinic with the nearest house number; a number halfway between two clinics asks. Street-type words are never evidence: "Lincoln Avenue in Salt Lake" is Sugar House (4821 Lincoln Ave), not The Avenues Family Clinic. A street the city lacks falls back to the city. An ordinal street ("second street") is a street, not a description for a model.
- **Confirmations.** A place matched by sound, by spelling or by part of its name is a guess, and the resolver confirms it by name, city and state before searching: "Trenton" gets "Did you mean Renton, Washington?". "Yes" searches Renton. "No" asks "Which city are you in?" afresh. "No, Trenton, New Jersey" searches that place. Only a plausible misspelling of one name is taken as that name ("San Antonyo", "Philedelphia").
- **States.** A state's cities are the metros with a clinic in it. A state whose only clinic city lies over the state line is not taken for that city. A state with no city of its own refuses `none_nearby` and names its nearest own clinic with the distance. A state with clinics of two cities asks which.
- **Search.** The area's radius, then twice that, then 50 miles. Past 50 miles the resolver refuses with `none_nearby` and returns the nearest valid clinic as a pickable alternative. After a widened search, offers and refusal alternatives go nearest first.
- **No place.** With no place and no provider to pin one, a multi-metro catalog asks "Which city are you in?". A town name that exists in two metros gets an either/or.
- A catalog without coordinates (SF) loads as one implicit metro and behaves exactly as before.

Exact full-name duplicates arise naturally in the generator: 0.46% of national providers (M). As collisions grow, the resolver asks for city or specialty first.

## Where a model fits: four disambiguator modes

A model does **not** go in the hot path for bare name or place ambiguity. Those are catalog facts (finding 1), and an instant question-picker resolves them in 0 ms. A model earns its wait only where the caller's words carry information the lexicon cannot use. `resolver.chooser` picks which model answers, one row of `choosers.CHOOSERS`:

| Mode | Client | Hooks | Notes |
|---|---|---|---|
| `jev` (default) | Command Code JEV | type choice + check, provider (with a yes/no gender question), site | Calibrated probabilities over named options |
| `openai` | gpt-4o-mini, one answer token, top-20 logprobs | type choice + check, provider, site | No gender question: one answer token has no calibrated yes. gpt-4.1-nano was tried on the tune set: 9 wrong commits of 24 against gpt-4o-mini's 4 (M). |
| `embed` | Local fastembed `BAAI/bge-small-en-v1.5`, softmax T = 0.0125 | one hook for type, provider and site; no check | Model load 1.0 s and national types and sites 3.4 s in a background preload (M) |
| `none` | none | none | Ambiguity becomes a question |

JEV and OpenAI share one client base (`model_client.CachedModelClient`): the disk cache keyed by request hash, the per-request timeout and the per-turn budget, and a telemetry record of every request. A client never raises into the resolver: a failure, timeout or malformed answer becomes a failed `Verdict`, and the resolver asks. A missing key or package runs the mode as `none`.

Budget on live calls: 2.5 s per turn and 1.5 s per request, no retry. With the old 1.2 s budget, 15 of 145 live requests ran out and 14 turns asked instead of booking (M). A spoken "One moment." covers waits over 0.3 s. A 20-option warm-up request at call start moves the connection setup cost off the first caller turn. A JEV type decision is two sequential requests: live JEV turns took p50 974 ms and p95 1,390 ms on the dev sets in round 3 (M).

Other JEV roles: the post-call grader (five questions over the transcript and decisions, about $0.00004 per call, M; `backend/grader.py`) is built. An eval judge for the paid dialog simulation is not built, and neither is the simulation.

Per-mode results on every blind set are in the README's evidence section and in eval/README.md. In short (M): JEV made the fewest wrong commits on every SF held-out and blind set. On national4, OpenAI and no model made 0 wrong commits against JEV's 1.

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
- `AgentConfig.resolver`: `{speak_direct, chooser, timeout_ms}`. `chooser` is `jev`, `openai`, `embed` or `none` (default `jev`); the Test call panel's Disambiguator switch writes it. `timeout_ms` defaults to 2500. Gate thresholds are tuned offline, not per agent.

Full contract: [AGENT_FORMAT.md](AGENT_FORMAT.md).

## Mock availability

`Availability` interface: `find(rows, time_pref, limit=3)`, `hold(slot)`. The mock seeds each (provider, location, week) to 2-3 clinic days per location. A multi-site provider is at different sites on different days, so location changes the times offered. Slots are duration-sized, inside location hours, about 55% open. `DEMO_NOW` is fixed for reproducible demos. Holds are idempotent per call and process-wide across calls. A real EHR adapter replaces the mock behind the same interface.

## Failure handling

- **Misheard name**: Jaro-Winkler + Double Metaphone. One match is confirmed implicitly ("Dr. Hannah Nguyen…"). Several get a splitting question. Two misses: "could you spell the last name?" Three: handoff.
- **Misheard place**: a plausible misspelling of one name is that place. A match by sound or by part of a name is confirmed first ("Did you mean Renton, Washington?").
- **Stated doubt**: the caller is asked between the visits they named. No model narrows them.
- **Change of mind**: `update_request` overwrites and recomputes. Dependent choices are kept only if still valid; otherwise the agent says so.
- **Nothing valid**: a specific reason plus the nearest valid alternatives. Example (M): a new patient asking for a knee MRI is refused, because MRI - Knee is closed to new patients and needs a referral.
- **Nothing nearby**: `none_nearby` past 50 miles, with the nearest valid clinic as an alternative.
- **Referral**: asked only when every remaining option requires one.
- **Booking fails at the last step** (policy or slot taken): the agent says so and offers fresh times without leaving `schedule`.
- **Model slow, down or unsure**: 2.5 s per turn, 1.5 s per request, no retry. A timeout or error is never committed on: the caller is asked. A low-confidence answer gives the no-model behavior.

## Evaluation methodology

Every case is a sequence of `update_request` arguments. The LLM's extraction is not measured offline. Full method and every result: [eval/README.md](../eval/README.md).

- **Metrics.** A wrong commit is an offer or confirmation that should not have happened, counted per commit: asks and refusals cannot commit wrongly, so a per-turn rate would flatter a resolver that rarely commits. Top-1 is a turn that matches the expected result exactly.
- **Dev vs blind.** A dev set is one whose failures we read and fixed against. Its numbers are reported but labelled as seen. A blind set is authored without the resolver code, its results or the failures, frozen, and scored once. Only blind numbers are evidence of generalization.
- **Freeze before fix.** Each blind round is committed before any fix it judges: `heldout3` and `national3` in `085a9e4`, before any round 3 resolver change; `heldout4` and `national4` in `99f7150`, before any round 4 fix. The sha256 of each file is recorded in eval/README.md, and `eval/sets.py` registers the files as blind, so no test, tuning run or `--set all` reads them.
- **Authored blind.** Labels come from catalog queries in seeded generators. DeepSeek writes only the caller's words, from plain scenario facts. The author lists uncertain labels in `label_notes.md` before scoring.
- **Scored once.** After the round's fixes land, each blind set is scored once in all four modes (JEV and OpenAI live). The raw output is committed in `eval/results/`. Nothing is tuned on it. Then it becomes a dev set, and a fresh blind round judges the next fixes.
- **Policy changes are pre-registered.** The gender policy was written down before round 3 was scored, and a label it changes is listed as a policy change, not a label error.
- **Incidents are disclosed.** Round 3 had two: a unit test that resolved the blind sets (it asserted only that no spoken text contains "None"), and a cleanup worker's code search that previewed h3-01 to h3-05. Both are reported with the score they could have touched.

Results, JEV mode (wrong commits per commit; top-1):

| Round | Dev sets after the round's fixes | SF set, scored once | National set, scored once |
|---|---|---|---|
| Before round 3 | national 0/39, 56/56 | heldout + heldout2: 2/39, 49/61 | national2: 1/37, 50/54 |
| 3 | 0 wrong commits; 323/330 turns | heldout3: 7/42 (16.7%), 42/54 | national3: 3/35 (8.6%), 46/56 |
| 4 | 1 wrong commit; 421/440 turns | heldout4: 3/38 (7.9%), 53/62 | national4: 1/31 (3.2%), 47/56 |
| 5 | pending | pending | pending |

Also free and run with the tests: the policy property test over every bookable row × patient flags, and a 6,000-conversation fuzz test against an independent oracle. Both find 0 policy violations (M).

Not built: the paid text-only dialog simulation (ours vs the naive dump, same model). Three live voice calls cover the LLM side qualitatively.

## Known failure classes (blind round 4, JEV mode)

- **Umbrella words** that name several visits, resolved with false confidence: "my stomach doctor said I need a scope" (upper or lower; chosen at 0.94, confirmed by the check at 0.82), "my baby's checkup", "some blood work".
- **Triage nuance**: "it burns when I pee ... since yesterday" went to a urology consultation, not a sick visit or UTI visit.
- **False refusals**: "my eyes get itchy and watery" (a seasonal allergy) got "we don't offer eye care"; a chest CT was refused within 50 miles while a general CT scan was available.

## Scaling past the national catalog

- The prompt does not change. The national fixed part is 625 tok (M), and the naive prompt at that size does not fit at all.
- At 138,870 rows the table lives in memory; the index builds in about 1 s at server start (M). A larger catalog moves it to SQLite or Postgres with the same indexes.
- Types: hierarchy (specialty → family → variant). Within a specialty, embeddings are precomputed offline; the disambiguator picks among the shortlist.
- Multi-clinic: one index per organization. Messy feeds normalize at ingest through per-source adapters, and a new clinic must pass its eval cases before going live.

## Not built, on purpose

EHR integration, identity verification, real reschedule and cancel (they go to handoff), insurance, a vector database (not needed at 314 types), LLM-interpreted policies, a rules DSL, persistence across restarts, non-English conversation (language is only a provider filter), and a catalog admin UI (optional per the brief).

## Demo (each beat checked against the catalog)

1. **New patient**, has a referral: "Cardiology consultation with Dr. Chen, soonest." Policy removes David Chen (not accepting new patients). The agent offers Dr. Emily Chen's times. Booked with no disambiguation question.
2. **Existing patient**, same sentence. Now both Chens are valid, so the agent asks "Dr. David Chen or Dr. Emily Chen?" Same words, different correct behavior, decided by policy.
3. **New patient**: "MRI of my knee with Dr. Nwin." Phonetic match → Hannah Nguyen. The agent refuses: an MRI needs an established patient with a referral. Then "Do you do eye exams?" → not offered.
4. **National**: "I hurt my knee playing football, I'm in Austin." Then "a dental cleaning, I live in Maine" → `none_nearby` with the nearest Boston site.
5. **Street level**: "a sick visit at the clinic on Market Street in San Jose" → "Is that Downtown at 1812 Market or Willow Glen at 3330 Market?" → "thirty-three thirty" → times at Willow Glen.
6. **Confirmation**: "a flu shot, I'm in Trenton" → "Did you mean Renton, Washington?" → "No, Trenton, New Jersey" → the nearest New Jersey clinic, Cherry Hill near Philadelphia.

## What changed after building

| Design said | Built | Why |
|---|---|---|
| `hold_slot` edge to a `confirm` node, then an LLM-called `book_offer` tool | One `confirm_booking` edge with the `offer_confirmed` precondition and the `book_confirmed` action, into a `booked` node | Live call 2: the LLM claimed a booking and invented a confirmation code without calling the tool. Now the edge books in code and speaks the real reference from a template. |
| `start(summary)` | `start(request)` and `book_another(request)` with the `new_request` action | Live call 2 lost a second request across the context reset. The caller's words now become the summary. |
| No date handling in prompts | Today's date in the prompts; past dates rejected | Live call 2: the LLM invented a 2023 date. |
| Turn start on any transcript | Turn start on VAD only; STT keyterms from the catalog | Live call 1: a late transcript fragment caused about 6 s of dead air; "eye exams" was heard as "ISX and SAMS". |
| JEV for confusable types only | Type, provider and site hooks; type choices on large catalogs use a ≤20-type shortlist; warm-up sends 20 options | JEV choice questions accept at most 255 options. The first warm-up was rejected in live call 2. |
| JEV as the only model; gpt-4.1-nano logprob fallback without a JEV key | Four modes in one chooser table (`jev`, `openai`, `embed`, `none`), switchable per call in the Test call panel; JEV and OpenAI share one client base | To measure what JEV adds against commodity models. gpt-4.1-nano made 9 wrong commits of 24 on the tune set against gpt-4o-mini's 4 (M), so gpt-4o-mini it is. |
| One question per decision, acted on above the gate | A second question, the check, with an "either" answer, settled by `CheckGate` | The first question is overconfident on twins ("my yearly exam" 0.80, M). Blind round 3 (h3-32) added the 0.2 margin. |
| The resolver picks once the gate passes | Stated doubt is never committed on; a failed model answer asks | Blind round 3: h3-31 chose although the caller said they did not know. |
| Policy filters, then the provider hook picks | The described doctor is found before the filters; refusals speak for that doctor | Blind round 3: h3-p04, a new patient, was offered a different Dr. Hernandez. |
| Inferred gender is a clue like any other | Pre-registered gender policy: counts only at p ≥ 0.9 or ≤ 0.1, never books alone | heldout2 h2-p07: JEV put 0.86 on Jennifer Nguyen, but Maria Nguyen is also a woman (M). |
| Places by clinic, neighborhood, city, state or ZIP | Also street, house number and full address; a place heard by sound is confirmed ("Did you mean Renton, Washington?") | A caller names a clinic by where it is. A review probe offered Renton, Washington, for "Trenton". |
| Refusal alternatives from the nearest metro | Nearest first, by distance to the caller | Blind round 3: nat3-geo-10 offered clinics about 40 miles away while one 12.5 miles away qualified (M). |
| 1.2 s JEV budget per turn | 2.5 s per turn, 1.5 s per request | With 1.2 s, 15 of 145 live requests ran out of budget (M). |
| Keys in `backend/.env` | Keys sheet in the studio; keys stay in the browser, go only to the local backend, and are blanked in logs | A reviewer can run a call without editing files. |
| One held-out set, scored once | Blind rounds: frozen before the fixes, scored once in every mode, then turned into dev sets | Dev sets read 0 wrong commits while blind round 3 read 7/42 (M). Only a fresh blind set shows generalization. |
| 100× synthetic catalog scale test (`eval/scale_catalog.py`) | National v2 generator (`backend/tools/gen_national_catalog.py`) with real metros and geography, plus dev and blind eval sets | A national catalog needs geography: at 299 sites, "near me" is the hard part (I). |
| `service_name` enum compared in the eval | Not shipped | It costs 799 tok of schema on SF and 2,006 on national (M). |
| JEV eval judge and paid dialog simulation | Not built | Costs money. Needs approval. |

## Final verification (2026-10-03, resolver frozen at `222eb22`)

Round 5 was scored once in all four modes, after the hard-suite fixes and before any blind result was inspected. No resolver fix was made after scoring. JEV and OpenAI used live calls; Off and embeddings ran locally. Scores are preserved in `eval/results/round5_*.txt`.

| Set | Off | Embeddings | OpenAI | JEV (default) |
|---|---|---|---|---|
| heldout5 | 11/26, 30/64 | 15/30, 31/64 | 8/40, 48/64 | 4/38, 52/64 |
| national5 | 4/37, 50/59 | 5/37, 49/59 | 3/38, 52/59 | 1/35, 51/59 |

Each cell is wrong commits per commit, then fully correct turns. JEV: SF **4/38 (10.5%), 52/64**; national **1/35 (2.9%), 51/59**. SF's wrong-commit rate rose from round 4's 3/38; national fell from 1/31. The sets differ, so this is not a matched comparison. The hard suite has zero unsafe JEV outcomes; fresh blind cases still fail.

Practice checks: all 11 sets retain zero JEV wrong commits, **535/566** fully correct turns. Off's wrong counts rose on no set (30 total). Backend: **1,288 passed, 1 intentional live-smoke skip**. Frontend: **122 passed**, production build successful; National Scheduler, JEV default and Dev view verified in a browser. The geographic property enumerates all national clinic addresses and metros in all four modes with two services; every actual offer stays within 50 miles of its original known anchor. A stale distant offer cannot be confirmed.

### Hard suite (development evidence)

88 catalog-derived cases: 40 SF and 48 national. These cases were seen before the fixes; they are not blind evidence. C / SBA / SO / U means correct / safe but asked / safe other / unsafe. Unsafe also counts a false refusal when an offer is required.

| Set | Off | Embeddings | JEV (live) | OpenAI (cache only) |
|---|---|---|---|---|
| SF (40) | 26 / 4 / 4 / 6 | 26 / 2 / 3 / 9 | **36 / 3 / 1 / 0** | 28 / 5 / 6 / 1 |
| National (48) | 40 / 5 / 0 / 3 | 40 / 5 / 0 / 3 | **43 / 5 / 0 / 0** | 37 / 11 / 0 / 0 |

OpenAI stress scores are partial: cache misses ask safely and no OpenAI stress network run was approved. The ordinary exact-match metric may flag a speech-only error even when the stress structural metric is correct; these metrics are intentionally distinct. Live JEV stress spend was $0.00232. Round 5 spend: JEV $0.00605; OpenAI $0.00976 (102 new requests).

### Remaining risks

- A named doctor can narrow "a blood test for my cholesterol" to a blood draw without clarifying the lipid panel.
- "The one at Richmond or Mission Bay" can select the Mission Bay doctor instead of asking between sites.
- Off can offer adult neurology for a pediatric request.
- SF has no catalog marking that separates its mammogram from diagnostic imaging for a lump.
- Embeddings had 9 unsafe SF stress outcomes, against Off's 6.
- Fresh blind round 5 still has confident choices on underspecified requests: MRI region, mental-health medication follow-up and school/sports physicals. One national symptomatic request gives a false refusal with an incorrect distant alternative.

Offline cases feed tool arguments directly. They do not test speech recognition, LLM extraction, interruptions or audio timing. Use [the live-call script](LIVE_CALL_TESTS.md) for that layer. Passing twelve calls cannot prove universal safety.
