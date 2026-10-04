# Prosper challenge: Agent Studio and catalog context management

A voice agent is a graph of nodes (Pipecat Flows) stored as JSON. Phase 1 is Agent Studio, a browser UI that edits that graph and places a live test call. Phase 2 is a scheduling agent that books across a large clinic catalog. The LLM never reads the catalog. It turns speech into one tool call. A deterministic resolver filters a precomputed table of bookable rows by the booking rules and returns one move: offer up to 3 times, ask the one question that changes the valid set, or refuse with a reason. The booking itself happens in code.

The same agent runs on two catalogs:

| Catalog | Size | Naive prompt (catalog in context) |
|---|---|---|
| SF sample (provided, `backend/data/catalog.json`) | 8 locations, 50 providers, 82 types, 760 bookable rows | 8,252 tok |
| National v2 (synthetic, seed 20261002, `backend/data/national/`) | 40 metros, 299 sites, 5,000 providers, 314 types, 138,870 bookable rows | 664,495 tok, 5.2x a 128k window. It does not fit. |

All counts measured (tiktoken `o200k_base` on compact JSON).

## Quick start

Needs Python 3.11 (3.12 also works; tested on 3.11.8) and Node 22. pnpm is optional: Node's bundled corepack provides it.

```bash
make setup    # once: backend virtualenv, studio dependencies, backend/.env; offers to take the API keys
make start    # backend and studio together, opens the browser; Ctrl+C stops both
```

Without `make` (Windows): `python scripts/studio.py setup`, then `python scripts/studio.py start` (or `py` instead of `python`). `make start` also runs the setup if it was skipped.

The studio opens on a scheduling agent. All agents ship in `backend/agents`, so there is nothing to create:

| Agent | Catalog |
|---|---|
| **Clinic Scheduler** | The provided SF sample, `backend/data/catalog.json` |
| **National Scheduler** | The generated national catalog, `backend/data/national/` |
| **Prosper Scheduler** | None: the original Phase 1 example |

**Keys.** Paste them when `make setup` asks (hidden input, saved to the gitignored `backend/.env`), or later in the studio: **Keys** in the top bar. **Test** next to each key makes the cheapest real check. Then open the Test call panel, choose a **Disambiguator** mode and click **Call**.

<details>
<summary>The same steps by hand</summary>

```bash
cd backend
python3.11 -m venv .venv                     # Windows: py -3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt   # Windows: .venv\Scripts\python
cd ..
backend/.venv/bin/python backend/bot.py      # the backend on :7860
cd frontend && pnpm install && pnpm dev      # the studio on http://localhost:5173
```

</details>

The server preloads both catalog indexes at start (SF about 10 ms, national about 1 s, measured). Starting it is free. Money is spent only once a browser connects a call. Vite proxies `/api`, `/start`, `/sessions` and `/status` to the backend on :7860.

On Windows, set `PYTHONIOENCODING=utf-8` before redirecting script output to a file. Pipecat's startup banner and some eval output are not cp1252-safe.

## API keys

No key is in the repo. Two ways in: `make setup` asks for them and writes the gitignored `backend/.env`, or the studio's **Keys** sheet keeps them in the browser and edits nothing on disk.

