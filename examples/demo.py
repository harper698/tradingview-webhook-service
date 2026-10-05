"""Offline end-to-end demonstration. Installs with `pip install -e '.[dev]'`."""

import os
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from tv_webhook.app import create_app


def main() -> None:
    with TemporaryDirectory(prefix="tv-webhook-demo-") as directory:
        database = Path(directory) / "demo.sqlite3"
        # This isolated process gets an ephemeral token; no user secret is printed or saved.
        os.environ["WEBHOOK_SECRET"] = secrets.token_urlsafe(32)
        os.environ["WEBHOOK_DB_PATH"] = str(database)
        os.environ["WEBHOOK_ALLOWED_SYMBOLS"] = "BINANCE:BTCUSDT"
        payload = {
            "token": os.environ["WEBHOOK_SECRET"],
            "event_id": "demo.BTCUSDT.buy.001",
            "symbol": "BINANCE:BTCUSDT",
            "action": "buy",
            "price": 60000.25,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        with TestClient(create_app()) as client:
            cases = [
                ("accepted", payload, 202),
                ("duplicate", payload, 200),
                ("conflict", {**payload, "action": "sell"}, 409),
                ("unauthorized", {**payload, "token": "wrong"}, 401),
            ]
            for name, body, expected in cases:
                response = client.post("/webhook", json=body)
                assert response.status_code == expected, response.text
                print(f"{name:12} HTTP {response.status_code}")
        with TestClient(create_app()) as restarted:
            response = restarted.post("/webhook", json=payload)
            assert response.status_code == 200
            print(f"{'after restart':12} HTTP {response.status_code}")
        with closing(sqlite3.connect(database)) as connection:
            alerts = connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
            actions = connection.execute("SELECT COUNT(*) FROM simulated_actions").fetchone()[0]
        assert alerts == actions == 1
        print(f"persisted    {alerts} alert, {actions} simulated action")
        print("PASS: no network calls, no live trades, no credentials printed")


if __name__ == "__main__":
    main()
