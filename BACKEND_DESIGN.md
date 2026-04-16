# Backend Design

## 1. Service Overview

FastAPI application server. Two inbound data streams: Nukkad pushes POS events over HTTP, edge devices push CV signals over MQTT. Outbound: REST API and WebSocket to the dashboard.

The server does four things: assemble transactions from a stream of POS events, aggregate CV signals into per-zone state, correlate the two, and run fraud rules against the result. Everything else (API layer, WebSocket broadcasts, persistence) is plumbing around those four.

See [ARCHITECTURE.md](ARCHITECTURE.md) for system topology and hardware. See [CV_PIPELINE.md](CV_PIPELINE.md) for what the edge devices send and why.

---

## 2. Nukkad Event Receiver

Nukkad pushes events to a single endpoint. Auth is an `x-authorization-key` header.

**Open question:** Nukkad docs say "all APIs need to be stringified." This likely means the JSON body is stringified inside a wrapper field. Pending clarification on exact format.

### Endpoint

```
POST /v1/rlcc/launch-event
```

### Routing

The `event` field in the payload determines what happens:

| Event | Action |
|-------|--------|
| `BeginTransactionWithTillLookup` | Open a new transaction session |
| `AddTransactionSaleLine` / `AddTransactionSaleLineWithTillLookup` | Add item to session |
| `AddTransactionPaymentLine` | Add payment to session |
| `AddTransactionEvent` | Lifecycle event (Suspended, Resumed, Cancelled) |
| `AddTransactionTotalLine` | Add total line (SubTotal, VAT, TotalDiscount, TotalAmountToBePaid, TotalEmployeeDiscount) |
| `CommitTransaction` | Seal the transaction, trigger correlation + fraud rules |
| `BillReprint` | Immediate alert, no assembly needed |
| `GetTill` | Till lookup (not a transaction event) |

All events for a transaction share a `transactionSessionId`. The receiver routes each event to the Transaction Assembler by that ID.

---

## 3. Transaction Assembler

Accumulates Nukkad events into complete transactions keyed by `transactionSessionId`.

### State Machine

```
OPEN ──[CommitTransaction]──> COMMITTED ──> correlation + fraud rules
  │
  └──[timeout, no commit]──> EXPIRED ──> flag as abandoned
```

- **OPEN:** `BeginTransaction` received. Accumulating sale lines, payment lines, total lines, and lifecycle events as they arrive.
- **COMMITTED:** `CommitTransaction` received. The transaction is sealed and handed to the correlation engine + fraud engine.
- **EXPIRED:** No `CommitTransaction` within a configurable timeout (default 30 min). Flagged as abandoned for review.

### Assembled Transaction Shape

**Header:**
- store, POS terminal, cashier
- transactionType (Sale, Return, Exchange, CancellationOfPrevious, DrawerOpenedOutsideATransaction, etc.)
- employeePurchase flag
- outsideOpeningHours
- timestamp (from BeginTransaction)

**Items[]:** Each from an `AddTransactionSaleLine` event:
- itemDescription, itemQuantity, itemUnitPrice, totalAmount
- scanAttribute: None / Auto / ManuallyEntered / ModifiedUnitPrice
- itemAttribute: ReturnItem / CancellationWithinTransaction / VoidedBackorderItem / ExchangeSlipWithoutMatchingLine / etc.
- discountType, discount amount, grantedBy

**Payments[]:** Each from an `AddTransactionPaymentLine` event:
- lineAttribute: Cash / CreditCard / UPI / GiftCard / CreditNotePayment / LoyaltyCard / ReturnCash / etc.
- amount, cardType, paymentTypeID

**Totals[]:** Each from an `AddTransactionTotalLine` event:
- lineAttribute: SubTotal / VAT / TotalDiscount / TotalAmountToBePaid / TotalEmployeeDiscount
- amount

**Events[]:** Lifecycle events from `AddTransactionEvent`:
- TransactionSuspended, TransactionResumed, TransactionCancelled (with timestamps)

**Commit fields:**
- billNumber (from CommitTransaction)
- isPreviousTransaction + linked transaction fields (for returns/exchanges referencing prior transactions)

---

## 4. CV Signal Receiver

Receives CV signals from edge devices via MQTT. See [CV_PIPELINE.md](CV_PIPELINE.md) for the full signal schema and MQTT topic definitions.

### Server-Side Aggregation

Signals arrive at 5-6 FPS per camera. The server aggregates them into two kinds of state:

