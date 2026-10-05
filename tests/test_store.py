import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from tv_webhook.app import create_app


def rows(settings):
    with closing(sqlite3.connect(settings.database)) as connection:
        return (
            connection.execute("SELECT * FROM alerts").fetchall(),
            connection.execute("SELECT * FROM simulated_actions").fetchall(),
        )


def test_accept_duplicate_conflict_and_no_token_persistence(client, payload, settings):
    accepted = client.post("/webhook", json=payload)
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "accepted"
    duplicate = client.post("/webhook", json=payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    conflict = client.post("/webhook", json={**payload, "action": "sell"})
    assert conflict.status_code == 409
    alerts, actions = rows(settings)
    assert len(alerts) == len(actions) == 1
    assert actions[0][4] == "simulated"
    assert payload["token"] not in repr(alerts) + repr(actions)
    assert "token" not in json.loads(alerts[0][1])


def test_canonical_decimal_and_timezone_deduplication(client, payload):
    payload["price"] = 100
    assert client.post("/webhook", json=payload).status_code == 202
    alternate = datetime.fromisoformat(payload["timestamp"]).astimezone(
        timezone(timedelta(hours=8))
    )
    payload["timestamp"] = alternate.isoformat()
    payload["price"] = 100.0
    assert client.post("/webhook", json=payload).status_code == 200


def test_restart_preserves_idempotence(settings, payload):
    with TestClient(create_app(settings)) as first:
        assert first.post("/webhook", json=payload).status_code == 202
    with TestClient(create_app(settings)) as restarted:
        assert restarted.post("/webhook", json=payload).status_code == 200
    assert len(rows(settings)[1]) == 1


def test_concurrent_requests_record_exactly_one_simulation(client, payload, settings):
    with ThreadPoolExecutor(max_workers=12) as executor:
        responses = list(executor.map(lambda _: client.post("/webhook", json=payload), range(24)))
    assert [response.status_code for response in responses].count(202) == 1
    assert [response.status_code for response in responses].count(200) == 23
    assert len(rows(settings)[0]) == len(rows(settings)[1]) == 1


def test_simulation_failure_rolls_back_alert(client, payload, settings, caplog):
    with closing(sqlite3.connect(settings.database)) as connection, connection:
        connection.execute("""
            CREATE TRIGGER fail_simulation BEFORE INSERT ON simulated_actions
            BEGIN SELECT RAISE(ABORT, 'private database failure'); END
        """)
    response = client.post("/webhook", json=payload)
    assert response.status_code == 500
    assert "private" not in response.text + caplog.text
    assert rows(settings) == ([], [])
    with closing(sqlite3.connect(settings.database)) as connection, connection:
        connection.execute("DROP TRIGGER fail_simulation")
    assert client.post("/webhook", json=payload).status_code == 202
    assert len(rows(settings)[1]) == 1


def test_database_lock_is_retryable_and_does_not_accept(client, payload, settings):
    with closing(sqlite3.connect(settings.database)) as locker, locker:
        locker.execute("BEGIN IMMEDIATE")
        response = client.post("/webhook", json=payload)
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
        locker.rollback()
    assert rows(settings) == ([], [])
    assert client.post("/webhook", json=payload).status_code == 202


def test_health_fails_when_schema_unavailable(client, settings):
    with closing(sqlite3.connect(settings.database)) as connection, connection:
        connection.execute("DROP TABLE simulated_actions")
    assert client.get("/healthz").status_code == 503
