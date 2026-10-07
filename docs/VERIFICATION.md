# Verification record

## 2026-10-07 joint verification — c2c_a490

Rechecked commit **`8e1cf649d7f5fe16dd34afeef0754ed513763da7`** on Windows with Python
**3.12.10**, SQLite **3.49.1**, FastAPI **0.142.2**, Pydantic **2.13.5**, and Uvicorn
**0.54.0**. The initial working tree was clean. This pass found no reproducible defect in the
application; production code and checked-in tests were unchanged. This verification record is
the only tracked change from the pass.

| Check | Observed result |
| --- | --- |
| HEAD, working tree, diff and whitespace checks | Recorded; initial tree clean, `git diff --check` exit 0 |
| Python version and `python -m pip check` | Python 3.12.10; no broken requirements |
| Ruff lint and format checks | Both exit 0 |
| `python -m pytest -q` | **77 passed** |
| `python -m build` | Wheel and source distribution built; exit 0 |
| `python examples/demo.py` | Expected acceptance/duplicate/conflict/authentication/restart results; exit 0 |
| Additional instrumented/ASGI/real HTTP checks | **54 passed**, separate from the pytest count |

The additional local Uvicorn run bound only to **127.0.0.1:8000**. Sixty-four identical requests
sent by 32 client workers produced exactly **one 202** and **sixty-three 200** responses, with
one alert and one simulated action for that ID. The two published clients,
`examples/send_alert.py` and `examples/send_alert.ps1`, both exited 0 and returned accepted then
duplicate. Stopping and restarting the actual server process preserved duplicate detection.

The same run checked Unicode and malformed-surrogate credentials; missing/non-string tokens;
stale, future, naive and overflowing timestamps; symbol syntax and allowlist enforcement;
unknown fields; numeric strings/booleans, extreme numbers and precision limits; duplicate JSON
keys; 1,500-level JSON nesting; non-finite numbers and extreme decimal exponents. All received
the expected controlled response. Oversized ordinary and **real chunked HTTP** bodies returned
413; compressed bodies returned 415. Separately, the **in-process ASGI transport** verified the
16 KiB stream limit both without Content-Length and with a misleading value of `1`. This is an
application-boundary test, not a claim that a real HTTP server accepts inconsistent framing.

A held SQLite write lock returned **503 with Retry-After: 1**, without recording the event;
the unchanged request succeeded after release. An injected SQLite trigger failure in the mock
dispatch returned **500** and left **zero rows in both tables** for the event; removing the
trigger allowed a successful retry. Canonical decimal/timezone representations deduplicated,
and a changed payload under an existing ID returned 409. At completion, the six distinct
accepted events had six corresponding mock rows, and `PRAGMA integrity_check` returned `ok`.

Source review and instrumentation confirmed that authentication uses byte arguments with
`hmac.compare_digest` and removes the token before schema validation and storage. This verifies
the constant-time comparison primitive, not the timing behavior of the whole HTTP service.
Generated tokens were absent from captured server/client output, HTTP responses, SQLite records
and database bytes. Tokens existed only in process memory/environment; no sensitive request body
was saved. Every verification server was stopped, and the loopback port was confirmed released.

**Harness correction:** the first extended concurrency attempt timed out because its verification
script did not drain the server's stdout pipe during requests (4,073 bytes were captured before
the stall). The local verification script was corrected to drain both output streams in background
threads. The subsequent HTTP checks passed without changing application code. The first attempt's
log and summary were retained; already-passing baseline checks were reused at the same unchanged
HEAD instead of rerun.

Local evidence, intentionally excluded from Git by `output/`, is retained at:

- `output/c2c-verification-20261007/evidence.log`: command stdout, stderr and exit codes, both
  HTTP attempts, controlled error outcomes, and server termination records.
- `output/c2c-verification-20261007/summary.json`: execution results captured before the independent review.
- `output/c2c-verification-20261007/attempt1-summary.json`: the original harness failure.
- `output/c2c-verification-20261007/verify.py`: local verification procedure; no embedded secret.

The coordinating agent checked the GitHub API and remote branch on 2026-10-07: local HEAD and
remote `main` matched the commit above, and existing [CI run 37257661092](https://github.com/harper698/tradingview-webhook-service/actions/runs/37257661092)
had successful Python 3.11, 3.12 and 3.13 jobs. This was verification of an **existing run**, not
a newly triggered hosted run. Its raw evidence is in the parent workspace's
`verification-20261007/hosted-ci.json`.

**Independent ChatGPT final review passed on 2026-10-07 (task `c2c_a490`).** The reviewer read all
19 execution-evidence parts across the three repositories and independently inspected the five
changed files. No further implementation change was requested. No public deployment,
actual TradingView alert delivery, Pine compilation, broker access, or live order was performed.
Local concurrency observations are not a throughput benchmark or latency SLA.

## Historical record — 2026-10-04

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
