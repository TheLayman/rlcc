# POC — 5 Stores on T4 Workstation

## Hardware

Single machine. Tesla T4 (16 GB VRAM), 32 GB RAM. Runs everything: CV inference, backend, dashboard.

## Scope

5 stores, 1 camera per store (POS-facing), 5 RTSP streams total. All Posifly POS (push API supported). Existing 3 stores have RTSP URLs, mappings, and zone polygons. 2 new stores need zone polygons drawn (5 min each via zone tool).

## Architecture

```
┌─────────────────────────────────────────────────┐
│  T4 Workstation                                 │
│                                                 │
│  5 RTSP streams (1 per store)                   │
│       │                                         │
│       ▼                                         │
│  CV Service (1 process, 5 camera threads)       │
│    1 shared YOLO model on T4                    │
│    Per-camera: detect persons, zone classify,   │
│    bill zone motion/bg change                   │
│    Publishes signals → Redis (localhost)         │
│    Rolling 15-min video buffer per camera        │
│    (ffmpeg segments on disk)                    │
│       │                                         │
│       ▼ Redis pub/sub                           │
│  Backend (FastAPI)                              │
│    Nukkad push receiver                         │
│    CV signal consumer                           │
│    Transaction assembler                        │
│    Correlation engine                           │
│    Fraud engine (29 rules)                      │
│    Sales API poller (reconciliation)            │
│    Event timeline builder                       │
│    REST API + WebSocket                         │
│    Serves React dashboard                       │
│       │                                         │
│       ▼                                         │
│  Dashboard (browser)                            │
│                                                 │
│  Redis (localhost, replaces MQTT for POC)        │
│  Storage: JSONL on disk                         │
└─────────────────────────────────────────────────┘
```

No edge devices, no MQTT broker, no distributed system. Redis pub/sub on localhost replaces MQTT. Same signal semantics — production switches to MQTT when we move to edge hardware.

## Components to build

### 1. CV Service

Single Python process. New code, simplified from POC pipeline.

**What it does (per camera, per frame at 5-6 FPS):**
- Grab RTSP frame (OpenCV, 1 thread per camera)
- YOLO detect persons (shared model on T4, can batch across cameras)
- Check seller zone polygon → `seller: true/false`
- Bill zone frame differencing → `bill_motion: true/false`
- Bill zone background comparison → `bill_bg: true/false`
- Count persons not in any seller zone → `non_seller_count`

**What it emits (Redis pub/sub):**
```json
{
  "ts": "2026-04-16T10:02:05.123Z",
  "camera_id": "cam-03",
  "zones": [
    {"pos_zone": "POS3", "seller": true, "bill_motion": false, "bill_bg": false}
  ],
  "non_seller_count": 1,
  "non_seller_present": true
}
```

Same schema as production CV_PIPELINE.md. Redis channel: `cv:{store_id}:{camera_id}`.

**What it does NOT do:** No FSM, no customer tracking, no re-lock, no MediaPipe hand detection, no session assembly. The backend handles correlation.

**What carries over from existing POC (`fds-cv`):**
- YOLO model loading + inference (`PersonDetector` from `vas_logic.py`)
- Zone polygon loading + point-in-polygon checks (`ZoneManager`, `ActivityDetector`)
- Bill zone motion detection (`BillZoneMotionDetector`)
- Bill zone background change detection
- Zone drawing tool (web UI for configuring new stores)

**What's new:**
- Multi-camera threading (5 RTSP grab threads, 1 inference loop)
- Redis publisher instead of file append
- Simplified output (signals, not sessions)
- Rolling video buffer (see below)

**Tech:** Python, ultralytics YOLOv8, OpenCV, Redis, ffmpeg. Runs on T4 via PyTorch CUDA.

#### Rolling video buffer

Each camera has an ffmpeg process running continuously, writing the RTSP stream to disk as 3-minute segment files:

```
/data/buffer/{camera_id}/
    segment_2026-04-16T10-00-00.mp4
    segment_2026-04-16T10-03-00.mp4
    segment_2026-04-16T10-06-00.mp4
    segment_2026-04-16T10-09-00.mp4
    segment_2026-04-16T10-12-00.mp4   ← 15 min rolling window
```

Segments older than 15 minutes are auto-deleted. When a transaction is flagged, the backend identifies the segments covering the time window (transaction start - 30s to transaction end + 30s), concatenates + trims with ffmpeg, and saves the final snippet:

```
/data/snippets/{transaction_id}.mp4
```

