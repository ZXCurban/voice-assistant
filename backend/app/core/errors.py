"""Domain exceptions. Services raise these; the API layer maps them to HTTP."""

from dataclasses import dataclass


@dataclass
class NotFoundError(Exception):
    """Entity missing or outside the requested clinic scope (→ 404)."""

    message: str = "not found"


@dataclass
class ConflictError(Exception):
    """Business-rule conflict: slot taken, inactive entity, bad transition (→ 409)."""

    message: str = "conflict"
