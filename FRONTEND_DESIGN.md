# Frontend Design — RLCC Dashboard

## Overview

Single-page React 18 + TypeScript app built with Vite. Radix UI primitives, Tailwind for styling, Recharts for visualization, WebRTC for video. Tab-based navigation across 7 views. Real-time updates via WebSocket. Architecture and backend details live in [ARCHITECTURE.md](./ARCHITECTURE.md).

**Current state:** POC dashboard works. Seven views: Transactions, Analytics, Alerts, Store Scorecard, Heatmap, Stream Viewer, Settings. A video playback component (`video-playback-view.tsx`) exists but is disconnected — no video source, not in nav. The main work ahead: wire up XProtect WebRTC, add event overlay, new filters, enhanced scorecards.

---

## Pages

### Transactions (default view)

**Stats bar** across the top: total transactions, high-risk count, medium-risk count, open alerts.

**Filters:**
- Search (free text)
- Risk level (Low / Medium / High)
- Time range (Today / 2 Days / Week / custom date range)
- Store (multi-select — currently single-select, upgrade needed)
- Amount range (min / max)
- **NEW** Payment mode: Cash, CreditCard, UPI, GiftCard, CreditNote, LoyaltyCard
- **NEW** Violation type: Manual Entry, Manual Discount, Manual Price, Void, Return, Reprint, Drawer Opened, Employee Purchase, Null Transaction

**Table columns:** ID, store, cashier, timestamp, total, risk level, camera ID, POS ID.

Click a row to open the transaction detail drawer.

### Transaction Detail Drawer

Slides in from the right.

**Store + device info** at the top.

**Transaction header:** cashier, timestamp, billNo, transactionType, employeePurchase flag.

**Items ordered** (per-item from `AddTransactionSaleLine`):
- Item name, qty, unit price, total
- `scanAttribute` badge: Auto / ManuallyEntered / ModifiedUnitPrice
- `discountType` badge, discount amount, `grantedBy`

**Payments** (per-payment from `AddTransactionPaymentLine`):
- Payment mode, amount, cardType

**Totals:** SubTotal, VAT, TotalDiscount, TotalEmployeeDiscount, TotalAmountToBePaid.

**Risk assessment:** level badge, triggered rules list, fraud category.

**Watch Footage** button opens the video player with event overlay.

### Analytics

**Summary cards:** total transactions, flagged %, total value, active stores.

**Charts:**
- Risk distribution (donut)
- Transactions over time (area)
- Transactions by store (stacked bar)
- Rule violations (horizontal bar)
- Hourly activity (bar)
- Avg transaction value by store (bar)
- Refund rate trend (area)
- **NEW** Manual entry rate trend
- **NEW** Void rate trend

### Alerts

**Status filter:** All, Open, Investigating, Closed.

**Summary cards:** total (neutral), open (red), investigating (amber), closed (green).

**Alert list:** status icon, transaction/rule info, risk badge, store, cashier, amount, timestamp, triggered rules.

**Resolve workflow:** status dropdown (Closed-Resolved, Closed-Genuine, Under Investigation, Confirmed Fraudulent) + remarks textarea.

**Watch Footage** button per alert.

### Store Scorecard

**Summary:** active stores, avg flag rate, high-risk stores.

**Table:** store name, total transactions, genuine/suspicious/fraudulent counts, flag rate %, revenue, risk level.

**NEW columns:** manual entry rate, void rate, discount rate per store.

### Heatmap

Card grid. Each store is a card. Background color by flag rate:
- Green: < 5%
- Yellow: 5–15%
- Amber: 15–30%
- Red: > 30%

Per card: store name, transaction count, revenue bar, genuine/suspicious/fraudulent breakdown, flag rate badge.

### Stream Viewer

Two columns: CV stream (edge signals) and POS stream (Nukkad events). Raw JSON, scrollable, event count badges. Diagnostic view for debugging ingestion.

### Settings

**Rule configuration cards** with slider + number input:
- Discount threshold (%)
- Refund amount threshold (INR)
- High value threshold (INR)
- Bulk purchase threshold (items)
- POS idle alert (minutes)

**NEW:** Per-rule enable/disable toggle for all fraud rules.

Save & apply, reset to defaults.

---

## Video Player + Event Overlay

The key new feature. Synchronized video playback with a timeline of POS + CV events for transaction investigation.

### Layout

