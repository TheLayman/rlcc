from __future__ import annotations

from datetime import datetime

from backend.config import Config
from backend.models import Alert, TransactionSession


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


def serialize_alert(alert: Alert, config: Config) -> dict:
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
        "clip_url": f"/api/alerts/{alert.id}/video" if alert.snippet_path else None,
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
