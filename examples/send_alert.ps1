# Run in a second terminal with the SAME WEBHOOK_SECRET as the running server.
$ErrorActionPreference = 'Stop'
if (-not $env:WEBHOOK_SECRET) { throw 'Set WEBHOOK_SECRET to the server token first.' }
$alert = @{
    token = $env:WEBHOOK_SECRET
    event_id = 'local.' + [guid]::NewGuid().ToString('N')
    symbol = 'BINANCE:BTCUSDT'
    action = 'buy'
    price = 60000.25
    timestamp = [DateTimeOffset]::UtcNow.ToString('o')
} | ConvertTo-Json -Compress
# The same serialized payload and event ID are reused: accepted, then duplicate.
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/webhook' -Method Post -ContentType 'application/json' -Body $alert
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/webhook' -Method Post -ContentType 'application/json' -Body $alert
