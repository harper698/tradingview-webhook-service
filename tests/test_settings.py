from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from tv_webhook.app import create_app
from tv_webhook.settings import Settings


@pytest.mark.parametrize("secret", ["", "short", "x" * 31, "x" * 129, "密" * 32, " " * 32])
def test_secret_required_and_bounded(secret, tmp_path):
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        Settings(secret=secret, database=tmp_path / "test.db")


def test_create_app_fails_closed_without_environment_secret(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        create_app()


def test_secret_not_in_settings_repr(settings):
    assert settings.secret not in repr(settings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_age_seconds", 0),
        ("max_age_seconds", 86401),
        ("future_skew_seconds", -1),
        ("future_skew_seconds", 301),
        ("allowed_symbols", frozenset({"bad symbol"})),
    ],
)
def test_invalid_configuration_fails_closed(settings, field, value):
    with pytest.raises(ValueError):
        replace(settings, **{field: value})


def test_environment_configuration(monkeypatch, settings):
    monkeypatch.setenv("WEBHOOK_SECRET", settings.secret)
    monkeypatch.setenv("WEBHOOK_DB_PATH", str(settings.database))
    monkeypatch.setenv("WEBHOOK_MAX_AGE_SECONDS", "600")
    monkeypatch.setenv("WEBHOOK_FUTURE_SKEW_SECONDS", "10")
    monkeypatch.setenv("WEBHOOK_ALLOWED_SYMBOLS", " BINANCE:BTCUSDT, NYSE:IBM ")
    config = Settings.from_env()
    assert config.database == settings.database
    assert config.max_age_seconds == 600
    assert config.future_skew_seconds == 10
    assert config.allowed_symbols == frozenset({"BINANCE:BTCUSDT", "NYSE:IBM"})


def test_invalid_environment_integer_no_value_leak(monkeypatch, settings):
    monkeypatch.setenv("WEBHOOK_MAX_AGE_SECONDS", settings.secret)
    with pytest.raises(ValueError) as caught:
        Settings.from_env()
    assert settings.secret not in str(caught.value)


def test_allowlist(settings, payload):
    config = replace(settings, allowed_symbols=frozenset({"NYSE:IBM"}))
    with TestClient(create_app(config)) as client:
        assert client.post("/webhook", json=payload).status_code == 422
        payload["symbol"] = "NYSE:IBM"
        assert client.post("/webhook", json=payload).status_code == 202
