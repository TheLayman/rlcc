# CV Pipeline Design

## What the edge does

The edge device is a sensor array. It watches 30-40 cameras and answers one question per POS zone per frame: **who is there and what's happening at the bill printer?**

It does not assemble transactions, run fraud rules, or make decisions. The app server does that. The edge sends raw signals; the server has the brains.

## Hardware allocation

| Chip | Task | Duty cycle |
|------|------|-----------|
| Axelera Metis AIPU | YOLO person detection (INT8) | Always — all cameras, 5-6 FPS each, 600 FPS aggregate |
| iGPU Arc 140T | H.265 decode (up to 40 streams*) + Phase 2 crop classifiers | Always (decode) + on-demand (classifiers) |

*\*Hardware validation needed:* 40 concurrent H.265 decode sessions on Arc 140T is unvalidated. Intel iGPUs typically support 16-32 concurrent sessions. At 5-6 FPS (not 30), the decode throughput is fine (240 frames/sec), but the concurrent session limit is the constraint. Fallback if limited to ~20 sessions: 20 cameras per device (13 devices for 250 cameras) or CPU-assisted decode for overflow streams.
| CPU 6P+8E | Zone logic, tracking, signal assembly, MQTT/HTTP | Always |

## Phase 1: Zone Presence Signals

Every POS counter in the camera view gets a zone configuration:

```
┌─────────────────────────────────────────────┐
│  Camera Frame                               │
│                                             │
│   ┌──────────────┐   ┌──────────────┐      │
│   │ POS 3        │   │ POS 4        │      │
│   │  [seller]    │   │  [seller]    │      │
│   │  [bill zone] │   │  [bill zone] │      │
│   └──────────────┘   └──────────────┘      │
│                                             │
│   Customers are anywhere in the open area   │
│   — not in per-POS zones, especially in     │
│     airport retail with shared counters     │
└─────────────────────────────────────────────┘
```

Per-POS zones (fixed, reliable):
- **seller_zone** — where the cashier stands (behind the counter, fixed position)
- **bill_zone** — the receipt printer area (fixed position)

Camera-wide (no polygon):
- **non_seller_present** — anyone detected who isn't inside a seller zone
- **non_seller_count** — how many such people

Per frame, the edge computes:

```json
{
  "ts": "2026-04-16T10:02:05.000Z",
  "camera_id": "cam-03",
  "zones": [
    {
      "pos_zone": "POS3",
      "seller": true,
      "bill_motion": false,
      "bill_bg": false
    },
    {
      "pos_zone": "POS4",
      "seller": true,
      "bill_motion": false,
      "bill_bg": false
    }
  ],
  "non_seller_count": 2,
  "non_seller_present": true
}
```

Per-POS: seller presence and bill zone activity (fixed positions, reliable zones). Camera-wide: non-seller count (customers can be anywhere). The app server does the rest.

### How each signal is computed

**seller (per POS zone):**
1. Metis AIPU runs YOLO — outputs person bounding boxes with track IDs
2. CPU checks: does any person bbox overlap this POS's seller_zone polygon? → `seller: true`

**non_seller_count / non_seller_present (camera-wide):**
Count all detected persons whose bbox does NOT overlap any seller_zone. These are customers, browsers, passersby — anyone who isn't a cashier. Camera-wide because customers don't stand in predictable per-POS zones.

**bill_motion (per POS zone):**
Frame differencing in the bill_zone polygon. Pixel change > 2% of zone area → true. Microsecond operation on CPU.

**bill_bg (per POS zone):**
Compare current bill_zone appearance against a learned baseline. Deviation > 3% → true. Baseline only updates when no motion (prevents learning hands into it).

### Multi-POS per camera

One camera can cover multiple POS counters. Seller zones and bill zones work fine — cashiers and printers are at fixed positions behind counters. Customer zones don't work per-POS because customers approach from any direction, stand between counters, lean across, or mill around in airport retail. You can't draw non-overlapping customer polygons when the physical spaces overlap.

**Approach:** Seller and bill zones are per-POS. Customer presence is camera-wide.

- **Per-POS:** `seller_present`, `bill_motion`, `bill_bg` — each POS has its own seller zone and bill zone polygon
- **Camera-wide:** `non_seller_count`, `non_seller_present` — anyone detected who isn't inside a seller zone

Single-POS cameras can optionally use a tighter customer zone polygon since there's no ambiguity — any non-seller near the counter is the customer for that POS.

