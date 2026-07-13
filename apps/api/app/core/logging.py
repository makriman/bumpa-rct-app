import json
import logging
import re
from collections import deque
from contextvars import ContextVar
from types import TracebackType
from typing import Any
from uuid import UUID

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

SAFE_EXTRA_FIELDS = frozenset(
    {
        "attempt",
        "component",
        "dispatched",
        "duration_ms",
        "job_id",
        "job_kind",
        "method",
        "path",
        "queue",
        "queued_wakeups",
        "recovered",
        "redispatched_wakeups",
        "scheduler_id",
        "status",
        "status_code",
        "worker_id",
    }
)
SAFE_HTTP_METHODS = frozenset(
    {"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"}
)
PROVIDER_NAMES = frozenset({"bumpa", "meta"})
PROVIDER_OPERATIONS = frozenset({"otp_delivery", "sync"})
PROVIDER_CATEGORIES = frozenset(
    {
        "authentication",
        "invalid_response",
        "provider",
        "rate_limited",
        "response_too_large",
        "timeout",
        "transport",
    }
)
PROVIDER_CODE_RE = re.compile(r"^(?:0|[1-9][0-9]{0,9})$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
FRAME_MODULE_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]{0,63})(?:\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,15}$"
)
FRAME_FUNCTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
MAX_EXCEPTION_FRAMES = 8
MAX_TRACEBACK_DEPTH = 64


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            exception_name = record.exc_info[0].__name__
            payload["exception_type"] = (
                exception_name
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", exception_name)
                else "Exception"
            )
            exception_frames = _sanitized_exception_frames(record.exc_info[2])
            if exception_frames:
                payload["exception_frames"] = exception_frames
        for field in SAFE_EXTRA_FIELDS:
            if hasattr(record, field):
                value = getattr(record, field)
                payload[field] = (
                    value
                    if field != "method" or (isinstance(value, str) and value in SAFE_HTTP_METHODS)
                    else "OTHER"
                )
        payload.update(_validated_provider_diagnostics(record))
        return json.dumps(payload, default=str)


def _sanitized_exception_frames(traceback: TracebackType | None) -> list[dict[str, object]]:
    """Return bounded traceback coordinates without inspecting sensitive values.

    Only a validated module and function label plus the line number are read from
    each frame. File paths, source lines, locals, arguments, exception messages,
    chained exceptions, and exception causes are deliberately never traversed.
    """

    frames: deque[dict[str, object]] = deque(maxlen=MAX_EXCEPTION_FRAMES)
    depth = 0
    while traceback is not None and depth < MAX_TRACEBACK_DEPTH:
        frame = traceback.tb_frame
        module = frame.f_globals.get("__name__")
        function = frame.f_code.co_name
        frames.append(
            {
                "module": (
                    module
                    if isinstance(module, str)
                    and len(module) <= 255
                    and FRAME_MODULE_RE.fullmatch(module)
                    else "unknown"
                ),
                "function": (function if FRAME_FUNCTION_RE.fullmatch(function) else "unknown"),
                "line": (traceback.tb_lineno if 1 <= traceback.tb_lineno <= 2_147_483_647 else 0),
            }
        )
        traceback = traceback.tb_next
        depth += 1
    return list(frames)


def _validated_provider_diagnostics(record: logging.LogRecord) -> dict[str, object]:
    """Validate provider fields again at the final serialization boundary.

    The field-name allowlist is not enough: logging ``extra`` is an open mapping,
    and a future caller could otherwise place a raw header or response body under
    an approved name without using the typed provider helper.
    """

    provider = getattr(record, "provider", None)
    operation = getattr(record, "provider_operation", None)
    category = getattr(record, "provider_category", None)
    retryable = getattr(record, "provider_retryable", None)
    if (
        provider not in PROVIDER_NAMES
        or operation not in PROVIDER_OPERATIONS
        or category not in PROVIDER_CATEGORIES
        or not isinstance(retryable, bool)
    ):
        return {}

    diagnostics: dict[str, object] = {
        "provider": provider,
        "provider_operation": operation,
        "provider_category": category,
        "provider_retryable": retryable,
    }
    http_status = getattr(record, "provider_http_status", None)
    if (
        isinstance(http_status, int)
        and not isinstance(http_status, bool)
        and 100 <= http_status <= 599
    ):
        diagnostics["provider_http_status"] = http_status
    code = getattr(record, "provider_code", None)
    if isinstance(code, str) and PROVIDER_CODE_RE.fullmatch(code) and int(code) <= 2_147_483_647:
        diagnostics["provider_code"] = code
    request_id_hash = getattr(record, "provider_request_id_hash", None)
    if isinstance(request_id_hash, str) and SHA256_RE.fullmatch(request_id_hash):
        diagnostics["provider_request_id_hash"] = request_id_hash
    retry_after = getattr(record, "retry_after_seconds", None)
    if (
        isinstance(retry_after, (int, float))
        and not isinstance(retry_after, bool)
        and 0 <= retry_after <= 86_400
    ):
        diagnostics["retry_after_seconds"] = retry_after
    sync_run_id = getattr(record, "sync_run_id", None)
    if isinstance(sync_run_id, str):
        try:
            canonical_run_id = str(UUID(sync_run_id))
        except ValueError:
            canonical_run_id = None
        if canonical_run_id == sync_run_id:
            diagnostics["sync_run_id"] = sync_run_id
    return diagnostics


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    # Uvicorn configures its own non-propagating default handler before importing
    # the ASGI app. If left in place, an exception re-raised by the request
    # middleware is formatted a second time with its raw message, chained cause,
    # source line, and path. Route the complete Uvicorn error hierarchy through
    # the same sanitized handler without also propagating it to the root handler.
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.handlers = [handler]
    uvicorn_logger.setLevel(logging.INFO)
    uvicorn_logger.propagate = False
    uvicorn_logger.disabled = False

    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    uvicorn_error_logger.handlers = []
    uvicorn_error_logger.setLevel(logging.NOTSET)
    uvicorn_error_logger.propagate = True
    uvicorn_error_logger.disabled = False

    # Access request lines include the raw URL and can therefore contain query
    # credentials. Compose already starts Uvicorn with --no-access-log; disabling
    # the logger here keeps that privacy boundary intact under other launchers.
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.handlers = []
    uvicorn_access_logger.propagate = False
    uvicorn_access_logger.disabled = True

    # httpx's INFO request line contains the complete URL. Bumpa scope IDs and
    # request parameters live in its query string, so third-party access logging
    # must never inherit the application's INFO root level.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
