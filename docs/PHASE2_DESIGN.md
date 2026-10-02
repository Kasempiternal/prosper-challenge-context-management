# Phase 2 design: catalog context management

Status: approved 2026-10-02. Decisions: speak-direct templates ON (with a config switch to turn off); build order = resolver + eval (free) first, then agent wiring (first live call needs approval), then JEV/scale test.
Labels: **M** = measured in this repo, **E** = estimate, **I** = inferred from docs/code.

## The idea in one paragraph

The LLM never sees the catalog. It turns what the caller says into one tool call, `update_request`, carrying the caller's own words. A deterministic resolver in our process does all catalog work. It keeps a precomputed table of every bookable (appointment type, provider, location) row. It filters that table by the booking policies. Then it returns one small next move: offer up to 3 concrete times, ask the single question that best splits what is left, or refuse with a specific reason. Policies are code, so a policy-violating booking cannot be produced. The prompt stays around 1.6k tokens per turn whether the catalog has 50 providers or 5,000.

## Why not the alternatives

| Approach | Cost per call | Accuracy | Latency | Verdict |
|---|---|---|---|---|
| Dump catalog in prompt | 8,252 tok (M) × ~15 LLM calls ≈ 124k tok ≈ $0.31 on gpt-4o (E). At 100× catalog it exceeds the 128k window. | Model must apply 6 cross-entity policies by reading. Nothing stops an MRI at a site without imaging. | Higher time-to-first-token every turn | Baseline we measure against |
| RAG over catalog chunks | Low tokens | Retrieves similar text but cannot do joins (capability × location × provider). Disambiguation still on the LLM. | +150–300 ms embedding hop per lookup (E) | Rejected at this scale. Kept as the scaling path for 1k+ appointment types. |
| One graph node per step (specialty → type → provider → location) | Low tokens | Good | 4–5 forced turns even when the caller said everything in one sentence | Rejected: worst caller experience |
| **Resolver + policy table (this)** | ~1.6k tok/turn ≈ 22k/call ≈ $0.055 (E) | Policy violations impossible by construction. Ambiguity resolved by the caller or by policy, never guessed. | Resolver <10 ms (E), no extra network hop | **Chosen** |

## Ambiguity is a catalog fact, not a probability

Three findings from the data shaped this (all M):

1. "Dr. Chen, the heart doctor" matches **two** cardiologists: David Chen (prov_000, not accepting new patients) and Emily Chen (prov_046). No model can pick between them; only the caller can, or policy can. For a **new** patient, policy removes David and the answer is Emily with no question asked.
2. Near-duplicate types (Annual Physical vs Annual Wellness Visit; Skin Cancer Screening vs Full Body Skin Exam) are offered by **identical provider sets with identical durations**. Choosing between them changes the booked label, not who or when.
3. 8 appointment types are offered by **no provider** (all 5 ophthalmology, both physical therapy, urology). The correct answer is "we don't offer that".

So the resolver's core rule: **ask only the question whose answer changes the valid set**, and pick the question that splits it best (specialty, then location, then first name, then spelling). Often the question collapses into a time offer: "Dr. Emily Chen has Tuesday 9:30 at Downtown or Wednesday 2 at Richmond." Picking a time settles the location too.

## Where JEV fits

JEV (Command Code decision model) returns calibrated probabilities over named options. Measured: ~520 ms warm, 1.7 s cold, ~390 input tokens, $0.04/M. "Can I see Dr. Chen?" returned 0.64 / 0.34 / 0.02 with confidence 0.45, so it correctly declines to guess.

It does **not** go in the hot path for provider or location choice. Those ambiguities are catalog facts (finding 1), and an instant question-picker resolves them in 0 ms.

It **does** earn its ~500 ms in one place: mapping a free-text reason to an appointment type when the lexical matcher ties between known confusables within one specialty ("a checkup", "skin check"). If p ≥ 0.85, the agent books the label without asking, which saves a 3–5 s question turn. Below that, it asks an either/or. The wait is covered by a spoken "let me check".

Fallback without a JEV key: gpt-4.1-nano with logprobs over the option letters. Last resort: ask the caller between the top 2.

Two more uses with no latency cost, because nobody is waiting on the line:

- **Post-call grader.** After each test call, JEV scores the transcript with `noul` and `score` questions: booking correct, unnecessary questions asked, any fact not in the catalog. Scores show in the UI right after hang-up.
- **Eval judge.** The paid dialog simulation uses JEV, not gpt-4o, to score simulated calls: cheaper, with probabilities instead of free text.

