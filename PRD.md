# RLCC — Revenue Leakage Control Center

**Product Requirements Document**

| Field | Value |
|-------|-------|
| Client | GHIAL (GMR Hyderabad International Airport Limited) |
| Domain | Fraud detection for airport retail concessions |
| Date | 2026-04-16 |
| Status | Active — Phase 0 (emulator build) |

---

## 1. Product Vision & Objectives

RLCC monitors retail transactions at airport concession stores by correlating POS data with video analytics to detect revenue leakage and fraud.

**Core capability:** For every retail transaction (or absence of one), the system cross-references structured POS event data from Nukkad with computer-vision signals from edge cameras, surfaces alerts when anomalies are detected, and provides synchronized video playback for investigation.

### Detection Categories

| Category | Examples |
|----------|----------|
| Transaction without bill | Customer served, payment taken, no EPOS record |
| Fraudulent voids/returns | Post-bill cancellation, return without customer present, return not recently sold |
| Discount abuse | Manual discounts, unauthorized discounts, discount > threshold |
| Manual entry abuse | Manually entered items, manually entered prices |
| Cash drawer anomalies | Drawer opened outside a transaction |
| Reprints | Bill reprinted (possible duplicate billing) |
| POS as ATM | Cash-back via return/refund pattern |
| Null transactions | Transaction opened and committed with no sale lines |
| Employee purchase irregularities | Self-checkout patterns, missing employee flags |

### System Components

| Component | Technology | Role |
|-----------|-----------|------|
| Edge CV pipeline | Intel Ultra 7 + Axelera Metis M.2 | 30-40 cameras per store at 5-6 FPS, customer/seller detection, bill zone monitoring, zone-based activity classification |
| POS event stream | Nukkad push API | Real-time transaction events (BeginTransactionWithTillLookup, AddTransactionSaleLine, AddTransactionPaymentLine, CommitTransaction, etc.) |
| Video management | Milestone XProtect VMS | Recording, retention, live streaming, recorded playback via WebRTC |
| Application server | FastAPI backend | Fraud engine, transaction assembly, alert management, API layer |
| Dashboard | React | Alert workflow, investigation UI, reporting, video playback |

---

## 2. Users

One team, one portal. The GHIAL/WAISL team handles everything — monitoring, investigation, analytics, rule configuration. Same dashboard, two access levels.

### Operator View (all users)

- Review incoming alerts in real time
- Investigate flagged transactions: view POS data, watch synchronized video, review event timeline
- Resolve alerts with remarks (genuine, escalated, false positive)
- View analytics: store comparisons, employee scorecards, trend charts
- Export reports (daily summaries, shift-wise breakdowns)

### Admin View (authorized users)

- Everything in operator view, plus:
- Configure alert rules: enable/disable categories, adjust thresholds
- Manage data retention policies
- Store/POS configuration

---

## 3. Requirements (BRD Mapping)

### Status Legend

| Status | Meaning |
|--------|---------|
| DONE | Implemented and working |
| DONE/ENHANCED | Was working, now improved with richer data from new APIs |
| PARTIALLY DONE | Core logic exists, gaps remain |
| BUILDABLE | Not yet built, but all required data/APIs are now available |
| PLANNED | Requires additional infrastructure or dependencies not yet available |
| NEEDS INPUT | Blocked on information from a stakeholder |
| NOT IN SCOPE | Excluded from current project phases |

All requirements in BRD order. Every item accounted for.

