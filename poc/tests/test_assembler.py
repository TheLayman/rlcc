from backend.assembler import TransactionAssembler


def test_begin_creates_session():
    asm = TransactionAssembler()
    asm.begin({
        "transactionSessionId": "s1",
        "storeIdentifier": "NDCIN1223",
        "posTerminalNo": "POS 3",
        "cashier": "EMP-042",
        "transactionType": "CompletedNormally",
        "employeePurchase": False,
        "transactionTimeStamp": "2026-04-16T10:02:00Z",
    })
    assert "s1" in asm.sessions
    assert asm.sessions["s1"].store_id == "NDCIN1223"
    assert asm.sessions["s1"].status == "open"


def test_add_sale_line():
    asm = TransactionAssembler()
    asm.begin({"transactionSessionId": "s1", "storeIdentifier": "X", "posTerminalNo": "P1"})
    asm.add_sale_line({
        "transactionSessionId": "s1",
        "itemDescription": "Burger",
        "itemQuantity": 1,
        "itemUnitPrice": 249.0,
        "totalAmount": 249.0,
        "scanAttribute": "Auto",
        "itemAttribute": "None",
        "discountType": "NoLineDiscount",
        "discount": 0,
    })
    assert len(asm.sessions["s1"].items) == 1
    assert asm.sessions["s1"].items[0].item_description == "Burger"


def test_add_payment_line():
    asm = TransactionAssembler()
    asm.begin({"transactionSessionId": "s1", "storeIdentifier": "X", "posTerminalNo": "P1"})
    asm.add_payment_line({
        "transactionSessionId": "s1",
        "lineAttribute": "Cash",
        "paymentDescription": "Cash",
        "amount": 500.0,
    })
    assert len(asm.sessions["s1"].payments) == 1
    assert asm.sessions["s1"].payments[0].line_attribute == "Cash"


def test_commit_returns_session():
    asm = TransactionAssembler()
    asm.begin({"transactionSessionId": "s1", "storeIdentifier": "X", "posTerminalNo": "P1"})
    asm.add_sale_line({
        "transactionSessionId": "s1",
        "itemDescription": "Burger",
        "itemQuantity": 1,
        "itemUnitPrice": 249.0,
        "totalAmount": 249.0,
        "scanAttribute": "Auto",
        "itemAttribute": "None",
        "discountType": "NoLineDiscount",
        "discount": 0,
    })
    txn = asm.commit({"transactionSessionId": "s1", "transactionNumber": "BILL-001"})
    assert txn is not None
    assert txn.status == "committed"
    assert txn.bill_number == "BILL-001"
    assert "s1" not in asm.sessions


def test_commit_unknown_session_returns_none():
    asm = TransactionAssembler()
    txn = asm.commit({"transactionSessionId": "unknown"})
    assert txn is None


def test_buffered_event_before_begin():
    asm = TransactionAssembler()
    asm.add_sale_line({
        "transactionSessionId": "s1",
        "itemDescription": "Fries",
        "itemQuantity": 1,
        "itemUnitPrice": 99.0,
        "totalAmount": 99.0,
        "scanAttribute": "ManuallyEntered",
        "itemAttribute": "None",
        "discountType": "NoLineDiscount",
        "discount": 0,
    })
    assert "s1" not in asm.sessions
    assert len(asm._buffer) == 1
    asm.begin({"transactionSessionId": "s1", "storeIdentifier": "X", "posTerminalNo": "P1"})
    assert len(asm.sessions["s1"].items) == 1
    assert asm.sessions["s1"].items[0].scan_attribute == "ManuallyEntered"


def test_check_expired():
    asm = TransactionAssembler(timeout_seconds=0)
    asm.begin({"transactionSessionId": "s1", "storeIdentifier": "X", "posTerminalNo": "P1"})
    asm.add_sale_line({
        "transactionSessionId": "s1",
        "itemDescription": "Burger",
        "itemQuantity": 1,
        "itemUnitPrice": 249.0,
        "totalAmount": 249.0,
        "scanAttribute": "Auto",
        "itemAttribute": "None",
        "discountType": "NoLineDiscount",
        "discount": 0,
    })
    expired = asm.check_expired()
    assert len(expired) == 1
    assert expired[0].status == "expired"
    assert "s1" not in asm.sessions
