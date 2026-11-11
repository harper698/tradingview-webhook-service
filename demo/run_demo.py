#!/usr/bin/env python3
"""
================================================================================
🎬 UPWORK PORTFOLIO DEMO & GIF RECORDING SCRIPT (CleanShot X / Kap / Lottie)
================================================================================
Recommended Recording Duration : 18 - 22 seconds (or run with `--fast` for CI)
Recommended Terminal Size      : 112 cols × 36 rows (Dark theme, 14pt monospace)
Command to Record              : python3 demo/run_demo.py  (or: python3 run_demo.py)

⏱️  SCREEN RECORDING TIMELINE & KEY HIGHLIGHTS:
  • 00s - 03s [INIT & FETCH]    : Boots zero-config sandbox (ephemeral HMAC token
                                  + SQLite WAL engine) and fetches simulated
                                  TradingView Pine v6 SMA(10/30) webhook stream.
  • 03s - 09s [VALIDATE]        : Demonstrates schema sanitization, Decimal/UTC
                                  canonicalization, and blocks malformed/hostile
                                  packets (401 Bad Token, 422 Stale, 400 NaN, 409).
  • 09s - 15s [RETRY & DEDUP]   : Simulates network jitter & SQLite lock contention
                                  (HTTP 503 Retry-After: 1 -> Auto-Retry -> 202),
                                  and absorbs duplicate webhook retry storms (200).
  • 15s - 22s [OUTPUT & POPUP]  : Verifies ACID SQLite audit tables (`alerts` &
                                  `simulated_actions`), renders high-res chart
                                  `demo_output.png`, and auto-pops it up on screen!
================================================================================
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from run_demo import main  # noqa: E402

if __name__ == "__main__":
    main()
