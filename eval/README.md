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
3. **Types, a tie between confusables, and the caller said more than the tied names/aliases explain**
   ("checkups while I'm expecting" leaves "expecting"). A bare "checkup", "MRI" or "follow-up"
   does not call JEV, and the caller is asked (see "What went wrong first" below).
4. **Providers**: two or more policy-valid providers match the name, and the phrase has a clue
   beyond the name, honorific and filler (specialty, site, title, gender, history words). A bare
   "Can I see Dr. Chen?" never calls JEV; the splitting question is a catalog fact.

Unoffered types are never JEV candidates ("we don't offer that" stays lexical). JEV only picks;
policy runs after it, so a policy-invalid pick takes the usual refusal path. A failed, timed-out or
low-confidence answer gives exactly the no-JEV behavior (unit-tested).

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

The live call path defaults to `JevClient(timeout_s=1.2, retries=0, turn_budget_s=1.2)`, with
`begin_turn()` called before each `resolve()`. The total wait is bounded with a worker thread,
because httpx timeouts are per phase. The eval uses 2.5 s and one connect retry so it fills the
cache. Live requests over 1.2 s: 1 of 49 in the second live run (1,709 ms, the first request of the
heldout set). In production that turn would have fallen back to no-JEV.

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