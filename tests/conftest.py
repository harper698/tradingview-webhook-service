from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tv_webhook.app import create_app
from tv_webhook.settings import Settings

TEST_SECRET = "test_only_0123456789_abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def settings(tmp_path):
    return Settings(secret=TEST_SECRET, database=tmp_path / "alerts.sqlite3")


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as session:
        yield session


@pytest.fixture
def payload():
    return {
        "token": TEST_SECRET,
        "event_id": "test.BTCUSD.buy.001",
        "symbol": "BINANCE:BTCUSDT",
        "action": "buy",
        "price": 60000.25,
        "timestamp": datetime.now(UTC).isoformat(),
    }
