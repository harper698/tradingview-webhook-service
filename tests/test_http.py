import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tv_webhook.app import MAX_BODY_BYTES


def test_health_and_documented_schema(client):
    assert client.get("/healthz").json() == {"status": "ok", "mode": "simulation"}
    assert client.get("/docs").status_code == 200
    operation = client.get("/openapi.json").json()["paths"]["/webhook"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["token"]["writeOnly"] is True


@pytest.mark.parametrize("token", [None, "wrong", "密码", "\ud800", 12, [], {}, True])
def test_invalid_authentication_never_crashes_or_echoes(client, payload, token, caplog):
    payload["token"] = token
    response = client.post(
        "/webhook", content=json.dumps(payload), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication token"}
    assert "test_only_" not in caplog.text


def test_missing_token(client, payload):
    payload.pop("token")
    assert client.post("/webhook", json=payload).status_code == 401


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", ""),
        ("event_id", "a" * 129),
        ("event_id", "line\nbreak"),
        ("event_id", 123),
        ("symbol", ""),
        ("symbol", "btcusdt"),
        ("symbol", "<script>"),
        ("symbol", "X" * 65),
        ("action", "BUY"),
        ("action", "cancel"),
        ("price", 0),
        ("price", -1),
        ("price", True),
        ("price", "100.00"),
        ("price", 1e13),
        ("price", 1e-13),
        ("timestamp", "2026-10-04T12:00:00"),
        ("timestamp", "not-a-date"),
        ("timestamp", 1735689600),
        ("timestamp", "9999-12-31T23:59:59-23:59"),
    ],
)
def test_invalid_schema(client, payload, field, value):
    payload[field] = value
    response = client.post("/webhook", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid alert schema"}


def test_extra_field_and_schema_errors_do_not_leak_input(client, payload, caplog):
    secret = payload["token"]
    payload[secret] = {"password": secret}
    payload["symbol"] = secret
    response = client.post("/webhook", json=payload)
    assert response.status_code == 422
    assert secret not in response.text
    assert secret not in caplog.text


@pytest.mark.parametrize("seconds", [-301, 31])
def test_stale_and_future_timestamp(client, payload, seconds):
    payload["timestamp"] = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
    response = client.post("/webhook", json=payload)
    assert response.status_code == 422
    assert "window" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        b"{broken",
        b"[]",
        b"null",
        b'"text"',
        b'{"token": "first", "token": "second"}',
        b'{"price": NaN}',
        b'{"price": Infinity}',
        b'{"price": -Infinity}',
        b'{"price": 1e9999999999999999999999999999999}',
        b"{" + b"\xff" + b"}",
        b"[" * 1200 + b"]" * 1200,
    ],
)
def test_malformed_json(client, body):
    response = client.post("/webhook", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 400


@pytest.mark.parametrize("content_type", ["text/plain", "application/xml", ""])
def test_unsupported_content_type(client, payload, content_type):
    response = client.post(
        "/webhook", content=json.dumps(payload), headers={"Content-Type": content_type}
    )
    assert response.status_code == 415


def test_compressed_body_rejected(client):
    response = client.post(
        "/webhook",
        content=b"compressed",
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert response.status_code == 415


@pytest.mark.parametrize("media_type", ["application/json", "Application/JSON; charset=utf-8"])
def test_json_charset_header(client, payload, media_type):
    response = client.post(
        "/webhook",
        content=json.dumps(payload),
        headers={"Content-Type": media_type, "Content-Encoding": "Identity"},
    )
    assert response.status_code == 202


def test_body_size_limit(client):
    response = client.post(
        "/webhook",
        content=b" " * (MAX_BODY_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


@pytest.mark.parametrize("length_header", [None, "1"])
def test_chunked_body_limit_without_trusting_content_length(app, length_header):
    async def run():
        async def chunks():
            yield b" " * (MAX_BODY_BYTES // 2)
            yield b" " * (MAX_BODY_BYTES // 2)
            yield b" "

        headers = {"Content-Type": "application/json"}
        if length_header is not None:
            headers["Content-Length"] = length_header
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/webhook", content=chunks(), headers=headers)
            assert response.status_code == 413

    asyncio.run(run())
