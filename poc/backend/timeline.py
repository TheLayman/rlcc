from __future__ import annotations

from datetime import datetime

from backend.models import TransactionSession


def _as_iso(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def build_timeline(txn: TransactionSession) -> list[dict]:
    events: list[dict] = []

    if txn.started_at:
        events.append(
            {
                "ts": txn.started_at,
                "source": "pos",
                "type": "begin_transaction",
                "data": {
                    "cashier": txn.cashier_id,
                    "transaction_type": txn.transaction_type,
                    "store_id": txn.store_id,
                    "pos_terminal_no": txn.pos_terminal_no,
                },
            }
        )

    for item in txn.items:
        events.append(
            {
                "ts": item.line_timestamp or txn.started_at or "",
                "source": "pos",
                "type": "sale_line",
                "data": {
                    "item": item.item_description,
                    "qty": item.item_quantity,
                    "amount": item.total_amount,
                    "scan": item.scan_attribute,
                    "attribute": item.item_attribute,
                    "discount_type": item.discount_type,
                    "discount": item.discount,
                    "granted_by": item.granted_by,
                },
            }
        )

    for payment in txn.payments:
        events.append(
            {
                "ts": payment.line_timestamp or "",
                "source": "pos",
                "type": "payment_line",
                "data": {
                    "mode": payment.line_attribute or payment.payment_description,
                    "amount": payment.amount,
                    "card_type": payment.card_type,
                },
            }
        )

    for total in txn.totals:
        events.append(
            {
                "ts": total.line_timestamp or txn.started_at or "",
                "source": "pos",
                "type": "total_line",
                "data": {"type": total.line_attribute, "amount": total.amount},
            }
        )

    for event in txn.events:
        events.append(
            {
                "ts": event.line_timestamp or txn.started_at or "",
                "source": "pos",
                "type": "transaction_event",
                "data": {"attribute": event.line_attribute, "description": event.event_description},
            }
        )

    if txn.cv_non_seller_present is not None:
        events.append(
            {
                "ts": txn.started_at or "",
                "source": "cv",
                "type": "customer_presence",
                "data": {
                    "present": txn.cv_non_seller_present,
                    "count": txn.cv_non_seller_count,
                    "confidence": txn.cv_confidence,
                },
            }
        )

    if txn.cv_receipt_detected is not None:
        events.append(
            {
                "ts": _as_iso(txn.committed_at),
                "source": "cv",
                "type": "receipt_detection",
                "data": {"detected": txn.cv_receipt_detected},
            }
        )

    if txn.committed_at:
        events.append(
            {
                "ts": _as_iso(txn.committed_at),
                "source": "pos",
                "type": "commit",
                "data": {"bill_number": txn.bill_number},
            }
        )

    if txn.triggered_rules:
        events.append(
            {
                "ts": _as_iso(txn.committed_at) or txn.started_at or "",
                "source": "fraud",
                "type": "rules_triggered",
                "data": {"rules": list(txn.triggered_rules), "risk_level": txn.risk_level},
            }
        )

    events.sort(key=lambda event: event.get("ts") or "")
    return events