The eval decides whether JEV stays. If it does not reduce wrong-commits or questions asked, it is cut, and the README says so.

## Data shape

- **`CatalogIndex`** (immutable, built at startup from `catalog.json`)
  - lookups by id
  - `bookable`: list of valid `(type_id, provider_id, location_id)` rows. Built from provider locations ∩ provider types ∩ location capability. About 600 rows (E).
  - name index: surname, first name, Double Metaphone of surname (for speech-to-text misspellings like "Dr. Nwin" → Nguyen)
  - `type_lexicon`: type names plus hand-reviewed aliases (`data/aliases.json`, ~150 entries)
  - `lay_terms → specialty` ("heart" → Cardiology)
  - `confusables[type_id]`: computed offline; the only trigger for the JEV path
  - `unoffered_types`: the 8 types no provider offers
- **`Request`** in `FlowManager.state["req"]` (JSON-serializable; tool handlers are the only writers)
  - `patient`: `is_new`, `has_referral`, `name` (each may be unknown)
  - `service`, `provider`, `location`: each a slot with `heard`, `candidates`, `resolved_id`, `asks`
  - `time_pref`: soonest, day, part of day
  - `offered` (≤3), `held`, `history` (supports "no, the other one")
- **`policy.check(row, patient)`**: the only place booking rules live. It runs in the resolver and again inside `book`.
- **`resolve(index, req) -> Plan`**: a pure function returning `offer | ask | refuse | confirm`, plus a ≤120-token summary.

## What the LLM sees

| Tool | Purpose | Returns (≤120 tok) |
|---|---|---|
| `update_request(service_phrase?, service_name?, specialty_hint?, provider_phrase?, location_phrase?, is_new?, has_referral?, time_pref?, pick_offer?, clear?)` | Merge what the caller just said | `{status, say, ask?, offers?, known}` |
| `lookup(kind, phrase)` | Answer questions: hours, address, languages, "do you offer X" | ≤5 facts |
| `book_offer()` (confirm node; no arguments) | Books exactly the offer the caller said yes to after the read-back; re-checks policy; idempotent | Confirmation ref |

- `specialty_hint` is a 21-value enum (M: 21 specialties).
- `service_name` is an optional strict enum of the 82 type names. The eval compares it with free-text `service_phrase`; whichever wins top-1 at this scale stays.
- **Speak-direct**: offers, questions and refusals come from templates. The handler queues the text to TTS and returns `NO_RESPONSE`, which skips the second LLM round trip (~0.6 s, E). Verified in `pipecat/flows/manager.py:520`. Names and times are never paraphrased, so they cannot be invented. Requires `parallel_tool_calls=False`. An offer interrupted mid-speech is re-spoken.

## Conversation graph

Five nodes, on purpose. Every transition costs a context reset and an LLM round trip, so the work lives in tools.

```
greeting --start(summary)--> schedule      (hub; tools: update_request, lookup)
schedule --hold_slot [precondition: offer_confirmed]--> confirm   (tools: book_offer, update_request)
confirm  --finish--> done (end)
confirm  --book_another--> schedule
greeting | schedule | confirm --transfer_to_staff--> handoff (end)
```

Transitions use `RESET` plus a `{{ summary }}` placeholder rendered from state. `RESET_WITH_SUMMARY` is deprecated in this Pipecat version (M: `types.py:142`) and costs an extra LLM call, so it is not used.

## Agent JSON changes (stay editable in the Phase 1 UI)

- `Node.tools: [str]` names tools from a code registry. The handler owns the parameter schema; the UI shows it read-only.
- `Node.context_strategy`, `Node.respond_immediately`.
- `Edge.precondition`: a state predicate (`offer_confirmed`); the edge returns a tool error until it holds.
- `AgentConfig.catalog`: path to the catalog.
- `AgentConfig.resolver`: `{speak_direct, jev: {enabled, timeout_ms}}`, tunable in the UI. JEV gate thresholds are tuned offline (`eval/tune_jev_gate.py`).
- Migrate the builder from standalone `pipecat_flows` to the bundled `pipecat.flows` (needed for placeholders and `NO_RESPONSE`).

## Mock availability

