# Integration Guide

External contracts. This doc is for Nukkad, WAISL, and ops — not our devs. For internal design, see BACKEND_DESIGN.md. Sections 3-4 are internal protocol specs included for ops reference.

## 1. Nukkad Push API Integration

Nukkad pushes POS events to our receiver in real-time. **There is no pull API on either side** — we don't poll Nukkad's sales endpoints, and Nukkad doesn't poll us. Every transaction state change crosses the wire as a push event the moment it happens at the POS.

We expose **9 endpoints, one per RLCC event type**, each accepting one specific event from the RLCC API spec. The payload's `event` field must match the route or the request is rejected with HTTP 400.

### Endpoint surface

```
Base: POST https://{our-server}:8001
Headers (every request):
  Content-Type: application/json
  x-authorization-key: {agreed-upon-key}
Body: application/json (the receiver also accepts a stringified JSON body)
```

| # | Event (`event` field) | Path |
|---|---|---|
| 4.1 | `BeginTransactionWithTillLookup`       | `POST /v1/rlcc/begin-transaction-with-till-lookup` |
| 4.2 | `AddTransactionEvent`                  | `POST /v1/rlcc/add-transaction-event` |
| 4.3 | `AddTransactionPaymentLine`            | `POST /v1/rlcc/add-transaction-payment-line` |
| 4.4 | `AddTransactionSaleLine`               | `POST /v1/rlcc/add-transaction-sale-line` |
| 4.5 | `AddTransactionSaleLineWithTillLookup` | `POST /v1/rlcc/add-transaction-sale-line-with-till-lookup` |
| 4.6 | `AddTransactionTotalLine`              | `POST /v1/rlcc/add-transaction-total-line` |
| 4.7 | `CommitTransaction`                    | `POST /v1/rlcc/commit-transaction` |
| 4.8 | `GetTill`                              | `POST /v1/rlcc/get-till` |
| 4.9 | `BillReprint`                          | `POST /v1/rlcc/bill-reprint` |

Section numbers above match `RLCC API Documentation.pdf` (v0.1, 25-Mar-25). Field-level schemas below mirror that document — when in doubt, the PDF wins.

### Event flow per transaction

```
1. BeginTransactionWithTillLookup       → opens session
2. AddTransactionSaleLine ×N            → one per item scanned
   (or AddTransactionSaleLineWithTillLookup if till resolution is needed)
3. AddTransactionPaymentLine ×N         → one per payment method
4. AddTransactionTotalLine ×N           → subtotal, VAT, discounts, total
5. CommitTransaction                    → finalizes, triggers fraud rules
```

Standalone events (not part of the normal flow):
- `AddTransactionEvent` — lifecycle: suspended, resumed, cancelled
- `BillReprint` — standalone, triggers alert immediately
- `GetTill` — till lookup, resolved against `camera_mapping.json`; returns till number on hit, `ErrorCode 11` on miss

All transactional events are correlated to the same session via `transactionSessionId` (returned by `BeginTransactionWithTillLookup`).

### Response envelopes

Two endpoints return the spec-shaped envelope in `data`:

- **`BeginTransactionWithTillLookup`** → `{status, message, data: {ErrorCode, Succeeded, TransactionSessionId}}`. The `TransactionSessionId` echoes the value Nukkad sent so it can thread subsequent calls.
- **`GetTill`** → on hit, `{status:200, message:"Success", data:{ErrorCode:"-1", Succeeded:"true", Till:"<till>"}}`; on miss, HTTP 400 with `{message:"Failure", data:{ErrorCode:"11", ErrorDescription:"No Till found with the specified Branch and Description.", Succeeded:"false"}}`.

The other seven endpoints return the lighter `{status:200, message:"Success", event:"<EventName>"}` envelope. Nukkad's POS-side client should treat any 2xx as accepted; the body shape is informational.

### 4.1  BeginTransactionWithTillLookup

`POST /v1/rlcc/begin-transaction-with-till-lookup`