Served directly by the backend: `GET /api/transactions/{id}/video` → MP4 file. Dashboard plays it with a standard `<video>` tag. Event timeline syncs by timestamp offset from clip start.

**Storage:**
- Rolling buffer: 5 cameras × 15 min × ~2 MB/min at 5 FPS = ~150 MB total (constant)
- Snippets: ~30 flagged/day × ~3 MB = ~90 MB/day
- Snippet retention: 90 days = ~8 GB. Trivial.

**ffmpeg command per camera:**
```bash
ffmpeg -i rtsp://{camera_url} \
    -c copy -f segment \
    -segment_time 180 \
    -segment_format mp4 \
    -reset_timestamps 1 \
    -strftime 1 \
    /data/buffer/{camera_id}/segment_%Y-%m-%dT%H-%M-%S.mp4
```

**Snippet extraction (on flag):**
```bash
ffmpeg -ss {start_offset} -t {duration} \
    -i "concat:{seg1}|{seg2}|..." \
    -c copy /data/snippets/{transaction_id}.mp4
```

No re-encoding. Just copies the relevant bytes. Runs in milliseconds.

### 2. Backend

FastAPI. Evolves existing `Retail-Trust-Backend-Service`.

**New components:**

| Component | What it does |
|-----------|-------------|
| Nukkad push receiver | `POST /v1/rlcc/launch-event` — accepts push events, routes by event type |
| Transaction assembler | Accumulates events per `transactionSessionId` until CommitTransaction. States: OPEN → COMMITTED → EXPIRED. Raw events to WAL. |
| CV signal consumer | Subscribes to Redis `cv:*` channels, aggregates into 30s windows per POS zone |
| Correlation engine | On CommitTransaction: look up CV windows for matching POS zone + time range. Attach non_seller_present, receipt_detected, cv_confidence. |
| Fraud engine | 29 rules (20 EPOS-only, 2 CV-only, 4 cross-validation, 3 additional). Feed-down suppression. Risk escalation matrix. |
| Event timeline | Merge POS events + CV signals per transaction, sorted by timestamp |
| Reconciliation job | Hourly poll of Nukkad sales API, compare by billNumber, backfill gaps |
| Video snippet extractor | On flag: slice rolling buffer segments, concat+trim with ffmpeg, save MP4 |
| Video endpoint | `GET /api/transactions/{id}/video` — serves snippet MP4 directly |

**What carries over from existing backend:**
- FastAPI app setup, CORS, static file serving
- WebSocket broadcaster (`ConnectionManager`)
- `SalesPoller` (refactored for reconciliation role)
- `stores.json` config loading
- Alert workflow (resolve with status + remarks)
- Dashboard API endpoints (`/api/transactions`, `/api/alerts`, `/api/config`, `/api/stores`)
- Rule config loading (`rule_config.json`)
- JSONL persistence (`append_jsonl`, `read_jsonl`, `update_jsonl_record`)

**What's replaced:**
- `scheduled_data_processor` (2-min batch poll+correlate) → push receiver + event-driven correlation
- `fraud_engine.py` (9 rules on aggregated bills) → new fraud engine (29 rules on per-item/per-payment data)
- `models.py` → new data models with `source` field, per-item SaleLine, per-payment PaymentLine
- `mapping.json` → `camera_mapping.json` (three-way: SellerWindowId ↔ camera_id ↔ XProtect device_id)

**Storage:** JSONL on disk. Same as today. Daily rotation. PostgreSQL deferred.

### 3. Dashboard

Evolve existing `Retail-Trust-and-Security-Dashboard`.

**Add:**
- New filters: payment mode, violation type (manual entry, manual discount, void, return, reprint, drawer opened), receipt status
- Per-item breakdown in transaction detail drawer (items from AddTransactionSaleLine with scanAttribute, discountType badges)
- Per-payment breakdown (payment modes from AddTransactionPaymentLine)
- Per-rule enable/disable toggles in settings
- Video player: standard `<video>` tag playing snippet MP4 from `GET /api/transactions/{id}/video`
- Event timeline panel synced to video playback (POS + CV events, highlight as video timestamp advances)
- Click event → seek video to that timestamp
- Enhanced store/employee scorecard with new metrics (manual entry rate, void rate, discount rate)

**Defer:**
- WebRTC live streaming (needs XProtect — not needed for POC since we record our own snippets)
- XProtect bookmarks/evidence locks integration

## What we skip for POC

