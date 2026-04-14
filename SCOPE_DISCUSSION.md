# RLCC Scope Discussion — BRD Requirements vs Implementation

**Date:** 2026-04-14
**Context:** Scope alignment meeting with client (GHIAL/WAISL)
**Reference:** "Functional Requirement finalised by Users" document

---

## How to read this document

Each BRD requirement is mapped to one of:

- **DONE** — Demonstrated in POC Demo 1
- **PLANNED** — In our roadmap, no external blockers
- **NEEDS INPUT** — Blocked on WAISL / Nukkad / GHIAL
- **CLARIFY** — KS comment needs correction or the requirement needs re-scoping
**Important distinction:** The CV pipeline detects **3 signals** from video:
1. Customer present (yes/no, with timestamps)
2. Receipt printed (yes/no, with confidence)
3. Session duration (start/end time)

Everything else (discount %, void, refund, item count, payment mode, cashier name) comes from **EPOS data only**. Several requirements in the BRD blur this line. This document calls those out.

---

## Req 1 — POS terminal mapped to camera stream

> KS system should map each POS terminal with the respective camera stream.

| Status | **DONE** |
|--------|----------|
| How | `mapping.json` maps each `StoreId_POSId` to a `SellerWindowId` (camera window). Backend uses this to correlate POS events with VAS (video) sessions. |
| Demo | Working in POC 1 for 3 stores. |

---

## Req 2 — Transaction mapped to CCTV footage + suspicious flagging

> Each transaction mapped with CCTV footage based on start/end time.
> When EPOS not available, flag as suspicious.
> Once EPOS available, auto-tag footage within 3 days.

| Sub-requirement | Status | Notes |
|----------------|--------|-------|
| Transaction matched to video session by timestamp | **DONE** | Backend matches VAS session (start/end) with POS event within time window |
| Flag suspicious when EPOS missing | **DONE** | "Missing POS" alert raised when video detects transaction but no EPOS match within 2 min |
| Video snippet storage & retrieval | **NOT DONE** | No video recording/storage system built yet |
| Auto-tag footage within 3 days | **NOT DONE** | Requires video storage + VMS API |

| Status | **PARTIALLY DONE** |
|--------|----------|
| Blocker | **WAISL** — VMS API availability. If VMS provides an API to request video by camera + timestamp range, we can build retrieval. If not, EPOS data is shown without video snippet (as stated in KS comment for Req 8). |
| Action | WAISL to confirm VMS API availability and share documentation. |

---

## Req 5 — Employee-specific transaction reports

> Reports related to specific employees with related transactions.

| Status | **PARTIALLY DONE** |
|--------|----------|
| What works | Cashier name flows from EPOS through to dashboard. Employee scorecard view exists in UI. |
| Gap | Scorecard not yet wired to real aggregated data (transaction count, flag rate, resolution outcomes per cashier). |
| Plan | Phase 1 — no external dependency. |

---

## Req 6 — Filter transactions by type to verify fraud

> Individual or specific type of transaction filters. Violations by EPOS checks and video analytics under different filters.

| Status | **PARTIALLY DONE** |
|--------|----------|
| What works | Filters: risk level, time range, store, amount range, search by cashier/transaction ID. Triggered rules shown per transaction. |
| Gap | No dedicated per-violation-type filter (e.g. "show all void transactions", "show all high-discount"). No separate "EPOS violations" vs "Video Analytics violations" filter. |
| Plan | Phase 1 — add per-rule-type filters. |

---

## Req 7 — Deterministic triggers for fraud events + notification workflow

> Every transaction captured via video analytics and EPOS. Suspicious transactions identified by business rules. Alert with notification workflow.

| Status | **DONE** |
|--------|----------|
| How | 9 fraud rules applied automatically. Alerts created with risk level. Dashboard has full workflow: new -> reviewing -> resolved/genuine/fraudulent with remarks. Real-time WebSocket push. |

---

## Req 8 — Recording retention (clean 1 week, suspicious 3 months)

> Clean transaction: 1 week. Non-legitimate: 3 months.

