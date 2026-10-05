"""Send two identical requests to the running localhost service using stdlib only."""

import json
import os
from datetime import UTC, datetime
from urllib.request import Request, urlopen
from uuid import uuid4


def main() -> None:
    payload = {
        "token": os.environ["WEBHOOK_SECRET"],
        "event_id": f"local.{uuid4().hex}",
        "symbol": "BINANCE:BTCUSDT",
        "action": "buy",
        "price": 60000.25,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    body = json.dumps(payload).encode("utf-8")
    for expected in (202, 200):
        request = Request(
            "http://127.0.0.1:8000/webhook",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            assert response.status == expected
            print(f"HTTP {response.status}: {response.read().decode('utf-8')}")


if __name__ == "__main__":
    main()
