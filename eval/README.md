# Resolver eval (offline, free)

Every case in `cases.jsonl` is a sequence of **`update_request` tool-call arguments**: what the LLM
would extract from the caller's speech (for example `{"service_phrase": "MRI of my knee",
"provider_phrase": "Dr. Nwin"}`). It is not raw audio or a transcript. Speech-to-text noise is
simulated by misspelled phrases ("Dr. Nwin", "Dr. Shen", "Dr. Garsha"). The LLM's extraction
accuracy is not measured here. The paid dialog simulation that would measure it is not built. Three
live voice calls cover the LLM side qualitatively (see the main README).

Expected values were derived by hand from `backend/data/catalog.json`, not from resolver output.
The `heldout` cases were written after the rest of the system was built and were scored once, with
no tuning afterwards. Every other category was written by the same author as `aliases.json`, so
treat its numbers as an in-distribution upper bound.

## Dev vs held-out

A **dev** set is one whose failures we read and fixed against. Its after-fix numbers are reported,
but always labelled as seen. A **held-out** set is authored before the tuning or fixes it would
judge, frozen (sha256 or a commit), and scored once. Only held-out numbers are evidence that the
resolver generalizes.

| set | file | catalog | role |
|---|---|---|---|
| main | `cases.jsonl` (90 non-held-out) | SF | written alongside the rules; in-distribution upper bound |
| tune | `cases_tune.jsonl` (26) | SF | JEV gate thresholds chosen on it; not evidence |
| heldout | `cases.jsonl` (12 held-out) | SF | held-out |
| heldout2 | `cases_heldout2.jsonl` (49) | SF | held-out, written before any JEV run |
| national | `cases_national.jsonl` (48) | national | **dev**: first run recorded, then fixed against |
| national2 | `cases_national2.jsonl` (48) | national | **held-out**: frozen before the national fixes were scored, run once |

Headline numbers (wrong commits per commit; top-1 per turn), from the commands below:

| set | JEV off | JEV on |
|---|---|---|
| SF heldout + heldout2 (held-out) | 4/22 (18.2%); 27/61 | 2/39 (5.1%); 49/61 |
| national, first run (dev) | 6/27 (22%); 37/56 | 7/32 (22%); 41/56 |
| national, after fixes (dev, seen) | 1/35 (2.9%); 51/56 | 0/39; 56/56 |
| **national2 (held-out)** | **1/24 (4.2%); 37/54** | **1/37 (2.7%); 50/54** |

```
backend/.venv/Scripts/python eval/run_resolver_eval.py [--verbose]   # metrics + misses
backend/.venv/Scripts/python eval/naive_baseline_tokens.py           # prompt size vs naive dump
backend/.venv/Scripts/python -m pytest backend/tests/scheduling -q   # unit + policy property tests
```

Metrics:

- **wrong-commit**: the resolver offered or confirmed something it should not have (wrong type,
  doctor or site, or it should have asked or refused). Reported per commit (wrong commits / turns
  that offered or confirmed), not per turn: asks and refusals cannot commit wrongly, so a per-turn
  rate would flatter a resolver that rarely commits. The script ends with a headline table that
  keeps the held-out sets (heldout, heldout2, and both combined) apart from main and tune.
- **top-1**: the turn matches the expected result exactly.
- **ask precision/recall**: computed at the level of the question's field.
- **questions per booking**: counted over cases that end in an offer.
- **refusal correctness**: the refusal code matches, and so do the alternatives when the case
  lists them.
- **latency**: wall time of `resolve()` alone.
- **token sizes**: `Plan.say`, `Plan.summary` and the JSON tool result, measured with `o200k_base`.

## JEV (Command Code decision model) in the resolver

```
backend/.venv/Scripts/python eval/run_resolver_eval.py --set all --jev off           # free, no network
backend/.venv/Scripts/python eval/run_resolver_eval.py --set all --jev on            # offline, from .jev_cache.json
backend/.venv/Scripts/python eval/run_resolver_eval.py --set all --jev on --live     # real calls (~$0.004), refreshes cache
backend/.venv/Scripts/python eval/tune_jev_gate.py                                   # gate grid search, tune set only
backend/.venv/Scripts/python eval/jev_warmup_experiment.py --plan cold,warm --gap 30   # cold-start latency
```

Sets: `main` = the 90 non-held-out cases of `cases.jsonl`, `heldout` = its 12 held-out cases,
`heldout2` = `cases_heldout2.jsonl`, `tune` = `cases_tune.jsonl`.

`eval/.jev_cache.json` is committed. It is keyed by sha256 of the request body, so `--jev on` without
`--live` reruns every case offline and reproduces the live run's decisions exactly (checked by diffing
the two outputs: only the header line differs).

### The two new case files

- `cases_heldout2.jsonl` (49 cases: 37 loose type phrasings, 12 provider phrases with or without
  clues). Written from the caller's side, using catalog facts, before any JEV-enabled run and before
  thresholds were tuned. sha256 `f9e93f33a484460fefc51b2b4749bd974d97971017b80bf9cf52339df1744043`,
  recorded 2026-10-02 19:36 +02:00. It was not edited afterwards, and nothing was tuned on it.
- `cases_tune.jsonl` (26 cases, same mix, different phrasings). Used only to choose the gate.

### Where JEV is consulted

The resolver consults it (`scheduling/jev.py`, hooks in `scheduling/resolver.py`) only when:

1. **Types, no lexical match** ("something for my back pain"): a choice over all 74 offered types.
2. **Types, only a specialty default matched** ("lung test" only reached Pulmonology's default type).
3. **Types, the caller said more than the matched names/aliases explain** ("checkups while I'm
   expecting" leaves "expecting"; since round 3 also a single match: "shots before my trip to
   Thailand" leaves "trip" and "Thailand"). A bare "checkup", "MRI" or "follow-up" does not call
   JEV, and the caller is asked (see "What went wrong first" below).
4. **Providers**: two or more policy-valid providers match the name, and the phrase has a clue
   beyond the name, honorific and filler. Since round 3, catalog facts in the clue (language, title,
   specialty, site) are applied without a model; JEV is asked only for gender and for words no fact
   explains. A bare "Can I see Dr. Chen?" never calls JEV; the splitting question is a catalog fact.

Unoffered types are never JEV candidates ("we don't offer that" stays lexical). JEV only picks;
policy runs after it, so a policy-invalid pick takes the usual refusal path. A low-confidence answer
gives the no-JEV behavior. Since round 3, every type choice is followed by a check (see round 3
below), and a failed or timed-out answer is never committed on: the caller is asked (unit-tested).

### Gate, and how it was chosen

`Gate(act_p=0.8, margin=0.6, pair_p=0.85)` (`scheduling/decision.py`): act if top p >= 0.8 and it
leads the runner-up by >= 0.6; otherwise ask either/or between the top two if they sum to >= 0.85;
otherwise behave as if JEV were absent.

Chosen by `eval/tune_jev_gate.py` on `cases_tune.jsonl` only (26 cases), over a 1,210-gate grid, from
the committed cache. Objective: fewest wrong commits, then most correct turns, then fewest questions
per booking. Tie-break: highest act_p and margin, because a wrong commit is the costly error, then the
lowest pair_p, because an either/or beats an open question. The pair_p tie-break was first written
as "highest", which nearly disables either/or questions. I changed it after seeing the tune plateau
and before any heldout2 JEV run.

**The upper edge is noise-sensitive (measured).** After the first live run the procedure picked
0.7 / 0.4 / 0.85, because the plateau ended at tune-01 ("my stomach is upset all the time",
p = 0.72). The second live run refreshed the cache and JEV returned 0.80 for the same request, which
moved the plateau to act_p 0.5-0.8 and margin 0-0.6. The shipped gate is the procedure's output on
the committed cache, so rerunning the script reproduces it. JEV probabilities moved by 0.02-0.08
between identical requests (h2-01: 0.53 → 0.55; tune-01: 0.72 → 0.80), so a single tune case sits on
the act edge. Both gates are reported below.

### Results (thresholds frozen; `--jev on` from the committed cache reproduces them)

Final gate 0.8 / 0.6 / 0.85, offline from the cache. Latency columns are from the live run that
filled the cache.

| set | wrong-commit per commit off → on | top-1 off → on | questions/booking off → on | JEV requests | JEV latency p50/p95/max (live) | JEV $ |
|---|---|---|---|---|---|---|
| main (90 original, non-held-out) | 0/48 → 0/48 | 105/105 → 105/105 | 0.17 → 0.17 | 0 | - | $0 |
| heldout (12 original) | 1/5 (20%) → 0/6 | 9/12 → 12/12 | 0.17 → 0.00 | 3 (25%) | 512 / 1709 / 1709 ms | $0.00029 |
| **heldout2 (49 fresh)** | **3/17 (17.6%) → 2/33 (6.1%)** | **18/49 → 37/49** | **0.57 → 0.20** | 30 (61%) | 525 / 684 / 908 ms | $0.00205 |
| tune (26, used for tuning) | 4/12 (33.3%) → 2/22 (9.1%) | 10/26 → 24/26 | 0.45 → 0.00 | 16 (62%) | 551 / 654 / 693 ms | $0.00117 |

With the first gate (0.7 / 0.4 / 0.85), the live run scored: heldout2 2/49 wrong commits, 39/49
top-1, 0.15 questions per booking; heldout 1/12, 11/12. The first heldout2 JEV run of all (before the
confusable-tie fix below, gate 0.7 / 0.4) scored 2/49 and 38/49. One request failed in that run and
fell back. Apart from that failure, the fix changed no heldout2 decision.

Input tokens per request: about 2,455 for a type choice over 74 types and 400-600 for a provider
choice. At $0.04/M that is about $0.0001 per type request.

**Headline (held-out, heldout + heldout2 combined):** wrong commits per commit 4/22 (18.2%) without
JEV → 2/39 (5.1%) with it; turns fully correct 27/61 → 49/61. On heldout2 alone, turns fully correct
went from 37% to 76% and questions per booking from 0.57 to 0.20, with wrong commits 3/17 → 2/33. JEV is consulted on 61% of these deliberately loose turns, and on
0% of the original main set.

heldout2 misses with JEV on (not tuned on, reported as is):
- h2-p07 "Dr. Nguyen, the lady doctor" (**wrong commit**): JEV put 0.86 on Jennifer Nguyen, but
  Maria Nguyen is also a woman. The catalog has no gender field, so the model inferred gender from
  first names and was overconfident.
- h2-10 "shots before my trip to Thailand" (**wrong commit**, lexical, JEV not consulted): the
  "shots" alias matched Vaccination strongly.
- h2-01, h2-03, h2-05, h2-28, h2-p08: JEV asked a sensible either/or where the case expected a direct
  booking (sick visit vs GI, cardiology vs EKG, sick visit vs pulmonology, ENT vs hearing test, Jennifer
  vs Daniel Nguyen for "he speaks Spanish": 0.73 for Daniel). Extra questions, not wrong commits.
- h2-14 "a weird spot on my arm": JEV acted on Dermatology Consultation (0.97), so a referral question
  followed. The case expected Skin Cancer Screening.
- h2-02, h2-06, h2-34, h2-37: lexical paths JEV never sees (strong but wrong or over-wide alias matches).

**Original held-out misses (held-01, held-02, held-07 were seen before JEV was built, so they are
not evidence for it):** with JEV on, all three are now correct. held-02 ("something for my back
pain") and held-07 ("lung test") are acted on. held-01 ("my yearly exam", 0.77 / 0.23) gets the
expected either/or. Under the first gate (0.7 / 0.4), held-01 was a wrong commit.

### What went wrong first

Trigger 3 originally called JEV on every confusable tie. On the original main set that produced 7
wrong commits (0 without JEV): "checkup" → Annual Physical 0.75, "MRI" → brain 0.89, "follow-up" →
Follow-up Visit 0.94. With nothing in the caller's words to separate the tied types, the
probabilities are priors, and no threshold the tune set supports would block 0.94. So I didn't retune
on main. The fix is structural: a tie consults JEV only when the phrase has words the tied names and
aliases don't explain (`lexicon.unexplained_words`), the same rule as the provider clue.

### Latency and the live budget

The live call path used `JevClient(timeout_s=1.2, retries=0, turn_budget_s=1.2)` until round 3
(now 1.5 s per request within a 2.5 s turn budget; see round 3), with `begin_turn()` called before
each `resolve()`. The total wait is bounded with a worker thread, because httpx timeouts are per
phase. The eval uses 2.5 s and one connect retry so it fills the cache. Live requests over 1.2 s:
1 of 49 in the second live run (1,709 ms, the first request of the heldout set). In production that
turn would have fallen back to no-JEV.

Warm-up experiment (`eval/jev_warmup_experiment.py`; each trial on a fresh connection, unique phrases):

| trial (time) | idle before | first request | next requests |
|---|---|---|---|
| cold (19:42) | unknown, under 1 h | 894 ms (real) | 513, 516 ms |
| warm (19:52) | 10 min | 1,391 ms (warm_up, 74 types) | 698, 922, 659 ms |
| small (20:02) | 10 min | 1,377 ms (2-option request) | 523, 570, 579 ms |
| cold (20:48) | ~30 min of my traffic* | 1,588 ms (real) | 760, 557 ms |
| warm (21:18) | 30 min | 1,461 ms (warm_up, 74 types) | 488, 668, 507 ms |

\* Idle only for this worker's traffic. Other agents in the repo may have called JEV in between.

The lead's 12.5 s cold start did not reproduce after 10- or 30-minute idles. The first request on a new
connection costs about 0.9-1.6 s, and a tiny 2-option request pays it as fully as the 74-type
warm-up. So the penalty measured here is connection/first-request setup, not tied to the large
criteria set. `warm_up()` (15 s timeout) at call start, on the same keep-alive client, moves that
cost off the first caller turn: after it, the next requests ran 488-922 ms across both batches.
A multi-hour idle was not tested.

## National dev set (`national`)

`cases_national.jsonl` (48 cases) runs against `backend/data/national/catalog.json` (40 metros,
299 sites, 5,000 providers, 314 types). Every case pins `catalog_sha256`. `--set national` refuses
to run if the catalog's sha256 differs, and `--set all` stays the four SF sets.

```
backend/.venv/Scripts/python eval/run_resolver_eval.py --set national [--jev on]       # national catalog by default
backend/.venv/Scripts/python eval/naive_baseline_tokens.py --catalog backend/data/national/catalog.json
backend/.venv/Scripts/python eval/national/build_cases.py scenarios|phrase|merge     # how the cases were made
```

### Results: first run, then fixes (dev)

The first run was recorded before any national fix: `eval/results/national_v1_first_run.txt`.

| national (dev, 56 turns) | JEV off | JEV on |
|---|---|---|
| first run: wrong commits per commit | 6/27 (22.2%) | 7/32 (21.9%) |
| first run: top-1 | 37/56 (66.1%) | 41/56 (73.2%) |
| first run: questions per booking | 0.54 | 0.41 |
| first run: JEV requests | 0 | 12 (21% of turns), p50/p95 533/776 ms live |
| after fixes: wrong commits per commit | 1/35 (2.9%) | 0/39 |
| after fixes: top-1 | 51/56 | 56/56 |

The fixes are general rules, each unit-tested with wording that is not in the set: a hard 50-mile
cap with a `none_nearby` refusal, full type names beating the generic types they contain, ZIP and
misspelled-city precedence, and suburb clinics searching their own suburb. The SF eval output did
not change. Because these numbers were produced after reading the failures, they show that the fixes
work on the cases they were written for. They do not show generalization. national2 does.

### How the cases were authored

- **Independence.** The cases were written by a worker who did not read `resolver.py`, `lexicon.py`,
  `templates.py`, `decision.py` or `national/aliases.json`, and who did not run the resolver on any
  national case. That worker wrote them while another worker was changing the resolver, and they
  were frozen before the resolver was scored on them.
- **Ground truth from catalog queries.** `eval/national/build_cases.py scenarios` is a seeded sampler
  (seed 20261002) over `catalog.json`. It applies the six policies as set queries (type in the
  provider's types, site in the provider's sites, required capability at the site, referral,
  new-patient type and provider) and uses haversine distance. The results go to
  `eval/national/scenarios.json`, which keeps the facts behind each answer under `truth`.
- **DeepSeek writes the caller's words, nothing else.** `phrase` sends batches of scenario facts in
  plain language ("existing patient who twisted their knee playing soccer; they are in Seattle") to
  `deepseek/deepseek-v4.1-flash` through `cmdc -p` (text only, no tools). It asks for one phrase per
  field: service, place, doctor, and the follow-up answer to "which city?". DeepSeek never sees alias
  lists, code or expected answers. The raw prompts and responses are kept in
  `eval/national/deepseek_raw/` for audit.
- **Leak checks.** `merge` drops a phrasing when a required token is missing (the ZIP, neighborhood,
  doctor's first and last name, the city or one of its catalog aliases), when a forbidden one appears
  (a state in an either/or town, a specialty word in a symptom, a place inside the service phrase),
  or when the phrasing came from a prompt that no longer matches the scenario. The drops and
  repairs are listed below. The two misspelled cities are hand-written STT errors, not DeepSeek's.

Expectation keys added for these cases:

| key | meaning |
|---|---|
| `ask_field: "metro"` | the turn must ask which city (state only, an ambiguous town, or no place at all) |
| `types_any` | every offered type is in this set (the acceptable types for a symptom or service) |
| `location_ids_subset` | every offered site is in this set. On a refusal there must be at least one suggested alternative, and every alternative's site must be in this set |
| `max_miles` + `anchor` | every offered site is within `max_miles` of `anchor` [lat, lon] |
| `refuse_reason` | the refusal code (`none_nearby`, `location_type`, `new_patient_provider`, `new_patient_type`, `not_offered`) |

Categories: geo 18 (city 2, "I'm in" 2, suburb 1, ZIP 2, ZIP3-only 1, neighborhood 2, "near"
neighborhood 2, state only 1, either/or town 2, Portland Maine 1, misspelled city 2), symptom to
specialty 8, duplicate doctor names across metros 6 (3 with a city, 3 answered after a city question),
capability by metro 5, new-patient rules 5, no location 3, unoffered 2, ring expansion 1. A
multi-turn case checks its first turn's `expect` and its last turn's `expected`.

Choices made where the spec could not be met literally:

- **Portland OR vs ME.** The catalog has no Portland, Maine, and there is no geocoder, so the resolver
  cannot know "Portland" is ambiguous. The either/or cases therefore use towns that the catalog
  itself holds in two metros (Lakewood CO/OH, Glendale CA/AZ; Pasadena and Arlington also qualify).
  "Portland, Maine" expects `none_nearby` with the nearest Boston site as the alternative. Boston is
  the nearest metro with that service from both Portland ME and the Maine centroid.
- **Ring expansion.** Albuquerque has no eye care within 60 miles. The nearest metro with it is
  Houston, by both nearest site (729 mi) and metro center, and the second-nearest metro is at least
  25% farther away.
- Washington DC is always written "Washington, DC" so that it cannot be read as the state.

Authoring log: 4 batch calls plus 1 repair call (3 scenarios re-asked after I reworded them: DC
naming, and swapping "Therapy Session" for "Botox Treatment" because "therapy session" also fits
"Individual Therapy", which new patients may book). There was 1 failed call (cmdc exit 8, rerun) and
2 smoke tests, so 8 calls in all at roughly $0.01. After repair, the automatic checks dropped
0 cases.

## National held-out set (`national2`)

```
backend/.venv/Scripts/python eval/run_resolver_eval.py --set national2 --jev off
backend/.venv/Scripts/python eval/run_resolver_eval.py --set national2 --jev on      # offline, from the cache
backend/.venv/Scripts/python eval/national/build_cases.py --set national2 scenarios|phrase|merge
```

### How it was kept independent

- **Different draw.** Seed 20261117 (the dev set uses 20261002), over different services, symptoms
  and types. The sampler excludes any scenario that shares a type and metro, a site, a provider, a
  name or a place with the dev set.
- **Authored blind.** The cases were written without access to the resolver code or to the dev-set
  failures. Ground truth comes from catalog queries, as for the dev set. DeepSeek wrote the caller's
  wording from scenario facts (raw prompts and responses in `eval/national2/deepseek_raw/`).
- **Frozen first.** The set was committed before the national fixes were scored on it, then run once
  after the fixes. Nothing was changed in response to its misses.

Categories (48 cases, 54 turns): geo 18 (city 2, "I'm in" 2, ZIP 2, "near" neighborhood 2,
neighborhood 2, either/or town 2, misspelled city 2, suburb 1, ZIP3 1, state only 1, far place 1),
symptom 8, duplicate names across metros 6, capability by metro 5, new-patient rules 5, no location 3,
unoffered 2, ring expansion 1. Every case pins the catalog's sha256.

### Results

| national2 (held-out) | JEV off | JEV on |
|---|---|---|
| wrong commits per commit | 1/24 (4.2%) | 1/37 (2.7%) |
| top-1 | 37/54 (68.5%) | 50/54 (92.6%) |
| questions per booking | 0.54 | 0.21 |
| JEV requests | 0 | 17 (31% of turns) |
| JEV latency p50/p95/max (live) | - | 550 / 720 / 790 ms |
| JEV cost for the set | $0 | $0.0008 |

The live run is recorded in `eval/results/national2_heldout_jev_off.txt` and
`eval/results/national2_heldout_jev_on.txt`. In that run one of the 17 JEV requests failed and fell
back to asking, so it scored 1/36 and 49/54 with 0.23 questions per booking. The cache now holds an
answer for that request, and the current code replays to the numbers in the table.

Misses with JEV on (4):

- nat2-new-05 (**wrong commit**): a new patient asking for "allergy shots", a type new patients may
  not book, was offered an Allergy Consultation instead of a refusal. A type choice, not a policy
  violation: the offered type is one new patients may book.
- nat2-geo-09: asked "Miami or St. Louis?" where the case expected an offer.
- nat2-geo-17: refused a chest X-ray at the named site and offered two nearby alternatives, where the
  case expected an offer.
- nat2-dup-06: asked "annual physical or annual wellness visit?" where the case expected an offer.

## Without JEV: OpenAI and local embeddings as the chooser

```
backend/.venv/Scripts/python eval/run_resolver_eval.py --set tune --chooser openai|embed|jev|none
backend/.venv/Scripts/python eval/tune_embed_temperature.py       # dev sets only
backend/.venv/Scripts/python eval/compare_choosers.py             # held-out table, from the caches
```

The same three hooks (type, provider, site), the same option texts and the same Gate (act_p 0.8,
margin 0.6, pair_p 0.85, chosen on `cases_tune.jsonl` for JEV) with a different model behind them:

- **OpenAI** (`scheduling/openai_chooser.py`): gpt-4o-mini sees the caller's phrase and the options
  behind numeric keys (`1`..`N`, each one o200k token; `0` means "none of these") and answers one
  token. The `top_logprobs` (20) of that token are the distribution. Mass on `0`, on non-key tokens
  and outside the top 20 is not renormalized onto the options: it lowers the top p, so a hesitant
  answer asks instead of acting. gpt-4.1-nano was tried on the tune set and made 9 wrong commits of
  24 against gpt-4o-mini's 4 of 24, so gpt-4o-mini it is. Answers are cached in
  `eval/.openai_cache.json` (keyed by request body hash) and replay offline.
- **Embeddings** (`scheduling/embed_chooser.py`): fastembed `BAAI/bge-small-en-v1.5` (ONNX, CPU,
  64 MB downloaded once), cosine similarity between the phrase and each option, softmax with
  temperature T. T=0.0125 was chosen on `cases_tune` + `cases_national` by the Gate's objective
  (fewest wrong commits, then top-1, then questions); the bge query prefix was tried and gained 1 turn
  of 82, so it was left out. Startup cost, measured: model load 1.0 s, national catalog's 314 types
  and 299 sites 3.4 s, both in the background preload thread; the 5,000 providers would take ~34 s,
  so they are embedded on first use (only same-named providers ever reach the chooser). One phrase:
  ~6 ms.

### Held-out results (run once, after tuning was frozen)

Recorded in `eval/results/chooser_comparison.txt`. Model rate: model requests per resolve turn (the
hooks fire on the same turns in every mode). Latency: per request as measured when fetched (network
round trip for JEV and OpenAI, local CPU for embeddings). $ per 1k turns: priced as if every request
were live.

| set | mode | wrong commits | top-1 | q/booking | model rate | p50/p95 ms | $ / 1k turns |
|---|---|---|---|---|---|---|---|
| national2 | JEV | 1/37 (2.7%) | 50/54 (92.6%) | 0.21 | 31% | 533/720 | $0.017 |
| national2 | OpenAI | 1/35 (2.9%) | 48/54 (88.9%) | 0.23 | 31% | 504/771 | $0.027 |
| national2 | Embeddings | 2/33 (6.1%) | 45/54 (83.3%) | 0.31 | 31% | 8/11 | $0 |
| national2 | Off | 1/24 (4.2%) | 37/54 (68.5%) | 0.54 | 0% | - | $0 |
| SF heldout2 | JEV | 2/33 (6.1%) | 37/49 (75.5%) | 0.20 | 61% | 525/684 | $0.042 |
| SF heldout2 | OpenAI | 8/38 (21.1%) | 33/49 (67.3%) | 0.12 | 61% | 508/652 | $0.071 |
| SF heldout2 | Embeddings | 6/32 (18.8%) | 29/49 (59.2%) | 0.28 | 61% | 6/22 | $0 |
| SF heldout2 | Off | 3/17 (17.6%) | 18/49 (36.7%) | 0.57 | 0% | - | $0 |

Trade-offs. On the national held-out set the commodity choosers recover most of JEV's gain (measured:
top-1 68.5% off, 83.3% embeddings, 88.9% OpenAI, 92.6% JEV, with wrong commits flat at 1 to 2). On the
SF held-out set, where most hook calls split same-named doctors by clue words ("the lady", "he speaks
Spanish", "works at Mission Bay"), both alternatives commit wrongly far more often than JEV (measured:
8 and 6 wrong commits against 2); gpt-4o-mini puts p=1.00 on wrong doctors, so a Gate tuned to JEV's
calibration acts on them (inferred: the logprobs are overconfident, and the Gate would need its own
tuning per model). Embeddings are free, local and about 60x faster per request, but a pronoun or a language does not
move a cosine similarity much, so they are a fair fallback for visit types and a poor one for people
(inferred from the misses). Model cost is at most $0.07 per 1,000 turns (measured), small next to the
gpt-4o conversation itself (inferred), so the choice is about wrong commits and latency, not money.

OpenAI spend for all of this work: 87 requests, 64,048 input + 87 output tokens, about $0.009
(51,007 gpt-4o-mini tokens at $0.15/M and 13,041 gpt-4.1-nano tokens at $0.10/M).

## Held-out round 3 (`heldout3`, `national3`)

Both sets were authored blind on 2026-10-03, while resolver changes were in progress. The author did
not read resolver code, resolver results or failure transcripts, and has not scored either set. Every
expectation comes from catalog queries in the generators. DeepSeek (12 calls via `cmdc`) wrote only
the caller's words, from plain-language scenario facts. Relabels and uncertain labels are listed in
`heldout3/label_notes.md`. sha256 values are of the committed (LF) bytes.

- `cases_heldout3.jsonl` (SF catalog, 54 cases): sha256
  `300e08e424db0446f08969c62212139b525fb041b89becf31bf5f86eae468400`. heldout3_type 35 (test 6,
  symptom 8, colloquial 9, abbreviation 2, slang 4, two types fit so ask 4, not offered 2);
  heldout3_provider 14 (specialty 2, title 2, language 3, full name 2, gender 2, site 3);
  heldout3_policy 5 (new_patient_type 2, referral 1, new_patient_provider 2). Expected: 39 offer,
  7 ask, 8 refuse.
- `cases_national3.jsonl` (national catalog, 48 cases, each pinning `catalog_sha256`): sha256
  `2bd6dd084954b5c287ed23e2fdbafa71a46b6cf93bfacaa25a5a5ba8e6771c2b`. geo 18, symptom 8,
  dup_name_metro 6, capability_metro 5, new_patient 5, no_location 3, unoffered 2, ring 1 (the same
  mix as national2). Seed 20261003. No type+metro pair is shared with national or national2.

Regenerate (raw DeepSeek outputs are committed, so reruns make no calls):

```
backend/.venv/Scripts/python eval/heldout3/build_cases.py scenarios
backend/.venv/Scripts/python eval/heldout3/build_cases.py merge
backend/.venv/Scripts/python eval/national/build_cases.py --set national3 scenarios
backend/.venv/Scripts/python eval/national/build_cases.py --set national3 merge
backend/.venv/Scripts/python eval/validate_case_format.py heldout3 national3   # format only, no scoring
```

`phrase` (and `repair IDS TAG`) is the step that calls DeepSeek. The national merge needs
`backend/data/national/catalog.json` checked out with LF line endings, so that its sha256 matches
`catalog.meta.json`.

## Round 3: every set is dev; JEV hillclimb to zero wrong commits

All the sets above are now **dev** sets: their failures were read and fixed against. The blind
held-out round 3 sets (`cases_heldout3.jsonl`, `cases_national3.jsonl`) were authored separately and
are scored once, after this work. Nothing here was run on them. Every number below is from dev
sets that were studied, so it shows the fixes work on the cases they were written for, not that
they generalize.

Priorities, set by the user during the round: zero wrong commits in JEV mode first, top-1 second;
when in doubt, ask; an answer that does not arrive is never committed on.

### Results (offline, from the committed caches)

Wrong commits per commit; top-1 per evaluated turn. Baseline is `phase1-ui` (76587ab).

| set | JEV before | JEV after | no model before | no model after |
|---|---|---|---|---|
| main | 0/48; 105/105 | 0/48; 105/105 | 0/48; 105/105 | 0/48; 105/105 |
| heldout | 0/6; 12/12 | 0/6; 12/12 | 1/5; 9/12 | 1/5; 10/12 |
| heldout2 | 2/33; 37/49 | **0/39; 48/49** | 3/17; 18/49 | 3/24; 28/49 |
| tune | 2/22; 24/26 | **0/22; 26/26** | 4/12; 10/26 | 4/15; 14/26 |
| national | 0/39; 56/56 | 0/39; 56/56 | 1/35; 51/56 | 1/35; 51/56 |
| national2 | 1/37; 50/54 | **0/38; 53/54** | 1/24; 37/54 | 0/25; 40/54 |
| street | 0/25; 36/36* | 0/25; 36/36 | 0/25; 36/36 | 0/25; 36/36 |

\* The baseline's 3 street site-chooser requests were never cached, so they failed and declined;
answered, one of them (str-25) asked "Downtown or Midtown?" until the street rule below.

The no-model wrong-commit count never rose; it fell on national2. Other choosers, same sets
(before -> after): OpenAI heldout2 8/38; 33/49 -> 3/35; 38/49, tune 4/24; 22/26 -> 1/20; 22/26,
national2 1/35; 48/54 -> 1/35; 49/54; embeddings heldout2 6/32; 29/49 -> 5/31; 31/49, tune
4/20; 18/26 -> 3/18; 17/26, national2 2/33; 45/54 -> 1/36; 50/54. Neither crashes; OpenAI has
no yes/no question, so it rules nobody out by gender, and embeddings have no check.

### What changed (one mechanism per commit, each measured on every dev set)

| mechanism | where | measured effect (JEV unless noted) |
|---|---|---|
| A clinic named outright beats a soundalike; a misheard city beats a clinic sharing some of its words | `names.match_locations`, `geo._resolve_head` | national2 50 -> 52/54 ("near The Hill", "San Antonyo") |
| A said alias drops types whose only evidence lies inside it | `lexicon._drop_inside_aliases` | national2 wrong 1 -> 0: "I need my allergy shots" (new patient) is refused, not offered the consultation |
| Words that are never names do not reach surnames by sound | `names.match_providers` | no metric change; "Dr. Chen, the family medicine one" reaches 3 Chens, not 15 providers |
| Same-named providers: catalog facts first, then gender from first names, then the model | `names.read_provider_clues`, `JevProviderChooser.provider_genders` | heldout2 37 -> 39/49, wrong 2 -> 1 ("the lady doctor" asks between the two women; "he speaks Spanish" books Daniel); no model +10 turns |
| A street that put the caller at no clinic is no site clue | `resolver._place_clue` | street str-25 offers in Atlanta |
| Every type choice gets a second question, with an "either" answer | `JevTypeDisambiguator.check_type`, `decision.CheckGate` | heldout2 39 -> 43/49 (h2-01, h2-03, h2-05, h2-28); national2 53 -> 52 (nat2-sym-04 asks) |
| A lexical match that leaves words unexplained is heard by the model | `resolver._verify_types` | heldout2 43 -> 46/49, wrong 1 -> 0; tune 24 -> 26/26, wrong 2 -> 0 (Thailand, hay fever, skip breakfast) |
| A specialty's default is no evidence beside an alias of that specialty | `lexicon._score_types` | heldout2 46 -> 47 ("an echo for my heart"); no model +2 |
| A model answer that never came is never committed on | `decision.FAILED`, resolver | unchanged offline; 18 unit cases |
| "yearly" says "annual" | `lexicon._SAME_AS` | national2 52 -> 53 ("I need my yearly physical"); no model +3 |
| A spoken ordinal street ("second street") is a street word | `names.hear_place` | OpenAI street wrong 1 -> 0 |

Reverted: dropping the specialty default before lexical verification existed ("talk to the bone
doctor before my knee surgery" became a wrong commit). Every attempt, kept or not, is a row in the
lead's `.kstack/log.tsv`.

### The check, and how its thresholds were chosen

A choice question over many types is overconfident on words that fit two visits alike and
under-confident on clear symptoms: a live replay of the cached "my yearly exam" request put 0.80 on
Annual Physical, while "my tummy's been hurting for weeks" got 0.55 for GI. After every choice,
the front-runner and its rival (the runner-up, or the lexical match the choice overruled) are asked
about alone, with their aliases and booking rules, plus "either: nothing the caller said tells these
two visits apart". `CheckGate` settles it:

- a confident choice stands unless "either" >= 0.8 or the rival leads the check by >= 0.2;
- a choice that left two is settled only by a check leader >= 0.65 with "either" < 0.5;
- an unanswered check asks (a confident choice is confirmed alone: "Is that a school physical?").

(As of the review fixes below: a confident choice stands only if the check still prefers it, and a
pair is settled only by a leader above 0.65.)

Chosen on the dev sets, from an experiment over the 132 dev phrases that reach a model: twins the
labels ask about scored "either" 0.92-0.98 ("checkup", "follow-up", "my yearly exam"), pairs the
labels accept either way 0.29-0.52. The settle value is the least robust: h2-28 ("I can't hear well
out of my left ear") settles at 0.65 exactly, h2-02 (labelled an either-or) has "either" 0.60.

### Latency, requests per turn, cost

A type decision is now two sequential requests (choice, then check). Measured live, production
client settings, cache bypassed, after warm-up, on heldout, heldout2, tune, national and national2
(197 turns, 125 with JEV): 193 requests, 0.98 per turn and 1.54 per JEV turn. All turns p50 523 ms,
p95 1,314 ms, max 2,158 ms; JEV turns p50 974 ms, p95 1,390 ms. One request hit the 1.5 s cap and
its turn asked. Live decisions: top-1 194/197, wrong commits 0/143 (misses: h2-14, nat2-sym-04 as
offline, and nat-noloc-03, the capped request).

With the old 1.2 s turn budget, 15 of 145 live requests on heldout2, tune and national2 ran out of
budget and 14 turns asked instead of booking, so the agents' `resolver.timeout_ms` is now 2500 and
one request may use at most 1.5 s of it. The voice path says "One moment." after 0.3 s.

Resolver alone, no network (p95, `--jev off`): national 12.1 -> 11.9 ms, national2 8.2 -> 7.7 ms,
street 18.0 -> 16.0 ms, SF main 5.9 -> 6.0 ms. p50 rose by 0.2-0.4 ms.

JEV spend for the round: about $0.06 (measured: 143 eval-cache requests, 138,221 input tokens,
$0.0055; 248 framing-experiment requests, 522,724 tokens, $0.021; estimated: 717 check and yes/no
experiment requests and 483 live latency requests, about $0.035). OpenAI: 132 requests, 58,856
input tokens, $0.0089.

### Still missed with JEV (asks, not wrong commits)

- h2-14 "a weird spot on my arm I want looked at" (expects Skin Cancer Screening): the choice says
  Dermatology Consultation 0.97, the check says Skin Cancer Screening 0.35 against 0.13 (either
  0.52). The two questions disagree, so the caller is asked between the two.
- nat2-sym-04 "my thumb and fingers ... go numb and tingle, worse at night" (accepts either):
  the choice says Hand and Wrist 0.94, the check says Neurology 0.59 against 0.18. Asked.

Making either one book would mean committing while the model contradicts itself, which this round's
priorities rule out.

### Label corrections, round 3

| case id | old | new | reason |
|---|---|---|---|
| h2-34 ("my annual") | ask between appt_002 (Annual Physical) and appt_003 (Annual Wellness Visit) | ask between appt_002, appt_003 and appt_041 (Annual Well-Woman Exam) | appt_041 is also an annual visit open to this returning patient (no referral), so "my annual" names it as much as the other two. |
| h2-p01 ("Dr. Chen, the woman", cardiology) | offer prov_046 (Emily Chen) | ask provider, options [prov_046] ("Do you mean Dr. Emily Chen?") | **Policy change (pre-registered): inferred gender never commits alone.** Not a label error. Gender is the only evidence that singles out Emily Chen (David Chen p(woman) 0.09 is ruled out, Emily 0.83 is unknown), so the doctor is confirmed by name. |

tune-p1 ("Dr. Chen, the guy") keeps its label (offer prov_000): under the policy it asks between both
cardiology Chens, not a one-option confirmation, because Emily Chen's 0.83 is not sure enough to rule
her out. It is counted as a miss.

## Round 3 review fixes

The round 3 review found commits on misread evidence. Each finding is one commit, measured on
every dev set in both modes. Accuracy first: when in doubt the resolver asks.

### Results (offline, from the committed caches)

Wrong commits per commit; top-1 per evaluated turn. "Before" is the reviewed round 3 hillclimb, an intermediate state inside `4bf40d5`.

| set | JEV before | JEV after | no model before | no model after |
|---|---|---|---|---|
| main | 0/48; 105/105 | 0/48; 105/105 | 0/48; 105/105 | 0/48; 105/105 |
| heldout | 0/6; 12/12 | 0/6; 12/12 | 1/5; 10/12 | 1/5; 10/12 |
| heldout2 | 0/39; 48/49 | 0/36; 45/49 | 3/24; 28/49 | 3/24; 28/49 |
| tune | 0/22; 26/26 | 0/21; 24/26 | 4/15; 14/26 | 4/15; 14/26 |
| national | 0/39; 56/56 | 0/39; 56/56 | 1/35; 51/56 | 1/35; 51/56 |
| national2 | 0/38; 53/54 | 0/38; 53/54 | 0/25; 40/54 | 0/25; 40/54 |
| street | 0/25; 36/36 | 0/25; 36/36 | 0/25; 36/36 | 0/25; 36/36 |

heldout2 "after" is scored with h2-p01's policy label (it passes); against its old label it would
be 44/49. Turns that no longer book, and why:

- h2-p07 "Dr. Nguyen, the lady doctor": asks among all three pulmonology Nguyens (label: the two
  women). Jennifer 0.85 and Maria 0.87 are not sure enough to count, and Daniel 0.17 is not sure
  enough to rule out.
- h2-p08 "Dr. Nguyen, he speaks Spanish": the facts leave Jennifer and Daniel; neither gender
  counts (0.82, 0.13), so it asks between them (label: Daniel).
- tune-p1 "Dr. Chen, the guy": David 0.09 counts as male but Emily 0.83 is unknown, so nobody is
  ruled out and it asks between both (label: David).
- tune-p5 "Dr. Patel, a woman": no Patel's gender counts (0.12-0.89), so it asks for the first name
  among four (label: the two women).
- h2-28 "I can't hear well out of my left ear": the check's lead is 0.65, exactly the settle
  threshold, which now takes the safe side (asks Hearing Test or ENT Consultation).

None of these is a wrong commit. The no-model column is identical turn for turn.

### What changed

| finding | mechanism | where | measured |
|---|---|---|---|
| 1 (critical) | A provider site fact needs a site's own name word as said, or its street said as a street; gender and link words never reach site matching; a negation asks without narrowing or a model; kin words (son, daughter, child, kid, boy, girl, baby; "my kids") are no specialty | `names.read_provider_clues`, `names._sites_named`, `resolver._consult_provider` | the review's 7 probes and "my main doctor" ask in both modes (were 7 wrong commits); dev unchanged |
| 2 | A confident choice stands only if the check still prefers it (chosen > rival); the conflict rule is gone (implied) | `decision.CheckGate` | stub 0.05 / 0.20 / 0.75 asks; dev unchanged |
| 3 | An alias drops a named type only when it covers more than that type's evidence (strict subset) | `lexicon._drop_inside_aliases` | national "I need my a1c" -> A1C Test; upper endoscopy, tonsils, depression keep their named types; allergy shots still refused; dev unchanged |
| 4 | Gender policy (below) | `decision.gender_of`, `resolver._consult_provider`, `resolver._confirmed_provider`, `names._said_gender`, `names.read_confirmation`, `names.match_providers` | see results |
| 5 | A runner-up under 0.05 gives way to the choice's nearest neighbour as the check's rival | `resolver._rival`, `lexicon.nearest_type` | 20 of 68 dev checks got a new rival; 0 outcomes changed |
| 6 | Sensitivity table (below); settle is strict at its boundary | `decision.CheckGate`, `eval/threshold_sensitivity.py` | h2-28 asks |
| 7 | Time words, a catalog name after a title, a place after a preposition are explained; an answer to a type question is heard among the options asked | `lexicon.unexplained_words`, `request.TIME_WORDS`, `resolver._verify_types` | "a flu shot today / next week / with Dr. Chen / at Mission Bay": 2 requests -> 0 (SF and national); dev unchanged |
| 8 | Check verdicts and final p in the Dev view; a gender verdict only when a question was asked; post-check type guard; miss printout shows checks and facts; no lock, 2-worker pool; `_SAME_AS` beside the word lists | `agent_tools/context.py`, `scheduling_tools._decision_event`, `jev.py`, `resolver._model_types`, `run_resolver_eval.py`, `lexicon.py` | dev unchanged |

Requests and latency: checks per type-model turn unchanged (68 checks over 119 JEV type turns, 193
requests over the dev sets), and the new rivals cost no outcome. Resolver alone, no network,
p95 before -> after (same machine, run back to back): main 6.08 -> 6.31 ms, national 12.79 -> 12.17
ms, national2 8.06 -> 8.38 ms, street 16.80 -> 16.68 ms.

Other choosers (wrong commits; top-1), before -> after: OpenAI heldout2 3/35; 38/49 -> 3/35; 38/49,
tune 1/20; 22/26 -> 1/21; 23/26, national2 1/35; 49/54 -> 1/36; 50/54; embeddings unchanged
(heldout2 5/31; 31/49, tune 3/18; 17/26, national2 1/36; 50/54). OpenAI has no yes/no question,
so it never narrows by gender.

Spend for these fixes: JEV 19 requests (the new check rivals), 8,041 input tokens, $0.00032; OpenAI
21 requests (the same rivals), 3,692 input tokens, $0.00057. Both caches are committed.

### Gender policy (pre-registered before round 3 scoring)

Inferred gender (JEV reads it off first names; the catalog has no gender field) may narrow the
doctors but never books one on its own:

- It counts only at p(woman) >= 0.9 (female) or <= 0.1 (male). Unknown is never ruled out.
- A doctor that gender alone singles out is confirmed by full name: "Do you mean Dr. Emily
  Chen?" (ask provider, one option). So is the one doctor the catalog facts leave when the model
  cannot confirm they are the gender the caller said. A gender that rules out everyone the facts
  left asks among all candidates.
- Only words about the doctor count: woman, lady, female, man, male, guy, gentleman, and he / she
  before any other person is mentioned ("she's the one at Mission Bay"). "her baby", "my
  daughter's doctor, she ...", "women's health" are no evidence.
- The answer: "yes" books that doctor; any "no" ("no", "not her", "the other one", "no, Lucas
  Chen") asks about the other doctors of that surname, even when one is left; a name is matched as
  a name, and a better match outside the options asked about wins ("David Chen" after "Do you mean
  Dr. Emily Chen?").

Dev name scores (p(woman), every gender request in the cache): David 0.09, Andre 0.12, Daniel
0.13 / 0.17, Kenji 0.16, Jennifer 0.82 / 0.85 (with different other names in the request), Emily
0.83, Olivia 0.85, Maria 0.87, Fatima 0.89. At 0.9 / 0.1 only David counts, so gender narrows
almost nothing on the dev sets with the current single-request framing.

### Threshold sensitivity (not re-tuned)

The thresholds were not re-tuned on these dev sets. Each was moved by -0.05 and +0.05 with the
others at their shipped values, over every dev set in JEV mode from the committed cache
(`eval/threshold_sensitivity.py`; the same requests, so no request failed in any variant).
Shipped: top-1 323/330 resolve turns (lookups excluded), 0 wrong commits.

| threshold (shipped) | -0.05 | +0.05 |
|---|---|---|
| twins: "either" at or above it asks (0.80) | no flips | no flips |
| act rule: a confident choice stands only if check chosen > rival (margin 0) | no flips | nat2-geo-06 offer -> ask (chosen 0.26, rival 0.24, either 0.50); top-1 -1 |
| conflict: rival ahead by this much asks (was 0.20) | removed: implied by the act rule, no outcome can depend on it | removed |
| settle: a pair is settled above it (0.65, strict) | h2-28 ask -> offer (lead 0.65); top-1 +1 | nat-sym-02 offer -> ask (lead 0.69); top-1 -1 |
| settle_either: a pair is settled only with "either" under it (0.50) | no flips | no flips |
| gender: p(woman) >= it is female, <= 1 - it male (0.90) | tune-p5 asks among 3 Patels instead of 4 (Andre 0.12 counts as male); still a miss | h2-p01 one-option confirm -> ask between both Chens (David 0.09 no longer counts); top-1 -1 |

No variant adds or removes a wrong commit. Two thresholds sit next to a case: settle 0.65 sat
exactly on h2-28 (check lead 0.65), which booked because the comparison was `>=`. It is now strict,
so h2-28 asks between Hearing Test and ENT Consultation: the safe side, at a cost of one heldout2
turn (46 -> 45/49). The act rule's thinnest lead is nat2-geo-06 at 0.02 (a 0.26 vs 0.24 check with
"either" 0.50): it books, and would ask under any positive margin. Gender 0.9 has no dev name within
0.01 (Fatima 0.89, David 0.09).

## Held-out round 3: scored once (2026-10-03)

`heldout3` and `national3` were frozen in commit `085a9e4` before any round 3 resolver change
landed. They were scored once, after the review fixes and the cleanup, with every chooser. JEV and
OpenAI ran live (`--live`). Raw outputs are in `eval/results/round3_*.txt`. Nothing below was tuned
on these sets.

| set | mode | wrong commits | top-1 | questions per booking |
|---|---|---|---|---|
| heldout3 (SF, 54 turns) | no model | 18/29 (62.1%) | 20/54 | 0.38 |
| | embeddings | 16/36 (44.4%) | 29/54 | 0.18 |
| | OpenAI gpt-4o-mini | 8/40 (20.0%) | 42/54 | 0.08 |
| | **JEV** | **7/42 (16.7%)** | **42/54** | 0.05 |
| national3 (56 turns) | no model | 5/19 (26.3%) | 28/56 | 0.67 |
| | embeddings | 5/31 (16.1%) | 41/56 | 0.33 |
| | OpenAI gpt-4o-mini | 3/31 (9.7%) | 43/56 | 0.38 |
| | **JEV** | **3/35 (8.6%)** | **46/56** | 0.26 |

The dev sets read 0 wrong commits in JEV mode, so the held-out sets show a real generalization gap.
The JEV-mode wrong commits fall into general failure classes, not one-offs:

- **The check committed while its own top answer was "either"** (h3-32, "my yearly all-over skin
  check to look for skin cancer": chosen 0.34, rival 0.25, either 0.41). The rule required only
  chosen > rival. nat2-geo-06 on dev passes the same way, at chosen 0.26, rival 0.24, either 0.50.
- **The caller said they did not know, and the resolver still chose** (h3-31, a scope where the
  caller does not remember "if it goes down my throat or up from below").
- **A service the clinics do not offer was replaced by a related one** (h3-34, "start PT after my
  shoulder surgery" offered an orthopedic consultation instead of refusing).
- **A named provider excluded by policy was replaced by another** (h3-p04, a new patient asking for
  "the nurse practitioner, Dr. Hernandez" was offered a different Dr. Hernandez instead of being told
  the nurse practitioner is not taking new patients).
- **A clinic named to pick the doctor did not also limit where** (h3-p11, h3-p14, "Dr. Michael
  Sato, the one at the Sunset clinic" offered Sato at North Gate too).
- **Alternatives skipped closer clinics** (nat3-geo-10: Rockridge drug test offered Santa Clara
  and Evergreen, about 40 miles away, while San Francisco sites within 13 miles qualify).
- **Type choice errors the check confirmed** (nat3-sym-05: perimenopause symptoms offered an OB/GYN
  new patient visit).
- **A habitual two-visit phrase** (h3-33, "regular six-month dental checkup" chose Dental Exam over
  Dental Cleaning). Its author marked this label as uncertain before scoring.

Other misses are asks, not wrong commits. They include two policy-driven ones:
- h3-p08 ("Dr. Garcia, the male one" asks, because p(woman) for Carlos is 0.16, above the
  pre-registered 0.1).
- h3-p09 (asks for the first name; the label expects a two-option provider ask, an ask-field
  convention the author flagged before scoring).

**Integrity notes.**
1. A unit test iterated over every `eval/cases*.jsonl`, so two pytest runs resolved the blind
   sets. They asserted only that no spoken text contains "None", passed silently, and printed
   nothing. Tests now read dev sets only (`4bf40d5`).
2. A cleanup worker's code search previewed h3-01 to h3-05 (phrases and expected types). That
   worker changed no decision logic: dev decisions were identical before and after its commits in
   all four modes. Excluding h3-01 to h3-05, heldout3 reads: no model 15/25 wrong, top-1 19/49;
   embeddings 14/32, 27/49; OpenAI 7/36, 39/49; JEV 7/37 (18.9%), 38/49.

From here on, `heldout3` and `national3` are dev sets. A fresh blind round judges the next fixes.

## Held-out round 4 (`heldout4`, `national4`)

Frozen on 2026-10-03 and not yet scored. Both sets were authored blind. The author read no resolver code
(`backend/scheduling/**`), no resolver results (`eval/results/**`) and no README section after the
round 3 heading. Neither the resolver nor `run_resolver_eval.py` was run on these cases. Both are
registered as blind in `eval/sets.py`, so no test, tuning run or `--set all` reads them. Labels come
from catalog queries. DeepSeek (`deepseek/deepseek-v4.1-flash` via `cmdc`) wrote only the caller's
words. Regenerated phrasings and labels I was unsure of are listed in `eval/heldout4/label_notes.md`.

| set | file | catalog | cases | turns | sha256 of the committed (LF) file |
|---|---|---|---|---|---|
| heldout4 | `cases_heldout4.jsonl` | SF | 56 | 62 | `669c339329004373413eee3435862cd0f4c795231eb4dd688d4696014de019e7` |
| national4 | `cases_national4.jsonl` | national | 48 | 56 | `701102143a9b9a84eda94ed57e4fc8388ea06308b69b34d67f4c7986a2fea007` |

Categories:

- **heldout4**: type 31 (lay test name 4, symptom 7, everyday description 8, abbreviation 3, slang 3,
  genuine two-way ask 4, not offered 2). Policy 5 (new-patient type 2, referral 1, new-patient
  provider 2). Provider description 14 (specialty 3, title 2, language 2, full name 2, gender 2,
  site 3); in 4 of these the description fits 2 or 3 doctors, so the case expects a provider
  question. Two-turn 6: the agent asks and the caller picks a provider (3) or a service (3).
- **national4**: geo 18 (city 2, "I'm in" 2, suburb 1, ZIP 2, ZIP3 1, neighborhood 2, "near"
  neighborhood 2, state only 1, either/or town 2, far place 1, misspelled city 2), symptom 8,
  duplicate names across metros 6 (3 with a city, 3 answered after a city question), capability by
  metro 5, new-patient rules 5, no location 3, unoffered 2, ring expansion 1. The seed is 20261004. No
  scenario shares a type+metro or a provider+type pair with national, national2 or national3 (the
  `scenarios` step prints 0/48 for each). Every case pins the catalog's sha256.

Generator commands:

```
backend/.venv/Scripts/python eval/heldout3/build_cases.py --set heldout4 scenarios|phrase|merge
backend/.venv/Scripts/python eval/heldout3/build_cases.py --set heldout4 repair h4-01,h4-05,h4-08,h4-23
backend/.venv/Scripts/python eval/national/build_cases.py --set national4 scenarios|phrase|merge
backend/.venv/Scripts/python eval/validate_case_format.py heldout4 national4   # format only, 0 problems
```

DeepSeek calls: 10, none of them failed. heldout4 took 5 batches plus 1 repair call covering 4
scenarios; national4 took 4 batches. After the repair, the leak checks dropped no cases.

## Round 4 fixes: the round 3 failure classes

`heldout3` and `national3` are dev sets now (`eval/sets.py`), so tests and the sensitivity sweep
read them. Every fix below was written after reading their failures, so these numbers show that the
fixes work on the cases they were written for. Only the round 4 blind sets can show whether they
generalize. Each fix is a general mechanism, unit-tested with wording that is not in any set. No
threshold was re-tuned. The check gate's margin is new, and its value is explained below.

### Results (offline, from the committed caches)

Wrong commits per commit; top-1 per evaluated turn. "Before" is `phase1-ui` (025f712), replayed from
the same caches. heldout3 JEV before reads 43/54 here: in the scored-once live run one request failed
(42/54).

| set | JEV before | JEV after | no model before | no model after |
|---|---|---|---|---|
| main | 0/48; 105/105 | 0/48; 105/105 | 0/48; 105/105 | 0/48; 105/105 |
| heldout | 0/6; 12/12 | 0/6; 12/12 | 1/5; 10/12 | 1/5; 10/12 |
| heldout2 | 0/36; 45/49 | 0/36; 45/49 | 3/24; 28/49 | 3/24; 28/49 |
| tune | 0/21; 24/26 | 0/21; 24/26 | 4/15; 14/26 | 4/15; 14/26 |
| heldout3 | 7/42; 43/54 | **1/38; 50/54** | 18/29; 20/54 | **13/26; 24/54** |
| national | 0/39; 56/56 | 0/35; 52/56 | 1/35; 51/56 | 1/35; 51/56 |
| national2 | 0/38; 53/54 | 0/36; 51/54 | 0/25; 40/54 | 0/25; 40/54 |
| street | 0/25; 36/36 | 0/25; 36/36 | 0/25; 36/36 | 0/25; 36/36 |
| national3 | 3/36; 48/56 | **0/37; 54/56** | 5/19; 28/56 | **4/19; 32/56** |

national3 "after" uses the two label corrections below. Against the old labels it reads JEV
1/37; 52/56 and no model 5/19; 30/56.

The one JEV wrong commit left is h3-33 (class 7 below). The no-model wrong-commit count never rose.
The JEV top-1 losses are all new questions: nat2-geo-06, nat-sym-05 and nat-sym-07 (the check's
margin), and nat-sym-04, nat-sym-08 and nat2-sym-05 (the specialty re-ask).

Other choosers, after, from the caches (wrong commits; top-1):

| set | OpenAI gpt-4o-mini | embeddings |
|---|---|---|
| main | 0/48; 105/105 | 0/48; 105/105 |
| heldout | 0/5; 11/12 | 0/5; 11/12 |
| heldout2 | 3/35; 38/49 | 5/31; 31/49 |
| tune | 1/20; 22/26 | 3/18; 17/26 |
| heldout3 | 2/36; 47/54 (round 3 live: 8/40; 42/54) | 10/32; 34/54 (16/36; 29/54) |
| national | 0/38; 55/56 | 1/37; 53/56 |
| national2 | 1/37; 51/54 | 1/35; 49/54 |
| street | 0/25; 36/36 | 0/25; 36/36 |
| national3 | 0/31; 48/56 (3/31; 43/56) | 4/32; 45/56 (5/31; 41/56) |

Neither crashes. The OpenAI wrong commits left on heldout3 are h3-04 and h3-23: the model agrees
with a wrong lexical match, and an agreeing answer gets no check. The embeddings chooser has no
check, so it never overrules a lexical match on its own word. Most of its wrong commits are that
case, the same lexical confusions the no-model column shows.

### What changed (one mechanism per class)

| class | case(s) | mechanism | where | measured (both modes unless noted) |
|---|---|---|---|---|
| 1 | h3-32 | A confident choice stands only if the check's top answer is that choice, ahead of the rival **and** "either" by `CheckGate.margin` 0.2; the twins threshold is implied and removed | `decision.CheckGate` | JEV h3-32 asks (was wrong). New asks: nat2-geo-06 (0.26 / 0.24 / either 0.50), nat-sym-05 (0.41 / 0.11 / 0.48), nat-sym-07 (0.55 / 0.03 / 0.42) |
| 2 | h3-31 | A stated doubt ("don't remember if", "not sure whether", "either ... or ..., I don't know") is never committed on. Each alternative the caller names becomes a visit by name or alias, else by one model choice over that alternative plus what they said before it, and the caller is asked between the visits found. Alternatives that all name one visit by name or alias are no doubt | `lexicon.stated_doubt`, `resolver._doubted` | JEV asks Colonoscopy or Endoscopy Consultation (was a wrong commit). No model, OpenAI, embeddings: "Is that a GI consultation?" (was a wrong commit) |
| 3 | h3-34 | A specialty default (inferred from a body word) is not added when the caller named, by name or alias, a visit no clinic offers. The tier is then unoffered and the resolver refuses not_offered before any model | `lexicon._score_types` | h3-34 refuses (was ortho consultation). No other dev turn changes |
| 4 | h3-p04 | The doctors the caller means come from the name plus catalog facts (language, title, specialty, site), read before type, place and policy filters. The existing refusals then speak for that doctor. Gender and other words are still weighed after the rules, also when they leave one of several namesakes | `resolver._described`, `_consult_provider` | h3-p04 refuses new_patient_provider with two alternatives (was Dr. Tomas Hernandez) |
| 5 | h3-p11, h3-p14 | A clinic named in the description is where the caller goes, unless they gave a place of their own; it is never silently dropped (location_type refusal with alternatives, or no_availability) | `names.ProviderClues.sites`, `resolver._search` | both offer only the named clinic; tune-p4 too (still correct) |
| 6 | nat3-geo-10, nat3-geo-05 | After a widened search ("the nearest is N miles away, in X"), offers come from the nearest clinic with openings first. Refusal alternatives go by distance; a doctor already suggested is skipped only for an equally close option. none_nearby names the nearest valid clinic, not the nearest of the nearest metro center | `resolver._nearest_first`, `_alternatives`, `_none_nearby` | nat3-geo-05 Hialeah only (was Coral Gables too); nat3-geo-10 Midtown only, 12.5 mi (was Santa Clara and Evergreen, ~40 mi); str-02/04 suggest two San Jose clinics, not Oakland |
| 7 | nat3-sym-05 | A specialty's default chosen from a shortlist is chosen again among that specialty's visits (one more request on 9 of 202 national-catalog dev turns) | `resolver._within_specialty` | Menopause Consultation (was OB/GYN New Patient Visit). New asks: nat-sym-04, nat-sym-08, nat2-sym-05 (each check put "either" or the rival on top) |
| 8 | nat3-new-03 | A name said first or after "Dr." beats a soundalike surname read off the last word ("about her" -> Abbott) | `names.match_providers` | refuses new_patient_provider for Dr. Inna Volkov (was "That doctor isn't within 50 miles", about Dr. Abad) |
| 8 | nat3-geo-15 | A state's cities are the metros with a clinic in it ("Virginia" answers "Arlington: Fort Worth or Washington?") | `geo.build_gazetteer` | JEV offers (was "nothing in Virginia"); no model now offers Diabetes Management, its existing A1C confusion |
| 8 | h3-30 | A check that does not confirm a choice among a lexical tie asks among the whole tie | `resolver._model_types` | asks all three MRIs (was two) |
| 8 | nat3-cap-05 | A type name said twice dominates the types inside it at every occurrence | `lexicon._drop_dominated` | no model refuses location_type (was a wrong commit) |
| review | h3-31, h3-p10 | Two descriptions a model maps to one visit leave the doubt standing; a site fact explains the place's own words ("clinic") | `resolver._doubted`, `names.read_provider_clues` | OpenAI and embeddings ask (were wrong commits); JEV unchanged |

Tried and not kept: checking a model answer that agrees with a lone lexical match (aimed at h3-33).
Over every dev set it sent 61 more JEV requests and changed no outcome. h3-33's check weighs Dental
Exam against Dental New Patient Exam and confirms the exam (1.00). Its entries were not kept in the
cache.

`--fill` (new in `run_resolver_eval.py`) sends only the requests the cache lacks.

### Label corrections, round 4

| case id | old | new | reason |
|---|---|---|---|
| nat3-geo-10 ("drug test", near Rockridge) | `location_ids_subset` loc_000, loc_008, loc_011; `max_miles` 10 | adds loc_005; `max_miles` 13 | `types_any` accepts Drug Screening (appt_233), and no site within 10 mi offers it. The nearest that does is Midtown (loc_005), 12.5 mi away. |
| nat3-geo-16 ("drug test", Fargo, North Dakota) | `location_ids_subset` loc_141, loc_143 | adds loc_147 | The scenario accepts Drug Screening, which no Minneapolis site offers. The nearest site that does is Como, St. Paul (loc_147, 219 mi from Fargo). |

Both come from the generator's rule (`eval/national/build_cases.py`): the site set is taken from
every acceptable type together. Regenerating national3 would undo them.
`cases_national3.jsonl` sha256 is now `af774a7e789eb47257d9b489110726e0edf30e9462964ff2aa04269ab2e42d0e`.

### Round 3 misses left as they are

JEV:
- h3-33 "due for my regular six-month dental checkup" (**wrong commit** by its label, which its
  author marked uncertain). The alias table maps "dental checkup" to Dental Exam, and the model
  agrees (0.98). No general mechanism found (see "Tried and not kept"). The label is not
  demonstrably wrong either, so it stays.
- h3-28 "sonogram of my thyroid" asks Thyroid Follow-up or Ultrasound. Every word is explained by
  the two aliases, so only the caller can choose (the round 3 rule that keeps "MRI" or "checkup"
  away from the model). A rule that reads "X of my Y" as X would break "follow-up of my thyroid".
- h3-p08, h3-p09: the pre-registered gender policy asks (Carlos 0.16, Patels 0.84 / 0.88 are not
  sure enough). The labels expect gender to narrow. The behavior follows the policy.
- nat3-sym-08: the check contradicts the choice (arrhythmia 0.12, cardiology 0.58), so the caller
  is asked.
- nat3-sym-01 (gout): the model's answer is open (rheumatology 0.52), so the lexicon's podiatry
  default ("toe") stands. No podiatry within 50 mi of Indianapolis, so the caller is told the
  nearest is in Chicago. That is a true refusal, not a commit.

No model (13 heldout3 and 4 national3 wrong commits left): lexical type confusions whose deciding
words need a model ("hormones ... blood test" -> Blood Draw, h3-x04; "A1C blood sugar test" ->
Diabetes Management, through the aliases "a1c" and "blood sugar"; "spine X-rayed" -> Spine
Consultation). Asking whenever a lexical match leaves words unexplained would remove them, but it
would also turn the no-model mode's correct bookings with extra words into questions. Not done.
nat3-dup-06 without a model refuses with a false reason: "Dr. Joseph White isn't at any of our
clinics within 50 miles of Raleigh". He is in Raleigh; he does not offer the Contraception
Consultation the lexicon chose. Reported, not fixed: the outcome stays a refusal either way.

### Thresholds

`eval/threshold_sensitivity.py`, every dev set (now including heldout3 and national3), from the
committed cache: shipped top-1 421/440 resolve turns, 1 wrong commit (h3-33). margin 0.15: no
flips. margin 0.25: nat3-geo-02 and nat3-noloc-01 (leads 0.24) ask, top-1 -2. Wrong commits do not
change in any variant. settle, settle_either and gender behave as in round 3, plus h3-p08 and h3-p09
at gender 0.85. Dev check leads (choice minus the larger of rival and "either") have a gap between
0.13 and 0.24. 0.2 sits in it, and h2-08's 0.04 (0.52 against either 0.48) falls below it. h2-08
still books: the check asks Psychiatric Evaluation or Therapy Session, and only the evaluation is
open to a new patient.

### Requests, latency, spend

JEV requests over the dev sets: 324 -> 330. The doubt path sends one choice per alternative and no
check. The specialty re-ask adds one request on 9 national-catalog turns. Resolver alone, no network,
p95 before -> after (`--chooser none`, same machine, one set at a time): main 5.27 -> 5.39 ms,
heldout 1.30 -> 1.14, heldout2 1.44 -> 1.19, tune 1.76 -> 1.56, heldout3 1.42 -> 1.61, national 7.31 -> 7.14, national2
7.15 -> 6.89, street 13.91 -> 14.66, national3 6.83 -> 6.64.

Spend: JEV 22 new cached requests, 16,051 input tokens, $0.00064, plus 61 requests ($0.00104) for the
experiment that was not kept. OpenAI 35 new cached requests, 9,136 input tokens, $0.0014. Both caches
are committed.

## Round 4 review fixes

The review's probe phrases are regression tests in `backend/tests/scheduling/test_sched_probes.py`,
each run with no model and with stub models that answer every question confidently.

| finding | mechanism | where |
|---|---|---|
| 1 states | A state whose only city with a clinic is another state's ("Virginia": Washington, DC) is not taken for that city. That city answers "which city?" only. A state with no city of its own never rings: none_nearby names its nearest own clinic and the distance ("Our nearest clinic in Virginia for a flu shot is Alexandria in Washington, about 135 miles away"). Two such cities still ask (Maryland). | `geo.over_state_line`, `Gazetteer.state_sites`, `resolver._ring`, `_none_nearby` |
| 2 PT as context | An unoffered visit said as the object of a time word ("after PT", "before I start PT", but not "after surgery, PT") or as the subject of a verb ("my PT says", "physio did not help") is no candidate. Asked for, it is refused with the body part's visit suggested ("I can book an orthopedic consultation instead"). | `lexicon._drop_unoffered_context`, `pointed_default`, `resolver._refuse_not_offered` |
| 3 doubt grammar | A doubt marker (a knowing word up to three words after a negation, or "unsure", "dunno", "maybe", "either"...), then alternatives joined by "or" in its clause or the next, else before it. "Or not" is no alternative. An alternative about the cost, coverage, how long, the clinic or the doctor (by name) is not about the visit. | `lexicon.stated_doubt` |
| 4 leading questions | A stated doubt marks the type model consulted: no model narrows the caller's alternatives. Fewer than two visits found: the found ones plus the whole phrase's lexical tier, plus the nearest neighbour of a visit the caller named, else an open question. Never "Is that X?". | `resolver._doubted` |
| 5 latency | The doubt path's picks are independent and sent together (`prefetch_pick`). A failed specialty re-ask asks among the specialty's visits (openly past three), not to confirm the default. | `resolver._modeled_alternatives`, `_within_specialty`, `jev.JevTypeDisambiguator.prefetch_pick` |
| 6 | A doctor who does not do the visit anywhere does not narrow an area search: provider_type with alternatives near the place, not "isn't within 50 miles". | `resolver._ring` |
| 7 | provider_location is judged on the doctors meant who do the visit ("Dr. Chen at Mission Bay" + Downtown). Facts that single out one of several namesakes, said with a gender no answer confirms ("Dr. Singh, he speaks Vietnamese"), confirm that doctor by name before any rule speaks for them. | `resolver._refuse_location`, `_described` |

Every dev set gives the same wrong commits and top-1 as the round 4 table, in both modes, and with
OpenAI and embeddings from their caches. Two no-model misses change wording only: h3-31 asks openly
(was "Is that a GI consultation?") and nat3-dup-06 refuses provider_type with two alternatives in
Raleigh (was "Dr. Joseph White isn't at any of our clinics within 50 miles of Raleigh").

JEV requests over the dev sets: 342 over 440 resolve turns, counting repeats, as before. Turns with
three or more sequential requests: 9 before, 9 after. All nine are the specialty re-ask (pick,
re-ask, check), where each request needs the previous answer. h3-31's two doubt picks now go
together (two rounds to one).

Spend: JEV 29 new cached requests for the review probes, 55,048 input tokens, $0.0022. No OpenAI
requests.

## Held-out round 4: scored once (2026-10-03)

`heldout4` and `national4` were frozen in `99f7150`, before any round 4 fix landed. They were
scored once on the code at `400f3d7`, with every chooser. JEV and OpenAI ran live. Raw outputs are
in `eval/results/round4_*.txt`.

| set | mode | wrong commits | top-1 | questions per booking |
|---|---|---|---|---|
| heldout4 (SF, 62 turns) | no model | 7/25 (28.0%) | 33/62 | 0.55 |
| | embeddings | 7/32 (21.9%) | 40/62 | 0.40 |
| | OpenAI gpt-4o-mini | 9/40 (22.5%) | 46/62 | 0.23 |
| | **JEV** | **3/38 (7.9%)** | **53/62** | 0.20 |
| national4 (56 turns) | no model | 0/26 (0.0%) | 41/56 | 0.54 |
| | embeddings | 1/30 (3.3%) | 46/56 | 0.36 |
| | OpenAI gpt-4o-mini | 0/33 (0.0%) | 50/56 | 0.36 |
| | **JEV** | **1/31 (3.2%)** | **47/56** | 0.36 |

JEV-mode wrong commits went from round 3's 7/42 to 3/38 on the SF set, and from 3/35 to 1/31
nationally. That is the generalization trend; the dev-set numbers do not show it. The remaining
JEV wrong commits:

- **Umbrella words** that name several visits, which the model resolved with false confidence:
  - h4-m06 "my stomach doctor said I need a scope" (upper or lower) chose 0.94, and the check
    confirmed it at 0.82.
  - h4-m05 "my baby's checkup" (well-child or newborn visit).
  - h4-27 "some blood work" (blood draw or fasting test).

  The author flagged h4-27 and h4-m05 as uncertain labels before scoring.
- **Triage nuance**: nat4-sym-03, "it burns when I pee ... since yesterday", went to a urology
  consultation; the label expects a sick visit or UTI visit.

Misses that are not wrong commits but matter:
- **False refusals.** h4-09 is a seasonal allergy described with "my eyes get itchy and watery";
  the agent answered "we don't offer eye care". nat4-geo-07 and nat4-noloc-02 refused a chest CT
  within 50 miles because the specific "CT - chest" visit is not offered there, while a general
  CT scan that the label accepts is.
- Gender policy (pre-registered): h4-p10 and h4-p11 confirm a doctor by name instead of booking.

## Held-out round 5 (`heldout5`, `national5`)

Frozen on 2026-10-03 and not yet scored. Both sets were authored blind. The author read no resolver code
(`backend/scheduling/**`), no resolver results (`eval/results/**`), no `.kstack` files and no README
section that reports scores, misses or failure classes. One exception, after authoring: when staging
the commit, `git diff` context printed the last three lines of the round 4 scored section (a national
CT-chest miss and a gender-policy note). Every case, label and note was final by then, and nothing
changed after it. Neither the resolver nor `run_resolver_eval.py` was run on these cases. Both are
registered as blind in `eval/sets.py`, so no test, tuning run or `--set all` reads them. Labels come
from catalog queries. DeepSeek (`deepseek/deepseek-v4.1-flash` via `cmdc`) wrote only the caller's
words. Regenerated phrasings and labels I was unsure of are listed in `eval/heldout5/label_notes.md`.

| set | file | catalog | cases | turns | sha256 of the committed (LF) file |
|---|---|---|---|---|---|
| heldout5 | `cases_heldout5.jsonl` | SF | 56 | 64 | `d9cde3bd4548c40f93f7b1d88358a121ccd2cc1e34f55cc0bca43ffade514cd4` |
| national5 | `cases_national5.jsonl` | national | 51 | 59 | `76b0b8ebb6aff1fce873d4d9734c536bbf56b8cb8d701185899996926616a233` |

Categories:

- **heldout5**: type 29 (lay test name 3, symptom 2, symptom whose body part points to another
  specialty 4, acute symptom with its onset 4, everyday description 4, abbreviation 3, slang 3, genuine
  two-way ask 4, not offered 2). Policy 5 (new-patient type 2, referral 1, new-patient provider 2).
  Provider description 14 (specialty 3, title 2, language 3, full name 2, gender 2, site 2); in 4 of
  these the description fits 2 doctors, so the case expects a provider question. Two-turn 8: the agent
  asks and the caller picks a provider (4) or a service (4).
- **national5**: geo 18 (city 2, "I'm in" 2, suburb 1, ZIP 2, ZIP3 1, neighborhood 2, "near"
  neighborhood 2, state only 1, either/or town 2, far place 1, misspelled city 2), symptom 8,
  duplicate names across metros 6 (3 with a city, 3 answered after a city question), capability by
  metro 5, new-patient rules 5, no location 3, unoffered 2, ring expansion 1, general variant 3 (only
  the general CT Scan or Ultrasound is bookable within 100 mi, not the specific test asked for). The
  seed is 20261006. No scenario shares a type+metro or a provider+type pair with national, national2,
  national3 or national4 (the `scenarios` step prints 0/51 for each). Every case pins the catalog's
  sha256.

Generator commands:

```
backend/.venv/Scripts/python eval/heldout3/build_cases.py --set heldout5 scenarios|phrase|merge
backend/.venv/Scripts/python eval/heldout3/build_cases.py --set heldout5 repair h5-10,h5-20,h5-m02
backend/.venv/Scripts/python eval/national/build_cases.py --set national5 scenarios|phrase|merge
backend/.venv/Scripts/python eval/national/build_cases.py --set national5 repair nat5-geo-17,nat5-geo-18 twins
backend/.venv/Scripts/python eval/validate_case_format.py heldout5 national5   # format only, 0 problems
```

DeepSeek calls: 12, none of them failed. heldout5 took 5 batches plus 1 repair call covering 3
scenarios; national5 took 5 batches plus 1 call for the 2 either/or scenarios. The leak checks dropped
no cases. The raw outputs are in `eval/heldout5/deepseek_raw/` and `eval/national5/deepseek_raw/`.
