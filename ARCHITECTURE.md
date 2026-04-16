# System Architecture

## Topology

```
┌───────────────────────────────────────────────┐
│  STORE (× N)                                  │
│                                               │
│  30-40 IP Cameras                             │
│       │                                       │
│       ├── RTSP ──> Edge Device                │
│       │            Ultra 7 + Metis M.2        │
│       │            CV inference only           │
│       │                 │                     │
│       │                 │ MQTT/HTTP (metadata) │
│       │                                       │
│       └── RTSP ──> XProtect Recording Server  │
│                    WAISL-managed, on-premise   │
│                    Records + retains video     │
└───────────────────┬───────────┬───────────────┘
                    │           │
        metadata ───┘           └── XProtect network
                    │           │
┌───────────────────▼───────────▼───────────────┐
│  CENTRALIZED                                   │
│                                                │
│  Application Server                            │
│    FastAPI + React dashboard                   │
│    Nukkad event receiver                       │
│    CV signal receiver (MQTT broker)            │
│    Correlation + fraud engine                  │
│                                                │
│  Nukkad POS Cloud                              │
│    Pushes events to app server                 │
│                                                │
│  XProtect API Gateway                          │
│    WebRTC signaling for video playback         │
│                                                │
│  GPU Server (deferred)                         │
│    VLM if edge + EPOS proves insufficient      │
└────────────────────────────────────────────────┘
```

Edge device and XProtect are independent consumers of the same RTSP streams. They don't talk to each other.

## Data Flow

Nukkad pushes POS events (BeginTransactionWithTillLookup → SaleLines → PaymentLines → CommitTransaction) to the app server in real-time. Edge devices push CV signals (per-POS zone: seller presence, bill zone activity; camera-wide: non-seller count) via MQTT. The app server:

1. **Persists** raw events to disk immediately (write-ahead log)
2. **Assembles** POS events into complete transactions per `transactionSessionId`
3. **Correlates** assembled transactions with CV signals by SellerWindowId + timestamp
4. **Runs** fraud rules on the correlated data
5. **Merges** POS + CV events into a unified timeline per transaction (for video overlay)
6. **Broadcasts** alerts via WebSocket to dashboard

Dashboard connects to app server for data and to XProtect API Gateway for video (separate connections).

## System Requirements

### Edge Device (per store)

| Component | Spec | RLCC role |
|-----------|------|-----------|
| CPU | Intel Core Ultra 7 255H (6P+8E cores) | Tracking, analytics state, MQTT/HTTP transport |
| AIPU | Axelera Metis M.2 1GB (4 cores) | YOLO v26s/11s INT8, 600 FPS aggregate across all streams |
| iGPU | Intel Arc 140T | H.265 decode (40 streams) + OpenVINO crop classifiers (Phase 2) |
| RAM | 32 GB DDR5 | ~5-10 MB per camera + analytics state |
| Storage | 128 GB+ SSD | OS, models, zone configs, local signal buffer |
| Network | Gigabit Ethernet | RTSP ingest from cameras (LAN), MQTT out to app server (WAN) |
| OS | Ubuntu 22.04 LTS | DLStreamer + Voyager SDK + OpenVINO runtime |

**Throughput:** 5-6 FPS per camera. 30-40 cameras per device.
**Bandwidth out:** ~40 KB/s metadata (6 FPS × ~170 bytes × 40 cameras). Trivial.

### Application Server (centralized)

| Component | Minimum | Recommended (10+ stores) | Role |
|-----------|---------|--------------------------|------|
| CPU | 8 cores | 16 cores | FastAPI, fraud engine, MQTT broker, correlation |
| RAM | 16 GB | 32 GB | In-memory transaction assembly, CV signal aggregation, WebSocket connections |
| Storage | 500 GB SSD | 1 TB SSD | Transaction data, alerts, raw event logs, video snippets (flagged txns) |
| Network | 100 Mbps stable WAN | 1 Gbps | Inbound: all edge devices + Nukkad. Outbound: dashboard, video snippet upload |
| OS | Ubuntu 22.04 LTS | Same | Python 3.11+, FastAPI, MQTT broker (Mosquitto) |

