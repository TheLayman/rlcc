import json
import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app


@pytest.mark.anyio
async def test_begin_transaction():
    payload = {
        "event": "BeginTransactionWithTillLookup",
        "storeIdentifier": "NDCIN1223",
        "posTerminalNo": "POS 3",
        "transactionSessionId": "test-session-001",
        "cashier": "EMP-042",
        "transactionType": "CompletedNormally",
        "employeePurchase": False,
    }
    stringified = json.dumps(json.dumps(payload))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/rlcc/launch-event",
            content=stringified,
            headers={"Content-Type": "application/json", "x-authorization-key": "test"},
        )
    assert resp.status_code == 200
    assert resp.json()["message"] == "Success"


@pytest.mark.anyio
async def test_full_transaction_flow():
    session_id = "test-session-full-001"
    transport = ASGITransport(app=app)

    async def send(payload):
        stringified = json.dumps(json.dumps(payload))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/v1/rlcc/launch-event", content=stringified,
                                      headers={"Content-Type": "application/json"})

    # Begin
    await send({"event": "BeginTransactionWithTillLookup", "storeIdentifier": "NDCIN1223",
                "posTerminalNo": "POS 3", "transactionSessionId": session_id,
                "cashier": "EMP-042", "transactionType": "CompletedNormally", "employeePurchase": False})

    # Sale line
    await send({"event": "AddTransactionSaleLine", "transactionSessionId": session_id,
                "itemDescription": "Burger", "itemQuantity": 1, "itemUnitPrice": 249.0,
                "totalAmount": 249.0, "scanAttribute": "Auto", "itemAttribute": "None",
                "discountType": "NoLineDiscount", "discount": 0, "lineNumber": 1})

    # Payment
    await send({"event": "AddTransactionPaymentLine", "transactionSessionId": session_id,
                "lineAttribute": "Cash", "paymentDescription": "Cash", "amount": 249.0, "lineNumber": 1})

    # Total
    await send({"event": "AddTransactionTotalLine", "transactionSessionId": session_id,
                "lineAttribute": "TotalAmountToBePaid", "totalDescription": "Total", "amount": 249.0, "lineNumber": 1})

    # Commit
    resp = await send({"event": "CommitTransaction", "transactionSessionId": session_id,
                        "transactionNumber": "BILL-TEST-001"})
    assert resp.status_code == 200

    # Verify transaction was persisted
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/transactions")
    txns = resp.json()
    matching = [t for t in txns if t.get("id") == session_id]
    assert len(matching) == 1
    assert matching[0]["bill_number"] == "BILL-TEST-001"
    assert matching[0]["status"] == "committed"


@pytest.mark.anyio
async def test_invalid_json():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/rlcc/launch-event", content="not json",
                                  headers={"Content-Type": "application/json"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_normal_json_also_works():
    """Backend should accept both stringified and normal JSON."""
    payload = {
        "event": "BeginTransactionWithTillLookup",
        "storeIdentifier": "NDCIN1223",
        "posTerminalNo": "POS 3",
        "transactionSessionId": "test-session-normal-001",
        "cashier": "EMP-042",
        "transactionType": "CompletedNormally",
        "employeePurchase": False,
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/rlcc/launch-event", json=payload,
                                  headers={"x-authorization-key": "test"})
    assert resp.status_code == 200