| Req | Requirement | Status | Pri | Approach | Acceptance Criteria |
|-----|-------------|--------|-----|----------|-------------------|
| 1 | POS terminal mapped to camera stream | DONE | P0 | `mapping.json` + Nukkad `GetTill` API for dynamic resolution | Given a POS terminal ID, system resolves the correct camera stream(s) |
| 2 | Transaction mapped to CCTV footage + suspicious flagging | BUILDABLE | P0 | XProtect WebRTC playback by timestamp. Missing POS alert when CV detects transaction with no EPOS match. Auto-tag when delayed EPOS arrives. | Clicking any transaction opens synchronized video. Missing POS alert fires within 30s. |
| 3, 4, 16 | POS text overlay on live/recorded video | BUILDABLE | P1 | Event overlay player: POS events (items, discounts, voids, prices) displayed alongside synchronized video playback. Functionally equivalent to text overlay but cleaner — events in a timeline panel synced to video, not burned into frames. | Investigator sees POS data synchronized with video during playback. Events highlight as video reaches their timestamp. |
| 5 | Employee-specific transaction reports | BUILDABLE | P1 | Nukkad push API provides `cashierID`, `employeePurchase` flag, `OperatorSignOn`/`Off` for shift boundaries. | Reports show per-employee: transaction count, void rate, discount rate, manual entry count, alerts triggered. Filterable by date range and shift. |
| 6 | Filter transactions by type | BUILDABLE | P1 | Per-violation-type filters from Nukkad enums. | Dashboard filters: Void, Return, Manual Entry, Manual Discount, Employee Purchase, Drawer Opened, Reprint. Filters combinable. |
| 7 | Deterministic triggers for fraud events + notification workflow | DONE | P0 | 9 existing rules + 16 new rules. Real-time push means alerts fire within seconds. | Alert appears in dashboard within 5 seconds of triggering event. |
| 8 | Recording retention (clean 1 week, suspicious 3 months) | BUILDABLE | P1 | Hybrid: XProtect for recent playback, self-stored snippets for flagged transactions. GHIAL to confirm clean retention period (1 week vs 1 month). | Clean transactions auto-purged after confirmed period. Flagged transaction video retained 3 months. |
| 9 | High availability / failover | NOT IN SCOPE | P3 | Phase 5 production infrastructure. | N/A for current phases. |
| 10 | 24x7 live monitoring with 30-day snippet retention | BUILDABLE | P1 | CV pipeline runs 24x7. XProtect WebRTC for live streaming. 30-day retention configurable in XProtect. | Live view available for any camera. Recorded footage retrievable for any timestamp within 30 days. |
| 11a | Object tracking (printer + Pax) | PARTIALLY DONE | P1 | Printer monitored via CV bill zone. Pax: EPOS payment line data as primary, optional CV Pax zone as secondary. | Bill printed/not-printed detected via CV. Payment instrument known from EPOS. |
| 11b | Void/blank line items | DONE/ENHANCED | P0 | Per-item `itemAttribute: CancellationWithinTransaction` from Nukkad push API. | Each voided line item captured individually with amount and timestamp. |
| 11c | Negative amount | DONE | P0 | `TransactionTotal < 0` | Alert fires for any committed transaction with negative total. |
| 11d | Null transactions | BUILDABLE | P0 | `BeginTransactionWithTillLookup` followed by `CommitTransaction` with no `AddTransactionSaleLine` events. | Alert fires when transaction commits with zero sale lines. |
| 11e | Manual punching of item | BUILDABLE | P0 | `scanAttribute: "ManuallyEntered"` from Nukkad push API. | Alert fires for each manually entered item. Manual entry rate tracked per cashier. |
| 11f | Manual punching of discount | BUILDABLE | P0 | `discountType: "ManuallyEnteredValue"/"ManuallyEnteredPercentage"` + `grantedBy` field. | Alert fires for manual discounts. `grantedBy` identifies who authorized. |
| 11g | Cancelled items | DONE/ENHANCED | P0 | Three types: item voided mid-transaction, entire transaction cancelled, post-bill cancellation. | Each type generates a distinct alert. |
| 11h | Cash drawer open without transaction | BUILDABLE | P0 | `transactionType: "DrawerOpenedOutsideATransaction"`. | Alert fires immediately on drawer-open event outside any transaction. |
| 11i | POS as ATM | BUILDABLE | P1 | Payment line `lineAttribute` gives explicit payment modes. Pattern detection for `ReturnCash` abuse. | Alert fires on return-cash patterns. Payment mode logged for every transaction. |
| 11j | Item scanning not reflecting on screen | NOT IN SCOPE | — | Failed scans produce no EPOS event — indistinguishable from "item not scanned." | N/A. |
| 11k | Mis-punching of item | DONE | P0 | Per BRD: "mis-punch considered as Void." Covered by `itemAttribute: CancellationWithinTransaction`. | Same as 11b. |
| 11l | Return/Refund | DONE/ENHANCED | P0 | Per-item: `ReturnItem`, `ReturnNotRecentlySold`, `ExchangeSlipWithoutMatchingLine`. Payment: `ReturnCash`. | Each return subtype generates a specific alert. |
| 11m | EPOS not utilized (goods exchanged, no bill) | DONE | P0 | CV detects customer session, no POS event = Missing POS alert. Timeout reduced to ~30s with push API. | Missing POS alert fires within 30s. |
| 11n | No printout from printer | DONE | P0 | CV bill zone detection. | Alert fires when transaction completes but no bill detected. |
| 11o | Render cash but no bill | DONE | P0 | Combination of 11m + 11n. | Alert fires on cash exchange with no bill and no EPOS record. |
| 11p | Reprinting / duplicate bill | BUILDABLE | P0 | `BillReprint` event from Nukkad. | Alert fires on every reprint. Reprint count tracked per cashier. |
| 11q | EDC/UPI/Cash transaction without POS | DONE/ENHANCED | P0 | Missing POS alert + payment instrument known from EPOS. | Alert includes detected payment method when available. |
| 11r | Enable/disable alert categories | BUILDABLE | P1 | Per-rule enable/disable checkbox + threshold config. | Admin can toggle each rule on/off. Changes take effect immediately. |
| 12(i)(a) | Unauthorized discount with video | BUILDABLE | P1 | `grantedBy` field; check against authorized granters. Video via XProtect. | Alert fires when discount granted by unauthorized operator. |
| 12(i)(b) | Manual discount with video | BUILDABLE | P0 | `discountType: ManuallyEnteredValue/Percentage`. | Alert fires on manual discount with amount, granter, and video. |
| 12(i)(c) | Discount > X% with video | DONE | P0 | Threshold-based rule. | Alert fires when discount exceeds configured threshold. |
| 12(ii)(a) | Return > X amount with video | DONE | P0 | Threshold-based rule. | Alert fires when return exceeds configured threshold. |
| 12(ii)(b) | Return without customer with video | BUILDABLE | P1 | Cross-validate: return + CV shows no customer. | Alert fires when return processed with no customer detected. |
| 12(ii)(c) | Refund without receipt | BUILDABLE | P1 | `ReturnNotRecentlySold` or `ExchangeSlipWithoutMatchingLine`. | Alert fires with return subtype detail. |
| 12(ii)(d) | Full return | BUILDABLE | P1 | All items in original transaction returned. | Alert fires when 100% of transaction value returned. |
| 12(ii)(e) | Credit note | BUILDABLE | P1 | `lineAttribute: CreditNotePayment` from payment line. | Alert fires on credit note issuance. |
| 12(iii)(a) | No transaction for X mins | DONE | P0 | CV-detected customer session with no EPOS match. Multi-zone: Phase 5. | Missing POS alert with video. |
| 12(iv)(a) | Void without customer knowledge | DONE/ENHANCED | P0 | Per-item void with `CancellationWithinTransaction`. | Each voided item generates alert with details. |
| 12(iv)(b) | Void lines > X% of transaction | BUILDABLE | P1 | Void count / total items per cashier per shift. | Alert fires when void rate exceeds threshold. |
| 12(iv)(c) | Quantity of no-sales | DONE | P0 | Post-bill cancellation from event sequence. | Alert fires on post-bill cancellation. |
| 12(v)(a) | Manual credit card entry | BUILDABLE | P1 | Payment line with manual card indicator. | Alert fires on manually entered card payment. |
| 12(v)(b) | Manual price change | BUILDABLE | P1 | `scanAttribute: ModifiedUnitPrice`. | Alert fires on manually changed price. |
| 12(v)(c) | Manual discount change | BUILDABLE | P0 | Same as 12(i)(b). | Alert fires on manual discount. |
| 12(v)(d) | Manual cash drawer opening | BUILDABLE | P0 | Same as 11h. | Alert fires on drawer opened outside transaction. |
| 12(vi) | Amount exceeding X value | DONE | P1 | Threshold-based rule on transaction total. | Alert fires when total exceeds threshold. |
| 12(vii) | Bulk purchase | DONE/ENHANCED | P1 | Threshold on item count. Enhanced with per-item detail. | Alert fires when count exceeds threshold. |
| 13(a) | Filter: bill amount range | DONE | P1 | Min/max amount filter. | From/to range filter on bill amount. |
| 13(b) | Filter: void amount range | BUILDABLE | P1 | Per-item void amounts from enriched data. | Min/max amount filter on void alerts. |
| 13(c) | Filter: date/time range | BUILDABLE | P1 | Custom date range picker. | Arbitrary start/end date selection. |
| 13(d) | Filter: business vertical | NEEDS INPUT | P2 | Requires WAISL store hierarchy API. | Blocked on WAISL. |
| 13(e) | Filter: payment mode | BUILDABLE | P1 | `lineAttribute` from payment lines. | Filter by: Cash, CreditCard, UPI, CreditNote, etc. |
| 13(f) | Filter: store (single & multiple) | BUILDABLE | P1 | Multi-select store filter. | Single and multi-store selection. |
| 13(g) | Filter: no physical bill issue | BUILDABLE | P1 | CV `ReceiptGenerationStatus == false`. | Toggle filter for no-bill transactions. |
| 13(h) | Filter: EPOS not touched + manual bill checkbox | BUILDABLE | P1 | CV Missing POS + `scanAttribute: ManuallyEntered`. | Filter + manual bill acknowledgement checkbox. |
| 13(i) | Filter: deleted invoices | NOT REQUIRED | — | Delete option not available in POS. | N/A. |
| 13(j) | Filter: refund/return | BUILDABLE | P1 | Return type enums from Nukkad. | Filter by return subtypes. |
| 13(k) | Filter: reprinting of invoice | BUILDABLE | P1 | `BillReprint` event flag. | Toggle to show/hide reprint alerts. |
| 14 | WhatsApp/Email/SMS notifications | NOT DONE | P2 | Phase 2. Blocked on WAISL messaging provider. | Alerts sent via configured channels within 60s. |
| 15, 20, 21 | Data retention policies | BUILDABLE | P1 | Hybrid video retention. GHIAL alignment needed on clean period. | Configurable retention periods. Auto-purge with audit log. |
| 17 | Device offline alerts | BUILDABLE | P2 | POS: `OperatorSignOn`/`Off` + absence detection. Camera: Phase 4. | POS offline alert fires when no events during business hours. |
| 18 | Video clips from recordings | BUILDABLE | P2 | Hybrid retention: extract snippets for flagged transactions. XProtect WebRTC for on-demand playback. | Flagged transaction clips stored. Any recording playable on demand. |
| 19 | Alert details (receipt no, POS ID, concessionaire, video link) | BUILDABLE | P0 | `billNumber` from `CommitTransaction`. Video via XProtect WebRTC. | Alert shows: bill number, items, amounts, timestamps, embedded video. |
| 22 | Remarks against alerts + role-based auth | PARTIALLY DONE | P1 | Remarks: DONE. Auth: Phase 3. GHIAL closure statements: NEEDS INPUT. | Remarks saved per alert. Role-based auth in Phase 3. |
| 23 | Create new rules from backend | PARTIALLY DONE | P2 | Thresholds configurable today. Custom rule builder: Phase 2. | Threshold values editable. Custom rule builder Phase 2. |
| 24 | Reports (store/vertical/exception/daily/monthly) | BUILDABLE | P1 | Store-wise daily, exception-wise, monthly buildable. Concessionaire/vertical: NEEDS INPUT (WAISL). | Reports generated on demand. |
| 25 | Store groups (zone-wise, risk-level) | NOT DONE | P3 | Phase 5. Needs WAISL hierarchy API. | N/A for current phases. |
| 26 | Report generation < 1 min | NOT DONE | P2 | Requires DB migration from JSONL. | Any report generates in under 60 seconds. |
| 27 | Camera location map / digital twin | NOT IN SCOPE | — | Excluded from current phases. | N/A. |
| 28 | Joystick support | NOT IN SCOPE | — | XProtect WebRTC supports PTZ commands; joystick mapping possible later if needed. | N/A. |
| 29 | Risk classification (High/Medium/Low) | DONE | P0 | Color-coded badges. Compound signals escalate severity. | Every alert assigned a risk level. |
| 30 | MIP Plugin CSV timezone fix | NOT IN SCOPE | — | Not applicable to our system. | N/A. |
| 31(a) | Cash drawer open > X seconds | PARTIAL | P2 | `DrawerOpenedOutsideATransaction` detects open. Duration tracking needs drawer-close event (not yet available from Nukkad). | Alert on drawer open. Duration deferred. |
| 31(b) | Drawer open without customer | BUILDABLE | P1 | CV customer detection + `DrawerOpenedOutsideATransaction`. | Alert when drawer opens with no customer in zone. |
| 31(c) | Staff taking money to pocket | NOT IN SCOPE | — | Requires pose estimation / VLM. | N/A. |
| 31(d) | Goods given, no bill | DONE | P0 | CV customer session + no EPOS match. | Missing POS alert fires within 30s. |
| 31(e) | Void when customer not present | BUILDABLE | P1 | CV customer detection + void event. | Alert when void occurs with no customer detected. |
| 32 | EPOS downtime report | BUILDABLE | P2 | `OperatorSignOn`/`Off` + event absence inference. | POS uptime/downtime per terminal per day. |
| 33 | Camera view blocked alert | NOT DONE | P3 | Phase 4. Reference frame comparison. | N/A for current phases. |
| 34 | Camera offline alert (email/WhatsApp/SMS) | NOT DONE | P3 | Phase 4 + Phase 2 notifications. | N/A for current phases. |
| 35 | Trend analysis (sales dip + void increase, low-sales cashier, void trend vs average) | BUILDABLE | P3 | Phase 5. Requires data accumulation for baselines. | Historical trend charts by type, store, cashier. |
| 36 | Single screen: alerts + video + investigate + remarks | BUILDABLE | P0 | Dashboard alert workflow with embedded WebRTC video player and event overlay timeline. | Investigate and resolve without navigating away. |
| 37 | Summary report (total/viewed/closed/investigating, day/shift/user-wise) | BUILDABLE | P1 | Shift-wise from `SignOn`/`Off`, cashier-wise breakdowns. CSV/PDF export. | Summary with all breakdowns. Exportable. |
| 38, 39 | Video wall NxN with auto-rotate | PLANNED | P3 | Phase 5. Multiple WebRTC sessions in grid layout. | NxN live camera grid with configurable auto-rotate. |
| 40 | Click receipt line, jump to video timestamp | BUILDABLE | P1 | Each `AddTransactionSaleLine` has `lineTimeStamp`. WebRTC `playbackTime` parameter. | Click any line item → video jumps to that timestamp. |
| 41 | Exported video with transaction data | BUILDABLE | P2 | Client-side WebRTC recording with event overlay, or self-stored snippets. | Exported clip includes POS event data. |
| 42 | Boarding pass / passenger details | NEEDS INPUT | P3 | Depends on Nukkad populating `debitor` field. | Passenger info displayed when available. |