**Scale math:** Each store generates ~240 MQTT messages/sec (40 cameras × 6 FPS) + ~200 POS transactions/day. At 10 stores: 2,400 MQTT messages/sec, 2,000 txns/day. At 50 stores: 12,000 messages/sec — likely needs horizontal scaling or message sampling.

### Storage Requirements

| Data | Volume (per store/day) | Retention | Storage |
|------|----------------------|-----------|---------|
| Raw Nukkad events | ~2,000 events (~5 MB) | 30 days | App server SSD |
| Assembled transactions | ~200 txns (~1 MB) | Clean: 7-30 days. Flagged: 90 days | App server SSD → PostgreSQL |
| Alerts | ~30 alerts (~100 KB) | 90 days | App server SSD → PostgreSQL |
| CV signal aggregation | ~240 msg/sec (~40 KB/s = ~3.4 GB raw) | Aggregated windows only, 14 days | App server SSD |
| Video snippets (flagged) | ~30 clips × ~3 MB = ~90 MB | 90 days | App server SSD or NAS |
| XProtect recordings | Continuous (managed by WAISL) | XProtect policy (7-30 days) | XProtect recording server |

**Phase 2 migration:** JSONL → PostgreSQL when data volume or query patterns outgrow flat files. Expected within weeks of multi-store operation.

### Network Requirements

| Path | Protocol | Bandwidth | Latency tolerance |
|------|----------|-----------|-------------------|
| Cameras → Edge device | RTSP (LAN) | ~2-5 Mbps per camera | <100ms |
| Cameras → XProtect | RTSP (LAN) | ~2-5 Mbps per camera | <100ms |
| Edge device → App server | MQTT over TLS (WAN) | ~40 KB/s per device | <5 seconds |
| Nukkad → App server | HTTPS (internet) | Bursty, <1 MB/s | <10 seconds |
| Dashboard → App server | HTTPS + WSS (WAN/LAN) | <1 MB/s | <1 second |
| Dashboard → XProtect | WebRTC (WAN, needs STUN/TURN) | 2-5 Mbps per video stream | <500ms for playback start |

**WebRTC caveat:** Airport networks are heavily firewalled. WebRTC needs STUN for NAT traversal and TURN as fallback. WAISL must confirm: (a) is a TURN server available? (b) can dashboard browsers reach the XProtect API Gateway? Test early in Phase 2A.

### GPU Server (deferred)

Not in current scope. Reserved for VLM inference on recorded video if edge CV + EPOS rules prove insufficient. Spec TBD based on model requirements.

## Software Stack

**Edge:** DLStreamer (decode + pipeline) + Voyager SDK (Metis inference) + OpenVINO (iGPU/CPU classifiers). Custom Python/C++ analytics on CPU. MQTT client (QoS 1) out.

**Backend:** Python 3.11+ FastAPI. WebSocket for real-time. MQTT broker (Mosquitto with persistent sessions). JSONL storage now, PostgreSQL in Phase 2.

**Frontend:** React 18 + TypeScript + Vite. Radix UI + Tailwind. Recharts. WebRTC (browser-native) for XProtect video.

**External:** Nukkad push API (POS events). Milestone XProtect WebRTC API (video). IP cameras (RTSP).

## Video Retention

**Problem:** We don't control XProtect retention policy. If they purge at 7 days, evidence for flagged transactions is gone.

**Solution: Hybrid.**

| Transaction type | Video access | Retention |
|-----------------|-------------|-----------|
| Clean | XProtect playback on demand | XProtect's policy (7-30 days) |
| Flagged | XProtect playback + self-stored snippet | Our storage, 90 days |

When a transaction is flagged (risk HIGH or MEDIUM), the server automatically initiates snippet extraction via XProtect WebRTC, records the relevant time window (transaction start - 30s to transaction end + 30s), and stores the clip as MP4/H.264. Retry 3× with exponential backoff on failure. A reconciliation job checks daily that all flagged transactions within the XProtect retention window have snippets.

