"""HTTP contracts: management flow, patient flow, slots, error mapping."""

from datetime import date, timedelta

from fastapi.testclient import TestClient


def _clinic_payload(name: str = "API Clinic") -> dict[str, str]:
    return {"name": name, "timezone": "Europe/Warsaw", "address": "Test 1"}


def test_management_then_patient_flow(api_client: TestClient) -> None:
    clinic = api_client.post("/api/v1/management/clinics", json=_clinic_payload()).json()
    assert clinic["timezone"] == "Europe/Warsaw"
    cid = clinic["id"]

    # Duplicate clinic name → 409.
    dup = api_client.post("/api/v1/management/clinics", json=_clinic_payload())
    assert dup.status_code == 409

    # Bad timezone → 422.
    bad_tz = api_client.post(
        "/api/v1/management/clinics",
        json={"name": "Bad TZ", "timezone": "Moon/Olympus"},
    )
    assert bad_tz.status_code == 422

    spec = api_client.post(
        "/api/v1/management/specialties",
        json={"clinic_id": cid, "name": "Dermatology"},
    ).json()
    dept = api_client.post(
        "/api/v1/management/departments",
        json={"clinic_id": cid, "name": "Outpatient"},
    ).json()
    room = api_client.post(
        "/api/v1/management/rooms",
        json={"clinic_id": cid, "department_id": dept["id"], "code": "B-201"},
    ).json()
    # Duplicate room code in the same clinic → 409.
    dup_room = api_client.post(
        "/api/v1/management/rooms",
        json={"clinic_id": cid, "code": "B-201"},
    )
    assert dup_room.status_code == 409

    doctor = api_client.post(
        "/api/v1/management/doctors",
        json={
            "clinic_id": cid,
            "full_name": "Dr. Test",
            "specialty_id": spec["id"],
            "department_id": dept["id"],
        },
    ).json()

    # Cross-clinic specialty link → 404.
    other = api_client.post(
        "/api/v1/management/clinics", json=_clinic_payload("Other Clinic")
    ).json()
    cross = api_client.post(
        "/api/v1/management/doctors",
        json={
            "clinic_id": other["id"],
            "full_name": "Dr. X",
            "specialty_id": spec["id"],
        },
    )
    assert cross.status_code == 404

    # Weekly schedules: clinic Mon-Fri, doctor Monday.
    for weekday in range(5):
        created = api_client.post(
            f"/api/v1/management/clinics/{cid}/schedule",
            json={"weekday": weekday, "start_local": "08:00", "end_local": "20:00"},
        )
        assert created.status_code == 201, created.text
    # Overlapping clinic interval → 409.
    overlap = api_client.post(
        f"/api/v1/management/clinics/{cid}/schedule",
        json={"weekday": 0, "start_local": "09:00", "end_local": "10:00"},
    )
    assert overlap.status_code == 409
    sched = api_client.post(
        f"/api/v1/management/doctors/{doctor['id']}/schedules?clinic_id={cid}",
        json={
            "weekday": 0,
            "start_local": "09:00",
            "end_local": "11:00",
            "slot_minutes": 30,
            "room_id": room["id"],
        },
    )
    assert sched.status_code == 201, sched.text

    patient = api_client.post(
        "/api/v1/patients",
        json={"clinic_id": cid, "full_name": "Jan Pacjent", "phone": "+48111111111"},
    ).json()

    # Next Monday (≥7 days out so it is never in the past).
    today = date.today()
    delta = (0 - today.weekday()) % 7 or 7
    while delta < 7:
        delta += 7
    monday = today + timedelta(days=delta)

    slots = api_client.get(
        f"/api/v1/slots?clinic_id={cid}&date={monday}&specialty_id={spec['id']}"
    ).json()
    assert len(slots) == 4
    assert slots[0]["room"]["code"] == "B-201"

    booking = api_client.post(
        "/api/v1/appointments",
        json={
            "clinic_id": cid,
            "doctor_id": doctor["id"],
            "patient_id": patient["id"],
            "starts_at": slots[0]["starts_at"],
        },
    )
    assert booking.status_code == 201, booking.text
    appointment_id = booking.json()["id"]

    # Same instant again → 409.
    again = api_client.post(
        "/api/v1/appointments",
        json={
            "clinic_id": cid,
            "doctor_id": doctor["id"],
            "patient_id": patient["id"],
            "starts_at": slots[0]["starts_at"],
        },
    )
    assert again.status_code == 409

    # Doctor slots endpoint reflects the booking (one fewer).
    remaining = api_client.get(
        f"/api/v1/doctors/{doctor['id']}/slots?clinic_id={cid}&date={monday}"
    ).json()
    assert len(remaining) == 3

    # Patient appointment list requires a scope filter → 422 without it.
    assert api_client.get(f"/api/v1/appointments?clinic_id={cid}").status_code == 422
    mine = api_client.get(f"/api/v1/appointments?clinic_id={cid}&patient_id={patient['id']}").json()
    assert [a["id"] for a in mine] == [appointment_id]

    # Reschedule → cancel → rebook freed slot.
    moved = api_client.post(
        f"/api/v1/appointments/{appointment_id}/reschedule?clinic_id={cid}",
        json={"new_starts_at": slots[1]["starts_at"]},
    )
    assert moved.status_code == 200, moved.text
    cancelled = api_client.post(f"/api/v1/appointments/{moved.json()['id']}/cancel?clinic_id={cid}")
    assert cancelled.status_code == 200
    # Double cancel → 409.
    assert (
        api_client.post(
            f"/api/v1/appointments/{moved.json()['id']}/cancel?clinic_id={cid}"
        ).status_code
        == 409
    )
    freed = api_client.post(
        "/api/v1/appointments",
        json={
            "clinic_id": cid,
            "doctor_id": doctor["id"],
            "patient_id": patient["id"],
            "starts_at": slots[0]["starts_at"],
        },
    )
    assert freed.status_code == 201

    # Cross-tenant read → 404.
    assert (
        api_client.get(f"/api/v1/doctors/{doctor['id']}?clinic_id={other['id']}").status_code == 404
    )
    assert (
        api_client.get(f"/api/v1/patients/{patient['id']}?clinic_id={other['id']}").status_code
        == 404
    )

    # Day off → no slots; delete → slots back (Scenario F over HTTP).
    exc = api_client.post(
        "/api/v1/management/schedule-exceptions",
        json={
            "clinic_id": cid,
            "doctor_id": doctor["id"],
            "date": str(monday),
            "kind": "day_off",
        },
    )
    assert exc.status_code == 201, exc.text
    assert (
        api_client.get(f"/api/v1/doctors/{doctor['id']}/slots?clinic_id={cid}&date={monday}").json()
        == []
    )
    delete = api_client.delete(
        f"/api/v1/management/schedule-exceptions/{exc.json()['id']}?clinic_id={cid}"
    )
    assert delete.status_code == 204
    back = api_client.get(
        f"/api/v1/doctors/{doctor['id']}/slots?clinic_id={cid}&date={monday}"
    ).json()
    assert len(back) == 3  # one slot still booked by the re-booked appointment


