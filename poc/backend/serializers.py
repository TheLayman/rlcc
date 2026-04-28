from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.config import Config
from backend.models import Alert, TransactionSession


CLIP_STATUS_AVAILABLE = "available"
CLIP_STATUS_PENDING = "pending"
CLIP_STATUS_OUTSIDE_BUFFER = "outside_buffer"
CLIP_STATUS_CAMERA_UNMAPPED = "camera_unmapped"
CLIP_STATUS_RETENTION_EXPIRED = "retention_expired"
CLIP_STATUS_NOT_RECORDED = "not_recorded"
CLIP_STATUS_UNKNOWN = "unknown"


def _humanize_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    days = seconds / 86400
    if days >= 1:
        return f"{int(days)}d"
    hours = seconds / 3600
    if hours >= 1:
        return f"{int(hours)}h"
    return f"{max(int(seconds / 60), 1)}m"


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def compute_alert_clip_status(
    alert: Alert,
    config: Config,
    *,
    video_manager: Any = None,
    video_buffer_minutes: int = 0,
) -> tuple[str, str]:
    """Return (status_code, human_readable_reason) for the alert's clip."""
    if alert.snippet_path and video_manager is not None and video_manager.clip_exists(alert.snippet_path):
        return CLIP_STATUS_AVAILABLE, ""

    if alert.snippet_path:
        return CLIP_STATUS_RETENTION_EXPIRED, "Clip file is missing (retention cleanup or purge)"

    if not alert.camera_id:
        camera = None
        if alert.pos_terminal_no:
            camera = config.get_camera_by_terminal(alert.store_id, alert.pos_terminal_no)
        if not camera:
            pos_label = alert.display_pos_label or alert.pos_terminal_no or "unknown POS"
            store_label = config.get_store_name(alert.store_id) or alert.store_id or "unknown store"
            return CLIP_STATUS_CAMERA_UNMAPPED, f"No camera mapped for {pos_label} at {store_label}"

    alert_ts = _coerce_datetime(alert.timestamp)
    if alert_ts is not None and video_buffer_minutes > 0:
        now = datetime.now(timezone.utc)
        age_seconds = (now - alert_ts).total_seconds()
        buffer_seconds = video_buffer_minutes * 60
        if age_seconds > buffer_seconds:
            return (
                CLIP_STATUS_OUTSIDE_BUFFER,
                f"Older than RTSP buffer ({_humanize_duration(age_seconds)} old, buffer keeps {video_buffer_minutes}m)",
            )
        if age_seconds < 60:
            return CLIP_STATUS_PENDING, "Clip still being extracted (just recorded)"

    return (
        CLIP_STATUS_NOT_RECORDED,
        "CV recorder did not produce footage for this window (recorder offline or ffmpeg failed)",
    )


def transaction_total(txn: TransactionSession) -> float:
    for total in txn.totals:
        if total.line_attribute in {"TotalAmountToBePaid", "GrandTotal", "Total"}:
            return float(total.amount)
    if txn.payments:
        return round(sum(float(p.amount) for p in txn.payments), 2)
    return round(sum(float(item.total_amount) for item in txn.items), 2)


def transaction_timestamp(txn: TransactionSession) -> str:
    if txn.committed_at:
        if isinstance(txn.committed_at, datetime):
            return txn.committed_at.isoformat()
        return str(txn.committed_at)
    if txn.started_at:
        return txn.started_at
    return datetime.utcnow().isoformat()


def clip_url_for_transaction(txn: TransactionSession) -> str | None:
    return f"/api/transactions/{txn.id}/video" if txn.snippet_path else None


def clip_url_for_alert(alert: Alert) -> str | None:
    if alert.snippet_path or alert.transaction_id:
        return f"/api/alerts/{alert.id}/video"
    return None


