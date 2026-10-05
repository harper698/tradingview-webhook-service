"""Start a loopback-only server by default."""

import argparse

import uvicorn

from tv_webhook.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="TradingView alert receiver (simulation only)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        app = create_app()
    except (ValueError, OSError) as error:
        # Configuration messages contain names/ranges, never supplied values.
        parser.exit(2, f"Configuration error: {error}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
