"""Production boundary for signed tenant and user claims from channel gateways."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings, is_production
from app.core.identity import TrustedChannelContext, decode_signed_context

MAX_JSON_BODY = 1024 * 1024


class AuthorizationDeniedError(Exception):
    """A valid principal attempted an operation outside its role."""


class TenantScopeMismatchError(Exception):
    """A request addresses another tenant; avoid exposing tenant existence."""


class MalformedRequestError(ValueError):
    """The HTTP framing itself is unreadable (bad length, aborted body)."""


class TrustedChannelContextMiddleware:
    """Authenticate versioned API calls and enforce the signed clinic scope.

    Local/demo mode deliberately remains unauthenticated and must use synthetic
    seed data. Production accepts only short-lived HMAC-signed gateway claims.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not is_production(self.settings)
            or not scope["path"].startswith("/api/v1/")
        ):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        try:
            context = decode_signed_context(
                headers[b"x-trusted-channel-context"].decode("ascii"),
                headers[b"x-trusted-channel-signature"].decode("ascii"),
                self.settings.channel_context_secret or "",
            )
            body, buffered_messages = await self._read_body(receive, headers)
            path = scope["path"]
            method = scope["method"].upper()
            tenant_id = self._tenant_id(path, scope.get("query_string", b""), body)
            self._authorize(context, path, method, tenant_id)
        except TenantScopeMismatchError:
            await self._respond(scope, send, 404, "Not found")
            return
        except AuthorizationDeniedError:
            await self._respond(scope, send, 403, "Forbidden")
            return
        except MalformedRequestError:
            await self._respond(scope, send, 400, "Malformed request")
            return
        except (KeyError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            await self._respond(scope, send, 401, "Invalid trusted channel context")
            return
        except OverflowError:
            await self._respond(scope, send, 413, "Request body is too large")
            return

        scope.setdefault("state", {})["trusted_context"] = context
        message_index = 0

        async def replay_receive() -> Message:
            nonlocal message_index
            if message_index < len(buffered_messages):
                message = buffered_messages[message_index]
                message_index += 1
                return message
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _read_body(
        receive: Receive, headers: dict[bytes, bytes]
    ) -> tuple[dict[str, object], list[Message]]:
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError as exc:
                raise MalformedRequestError(f"bad content-length: {content_length!r}") from exc
            if declared > MAX_JSON_BODY:
                raise OverflowError
        messages: list[Message] = []
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise MalformedRequestError("client disconnected mid-body")
            messages.append(message)
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > MAX_JSON_BODY:
                raise OverflowError
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body: dict[str, object] = {}
        content_type = headers.get(b"content-type", b"").split(b";", 1)[0]
        if chunks and content_type == b"application/json":
            raw = b"".join(chunks)
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    body = parsed
        return body, messages

    @staticmethod
    def _tenant_id(path: str, query_string: bytes, body: dict[str, object]) -> int | None:
        query = parse_qs(query_string.decode("ascii", errors="ignore"))
        raw = query.get("clinic_id", [None])[0]
        body_id = body.get("clinic_id")
        if raw is None and isinstance(body_id, int):
            raw = str(body_id)
        if raw is None:
            parts = path.strip("/").split("/")
            if "clinics" in parts:
                index = parts.index("clinics") + 1
                if index < len(parts) and parts[index].isdigit():
                    raw = parts[index]
        try:
            tenant_id = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None
        return tenant_id if tenant_id is not None and tenant_id > 0 else None

    @staticmethod
    def _authorize(
        context: TrustedChannelContext, path: str, method: str, tenant_id: int | None
    ) -> None:
        management = "/management/" in path
        public_clinic_list = path == "/api/v1/clinics" and method == "GET"
        if path == "/api/v1/chat" and tenant_id is None:
            tenant_id = context.clinic_id

        if context.role == "platform_admin":
            return
        if management:
            if context.role != "clinic_admin" or tenant_id is None:
                raise AuthorizationDeniedError
        elif context.role == "patient":
            if path.startswith(("/api/v1/patients", "/api/v1/appointments")):
                raise AuthorizationDeniedError
            if tenant_id is None and not public_clinic_list:
                raise AuthorizationDeniedError
        if tenant_id is not None and tenant_id != context.clinic_id:
            raise TenantScopeMismatchError

    @staticmethod
    async def _respond(scope: Scope, send: Send, status: int, detail: str) -> None:
        from starlette.responses import JSONResponse

        response = JSONResponse(status_code=status, content={"detail": detail})
        await response(scope, _empty_receive, send)


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}
