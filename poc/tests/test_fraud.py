import json
from backend.fraud import FraudEngine
from backend.models import TransactionSession, SaleLine, PaymentLine, TotalLine


def _make_config():
    with open("config/rule_config.json") as f:
        return json.load(f)


def _make_txn(**kwargs) -> TransactionSession:
    defaults = {"id": "TXN-001", "store_id": "NDCIN1223", "pos_terminal": "POS 3", "source": "push_assembled", "status": "committed"}
    defaults.update(kwargs)
    return TransactionSession(**defaults)


def test_negative_amount():
    engine = FraudEngine(_make_config())
    txn = _make_txn()
    txn.totals = [TotalLine(line_attribute="TotalAmountToBePaid", amount=-50.0)]
    alerts = engine.evaluate(txn)
    assert "5_negative_amount" in txn.triggered_rules
    assert txn.risk_level == "High"


def test_manual_entry():
    engine = FraudEngine(_make_config())
    txn = _make_txn()
    txn.items = [SaleLine(scan_attribute="ManuallyEntered", item_description="Fries", total_amount=99)]
    alerts = engine.evaluate(txn)
    assert "8_manual_entry" in txn.triggered_rules


def test_manual_discount():
    engine = FraudEngine(_make_config())
    txn = _make_txn()
    txn.items = [SaleLine(discount_type="ManuallyEnteredValue", discount=30, total_amount=70)]
    alerts = engine.evaluate(txn)
    assert "10_manual_discount" in txn.triggered_rules


def test_drawer_opened():
    engine = FraudEngine(_make_config())
    txn = _make_txn(transaction_type="DrawerOpenedOutsideATransaction")
    alerts = engine.evaluate(txn)
    assert "12_drawer_opened" in txn.triggered_rules
    assert txn.risk_level == "High"


def test_null_transaction():
    engine = FraudEngine(_make_config())
    txn = _make_txn()
    alerts = engine.evaluate(txn)
    assert "14_null_transaction" in txn.triggered_rules


def test_void_no_customer():
    engine = FraudEngine(_make_config())
    txn = _make_txn(cv_non_seller_present=False, cv_confidence="HIGH")
    txn.items = [SaleLine(item_attribute="CancellationWithinTransaction", total_amount=100)]
    alerts = engine.evaluate(txn)
    assert "26_void_no_customer" in txn.triggered_rules
    assert txn.risk_level == "High"


def test_genuine_transaction():
    engine = FraudEngine(_make_config())
    txn = _make_txn()
    txn.items = [SaleLine(scan_attribute="Auto", item_description="Burger", total_amount=249)]
    txn.payments = [PaymentLine(line_attribute="Cash", amount=249)]
    alerts = engine.evaluate(txn)
    assert txn.risk_level == "Low"
    assert len(alerts) == 0


def test_risk_escalation_two_mediums():
    engine = FraudEngine(_make_config())
    txn = _make_txn()
    txn.items = [
        SaleLine(scan_attribute="ManuallyEntered", item_description="Fries", total_amount=99),
        SaleLine(discount_type="ManuallyEnteredValue", discount=30, total_amount=70),
    ]
    alerts = engine.evaluate(txn)
    assert txn.risk_level == "Medium"


def test_disabled_rule_skipped():
    config = _make_config()
    config["rules"]["8_manual_entry"]["enabled"] = False
    engine = FraudEngine(config)
    txn = _make_txn()
    txn.items = [SaleLine(scan_attribute="ManuallyEntered", item_description="Fries", total_amount=99)]
    alerts = engine.evaluate(txn)
    assert "8_manual_entry" not in txn.triggered_rules
