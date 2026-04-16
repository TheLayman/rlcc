# POC Implementation Design

## What we're building

Self-contained fraud detection system for 5 airport retail stores. Runs on a single T4 workstation (headless). Accessed via browser from a laptop on the same network. Clean rewrite — no legacy code imported, algorithms referenced from existing repos.

## Hardware & access

- T4 VM: Tesla T4 (16 GB VRAM), 32 GB RAM, headless, behind firewall
- Laptop: SSH to VM for ops, browser to VM for dashboard
- Single port: FastAPI serves API + WebSocket + dashboard static build on `:8001`
- 5 RTSP cameras on store LAN, reachable from VM

## Folder structure

```
rlcc-scope/poc/
├── cv_service/
│   ├── main.py           — entry point: watches camera_mapping.json, manages camera threads
│   ├── detector.py       — YOLO wrapper (auto CPU/CUDA)
│   ├── zones.py          — zone polygon math, point-in-polygon
│   ├── motion.py         — bill zone motion + background change
│   ├── buffer.py         — ffmpeg rolling buffer manager (15 min, 3-min segments)
│   └── publisher.py      — Redis signal publisher
│
├── backend/
│   ├── main.py           — FastAPI app, startup hooks, static file serving, WebSocket
│   ├── receiver.py       — Nukkad push event receiver + stringified JSON parser
│   ├── assembler.py      — transaction assembler state machine
│   ├── cv_consumer.py    — Redis subscriber, 30s window aggregation
│   ├── correlator.py     — match committed txns to CV windows
│   ├── fraud.py          — 29 rules, risk matrix, feed-down suppression
│   ├── timeline.py       — merge POS + CV events into unified timeline
│   ├── snippets.py       — extract video from rolling buffer on flag
│   ├── reconciler.py     — hourly sales API poll + gap backfill
│   ├── storage.py        — JSONL read/write/rotate
│   ├── models.py         — Pydantic models
│   ├── config.py         — stores, camera mappings, rule config, hot reload
│   └── camera_api.py     — RTSP frame grab endpoint, store/camera/zone CRUD
│
├── dashboard/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── pages/
│   │   │   ├── Transactions.tsx    — list + filters + detail drawer + video player
│   │   │   ├── Alerts.tsx          — list + resolve workflow
│   │   │   ├── Analytics.tsx       — charts
│   │   │   ├── Scorecard.tsx       — store + employee metrics
│   │   │   ├── Settings.tsx        — rule config + toggles
│   │   │   ├── StoreSetup.tsx      — add store, add camera, draw zones
│   │   │   └── StreamViewer.tsx    — raw CV + POS event stream (debug)
│   │   ├── components/
│   │   │   ├── TransactionDetail.tsx
│   │   │   ├── VideoPlayer.tsx     — <video> tag + event timeline sync
│   │   │   ├── ZoneDrawer.tsx      — canvas overlay for polygon drawing
│   │   │   ├── AlertResolver.tsx
│   │   │   ├── FilterBar.tsx
│   │   │   └── ui/                 — minimal Radix primitives (Button, Badge, Card, Select, etc.)
│   │   └── lib/
│   │       ├── api.ts              — REST API client
│   │       └── ws.ts               — WebSocket client
│   ├── package.json
│   ├── vite.config.ts
│   └── tsconfig.json
│
├── emulator/
│   ├── nukkad_emulator.py    — generates push event sequences
│   ├── cv_emulator.py        — generates fake CV signals to Redis
│   └── scenarios.py          — predefined fraud patterns
│
├── config/
│   ├── stores.json           — store list (CIN, name, pos_system)
│   ├── camera_mapping.json   — three-way mapping + zone configs inline
│   └── rule_config.json      — rule thresholds + enable/disable
│
├── data/
│   ├── buffer/               — rolling video segments per camera
│   ├── snippets/             — extracted MP4 clips for flagged txns
│   ├── events/               — raw Nukkad event WAL (daily rotation)
│   ├── transactions.jsonl
│   └── alerts.jsonl
│
├── requirements.txt
├── start.sh
└── README.md
```

## Component design

### CV Service (`cv_service/`)

Single process. One thread per camera. One shared YOLO model.

**Startup:**
1. Load `config/camera_mapping.json`
2. Load YOLO model (CUDA if available, else CPU)
3. For each camera entry: start a grab thread + ffmpeg rolling buffer
4. Watch `camera_mapping.json` for changes (poll every 10s) — add/remove cameras without restart

