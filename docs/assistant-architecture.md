# Assistant orchestration boundary

```text
STT
 ↓
LLM / Intent & Entity Extraction        ← AI teammate (not in this repo)
 ↓
AssistantRequest                         ← app/assistant/schemas.py
 ↓
AssistantOrchestrator                    ← app/assistant/orchestrator.py
 ↓
existing application services            ← app/services/*
 ↓
repositories → PostgreSQL
```

No HTTP endpoint was added for the assistant layer: the AI teammate
imports `AssistantOrchestrator` in-process (same service). The HTTP API
documented in `docs/voice-map.md` stays the integration contract for
remote clients; the orchestrator reuses the same services the routers
call, so both surfaces share one source of truth.

## Division of responsibilities

```text
LLM (teammate):
- understands natural language ("запишите меня к дерматологу завтра")
- extracts intent + entities (clinic, specialty/doctor, date, patient)
- keeps dialogue state, asks follow-up questions, handles confirmation UX
- NEVER accesses PostgreSQL / services / repositories
- NEVER calculates availability or implements scheduling rules

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
  `starts_at`/`new_starts_at` (tz-aware), `reason`, patient fields,
  `confirmed`, `context`.
- `AssistantResult`: `status` (`success` | `need_clarification` |
  `not_found` | `conflict` | `invalid_input` |
  `confirmation_required`), `code` (stable machine string),
  `message` (human-readable, may be paraphrased), `requires_confirmation`,
  `details` (JSON payloads: `slots`, `appointment`, `candidates`, …).
- `AssistantContext`: minimal conversational memory
  (`clinic_id`, `patient_id`, selected specialty/doctor/slot). Managed by
  the dialogue layer; no persistence, no Redis. Explicit request fields
  always win over context.

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
| Malformed LLM params (bad status, missing ids) | `invalid_input` / `INVALID_INPUT` |

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

## Rules the AI teammate must follow

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

No ToolRegistry/plugin framework (explicit `handle` dispatch is enough),
no conversation-history storage, no Redis, no auth, no LLM/STT/TTS SDKs.
`AssistantOrchestrator` is stateless; all state lives in the dialogue
layer (`AssistantContext`) or PostgreSQL.
