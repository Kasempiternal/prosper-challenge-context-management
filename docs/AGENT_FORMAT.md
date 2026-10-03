# Agent format and API

The contract between the backend (`backend/agent_builder/`, `backend/agents_api.py`, `backend/grader.py`, `backend/bot.py`) and the Agent Studio frontend. The Python source of truth is `backend/agent_builder/schema.py` (shape), `backend/agent_builder/validation.py` (rules) and `backend/agent_tools/registry.py` (the tools, guards and actions an agent may name). The REST API and the builder both call the same `validate_agent`.

## Agent JSON

An agent is one JSON file in `backend/agents/<id>.json`. Field names follow Pipecat Flows' `NodeConfig` where one exists. The structural addition is `edges`: transitions stored as data (a string `target`), which `AgentBuilder` compiles into Pipecat Flows functions.

```jsonc
{
  "id": "national-scheduler",          // slug, also the filename (owned by the UI and API)
  "name": "National Scheduler",
  "initial_node": "greeting",
  "persona": "...",                    // global role message, applied to every node
  "voice_id": "21m00Tcm4TlvDq8ikWAM",  // ElevenLabs voice
  "model": "gpt-4o",                   // OpenAI model
  "catalog": "data/national/catalog.json",  // Phase 2, optional; path inside backend/
  "resolver": { "speak_direct": true, "chooser": "openai", "timeout_ms": 1200 }, // Phase 2, optional
  "nodes": [
    {
      "name": "schedule",
      "task_messages": [{ "role": "developer", "content": "... {{ summary }} ..." }],
      "role_message": null,            // optional per-node persona override
      "pre_actions": [], "post_actions": [],
      "end": false,
      "tools": ["update_request", "lookup"],  // Phase 2, optional
      "context_strategy": "reset",            // Phase 2, optional: "append" (default) | "reset"
      "respond_immediately": true,            // Phase 2, optional; omitted = Pipecat's default (true)
      "edges": [
        {
          "function": "confirm_booking",
          "description": "When the LLM should call this.",
          "target": "booked",
          "properties": {},            // JSON-schema properties the LLM fills when it takes the edge
          "required": [],
          "precondition": "offer_confirmed",  // Phase 2, optional
          "action": "book_confirmed"          // Phase 2, optional
        }
      ],
      "ui": { "x": 420, "y": 0 }       // canvas position, owned by the UI
    }
  ]
}
```

The UI round-trips keys it does not know on the agent, node and edge. It never drops them.

## Saved agents

| File | Catalog | Graph |
|---|---|---|
| `clinic-scheduler.json` | `data/catalog.json` (SF) | `greeting -> schedule -> booked -> done`, plus `handoff` |
| `national-scheduler.json` | `data/national/catalog.json` | Same graph and node prompts. Only the persona differs. |
| `prosper-scheduler.json` | none | The original Phase 1 example, four nodes, no tools |

In both scheduler agents: `start` (greeting to schedule) and `book_another` (booked to schedule) use the `new_request` action. `confirm_booking` (schedule to booked) uses the `offer_confirmed` precondition and the `book_confirmed` action. `schedule` has `update_request` and `lookup` with `context_strategy: "reset"`. `booked` has `lookup`. Every non-end node has `transfer_to_staff` to `handoff`.

## Validation rules

`POST /api/agents/validate` and `PUT /api/agents/{id}` return errors as `{path, message}`, for example `nodes[1].edges[0].target`.