| Key | Type | Required | Description |
|---|---|---|---|
| `event` | string | Yes | Always `"BeginTransactionWithTillLookup"`. |
| `applicationType` | string | Yes | Application type, e.g. `"Retail"`. |
| `storeIdentifier` | string | Yes | Unique store identifier. |
| `posTerminalNo` | string | Yes | POS terminal processing the transaction. |
| `isForTillLookup` | boolean | Yes | Indicates the transaction uses till-description lookup. Always `true`. |
| `isPreviousTransaction` | boolean | Yes | Whether this transaction links to a previous one. |
| `transactionSessionId` | string | No | Session id for tracking the transaction. |
| `branch` | string | Yes | Branch / store name where the transaction occurs. |
| `tillDescription` | string | Yes | Text description of the till. |
| `transactionNumber` | string | Yes | Unique transaction number. |
| `branchLinkedTo` | string | If `isPreviousTransaction=true` | Branch of the previous (linked) transaction. |
| `tillDescriptionLinkedTo` | string | If `isPreviousTransaction=true` | Till description of the previous transaction. |
| `LinkedTo` | string | If `isPreviousTransaction=true` | Previous transaction's till description (link). |
| `transactionNumberLinkedTo` | string | If `isPreviousTransaction=true` | Previous transaction number. |
| `transactionTimeStamp` | string (ISO 8601) | No | UTC timestamp `YYYY-MM-DDTHH:MM:SSZ`. |
| `debitor` | string | No | Customer identifier. |
| `cashier` | string | No | Cashier identifier. |
| `currencyCode` | string (ISO 4217) | Yes | 3-letter currency code, e.g. `"INR"`. |
| `transactionType` | string (enum) | Yes | See enum §6.4. |
| `employeePurchase` | boolean | Yes | Whether the transaction is an employee purchase. |
| `outsideOpeningHours` | string (enum) | No | `InsideOpeningHours` / `OutsideOpeningHours` / `PartlyOutsideOpeningHours`. |
| `maximumScanGap` | integer | No | Max time (seconds) between scanned items. |

Success response: `{"status": 200, "message": "Success", "data": {"ErrorCode": "-1", "Succeeded": "true", "TransactionSessionId": "<id>"}}`

### 4.2  AddTransactionEvent

`POST /v1/rlcc/add-transaction-event`

| Key | Type | Required | Description |
|---|---|---|---|
| `event` | string | Yes | Always `"AddTransactionEvent"`. |
| `applicationType` | string | Yes | e.g. `"Retail"`. |
| `storeIdentifier` | string | Yes | Store id. |
| `posTerminalNo` | string | Yes | POS terminal id. |
| `transactionSessionId` | string | Yes | Session id from Begin. |
| `lineTimeStamp` | string (ISO 8601) | No | UTC timestamp of the line. |
| `lineNumber` | integer | No | Sequential line number. |
| `lineAttribute` | string (enum) | Yes | Lifecycle: see enum §6.2 (`None`, `TransactionSuspended`, `TransactionResumed`, `TransactionCancelled`). |
| `eventDescription` | string | Yes | Human description of the event. |
| `printable` | boolean | Yes | Whether the event prints on the receipt. |

### 4.3  AddTransactionPaymentLine

`POST /v1/rlcc/add-transaction-payment-line`

