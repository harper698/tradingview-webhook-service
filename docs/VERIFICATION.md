# Verification record

Verified locally on **2026-10-04** using Windows, Python **3.12.10**, FastAPI **0.142.2**,
Pydantic **2.13.5**, Uvicorn **0.54.0**, and SQLite from the Python standard library.
The exact runtime dependencies are recorded in [`requirements-lock.txt`](../requirements-lock.txt).

## Reproducible checks

Run from the repository root after `python -m pip install -e ".[dev]"`:

| Command | Observed result |
| --- | --- |
| `python -m pytest -q` | **77 passed**, including the demo subprocess and cleanup |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | All checked files already formatted |
| `python -m pip check` | No broken requirements found |
| `python examples/demo.py` | Accepted 202, duplicate 200, conflict 409, unauthorized 401, restart duplicate 200; exit 0 |
| `python -m build` | Wheel and source distribution built successfully |

The offline demo ends with exactly one row in `alerts` and one in `simulated_actions`.
Its database and ephemeral credential are temporary; no network connection is used.

## Real local HTTP checks

A Uvicorn process was started on loopback and called using real HTTP requests. Observed responses:

```text
first request:              202 accepted
identical retry:            200 duplicate
same ID, changed action:    409 conflict
incorrect token:           401 unauthorized
after process restart:     200 duplicate
persisted rows:            1 alert, 1 simulated action
token in audit payload:    absent
```

Both shipped client examples were additionally executed against a running local server:

- `python examples/send_alert.py` returned accepted then duplicate.
- `pwsh -NoProfile -File examples/send_alert.ps1` returned accepted then duplicate.

Each local verification server was stopped afterward. No public endpoint was exposed.

## Meaningful failure cases

- Missing, incorrect, non-string, Unicode and malformed-surrogate tokens fail without reflecting
  the credential or crashing. The configuration representation hides its secret.
- Invalid JSON, repeated object keys, `NaN`/infinities, huge decimal exponents and deep nesting
  receive controlled errors. Unknown fields do not expose user-controlled values in validation errors.
- Body limits are tested for ordinary and streaming requests, including missing or misleading
  `Content-Length`. Compressed requests are rejected. JSON media type matching is case-insensitive.
- Field tests reject nonpositive, oversized, over-precise, string and boolean prices; invalid IDs,
  symbols and actions; naive/invalid timestamps; stale alerts and excessive future clock skew.
- Canonical numeric and UTC values deduplicate even if their original representations differ.
- Twenty-four concurrent identical requests produce one `202`, twenty-three `200` responses and
  exactly one simulated action in the tested local run.
- A SQLite trigger forces the mock insertion to fail: no orphan alert is committed, and a retry
  succeeds after the fault is removed. Lock contention returns `503` with `Retry-After: 1`.
- A subprocess test ensures the offline demo closes SQLite handles before deleting its temporary
  directory, including on Windows.

## Scope and limits

The CI workflow is configured for Python 3.11, 3.12 and 3.13 on Ubuntu 24.04; consult the
[Actions page](https://github.com/harper698/tradingview-webhook-service/actions) for actual hosted
run results. This local record does not assert a hosted run succeeded before it occurred.

The Pine example and TradingView setup guidance were checked against official documentation.
They have not been compiled in Pine Editor or verified with an actual TradingView alert delivery.
No broker connection, live trade, public deployment, load benchmark or latency SLA was tested or
claimed. The idempotence guarantee covers retained records within one SQLite database, not an
external side effect. The test count is a verification result, not a claim of production readiness.
