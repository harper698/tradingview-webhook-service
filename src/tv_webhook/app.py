"""HTTP boundary: bounded body, authentication, validation, transactional simulation."""

import hmac
import json
import logging
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal, DecimalException

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from tv_webhook.models import Alert
from tv_webhook.settings import Settings
from tv_webhook.store import EventConflict, Store

MAX_BODY_BYTES = 16 * 1024
logger = logging.getLogger("tv_webhook")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


async def read_json(request: Request) -> dict:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(415, "Content-Type must be application/json")
    if request.headers.get("content-encoding", "identity").strip().lower() != "identity":
        raise HTTPException(415, "Compressed request bodies are not supported")
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                raise HTTPException(413, "Request body exceeds 16 KiB")
            body.extend(chunk)
    except ClientDisconnect:
        raise HTTPException(400, "Incomplete request body") from None
    try:
        data = json.loads(
            body.decode("utf-8"),
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (ValueError, UnicodeError, RecursionError, DecimalException):
        raise HTTPException(400, "Invalid JSON object") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "Expected a JSON object")
    return data


def authenticate(data: dict, secret: str) -> None:
    token = data.pop("token", None)
    # Bytes comparison supports untrusted Unicode safely; malformed surrogates fail closed.
    try:
        valid = isinstance(token, str) and hmac.compare_digest(
            token.encode("utf-8"), secret.encode("ascii")
        )
    except UnicodeError:
        valid = False
    if not valid:
        raise HTTPException(401, "Invalid authentication token")


def database_error(error: sqlite3.Error) -> HTTPException:
    code = getattr(error, "sqlite_errorcode", 0) or 0
    if code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return HTTPException(
            503, "Storage busy; retry the same event", headers={"Retry-After": "1"}
        )
    logger.error("Storage operation failed")  # Never log SQL, parameters, or raw request data.
    return HTTPException(500, "Storage operation failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings.from_env()
    store = Store(config.database)
    store.initialize()
    app = FastAPI(
        title="TradingView Webhook Service",
        version="1.0.0",
        description="Authenticated alerts forwarded to a transactional simulation audit.",
    )
    app.state.store = store

    @app.get("/healthz")
    def healthz() -> dict:
        try:
            store.health()
        except sqlite3.Error:
            raise HTTPException(503, "Storage unavailable") from None
        return {"status": "ok", "mode": "simulation"}

    schema = Alert.model_json_schema(mode="validation")
    schema["properties"]["token"] = {"type": "string", "writeOnly": True}
    schema["properties"]["price"] = {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12}
    schema["required"].append("token")

    @app.post(
        "/webhook",
        status_code=202,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": schema}},
            }
        },
        responses={
            200: {"description": "Identical event already recorded"},
            401: {"description": "Invalid authentication"},
            409: {"description": "Event ID conflicts with an existing event"},
            413: {"description": "Request exceeds 16 KiB"},
            422: {"description": "Invalid alert or timestamp"},
            503: {"description": "Storage busy; retry the identical event"},
        },
    )
    async def webhook(request: Request) -> JSONResponse:
        data = await read_json(request)
        authenticate(data, config.secret)
        try:
            alert = Alert.model_validate(data)
        except (ValidationError, ValueError, OverflowError):
            # Pydantic errors can contain user-controlled values and field names.
            raise HTTPException(422, "Invalid alert schema") from None
        age = (datetime.now(UTC) - alert.timestamp).total_seconds()
        if age > config.max_age_seconds or age < -config.future_skew_seconds:
            raise HTTPException(422, "Timestamp outside the permitted window")
        if config.allowed_symbols and alert.symbol not in config.allowed_symbols:
            raise HTTPException(422, "Symbol is not allowed")
        try:
            created = await run_in_threadpool(store.accept, alert)
        except EventConflict:
            raise HTTPException(409, "Event ID already has a different payload") from None
        except sqlite3.Error as error:
            raise database_error(error) from None
        return JSONResponse(
            status_code=202 if created else 200,
            content={
                "event_id": alert.event_id,
                "status": "accepted" if created else "duplicate",
                "mode": "simulation",
            },
        )

    return app