**Per-POS (from zone signals):**
- Seller presence timeline — when seller entered/left each POS zone, duration
- Bill zone activity windows — when motion and/or background change detected at each POS's bill printer area

**Camera-wide (from non_seller fields):**
- Non-seller presence — are non-sellers (customers, browsers, etc.) visible on this camera?
- Non-seller count max — peak count during a time window

These are different scopes. Seller and bill signals are per-POS because cashiers and printers are at fixed positions. Non-seller presence is camera-wide because customers move freely, especially in airport retail where per-POS customer zones don't work.

This aggregated state is what the correlation engine queries when a transaction commits.

### Other MQTT Topics

- `rlcc/{store_id}/{camera_id}/activity` — Phase 2 seller activity classification (handling_item, handling_cash, idle, etc.)
- `rlcc/{store_id}/health` — device health (cameras active, FPS, CPU/memory)

---

## 5. Correlation Engine

Links POS transactions with CV signals. Two modes.

### POS-Anchored (primary)

When a transaction commits, the engine looks up the CV signal window for the matching POS zone during the transaction's time range.

Matching logic: the transaction's SellerWindowId (resolved via `mapping.json` or `GetTill`) maps to a `pos_zone` from CV signals. The POS zone tells us which camera covers it. Timestamp overlap confirms the match.