| Skipped | Why | Production adds |
|---------|-----|-----------------|
| MQTT | Redis on localhost. Same semantics. | MQTT when edge devices deploy |
| Edge devices | CV runs on same box | Intel Ultra 7 + Metis M.2 per store |
| WebRTC video | We record our own snippets from RTSP. No XProtect dependency for POC. | WebRTC player for live + XProtect recorded playback |
| XProtect integration | Not needed for POC — we have RTSP direct access | Bookmarks, evidence locks, ONVIF Bridge export |
| Auth | 5-store POC, trusted network | Basic auth Phase 1A, JWT Phase 3 |
| PostgreSQL | JSONL fine for 5 stores | Migrate when query patterns demand it |
| Activity classifier | Measure false positive rate first | Phase 2 on iGPU (production) or T4 (POC extension) |

## Resource budget

| Component | VRAM | RAM | Disk |
|-----------|------|-----|------|
| YOLO v8s (1 model, FP16) | ~100 MB | — | — |
| 5 RTSP decode (CPU) | 0 | ~200 MB | — |
| CV service process | 0 | ~500 MB | — |
| 5 ffmpeg rolling buffers | 0 | ~100 MB | ~150 MB (constant) |
| FastAPI backend | 0 | ~500 MB | — |
| Redis | 0 | ~100 MB | — |
| JSONL storage | 0 | — | ~50 MB/day |
| Video snippets | 0 | — | ~90 MB/day |
| OS + headroom | 0 | ~5 GB | — |
| **Total** | **~100 MB / 16 GB** | **~6 GB / 32 GB** | **~300 MB/day** |

Machine is at ~1% GPU, ~20% RAM. 90 days of snippets + data = ~30 GB. Trivial.

## External dependencies

| Item | From | Status |
|------|------|--------|
| Nukkad push endpoint → our receiver URL | Nukkad | **Need** — they configure staging to push to `https://{our-box}:8001/v1/rlcc/launch-event` |
| Nukkad sales API token | Nukkad | **Have** — already used by SalesPoller |
| RTSP URLs for 5 stores | WAISL | **Have** — 3 existing + 2 to add |
| Camera-to-POS mapping | Us | **Have** — 3 existing, 2 new drawn via zone tool |
| Zone polygons | Us | **Have** — 3 existing, 2 new drawn via zone tool |
| Store CIN codes | Nukkad | **Have** — in stores.json (NDCIN prefix for Posifly stores) |

**Resolved:** "Stringified" JSON format — confirmed stringified. Receiver parses outer string, then JSON-decodes the inner payload.

## Status

### Done

| Component | Status | Tests | Details |
|-----------|--------|-------|---------|
| Project scaffold | Done | — | `poc/` with backend, emulator, tests, config, data dirs |
| Pydantic models | Done | 5 pass | TransactionSession, SaleLine, PaymentLine, TotalLine, Alert, CVWindow, TimelineEvent |
| JSONL storage | Done | 4 pass | Append, read, update, WAL (daily rotation), in-memory dedup |
| Config loader | Done | — | Stores, cameras, rules. Hot reload on file change. Save methods. |
| Transaction assembler | Done | 7 pass | OPEN → COMMITTED → EXPIRED. Out-of-order event buffering. 30-min timeout with abandoned alerts. |
| Fraud engine | Done | 9 pass | 29 rules, risk escalation matrix (3 MEDIUM → HIGH), feed-down suppression (10 min silence → suppress CV alerts) |
| FastAPI app | Done | — | Lifespan, WebSocket, CORS, config watcher (10s), expiry checker (60s), REST API, static dashboard mount |
| Push event receiver | Done | 4 pass | Stringified JSON parsing. WAL + dedup. Routes to assembler. Fraud eval + broadcast on commit. BillReprint alerts. |
| Nukkad emulator | Done | — | 9 fraud scenarios: manual entry, manual discount, high discount, void, return not recent, null txn, drawer opened, reprint, employee purchase |
| CV signal consumer | Done | — | Redis pub/sub subscriber. 30s fixed window aggregation per POS zone. 14-day window retention. |
| Correlator | Done | 3 pass | Match committed txns to CV windows by SellerWindowId + timestamp (±3s). Sets cv_non_seller_present, cv_receipt_detected, cv_confidence. |
| Event timeline | Done | — | Merge POS + CV events into sorted list per transaction. `/api/transactions/{id}/timeline` endpoint. |
| CV emulator | Done | — | Publishes fake signals to Redis for 5 cameras at configurable FPS. |

**Total: 32 tests passing. Backend fully functional on emulated data.**