`Availability` interface: `find(rows, time_pref, limit=3)`, `hold(slot)`. The mock seeds each (provider, location, week) to 2–3 clinic days per location. A multi-site provider is at different sites on different days, so location changes the times offered. Slots are duration-sized, inside location hours, about 55% open. `DEMO_NOW` is fixed for reproducible demos. Holds are idempotent. A real EHR adapter replaces the mock behind the same interface.

## Failure handling

- **Misheard name**: Jaro-Winkler + Double Metaphone. One match is confirmed implicitly ("Dr. Hannah Nguyen…"). Several get a splitting question. Two misses: "could you spell the last name?" Three: handoff.
- **Change of mind**: `update_request` overwrites and recomputes. Dependent choices are kept only if still valid; otherwise the agent says so.
- **Nothing valid**: a specific reason plus the nearest valid alternative. Example (M): a new patient asking for a knee MRI is refused, because MRI - Knee is closed to new patients and needs a referral.
- **Referral**: asked only when every remaining option requires one.
- **JEV slow or down**: 1.2 s total budget per turn, no retry; on timeout the resolver behaves as without JEV (ask the caller). A spoken "One moment." covers waits over 300 ms. **LLM down**: templated apology and handoff.

## Eval (the evidence)

- `eval/cases.jsonl`, ~70 cases: duplicate names, misheard names, confusable types, confusable locations (Mission Bay vs Mission District, North Beach vs North Gate), policy traps, unoffered types, mind changes, Q&A, and one-sentence bookings.
- **Free, run in CI:**
  - resolver eval: wrong-commit rate (headline), top-1, ask precision/recall, questions per booking, p50/p95 latency
  - policy property test over every bookable row × patient flags: 0 violations
  - token budget per node (tiktoken)
- **Scale test:** `eval/scale_catalog.py` builds a 100× synthetic catalog with realistic name collisions. Expected: tokens per turn unchanged, wrong-commit flat, ask rate up (correctly).
- **Paid, only with approval (~$1–2, E):** text-only dialog simulation of ~20 scripted callers, ours vs the naive dump, same model. Measures accuracy, tokens, cost and turns. Also compares gpt-4o vs gpt-4.1-mini, and with vs without JEV.

## Scaling to 5k providers / 500 locations / 1k types

- The prompt does not change. That is the main claim, and the scale test proves it.
- The bookable table (~60–80k rows, E) moves to SQLite or Postgres with indexes.
- Names: "Nguyen" could match ~800 providers, so name-only resolution stops working. The question picker asks specialty or neighborhood first, so the ask rate rises and wrong-commits do not.
- Types: hierarchy (specialty → family → variant). Within a specialty, embeddings are precomputed offline; the query embedding runs in parallel with the resolver. JEV picks among the top 5.
- Multi-clinic: one index per organization. Messy feeds normalize at ingest through per-source adapters, and a new clinic must pass its eval cases before going live.

## Not built, on purpose

EHR integration, identity verification, real reschedule and cancel (they go to handoff), insurance, a vector database (not needed at 82 types), LLM-interpreted policies, a rules DSL, persistence across restarts, non-English conversation (language is only a provider filter), and a catalog admin UI (optional per the brief; last if time allows).

## Demo (each beat checked against the catalog)

1. **New patient**, has a referral: "Cardiology consultation with Dr. Chen, soonest." Policy removes David Chen (not accepting new patients). The agent says "Dr. Emily Chen has Tuesday 9:30 at Downtown or Wednesday 2 at Richmond." Booked with no disambiguation question. A side panel shows why David was filtered and the tokens used (~1.6k vs 8.3k).
2. **Existing patient**, same sentence. Now both Chens are valid, so the agent asks "Dr. David Chen or Dr. Emily Chen?" Same words, different correct behavior, decided by policy.
3. **New patient**: "MRI of my knee with Dr. Nwin." Phonetic match → Hannah Nguyen. The agent refuses: an MRI needs an established patient with a referral. Then "Do you do eye exams?" → "We don't offer eye care at our clinics."
4. If time allows: swap in the 100× catalog live and repeat beat 2. Tokens unchanged; the agent asks a better splitting question.

## Build order

1. `backend/scheduling/`: index, policy, resolver, aliases + resolver eval + policy property test. Free; no API calls.
2. Availability mock, tool registry, schema/builder changes, `pipecat.flows` migration, `scheduling_flow.json`. First live call needs your approval (costs money).
3. Phase 1 React UI: graph editor + test call panel showing the live node, transcript and resolver decisions.
4. JEV path + scale test + paid dialog sim (with approval) + README trade-off table.
