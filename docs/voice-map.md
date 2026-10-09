# Voice-assistant API map

How the HTTP API maps onto future LLM tool calls. The assistant never
touches PostgreSQL, services, or repositories — only these endpoints.
All datetimes are UTC ISO-8601 (`...Z`); `date` params are `YYYY-MM-DD`
in the **clinic's** timezone.

> In-process alternative: the AI teammate may call
> `AssistantOrchestrator.handle(session, AssistantRequest)` directly
> instead of HTTP — same services, same rules, typed
> request/result. See `docs/assistant-architecture.md` for the
> clarification/confirmation contract. The table below stays the
> contract for remote clients.

## Patient tools

| Future tool | HTTP | Required | Optional | Returns | Errors |
|---|---|---|---|---|---|
| `list_clinics` | `GET /api/v1/clinics` | — | `active_only`, `limit`, `offset` | clinics (`id`, `name`, `timezone`, …) | — |
| `get_clinic` | `GET /api/v1/clinics/{id}` | `clinic_id` (path) | — | clinic incl. `timezone` for display | `404` |
| `list_specialties` | `GET /api/v1/clinics/{id}/specialties` | `clinic_id` (path) | `active_only` | specialties (names **lowercased**) | `404` |
| `list_doctors` | `GET /api/v1/clinics/{id}/doctors` | `clinic_id` (path) | `specialty_id`, `department_id`, `active_only` | doctors **with nested specialty/department names** (disambiguation) | `404` bad filter scope |
| `get_doctor` | `GET /api/v1/doctors/{id}?clinic_id=` | `doctor_id`, `clinic_id` | — | doctor + specialty + department | `404` (incl. wrong clinic) |
| `find_available_slots` | `GET /api/v1/slots?clinic_id=&date=` | `clinic_id`, `date`, exactly one of `specialty_id`/`doctor_id` | `limit` | slots: `doctor`, `specialty`, `starts_at`, `ends_at` (UTC), `room` | `404` bad scope / no doctors, `422` bad date, `409` inactive clinic/doctor |
| `find_nearest_slots` (assistant intent + LLM tool, no separate HTTP route — loops `find_slots` server-side) | — | `clinic_id`, one of `specialty_id`/`specialty_name`/`doctor_id`/`doctor_name`, optional `date` (default tomorrow), `days_ahead` (default 10) | — | first date with `slots` + `date`, or `not_found`/`NO_SLOTS_AVAILABLE` with `checked_dates` | same as `find_slots` |
| `get_doctor_slots` | `GET /api/v1/doctors/{id}/slots?clinic_id=&date=` | same minus specialty | — | same | same |
| `create_patient` | `POST /api/v1/patients` | `clinic_id`, `full_name`, `phone` | `birth_date` (past) | patient | `404` clinic, `422` validation |
| `get_patient` | `GET /api/v1/patients/{id}?clinic_id=` | `patient_id`, `clinic_id` | — | patient | `404` |
| `create_appointment` | `POST /api/v1/appointments` | `clinic_id`, `doctor_id`, `patient_id`, `starts_at` (aware, **copied verbatim from a slot**) | `room_id`, `reason` | appointment (`ends_at` computed) + nested `doctor`/`specialty`/`patient`/`room` | `409` slot taken / inactive / past, `404` scope, `422` naive datetime |
| `get_appointment` | `GET /api/v1/appointments/{id}?clinic_id=` | `appointment_id`, `clinic_id` | — | same shape | `404` |
| `list_appointments` | `GET /api/v1/appointments?clinic_id=` | `clinic_id` + `patient_id` and/or `doctor_id` | `date_from/to`, `status`, `limit`, `offset` | list, same shape | `422` without scope filter |
| `reschedule_appointment` | `POST /api/v1/appointments/{id}/reschedule?clinic_id=` | `appointment_id`, `clinic_id`, `new_starts_at` | — | **new** appointment (old → `cancelled`) | `409` terminal state / taken slot |
| `cancel_appointment` | `POST /api/v1/appointments/{id}/cancel?clinic_id=` | `appointment_id`, `clinic_id` | — | cancelled appointment | `409` already terminal |
| `complete_appointment` | `POST /api/v1/appointments/{id}/complete?clinic_id=` | same | — | completed appointment | `409` already terminal |

## Management tools

| Future tool | HTTP | Notes |
|---|---|---|
| `create_clinic` / `update_clinic` | `POST/PATCH /api/v1/management/clinics…` | `timezone` mandatory IANA; `PATCH` also deactivates |
| `create_department` / `update_department` | `POST/PATCH /api/v1/management/departments…` | names unique per clinic |
| `create_room` / `update_room` | `POST/PATCH /api/v1/management/rooms…` | `code` unique per clinic; informational only |
| `create_specialty` / `update_specialty` | `POST/PATCH /api/v1/management/specialties…` | names stored lowercased |
| `create_doctor` / `update_doctor` | `POST/PATCH /api/v1/management/doctors…` | one specialty (required) + one department (optional); `active` toggles |
| `set_clinic_schedule` / `update_clinic_schedule` / `remove_clinic_schedule` | `POST/PATCH/DELETE …/management/clinics/{id}/schedule`, `…/clinic-schedules/{sid}` | weekly grid; `?active_only=false` reveals deactivated rows |
| `set_doctor_schedule` / `update_doctor_schedule` / `remove_doctor_schedule` | `POST/PATCH/DELETE …/management/doctors/{id}/schedules`, `…/doctor-schedules/{sid}` | fixed `slot_minutes` grid; same value per weekday enforced |
| `create_exception` / `update_exception` / `remove_exception` | `POST/PATCH/DELETE …/management/schedule-exceptions…` | `doctor_id=null` = whole clinic; `date`+`doctor` immutable on update |

## Canonical booking flow (Russian example)

```text
"Я хочу записаться к дерматологу завтра."
  1. list_clinics → patient picks clinic (ask if >1)
  2. list_specialties(clinic) → match "dermatology" (case-insensitive)
  3. find_available_slots(clinic, specialty, tomorrow)
     → "Есть Анна Смирнова в 10:00, 10:15 и 10:30."
  4. "Давай на 10:15." → create_patient (first visit) then
     create_appointment(starts_at=<exact string from step 3>)
```

## Scenario answers

- *"What doctors are available for dermatology tomorrow?"* —
  `list_specialties` → `find_available_slots(specialty_id, date)`.
- *"Where does Dr. Volkov see patients?"* —
  `list_doctors` (disambiguate by specialty/department) → `get_doctor` +
  `get_doctor_slots` (each slot carries `room {code, label}`).
- *"Which clinics have dermatologists tomorrow?"* — `list_clinics`,
  then per clinic `list_specialties` (name match) + `find_available_slots`
  (small N; no cross-clinic endpoint by design — tenant data is never
  mixed in one response).
- *Same-name doctors* — `list_doctors` returns specialty/department
  names inline; ask "Какую Анну Смирнову вы имеете в виду —
  кардиолога в Москве или в Казани?" using those fields plus `doctor_id`.

## Error cheat-sheet for the LLM

- `404` — wrong id **or** right id in the wrong `clinic_id`; treat as
  "not found", never reveal the distinction.
- `409 slot unavailable` — offer another slot from the same search.
- `409 clinic/doctor inactive` — escalate, don't retry times.
- `409 already cancelled/completed` — the booking is history; list
  appointments to show current state.
- `422` — the request itself is malformed (naive datetime, past
  `birth_date`, missing filter); fix params, don't retry verbatim.