- `name` and `initial_node` are required, and `initial_node` names a defined node. `persona`, `voice_id` and `model` are strings when present.
- Node names are unique.
- A non-end node has at least one task message, each with non-empty `content`.
- A node with no edges must be `end: true`.
- Edge `function` names match `^[a-zA-Z_][a-zA-Z0-9_]{0,63}$` and are unique within a node. `description` is a string.
- Edge `target` names a defined node. Every name in `required` is a key of `properties`.
- `context_strategy` is the string `"append"` or `"reset"`. `respond_immediately` and `end` are booleans. `pre_actions` and `post_actions` are lists.
- `tools` names come from `TOOLS` in `backend/agent_tools/registry.py`: `update_request`, `lookup`. A tool name must not repeat or collide with an edge function in the same node.
- `precondition` names a guard from `EDGE_GUARDS`: `offer_confirmed`.
- `action` names an action from `EDGE_ACTIONS`: `book_confirmed`, `new_request`. An action's parameters must be listed in the edge's `required`. `new_request` needs `request`.
- `catalog` is a path inside `backend/` that exists. It is required when any node has `tools` or any edge has an `action`.
- `resolver.speak_direct` and `resolver.jev.enabled` are booleans. `resolver.chooser` is one of `jev`, `openai`, `embed`, `none`. `resolver.timeout_ms` and `resolver.jev.timeout_ms` are integers from 1 to 30000.

## Phase 2 fields

| Field | Effect |
|---|---|
| `catalog` | Catalog JSON the scheduling tools load. Indexed once per process. A catalog with `metros` and site coordinates enables geography. One without them loads as one implicit metro. |
| `resolver.speak_direct` | Templated offers, questions, refusals and the booking confirmation go straight to TTS. The tool result carries `spoken` (what the caller heard) instead of `say`, and the LLM is not called again for that turn. |
| `resolver.chooser` | The model behind the resolver's type, provider and site hooks; every one feeds the same confidence gate. `jev`: Command Code JEV (needs `CMD_API_KEY`). `openai`: gpt-4o-mini answers one option key and its token logprobs are the distribution (needs `OPENAI_API_KEY`). `embed`: local fastembed `BAAI/bge-small-en-v1.5` cosine similarity, softmax T=0.0125, no network. `none`: no model, ambiguity becomes a question. Absent: `jev` when `resolver.jev.enabled` (the default), else `none`. A chooser whose key or package is missing runs as `none`. The Test call panel and agent settings switch it. |
| `resolver.timeout_ms` | Per-turn budget of a networked chooser (`jev`, `openai`), no retry; one request may use at most 1.5 s of it. On a timeout the resolver asks the caller instead of committing. Absent: `resolver.jev.timeout_ms`, then 2500. |
| `resolver.jev` | Legacy: `enabled` picks the default chooser when `chooser` is absent; `timeout_ms` is the fallback budget. |
| node `tools` | Code-defined tools attached by name. The handler owns the parameter schema. The UI shows it read-only. |
| node `context_strategy` | `"reset"` clears the LLM context on entry. The node prompt carries a `{{ summary }}` placeholder rendered from flow state. |
| edge `precondition` | While the guard returns a reason, calling the edge returns `{"status": "error", "error": <reason>}` and no transition happens. `offer_confirmed` holds only when flow state `status == "confirm"`: the caller picked an offer and heard it read back. |
| edge `action` | Code run when the edge is called, after the precondition and before the transition. It may keep the call on the current node. |

### Tools

| Tool | Arguments | Result |
|---|---|---|
| `update_request` | `service_phrase`, `specialty_hint` (enum of the catalog's specialties), `provider_phrase`, `location_phrase` (clinic, neighborhood, city, state or ZIP in the caller's words), `is_new`, `has_referral`, `time_pref` (`soonest`, `day`, `days`, `part_of_day`, `not_before`), `pick_offer` (1-3), `clear` | `{status, spoken or say, offers?, ask?, reason?, ...}` for `offer`, `ask`, `refuse` or `confirm` |
| `lookup` | `kind` (`location_info`, `provider_info`, `do_you_offer`), `phrase` | At most 5 facts: hours, addresses, languages, "do you offer X" |

### Edge actions

| Action | Behavior |
|---|---|
| `book_confirmed` | Books exactly the offer `update_request` read back, whatever the LLM passed. Re-checks policy and holds the slot. On success it speaks "You're all booked. Your confirmation is ..." with the reference spelled one character at a time, appends to `bookings`, and moves to the target. If policy fails or the slot was taken, it says so, offers fresh times and stays on the node. Holding a slot this call already holds returns the same reference, so a retry converges on one booking. |
| `new_request` | Starts a fresh request from the caller's latest words (`request`). Only the patient flags carry over. The words become `summary`, so they survive the context reset. |

