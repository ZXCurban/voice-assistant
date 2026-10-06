# Assistant orchestration boundary

The active user interface is text. The request flow is text input → rules NLU
→ normalizer → optional FRIDA policy → stateful dialogue manager → backend
orchestrator/services → event-based response template. State persists in Redis
between requests, scoped by the signed principal and clinic in production.
STT/TTS adapters and trained conversational NLU weights are not included.

```text
Text input
 ↓
Rules NLU + confidence (app/assistant/nlu.py)
 ↓
Normalizer (app/assistant/normalizer.py)
 ↓
FRIDA decision policy (optional, normalized JSON only)
 ↓
Dialogue Manager + Redis state
 ↓
AssistantOrchestrator → existing application services
 ↓
Event Response Engine (Jinja2) → text output
```

The existing `POST /api/v1/chat` contract is unchanged. The chat route calls
`AssistantOrchestrator` in-process; backend services remain the single source
of business rules. Redis is used only for dialogue state, not domain data.

## Division of responsibilities

```text
NLU / FRIDA:
- parser identifies intent and candidate entities with confidence
- normalizer converts dates, times and known specialty mentions
- FRIDA can choose a bounded backend route or request clarification
- neither component accesses PostgreSQL / services / repositories

Backend (this repo):
- validates entities against the database
- resolves names to ids (specialty/doctor), detects ambiguity
- calculates availability (existing slot engine only)
- creates/reschedules/cancels appointments with race protection
- enforces tenant isolation (clinic_id scope, cross-tenant → 404)
```

## Contracts

- `AssistantRequest`: flat typed model — `intent` (13 literals),
  `clinic_id`, `patient_id`, `specialty_id`/`specialty_name`,
  `doctor_id`/`doctor_name`, `appointment_id`, `date` (clinic-local day),
  `time_after`/`time_at` (clinic-local spoken time),
  `starts_at`/`new_starts_at` (tz-aware), `reason`, patient fields,
  `confirmed`, `context`, plus optional `city`/`address` for
  `find_clinics` ranking (nearest-first via `services/geo.py`; absent →
  original order, `details` gains `matched_city`/`sorted_by` only when
  a query was given).
- `AssistantResult`: `status` (`success` | `need_clarification` |
  `not_found` | `conflict` | `invalid_input` |
  `confirmation_required`), `code` (stable machine string),
  `message` (human-readable, may be paraphrased), `requires_confirmation`,
  `details` (JSON payloads: `slots`, `appointment`, `candidates`, …).
- `DialogueState`: workflow and candidate values persisted in Redis with TTL;
  key scope includes a hash of principal+clinic and conversation ID. Raw user
  utterances are not kept in conversation history. A short Redis lock
  serializes turns for the same conversation.

## Clarification model

The orchestrator never invents missing information:

| Situation | Result |
|---|---|
| No `clinic_id` (and none in context) | `need_clarification` / `CLINIC_REQUIRED` |
| No date for slot search | `need_clarification` / `DATE_REQUIRED` |
| Neither specialty nor doctor | `need_clarification` / `SPECIALTY_OR_DOCTOR_REQUIRED` |
| No patient for booking | `need_clarification` / `PATIENT_REQUIRED` |
| No slot selected / no new slot | `need_clarification` / `SLOT_REQUIRED` / `NEW_SLOT_REQUIRED` |
| Doctor name matches 2+ doctors | `need_clarification` / `AMBIGUOUS_DOCTOR` + `candidates` |
| Unknown specialty/doctor/clinic/… | `not_found` / `<ENTITY>_NOT_FOUND` |
| Search yields zero slots | `not_found` / `NO_SLOTS_AVAILABLE` (no fabricated alternatives) |
| Invalid normalized params (bad status, missing ids) | `invalid_input` / `INVALID_INPUT` |

Cross-tenant references behave exactly like the HTTP API: `not_found`
(never reveal whether the foreign id exists).

## Confirmation model

Read-only intents execute immediately. Mutating intents
(`book_appointment`, `reschedule_appointment`, `cancel_appointment`,
`complete_appointment`) require `confirmed=true`:

1. Call with `confirmed=false` → `confirmation_required` +
   `requires_confirmation=true` + preview in `details` (the exact slot /
   current appointment). The slot is re-validated at preview time: a
   vanished slot returns `conflict` immediately.
2. Dialogue layer asks the user ("Есть слот завтра в 14:30. Записать?").
3. Call again with identical params + `confirmed=true` → executes via
   the existing race-safe services.

The orchestrator implements no conversation itself.

## Error mapping (services → result)

`NotFoundError` → `not_found`, `ConflictError` → `conflict`
(`SLOT_ALREADY_BOOKED`, `CLINIC_INACTIVE`, `DOCTOR_INACTIVE`,
`APPOINTMENT_ALREADY_<STATE>`, …), `ValueError` → `invalid_input`.
Original messages are preserved in `message`; nothing collapses into a
generic error.

## Rules the assistant pipeline must follow

1. Resolve `clinic_id` first; every call is tenant-scoped.
2. Copy `starts_at` verbatim from a `find_slots` response; never invent
   times (`ends_at` is always computed server-side).
3. Treat `not_found` as final for that scope; do not retry other
   clinics silently — ask the user.
4. On `SLOT_ALREADY_BOOKED`, offer another slot from the same search.
5. Specialty names are stored lowercased; match case-insensitively.
6. `doctor_name` matching is exact (case-insensitive); partial names
   return `DOCTOR_NOT_FOUND` — list doctors and let the user pick.

## What is deliberately absent

No ToolRegistry/plugin framework (explicit `handle` dispatch is enough), no
LLM in the chat path, and no STT/TTS SDKs. `AssistantOrchestrator` remains
stateless. Production requests require signed gateway claims, but a concrete
gateway/OTP/SSO implementation, rate limiting, audit pipeline, and clinician-
approved symptom-to-specialty map remain outside the repository.
