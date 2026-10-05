"""Normalized, bounded alert data, excluding the authentication credential."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tv_webhook.settings import SYMBOL_PATTERN


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    event_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    symbol: str = Field(min_length=1, max_length=64, pattern=f"^{SYMBOL_PATTERN}$")
    action: Literal["buy", "sell"]
    price: Decimal = Field(gt=0, le=10**12, max_digits=24, decimal_places=12)
    timestamp: datetime

    @field_validator("price", mode="before")
    @classmethod
    def numeric_price(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError("Price must be a JSON number")
        return value

    @field_validator("timestamp", mode="before")
    @classmethod
    def iso_timestamp(cls, value: object) -> datetime:
        if not isinstance(value, str) or "T" not in value or len(value) > 40:
            raise ValueError("Timestamp must be an ISO 8601 string")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Timestamp must include a timezone")
        return parsed.astimezone(UTC)

    def canonical_json(self) -> str:
        """Equivalent decimal prices and timezone offsets have one identity."""
        return json.dumps(
            {
                "event_id": self.event_id,
                "symbol": self.symbol,
                "action": self.action,
                "price": format(self.price.normalize(), "f"),
                "timestamp": self.timestamp.isoformat(timespec="microseconds"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
