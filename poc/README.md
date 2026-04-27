# RLCC POC

Single-box POC for the airport store rollout. The active store list lives in
[`config/stores.json`](config/stores.json) and is edited on the server (or
through the dashboard's Store Config view).

## Ports

- `8000` CV debug service
- `8001` RLCC backend (Nukkad push API + dashboard API)
- `5173` dashboard
- `6379` Redis on `127.0.0.1`

## Push API (Nukkad → RLCC)

Nukkad pushes POS events to nine event-typed endpoints. Auth header is
`x-authorization-key: <NUKKAD_PUSH_AUTH_KEY>` on every request, body is
`application/json` (the receiver accepts both normal and stringified JSON
payloads). Each endpoint mirrors one `event` from the RLCC API spec; the
payload's `event` field must match the route or the request is rejected
with HTTP 400.

| Event | Path |
|---|---|
| `BeginTransactionWithTillLookup`       | `POST /v1/rlcc/begin-transaction-with-till-lookup` |
| `AddTransactionEvent`                  | `POST /v1/rlcc/add-transaction-event` |
| `AddTransactionPaymentLine`            | `POST /v1/rlcc/add-transaction-payment-line` |
| `AddTransactionSaleLine`               | `POST /v1/rlcc/add-transaction-sale-line` |
| `AddTransactionSaleLineWithTillLookup` | `POST /v1/rlcc/add-transaction-sale-line-with-till-lookup` |
| `AddTransactionTotalLine`              | `POST /v1/rlcc/add-transaction-total-line` |
| `CommitTransaction`                    | `POST /v1/rlcc/commit-transaction` |
| `GetTill`                              | `POST /v1/rlcc/get-till` |
| `BillReprint`                          | `POST /v1/rlcc/bill-reprint` |

Field-level schemas for each event are in `RLCC API Documentation.pdf`
(section 4) and mirrored in [INTEGRATION.md](../INTEGRATION.md).

There is no pull API. Historical data comes from the JSONL the push
receiver writes to (`data/transactions.jsonl`); `GET /api/history?days=N`
returns a date-filtered view of those persisted transactions.

To smoke-test all nine endpoints after deploy:

```bash
python3 poc/scripts/verify_push_endpoints.py \
    --base-url http://localhost:8001 \
    --auth-key "$NUKKAD_PUSH_AUTH_KEY"
```

## Required Files

- `poc/.env`
- `poc/config/stores.json`
- `poc/config/camera_mapping.json`
- `poc/config/rule_config.json`

Start by copying `poc/.env.example` to `poc/.env`.

## Install

From repo root:

```bash
./bootstrap.sh
```

`bootstrap.sh`:

- installs missing Ubuntu packages
- creates `poc/.venv`
- installs backend, CV, and test Python packages
- installs Node 20 if needed
- installs dashboard dependencies
- reuses an existing system Torch install when available, so `pytorch.org` is not required by default

## Run

From repo root:

```bash
./start.sh
```

Stop:

```bash
./start.sh stop
```

## Store Config Workflow

After startup:

- open `http://<server-ip>:5173`
- go to `Store Config`
- maintain the active store catalog
- verify each camera's `store_id`, `camera_id`, `POS Terminal No`, and `rtsp_url`
- draw `seller_zone` and `bill_zone` polygons on the live frame
- click `Save Store Config`

The dashboard saves both `stores.json` and `camera_mapping.json`. Saving also asks the CV service to reload, so updated RTSP URLs and zones apply without a full VM restart.

## Runtime Paths

- WAL events: `poc/data/events/`
- transaction store: `poc/data/transactions.jsonl`
- alert store: `poc/data/alerts.jsonl`
- rolling video buffer: `poc/data/buffer/`
- saved clips: `poc/data/snippets/`
- logs: `poc/logs/`

## Notes

- Use the dashboard Store Config view for RTSP updates and zone drawing. Manual file edits are optional, not required.
- Saved clips are trimmed around each transaction or missing-POS alert and retained for 2 days.
- The RLCC CV detector now follows the same default profile as the older `fds-cv` stack: `yolov8m` on GPU, `yolov8s` on CPU. Override with `YOLO_MODEL_PATH` only if you want a different model.
- Until Nukkad starts pushing for a given store, missing-POS alerts stay suppressed for that store (no push events seen yet).