Attached to the transaction:
- `non_seller_present` (bool) — were non-sellers visible on this camera during the transaction? Camera-wide, not per-POS.
- `non_seller_count` (int) — peak non-seller count during the transaction window. Camera-wide.
- `receipt_detected` (bool) — did the bill zone for this POS show motion + background change? Per-POS.
- `cv_confidence` — HIGH for single-POS cameras (non_seller_present is unambiguous), REDUCED for multi-POS cameras (can't attribute non-sellers to a specific POS).

**Multi-POS limitation:** On cameras covering multiple POS counters, `non_seller_present: true` can't be attributed to a specific POS — someone is there, but we don't know which counter they're at. However, `non_seller_present: false` is definitive — nobody is at any counter on this camera. This asymmetry matters for rules like "void without customer."

### CV-Initiated

When CV signals show sustained seller presence at a POS zone + `non_seller_present: true` camera-wide for >30 seconds with no `BeginTransaction` from Nukkad, the engine raises a "Missing POS" alert. This is the revenue leakage detection path — a transaction happened in physical space but not in the POS system.

Phase 2 adds seller activity classification to reduce false positives here (cashier chatting vs. actually transacting). See [CV_PIPELINE.md](CV_PIPELINE.md) Phase 2 section.

---

## 6. Fraud Engine

29 rules organized by data source required.

### EPOS-Only Rules

Fire on assembled transaction data alone. No CV signal needed.

| # | Rule | Trigger | Default Risk |
|---|------|---------|-------------|
| 1 | High discount | discount > threshold % | Medium |
| 2 | Refund / excess cash return | refund > threshold amount | Medium |
| 3 | Complementary order | IsComplementary flag | Low |
| 4 | Void / cancelled transaction | VoidReason or CancelDate present | Medium |
| 5 | Negative amount | TransactionTotal < 0 | High |
| 6 | High value transaction | total > threshold | Low |
| 7 | Bulk purchase | item count > threshold | Low |
| 8 | Manual item entry | scanAttribute: ManuallyEntered | Medium |
| 9 | Manual price change | scanAttribute: ModifiedUnitPrice | Medium |
| 10 | Manual discount | discountType: ManuallyEnteredValue/Percentage | Medium |
| 11 | Self-granted discount | grantedBy == cashier | Medium |
| 12 | Drawer opened outside transaction | transactionType: DrawerOpenedOutsideATransaction | High |
| 13 | Bill reprint | BillReprint event | Medium |
| 14 | Null transaction | CommitTransaction with zero sale lines | Medium |
| 15 | Post-bill cancellation | transactionType: CancellationOfPrevious | High |
| 16 | Return not recently sold | itemAttribute: ReturnNotRecentlySold | Medium |
| 17 | Exchange without matching line | itemAttribute: ExchangeSlipWithoutMatchingLine | Medium |
| 18 | Employee purchase | employeePurchase: true | Low |
| 19 | Per-item void percentage | CancellationWithinTransaction items / total items > threshold | Medium |
| 20 | Outside opening hours | outsideOpeningHours != InsideOpeningHours | Medium |
| 21 | Credit note payment | lineAttribute: CreditNotePayment | Medium |
| 22 | Manual credit card entry | CreditCard payment + manual entry indicator | Medium |
| 23 | Full return | All items in transaction are ReturnItem | High |

Employee purchase (rule 18) is not fraud by itself but tracked for audit visibility.

### CV-Only Rules

Fire on CV signals alone when no POS event exists to correlate with.

| # | Rule | Trigger | Default Risk |
|---|------|---------|-------------|
| 24 | Missing POS | CV non_seller + seller presence with no Nukkad events for >30s | High |
| 25 | POS idle | No CV seller presence + no Nukkad events during business hours | Low |

### Cross-Validation Rules

Require both POS data and CV signals.

| # | Rule | Trigger | Default Risk |
|---|------|---------|-------------|
| 26 | Void without customer | CancellationWithinTransaction + non_seller_present: false | High |
| 27 | Return without customer | ReturnItem + non_seller_present: false | High |
| 28 | Drawer open without customer | DrawerOpenedOutsideATransaction + non_seller_present: false | High |
| 29 | Bill not generated | CommitTransaction but receipt_detected: false | Medium |

### Risk Scoring

Each rule's default risk level is in the table above. The rule table is the source of truth — no need to duplicate the list here.

**Compound escalation:** Multiple signals on the same transaction increase severity. Two MEDIUM triggers on one transaction escalate to HIGH.

### Configuration

Each rule is independently enable/disable-able. Rules with thresholds (discount %, refund amount, high value, bulk quantity, void %) accept configurable values. Stored in `rule_config.json` now, database in Phase 2.

---

## 7. Unified Event Timeline

For each committed transaction, the server merges POS events and CV signals into a single chronologically sorted timeline. This powers the event overlay on the video player in the dashboard.

```json
{
  "transaction_id": "TXN-000142",
  "device_id": "cam-pos3-store-asha",
  "timeline": [
    {"ts": "...", "source": "cv", "type": "customer_entered", "data": {}},
    {"ts": "...", "source": "pos", "type": "begin_transaction", "data": {"cashier": "Ravi"}},
    {"ts": "...", "source": "pos", "type": "sale_line", "data": {"item": "Chicken Burger", "qty": 1, "amount": 249, "scan": "Auto"}},
    {"ts": "...", "source": "pos", "type": "sale_line", "data": {"item": "Fries", "qty": 1, "amount": 99, "scan": "ManuallyEntered"}},
    {"ts": "...", "source": "cv", "type": "receipt_detected", "data": {"method": "motion+bg_change"}},
    {"ts": "...", "source": "pos", "type": "commit", "data": {"billNo": "INV-9823"}},
    {"ts": "...", "source": "cv", "type": "customer_left", "data": {}}
  ]
}
```

Fetched via `GET /api/transactions/{id}/timeline`. Each `sale_line` entry carries a `lineTimeStamp` from Nukkad, which the dashboard uses to jump the video player to that exact moment when the user clicks a receipt line.

---

## 8. Data Model

### TransactionSession

| Field | Type | Notes |
|-------|------|-------|
| id | string | transactionSessionId from Nukkad |
| store_id | string | |
| pos_terminal | string | |
| cashier_id | string | |
| transaction_type | enum | Sale, Return, Exchange, CancellationOfPrevious, DrawerOpenedOutsideATransaction, etc. |
| employee_purchase | bool | |
| outside_opening_hours | enum | InsideOpeningHours / OutsideOpeningHours |
| status | enum | assembling / committed / expired |
| started_at | datetime | |
| committed_at | datetime | nullable |
| bill_number | string | from CommitTransaction, nullable |
| is_previous_transaction | bool | for returns/exchanges |
| linked_transaction_id | string | nullable |
| risk_level | enum | High / Medium / Low |
| triggered_rules | string[] | |
| camera_id | string | From CV correlation or mapping |
| device_id | string | XProtect device GUID for video playback |
| cv_non_seller_present | bool | From correlation (camera-wide) |
| cv_receipt_detected | bool | From correlation (per-POS bill zone) |
| cv_non_seller_count | int | Peak count from correlation (camera-wide) |
| cv_confidence | enum | HIGH (single-POS camera) / REDUCED (multi-POS camera) / UNAVAILABLE (CV data gap) |

### SaleLine

| Field | Type | Notes |
|-------|------|-------|
| transaction_session_id | FK | |
| line_number | int | |
| line_timestamp | datetime | |
| item_id | string | |
| item_description | string | |
| item_quantity | decimal | |
| item_unit_price | decimal | |
| total_amount | decimal | |
| scan_attribute | enum | None / Auto / ManuallyEntered / ModifiedUnitPrice |
| item_attribute | enum | None / ReturnItem / CancellationWithinTransaction / VoidedBackorderItem / ExchangeSlipWithoutMatchingLine / etc. |
| discount_type | enum | NoLineDiscount / AutoGeneratedValue / AutoGeneratedPercentage / ManuallyEnteredValue / ManuallyEnteredPercentage |
| discount_amount | decimal | |
| granted_by | string | nullable |

### PaymentLine

| Field | Type | Notes |
|-------|------|-------|
| transaction_session_id | FK | |
| line_number | int | |
| line_timestamp | datetime | |
| line_attribute | enum | Cash / CreditCard / UPI / GiftCard / CreditNotePayment / LoyaltyCard / ReturnCash / etc. |
| payment_description | string | |
| amount | decimal | |
| currency_code | string | |
| card_type | string | nullable |
| payment_type_id | string | nullable |

### TotalLine

| Field | Type | Notes |
|-------|------|-------|
| transaction_session_id | FK | |
| line_attribute | enum | SubTotal / VAT / TotalDiscount / TotalAmountToBePaid / TotalEmployeeDiscount / etc. |
| description | string | |
| amount | decimal | |

### TransactionEvent

| Field | Type | Notes |
|-------|------|-------|
| transaction_session_id | FK | |
| line_timestamp | datetime | |
| line_attribute | enum | TransactionSuspended / TransactionResumed / TransactionCancelled |
| event_description | string | |

### Alert

| Field | Type | Notes |
|-------|------|-------|
| id | string | |
| transaction_session_id | FK | nullable — CV-only alerts have no transaction |
| store_id | string | |
| pos_zone | string | |
| cashier_id | string | nullable |
| risk_level | enum | High / Medium / Low |
| triggered_rules | string[] | |
| timestamp | datetime | |
| status | enum | new / reviewing / resolved / fraudulent / genuine. Frontend labels: new→Open, reviewing→Investigating, resolved→Closed-Resolved, genuine→Closed-Genuine, fraudulent→Confirmed Fraudulent |
| resolved_by | string | nullable |
| resolved_at | datetime | nullable |
| remarks | string | nullable |

### CVSignalWindow

| Field | Type | Notes |
|-------|------|-------|
| store_id | string | |
| camera_id | string | |
| pos_zone | string | |
| window_start | datetime | |
| window_end | datetime | |
| seller_present_pct | float | % of frames with seller detected (per-POS zone) |
| non_seller_present_pct | float | % of frames with non-sellers detected (camera-wide, not zone-level) |
| non_seller_count_max | int | Peak non-seller count in window (camera-wide) |
| bill_motion_detected | bool | |
| bill_bg_change_detected | bool | |
| seller_activity | enum | Phase 2: idle / handling_item / handling_cash / using_pos / giving_receipt |

### EventTimeline

| Field | Type | Notes |
|-------|------|-------|
| transaction_session_id | FK | |
| events | json[] | sorted by timestamp, includes both POS and CV events |

### Storage

- **Current:** JSONL flat files (`transactions.jsonl`, `alerts.jsonl`). Good enough for POC and Phase 0/1.
- **Phase 2:** PostgreSQL with the schema above.
- **Raw events:** All Nukkad push events stored separately in `raw_events.jsonl` for replay and debugging. Never modified after write.

---

## 9. API Specifications

### Nukkad Receiver (inbound)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/rlcc/launch-event` | Accepts all Nukkad push events |

### Dashboard REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/transactions` | List transactions (filterable, paginated) |
| GET | `/api/transactions/{id}` | Transaction detail with items, payments, totals |
| GET | `/api/transactions/{id}/timeline` | Unified event timeline for video overlay |
| GET | `/api/alerts` | List alerts (filterable, paginated) |
| POST | `/api/alerts/{id}/resolve` | Resolve alert with status + remarks |
| GET | `/api/stores` | Store list |
| GET | `/api/config` | Current rule configuration |
| POST | `/api/config` | Update rule thresholds + enable/disable rules |
| GET | `/api/reports/store-daily` | Store daily summary |
| GET | `/api/reports/employee-scorecard` | Per-employee metrics |
| GET | `/api/history?days=N` | Backfill historical data |

### Dashboard WebSocket

| Path | Events |
|------|--------|
| `WS /ws` | `NEW_TRANSACTION` — new transaction processed |
| | `NEW_ALERT` — new alert fired |
| | `ALERT_UPDATED` — alert status changed |
| | `CV_SIGNAL` — raw CV signal (for stream viewer) |

### Edge Device (inbound, via MQTT)

| Topic | Description |
|-------|-------------|
| `rlcc/{store_id}/{camera_id}/signals` | CV zone signals (5-6 FPS) |
| `rlcc/{store_id}/{camera_id}/activity` | Phase 2 activity classification |
| `rlcc/{store_id}/health` | Device health (every 60s) |

---

## 10. Sales Data API (Existing Pull-Based Integration)

The existing Nukkad sales data API (polled by `SalesPoller`) is not replaced by the push API. It serves three purposes:

**Primary source for ARMS POS stores.** The push API does not cover ARMS. Stores with `pos_system: "ARMS-Dino"` continue on pull-based 2-minute polling. The fraud engine accepts transactions from both paths: assembled from push events (granular) or from polled bill data (aggregated). Same rules, same alert pipeline — but polled bills lack per-item `scanAttribute`, `discountType`, and `itemAttribute` enums, so the new rules (manual entry, manual discount, etc.) only fire for push API stores.

**Historical backfill.** When a new store comes online or the system is redeployed, the push API won't replay past events. The sales data API fetches completed bills for the past N days via `SalesPoller.fetch_historical(days)`. This populates the transaction history so the dashboard isn't empty on day one.

**Reconciliation.** If our server was down and Nukkad didn't retry push events, those transactions are lost from the push stream. A periodic reconciliation job polls the sales data API for completed bills, compares against assembled transactions by `billNumber`, and backfills any gaps. This runs hourly (configurable) and catches any push API misses.

```
Nukkad Push API (real-time, per-item, per-payment)
    → Primary ingest for Posifly POS stores
    → Events arrive in seconds, full enum detail
    → 29 fraud rules applicable

Nukkad Sales Data API (poll every 2 min, aggregated bills)
    → Primary ingest for ARMS POS stores
    → Historical backfill on deploy/restart
    → Hourly reconciliation to catch push API gaps
    → 9 original fraud rules applicable (less granular data)
```

---

## 11. Resilience & Failure Handling

### Assembler state persistence

Every raw Nukkad event is appended to `raw_events.jsonl` on receipt, before any processing. On server restart, we replay all events for OPEN sessions from this log to reconstruct assembler state. The assembler is stateless between restarts — its state is derivable from the event log.

### Idempotency

Deduplicate incoming events by `transactionSessionId` + event type + `lineNumber`. The server acknowledges to Nukkad only after the event is persisted to `raw_events.jsonl`. If Nukkad retries a delivery, the duplicate is dropped silently.

### Event ordering

POS events can arrive out of order (e.g., a SaleLine before its BeginTransaction). If an event arrives for an unknown session, buffer it for up to 5 seconds. If BeginTransaction arrives within that window, attach the buffered events normally. If it doesn't, create the session implicitly from the buffered event's metadata — better to have an incomplete session than to drop data.

CommitTransaction waits 500ms after sealing the session before handing it to the correlation + fraud engines. This catches straggler events (a final SaleLine or PaymentLine that arrives just after the commit) without adding meaningful latency.

### Lost CommitTransaction

Sessions that hit the 30-minute timeout with sale lines present are not silently discarded. They generate an "Abandoned Transaction" alert. Available fraud rules still run on the partial data — a session with 10 voided items and no commit is still worth flagging.

### MQTT QoS

QoS 1 (at least once) for all topics. CV signals are idempotent snapshots — the latest frame's signal for a zone completely supersedes the previous one, so duplicates are harmless. QoS 1 ensures delivery across brief network hiccups without the overhead of QoS 2's four-step handshake.

### CV signal gaps

If the server restarts, recent CV aggregation state (the in-memory signal windows) is lost. Transactions that committed during a CV data gap are marked `cv_confidence: UNAVAILABLE`. Cross-validation rules (26-29) are suppressed for those transactions — we'd rather skip CV checks than fire false positives from missing data.

### Clock skew

NTP is required on all edge devices and the app server. The correlation time window is widened by ±3 seconds beyond the actual transaction duration to tolerate clock drift. A `received_at` timestamp is added server-side on every incoming event as an ordering fallback — if edge timestamps look wrong, we fall back to arrival order.
