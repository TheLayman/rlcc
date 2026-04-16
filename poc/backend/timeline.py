from datetime import datetime
from backend.models import TransactionSession


def build_timeline(txn: TransactionSession) -> list[dict]:
    events = []

    if txn.started_at:
        events.append({"ts": txn.started_at, "source": "pos", "type": "begin_transaction",
                        "data": {"cashier": txn.cashier_id, "transaction_type": txn.transaction_type}})

    for item in txn.items:
        events.append({"ts": item.line_timestamp or txn.started_at or "", "source": "pos", "type": "sale_line",
                        "data": {"item": item.item_description, "qty": item.item_quantity,
                                 "amount": item.total_amount, "scan": item.scan_attribute,
                                 "attribute": item.item_attribute, "discount_type": item.discount_type,
                                 "discount": item.discount}})

    for pay in txn.payments:
        events.append({"ts": pay.line_timestamp or "", "source": "pos", "type": "payment_line",
                        "data": {"mode": pay.line_attribute, "amount": pay.amount}})

    for total in txn.totals:
        events.append({"ts": "", "source": "pos", "type": "total_line",
                        "data": {"type": total.line_attribute, "amount": total.amount}})

    if txn.committed_at:
        events.append({"ts": txn.committed_at.isoformat() if isinstance(txn.committed_at, datetime) else str(txn.committed_at),
                        "source": "pos", "type": "commit",
                        "data": {"bill_number": txn.bill_number}})

    for ev in txn.events:
        events.append({"ts": ev.line_timestamp or "", "source": "pos", "type": "transaction_event",
                        "data": {"attribute": ev.line_attribute, "description": ev.event_description}})

    # CV events
    if txn.cv_confidence and txn.cv_confidence not in ("UNAVAILABLE", "UNMAPPED", ""):
        if txn.cv_non_seller_present is not None:
            events.append({"ts": txn.started_at or "", "source": "cv", "type": "customer_presence",
                            "data": {"present": txn.cv_non_seller_present, "count": txn.cv_non_seller_count}})
        if txn.cv_receipt_detected is not None:
            ts = txn.committed_at.isoformat() if isinstance(txn.committed_at, datetime) else str(txn.committed_at) if txn.committed_at else ""
            events.append({"ts": ts, "source": "cv", "type": "receipt_detection",
                            "data": {"detected": txn.cv_receipt_detected}})

    events.sort(key=lambda e: e.get("ts", "") or "")
    return events