Flow state written by the scheduling tools and actions: `req` (the request), `status` (last resolver status `offer | ask | refuse | confirm`, or `booked`), `summary`, `bookings` (a list of `{ref, visit, provider, location, when}`), `today` (the date phrase the prompts use).

## REST API

Served by the Pipecat runner's FastAPI app on port 7860.

| Method and path | Result |
|---|---|
| `GET /api/agents` | `[{id, name, node_count, updated_at}]`, newest first |
| `GET /api/agents/{id}` | The agent JSON, or 404 |
| `POST /api/agents {name}` | 201 with a new two-node agent. `id` is a unique slug of the name. |
| `PUT /api/agents/{id}` | 200 `{ok: true, agent}`, or 422 `{ok: false, errors}` and nothing saved |
| `DELETE /api/agents/{id}` | 204, or 404 |
| `POST /api/agents/validate` | `{ok, errors}`. No LLM call. |
| `GET /api/catalogs` | Every `backend/data/**/catalog.json`, shallowest first: `[{path, label, locations, providers, appointment_types, metros, naive_tokens}]`. Counts come from the `catalog.meta.json` sidecar when present. |
| `POST /api/grade` | Post-call grade. See below. |
| `GET /api/voices` | Fixed list of six ElevenLabs voices |
| `GET /api/models` | `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `gpt-4.1-mini`, `gpt-4.1-nano` |

On first start an empty `backend/agents/` folder is seeded with `example_flow.json` as `prosper-scheduler`.

### POST /api/grade

Body: `{agent_id? | agent?, transcript: [{role: "user" | "bot", text}], decisions?: [resolver_decision...], collected?: {}}`. The transcript needs at least one non-empty turn.

One JEV request with five questions. The response is `{ok: true, scores, usage: {input_tokens, output_tokens, usd}, ms}`:

| Score | Shape |
|---|---|
| `booked_correctly`, `unnecessary_questions`, `unsupported_claims` | `{p}`, probability of yes |
| `caller_effort` | `{level, probabilities, confidence}`, expected level from 1 (very easy) to 5 |
| `outcome` | `{choice, confidence, probabilities}`, one of `booked`, `refused_correctly`, `handed_off`, `abandoned`, `unclear` |

Errors return `{ok: false, reason}`: 422 for a bad body or unknown `agent_id`, 503 when JEV is not configured or rejects the key, 504 on timeout (8 s, one retry), 502 for other JEV failures.

## Test call

The browser calls the runner's `POST /start` with `{"transport": "webrtc", "body": {"agent": <editor agent>}}`. Unsaved edits are included. The WebRTC offer then goes to `/sessions/{sessionId}/api/offer`.

`bot()` in `backend/bot.py` picks the agent from `body.agent`, else `body.agent_id` (loaded from `backend/agents/`), else `example_flow.json`. An invalid agent is logged and the session ends cleanly.

## Live call events

The bot sends RTVI server messages. The client reads them with `onServerMessage`.

| Event | Payload |
|---|---|
| `node_entered` | `{node, state}`. Also sent for the initial node. |
| `edge_taken` | `{function, from, to, args}` |
| `resolver_mode` | `{requested, active}`. Sent once when the client connects: the chooser the agent asked for and the one running (`none` when its key or package is missing). |
| `resolver_decision` | `{status, say, summary, notes, valid_rows, offers?, candidates?, reason?, model: {used, provider?, p?, ms?}, tokens: {result}, ms}`. `ms` is the resolve time including any model wait. |
| `model_call` | `{provider, purpose, ms, input_tokens, usd, ok, source, p}`. One per chooser request; `provider` is `jev`, `openai` or `embed` (usd 0). `purpose` is `type`, `type check`, `provider`, `provider gender`, `site` or `warmup`; one turn may emit several. |
| `call_ended` | `{reason: "end_node"}`. A transport disconnect also ends the call in the UI. |

Transcripts, LLM and TTS text, tool start and stop, latency metrics and usage come from the standard RTVI events. Dev view reads both. RTVI reports tool names only, never arguments or results.