### How to run (emulator mode)

```bash
cd poc
redis-server --daemonize yes --dir ./data
python3 -m backend.main &                          # backend on :8001
python3 -m emulator.cv_emulator --fps 2 &          # fake CV signals
python3 -m emulator.nukkad_emulator --interval 3   # fake POS events
```

Check: `curl http://localhost:8001/api/transactions | python3 -m json.tool`

### Pending (external)

| Item | From | Status |
|------|------|--------|
| Nukkad push endpoint → our box | Nukkad + WAISL (firewall) | FAR submitted, waiting approval |
| Network path for Nukkad inbound | WAISL | Need: NAT/reverse proxy or outbound tunnel |

### Next steps

**1. CV Service** (replaces cv_emulator with real cameras)
- Multi-threaded RTSP grabber (1 thread per camera)
- Shared YOLO model (CPU on Mac, CUDA on T4)
- Zone classification (seller zone polygon checks)
- Bill zone motion + background change detection
- 15-min ffmpeg rolling buffer per camera
- Redis signal publisher (same format as cv_emulator)
- Hot reload: watches camera_mapping.json, adds/removes cameras without restart

**2. Camera API** (backend endpoints for store management)
- `POST /api/stores` — add/update store
- `POST /api/cameras` — add/update camera with RTSP URL
- `GET /api/cameras/{id}/frame` — grab one RTSP frame as JPEG
- `POST /api/cameras/{id}/zones` — save zone polygons
- Snippet extraction: `GET /api/transactions/{id}/video` — extract MP4 from rolling buffer

**3. Dashboard** (React app, 7 pages)
- Transactions: list + filters + detail drawer with per-item/per-payment breakdown
- Alerts: list + resolve workflow (status + remarks)
- Analytics: risk distribution, txns over time, rule violations, hourly activity
- Scorecard: per-store and per-employee metrics
- Settings: per-rule enable/disable toggles + threshold sliders
- Store Setup: add store, add camera, draw zone polygons on live camera frame
- Stream Viewer: raw CV + POS events (debug)
- Video player: `<video>` tag with event timeline sync (click event → seek video)

**4. Reconciler** (hourly sales API poll)
- Poll Nukkad sales API (F&B + Retail endpoints)
- Compare against push-assembled transactions by billNumber
- Backfill gaps
- Not urgent until Nukkad push is live

## Build plan

### Completed

| Week | What | Status |
|------|------|--------|
| Week 1 (Days 3-5) + Week 2 | Backend core + emulators | **Done** — 12 components, 32 tests |

### Remaining

| Week | What | Blocked by |
|------|------|-----------|
| Week 2 remaining | CV service (YOLO + zones + buffer) + Camera API | Nothing — can start now |
| Week 3 | Dashboard (7 pages) | Nothing — can start now |
| Week 3-4 | Connect real Nukkad push + real RTSP | Firewall approval |
| Week 4 | Tuning + demo | Real data flowing |

## POC → Production migration

| POC component | Production change |
|---------------|-------------------|
| CV service (PyTorch + T4) | DLStreamer + Voyager SDK on edge devices (Metis AIPU) |
| Redis pub/sub (localhost) | MQTT over TLS (WAN to centralized server) |
| Rolling buffer (local ffmpeg) | XProtect handles recording. ONVIF Bridge for clip export. Bookmarks + evidence locks for retention. |
| Video served as MP4 file | WebRTC for live + recorded playback via XProtect. MP4 snippets as fallback/export. |
| Backend (same box) | Centralized app server (separate from edge) |
| Dashboard (same box) | Same code, served from app server |
| JSONL storage | PostgreSQL |
| No auth | Basic auth → JWT + RBAC |

Backend, dashboard, and fraud engine code is production code from day one. CV inference stack, transport layer, and video source change.

## 5 POC stores

Picking from Posifly stores (push API supported):

| CIN | Store | POS System | RTSP | Zones |
|-----|-------|-----------|------|-------|
| NDCIN1223 | Ram Ki Bandi | Posifly-Dino | Have | Have |
| NSCIN8227 | Encalm Lounge | Posifly-Dino | Have | Have |
| NDCIN1227 | KFC | Posifly-Dino | Have | Have |
| TBD | Store 4 | Posifly-Dino | Have | Draw |
| TBD | Store 5 | Posifly-Dino | Have | Draw |

First 3 are the existing POC stores. Store 4 and 5 selected based on which cameras WAISL confirms are POS-facing.
