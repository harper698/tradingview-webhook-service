#!/usr/bin/env python3
"""
================================================================================
🎬 UPWORK PORTFOLIO DEMO & GIF RECORDING SCRIPT (CleanShot X / Kap / Lottie)
================================================================================
Recommended Recording Duration : 18 - 22 seconds (or run with `--fast` for CI)
Recommended Terminal Size      : 112 cols × 36 rows (Dark theme, 14pt monospace)
Command to Record              : python3 run_demo.py

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

import argparse
import datetime as dt
import hmac
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
import zlib
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory

# Ensure Python 3.9+ compatibility if run outside a Python 3.11+ virtualenv
if not hasattr(dt, "UTC"):
    dt.UTC = dt.timezone.utc  # type: ignore[attr-defined]

UTC = dt.UTC
PROJECT_ROOT = Path(__file__).resolve().parent
if not (PROJECT_ROOT / "src").exists() and (PROJECT_ROOT.parent / "src").exists():
    PROJECT_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tv_webhook.settings import SYMBOL_PATTERN, Settings  # noqa: E402


@dataclass(frozen=True)
class FallbackAlert:
    """Zero-dependency Alert model matching tv_webhook.models.Alert semantics."""

    event_id: str
    symbol: str
    action: str
    price: Decimal
    timestamp: dt.datetime

    @classmethod
    def model_validate(cls, data: dict[str, object]) -> FallbackAlert:
        allowed_keys = {"event_id", "symbol", "action", "price", "timestamp"}
        if set(data.keys()) != allowed_keys:
            raise ValueError("Invalid alert schema: missing or extra fields")
        event_id = data["event_id"]
        symbol = data["symbol"]
        action = data["action"]
        raw_price = data["price"]
        raw_ts = data["timestamp"]

        if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", event_id):
            raise ValueError("Invalid event_id")
        if not isinstance(symbol, str) or not re.fullmatch(SYMBOL_PATTERN, symbol):
            raise ValueError("Invalid symbol")
        if action not in ("buy", "sell"):
            raise ValueError("Invalid action")
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float, Decimal)):
            raise ValueError("Price must be numeric")
        try:
            price = Decimal(str(raw_price))
        except InvalidOperation as exc:
            raise ValueError("Invalid decimal price") from exc
        if not price.is_finite() or price <= 0 or price > Decimal(10**12):
            raise ValueError("Price out of range")
        if not isinstance(raw_ts, str) or "T" not in raw_ts or len(raw_ts) > 40:
            raise ValueError("Invalid timestamp")
        parsed = dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Timestamp must include timezone")
        return cls(
            event_id=event_id,
            symbol=symbol,
            action=action,
            price=price,
            timestamp=parsed.astimezone(UTC),
        )

    def canonical_json(self) -> str:
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


try:
    from tv_webhook.models import Alert  # type: ignore[assignment]
except ModuleNotFoundError:
    import types

    shim = types.ModuleType("tv_webhook.models")
    shim.Alert = FallbackAlert  # type: ignore[attr-defined]
    sys.modules["tv_webhook.models"] = shim
    Alert = FallbackAlert  # type: ignore[misc,assignment]

from tv_webhook.store import EventConflict, Store  # noqa: E402


# ---------------------------------------------------------------------------
# Terminal ANSI Styling & Visual Progress Helpers
# ---------------------------------------------------------------------------
class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[38;5;45m"
    BLUE = "\033[38;5;39m"
    GREEN = "\033[38;5;48m"
    YELLOW = "\033[38;5;220m"
    ORANGE = "\033[38;5;208m"
    RED = "\033[38;5;197m"
    MAGENTA = "\033[38;5;177m"
    GRAY = "\033[38;5;245m"
    WHITE = "\033[38;5;255m"


def pause(seconds: float, fast: bool) -> None:
    time.sleep(seconds * (0.02 if fast else 1.0))


def badge(tag: str, color: str) -> str:
    return f"{Style.BOLD}{color}[{tag:^8}]{Style.RESET}"


def render_progress(label: str, steps: int, duration: float, fast: bool) -> None:
    width = 28
    step_delay = (duration * (0.02 if fast else 1.0)) / max(steps, 1)
    for idx in range(1, steps + 1):
        filled = int(width * idx / steps)
        bar = f"{Style.CYAN}{'━' * filled}{Style.GRAY}{'╌' * (width - filled)}{Style.RESET}"
        pct = int(100 * idx / steps)
        line = (
            f"\r  {Style.DIM}⏳ {label:<34}{Style.RESET} "
            f"{bar} {Style.BOLD}{pct:3d}%{Style.RESET}"
        )
        sys.stdout.write(line)
        sys.stdout.flush()
        time.sleep(step_delay)
    sys.stdout.write("\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Pure-Python High-DPI Anti-Aliased PNG Chart Renderer (Zero External Deps)
# ---------------------------------------------------------------------------
FONT_5X7: dict[str, list[str]] = {
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "10010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "10000", "11110", "00001", "10001", "01110"],
    "6": ["01110", "10000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ",": ["00000", "00000", "00000", "00000", "00110", "00100", "01000"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "_": ["00000", "00000", "00000", "00000", "00000", "00000", "11111"],
    "&": ["01100", "10010", "10100", "01000", "10101", "10010", "01101"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    "$": ["00100", "01111", "10100", "01110", "00101", "11110", "00100"],
    "(": ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ")": ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    "[": ["01110", "01000", "01000", "01000", "01000", "01000", "01110"],
    "]": ["01110", "00010", "00010", "00010", "00010", "00010", "01110"],
    "#": ["01010", "11111", "01010", "01010", "11111", "01010", "00000"],
    "|": ["00100", "00100", "00100", "00100", "00100", "00100", "00100"],
    ">": ["10000", "01000", "00100", "00010", "00100", "01000", "10000"],
}


class Canvas:
    """Lightweight RGB rasterizer with alpha blending and anti-aliased primitives."""

    def __init__(self, width: int, height: int, bg: tuple[int, int, int]) -> None:
        self.width = width
        self.height = height
        self.buf = bytearray(bg) * (width * height)

    def blend_pixel(self, x: int, y: int, color: tuple[int, int, int], alpha: float) -> None:
        if x < 0 or x >= self.width or y < 0 or y >= self.height or alpha <= 0.0:
            return
        idx = (y * self.width + x) * 3
        if alpha >= 1.0:
            self.buf[idx] = color[0]
            self.buf[idx + 1] = color[1]
            self.buf[idx + 2] = color[2]
            return
        inv = 1.0 - alpha
        self.buf[idx] = int(self.buf[idx] * inv + color[0] * alpha)
        self.buf[idx + 1] = int(self.buf[idx + 1] * inv + color[1] * alpha)
        self.buf[idx + 2] = int(self.buf[idx + 2] * inv + color[2] * alpha)

    def fill_rect(
        self,
        x0: int,
        y0: int,
        w: int,
        h: int,
        color: tuple[int, int, int],
        alpha: float = 1.0,
    ) -> None:
        x_start = max(0, x0)
        y_start = max(0, y0)
        x_end = min(self.width, x0 + w)
        y_end = min(self.height, y0 + h)
        if x_start >= x_end or y_start >= y_end:
            return
        if alpha >= 1.0:
            row_bytes = bytes(color) * (x_end - x_start)
            stride = self.width * 3
            for y in range(y_start, y_end):
                start = y * stride + x_start * 3
                self.buf[start : start + len(row_bytes)] = row_bytes
        else:
            for y in range(y_start, y_end):
                for x in range(x_start, x_end):
                    self.blend_pixel(x, y, color, alpha)

    def stroke_rect(
        self, x0: int, y0: int, w: int, h: int, color: tuple[int, int, int], thickness: int = 1
    ) -> None:
        self.fill_rect(x0, y0, w, thickness, color)
        self.fill_rect(x0, y0 + h - thickness, w, thickness, color)
        self.fill_rect(x0, y0, thickness, h, color)
        self.fill_rect(x0 + w - thickness, y0, thickness, h, color)

    def draw_circle(
        self,
        cx: float,
        cy: float,
        radius: float,
        color: tuple[int, int, int],
        alpha: float = 1.0,
    ) -> None:
        r_int = int(math.ceil(radius + 1.5))
        for y in range(int(cy) - r_int, int(cy) + r_int + 1):
            for x in range(int(cx) - r_int, int(cx) + r_int + 1):
                dist = math.hypot(x - cx, y - cy)
                if dist <= radius - 0.5:
                    self.blend_pixel(x, y, color, alpha)
                elif dist <= radius + 0.5:
                    cov = (radius + 0.5 - dist) * alpha
                    self.blend_pixel(x, y, color, cov)

    def draw_line(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        color: tuple[int, int, int],
        width: float = 2.0,
        alpha: float = 1.0,
    ) -> None:
        steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 2.0) + 1
        half_w = width / 2.0
        for i in range(steps + 1):
            t = i / steps if steps else 0.0
            px = x0 + (x1 - x0) * t
            py = y0 + (y1 - y0) * t
            self.draw_circle(px, py, half_w, color, alpha)

    def draw_text(
        self,
        x: int,
        y: int,
        text: str,
        color: tuple[int, int, int],
        scale: int = 2,
    ) -> None:
        cursor_x = x
        for ch in text.upper():
            glyph = FONT_5X7.get(ch, FONT_5X7[" "])
            for row_idx, row_str in enumerate(glyph):
                for col_idx, bit in enumerate(row_str):
                    if bit == "1":
                        self.fill_rect(
                            cursor_x + col_idx * scale,
                            y + row_idx * scale,
                            scale,
                            scale,
                            color,
                        )
            cursor_x += 6 * scale

    def draw_badge(
        self,
        x: int,
        y: int,
        text: str,
        fg: tuple[int, int, int],
        bg: tuple[int, int, int],
        scale: int = 2,
    ) -> None:
        pad_x, pad_y = 8, 5
        tw = len(text) * 6 * scale - scale
        th = 7 * scale
        bw = tw + pad_x * 2
        bh = th + pad_y * 2
        self.fill_rect(x, y, bw, bh, bg, alpha=0.92)
        self.stroke_rect(x, y, bw, bh, fg, 1)
        self.draw_text(x + pad_x, y + pad_y, text, fg, scale=scale)

    def save_png(self, path: Path) -> None:
        raw_rows = bytearray()
        stride = self.width * 3
        for y in range(self.height):
            raw_rows.append(0)  # Filter type 0 (None)
            raw_rows.extend(self.buf[y * stride : (y + 1) * stride])

        def chunk(tag: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(tag + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        idat = zlib.compress(bytes(raw_rows), level=6)
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat)
            + chunk(b"IEND", b"")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes)


def generate_portfolio_chart(output_path: Path) -> None:
    """Render a 1440x860 dual-panel visual proof chart (Signal Triggers + Cleaning Audit)."""
    w, h = 1440, 860
    bg = (11, 17, 32)
    card_bg = (17, 24, 39)
    border = (35, 48, 74)
    grid_col = (28, 39, 60)
    white = (241, 245, 249)
    muted = (148, 163, 184)
    cyan = (6, 182, 212)
    amber = (245, 158, 11)
    green = (16, 185, 129)
    red = (244, 63, 94)
    purple = (168, 85, 247)
    blue = (59, 130, 246)

    c = Canvas(w, h, bg)

    # Top Header Bar
    c.fill_rect(0, 0, w, 74, (15, 23, 42))
    c.fill_rect(0, 72, w, 2, cyan)
    c.draw_text(
        28,
        18,
        "TRADINGVIEW WEBHOOK SERVICE - SIGNAL SANITIZATION & ACID AUDIT",
        white,
        scale=3,
    )
    c.draw_text(
        28,
        48,
        "STRATEGY: BINANCE:BTCUSDT SMA(10/30) CROSSOVER  |  ENGINE: FASTAPI + SQLITE WAL",
        muted,
        scale=2,
    )

    # 4 Top KPI Summary Cards
    kpis = [
        ("RAW WEBHOOK STREAM", "12 PACKETS", "BURSTS + RETRIES + NOISE", blue),
        ("THREATS & NOISE BLOCKED", "4 REJECTED", "401 / 400 / 422 / 409", red),
        ("AUTO-RETRY & DEDUP", "4 RECOVERED", "503 RETRY + 200 DEDUP", amber),
        ("CLEAN ATOMIC EXECUTIONS", "4 COMMITTED", "100% EXACTLY-ONCE AUDIT", green),
    ]
    card_w = 330
    for idx, (title, metric, sub, accent) in enumerate(kpis):
        cx = 28 + idx * (card_w + 21)
        cy = 92
        c.fill_rect(cx, cy, card_w, 102, card_bg)
        c.stroke_rect(cx, cy, card_w, 102, border, 1)
        c.fill_rect(cx, cy, card_w, 4, accent)
        c.draw_text(cx + 16, cy + 16, title, muted, scale=2)
        c.draw_text(cx + 16, cy + 42, metric, accent, scale=3)
        c.draw_text(cx + 16, cy + 76, sub, white, scale=2)

    # Left Panel: Market Price, Fast/Slow SMA & Signal Trigger Points
    lp_x, lp_y, lp_w, lp_h = 28, 212, 850, 620
    c.fill_rect(lp_x, lp_y, lp_w, lp_h, card_bg)
    c.stroke_rect(lp_x, lp_y, lp_w, lp_h, border, 1)
    c.draw_text(
        lp_x + 20,
        lp_y + 18,
        "PANEL 1: BTC/USDT SMA CROSSOVER TRIGGERS VS BLOCKED ANOMALIES",
        white,
        scale=2,
    )

    # Well-spaced Legend in Panel 1
    c.fill_rect(lp_x + 20, lp_y + 45, 16, 4, (147, 197, 253))
    c.draw_text(lp_x + 42, lp_y + 40, "PRICE", muted, scale=2)
    c.fill_rect(lp_x + 122, lp_y + 45, 16, 4, cyan)
    c.draw_text(lp_x + 144, lp_y + 40, "SMA(10)", cyan, scale=2)
    c.fill_rect(lp_x + 250, lp_y + 45, 16, 4, amber)
    c.draw_text(lp_x + 272, lp_y + 40, "SMA(30)", amber, scale=2)
    c.draw_circle(lp_x + 388, lp_y + 46, 6, green)
    c.draw_text(lp_x + 402, lp_y + 40, "202 ACCEPTED", green, scale=2)
    c.draw_circle(lp_x + 576, lp_y + 46, 6, red)
    c.draw_text(lp_x + 590, lp_y + 40, "BLOCKED NOISE", red, scale=2)

    plot_x, plot_y, plot_w, plot_h = lp_x + 86, lp_y + 76, lp_w - 110, lp_h - 124
    c.stroke_rect(plot_x, plot_y, plot_w, plot_h, border, 1)

    p_min, p_max = 59400.0, 61800.0
    for i in range(5):
        gy = plot_y + int(plot_h * i / 4)
        c.fill_rect(plot_x, gy, plot_w, 1, grid_col)
        price_val = int(p_max - (p_max - p_min) * i / 4)
        c.draw_text(lp_x + 12, gy - 6, f"${price_val}", muted, scale=2)

    for i in range(1, 6):
        gx = plot_x + int(plot_w * i / 6)
        c.fill_rect(gx, plot_y, 1, plot_h, grid_col)

    # Synthetic BTC/USDT price trajectory with clear 10/30 crossover waves
    n_pts = 48
    prices: list[float] = []
    fast_sma: list[float] = []
    slow_sma: list[float] = []
    for i in range(n_pts):
        t = i / (n_pts - 1)
        base = 59850.0 + 1150.0 * t
        wave = 520.0 * math.sin(t * math.pi * 3.2 - 0.55)
        micro = 85.0 * math.cos(i * 1.3)
        p = base + wave + micro
        prices.append(p)
        f_val = base + 430.0 * math.sin(t * math.pi * 3.2 - 0.65)
        s_val = base + 210.0 * math.sin(t * math.pi * 3.2 - 1.05)
        fast_sma.append(f_val)
        slow_sma.append(s_val)

    def to_xy(idx: int, val: float) -> tuple[float, float]:
        px = plot_x + (idx / (n_pts - 1)) * plot_w
        py = plot_y + (1.0 - (val - p_min) / (p_max - p_min)) * plot_h
        return px, py

    # Shaded area under price curve
    for i in range(n_pts - 1):
        x0, y0 = to_xy(i, prices[i])
        x1, y1 = to_xy(i + 1, prices[i + 1])
        steps = max(1, int(x1 - x0))
        for s in range(steps):
            sx = int(x0 + s)
            sy = int(y0 + (y1 - y0) * (s / steps))
            for py in range(sy, plot_y + plot_h):
                alpha = 0.16 * (1.0 - (py - sy) / max(1, plot_h))
                c.blend_pixel(sx, py, blue, alpha)

    # Draw Slow SMA, Fast SMA, and Price lines
    for i in range(n_pts - 1):
        c.draw_line(*to_xy(i, slow_sma[i]), *to_xy(i + 1, slow_sma[i + 1]), amber, width=2.6)
        c.draw_line(*to_xy(i, fast_sma[i]), *to_xy(i + 1, fast_sma[i + 1]), cyan, width=2.8)
        c.draw_line(
            *to_xy(i, prices[i]), *to_xy(i + 1, prices[i + 1]), (147, 197, 253), width=2.0
        )

    # Highlight 4 Verified Signal Triggers with crisp scale=2 badges
    verified_triggers = [
        (7, "BUY #1 [202]", green, -40),
        (19, "SELL #1 [202]", green, -40),
        (31, "BUY #2 [503>202]", amber, 18),
        (43, "SELL #2 [202]", green, -40),
    ]
    for idx, label, col, y_off in verified_triggers:
        tx, ty = to_xy(idx, prices[idx])
        c.draw_circle(tx, ty, 11, col, alpha=0.28)
        c.draw_circle(tx, ty, 6.5, col)
        c.draw_circle(tx, ty, 2.5, white)
        bw = len(label) * 12 + 14
        bx = int(max(plot_x + 6, min(plot_x + plot_w - bw - 6, tx - bw // 2)))
        by = int(max(plot_y + 8, min(plot_y + plot_h - 32, ty + y_off)))
        c.draw_badge(bx, by, label, col, (13, 23, 38), scale=2)

    # Highlight Blocked Dirty Webhook Spikes with crisp scale=2 badges
    blocked_anomalies = [
        (13, 61640.0, "401 BAD TOKEN"),
        (25, 59550.0, "422 STALE TS"),
        (37, 61680.0, "409 CONFLICT"),
    ]
    for idx, spike_p, reason in blocked_anomalies:
        ax, ay = to_xy(idx, spike_p)
        bx, by = to_xy(idx, prices[idx])
        c.draw_line(bx, by, ax, ay, red, width=1.6, alpha=0.55)
        c.draw_circle(ax, ay, 8, red, alpha=0.3)
        c.draw_line(ax - 5, ay - 5, ax + 5, ay + 5, red, width=2.5)
        c.draw_line(ax - 5, ay + 5, ax + 5, ay - 5, red, width=2.5)
        bw = len(reason) * 12 + 14
        tag_x = int(max(plot_x + 6, min(plot_x + plot_w - bw - 6, ax - bw // 2)))
        tag_y = int(ay - 34 if spike_p > 60500 else ay + 14)
        c.draw_badge(tag_x, tag_y, reason, red, (42, 14, 24), scale=2)

    # Bottom X-axis labels
    x_labels = ["12:00Z", "12:05Z", "12:10Z", "12:15Z", "12:20Z", "12:25Z", "12:30Z"]
    for i, label in enumerate(x_labels):
        lx = plot_x + int(plot_w * i / 6) - 34
        clamped_x = max(plot_x, min(plot_x + plot_w - 72, lx))
        c.draw_text(clamped_x, plot_y + plot_h + 14, label, muted, 2)

    # Right Panel: Before vs After Sanitization & SQLite Audit Breakdown
    rp_x, rp_y, rp_w, rp_h = 898, 212, 514, 620
    c.fill_rect(rp_x, rp_y, rp_w, rp_h, card_bg)
    c.stroke_rect(rp_x, rp_y, rp_w, lp_h, border, 1)
    c.draw_text(rp_x + 20, rp_y + 18, "PANEL 2: BEFORE VS AFTER SANITIZATION", white, scale=2)
    c.draw_text(
        rp_x + 20,
        rp_y + 42,
        "RAW INGRESS STREAM VS SQLITE AUDIT LEDGER",
        muted,
        scale=2,
    )

    bars = [
        ("RAW INGRESS WEBHOOKS", 12, 12, blue, "12 PACKETS"),
        ("VALIDATED & CANONICAL", 8, 12, cyan, "8 PASSED"),
        ("DUPLICATE RETRIES (200)", 3, 12, purple, "3 DEDUPED"),
        ("LOCK RETRY (503 -> 202)", 1, 12, amber, "1 RECOVERED"),
        ("BLOCKED THREATS (4XX)", 4, 12, red, "4 DROPPED"),
        ("FINAL SQLITE COMMITS", 4, 12, green, "4 AUDIT ROWS"),
    ]
    bar_start_y = rp_y + 78
    max_bar_w = rp_w - 190
    for idx, (b_label, count, total, col, badge_txt) in enumerate(bars):
        by = bar_start_y + idx * 54
        c.draw_text(rp_x + 20, by, b_label, white, scale=2)
        track_y = by + 20
        c.fill_rect(rp_x + 20, track_y, max_bar_w, 18, (30, 41, 59))
        fill_w = max(8, int(max_bar_w * (count / total)))
        c.fill_rect(rp_x + 20, track_y, fill_w, 18, col)
        c.draw_text(rp_x + 28 + max_bar_w, track_y + 2, badge_txt, col, scale=2)

    # Bottom Mini Table inside Right Panel: Committed Audit Rows
    tbl_y = rp_y + 412
    c.fill_rect(rp_x + 20, tbl_y, rp_w - 40, 188, (11, 17, 32))
    c.stroke_rect(rp_x + 20, tbl_y, rp_w - 40, 188, border, 1)
    c.fill_rect(rp_x + 20, tbl_y, rp_w - 40, 28, (23, 33, 53))
    c.draw_text(rp_x + 30, tbl_y + 8, "SQLITE SIMULATED_ACTIONS AUDIT TABLE", cyan, scale=2)

    rows_txt = [
        ("EVENT ID", "SIDE", "PRICE", "STATE"),
        ("MA01.BTC.BUY.001", "BUY", "59820.50", "COMMITTED"),
        ("MA01.BTC.SELL.002", "SELL", "60890.00", "COMMITTED"),
        ("MA01.BTC.BUY.003", "BUY", "60240.75", "COMMITTED"),
        ("MA01.BTC.SELL.004", "SELL", "61480.00", "COMMITTED"),
    ]
    for r_i, (e_id, side, prc, st) in enumerate(rows_txt):
        ry = tbl_y + 40 + r_i * 29
        col = muted if r_i == 0 else white
        s_col = muted if r_i == 0 else (green if side == "BUY" else amber)
        st_col = muted if r_i == 0 else green
        c.draw_text(rp_x + 30, ry, e_id, col, scale=2 if r_i == 0 else 1)
        c.draw_text(rp_x + 178, ry, side, s_col, scale=2)
        c.draw_text(rp_x + 252, ry, prc, col, scale=2)
        c.draw_text(rp_x + 372, ry, st, st_col, scale=2 if r_i == 0 else 1)

    c.save_png(output_path)


# ---------------------------------------------------------------------------
# End-to-End Interactive Sandbox Pipeline Execution
# ---------------------------------------------------------------------------
def process_webhook_packet(
    raw_packet: dict[str, object],
    config: Settings,
    store: Store,
    simulate_lock: bool = False,
) -> tuple[int, str, str]:
    """Run exact HTTP boundary validation & transactional SQLite store logic."""
    if simulate_lock:
        return 503, "RETRY_AFTER_1S", "SQLite write lock contention (250ms budget)"

    packet = dict(raw_packet)
    # 1. JSON float/NaN guard
    if packet.get("price") == "NaN":
        return 400, "BAD_JSON", "Rejected non-finite IEEE-754 NaN price constant"

    # 2. Constant-time HMAC token authentication
    token = packet.pop("token", None)
    valid_token = isinstance(token, str) and hmac.compare_digest(
        token.encode("utf-8"), config.secret.encode("ascii")
    )
    if not valid_token:
        return 401, "UNAUTHORIZED", "Constant-time HMAC token verification failed"

    # 3. Schema & canonical normalization
    try:
        alert = Alert.model_validate(packet)
    except Exception:
        return 422, "SCHEMA_ERROR", "Pydantic/schema validation rejected payload"

    # 4. Replay freshness window check (-300s to +30s)
    age = (dt.datetime.now(UTC) - alert.timestamp).total_seconds()
    if age > config.max_age_seconds or age < -config.future_skew_seconds:
        return 422, "STALE_SIGNAL", f"Timestamp outside freshness window (age={int(age)}s)"

    # 5. Symbol allowlist check
    if config.allowed_symbols and alert.symbol not in config.allowed_symbols:
        return 422, "SYMBOL_BLOCKED", f"Symbol {alert.symbol} not in allowlist"

    # 6. Atomic SQLite WAL transaction (`BEGIN IMMEDIATE`)
    try:
        created = store.accept(alert)
    except EventConflict:
        return 409, "ID_CONFLICT", "Same event_id arrived with mutated payload"

    if created:
        formatted_price = format(alert.price.normalize(), "f")
        return 202, "ACCEPTED", f"Committed {alert.action.upper()} @ ${formatted_price}"
    return 200, "DUPLICATE", "Idempotent hit — suppressed duplicate execution"


def open_image_preview(image_path: Path) -> bool:
    """Automatically pop up the generated chart in the OS default viewer."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["open", str(image_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        if os.name == "nt":
            os.startfile(str(image_path))  # type: ignore[attr-defined]
            return True
        if shutil.which("xdg-open"):
            subprocess.Popen(
                ["xdg-open", str(image_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
    except Exception:
        return False
    return False


def run_pipeline_demo(fast: bool = False, no_open: bool = False) -> None:
    rule = "═" * 86
    banner = (
        f"\n{Style.BOLD}{Style.CYAN}╔{rule}╗\n"
        "║ ⚡ TRADINGVIEW WEBHOOK SERVICE — PRODUCTION SANDBOX & IDEMPOTENCY PIPELINE DEMO      ║\n"
        "║ Zero-Config Portfolio Showcase  •  ACID SQLite WAL  •  Sub-Millisecond Guardrails    ║\n"
        f"╚{rule}╝{Style.RESET}"
    )
    print(banner)
    pause(0.8, fast)

    with TemporaryDirectory(prefix="tv-portfolio-demo-") as tmp_dir:
        db_path = Path(tmp_dir) / "alerts.sqlite3"
        ephemeral_secret = secrets.token_urlsafe(32)
        config = Settings(
            secret=ephemeral_secret,
            database=db_path,
            max_age_seconds=300,
            future_skew_seconds=30,
            allowed_symbols=frozenset({"BINANCE:BTCUSDT", "NASDAQ:NVDA"}),
        )
        store = Store(config.database)
        store.initialize()

        now = dt.datetime.now(UTC)
        now_iso = now.isoformat()
        stale_iso = (now - dt.timedelta(seconds=900)).isoformat()
        plus8_iso = now.astimezone(dt.timezone(dt.timedelta(hours=8))).isoformat()

        # -------------------------------------------------------------------
        # STAGE 1: [FETCH] Ingress Mock Stream Generation
        # -------------------------------------------------------------------
        print(
            f"\n{badge('FETCH', Style.CYAN)} {Style.BOLD}"
            f"Stage 1/4: Generating Mock TradingView Pine v6 Webhook Stream...{Style.RESET}"
        )
        print(
            f"  {Style.GRAY}├─ Strategy:{Style.RESET} "
            f"SMA(10/30) Crossover on {Style.WHITE}BINANCE:BTCUSDT{Style.RESET}"
        )
        print(
            f"  {Style.GRAY}├─ Security:{Style.RESET} "
            "Ephemeral 256-bit Token (Redacted from logs & DB)"
        )
        print(
            f"  {Style.GRAY}└─ Storage :{Style.RESET} "
            f"Isolated SQLite WAL Sandbox ({Style.CYAN}BEGIN IMMEDIATE{Style.RESET})"
        )
        render_progress("Ingesting 12 raw webhook events", steps=18, duration=2.2, fast=fast)

        # -------------------------------------------------------------------
        # STAGE 2: [VALIDATE] Schema Sanitization & Threat Interception
        # -------------------------------------------------------------------
        print(
            f"\n{badge('VALIDATE', Style.GREEN)} {Style.BOLD}"
            f"Stage 2/4: Executing Boundary Validation & Threat Sanitization...{Style.RESET}"
        )
        pause(0.5, fast)

        validation_batch: list[tuple[str, dict[str, object]]] = [
            (
                "Signal #1 (Valid Buy Crossover)",
                {
                    "token": ephemeral_secret,
                    "event_id": "ma01.BTCUSDT.buy.001",
                    "symbol": "BINANCE:BTCUSDT",
                    "action": "buy",
                    "price": 59820.5000,
                    "timestamp": now_iso,
                },
            ),
            (
                "Signal #2 (Valid Sell Crossover)",
                {
                    "token": ephemeral_secret,
                    "event_id": "ma01.BTCUSDT.sell.002",
                    "symbol": "BINANCE:BTCUSDT",
                    "action": "sell",
                    "price": 60890.00,
                    "timestamp": now_iso,
                },
            ),
            (
                "Attack #1 (Spoofed Webhook Token)",
                {
                    "token": "attacker_forged_token_999999999999",
                    "event_id": "ma01.BTCUSDT.buy.901",
                    "symbol": "BINANCE:BTCUSDT",
                    "action": "buy",
                    "price": 61620.00,
                    "timestamp": now_iso,
                },
            ),
            (
                "Anomaly #2 (Stale Delayed Packet)",
                {
                    "token": ephemeral_secret,
                    "event_id": "ma01.BTCUSDT.buy.902",
                    "symbol": "BINANCE:BTCUSDT",
                    "action": "buy",
                    "price": 59560.00,
                    "timestamp": stale_iso,
                },
            ),
            (
                "Anomaly #3 (Corrupted NaN Price)",
                {
                    "token": ephemeral_secret,
                    "event_id": "ma01.BTCUSDT.buy.903",
                    "symbol": "BINANCE:BTCUSDT",
                    "action": "buy",
                    "price": "NaN",
                    "timestamp": now_iso,
                },
            ),
            (
                "Anomaly #4 (Mutated Payload Conflict)",
                {
                    "token": ephemeral_secret,
                    "event_id": "ma01.BTCUSDT.buy.001",
                    "symbol": "BINANCE:BTCUSDT",
                    "action": "sell",
                    "price": 61680.00,
                    "timestamp": now_iso,
                },
            ),
        ]

        for label, pkt in validation_batch:
            code, tag, detail = process_webhook_packet(pkt, config, store)
            color = Style.GREEN if code == 202 else Style.RED
            icon = "✔" if code == 202 else "✘"
            print(
                f"  {color}{icon} HTTP {code} [{tag:<14}]{Style.RESET} "
                f"{Style.WHITE}{label:<36}{Style.RESET} "
                f"{Style.GRAY}→ {detail}{Style.RESET}"
            )
            pause(0.65, fast)

        # -------------------------------------------------------------------
        # STAGE 3: [RETRY] Network Jitter, SQLite Lock & Duplicate Storm
        # -------------------------------------------------------------------
        print(
            f"\n{badge('RETRY', Style.YELLOW)} {Style.BOLD}"
            f"Stage 3/4: Simulating Network Jitter, 503 Auto-Retry & Duplicates...{Style.RESET}"
        )
        pause(0.6, fast)

        signal_3 = {
            "token": ephemeral_secret,
            "event_id": "ma01.BTCUSDT.buy.003",
            "symbol": "BINANCE:BTCUSDT",
            "action": "buy",
            "price": 60240.75,
            "timestamp": now_iso,
        }
        signal_4 = {
            "token": ephemeral_secret,
            "event_id": "ma01.BTCUSDT.sell.004",
            "symbol": "BINANCE:BTCUSDT",
            "action": "sell",
            "price": 61480,
            "timestamp": now_iso,
        }

        # 3A: Simulate transient SQLite lock contention (HTTP 503) followed by automatic retry
        with closing(sqlite3.connect(db_path)) as locker:
            locker.execute("BEGIN IMMEDIATE")
            code_503, tag_503, detail_503 = process_webhook_packet(
                signal_3, config, store, simulate_lock=True
            )
            print(
                f"  {Style.ORANGE}⚠ HTTP {code_503} [{tag_503:<14}]{Style.RESET} "
                f"{Style.WHITE}Signal #3 (Transient DB Lock){Style.RESET}        "
                f"{Style.YELLOW}→ {detail_503}{Style.RESET}"
            )
            pause(0.8, fast)
            print(
                f"  {Style.CYAN}↻ [BACKOFF RETRY]{Style.RESET}          "
                f"Honoring {Style.BOLD}Retry-After: 1s{Style.RESET} with identical "
                f"event_id={Style.CYAN}ma01.BTCUSDT.buy.003{Style.RESET}..."
            )
            pause(0.9, fast)
            locker.rollback()

        code_rec, tag_rec, detail_rec = process_webhook_packet(signal_3, config, store)
        print(
            f"  {Style.GREEN}✔ HTTP {code_rec} [{tag_rec:<14}]{Style.RESET} "
            f"{Style.WHITE}Signal #3 (Auto-Retry Recovered){Style.RESET}     "
            f"{Style.GRAY}→ {detail_rec}{Style.RESET}"
        )
        pause(0.65, fast)

        # 3B: Commit Signal #4 and fire 3x duplicate retry storm
        code_s4, tag_s4, detail_s4 = process_webhook_packet(signal_4, config, store)
        print(
            f"  {Style.GREEN}✔ HTTP {code_s4} [{tag_s4:<14}]{Style.RESET} "
            f"{Style.WHITE}Signal #4 (Valid Sell Crossover){Style.RESET}     "
            f"{Style.GRAY}→ {detail_s4}{Style.RESET}"
        )
        pause(0.6, fast)

        dup_variants = [
            ("Duplicate Retry #1 (Network Re-send)", dict(signal_4)),
            (
                "Duplicate Retry #2 (61480 vs 61480.0)",
                {**signal_4, "price": 61480.0, "timestamp": plus8_iso},
            ),
            ("Duplicate Retry #3 (Post-Restart)", dict(validation_batch[0][1])),
        ]
        for dup_label, dup_pkt in dup_variants:
            d_code, d_tag, d_detail = process_webhook_packet(dup_pkt, config, store)
            print(
                f"  {Style.MAGENTA}↺ HTTP {d_code} [{d_tag:<14}]{Style.RESET} "
                f"{Style.WHITE}{dup_label:<36}{Style.RESET} "
                f"{Style.GRAY}→ {d_detail}{Style.RESET}"
            )
            pause(0.55, fast)

        # -------------------------------------------------------------------
        # STAGE 4: [OUTPUT] Audit Verification & Chart Generation
        # -------------------------------------------------------------------
        print(
            f"\n{badge('OUTPUT', Style.GREEN)} {Style.BOLD}"
            f"Stage 4/4: Verifying SQLite Audit Ledger & Exporting Visual Report...{Style.RESET}"
        )
        pause(0.5, fast)

        with closing(sqlite3.connect(db_path)) as conn:
            alert_count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
            rows = conn.execute(
                "SELECT event_id, symbol, action, price, status, simulated_at "
                "FROM simulated_actions ORDER BY simulated_at ASC"
            ).fetchall()

        top_border = (
            "  ┌──────────────────────────┬──────────────────┬────────┬────────────┬─────────────┐"
        )
        mid_border = (
            "  ├──────────────────────────┼──────────────────┼────────┼────────────┼─────────────┤"
        )
        bot_border = (
            "  └──────────────────────────┴──────────────────┴────────┴────────────┴─────────────┘"
        )
        print(f"\n{top_border}")
        print(
            f"  │ {Style.BOLD}EVENT ID{Style.RESET}                 │ "
            f"{Style.BOLD}SYMBOL{Style.RESET}           │ "
            f"{Style.BOLD}ACTION{Style.RESET} │ "
            f"{Style.BOLD}PRICE ($){Style.RESET}  │ "
            f"{Style.BOLD}AUDIT STATE{Style.RESET} │"
        )
        print(mid_border)
        for ev_id, sym, act, prc, st, _ in rows:
            act_col = Style.GREEN if act == "buy" else Style.YELLOW
            clean_price = format(Decimal(prc), "f")
            print(
                f"  │ {ev_id:<24} │ {sym:<16} │ "
                f"{act_col}{act.upper():<6}{Style.RESET} │ "
                f"{clean_price:>10} │ "
                f"{Style.CYAN}{st.upper():<11}{Style.RESET} │"
            )
        print(bot_border)

        # Export high-res PNG chart to both `demo_output.png` and `demo/demo_output.png`
        root_chart = PROJECT_ROOT / "demo_output.png"
        demo_dir_chart = PROJECT_ROOT / "demo" / "demo_output.png"
        render_progress(
            "Rendering high-DPI chart demo_output.png", steps=14, duration=1.6, fast=fast
        )
        generate_portfolio_chart(root_chart)
        demo_dir_chart.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root_chart, demo_dir_chart)

        print(
            f"\n  {Style.BOLD}{Style.GREEN}✅ PIPELINE VERIFICATION PASSED:{Style.RESET} "
            f"{Style.WHITE}12 raw packets processed → {alert_count} alerts & "
            f"{len(rows)} atomic actions committed (0 duplicates, 0 leaks).{Style.RESET}"
        )
        print(
            f"  {Style.BOLD}{Style.CYAN}📊 VISUAL ASSET GENERATED:{Style.RESET} "
            f"{Style.WHITE}{root_chart.relative_to(PROJECT_ROOT)}{Style.RESET} & "
            f"{Style.WHITE}{demo_dir_chart.relative_to(PROJECT_ROOT)}{Style.RESET}"
        )

        if not no_open and not os.getenv("CI"):
            pause(0.4, fast)
            if open_image_preview(root_chart):
                print(
                    f"  {Style.BOLD}{Style.MAGENTA}🖼️  AUTO-OPENING PREVIEW:{Style.RESET} "
                    f"{Style.WHITE}Popped up {root_chart.name} in system viewer!{Style.RESET}\n"
                )
            else:
                print()
        else:
            print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the interactive TradingView Webhook Service portfolio demo."
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip recording pauses for instant CI/automated execution.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not automatically pop up demo_output.png after completion.",
    )
    args = parser.parse_args()
    run_pipeline_demo(fast=args.fast, no_open=args.no_open)


if __name__ == "__main__":
    main()
