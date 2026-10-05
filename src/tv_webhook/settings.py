"""Environment configuration; configuration errors must never print the secret."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

SYMBOL_PATTERN = r"[A-Z0-9][A-Z0-9._:/-]{0,63}"


@dataclass(frozen=True)
class Settings:
    secret: str = field(repr=False)
    database: Path = Path("data/alerts.sqlite3")
    max_age_seconds: int = 300
    future_skew_seconds: int = 30
    allowed_symbols: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", self.secret):
            raise ValueError("WEBHOOK_SECRET must be 32-128 URL-safe ASCII characters")
        if not 1 <= self.max_age_seconds <= 86400:
            raise ValueError("WEBHOOK_MAX_AGE_SECONDS must be between 1 and 86400")
        if not 0 <= self.future_skew_seconds <= 300:
            raise ValueError("WEBHOOK_FUTURE_SKEW_SECONDS must be between 0 and 300")
        if any(not re.fullmatch(SYMBOL_PATTERN, value) for value in self.allowed_symbols):
            raise ValueError("WEBHOOK_ALLOWED_SYMBOLS contains an invalid symbol")

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            max_age = int(os.getenv("WEBHOOK_MAX_AGE_SECONDS", "300"))
            future = int(os.getenv("WEBHOOK_FUTURE_SKEW_SECONDS", "30"))
        except ValueError:
            raise ValueError("Timestamp settings must be integers") from None
        symbols = frozenset(
            value.strip()
            for value in os.getenv("WEBHOOK_ALLOWED_SYMBOLS", "").split(",")
            if value.strip()
        )
        return cls(
            secret=os.getenv("WEBHOOK_SECRET", ""),
            database=Path(os.getenv("WEBHOOK_DB_PATH", "data/alerts.sqlite3")),
            max_age_seconds=max_age,
            future_skew_seconds=future,
            allowed_symbols=symbols,
        )
