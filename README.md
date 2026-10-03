# Prosper challenge: Agent Studio and catalog context management

A voice agent is a graph of nodes (Pipecat Flows) stored as JSON. Phase 1 is Agent Studio, a browser UI that edits that graph and places a test call against the live agent. Phase 2 is a scheduling agent that books across a messy clinic catalog without the LLM ever reading the catalog. The LLM turns speech into one tool call. A deterministic resolver in the same process filters a precomputed table of bookable rows by the booking policies and returns one move: offer up to 3 times, ask the one question that changes the valid set, or refuse with a reason. The booking itself happens in code. Policy-violating bookings cannot be produced, because the policies are code.

The same agent runs on two catalogs:

| Catalog | Size | Naive prompt (catalog in context) |
|---|---|---|
| SF sample (provided, `backend/data/catalog.json`) | 8 locations, 50 providers, 82 types, 760 bookable rows | 8,252 tok |
| National v2 (synthetic, seed 20261002, `backend/data/national/`) | 40 metros, 299 sites, 5,000 providers, 314 types, 138,870 bookable rows | 664,495 tok, 5.2x a 128k window. It does not fit. |

All counts measured (tiktoken `o200k_base` on compact JSON).

```
 Browser: Agent Studio (React + Vite, :5173)
   |  REST  /api/agents, /api/catalogs, /api/grade
   |  WebRTC audio + RTVI events (node_entered, edge_taken, resolver_decision, jev_call)
   v
 Pipecat runner (FastAPI, :7860, backend/bot.py)
   mic -> ElevenLabs STT -> OpenAI LLM (Flows node + tools) -> ElevenLabs TTS -> speaker
                                 |  update_request / lookup / confirm_booking edge
                                 v
   Resolver (backend/scheduling, in-process)
     CatalogIndex      bookable (type, provider, location) rows, built at server start
     policy.check      6 catalog policies, re-run at booking time
     geo               city, state, ZIP, neighborhood, "near X" over the catalog's own coordinates
     lexicon + names   aliases, lay terms, fuzzy + phonetic surname match ("Dr. Nwin" -> Nguyen)
     JEV (optional)    type, provider and site choosers, only when the caller's words carry extra information
     -> Plan: offer | ask | refuse | confirm
                                 |
   templates -> spoken text -> TTS directly (speak-direct, the LLM stays silent)
```

## Quickstart

Needs Python 3.11 (tested on 3.11.8), Node 22 and pnpm 10. No `make` needed.

### 1. Backend

Windows (PowerShell):

```powershell
cd backend
py -3.11 -m venv .venv                 # or: uv venv .venv --python 3.11
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env                 # optional: keys can be pasted in the studio instead
```

macOS / Linux:

```bash
cd backend
python3.11 -m venv .venv               # or: uv venv .venv --python 3.11
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                   # optional: keys can be pasted in the studio instead
```

Run from the repo root, with the venv's Python:

```bash
backend/.venv/Scripts/python backend/bot.py    # Windows
backend/.venv/bin/python backend/bot.py        # macOS / Linux
```

The server preloads both catalog indexes at start (SF about 10 ms, national about 1 s, measured). Starting the server is free. Money is spent only once a browser connects a call.

On Windows, set `PYTHONIOENCODING=utf-8` before redirecting any script output to a file. Pipecat's startup banner and some eval output are not cp1252-safe.

### 2. Frontend

```bash
cd frontend
pnpm install
pnpm dev
```

Open http://localhost:5173. Vite proxies `/api`, `/start`, `/sessions` and `/status` to the backend on :7860. In the sidebar, pick **Clinic Scheduler** (SF catalog) or **National Scheduler** (national catalog). **Prosper Scheduler** is the original Phase 1 example and uses no catalog.

