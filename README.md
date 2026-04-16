# RLCC — Revenue Leakage Control Center

Fraud detection system for retail stores combining real-time POS event analysis with computer vision-based transaction monitoring.

**Core idea:** Nukkad POS pushes transaction events in real-time. Edge CV devices detect customer presence and receipt generation at each POS counter. The backend correlates both streams — when they agree, the transaction is clean. When they disagree (or one is missing), fraud rules fire.

## Documentation

| Document | What it covers |
|----------|---------------|
| [PRD.md](PRD.md) | Product requirements, BRD mapping, acceptance criteria, phase roadmap, blockers |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System topology, hardware, software stack, network, video retention strategy |
| [CV_PIPELINE.md](CV_PIPELINE.md) | Edge CV design — zone signals (Phase 1), activity classifier (Phase 2), multi-POS handling |
| [BACKEND_DESIGN.md](BACKEND_DESIGN.md) | Event receiver, transaction assembler, fraud engine, data model, API specs |
| [FRONTEND_DESIGN.md](FRONTEND_DESIGN.md) | Dashboard pages, WebRTC video player, event overlay timeline, filters |
| [INTEGRATION.md](INTEGRATION.md) | Nukkad push API, XProtect WebRTC, edge protocol, onboarding checklist |

## System Overview

```
IP Cameras (30-40 per store)
    |
    |--- RTSP ---> Edge Device (Intel Ultra 7 + Axelera Metis)
    |                  CV inference: person detection, zone presence, bill zone activity
    |                  Sends metadata to app server (MQTT/HTTP)
    |
    |--- RTSP ---> XProtect Recording Server (WAISL-managed)
                       Records video, serves playback via WebRTC API

Nukkad POS Cloud
    |--- Push events ---> Application Server (FastAPI)
                              Receives POS events + CV signals
                              Correlates, runs fraud rules, fires alerts
                              Serves dashboard + WebSocket updates

Dashboard (React)
    |--- REST/WebSocket ---> Application Server
    |--- WebRTC -----------> XProtect API Gateway (video playback)
```

## Stakeholders

| Team | Role |
|------|------|
| **Us** | Build and operate the RLCC system |
| **GHIAL** | Client — airport retail operator |
| **WAISL** | Infrastructure provider — cameras, XProtect VMS, network |
| **Nukkad** | POS software provider — transaction data via push API |

## Current Status

- **POC complete** — 3 stores, basic fraud rules, dashboard with alert workflow
- **API docs received** — Nukkad push API + XProtect WebRTC API
- **Architecture defined** — edge CV + centralized backend + XProtect video
- **Building with emulators** — full system testable without live camera/POS connections
