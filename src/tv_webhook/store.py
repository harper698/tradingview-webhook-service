"""SQLite transaction is the boundary of the simulated action's guarantee."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from tv_webhook.models import Alert


class EventConflict(Exception):
    """The event ID is already bound to a different normalized payload."""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        # A short lock budget leaves headroom under TradingView's 3-second timeout.
        connection = sqlite3.connect(self.path, timeout=0.25)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS alerts (
                    event_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS simulated_actions (
                    event_id TEXT PRIMARY KEY REFERENCES alerts(event_id),
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('buy', 'sell')),
                    price TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status = 'simulated'),
                    simulated_at TEXT NOT NULL
                );
            """)

    def health(self) -> None:
        with closing(self.connect()) as connection:
            connection.execute("SELECT event_id FROM simulated_actions LIMIT 1").fetchone()

    def accept(self, alert: Alert) -> bool:
        """Commit both rows or neither. Return True only for a new simulation."""
        payload = alert.canonical_json()
        now = datetime.now(UTC).isoformat(timespec="microseconds")
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT payload FROM alerts WHERE event_id = ?", (alert.event_id,)
            ).fetchone()
            if previous is not None:
                if previous[0] != payload:
                    raise EventConflict
                return False
            connection.execute(
                "INSERT INTO alerts (event_id, payload, received_at) VALUES (?, ?, ?)",
                (alert.event_id, payload, now),
            )
            # This is the mock dispatcher: its only side effect is a durable audit row.
            # Replacing it with network I/O needs an outbox/worker, not a direct call.
            connection.execute(
                """INSERT INTO simulated_actions
                   (event_id, symbol, action, price, status, simulated_at)
                   VALUES (?, ?, ?, ?, 'simulated', ?)""",
                (alert.event_id, alert.symbol, alert.action, str(alert.price.normalize()), now),
            )
            return True