---

## 4. Phase-wise Roadmap

### Phase 0 — Emulator Build (current)

Build the complete system against emulated data sources.

| Deliverable | Detail |
|-------------|--------|
| Nukkad event emulator | Generates realistic push API event sequences (BeginTransactionWithTillLookup through CommitTransaction) |
| CV signal emulator | Simulates customer-enter, customer-exit, bill-detected, no-bill events |
| Video mock | Placeholder video player for dashboard integration |
| Full backend | FastAPI: fraud engine, transaction assembly, alert management, API layer |
| Full dashboard | React: alert list, investigation drawer, filters, employee scorecard |
| Fraud engine | All fraud rules running against emulated data (see BACKEND_DESIGN.md for full rule list) |

### Phase 1A — Nukkad Event Receiver + Transaction Assembler (1-2 weeks)

| Deliverable | Detail |
|-------------|--------|
| Receiver API | HTTP endpoint accepting Nukkad push events |
| Transaction assembler | Accumulates events per `transactionSessionId` until `CommitTransaction` |
| Data models | Per-item (`AddTransactionSaleLine`), per-payment (`AddTransactionPaymentLine`), session metadata |
| Event persistence | Store raw events for replay and debugging |

### Phase 1B — Expanded Fraud Engine (1-2 weeks)