**Per camera thread loop (5-6 FPS):**
```python
while running:
    frame = grab_rtsp_frame()          # OpenCV VideoCapture
    persons = detector.detect(frame)   # YOLO, batched across cameras
    signal = {
        "ts": utc_now_iso(),
        "camera_id": camera_id,
        "zones": [],
        "non_seller_count": 0,
        "non_seller_present": False
    }
    for zone in pos_zones:
        seller = any(person_in_polygon(p, zone.seller_zone) for p in persons)
        bill_motion = motion_detector.check(frame, zone.bill_zone)
        bill_bg = motion_detector.check_background(frame, zone.bill_zone)
        signal["zones"].append({
            "pos_zone": zone.zone_id,
            "seller": seller,
            "bill_motion": bill_motion,
            "bill_bg": bill_bg
        })
    non_sellers = [p for p in persons if not in_any_seller_zone(p, pos_zones)]
    signal["non_seller_count"] = len(non_sellers)
    signal["non_seller_present"] = len(non_sellers) > 0
    publisher.publish(store_id, camera_id, signal)
    sleep_to_maintain_fps(target=6)
```

**YOLO batching:** Frames from all 5 cameras are queued. Inference loop pops a batch (up to 5), runs YOLO once, distributes results back to each camera's thread. On Mac (CPU) this is slower but functional. On T4 it's trivially fast.

**detector.py:**
```python
class Detector:
    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = YOLO("yolov8s.pt").to(self.device)
    
    def detect(self, frames: list[np.ndarray]) -> list[list[Person]]:
        results = self.model(frames, conf=0.25, classes=[0])  # class 0 = person
        # return list of Person(bbox, center, confidence) per frame
```

**zones.py:**
```python
@dataclass
class PosZone:
    zone_id: str
    seller_window_id: str
    seller_zone: list[tuple[int, int]]    # polygon points
    bill_zone: list[tuple[int, int]]

def point_in_polygon(point, polygon) -> bool:
    # ray casting algorithm

def person_in_polygon(person, polygon) -> bool:
    # check if person bbox center is inside polygon
```

**motion.py:**
```python
class BillZoneMotion:
    def __init__(self, zone_polygon):
        self.baseline = None
        self.threshold_motion = 0.02    # 2% pixel change
        self.threshold_bg = 0.03        # 3% deviation from baseline
    
    def check(self, frame, zone_polygon) -> bool:
        # crop zone, frame diff, threshold
    
    def check_background(self, frame, zone_polygon) -> bool:
        # crop zone, compare to baseline, threshold
        # only update baseline when no motion
```

**buffer.py:**
```python
class RollingBuffer:
    def __init__(self, camera_id, rtsp_url, buffer_dir, segment_duration=180, max_age=900):
        # start ffmpeg process writing 3-min segments
        # cleanup thread deletes segments older than 15 min
    
    def extract_snippet(self, start_ts, end_ts, output_path) -> bool:
        # find segments covering the time range
        # ffmpeg concat + trim → output MP4
```

**publisher.py:**
```python
class SignalPublisher:
    def __init__(self, redis_url="redis://localhost:6379"):
        self.redis = Redis.from_url(redis_url)
    
    def publish(self, store_id, camera_id, signal: dict):
        self.redis.publish(f"cv:{store_id}:{camera_id}", json.dumps(signal))
```

### Backend (`backend/`)

FastAPI app. Single process. Async.

**main.py — startup:**
1. Load configs (stores, camera mappings, rules)
2. Start Redis subscriber (cv_consumer) as background task
3. Start reconciliation job (hourly) as background task
4. Start config file watcher as background task
5. Mount static files (dashboard build)
6. Serve on `:8001`