def serialize_transaction(txn: TransactionSession, config: Config) -> dict:
    return {
        "id": txn.id,
        "shop_id": txn.store_id,
        "shop_name": txn.store_name or config.get_store_name(txn.store_id),
        "cam_id": txn.camera_id,
        "pos_id": txn.display_pos_label or txn.pos_terminal_no,
        "cashier_name": txn.cashier_id,
        "timestamp": transaction_timestamp(txn),
        "started_at": txn.started_at,
        "committed_at": txn.committed_at.isoformat() if isinstance(txn.committed_at, datetime) else str(txn.committed_at or ""),
        "transaction_total": transaction_total(txn),
        "risk_level": txn.risk_level,
        "triggered_rules": list(txn.triggered_rules),
        "status": getattr(txn, "status", "pending"),
        "fraud_category": txn.triggered_rules[0] if txn.triggered_rules else "",
        "notes": txn.notes,
        "source": txn.source,
        "bill_number": txn.bill_number,
        "transaction_number": txn.transaction_number,
        "transaction_type": txn.transaction_type,
        "employee_purchase": txn.employee_purchase,
        "clip_url": clip_url_for_transaction(txn),
        "receipt_status": "generated" if txn.cv_receipt_detected else "not_generated" if txn.cv_receipt_detected is False else "unknown",
        "items": [item.model_dump() for item in txn.items],
        "payments": [payment.model_dump() for payment in txn.payments],
        "totals": [total.model_dump() for total in txn.totals],
        "events": [event.model_dump() for event in txn.events],
        "cv_non_seller_present": txn.cv_non_seller_present,
        "cv_non_seller_count": txn.cv_non_seller_count,
        "cv_receipt_detected": txn.cv_receipt_detected,
        "cv_confidence": txn.cv_confidence,
        "timeline_url": f"/api/transactions/{txn.id}/timeline",
    }


def serialize_alert(
    alert: Alert,
    config: Config,
    *,
    video_manager: Any = None,
    video_buffer_minutes: int = 0,
) -> dict:
    clip_status, clip_reason = compute_alert_clip_status(
        alert,
        config,
        video_manager=video_manager,
        video_buffer_minutes=video_buffer_minutes,
    )
    return {
        "id": alert.id,
        "transaction_id": alert.transaction_id or "N/A",
        "shop_id": alert.store_id,
        "shop_name": alert.store_name or config.get_store_name(alert.store_id),
        "cashier_name": alert.cashier_id,
        "risk_level": alert.risk_level,
        "triggered_rules": list(alert.triggered_rules),
        "timestamp": alert.timestamp.isoformat() if isinstance(alert.timestamp, datetime) else str(alert.timestamp),
        "status": alert.status,
        "cam_id": alert.camera_id,
        "pos_id": alert.display_pos_label or alert.pos_terminal_no,
        "clip_url": clip_url_for_alert(alert),
        "clip_status": clip_status,
        "clip_reason": clip_reason,
        "cv_confidence": getattr(alert, "cv_confidence", "") or "",
        "remarks": alert.remarks or "",
        "source": alert.source,
    }


def build_bill_data(txn: TransactionSession) -> dict:
    payments = [
        {
            "mode": payment.line_attribute or payment.payment_description or "Unknown",
            "amt": float(payment.amount),
            "cardType": payment.card_type,
            "approvalCode": payment.approval_code,
        }
        for payment in txn.payments
    ]
    items = [
        {
            "name": item.item_description,
            "qty": item.item_quantity,
            "price": float(item.item_unit_price),
            "totAmt": float(item.total_amount),
            "discount": float(item.discount),
            "scanAttribute": item.scan_attribute,
            "itemAttribute": item.item_attribute,
            "discountType": item.discount_type,
            "grantedBy": item.granted_by,
            "lineTimeStamp": item.line_timestamp,
        }
        for item in txn.items
    ]
    totals = {total.line_attribute: float(total.amount) for total in txn.totals}
    return {
        "billNo": txn.bill_number,
        "terminalName": txn.display_pos_label or txn.pos_terminal_no,
        "billType": txn.transaction_type,
        "status": txn.status,
        "employeePurchase": txn.employee_purchase,
        "items": items,
        "payModes": payments,
        "totals": totals,
        "billAmt": transaction_total(txn),
        "netSale": transaction_total(txn),
        "discAmt": round(sum(float(item.discount) for item in txn.items), 2),
        "returnAmt": round(
            sum(float(payment.amount) for payment in txn.payments if payment.line_attribute == "ReturnCash"),
            2,
        ),
        "taxBreakup": [],
    }