| Status | **NOT DONE** |
|--------|----------|
| Gap | No data retention/cleanup policy. JSONL files grow indefinitely. No video storage at all. |
| Plan | Phase 2 — auto-purge clean transactions after 1 week, retain flagged for 3 months. Video retention depends on VMS API (Req 2 blocker). |
| Note | Users want clean transactions stored for 1 month (vs KS proposed 1 week). **Needs alignment.** |

---

## Req 9 — High availability / failover

> HA with hot-standby, failover server, automatic sync.

| Status | **NOT DONE** |
|--------|----------|
| Current | Single process on single GPU machine. No redundancy. |
| Plan | Phase 5 — production infrastructure. Not POC scope. |
| KS Position | KS will ensure platform is HA and video transactions are resilient across node failure. |

---

## Req 10 — 24x7 live monitoring with 30-day snippet retention

> 24x7 monitoring of live camera feeds with transaction snippets for 30 days. Store camera view only.

| Status | **PARTIALLY DONE** |
|--------|----------|
| What works | CV pipeline runs 24x7 on RTSP stream, detects all transactions in real-time. |
| Gap | No video snippet recording or 30-day storage. |
| Blocker | Same as Req 2 — VMS API. |

---

## Req 11 — Events of Interest (Fraud Detection Rules)

This is the largest section. Here is each sub-item mapped honestly:

### 11a — Object tracking & monitoring (printer + Pax)

| Status | **PARTIALLY DONE** |
|--------|----------|
| Printer monitoring | CV detects activity in bill zone (motion + hand + background change). This effectively monitors the printer. |
| Pax/EDC monitoring | **Not built.** Would need a new zone configured around the Pax machine + detection logic for hand near Pax. |
| Object tracking (individual items) | **Not possible** — KS correctly noted "can't track individual items." |
| CLARIFY | KS comment says "POS printer and Pax can be monitored." Printer yes (already working). Pax is feasible but not yet implemented — needs dedicated zone setup per store. |

### 11b — Void / Blank line items

| Status | **DONE** |
|--------|----------|
| Source | EPOS data — `VoidReason` and `BillStatus` fields. |

### 11c — Negative amount entered

| Status | **DONE** |
|--------|----------|
| Source | EPOS data — `TransactionTotal < 0`. |

### 11d — Null transactions

| Status | **NOT DONE** |
|--------|----------|
| Gap | No explicit "null transaction" rule. |
| Plan | Phase 1 — add rule for `TransactionTotal == 0` or empty line items. |
| Note | Need to clarify with Nukkad what a null transaction looks like in their data. |

### 11e — Manual punching of item

| Status | **ON HOLD** |
|--------|----------|
| KS Position | Depends on Nukkad providing `isManualEntry` flag per line item. |
| Action | **WAISL** to follow up with Nukkad. |

### 11f — Manual punching of discount

| Status | **PARTIALLY DONE** |
|--------|----------|
| What works | Discount percentage threshold rule (configurable). |
| Gap | No distinction between manual vs system-applied discount. |
| Blocker | Nukkad needs to provide `isManualDiscount` field. |
| CLARIFY | KS comment says "subject to EPOS data." Current implementation flags ALL discounts above threshold, which is a reasonable approximation until Nukkad provides the manual flag. |

### 11g — Cancelled items

| Status | **DONE** |
|--------|----------|
| Source | EPOS data — `CancelDate` field. |

### 11h — Cash drawer open without transaction

| Status | **NOT POSSIBLE without EPOS event** |
|--------|----------|
| CLARIFY | KS comment correctly states this needs EPOS data. CV cannot see the cash drawer from overhead camera. Cash drawer opened via physical key cannot be detected at all. Only works if EPOS sends a `cashDrawerOpen` event. |
| Blocker | **Nukkad** — does their system emit cash drawer events? |

### 11i — POS as ATM (own cards, take cash from customers)

| Status | **CLARIFY** |
|--------|----------|
| KS Comment | "When EPOS and video analytics transaction types differ, identified as unmatched. Accuracy may not be achieved." |
| Reality | **The CV pipeline cannot detect payment mode.** `ModeOfTransaction` is hardcoded `"N/A"` in the CV output. The code explicitly states: "Hudson can't determine payment method." The payment mode mismatch rule exists in the backend but only fires if both sides report a known mode — which never happens currently. |
| What's realistic | This rule works **only with EPOS data** (comparing reported payment mode against expected patterns). Video adds customer presence confirmation, not payment method identification. |
| Recommendation | Re-scope as EPOS-only rule. Flag unusual payment patterns (e.g., same cashier, high frequency of card transactions with cash change). Don't promise video-based payment mode detection. |