| Deliverable | Detail |
|-------------|--------|
| 16 new rules | Manual entry, manual discount, manual price, drawer opened, bill reprint, per-item voids, return subtypes (`ReturnNotRecentlySold`, `ExchangeSlipWithoutMatchingLine`), self-granted discount, employee purchase, null transaction, post-bill cancellation |
| Cross-validation rules | Void + no customer, return + no customer, drawer open + no customer |
| Risk scoring | Compound signal scoring — multiple signals on same transaction increase severity |

### Phase 1C — Dashboard Enhancements (1 week)

| Deliverable | Detail |
|-------------|--------|
| New filters | Payment mode, violation type, reprint, manual entry, return subtype |
| Rule toggles | Per-rule enable/disable with threshold configuration |
| Enhanced scorecards | Per-employee and per-store scorecards with new metrics (manual entry rate, void rate, discount rate) |
| Transaction detail | Per-item and per-payment breakdown in investigation drawer |

### Phase 2A — Video Integration (1-2 weeks)

| Deliverable | Detail |
|-------------|--------|
| XProtect WebRTC | Live and recorded playback embedded in dashboard |
| Event overlay player | Synchronized timeline of POS + CV events rendered on video |
| Receipt-to-video | Click any receipt line item to jump to that timestamp in video |
| Video snippets | Extract and store video clips for flagged transactions (hybrid retention) |
| **Requires** | WAISL to provide: XProtect API Gateway URL, OAuth credentials, camera device IDs |

