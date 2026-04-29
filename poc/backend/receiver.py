from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import backend.deps as deps
from backend.correlator import correlate
from backend.models import Alert, TransactionSession
from backend.persistence import find_transaction_by_bill_number, load_alerts, load_transactions, save_alerts, save_transactions
from backend.serializers import serialize_alert, serialize_transaction

router = APIRouter()


def _serialize_alert(alert: Alert) -> dict:
    return serialize_alert(
        alert,
        deps.config,
        video_manager=deps.video_manager,
        video_buffer_minutes=deps.settings.video_buffer_minutes,
    )


def _parse_payload(raw_body: bytes) -> tuple[dict | None, str | None]:
    try:
        body_text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return None, "Invalid request body"

    try:
        parsed = json.loads(body_text)
    except json.JSONDecodeError:
        return None, "Invalid JSON"

    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            return None, "Invalid stringified JSON"

    if not isinstance(parsed, dict):
        return None, "Payload must be a JSON object"

    return parsed, None


_IST = timezone(timedelta(hours=5, minutes=30))


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Nukkad's live-prod feed sends naive ISO 8601 (e.g. "2026-04-28T12:53:33").
        # Per BACKEND_DESIGN §11 we assume IST (deployment timezone) and normalize
        # to UTC so it compares cleanly with the tz-aware datetimes used elsewhere
        # (CV signals, video buffer math).
        dt = dt.replace(tzinfo=_IST).astimezone(timezone.utc)
    return dt