### 11l — Return / Refund

| Status | **DONE** |
|--------|----------|
| Source | EPOS data — `RefundAmount` with configurable threshold. |

### 11m — EPOS machine not utilized (goods exchanged, no bill)

| Status | **DONE** |
|--------|----------|
| How | CV detects customer session (goods exchanged). If no matching EPOS event within time window, "Missing POS" alert is raised. |
| Strength | This is the core value of the CV+EPOS combination. |

### 11n — No printout from printer

| Status | **DONE** |
|--------|----------|
| How | CV monitors bill zone. `ReceiptGenerationStatus == false` when no motion/hand/background change detected at printer during session. |

### 11o — Render cash but no bill

| Status | **DONE** |
|--------|----------|
| How | Same as 11m+11n combined — customer present (implies exchange), no receipt detected by CV. |

### 11p — Reprinting / duplicate bill

| Status | **NOT DONE** |
|--------|----------|
| Blocker | Nukkad needs to provide `isReprint` flag or reprint count on bill data. |
| Plan | Simple rule once data is available. |

### 11q — EDC/UPI/Cash transaction without POS

| Status | **PARTIALLY DONE** |
|--------|----------|
| What works | CV detects customer session. If no EPOS match, flagged as "Missing POS". |
| Gap | Cannot distinguish payment instrument (EDC vs UPI vs cash) from video. |
| KS Comment | "Printer and UPI scanner should be at standard place." |
| Reality | Detecting UPI scanner usage from overhead camera is unreliable. Detecting hand near EDC zone is feasible with a new zone but untested. |
| Recommendation | Keep current approach: "transaction detected, no EPOS match" is the alert. Don't promise instrument-level detection from video. |

### 11r — Enable/disable alert categories

| Status | **PARTIALLY DONE** |
|--------|----------|
| What works | Threshold sliders in settings (discount %, refund amount, high value, bulk qty, idle minutes). |
| Gap | No per-rule on/off toggle. Currently you can set thresholds very high to effectively disable, but no clean toggle. |
| Plan | Phase 1 — add enable/disable checkbox per rule. |

---

## Req 12 — Exception alerts with video snippets

**General note:** All sub-items say "with video snippets." Video snippet capability is blocked on VMS API (Req 2). Until then, alerts are shown with EPOS data only.

### 12(i) Discounts

| Sub-item | Status | Notes |
|----------|--------|-------|
| (a) Unauthorized discounts with video | **PARTIAL** | Discount threshold rule works. "Unauthorized" vs "authorized" needs Nukkad flag. Video snippet blocked on VMS. |
| (b) Manual discounts | **ON HOLD** | Needs Nukkad — `isManualDiscount` field. |
| (c) Discounts > X% (configurable) | **DONE** | `discount_threshold_percent` in settings, adjustable via dashboard slider. |

### 12(ii) Returns

| Sub-item | Status | Notes |
|----------|--------|-------|
| (a) Return > X amount (configurable) | **DONE** | `refund_amount_threshold` in settings. |
| (b) Return without customer | **FEASIBLE** | EPOS shows refund + CV shows no customer in session = this rule. Not yet built as explicit rule. Phase 1. |
| (c) Refund without receipt | **FEASIBLE** | EPOS refund + CV `ReceiptGenerationStatus == false`. Phase 1. |
| (d) Full return (whole transaction) | **NOT DONE** | Need to detect `RefundAmount == TransactionTotal`. Simple rule. Phase 1. |
| (e) Credit note / payment mode filter | **NOT DONE** | Needs Nukkad — credit note as payment mode field. |

### 12(iii) No-Sales

| Sub-item | Status | Notes |
|----------|--------|-------|
| (a) No transaction for X minutes (configurable) | **DONE** | Idle POS monitor runs every minute. `idle_pos_minutes` configurable. |
| Multi-zone selection | **NOT DONE** | Currently per-POS. Phase 5 — store grouping. |