| Key | Type | Required | Description |
|---|---|---|---|
| `event` | string | Yes | Always `"AddTransactionPaymentLine"`. |
| `applicationType` | string | Yes | e.g. `"Retail"`. |
| `storeIdentifier` | string | Yes | Store id. |
| `posTerminalNo` | string | Yes | POS terminal id. |
| `transactionSessionId` | string | Yes | Session id. |
| `lineTimeStamp` | string (ISO 8601) | No | UTC timestamp. |
| `lineNumber` | integer | No | Sequential payment-line number. |
| `lineAttribute` | string (enum) | Yes | Payment method enum §6.3 (`Cash`, `CreditCard`, `UPI`, `GiftCard`, …). |
| `paymentDescription` | string | Yes | Free-text description, e.g. `"Credit Card"`. |
| `currencyCode` | string (ISO 4217) | No | 3-letter currency, e.g. `"USD"`. |
| `currencyAmount` | decimal | No | Original payment amount in the foreign currency. |
| `exchangeRate` | decimal | No | Exchange rate if foreign currency. |
| `amount` | decimal | Yes | Final amount in the base currency. |
| `paymentTypeID` | string | No | Payment-type identifier (e.g. `"CreditCard"`, `"GiftCard"`). |
| `cardType` | string | No | Card brand if a card payment, e.g. `"Visa"`. |
| `printable` | boolean | Yes | Whether to print on the receipt. |

### 4.4  AddTransactionSaleLine

`POST /v1/rlcc/add-transaction-sale-line`

| Key | Type | Required | Description |
|---|---|---|---|
| `event` | string | Yes | Always `"AddTransactionSaleLine"`. |
| `applicationType` | string | Yes | e.g. `"Retail"`. |
| `storeIdentifier` | string | Yes | Store id. |
| `posTerminalNo` | string | Yes | POS terminal id. |
| `isForTillLookup` | boolean | Yes | Whether the request uses till lookup. |
| `isPreviousTransaction` | boolean | Yes | Whether linked to a previous transaction. |
| `transactionSessionId` | string | Yes | Session id. |
| `lineTimeStamp` | string (ISO 8601) | No | UTC timestamp. |
| `lineNumber` | integer | No | Sequential line number. |
| `itemAttribute` | string (enum) | Yes | Item attribute, see enum §6.5. |
| `scanAttribute` | string (enum) | Yes | Scan method, see enum §6.6 (`None`, `Auto`, `ModifiedUnitPrice`, `ManuallyEntered`). |
| `itemID` | string | No | Unique item id. |
| `itemDescription` | string | Yes | Item name. |
| `itemQuantity` | integer | Yes | Quantity. *Receiver accepts decimals (e.g. `1.250` kg) — the spec's `integer` is too narrow for weighed items.* |
| `itemUnitMeasure` | string | No | Unit of measure (e.g. `kg`, `pcs`). |
| `itemUnitPrice` | decimal | Yes | Unit price. |
| `discountType` | string (enum) | Yes | Discount type, see enum §6.7. |
| `discount` | decimal | No | Discount amount applied to the line. |
| `totalAmount` | decimal | Yes | Final line price after discount. |
| `branchLinkedTo` | string | Yes (per spec) | Linked branch — relevant when `isPreviousTransaction=true`. |
| `tillLinkedTo` | string | Yes (per spec) | Linked till. |
| `tillDescriptionLinkedTo` | string | No | Description of the linked till. |
| `transactionNumberLinkedTo` | string | Yes (per spec) | Linked transaction number. |
| `transactionTimestampLinkedTo` | string (ISO 8601) | Yes (per spec) | Linked transaction timestamp. |
| `grantedBy` | string | No | User who granted the discount. |
| `printable` | boolean | Yes | Whether to print on the receipt. |

> Note: The PDF marks the `*LinkedTo` fields as required, but they are only meaningful when `isPreviousTransaction=true`. Send empty strings on a fresh sale line if Nukkad's client requires the keys to be present.

### 4.5  AddTransactionSaleLineWithTillLookup

`POST /v1/rlcc/add-transaction-sale-line-with-till-lookup`

Same shape as 4.4 but the `*LinkedTo` fields are explicitly marked optional, and `lineTimeStamp` / `lineNumber` are required.