**receiver.py — Nukkad push events:**
```python
@router.post("/v1/rlcc/launch-event")
async def receive_event(request: Request):
    body = await request.body()
    # Nukkad sends stringified JSON — parse outer string, then JSON decode
    payload = json.loads(json.loads(body))
    event_type = payload["event"]
    
    # persist raw event to WAL immediately
    storage.append_event(payload)
    
    # deduplicate by transactionSessionId + event + lineNumber
    if storage.is_duplicate(payload):
        return {"status": 200, "message": "duplicate, ignored"}
    
    # route by event type
    if event_type == "BeginTransactionWithTillLookup":
        assembler.begin(payload)
    elif event_type in ("AddTransactionSaleLine", "AddTransactionSaleLineWithTillLookup"):
        assembler.add_sale_line(payload)
    elif event_type == "AddTransactionPaymentLine":
        assembler.add_payment_line(payload)
    elif event_type == "AddTransactionTotalLine":
        assembler.add_total_line(payload)
    elif event_type == "AddTransactionEvent":
        assembler.add_event(payload)
    elif event_type == "CommitTransaction":
        txn = assembler.commit(payload)
        if txn:
            await process_committed_transaction(txn)
    elif event_type == "BillReprint":
        await handle_reprint(payload)
    
    return {"status": 200, "message": "Success"}
```

**assembler.py:**
```python
class TransactionAssembler:
    def __init__(self):
        self.sessions: dict[str, TransactionSession] = {}  # keyed by transactionSessionId
        self.timeout = 1800  # 30 min
    
    def begin(self, payload):
        session_id = payload["transactionSessionId"]
        self.sessions[session_id] = TransactionSession(
            id=session_id,
            store_id=payload["storeIdentifier"],
            pos_terminal=payload["posTerminalNo"],
            cashier_id=payload.get("cashier"),
            transaction_type=payload.get("transactionType"),
            employee_purchase=payload.get("employeePurchase", False),
            started_at=payload.get("transactionTimeStamp"),
            source="push_assembled",
            status="open"
        )
    
    def add_sale_line(self, payload):
        session = self._get_or_buffer(payload)
        if session:
            session.items.append(SaleLine.from_nukkad(payload))
    
    def commit(self, payload) -> TransactionSession | None:
        session_id = payload["transactionSessionId"]
        session = self.sessions.pop(session_id, None)
        if session:
            session.bill_number = payload.get("transactionNumber")
            session.status = "committed"
            session.committed_at = utc_now()
            return session
        return None
    
    def check_expired(self):
        # called periodically, moves stale sessions to expired
        # generates "Abandoned Transaction" alerts for sessions with items
```

**cv_consumer.py:**
```python
class CVConsumer:
    def __init__(self, redis_url):
        self.windows: dict[str, SortedList] = defaultdict(SortedList)  # keyed by pos_zone
        self.current: dict[str, dict] = {}  # latest signal per camera
    
    async def run(self):
        pubsub = self.redis.pubsub()
        await pubsub.psubscribe("cv:*")
        async for message in pubsub.listen():
            signal = json.loads(message["data"])
            self._update_current(signal)
            self._update_windows(signal)
    
    def get_window(self, pos_zone, start_ts, end_ts) -> CVWindow:
        # binary search in self.windows[pos_zone] for overlapping 30s windows
        # aggregate: seller_pct, non_seller_pct, bill_motion, bill_bg
```

**correlator.py:**
```python
def correlate(txn: TransactionSession, cv_consumer: CVConsumer, mapping: CameraMapping) -> TransactionSession:
    camera = mapping.get_camera(txn.seller_window_id)
    if not camera:
        txn.cv_confidence = "UNMAPPED"
        return txn
    
    window = cv_consumer.get_window(
        pos_zone=camera.pos_zone,
        start_ts=txn.started_at,
        end_ts=txn.committed_at
    )
    
    if not window:
        txn.cv_confidence = "UNAVAILABLE"
        return txn
    
    txn.cv_non_seller_present = window.non_seller_present_pct > 0.3
    txn.cv_receipt_detected = window.bill_motion or window.bill_bg
    txn.cv_confidence = "REDUCED" if camera.multi_pos else "HIGH"
    txn.camera_id = camera.camera_id
    txn.device_id = camera.xprotect_device_id
    return txn
```

**fraud.py:**
```python
class FraudEngine:
    def __init__(self, config):
        self.rules = self._load_rules(config)
    
    def evaluate(self, txn: TransactionSession) -> list[Alert]:
        triggered = []
        for rule in self.rules:
            if rule.enabled and rule.evaluate(txn):
                triggered.append(rule)
        
        risk = self._calculate_risk(triggered)
        txn.risk_level = risk
        txn.triggered_rules = [r.id for r in triggered]
        
        if risk in ("HIGH", "MEDIUM"):
            return [self._create_alert(txn, triggered)]
        return []
    
    # Feed-down suppression
    def _is_feed_down(self, store_id) -> bool:
        # check last Nukkad event time for this store
        # if >10 min during business hours, suppress CV-initiated rules
```

