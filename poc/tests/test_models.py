from backend.models import (
    SaleLine, PaymentLine, TotalLine, TransactionEvent,
    TransactionSession, Alert, CVWindow
)
from datetime import datetime, timezone


def test_sale_line_from_nukkad():
    payload = {
        "lineTimeStamp": "2026-04-16T10:02:05Z",
        "lineNumber": 1,
        "itemDescription": "Chicken Burger",
        "itemQuantity": 2,
        "itemUnitPrice": 249.0,
        "totalAmount": 498.0,
        "scanAttribute": "Auto",
        "itemAttribute": "None",
        "discountType": "NoLineDiscount",
        "discount": 0,
        "grantedBy": "",
    }
    line = SaleLine.from_nukkad(payload)
    assert line.item_description == "Chicken Burger"
    assert line.scan_attribute == "Auto"
    assert line.total_amount == 498.0


def test_payment_line_from_nukkad():
    payload = {
        "lineTimeStamp": "2026-04-16T10:02:35Z",
        "lineNumber": 1,
        "lineAttribute": "Cash",
        "paymentDescription": "Cash",
        "amount": 500.0,
        "cardType": "",
        "paymentTypeID": "",
    }
    line = PaymentLine.from_nukkad(payload)
    assert line.line_attribute == "Cash"
    assert line.amount == 500.0


def test_transaction_session_defaults():
    txn = TransactionSession(
        id="sess-001",
        store_id="NDCIN1223",
        pos_terminal="POS 3",
        source="push_assembled",
    )
    assert txn.status == "open"
    assert txn.items == []
    assert txn.payments == []
    assert txn.totals == []
    assert txn.events == []
    assert txn.risk_level == "Low"
    assert txn.triggered_rules == []


def test_alert_creation():
    alert = Alert(
        id="ALT-001",
        transaction_id="TXN-001",
        store_id="NDCIN1223",
        risk_level="High",
        triggered_rules=["5_negative_amount"],
    )
    assert alert.status == "new"
    assert alert.remarks is None


def test_cv_window_defaults():
    w = CVWindow(
        pos_zone="POS3",
        camera_id="cam-01",
        window_start=datetime.now(timezone.utc),
        window_end=datetime.now(timezone.utc),
    )
    assert w.seller_present_pct == 0.0
    assert w.non_seller_present_pct == 0.0
    assert w.bill_motion_detected is False