| Key | Type | Required | Description |
|---|---|---|---|
| `event` | string | Yes | Always `"AddTransactionSaleLineWithTillLookup"`. |
| `applicationType` | string | Yes | e.g. `"Retail"`. |
| `storeIdentifier` | string | Yes | Store id. |
| `posTerminalNo` | string | Yes | POS terminal id. |
| `isForTillLookup` | boolean | Yes | Whether till lookup is enabled. |
| `isPreviousTransaction` | boolean | Yes | Whether linked to a previous transaction. |
| `transactionSessionId` | string | Yes | Session id. |
| `lineTimeStamp` | string (ISO 8601) | Yes | Timestamp of the line. |
| `lineNumber` | integer | Yes | Unique line number in the transaction. |
| `itemAttribute` | string (enum) | Yes | See enum §6.5. |
| `scanAttribute` | string (enum) | Yes | See enum §6.6. |
| `itemID` | string | No | Unique item id. |
| `itemDescription` | string | Yes | Item name. |
| `itemQuantity` | integer | Yes | Quantity. *Receiver accepts decimals (e.g. `1.250` kg).* |
| `itemUnitMeasure` | string | No | Unit of measure. |
| `itemUnitPrice` | decimal | Yes | Unit price. |
| `discountType` | string (enum) | Yes | See enum §6.7. |
| `discount` | decimal | No | Discount amount. |
| `totalAmount` | decimal | Yes | Total after discount. |
| `branchLinkedTo` | string | No | Linked branch. |
| `tillLinkedTo` | string | No | Linked till. |
| `tillDescriptionLinkedTo` | string | No | Linked till description. |
| `transactionNumberLinkedTo` | string | No | Linked transaction number. |
| `transactionTimestampLinkedTo` | string (ISO 8601) | No | Linked transaction timestamp. |
| `grantedBy` | string | No | Discount granter. |
| `printable` | boolean | Yes | Whether to print on the receipt. |

### 4.6  AddTransactionTotalLine

`POST /v1/rlcc/add-transaction-total-line`

| Key | Type | Required | Description |
|---|---|---|---|
| `event` | string | Yes | Always `"AddTransactionTotalLine"`. |
| `applicationType` | string | Yes | e.g. `"Retail"`. |
| `storeIdentifier` | string | Yes | Store id. |
| `posTerminalNo` | string | Yes | POS terminal id. |
| `transactionSessionId` | string | Yes | Session id. |
| `lineTimeStamp` | string (ISO 8601) | No | UTC timestamp of the line. |
| `lineNumber` | integer | No | Sequence number. |
| `lineAttribute` | string (enum) | Yes | Total-line attribute, see enum §6.8 (must be a valid `TotalLineAttribute`). |
| `totalDescription` | string | Yes | Description, e.g. `"Total Amount Payable"` or `"VAT Calculation"`. |
| `amount` | decimal | Yes | Total amount for this line. |
| `printable` | boolean | Yes | Whether to print on the receipt. |

### 4.7  CommitTransaction

`POST /v1/rlcc/commit-transaction`

| Key | Type | Required | Description |
|---|---|---|---|
| `event` | string | Yes | Always `"CommitTransaction"`. |
| `applicationType` | string | Yes | e.g. `"Retail"`. |
| `storeIdentifier` | string | Yes | Store id. |
| `posTerminalNo` | string | Yes | POS terminal id. |
| `transactionSessionId` | string | Yes | Session id from Begin. |
| `transactionNumber` | string | No | Optional bill number for the committed transaction. |

Success collapses the session into a finalized transaction in our store and triggers fraud-rule evaluation.

### 4.8  GetTill

`POST /v1/rlcc/get-till`

