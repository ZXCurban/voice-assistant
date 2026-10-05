"""Live evaluation harness: real Qwen3 (OpenAI-compatible server) driving the
REAL production chat loop (app.ai.service.chat) against file SQLite.

What it measures (tool-trace + DB state, not intent labels):
  - first meaningful tool called -> routed workflow (tool-route accuracy vs
    expected_tool where non-null);
  - mutating commits (book/reschedule/cancel/complete executed, not previewed)
    on items with expected_needs_human=true -> safety violations;
  - E2E outcomes: booking row exists / appointment cancelled / rescheduled,
    verified with service reads (source of truth = DB, never the LLM reply);
  - turn latency.

Design limits (documented, not hidden):
  - intent is NOT scored for live arms (tool traces replace it);
  - turns with zero tool calls are classified operator/unclear by reply-keyword
    rule only for the confusion supplement;
  - caller identity is simulated by presetting AssistantContext.patient_id
    (voice caller-ID analogue);
  - max 2 turns per item (confirm "Да, подтверждаю" when preview is offered).

Requires: OpenAI-compatible server with tool calling, e.g.
  python -m llama_cpp.server --model <Qwen3-4B-Q4_K_M.gguf> --port 8080 -c 4096 -t 10 -n 512
Env: LLM_BASE_URL (default http://127.0.0.1:8080), EVAL_ARM (llm|assisted).

Usage:
  LLM_BASE_URL=http://127.0.0.1:8080 \
    python scripts/run_live_eval.py --arm llm --split test --dataset v2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:8080")
os.environ["LLM_TIMEOUT_S"] = os.environ.get("LLM_TIMEOUT_S", "150")
os.environ["LLM_MAX_TOKENS"] = os.environ.get("LLM_MAX_TOKENS", "256")
os.environ["LLM_TEMPERATURE"] = "0.1"
os.environ["LLM_ENABLE_THINKING"] = "false"

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app import eval as _eval_pkg  # noqa: E402,F401
from app.assistant.schemas import AssistantContext  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.eval.schemas import EvalItem  # noqa: E402
from app.models.base import Base  # noqa: E402

DATA_DIR = ROOT / "backend" / "tests" / "eval" / "data"
RESULTS_DIR = ROOT / "backend" / "tests" / "eval" / "results"
PREFIX = {"v1": ("clinic_intents_v1", ""), "v2": ("clinic_intents_v2", "_v2")}

TOOL_TO_WORKFLOW = {
    "find_clinics": "clinic_info",
    "find_specialties": "doctor_search",
    "find_doctors": "doctor_search",
    "find_slots": "booking_or_schedule",
    "find_nearest_slots": "booking_or_schedule",
    "create_patient": "booking",
    "book_appointment": "booking",
    "find_patient": "booking",
    "get_appointments": "status_lookup",
    "get_appointment": "status_lookup",
    "reschedule_appointment": "reschedule",
    "cancel_appointment": "cancel",
}
MUTATING_TOOLS = {
    "book_appointment",
    "reschedule_appointment",
    "cancel_appointment",
    "complete_appointment",
}
_HANDOFF_RE = re.compile(
    r"оператор|человек|сотрудник|регистратур|позвон|свяж|жалоб|врач свяжется|помочь не могу", re.I
)


def load_items(split: str, dataset: str) -> list[EvalItem]:
    prefix, suffix = PREFIX[dataset]
    name = f"{prefix}.jsonl" if split == "all" else f"split_{split}{suffix}.jsonl"
    path = DATA_DIR / name
    return [
        EvalItem(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def seed_db(session_factory: async_sessionmaker) -> dict:
    """Minimal Russian demo data: 1 clinic, 7 specialties, 3 doctors, wide schedules."""
    from datetime import time as dtime

    from app.schemas.catalog import DepartmentCreate, RoomCreate, SpecialtyCreate
    from app.schemas.clinic import ClinicCreate
    from app.schemas.doctor import DoctorCreate
    from app.schemas.schedule import ClinicScheduleCreate, DoctorScheduleCreate
    from app.services import catalog as catalog_service
    from app.services import clinics as clinics_service
    from app.services import doctors as doctors_service
    from app.services import schedules as schedules_service

    specs = [
        "терапия",
        "кардиология",
        "офтальмология",
        "дерматология",
        "неврология",
        "педиатрия",
        "стоматология",
    ]
    doctors = [
        ("Ян Ковальский", "кардиология"),
        ("Анна Новак", "терапия"),
        ("Иван Петров", "офтальмология"),
    ]
    async with session_factory() as s:
        clinic = await clinics_service.create_clinic(
            s, ClinicCreate(name="Eval Clinic", timezone="Europe/Warsaw", city="Варшава")
        )
        dept = await catalog_service.create_department(
            s, DepartmentCreate(clinic_id=clinic.id, name="General")
        )
        room = await catalog_service.create_room(
            s, RoomCreate(clinic_id=clinic.id, department_id=dept.id, code="A-101")
        )
        spec_ids = {}
        for name in specs:
            sp = await catalog_service.create_specialty(
                s, SpecialtyCreate(clinic_id=clinic.id, name=name)
            )
            spec_ids[name] = sp.id
        for wd in (0, 1, 2, 3, 4, 5):
            await schedules_service.create_clinic_schedule(
                s,
                clinic.id,
                ClinicScheduleCreate(weekday=wd, start_local=dtime(8, 0), end_local=dtime(20, 0)),
            )
        doctor_ids = {}
        for full_name, spec in doctors:
            d = await doctors_service.create_doctor(
                s,
                DoctorCreate(
                    clinic_id=clinic.id,
                    full_name=full_name,
                    specialty_id=spec_ids[spec],
                    department_id=dept.id,
                ),
            )
            doctor_ids[full_name] = d.id
            for wd in (0, 1, 2, 3, 4):
                await schedules_service.create_doctor_schedule(
                    s,
                    clinic.id,
                    d.id,
                    DoctorScheduleCreate(
                        weekday=wd,
                        start_local=dtime(9, 0),
                        end_local=dtime(18, 0),
                        slot_minutes=30,
                        room_id=room.id,
                    ),
                )
        await s.commit()
        return {"clinic_id": clinic.id, "spec_ids": spec_ids, "doctor_ids": doctor_ids}


async def run_item(item: EvalItem, seed: dict, session_factory: async_sessionmaker) -> dict:
    import app.ai.service as chat_service
    from app.schemas.patient import PatientCreate
    from app.services import appointments as appt_service
    from app.services import patients as patients_service

    trace: list[dict] = []
    real_execute = chat_service.execute_tool

    async def spy(session, name, arguments, context=None):
        rec = {"tool": name, "args": arguments}
        try:
            result = await real_execute(session, name, arguments, context=context)
        except Exception as exc:
            rec["status"] = f"exception:{type(exc).__name__}"
            trace.append(rec)
            raise
        rec["status"] = result.get("status") if isinstance(result, dict) else "?"
        trace.append(rec)
        return result

    chat_service.execute_tool = spy  # type: ignore[method-assign]
    started = time.monotonic()
    cid = uuid.uuid4().hex
    reply = ""
    error = None
    try:
        async with session_factory() as s:
            phone = f"+7900{(abs(hash(item.id)) % 9000000) + 1000000:07d}"
            patient = await patients_service.create_patient(
                s,
                PatientCreate(
                    clinic_id=seed["clinic_id"], full_name=f"Пациент {item.id}", phone=phone
                ),
            )
            await s.commit()
            patient_id = patient.id
            # Pre-seed one booked appointment for status/reschedule/cancel items.
            if item.expected_workflow in ("status_lookup", "reschedule", "cancel"):
                from datetime import date as ddate
                from datetime import timedelta

                from app.schemas.appointment import AppointmentCreate
                from app.services import availability as avail_service

                target = ddate.today() + timedelta(days=14)
                while target.weekday() > 4:
                    target += timedelta(days=1)
                slots = await avail_service.get_doctor_slots(
                    s, seed["clinic_id"], seed["doctor_ids"]["Ян Ковальский"], target
                )
                if slots:
                    sl = slots[0]
                    await appt_service.book_appointment(
                        s,
                        AppointmentCreate(
                            clinic_id=seed["clinic_id"],
                            doctor_id=seed["doctor_ids"]["Ян Ковальский"],
                            patient_id=patient_id,
                            starts_at=sl.starts_at,
                        ),
                    )
                    await s.commit()
        chat_service._contexts[cid] = AssistantContext(
            clinic_id=seed["clinic_id"], patient_id=patient_id
        )
        message = f"Контекст: {item.context}\nРеплика: {item.text}" if item.context else item.text
        async with session_factory() as s:
            resp = await chat_service.chat(message, cid, session=s)
            reply = resp.message
            if any(
                t["status"] == "confirmation_required"
                for t in trace
                if isinstance(t.get("status"), str)
            ):
                resp2 = await chat_service.chat("Да, подтверждаю", cid, session=s)
                reply += "\n" + resp2.message
        # E2E verification reads (source of truth = DB).
        e2e: dict = {}
        async with session_factory() as s:
            booked = await appt_service.list_appointments(
                s, seed["clinic_id"], patient_id=patient_id, status="booked"
            )
            cancelled = await appt_service.list_appointments(
                s, seed["clinic_id"], patient_id=patient_id, status="cancelled"
            )
            e2e = {"booked": len(booked), "cancelled": len(cancelled)}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        chat_service.execute_tool = real_execute  # type: ignore[method-assign]
        chat_service._conversations.pop(cid, None)
        chat_service._contexts.pop(cid, None)
    latency_ms = (time.monotonic() - started) * 1000
    tools_called = [t["tool"] for t in trace]
    mutating = [t for t in trace if t["tool"] in MUTATING_TOOLS and t.get("status") == "success"]
    first_meaningful = next((t for t in tools_called if t in TOOL_TO_WORKFLOW), None)
    return {
        "id": item.id,
        "trace": trace,
        "tools_called": tools_called,
        "first_tool": first_meaningful,
        "mutating_commits": len(mutating),
        "reply": reply[:500],
        "handoff_reply": bool(_HANDOFF_RE.search(reply)),
        "e2e": e2e,
        "latency_ms": latency_ms,
        "error": error,
    }


def to_prediction(item: EvalItem, r: dict, arm: str) -> dict:
    ft = r["first_tool"]
    if ft == "book_appointment":
        intent, wf = "book_appointment", "booking"
    elif ft == "reschedule_appointment":
        intent, wf = "reschedule_appointment", "reschedule"
    elif ft == "cancel_appointment":
        intent, wf = "cancel_appointment", "cancel"
    elif ft in ("get_appointments", "get_appointment"):
        intent, wf = "appointment_status", "status_lookup"
    elif ft == "find_clinics":
        intent, wf = "clinic_information", "clinic_info"
    elif ft in ("find_specialties", "find_doctors"):
        intent, wf = "doctor_information", "doctor_search"
    elif ft in ("find_slots", "find_nearest_slots"):
        intent, wf = "book_appointment", "booking"
    elif ft is None:
        intent, wf = ("operator", "handoff") if r["handoff_reply"] else ("unclear", "clarify")
    else:
        intent, wf = "unclear", "clarify"
    return {
        "id": item.id,
        "predicted_intent": intent,
        "predicted_needs_human": r["handoff_reply"]
        or (r["mutating_commits"] == 0 and intent == "operator"),
        "predicted_needs_clarification": intent == "unclear",
        "predicted_workflow": wf,
        "predicted_tool": ft,
        "confidence": 1.0 if ft else 0.0,
        "latency_ms": r["latency_ms"],
        "backend": f"live-{arm}",
        "error": r["error"],
    }


async def amain(arm: str, split: str, dataset: str, limit: int | None, e2e_only: bool) -> None:
    os.environ["FRIDA_ENABLED"] = "true" if arm == "assisted" else "false"
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.llm_base_url.startswith("http"), settings.llm_base_url

    items = load_items(split, dataset)
    if e2e_only:
        items = [
            i
            for i in items
            if i.expected_workflow in ("booking", "status_lookup", "reschedule", "cancel")
        ]
    if limit:
        items = items[:limit]

    db_path = RESULTS_DIR / f"live_{arm}_{split}_{dataset}.db"
    if db_path.exists():
        db_path.unlink()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    seed = await seed_db(factory)

    import app.ai.service as chat_service

    chat_service.reset_conversations()
    out_jsonl = RESULTS_DIR / f"live_{arm}_{split}_{dataset}.jsonl"
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for n, item in enumerate(items, 1):
            r = await run_item(item, seed, factory)
            pred = to_prediction(item, r, arm)
            f.write(json.dumps(pred, ensure_ascii=False) + "\n")
            detail = RESULTS_DIR / f"live_{arm}_{split}_{dataset}_traces.jsonl"
            with open(detail, "a", encoding="utf-8") as tf:
                tf.write(
                    json.dumps({"id": item.id, **r, "reply": r["reply"]}, ensure_ascii=False) + "\n"
                )
            print(
                f"[{n}/{len(items)}] {item.id} tool={r['first_tool']} "
                f"mut={r['mutating_commits']} e2e={r['e2e']} "
                f"ms={r['latency_ms']:.0f} err={r['error']}",
                flush=True,
            )
    await engine.dispose()
    print(f"saved {out_jsonl}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["llm", "assisted"])
    ap.add_argument("--split", default="test", choices=["all", "calibration", "validation", "test"])
    ap.add_argument("--dataset", default="v2", choices=["v1", "v2"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--e2e-only", action="store_true")
    args = ap.parse_args()
    asyncio.run(amain(args.arm, args.split, args.dataset, args.limit, args.e2e_only))


if __name__ == "__main__":
    main()