| Key | Needed for | Get one |
|---|---|---|
| OpenAI (`OPENAI_API_KEY`) | The conversation (the agent's model) and the OpenAI disambiguator. Required for a call. | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| ElevenLabs (`ELEVENLABS_API_KEY`) | Speech to text and the agent's voice. Required for a call. | [elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys) |
| Command Code JEV (`CMD_API_KEY`) | Optional. The JEV disambiguator and the post-call review. Without it, JEV mode runs as Off: the resolver asks the caller instead. | [commandcode.ai](https://commandcode.ai) |

**Test** calls OpenAI's model list, the ElevenLabs account (both free) or one tiny JEV choice. The sheet shows where each key comes from: **Server key**, **This browser** or **Missing**. The dot on the **Keys** button is green with all three keys, amber without JEV, and red when a call cannot start. Call then names the missing keys and opens the sheet. With JEV or OpenAI picked as the disambiguator, that key's row also sits under the switch.

How a pasted key travels. The browser keeps it in `localStorage` (`agent-studio:openai-key`, `agent-studio:elevenlabs-key`, `agent-studio:jev-key`). It sends the key only to the local backend: in the `/start` body as `api_keys` (the JEV key only for a call that consults JEV), as an `X-CMD-API-Key` header for the call review, and as an `X-Api-Key` header for a key test. The backend uses it for that call or request only. It never writes it to disk and never sends it back: `GET /api/keys/status` says only whether the server has each key. Log lines blank every key field, key header and bearer token, because the Pipecat runner logs every `/start` body.

Any script running on the studio's origin can read `localStorage`. That is fine for a local demo. Don't paste a key into a studio served to other people.

The alternative is `backend/.env`: copy `backend/.env.example`, which lists every variable the backend reads, and fill it. A key pasted in the studio wins over `.env` for that one call or request.

## Architecture

```
 Browser: Agent Studio (React + Vite, :5173)
   |  REST  /api/agents, /api/catalogs, /api/grade, /api/keys
   |  WebRTC audio + RTVI events (node_entered, edge_taken, resolver_mode, resolver_decision, model_call)
   v
 Pipecat runner (FastAPI, :7860, backend/bot.py)
   mic -> ElevenLabs STT -> OpenAI LLM (Flows node + tools) -> ElevenLabs TTS -> speaker
                                 |  update_request / lookup / confirm_booking edge
                                 v
   Resolver (backend/scheduling, in-process)
     CatalogIndex      bookable (type, provider, location) rows, built at server start
     policy.check      6 catalog policies, re-run at booking time
     geo + names       clinic, street, house number, city, state, ZIP, neighborhood, "near X"
     lexicon           aliases, lay terms, stated doubt, fuzzy + phonetic surname match
     chooser           JEV | OpenAI gpt-4o-mini | local embeddings | off (one table: choosers.CHOOSERS)
     -> Plan: offer | ask | refuse | confirm
                                 |
   templates -> spoken text -> TTS directly (speak-direct, the LLM stays silent)
```

## Phase 1: Agent Studio

| Feature | What it does |
|---|---|
| Graph editor | Nodes are cards, edges are labelled with their function name. Add, connect, delete, drag. Auto-layout (dagre). Parallel and backward edges are routed apart so every label stays readable. |
| Inspector | Node: task messages, role override, end flag, tools, context strategy. Edge: function, description, target, collected fields. Agent: persona, voice, model, catalog (from `GET /api/catalogs`, with its counts and naive token size), resolver settings. Edge `precondition` and `action` are edited in the raw JSON view. |
| Live validation | The editor sends the draft to `POST /api/agents/validate` as you type. Errors point at the exact node or edge and block the test call. The backend rules are the only rule set. |
| Undo / redo | Ctrl/Cmd+Z, Ctrl/Cmd+Shift+Z or Ctrl+Y. Ctrl/Cmd+S saves. |
| JSON round-trip | A raw JSON view per node and edge. Keys the UI does not know survive load and save. |
| Test call | WebRTC call from the browser with the current draft, unsaved edits included. The canvas highlights the live node and the visited path. The panel shows the transcript, latency, cost, collected flow state and the **Disambiguator** switch. |
| Keys | The API keys sheet described above. |
| Decisions tab | One row per resolver decision: status, what was said, the offers or the question asked, the refusal reason, the model's probability and latency, tool-result tokens, and any caller words the handler kept over the conversation model's. |
| Call review | After hang-up, `POST /api/grade` sends the transcript and decisions to JEV once. It returns five scores: booked correctly, unnecessary questions, unsupported claims, caller effort (1-5) and outcome. Cost about $0.00004 per call (measured). |
| Dev view | Press D. Live pipeline strip (mic, STT, LLM, tools, TTS, speaker) with the latest latency per stage, a per-turn voice-to-voice waterfall, a streaming transcript, tool and model bubbles on the live node, running cost by provider (prices editable), and prompt tokens per LLM call against the catalog's naive size. |
| Themes | Light, dark, or follow the system. |

`python frontend/scripts/screenshots.py` captures both themes from the running dev server into `frontend/screenshots/` (Playwright, Brave when installed).

The agent format, validation rules, REST API and live call events are in [docs/AGENT_FORMAT.md](docs/AGENT_FORMAT.md). In short: an agent has `name`, `initial_node`, `persona`, `voice_id`, `model` and `nodes`. A node has `task_messages`, optional `role_message`, `edges` and `end`. An edge has `function`, `description`, `target` and JSON-schema `properties`. Phase 2 adds agent `catalog` and `resolver`, node `tools` and `context_strategy`, and edge `precondition` and `action`. All are optional, so Phase 1 agents run unchanged.

## Phase 2: context management

Full design: [docs/PHASE2_DESIGN.md](docs/PHASE2_DESIGN.md). Eval method and every result: [eval/README.md](eval/README.md).

### The approach

1. **The LLM never sees the catalog.** Its job is extraction. It passes the caller's own words to `update_request` (`service_phrase`, `provider_phrase`, `location_phrase`, `specialty_hint`, `is_new`, `has_referral`, `time_pref`, `pick_offer`, `clear`). Questions about hours, addresses and "do you offer X" go to `lookup`, which returns at most 5 facts.
2. **A precomputed policy table.** At server start `CatalogIndex` joins provider locations, provider types and location capabilities into bookable rows. `policy.check(row, patient)` is the only home of the booking rules. It runs in the resolver and again at booking time.
3. **Ask only what changes the set.** Ambiguity is often a catalog fact, not a probability. "Dr. Chen, the heart doctor" matches two SF cardiologists. For a new patient, policy removes David Chen (not accepting new patients), and Emily Chen is offered with no question. For an established patient both are valid, so the agent asks.
4. **Geography without a geocoder.** The national catalog carries metros and site coordinates. `scheduling/geo.py` reads a clinic name, street, house number, full address, city, state, ZIP, ZIP3, neighborhood or "near X" against the catalog's own places. It searches the place's own radius, then twice that, then 50 miles. Past 50 miles it refuses with `none_nearby` and names the nearest valid alternative for explicit caller consent. It never silently offers a distant booking. With no place at all it asks "Which city are you in?". The SF catalog has no coordinates and loads as one implicit metro.
5. **Speak-direct templates.** Offers, questions and refusals are rendered from templates and queued straight to TTS. The handler returns `NO_RESPONSE`, which skips the LLM's second round trip. Names and times come from the catalog and the availability source, so the LLM cannot paraphrase them into something false.
6. **Booking in code.** The `confirm_booking` edge has the `offer_confirmed` precondition and the `book_confirmed` action. It is refused until the caller picked a time, heard it read back and said yes. The action books exactly that offer, re-checks policy, holds the slot, and speaks the confirmation reference from a template.
7. **Small prompts.** Five nodes: `greeting -> schedule -> booked -> done`, plus `handoff`. `schedule` uses `context_strategy: "reset"` and a `{{ summary }}` placeholder, so the prompt stays small as the call grows.

### The resolver pipeline

One `resolve()` call per tool call, in this order. [docs/PHASE2_DESIGN.md](docs/PHASE2_DESIGN.md) has the detail.

1. **Visit.** Aliases and lay terms match the caller's words to visit types. A stated doubt ("I don't remember if it goes down my throat or up from below") is never committed on: the caller is asked between the visits named. A visit no clinic offers is refused here, before any model.
2. **Model, only when the words carry more.** The model is asked only when nothing matched, only a specialty default matched, or the caller said words the matched names do not explain. It picks among named options. A second question, the check, weighs that pick against its rival with an "either" answer. A pick the check does not confirm becomes a question.
3. **Doctor.** The doctor the caller means comes from the name plus catalog facts (language, title, specialty, clinic), before type, place and policy filters. A refusal then speaks for that doctor. It never swaps in another one.
4. **Place.** A place heard by sound or by part of its name is confirmed first ("Did you mean Renton, Washington?"). The area search runs nearest first.
5. **Policy.** `policy.check` on every remaining row. One question if it changes the valid set. A refusal names the reason and the nearest valid alternative.
6. **Offer.** Up to 3 times from the availability source, spoken from a template.

### Four disambiguator modes

The **Disambiguator** switch in the Test call panel (also in Agent settings) writes `resolver.chooser` into the draft: JEV, OpenAI, Embeddings or Off. The next call uses it, and the switch locks during a call. JEV, OpenAI and Embeddings answer the same three hooks (visit type, doctor, clinic) through the same gate. A mode whose key or package is missing runs as Off, and the panel shows the mode the call really runs.

| Mode | What answers | When to use it (opinion) | $ per 1,000 turns | Latency per request, p50 / p95 | Round 4 blind wrong commits, SF / national |
|---|---|---|---|---|---|
| **JEV** (default) | Command Code JEV: calibrated probabilities over named options. A check after every visit choice. A yes/no question for gender. | The default. Best measured on doctor descriptions ("the lady doctor", "he speaks Spanish"). | $0.017 to $0.042 | 525-533 / 684-720 ms | 3/38 / 1/31 |
| **OpenAI** | gpt-4o-mini answers one option key. Its top-20 token logprobs are the distribution. Same choice and check, no gender question. | No JEV key. Close to JEV on national place and symptom turns. Weaker on doctors. | $0.027 to $0.071 | 504-508 / 652-771 ms | 9/40 / 0/33 |
| **Embeddings** | Local `BAAI/bge-small-en-v1.5`, cosine similarity, softmax T = 0.0125. No network, no check. | Offline and free. A fair fallback for visit types, a poor one for people. | $0 | 6-8 / 11-22 ms | 7/32 / 1/30 |
| **Off** | No model. Anything ambiguous becomes a question. | To show the rules path, or with no model keys. Expect more questions. | $0 | none | 7/25 / 0/26 |

Cost and latency come from the chooser comparison in eval/README.md (national2 and heldout2, measured; $ priced as if every request were live). A JEV visit decision is two sequential requests, a choice and then its check. Measured live on the dev sets in round 3, JEV turns took p50 974 ms and p95 1,390 ms. A live call gives a model 2.5 s per turn and 1.5 s per request, with no retry. A spoken "One moment." covers waits over 0.3 s. Embeddings load in a background preload at server start: 1.0 s for the model and 3.4 s for the national types and sites (measured). Model cost is at most $0.07 per 1,000 turns, small next to the gpt-4o conversation (inferred), so the choice is about wrong commits and latency, not money.

### Safety principles

1. **Asking beats committing.** A wrong booking is the costly error. One more question is cheap. So wrong commits are counted per commit. Since round 3 the priority has been zero wrong commits first, top-1 second.
2. **Never commit on a missing answer.** A model answer that fails or times out is never acted on. The caller is asked (unit-tested). A stated doubt and a place guessed by sound are treated the same way.
3. **`policy.check` is the only home of the rules.** Models only pick among options. Policy runs after every pick and again at booking time, so a model mistake cannot book a policy violation.

The review-driven fixes in rounds 3 and 4 apply these principles. Details and measurements are in eval/README.md.

| Fix | What it does |
|---|---|
| Check gate | A confident visit choice stands only if the check's top answer is that choice, ahead of both the rival and "either" by 0.2. Otherwise the caller is asked. |
| Doubt grammar | "I don't remember if", "not sure whether", "either ... or ..., I don't know": the visits the caller names become the question. No model narrows them. |
| Not-offered refusals | A visit no clinic offers is refused before any model runs. "After PT" or "my PT says" is context, not a request. |
| Provider before policy | The doctor the caller described is found first. If policy excludes that doctor, the agent says why and suggests alternatives. It never books a namesake. |
| Nearest first | After a widened search, offers and refusal alternatives go by distance. |
| Geography confirmations | "Trenton" sounds like Renton: the agent asks "Did you mean Renton, Washington?" before it searches. A state's cities are the metros with a clinic in that state. |
| Gender policy | Inferred gender (from first names; the catalog has no gender field) counts only at p >= 0.9 or <= 0.1. It may narrow the doctors but never books one alone: "Do you mean Dr. Emily Chen?". Pre-registered before round 3 was scored. |

## Evidence

All numbers are measured in this repo unless marked (est.). Each eval case is a sequence of `update_request` arguments. Speech-to-text noise is simulated by misspellings. The LLM's own extraction is not measured offline. The live calls cover it.

- **Wrong commit**: the resolver offered or confirmed something it should not have. Counted per commit, because asks and refusals cannot commit wrongly.
- **Top-1**: the turn matched the expected result exactly.
- **Dev set**: we read its failures and fixed against them. Its numbers show the fixes work on the cases they were written for.
- **Blind set**: authored without seeing resolver code or results, frozen in a commit before the fixes it judges, and scored once. Only blind numbers show generalization. After scoring, a blind set becomes a dev set and a fresh blind round judges the next fixes.

### Dev vs blind, per round (JEV mode)

Wrong commits per commit, then top-1 turns. heldout and heldout2 were held out before tuning. national2 and every round 3 and round 4 set were also authored blind.

| Round | Dev sets after the round's fixes (seen) | SF set, scored once | National set, scored once |
|---|---|---|---|
| Before round 3 | national 0/39, 56/56; SF main 0/48, 105/105 | heldout + heldout2: 2/39 (5.1%), 49/61 | national2: 1/37 (2.7%), 50/54 |
| Round 3 | 0 wrong commits over every dev set; 323/330 turns | heldout3: **7/42 (16.7%)**, 42/54 | national3: **3/35 (8.6%)**, 46/56 |
| Round 4 | 1 wrong commit over every dev set (h3-33); 421/440 turns | heldout4: **3/38 (7.9%)**, 53/62 | national4: **1/31 (3.2%)**, 47/56 |
| **Round 5 (blind)** | 0 wrong; 535/566 turns | **4/38 (10.5%), 52/64** | **1/35 (2.9%), 51/59** |

The dev sets read 0 or 1 wrong commits. The blind sets still contain errors. That gap is the honest measure, and it is why blind rounds exist. Between rounds 3 and 4 the blind JEV wrong-commit rate fell on both catalogs.

### Held-out and blind sets, every mode

| Set (turns) | Off | Embeddings | OpenAI gpt-4o-mini | JEV |
|---|---|---|---|---|
| heldout2, SF (49) | 3/17, 18/49 | 6/32, 29/49 | 8/38, 33/49 | 2/33, 37/49 |
| national2 (54) | 1/24, 37/54 | 2/33, 45/54 | 1/35, 48/54 | 1/37, 50/54 |
| heldout3, SF (54) | 18/29, 20/54 | 16/36, 29/54 | 8/40, 42/54 | 7/42, 42/54 |
| national3 (56) | 5/19, 28/56 | 5/31, 41/56 | 3/31, 43/56 | 3/35, 46/56 |
| heldout4, SF (62) | 7/25, 33/62 | 7/32, 40/62 | 9/40, 46/62 | 3/38, 53/62 |
| national4 (56) | 0/26, 41/56 | 1/30, 46/56 | 0/33, 50/56 | 1/31, 47/56 |
| heldout5 | 11/26, 30/64 | 15/30, 31/64 | 8/40, 48/64 | 4/38, 52/64 |
| national5 | 4/37, 50/59 | 5/37, 49/59 | 3/38, 52/59 | 1/35, 51/59 |

How to read it:
- On heldout2, most model calls split same-named doctors by clue words. OpenAI and embeddings made 8 and 6 wrong commits against JEV's 2. gpt-4o-mini put p = 1.00 on wrong doctors, so a gate tuned to JEV's calibration acts on them (inferred: its logprobs are overconfident). On heldout4 the gap held (9 and 7 against 3). On heldout3 it was small for OpenAI (8 against 7).
- On national4, OpenAI and Off made 0 wrong commits, and OpenAI had the best top-1 (50/56 against JEV's 47/56). JEV is not best everywhere.
- Off is the rules path. On SF it commits wrongly on lexical confusions whose deciding words need a model. Nationally it ranged from 0/26 (national4) to 5/19 (national3).

### Round 3: what failed, and the general fix

Each fix is a general mechanism, unit-tested with wording that is not in any set. Then the fresh round 4 sets judged them.

| Round 3 failure class (case) | Round 4 fix |
|---|---|
| The check committed while its own top answer was "either" (h3-32) | Check gate margin: the choice must lead both the rival and "either" by 0.2 |
| The caller said they did not know, and the resolver still chose (h3-31) | Stated doubt is never committed on |
| A service no clinic offers was replaced by a related one (h3-34) | Refuse not-offered before adding a specialty default |
| A named doctor excluded by policy was replaced by another (h3-p04) | Find the described doctor before the filters; the refusal speaks for them |
| A clinic named to pick the doctor did not also limit where (h3-p11, h3-p14) | The described clinic is where the caller goes |
| Alternatives skipped closer clinics (nat3-geo-10) | Nearest first |
| A type error the check confirmed (nat3-sym-05) | A specialty default is chosen again among that specialty's visits |

### Integrity: two incidents in round 3, disclosed

1. A unit test iterated over every `eval/cases*.jsonl`, so two pytest runs resolved the blind sets. The test only asserted that no spoken text contains "None", and it printed nothing. Tests now read dev sets only, and blind sets are registered in `eval/sets.py` so that no test, tuning run or `--set all` reads them.
2. A cleanup worker's code search previewed h3-01 to h3-05 (phrases and expected types). That worker changed no decision logic: dev decisions were identical before and after its commits in all four modes. Without h3-01 to h3-05, heldout3 JEV reads 7/37 (18.9%), 38/49.

A blind score is only worth something if the reader can check it was blind. So both incidents are reported with the score they could have touched.

### Context, latency, cost

| | Naive (catalog in prompt) | Ours |
|---|---|---|
| SF, per LLM call | 8,405 tok | 779 at turn 1, 2,324 at turn 15 (10.8x and 3.6x fewer) |
| SF, 15 LLM calls, gpt-4o input (est.) | ~$0.315 | ~$0.058 |
| National, per LLM call | 664,495 tok. Does not fit a 128k window. | 625 tok fixed part, plus summary and tool history |
| Live call 3 (national, 13 LLM calls) | | 20,805 prompt tok total, ~1,600 per call |

| Stage | Time |
|---|---|
| Resolver alone, no network, round 4 blind runs (all modes), p95 | SF 1.0-4.5 ms, national 5.1-6.4 ms |
| JEV request, live, round 4 blind runs, p50 / p95 | 549-554 / 776-995 ms |
| JEV turn with a choice and a check, live, dev sets in round 3, p50 / p95 | 974 / 1,390 ms |
| Live calls: LLM TTFB 0.8-1.1 s, TTS TTFB ~0.15 s, STT final ~0.35-0.45 s | |
| Live calls: voice-to-voice on in-node turns | 1.1-1.3 s |
| Live calls: a node transition adds one LLM hop | ~2-3 s total |

Live call 3, about 2 minutes, at list prices (est.): gpt-4o ~$0.055 (20,805 in / 254 out), ElevenLabs TTS ~$0.30 (996 chars), STT ~$0.013 (117 s), JEV ~$0.0001. Total ~$0.37. After context management, TTS is about 80% of a call. The LLM is no longer the cost to cut.

### Reproduce

From the repo root with the backend venv's Python (`backend/.venv/Scripts/python` on Windows, `backend/.venv/bin/python` elsewhere). All free and offline: model answers replay from the committed caches (`eval/.jev_cache.json`, `eval/.openai_cache.json`), keyed by request hash.

```bash
python eval/run_resolver_eval.py --set all --chooser jev           # SF dev sets, JEV replayed
python eval/run_resolver_eval.py --set national3 --chooser none    # any set, any mode: jev | openai | embed | none
python eval/compare_choosers.py                                    # the chooser comparison table
python eval/naive_baseline_tokens.py --catalog backend/data/national/catalog.json
```

The blind runs are recorded as scored in `eval/results/round3_*.txt` and `eval/results/round4_*.txt`. `--live` makes real calls and refreshes the cache.

## What the live calls taught us

The first three voice calls, driven by a person. Each found something the offline eval could not.

| Call | What happened | What changed |
|---|---|---|
| 1. SF, Clinic Scheduler | Booked, but with about 6 s of dead air: a late transcript fragment re-opened the turn. STT heard "eye exams" as "ISX and SAMS". | User turns start on VAD only. ElevenLabs STT gets up to 50 catalog keyterms. |
| 2. National Scheduler | Critical. The LLM said an appointment was booked and invented a confirmation code without calling the booking tool. A second request was lost across a context reset. The LLM invented a 2023 date. The JEV warm-up sent more than the 255 options a choice accepts. | Booking moved into code: the `confirm_booking` edge books and speaks the real reference. `start` and `book_another` carry the caller's words across the reset. Prompts carry today's date. The warm-up sends 20 options. |
| 3. National Scheduler | Clean pass. Two real bookings. A request from Maine got `none_nearby` with the nearest Boston site as the alternative. | None needed. |

A prompt instruction did not stop the model from claiming a booking in call 2. Moving the booking into an edge action did.

Later rounds of live calls on 2026-10-03 (about 40 calls) found one recurring fault: the conversation model rewrote the caller before the resolver saw them.

| What the caller said | What the model sent | What changed |
|---|---|---|
| "Washington." to "Seattle, Washington or Washington, DC?" | `Washington, DC` | While a doctor, place or visit question is open, an answer with a word the caller never said is replaced by the caller's own words |
| "The lady one." to "Dr. David Chen or Dr. Emily Chen?" | `Dr. Emily Chen`, skipping the name confirmation | Same check: the resolver gets "The lady one." and confirms by name |
| "I don't remember if it goes down my throat or up from below" | `scope` | Sentences that state a doubt or say whom the visit is for are restored into the visit phrase, including on the first turn after the context reset |
| "My 10-year-old needs a physical…" | `physical and form signed` (a child was offered a pre-employment physical) | Same restore |
| "Day of checkup." | `time_pref.day = wednesday` | A day the caller did not say is dropped |
| "What are the hours at the Mission Bay clinic?" as the first words | `transfer_to_staff`: the greeting node had no `lookup` tool, so a question could only start a booking or end in a handoff | The greeting node has `lookup` and answers questions from its facts. Replayed through gpt-4o: 9/9 questions went to `lookup`, 6/6 booking requests to `start` |
| Unscripted elderly-caller roleplays: "Uh, the lady one." answering "Do you mean Dr. Emily Chen?"; "Yes." sent as a time pick; "Does the clinic have parking?" at "Shall I book it?"; "Can you do my husband too?" | A spelling request, a repeated question, an invented "Yes" plus a transfer that lost the booking, and a transfer instead of a second booking | A gender-only answer confirms by the model's reading of the name; unclear answers repeat the yes-or-no question; a pick with nothing offered answers the open question; `lookup` says what it does not know and never transfers; `book_another` covers family members and clears their patient details |

Replaying those turns through the same prompt with gpt-4.1 gave the same rewrites, and it answered "dental exam" for a caller who had not chosen. A stronger model was not the fix; a check in code was. The same calls led to a scope shortlist fix ("GI doc wants a scope" now asks), visit names matching without a bracketed abbreviation ("Upper Endoscopy (EGD)"), a second unknown place asking for a nearby city or ZIP instead of repeating itself, and the agent's name in the call panel header. The scripted retests are in [docs/live-call-tests.html](docs/live-call-tests.html); every fault and fix is in [eval/README.md](eval/README.md).

## Honest limits

Remaining failure classes from blind round 4 (JEV mode):

- **Umbrella words** that name several visits, resolved with false confidence. h4-m06 "my stomach doctor said I need a scope" (upper or lower) chose at 0.94 and the check confirmed at 0.82. Also h4-m05 "my baby's checkup" and h4-27 "some blood work". The author flagged h4-27 and h4-m05 as uncertain labels before scoring.
- **Triage nuance.** nat4-sym-03 "it burns when I pee ... since yesterday" went to a urology consultation. The label expects a sick visit or a UTI visit.
- **False refusals.** h4-09 described a seasonal allergy as "my eyes get itchy and watery", and the agent said "we don't offer eye care". nat4-geo-07 and nat4-noloc-02 refused a chest CT within 50 miles, while a general CT scan that the label accepts was offered there.
- **Gender policy asks by design.** h4-p10 and h4-p11 confirm a doctor by name instead of booking.

Other limits:

- The offline eval feeds tool-call arguments, not audio. LLM extraction accuracy is measured only by live calls, about 40 so far.
- Availability is a seeded mock with a fixed `DEMO_NOW` (Wednesday 2026-10-07, 09:00). Bookings and holds live in memory and are lost on restart.
- JEV probabilities move by 0.02-0.08 between identical requests, so a case near a threshold can flip between runs.
- Reschedule, cancel and anything outside booking go to a handoff node with no real transfer behind it.

## Why not the alternatives

| Approach | Cost per call | Accuracy | Latency | Verdict |
|---|---|---|---|---|
| Dump catalog in prompt | SF ~$0.315 per 15 LLM calls (est.). National does not fit a 128k window. | The model must apply 6 cross-entity policies by reading. Nothing stops an MRI at a site without imaging. | Higher time-to-first-token every turn | Baseline |
| RAG over catalog chunks | Low tokens | Retrieves similar text but cannot join capability x location x provider. Disambiguation stays on the LLM. | +150-300 ms embedding hop per lookup (est.) | Rejected. Kept as a scaling path for 1k+ types. |
| One graph node per step | Low tokens | Good | 4-5 forced turns even when the caller said everything in one sentence | Rejected: worst caller experience |
| Resolver + policy table (this) | SF ~$0.058 per 15 LLM calls (est.). Live call 3: ~$0.055 for the LLM. | Policy violations impossible by construction. Ambiguity resolved by the caller or by policy. | Resolver p95 under 7 ms. No extra network hop without a model. | Chosen |

Trade-offs we accepted:

- **Hand-written aliases.** SF has 237 aliases and 47 lay terms, national 426 and 80. They are cheap and auditable, but cover only phrasings someone wrote down. The disambiguator covers the rest. Measured with all of them removed (a new client on day one): JEV loses 3-4 correct turns per set and stays at 0-1 wrong commits; Off loses up to 15 on SF. Details in eval/README.md.
- **A model adds latency on the turns that use it.** The 2.5 s turn budget bounds the worst case.
- **Speak-direct trades variety for safety.** `resolver.speak_direct: false` turns it off per agent.
- **Node transitions cost a hop.** That is why the graph has five nodes and the work lives in tools.

## Scoping

| | What | Why |
|---|---|---|
| Built | Agent Studio, REST API, live call events, Dev view, call review, API keys sheet | Phase 1 scope plus observability |
| Built | Resolver, policy table, geography down to street and house number, templates, lookup, booking in code | The core of Phase 2 |
| Built | National synthetic catalog generator (`backend/tools/gen_national_catalog.py`) | Proves the prompt does not grow with the catalog |
| Built | Four disambiguator modes behind one chooser table, with a gate, a check, caches, timeouts and fallbacks; JEV post-call grader | Measured per mode on every blind set |
| Built | Offline eval: dev sets, blind rounds scored once, text call simulator | Evidence without spending money |
| Mocked | Availability | Deterministic seeded slots inside location hours. A real EHR adapter replaces it behind the same `Availability` interface. |
| Mocked | Bookings | In memory, lost on restart |
| Designed, not built | Paid dialog simulation (ours vs naive dump, same model) and its JEV eval judge | Costs money. Needs approval. |
| Left out | EHR integration, identity verification, insurance, real reschedule and cancel | Out of scope for a take-home. Reschedule and cancel go to handoff. |
| Left out | Vector database, LLM-interpreted policies, a rules DSL | Not needed at this scale. Policies are code on purpose. |
| Left out | Non-English conversation, catalog admin UI | Language is only a provider filter. Admin UI is optional per the brief. |

## Demo script

The **Demo script** button in the studio's top bar has every beat below as a full call, from the first word to the goodbye, plus five messy real-caller calls (14 in all), with "If" branches where the agent can take more than one good path and a "Fail if" line for each. Every line was run against the real agent in text before it went in. Open the agent in Agent Studio, start a test call, and keep the Decisions tab open (or press D for Dev view). The lines below were checked offline against the real resolver. **The same script is in the studio**: the **Demo script** button in the top bar opens a card beside the canvas with every beat's lines (click one to copy it), what the agent should answer, a button that opens the right agent, and a tick for each beat done.

1. **New patient with a referral** (Clinic Scheduler). "I'm a new patient and I have a referral. I need a cardiology consultation with Dr. Chen, soonest you have." Policy removes David Chen. The agent offers Dr. Emily Chen's times with no question. Pick a time, hear the read-back, say yes, and hear the confirmation reference.
2. **Established patient, same request.** Both Chens are valid now, so the agent asks "Do you mean Dr. David Chen or Dr. Emily Chen?" Answer "the lady one". With JEV the agent asks "Do you mean Dr. Emily Chen?": gender is inferred from the first name and confirmed by name, never booked on its own. With the Disambiguator Off it asks between both again, because the catalog has no gender field.
3. **Street level** (National Scheduler). "I need a sick visit at the clinic on Market Street in San Jose." Agent: "Is that Downtown at 1812 Market or Willow Glen at 3330 Market?" Say "thirty-three thirty." The agent offers times at Willow Glen. Then "a sick visit at the one on Lincoln Avenue in Salt Lake" gets times at Sugar House (4821 Lincoln Ave). "Avenue" is not taken as The Avenues Family Clinic.
4. **A place heard by sound.** "I need a flu shot, I'm in Trenton." Agent: "Did you mean Renton, Washington?" Say "No, Trenton, New Jersey." Agent: "Our nearest clinic in New Jersey for a flu shot is Cherry Hill, near Philadelphia. Want me to look there?"
5. **The switch** (Clinic Scheduler, new call). With JEV, "something for my back pain" is read as an orthopedic consultation, and the agent asks "Do you have a referral for an orthopedic consultation?". Flip the Disambiguator to **Off** and call again: the same words get "What's the visit for?". That is the rules path, with no model.
6. **Consent guard.** After an offer, say "Sure, book it" without picking a time. `confirm_booking` is refused, and the agent asks which time first.
7. **Nothing nearby** (National Scheduler). "I need a dental cleaning, I live in Maine." The agent says there is none in Maine, names the nearest Boston site and asks before looking there.
8. **Questions** (Clinic Scheduler, new call, as the first words). "What are the hours at the Mission Bay clinic?" gets Monday to Friday, 8 to 5. "Does anyone at Mission Bay speak Spanish?" gets the two doctors there who do. "Do I need a referral for an MRI?" gets yes, and established patients only. Each answer comes from `lookup` facts, then the agent offers to book.
9. **The model cannot answer for the caller** (National Scheduler). "A flu shot in Washington." Agent: "Is that Seattle, Washington or Washington, DC?" Say only "Washington." The agent asks again, with a ZIP code as a way out. The Decisions tab shows the row: "kept the caller's words: model sent “Washington, DC”, caller said “Washington.”"

Replay beats offline, with no LLM, audio or network: `python backend/tools/text_sim.py` (all beats) or `python backend/tools/text_sim.py N3` (the second live call).

## Repo layout

| Path | What it holds |
|---|---|
| `backend/bot.py` | Voice pipeline. Loads an agent from the `/start` body and runs it. Serves the API. One tool call per LLM turn. |
| `backend/agents_api.py` | REST API over `backend/agents/*.json`, `/api/catalogs`, `/api/grade` |
| `backend/api_keys.py` | The browser's keys over `.env` per call or request, `/api/keys/status`, `/api/keys/test`, log redaction |
| `backend/grader.py` | JEV post-call grader |
| `backend/agent_builder/` | `schema.py` (agent shape), `validation.py` (the one rule set), `builder.py` (JSON to Pipecat Flows, live events, edge preconditions and actions) |
| `backend/agent_tools/` | Tool, guard and action registry, scheduling tools, per-call context, STT keyterms, model warm-up |
| `backend/scheduling/` | Resolver: `catalog_index`, `policy`, `geo`, `names`, `lexicon`, `request`, `resolver`, `decision` (gate, check gate, gender), `templates`, `lookup`, `availability`; disambiguators: `choosers` (the one table), `model_client` (shared client base), `jev`, `openai_chooser`, `embed_chooser` |
| `backend/agents/` | `clinic-scheduler.json` (SF), `national-scheduler.json` (national), `prosper-scheduler.json` (the original example) |
| `backend/data/` | SF `catalog.json` and `aliases.json`; `national/` holds the generated catalog, aliases and metadata |
| `backend/tools/` | `text_sim.py` (text-mode call simulator), `gen_national_catalog.py` |
| `backend/tests/` | Backend tests, including the policy property test, the fuzz test and the review probes |
| `eval/` | Resolver eval, case files, set registry (`sets.py`), model caches, tuning scripts, recorded results |
| `frontend/` | Agent Studio (React 19, Vite, React Flow, Zustand, Pipecat client) |
| `docs/` | `PHASE2_DESIGN.md`, `AGENT_FORMAT.md`, `live-call-tests.html` (live-call retest script) |
| `solution.md` | The submission overview: requirements, key decisions, trade-offs |

## Tests

```bash
backend/.venv/Scripts/python -m pytest backend/tests -q    # Windows; backend/.venv/bin/python elsewhere
cd frontend && pnpm test                                   # vitest
cd frontend && pnpm typecheck && pnpm lint && pnpm build
```

Last run: **1,495 backend tests passed, 1 live JEV smoke test skipped** (2026-10-04, after the real-caller fixes). **127 frontend tests passed**, and the frontend production build passed.

## Final verification (2026-10-03, resolver frozen at `222eb22`)

This section is the round 5 score, taken at `222eb22`. Fixes made after it came from live voice calls, not from the blind cases, and are listed in [What the live calls taught us](#what-the-live-calls-taught-us). Every dev and stress set was re-run after them: still 0 JEV wrong commits, both stress gates PASS.

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

Offline cases feed tool arguments directly. They do not test speech recognition, LLM extraction, interruptions or audio timing. Use [the live-call script](docs/live-call-tests.html) for that layer. Passing these calls cannot prove universal safety.