**Open questions for WAISL:**
- Current XProtect retention policy?
- Per-camera or global?
- Server-side video export API available (beyond WebRTC playback)?

## Resilience

### Known risks and mitigations (pre-Phase 5)

| Risk | Impact | Mitigation |
|------|--------|-----------|
| App server crash | In-flight transactions lost, CV signals dropped, dashboard down | Raw events persisted to WAL on receipt → replay on restart reconstructs assembler state. Process supervisor (systemd) auto-restarts. |
| MQTT broker crash | Edge CV signals dropped, correlation blind | Mosquitto persistent sessions retain QoS 1 messages. Edge devices buffer locally and retry. EPOS-only rules continue working. |
| Nukkad doesn't retry | POS events permanently lost during our downtime | WAL persists events on receipt. If Nukkad truly doesn't retry, we add a periodic reconciliation poll against Nukkad's API to catch gaps. |
| Clock skew (edge vs Nukkad vs server) | Correlation matches wrong CV window, timeline ordering wrong | NTP mandatory on edge devices. Correlation window widened ±3s. Server adds `received_at` timestamp as ordering fallback. |
| JSONL files grow unbounded | Query slowdown, disk full | Daily file rotation. Disk space monitoring. PostgreSQL migration in Phase 2. |
| Edge device offline | No CV signals for that store | Health heartbeat every 30s. Server tracks `last_signal_seen` per camera. After 5 min silence, CV-initiated alerts suppressed for that store (avoid false negatives). |
| WebRTC can't connect (firewall) | No video playback in dashboard | Test connectivity early in Phase 2A. Fallback: WAISL confirms TURN server availability. If WebRTC fails entirely, video access via XProtect Smart Client (desktop app) as manual fallback. |
| XProtect retention expires before snippet extraction | Evidence for flagged transactions lost | Reconciliation job runs daily. Alerts generated for any flagged transaction missing a snippet within 24h of flagging. |

### What's NOT resilient until Phase 5

- No HA / failover for app server (single instance)
- No horizontal scaling for MQTT ingestion
- No real-time replication of transaction data
- Single MQTT broker (no cluster)

These are acceptable for POC and early production with <10 stores. Phase 5 addresses production hardening.

## Security

| Boundary | Auth | Status |
|----------|------|--------|
| Nukkad → App Server | `x-authorization-key` header + IP allowlist | Key defined in Nukkad API docs. Add IP allowlist. |
| Edge → App Server | MQTT username/password over TLS | Per-device credentials. ACLs restrict each device to its store's topics. |
| Dashboard → App Server | None (POC) → JWT (Phase 3) | Write endpoints (config, resolve) should get basic auth before any non-POC deployment. |
| Dashboard → XProtect | OAuth bearer token (1h expiry, auto-refresh) | Per MIP SDK docs. |

Phase 3 adds role-based access: operator (view + investigate + resolve) and admin (+ rule config, retention, store setup). Same portal, same team.

### Minimum before non-POC deployment
- Basic auth on dashboard write endpoints (POST /api/config, POST /api/alerts/{id}/resolve)
- MQTT per-device credentials with topic ACLs
- Nukkad IP allowlist
- TLS everywhere (MQTT, HTTPS, WSS)

## Monitoring

| What | How | Alert threshold |
|------|-----|-----------------|
| App server health | `/health` endpoint checks: MQTT connected, disk space, last Nukkad event time, last CV signal time, in-memory txn count | Any check failing |
| Edge device health | MQTT heartbeat every 30s on `rlcc/{store_id}/health` | No heartbeat for 5 minutes |
| Per-camera status | Track `last_signal_seen` per camera from CV signal stream | No signals for 5 minutes |
| Disk usage | App server SSD utilization | >80% |
| MQTT broker | Connection count, message rate, queue depth | Queue depth > 10,000 |
| Nukkad event flow | Last event received timestamp per store | No events for 2 hours during business hours |