def _parse_ts_or_ms(value) -> datetime | None:
    """Accept ISO 8601 strings or numeric epoch milliseconds.

    BillReprint's `transactionTimestamp` is spec'd as Long ms; other events use
    ISO 8601 strings on `transactionTimeStamp`. This helper covers both.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    if isinstance(value, str):
        # Try ISO 8601 first; fall back to numeric-string interpretation.
        iso = _parse_ts(value)
        if iso is not None:
            return iso
        try:
            return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)
        except (TypeError, ValueError):
            return None
    return None


import hashlib


def _assign_till(branch: str, pos_terminal_no: str) -> str:
    """Assign a numeric Till that is unique per (branch, posTerminalNo) pair.

    `branch` carries the store CIN in Nukkad's format (e.g. "NSCIN10489").
    Same (branch, terminal) → same Till, every time, with no server state.
    Different (branch, terminal) → near-certainly different Till
    (md5[:6] mod 1_000_000 ≈ 0.1% collision risk across 50 combos, fine for
    POC scale). Nukkad's POS reuses the Till in downstream calls so it must
    be stable across restarts.

    NB: We intentionally don't shortcut to "POS 3" → "3" anymore — that
    collides across stores (every "POS 1" everywhere would map to "1"),
    violating the per-(branch, terminal) uniqueness contract.
    """
    blob = f"{(branch or '').strip()}|{(pos_terminal_no or '').strip()}".encode("utf-8")
    return str(int(hashlib.md5(blob).hexdigest()[:6], 16) % 1_000_000)


def _camera_for(store_id: str, pos_terminal_no: str):
    return deps.config.get_camera_by_terminal(store_id, pos_terminal_no)


def _camera_for_payload(store_id: str, payload: dict):
    """Resolve a camera, trying posTerminalNo then tillDescription.

    Per Nukkad: the POS identifier can land in either field — `posTerminalNo`
    on transactional events, often `tillDescription` on GetTill / BillReprint
    (and they may carry different forms — "383" vs "POS 1" — for the same
    till). Camera matching honours `nukkad_pos_aliases` on each camera entry,
    so both forms resolve as long as the alias is configured.
    """
    for candidate in (payload.get("posTerminalNo"), payload.get("tillDescription")):
        if candidate:
            cam = deps.config.get_camera_by_terminal(store_id, candidate)
            if cam:
                return cam
    return None


def _hydrate_transaction(txn: TransactionSession) -> TransactionSession:
    txn.store_name = txn.store_name or deps.config.get_store_name(txn.store_id)
    # POC contract: lookup by store, not by (store, POS). One camera per store,
    # one zone per camera. Multi-POS-per-camera (Cafe Niloufer) carries
    # multi_pos:true so cv_confidence ends up REDUCED.
    camera = deps.config.get_camera_for_store(txn.store_id)
    if camera:
        # Preserve what Nukkad sent for the POS — camera.display_pos_label is
        # the camera's "primary" and would mislabel multi-POS transactions.
        if not txn.display_pos_label:
            txn.display_pos_label = txn.pos_terminal_no or camera.display_pos_label
        txn.camera_id = txn.camera_id or camera.camera_id
        txn.device_id = txn.device_id or camera.xprotect_device_id
        txn.seller_window_id = txn.seller_window_id or camera.seller_window_key
    return txn


def _extract_transaction_clip(txn: TransactionSession) -> str:
    if not deps.video_manager or not txn.camera_id:
        return ""
    start_ts = _parse_ts(txn.started_at)
    end_ts = txn.committed_at if isinstance(txn.committed_at, datetime) else _parse_ts(str(txn.committed_at or ""))
    if not start_ts or not end_ts:
        return ""
    return deps.video_manager.extract_clip(
        camera_id=txn.camera_id,
        clip_id=txn.id,
        start_ts=start_ts - timedelta(seconds=30),
        end_ts=end_ts + timedelta(seconds=30),
    )


def _extract_event_clip(*, clip_id: str, store_id: str, pos_terminal_no: str, at: datetime | None) -> tuple[str, str]:
    # POC contract: store-based camera lookup (POS terminal ignored for
    # camera resolution; per-POS data stays on the transaction itself).
    # The pos_terminal_no kwarg is kept for signature compatibility but
    # unused — callers may pass anything.
    camera = deps.config.get_camera_for_store(store_id)
    if not camera or not deps.video_manager or not at:
        return "", camera.camera_id if camera else ""
    return (
        deps.video_manager.extract_clip(
            camera_id=camera.camera_id,
            clip_id=clip_id,
            start_ts=at - timedelta(seconds=30),
            end_ts=at + timedelta(seconds=30),
        ),
        camera.camera_id,
    )


def _canonical_store_id(payload: dict) -> str:
    """Resolve the store CIN from any push payload.

    Nukkad's live-prod feed sends the CIN (NDCIN.../NSCIN...) in `branch`,
    but only on events that have one in the spec (Begin, GetTill, BillReprint).
    Sale, Payment, Total, Commit, and AddTransactionEvent only carry
    `storeIdentifier`, which in live-prod is a Mongo ObjectId — useless for
    looking anything up in our config.

    Resolution order:
      1. `branch` if present (already CIN)
      2. fallback to the assembler session created at Begin — its store_id
         was set from `branch` and is the CIN
      3. last resort: whatever's in `storeIdentifier` (probably an ObjectId,
         won't match config but keeps us from logging the empty string)
    """
    branch = (payload.get("branch") or "").strip()
    if branch:
        return branch
    session_id = payload.get("transactionSessionId") or ""
    if session_id:
        session = deps.assembler.sessions.get(session_id)
        if session and session.store_id:
            return session.store_id
    return (payload.get("storeIdentifier") or "").strip()


async def _broadcast_raw_pos() -> None:
    await deps.ws_manager.broadcast("RAW_POS_DATA", deps.storage.get_recent_pos_events())


def _persist_committed_transaction(txn: TransactionSession, alerts: list[Alert]) -> bool:
    existing = find_transaction_by_bill_number(txn.bill_number)
    if existing and existing.source == "push_assembled":
        return False

    transactions = [
        current
        for current in load_transactions()
        if current.id != txn.id and current.bill_number != txn.bill_number
    ]
    current_alerts = [
        alert
        for alert in load_alerts()
        if alert.transaction_id != txn.id
    ]

    transactions.append(txn)
    current_alerts.extend(alerts)
    save_transactions(transactions)
    save_alerts(current_alerts)
    return True


_VERIFY_HEADER = "x-rlcc-verify"  # set by poc/scripts/verify_push_endpoints.py


async def _ingest(request: Request, expected_event: str) -> JSONResponse | dict:
    expected_key = deps.settings.push_auth_key
    provided_key = request.headers.get("x-authorization-key", "")
    if expected_key and provided_key != expected_key:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})

    payload, error = _parse_payload(await request.body())
    if error:
        return JSONResponse(status_code=400, content={"message": error})

    body_event = payload.get("event", "")
    if body_event and body_event != expected_event:
        return JSONResponse(
            status_code=400,
            content={"message": f"event mismatch: route expects {expected_event}, payload says {body_event}"},
        )
    payload.setdefault("event", expected_event)

    # Verify-script traffic must NOT mutate any persistent state (events log,
    # transactions.jsonl, alerts.jsonl, video snippets) or push to the dashboard
    # WebSocket. The assembler's in-memory state is fine to mutate so scenarios
    # like duplicate_event / commit_no_begin still test real behavior — only the
    # write-side and broadcast-side calls are gated.
    is_verify = bool(request.headers.get(_VERIFY_HEADER))

    # GetTill is an idempotent query, not a stateful event — skip dedup so
    # repeated lookups (which all share an empty dedup key) don't get suppressed.
    if expected_event != "GetTill":
        if deps.storage.is_duplicate(payload):
            if not is_verify:
                await _broadcast_raw_pos()
            return {"status": 200, "message": "duplicate, ignored"}
        if not is_verify:
            deps.storage.append_event(payload)
        # mark_seen tracks an in-memory dedup set (no disk write) — keep it on
        # for verify so the duplicate_event scenario still works.
        deps.storage.mark_seen(payload)

    # Always work in CIN — branch (when present) or session-resolved CIN.
    # See _canonical_store_id for why payload.storeIdentifier alone isn't enough.
    store_id = _canonical_store_id(payload)
    pos_terminal_no = payload.get("posTerminalNo", "")
    cashier_id = payload.get("cashier", "")

    if store_id and not is_verify:
        deps.fraud_engine.record_nukkad_event(store_id)

    try:
        if expected_event == "BeginTransactionWithTillLookup":
            deps.assembler.begin(payload)
            if not is_verify:
                await _broadcast_raw_pos()
            return {
                "status": 200,
                "message": "Success",
                "data": {
                    "ErrorCode": "-1",
                    "Succeeded": "true",
                    "TransactionSessionId": payload.get("transactionSessionId", ""),
                },
            }

        elif expected_event in {"AddTransactionSaleLine", "AddTransactionSaleLineWithTillLookup"}:
            deps.assembler.add_sale_line(payload)

        elif expected_event == "AddTransactionPaymentLine":
            deps.assembler.add_payment_line(payload)

        elif expected_event == "AddTransactionTotalLine":
            deps.assembler.add_total_line(payload)

        elif expected_event == "AddTransactionEvent":
            deps.assembler.add_event(payload)

        elif expected_event == "GetTill":
            # GetTill is a till-assignment RPC: Nukkad's POS gates the entire
            # transaction flow on a successful response (Begin/Sale/Commit
            # don't fire until we hand back a Till), so this MUST always
            # succeed. The Till is reused by Nukkad in downstream calls — see
            # _assign_till for the per-(branch, terminal) derivation.
            # store_id is already the CIN form (set above via _canonical_store_id).
            till = _assign_till(store_id, payload.get("posTerminalNo", ""))
            if not is_verify:
                await _broadcast_raw_pos()
            return {
                "status": 200,
                "message": "Success",
                "data": {"ErrorCode": "-1", "Succeeded": "true", "Till": till},
            }

        elif expected_event == "CommitTransaction":
            txn = deps.assembler.commit(payload)
            if txn and not is_verify:
                # Verify mode skips this whole tail: no CV correlation, no clip
                # extraction (would write to data/snippets/), no fraud eval, no
                # persistence to transactions.jsonl/alerts.jsonl, no broadcasts.
                # commit() above already cleaned up the in-memory session.
                txn = _hydrate_transaction(txn)
                txn = correlate(txn, deps.cv_consumer, deps.config)
                txn.snippet_path = await asyncio.to_thread(_extract_transaction_clip, txn)

                alerts = deps.fraud_engine.evaluate(txn)
                for alert in alerts:
                    alert.store_name = alert.store_name or txn.store_name
                    alert.pos_terminal_no = alert.pos_terminal_no or txn.pos_terminal_no
                    alert.display_pos_label = alert.display_pos_label or txn.display_pos_label
                    alert.camera_id = alert.camera_id or txn.camera_id
                    alert.device_id = alert.device_id or txn.device_id
                    alert.snippet_path = alert.snippet_path or txn.snippet_path

                if _persist_committed_transaction(txn, alerts):
                    await deps.ws_manager.broadcast("NEW_TRANSACTION", serialize_transaction(txn, deps.config))
                    for alert in alerts:
                        await deps.ws_manager.broadcast("NEW_ALERT", _serialize_alert(alert))
            elif not txn:
                # Unknown session — likely a stray retry or a Commit that arrived
                # after we restarted and lost the session. Don't 400 (Nukkad's queue
                # would keep retrying); ack with a distinguishing message so the
                # caller can tell the no-op case apart from a real commit.
                if not is_verify:
                    await _broadcast_raw_pos()
                return {
                    "status": 200,
                    "message": "no session matched, ignored",
                    "event": expected_event,
                    "transactionSessionId": payload.get("transactionSessionId", ""),
                }

        elif expected_event == "BillReprint":
            if not is_verify:
                # Skip clip extraction (writes data/snippets/), alert creation
                # (writes alerts.jsonl), and ws broadcast for verify traffic.
                bill_number = (
                    payload.get("billNumber")
                    or payload.get("transactionNumber")
                    or ""
                )
                event_ts = (
                    _parse_ts_or_ms(payload.get("transactionTimestamp"))
                    or _parse_ts_or_ms(payload.get("transactionTimeStamp"))
                )
                # POC: store-based camera lookup, ignore POS for resolution.
                clip_path, camera_id = await asyncio.to_thread(
                    _extract_event_clip,
                    clip_id=f"bill-reprint-{bill_number or 'unknown'}",
                    store_id=store_id,
                    pos_terminal_no=pos_terminal_no,
                    at=event_ts,
                )
                alert = Alert(
                    transaction_id=bill_number,
                    store_id=store_id,
                    store_name=deps.config.get_store_name(store_id),
                    pos_terminal_no=pos_terminal_no,
                    display_pos_label=pos_terminal_no,
                    cashier_id=cashier_id,
                    risk_level="Medium",
                    triggered_rules=["13_bill_reprint"],
                    timestamp=event_ts or datetime.now(timezone.utc),
                    camera_id=camera_id,
                    snippet_path=clip_path,
                    source="bill_reprint",
                )
                deps.storage.append("alerts", alert.model_dump())
                await deps.ws_manager.broadcast("NEW_ALERT", _serialize_alert(alert))
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"message": f"invalid {expected_event} payload: {exc}"},
        )

    if not is_verify:
        await _broadcast_raw_pos()
    return {"status": 200, "message": "Success", "event": expected_event}


ROUTES: list[tuple[str, str]] = [
    ("/v1/rlcc/begin-transaction-with-till-lookup",         "BeginTransactionWithTillLookup"),
    ("/v1/rlcc/add-transaction-event",                      "AddTransactionEvent"),
    ("/v1/rlcc/add-transaction-payment-line",               "AddTransactionPaymentLine"),
    ("/v1/rlcc/add-transaction-sale-line",                  "AddTransactionSaleLine"),
    ("/v1/rlcc/add-transaction-sale-line-with-till-lookup", "AddTransactionSaleLineWithTillLookup"),
    ("/v1/rlcc/add-transaction-total-line",                 "AddTransactionTotalLine"),
    ("/v1/rlcc/commit-transaction",                         "CommitTransaction"),
    ("/v1/rlcc/get-till",                                   "GetTill"),
    ("/v1/rlcc/bill-reprint",                               "BillReprint"),
]


def _make_handler(event: str):
    async def handler(request: Request):
        return await _ingest(request, event)
    handler.__name__ = f"receive_{event}"
    return handler


for _path, _event in ROUTES:
    router.add_api_route(_path, _make_handler(_event), methods=["POST"])


@router.post("/v1/rlcc/{rest:path}")
async def unknown_rlcc_path(rest: str, request: Request):
    """Catch-all for unrecognised /v1/rlcc/* POSTs.

    Nukkad's POS has been observed POSTing to `/v1/rlcc/undefined` when their
    client-side event-name resolution fails (likely a missing case for
    `BeginTransactionWithTillLookup`). The traffic-log middleware in main.py
    captures the body so we can identify what they meant to send; this handler
    just returns a structured 404 so they see a clean spec-shaped error.
    """
    return JSONResponse(
        status_code=404,
        content={
            "status": 404,
            "message": "Failure",
            "data": {
                "ErrorCode": "404",
                "ErrorDescription": f"Unknown RLCC event path: /v1/rlcc/{rest}",
                "Succeeded": "false",
            },
        },
    )


_CONTRACT_SAMPLES: dict[str, dict] = {
    "BeginTransactionWithTillLookup": {
        "event": "BeginTransactionWithTillLookup",
        "transactionSessionId": "<uuid>",
        "storeIdentifier": "NDCIN1231",
        "posTerminalNo": "POS 1",
        "cashier": "EMP-042",
        "transactionType": "CompletedNormally",
        "employeePurchase": False,
        "outsideOpeningHours": "InsideOpeningHours",
        "transactionTimeStamp": "2026-04-28T10:00:00Z",
    },
    "AddTransactionSaleLine": {
        "event": "AddTransactionSaleLine",
        "transactionSessionId": "<uuid>",
        "storeIdentifier": "NDCIN1231",
        "posTerminalNo": "POS 1",
        "cashier": "EMP-042",
        "lineTimeStamp": "2026-04-28T10:00:01Z",
        "lineNumber": 1,
        "itemID": "CB001",
        "itemDescription": "Chicken Burger",
        "itemQuantity": 1,
        "itemUnitPrice": 249.0,
        "totalAmount": 249.0,
        "scanAttribute": "None",
        "itemAttribute": "None",
        "discountType": "NoLineDiscount",
        "discount": 0.0,
        "grantedBy": "",
    },
    "AddTransactionSaleLineWithTillLookup": {
        "event": "AddTransactionSaleLineWithTillLookup",
        "transactionSessionId": "<uuid>",
        "storeIdentifier": "NDCIN1231",
        "posTerminalNo": "POS 1",
        "cashier": "EMP-042",
        "lineTimeStamp": "2026-04-28T10:00:01Z",
        "lineNumber": 1,
        "itemID": "CB001",
        "itemDescription": "Chicken Burger",
        "itemQuantity": 1,
        "itemUnitPrice": 249.0,
        "totalAmount": 249.0,
    },
    "AddTransactionPaymentLine": {
        "event": "AddTransactionPaymentLine",
        "transactionSessionId": "<uuid>",
        "storeIdentifier": "NDCIN1231",
        "posTerminalNo": "POS 1",
        "cashier": "EMP-042",
        "lineTimeStamp": "2026-04-28T10:00:05Z",
        "lineNumber": 2,
        "lineAttribute": "None",
        "paymentDescription": "Cash",
        "amount": 249.0,
        "cardType": "",
        "paymentTypeID": "CASH",
        "approvalCode": "",
        "cardNo": "",
    },
    "AddTransactionTotalLine": {
        "event": "AddTransactionTotalLine",
        "transactionSessionId": "<uuid>",
        "storeIdentifier": "NDCIN1231",
        "posTerminalNo": "POS 1",
        "cashier": "EMP-042",
        "lineAttribute": "GrandTotal",
        "totalDescription": "Grand Total",
        "amount": 249.0,
    },
    "AddTransactionEvent": {
        "event": "AddTransactionEvent",
        "transactionSessionId": "<uuid>",
        "storeIdentifier": "NDCIN1231",
        "posTerminalNo": "POS 1",
        "cashier": "EMP-042",
        "lineTimeStamp": "2026-04-28T10:00:06Z",
        "lineAttribute": "DrawerOpenedOutsideATransaction",
        "eventDescription": "Cash drawer opened outside of a transaction",
    },
    "CommitTransaction": {
        "event": "CommitTransaction",
        "transactionSessionId": "<uuid>",
        "storeIdentifier": "NDCIN1231",
        "posTerminalNo": "POS 1",
        "cashier": "EMP-042",
        "transactionNumber": "BILL-12345",
        "billNumber": "BILL-12345",
        "transactionTimeStamp": "2026-04-28T10:00:08Z",
    },
    "BillReprint": {
        "event": "BillReprint",
        "storeIdentifier": "NDCIN1231",
        "posTerminalNo": "POS 1",
        "cashier": "EMP-042",
        "transactionTimestamp": 1714298468000,
        "billNumber": "BILL-12345",
    },
    "GetTill": {
        "event": "GetTill",
        "storeIdentifier": "NDCIN1231",
        "tillDescription": "POS 1",
    },
}


@router.get("/v1/rlcc/contract")
async def receiver_contract():
    """Self-describing schema for the push API.

    Returned to the POS team so they can confirm field names + path/event
    pairings + sample payload formats match what the assembler expects.
    No auth required — read-only introspection.
    """
    return {
        "auth_header": "x-authorization-key",
        "content_type": "application/json",
        "encoding_note": (
            "Body MUST be parseable as JSON. Stringified JSON (a JSON-encoded "
            "string whose value is itself JSON) is also accepted for backward "
            "compatibility with Nukkad's double-encoded format."
        ),
        "common_fields": {
            "transactionSessionId": "uuid that ties Begin/AddSaleLine/.../Commit together",
            "storeIdentifier": "store CIN, must match an entry in stores.json",
            "posTerminalNo": "POS terminal label, must match an enabled camera mapping",
            "cashier": "cashier id (free-form string)",
            "transactionTimeStamp / lineTimeStamp": "ISO-8601 with TZ; 'Z' or '+00:00'",
            "transactionTimestamp (BillReprint)": "epoch milliseconds (Long)",
        },
        "endpoints": [
            {"method": "POST", "path": path, "event": event}
            for path, event in ROUTES
        ],
        "samples": _CONTRACT_SAMPLES,
        "responses": {
            "200": {"status": 200, "message": "Success", "event": "<event-name>"},
            "200_duplicate": {"status": 200, "message": "duplicate, ignored"},
            "200_no_session": {"status": 200, "message": "no session matched, ignored"},
            "400": {"message": "<reason>"},
            "401": {"message": "Unauthorized"},
        },
    }