### 12(iv) Voids

| Sub-item | Status | Notes |
|----------|--------|-------|
| (a) Void without customer knowledge (EPOS) | **DONE** | Void rule fires on `VoidReason` present. |
| (b) Void lines > X% of transaction | **NOT DONE** | Current rule is binary. Needs line-item level data from Nukkad to calculate percentage. |
| (c) Quantity of no-sales | **DONE** | Same as void rule — suspended/hold transactions flagged. |

### 12(v) Manually Entered Values

| Sub-item | Status | Notes |
|----------|--------|-------|
| (a) Manual credit card entry | **NOT DONE** | EPOS data needed. CV can't detect this. |
| (b) Manual price change | **ON HOLD** | Needs Nukkad. |
| (c) Manual discount change | **ON HOLD** | Needs Nukkad. |
| (d) Manual cash drawer opening | **NOT POSSIBLE** | Without EPOS cash drawer event. KS comment acknowledges this. |

### 12(vi) — Amount exceeding X value

| Status | **DONE** |
|--------|----------|
| How | `high_value_threshold` configurable in settings. |

### 12(vii) — Bulk purchase

| Status | **DONE** |
|--------|----------|
| How | `bulk_quantity_threshold` configurable in settings. |

---

## Req 13 — Alert Filters

| Filter | Status | Notes |
|--------|--------|-------|
| (a) Bill amount from/to range | **DONE** | Min/max amount filter in dashboard. |
| (b) Item void amount from/to range | **NOT DONE** | Phase 1. Need void amount from Nukkad line-item data. |
| (c) Date/time from/to range | **DONE** | Time range filter (Today/2 Days/Week). Custom range not yet built. |
| (d) Business vertical | **NOT DONE** | **WAISL** to provide store hierarchy API. Phase 5. |
| (e) Payment mode (cash/card/UPI) | **NOT DONE** | Data exists in EPOS. Dashboard filter not built. Phase 1. |
| (f) Store (single & multiple) | **PARTIAL** | Single store filter works. Multi-select not built. Phase 1. |
| (g) No physical bill issue cases | **NOT DONE** | Data exists (`ReceiptGenerationStatus`). Dedicated filter not built. Phase 1. |
| (h) EPOS machine not touched + manual bill checkbox | **NOT DONE** | Needs design discussion. Phase 2. |
| (i) Deleted invoices | **NOT REQUIRED** | KS: "delete option not available in POS." |
| (j) Refund/Return | **NOT DONE** | Data exists. Dedicated filter not built. Phase 1. |
| (k) Reprinting of invoice | **NOT DONE** | Needs Nukkad `isReprint` field. |

---

## Req 14 — WhatsApp / Email / SMS notifications

| Status | **NOT DONE** |
|--------|----------|
| Plan | Phase 2. |
| Blocker | **WAISL** to provide/approve messaging provider accounts (WhatsApp Business API, SMS gateway). |

---

## Req 15, 20, 21 — Data retention

| Status | **NOT DONE** |
|--------|----------|
| Plan | Phase 2. Clean: 1 week (users want 1 month — **needs alignment**). Suspicious: 3 months. |

---

## Req 17 — Device offline alerts

| Status | **NOT DONE** |
|--------|----------|
| Camera offline | Feasible — detect RTSP frame timeout. Phase 4. |
| POS offline | Needs Nukkad to provide online/offline status. KS comment: "If data provided by Nukkad regarding POS online/offline, KS will trigger that too." |

---

## Req 19 — Alert details (receipt no, POS ID, concessionaire, video link)

| Field | Status |
|-------|--------|
| Receipt no (billNo) | **PARTIAL** — data flows from Nukkad API, not yet exposed in alert UI. Phase 1. |
| Time, date | **DONE** |
| Alert no | **DONE** (alert ID) |
| POS ID | **DONE** |
| Concessionaire name | **DONE** (store name from `stores.json`) |
| Video link | **NOT DONE** — blocked on VMS API (Req 2). |
| Status resolved/unresolved | **DONE** |

---

## Req 22 — Reasons against alerts to resolve + role-based auth