**snippets.py:**
```python
class SnippetExtractor:
    def __init__(self, buffer_dir, snippet_dir):
        self.buffer_dir = buffer_dir
        self.snippet_dir = snippet_dir
    
    def extract(self, txn: TransactionSession) -> str | None:
        camera_id = txn.camera_id
        if not camera_id:
            return None
        
        start = txn.started_at - timedelta(seconds=30)
        end = txn.committed_at + timedelta(seconds=30)
        
        output = f"{self.snippet_dir}/{txn.id}.mp4"
        segments = self._find_segments(camera_id, start, end)
        if not segments:
            return None
        
        # ffmpeg concat + trim, no re-encode
        self._ffmpeg_extract(segments, start, end, output)
        return output
```

**camera_api.py — store/camera/zone management:**
```python
@router.get("/api/cameras/{camera_id}/frame")
async def get_frame(camera_id: str):
    # grab one frame from RTSP, return as JPEG
    # used by zone drawing tool in dashboard

@router.post("/api/stores")
async def upsert_store(store: StoreCreate):
    # add/update store in stores.json

@router.post("/api/cameras")
async def upsert_camera(camera: CameraCreate):
    # add/update camera in camera_mapping.json
    # CV service picks up change via file watcher

@router.post("/api/cameras/{camera_id}/zones")
async def save_zones(camera_id: str, zones: list[PosZoneConfig]):
    # save zone polygons to camera_mapping.json

@router.get("/api/cameras/{camera_id}/zones")
async def get_zones(camera_id: str):
    # return zone polygons for the zone drawing UI
```

**REST API summary:**
```
# Config / Management
GET    /api/stores                          — list stores
POST   /api/stores                          — add/update store
GET    /api/cameras                         — list cameras
POST   /api/cameras                         — add/update camera
GET    /api/cameras/{id}/frame              — grab RTSP frame (JPEG)
GET    /api/cameras/{id}/zones              — get zone polygons
POST   /api/cameras/{id}/zones              — save zone polygons
GET    /api/config                          — rule config
POST   /api/config                          — update rule config

# Nukkad
POST   /v1/rlcc/launch-event               — push event receiver

# Transactions & Alerts
GET    /api/transactions                    — list (filterable, paginated)
GET    /api/transactions/{id}               — detail with items, payments, totals
GET    /api/transactions/{id}/timeline      — unified POS + CV event timeline
GET    /api/transactions/{id}/video         — MP4 snippet (if available)
GET    /api/alerts                          — list (filterable)
POST   /api/alerts/{id}/resolve             — resolve with status + remarks

# WebSocket
WS     /ws                                  — NEW_TRANSACTION, NEW_ALERT, ALERT_UPDATED
```

### Dashboard (`dashboard/`)

Fresh Vite + React 18 + TypeScript. Minimal deps: Radix UI primitives, Tailwind, Recharts, Lucide icons.

**7 pages:**

| Page | What it does |
|------|-------------|
| **Transactions** | Table with filters (risk, store, payment mode, violation type, date range, amount). Click row → detail drawer with per-item/per-payment breakdown + video player + event timeline. |
| **Alerts** | List with status filter (open/investigating/closed). Resolve workflow: dropdown + remarks. |
| **Analytics** | Risk distribution donut, txns over time, txns by store, rule violations bar, hourly activity. |
| **Scorecard** | Per-store and per-employee metrics: txn count, flag rate, void rate, manual entry rate, discount rate. |
| **Settings** | Per-rule enable/disable toggles. Threshold sliders for configurable rules. Save & apply. |
| **Store Setup** | Add store (CIN, name). Add camera (RTSP URL, store, POS terminal). Draw zones (canvas overlay on live frame). |
| **Stream Viewer** | Raw CV signals + POS events scrolling in real-time. Debug tool. |

**Video player + event timeline:**
```
┌─────────────────────┬──────────────────────────┐
│  <video> tag         │  Event list              │
│  src=/api/txns/      │  10:02:00 Txn opened     │
│    {id}/video        │  10:02:05 Chicken ×1     │
│                      │  10:02:18 ⚠ Fries MANUAL │
│  Standard controls   │  10:02:35 Cash ₹400      │
│  play/pause/seek     │  10:02:38 Committed      │
│                      │  10:02:34 Receipt (CV)    │
├──────────────────────┴──────────────────────────┤
│  Timeline bar (events as markers, seek on click) │
└──────────────────────────────────────────────────┘
```