```
┌──────────────────────┬─────────────────────────┐
│                      │  EVENT STREAM            │
│  XProtect WebRTC     │                          │
│  Video Playback      │  10:01:55 Customer enter │
│                      │  10:02:00 Txn opened     │
│  (recorded video     │  10:02:05 Chicken ×1 ₹249│
│   from transaction   │  10:02:18 ⚠ Fries MANUAL │
│   time window)       │  10:02:25 ⚠ Manual disc  │
│                      │  10:02:35 Cash ₹400      │
│                      │  10:02:38 Bill committed  │
│  [▶][⏸][⏪][⏩]      │  10:02:34 🧾 Receipt     │
│                      │  10:02:42 Customer left   │
├──────────────────────┴─────────────────────────┤
│  TIMELINE BAR                                   │
│  ──|──|──|──⚠──⚠──|──|──|──────────           │
│    ▲ current position                           │
└─────────────────────────────────────────────────┘
```

Left panel: video. Right panel: scrolling event stream. Bottom: scrubable timeline with event markers (warnings highlighted).

### XProtect WebRTC Integration

Connection sequence:

1. Fetch transaction detail — get `device_id`, `session_start`, `session_end`
2. `POST /webRTC/session` with `deviceId` + `playbackTime` (session_start as ISO 8601)
3. Exchange SDP offer/answer
4. Exchange ICE candidates via `/webRTC/iceCandidates/{sessionId}`
5. Peer connection established — H.264 video streams to `<video>` element
6. Auth: OAuth bearer token, 1-hour expiry, auto-refresh via `PATCH`

### Event Synchronization

Fetch unified timeline from `GET /api/transactions/{id}/timeline`.

Video playback starts at `playbackTime` (absolute timestamp). Track position via `rtpTimestamp` (millisecond offset from first frame):

```
currentAbsoluteTime = playbackTime + (rtpTimestamp / 1000)
```

As video plays, highlight events in the stream whose timestamp <= current time. Upcoming events stay dimmed.

Click any event in the stream to seek video to that event's timestamp.

### Receipt Line to Video Jump

Each sale line carries a `lineTimeStamp` from Nukkad. Click an item in the transaction detail drawer and the video seeks to that timestamp. Implemented by creating a new WebRTC session or seeking within the current one.

---

## State Management

All state lives in `dashboard.tsx` via React hooks. No external state library.

**Data state:**
- `transactions[]`, `alerts[]`, `billsMap` — fetched from API
- `rawCvData[]`, `rawPosData[]` — from WebSocket (current code uses `rawVasData` — rename pending)
- `storeNames` — from `/api/stores`

**Filter state:**
- `searchTerm`, `activeFilter`, `timeRange`, `storeFilter`, `minAmount`, `maxAmount`
- **NEW:** `paymentModeFilter`, `violationTypeFilter`, `receiptStatusFilter`

**UI state:**
- `activeTab`, `selectedTransaction`, `drawerOpen`, `isConnected`, `page`

**WebSocket** connects on mount. Event types: `NEW_TRANSACTION`, `NEW_ALERT`, `ALERT_UPDATED`, `CV_SIGNAL`.

---

## New Filters

What exists: search, risk level, time range (preset only), store (single-select), amount range.

What we add:

| Filter | Source | Type |
|---|---|---|
| Payment mode | Payment lines on transaction | Multi-select: Cash, CreditCard, UPI, GiftCard, CreditNote, LoyaltyCard |
| Violation type | `triggered_rules` on transaction | Multi-select: Manual Entry, Manual Discount, Manual Price, Void, Return, Reprint, Drawer Opened, Employee Purchase, Null Transaction |
| Receipt status | CV signal | Toggle: Generated / Not Generated |
| Store | `/api/stores` | Upgrade to multi-select |
| Date range | User input | Add custom date picker alongside existing presets |

---

## Existing Component: video-playback-view.tsx

Already built. Has play/pause, skip ±10s, seek timeline, markers, receipt item sync. Currently orphaned — no video source, not in navigation.

Work needed:
1. Connect to XProtect WebRTC as the video source
2. Add the event overlay timeline (right panel + bottom scrubber)
3. Wire into the transaction detail drawer ("Watch Footage" button)
4. Wire into the alert workflow ("Watch Footage" button)
5. Implement receipt-line-to-video-jump via `lineTimeStamp`