def test_public_catalog_and_health(api_client: TestClient) -> None:
    assert api_client.get("/health").json() == {"status": "ok"}
    clinic = api_client.post(
        "/api/v1/management/clinics", json=_clinic_payload("Catalog Clinic")
    ).json()
    cid = clinic["id"]
    listed = api_client.get("/api/v1/clinics").json()
    assert any(c["id"] == cid for c in listed)
    assert api_client.get(f"/api/v1/clinics/{cid}").json()["name"] == "Catalog Clinic"
    assert api_client.get(f"/api/v1/clinics/{cid}/doctors").json() == []
    assert api_client.get("/api/v1/clinics/999999").status_code == 404


def test_voice_nesting_and_patch_endpoints(api_client: TestClient) -> None:
    """Doctor list carries specialty names; appointments carry context; PATCH works."""
    clinic = api_client.post(
        "/api/v1/management/clinics", json=_clinic_payload("Nesting Clinic")
    ).json()
    cid = clinic["id"]
    spec = api_client.post(
        "/api/v1/management/specialties",
        json={"clinic_id": cid, "name": "Cardiology"},
    ).json()
    dept = api_client.post(
        "/api/v1/management/departments",
        json={"clinic_id": cid, "name": "Internal"},
    ).json()
    doctor = api_client.post(
        "/api/v1/management/doctors",
        json={
            "clinic_id": cid,
            "full_name": "Jan Kowalski",
            "specialty_id": spec["id"],
            "department_id": dept["id"],
        },
    ).json()

    # Doctor list includes nested specialty/department (disambiguation).
    doctors = api_client.get(f"/api/v1/clinics/{cid}/doctors").json()
    assert doctors[0]["specialty"]["name"] == "cardiology"
    assert doctors[0]["department"]["name"] == "Internal"

    for weekday in range(5):
        api_client.post(
            f"/api/v1/management/clinics/{cid}/schedule",
            json={"weekday": weekday, "start_local": "08:00", "end_local": "20:00"},
        )
    sched = api_client.post(
        f"/api/v1/management/doctors/{doctor['id']}/schedules?clinic_id={cid}",
        json={
            "weekday": 0,
            "start_local": "09:00",
            "end_local": "11:00",
            "slot_minutes": 30,
        },
    ).json()

    # PATCH doctor schedule: shrink the interval.
    patched = api_client.patch(
        f"/api/v1/management/doctor-schedules/{sched['id']}?clinic_id={cid}",
        json={"end_local": "10:00"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["end_local"] == "10:00:00"

    # PATCH clinic schedule: deactivate Monday → no slots that day.
    week = api_client.get(f"/api/v1/management/clinics/{cid}/schedule").json()
    monday_row = next(r for r in week if r["weekday"] == 0)
    off = api_client.patch(
        f"/api/v1/management/clinic-schedules/{monday_row['id']}?clinic_id={cid}",
        json={"active": False},
    )
    assert off.status_code == 200

    patient = api_client.post(
        "/api/v1/patients",
        json={"clinic_id": cid, "full_name": "Voice Patient", "phone": "+48222222222"},
    ).json()
    today = date.today()
    delta = (0 - today.weekday()) % 7 or 7
    while delta < 7:
        delta += 7
    monday = today + timedelta(days=delta)
    empty = api_client.get(
        f"/api/v1/doctors/{doctor['id']}/slots?clinic_id={cid}&date={monday}"
    ).json()
    assert empty == []

    # Reactivate → slots back.
    on = api_client.patch(
        f"/api/v1/management/clinic-schedules/{monday_row['id']}?clinic_id={cid}",
        json={"active": True},
    )
    assert on.status_code == 200
    slots = api_client.get(
        f"/api/v1/doctors/{doctor['id']}/slots?clinic_id={cid}&date={monday}"
    ).json()
    assert len(slots) == 2  # 09:00-10:00 @30 after the shrink

    booking = api_client.post(
        "/api/v1/appointments",
        json={
            "clinic_id": cid,
            "doctor_id": doctor["id"],
            "patient_id": patient["id"],
            "starts_at": slots[0]["starts_at"],
            "reason": "   ",
        },
    )
    assert booking.status_code == 201, booking.text
    body = booking.json()
    # Nested voice context + whitespace-only reason normalized to null.
    assert body["doctor"]["full_name"] == "Jan Kowalski"
    assert body["specialty"]["name"] == "cardiology"
    assert body["patient"]["full_name"] == "Voice Patient"
    assert body["reason"] is None
    assert body["starts_at"].endswith("Z") or "+" in body["starts_at"]

    # Exception PATCH: create custom hours, switch to day off.
    exc = api_client.post(
        "/api/v1/management/schedule-exceptions",
        json={
            "clinic_id": cid,
            "doctor_id": doctor["id"],
            "date": str(monday),
            "kind": "custom_hours",
            "start_local": "12:00",
            "end_local": "14:00",
        },
    )
    assert exc.status_code == 201, exc.text
    eid = exc.json()["id"]
    switched = api_client.patch(
        f"/api/v1/management/schedule-exceptions/{eid}?clinic_id={cid}",
        json={"kind": "day_off"},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["kind"] == "day_off"
    assert switched.json()["start_local"] is None
    gone = api_client.get(
        f"/api/v1/doctors/{doctor['id']}/slots?clinic_id={cid}&date={monday}"
    ).json()
    assert gone == []
