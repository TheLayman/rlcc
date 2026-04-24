from __future__ import annotations

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


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _camera_for(store_id: str, pos_terminal_no: str):
    return deps.config.get_camera_by_terminal(store_id, pos_terminal_no)


def _hydrate_transaction(txn: TransactionSession) -> TransactionSession:
    txn.store_name = txn.store_name or deps.config.get_store_name(txn.store_id)
    camera = _camera_for(txn.store_id, txn.pos_terminal_no)
    if camera:
        txn.display_pos_label = camera.display_pos_label
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
    camera = _camera_for(store_id, pos_terminal_no)
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


async def _broadcast_raw_pos() -> None:
    await deps.ws_manager.broadcast("RAW_POS_DATA", deps.storage.get_recent_pos_events())


def _persist_committed_transaction(txn: TransactionSession, alerts: list[Alert]) -> bool:
    existing = find_transaction_by_bill_number(txn.bill_number)
    if existing and existing.source == "push_assembled":
        return False

    replace_ids: set[str] = set()
    if existing and existing.source.startswith("poll_"):
        replace_ids.add(existing.id)

    transactions = [
        current
        for current in load_transactions()
        if current.id not in replace_ids and current.id != txn.id and current.bill_number != txn.bill_number
    ]
    current_alerts = [
        alert
        for alert in load_alerts()
        if alert.transaction_id not in replace_ids and alert.transaction_id != txn.id
    ]

    transactions.append(txn)
    current_alerts.extend(alerts)
    save_transactions(transactions)
    save_alerts(current_alerts)
    return True


@router.post("/v1/rlcc/launch-event")
async def receive_event(request: Request):
    expected_key = deps.settings.push_auth_key
    provided_key = request.headers.get("x-authorization-key", "")
    if expected_key and provided_key != expected_key:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})

    payload, error = _parse_payload(await request.body())
    if error:
        return JSONResponse(status_code=400, content={"message": error})

    deps.storage.append_event(payload)

    if deps.storage.is_duplicate(payload):
        await _broadcast_raw_pos()
        return {"status": 200, "message": "duplicate, ignored"}
    deps.storage.mark_seen(payload)

    event_type = payload.get("event", "")
    store_id = payload.get("storeIdentifier", "")
    pos_terminal_no = payload.get("posTerminalNo", "")
    cashier_id = payload.get("cashier", "")

    if store_id:
        deps.fraud_engine.record_nukkad_event(store_id)

    if event_type == "BeginTransactionWithTillLookup":
        deps.assembler.begin(payload)

    elif event_type in {"AddTransactionSaleLine", "AddTransactionSaleLineWithTillLookup"}:
        deps.assembler.add_sale_line(payload)

    elif event_type == "AddTransactionPaymentLine":
        deps.assembler.add_payment_line(payload)

    elif event_type == "AddTransactionTotalLine":
        deps.assembler.add_total_line(payload)

    elif event_type == "AddTransactionEvent":
        deps.assembler.add_event(payload)

    elif event_type == "GetTill":
        await _broadcast_raw_pos()
        return {"status": 200, "message": "GetTill acknowledged"}

    elif event_type == "CommitTransaction":
        txn = deps.assembler.commit(payload)
        if txn:
            txn = _hydrate_transaction(txn)
            txn = correlate(txn, deps.cv_consumer, deps.config)
            txn.snippet_path = _extract_transaction_clip(txn)

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

    elif event_type == "BillReprint":
        event_ts = _parse_ts(payload.get("transactionTimeStamp"))
        clip_path, camera_id = _extract_event_clip(
            clip_id=f"bill-reprint-{payload.get('transactionNumber', 'unknown')}",
            store_id=store_id,
            pos_terminal_no=pos_terminal_no,
            at=event_ts,
        )
        alert = Alert(
            transaction_id=payload.get("transactionNumber", ""),
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

    await _broadcast_raw_pos()
    return {"status": 200, "message": "Success", "event": event_type}
