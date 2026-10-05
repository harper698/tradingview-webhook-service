# TradingView alert setup

The receiver accepts ordinary JSON webhook messages. The Python/PowerShell examples are tested
against local HTTP; the platform instructions and Pine example below are based on official
documentation, and have not been compiled or delivered through a live TradingView account.

## Prerequisites

TradingView sends JSON alert messages as `application/json`; valid JSON is therefore required.
Its webhook requests support ports 80 and 443, have a three-second processing timeout, do not
currently support IPv6, and require two-factor authentication on the TradingView account.
These constraints are documented in [TradingView's webhook guide](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/).

For actual delivery, place the service behind a public HTTPS endpoint on port 443. Configure
the alert webhook URL as `https://YOUR_HOST/webhook`. `127.0.0.1:8000` is only reachable locally.
The repository does not provision hosting, tunnels, DNS or TLS.

## Message template

Paste the contents of [`examples/alert-message.txt`](../examples/alert-message.txt) into the
TradingView alert message field. This is a template; `{{close}}` becomes a number when the alert
fires, so the file is intentionally named `.txt` rather than `.json`.

```text
{"token":"REPLACE_WITH_YOUR_SCOPED_WEBHOOK_TOKEN","event_id":"ma01-{{exchange}}-{{ticker}}-{{interval}}-{{timenow}}-buy","symbol":"{{exchange}}:{{ticker}}","action":"buy","price":{{close}},"timestamp":"{{timenow}}"}
```

1. Replace the token in the private alert dialog with the server's dedicated webhook token.
   This is a scoped routing credential, never an account password or brokerage API key.
2. Give each alert rule its own stable prefix in place of `ma01`; keep `buy`/`sell` consistent.
3. Keep `price` unquoted. A quoted `"{{close}}"` becomes a string and is rejected by this service.
4. Use `{{timenow}}` for freshness: it represents alert fire time. `{{time}}` represents bar time
   and can be stale on longer chart intervals. See the [official placeholder reference](https://www.tradingview.com/support/solutions/43000531021-how-to-use-a-variable-value-in-alert/).
5. Configure signals once per bar close for this example. `{{timenow}}` has one-second precision;
   the same rule, symbol, interval and action firing more than once in a second can collide.
   High-frequency use needs a producer-generated unique logical signal ID.
6. Confirm the resolved event ID is within 128 characters and the symbol matches the service's
   allowlist. This example targets ordinary stock/crypto symbols that fit the documented schema.

An ID must stay unchanged when retrying a logical signal. A duplicate inside the freshness window
returns `200`. A different payload with the same ID returns `409`; an expired timestamp returns
`422`, even when its ID has been seen before. Consult the TradingView alert log for delivery status;
this service does not rely on undocumented retry behavior.

## Optional Pine Script example

[`examples/ma_alert.pine`](../examples/ma_alert.pine) defines a Pine v6 indicator with 10/30-period
moving averages and separate buy/sell `alertcondition()` calls. It draws indicators and emits
conditions; it does not submit orders. The structure follows the [official Pine alerts documentation](https://www.tradingview.com/pine-script-docs/concepts/alerts/).

Copy it into Pine Editor, add it to a chart, and create separate alerts from the Buy signal and
Sell signal conditions. Choose once per bar close. Replace the token and unique rule prefix in
each alert's message dialog. Do not publish a script containing your token. The placeholder token
checked into this repository is not a working credential.

Clock synchronization, HTTPS, ingress limits, body-log redaction and durable disk ownership are
deployment responsibilities. The built-in SQLite timeout is 250 ms, but actual delivery latency
also depends on your network and deployment. No live-delivery SLA is claimed.
