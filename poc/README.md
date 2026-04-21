# RLCC POC

Single-box POC for Ram Ki Bandi, Nizami Daawat, KFC, and Haldiram's-AeroPlaza.

## Ports

- `8000` CV debug service
- `8001` RLCC backend and Nukkad push API
- `5173` dashboard
- `6379` Redis on `127.0.0.1`

## Push API

- Method: `POST`
- Path: `/v1/rlcc/launch-event`
- Example: `http://<server-ip>:8001/v1/rlcc/launch-event`
- Header: `x-authorization-key: <NUKKAD_PUSH_AUTH_KEY>`
- Body: `application/json`; the receiver accepts both normal JSON and stringified JSON payloads

## Sales Pull API

- Source: `EXTERNAL_SALES_URL` + `EXTERNAL_SALES_HEADER_TOKEN`
- Use: historical backfill, recent reconciliation, and pre-push dashboard population
- Background sync: every `SALES_RECONCILIATION_MINUTES` minutes
- Manual trigger: `GET /api/history?days=5`

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
- maintain the four-store catalog
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
- The seed config is trimmed to Ram Ki Bandi, Nizami Daawat, KFC, and Haldiram's-AeroPlaza.
- Saved clips are trimmed around each transaction or missing-POS alert and retained for 2 days.
- The RLCC CV detector now follows the same default profile as the older `fds-cv` stack: `yolov8m` on GPU, `yolov8s` on CPU. Override with `YOLO_MODEL_PATH` only if you want a different model.
- If push is not live yet, the dashboard still populates from the sales pull API. Missing-POS alerts stay suppressed until push traffic is actually seen for that store.
