"""Reproducible voice-flow demo against a running API (stdlib only).

Assumes: `docker compose up -d db` (or local PG), migrations applied,
seed loaded, API on http://localhost:8000.

    make demo

Flow: clinic -> specialty -> doctor -> slots -> patient -> book ->
reschedule -> cancel, plus Clinic B isolation proof (same doctor name,
same room code, different data).
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


def call(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read().decode() or "null"
            return resp.status, json.loads(payload)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:300]


def check(label: str, cond: bool, detail: object = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {label} {detail if not cond else ''}")
    if not cond:
        sys.exit(1)


def main() -> None:
    status, clinics = call("GET", "/api/v1/clinics")
    check("list clinics", status == 200 and len(clinics) >= 2, clinics)
    assert isinstance(clinics, list)
    clinic_a = next(c for c in clinics if c["name"] == "Przychodnia Srodmiescie")
    clinic_b = next(c for c in clinics if c["name"] == "Clinica Atlantica")
    cid = clinic_a["id"]
    print(f"Clinic A id={cid} tz={clinic_a['timezone']}")

    _, specs = call("GET", f"/api/v1/clinics/{cid}/specialties")
    assert isinstance(specs, list)
    derm = next(s for s in specs if s["name"] == "dermatology")
    _, doctors = call(
        "GET", f"/api/v1/clinics/{cid}/doctors?specialty_id={derm['id']}"
    )
    assert isinstance(doctors, list)
    doctor = doctors[0]
    print(f"Doctor: {doctor['full_name']} ({doctor['specialty']['name']})")
    check("doctor nesting", doctor["specialty"]["name"] == "dermatology", doctor)

    target_date = None
    slots = []
    for ahead in range(1, 15):
        day = date.today() + timedelta(days=ahead)
        status, found = call(
            "GET",
            f"/api/v1/slots?clinic_id={cid}&date={day}&specialty_id={derm['id']}",
        )
        assert isinstance(found, list)
        if status == 200 and len(found) >= 2:
            target_date, slots = day, found
            break
    check("slots found", bool(slots), (status, target_date))
    print(f"First slots on {target_date}: {[s['starts_at'] for s in slots[:3]]}")

    status, patient = call(
        "POST",
        "/api/v1/patients",
        {"clinic_id": cid, "full_name": "Demo Voice", "phone": "+48000000001"},
    )
    check("patient created", status == 201, (status, patient))
    assert isinstance(patient, dict)

    status, booking = call(
        "POST",
        "/api/v1/appointments",
        {
            "clinic_id": cid,
            "doctor_id": doctor["id"],
            "patient_id": patient["id"],
            "starts_at": slots[0]["starts_at"],
        },
    )
    check("booked", status == 201, (status, booking))
    assert isinstance(booking, dict)
    check("nested doctor", booking["doctor"]["full_name"] == doctor["full_name"], booking)

    status, _ = call(
        "POST",
        "/api/v1/appointments",
        {
            "clinic_id": cid,
            "doctor_id": doctor["id"],
            "patient_id": patient["id"],
            "starts_at": slots[0]["starts_at"],
        },
    )
    check("double booking rejected (409)", status == 409, status)

    status, moved = call(
        "POST",
        f"/api/v1/appointments/{booking['id']}/reschedule?clinic_id={cid}",
        {"new_starts_at": slots[1]["starts_at"]},
    )
    check("rescheduled", status == 200, (status, moved))
    assert isinstance(moved, dict)

    status, _ = call(
        "POST", f"/api/v1/appointments/{moved['id']}/cancel?clinic_id={cid}"
    )
    check("cancelled", status == 200, status)

    # Clinic B isolation: same names, different records.
    _, rooms_b = call("GET", f"/api/v1/clinics/{clinic_b['id']}/rooms")
    assert isinstance(rooms_b, list)
    codes_b = {r["code"] for r in rooms_b}
    _, rooms_a = call("GET", f"/api/v1/clinics/{cid}/rooms")
    assert isinstance(rooms_a, list)
    check("room A-101 exists in both, different ids", "A-101" in codes_b, rooms_b)
    id_a = next(r["id"] for r in rooms_a if r["code"] == "A-101")
    id_b = next(r["id"] for r in rooms_b if r["code"] == "A-101")
    check("room ids differ per clinic", id_a != id_b, (id_a, id_b))

    status, _ = call("GET", f"/api/v1/doctors/{doctor['id']}?clinic_id={clinic_b['id']}")
    check("cross-tenant doctor -> 404", status == 404, status)

    print("\nDemo OK: voice flow works end-to-end on live data.")


if __name__ == "__main__":
    main()
