"""LLM tool definitions executed through AssistantOrchestrator.

Model-neutral OpenAI function-calling schemas: no model-specific tokens
or prompt tricks here, so the same registry works with any
OpenAI-compatible model (see app.ai.client.build_client). Every call is
validated as an AssistantRequest and handled by the existing services
layer — the model never touches the database and never invents ids,
slots or bookings.
"""

import logging
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.orchestrator import AssistantOrchestrator
from app.assistant.schemas import AssistantContext, AssistantRequest

logger = logging.getLogger(__name__)

_orchestrator = AssistantOrchestrator()


def _fn(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOLS: list[dict[str, Any]] = [
    _fn(
        "find_clinics",
        "Список клиник сети. Если пользователь назвал город/адрес — "
        "передай его в city/address, вернётся отсортированный список "
        "(ближайшие сначала).",
        {
            "city": {"type": "string"},
            "address": {"type": "string"},
        },
        [],
    ),
    _fn(
        "find_specialties",
        "Специальности одной клиники.",
        {"clinic_id": {"type": "integer"}},
        ["clinic_id"],
    ),
    _fn(
        "find_doctors",
        "Врачи клиники, опционально по специальности или имени.",
        {
            "clinic_id": {"type": "integer"},
            "specialty_name": {"type": "string"},
            "doctor_name": {"type": "string"},
        },
        ["clinic_id"],
    ),
    _fn(
        "find_slots",
        "Свободные окна на ТОЧНУЮ дату (YYYY-MM-DD). Нужна специальность ИЛИ врач.",
        {
            "clinic_id": {"type": "integer"},
            "date": {"type": "string"},
            "specialty_name": {"type": "string"},
            "doctor_name": {"type": "string"},
            "specialty_id": {"type": "integer"},
            "doctor_id": {"type": "integer"},
        },
        ["clinic_id", "date"],
    ),
    _fn(
        "find_nearest_slots",
        "БЛИЖАЙШЕЕ окно: сам проверяет 10 дней начиная с date (по умолчанию "
        "с завтра) и возвращает первый день со слотами. Вызывать ОДИН раз "
        "вместо цикла find_slots. Нужна специальность ИЛИ врач.",
        {
            "clinic_id": {"type": "integer"},
            "date": {"type": "string"},
            "specialty_name": {"type": "string"},
            "doctor_name": {"type": "string"},
            "specialty_id": {"type": "integer"},
            "doctor_id": {"type": "integer"},
        },
        ["clinic_id"],
    ),
    _fn(
        "create_patient",
        "Зарегистрировать пациента в клинике. Вернёт patient_id.",
        {
            "clinic_id": {"type": "integer"},
            "full_name": {"type": "string"},
            "phone": {"type": "string"},
        },
        ["clinic_id", "full_name", "phone"],
    ),
    _fn(
        "book_appointment",
        "Запись в ТОЧНЫЙ слот из find_slots (starts_at дословно). Пациент: "
        "patient_id, а если неизвестен — full_name+phone (найдётся или "
        "зарегистрируется сам, выдумывать id ЗАПРЕЩЕНО). Снаряди "
        "confirmed=false, покажи превью, спроси «да»; после «да» повтори "
        "с confirmed=true.",
        {
            "clinic_id": {"type": "integer"},
            "patient_id": {"type": "integer"},
            "full_name": {"type": "string"},
            "phone": {"type": "string"},
            "starts_at": {"type": "string"},
            "doctor_id": {"type": "integer"},
            "doctor_name": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
        ["clinic_id", "starts_at"],
    ),
    _fn(
        "find_patient",
        "Найти пациента клиники по телефону (для «моих записей»).",
        {
            "clinic_id": {"type": "integer"},
            "phone": {"type": "string"},
        },
        ["clinic_id", "phone"],
    ),
    _fn(
        "get_appointments",
        "Записи пациента или врача в клинике.",
        {
            "clinic_id": {"type": "integer"},
            "patient_id": {"type": "integer"},
            "doctor_id": {"type": "integer"},
            "appointment_status": {"type": "string"},
        },
        ["clinic_id"],
    ),
    _fn(
        "reschedule_appointment",
        "Перенос в слот из find_slots. Подтверждение как в book_appointment.",
        {
            "clinic_id": {"type": "integer"},
            "appointment_id": {"type": "integer"},
            "new_starts_at": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
        ["clinic_id", "appointment_id", "new_starts_at"],
    ),
    _fn(
        "cancel_appointment",
        "Отменить запись. Сначала confirmed=false (превью), после явного «да» — confirmed=true.",
        {
            "clinic_id": {"type": "integer"},
            "appointment_id": {"type": "integer"},
            "confirmed": {"type": "boolean"},
        },
        ["clinic_id", "appointment_id"],
    ),
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


# How many slots the model sees: enough for a choice, small enough
# to fit the local model's context window and match the "2–4 options" rule.
MAX_SLOTS_FOR_MODEL = 5


async def execute_tool(
    session: AsyncSession,
    name: str,
    arguments: dict[str, Any],
    context: AssistantContext | None = None,
    *,
    limit_slots: bool = True,
) -> dict[str, Any]:
    """Validate args, run through the orchestrator, return JSON-safe result.

    Sticky conversation context fills ids the model omitted; explicit
    arguments always win (enforced by the orchestrator). `limit_slots`
    caps slot lists for the LLM's context window; the deterministic
    dialogue layer passes False and filters/pages the full list itself.
    """
    if name not in TOOL_NAMES:
        return {
            "status": "invalid_input",
            "code": "UNKNOWN_TOOL",
            "message": f"Unknown tool: {name}.",
            "details": {},
        }
    try:
        request = AssistantRequest(intent=name, context=context, **arguments)  # type: ignore[arg-type]
    except ValidationError as exc:
        logger.info("tool %s invalid args=%s", name, arguments)
        return {
            "status": "invalid_input",
            "code": "INVALID_TOOL_ARGUMENTS",
            "message": (
                "Исправь указанные параметры и повтори вызов, не показывая эту ошибку пользователю."
            ),
            "details": {"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
        }
    result = await _orchestrator.handle(session, request)
    logger.info("tool %s args=%s -> %s/%s", name, arguments, result.status, result.code)
    payload = result.model_dump(mode="json")
    if limit_slots and name in ("find_slots", "find_nearest_slots"):
        slots = (payload.get("details") or {}).get("slots") or []
        if len(slots) > MAX_SLOTS_FOR_MODEL:
            payload["details"]["slots"] = slots[:MAX_SLOTS_FOR_MODEL]
            payload["details"]["showing_first"] = MAX_SLOTS_FOR_MODEL
            payload["details"]["total"] = len(slots)
    return payload