YOLO detection runs once per frame. Seller zone classification runs per-person per-zone on CPU. No extra inference cost for multi-POS.

### What the app server does with these signals

**POS-anchored mode (primary):**
Nukkad pushes `BeginTransactionWithTillLookup` for POS 3 → app server looks at CV signals for POS 3's camera → confirms `non_seller_present: true` (someone other than cashiers is around). At `CommitTransaction` time → checks `bill_motion`/`bill_bg` for POS 3's bill zone for receipt confirmation. If void or drawer-open event → checks `non_seller_present` (void without anyone around = high risk).

Note: on multi-POS cameras, customer presence is camera-wide. If POS 3 and POS 4 are both in frame and non_seller_present is true, we can't tell which POS the customer is at. But `non_seller_present: false` is definitive — nobody is there.

**CV-initiated mode:**
App server sees sustained `seller: true` for a POS zone + `non_seller_present: true` camera-wide for >30s with no Nukkad `BeginTransactionWithTillLookup` → raises "Missing POS" alert. On single-POS cameras this is precise. On multi-POS cameras the alert is camera-level: "activity at cam-03 with seller at POS3, no EPOS event."

**Idle monitoring:**
App server sees `seller: false` for a POS zone for extended period during business hours → POS idle/offline.

### Why this is better than the POC approach

The POC runs a full transaction FSM on the edge — session start/end, customer locking with decaying re-lock, 3-tier receipt detection, warmup validation. This breaks in production because:

