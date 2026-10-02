# Resolver eval (offline, free)

Every case in `cases.jsonl` is a sequence of **`update_request` tool-call arguments**: what the LLM
would extract from the caller's speech (for example `{"service_phrase": "MRI of my knee",
"provider_phrase": "Dr. Nwin"}`). It is not raw audio or a transcript. Speech-to-text noise is
simulated by misspelled phrases ("Dr. Nwin", "Dr. Shen", "Dr. Garsha"). The LLM's extraction
accuracy is not measured here. The paid dialog simulation covers that.

Expected values were derived by hand from `backend/data/catalog.json`, not from resolver output.
The `heldout` cases were written after the rest of the system was built and were scored once, with
no tuning afterwards. Every other category was written by the same author as `aliases.json`, so
treat its numbers as an in-distribution upper bound.

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