Video `currentTime` synced to event list via `timeupdate` event. Click event → `video.currentTime = event.ts - clip.start_ts`. Simple.

**Zone drawer component:**
```
┌──────────────────────────────────────┐
│  Camera frame (from /api/cameras/    │
│    {id}/frame, refreshed every 2s)   │
│                                      │
│  Click to place polygon vertices     │
│  [seller_zone] [bill_zone] toggles   │
│                                      │
│  [Clear] [Undo] [Save]              │
└──────────────────────────────────────┘
```

### Emulator (`emulator/`)

**nukkad_emulator.py:**
Generates a stream of realistic Nukkad push events against the backend's receiver endpoint. Configurable:
- Store CIN, POS terminal, cashier
- Items per transaction (1-10, random from a product catalog)
- Payment modes (Cash, Card, UPI, Phonepe — weighted random)
- Transaction frequency (configurable, default 1 per minute per store)
- Fraud injection rate (configurable, default 10%)

Fraud scenarios from `scenarios.py`:
- Manual item entry (scanAttribute: ManuallyEntered)
- Manual discount (discountType: ManuallyEnteredValue, self-granted)
- High discount (>20%)
- Void mid-transaction (CancellationWithinTransaction)
- Post-bill cancellation (CancellationOfPrevious)
- Drawer opened outside transaction
- Bill reprint
- Null transaction (begin + commit, no items)
- Return not recently sold
- Employee purchase
- Complementary order (F&B)

Events are stringified JSON (matching confirmed Nukkad format).

**cv_emulator.py:**
Publishes fake CV signals to Redis channels. Correlated with the Nukkad emulator's timing:
- When a transaction is emulated, CV shows seller + non_seller_present at that POS zone
- Bill zone activity at transaction end (receipt printed)
- For "Missing POS" scenarios: CV shows activity but no Nukkad events

### Config (`config/`)

**camera_mapping.json:**
```json
[
  {
    "seller_window_id": "NDCIN1223_POS3",
    "store_id": "NDCIN1223",
    "pos_terminal": "POS 3",
    "camera_id": "cam-rambandi-01",
    "rtsp_url": "rtsp://10.86.x.x:554/stream1",
    "xprotect_device_id": "",
    "multi_pos": false,
    "zones": {
      "pos_zones": [
        {
          "zone_id": "POS3",
          "seller_zone": [[431,568], [861,550], [872,720], [420,720]],
          "bill_zone": [[710,375], [850,370], [855,440], [715,445]]
        }
      ]
    }
  }
]
```

Zones are inline in the mapping file — no separate zone config files. Dashboard reads and writes this directly.

### Dependencies

```
# requirements.txt
fastapi>=0.128.0
uvicorn[standard]>=0.40.0
redis>=5.0.0
ultralytics>=8.0.0
opencv-python-headless>=4.8.0
numpy>=1.24.0
httpx>=0.28.0
pydantic>=2.0.0
python-dotenv>=1.0.0
```

No torch in requirements — ultralytics pulls the right version. `opencv-python-headless` since the VM is headless (no GUI).

### start.sh

```bash
#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "Starting Redis..."
redis-server --daemonize yes --dir ./data

echo "Starting CV service..."
python -m cv_service.main &
CV_PID=$!

echo "Starting backend on :8001..."
python -m backend.main
# backend is foreground, Ctrl+C stops everything

kill $CV_PID 2>/dev/null
redis-cli shutdown 2>/dev/null
```

### Build order

**Week 1:**
- Day 1-2: `cv_service/` — detector, zones, motion, publisher, main loop. Test with 1 RTSP stream.
- Day 3-4: `backend/` — receiver, assembler, models, storage, config. Test with emulator.
- Day 5: `emulator/` — nukkad + cv emulators with fraud scenarios.

**Week 2:**
- Day 1-2: `backend/` — cv_consumer, correlator, fraud engine (29 rules).
- Day 3-4: `backend/` — timeline builder, snippet extractor, reconciler. camera_api for store setup.
- Day 5: Integration test — emulator end-to-end through all components.

**Week 3:**
- Day 1-3: `dashboard/` — all 7 pages including zone drawer and video player.
- Day 4-5: Connect real RTSP streams + real Nukkad (if firewall approved). End-to-end with live data.

**Week 4:**
- Tuning, bug fixes, demo prep.