| POC problem | Why it happens | Phase 1 solution |
|-------------|---------------|-----------------|
| Passing customers trigger sessions | 3s warmup isn't enough; people pause near POS | POS-anchored mode: no POS event = CV signals are just noise, ignored |
| Sitting customers (food court) | Near POS zone, move occasionally, not truly static | No per-POS customer zones. Camera-wide non_seller_count is just a presence signal, not a session trigger. POS event anchors the detection. |
| Wrong customer locked | Re-lock grabs nearest person, which changes at busy counters | No locking needed. Just: are non-sellers present? Y/N + count. |
| Two POS one camera | POC assumes 1 POS per SellerWindowId | Seller zone + bill zone per POS (reliable, fixed positions). Customer presence camera-wide (can't attribute to specific POS). |
| Customer zones overlap between POS | Airport retail — customers don't stand in neat per-POS areas | Drop per-POS customer zones entirely on multi-POS cameras. Use camera-wide non_seller_present instead. |
| Camera angle variation | Zone polygons tuned for one angle break at another | Simpler signal (person in polygon for sellers, any non-seller for customers) is more robust across angles |

The core insight: **with real-time POS events, we don't need CV to detect transactions.** We need CV to (a) confirm what POS reports and (b) catch what POS doesn't report.

## Phase 2: Seller Activity Classifier

Phase 1 catches "Missing POS" but with false positives — cashier chatting with someone, employee on break near the counter, customer asking directions. All show seller + customer co-present without a POS event.

Phase 2 adds: **what is the seller doing with their hands?**

### Trigger

Only runs when Phase 1 CV-initiated mode detects: `seller: true` at a POS zone + `non_seller_present: true` camera-wide for >15s with no Nukkad event.

### Execution

1. Crop the seller region from the frame (bbox from YOLO)
2. Run activity classifier on iGPU via OpenVINO (2-3 FPS, only triggered cameras)
3. Classify:

| Class | Meaning | Suspicion |
|-------|---------|-----------|
| `idle` | Standing, talking, not handling anything | Low — probably chatting |
| `handling_item` | Picking up, bagging, scanning items | HIGH — transacting without POS |
| `handling_cash` | Hand at drawer, exchanging money | HIGH — cash transaction without POS |
| `using_pos` | Typing, looking at screen | Expect POS event soon |
| `giving_receipt` | Hand at printer, handing paper | Expect bill_zone_activity |

### Confidence ladder

```
LOW       customer + seller, no POS event (Phase 1)
MEDIUM    above + customer dwell > 30s + customer leaves (Phase 1)
HIGH      above + seller classified as handling_item or handling_cash (Phase 2)
VERY HIGH above + bill_zone_activity but still no POS event (receipt printed off-books?)
```

### Resource budget

At any given moment, maybe 1-3 out of 40 cameras need Phase 2 classification. The iGPU handles this easily alongside its decode workload. The classifier is a small crop model — not a full-frame inference.

### Training the classifier

Needs labeled data from real store footage:
- Seller handling items vs idle vs using POS
- Can bootstrap from the POC video recordings
- Model: lightweight classification head (MobileNet-scale), INT8 via OpenVINO

## Zone Configuration

Each store needs a zone config file per camera:

```json
{
  "camera_id": "cam-03",
  "multi_pos": true,
  "pos_zones": [
    {
      "zone_id": "POS3",
      "seller_window_id": "NDCIN1223_POS3",
      "seller_zone": [[431,568], [861,550], [872,720], [420,720]],
      "bill_zone": [[710,375], [850,370], [855,440], [715,445]]
    },
    {
      "zone_id": "POS4",
      "seller_window_id": "NDCIN1223_POS4",
      "seller_zone": [[100,568], [400,550], [410,720], [90,720]],
      "bill_zone": [[300,375], [440,370], [445,440], [305,445]]
    }
  ]
}
```

No `customer_zone` on multi-POS cameras — customers don't stand in predictable per-POS areas in airport retail. Non-seller presence is computed camera-wide. Single-POS cameras (`"multi_pos": false`) can optionally add a `customer_zone` for tighter attribution.

The POC already has an interactive zone drawing tool (web UI at `/` on the vas_server). This carries over to production — operator opens the tool, draws polygons on a camera frame, saves config.

## Edge → Server Protocol

### Transport resilience

MQTT QoS 1 over TLS. If the broker is unreachable, the edge device buffers signals in a local ring buffer (last 5 minutes per camera, ~50 MB total for 40 cameras). On reconnect, buffered signals are replayed in order. Signals older than 5 minutes are dropped — stale presence data is worse than missing data.

Mosquitto persistent sessions help with brief disconnects (<1 second), but the per-client queue limit (default 100 messages) fills in <1 second at 240 msg/sec. The edge-side ring buffer is the real safety net.

### Signal stream (MQTT, high frequency)

Topic: `rlcc/{store_id}/{camera_id}/signals`

Published at 5-6 FPS per camera:

```json
{
  "ts": "2026-04-16T10:02:05.123Z",
  "camera_id": "cam-03",
  "zones": [
    {
      "pos_zone": "POS3",
      "seller": true,
      "bill_motion": false,
      "bill_bg": false
    },
    {
      "pos_zone": "POS4",
      "seller": true,
      "bill_motion": false,
      "bill_bg": false
    }
  ],
  "non_seller_count": 2,
  "non_seller_present": true
}
```

Per-POS: seller + bill zone (fixed positions). Camera-wide: non-seller presence (customers can be anywhere, especially on multi-POS cameras where per-POS customer zones don't work).

~170 bytes per message. 40 cameras × 6 FPS = 240 messages/sec = ~40 KB/s per edge device.

### Phase 2 activity signals (MQTT, low frequency)

Topic: `rlcc/{store_id}/{camera_id}/activity`

Published only when Phase 2 classifier runs (triggered, 2-3 FPS on 1-3 cameras):

```json
{
  "ts": "2026-04-16T10:02:05.123Z",
  "camera_id": "cam-03",
  "pos_zone": "POS3",
  "seller_activity": "handling_item",
  "confidence": 0.82
}
```

### Health (MQTT, low frequency)

Topic: `rlcc/{store_id}/health`

Published every 60 seconds:

```json
{
  "ts": "2026-04-16T10:02:00Z",
  "store_id": "NDCIN1223",
  "cameras_active": 38,
  "cameras_total": 40,
  "fps_avg": 5.4,
  "cpu_percent": 62,
  "mem_percent": 45
}
```

## POC → Production

**Carries over (logic):**
- Zone-based person classification (seller/customer by polygon overlap)
- Bill zone motion detection (frame differencing)
- Bill zone background change detection (baseline comparison)
- Zone drawing tool (interactive web UI)

**Replaced (inference stack):**
- YOLO: ultralytics PyTorch → Voyager SDK on Axelera Metis (INT8)
- Hand detection: MediaPipe → OpenVINO crop classifier on iGPU (Phase 2)
- Decode: OpenCV VideoCapture → DLStreamer on iGPU (H.265, 40 streams)
- Scale: 1 camera/process → 30-40 cameras/device multiplexed

**Moved to app server:**
- Transaction FSM (IDLE → warmup → ACTIVE) — replaced by server-side correlation
- Customer locking + re-lock — replaced by simple presence count
- Session assembly — server merges CV signals with POS events
- Doc Hudson quality analysis — runs on server, not edge