| Sub-item | Status | Notes |
|----------|--------|-------|
| Remarks/reasons on alerts | **DONE** | Text remarks field on resolution. |
| Role-based authorization | **NOT DONE** | No auth/user system. All users have same access. Phase 2-3. |
| GHIAL acknowledgement statements | **NEEDS INPUT** | **GHIAL** to provide standard closure statements. |

---

## Req 23 — Create new rules from backend

| Status | **PARTIALLY DONE** |
|--------|----------|
| What works | Existing rule thresholds are configurable via dashboard + API. |
| Gap | Cannot create entirely new rule types from the UI. Needs code changes. |
| KS Position | "Need more discussion." |

---

## Req 24 — Reports

| Report Type | Status | Notes |
|-------------|--------|-------|
| Store-wise daily | **NOT DONE** | Phase 2. |
| Exception-wise | **NOT DONE** | Phase 2. |
| Concessionaire/RO/Business vertical | **NOT DONE** | Needs WAISL store hierarchy API. Phase 5. |
| Monthly summaries | **NOT DONE** | Phase 2. |
| Video link per alert in report | **NOT DONE** | Blocked on VMS API. |
| Blocker | **WAISL** to provide report template that aligns with KS format. |

---

## Req 25 — Store groups (zone-wise, risk-level)

| Status | **NOT DONE** |
|--------|----------|
| Plan | Phase 5. Needs WAISL store hierarchy. |

---

## Req 26 — Report generation time < 1 minute

| Status | **NOT DONE** — no report generation system yet. |
|--------|----------|
| Plan | Phase 2. For the data volumes in POC (few stores), sub-minute is trivial. At scale, needs DB instead of JSONL files. |

---

## Req 27 — Camera location map / Digital Twin

| Status | **NOT DONE** |
|--------|----------|
| Plan | Phase 5. Needs store floor plans from WAISL/GHIAL. |

---

## Req 29 — High / Medium / Low risk classification

| Status | **DONE** |
|--------|----------|
| How | Fraud engine assigns risk based on rule severity. Dashboard shows color-coded badges. |

---

## Req 31 — Video analytics for revenue leakage

| Sub-item | Status | Source | Notes |
|----------|--------|--------|-------|
| (b) Staff opened drawer without customer | **NOT DONE** | EPOS only | Needs EPOS cash drawer event. KS correctly scoped this. |
| (d) Goods given, no bill from EPOS | **DONE** | CV + EPOS | CV detects session, no EPOS match = alert. |
| (e) Void/cancel when customer not there | **FEASIBLE** | CV + EPOS | EPOS void + CV no customer. Not yet built as explicit rule. Phase 1. |

---

## Req 32 — EPOS downtime report

| Status | **NOT DONE** |
|--------|----------|
| Note | Needs Nukkad to provide POS uptime/downtime data, or KS infers from "last seen" timestamps. |

---

## Req 33 — Camera view blocked alert

| Status | **NOT DONE** |
|--------|----------|
| Feasibility | Feasible with reference frame comparison in fixed ROI. |
| Dependency | **WAISL** must ensure an immovable, unobstructed indicator exists in camera ROI. |
| Plan | Phase 4. |

---

## Req 34 — Camera offline alert (email/WhatsApp/SMS)

| Status | **NOT DONE** |
|--------|----------|
| Plan | Phase 4 (camera detection) + Phase 2 (notification channels). |
| Dependency | **WAISL** to check WhatsApp/SMS feasibility. |

---

## Req 35 — Trend analysis

| Sub-item | Status | Notes |
|----------|--------|-------|
| (a) Sales dip + void increase | **NOT DONE** | Phase 5. Needs historical baseline. |
| (b) Low-sales cashier with high change due | **NOT DONE** | Phase 5. Data available once aggregation is built. |
| (c) Void alert trend vs historical average | **NOT DONE** | Phase 5. Needs 1+ month of data. |
| Dependency | Historical EPOS data from **Nukkad** for baseline. |

---

## Req 36 — Single screen: all alerts + video + close/investigate + remarks