A till-lookup RPC. The receiver matches `storeIdentifier` + `tillDescription` against `camera_mapping.json` (comparing `tillDescription` to each camera's `display_pos_label` or `pos_terminal_no`) and returns the matching POS terminal as the till number. `branch` is informational on our side — the strong key is `storeIdentifier`.

| Key | Type | Required | Description |
|---|---|---|---|
| `event` | string | Yes | Always `"GetTill"`. |
| `applicationType` | string | Yes | e.g. `"Retail"`. |
| `storeIdentifier` | string | Yes | Store id. |
| `posTerminalNo` | string | Yes | POS terminal id. |
| `branch` | string | Yes | Branch / store name. |
| `tillDescription` | string | Yes | Till description (e.g. `"POS1"`). |

Success response (till resolved):
`{"status": 200, "message": "Success", "data": {"ErrorCode": "-1", "Succeeded": "true", "Till": "<till-number>"}}`

Failure response (no matching camera entry) — HTTP 400:
`{"status": 400, "message": "Failure", "data": {"ErrorCode": "11", "ErrorDescription": "No Till found with the specified Branch and Description.", "Succeeded": "false"}}`

If `GetTill` returns 400 in the field, check that the store's `camera_mapping.json` entry has a camera whose `display_pos_label` or `pos_terminal_no` matches the `tillDescription` Nukkad is sending.

### 4.9  BillReprint

`POST /v1/rlcc/bill-reprint`

Standalone notification — fires a fraud alert immediately.

| Key | Type | Required | Description |
|---|---|---|---|
| `event` | string | Yes | Always `"BillReprint"`. |
| `applicationType` | string | Yes | e.g. `"Retail"`. |
| `storeIdentifier` | string | Yes | Store id. |
| `posTerminalNo` | string | Yes | POS terminal id. |
| `branch` | string | Yes | Branch / store name. |
| `tillDescription` | string | Yes | Till description used. |
| `transactionTimestamp` | long | Yes | Milliseconds since epoch. *Receiver also accepts ISO 8601 strings and the alternate casing `transactionTimeStamp` for safety.* |
| `billNumber` | string | Yes | Unique bill number being reprinted. *Receiver also reads `transactionNumber` as a fallback if `billNumber` is missing — let us know which key your client actually sends so we can drop the fallback.* |
| `cashier` | string | Yes | Cashier id handling the reprint. |

### Error envelope

The receiver returns the same error envelope across endpoints:

```
{ "status": 400, "message": "Failure",
  "data": { "ErrorCode": "11", "ErrorDescription": "...", "Succeeded": "false" } }
```

Common cases: missing required field (`"<field>" is required`), wrong type (`"<field>" must be a string`), till not found (`ErrorCode 11`), unknown `transactionSessionId` (`ErrorCode 1`).

### What we need from Nukkad

| # | Item | Status | Impact |
|---|------|--------|--------|
| 1 | Point staging/production push to our 9 endpoints at `https://{our-server}:8001/v1/rlcc/*` | Pending | Nothing works without this |
| 2 | Confirm body format: normal JSON (`{...}`) or stringified (`"{...}"`)? Receiver accepts both, but knowing the canonical form helps debugging. | Pending | Affects parser logging |
| 3 | Confirm `storeIdentifier` values — CIN codes (`NDCIN1223`) or store names (`"Asha"`)? | Pending | Affects store mapping |
| 4 | Confirm retry/queue policy if our endpoint is down | Pending | Determines whether we need our own ingest queue |
| 5 | Confirm timezone of `transactionTimeStamp` / `lineTimeStamp` (UTC vs IST) | Pending | Backend converts to UTC; mis-tagged input drifts the timeline |
| 6 | Confirm casing for BillReprint timestamp (`transactionTimestamp` per the §4.9 table vs `transactionTimeStamp` per the §4.1 prose) and confirm whether the bill identifier is `billNumber` (per §4.9) or `transactionNumber` | Pending | Receiver tolerates both today; we'd like to remove the fallbacks once confirmed |
| 7 | Confirm whether boolean flags (`employeePurchase`, `isPreviousTransaction`, `isForTillLookup`, `printable`) arrive as JSON booleans or stringified `"true"`/`"false"` | Pending | Receiver parses both; flagging so we can tighten validation later |

### Key enums we consume

Mirrors §6 of the PDF.

**outsideOpeningHours (§6.1):** `InsideOpeningHours`, `OutsideOpeningHours`, `PartlyOutsideOpeningHours`

**lineAttribute — `AddTransactionEvent` (§6.2):** `None`, `TransactionSuspended`, `TransactionResumed`, `TransactionCancelled`

**lineAttribute — payment line (§6.3):** `None`, `Cash`, `CashInForeignAmount`, `CreditCard`, `GiftCard`, `InternalShopVoucher`, `BankVoucher`, `AccountSale`, `ReturnCash`, `FractionRounding`, `PurchaseOrder`, `CreditNoteIssued`, `CreditNotePayment`, `LoyaltyCard`, `UPI`

**transactionType (§6.4):** `CompletedNormally` (default), `Suspended`, `Cancelled`, `CancellationOfPrevious`, `OperatorSignOn`, `OperatorSignOff`, `DrawerOpenedOutsideATransaction`, `CashStatement`, `SurpriseTillCashCounts`

**itemAttribute (§6.5):** `None`, `ExchangeSlipWithoutMatchingLine`, `BackorderItem`, `VoidedBackorderItem`, `ReturnItem`, `CancellationWithinTransaction`, `GiftCard`, `ExchangeSlip`, `ReturnNotRecentlySold`, `Reserved`, `FastMovingItem`, `BulkPurchase`

**scanAttribute (§6.6):** `None`, `Auto`, `ModifiedUnitPrice`, `ManuallyEntered`

**discountType (§6.7):** `NoLineDiscount`, `DiscountNotAllowed`, `AutoGeneratedValue`, `AutoGeneratedPercentage`, `ManuallyEnteredValue`, `ManuallyEnteredPercentage`

**totalLineAttributes (§6.8):** `None`, `SubTotal`, `VAT`, `FinalDiscountLess`, `DiscountIncludedInTotalAmount`, `LoyaltyCardDiscount`, `TotalAmountToBePaid`, `TotalDiscount`, `TotalEmployeeDiscout` (PDF spelling — verify before relying on it)

### Smoke test

After Nukkad is wired up to our endpoints, on the server:

```bash
# all 17 scenarios
python3 poc/scripts/verify_push_endpoints.py

# pick a subset
python3 poc/scripts/verify_push_endpoints.py --only happy_path,get_till_unknown,bill_reprint

# list available scenarios
python3 poc/scripts/verify_push_endpoints.py --list
```

Defaults: `BASE_URL=http://localhost:8001`, auth key auto-loaded from `poc/.env` (`NUKKAD_PUSH_AUTH_KEY`).

Beyond the basic happy-path flow, the 17 scenarios exercise: response-envelope shape on `BeginTransactionWithTillLookup`, fractional `itemQuantity` (1.250 kg), stringified-bool flags, the `GetTill` failure envelope (`ErrorCode 11`), `BillReprint` with `billNumber` + Long-ms timestamp, duplicate-event dedupe, commit-without-Begin, sale-line-without-Begin, event/route mismatch, malformed JSON, stringified body, and auth failures. Exit code is the count of failed scenarios.

## 2. XProtect WebRTC Integration

Milestone XProtect VMS records video from all store cameras. We access it via the MIP VMS WebRTC API for dashboard video playback.

### What we use

**Live video:** `POST /webRTC/session` with `deviceId` (camera GUID) — streams live feed.

**Recorded playback:** `POST /webRTC/session` with `deviceId` + `playbackTime` (ISO 8601 UTC) — streams recorded video from that timestamp. Supports `speed` (playback speed) and `skipGaps` (skip gaps between recordings).

**Session lifecycle:**
1. POST `/webRTC/session` → get sessionId + offerSDP
2. PATCH `/webRTC/session/{sessionId}` with answerSDP
3. Exchange ICE candidates via `/webRTC/iceCandidates/{sessionId}`
4. WebRTC peer connection established → H.264 video streams
5. Token refresh: PATCH session with new OAuth token before expiry (1h default)

**Video time tracking:** `rtpTimestamp` in each frame = milliseconds since first frame. Absolute time = `playbackTime` + `rtpTimestamp`.

### What we need from WAISL

| # | Item | Status | Impact |
|---|------|--------|--------|
| 1 | XProtect API Gateway URL (internal or public) | Pending | Blocks all video integration |
| 2 | OAuth credentials (client ID/secret, token endpoint) | Pending | Can't authenticate |
| 3 | Camera device IDs (GUIDs) mapped to store + POS | Pending | Can't request correct camera |
| 4 | Network path: can dashboard browser reach API Gateway? | Pending | May need STUN/TURN config |
| 5 | Current retention policy (days per recording server) | Pending | Affects our hybrid retention strategy |
| 6 | Server-side video export API (beyond WebRTC playback) | Pending | Alternative for snippet extraction |

### Camera-to-POS mapping

We need a mapping from our `SellerWindowId` (e.g., `NDCIN1223_POS3`) to XProtect's `deviceId` (GUID). This could be:
- A static config file we maintain (like current `mapping.json`)
- Or derived from XProtect's device listing API if available

WAISL to confirm which approach and provide the initial mapping.

## 3. Edge Device Protocol

Edge devices send CV inference results to the app server. No video leaves the edge — metadata only.

### Transport

MQTT over TLS. Broker runs on or near the app server. QoS 1 for all topics. CV signals are idempotent snapshots — duplicates are harmless, dropped signals are not.

### Topics

| Topic | Frequency | Payload |
|-------|-----------|---------|
| `rlcc/{store_id}/{camera_id}/signals` | 5-6 Hz per camera | Per-POS-zone presence + bill zone signals |
| `rlcc/{store_id}/{camera_id}/activity` | 2-3 Hz, triggered only | Phase 2 seller activity classification |
| `rlcc/{store_id}/health` | Every 60s | Device status (cameras active, FPS, CPU/mem) |

### Signal payload

```json
{
  "ts": "2026-04-16T10:02:05.123Z",
  "camera_id": "cam-03",
  "zones": [
    {"pos_zone": "POS3", "seller": true, "bill_motion": false, "bill_bg": false},
    {"pos_zone": "POS4", "seller": true, "bill_motion": false, "bill_bg": false}
  ],
  "non_seller_count": 2,
  "non_seller_present": true
}
```

Per-POS: seller + bill zone (fixed positions). Camera-wide: non-seller presence (customers don't stand in predictable per-POS zones in airport retail).

### Provisioning a new edge device

1. Install OS + DLStreamer + Voyager SDK + OpenVINO
2. Configure camera RTSP URLs
3. Draw zone polygons per POS using the zone drawing tool (web UI)
4. Set `store_id` in device config
5. Configure MQTT broker address + credentials
6. Verify signals arriving at app server

## 4. New Store Onboarding

Checklist for bringing a new store online:

### Prerequisites (WAISL/Nukkad)
- [ ] IP cameras installed and streaming RTSP
- [ ] XProtect recording server connected to cameras
- [ ] Nukkad POS system deployed at store
- [ ] Network: edge device can reach app server, cameras reachable on LAN

### Edge setup
- [ ] Edge device deployed at store
- [ ] Camera RTSP URLs configured
- [ ] Zone polygons drawn for each POS counter (using zone tool)
- [ ] MQTT connectivity verified — signals arriving at app server
- [ ] Health check passing

### Backend config
- [ ] Store added to `stores.json` (cin, name, operator, pos_system)
- [ ] POS-to-camera mapping added (`mapping.json` or via GetTill)
- [ ] XProtect device ID mapped to store/POS
- [ ] Nukkad pushing all 9 RLCC events for this store's `storeIdentifier`

### Validation
- [ ] Make a test transaction at POS
- [ ] Verify: Begin → SaleLine → Payment → Total → Commit events received in order
- [ ] Verify: `verify_push_endpoints.py` passes against live backend
- [ ] Verify: CV signals show customer + seller presence during transaction
- [ ] Verify: Transaction appears in dashboard with correct data
- [ ] Verify: Video playback works for the transaction timestamp
- [ ] Verify: Alert rules fire correctly (test with a void or high-discount transaction)