Click **Keys** in the top bar and paste your OpenAI and ElevenLabs keys; Command Code JEV is optional. Nothing to edit on disk. See [API keys](#api-keys).

## API keys

No key is in the repo. The fastest path: start the backend and the frontend, open the studio, click **Keys** in the top bar, paste three keys, and call. **Test** next to a key makes the cheapest real check: OpenAI's model list, the ElevenLabs account (free), one tiny JEV choice.

| Key | Needed for | Get one |
|---|---|---|
| OpenAI (`OPENAI_API_KEY`) | The conversation (the agent's model), and the OpenAI disambiguator. Required for a call. | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| ElevenLabs (`ELEVENLABS_API_KEY`) | Speech to text and the agent's voice. Required for a call. | [elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys) |
| Command Code JEV (`CMD_API_KEY`) | Optional. The JEV disambiguator and the post-call review. Without it the resolver never consults a model and asks the caller instead, and the call review reports that JEV is not configured. | [commandcode.ai](https://commandcode.ai) |

The sheet shows where each key comes from: **Server key**, **This browser** or **Missing**. The dot on the **Keys** button is green with all three, amber without JEV, and red when a call cannot start; Call then says which keys to add and opens the sheet. The OpenAI and JEV rows also sit under the Disambiguator switch when that chooser is picked.

The alternative is `backend/.env`: copy `backend/.env.example`, which lists every variable the backend reads, and fill it. `AGENTS_DIR` (optional) sets where agent JSON files live; default `backend/agents`. A key pasted in the studio wins over `.env` for that one call or request.

How a pasted key travels. The browser keeps it in `localStorage` (`agent-studio:openai-key`, `agent-studio:elevenlabs-key`, `agent-studio:jev-key`) and sends it only to the local backend: in the `/start` body as `api_keys` (the JEV key only for a call that consults JEV), as an `X-CMD-API-Key` header for the call review, and as an `X-Api-Key` header for a key test. The backend uses it for that call or request only, never writes it to disk and never sends it back: `GET /api/keys/status` tells the studio only whether the server has each key. Log lines blank every key field, key header and bearer token (the Pipecat runner logs every `/start` body).

Any script running on the studio's origin can read `localStorage`. That is fine for a local demo, but don't paste a key into a studio served to other people.

## Phase 1: Agent Studio

| Feature | What it does |
|---|---|
| Graph editor | Nodes are cards, edges are labelled with their function name. Add, connect, delete, drag. Auto-layout (dagre). Parallel and backward edges are routed apart so every label stays readable. |
| Inspector | Node: task messages, role override, end flag, tools, context strategy. Edge: function, description, target, collected fields. Agent: persona, voice, model, catalog (picked from `GET /api/catalogs`, with its counts and naive token size), resolver settings. Edge `precondition` and `action` are edited in the raw JSON view. |
| Live validation | The editor sends the draft to `POST /api/agents/validate` as you type. Errors point at the exact node or edge and block the test call. The backend rules are the only rule set. |
| Undo / redo | Ctrl/Cmd+Z, Ctrl/Cmd+Shift+Z or Ctrl+Y. Ctrl/Cmd+S saves. |
| JSON round-trip | A raw JSON view per node and edge. Keys the UI does not know survive load and save. |
| Test call | WebRTC call from the browser with the current draft, unsaved edits included. The canvas highlights the live node and the visited path. The panel shows the transcript, latency, cost and collected flow state. |
| Decisions tab | One row per resolver decision: status, what was said, policy notes, JEV probability and latency, tool-result tokens. |
| Call review | After hang-up, `POST /api/grade` sends the transcript and decisions to JEV once. It returns five scores: booked correctly, unnecessary questions, unsupported claims, caller effort (1-5) and outcome. Cost about $0.00004 per call (measured). |
| Dev view | Press D. Live pipeline strip (STT, LLM, tools, JEV, TTS) with the latest latency per stage, a per-turn voice-to-voice waterfall, a streaming transcript, tool and JEV bubbles on the live node, running cost by provider (prices editable), and prompt tokens per LLM call against the catalog's naive size. |
| Themes | Light, dark, or follow the system. |

Screenshots of both themes are in `frontend/screenshots/`.

The agent format, validation rules, REST API and live call events are in [docs/AGENT_FORMAT.md](docs/AGENT_FORMAT.md). In short: an agent has `name`, `initial_node`, `persona`, `voice_id`, `model` and `nodes`. A node has `task_messages`, optional `role_message`, `edges` and `end`. An edge has `function`, `description`, `target` and JSON-schema `properties`. Phase 2 adds agent `catalog` and `resolver`, node `tools` and `context_strategy`, and edge `precondition` and `action`. All are optional, so Phase 1 agents run unchanged.

## Phase 2: context management

Full design and trade-offs: [docs/PHASE2_DESIGN.md](docs/PHASE2_DESIGN.md). Eval method and every result: [eval/README.md](eval/README.md).

### The approach

1. **The LLM never sees the catalog.** Its job is extraction. It passes the caller's own words to `update_request` (`service_phrase`, `provider_phrase`, `location_phrase`, `specialty_hint`, `is_new`, `has_referral`, `time_pref`, `pick_offer`, `clear`). Questions about hours, addresses and "do you offer X" go to `lookup`, which returns at most 5 facts.
2. **A precomputed policy table.** At server start `CatalogIndex` joins provider locations, provider types and location capabilities into bookable rows. `policy.check(row, patient)` is the only home of the booking rules. It runs in the resolver and again at booking time, so a stale row cannot slip through.
3. **Ask only what changes the set.** Ambiguity here is a catalog fact, not a probability. "Dr. Chen, the heart doctor" matches two SF cardiologists. For a new patient, policy removes David Chen (not accepting new patients) and Emily Chen is offered with no question. For an established patient both are valid, so the agent asks. The resolver picks the question that splits the remaining rows best, and often collapses it into a time offer.
4. **Geography without a geocoder.** The national catalog carries metros and site coordinates. `scheduling/geo.py` resolves a city, state, ZIP, ZIP3, neighborhood or "near X" to an anchor and searches by haversine distance: the place's own radius, then twice that, then 50 miles. Past 50 miles the agent refuses with `none_nearby` and offers the nearest valid site as a pickable alternative ("dental cleaning in Maine" gets Boston). With no place at all, it asks "Which city are you in?". A town that exists in two metros gets an either/or. The SF catalog has no coordinates and loads as one implicit metro, so its behavior is unchanged.
5. **Speak-direct templates.** Offers, questions and refusals are rendered from templates and queued straight to TTS. The handler returns `NO_RESPONSE`, which skips the LLM's second round trip. Names and times come from the catalog and the availability source, so the LLM cannot paraphrase them into something false. `parallel_tool_calls=False` keeps this sound.
6. **Booking in code.** The `confirm_booking` edge has the `offer_confirmed` precondition and the `book_confirmed` action. It is refused until the caller picked a time, heard it read back and said yes. The action books exactly that read-back offer, re-checks policy, holds the slot, and speaks the confirmation reference from a template. The LLM cannot pick a different offer or announce a booking that did not happen.
7. **Small prompts.** Five nodes: `greeting -> schedule -> booked -> done`, plus `handoff`. `schedule` uses `context_strategy: "reset"` and a `{{ summary }}` placeholder, so the prompt stays small as the call grows. `start` and `book_another` carry the caller's latest words (`new_request` action), so a request survives the reset.

### JEV roles

JEV (Command Code decision model) returns calibrated probabilities over named options. It is used in four places. Policy always runs after a pick, so a model mistake cannot book a policy violation.

| Role | When | Built |
|---|---|---|
| Type chooser | The lexical matcher has no match, only a specialty default, or leaves words of the phrase unexplained ("something for my back pain", "shots before my trip to Thailand"). A second question then weighs the front-runner against its rival with an "either" answer, and the caller is asked when the words fit both or the two questions disagree. National catalogs send a shortlist of at most 20 types. | Yes |
| Provider chooser | Two or more policy-valid providers match the name and the phrase has a clue beyond the name. Catalog facts in it (language, title, specialty, site) narrow without JEV; JEV answers gender from first names ("Dr. Nguyen, the lady doctor") and weighs words no fact explains. A bare "Dr. Chen" never calls JEV. | Yes |
| Site chooser | A descriptive clinic clue matches several sites. | Yes |
| Post-call grader | Once per call, after hang-up (Call review). | Yes |
| Eval judge for a paid dialog simulation | | No. Designed, not built. |

On live calls JEV has a 2.5 s budget per turn (at most 1.5 s per request) with no retry; an answer that does not arrive is never committed on, and the resolver asks. A spoken "One moment." covers waits over 0.3 s. A 20-option warm-up request at call start moves the connection cost off the caller's first turn.

## Evidence

All numbers measured in this repo unless marked (est.). Each eval case is a sequence of `update_request` arguments, with speech-to-text noise simulated by misspellings. The LLM's own extraction is not measured by the resolver eval. The live calls cover it.

**Wrong commit** = the resolver offered or confirmed something it should not have, counted per commit. **Top-1** = the turn matched the expected result exactly. **Dev** sets are sets we looked at and fixed against. **Held-out** sets were authored before tuning and scored once.

### Headline: national2, held-out

48 cases, 54 turns. A fresh draw (seed 20261117) with no type and metro overlap with the national dev set. The cases were written without access to the resolver code or the dev-set failures. DeepSeek wrote the caller's wording from scenario facts. The set was frozen before the fixes were scored on it, then run once.

| national2 (held-out) | JEV off | JEV on |
|---|---|---|
| Wrong commits per commit | 1/24 (4.2%) | 1/37 (2.7%) |
| Top-1 | 37/54 (68.5%) | 50/54 (92.6%) |
| Questions per booking | 0.54 | 0.21 |
| Turns that consulted JEV | 0 | 31% |
| JEV cost for the whole set | $0 | $0.0008 |

The one wrong commit: "allergy shots" from a new patient (a type new patients may not book) got an Allergy Consultation offer instead of a refusal. It is a type choice, not a policy violation.

### National dev set (seen, so not evidence of generalization)

| national (dev, 48 cases, 56 turns) | JEV off | JEV on |
|---|---|---|
| First run, before any fix | 6/27 wrong (22%), top-1 37/56 (66%) | 7/32 wrong (22%), top-1 41/56 (73%) |
| After the fixes | 1/35 wrong (2.9%), top-1 51/56 | 0/39 wrong, top-1 56/56 |

The first run is kept in `eval/results/national_v1_first_run.txt`. The fixes are general rules, each unit-tested with different wording: a hard 50-mile cap with `none_nearby`, full type names beating the generic types they contain, ZIP and misspelled-city precedence, suburb clinics searching their suburb.

### SF catalog

| SF set | JEV off | JEV on |
|---|---|---|
| main (rules written against) | 0/48 wrong, top-1 105/105 | 0/48 wrong, top-1 105/105 |
| held-out combined (heldout + heldout2) | 4/22 wrong (18.2%), top-1 27/61 | 2/39 wrong (5.1%), top-1 49/61 |

Policy violations: 0 across the policy property test over every bookable row and a 6,000-conversation fuzz test checked by an independent oracle.

### Latency

| Stage | p50 / p95 |
|---|---|
| Resolver, SF | ~2 / 5 ms |
| Resolver, national (JEV off) | ~1.2 / 6.7 ms |
| Resolver, national (JEV on, answers from cache) | ~1.8 / 7.7 ms |
| JEV request, live | ~550 / 720-780 ms |
| Live calls: LLM TTFB 0.8-1.1 s, TTS TTFB ~0.15 s, STT final ~0.35-0.45 s | |
| Live calls: voice-to-voice on in-node turns | 1.1-1.3 s |
| Live calls: a node transition adds one LLM hop | ~2-3 s total |

### Prompt tokens and cost

| | Naive (catalog in prompt) | Ours |
|---|---|---|
| SF, per LLM call | 8,405 tok | 779 at turn 1, 2,324 at turn 15 (10.8x and 3.6x fewer) |
| SF, 15 LLM calls, gpt-4o input (est.) | ~$0.315 | ~$0.058 |
| National, per LLM call | 664,495 tok. Does not fit a 128k window. | 625 tok fixed part, plus summary and tool history |
| National, 15 LLM calls if it fit (est.) | ~$24.92 | |
| Live call 3 (national, 13 LLM calls) | | 20,805 prompt tok total, ~1,600 per call |

Live call 3 cost, about 2 minutes, estimated at list prices:

| Item | Cost (est.) |
|---|---|
| gpt-4o (20,805 in / 254 out) | ~$0.055 |
| ElevenLabs TTS (996 chars) | ~$0.30 |
| STT (117 s) | ~$0.013 |
| JEV | ~$0.0001 |
| Total | ~$0.37 |

After context management, TTS is about 80% of the cost of a call. The LLM is no longer the cost to cut.

### Reproduce

From the repo root with the backend venv's Python (`backend/.venv/Scripts/python` on Windows, `backend/.venv/bin/python` elsewhere). All of these are free and offline.

```bash
python eval/run_resolver_eval.py --set all --jev on          # SF sets; JEV answers replayed from eval/.jev_cache.json
python eval/run_resolver_eval.py --set national --jev on     # national dev set
python eval/run_resolver_eval.py --set national2 --jev on    # national held-out set
python eval/run_resolver_eval.py --set national2 --jev off --verbose   # no model, every miss printed
python eval/naive_baseline_tokens.py                          # SF naive vs ours
python eval/naive_baseline_tokens.py --catalog backend/data/national/catalog.json
```

`--jev on` without `--live` replays the cache keyed by request hash, so it reproduces the live decisions with no network. `--live` makes real calls and refreshes the cache.

## What the live calls taught us

Three voice calls, driven by a person. Each found something the offline eval could not.

| Call | What happened | What changed |
|---|---|---|
| 1. SF, Clinic Scheduler | Booked, but with about 6 s of dead air: a late transcript fragment re-opened the turn and cancelled the reply. STT heard "eye exams" as "ISX and SAMS". | User turns start on VAD only. ElevenLabs STT gets up to 50 catalog keyterms. |
| 2. National Scheduler | Critical. The LLM said an appointment was booked and invented a confirmation code without calling the booking tool. A second request was lost across a context reset. The LLM invented a 2023 date. The JEV warm-up was rejected, because it sent more options than the 255 a JEV choice question accepts. | Booking moved into code: the `confirm_booking` edge books and speaks the real reference from a template. `start` and `book_another` carry the caller's words across the reset. Prompts carry today's date, and past dates are rejected. The warm-up sends 20 options. |
| 3. National Scheduler | Clean pass. Two real bookings. A request from Maine got `none_nearby` with the nearest Boston site as the alternative. The post-call grader ran. | None needed. |

Call 2 is why the original design's LLM-called booking tool is gone. A prompt instruction did not stop the model from claiming a booking. Moving the booking into an edge action did.

## Why not the alternatives

| Approach | Cost per call | Accuracy | Latency | Verdict |
|---|---|---|---|---|
| Dump catalog in prompt | SF ~$0.315 per 15 LLM calls (est.). National does not fit a 128k window. | The model must apply 6 cross-entity policies by reading. Nothing stops an MRI at a site without imaging. | Higher time-to-first-token every turn | Baseline |
| RAG over catalog chunks | Low tokens | Retrieves similar text but cannot join capability x location x provider. Disambiguation stays on the LLM. | +150-300 ms embedding hop per lookup (est.) | Rejected. Kept as a scaling path for 1k+ types. |
| One graph node per step (specialty, type, provider, location) | Low tokens | Good | 4-5 forced turns even when the caller said everything in one sentence | Rejected: worst caller experience |
| Resolver + policy table (this) | SF ~$0.058 per 15 LLM calls (est.). Live call 3: ~$0.055 for the LLM. | Policy violations impossible by construction. Ambiguity resolved by the caller or by policy. | Resolver p95 under 8 ms on both catalogs. No extra network hop. | Chosen |

### Trade-offs we accepted

- **Hand-written aliases.** SF has 237 aliases and 47 lay terms, national 426 and 80. They are cheap and auditable, but they cover only phrasings someone wrote down. JEV is the fallback for the rest.
- **JEV adds latency on the turns that use it.** A type decision is two sequential requests (a choice, then its check): measured live on heldout2, tune and national2, JEV turns took 1.0 s p50 and 1.46 s p95 (eval/README.md, round 3). The 2.5 s budget bounds the worst case.
- **Speak-direct trades flexibility for safety.** Templated sentences are less varied than LLM prose. `resolver.speak_direct: false` turns it off per agent.
- **Node transitions cost a hop.** A transition turn takes about 2-3 s against 1.1-1.3 s in-node. That is why the graph has five nodes and the work lives in tools.

### Beyond the national catalog

- The prompt does not change with catalog size. The national fixed part is 625 tok.
- At 138,870 rows the table still lives in memory (index build about 1 s). Larger catalogs move it to SQLite or Postgres with the same indexes.
- Name-only resolution gets weaker as names collide. 0.46% of national providers share an exact full name with another. The resolver asks city or specialty first.
- Types would get a hierarchy (specialty, family, variant), with precomputed embeddings shortlisting candidates for JEV.

### Failure handling

| Situation | Behavior |
|---|---|
| Misheard name | Jaro-Winkler plus Double Metaphone, with a respelling rule for spoken "Ng-" surnames. One match is confirmed implicitly. Several get a splitting question. After a miss the agent asks again, then asks for the spelling. The third miss hands off to staff. |
| Misheard place | Fuzzy and phonetic matching against the catalog's own city, neighborhood and site names. |
| Change of mind | `update_request` overwrites the slot and recomputes. Dependent choices are kept only if still valid. `clear` withdraws a choice ("any doctor is fine"). |
| Nothing valid | A specific refusal plus the nearest valid alternative when one exists. |
| Nothing nearby | `none_nearby` past 50 miles, with the nearest valid site as a pickable alternative. |
| Unoffered type | The agent says the clinic does not offer it. Unoffered types are never JEV candidates. |
| JEV slow, down or unsure | Timeout, error or low confidence gives exactly the no-JEV behavior (unit-tested). |
| Booking without consent | `confirm_booking` is refused until the caller said yes to the read-back. |
| Slot taken or policy fails at booking | The agent says so and offers fresh times in the same turn. |
| Same slot, two calls | Holds are process-wide, so two concurrent calls cannot book the same slot. |

## Scoping

| | What | Why |
|---|---|---|
| Built | Agent Studio, REST API, live call events, Dev view, call review | Phase 1 scope plus observability |
| Built | Resolver, policy table, geography, templates, lookup, booking in code | The core of Phase 2 |
| Built | National synthetic catalog generator (`backend/tools/gen_national_catalog.py`) | Proves the prompt does not grow with the catalog |
| Built | JEV type, provider and site choosers with a tuned gate, cache, timeouts, fallbacks; JEV post-call grader | Measured to help on loose phrasings |
| Built | Offline eval with dev and held-out sets, text call simulator | Evidence without spending money |
| Mocked | Availability | Deterministic seeded slots inside location hours, fixed `DEMO_NOW`. A real EHR adapter replaces it behind the same `Availability` interface. |
| Mocked | Bookings | In memory, lost on restart |
| Designed, not built | Paid dialog simulation (ours vs naive dump, same model) and its JEV eval judge | Costs money. Needs approval. |
| Left out | EHR integration, identity verification, insurance | Out of scope for a take-home |
| Left out | Real reschedule and cancel | They go to handoff |
| Left out | Vector database, LLM-interpreted policies, a rules DSL | Not needed at this scale. Policies are code on purpose. |
| Left out | Non-English conversation | Language is only a provider filter |
| Left out | Catalog admin UI | Optional per the brief |

## Demo script

Open Clinic Scheduler in Agent Studio, start a test call, and keep the Decisions tab open (or press D for Dev view).

1. **New patient with a referral.** "I'm a new patient and I have a referral. I need a cardiology consultation with Dr. Chen, soonest you have." Policy removes David Chen. The agent offers times with Dr. Emily Chen and asks no disambiguation question. Pick a time, hear the read-back, say yes, and hear the confirmation reference spelled out.
2. **Established patient, same request.** Both Chens are now valid, so the agent asks "Do you mean Dr. David Chen or Dr. Emily Chen?" Same words, different correct behavior, decided by policy.
3. **Misheard name and a policy refusal.** "I'm a new patient, I need an MRI of my knee with Dr. Nwin." The phonetic match finds Dr. Nguyen. The agent refuses: a knee MRI is only for established patients and needs a referral. Then "Do you do eye exams?" gets "not offered".
4. **Consent guard.** After an offer, say "Sure, book it" without picking a time. `confirm_booking` is refused, and the agent asks which time first.
5. **National.** Switch to National Scheduler. "I hurt my knee playing football. I'm in Austin, soonest." Then "I need a dental cleaning, I live in Maine." The second request gets the nearest Boston site.

Replay the beats offline, with no LLM, audio or network:

```bash
python backend/tools/text_sim.py        # all beats (1-5 SF, N1-N3 national)
python backend/tools/text_sim.py N3     # one beat: replays the second live call
```

## Repo layout

| Path | What it holds |
|---|---|
| `backend/bot.py` | Voice pipeline. Loads an agent from the `/start` body and runs it. Serves the API. One tool call per LLM turn. |
| `backend/agents_api.py` | REST API over `backend/agents/*.json`, `/api/catalogs`, `/api/grade` |
| `backend/grader.py` | JEV post-call grader |
| `backend/api_keys.py` | The browser's keys over `.env` per call or request (read through `call_key` in `scheduling/choosers.py`), `/api/keys/status`, `/api/keys/test`, log redaction |
| `backend/agent_builder/` | `schema.py` (agent shape), `validation.py` (the one rule set), `builder.py` (JSON to Pipecat Flows, live events, edge preconditions and actions) |
| `backend/agent_tools/` | Tool, guard and action registry, scheduling tools, per-call context, STT keyterms, JEV warm-up |
| `backend/scheduling/` | Resolver: `catalog_index`, `policy`, `geo`, `request`, `resolver`, `lexicon`, `names`, `text`, `templates`, `lookup`, `availability`, `decision`, `jev` |
| `backend/agents/` | `clinic-scheduler.json` (SF), `national-scheduler.json` (national), `prosper-scheduler.json` (the original example) |
| `backend/data/` | SF `catalog.json` and `aliases.json`; `national/` holds the generated catalog, aliases and metadata |
| `backend/tools/` | `text_sim.py` (text-mode call simulator), `gen_national_catalog.py` |
| `backend/tests/` | Backend tests, including the policy property test and the fuzz test |
| `eval/` | Resolver eval, case files, JEV cache, gate tuning, token baseline, recorded results |
| `frontend/` | Agent Studio (React 19, Vite, React Flow, Zustand, Pipecat client) |
| `docs/` | `PHASE2_DESIGN.md`, `AGENT_FORMAT.md` |

## Tests

```bash
backend/.venv/Scripts/python -m pytest backend/tests -q    # Windows; backend/.venv/bin/python elsewhere
cd frontend && pnpm test                                   # vitest
cd frontend && pnpm typecheck && pnpm lint && pnpm build
```

Last run: 636 backend tests passed (1 live JEV smoke test skipped), 83 frontend tests passed.

## Known limitations

- Availability is a seeded mock with a fixed `DEMO_NOW` (Wednesday 2026-10-07, 09:00). Prompts use that date as today.
- Bookings and holds live in memory and are lost on restart.
- Three live calls are a smoke test, not a measurement of the LLM's extraction accuracy.
- SF numbers outside the held-out sets are an in-distribution upper bound. The same author wrote those cases and the aliases.
- JEV infers gender from first names when the caller says "the lady doctor". The catalog has no gender field, and one SF held-out case is a wrong commit for that reason.
- JEV probabilities move by 0.02-0.08 between identical requests, so a case near the act threshold can flip between runs.
- Reschedule, cancel and anything outside booking go to a handoff node with no real transfer behind it.