### Phase 2B — Notifications, Reporting, Data Retention (2-3 weeks)

| Deliverable | Detail |
|-------------|--------|
| Notifications | WhatsApp/Email/SMS alerts (requires WAISL messaging provider) |
| Reports | Store-wise daily, exception-wise, monthly, cashier-wise, shift-wise |
| Data retention | Auto-purge clean transactions, retain flagged 3 months |
| DB migration | JSONL to proper database for query performance |
| CV Phase 2 | Seller activity classifier on iGPU for improved "transaction without POS" confidence |

### Phase 3 — Auth + Rule Builder (2 weeks)

| Deliverable | Detail |
|-------------|--------|
| Role-based auth | Two levels: operator (view + investigate + resolve) and admin (+ rule config, retention, store setup) |
| Custom rule builder | Define new trigger conditions from dashboard UI without code changes |

### Phase 4 — Device Monitoring (2-3 weeks)

| Deliverable | Detail |
|-------------|--------|
| Camera offline | RTSP timeout detection with alert |
| Camera blocked | Reference frame comparison to detect obstructed views |
| POS offline | Event absence during business hours triggers alert |

### Phase 5 — Scale + Analytics (4-6 weeks)

| Deliverable | Detail |
|-------------|--------|
| Store groups | Zone/risk/vertical grouping (needs WAISL hierarchy API) |
| Trend analysis | Historical baselines, deviation detection, trend charts |
| Video wall | NxN live camera grid with WebRTC |
| Multi-store reporting | Cross-store comparisons, business vertical views |