| Status | **MOSTLY DONE** |
|--------|----------|
| What works | Single dashboard screen with alerts, resolution workflow (close/investigate/fraudulent/genuine), remarks field. |
| Gap | Video playback — button exists but disabled. Blocked on VMS API. |

---

## Req 37 — Summary report

| Status | **PARTIALLY DONE** |
|--------|----------|
| What works | Total/open/investigating/closed counts on alert dashboard. Basic analytics charts. |
| Gap | No day-wise, shift-wise, user-wise breakdown. No exportable summary. |
| Plan | Phase 2. |

---

## Req 38 & 39 — Video wall NxN matrix with auto-rotate

| Status | **NOT DONE** |
|--------|----------|
| Plan | Phase 5. Separate discussion during video wall design. |

---

## Req 40 — Click receipt line, jump to video timestamp

| Status | **NOT DONE** |
|--------|----------|
| Dependency | VMS API with timestamp-based seek. |

---

## Req 41 — Exported video with transaction data

| Status | **NOT DONE** |
|--------|----------|
| Dependency | VMS API. |

---

## Req 42 — Boarding pass / passenger details in reports

| Status | **NOT DONE** |
|--------|----------|
| Dependency | **Nukkad** to include customer fields in bill API. |

---

# Summary: What's Blocking Progress

## Top blockers by owner

### WAISL (most critical)

| # | Action | Blocks |
|---|--------|--------|
| 1 | **Confirm VMS API availability + share docs** | Video snippets (Req 2, 10, 12, 36, 40, 41) — this is the single biggest gap |
| 2 | Provide store hierarchy / business vertical API | Filters (Req 13d), reports (Req 24), store groups (Req 25) |
| 3 | Provide report template aligned with KS format | All reporting (Req 24) |
| 4 | Set up WhatsApp / SMS provider | Notifications (Req 14, 34) |
| 5 | Ensure fixed indicator in camera ROI per store | Camera tamper detection (Req 33) |
| 6 | Coordinate with Nukkad on API field extensions | See Nukkad section below |

### Nukkad (POS provider)

| # | Field / Event Needed | Blocks |
|---|---------------------|--------|
| 1 | `isManualEntry` per line item | Req 11e — manual item punch |
| 2 | `isManualDiscount` flag | Req 11f, 12(i)b — manual discount |
| 3 | `isReprint` flag | Req 11p, 13k — reprint detection |
| 4 | `cashDrawerOpen` event | Req 11h, 31b — drawer without transaction |
| 5 | `creditNoteId` in payment modes | Req 12(ii)e — credit note filter |
| 6 | Line-item void amounts | Req 12(iv)b, 13b — void % and amount filter |
| 7 | POS online/offline status | Req 17, 32 — device offline alert |
| 8 | Customer fields (boarding pass, mobile) | Req 42 |

### GHIAL

| # | Action | Blocks |
|---|--------|--------|
| 1 | Standard acknowledgement statements for alert closure | Req 22 |
| 2 | Align on clean transaction retention: 1 week vs 1 month | Req 8, 15, 20 |
| 3 | Store floor plans (for digital twin) | Req 27 |

### KS (our team) — no blockers, can start immediately

| Phase | Items | Timeline |
|-------|-------|----------|
| Phase 1 | Dashboard filters, missing rules, employee scorecard, enable/disable rules | 1-2 weeks |
| Phase 2 | Notifications, reporting, data retention | 2-3 weeks |
| Phase 4 | Camera offline/blocked detection | 2-3 weeks |

---

# Scope Clarifications to Raise in Meeting

1. **Payment mode detection from video is not possible.** Req 11i implies CV can detect cash vs card vs UPI — it cannot. Payment mode comes from EPOS only. The "mismatch" rule only works as an EPOS-side pattern check.

2. **"With video snippets" appears in ~15 requirements.** All of these are blocked on VMS API. Without it, KS shows EPOS data and alert details without video. This is the single biggest scope item to resolve.

3. **Clean transaction retention: 1 week vs 1 month.** KS proposed 1 week, users want 1 month. Needs agreement — affects storage planning.

4. **Manual entry detection** (items, discounts, prices) is entirely dependent on Nukkad adding flags to their API. Without those flags, we can only threshold-based detect (e.g., "discount > 20%") without knowing if it was manual.