---

## 5. Dependencies & Blockers

### Pending Clarifications

#### From Nukkad (quick — email)

| # | Question | Impact |
|---|----------|--------|
| 1 | "All APIs need to be stringified" — exact format? Normal JSON body or string-wrapped JSON? | Receiver API implementation |
| 2 | `storeIdentifier` values — same as CIN codes (`NDCIN1223`) or store names (`"Asha"`)? | Store mapping logic |
| 3 | Retry policy if our endpoint is down? Do they queue and replay, or drop? | Reliability design, need for our own Nukkad polling fallback |

#### From WAISL (needed for Phase 2A)

| # | Question | Impact |
|---|----------|--------|
| 1 | XProtect API Gateway URL + OAuth credentials | Blocks all video integration |
| 2 | Camera device IDs mapped to stores/POS terminals | Blocks camera-to-POS mapping |
| 3 | Store hierarchy / business vertical API | Blocks store grouping and vertical-wise reporting (Phase 5) |
| 4 | Messaging provider credentials (WhatsApp/Email/SMS) | Blocks notifications (Phase 2B) |

#### From GHIAL (not blocking current work)

| # | Question | Impact |
|---|----------|--------|
| 1 | Clean transaction retention period: 1 week vs 1 month? | Retention policy configuration |
| 2 | Standard acknowledgement/closure statements for alert resolution | Alert closure workflow dropdown options |
| 3 | Report template preferences | Report layout and content (Phase 2B) |

### Resolved Blockers

The following 10 items were previously blocked and are now unblocked by new Nukkad push API fields and XProtect WebRTC availability:

| # | Previously Blocked Item | Resolution |
|---|------------------------|------------|
| 1 | Manual item entry detection | `scanAttribute: "ManuallyEntered"` now available per sale line |
| 2 | Manual discount detection | `discountType: "ManuallyEnteredValue"` / `"ManuallyEnteredPercentage"` now available |
| 3 | Cash drawer event outside transaction | `transactionType: "DrawerOpenedOutsideATransaction"` now sent as distinct event |
| 4 | Bill reprint detection | `BillReprint` event now sent by Nukkad |
| 5 | Credit note payment identification | `lineAttribute: "CreditNotePayment"` on payment lines |
| 6 | Per-item void amounts | `AddTransactionSaleLine` with `itemAttribute: "CancellationWithinTransaction"` gives per-item detail |
| 7 | POS online/offline status | `OperatorSignOn` / `OperatorSignOff` events provide shift boundaries |
| 8 | Payment mode identification | `AddTransactionPaymentLine` with `lineAttribute: Cash/CreditCard/UPI/etc.` |
| 9 | Video playback by timestamp | XProtect WebRTC API with `playbackTime` parameter |
| 10 | Video recording and retention | XProtect VMS handles continuous recording with configurable retention |
